"""Create and idempotently populate the unified tender notice table."""

from __future__ import annotations

import logging
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import config


LOGGER = logging.getLogger(__name__)

SOURCES = ("canadabuys", "seao", "bidsandtenders", "municipal_site")

#: Columns that describe the notice itself. A re-ingestion that leaves every one
#: of these unchanged must not touch ``updated_at``, which is what makes repeated
#: runs observably idempotent rather than merely convergent.
CONTENT_COLUMNS = (
    "title",
    "description",
    "buyer_name",
    "buyer_type",
    "category_raw",
    "category_normalized",
    "region",
    "estimated_value",
    "currency",
    "closing_date",
    "posted_date",
    "notice_url",
    "documents_open",
    "status",
)

SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS tenders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT NOT NULL
            CHECK (source IN ('canadabuys', 'seao', 'bidsandtenders',
                              'municipal_site')),
        source_id TEXT NOT NULL,
        title TEXT NOT NULL,
        description TEXT,
        buyer_name TEXT,
        buyer_type TEXT,
        category_raw TEXT,
        category_normalized TEXT,
        region TEXT,
        estimated_value REAL,
        currency TEXT,
        closing_date TEXT,
        posted_date TEXT,
        notice_url TEXT,
        documents_open INTEGER NOT NULL DEFAULT 0
            CHECK (documents_open IN (0, 1)),
        status TEXT NOT NULL DEFAULT 'unknown',
        ingested_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE (source, source_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_tenders_closing_date ON tenders (closing_date)",
    """
    CREATE INDEX IF NOT EXISTS idx_tenders_category_region
        ON tenders (category_normalized, region)
    """,
    "CREATE INDEX IF NOT EXISTS idx_tenders_status ON tenders (status)",
)


def database_path(db_path: Path | str | None = None) -> Path:
    """Resolve the database location, defaulting to the configured path."""
    return Path(db_path) if db_path is not None else Path(config.NOTICES_DB_PATH)


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Open a connection with the schema applied and row access by name."""
    path = database_path(db_path)
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    create_schema(connection)
    return connection


def create_schema(connection: sqlite3.Connection) -> None:
    """Create the tenders table and its indexes when they do not yet exist."""
    with connection:
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)


def upsert_notices(
    connection: sqlite3.Connection,
    records: Iterable[dict],
    now: str | None = None,
) -> dict[str, int]:
    """Insert or update notices by (source, source_id) and return the tally."""
    timestamp = now or utc_timestamp()
    tally = {"inserted": 0, "updated": 0, "unchanged": 0, "skipped": 0}

    with connection:
        for record in records:
            prepared = _prepare(record)
            if prepared is None:
                tally["skipped"] += 1
                continue
            existing = connection.execute(
                "SELECT id, "
                + ", ".join(CONTENT_COLUMNS)
                + " FROM tenders WHERE source = ? AND source_id = ?",
                (prepared["source"], prepared["source_id"]),
            ).fetchone()

            if existing is None:
                columns = ["source", "source_id", *CONTENT_COLUMNS]
                connection.execute(
                    f"INSERT INTO tenders ({', '.join(columns)}, "
                    "ingested_at, updated_at) VALUES ("
                    + ", ".join("?" for _ in columns)
                    + ", ?, ?)",
                    [*(prepared[column] for column in columns), timestamp, timestamp],
                )
                tally["inserted"] += 1
                continue

            changed = [
                column
                for column in CONTENT_COLUMNS
                if not _values_equal(existing[column], prepared[column])
            ]
            if not changed:
                tally["unchanged"] += 1
                continue
            connection.execute(
                "UPDATE tenders SET "
                + ", ".join(f"{column} = ?" for column in changed)
                + ", updated_at = ? WHERE id = ?",
                [*(prepared[column] for column in changed), timestamp, existing["id"]],
            )
            tally["updated"] += 1
            LOGGER.debug(
                "Updated %s/%s: %s",
                prepared["source"],
                prepared["source_id"],
                ", ".join(changed),
            )

    LOGGER.info(
        "Upsert tally: inserted %d, updated %d, unchanged %d, skipped %d",
        tally["inserted"],
        tally["updated"],
        tally["unchanged"],
        tally["skipped"],
    )
    return tally


#: Scale columns, added after the table shipped. See ``model.scale``.
SCALE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("scale_band", "TEXT"),
    ("scale_source", "TEXT"),
    ("scale_confidence", "REAL"),
)


def migrate_scale_columns(connection: sqlite3.Connection) -> dict[str, Any]:
    """Add the estimated-scale columns to ``tenders``.

    Purely additive, so unlike :func:`migrate_source_constraint` this needs no table
    rebuild — ``ALTER TABLE ADD COLUMN`` is safe and the existing rows simply carry
    NULL until a backfill runs. The CHECK constraints are enforced in the same spirit
    as the source constraint: a value outside the vocabulary is a bug that should fail
    at the write, not surface later as a band nobody can explain.

    Idempotent: columns that already exist are left alone, so this can run on every
    backfill without a guard at the call site.
    """
    existing = {
        row["name"] for row in connection.execute("PRAGMA table_info(tenders)").fetchall()
    }
    if not existing:
        return {"migrated": False, "reason": "no tenders table"}

    before = int(connection.execute("SELECT COUNT(*) FROM tenders").fetchone()[0])
    added = []
    with connection:
        for name, sql_type in SCALE_COLUMNS:
            if name in existing:
                continue
            connection.execute(f"ALTER TABLE tenders ADD COLUMN {name} {sql_type}")
            added.append(name)

    after = int(connection.execute("SELECT COUNT(*) FROM tenders").fetchone()[0])
    if before != after:
        raise RuntimeError(
            f"Row count changed during an additive migration: {before} -> {after}"
        )

    if added:
        LOGGER.info("Added scale columns to tenders: %s", ", ".join(added))
    return {"migrated": bool(added), "added": added, "rows": after}


def migrate_source_constraint(connection: sqlite3.Connection) -> dict[str, Any]:
    """Rebuild ``tenders`` so its source CHECK matches ``SOURCES``.

    SQLite cannot alter a CHECK constraint in place, so this creates a new table,
    copies every row, and swaps the names — all inside one transaction, with the
    row count verified before and after. A failure anywhere rolls back and leaves
    the original table untouched. Idempotent: a table whose CHECK already lists
    every source is left alone.
    """
    current = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'tenders'"
    ).fetchone()
    if current is None:
        return {"migrated": False, "reason": "no tenders table"}

    original_sql = str(current["sql"])
    match = re.search(r"CHECK\s*\(\s*source\s+IN\s*\(([^)]*)\)\s*\)", original_sql, re.I)
    if match is None:
        return {"migrated": False, "reason": "no source CHECK constraint found"}

    existing = {
        value.strip().strip("'\"") for value in match.group(1).split(",") if value.strip()
    }
    if set(SOURCES) <= existing:
        LOGGER.info("Source CHECK already allows %s", ", ".join(sorted(SOURCES)))
        return {"migrated": False, "reason": "already current", "sources": sorted(existing)}

    before = int(connection.execute("SELECT COUNT(*) FROM tenders").fetchone()[0])
    columns = [
        row["name"] for row in connection.execute("PRAGMA table_info(tenders)").fetchall()
    ]
    indexes = [
        str(row["sql"])
        for row in connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' "
            "AND tbl_name = 'tenders' AND sql IS NOT NULL"
        ).fetchall()
    ]

    allowed = ", ".join(f"'{source}'" for source in SOURCES)
    migrated_sql = (
        original_sql[: match.start()]
        + f"CHECK (source IN ({allowed}))"
        + original_sql[match.end() :]
    ).replace("CREATE TABLE tenders", "CREATE TABLE tenders_migrated", 1)
    if "tenders_migrated" not in migrated_sql:
        raise RuntimeError("Could not rewrite the tenders DDL; migration aborted")

    column_list = ", ".join(columns)
    LOGGER.info("Migrating tenders: %d rows, %d index(es)", before, len(indexes))
    # The rebuild owns its transaction, so any pending work from the caller is
    # committed first rather than being swept into this one and rolled back with it.
    if connection.in_transaction:
        connection.commit()
    try:
        connection.execute("BEGIN")
        connection.execute("DROP TABLE IF EXISTS tenders_migrated")
        connection.execute(migrated_sql)
        connection.execute(
            f"INSERT INTO tenders_migrated ({column_list}) "
            f"SELECT {column_list} FROM tenders"
        )
        copied = int(
            connection.execute("SELECT COUNT(*) FROM tenders_migrated").fetchone()[0]
        )
        if copied != before:
            raise RuntimeError(
                f"Row count changed during migration: {before} before, {copied} copied"
            )
        connection.execute("DROP TABLE tenders")
        connection.execute("ALTER TABLE tenders_migrated RENAME TO tenders")
        for statement in indexes:
            connection.execute(statement)
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        LOGGER.error("Migration failed and was rolled back; tenders is unchanged")
        raise

    after = int(connection.execute("SELECT COUNT(*) FROM tenders").fetchone()[0])
    if after != before:
        raise RuntimeError(
            f"Row count changed across migration: {before} before, {after} after"
        )
    LOGGER.info(
        "Migrated tenders to allow %s (%d rows preserved)",
        ", ".join(sorted(set(SOURCES) - existing)),
        after,
    )
    return {
        "migrated": True,
        "rows_before": before,
        "rows_after": after,
        "added_sources": sorted(set(SOURCES) - existing),
        "indexes_restored": len(indexes),
    }


def count_by_source(connection: sqlite3.Connection) -> dict[str, int]:
    """Return the stored row count per source."""
    rows = connection.execute(
        "SELECT source, COUNT(*) AS total FROM tenders GROUP BY source ORDER BY source"
    ).fetchall()
    return {str(row["source"]): int(row["total"]) for row in rows}


def sample_rows(
    connection: sqlite3.Connection,
    source: str,
    limit: int = 3,
    now: str | None = None,
) -> list[dict]:
    """Return the next-closing rows for one source, for CLI reporting.

    Upcoming deadlines come first (soonest first), then the most recently closed,
    then rows with no deadline at all. Comparison is lexicographic over ISO-8601
    strings, which is good enough for ordering a sample even though stored offsets
    vary by source.
    """
    reference = now or utc_timestamp()
    rows = connection.execute(
        "SELECT * FROM tenders WHERE source = ? "
        "ORDER BY (closing_date IS NULL), "
        "         (closing_date < ?), "
        "         CASE WHEN closing_date >= ? THEN closing_date END ASC, "
        "         closing_date DESC, source_id "
        "LIMIT ?",
        (source, reference, reference, int(limit)),
    ).fetchall()
    return [dict(row) for row in rows]


def utc_timestamp() -> str:
    """Return the current local time as a timezone-aware ISO-8601 string."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _prepare(record: Any) -> dict | None:
    """Validate and coerce one record, logging and rejecting unusable input."""
    if not isinstance(record, dict):
        LOGGER.warning("Skipping non-object notice record: %r", record)
        return None

    source = _text(record.get("source"))
    if source not in SOURCES:
        LOGGER.warning("Skipping notice with unknown source %r", source)
        return None
    source_id = _text(record.get("source_id"))
    if not source_id:
        LOGGER.warning("Skipping %s notice with missing source_id", source)
        return None
    title = _text(record.get("title"))
    if not title:
        LOGGER.warning("Skipping %s notice %s with missing title", source, source_id)
        return None

    prepared: dict[str, Any] = {
        "source": source,
        "source_id": source_id,
        "title": title,
        "description": _nullable_text(record.get("description")),
        "buyer_name": _nullable_text(record.get("buyer_name")),
        "buyer_type": _nullable_text(record.get("buyer_type")),
        "category_raw": _nullable_text(record.get("category_raw")),
        "category_normalized": _nullable_text(record.get("category_normalized")),
        "region": _nullable_text(record.get("region")),
        "estimated_value": _nullable_float(record.get("estimated_value")),
        "currency": _nullable_text(record.get("currency")),
        "closing_date": _nullable_text(record.get("closing_date")),
        "posted_date": _nullable_text(record.get("posted_date")),
        "notice_url": _nullable_text(record.get("notice_url")),
        "documents_open": 1 if record.get("documents_open") else 0,
        "status": _text(record.get("status")) or "unknown",
    }
    return prepared


def _values_equal(stored: Any, incoming: Any) -> bool:
    """Compare a stored column against an incoming value tolerantly."""
    if stored is None or incoming is None:
        return stored is None and incoming is None
    if isinstance(stored, float) or isinstance(incoming, float):
        return float(stored) == float(incoming)
    return stored == incoming


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _nullable_text(value: Any) -> str | None:
    text = _text(value)
    return text or None


def _nullable_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        LOGGER.warning("Discarding non-numeric estimated_value %r", value)
        return None
