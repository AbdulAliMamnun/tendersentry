"""Ranking metrics, reported per firm and broken out by experience cohort.

Averaging over firms rather than over interactions is deliberate: a handful of very
active bidders would otherwise dominate the score, and the model would look good
because it had learned Colas and EXP. Every metric here is a mean over firms.

The cohort split is the number that matters commercially. A model will always rank
better for a firm with fifty observed bids than for one with five, and **a new
TenderSentry signup is the cold-start case by definition** — so the report shows how
performance degrades toward that end rather than hiding it inside an average.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable, Sequence

import numpy as np


LOGGER = logging.getLogger(__name__)

#: Cohorts by number of *training* interactions. The lowest band is the one a new
#: customer lands in.
COHORTS = ((5, 19), (20, 49), (50, None))

RECALL_KS = (10, 25)


@dataclass
class FirmResult:
    """One firm's ranked candidate list for the test period."""

    firm_id: str
    train_interactions: int
    scores: np.ndarray
    labels: np.ndarray

    @property
    def positives(self) -> int:
        return int(self.labels.sum())


def recall_at_k(scores: np.ndarray, labels: np.ndarray, k: int) -> float:
    """Share of a firm's actual bids that appear in the top k of its ranking."""
    total = float(labels.sum())
    if total == 0:
        return float("nan")
    order = np.argsort(-scores, kind="stable")
    return float(labels[order][:k].sum() / total)


def reciprocal_rank(scores: np.ndarray, labels: np.ndarray) -> float:
    """1/rank of the first actual bid in the ranking."""
    if labels.sum() == 0:
        return float("nan")
    order = np.argsort(-scores, kind="stable")
    ranked = labels[order]
    hits = np.flatnonzero(ranked)
    return float(1.0 / (hits[0] + 1)) if hits.size else 0.0


def cohort_of(train_interactions: int) -> str | None:
    """Which experience cohort a firm belongs to, or None if below the floor."""
    for low, high in COHORTS:
        if train_interactions >= low and (high is None or train_interactions <= high):
            return f">={low}" if high is None else f"{low}-{high}"
    return None


def evaluate(results: Sequence[FirmResult]) -> dict:
    """Aggregate per-firm metrics overall and by cohort."""
    usable = [item for item in results if item.positives > 0 and item.scores.size > 0]
    if not usable:
        LOGGER.warning("No firm had a positive in the test period; metrics undefined")
        return {"firms": 0, "overall": {}, "cohorts": {}}

    def summarize(subset: Iterable[FirmResult]) -> dict:
        subset = list(subset)
        if not subset:
            return {"firms": 0}
        summary: dict = {
            "firms": len(subset),
            "median_candidates": float(
                np.median([item.scores.size for item in subset])
            ),
            "median_positives": float(np.median([item.positives for item in subset])),
        }
        for k in RECALL_KS:
            values = [recall_at_k(item.scores, item.labels, k) for item in subset]
            summary[f"recall@{k}"] = float(np.nanmean(values))
        summary["mrr"] = float(
            np.nanmean([reciprocal_rank(item.scores, item.labels) for item in subset])
        )
        return summary

    cohorts: dict[str, dict] = {}
    for low, high in COHORTS:
        label = f">={low}" if high is None else f"{low}-{high}"
        cohorts[label] = summarize(
            item for item in usable if cohort_of(item.train_interactions) == label
        )

    return {
        "firms": len(usable),
        "overall": summarize(usable),
        "cohorts": cohorts,
    }


def format_table(name: str, evaluation: dict) -> str:
    """Render one model's metrics as a fixed-width block for the report."""
    lines = [f"{name}"]
    overall = evaluation.get("overall") or {}
    if not overall:
        return f"{name}: no evaluable firms"
    header = f"  {'cohort':<10}{'firms':>7}{'recall@10':>11}{'recall@25':>11}{'MRR':>9}"
    lines.append(header)
    lines.append(f"  {'overall':<10}{overall['firms']:>7}"
                 f"{overall['recall@10']:>11.3f}{overall['recall@25']:>11.3f}"
                 f"{overall['mrr']:>9.3f}")
    for label, cohort in (evaluation.get("cohorts") or {}).items():
        if not cohort.get("firms"):
            lines.append(f"  {label:<10}{0:>7}{'—':>11}{'—':>11}{'—':>9}")
            continue
        lines.append(
            f"  {label:<10}{cohort['firms']:>7}"
            f"{cohort['recall@10']:>11.3f}{cohort['recall@25']:>11.3f}"
            f"{cohort['mrr']:>9.3f}"
        )
    return "\n".join(lines)
