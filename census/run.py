"""Run the municipal procurement census: discover, classify, report.

Resumable by construction: every municipality is written the moment its verdict is
known, so an interrupted run loses at most the rows in flight. Workers run across
*different* hosts, never the same one, so the per-host politeness floor holds no
matter how many are in play.
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from census import classify, discover, fetcher, schema, sources
from notices import db


LOGGER = logging.getLogger(__name__)

DEFAULT_WORKERS = 6


def load_roster(
    connection: sqlite3.Connection, cache_dir: Any = None
) -> dict[str, Any]:
    """Fetch the authoritative roster and store it, leaving verdicts intact."""
    records, coverage = sources.build_roster(cache_dir)
    tally = schema.upsert_municipalities(connection, records)
    no_website = _mark_websiteless(connection)
    return {
        "roster": tally,
        "population": coverage,
        "municipalities": len(records),
        "no_website": no_website,
    }


def _mark_websiteless(connection: sqlite3.Connection) -> int:
    """Give the register's website-less municipalities a verdict of their own.

    They can never be fetched, so without this they sit at ``pending`` forever and
    read as unfinished work rather than as a fact about the register.
    """
    with connection:
        cursor = connection.execute(
            "UPDATE municipalities SET classification = ?, "
            "       evidence_note = 'the municipal register lists no website', "
            "       checked_at = COALESCE(checked_at, ?), updated_at = ? "
            "WHERE (website_url IS NULL OR website_url = '') AND classification = ?",
            (
                schema.CLASS_NO_WEBSITE,
                db.utc_timestamp(),
                db.utc_timestamp(),
                schema.CLASS_PENDING,
            ),
        )
    return int(cursor.rowcount or 0)


def classify_municipality(
    municipality: dict, client: fetcher.PoliteFetcher
) -> dict:
    """Discover and classify one municipality, returning its result row."""
    website = str(municipality.get("website_url") or "")
    if not website:
        return {
            "classification": schema.CLASS_NO_WEBSITE,
            "confidence": None,
            "platform": None,
            "procurement_url": None,
            "evidence_url": None,
            "evidence_note": "the municipal register lists no website",
            "cms_fingerprint": None,
            "robots_ok": None,
            "http_status": None,
            "requests_made": 0,
        }

    before = client.request_count
    try:
        found = discover.find_procurement_page(client, website)
    except fetcher.BlockedHostError as exc:
        # Defensive: discovery filters platform links, so reaching here is a bug
        # worth seeing rather than a state worth recording as a normal outcome.
        LOGGER.error("Blocked host reached for %s: %s", municipality.get("slug"), exc)
        return {
            "classification": schema.CLASS_FETCH_FAILED,
            "confidence": None,
            "platform": None,
            "procurement_url": None,
            "evidence_url": website,
            "evidence_note": f"blocked host: {exc}",
            "cms_fingerprint": None,
            "robots_ok": None,
            "http_status": None,
            "requests_made": client.request_count - before,
        }

    requests_made = client.request_count - before
    trail = found.get("trail") or []
    last_status = next(
        (step["status"] for step in reversed(trail) if step.get("status")), None
    )

    if not found.get("robots_ok", True):
        return {
            "classification": schema.CLASS_ROBOTS_DISALLOWED,
            "confidence": None,
            "platform": None,
            "procurement_url": None,
            "evidence_url": website,
            "evidence_note": found.get("note") or "robots.txt disallows",
            "cms_fingerprint": None,
            "robots_ok": 0,
            "http_status": last_status,
            "requests_made": requests_made,
        }

    if found.get("platform_link"):
        link = str(found["platform_link"])
        name = fetcher.platform_name(link) or "unknown"
        classification = next(
            (
                item[1]
                for item in classify.PLATFORM_CLASSES
                if item[0] in link.casefold()
            ),
            schema.CLASS_OTHER_PLATFORM,
        )
        return {
            "classification": classification,
            "confidence": schema.CONFIDENCE_HIGH,
            "platform": name,
            "procurement_url": None,
            "evidence_url": link,
            "evidence_note": found.get("note") or "links a procurement platform",
            "cms_fingerprint": None,
            "robots_ok": 1,
            "http_status": last_status,
            "requests_made": requests_made,
        }

    page = found.get("page")
    if not page:
        # A homepage that refused us (403) or was not there (404) is a failed fetch,
        # not evidence that the municipality has no procurement page. Ottawa and
        # Vaughan both block this bot outright, and calling that "none found" would
        # quietly understate the inventory.
        homepage = next(
            (step for step in trail if step.get("stage") == "homepage"), {}
        )
        homepage_status = homepage.get("status")
        blocked = bool(homepage.get("error")) or (
            homepage_status is not None and int(homepage_status) >= 400
        )
        return {
            "classification": (
                schema.CLASS_FETCH_FAILED if blocked else schema.CLASS_NONE_FOUND
            ),
            "confidence": None,
            "platform": None,
            "procurement_url": None,
            "evidence_url": website,
            "evidence_note": found.get("note") or "no procurement page found",
            "cms_fingerprint": None,
            "robots_ok": 1,
            "http_status": homepage_status or last_status,
            "requests_made": requests_made,
        }

    verdict = classify.classify_page(page["html"], page["url"])
    return {
        "classification": verdict["classification"],
        "confidence": verdict["confidence"],
        "platform": verdict["platform"],
        "procurement_url": page["url"],
        "evidence_url": page["url"],
        "evidence_note": verdict["evidence_note"],
        "cms_fingerprint": verdict["cms_fingerprint"],
        "robots_ok": 1,
        "http_status": last_status,
        "requests_made": requests_made,
    }


def run_census(
    connection: sqlite3.Connection,
    limit: int | None = None,
    resume: bool = True,
    workers: int = DEFAULT_WORKERS,
    client: fetcher.PoliteFetcher | None = None,
    recheck_before: str | None = None,
    recheck_classes: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Classify every pending municipality, writing each verdict as it lands."""
    client = client or fetcher.PoliteFetcher()
    pending = schema.pending_municipalities(
        connection,
        limit=limit,
        resume=resume,
        recheck_before=recheck_before,
        recheck_classes=recheck_classes,
    )
    if not pending:
        LOGGER.info("Nothing pending; every municipality already has a verdict")
        return {"processed": 0, "counts": {}}

    LOGGER.info(
        "Classifying %d municipalities with %d worker(s)", len(pending), max(1, workers)
    )
    counts: dict[str, int] = {}
    processed = 0

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {
            pool.submit(classify_municipality, municipality, client): municipality
            for municipality in pending
        }
        for future in as_completed(futures):
            municipality = futures[future]
            try:
                result = future.result()
            except Exception as exc:  # A single bad site must not end the run.
                LOGGER.exception(
                    "Unexpected failure for %s: %s", municipality.get("slug"), exc
                )
                result = {
                    "classification": schema.CLASS_FETCH_FAILED,
                    "confidence": None,
                    "platform": None,
                    "procurement_url": None,
                    "evidence_url": municipality.get("website_url"),
                    "evidence_note": f"unhandled error: {exc}"[:300],
                    "cms_fingerprint": None,
                    "robots_ok": None,
                    "http_status": None,
                    "requests_made": 0,
                }
            schema.record_result(connection, str(municipality["slug"]), result)
            counts[result["classification"]] = (
                counts.get(result["classification"], 0) + 1
            )
            processed += 1
            if processed % 25 == 0:
                LOGGER.info("Progress: %d of %d classified", processed, len(pending))

    return {"processed": processed, "counts": counts}


def print_distribution(connection: sqlite3.Connection) -> None:
    """Print the census distribution by count and by population."""
    rows = schema.distribution(connection)
    coverage = schema.population_coverage(connection)
    headers = ["classification", "munis", "% munis", "population", "% pop", "no pop"]
    rendered = [
        [
            str(row["classification"]),
            str(row["municipalities"]),
            f"{row['share_of_municipalities']:.1f}",
            f"{row['population']:,}",
            f"{row['share_of_population']:.1f}",
            str(row["population_unknown"]),
        ]
        for row in rows
    ]
    widths = [
        max(len(entry[index]) for entry in [headers, *rendered])
        for index in range(len(headers))
    ]

    def line(entry: list[str]) -> str:
        return " | ".join(value.ljust(widths[index]) for index, value in enumerate(entry))

    print(line(headers))
    print("-+-".join("-" * width for width in widths))
    for entry in rendered:
        print(line(entry))
    print(
        f"\npopulation matched for {coverage['matched']} of {coverage['total']} "
        f"municipalities"
    )

    platform_rows = [
        row
        for row in rows
        if row["classification"]
        in (schema.CLASS_BIDS_AND_TENDERS, schema.CLASS_BIDDINGO, schema.CLASS_OTHER_PLATFORM)
    ]
    if platform_rows:
        print("\ngated platforms:")
        for row in platform_rows:
            print(
                f"  {row['classification']:<26} {row['municipalities']:>4} munis "
                f"({row['share_of_municipalities']:.1f}%)  "
                f"{row['population']:>10,} people ({row['share_of_population']:.1f}%)"
            )
        total_munis = sum(row["municipalities"] for row in platform_rows)
        total_pop = sum(row["population"] for row in platform_rows)
        all_pop = sum(row["population"] for row in rows) or 1
        print(
            f"  {'combined':<26} {total_munis:>4} munis  {total_pop:>10,} people "
            f"({100.0 * total_pop / all_pop:.1f}% of Ontario's counted population)"
        )


def _main() -> None:
    parser = argparse.ArgumentParser(
        description="Census of Ontario municipal procurement pages"
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--resume",
        action="store_true",
        help="skip municipalities that already have a verdict (default)",
    )
    parser.add_argument(
        "--recheck",
        action="store_true",
        help="re-classify every municipality, ignoring stored verdicts",
    )
    parser.add_argument("--recheck-before", default=None)
    parser.add_argument(
        "--recheck-class",
        action="append",
        default=None,
        help="re-classify municipalities currently in this class (repeatable)",
    )
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--db", default=None)
    parser.add_argument(
        "--roster-only", action="store_true", help="refresh the roster and stop"
    )
    parser.add_argument(
        "--report-only", action="store_true", help="print the distribution and stop"
    )
    args = parser.parse_args()

    connection = schema.connect(args.db)
    try:
        db.migrate_source_constraint(connection)
        if args.report_only:
            print_distribution(connection)
            return

        roster = load_roster(connection)
        print(
            f"roster: {roster['municipalities']} municipalities "
            f"(inserted {roster['roster']['inserted']}, "
            f"updated {roster['roster']['updated']}, "
            f"unchanged {roster['roster']['unchanged']}); "
            f"population matched {roster['population']['matched']}"
            f"/{roster['population']['total']}"
        )
        if args.roster_only:
            return

        result = run_census(
            connection,
            limit=args.limit,
            resume=not args.recheck,
            workers=args.workers,
            recheck_before=args.recheck_before,
            recheck_classes=tuple(args.recheck_class) if args.recheck_class else None,
        )
        print(f"\nclassified {result['processed']} municipalities this run")
        print()
        print_distribution(connection)
    finally:
        connection.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    _main()
