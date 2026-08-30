"""The two guards the scheduled refresh depends on.

Both exist because a daily job is unattended: nobody reads its output unless it
fails, so anything that can go wrong quietly will go wrong quietly for weeks.

* `require_corpus` makes retraining impossible against the committed slim database.
  Not discouraged — impossible, because the tables a refit reads are not there. The
  guard turns that into a sentence rather than an OperationalError from four frames
  down, so a workflow that grows a `--refit` fails on its first run with something a
  person can act on.
* `adaptive_weeks` sizes the SEAO window from the gap since the last ingest, so a run
  after a missed day widens instead of stepping over the middle.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from matchrec import schema as matchrec_schema
from model import dataset, scale
from notices import db, ingest, seao
from scripts import export_model_service as export_service


def _slim_like() -> sqlite3.Connection:
    """A database shaped like the committed slim one: no corpus tables."""
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    db.create_schema(connection)
    matchrec_schema.ensure_schema(connection)
    db.migrate_scale_columns(connection)
    return connection


def _with_corpus(rows: int = 3) -> sqlite3.Connection:
    connection = _slim_like()
    dataset.ensure_schema(connection)
    with connection:
        for index in range(rows):
            connection.execute(
                "INSERT INTO bid_interactions (canonical_id, ocid, bid_amount, won, "
                "interaction_date) VALUES (?, ?, 1000.0, 1, '2024-01-01')",
                (f"c{index}", f"o{index}"),
            )
            connection.execute(
                "INSERT INTO firm_entities VALUES (?, ?, 'Acme', 'acme', NULL, 'x', 1)",
                (f"f{index}", f"c{index}"),
            )
    return connection


class RequireCorpusTests(unittest.TestCase):
    def test_a_slim_database_cannot_retrain(self) -> None:
        connection = _slim_like()
        try:
            with self.assertRaises(dataset.CorpusUnavailable) as caught:
                dataset.require_corpus(connection, "--refit")
            message = str(caught.exception)
            self.assertIn("bid_interactions", message)
            self.assertIn("slim", message)
            # It must say what to do instead, not only what went wrong.
            self.assertIn("model.dataset", message)
        finally:
            connection.close()

    def test_an_empty_corpus_counts_as_absent(self) -> None:
        """A present-but-empty table would train on nothing and report success."""
        connection = _slim_like()
        dataset.ensure_schema(connection)
        try:
            with self.assertRaises(dataset.CorpusUnavailable):
                dataset.require_corpus(connection, "--fit")
        finally:
            connection.close()

    def test_a_full_database_passes_and_reports_counts(self) -> None:
        connection = _with_corpus(3)
        try:
            counts = dataset.require_corpus(connection, "--refit")
            self.assertEqual(3, counts["bid_interactions"])
            self.assertEqual(3, counts["firm_entities"])
        finally:
            connection.close()


class RetrainPathsRefuseSlimTests(unittest.TestCase):
    """Both entry points, through their real call paths."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "slim.db"
        connection = db.connect(self.path)
        matchrec_schema.ensure_schema(connection)
        db.migrate_scale_columns(connection)
        connection.close()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_scale_fit_refuses(self) -> None:
        connection = db.connect(self.path)
        try:
            with self.assertRaises(dataset.CorpusUnavailable):
                scale.fit(
                    connection,
                    use_gbm=False,
                    estimator_path=Path(self.temp.name) / "e.json",
                    booster_path=Path(self.temp.name) / "e.lgb",
                )
        finally:
            connection.close()

    def test_export_refit_refuses(self) -> None:
        out = Path(self.temp.name) / "out"
        out.mkdir()
        with self.assertRaises(dataset.CorpusUnavailable):
            export_service.export(out_dir=out, db_path=self.path, refit=True)

    def test_the_daily_export_is_unaffected(self) -> None:
        """The guard must not touch the path the cron actually runs."""
        out = Path(self.temp.name) / "out2"
        out.mkdir()
        # Fails on the stale-booster check, never on the corpus guard.
        with self.assertRaises(export_service.StaleBooster):
            export_service.export(out_dir=out, db_path=self.path, refit=False)


class AdaptiveWindowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = _slim_like()
        self.now = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        self.connection.close()

    def _ingested(self, when: datetime) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO tenders (source, source_id, title, documents_open, status, "
                "ingested_at, updated_at) VALUES ('seao', ?, 'T', 0, 'open', ?, ?)",
                (when.isoformat(), when.isoformat(), when.isoformat()),
            )

    def test_a_cold_table_backfills(self) -> None:
        weeks, why = ingest.adaptive_weeks(self.connection, self.now)
        self.assertEqual(seao.BACKFILL_WEEKS, weeks)
        self.assertIn("cold-start", why["reason"])

    def test_a_daily_cadence_sits_on_the_floor(self) -> None:
        self._ingested(self.now - timedelta(days=1))
        weeks, why = ingest.adaptive_weeks(self.connection, self.now)
        self.assertEqual(ingest.INGEST_FLOOR_WEEKS, weeks)
        self.assertIn("floor", why["reason"])
        self.assertEqual(1.0, why["gap_days"])

    def test_a_twelve_day_gap_widens_beyond_the_floor(self) -> None:
        """The case the floor alone would step over."""
        self._ingested(self.now - timedelta(days=12))
        weeks, why = ingest.adaptive_weeks(self.connection, self.now)
        self.assertGreater(weeks, ingest.INGEST_FLOOR_WEEKS)
        self.assertEqual(4, weeks)
        self.assertEqual(12.0, why["gap_days"])

    def test_a_long_outage_is_capped_and_says_so(self) -> None:
        self._ingested(self.now - timedelta(days=400))
        weeks, why = ingest.adaptive_weeks(self.connection, self.now)
        self.assertEqual(seao.BACKFILL_WEEKS, weeks)
        self.assertIn("may not close it", why["reason"])

    def test_the_window_never_exceeds_the_ceiling(self) -> None:
        for days in (0, 1, 7, 12, 30, 90, 365, 5000):
            self._ingested(self.now - timedelta(days=days))
            weeks, _ = ingest.adaptive_weeks(self.connection, self.now)
            self.assertLessEqual(weeks, seao.BACKFILL_WEEKS)
            self.assertGreaterEqual(weeks, ingest.INGEST_FLOOR_WEEKS)

    def test_an_unparseable_timestamp_widens_rather_than_crashing(self) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO tenders (source, source_id, title, documents_open, status, "
                "ingested_at, updated_at) VALUES ('seao', 'x', 'T', 0, 'open', "
                "'not-a-date', 'not-a-date')"
            )
        weeks, why = ingest.adaptive_weeks(self.connection, self.now)
        self.assertEqual(seao.BACKFILL_WEEKS, weeks)
        self.assertIn("unparseable", why["reason"])

    def test_the_reasoning_is_reported_for_the_job_summary(self) -> None:
        """A narrow window has to be visible as a decision, not a default."""
        self._ingested(self.now - timedelta(days=2))
        _, why = ingest.adaptive_weeks(self.connection, self.now)
        self.assertEqual(
            {"weeks", "reason", "last_ingested_at", "gap_days", "floor", "ceiling"},
            set(why),
        )


if __name__ == "__main__":
    unittest.main()
