"""Estimate how big a job a tender notice actually is.

Under 1% of open notices publish a value (241 of 48,834), so "what size is this?" — the
first question a contractor asks — is unanswerable from the notice itself almost every
time. But 199,714 *past* procurements in `bid_interactions` carry a winning bid amount,
which is the contract value. This module learns the relationship between how a job is
described and what it went for, and uses it to put a band on notices that publish
nothing.

**Three tiers, best-available wins.**

1. ``published``  — the notice states a value. Nothing overrides this.
2. ``estimated_model``   — a learned estimate from comparable past contracts.
3. ``estimated_pattern`` — deterministic EN+FR markers, when the learned tier has no
   comparable cell to draw on.

A notice with no signal gets ``unknown``. A band is never forced: "we don't know" is a
usable answer to a contractor and a fabricated band is not.

**The estimators are artifacts, and ``--fit`` must run before any ``--backfill``.**

    python3 -m model.scale --fit          # reads bid_interactions, writes the artifact
    python3 -m model.scale --backfill     # reads the artifact, writes scale_* columns

``--backfill`` never refits. Without ``model/artifacts/scale-estimator.json`` it exits
naming ``--fit`` rather than rebuilding the corpus, because a refit against a partial
database produces bands that look exactly like correct ones. ``--fit`` belongs to the
retrain path and runs when ``bid_interactions`` changes; ``--backfill`` is a daily step
and touches only notices with no band yet, unless ``--all`` is passed. Re-fit and you
want ``--backfill --all``, or the existing bands go on describing the previous fit.

**Estimates are never model features.** They filter and they display, and a declared
job size applies the same bounded modifier `matchrec.scoring` uses — nothing more. The
estimate is derived from the notice's title, and so is the trade match; feeding one
into a ranking that already uses the other would make "size fit" a restatement of
"trade fit" wearing the costume of independent evidence. That is the same failure as a
leaked feature, and it is ruled out by design rather than by care.

**Known limit: the corpus is Québec.** 199,644 of 199,714 priced awards are QC; Ontario
has nine. The MTO contract-award dataset that would have supplied the Ontario side is a
listing with no resources and no licence (see model/README.md). So a band on an Ontario
notice is an inference from Québec comparables, and the artifact says so.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import re
import sqlite3
import statistics
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import config
from matchrec import trades
from model import inflation
from notices import db
from notices.normalize import normalize_buyer_type


LOGGER = logging.getLogger(__name__)

#: Where the fitted estimators live. Committed, because the bands they produce are
#: published and an unreproducible published number is the thing this repo keeps
#: refusing to ship.
ARTIFACT_DIR = Path(config.PROJECT_ROOT) / "model" / "artifacts"
ESTIMATOR_PATH = ARTIFACT_DIR / "scale-estimator.json"
BOOSTER_PATH = ARTIFACT_DIR / "scale-estimator.lgb"

#: Bumped when the artifact's shape changes in a way a previous loader would
#: misread. A mismatch fails the load rather than reading the old shape wrongly.
ARTIFACT_VERSION = 1

#: Bands in ascending order. The upper bound is exclusive.
BANDS: tuple[tuple[str, float, float], ...] = (
    ("<$100K", 0.0, 100_000.0),
    ("$100–500K", 100_000.0, 500_000.0),
    ("$500K–2M", 500_000.0, 2_000_000.0),
    ("$2–10M", 2_000_000.0, 10_000_000.0),
    (">$10M", 10_000_000.0, math.inf),
)

BAND_NAMES = tuple(name for name, _, _ in BANDS)
UNKNOWN = "unknown"

SOURCES = ("published", "estimated_model", "estimated_pattern", "unknown")

#: A cell needs this many past contracts before its median is worth quoting.
MIN_CELL = 12

#: Corpus floor. The price index starts 2017-Q1, and an amount we cannot deflate is
#: excluded rather than carried at face value — 3.8% of priced awards.
CORPUS_START = "2017-01-01"

#: Amounts outside this range are data errors, not contracts.
MIN_AMOUNT = 1_000.0
MAX_AMOUNT = 2_000_000_000.0


def band_of(amount: float | None) -> str:
    """Which band an amount falls in."""
    if amount is None or amount <= 0:
        return UNKNOWN
    for name, low, high in BANDS:
        if low <= amount < high:
            return name
    return BANDS[-1][0]


def band_index(band: str) -> int | None:
    """Ordinal position, for measuring how far apart two bands are."""
    return BAND_NAMES.index(band) if band in BAND_NAMES else None


def band_distance(first: str, second: str) -> int | None:
    """How many bands apart, or None when either side is unknown."""
    a, b = band_index(first), band_index(second)
    return None if a is None or b is None else abs(a - b)


# --------------------------------------------------------------------------------------
# Pattern rules — tier 3
# --------------------------------------------------------------------------------------

#: Markers of a large programme rather than a single job. EN and FR, because half the
#: corpus is French and a rule that only reads English silently never fires on it.
LARGE_MARKERS: tuple[tuple[str, str], ...] = (
    (r"\bdiverses rues\b|\bplusieurs rues\b|\bvarious streets\b", "multiple streets"),
    (r"\bpluriannuel|\bmulti[- ]?year\b|\b\d{4}\s*[-–]\s*\d{4}\b", "multi-year"),
    # "station d'épuration", "station de pompage", "station du traitement" — the
    # article varies, so match across it rather than enumerating each form.
    (r"\bstation d\w{0,2}\W{0,2}(?:pompage|epuration|traitement)|\b(?:water|wastewater|treatment) plant\b|\bpumping station\b", "plant or station"),
    (r"\busine\b|\bcentrale\b", "plant"),
    (r"\bphase\s*[0-9ivx]+\b|\blot\s*\d+\b", "phased or lotted"),
    (r"\baccord[- ]cadre\b|\bentente[- ]cadre\b|\bstanding offer\b|\bsupply arrangement\b|\brfso\b|\brfsa\b", "standing offer"),
    (r"\breconstruction\b|\bconstruction d['’]une?\b", "reconstruction or new build"),
)

#: Markers of a small, single-object job.
SMALL_MARKERS: tuple[tuple[str, str], ...] = (
    (r"\btravaux mineurs\b|\bminor works\b|\bpetits travaux\b", "minor works"),
    (r"\bponceau\b|\bculvert\b", "culvert"),
    (r"\bfourniture (?:et livraison )?d['’]un\b|\bsupply of one\b|\bacquisition d['’]un\b", "single item supply"),
    (r"\blocation d['’](?:un|une)\b|\bequipment rental\b", "equipment rental"),
    (r"\binspection\b|\betude\b|\bstudy\b|\bexpertise\b", "study or inspection"),
    (r"\bentretien (?:menager|sanitaire)\b|\bjanitorial\b|\bdeneigement d['’]un\b", "routine maintenance"),
)

_LARGE = [(re.compile(p), label) for p, label in LARGE_MARKERS]
_SMALL = [(re.compile(p), label) for p, label in SMALL_MARKERS]


def _fold(value: Any) -> str:
    import unicodedata

    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(c for c in text if not unicodedata.combining(c))
    # Typographic apostrophes are the norm in French notice titles; a rule written
    # with a straight quote would silently never fire on real SEAO data.
    text = text.replace("\u2019", "'").replace("\u2018", "'")
    return re.sub(r"\s+", " ", text).lower().strip()


def pattern_band(title: str) -> tuple[str, str] | None:
    """A band from deterministic markers, with the marker that produced it.

    Only fires when the evidence is one-sided. A title carrying both a large-programme
    and a small-job marker is genuinely ambiguous, and guessing between them would be
    the sort of confident nonsense the whole pipeline is built to avoid.
    """
    folded = _fold(title)
    large = [label for pattern, label in _LARGE if pattern.search(folded)]
    small = [label for pattern, label in _SMALL if pattern.search(folded)]

    if large and not small:
        return "$2–10M", large[0]
    if small and not large:
        return "<$100K", small[0]
    return None


# --------------------------------------------------------------------------------------
# Corpus
# --------------------------------------------------------------------------------------


@dataclass
class Award:
    """One past procurement whose contract value we know, in current dollars."""

    ocid: str
    date: str
    amount: float
    slug: str | None
    buyer_type: str
    region: str | None
    title: str


def build_corpus(connection: sqlite3.Connection, limit: int | None = None) -> list[Award]:
    """Winning bids with amounts, mapped to trades and restated in current dollars."""
    mapping = trades.load_mapping()
    query = (
        "SELECT ocid, interaction_date, bid_amount, buyer_name, region, title "
        "FROM bid_interactions "
        "WHERE won = 1 AND bid_amount IS NOT NULL AND interaction_date >= ? "
        "ORDER BY interaction_date"
    )
    if limit:
        query += f" LIMIT {int(limit)}"

    awards: list[Award] = []
    skipped_range = 0
    skipped_index = 0
    for row in connection.execute(query, (CORPUS_START,)):
        raw = float(row["bid_amount"])
        if not (MIN_AMOUNT <= raw <= MAX_AMOUNT):
            skipped_range += 1
            continue
        adjusted = inflation.adjust(raw, str(row["interaction_date"]))
        if adjusted is None:
            skipped_index += 1
            continue

        classified = mapping.classify({"title": row["title"]})
        slugs = classified.get("trade_slugs") or []
        awards.append(
            Award(
                ocid=str(row["ocid"]),
                date=str(row["interaction_date"]),
                amount=adjusted,
                slug=slugs[0] if slugs else None,
                buyer_type=normalize_buyer_type(row["buyer_name"]),
                region=row["region"],
                title=str(row["title"] or ""),
            )
        )

    LOGGER.info(
        "Corpus: %d priced awards from %s (%d outside the plausible range, %d before "
        "the price index starts)",
        len(awards),
        CORPUS_START,
        skipped_range,
        skipped_index,
    )
    return awards


# --------------------------------------------------------------------------------------
# Tier 2a — median lookup
# --------------------------------------------------------------------------------------


class LookupEstimator:
    """Median contract value by trade, buyer type, and region.

    Cells fall back from specific to general, and a cell thinner than ``MIN_CELL`` is
    skipped rather than quoted — a median of four contracts is an anecdote.
    """

    def __init__(self, min_cell: int = MIN_CELL) -> None:
        self.min_cell = min_cell
        self.cells: dict[tuple, float] = {}
        self.counts: dict[tuple, int] = {}
        self.global_median: float | None = None

    def fit(self, awards: Iterable[Award]) -> "LookupEstimator":
        buckets: dict[tuple, list[float]] = defaultdict(list)
        everything: list[float] = []
        for award in awards:
            everything.append(award.amount)
            for key in self._keys(award.slug, award.buyer_type, award.region):
                buckets[key].append(award.amount)

        for key, values in buckets.items():
            self.counts[key] = len(values)
            if len(values) >= self.min_cell:
                self.cells[key] = statistics.median(values)
        self.global_median = statistics.median(everything) if everything else None
        LOGGER.info(
            "Lookup: %d cells above the %d-contract floor (of %d)",
            len(self.cells),
            self.min_cell,
            len(buckets),
        )
        return self

    @staticmethod
    def _keys(slug: str | None, buyer_type: str, region: str | None) -> list[tuple]:
        """Most specific first."""
        keys: list[tuple] = []
        if slug:
            keys.append(("sbr", slug, buyer_type, region))
            keys.append(("sb", slug, buyer_type))
            keys.append(("sr", slug, region))
            keys.append(("s", slug))
        keys.append(("br", buyer_type, region))
        return keys

    def predict(
        self, slug: str | None, buyer_type: str, region: str | None
    ) -> tuple[float, str] | None:
        """Median for the most specific cell with enough contracts behind it."""
        for key in self._keys(slug, buyer_type, region):
            if key in self.cells:
                return self.cells[key], key[0]
        return (self.global_median, "global") if self.global_median else None


# --------------------------------------------------------------------------------------
# Tier 2b — learned regressor
# --------------------------------------------------------------------------------------


def _build_matrix(
    slugs: list[str | None],
    buyer_types: list[str],
    regions: list[str | None],
    titles: list[str],
    embeddings: Any,
    slug_order: list[str],
    buyer_order: list[str],
    region_order: list[str],
) -> Any:
    """Fill a preallocated float32 matrix.

    Deliberately not a list comprehension of lists. At 188k rows and ~420 columns that
    shape is roughly two gigabytes of boxed Python floats, which is enough to get the
    process killed mid-backfill with no traceback — which is exactly what it did.
    """
    import numpy as np

    slug_at = {name: i for i, name in enumerate(slug_order)}
    buyer_at = {name: len(slug_order) + i for i, name in enumerate(buyer_order)}
    region_at = {
        name: len(slug_order) + len(buyer_order) + i for i, name in enumerate(region_order)
    }
    offset = len(slug_order) + len(buyer_order) + len(region_order)
    width = offset + 2 + int(np.asarray(embeddings).shape[1])

    matrix = np.zeros((len(titles), width), dtype=np.float32)
    for row, (slug, buyer, region, title) in enumerate(
        zip(slugs, buyer_types, regions, titles)
    ):
        if slug in slug_at:
            matrix[row, slug_at[slug]] = 1.0
        if buyer in buyer_at:
            matrix[row, buyer_at[buyer]] = 1.0
        if region in region_at:
            matrix[row, region_at[region]] = 1.0
        matrix[row, offset] = len(title)
        matrix[row, offset + 1] = title.count(" ") + 1
    matrix[:, offset + 2 :] = np.asarray(embeddings, dtype=np.float32)
    return matrix


@dataclass
class GbmEstimator:
    """LightGBM regressor over title embedding plus the categorical keys."""

    model: Any
    slug_order: list[str]
    buyer_order: list[str]
    region_order: list[str]

    def predict(
        self, slug: str | None, buyer_type: str, region: str | None, title: str, embedding: Any
    ) -> float:
        import numpy as np

        matrix = _build_matrix(
            [slug], [buyer_type], [region], [title], np.asarray([embedding]),
            self.slug_order, self.buyer_order, self.region_order,
        )
        return float(10.0 ** float(self.model.predict(matrix)[0]))

    def predict_many(
        self,
        slugs: list[str | None],
        buyer_types: list[str],
        regions: list[str | None],
        titles: list[str],
        embeddings: Any,
    ) -> list[float]:
        """Batch prediction. One matrix and one call rather than N of each."""
        matrix = _build_matrix(
            slugs, buyer_types, regions, titles, embeddings,
            self.slug_order, self.buyer_order, self.region_order,
        )
        return [float(10.0**value) for value in self.model.predict(matrix)]


def fit_gbm(awards: list[Award], embeddings: Any) -> GbmEstimator:
    """Fit log10(adjusted value) from title embedding + trade + buyer + region."""
    import numpy as np
    from lightgbm import LGBMRegressor

    slug_order = sorted({a.slug for a in awards if a.slug})
    buyer_order = sorted({a.buyer_type for a in awards})
    region_order = sorted({a.region for a in awards if a.region})

    matrix = _build_matrix(
        [a.slug for a in awards],
        [a.buyer_type for a in awards],
        [a.region for a in awards],
        [a.title for a in awards],
        embeddings,
        slug_order, buyer_order, region_order,
    )
    targets = np.array([math.log10(a.amount) for a in awards], dtype=np.float32)

    model = LGBMRegressor(
        n_estimators=400, learning_rate=0.05, num_leaves=63,
        min_child_samples=40, subsample=0.9, colsample_bytree=0.7,
        random_state=20260804, verbose=-1,
    )
    model.fit(matrix, targets)
    return GbmEstimator(model, slug_order, buyer_order, region_order)


# --------------------------------------------------------------------------------------
# Artifacts — fit once, load thereafter
# --------------------------------------------------------------------------------------


class MissingArtifact(RuntimeError):
    """Raised when a backfill is asked to run without a fitted estimator.

    Deliberately fatal. The alternative — refitting from whatever corpus happens to
    be reachable — is the failure this whole artifact exists to remove: a partial
    corpus produces plausible bands with no error, and nothing downstream can tell
    the difference between a band learned from 187,870 awards and one learned from
    a thousand.
    """


def git_revision() -> str | None:
    """Current git revision, or None outside a repository."""
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(config.PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        LOGGER.warning("Could not read the git revision: %s", exc)
        return None
    return completed.stdout.strip() or None


def _encode_keys(mapping: dict[tuple, Any]) -> list[list]:
    """Tuple keys as JSON lists.

    Not joined into a delimited string: key parts are trade slugs, buyer types and
    region codes drawn from source data, and any separator chosen here would be one
    a future slug could contain.
    """
    return [[list(key), value] for key, value in mapping.items()]


def _decode_keys(pairs: Any) -> dict[tuple, Any]:
    return {tuple(key): value for key, value in pairs}


def save_estimators(
    lookup: LookupEstimator,
    gbm: GbmEstimator | None,
    awards: list[Award],
    estimator_path: Path | str | None = None,
    booster_path: Path | str | None = None,
) -> dict[str, Any]:
    """Write the fitted estimators and the provenance that explains them."""
    estimator_file = Path(estimator_path or ESTIMATOR_PATH)
    booster_file = Path(booster_path or BOOSTER_PATH)
    estimator_file.parent.mkdir(parents=True, exist_ok=True)

    if gbm is not None:
        gbm.model.booster_.save_model(str(booster_file))
    elif booster_file.exists():
        # A lookup-only refit must not leave the previous run's booster behind for
        # the loader to pick up and pair with mismatched slug orders.
        booster_file.unlink()

    dates = [award.date for award in awards if award.date]
    payload = {
        "artifact_version": ARTIFACT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_revision": git_revision(),
        "corpus": {
            "awards": len(awards),
            "date_filter": f"interaction_date >= {CORPUS_START}",
            "corpus_start": CORPUS_START,
            "first_award": min(dates) if dates else None,
            "last_award": max(dates) if dates else None,
            "min_amount": MIN_AMOUNT,
            "max_amount": MAX_AMOUNT,
            "source_table": "bid_interactions",
            "source_filter": "won = 1 AND bid_amount IS NOT NULL",
        },
        "deflator": dict(inflation.SERIES),
        "bands": list(BAND_NAMES),
        "lookup": {
            "min_cell": lookup.min_cell,
            "cells_above_floor": len(lookup.cells),
            "buckets_total": len(lookup.counts),
            "global_median": lookup.global_median,
            "cells": _encode_keys(lookup.cells),
            "counts": _encode_keys(lookup.counts),
        },
        "gbm": None
        if gbm is None
        else {
            "booster_file": booster_file.name,
            "embedding_model": embeddings_model_name(),
            "slug_order": gbm.slug_order,
            "buyer_order": gbm.buyer_order,
            "region_order": gbm.region_order,
        },
        "notes": [
            "Bands are ESTIMATES unless scale_source is 'published'. Any surface "
            "showing a band must show its source alongside it.",
            "The corpus is 99.96% Quebec, so a band on an Ontario notice is an "
            "inference from Quebec comparables.",
            "Amounts are deflated to current dollars before fitting; awards before "
            f"{CORPUS_START} are excluded rather than carried unadjusted.",
            "Scale estimates are a filter and a bounded modifier only, never a "
            "ranking-model feature.",
        ],
    }
    with estimator_file.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")

    LOGGER.info(
        "Wrote %s (%.1f KB) and %s",
        estimator_file.name,
        estimator_file.stat().st_size / 1024,
        booster_file.name if gbm is not None else "(no booster: lookup-only fit)",
    )
    return payload


def embeddings_model_name() -> str:
    """The embedding model name, without importing torch when nobody needs it."""
    from model import embeddings as emb

    return emb.MODEL_NAME


def load_estimators(
    estimator_path: Path | str | None = None,
    booster_path: Path | str | None = None,
) -> tuple[LookupEstimator, GbmEstimator | None, dict[str, Any]]:
    """Load the fitted estimators, or fail loudly naming ``--fit``."""
    estimator_file = Path(estimator_path or ESTIMATOR_PATH)
    if not estimator_file.is_file():
        raise MissingArtifact(
            f"No scale estimator at {estimator_file}. Fit one first:\n"
            f"    python3 -m model.scale --fit\n"
            "A backfill will not refit from the database: doing so silently produces "
            "bands from whatever corpus is reachable, which is indistinguishable "
            "from a correct run."
        )
    try:
        with estimator_file.open(encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise MissingArtifact(
            f"Could not read the scale estimator at {estimator_file}: {exc}. "
            "Re-fit it with `python3 -m model.scale --fit`."
        ) from exc

    version = payload.get("artifact_version")
    if version != ARTIFACT_VERSION:
        raise MissingArtifact(
            f"Scale estimator at {estimator_file} is version {version!r}, but this "
            f"code reads version {ARTIFACT_VERSION}. Re-fit it with "
            "`python3 -m model.scale --fit`."
        )

    body = payload.get("lookup") or {}
    lookup = LookupEstimator(min_cell=int(body.get("min_cell", MIN_CELL)))
    lookup.cells = _decode_keys(body.get("cells") or [])
    lookup.counts = _decode_keys(body.get("counts") or [])
    lookup.global_median = body.get("global_median")

    gbm = None
    gbm_body = payload.get("gbm")
    if gbm_body:
        booster_file = Path(booster_path or BOOSTER_PATH)
        if not booster_file.is_file():
            raise MissingArtifact(
                f"{estimator_file.name} describes a GBM but {booster_file} is "
                "missing. Re-fit with `python3 -m model.scale --fit`."
            )
        from lightgbm import Booster

        gbm = GbmEstimator(
            model=Booster(model_file=str(booster_file)),
            slug_order=list(gbm_body.get("slug_order") or []),
            buyer_order=list(gbm_body.get("buyer_order") or []),
            region_order=list(gbm_body.get("region_order") or []),
        )

    LOGGER.info(
        "Loaded scale estimator generated %s (%s awards, %d cells, gbm=%s)",
        payload.get("generated_at"),
        (payload.get("corpus") or {}).get("awards"),
        len(lookup.cells),
        gbm is not None,
    )
    return lookup, gbm, payload


def fit(
    connection: sqlite3.Connection,
    use_gbm: bool = True,
    estimator_path: Path | str | None = None,
    booster_path: Path | str | None = None,
) -> dict[str, Any]:
    """Build the corpus, fit both estimators, and write the artifact."""
    awards = build_corpus(connection)
    if not awards:
        raise RuntimeError(
            "The corpus is empty; refusing to write an estimator that would band "
            "every notice from nothing."
        )
    lookup = LookupEstimator().fit(awards)

    gbm = None
    if use_gbm:
        from model import embeddings as emb

        vectors = emb.embed([award.title for award in awards])
        gbm = fit_gbm(awards, vectors)

    return save_estimators(lookup, gbm, awards, estimator_path, booster_path)


# --------------------------------------------------------------------------------------
# Combined estimate
# --------------------------------------------------------------------------------------


@dataclass
class Estimate:
    band: str
    source: str
    confidence: float
    detail: str


def estimate_notice(
    notice: dict,
    lookup: LookupEstimator | None,
    gbm: GbmEstimator | None = None,
    embedding: Any = None,
    mapping: trades.TradeMapping | None = None,
) -> Estimate:
    """Best available band for one notice."""
    published = notice.get("estimated_value")
    if published is not None:
        try:
            amount = float(published)
        except (TypeError, ValueError):
            amount = 0.0
        if amount > 0:
            return Estimate(band_of(amount), "published", 1.0, "value published by the buyer")

    title = str(notice.get("title") or "")
    mapping = mapping or trades.load_mapping()
    slugs = notice.get("trade_slugs")
    if slugs is None:
        slugs = mapping.classify({"title": title}).get("trade_slugs") or []
    slug = slugs[0] if slugs else None
    buyer_type = notice.get("buyer_type") or normalize_buyer_type(notice.get("buyer_name"))
    region = notice.get("region")

    if gbm is not None and embedding is not None and slug:
        predicted = gbm.predict(slug, buyer_type, region, title, embedding)
        return Estimate(
            band_of(predicted),
            "estimated_model",
            0.6,
            "learned from comparable past contracts",
        )

    if lookup is not None and slug:
        result = lookup.predict(slug, buyer_type, region)
        if result is not None:
            median, cell = result
            # A broader fallback cell is a weaker claim, and the number says so.
            confidence = {"sbr": 0.7, "sb": 0.6, "sr": 0.6, "s": 0.5, "br": 0.35}.get(cell, 0.3)
            return Estimate(
                band_of(median),
                "estimated_model",
                confidence,
                f"median of comparable contracts ({cell})",
            )

    pattern = pattern_band(title)
    if pattern is not None:
        band, marker = pattern
        return Estimate(band, "estimated_pattern", 0.35, f"pattern: {marker}")

    return Estimate(UNKNOWN, "unknown", 0.0, "no signal")


# --------------------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------------------


#: Held-out window. Temporal, never random — a random split would let the model see
#: contracts awarded after the ones it is asked to predict.
EVAL_CUTOFF = "2025-07-01"


def evaluate(
    awards: list[Award], cutoff: str = EVAL_CUTOFF, use_gbm: bool = True
) -> dict[str, Any]:
    """Band accuracy of the lookup baseline and the GBM on a held-out time window."""
    train = [a for a in awards if a.date < cutoff]
    test = [a for a in awards if a.date >= cutoff]
    LOGGER.info("Split at %s: %d train, %d test", cutoff, len(train), len(test))
    if not test:
        return {"error": "empty test window"}

    truth = [band_of(a.amount) for a in test]

    def score(predicted: list[str]) -> dict[str, Any]:
        exact = sum(1 for p, t in zip(predicted, truth) if p == t)
        within = sum(
            1
            for p, t in zip(predicted, truth)
            if (d := band_distance(p, t)) is not None and d <= 1
        )
        return {
            "exact": round(exact / len(truth), 4),
            "within_one_band": round(within / len(truth), 4),
            "n": len(truth),
        }

    lookup = LookupEstimator().fit(train)
    lookup_bands = []
    for award in test:
        result = lookup.predict(award.slug, award.buyer_type, award.region)
        lookup_bands.append(band_of(result[0]) if result else UNKNOWN)

    report: dict[str, Any] = {
        "cutoff": cutoff,
        "train": len(train),
        "test": len(test),
        "baseline_lookup": score(lookup_bands),
    }

    # A pattern-only reading of the same titles, so the learned tiers have a floor to
    # beat that is not just "guess the most common band".
    pattern_bands = [
        (pattern_band(a.title) or (UNKNOWN, ""))[0] for a in test
    ]
    report["baseline_pattern"] = score(pattern_bands)
    majority = statistics.mode(band_of(a.amount) for a in train)
    report["baseline_majority"] = score([majority] * len(test)) | {"band": majority}

    if use_gbm:
        try:
            from model import embeddings as emb

            train_vectors = emb.embed([a.title for a in train])
            test_vectors = emb.embed([a.title for a in test])
            gbm = fit_gbm(train, train_vectors)
            predictions = gbm.predict_many(
                [a.slug for a in test],
                [a.buyer_type for a in test],
                [a.region for a in test],
                [a.title for a in test],
                test_vectors,
            )
            report["gbm"] = score([band_of(value) for value in predictions])
        except Exception as error:  # pragma: no cover - optional dependency path
            report["gbm"] = {"error": str(error)}

    ranked = [
        (name, body["exact"])
        for name, body in report.items()
        if isinstance(body, dict) and "exact" in body
    ]
    report["winner"] = max(ranked, key=lambda pair: pair[1])[0] if ranked else None
    return report


def _primary_slug(notice: dict, mapping: trades.TradeMapping) -> str | None:
    """The notice's leading trade, from its stored slugs or the mapping."""
    slugs = notice.get("trade_slugs")
    if not slugs:
        slugs = mapping.classify({"title": notice.get("title")}).get("trade_slugs") or []
    return slugs[0] if slugs else None


def backfill(
    connection: sqlite3.Connection,
    all_rows: bool = False,
    estimator_path: Path | str | None = None,
    booster_path: Path | str | None = None,
) -> dict[str, Any]:
    """Band the notices that need it, from the fitted artifact. Re-runnable.

    Incremental by default: only rows with no ``scale_band`` are touched, which on a
    daily run is the handful of notices ingestion just added. ``all_rows`` forces a
    full pass, which is what to use after re-fitting the estimator — otherwise the
    existing bands keep describing the previous fit.

    Never refits. A missing artifact raises :class:`MissingArtifact`.
    """
    db.migrate_scale_columns(connection)

    lookup, gbm, provenance = load_estimators(estimator_path, booster_path)
    embeddings_for = None
    if gbm is not None:
        from model import embeddings as emb

        embeddings_for = emb

    query = (
        "SELECT t.id, t.title, t.buyer_name, t.buyer_type, t.region, t.estimated_value, "
        "       nt.trade_slugs "
        "FROM tenders t LEFT JOIN notice_trades nt ON nt.tender_id = t.id"
    )
    if not all_rows:
        query += " WHERE t.scale_band IS NULL"
    rows = connection.execute(query).fetchall()
    LOGGER.info(
        "Banding %d notices (%s)",
        len(rows),
        "full pass" if all_rows else "unbanded only",
    )

    notices = []
    for row in rows:
        from matchrec import schema as matchrec_schema

        notices.append(
            {
                "id": int(row["id"]),
                "title": row["title"],
                "buyer_name": row["buyer_name"],
                "buyer_type": row["buyer_type"],
                "region": row["region"],
                "estimated_value": row["estimated_value"],
                "trade_slugs": matchrec_schema.loads(row["trade_slugs"], []),
            }
        )

    mapping = trades.load_mapping()

    # One batched call rather than one prediction per notice. Only rows the GBM will
    # actually score are embedded — a published value or a missing slug sends the
    # notice down another tier, and embedding its title would be work thrown away.
    predicted: dict[int, float] = {}
    if gbm is not None and embeddings_for is not None:
        rows_for_gbm = [
            index
            for index, notice in enumerate(notices)
            if not notice["estimated_value"] and _primary_slug(notice, mapping)
        ]
        if rows_for_gbm:
            title_vectors = embeddings_for.embed(
                [str(notices[i]["title"] or "") for i in rows_for_gbm]
            )
            values = gbm.predict_many(
                [_primary_slug(notices[i], mapping) for i in rows_for_gbm],
                [
                    notices[i]["buyer_type"] or normalize_buyer_type(notices[i]["buyer_name"])
                    for i in rows_for_gbm
                ],
                [notices[i]["region"] for i in rows_for_gbm],
                [str(notices[i]["title"] or "") for i in rows_for_gbm],
                title_vectors,
            )
            predicted = dict(zip(rows_for_gbm, values))

    counts: dict[str, int] = defaultdict(int)
    updates = []
    for index, notice in enumerate(notices):
        if index in predicted:
            result = Estimate(
                band_of(predicted[index]),
                "estimated_model",
                0.6,
                "learned from comparable past contracts",
            )
        else:
            result = estimate_notice(notice, lookup, None, None, mapping)
        counts[result.source] += 1
        updates.append((result.band, result.source, round(result.confidence, 3), notice["id"]))

    with connection:
        connection.executemany(
            "UPDATE tenders SET scale_band = ?, scale_source = ?, scale_confidence = ? "
            "WHERE id = ?",
            updates,
        )

    summary = {
        "notices": len(updates),
        "scope": "all" if all_rows else "unbanded",
        "by_source": dict(counts),
        "gbm": gbm is not None,
        "estimator": {
            "generated_at": provenance.get("generated_at"),
            "git_revision": provenance.get("git_revision"),
            "corpus_awards": (provenance.get("corpus") or {}).get("awards"),
        },
    }
    LOGGER.info("Backfilled %d notices: %s", len(updates), dict(counts))
    return summary


def _main() -> None:
    parser = argparse.ArgumentParser(description="Estimate contract scale for notices")
    parser.add_argument(
        "--fit",
        action="store_true",
        help="fit the estimators from bid_interactions and write the artifact",
    )
    parser.add_argument("--backfill", action="store_true", help="write bands to the database")
    parser.add_argument(
        "--all",
        action="store_true",
        help="with --backfill: re-band every notice, not only unbanded ones",
    )
    parser.add_argument("--no-gbm", action="store_true", help="lookup baseline only")
    parser.add_argument("--report", action="store_true", help="held-out comparison")
    parser.add_argument("--db", default=None)
    parser.add_argument("--artifact", default=None, help="path to scale-estimator.json")
    parser.add_argument("--booster", default=None, help="path to scale-estimator.lgb")
    args = parser.parse_args()

    connection = db.connect(args.db)
    try:
        if args.fit:
            payload = fit(
                connection,
                use_gbm=not args.no_gbm,
                estimator_path=args.artifact,
                booster_path=args.booster,
            )
            print(
                json.dumps(
                    {
                        key: value
                        for key, value in payload.items()
                        if key not in {"lookup", "notes"}
                    }
                    | {
                        "lookup": {
                            key: value
                            for key, value in payload["lookup"].items()
                            if key not in {"cells", "counts"}
                        }
                    },
                    indent=2,
                )
            )
        elif args.backfill:
            try:
                summary = backfill(
                    connection,
                    all_rows=args.all,
                    estimator_path=args.artifact,
                    booster_path=args.booster,
                )
            except MissingArtifact as error:
                raise SystemExit(str(error)) from error
            print(json.dumps(summary, indent=2))
        elif args.report:
            awards = build_corpus(connection)
            print(json.dumps(evaluate(awards, use_gbm=not args.no_gbm), indent=2))
        else:
            awards = build_corpus(connection)
            lookup = LookupEstimator().fit(awards)
            print(json.dumps({"awards": len(awards), "cells": len(lookup.cells)}, indent=2))
    finally:
        connection.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    _main()
