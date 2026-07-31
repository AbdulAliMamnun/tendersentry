"""Ingest notices from municipalities that post tenders on their own websites.

Phase B of the municipal census. Only municipalities the census classified as
``own_site_open`` or ``own_site_notices`` are visited, and the same guardrails apply
as during the census: public pages only, robots respected, at least five seconds
between requests to a host, and never a platform-hosted URL even when a municipal
page links to one.

These pages are heterogeneous, so parsing is by observed pattern rather than by
municipality. A page that fits no pattern is recorded as ``parser_needed`` instead of
receiving a fragile one-off scraper.
"""

from __future__ import annotations

import argparse
import logging
import re
import sqlite3
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from census import classify, fetcher, schema as census_schema
from notices import db
from notices.normalize import normalize_status


LOGGER = logging.getLogger(__name__)

SOURCE = "municipal_site"

#: Parser outcomes recorded against a municipality.
STATUS_PARSED = "parsed"
STATUS_PARSER_NEEDED = "parser_needed"
STATUS_FETCH_FAILED = "fetch_failed"
STATUS_NO_NOTICES = "no_notices_found"

SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS notice_documents (
        tender_id INTEGER NOT NULL REFERENCES tenders (id),
        url TEXT NOT NULL,
        filename TEXT,
        link_text TEXT,
        kind TEXT,
        discovered_at TEXT NOT NULL,
        PRIMARY KEY (tender_id, url)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS municipal_parse_runs (
        slug TEXT PRIMARY KEY REFERENCES municipalities (slug),
        status TEXT NOT NULL,
        pattern TEXT,
        notices_found INTEGER NOT NULL DEFAULT 0,
        documents_found INTEGER NOT NULL DEFAULT 0,
        note TEXT,
        parsed_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_notice_documents_url ON notice_documents (url)",
)

#: Suffixes that mark a document as an amendment to a notice rather than a new one.
ADDENDUM_PATTERN = re.compile(
    r"[-_ ](?:addendum|addenda|amendment|amd|revision|rev|update)[-_ ]?\d*$",
    re.IGNORECASE,
)

#: The identifier municipalities put in front of a tender: T-2026-31, RFP2026-04.
NOTICE_ID_PATTERN = re.compile(
    r"(?<![a-z0-9])((?:rfp|rfq|rft|itt|itb|cr|pw|t|q)[-_ ]?\d{2,4}[-_ ]?\d{1,4})",
    re.IGNORECASE,
)

CLOSING_LABEL_PATTERN = re.compile(
    r"clos(?:es|ing|ed)?\s*(?:date|on)?\s*[:\-]?\s*(.{0,40})", re.IGNORECASE
)

DATE_FORMATS = (
    "%B %d, %Y %I:%M %p",
    "%B %d, %Y",
    "%b %d, %Y",
    "%Y-%m-%d",
    "%d %B %Y",
)


def ensure_schema(connection: sqlite3.Connection) -> None:
    """Create the Phase B side tables."""
    with connection:
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)


def notice_key(filename: str) -> str | None:
    """Return the notice identifier a document belongs to.

    ``t-2026-28-fuel-tanks.pdf`` and ``t-2026-28-fuel-tanks-addendum-1.pdf`` are one
    notice with two documents, which is why grouping happens on the identifier
    rather than on the file.
    """
    stem = Path(str(filename or "")).stem
    match = NOTICE_ID_PATTERN.search(stem)
    if match is None:
        return None
    return re.sub(r"[-_ ]", "-", match.group(1)).casefold()


def is_addendum(filename: str) -> bool:
    """Whether a document is an amendment to an existing notice."""
    return bool(ADDENDUM_PATTERN.search(Path(str(filename or "")).stem))


def parse_document_list(html: str, page_url: str) -> list[dict]:
    """Group a page's tender documents into notices.

    The pattern shared by the eSCRIBE-style sites most Ontario municipalities run:
    a page of links to tender packages, one or more files per opportunity, with the
    tender number carried in the filename.
    """
    documents = [
        document
        for document in classify.collect_documents(html, page_url)
        if document["is_tender"]
    ]
    grouped: dict[str, dict] = {}
    for document in documents:
        key = notice_key(document["filename"]) or notice_key(document["text"])
        if key is None:
            continue
        entry = grouped.setdefault(
            key,
            {
                "notice_id": key.upper(),
                "title": "",
                "documents": [],
                "closing_text": None,
            },
        )
        entry["documents"].append(
            {
                "url": document["url"],
                "filename": document["filename"],
                "link_text": document["text"],
                "kind": "addendum" if is_addendum(document["filename"]) else "package",
            }
        )
        candidate = _title_from(document)
        if not candidate:
            continue
        # A package always names the notice better than its addendum does: without
        # this, "Building Condition Assessments Addendum 1" becomes the title.
        addendum = is_addendum(document["filename"])
        current_is_addendum = entry.get("title_from_addendum", True)
        if (
            not entry["title"]
            or (current_is_addendum and not addendum)
            or (current_is_addendum == addendum and len(candidate) > len(entry["title"]))
        ):
            entry["title"] = candidate
            entry["title_from_addendum"] = addendum

    notices = [entry for entry in grouped.values() if entry["title"]]
    for notice in notices:
        notice.pop("title_from_addendum", None)
        notice["documents"].sort(key=lambda item: (item["kind"] != "package", item["url"]))
    notices.sort(key=lambda item: item["notice_id"])
    return notices


def parse_notice_page(html: str, page_url: str) -> dict:
    """Parse one municipal procurement page, reporting which pattern matched."""
    notices = parse_document_list(html, page_url)
    if notices:
        closings = _closing_dates(html)
        for notice in notices:
            notice["closing_text"] = closings.get(notice["notice_id"].casefold())
        return {"pattern": "document-list", "notices": notices}

    return {"pattern": None, "notices": []}


def to_notice_records(
    municipality: dict, parsed: dict, page_url: str
) -> list[dict]:
    """Convert parsed notices into rows for the unified tenders table."""
    slug = str(municipality["slug"])
    documents_open = (
        municipality.get("classification") == census_schema.CLASS_OWN_SITE_OPEN
    )
    records: list[dict] = []
    for notice in parsed["notices"]:
        closing = _parse_date(notice.get("closing_text"))
        records.append(
            {
                "record": {
                    "source": SOURCE,
                    "source_id": f"{slug}:{notice['notice_id'].casefold()}",
                    "title": notice["title"],
                    "description": None,
                    "buyer_name": municipality.get("name"),
                    "buyer_type": "municipal",
                    "category_raw": None,
                    "category_normalized": None,
                    "region": "ON",
                    "estimated_value": None,
                    "currency": None,
                    "closing_date": closing,
                    "posted_date": None,
                    "notice_url": page_url,
                    "documents_open": documents_open,
                    # An undated notice is "unknown", never "open": these pages mix
                    # live opportunities with years of archive, and guessing open
                    # would push closed 2024 work into recommendations.
                    "status": (
                        normalize_status("open", closing) if closing else "unknown"
                    ),
                },
                "documents": notice["documents"],
            }
        )
    return records


def ingest_municipality(
    connection: sqlite3.Connection,
    municipality: dict,
    client: fetcher.PoliteFetcher,
    now: str | None = None,
) -> dict:
    """Fetch, parse, and store one municipality's notices."""
    slug = str(municipality["slug"])
    page_url = str(municipality.get("procurement_url") or "")
    timestamp = now or db.utc_timestamp()

    if not page_url:
        return _record_run(
            connection, slug, STATUS_PARSER_NEEDED, None, 0, 0,
            "the census recorded no procurement page", timestamp,
        )

    result = client.get(page_url)
    if not result.ok:
        return _record_run(
            connection, slug, STATUS_FETCH_FAILED, None, 0, 0,
            result.error or f"page returned {result.status}", timestamp,
        )

    parsed = parse_notice_page(result.text, result.final_url or page_url)
    if not parsed["notices"]:
        status = STATUS_PARSER_NEEDED if parsed["pattern"] is None else STATUS_NO_NOTICES
        return _record_run(
            connection, slug, status, parsed["pattern"], 0, 0,
            "no notices matched the known page patterns", timestamp,
        )

    records = to_notice_records(municipality, parsed, result.final_url or page_url)
    tally = db.upsert_notices(connection, [item["record"] for item in records])
    documents = _store_documents(connection, records, timestamp)

    return _record_run(
        connection,
        slug,
        STATUS_PARSED,
        parsed["pattern"],
        len(records),
        documents,
        f"inserted {tally['inserted']}, updated {tally['updated']}, "
        f"unchanged {tally['unchanged']}",
        timestamp,
    )


def ingest_all(
    connection: sqlite3.Connection,
    limit: int | None = None,
    client: fetcher.PoliteFetcher | None = None,
    confidence_first: bool = True,
) -> dict[str, Any]:
    """Ingest every municipality the census marked as posting on its own site."""
    ensure_schema(connection)
    client = client or fetcher.PoliteFetcher()

    order = (
        "CASE WHEN confidence = 'high' THEN 0 ELSE 1 END, population DESC"
        if confidence_first
        else "population DESC"
    )
    placeholders = ", ".join("?" for _ in census_schema.INGESTABLE_CLASSES)
    query = (
        f"SELECT * FROM municipalities WHERE classification IN ({placeholders}) "
        f"ORDER BY {order}"
    )
    if limit:
        query += f" LIMIT {int(limit)}"
    targets = [
        dict(row)
        for row in connection.execute(query, census_schema.INGESTABLE_CLASSES).fetchall()
    ]

    LOGGER.info("Parsing %d municipal procurement pages", len(targets))
    counts: dict[str, int] = {}
    notices = 0
    documents = 0
    for municipality in targets:
        outcome = ingest_municipality(connection, municipality, client)
        counts[outcome["status"]] = counts.get(outcome["status"], 0) + 1
        notices += outcome["notices_found"]
        documents += outcome["documents_found"]

    return {
        "municipalities": len(targets),
        "statuses": counts,
        "notices": notices,
        "documents": documents,
    }


def parser_needed(connection: sqlite3.Connection, limit: int = 20) -> list[dict]:
    """Municipalities whose pages defy the known patterns, largest first."""
    rows = connection.execute(
        "SELECT m.name, m.slug, m.population, m.classification, m.confidence, "
        "       m.procurement_url, r.note "
        "FROM municipal_parse_runs r JOIN municipalities m ON m.slug = r.slug "
        "WHERE r.status = ? "
        "ORDER BY (m.population IS NULL), m.population DESC LIMIT ?",
        (STATUS_PARSER_NEEDED, int(limit)),
    ).fetchall()
    return [dict(row) for row in rows]


def top_municipalities(connection: sqlite3.Connection, limit: int = 10) -> list[dict]:
    """Municipalities yielding the most notices."""
    rows = connection.execute(
        "SELECT m.name, m.population, r.notices_found, r.documents_found, r.pattern "
        "FROM municipal_parse_runs r JOIN municipalities m ON m.slug = r.slug "
        "WHERE r.notices_found > 0 ORDER BY r.notices_found DESC LIMIT ?",
        (int(limit),),
    ).fetchall()
    return [dict(row) for row in rows]


def _store_documents(
    connection: sqlite3.Connection, records: list[dict], timestamp: str
) -> int:
    """Store document links for stored notices. Links only — nothing is downloaded."""
    stored = 0
    with connection:
        for item in records:
            row = connection.execute(
                "SELECT id FROM tenders WHERE source = ? AND source_id = ?",
                (SOURCE, item["record"]["source_id"]),
            ).fetchone()
            if row is None:
                continue
            for document in item["documents"]:
                if fetcher.is_platform_url(document["url"]):
                    continue
                connection.execute(
                    "INSERT OR IGNORE INTO notice_documents "
                    "(tender_id, url, filename, link_text, kind, discovered_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        int(row["id"]),
                        document["url"],
                        document["filename"],
                        document["link_text"],
                        document["kind"],
                        timestamp,
                    ),
                )
                stored += 1
    return stored


def _record_run(
    connection: sqlite3.Connection,
    slug: str,
    status: str,
    pattern: str | None,
    notices: int,
    documents: int,
    note: str,
    timestamp: str,
) -> dict:
    with connection:
        connection.execute(
            "INSERT INTO municipal_parse_runs "
            "(slug, status, pattern, notices_found, documents_found, note, parsed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (slug) DO UPDATE SET status = excluded.status, "
            "pattern = excluded.pattern, notices_found = excluded.notices_found, "
            "documents_found = excluded.documents_found, note = excluded.note, "
            "parsed_at = excluded.parsed_at",
            (slug, status, pattern, notices, documents, note, timestamp),
        )
    return {
        "slug": slug,
        "status": status,
        "pattern": pattern,
        "notices_found": notices,
        "documents_found": documents,
        "note": note,
    }


def _title_from(document: dict) -> str:
    """Best available human title for a notice document."""
    text = re.sub(r"\s+", " ", str(document.get("text") or "")).strip()
    if len(text) > 12 and not text.casefold().startswith(("download", "click", "pdf")):
        return text[:200]
    stem = Path(str(document.get("filename") or "")).stem
    stem = re.sub(r"[-_]+", " ", stem).strip()
    return stem[:200].title() if len(stem) > 6 else ""


def _closing_dates(html: str) -> dict[str, str]:
    """Map notice identifiers to any closing date text found beside them."""
    soup = BeautifulSoup(html or "", "html.parser")
    closings: dict[str, str] = {}
    for element in soup.find_all(["tr", "li", "div", "p", "section"]):
        text = re.sub(r"\s+", " ", element.get_text(" ", strip=True))
        if len(text) > 400:
            continue
        identifier = NOTICE_ID_PATTERN.search(text)
        closing = CLOSING_LABEL_PATTERN.search(text)
        if identifier and closing:
            key = re.sub(r"[-_ ]", "-", identifier.group(1)).casefold()
            closings.setdefault(key, closing.group(1).strip())
    return closings


def _parse_date(text: Any) -> str | None:
    """Parse a closing date out of free text, returning ISO-8601 or None."""
    candidate = _fold(text)
    if not candidate:
        return None
    match = re.search(
        r"([a-z]+ \d{1,2},? \d{4}(?: \d{1,2}:\d{2} ?[ap]m)?|\d{4}-\d{2}-\d{2})",
        candidate,
    )
    if match is None:
        return None
    cleaned = match.group(1).replace(",", "").strip()
    for pattern in DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, pattern.replace(",", "")).isoformat()
        except ValueError:
            continue
    return None


def _fold(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).casefold()
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text).strip()


def _main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest notices from municipalities that post on their own sites"
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--db", default=None)
    args = parser.parse_args()

    connection = census_schema.connect(args.db)
    try:
        db.migrate_source_constraint(connection)
        result = ingest_all(connection, limit=args.limit)
        print(
            f"parsed {result['municipalities']} municipal page(s): "
            f"{result['notices']} notice(s), {result['documents']} document link(s)"
        )
        for status, count in sorted(result["statuses"].items()):
            print(f"  {status:<18} {count}")

        top = top_municipalities(connection)
        if top:
            print("\ntop municipalities by notices found:")
            for row in top:
                print(
                    f"  {row['notices_found']:>4} notices  "
                    f"{row['documents_found']:>4} docs  {row['name']}"
                )

        needed = parser_needed(connection)
        if needed:
            print("\nparser_needed, ranked by population:")
            for row in needed:
                print(
                    f"  {row['population'] or 0:>9,}  {row['name'][:38]:<40} "
                    f"{str(row['procurement_url'])[:52]}"
                )
    finally:
        connection.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    _main()
