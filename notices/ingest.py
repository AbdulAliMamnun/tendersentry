"""Command-line entry point for multi-source tender notice ingestion."""

from __future__ import annotations

import argparse
import logging
from typing import Any, Callable

from notices import bidsandtenders, canadabuys, db, seao


LOGGER = logging.getLogger(__name__)

SOURCE_CHOICES = ("canadabuys", "seao", "bidsandtenders", "all")

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
) -> list[dict]:
    """Run the requested ingesters against one database and return their results."""
    if source not in SOURCE_CHOICES:
        raise ValueError(f"source must be one of {', '.join(SOURCE_CHOICES)}")

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
        if samples > 0:
            for name in selected:
                _print_samples(name, db.sample_rows(connection, name, samples))
    finally:
        connection.close()
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
    args = parser.parse_args()
    run(source=args.source, db_path=args.db, weeks=args.weeks, samples=args.samples)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    _main()
