"""Export the serving artifacts for the live demo ranker.

Emits four files under ``web/data/model/``:

* ``booster.json``  — the leak-free GBM, dumped as plain tree structures so a
  dependency-free TypeScript walker can evaluate it.
* ``pool.json``     — the open-tender pool: display fields, tender-side features, and
  each tender's MiniLM embedding as base64 float32.
* ``slugs.json``    — one centroid per trade slug, computed from the pool.
* ``manifest.json`` — versions and the caveats that must travel with the artifacts.

**The demo does not embed the visitor's text.** MiniLM cannot fit in a serverless
function, and embedding a description with a *different* model would make the cosine
against MiniLM pool vectors meaningless rather than merely worse. Instead the
description is mapped to trade slugs by the existing deterministic rules, and the
firm vector is the centroid of pool tenders carrying those slugs — which is exactly
how a firm vector is defined in training: the mean of the tenders it engaged with.

**Consequence, recorded in the manifest.** Under this scheme the embedding-similarity
feature becomes substantially a function of trade overlap, since both sides derive
from the same slug assignment. The demo therefore leans harder on the mapping rules
than production does, where the centroid comes from a firm's real bidding history.
Demo behaviour is not model behaviour.
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

import config
from matchrec import schema as matchrec_schema
from matchrec import trades
from model import embeddings, features, profiles, train
from notices import db


LOGGER = logging.getLogger(__name__)

OUT_DIR = Path(config.PROJECT_ROOT) / "web" / "data" / "model"

#: Cutoff for the model that ships. Evaluation used held-out windows; the served
#: model is fitted on everything up to here so it is as current as the corpus allows.
SERVING_CUTOFF = "2026-07-01"

MANIFEST_NOTES = [
    "The demo does not embed the visitor's description. The description is mapped to "
    "trade slugs by matchrec's deterministic rules, and the firm vector is the "
    "centroid of pool tenders carrying those slugs.",
    "Because both sides of cross_embedding_similarity derive from the same slug "
    "assignment, the demo's ranking leans substantially on the mapping rules. In "
    "production the centroid comes from a firm's real bidding history instead. Demo "
    "behaviour is not model behaviour.",
    "Feature order in this manifest is authoritative. A scorer that reads features in "
    "a different order produces plausible-looking nonsense rather than an error.",
    "Scores are bid propensity, never a probability of winning.",
    "The demo firm is a cold start: every firm_* and cross_* history feature is zero "
    "except firm_days_since_last. Only cross_embedding_similarity and the tender's own "
    "attributes separate one notice from another.",
    "A declared region filters the pool and a declared job size applies the same "
    "bounded value modifier matchrec uses. Neither is fed to the model as a feature — "
    "a declared median bid alongside zero interactions is a combination the model "
    "never saw in training.",
    "Pool coverage is uneven by province. Most Ontario municipal notices sit behind "
    "portals TenderSentry monitors rather than republishes, so an Ontario-filtered "
    "ranking can be genuinely thin. The demo says so rather than padding the list.",
    "Slug centroids are computed from the pool, which is ~65% French SEAO notices, so "
    "cosine against them partly measures how French a title is. English notices carry "
    "a systematic penalty of roughly 0.2 — large enough that an English watermain "
    "notice scores below an English janitorial one. Eligibility is therefore decided "
    "by trade-slug agreement, which is language-independent; the cosine floor is only "
    "a backstop. Per-language centroids would fix this and are a known follow-up.",
    "Displayed fit is an absolute logistic on the raw score, never a min-max over the "
    "day's pool. Pool-relative scoring let the best row of a bad pool read 100.",
    "scale_band is an ESTIMATE unless scale_source is 'published'. It is inferred from "
    "historical winning bids on similar work, never stated by the buyer. Any surface "
    "that shows a band must show its source alongside it.",
    "Scale estimates are a filter and a bounded modifier only, never a model feature. "
    "The estimate derives from the notice title and so does the trade match; feeding "
    "one into a ranking that already uses the other would make size fit a restatement "
    "of trade fit dressed as independent evidence.",
    "The scale corpus is 99.96% Quebec: 199,644 of 199,714 priced awards are QC and "
    "Ontario has 9. The Ontario counterweight (data.ontario.ca's MTO contract-award "
    "dataset) is a listing with zero resources and an unspecified licence, so a band "
    "on an Ontario notice is an inference from Quebec comparables.",
    "Firm profiles carry aggregate facts only: counts, distinct-value tallies, "
    "categories, regions, and buyer keys. They never carry the list of procurements a "
    "named firm bid on, and no served surface attributes a bid amount to a named firm. "
    "Name lookup is gated behind the beta form, not open on the public demo.",
    "Firm centroids ship int8-quantized to keep the artifact affordable. "
    "tests/test_profiles.py asserts the ranking divergence against float32 rather than "
    "assuming quantization is harmless. Measured: max cosine error 0.0018, zero "
    "MATERIAL reorderings (a swap between tenders separated by more than twice the "
    "quantization error). Raw rank shifts of a few positions do occur and are not a "
    "defect: they are swaps between near-tied tenders, which any perturbation produces.",
    "Two ways to measure quantization damage that produce confident nonsense, both "
    "hit while building this. (1) Re-quantizing the SHIPPED centroids is idempotent "
    "and reports exactly zero error -- a perfect-looking number that measures nothing; "
    "rebuild in float32 instead. (2) Building test centroids from RANDOM tender "
    "subsets collapses them toward the global mean, leaving every cosine near-tied, so "
    "the measurement reports tie-breaking rather than quantization; cluster by trade, "
    "which is what a real firm centroid is.",
    "Amounts are deflated with StatCan 18-10-0289-01 (Quebec, non-residential "
    "buildings, division composite, 2023=100). That is a BUILDING index used as a "
    "proxy for engineering construction, because StatCan publishes no active "
    "infrastructure index: 18-10-0022 ends 2019 and 18-10-0096 ends 1993. Awards "
    "before 2017-Q1 are excluded rather than carried unadjusted.",
]


def open_pool(connection: Any, limit: int | None = None) -> list[dict]:
    """Every currently open tender, with the display fields the boards use."""
    query = (
        "SELECT t.id, t.source, t.source_id, t.title, t.buyer_name, t.buyer_type, "
        "       t.region, t.estimated_value, t.closing_date_utc, t.notice_url, "
        "       t.scale_band, t.scale_source, t.scale_confidence, "
        "       nt.trade_slugs, nt.mapping_status "
        "FROM tenders t LEFT JOIN notice_trades nt ON nt.tender_id = t.id "
        "WHERE t.status = 'open' AND t.closing_date_utc IS NOT NULL "
        "ORDER BY t.closing_date_utc"
    )
    if limit:
        query += f" LIMIT {int(limit)}"
    rows = connection.execute(query).fetchall()
    pool = []
    for row in rows:
        pool.append(
            {
                "tender_id": int(row["id"]),
                "source": str(row["source"]),
                "title": str(row["title"] or ""),
                "buyer": str(row["buyer_name"] or ""),
                "buyer_type": row["buyer_type"],
                "region": row["region"],
                "value": row["estimated_value"],
                "closing_date": str(row["closing_date_utc"] or "")[:10],
                "url": row["notice_url"],
                "trade_slugs": matchrec_schema.loads(row["trade_slugs"], []),
                "mapping_status": row["mapping_status"] or "unmapped",
                # Scale travels with its provenance. A band whose source the UI cannot
                # see would be shown as though the buyer had published it.
                "scale_band": row["scale_band"] or "unknown",
                "scale_source": row["scale_source"] or "unknown",
                "scale_confidence": row["scale_confidence"] or 0.0,
            }
        )
    LOGGER.info("Open-tender pool: %d notices", len(pool))
    return pool


def tender_feature_rows(pool: list[dict], feature_names: list[str]) -> list[dict]:
    """Tender-side features for each pool entry, keyed by feature name."""
    rows = []
    for entry in pool:
        values = features.tender_features(
            {
                "value": entry["value"],
                "title": entry["title"],
                "category": ", ".join(entry["trade_slugs"]),
                # Unknown before bidding closes, and excluded from the served model.
                "bidder_count": 0,
            }
        )
        rows.append({name: float(values.get(name, 0.0)) for name in values})
    return rows


def slug_centroids(pool: list[dict], vectors: np.ndarray) -> dict[str, list[float]]:
    """Mean embedding of the pool tenders carrying each trade slug."""
    by_slug: dict[str, list[int]] = {}
    for index, entry in enumerate(pool):
        for slug in entry["trade_slugs"]:
            by_slug.setdefault(slug, []).append(index)
    centroids = {}
    for slug, indices in sorted(by_slug.items()):
        centroid = embeddings.centroid([vectors[i] for i in indices])
        centroids[slug] = [round(float(value), 6) for value in centroid]
    LOGGER.info("Computed centroids for %d trade slugs", len(centroids))
    return centroids


def dump_booster(booster: Any, feature_names: list[str]) -> dict:
    """Dump the GBM as portable tree structures."""
    model = booster.dump_model()
    return {
        "feature_names": feature_names,
        "trees": model.get("tree_info", []),
        "num_trees": len(model.get("tree_info", [])),
        "objective": model.get("objective", "lambdarank"),
    }


def _encode(vectors: np.ndarray) -> str:
    return base64.b64encode(vectors.astype(np.float32).tobytes()).decode("ascii")


def export(
    out_dir: Path | str | None = None,
    db_path: Any = None,
    cutoff: str = SERVING_CUTOFF,
    pool_limit: int | None = None,
) -> dict:
    """Fit the served model and write every serving artifact."""
    directory = Path(out_dir) if out_dir else OUT_DIR
    directory.mkdir(parents=True, exist_ok=True)

    connection = db.connect(db_path)
    try:
        interactions = features.load_interactions(connection)
        pool = open_pool(connection, pool_limit)
    finally:
        connection.close()

    split = train.Split("serving", cutoff, None, "Model fitted for serving.")
    dataset = train.build_dataset(interactions, split, max_eval_firms=1)

    clean = [
        index
        for index, name in enumerate(dataset.feature_names)
        if name not in train.LEAKY_FEATURES
    ]
    served_names = [dataset.feature_names[i] for i in clean]
    gbm = train.fit_gbm(dataset, clean)

    vectors = embeddings.embed([entry["title"] for entry in pool])
    centroids = slug_centroids(pool, vectors)

    booster = dump_booster(gbm.booster_, served_names)
    _write(directory / "booster.json", booster)
    _write(
        directory / "pool.json",
        {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "count": len(pool),
            "embedding_dim": embeddings.EMBEDDING_DIM,
            "embeddings_base64": _encode(vectors),
            "tenders": pool,
            "tender_features": tender_feature_rows(pool, served_names),
        },
    )
    _write(directory / "slugs.json", {"centroids": centroids})

    # Firm profiles for the gated name-lookup path. Computed as-of the export date via
    # the same strict-inequality machinery training uses, so a shipped profile
    # describes what a firm had done before today and nothing after.
    firm_profiles = profiles.build_profiles(
        db.connect(db_path), interactions, as_of=datetime.now(timezone.utc).strftime("%Y-%m-%d")
    )
    _write(directory / "firms.json", profiles.serialize(firm_profiles))
    LOGGER.info("Firm profiles: %s", profiles.bytes_estimate(firm_profiles))
    # The demo derives trade slugs in the browser-facing function, so the rules
    # must travel with the artifacts rather than being re-implemented.
    mapping_path = Path(config.PROJECT_ROOT) / "matchrec" / "trade_mapping.json"
    _write(directory / "mapping.json", json.loads(mapping_path.read_text(encoding="utf-8")))

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "serving_cutoff": cutoff,
        "embedding_model": embeddings.MODEL_NAME,
        "mapping_version": trades.load_mapping().version,
        "feature_order": served_names,
        "leaky_features_excluded": list(train.LEAKY_FEATURES),
        "pool": {"count": len(pool), "sources": sorted({e["source"] for e in pool})},
        "firms": {
            "count": len(firm_profiles),
            "min_bids": profiles.MIN_BIDS,
            "distinct_names": len(profiles.name_index(firm_profiles)),
        },
        "model": {
            "trees": booster["num_trees"],
            "training_rows": int(dataset.train_x.shape[0]),
            "training_firms": len(dataset.train_groups),
        },
        "notes": MANIFEST_NOTES,
    }
    _write(directory / "manifest.json", manifest)
    LOGGER.info("Exported serving artifacts to %s", directory)
    return manifest


def _write(path: Path, payload: Any) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
        handle.write("\n")


def _main() -> None:
    parser = argparse.ArgumentParser(description="Export demo serving artifacts")
    parser.add_argument("--out", default=None)
    parser.add_argument("--db", default=None)
    parser.add_argument("--cutoff", default=SERVING_CUTOFF)
    parser.add_argument("--pool-limit", type=int, default=None)
    args = parser.parse_args()

    manifest = export(args.out, args.db, args.cutoff, args.pool_limit)
    print(json.dumps({k: v for k, v in manifest.items() if k != "feature_order"}, indent=2))
    print(f"features: {len(manifest['feature_order'])}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    _main()
