"""Timezone-aware handling of the mixed closing-date formats in the tenders table.

Two sources, two conventions: SEAO publishes UTC offsets (``2026-08-27T11:00:00-04:00``)
while CanadaBuys publishes naive local times (``2026-07-30T14:00:00``). Naive values are
localized to America/Toronto — correct for Ontario notices and for the Eastern-time
deadlines federal notices are written against, and DST-aware either way.

Everything downstream compares ``closing_date_utc``, which is always stored with a
``+00:00`` offset so lexicographic ordering in SQL matches chronological ordering.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo


LOGGER = logging.getLogger(__name__)

#: The timezone naive source timestamps are assumed to be written in.
DEFAULT_TIMEZONE = ZoneInfo("America/Toronto")


def parse_source_datetime(value: Any) -> datetime | None:
    """Parse a stored closing date into an aware datetime, or None if unusable."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        LOGGER.debug("Could not parse closing date %r", text)
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=DEFAULT_TIMEZONE)
    return parsed


def to_utc(value: Any) -> datetime | None:
    """Return a stored closing date as an aware UTC datetime."""
    parsed = parse_source_datetime(value)
    return parsed.astimezone(timezone.utc) if parsed is not None else None


def utc_iso(value: Any) -> str | None:
    """Return a stored closing date as a normalized UTC ISO-8601 string."""
    converted = to_utc(value)
    return converted.isoformat(timespec="seconds") if converted is not None else None


def now_utc() -> datetime:
    """Return the current time as an aware UTC datetime."""
    return datetime.now(timezone.utc)


def now_utc_iso() -> str:
    """Return the current time as a normalized UTC ISO-8601 string."""
    return now_utc().isoformat(timespec="seconds")


def shift_iso(reference: datetime, hours: float) -> str:
    """Return an ISO-8601 UTC string offset from a reference datetime."""
    return (
        reference.astimezone(timezone.utc) + timedelta(hours=hours)
    ).isoformat(timespec="seconds")


def hours_until(closing_utc: Any, now: datetime | None = None) -> float | None:
    """Return the hours remaining until a closing date, negative once past."""
    target = to_utc(closing_utc)
    if target is None:
        return None
    reference = (now or now_utc()).astimezone(timezone.utc)
    return (target - reference).total_seconds() / 3600.0


def days_until(closing_utc: Any, now: datetime | None = None) -> float | None:
    """Return the days remaining until a closing date, negative once past."""
    hours = hours_until(closing_utc, now)
    return None if hours is None else hours / 24.0
