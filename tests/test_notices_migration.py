import sqlite3
import unittest

from notices import db


LEGACY_DDL = """
CREATE TABLE tenders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL
        CHECK (source IN ('canadabuys', 'seao', 'bidsandtenders')),
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
"""


def _legacy_connection(rows: int = 3) -> sqlite3.Connection:
    """A database at the pre-migration schema, as Milestone 1 shipped it."""
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute(LEGACY_DDL)
    connection.execute(
        "CREATE INDEX idx_tenders_closing_date ON tenders (closing_date)"
    )
    connection.execute("CREATE INDEX idx_tenders_status ON tenders (status)")
    for index in range(rows):
        connection.execute(
            "INSERT INTO tenders (source, source_id, title, status, "
            "ingested_at, updated_at) VALUES ('canadabuys', ?, ?, 'open', ?, ?)",
            (f"cb-{index}", f"Notice {index}", "2026-07-29T09:00:00", "2026-07-29T09:00:00"),
        )
    connection.commit()
    return connection


class MigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = _legacy_connection()
        self.addCleanup(self.connection.close)

    def _check_sources(self) -> set[str]:
        sql = self.connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'tenders'"
        ).fetchone()["sql"]
        import re

        match = re.search(r"CHECK\s*\(\s*source\s+IN\s*\(([^)]*)\)\s*\)", sql, re.I)
        return {
            value.strip().strip("'\"") for value in match.group(1).split(",")
        }

    def test_the_legacy_schema_rejects_municipal_site_before_migrating(self) -> None:
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                "INSERT INTO tenders (source, source_id, title, status, "
                "ingested_at, updated_at) VALUES "
                "('municipal_site', 'x', 'T', 'open', 'n', 'n')"
            )

    def test_migrating_adds_the_new_source_and_keeps_every_row(self) -> None:
        result = db.migrate_source_constraint(self.connection)

        self.assertTrue(result["migrated"])
        self.assertEqual(result["rows_before"], 3)
        self.assertEqual(result["rows_after"], 3)
        self.assertEqual(result["added_sources"], ["municipal_site"])
        self.assertEqual(self._check_sources(), set(db.SOURCES))

    def test_municipal_notices_are_accepted_after_migrating(self) -> None:
        db.migrate_source_constraint(self.connection)

        tally = db.upsert_notices(
            self.connection,
            [
                {
                    "source": "municipal_site",
                    "source_id": "muskoka-lakes:t-2026-28",
                    "title": "Removal and replacement of fuel tanks",
                }
            ],
        )

        self.assertEqual(tally["inserted"], 1)

    def test_an_unknown_source_is_still_rejected_after_migrating(self) -> None:
        db.migrate_source_constraint(self.connection)

        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                "INSERT INTO tenders (source, source_id, title, status, "
                "ingested_at, updated_at) VALUES "
                "('merx', 'x', 'T', 'open', 'n', 'n')"
            )

    def test_the_migration_is_idempotent(self) -> None:
        db.migrate_source_constraint(self.connection)

        second = db.migrate_source_constraint(self.connection)

        self.assertFalse(second["migrated"])
        self.assertEqual(second["reason"], "already current")

    def test_indexes_survive_the_rebuild(self) -> None:
        before = {
            row["name"]
            for row in self.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' "
                "AND tbl_name = 'tenders' AND sql IS NOT NULL"
            )
        }

        db.migrate_source_constraint(self.connection)

        after = {
            row["name"]
            for row in self.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' "
                "AND tbl_name = 'tenders' AND sql IS NOT NULL"
            )
        }
        self.assertEqual(before, after)

    def test_additive_columns_from_later_milestones_are_carried_over(self) -> None:
        self.connection.execute("ALTER TABLE tenders ADD COLUMN closing_date_utc TEXT")
        self.connection.execute(
            "UPDATE tenders SET closing_date_utc = '2026-08-01T18:00:00+00:00'"
        )

        db.migrate_source_constraint(self.connection)

        columns = [
            row["name"]
            for row in self.connection.execute("PRAGMA table_info(tenders)")
        ]
        self.assertIn("closing_date_utc", columns)
        self.assertEqual(
            self.connection.execute(
                "SELECT closing_date_utc FROM tenders LIMIT 1"
            ).fetchone()[0],
            "2026-08-01T18:00:00+00:00",
        )

    def test_row_data_survives_intact(self) -> None:
        before = [
            dict(row) for row in self.connection.execute("SELECT * FROM tenders ORDER BY id")
        ]

        db.migrate_source_constraint(self.connection)

        after = [
            dict(row) for row in self.connection.execute("SELECT * FROM tenders ORDER BY id")
        ]
        self.assertEqual(before, after)

    def test_a_failed_copy_leaves_the_original_table_intact(self) -> None:
        original = [
            dict(row) for row in self.connection.execute("SELECT * FROM tenders ORDER BY id")
        ]

        # A view occupying the scratch name forces the rebuild to fail partway:
        # DROP TABLE refuses to remove a view, so the migration raises mid-transaction.
        self.connection.execute("CREATE VIEW tenders_migrated AS SELECT 1 AS id")
        self.connection.commit()

        with self.assertLogs("notices.db", level="ERROR"):
            with self.assertRaises(sqlite3.Error):
                db.migrate_source_constraint(self.connection)

        self.assertEqual(
            [dict(row) for row in self.connection.execute("SELECT * FROM tenders ORDER BY id")],
            original,
        )
        self.assertEqual(self._check_sources(), {"canadabuys", "seao", "bidsandtenders"})

    def test_a_database_without_the_table_is_reported_not_crashed(self) -> None:
        empty = sqlite3.connect(":memory:")
        empty.row_factory = sqlite3.Row
        self.addCleanup(empty.close)

        result = db.migrate_source_constraint(empty)

        self.assertFalse(result["migrated"])
        self.assertEqual(result["reason"], "no tenders table")


if __name__ == "__main__":
    unittest.main()
