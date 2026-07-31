"""Stage 1: deterministic hard filters with auditable exclusion reasons.

Every notice a firm does not see must be explainable by a reason recorded here.
Filters also return the context their decisions were based on (region kind, trade
affinity, runway), so Stage 2 scores exactly what Stage 1 saw instead of
recomputing it and risking drift.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from matchrec import timeutil
from profiles import vocabulary


LOGGER = logging.getLogger(__name__)

REASON_CLOSED = "closed"
REASON_CLOSING_SOON = "closing_within_24h"
REASON_NO_CLOSING_DATE = "closing_date_missing"
#: The source published no closing date, so whether the notice is live is unknown.
#: Still excluded — an undated notice is never recommended — but not reported as
#: "closed", which claimed knowledge we do not have and hid the real cause of the
#: municipal ingest yielding zero candidates.
REASON_CLOSING_DATE_UNKNOWN = "closing_date_unknown"
REASON_NON_CONSTRUCTION = "non_construction"
REASON_TRADE_MISMATCH = "trade_mismatch"
REASON_REGION_MISMATCH = "region_mismatch"
REASON_VALUE_OUT_OF_RANGE = "value_out_of_range"

FLAG_REGION_UNKNOWN = "region_unknown"
FLAG_VALUE_UNKNOWN = "value_unknown"
FLAG_TRADE_UNMAPPED = "trade_unmapped"
FLAG_TRADE_FAMILY_ONLY = "trade_family_only"

#: Region relationships a surviving notice can have to a firm's coverage.
REGION_SINGLE_PROVINCE = "single_province_match"
REGION_MULTI_PROVINCE = "multi_province_or_national"
REGION_UNKNOWN = "unknown"

TRADE_EXACT = "exact"
TRADE_FAMILY = "family"
TRADE_UNMAPPED = "unmapped"
TRADE_NONE = "none"

#: Whether a trade match rests on the notice's own summary (title or category) or
#: only on description prose, which scoring discounts.
EVIDENCE_STRONG = "title"
EVIDENCE_DESCRIPTION = "description"

DEFAULT_MIN_HOURS_TO_CLOSING = 24.0


def evaluate(
    notice: dict,
    firm: dict,
    mapping: Any,
    now: datetime | None = None,
    min_hours_to_closing: float = DEFAULT_MIN_HOURS_TO_CLOSING,
) -> dict:
    """Decide whether a firm should see one notice, and why.

    Returns ``{"included", "reasons", "flags", "detail", "context"}``. ``reasons``
    lists every exclusion that applies, not just the first, so the audit trail
    answers "why not" completely.
    """
    reference = now or timeutil.now_utc()
    reasons: list[str] = []
    flags: list[str] = []
    details: list[str] = []

    hours = timeutil.hours_until(notice.get("closing_date_utc"), reference)
    status = str(notice.get("status") or "").strip().casefold()
    if status in {"", "unknown"}:
        reasons.append(REASON_CLOSING_DATE_UNKNOWN)
        details.append("the source published no closing date, so liveness is unknown")
    elif status != "open":
        reasons.append(REASON_CLOSED)
        details.append(f"status={status}")
    if hours is None:
        reasons.append(REASON_NO_CLOSING_DATE)
        details.append("closing_date_utc is null")
    elif hours <= min_hours_to_closing:
        reasons.append(REASON_CLOSING_SOON)
        # Deliberately not the live hours remaining: a clock reading in the stored
        # detail would rewrite this row on every run and turn an unchanged corpus
        # into permanent churn.
        details.append(
            "closing date has passed"
            if hours <= 0
            else f"closes inside the {min_hours_to_closing:g}h floor"
        )

    trade = _evaluate_trades(notice, firm, mapping)
    if trade["reason"]:
        reasons.append(trade["reason"])
        details.append(trade["detail"])
    flags.extend(trade["flags"])

    region = _evaluate_region(notice, firm)
    if region["reason"]:
        reasons.append(region["reason"])
        details.append(region["detail"])
    flags.extend(region["flags"])

    value = _evaluate_value(notice, firm)
    if value["reason"]:
        reasons.append(value["reason"])
        details.append(value["detail"])
    flags.extend(value["flags"])

    return {
        "included": not reasons,
        "reasons": reasons,
        "flags": flags,
        "detail": "; ".join(details) or None,
        "context": {
            "hours_to_close": hours,
            "days_to_close": None if hours is None else hours / 24.0,
            "region_kind": region["kind"],
            "trade_affinity_kind": trade["kind"],
            "trade_overlap": trade["overlap"],
            "matched_trades": trade["matched"],
            "trade_evidence": trade["evidence"],
            "construction_coded": bool(notice.get("construction_marked")),
        },
    }


def _evaluate_trades(notice: dict, firm: dict, mapping: Any) -> dict:
    """Compare a notice's trade slugs against a firm's trades."""
    status = str(notice.get("mapping_status") or vocabulary.TRADE_STATUS_UNMAPPED)
    notice_slugs = [str(slug) for slug in notice.get("trade_slugs") or []]
    firm_trades = [str(slug) for slug in firm.get("trades") or []]

    if status == vocabulary.TRADE_STATUS_NON_CONSTRUCTION:
        return {
            "kind": TRADE_NONE,
            "reason": REASON_NON_CONSTRUCTION,
            "detail": "category maps outside construction",
            "flags": [],
            "overlap": 0,
            "matched": [],
            "evidence": EVIDENCE_STRONG,
        }

    if status != vocabulary.TRADE_STATUS_MAPPED or not notice_slugs:
        # Unknown work type: kept on purpose, flagged so it is never mistaken for
        # a confirmed trade match. Scoring credits it less when the source filed it
        # under a goods or services code than when it is construction-coded.
        return {
            "kind": TRADE_UNMAPPED,
            "reason": None,
            "detail": "",
            "flags": [FLAG_TRADE_UNMAPPED],
            "overlap": 0,
            "matched": [],
            "evidence": EVIDENCE_STRONG,
        }

    sources = notice.get("slug_sources") or {}

    def evidence(slugs: list[str]) -> str:
        """Strongest evidence behind any of the matched slugs.

        A slug missing from ``slug_sources`` is treated as strong: the discount is
        for knowing the evidence was description-only, not for lacking the record.
        """
        return (
            EVIDENCE_STRONG
            if any(
                str(sources.get(slug) or EVIDENCE_STRONG) != EVIDENCE_DESCRIPTION
                for slug in slugs
            )
            else EVIDENCE_DESCRIPTION
        )

    exact = [slug for slug in notice_slugs if slug in firm_trades]
    if exact:
        return {
            "kind": TRADE_EXACT,
            "reason": None,
            "detail": "",
            "flags": [],
            "overlap": len(exact),
            "matched": exact,
            "evidence": evidence(exact),
        }

    # A firm's trade and the notice's trade in one family (both civil, say) is not
    # an obvious mismatch, so it survives with a flag. Without this, the family
    # affinity weight in Stage 2 could never apply to anything.
    family = [
        slug
        for slug in notice_slugs
        if any(mapping.shares_family(slug, trade) for trade in firm_trades)
    ]
    if family:
        return {
            "kind": TRADE_FAMILY,
            "reason": None,
            "detail": "",
            "flags": [FLAG_TRADE_FAMILY_ONLY],
            "overlap": len(family),
            "matched": family,
            "evidence": evidence(family),
        }

    return {
        "kind": TRADE_NONE,
        "reason": REASON_TRADE_MISMATCH,
        "detail": f"notice trades {notice_slugs} vs firm trades {firm_trades}",
        "flags": [],
        "overlap": 0,
        "matched": [],
        "evidence": EVIDENCE_STRONG,
    }


def _evaluate_region(notice: dict, firm: dict) -> dict:
    """Compare a notice's province coverage against a firm's regions.

    ``notices`` stores region at province granularity, so a county-level firm
    profile can only ever be matched province-to-province. A province-wide or
    nationwide notice is therefore a partial match, not an exact one.
    """
    firm_regions = [str(slug) for slug in firm.get("regions") or []]
    firm_provinces = vocabulary.provinces_for_regions(firm_regions)
    raw = str(notice.get("region") or "").strip()

    if not raw:
        return {
            "kind": REGION_UNKNOWN,
            "reason": None,
            "detail": "",
            "flags": [FLAG_REGION_UNKNOWN],
        }

    notice_provinces = {part.strip().upper() for part in raw.split(",") if part.strip()}
    if "CA" in notice_provinces:
        return {"kind": REGION_MULTI_PROVINCE, "reason": None, "detail": "", "flags": []}

    overlap = notice_provinces & firm_provinces
    if overlap and len(notice_provinces) == 1:
        return {"kind": REGION_SINGLE_PROVINCE, "reason": None, "detail": "", "flags": []}
    if overlap:
        return {"kind": REGION_MULTI_PROVINCE, "reason": None, "detail": "", "flags": []}

    buyer_type = vocabulary.normalize_buyer_type(notice.get("buyer_type"))
    if vocabulary.REGION_FEDERAL_ANY in firm_regions and buyer_type == "federal":
        return {"kind": REGION_MULTI_PROVINCE, "reason": None, "detail": "", "flags": []}

    return {
        "kind": None,
        "reason": REASON_REGION_MISMATCH,
        "detail": f"notice region {sorted(notice_provinces)} vs firm {firm_regions}",
        "flags": [],
    }


def _evaluate_value(notice: dict, firm: dict) -> dict:
    """Compare a notice's estimated value against a firm's value band."""
    value = notice.get("estimated_value")
    if value is None:
        return {"reason": None, "detail": "", "flags": [FLAG_VALUE_UNKNOWN]}
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return {"reason": None, "detail": "", "flags": [FLAG_VALUE_UNKNOWN]}

    minimum = firm.get("value_min")
    maximum = firm.get("value_max")
    if minimum is not None and amount < float(minimum):
        return {
            "reason": REASON_VALUE_OUT_OF_RANGE,
            "detail": f"value {amount:,.0f} below firm minimum {float(minimum):,.0f}",
            "flags": [],
        }
    if maximum is not None and amount > float(maximum):
        return {
            "reason": REASON_VALUE_OUT_OF_RANGE,
            "detail": f"value {amount:,.0f} above firm maximum {float(maximum):,.0f}",
            "flags": [],
        }
    return {"reason": None, "detail": "", "flags": []}
