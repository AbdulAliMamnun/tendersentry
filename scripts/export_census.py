"""Export the municipal census to build-time JSON for the public site.

Read-only. Emits the full 444-row register for client-side lookup, the nine-class
distribution with both municipality and population shares, and the homepage's
five-bucket rollup.

The ``own_site_open`` row carries a second set of figures excluding the County of
Frontenac: the provincial register lists a neighbouring township's website against
that county, so its 161,780 residents would otherwise account for nearly half the
class on the strength of a data error. Both figures ship, and the site shows the
corrected one.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any

import config
from census import schema as census_schema


LOGGER = logging.getLogger(__name__)

WEB_DATA_DIR = Path(config.PROJECT_ROOT) / "web" / "data"

#: The register error documented in census/README.md.
EXCLUDED_FROM_OPEN = "frontenac-county"
EXCLUSION_FOOTNOTE = (
    "Excludes the County of Frontenac, for which the provincial register lists a "
    "neighbouring township's website. Including it would report 47 municipalities "
    "and 332,059 residents (1.5%) on the strength of a data error."
)

#: Homepage rollup. Every class lands in exactly one bucket.
BUCKETS = (
    (
        "bids_and_tenders",
        "bids&tenders",
        ("bids_and_tenders",),
    ),
    (
        "unknown",
        "unknown",
        (
            "no_procurement_page_found",
            "fetch_failed",
            "no_website_listed",
            "robots_disallowed",
        ),
    ),
    (
        "notices_gated",
        "notices visible, documents gated",
        ("own_site_notices",),
    ),
    (
        "other_platforms",
        "other platforms",
        ("biddingo", "bidnet_or_other_platform"),
    ),
    (
        "open",
        "openly posted",
        ("own_site_open",),
    ),
)

CLASS_LABELS = {
    "bids_and_tenders": "On bids&tenders",
    "biddingo": "On Biddingo",
    "bidnet_or_other_platform": "On another platform",
    "own_site_open": "Open documents on their own site",
    "own_site_notices": "Notices visible, documents gated or absent",
    "no_procurement_page_found": "No procurement page found",
    "fetch_failed": "Could not be read",
    "no_website_listed": "No website in the register",
    "robots_disallowed": "Crawling disallowed",
}


def municipalities(connection: Any) -> list[dict]:
    """Every municipality, for the site's client-side lookup."""
    rows = connection.execute(
        "SELECT slug, name, tier, geographic_area, population, classification, "
        "       confidence, platform, procurement_url "
        "FROM municipalities ORDER BY name"
    ).fetchall()
    return [
        {
            "slug": str(row["slug"]),
            "name": str(row["name"]),
            "tier": str(row["tier"]),
            "area": str(row["geographic_area"] or ""),
            "population": int(row["population"]) if row["population"] is not None else None,
            "classification": str(row["classification"]),
            "label": CLASS_LABELS.get(str(row["classification"]), str(row["classification"])),
            "confidence": row["confidence"],
            "platform": row["platform"],
            "url": row["procurement_url"],
        }
        for row in rows
    ]


def distribution(connection: Any) -> list[dict]:
    """The nine-class distribution, with the Frontenac correction attached."""
    rows = census_schema.distribution(connection)
    corrected = connection.execute(
        "SELECT COUNT(*) AS municipalities, COALESCE(SUM(population), 0) AS population "
        "FROM municipalities WHERE classification = ? AND slug != ?",
        (census_schema.CLASS_OWN_SITE_OPEN, EXCLUDED_FROM_OPEN),
    ).fetchone()
    total_population = int(
        connection.execute(
            "SELECT COALESCE(SUM(population), 0) FROM municipalities"
        ).fetchone()[0]
    )
    total_count = int(
        connection.execute("SELECT COUNT(*) FROM municipalities").fetchone()[0]
    )

    result = []
    for row in rows:
        entry = {
            **row,
            "label": CLASS_LABELS.get(row["classification"], row["classification"]),
        }
        if row["classification"] == census_schema.CLASS_OWN_SITE_OPEN:
            municipality_count = int(corrected["municipalities"])
            population = int(corrected["population"])
            entry["corrected"] = {
                "municipalities": municipality_count,
                "share_of_municipalities": round(
                    100.0 * municipality_count / total_count, 1
                ),
                "population": population,
                "share_of_population": round(100.0 * population / total_population, 2),
                "footnote": EXCLUSION_FOOTNOTE,
            }
        result.append(entry)
    return result


def buckets(rows: list[dict]) -> list[dict]:
    """Roll the nine classes into the homepage's five-item legend."""
    by_class = {row["classification"]: row for row in rows}
    total_population = sum(row["population"] for row in rows) or 1

    result = []
    for key, label, classes in BUCKETS:
        population = 0
        for name in classes:
            row = by_class.get(name)
            if row is None:
                continue
            # The open bucket uses the Frontenac-corrected figure.
            if name == census_schema.CLASS_OWN_SITE_OPEN and "corrected" in row:
                population += row["corrected"]["population"]
            else:
                population += row["population"]
        result.append(
            {
                "key": key,
                "label": label,
                "population": population,
                "share_of_population": round(100.0 * population / total_population, 2),
            }
        )
    return result


def build_payload(connection: Any) -> dict:
    """Assemble the full census export."""
    rows = distribution(connection)
    coverage = census_schema.population_coverage(connection)
    total_population = sum(row["population"] for row in rows)
    return {
        "retrieved": "2026-07-31",
        "totals": {
            "municipalities": coverage["total"],
            "population": total_population,
            "population_matched": coverage["matched"],
        },
        "distribution": rows,
        "buckets": buckets(rows),
        "municipalities": municipalities(connection),
        "sources": {
            "register": {
                "name": "Municipalities, Ministry of Municipal Affairs and Housing",
                "dataset_id": "62e83cbc-0731-4d66-abdc-2f2b31bcd76c",
                "licence": "Open Government Licence – Ontario",
                "url": "https://data.ontario.ca/dataset/ontario-municipalities",
            },
            "population": {
                "name": "Statistics Canada, table 98-10-0002 (2021 Census)",
                "matched": f"{coverage['matched']} of {coverage['total']}",
                "url": "https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=9810000201",
            },
        },
    }


def export(out_dir: Path | str | None = None, db_path: Any = None) -> Path:
    """Write census.json and return its path."""
    directory = Path(out_dir) if out_dir else WEB_DATA_DIR
    directory.mkdir(parents=True, exist_ok=True)
    connection = census_schema.connect(db_path)
    try:
        payload = build_payload(connection)
    finally:
        connection.close()

    destination = directory / "census.json"
    with destination.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    LOGGER.info(
        "Exported %d municipalities across %d classes to %s",
        len(payload["municipalities"]),
        len(payload["distribution"]),
        destination,
    )
    return destination


def _main() -> None:
    parser = argparse.ArgumentParser(description="Export the municipal census")
    parser.add_argument("--out", default=None)
    parser.add_argument("--db", default=None)
    args = parser.parse_args()
    print(f"wrote census.json: {export(args.out, args.db)}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    _main()
