"""Phase B: features for (firm, tender) pairs, computed strictly as-of a date.

**The whole file turns on one rule: a feature describing a firm may only use
interactions that closed strictly before the tender being scored.** A firm-history
feature built from the full table would encode the answer — the model would "know"
the firm bid on this tender because that bid is in its own history. Every history
function therefore takes an ``as_of`` date and filters on it, and
``tests/test_model_features.py`` asserts the filtering rather than trusting it.

Three ablatable groups:

* ``firm``   — what this firm has done before: categories, buyers, regions, sizes,
  cadence, win rate, recency.
* ``tender`` — what this opportunity is: category, buyer, region, value if published,
  and a sentence-embedding of its title.
* ``cross``  — how the two relate: category overlap, value fit, region match, prior
  relationship with this buyer, and embedding similarity to the firm's history.

Embeddings are computed locally; nothing here calls a hosted API.
"""

from __future__ import annotations

import logging
import math
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable

import numpy as np


LOGGER = logging.getLogger(__name__)

FEATURE_GROUPS = ("firm", "tender", "cross")

#: Bands used to describe the size of work a firm bids on, in CAD.
VALUE_BANDS = (50_000, 250_000, 1_000_000, 5_000_000)


@dataclass
class Interaction:
    """One observed firm↔procurement event."""

    canonical_id: str
    ocid: str
    date: str
    won: int
    bid_amount: float | None
    buyer_id: str | None
    category: str | None
    region: str | None
    title: str | None


@dataclass
class FirmHistory:
    """A firm's behaviour up to, and excluding, a cutoff date."""

    interactions: int = 0
    wins: int = 0
    categories: Counter = field(default_factory=Counter)
    buyers: Counter = field(default_factory=Counter)
    regions: Counter = field(default_factory=Counter)
    amounts: list[float] = field(default_factory=list)
    first_date: str | None = None
    last_date: str | None = None
    ocids: set[str] = field(default_factory=set)


def load_interactions(
    connection: sqlite3.Connection, competitive_only: bool = True
) -> list[Interaction]:
    """Load the interaction table, optionally restricted to competitive procurements.

    Single-bidder procurements name only the winner, so treating them as bidding
    behaviour would teach the model who wins rather than who bids. They are excluded
    from training by default and reported separately.
    """
    rows = connection.execute(
        "SELECT canonical_id, ocid, interaction_date, won, bid_amount, buyer_id, "
        "       category, region, title "
        "FROM bid_interactions WHERE interaction_date IS NOT NULL"
    ).fetchall()

    interactions = [
        Interaction(
            canonical_id=str(row["canonical_id"]),
            ocid=str(row["ocid"]),
            date=str(row["interaction_date"]),
            won=int(row["won"] or 0),
            bid_amount=row["bid_amount"],
            buyer_id=row["buyer_id"],
            category=row["category"],
            region=row["region"],
            title=row["title"],
        )
        for row in rows
    ]
    if not competitive_only:
        return interactions

    bidders = Counter(item.ocid for item in interactions)
    competitive = [item for item in interactions if bidders[item.ocid] > 1]
    LOGGER.info(
        "Loaded %d interactions; %d on competitive procurements (%d single-bidder "
        "procurements excluded)",
        len(interactions),
        len(competitive),
        sum(1 for count in bidders.values() if count == 1),
    )
    return competitive


def build_histories(
    interactions: Iterable[Interaction], as_of: str
) -> dict[str, FirmHistory]:
    """Summarize every firm's behaviour strictly before ``as_of``.

    The strict inequality is the leakage guard: an interaction dated on the cutoff is
    excluded, because a tender closing that day must not see bids placed that day.
    """
    histories: dict[str, FirmHistory] = defaultdict(FirmHistory)
    for item in interactions:
        if not item.date or item.date >= as_of:
            continue
        history = histories[item.canonical_id]
        history.interactions += 1
        history.wins += item.won
        history.ocids.add(item.ocid)
        if item.category:
            history.categories[item.category] += 1
        if item.buyer_id:
            history.buyers[item.buyer_id] += 1
        if item.region:
            history.regions[item.region] += 1
        if item.bid_amount is not None and item.bid_amount > 0:
            history.amounts.append(float(item.bid_amount))
        if history.first_date is None or item.date < history.first_date:
            history.first_date = item.date
        if history.last_date is None or item.date > history.last_date:
            history.last_date = item.date
    return dict(histories)


def firm_features(history: FirmHistory | None, as_of: str) -> dict[str, float]:
    """Group 1: what this firm has done before."""
    if history is None or history.interactions == 0:
        return {
            "firm_interactions": 0.0,
            "firm_wins": 0.0,
            "firm_win_rate": 0.0,
            "firm_distinct_categories": 0.0,
            "firm_distinct_buyers": 0.0,
            "firm_distinct_regions": 0.0,
            "firm_category_concentration": 0.0,
            "firm_median_bid": 0.0,
            "firm_log_median_bid": 0.0,
            "firm_bid_spread": 0.0,
            "firm_days_since_last": 3650.0,
            "firm_active_days": 0.0,
            "firm_bids_per_month": 0.0,
            "firm_has_amounts": 0.0,
        }

    amounts = sorted(history.amounts)
    median = float(np.median(amounts)) if amounts else 0.0
    spread = float(np.std(np.log1p(amounts))) if len(amounts) > 1 else 0.0
    span = max(_days_between(history.first_date, as_of), 1.0)

    return {
        "firm_interactions": float(history.interactions),
        "firm_wins": float(history.wins),
        "firm_win_rate": history.wins / history.interactions,
        "firm_distinct_categories": float(len(history.categories)),
        "firm_distinct_buyers": float(len(history.buyers)),
        "firm_distinct_regions": float(len(history.regions)),
        "firm_category_concentration": _herfindahl(history.categories),
        "firm_median_bid": median,
        "firm_log_median_bid": math.log1p(median),
        "firm_bid_spread": spread,
        "firm_days_since_last": _days_between(history.last_date, as_of),
        "firm_active_days": span,
        "firm_bids_per_month": history.interactions / (span / 30.44),
        "firm_has_amounts": 1.0 if amounts else 0.0,
    }


def tender_features(tender: dict) -> dict[str, float]:
    """Group 2: what this opportunity is, structurally.

    ``tender.value`` is published on under 1% of SEAO notices, so the value features
    are mostly a presence flag. They are kept to measure exactly how weak they are
    rather than to carry the model.
    """
    value = tender.get("value")
    has_value = value is not None and float(value) > 0
    return {
        "tender_has_value": 1.0 if has_value else 0.0,
        "tender_log_value": math.log1p(float(value)) if has_value else 0.0,
        "tender_bidder_count": float(tender.get("bidder_count") or 0),
        "tender_title_length": float(len(str(tender.get("title") or ""))),
        "tender_is_works": 1.0 if _is_works(tender.get("category")) else 0.0,
        "tender_is_services": 1.0 if _is_services(tender.get("category")) else 0.0,
        "tender_is_goods": 1.0 if _is_goods(tender.get("category")) else 0.0,
    }


def cross_features(
    history: FirmHistory | None,
    tender: dict,
    firm_embedding: np.ndarray | None = None,
    tender_embedding: np.ndarray | None = None,
) -> dict[str, float]:
    """Group 3: how this firm relates to this opportunity."""
    if history is None or history.interactions == 0:
        base = {
            "cross_category_share": 0.0,
            "cross_category_seen": 0.0,
            "cross_buyer_prior_bids": 0.0,
            "cross_buyer_prior_wins": 0.0,
            "cross_buyer_seen": 0.0,
            "cross_region_share": 0.0,
            "cross_region_seen": 0.0,
            "cross_value_fit": 0.0,
            "cross_value_ratio": 0.0,
        }
    else:
        total = max(history.interactions, 1)
        category = tender.get("category")
        buyer_id = tender.get("buyer_id")
        region = tender.get("region")
        category_count = history.categories.get(category, 0) if category else 0
        buyer_count = history.buyers.get(buyer_id, 0) if buyer_id else 0
        region_count = history.regions.get(region, 0) if region else 0

        base = {
            "cross_category_share": category_count / total,
            "cross_category_seen": 1.0 if category_count else 0.0,
            "cross_buyer_prior_bids": float(buyer_count),
            # A prior *win* with this buyer is the incumbency signal; it is reported
            # in the bias notes because it is self-reinforcing.
            "cross_buyer_prior_wins": float(min(buyer_count, history.wins)),
            "cross_buyer_seen": 1.0 if buyer_count else 0.0,
            "cross_region_share": region_count / total,
            "cross_region_seen": 1.0 if region_count else 0.0,
            **_value_fit(history, tender),
        }

    similarity = 0.0
    if firm_embedding is not None and tender_embedding is not None:
        similarity = _cosine(firm_embedding, tender_embedding)
    base["cross_embedding_similarity"] = similarity
    return base


def _value_fit(history: FirmHistory, tender: dict) -> dict[str, float]:
    """How well a tender's size matches what this firm usually bids.

    Only computable when the notice publishes a value, which is under 1% of the time.
    The flag lets the model learn to ignore the ratio when it is absent instead of
    reading a zero as "small".
    """
    value = tender.get("value")
    if not history.amounts or value is None or float(value) <= 0:
        return {"cross_value_fit": 0.0, "cross_value_ratio": 0.0}
    median = float(np.median(history.amounts))
    if median <= 0:
        return {"cross_value_fit": 0.0, "cross_value_ratio": 0.0}
    ratio = float(value) / median
    # A log-ratio of 0 means a perfect size match; the gaussian turns distance in
    # orders of magnitude into a 0..1 fit.
    fit = math.exp(-0.5 * (math.log10(ratio) / 0.75) ** 2)
    return {"cross_value_fit": fit, "cross_value_ratio": math.log10(ratio)}


def feature_names() -> list[str]:
    """Every feature this module produces, in a stable order."""
    empty_history = FirmHistory()
    tender = {"category": None, "buyer_id": None, "region": None, "value": None}
    names = list(firm_features(empty_history, "2026-01-01"))
    names += list(tender_features(tender))
    names += list(cross_features(empty_history, tender))
    return names


def group_of(name: str) -> str:
    """Which ablatable group a feature belongs to."""
    for group in FEATURE_GROUPS:
        if name.startswith(f"{group}_"):
            return group
    return "other"


def _herfindahl(counter: Counter) -> float:
    total = sum(counter.values())
    if not total:
        return 0.0
    return sum((count / total) ** 2 for count in counter.values())


def _days_between(start: str | None, end: str) -> float:
    if not start:
        return 3650.0
    from datetime import date

    try:
        first = date.fromisoformat(start[:10])
        last = date.fromisoformat(end[:10])
    except ValueError:
        return 3650.0
    return float(max((last - first).days, 0))


def _cosine(first: np.ndarray, second: np.ndarray) -> float:
    denominator = float(np.linalg.norm(first) * np.linalg.norm(second))
    if denominator == 0:
        return 0.0
    return float(np.dot(first, second) / denominator)


def _is_works(category: Any) -> bool:
    text = str(category or "").casefold()
    return "travaux" in text or "works" in text or "construction" in text


def _is_services(category: Any) -> bool:
    text = str(category or "").casefold()
    return "service" in text


def _is_goods(category: Any) -> bool:
    text = str(category or "").casefold()
    return "bien" in text or "approvisionnement" in text or "goods" in text
