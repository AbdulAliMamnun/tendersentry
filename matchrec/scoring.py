"""Stage 2: deterministic 0-100 scoring with an explainable component breakdown.

The base score is composed from the four components every notice has, so any two
base scores are directly comparable. Estimated value — present on well under 1% of
notices — is applied afterwards as a bounded, separately stored modifier of
-10..+10 points, which keeps the base comparable and makes the bonus legible in the
UI ("78 (+6 value fit)"). Future sparse signals should follow the same pattern.
"""

from __future__ import annotations

import json
import logging
import math
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any

import config
from matchrec import filters
from profiles import schema as profiles_schema
from profiles import vocabulary


LOGGER = logging.getLogger(__name__)

WEIGHTS_PATH = Path(config.PROJECT_ROOT) / "matchrec" / "weights.json"

BASE_COMPONENTS = (
    "trade_match",
    "region_match",
    "buyer_type_preference",
    "recency_urgency",
)

FLAG_VALUE_BASELINE_UNKNOWN = "value_baseline_unknown"
FLAG_LONG_HORIZON = "long_horizon"


def load_weights(path: Path | str | None = None) -> dict:
    """Load and validate the scoring configuration."""
    source = Path(path) if path else WEIGHTS_PATH
    with source.open(encoding="utf-8") as handle:
        weights = json.load(handle)

    components = weights.get("components") or {}
    missing = [name for name in BASE_COMPONENTS if name not in components]
    if missing:
        raise ValueError(f"weights file is missing component(s): {missing}")
    unexpected = sorted(set(components) - set(BASE_COMPONENTS))
    if unexpected:
        raise ValueError(
            f"weights file has unknown component(s): {unexpected}. Value fit is a "
            "modifier, not a base component."
        )
    total = sum(float(value) for value in components.values())
    if abs(total - 100.0) > 1e-6:
        raise ValueError(f"component weights must sum to 100, got {total}")
    if not weights.get("recency_curve"):
        raise ValueError("weights file is missing recency_curve")
    weights.setdefault("version", "unversioned")
    return weights


def min_hours_to_closing(weights: dict) -> float:
    """Read the closing-runway floor Stage 1 should apply."""
    return float(
        (weights.get("filters") or {}).get(
            "min_hours_to_closing", filters.DEFAULT_MIN_HOURS_TO_CLOSING
        )
    )


def score_notice(
    notice: dict,
    firm: dict,
    context: dict,
    weights: dict,
    now: datetime | None = None,
) -> dict:
    """Score one surviving notice and return its full breakdown."""
    del now  # Runway already resolved by Stage 1; kept for signature stability.
    component_weights = weights["components"]
    components: dict[str, dict] = {}

    trade_score, trade_detail = _trade_component(context, weights)
    components["trade_match"] = _component(
        trade_score, component_weights["trade_match"], trade_detail
    )

    region_score, region_detail = _region_component(context, weights)
    components["region_match"] = _component(
        region_score, component_weights["region_match"], region_detail
    )

    buyer_score, buyer_detail = _buyer_component(notice, firm, weights)
    components["buyer_type_preference"] = _component(
        buyer_score, component_weights["buyer_type_preference"], buyer_detail
    )

    recency_score, recency_detail = _recency_component(context, weights)
    components["recency_urgency"] = _component(
        recency_score, component_weights["recency_urgency"], recency_detail
    )

    base_score = round(sum(part["points"] for part in components.values()), 2)
    modifier, modifier_detail, modifier_flags = value_modifier(notice, firm, weights)
    final_score = round(min(100.0, max(0.0, base_score + modifier)), 2)

    flags = list(modifier_flags)
    days = context.get("days_to_close")
    horizon = float(weights.get("long_horizon_days", 180))
    if days is not None and float(days) > horizon:
        # Standing offers and prequalification lists close months or years out; the
        # UI badges them rather than presenting them as live opportunities.
        flags.append(FLAG_LONG_HORIZON)

    return {
        "base_score": base_score,
        "value_modifier": modifier,
        "final_score": final_score,
        "components": components,
        "value_detail": modifier_detail,
        "flags": flags,
    }


def value_modifier(
    notice: dict, firm: dict, weights: dict
) -> tuple[float, str, list[str]]:
    """Return the bounded value-fit adjustment, its explanation, and any flags.

    Zero — never a penalty — when the notice publishes no value or the firm has no
    past-project baseline to compare against, so an absent signal cannot move a
    ranking in either direction.
    """
    settings = weights.get("value_modifier") or {}
    max_points = float(settings.get("max_points", 10))
    sigma_ratio = float(settings.get("sigma_ratio", 0.6))

    value = notice.get("estimated_value")
    if value is None:
        return 0.0, "no published value", []
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return 0.0, "unreadable published value", []

    baseline_values = profiles_schema.past_project_values(firm)
    if not baseline_values:
        return (
            0.0,
            "firm has no past-project values to compare against",
            [FLAG_VALUE_BASELINE_UNKNOWN],
        )

    baseline = statistics.median(baseline_values)
    sigma = max(sigma_ratio * baseline, 1.0)
    fit = math.exp(-0.5 * ((amount - baseline) / sigma) ** 2)
    modifier = round((2.0 * fit - 1.0) * max_points, 2)
    return (
        modifier,
        f"value {amount:,.0f} vs past-project median {baseline:,.0f} "
        f"(fit {fit:.2f})",
        [],
    )


def _component(score: float, weight: Any, detail: str) -> dict:
    bounded = min(1.0, max(0.0, float(score)))
    weight_value = float(weight)
    return {
        "score": round(bounded, 4),
        "weight": weight_value,
        "points": round(bounded * weight_value, 2),
        "detail": detail,
    }


def _trade_component(context: dict, weights: dict) -> tuple[float, str]:
    affinity = weights.get("trade_affinity") or {}
    kind = str(context.get("trade_affinity_kind") or filters.TRADE_NONE)

    if kind == filters.TRADE_UNMAPPED and not context.get("construction_coded"):
        # A notice the source filed under goods or services that no trade rule
        # recognized is much weaker evidence than an unclassified construction
        # notice, so it earns less benefit of the doubt.
        base = float(
            affinity.get(
                "unmapped_non_construction_coded", affinity.get("unmapped", 0.0)
            )
        )
    else:
        base = float(affinity.get(kind, affinity.get("none", 0.0)))

    overlap = int(context.get("trade_overlap") or 0)
    bonus = 0.0
    if kind in {filters.TRADE_EXACT, filters.TRADE_FAMILY} and overlap > 1:
        bonus = min(
            float(affinity.get("max_bonus", 0.0)),
            (overlap - 1) * float(affinity.get("additional_overlap_bonus", 0.0)),
        )

    matched = context.get("matched_trades") or []
    if kind == filters.TRADE_EXACT:
        detail = f"firm trade match on {', '.join(matched)}"
    elif kind == filters.TRADE_FAMILY:
        detail = f"same trade family as firm work: {', '.join(matched)}"
    elif kind == filters.TRADE_UNMAPPED:
        detail = "work type could not be classified from the notice text"
        if not context.get("construction_coded"):
            detail += " and the source filed it outside construction"
    else:
        detail = "no trade relationship"
    if bonus:
        detail += f" (+{bonus:.2f} for {overlap} overlapping trades)"

    score = base + bonus
    if (
        kind in {filters.TRADE_EXACT, filters.TRADE_FAMILY}
        and str(context.get("trade_evidence")) == filters.EVIDENCE_DESCRIPTION
    ):
        multiplier = float(affinity.get("description_only_multiplier", 1.0))
        score *= multiplier
        detail += (
            f" — discounted to {multiplier:g}x: the matching trade appears only in "
            "the description, not the title or category"
        )
    return score, detail


def _region_component(context: dict, weights: dict) -> tuple[float, str]:
    scores = weights.get("region_scores") or {}
    kind = str(context.get("region_kind") or filters.REGION_UNKNOWN)
    if kind == filters.REGION_SINGLE_PROVINCE:
        return float(scores.get("single_province_match", 1.0)), "notice province matches"
    if kind == filters.REGION_UNKNOWN:
        return float(scores.get("unknown", 0.5)), "notice publishes no region"
    return (
        float(scores.get("multi_province_or_national", 0.5)),
        "multi-province or nationwide notice; county-level fit unknown",
    )


def _buyer_component(notice: dict, firm: dict, weights: dict) -> tuple[float, str]:
    scores = weights.get("buyer_type_scores") or {}
    preferences = [str(item) for item in firm.get("buyer_type_preferences") or []]
    buyer_type = vocabulary.normalize_buyer_type(notice.get("buyer_type"))
    if buyer_type is None:
        return float(scores.get("unknown", 0.5)), "buyer type unknown"
    if buyer_type in preferences:
        return float(scores.get("preferred", 1.0)), f"{buyer_type} is a preferred buyer"
    return (
        float(scores.get("other", 0.4)),
        f"{buyer_type} is outside the firm's stated preferences",
    )


def _recency_component(context: dict, weights: dict) -> tuple[float, str]:
    days = context.get("days_to_close")
    if days is None:
        return 0.0, "no closing date"
    cap = max(
        (
            float(step["max_days"])
            for step in weights["recency_curve"]
            if step.get("max_days") is not None
        ),
        default=None,
    )
    for step in weights["recency_curve"]:
        limit = step.get("max_days")
        if limit is None or float(days) < float(limit):
            detail = f"{float(days):.1f} days of runway"
            if limit is not None and float(limit) <= 5:
                detail += " (short-runway penalty)"
            elif limit is None and cap is not None:
                detail += f" (beyond the {cap:g}-day cap: not an actionable deadline)"
            return float(step["score"]), detail
    return 0.0, f"{float(days):.1f} days of runway"
