import sqlite3
import unittest

from notices import db


def _record(**overrides) -> dict:
    record = {
        "source": "canadabuys",
        "source_id": "cb-604-41302770",
        "title": "Heavy construction equipment rental",
        "description": "Rental of heavy equipment.",
        "buyer_name": "Department of National Defence (DND)",
        "buyer_type": "federal",
        "category_raw": "*CNST",
        "category_normalized": "construction",
        "region": "CA",
        "estimated_value": None,
        "currency": None,
        "closing_date": "2026-07-28T14:00:00",
        "posted_date": "2026-07-14T00:00:00",
        "notice_url": "https://canadabuys.canada.ca/en/tender-opportunities/1",
        "documents_open": True,
        "status": "open",
    }
    record.update(overrides)
    return record


class SchemaTests(unittest.TestCase):
    def test_connect_creates_table_columns_and_indexes(self) -> None:
        connection = db.connect(":memory:")
        self.addCleanup(connection.close)

        columns = [
            row["name"]
            for row in connection.execute("PRAGMA table_info(tenders)").fetchall()
        ]
        self.assertEqual(
            columns,
            [
                "id",
                "source",
                "source_id",
                *db.CONTENT_COLUMNS,
                "ingested_at",
                "updated_at",
            ],
        )
        indexes = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            ).fetchall()
        }
        self.assertIn("idx_tenders_closing_date", indexes)
        self.assertIn("idx_tenders_category_region", indexes)

    def test_create_schema_is_safe_to_run_twice(self) -> None:
        connection = db.connect(":memory:")
        self.addCleanup(connection.close)
        db.upsert_notices(connection, [_record()])

        db.create_schema(connection)

        self.assertEqual(db.count_by_source(connection), {"canadabuys": 1})

    def test_source_and_source_id_are_unique(self) -> None:
        connection = db.connect(":memory:")
        self.addCleanup(connection.close)
        values = ("canadabuys", "cb-1", "Title", "open", "now", "now")
        statement = (
            "INSERT INTO tenders (source, source_id, title, status, "
            "ingested_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)"
        )
        connection.execute(statement, values)

        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute(statement, values)

    def test_unknown_source_is_rejected_by_the_check_constraint(self) -> None:
        connection = db.connect(":memory:")
        self.addCleanup(connection.close)

        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO tenders (source, source_id, title, status, "
                "ingested_at, updated_at) VALUES ('merx', 'x', 'T', 'open', 'n', 'n')"
            )


class UpsertTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = db.connect(":memory:")
        self.addCleanup(self.connection.close)

    def _row(self, source_id: str = "cb-604-41302770") -> sqlite3.Row:
        return self.connection.execute(
            "SELECT * FROM tenders WHERE source_id = ?", (source_id,)
        ).fetchone()

    def test_first_ingestion_inserts_and_stamps_both_timestamps(self) -> None:
        tally = db.upsert_notices(self.connection, [_record()], now="2026-07-29T09:00:00")

        self.assertEqual(
            tally, {"inserted": 1, "updated": 0, "unchanged": 0, "skipped": 0}
        )
        row = self._row()
        self.assertEqual(row["ingested_at"], "2026-07-29T09:00:00")
        self.assertEqual(row["updated_at"], "2026-07-29T09:00:00")
        self.assertEqual(row["documents_open"], 1)

    def test_reingesting_identical_records_changes_nothing(self) -> None:
        db.upsert_notices(self.connection, [_record()], now="2026-07-29T09:00:00")
        before = dict(self._row())

        tally = db.upsert_notices(self.connection, [_record()], now="2026-07-30T09:00:00")

        self.assertEqual(
            tally, {"inserted": 0, "updated": 0, "unchanged": 1, "skipped": 0}
        )
        self.assertEqual(dict(self._row()), before)

    def test_changed_content_updates_and_preserves_ingested_at(self) -> None:
        db.upsert_notices(self.connection, [_record()], now="2026-07-29T09:00:00")

        tally = db.upsert_notices(
            self.connection,
            [_record(status="closed", estimated_value=653199)],
            now="2026-07-30T09:00:00",
        )

        self.assertEqual(
            tally, {"inserted": 0, "updated": 1, "unchanged": 0, "skipped": 0}
        )
        row = self._row()
        self.assertEqual(row["status"], "closed")
        self.assertEqual(row["estimated_value"], 653199.0)
        self.assertEqual(row["ingested_at"], "2026-07-29T09:00:00")
        self.assertEqual(row["updated_at"], "2026-07-30T09:00:00")

    def test_same_key_from_different_sources_are_separate_rows(self) -> None:
        db.upsert_notices(
            self.connection,
            [_record(source_id="shared-1"), _record(source="seao", source_id="shared-1")],
        )

        self.assertEqual(
            db.count_by_source(self.connection), {"canadabuys": 1, "seao": 1}
        )

    def test_duplicate_key_within_one_batch_updates_the_first_row(self) -> None:
        tally = db.upsert_notices(
            self.connection,
            [_record(), _record(title="Heavy equipment rental, amended")],
        )

        self.assertEqual(
            tally, {"inserted": 1, "updated": 1, "unchanged": 0, "skipped": 0}
        )
        self.assertEqual(self._row()["title"], "Heavy equipment rental, amended")

    def test_unusable_records_are_skipped_not_fatal(self) -> None:
        with self.assertLogs("notices.db", level="WARNING"):
            tally = db.upsert_notices(
                self.connection,
                [
                    "not-a-record",
                    _record(source="merx"),
                    _record(source_id=""),
                    _record(title="   "),
                    _record(),
                ],
            )

        self.assertEqual(
            tally, {"inserted": 1, "updated": 0, "unchanged": 0, "skipped": 4}
        )

    def test_values_are_coerced_and_blank_strings_become_null(self) -> None:
        db.upsert_notices(
            self.connection,
            [
                _record(
                    description="",
                    documents_open=0,
                    estimated_value="not a number",
                    status="",
                )
            ],
        )

        row = self._row()
        self.assertIsNone(row["description"])
        self.assertEqual(row["documents_open"], 0)
        self.assertIsNone(row["estimated_value"])
        self.assertEqual(row["status"], "unknown")

    def test_sample_rows_orders_by_closing_date_with_nulls_last(self) -> None:
        db.upsert_notices(
            self.connection,
            [
                _record(source_id="late", closing_date="2026-12-01T14:00:00"),
                _record(source_id="never", closing_date=None),
                _record(source_id="soon", closing_date="2026-08-01T14:00:00"),
            ],
        )

        rows = db.sample_rows(self.connection, "canadabuys", limit=3)

        self.assertEqual(
            [row["source_id"] for row in rows], ["soon", "late", "never"]
        )


if __name__ == "__main__":
    unittest.main()
