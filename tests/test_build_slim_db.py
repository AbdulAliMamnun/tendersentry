"""The projection that produces the committed daily database.

Two properties matter more than the size reduction. The projection must not lose or
alter a row it claims to keep, and the result must be structurally incapable of
retraining — no `bid_interactions`, so `--refit` and `--fit` cannot run against it
whatever anyone passes on the command line.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from matchrec import schema as matchrec_schema
from notices import db
from profiles import schema as profiles_schema
from scripts import build_slim_db


def _seed(path: Path, notices: int = 12) -> None:
    """A database shaped like the full one, including the tables to be dropped."""
    connection = db.connect(path)
    profiles_schema.create_schema(connection)
    matchrec_schema.ensure_schema(connection)
    db.migrate_scale_columns(connection)
    with connection:
        for index in range(notices):
            connection.execute(
                "INSERT INTO tenders (source, source_id, title, buyer_name, region, "
                "documents_open, status, ingested_at, updated_at) VALUES "
                "('seao', ?, ?, 'Ville', 'QC', 0, 'open', '2026-07-31', '2026-07-31')",
                (f"t-{index}", f"Notice {index}"),
            )
            connection.execute(
                "INSERT INTO notice_trades (tender_id, trade_slugs, mapping_status, "
                "matched_terms, mapping_version, updated_at) VALUES "
                "(?, '[]', 'unmapped', '[]', 'v1', '2026-07-31')",
                (index + 1,),
            )
        connection.execute(
            "INSERT INTO firms (name, created_at, updated_at) "
            "VALUES ('Test Firm', 'n', 'n')"
        )
        # The two side tables carry rows here, and must not in the projection.
        connection.execute(
            "INSERT INTO firm_notice_scores (firm_id, tender_id, base_score, "
            "value_modifier, final_score, weights_version, mapping_version, scored_at) "
            "VALUES (1, 1, 1.0, 0.0, 1.0, 'w', 'v1', 'n')"
        )
        connection.execute(
            "INSERT INTO firm_notice_exclusions (firm_id, tender_id, primary_reason, "
            "evaluated_at) VALUES (1, 2, 'closed', 'n')"
        )
        # The 356 MB the projection exists to remove.
        connection.execute(
            "CREATE TABLE bid_interactions (canonical_id TEXT, ocid TEXT, "
            "bid_amount REAL, won INTEGER, interaction_date TEXT, buyer_id TEXT, "
            "buyer_name TEXT, category TEXT, region TEXT, title TEXT)"
        )
        connection.execute(
            "INSERT INTO bid_interactions VALUES "
            "('c1', 'o1', 100.0, 1, '2024-01-01', 'b', 'B', 'cat', 'QC', 'Work')"
        )
        connection.execute(
            "CREATE TABLE firm_entities (firm_id TEXT PRIMARY KEY, canonical_id TEXT, "
            "raw_name TEXT, normalized_name TEXT, neq TEXT, merge_rule TEXT, "
            "observations INTEGER)"
        )
        connection.execute(
            "INSERT INTO firm_entities VALUES ('f1', 'c1', 'Acme', 'acme', NULL, 'x', 3)"
        )
        connection.execute(
            "CREATE TABLE municipalities (id INTEGER PRIMARY KEY, slug TEXT, "
            "name TEXT, tier TEXT, created_at TEXT, updated_at TEXT)"
        )
        connection.execute(
            "INSERT INTO municipalities VALUES (1, 'x', 'X', 'lower', 'n', 'n')"
        )
        connection.execute(
            "CREATE TABLE notice_documents (tender_id INTEGER, url TEXT, "
            "discovered_at TEXT, PRIMARY KEY (tender_id, url))"
        )
    connection.close()


class ProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.source = Path(self.temp.name) / "full.db"
        self.dest = Path(self.temp.name) / "slim.db"
        _seed(self.source)
        self.summary = build_slim_db.build(self.source, self.dest)
        self.slim = sqlite3.connect(f"file:{self.dest}?mode=ro", uri=True)

    def tearDown(self) -> None:
        self.slim.close()
        self.temp.cleanup()

    def _tables(self) -> set[str]:
        return {
            str(row[0])
            for row in self.slim.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }

    def test_the_daily_path_tables_survive(self) -> None:
        self.assertLessEqual(set(build_slim_db.KEEP_TABLES), self._tables())

    def test_the_historical_tables_are_gone(self) -> None:
        """This is the enforcement, not a size optimisation."""
        self.assertNotIn("bid_interactions", self._tables())
        self.assertNotIn("firm_entities", self._tables())

    def test_retraining_cannot_read_its_inputs(self) -> None:
        """--refit and --fit both start here, and there is nothing to start from."""
        with self.assertRaises(sqlite3.OperationalError):
            self.slim.execute("SELECT COUNT(*) FROM bid_interactions")

    def test_kept_rows_are_preserved_exactly(self) -> None:
        self.assertEqual(12, self.slim.execute("SELECT COUNT(*) FROM tenders").fetchone()[0])
        self.assertEqual(
            12, self.slim.execute("SELECT COUNT(*) FROM notice_trades").fetchone()[0]
        )
        self.assertEqual(1, self.slim.execute("SELECT COUNT(*) FROM firms").fetchone()[0])

    def test_the_side_tables_ship_empty_but_present(self) -> None:
        """Their rows are outputs of a run; rank_firm regenerates every one."""
        for table in build_slim_db.EMPTY_TABLES:
            self.assertIn(table, self._tables())
            self.assertEqual(
                0, self.slim.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            )

    def test_the_summary_reports_what_it_did(self) -> None:
        self.assertIn("bid_interactions", self.summary["tables_dropped"])
        self.assertIn("firm_entities", self.summary["tables_dropped"])
        self.assertEqual(12, self.summary["rows"]["tenders"])


class ProvenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.source = Path(self.temp.name) / "full.db"
        self.dest = Path(self.temp.name) / "slim.db"
        _seed(self.source)
        build_slim_db.build(self.source, self.dest)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_the_stamp_identifies_the_database_it_came_from(self) -> None:
        stamp = build_slim_db.read_provenance(self.dest)
        self.assertEqual(str(self.source), stamp["source_path"])
        self.assertEqual(build_slim_db.file_sha256(self.source), stamp["source_sha256"])
        self.assertEqual(self.source.stat().st_size, stamp["source_size_bytes"])
        self.assertEqual("2026-07-31", stamp["source_max_ingested_at"])
        # The counts of what was dropped, so the projection is auditable after the fact.
        self.assertEqual(1, stamp["source_bid_interactions"])
        self.assertEqual(1, stamp["source_firm_entities"])
        self.assertIn("bid_interactions", json.loads(stamp["tables_dropped"]))

    def test_the_stamp_names_the_retrain_generation(self) -> None:
        """Which fitted artifacts this database is meant to be paired with."""
        stamp = build_slim_db.read_provenance(self.dest)
        for field in (
            "scale_estimator_generated_at",
            "booster_sha256",
            "booster_trees",
        ):
            self.assertIsNotNone(stamp[field], f"{field} was not stamped")

    def test_a_slim_database_refuses_to_be_re_slimmed(self) -> None:
        """A projection of a projection would stamp the copy, not the retrain."""
        again = Path(self.temp.name) / "slimmer.db"
        with self.assertRaises(SystemExit) as caught:
            build_slim_db.build(self.dest, again)
        self.assertIn("already a slim database", str(caught.exception))


class RefusalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.source = Path(self.temp.name) / "full.db"
        self.dest = Path(self.temp.name) / "slim.db"
        _seed(self.source)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_an_existing_destination_needs_overwrite(self) -> None:
        build_slim_db.build(self.source, self.dest)
        with self.assertRaises(SystemExit) as caught:
            build_slim_db.build(self.source, self.dest)
        self.assertIn("--overwrite", str(caught.exception))

    def test_overwrite_replaces_it(self) -> None:
        build_slim_db.build(self.source, self.dest)
        summary = build_slim_db.build(self.source, self.dest, overwrite=True)
        self.assertEqual(12, summary["rows"]["tenders"])

    def test_source_and_destination_must_differ(self) -> None:
        with self.assertRaises(SystemExit):
            build_slim_db.build(self.source, self.source)

    def test_a_missing_source_is_named(self) -> None:
        with self.assertRaises(SystemExit) as caught:
            build_slim_db.build(Path(self.temp.name) / "absent.db", self.dest)
        self.assertIn("No source database", str(caught.exception))

    def test_a_source_missing_daily_tables_is_refused(self) -> None:
        bare = Path(self.temp.name) / "bare.db"
        connection = sqlite3.connect(bare)
        connection.execute("CREATE TABLE tenders (id INTEGER PRIMARY KEY)")
        connection.commit()
        connection.close()
        with self.assertRaises(SystemExit) as caught:
            build_slim_db.build(bare, self.dest)
        self.assertIn("missing tables", str(caught.exception))


class CommittedSlimDatabaseTests(unittest.TestCase):
    """The database that actually ships."""

    def setUp(self) -> None:
        if not build_slim_db.DEFAULT_DEST.is_file():
            self.skipTest("the slim database has not been built")
        self.connection = sqlite3.connect(
            f"file:{build_slim_db.DEFAULT_DEST}?mode=ro", uri=True
        )

    def tearDown(self) -> None:
        self.connection.close()

    def test_it_cannot_retrain(self) -> None:
        tables = {
            str(row[0])
            for row in self.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        self.assertNotIn("bid_interactions", tables)
        self.assertNotIn("firm_entities", tables)

    def test_it_carries_a_stamp_naming_its_source(self) -> None:
        stamp = build_slim_db.read_provenance(build_slim_db.DEFAULT_DEST)
        self.assertIsNotNone(stamp)
        self.assertTrue(stamp["source_sha256"])
        self.assertTrue(stamp["generated_at"])


if __name__ == "__main__":
    unittest.main()
