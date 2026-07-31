"""Ingest CanadaBuys open tender notices into the unified tenders table.

Unlike ``ingest.canadabuys`` — which filters hard to the Ontario construction demo
slice and downloads document packages — this module keeps every category and every
region. Filtering belongs to the recommendation layer, so the table stays a
complete picture of what the source published.
"""

from __future__ import annotations

import argparse
import logging
import re
import sqlite3
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

import config
from ingest.canadabuys import fetch_notices_csv
from notices import db
from notices.normalize import (
    iso_timestamp,
    normalize_buyer_type,
    normalize_category,
    normalize_region,
    normalize_status,
)


LOGGER = logging.getLogger(__name__)

SOURCE = "canadabuys"

#: Concept -> ordered candidate header patterns. Every pattern is a tuple of
#: substrings that must all appear in the normalized header, so bilingual headers
#: such as ``title-titre-eng`` are matched without hard-coding them.
COLUMN_CONCEPTS: dict[str, tuple[tuple[str, ...], ...]] = {
    "reference_number": (("referencenumber",), ("reference",)),
    "title": (("title", "eng"), ("title",)),
    "description": (("tenderdescription", "eng"), ("description", "eng")),
    "publication_date": (("publicationdate",), ("publication",)),
    "closing_date": (("closingdate",), ("cloture",)),
    "status": (("tenderstatus", "eng"), ("status", "eng")),
    "category": (("procurementcategory",),),
    "unspsc": (("unspsc",),),
    "gsin": (("gsin",),),
    "regions_delivery": (("regionsofdelivery", "eng"), ("regionslivraison", "eng")),
    "regions_opportunity": (
        ("regionsofopportunity", "eng"),
        ("regionappeloffres", "eng"),
    ),
    "buyer_name": (("contractingentityname", "eng"), ("contractingentityname",)),
    "buyer_province": (("contractingentityaddressprovince", "eng"),),
    "notice_url": (("noticeurl",), ("url", "eng")),
    "attachments": (("attachment", "eng"),),
}

#: Concepts without which a row cannot become a usable notice record.
REQUIRED_CONCEPTS = ("reference_number", "title", "closing_date")

#: Substrings that disqualify a header for a concept. Classification-code columns
#: must not win the description match, and the code columns themselves must not be
#: satisfied by their neighbouring ``...Description-eng`` label columns.
CONCEPT_EXCLUSIONS: dict[str, tuple[str, ...]] = {
    "description": ("gsin", "unspsc"),
    "unspsc": ("description",),
    "gsin": ("description",),
}


def parse_notices(csv_path: str | Path) -> list[dict]:
    """Parse every row of a CanadaBuys CSV into unified notice records."""
    dataframe = _read_csv(Path(csv_path))
    headers = [str(column) for column in dataframe.columns]
    columns = map_columns(headers)
    LOGGER.info(
        "Mapped %d of %d CanadaBuys concepts",
        sum(1 for value in columns.values() if value),
        len(COLUMN_CONCEPTS),
    )

    records: list[dict] = []
    skipped = 0
    for row_number, (_, row) in enumerate(dataframe.iterrows(), start=2):
        record = _build_record(row, columns)
        if record is None:
            skipped += 1
            LOGGER.warning("Skipping CSV row %d: missing reference number", row_number)
            continue
        records.append(record)

    LOGGER.info(
        "Parsed %d CanadaBuys notices from %d rows (%d skipped)",
        len(records),
        len(dataframe),
        skipped,
    )
    return records


def map_columns(headers: Iterable[str]) -> dict[str, str | None]:
    """Map concepts to headers, raising when a required concept is absent."""
    normalized = {str(header): _normalize_header(str(header)) for header in headers}
    mapped: dict[str, str | None] = {}
    for concept, patterns in COLUMN_CONCEPTS.items():
        mapped[concept] = _find_header(
            normalized, patterns, CONCEPT_EXCLUSIONS.get(concept, ())
        )

    missing = [concept for concept in REQUIRED_CONCEPTS if not mapped[concept]]
    if missing:
        LOGGER.error("CanadaBuys CSV header: %s", list(normalized))
        raise ValueError(
            "Could not map required CSV concept(s): " + ", ".join(missing)
        )
    for concept, header in mapped.items():
        if header is None:
            LOGGER.warning("CanadaBuys CSV has no column for %s", concept)
    return mapped


def ingest(
    connection: sqlite3.Connection | None = None,
    csv_path: str | Path | None = None,
) -> dict[str, Any]:
    """Download or reuse the notices CSV and upsert every row it contains."""
    path = Path(csv_path) if csv_path else Path(
        fetch_notices_csv(str(config.OPEN_TENDERS_CSV_PATH))
    )
    records = parse_notices(path)
    owned = connection is None
    connection = connection or db.connect()
    try:
        tally = db.upsert_notices(connection, records)
    finally:
        if owned:
            connection.close()
    return {"source": SOURCE, "parsed": len(records), **tally, "notes": []}


def _build_record(row: pd.Series, columns: dict[str, str | None]) -> dict | None:
    """Convert one CSV row into a unified notice record."""
    def cell(concept: str) -> str:
        header = columns.get(concept)
        return _cell_text(row[header]) if header else ""

    source_id = cell("reference_number")
    if not source_id:
        return None

    title = cell("title")
    description = cell("description")
    category_raw = cell("category")
    codes = [
        code
        for value in (cell("unspsc"), cell("gsin"))
        for code in re.split(r"[^A-Za-z0-9]+", value)
        if code
    ]
    buyer_name = cell("buyer_name")
    attachments = _split_attachment_urls(cell("attachments"))

    return {
        "source": SOURCE,
        "source_id": source_id,
        "title": title,
        "description": description or None,
        "buyer_name": buyer_name or None,
        # CanadaBuys is the federal tender feed, but provincial, municipal, and
        # broader-public-sector bodies also publish here, so trust the name first.
        "buyer_type": normalize_buyer_type(buyer_name, default="federal"),
        "category_raw": category_raw or None,
        "category_normalized": normalize_category(
            category_raw=category_raw,
            title=title,
            description=description,
            classification_codes=codes,
        ),
        # Where the work is, not where the buyer sits: the entity's own province is
        # only a fallback for notices that publish no delivery or opportunity region.
        "region": normalize_region(
            cell("regions_delivery"), cell("regions_opportunity")
        )
        or normalize_region(cell("buyer_province")),
        # The open-notices CSV publishes no contract value column.
        "estimated_value": None,
        "currency": None,
        "closing_date": iso_timestamp(cell("closing_date")),
        "posted_date": iso_timestamp(cell("publication_date")),
        "notice_url": cell("notice_url") or None,
        "documents_open": bool(attachments),
        "status": normalize_status(cell("status"), cell("closing_date")),
    }


def _read_csv(csv_path: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(
            csv_path, encoding="utf-8-sig", dtype=str, keep_default_na=False
        )
    except UnicodeDecodeError:
        LOGGER.info("UTF-8 decoding failed for %s; retrying as latin-1", csv_path)
        return pd.read_csv(
            csv_path, encoding="latin-1", dtype=str, keep_default_na=False
        )


def _find_header(
    normalized: dict[str, str],
    patterns: tuple[tuple[str, ...], ...],
    exclusions: tuple[str, ...] = (),
) -> str | None:
    for pattern in patterns:
        matches = [
            header
            for header, value in normalized.items()
            if all(part in value for part in pattern)
            and not any(excluded in value for excluded in exclusions)
        ]
        if not matches:
            continue
        english = [header for header in matches if "eng" in normalized[header]]
        return sorted(english or matches, key=lambda header: len(normalized[header]))[0]
    return None


def _normalize_header(header: str) -> str:
    return re.sub(r"[^a-z0-9]", "", header.casefold())


def _cell_text(value: Any) -> str:
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return ""
    return str(value).strip()


def _split_attachment_urls(value: str) -> list[str]:
    urls = [
        part.strip()
        for part in re.split(r"[|,\r\n]+", value)
        if part.strip().lower().startswith(("http://", "https://"))
    ]
    return list(dict.fromkeys(urls))


def _main() -> None:
    parser = argparse.ArgumentParser(description="Ingest CanadaBuys notices")
    parser.add_argument("--csv-path", default=None)
    parser.add_argument("--db", default=None)
    args = parser.parse_args()

    connection = db.connect(args.db)
    try:
        result = ingest(connection, csv_path=args.csv_path)
    finally:
        connection.close()
    print(
        f"{result['source']}: parsed {result['parsed']}, "
        f"inserted {result['inserted']}, updated {result['updated']}, "
        f"unchanged {result['unchanged']}, skipped {result['skipped']}"
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    _main()
