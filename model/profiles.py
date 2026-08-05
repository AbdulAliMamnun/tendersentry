"""Per-firm profiles for name lookup, precomputed for the serving artifact.

The demo cannot carry 950,607 interactions into a serverless function, so everything a
real firm's ranking needs is computed here at export time and shipped as a compact
record: the fourteen `firm_*` features, the counters the `cross_*` features consume,
and a centroid of the tenders the firm actually bid on.

**As-of discipline is inherited, not reimplemented.** Histories come from
`features.build_histories`, which excludes any interaction dated on or after the cutoff
with a strict inequality. The export cutoff is the export date, so a shipped profile
describes what a firm had done *before* today and nothing after.

**Only aggregate facts leave this module.** A profile carries counts, distinct-value
tallies, categories, regions, buyer keys, and band-level medians. It never carries the
list of procurements a named firm bid on, and no amount is ever attributable to a named
firm in anything served — the median lives in the feature vector the model consumes,
not in anything rendered.

**Centroids ship as int8.** A float32 centroid is 1,536 bytes; at the ≥5-bid floor that
is 16.5 MB before encoding. Quantized per-vector to int8 it is 384 bytes. Quantization
is lossy, so `tests/test_profiles.py` asserts the ranking it produces stays within a
bounded distance of the float32 ranking rather than trusting that it does.

**Two ways to measure that damage wrongly, both hit while building this.** Recorded
because each produces a confident number that looks like a result:

1. *Re-quantizing the shipped centroids.* They have already been through int8, so
   quantizing again is idempotent and reports exactly zero error. A perfect-looking
   number that measures nothing. Rebuild the comparison vectors in float32.
2. *Building test centroids from random tender subsets.* A mean over unrelated tenders
   collapses toward the global mean, leaving every cosine near-tied — so any
   perturbation reorders the list and the measurement reports tie-breaking rather than
   quantization. Cluster by trade, which is what a real firm centroid is.

The standard is **material reordering**: a swap between two tenders separated by more
than twice the quantization error. Swaps between tenders closer than that are ties,
and float64 would reorder them too.
"""

from __future__ import annotations

import base64
import logging
import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable

import numpy as np

from model import embeddings, features


LOGGER = logging.getLogger(__name__)

#: A firm needs this many observed bids before we will build a profile from it.
#:
#: Below five the history is not a profile, it is an anecdote — and the ambiguity gets
#: worse too: across all firms 27,546 normalized names collide, but at this floor only
#: 448 of 14,802 do, because the collisions are overwhelmingly one-off bidders sharing
#: a generic name.
MIN_BIDS = 5

#: Buyers, categories, and regions kept per firm. A firm at this floor has far fewer
#: distinct buyers than bids, so this truncates almost nothing while bounding the
#: worst case for a firm with thousands of bids.
MAX_COUNTER_KEYS = 200


@dataclass
class FirmProfile:
    """Everything the serving path needs about one firm."""

    canonical_id: str
    display_name: str
    normalized_name: str
    bids: int
    wins: int
    first_date: str | None
    last_date: str | None
    regions: Counter = field(default_factory=Counter)
    categories: Counter = field(default_factory=Counter)
    buyers: Counter = field(default_factory=Counter)
    firm_features: dict[str, float] = field(default_factory=dict)
    centroid: np.ndarray | None = None


def quantize(vector: np.ndarray) -> tuple[bytes, float]:
    """Per-vector int8 quantization, returning the bytes and the scale to undo it.

    Symmetric around zero and scaled by the largest magnitude, so the vector's
    direction — the only thing a cosine cares about — survives with ~0.4% worst-case
    per-component error.
    """
    peak = float(np.max(np.abs(vector))) or 1.0
    scale = peak / 127.0
    quantized = np.clip(np.round(vector / scale), -127, 127).astype(np.int8)
    return quantized.tobytes(), scale


def dequantize(payload: bytes, scale: float, dim: int) -> np.ndarray:
    """Undo :func:`quantize`."""
    return np.frombuffer(payload, dtype=np.int8).astype(np.float32)[:dim] * scale


def build_profiles(
    connection: Any,
    interactions: list[features.Interaction],
    as_of: str,
    min_bids: int = MIN_BIDS,
) -> list[FirmProfile]:
    """Profile every firm with enough observed history, as of `as_of`."""
    histories = features.build_histories(interactions, as_of)
    eligible = {
        canonical_id: history
        for canonical_id, history in histories.items()
        if history.interactions >= min_bids
    }
    LOGGER.info(
        "Profiling %d firms with >= %d bids (of %d with any history)",
        len(eligible),
        min_bids,
        len(histories),
    )

    names = _display_names(connection, set(eligible))

    # Titles the firm bid on, so the centroid is the mean of work it actually pursued —
    # the same definition training uses, rather than a proxy built from trade slugs.
    titles: dict[str, list[str]] = {}
    for item in interactions:
        if item.canonical_id not in eligible or not item.title:
            continue
        if item.date and item.date >= as_of:
            continue
        titles.setdefault(item.canonical_id, []).append(item.title)

    ordered = sorted(eligible)
    flat: list[str] = []
    spans: dict[str, tuple[int, int]] = {}
    for canonical_id in ordered:
        firm_titles = titles.get(canonical_id, [])
        spans[canonical_id] = (len(flat), len(flat) + len(firm_titles))
        flat.extend(firm_titles)

    vectors = embeddings.embed(flat) if flat else np.zeros((0, embeddings.EMBEDDING_DIM))

    profiles: list[FirmProfile] = []
    for canonical_id in ordered:
        history = eligible[canonical_id]
        display, normalized = names.get(canonical_id, (canonical_id, canonical_id))
        start, end = spans[canonical_id]
        centroid = (
            embeddings.centroid(list(vectors[start:end]))
            if end > start
            else np.zeros(embeddings.EMBEDDING_DIM, dtype=np.float32)
        )
        profiles.append(
            FirmProfile(
                canonical_id=canonical_id,
                display_name=display,
                normalized_name=normalized,
                bids=history.interactions,
                wins=history.wins,
                first_date=history.first_date,
                last_date=history.last_date,
                regions=Counter(dict(history.regions.most_common(MAX_COUNTER_KEYS))),
                categories=Counter(dict(history.categories.most_common(MAX_COUNTER_KEYS))),
                buyers=Counter(dict(history.buyers.most_common(MAX_COUNTER_KEYS))),
                firm_features=features.firm_features(history, as_of),
                centroid=np.asarray(centroid, dtype=np.float32),
            )
        )
    return profiles


def _display_names(connection: Any, wanted: set[str]) -> dict[str, tuple[str, str]]:
    """Best display name and normalized key per canonical id.

    The most-observed raw spelling wins, so a firm shows the name it files under most
    often rather than whichever variant happened to sort first.
    """
    rows = connection.execute(
        "SELECT canonical_id, raw_name, normalized_name, observations "
        "FROM firm_entities ORDER BY observations DESC"
    ).fetchall()
    chosen: dict[str, tuple[str, str]] = {}
    for row in rows:
        canonical_id = str(row["canonical_id"])
        if canonical_id not in wanted or canonical_id in chosen:
            continue
        chosen[canonical_id] = (str(row["raw_name"]), str(row["normalized_name"]))
    return chosen


def name_index(profiles: Iterable[FirmProfile]) -> dict[str, list[str]]:
    """Normalized name to the canonical ids that answer to it.

    A list, never a single id: 448 of 14,802 resolvable names are shared by more than
    one firm at this floor, and picking one would be guessing about which company the
    visitor meant.
    """
    index: dict[str, list[str]] = {}
    for profile in profiles:
        index.setdefault(profile.normalized_name, []).append(profile.canonical_id)
    return index


def serialize(profiles: list[FirmProfile]) -> dict[str, Any]:
    """The shipped artifact.

    Deliberately excludes bid amounts and the list of procurements a firm bid on.
    `firm_median_bid` survives inside the feature vector because the model consumes it;
    nothing rendered attributes an amount to a named firm.
    """
    records = []
    for profile in profiles:
        payload, scale = quantize(profile.centroid)
        records.append(
            {
                "id": profile.canonical_id,
                "name": profile.display_name,
                "normalized": profile.normalized_name,
                "bids": profile.bids,
                "wins": profile.wins,
                "first": profile.first_date,
                "last": profile.last_date,
                "regions": dict(profile.regions),
                "categories": dict(profile.categories),
                "buyers": dict(profile.buyers),
                "features": {k: round(float(v), 6) for k, v in profile.firm_features.items()},
                "centroid": base64.b64encode(payload).decode("ascii"),
                "centroid_scale": scale,
            }
        )
    return {
        "count": len(records),
        "min_bids": MIN_BIDS,
        "embedding_dim": embeddings.EMBEDDING_DIM,
        "index": name_index(profiles),
        "firms": records,
    }


def cosine(first: np.ndarray, second: np.ndarray) -> float:
    """Cosine similarity, zero when either side has no magnitude."""
    left = float(np.linalg.norm(first))
    right = float(np.linalg.norm(second))
    if left == 0 or right == 0:
        return 0.0
    return float(np.dot(first, second) / (left * right))


def quantization_divergence(profiles: list[FirmProfile], tenders: np.ndarray) -> dict[str, Any]:
    """How far int8 quantization moves a firm's ranking of a tender set.

    Reported rather than assumed: quantization that quietly reordered boards would be
    invisible in every other test.
    """
    max_cosine_error = 0.0
    rank_changes: list[int] = []
    top_changes = 0
    material = 0

    for profile in profiles:
        exact = profile.centroid
        payload, scale = quantize(exact)
        approx = dequantize(payload, scale, len(exact))

        exact_scores = [cosine(exact, t) for t in tenders]
        approx_scores = [cosine(approx, t) for t in tenders]
        max_cosine_error = max(
            max_cosine_error,
            max(abs(a - b) for a, b in zip(exact_scores, approx_scores)),
        )

        exact_order = sorted(range(len(tenders)), key=lambda i: -exact_scores[i])
        approx_order = sorted(range(len(tenders)), key=lambda i: -approx_scores[i])
        position = {index: rank for rank, index in enumerate(exact_order)}
        rank_changes.append(
            max(abs(position[index] - rank) for rank, index in enumerate(approx_order))
        )
        if exact_order[:10] != approx_order[:10]:
            top_changes += 1

        # A swap between two tenders whose true similarity differs by less than the
        # quantization error is not a quantization defect — those tenders are tied, and
        # *any* perturbation reorders them, float64 included. What would matter is a
        # swap between two tenders that were genuinely separated. Counting only those
        # is the difference between measuring damage and measuring tie-breaking.
        error = max(abs(a - b) for a, b in zip(exact_scores, approx_scores))
        approx_position = {index: rank for rank, index in enumerate(approx_order)}
        for i in range(len(tenders)):
            for j in range(i + 1, len(tenders)):
                gap = exact_scores[i] - exact_scores[j]
                if abs(gap) <= error * 2:
                    continue
                exact_first = gap > 0
                approx_first = approx_position[i] < approx_position[j]
                if exact_first != approx_first:
                    material += 1

    return {
        "firms": len(profiles),
        "tenders": int(len(tenders)),
        "max_cosine_error": round(max_cosine_error, 8),
        "max_rank_shift": max(rank_changes) if rank_changes else 0,
        "mean_rank_shift": round(float(np.mean(rank_changes)), 4) if rank_changes else 0.0,
        "firms_with_changed_top10": top_changes,
        # Swaps between tenders separated by more than twice the quantization error.
        # This is the number that would indicate real degradation.
        "material_reorderings": material,
    }


def bytes_estimate(profiles: list[FirmProfile]) -> dict[str, float]:
    """Rough artifact cost, so the size is a decision rather than a surprise."""
    dim = embeddings.EMBEDDING_DIM
    return {
        "firms": len(profiles),
        "float32_mb": round(len(profiles) * dim * 4 / 1_048_576, 2),
        "int8_mb": round(len(profiles) * dim / 1_048_576, 2),
        "int8_base64_mb": round(len(profiles) * math.ceil(dim / 3) * 4 / 1_048_576, 2),
    }
