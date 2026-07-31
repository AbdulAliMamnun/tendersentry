import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from notices import db, ingest


def _result(source: str, **overrides) -> dict:
    result = {
        "source": source,
        "parsed": 1,
        "inserted": 1,
        "updated": 0,
        "unchanged": 0,
        "skipped": 0,
        "notes": [],
    }
    result.update(overrides)
    return result


class RunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db_path = Path(self.enterContext(tempfile.TemporaryDirectory())) / "t.db"

    def _run(self, source: str, **kwargs) -> tuple[list[dict], str]:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            results = ingest.run(source=source, db_path=str(self.db_path), **kwargs)
        return results, stdout.getvalue()

    def test_an_unknown_source_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            ingest.run(source="merx", db_path=str(self.db_path))

    def test_a_single_source_runs_only_that_ingester(self) -> None:
        with (
            patch.object(
                ingest.canadabuys, "ingest", return_value=_result("canadabuys")
            ) as canadabuys_ingest,
            patch.object(ingest.seao, "ingest") as seao_ingest,
            patch.object(ingest.bidsandtenders, "ingest") as bt_ingest,
        ):
            results, output = self._run("canadabuys")

        canadabuys_ingest.assert_called_once()
        seao_ingest.assert_not_called()
        bt_ingest.assert_not_called()
        self.assertEqual([result["source"] for result in results], ["canadabuys"])
        self.assertIn("canadabuys", output)

    def test_all_runs_every_source_and_passes_the_seao_week_window(self) -> None:
        with (
            patch.object(
                ingest.canadabuys, "ingest", return_value=_result("canadabuys")
            ),
            patch.object(ingest.seao, "ingest", return_value=_result("seao")) as seao_ingest,
            patch.object(
                ingest.bidsandtenders, "ingest", return_value=_result("bidsandtenders")
            ),
        ):
            results, _ = self._run("all", weeks=12)

        self.assertEqual(
            [result["source"] for result in results],
            ["canadabuys", "seao", "bidsandtenders"],
        )
        self.assertEqual(seao_ingest.call_args.kwargs["weeks"], 12)

    def test_one_failing_source_does_not_stop_the_others(self) -> None:
        with (
            patch.object(
                ingest.canadabuys, "ingest", side_effect=RuntimeError("csv unreachable")
            ),
            patch.object(ingest.seao, "ingest", return_value=_result("seao")),
            patch.object(
                ingest.bidsandtenders, "ingest", return_value=_result("bidsandtenders")
            ),
            self.assertLogs("notices.ingest", level="ERROR"),
        ):
            results, output = self._run("all")

        failed = next(result for result in results if result["source"] == "canadabuys")
        self.assertEqual(failed["inserted"], 0)
        self.assertEqual(failed["notes"], ["failed: csv unreachable"])
        self.assertIn("csv unreachable", output)
        self.assertEqual(len(results), 3)

    def test_the_database_file_is_created_with_the_schema(self) -> None:
        with (
            patch.object(
                ingest.canadabuys, "ingest", return_value=_result("canadabuys")
            ),
        ):
            self._run("canadabuys")

        self.assertTrue(self.db_path.is_file())
        connection = db.connect(self.db_path)
        self.addCleanup(connection.close)
        self.assertEqual(db.count_by_source(connection), {})

    def test_samples_are_printed_from_stored_rows(self) -> None:
        connection = db.connect(self.db_path)
        db.upsert_notices(
            connection,
            [
                {
                    "source": "seao",
                    "source_id": "ocds-ec9k95-20004970",
                    "title": "Rénovation intérieure salle municipale",
                    "closing_date": "2026-08-27T11:00:00-04:00",
                }
            ],
        )
        connection.close()

        with patch.object(ingest.seao, "ingest", return_value=_result("seao")):
            _, output = self._run("seao", samples=3)

        self.assertIn("sample rows — seao", output)
        self.assertIn("ocds-ec9k95-20004970", output)


if __name__ == "__main__":
    unittest.main()
