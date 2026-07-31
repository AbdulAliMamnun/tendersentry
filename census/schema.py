"""The municipalities table: one row per Ontario municipality and its verdict."""

from __future__ import annotations

import logging
import re
import sqlite3
import unicodedata
from typing import Any

from notices import db


LOGGER = logging.getLogger(__name__)

#: How a municipality publishes its tenders. The first six are the classes asked
#: for; the last three record states where no classification was possible, so a
#: blank verdict never masquerades as "no procurement page".
CLASS_OWN_SITE_OPEN = "own_site_open"
CLASS_OWN_SITE_NOTICES = "own_site_notices"
CLASS_BIDS_AND_TENDERS = "bids_and_tenders"
CLASS_BIDDINGO = "biddingo"
CLASS_OTHER_PLATFORM = "bidnet_or_other_platform"
CLASS_NONE_FOUND = "no_procurement_page_found"
CLASS_ROBOTS_DISALLOWED = "robots_disallowed"
CLASS_NO_WEBSITE = "no_website_listed"
CLASS_FETCH_FAILED = "fetch_failed"
CLASS_PENDING = "pending"

CLASSIFICATIONS = (
    CLASS_OWN_SITE_OPEN,
    CLASS_OWN_SITE_NOTICES,
    CLASS_BIDS_AND_TENDERS,
    CLASS_BIDDINGO,
    CLASS_OTHER_PLATFORM,
    CLASS_NONE_FOUND,
    CLASS_ROBOTS_DISALLOWED,
    CLASS_NO_WEBSITE,
    CLASS_FETCH_FAILED,
    CLASS_PENDING,
)

#: Classes whose notice pages Phase B parses.
INGESTABLE_CLASSES = (CLASS_OWN_SITE_OPEN, CLASS_OWN_SITE_NOTICES)

CONFIDENCE_HIGH = "high"
CONFIDENCE_LOW = "low"

CONTENT_COLUMNS = (
    "name",
    "tier",
    "geographic_area",
    "website_url",
    "website_host",
    "population",
    "population_source",
)

RESULT_COLUMNS = (
    "classification",
    "confidence",
    "platform",
    "procurement_url",
    "evidence_url",
    "evidence_note",
    "cms_fingerprint",
    "robots_ok",
    "http_status",
    "requests_made",
    "checked_at",
)

SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS municipalities (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        slug TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL,
        tier TEXT NOT NULL,
        geographic_area TEXT,
        website_url TEXT,
        website_host TEXT,
        population INTEGER,
        population_source TEXT,
        classification TEXT NOT NULL DEFAULT 'pending',
        confidence TEXT,
        platform TEXT,
        procurement_url TEXT,
        evidence_url TEXT,
        evidence_note TEXT,
        cms_fingerprint TEXT,
        robots_ok INTEGER,
        http_status INTEGER,
        requests_made INTEGER,
        checked_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_municipalities_class ON municipalities (classification)",
    "CREATE INDEX IF NOT EXISTS idx_municipalities_checked ON municipalities (checked_at)",
)


def connect(db_path: Any = None) -> sqlite3.Connection:
    """Open the shared database with the municipalities table applied."""
    connection = db.connect(db_path)
    create_schema(connection)
    return connection


def create_schema(connection: sqlite3.Connection) -> None:
    """Create the municipalities table when it does not yet exist."""
    with connection:
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)


#: Municipal status -> slug suffix. The status is part of the slug because six
#: Ontario names are shared by two distinct governments — the City of Hamilton and
#: Hamilton Township, the Region of Waterloo and the City of Waterloo, and four more.
#: Dropping the status silently merged those pairs and lost one of each.
STATUS_SUFFIXES = (
    ("regional municipality", "region"),
    ("united counties", "united-counties"),
    ("separated town", "town"),
    ("township", "township"),
    ("municipality", "municipality"),
    ("village", "village"),
    ("county", "county"),
    ("district", "district"),
    ("city", "city"),
    ("town", "town"),
)


def slugify(name: str) -> str:
    """Return a stable, collision-free slug for an MMAH municipality name.

    Deterministic per record rather than per roster: a slug never shifts because
    some other municipality was added, which matters because Phase B builds notice
    identifiers out of these.
    """
    folded = unicodedata.normalize("NFKD", str(name)).casefold()
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))

    base, _, status = folded.partition(",")
    suffix = ""
    if status:
        for term, token in STATUS_SUFFIXES:
            if term in status:
                suffix = token
                break

    slug = re.sub(r"[^a-z0-9]+", "-", base).strip("-")
    if suffix:
        slug = f"{slug}-{suffix}"
    return slug or "unnamed"


def upsert_municipalities(
    connection: sqlite3.Connection, records: list[dict], now: str | None = None
) -> dict[str, int]:
    """Insert or update the roster, leaving classification results untouched."""
    timestamp = now or db.utc_timestamp()
    tally = {"inserted": 0, "updated": 0, "unchanged": 0}

    with connection:
        for record in records:
            slug = str(record.get("slug") or "")
            if not slug:
                continue
            existing = connection.execute(
                "SELECT " + ", ".join(CONTENT_COLUMNS) + " FROM municipalities "
                "WHERE slug = ?",
                (slug,),
            ).fetchone()
            values = [record.get(column) for column in CONTENT_COLUMNS]

            if existing is None:
                connection.execute(
                    "INSERT INTO municipalities (slug, "
                    + ", ".join(CONTENT_COLUMNS)
                    + ", created_at, updated_at) VALUES (?, "
                    + ", ".join("?" for _ in CONTENT_COLUMNS)
                    + ", ?, ?)",
                    [slug, *values, timestamp, timestamp],
                )
                tally["inserted"] += 1
                continue

            changed = [
                column
                for column, value in zip(CONTENT_COLUMNS, values)
                if existing[column] != value
            ]
            if not changed:
                tally["unchanged"] += 1
                continue
            connection.execute(
                "UPDATE municipalities SET "
                + ", ".join(f"{column} = ?" for column in changed)
                + ", updated_at = ? WHERE slug = ?",
                [*(record.get(column) for column in changed), timestamp, slug],
            )
            tally["updated"] += 1

    LOGGER.info(
        "Municipality roster: inserted %d, updated %d, unchanged %d",
        tally["inserted"],
        tally["updated"],
        tally["unchanged"],
    )
    return tally


def record_result(
    connection: sqlite3.Connection, slug: str, result: dict, now: str | None = None
) -> None:
    """Store one municipality's classification verdict."""
    timestamp = now or db.utc_timestamp()
    values = [result.get(column) for column in RESULT_COLUMNS[:-1]]
    with connection:
        connection.execute(
            "UPDATE municipalities SET "
            + ", ".join(f"{column} = ?" for column in RESULT_COLUMNS[:-1])
            + ", checked_at = ?, updated_at = ? WHERE slug = ?",
            [*values, result.get("checked_at") or timestamp, timestamp, slug],
        )


def pending_municipalities(
    connection: sqlite3.Connection,
    limit: int | None = None,
    resume: bool = True,
    recheck_before: str | None = None,
    recheck_classes: tuple[str, ...] | None = None,
) -> list[dict]:
    """Return municipalities still needing a verdict, newest-first by population.

    With ``resume`` the already-classified are skipped, which is what makes a run
    that dies after four hours cost nothing. ``recheck_before`` re-queues rows whose
    ``checked_at`` predates a cutoff.
    """
    clauses = ["website_url IS NOT NULL", "website_url != ''"]
    params: list[Any] = []
    if resume:
        condition = "(checked_at IS NULL OR classification = ?)"
        params.append(CLASS_PENDING)
        if recheck_before:
            condition = f"({condition} OR checked_at < ?)"
            params.append(recheck_before)
        if recheck_classes:
            placeholders = ", ".join("?" for _ in recheck_classes)
            condition = f"({condition} OR classification IN ({placeholders}))"
            params.extend(recheck_classes)
        clauses.append(condition)

    query = (
        "SELECT * FROM municipalities WHERE "
        + " AND ".join(clauses)
        + " ORDER BY (population IS NULL), population DESC, name"
    )
    if limit:
        query += f" LIMIT {int(limit)}"
    return [dict(row) for row in connection.execute(query, params).fetchall()]


def distribution(connection: sqlite3.Connection) -> list[dict]:
    """Return the census distribution with counts and population shares."""
    rows = connection.execute(
        "SELECT classification, COUNT(*) AS municipalities, "
        "       COALESCE(SUM(population), 0) AS population, "
        "       SUM(CASE WHEN population IS NULL THEN 1 ELSE 0 END) AS population_unknown "
        "FROM municipalities GROUP BY classification"
    ).fetchall()
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
        population = int(row["population"] or 0)
        result.append(
            {
                "classification": row["classification"],
                "municipalities": int(row["municipalities"]),
                "share_of_municipalities": (
                    round(100.0 * int(row["municipalities"]) / total_count, 1)
                    if total_count
                    else 0.0
                ),
                "population": population,
                "share_of_population": (
                    round(100.0 * population / total_population, 1)
                    if total_population
                    else 0.0
                ),
                "population_unknown": int(row["population_unknown"]),
            }
        )
    result.sort(key=lambda item: -item["population"])
    return result


def population_coverage(connection: sqlite3.Connection) -> dict[str, int]:
    """How many municipalities carry a population figure."""
    row = connection.execute(
        "SELECT COUNT(*) AS total, "
        "       SUM(CASE WHEN population IS NOT NULL THEN 1 ELSE 0 END) AS matched "
        "FROM municipalities"
    ).fetchone()
    return {"total": int(row["total"]), "matched": int(row["matched"] or 0)}
