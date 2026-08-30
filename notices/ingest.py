"""Command-line entry point for multi-source tender notice ingestion."""

from __future__ import annotations

import argparse
import logging
import math
from datetime import datetime
from typing import Any, Callable

from notices import bidsandtenders, canadabuys, db, seao


LOGGER = logging.getLogger(__name__)

SOURCE_CHOICES = ("canadabuys", "seao", "bidsandtenders", "all")

#: Narrowest window a scheduled run will use, however recently it last succeeded.
#:
#: Not 1. SEAO publishes weekly and revises a procurement into whichever file is
#: current when the revision lands, so a window sized to the literal gap can step over
#: an amendment to a notice ingested days earlier. Three weeks is two weeks of overlap
#: on a daily cadence, which is cheap: the median weekly file is 9 MB.
INGEST_FLOOR_WEEKS = 3

#: Overlap added on top of the measured gap, in weeks. Same reasoning as the floor.
INGEST_OVERLAP_WEEKS = 2


def adaptive_weeks(
    connection: Any, now: datetime | None = None
) -> tuple[int, dict[str, Any]]:
    """Size the SEAO window to cover the gap since the last ingest.

    A scheduled job that always asks for a fixed window silently skips the middle
    whenever it misses a day: the run after a twelve-day outage fetches the same three
    weeks it always does and never learns what it lost. Sizing from
    ``MAX(ingested_at)`` makes the window widen on its own, and returns the reasoning
    alongside it so a narrow window shows up in the job summary as a decision rather
    than as a default nobody looked at.

    Bounded at both ends: never narrower than :data:`INGEST_FLOOR_WEEKS`, never wider
    than ``seao.BACKFILL_WEEKS`` — past that, the correct move is a deliberate local
    backfill, not a cron job quietly downloading a year of weekly files.
    """
    reference = now or datetime.now().astimezone()
    last = None
    try:
        row = connection.execute("SELECT MAX(ingested_at) FROM tenders").fetchone()
        last = row[0] if row else None
    except Exception as exc:  # A cold or unreadable table is a cold start, not a crash.
        LOGGER.warning("Could not read the last ingest time: %s", exc)

    if not last:
        return seao.BACKFILL_WEEKS, {
            "weeks": seao.BACKFILL_WEEKS,
            "reason": "no notices ingested yet; cold-start backfill",
            "last_ingested_at": None,
            "gap_days": None,
            "floor": INGEST_FLOOR_WEEKS,
            "ceiling": seao.BACKFILL_WEEKS,
        }

    try:
        previous = datetime.fromisoformat(str(last))
        if previous.tzinfo is None:
            previous = previous.astimezone()
        gap_days = max((reference - previous).total_seconds() / 86400.0, 0.0)
    except ValueError:
        return seao.BACKFILL_WEEKS, {
            "weeks": seao.BACKFILL_WEEKS,
            "reason": f"unparseable last ingest time {last!r}; widening to the ceiling",
            "last_ingested_at": str(last),
            "gap_days": None,
            "floor": INGEST_FLOOR_WEEKS,
            "ceiling": seao.BACKFILL_WEEKS,
        }

    needed = math.ceil(gap_days / 7.0) + INGEST_OVERLAP_WEEKS
    weeks = max(INGEST_FLOOR_WEEKS, min(needed, seao.BACKFILL_WEEKS))
    if weeks == seao.BACKFILL_WEEKS and needed > seao.BACKFILL_WEEKS:
        reason = (
            f"gap of {gap_days:.1f} days needs {needed} weeks; capped at the "
            f"{seao.BACKFILL_WEEKS}-week ceiling, so this run may not close it"
        )
    elif weeks == INGEST_FLOOR_WEEKS and needed <= INGEST_FLOOR_WEEKS:
        reason = f"gap of {gap_days:.1f} days is inside the {INGEST_FLOOR_WEEKS}-week floor"
    else:
        reason = (
            f"gap of {gap_days:.1f} days plus {INGEST_OVERLAP_WEEKS} weeks of overlap"
        )

    return weeks, {
        "weeks": weeks,
        "reason": reason,
        "last_ingested_at": str(last),
        "gap_days": round(gap_days, 1),
        "floor": INGEST_FLOOR_WEEKS,
        "ceiling": seao.BACKFILL_WEEKS,
    }

SAMPLE_COLUMNS = (
    "source_id",
    "title",
    "buyer_name",
    "buyer_type",
    "category_normalized",
    "region",
    "estimated_value",
    "closing_date",
    "documents_open",
    "status",
)


def run(
    source: str = "all",
    db_path: str | None = None,
    weeks: int | None = None,
    samples: int = 0,
    adaptive: bool = False,
) -> list[dict]:
    """Run the requested ingesters against one database and return their results."""
    if source not in SOURCE_CHOICES:
        raise ValueError(f"source must be one of {', '.join(SOURCE_CHOICES)}")

    window: dict[str, Any] | None = None
    if adaptive and weeks is None:
        probe = db.connect(db_path)
        try:
            weeks, window = adaptive_weeks(probe)
        finally:
            probe.close()
        LOGGER.info("SEAO window: %d week(s) — %s", window["weeks"], window["reason"])

    runners: dict[str, Callable[..., dict[str, Any]]] = {
        "canadabuys": lambda connection: canadabuys.ingest(connection),
        "seao": lambda connection: seao.ingest(connection, weeks=weeks),
        "bidsandtenders": lambda connection: bidsandtenders.ingest(connection),
    }
    selected = list(runners) if source == "all" else [source]

    connection = db.connect(db_path)
    results: list[dict] = []
    try:
        for name in selected:
            LOGGER.info("Ingesting source %s", name)
            try:
                results.append(runners[name](connection))
            except Exception as exc:  # One failing source must not stop the rest.
                LOGGER.exception("Ingestion failed for %s: %s", name, exc)
                results.append(
                    {
                        "source": name,
                        "parsed": 0,
                        "inserted": 0,
                        "updated": 0,
                        "unchanged": 0,
                        "skipped": 0,
                        "notes": [f"failed: {exc}"],
                    }
                )
        totals = db.count_by_source(connection)
        _print_results(results, totals)
        if window is not None:
            print(f"\nseao window: {window['weeks']} week(s) — {window['reason']}")
        if samples > 0:
            for name in selected:
                _print_samples(name, db.sample_rows(connection, name, samples))
    finally:
        connection.close()
    if window is not None:
        for result in results:
            if result.get("source") == "seao":
                result["window"] = window
    return results


def _print_results(results: list[dict], totals: dict[str, int]) -> None:
    headers = [
        "source",
        "parsed",
        "inserted",
        "updated",
        "unchanged",
        "skipped",
        "rows in db",
    ]
    rows = [
        [
            str(result["source"]),
            str(result["parsed"]),
            str(result["inserted"]),
            str(result["updated"]),
            str(result["unchanged"]),
            str(result["skipped"]),
            str(totals.get(str(result["source"]), 0)),
        ]
        for result in results
    ]
    widths = [
        max(len(row[index]) for row in [headers, *rows]) for index in range(len(headers))
    ]

    def format_row(row: list[str]) -> str:
        return " | ".join(value.ljust(widths[index]) for index, value in enumerate(row))

    print(format_row(headers))
    print("-+-".join("-" * width for width in widths))
    for row in rows:
        print(format_row(row))

    notes = [
        (str(result["source"]), note)
        for result in results
        for note in result.get("notes", [])
    ]
    if notes:
        print("\nnotes:")
        for source, note in notes:
            print(f"  {source}: {note}")


def _print_samples(source: str, rows: list[dict]) -> None:
    print(f"\nsample rows — {source} ({len(rows)}):")
    if not rows:
        print("  (none)")
        return
    for row in rows:
        print("  " + "-" * 60)
        for column in SAMPLE_COLUMNS:
            value = row.get(column)
            if isinstance(value, str) and len(value) > 100:
                value = value[:97] + "..."
            print(f"  {column:<20} {value}")


def _main() -> None:
    parser = argparse.ArgumentParser(description="Ingest tender notices")
    parser.add_argument("--source", choices=SOURCE_CHOICES, default="all")
    parser.add_argument("--db", default=None)
    parser.add_argument(
        "--weeks",
        type=int,
        default=None,
        help="SEAO weekly files to ingest (default: 12 on first run, then 4)",
    )
    parser.add_argument(
        "--samples", type=int, default=0, help="print N sample rows per source"
    )
    parser.add_argument(
        "--adaptive",
        action="store_true",
        help=(
            "size the SEAO window from the gap since the last ingest "
            f"(floor {INGEST_FLOOR_WEEKS} weeks, ceiling {seao.BACKFILL_WEEKS}); "
            "ignored when --weeks is given"
        ),
    )
    args = parser.parse_args()
    run(
        source=args.source,
        db_path=args.db,
        weeks=args.weeks,
        samples=args.samples,
        adaptive=args.adaptive,
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    _main()
