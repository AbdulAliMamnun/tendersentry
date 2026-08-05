"""Phase C: temporal splits, baselines, and the bid-propensity ranker.

Pre-registered before any model was fit:

* **Task** — ranking. For each firm, rank the tenders that closed in the test window;
  positives are the ones it actually bid on. Winning is reported separately and is
  never the training target: bidding is denser and far less biased.
* **Split** — temporal, always. Train strictly before a cutoff, evaluate strictly
  after. Firm-history features are computed as-of each tender's close date, so a
  firm's own future never informs its past.
* **Baselines**, in ascending order, evaluated identically:
  1. deterministic — the Stage-2 scoring logic, driven by history-derived profiles;
  2. embedding-only — cosine between the tender and the firm's history centroid;
  3. compact linear — embeddings plus the three strongest deterministic signals;
  4. LightGBM LambdaRank on the full feature set.
* **Metrics** — recall@10, recall@25, MRR, averaged over firms, broken out by
  experience cohort because cold-start is what a new signup actually is.

Scores are **bid propensity**. Nothing here estimates a probability of winning, and
the words do not appear in any output.
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

import config
from model import embeddings, features, metrics
from notices import db


LOGGER = logging.getLogger(__name__)

REPORT_DIR = Path(config.PROJECT_ROOT) / "eval" / "model"

#: Negative candidates sampled per firm. Ranking every firm against every test tender
#: is quadratic and unnecessary; sampled candidates are standard for ranking
#: evaluation, and the sample is seeded so runs are comparable.
NEGATIVES_PER_FIRM = 400
RANDOM_SEED = 20260801

#: Firms evaluated per split. Each is ranked against the entire test pool, so this
#: caps build time without making the ranking task artificially easy.
MAX_EVAL_FIRMS = 400

#: The three deterministic signals the compact baseline is allowed, chosen before
#: fitting anything: prior relationship with the buyer, category familiarity, region.
COMPACT_FEATURES = ("cross_buyer_prior_bids", "cross_category_share", "cross_region_share")

#: Known only once bidding has closed, so unusable at prediction time. It is kept
#: in the pre-registered feature set and reported, but the model that could ship is
#: the one fitted without it.
LEAKY_FEATURES = ("tender_bidder_count",)


@dataclass
class Split:
    """One pre-registered temporal split."""

    name: str
    cutoff: str
    test_end: str | None = None
    note: str = ""


#: **Standing rule: any externally-quoted metric names its split.**
#:
#: The default for anything that leaves this repository -- marketing copy, a guide, the
#: research page, a commit message -- is the PRIMARY split: recall@10 0.219, 2.33x the
#: deterministic baseline. Use another split only with a stated reason, and say which.
#:
#: This is not pedantry. "recall@10 0.219, 2.8x baseline" was quoted externally and is
#: two splits welded together: 0.219 is primary (2026-05-01, ratio 2.33x) while 2.82x is
#: settled (2025-10-01, where recall@10 is 0.217). Each figure is true; the pair
#: overstates the result, and a reader citing us would repeat it.

SPLITS = (
    Split("primary", "2026-05-01", None, "Approved split: test window is the period "
          "with complete bidder coverage in the original 12-week sample."),
    Split("strict", "2026-06-01", None, "Robustness check: test window sits entirely "
          "inside the original observation window."),
    Split("settled", "2025-10-01", "2026-04-01", "Robustness check added after the "
          "full history showed the recent edge is censored — bidder lists are "
          "published at award time, so the newest months under-report positives."),
)


@dataclass
class Dataset:
    """Assembled training and evaluation matrices for one split."""

    split: Split
    feature_names: list[str] = field(default_factory=list)
    train_x: np.ndarray = field(default_factory=lambda: np.zeros((0, 0)))
    train_y: np.ndarray = field(default_factory=lambda: np.zeros(0))
    train_groups: list[int] = field(default_factory=list)
    test_firms: list[str] = field(default_factory=list)
    test_x: list[np.ndarray] = field(default_factory=list)
    test_y: list[np.ndarray] = field(default_factory=list)
    test_train_counts: list[int] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


def build_dataset(
    interactions: list[features.Interaction],
    split: Split,
    negatives: int = NEGATIVES_PER_FIRM,
    seed: int = RANDOM_SEED,
    max_eval_firms: int = MAX_EVAL_FIRMS,
) -> Dataset:
    """Assemble features for one split, honouring the as-of rule throughout."""
    rng = random.Random(seed)
    train = [item for item in interactions if item.date < split.cutoff]
    test = [
        item
        for item in interactions
        if item.date >= split.cutoff
        and (split.test_end is None or item.date < split.test_end)
    ]
    if not train or not test:
        raise ValueError(f"Split {split.name} has an empty side")

    tenders: dict[str, dict] = {}
    bidder_counts: dict[str, int] = {}
    for item in interactions:
        entry = tenders.setdefault(
            item.ocid,
            {
                "ocid": item.ocid,
                "date": item.date,
                "buyer_id": item.buyer_id,
                "category": item.category,
                "region": item.region,
                "title": item.title,
                "value": None,
            },
        )
        if item.title and not entry["title"]:
            entry["title"] = item.title
        bidder_counts[item.ocid] = bidder_counts.get(item.ocid, 0) + 1
    for ocid, entry in tenders.items():
        entry["bidder_count"] = bidder_counts.get(ocid, 0)

    # Embeddings for every tender that appears anywhere, computed once.
    ordered_ocids = sorted(tenders)
    vectors = embeddings.embed([tenders[ocid]["title"] or "" for ocid in ordered_ocids])
    embedding_of = {ocid: vectors[index] for index, ocid in enumerate(ordered_ocids)}

    histories = features.build_histories(interactions, as_of=split.cutoff)
    train_counts = {firm: history.interactions for firm, history in histories.items()}

    # Firm centroids from training-period tenders only.
    firm_tenders: dict[str, list[str]] = {}
    for item in train:
        firm_tenders.setdefault(item.canonical_id, []).append(item.ocid)
    centroids = {
        firm: embeddings.centroid([embedding_of[ocid] for ocid in ocids])
        for firm, ocids in firm_tenders.items()
    }

    names = features.feature_names()
    dataset = Dataset(split=split, feature_names=names)

    # Indexed once. Scanning the interaction list per firm would be quadratic, and
    # with ~700k interactions that is the difference between a minute and an hour.
    train_by_firm: dict[str, set[str]] = {}
    for item in train:
        train_by_firm.setdefault(item.canonical_id, set()).add(item.ocid)

    # Training pairs: each firm's training bids as positives, sampled negatives from
    # the same period. Grouped per firm for the LambdaRank objective.
    train_pool = sorted({item.ocid for item in train})
    train_rows: list[np.ndarray] = []
    train_labels: list[int] = []
    groups: list[int] = []
    for firm in sorted(train_by_firm):
        if train_counts.get(firm, 0) < metrics.COHORTS[0][0]:
            continue
        positives = train_by_firm[firm]
        sampled = rng.sample(train_pool, min(len(train_pool), negatives))
        candidates = list(
            dict.fromkeys(sorted(positives) + [o for o in sampled if o not in positives])
        )
        history = histories.get(firm)
        firm_centroid = centroids.get(firm)
        for ocid in candidates:
            train_rows.append(
                _vector(names, history, tenders[ocid], firm_centroid,
                        embedding_of[ocid], split.cutoff)
            )
            train_labels.append(1 if ocid in positives else 0)
        groups.append(len(candidates))

    dataset.train_x = np.vstack(train_rows) if train_rows else np.zeros((0, len(names)))
    dataset.train_y = np.array(train_labels, dtype=np.int32)
    dataset.train_groups = groups

    # Evaluation: firms with at least five test-period bids and some training history.
    test_by_firm: dict[str, set[str]] = {}
    for item in test:
        test_by_firm.setdefault(item.canonical_id, set()).add(item.ocid)
    test_pool = sorted({item.ocid for item in test})

    # Evaluation ranks each firm against the **whole** test pool, not a sample.
    # Sampled negatives make the task far easier than production — a firm ranked
    # against 200 candidates scores nothing like one ranked against every open
    # tender — and would inflate every metric in this report.
    eligible = [
        firm
        for firm in sorted(test_by_firm)
        if len(test_by_firm[firm]) >= metrics.COHORTS[0][0]
        and train_counts.get(firm, 0) >= metrics.COHORTS[0][0]
    ]
    # Building every row is O(firms x pool); cap the firm sample rather than the
    # candidate list, so each firm still faces the real ranking problem.
    if len(eligible) > max_eval_firms:
        eligible = rng.sample(eligible, max_eval_firms)
        eligible.sort()

    for firm in eligible:
        positives = test_by_firm[firm]
        candidates = test_pool
        history = histories.get(firm)
        rows = np.vstack(
            [
                _vector(names, history, tenders[ocid], centroids.get(firm),
                        embedding_of[ocid], split.cutoff)
                for ocid in candidates
            ]
        )
        dataset.test_firms.append(firm)
        dataset.test_x.append(rows)
        dataset.test_y.append(
            np.array([1 if ocid in positives else 0 for ocid in candidates], dtype=np.int32)
        )
        dataset.test_train_counts.append(train_counts.get(firm, 0))

    dataset.stats = {
        "train_interactions": len(train),
        "test_interactions": len(test),
        "train_firms_used": len(groups),
        "test_firms_evaluated": len(dataset.test_firms),
        "train_rows": int(dataset.train_x.shape[0]),
        "test_pool": len(test_pool),
        "eval_protocol": "each firm ranked against the full test pool",
    }
    LOGGER.info("Split %s: %s", split.name, dataset.stats)
    return dataset


def _vector(
    names: list[str],
    history: features.FirmHistory | None,
    tender: dict,
    centroid: np.ndarray | None,
    tender_embedding: np.ndarray,
    as_of: str,
) -> np.ndarray:
    values = {
        **features.firm_features(history, as_of),
        **features.tender_features(tender),
        **features.cross_features(history, tender, centroid, tender_embedding),
    }
    return np.array([values.get(name, 0.0) for name in names], dtype=np.float32)


def score_deterministic(dataset: Dataset, rows: np.ndarray) -> np.ndarray:
    """Baseline 1: the Stage-2 scoring shape, driven by history-derived profiles.

    Not a call into ``matchrec`` — these firms have no curated profile — but the same
    weighting idea: trade familiarity dominates, then buyer and region, then recency.
    """
    index = {name: position for position, name in enumerate(dataset.feature_names)}
    category = rows[:, index["cross_category_share"]]
    buyer = np.minimum(rows[:, index["cross_buyer_prior_bids"]], 5.0) / 5.0
    region = rows[:, index["cross_region_share"]]
    return 45.0 * category + 20.0 * buyer + 20.0 * region


def score_embedding(dataset: Dataset, rows: np.ndarray) -> np.ndarray:
    """Baseline 2: semantic similarity to the firm's history, and nothing else."""
    index = {name: position for position, name in enumerate(dataset.feature_names)}
    return rows[:, index["cross_embedding_similarity"]]


def fit_compact(dataset: Dataset) -> Any:
    """Baseline 3: embeddings plus three deterministic signals, logistic."""
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    from sklearn.pipeline import make_pipeline

    columns = _compact_columns(dataset)
    model = make_pipeline(
        StandardScaler(), LogisticRegression(max_iter=2000, class_weight="balanced")
    )
    model.fit(dataset.train_x[:, columns], dataset.train_y)
    return model


def _compact_columns(dataset: Dataset) -> list[int]:
    index = {name: position for position, name in enumerate(dataset.feature_names)}
    wanted = ["cross_embedding_similarity", *COMPACT_FEATURES]
    return [index[name] for name in wanted if name in index]


def fit_gbm(dataset: Dataset, columns: list[int] | None = None) -> Any:
    """Baseline 4: LightGBM LambdaRank, optionally on a column subset."""
    import lightgbm as lgb

    ranker = lgb.LGBMRanker(
        objective="lambdarank",
        n_estimators=300,
        learning_rate=0.05,
        num_leaves=31,
        min_child_samples=20,
        random_state=RANDOM_SEED,
        verbose=-1,
    )
    used = list(range(len(dataset.feature_names))) if columns is None else columns
    ranker.fit(
        dataset.train_x[:, used],
        dataset.train_y,
        group=dataset.train_groups,
        feature_name=[dataset.feature_names[i] for i in used],
    )
    return ranker


def evaluate_model(dataset: Dataset, scorer: Any) -> dict:
    """Score every evaluated firm's candidate list and aggregate."""
    results = [
        metrics.FirmResult(
            firm_id=firm,
            train_interactions=count,
            scores=np.asarray(scorer(rows), dtype=np.float64).ravel(),
            labels=labels,
        )
        for firm, rows, labels, count in zip(
            dataset.test_firms,
            dataset.test_x,
            dataset.test_y,
            dataset.test_train_counts,
        )
    ]
    return metrics.evaluate(results)


def run_split(
    interactions: list[features.Interaction], split: Split, negatives: int,
    max_eval_firms: int = MAX_EVAL_FIRMS,
) -> dict:
    """Fit and evaluate every baseline for one split."""
    dataset = build_dataset(interactions, split, negatives, RANDOM_SEED, max_eval_firms)
    outcome: dict[str, Any] = {
        "split": {
            "name": split.name,
            "cutoff": split.cutoff,
            "test_end": split.test_end,
            "note": split.note,
        },
        "stats": dataset.stats,
        "models": {},
    }
    if not dataset.test_firms:
        LOGGER.warning("Split %s evaluated no firms", split.name)
        return outcome

    outcome["models"]["1_deterministic"] = evaluate_model(
        dataset, lambda rows: score_deterministic(dataset, rows)
    )
    outcome["models"]["2_embedding_only"] = evaluate_model(
        dataset, lambda rows: score_embedding(dataset, rows)
    )

    compact = fit_compact(dataset)
    columns = _compact_columns(dataset)
    outcome["models"]["3_compact_linear"] = evaluate_model(
        dataset, lambda rows: compact.predict_proba(rows[:, columns])[:, 1]
    )

    gbm = fit_gbm(dataset)
    outcome["models"]["4_gbm_lambdarank"] = evaluate_model(dataset, gbm.predict)
    outcome["feature_importance"] = _importance(gbm, dataset, None)

    # The shippable variant: same model without features that cannot exist at
    # prediction time.
    clean = [
        index
        for index, name in enumerate(dataset.feature_names)
        if name not in LEAKY_FEATURES
    ]
    gbm_clean = fit_gbm(dataset, clean)
    outcome["models"]["5_gbm_no_leakage"] = evaluate_model(
        dataset, lambda rows: gbm_clean.predict(rows[:, clean])
    )
    outcome["feature_importance_no_leakage"] = _importance(gbm_clean, dataset, clean)
    outcome["leaky_features_excluded"] = list(LEAKY_FEATURES)
    return outcome


def _importance(gbm: Any, dataset: Dataset, columns: list[int] | None = None) -> dict:
    """Gain-based importance, plus group-level totals.

    This ranking is the deliverable that outlives the model: it is the shortlist of
    what an extraction step would need to pull out of a tender document.
    """
    gains = gbm.booster_.feature_importance(importance_type="gain")
    names = (
        dataset.feature_names
        if columns is None
        else [dataset.feature_names[i] for i in columns]
    )
    ranked = sorted(
        ({"feature": name, "gain": float(gain), "group": features.group_of(name)}
         for name, gain in zip(names, gains)),
        key=lambda item: -item["gain"],
    )
    totals: dict[str, float] = {}
    for entry in ranked:
        totals[entry["group"]] = totals.get(entry["group"], 0.0) + entry["gain"]
    total = sum(totals.values()) or 1.0
    return {
        "by_feature": ranked,
        "by_group": {
            group: {"gain": value, "share": round(100.0 * value / total, 1)}
            for group, value in sorted(totals.items(), key=lambda item: -item[1])
        },
    }


def main(
    db_path: Any = None,
    negatives: int = NEGATIVES_PER_FIRM,
    splits: tuple[Split, ...] = SPLITS,
) -> Path:
    """Run every split and write a timestamped report."""
    connection = db.connect(db_path)
    try:
        interactions = features.load_interactions(connection)
    finally:
        connection.close()

    bidders: dict[str, int] = {}
    for item in interactions:
        bidders[item.ocid] = bidders.get(item.ocid, 0) + 1

    report: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "embedding_model": embeddings.MODEL_NAME,
        "negatives_per_firm": negatives,
        "random_seed": RANDOM_SEED,
        "corpus": {
            "competitive_interactions": len(interactions),
            "competitive_procurements": len(bidders),
        },
        "splits": [],
    }
    for split in splits:
        try:
            report["splits"].append(run_split(interactions, split, negatives))
        except ValueError as exc:
            LOGGER.error("Skipping split %s: %s", split.name, exc)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = report["generated_at"].replace(":", "").replace("-", "")
    path = REPORT_DIR / f"model-report-{stamp}.json"
    with path.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    LOGGER.info("Wrote %s", path)
    _print_report(report)
    return path


def _print_report(report: dict) -> None:
    print(f"\nembedding model: {report['embedding_model']}")
    print(f"competitive interactions: {report['corpus']['competitive_interactions']:,}")
    for split in report["splits"]:
        info = split["split"]
        print(f"\n{'=' * 74}")
        print(f"SPLIT {info['name']}  train < {info['cutoff']}"
              + (f"  test < {info['test_end']}" if info["test_end"] else ""))
        print(f"  {split['stats']}")
        for name, evaluation in split.get("models", {}).items():
            print()
            print(metrics.format_table(f"  {name}", evaluation))
        importance = split.get("feature_importance")
        if importance:
            print("\n  feature importance by group:")
            for group, entry in importance["by_group"].items():
                print(f"    {group:<8} {entry['share']:>5.1f}%")
            print("  top features by gain:")
            for entry in importance["by_feature"][:12]:
                print(f"    {entry['gain']:>12,.0f}  {entry['feature']}")


def _main() -> None:
    parser = argparse.ArgumentParser(description="Train and evaluate the ranker")
    parser.add_argument("--db", default=None)
    parser.add_argument("--negatives", type=int, default=NEGATIVES_PER_FIRM)
    parser.add_argument("--split", default=None, help="run one split by name")
    args = parser.parse_args()

    chosen = SPLITS
    if args.split:
        chosen = tuple(item for item in SPLITS if item.name == args.split)
        if not chosen:
            raise SystemExit(f"Unknown split {args.split}")
    main(args.db, args.negatives, chosen)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    _main()
