"""Side-tables and the closing_date_utc migration for the recommendation engine.

Nothing here rewrites what the ingesters own: ``closing_date_utc`` is an additive
column derived from ``closing_date``, and every other table is keyed off
``tenders.id``. Milestone 3 can move the derivation into ``notices.db._prepare`` and
delete ``backfill_closing_dates`` without touching anything else.
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
from typing import Any, Iterable

from matchrec import timeutil
from notices import db
from profiles import schema as profiles_schema


LOGGER = logging.getLogger(__name__)

CLOSING_DATE_UTC_COLUMN = "closing_date_utc"

SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS notice_trades (
        tender_id INTEGER PRIMARY KEY REFERENCES tenders (id),
        trade_slugs TEXT NOT NULL DEFAULT '[]',
        slug_sources TEXT NOT NULL DEFAULT '{}',
        mapping_status TEXT NOT NULL,
        matched_terms TEXT NOT NULL DEFAULT '[]',
        construction_marked INTEGER NOT NULL DEFAULT 0,
        mapping_version TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS firm_notice_exclusions (
        firm_id INTEGER NOT NULL REFERENCES firms (id),
        tender_id INTEGER NOT NULL REFERENCES tenders (id),
        primary_reason TEXT NOT NULL,
        reasons TEXT NOT NULL DEFAULT '[]',
        detail TEXT,
        evaluated_at TEXT NOT NULL,
        PRIMARY KEY (firm_id, tender_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS firm_notice_scores (
        firm_id INTEGER NOT NULL REFERENCES firms (id),
        tender_id INTEGER NOT NULL REFERENCES tenders (id),
        base_score REAL NOT NULL,
        value_modifier REAL NOT NULL DEFAULT 0,
        final_score REAL NOT NULL,
        components TEXT NOT NULL DEFAULT '{}',
        flags TEXT NOT NULL DEFAULT '[]',
        weights_version TEXT NOT NULL,
        mapping_version TEXT NOT NULL,
        scored_at TEXT NOT NULL,
        PRIMARY KEY (firm_id, tender_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_scores_firm_rank
        ON firm_notice_scores (firm_id, final_score DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_exclusions_reason
        ON firm_notice_exclusions (firm_id, primary_reason)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_notice_trades_status
        ON notice_trades (mapping_status)
    """,
)

#: Columns compared when deciding whether a stored row actually changed.
SCORE_CONTENT_COLUMNS = (
    "base_score",
    "value_modifier",
    "final_score",
    "components",
    "flags",
    "weights_version",
    "mapping_version",
)
EXCLUSION_CONTENT_COLUMNS = ("primary_reason", "reasons", "detail")
TRADE_CONTENT_COLUMNS = (
    "trade_slugs",
    "slug_sources",
    "mapping_status",
    "matched_terms",
    "construction_marked",
    "mapping_version",
)

#: Columns added to existing tables after their first release, as
#: (table, column, definition). Applied by ``ensure_schema``.
ADDITIVE_COLUMNS = (
    ("tenders", CLOSING_DATE_UTC_COLUMN, "TEXT"),
    ("notice_trades", "slug_sources", "TEXT NOT NULL DEFAULT '{}'"),
    ("notice_trades", "construction_marked", "INTEGER NOT NULL DEFAULT 0"),
)


def connect(db_path: Any = None) -> sqlite3.Connection:
    """Open the shared database with notices, firms, and matchrec schemas applied."""
    connection = profiles_schema.connect(db_path)
    ensure_schema(connection)
    return connection


def ensure_schema(connection: sqlite3.Connection) -> None:
    """Create the side-tables and apply any additive columns still missing."""
    _add_closing_date_utc_column(connection)
    with connection:
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
    for table, column, definition in ADDITIVE_COLUMNS:
        _add_column_if_missing(connection, table, column, definition)


def backfill_closing_dates(
    connection: sqlite3.Connection, batch_size: int = 5000
) -> dict[str, int]:
    """Recompute closing_date_utc for every notice and return the tally.

    Recomputes rather than filling only NULLs, so a re-ingested notice whose deadline
    moved cannot leave a stale UTC value behind. Only rows whose value actually
    changes are written.
    """
    tally = {"updated": 0, "unchanged": 0, "missing_closing_date": 0}
    pending: list[tuple[str | None, int]] = []

    rows = connection.execute(
        f"SELECT id, closing_date, {CLOSING_DATE_UTC_COLUMN} FROM tenders"
    ).fetchall()
    for row in rows:
        computed = timeutil.utc_iso(row["closing_date"])
        if computed is None:
            tally["missing_closing_date"] += 1
        if computed == row[CLOSING_DATE_UTC_COLUMN]:
            tally["unchanged"] += 1
            continue
        pending.append((computed, int(row["id"])))
        if len(pending) >= batch_size:
            _write_closing_dates(connection, pending)
            tally["updated"] += len(pending)
            pending = []

    if pending:
        _write_closing_dates(connection, pending)
        tally["updated"] += len(pending)

    LOGGER.info(
        "closing_date_utc backfill: updated %d, unchanged %d, without a closing date %d",
        tally["updated"],
        tally["unchanged"],
        tally["missing_closing_date"],
    )
    return tally


def upsert_rows(
    connection: sqlite3.Connection,
    table: str,
    key_columns: tuple[str, ...],
    content_columns: tuple[str, ...],
    timestamp_column: str,
    rows: Iterable[dict],
    now: str,
) -> dict[str, int]:
    """Insert or update rows, leaving the timestamp alone when nothing changed.

    The same compare-then-write discipline ``notices.db.upsert_notices`` uses, so a
    re-run over unchanged inputs is observably a no-op rather than silent churn.
    """
    tally = {"inserted": 0, "updated": 0, "unchanged": 0}
    all_columns = (*key_columns, *content_columns)
    where = " AND ".join(f"{column} = ?" for column in key_columns)

    with connection:
        for row in rows:
            key_values = [row[column] for column in key_columns]
            existing = connection.execute(
                f"SELECT {', '.join(content_columns)} FROM {table} WHERE {where}",
                key_values,
            ).fetchone()

            if existing is None:
                connection.execute(
                    f"INSERT INTO {table} ({', '.join(all_columns)}, "
                    f"{timestamp_column}) VALUES ("
                    + ", ".join("?" for _ in all_columns)
                    + ", ?)",
                    [*(row[column] for column in all_columns), now],
                )
                tally["inserted"] += 1
                continue

            changed = [
                column
                for column in content_columns
                if not _values_equal(existing[column], row[column])
            ]
            if not changed:
                tally["unchanged"] += 1
                continue
            connection.execute(
                f"UPDATE {table} SET "
                + ", ".join(f"{column} = ?" for column in changed)
                + f", {timestamp_column} = ? WHERE {where}",
                [*(row[column] for column in changed), now, *key_values],
            )
            tally["updated"] += 1
    return tally


def delete_stale(
    connection: sqlite3.Connection,
    table: str,
    firm_id: int,
    keep_tender_ids: set[int],
) -> int:
    """Remove a firm's rows in one table that the current run did not produce."""
    existing = {
        int(row["tender_id"])
        for row in connection.execute(
            f"SELECT tender_id FROM {table} WHERE firm_id = ?", (firm_id,)
        ).fetchall()
    }
    stale = existing - keep_tender_ids
    if not stale:
        return 0
    with connection:
        connection.executemany(
            f"DELETE FROM {table} WHERE firm_id = ? AND tender_id = ?",
            [(firm_id, tender_id) for tender_id in sorted(stale)],
        )
    LOGGER.info("Removed %d stale row(s) from %s for firm %d", len(stale), table, firm_id)
    return len(stale)


def dumps(value: Any) -> str:
    """Serialize a JSON column value deterministically."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def loads(value: Any, default: Any) -> Any:
    """Deserialize a JSON column value, falling back to a default."""
    try:
        return json.loads(value) if value else default
    except (TypeError, json.JSONDecodeError):
        return default


def _add_closing_date_utc_column(connection: sqlite3.Connection) -> None:
    columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(tenders)").fetchall()
    }
    if not columns:
        raise RuntimeError(
            "The tenders table does not exist; run python3 -m notices.ingest first"
        )
    if CLOSING_DATE_UTC_COLUMN in columns:
        return
    with connection:
        connection.execute(
            f"ALTER TABLE tenders ADD COLUMN {CLOSING_DATE_UTC_COLUMN} TEXT"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_tenders_closing_utc "
            f"ON tenders ({CLOSING_DATE_UTC_COLUMN})"
        )
    LOGGER.info("Added %s to the tenders table", CLOSING_DATE_UTC_COLUMN)


def _add_column_if_missing(
    connection: sqlite3.Connection, table: str, column: str, definition: str
) -> None:
    """Add a column to an existing table, so old databases upgrade in place."""
    existing = {
        row["name"]
        for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if not existing or column in existing:
        return
    with connection:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    LOGGER.info("Added %s.%s", table, column)


def _write_closing_dates(
    connection: sqlite3.Connection, pending: list[tuple[str | None, int]]
) -> None:
    with connection:
        connection.executemany(
            f"UPDATE tenders SET {CLOSING_DATE_UTC_COLUMN} = ? WHERE id = ?", pending
        )


def _values_equal(stored: Any, incoming: Any) -> bool:
    if stored is None or incoming is None:
        return stored is None and incoming is None
    if isinstance(stored, float) or isinstance(incoming, float):
        return abs(float(stored) - float(incoming)) < 1e-9
    return stored == incoming


def _main() -> None:
    parser = argparse.ArgumentParser(
        description="Create matchrec tables and backfill closing_date_utc"
    )
    parser.add_argument("--db", default=None)
    args = parser.parse_args()

    connection = connect(args.db)
    try:
        tally = backfill_closing_dates(connection)
        totals = db.count_by_source(connection)
    finally:
        connection.close()
    print(
        f"closing_date_utc: updated {tally['updated']}, "
        f"unchanged {tally['unchanged']}, "
        f"without a closing date {tally['missing_closing_date']}"
    )
    print(f"notices in db: {totals}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    _main()
