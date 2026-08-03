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
from model import embeddings, features, train
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
]


def open_pool(connection: Any, limit: int | None = None) -> list[dict]:
    """Every currently open tender, with the display fields the boards use."""
    query = (
        "SELECT t.id, t.source, t.source_id, t.title, t.buyer_name, t.buyer_type, "
        "       t.region, t.estimated_value, t.closing_date_utc, t.notice_url, "
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
