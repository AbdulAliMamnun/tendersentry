import csv
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from eval import snapshot
from matchrec import rank, schema, trades
from notices import db
from profiles import schema as profiles_schema


NOW = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)

FIRM = {
    "name": "Georgian Bay Civil Ltd.",
    "trades": ["water_wastewater"],
    "regions": ["ontario_any"],
    "buyer_type_preferences": ["municipal"],
    "past_projects": [],
}

NOTICES = [
    {
        "source": "canadabuys",
        "source_id": f"open-{index}",
        "title": f"Watermain replacement, phase {index}",
        "category_raw": "*CNST",
        "category_normalized": "construction",
        "region": "ON",
        "buyer_type": "municipal",
        "closing_date": f"2026-08-{10 + index:02d}T14:00:00",
        "status": "open",
    }
    for index in range(1, 5)
]


def _result(connection) -> dict:
    mapping = trades.load_mapping()
    rank.prepare(connection, mapping)
    firm_id = profiles_schema.upsert_firm(connection, FIRM)
    return rank.rank_firm(connection, firm_id, mapping=mapping, now=NOW)


class SnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = schema.connect(":memory:")
        self.addCleanup(self.connection.close)
        db.upsert_notices(self.connection, NOTICES)
        self.result = _result(self.connection)
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def test_the_snapshot_carries_versions_and_unlabelled_rows(self) -> None:
        payload = snapshot.build_snapshot(self.result, top=3, taken_at=NOW)

        self.assertEqual(payload["snapshot_version"], 1)
        self.assertEqual(payload["taken_at"], "2026-07-30T12:00:00+00:00")
        self.assertTrue(payload["weights_version"])
        self.assertTrue(payload["mapping_version"])
        self.assertFalse(payload["labelled"])
        self.assertEqual(len(payload["results"]), 3)
        for row in payload["results"]:
            self.assertIsNone(row["label"])
            self.assertIsNone(row["label_note"])

    def test_the_snapshot_keeps_the_score_breakdown_for_later_diagnosis(self) -> None:
        payload = snapshot.build_snapshot(self.result, top=1, taken_at=NOW)

        row = payload["results"][0]
        self.assertIn("components", row)
        self.assertIn("base_score", row)
        self.assertIn("value_modifier", row)
        self.assertIn("flags", row)

    def test_candidate_and_exclusion_counts_travel_with_the_snapshot(self) -> None:
        payload = snapshot.build_snapshot(self.result, top=2, taken_at=NOW)

        self.assertEqual(payload["candidate_count"], 4)
        self.assertEqual(payload["top"], 2)
        self.assertIn("excluded_count", payload)

    def test_writing_uses_a_timestamped_filename(self) -> None:
        payload = snapshot.build_snapshot(self.result, top=2, taken_at=NOW)

        destination = snapshot.write_snapshot(payload, directory=self.directory)

        self.assertTrue(destination.name.startswith("firm"))
        self.assertTrue(destination.name.endswith(".json"))
        with destination.open(encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["taken_at"], payload["taken_at"])

    def test_two_snapshots_do_not_overwrite_each_other(self) -> None:
        first = snapshot.write_snapshot(
            snapshot.build_snapshot(self.result, 2, NOW), directory=self.directory
        )
        later = NOW.replace(hour=13)
        second = snapshot.write_snapshot(
            snapshot.build_snapshot(self.result, 2, later), directory=self.directory
        )

        self.assertNotEqual(first, second)
        self.assertEqual(len(list(self.directory.glob("*.json"))), 2)


def _payload(rows: list[dict]) -> dict:
    return {
        "snapshot_version": 1,
        "taken_at": "2026-07-31T03:50:23+00:00",
        "weights_version": "test-weights",
        "mapping_version": "test-mapping",
        "firm": {"id": 1, "name": "Georgian Bay Civil Ltd."},
        "top": len(rows),
        "labelled": False,
        "label_values": list(snapshot.LABEL_VALUES),
        "results": [
            {
                "rank": index,
                "title": row.get("title", f"Notice {index}"),
                "buyer_name": row.get("buyer_name", "Ville de Test"),
                "closing_date_utc": "2026-08-10T18:00:00+00:00",
                "final_score": row.get("final_score", 80.0 - index),
                "notice_url": row.get("notice_url", f"https://example.invalid/{index}"),
                "flags": row.get("flags", ["value_unknown"]),
                "label": row.get("label"),
                "label_note": None,
            }
            for index, row in enumerate(rows, start=1)
        ],
    }


FRENCH_TITLE = "Réfection de la rue Principale — égouts et trottoirs à Saint-Rémi"


class CsvExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.snapshot_path = self.directory / "firm1-20260731T035023+0000.json"
        self.payload = _payload(
            [
                {"title": FRENCH_TITLE, "flags": ["trade_unmapped", "value_unknown"]},
                {"title": "South Campus Watermain Replacement"},
            ]
        )
        with self.snapshot_path.open("w", encoding="utf-8") as handle:
            json.dump(self.payload, handle, ensure_ascii=False)

    def test_the_csv_lands_beside_the_snapshot_with_the_agreed_columns(self) -> None:
        destination, _ = snapshot.export_csv(self.snapshot_path)

        self.assertEqual(destination.name, "firm1-20260731T035023+0000.csv")
        with destination.open(encoding=snapshot.CSV_ENCODING, newline="") as handle:
            reader = csv.DictReader(handle)
            self.assertEqual(reader.fieldnames, list(snapshot.CSV_COLUMNS))

    def test_the_file_starts_with_a_bom_so_excel_reads_it_as_utf8(self) -> None:
        destination, _ = snapshot.export_csv(self.snapshot_path)

        self.assertTrue(destination.read_bytes().startswith(b"\xef\xbb\xbf"))

    def test_a_french_title_survives_the_encoding(self) -> None:
        destination, _ = snapshot.export_csv(self.snapshot_path)

        with destination.open(encoding=snapshot.CSV_ENCODING, newline="") as handle:
            rows = list(csv.DictReader(handle))

        self.assertEqual(rows[0]["title"], FRENCH_TITLE)
        self.assertIn("é", rows[0]["title"])

    def test_the_label_column_starts_empty_and_flags_are_readable(self) -> None:
        destination, _ = snapshot.export_csv(self.snapshot_path)

        with destination.open(encoding=snapshot.CSV_ENCODING, newline="") as handle:
            rows = list(csv.DictReader(handle))

        self.assertEqual([row["label"] for row in rows], ["", ""])
        self.assertEqual(rows[0]["flags"], "trade_unmapped|value_unknown")

    def test_existing_labels_are_carried_into_a_re_export(self) -> None:
        self.payload["results"][0]["label"] = "relevant"
        with self.snapshot_path.open("w", encoding="utf-8") as handle:
            json.dump(self.payload, handle, ensure_ascii=False)

        destination, _ = snapshot.export_csv(self.snapshot_path)

        with destination.open(encoding=snapshot.CSV_ENCODING, newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(rows[0]["label"], "relevant")


class CsvImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.snapshot_path = self.directory / "firm1-20260731T035023+0000.json"
        self.payload = _payload(
            [
                {"title": FRENCH_TITLE, "flags": ["trade_unmapped", "value_unknown"]},
                {"title": "South Campus Watermain Replacement"},
                {"title": "Bus charter services", "flags": ["trade_unmapped"]},
            ]
        )
        self._write_snapshot()
        self.csv_path, _ = snapshot.export_csv(self.snapshot_path)

    def _write_snapshot(self) -> None:
        with self.snapshot_path.open("w", encoding="utf-8") as handle:
            json.dump(self.payload, handle, ensure_ascii=False)

    def _label(self, labels: list[str], **overrides) -> Path:
        with self.csv_path.open(encoding=snapshot.CSV_ENCODING, newline="") as handle:
            rows = list(csv.DictReader(handle))
        for row, label in zip(rows, labels):
            row["label"] = label
        for index, changes in (overrides.get("edits") or {}).items():
            rows[index].update(changes)
        if overrides.get("drop_last"):
            rows = rows[:-1]
        with self.csv_path.open("w", encoding=snapshot.CSV_ENCODING, newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(snapshot.CSV_COLUMNS))
            writer.writeheader()
            writer.writerows(rows)
        return self.csv_path

    def test_labels_come_back_into_the_snapshot(self) -> None:
        self._label(["relevant", "marginal", "irrelevant"])

        destination, tally = snapshot.import_csv(self.csv_path)

        self.assertEqual(destination, self.snapshot_path)
        self.assertEqual(tally["labelled"], 3)
        payload = snapshot.read_snapshot(self.snapshot_path)
        self.assertEqual(
            [row["label"] for row in payload["results"]],
            ["relevant", "marginal", "irrelevant"],
        )
        self.assertTrue(payload["labelled"])

    def test_a_french_title_round_trips_without_corruption(self) -> None:
        self._label(["relevant", "", ""])

        snapshot.import_csv(self.csv_path)

        payload = snapshot.read_snapshot(self.snapshot_path)
        self.assertEqual(payload["results"][0]["title"], FRENCH_TITLE)
        self.assertEqual(payload["results"][0]["label"], "relevant")

    def test_re_importing_the_same_csv_writes_nothing(self) -> None:
        self._label(["relevant", "marginal", "irrelevant"])
        snapshot.import_csv(self.csv_path)

        _, tally = snapshot.import_csv(self.csv_path)

        self.assertFalse(tally["written"])
        self.assertEqual(tally["unchanged"], 3)
        self.assertEqual(tally["labelled"], 0)

    def test_blank_labels_are_allowed_and_mean_unlabelled(self) -> None:
        self._label(["relevant", "", ""])

        _, tally = snapshot.import_csv(self.csv_path)

        payload = snapshot.read_snapshot(self.snapshot_path)
        self.assertIsNone(payload["results"][1]["label"])
        self.assertFalse(payload["labelled"])
        self.assertEqual(tally["labelled_total"], 1)

    def test_clearing_a_label_is_applied_and_counted(self) -> None:
        self._label(["relevant", "marginal", "irrelevant"])
        snapshot.import_csv(self.csv_path)
        self._label(["", "marginal", "irrelevant"])

        _, tally = snapshot.import_csv(self.csv_path)

        self.assertEqual(tally["cleared"], 1)
        self.assertIsNone(
            snapshot.read_snapshot(self.snapshot_path)["results"][0]["label"]
        )

    def test_labels_are_case_and_space_tolerant(self) -> None:
        self._label([" Relevant ", "MARGINAL", "irrelevant"])

        snapshot.import_csv(self.csv_path)

        payload = snapshot.read_snapshot(self.snapshot_path)
        self.assertEqual(
            [row["label"] for row in payload["results"]],
            ["relevant", "marginal", "irrelevant"],
        )

    def test_an_invalid_label_is_refused_with_the_line_and_value(self) -> None:
        self._label(["relevant", "probably?", ""])

        with self.assertRaises(ValueError) as raised:
            snapshot.import_csv(self.csv_path)

        message = str(raised.exception)
        self.assertIn("line 3", message)
        self.assertIn("probably?", message)
        self.assertIn("relevant, marginal, irrelevant", message)

    def test_an_invalid_label_leaves_the_snapshot_untouched(self) -> None:
        self._label(["relevant", "probably?", ""])

        with self.assertRaises(ValueError):
            snapshot.import_csv(self.csv_path)

        payload = snapshot.read_snapshot(self.snapshot_path)
        self.assertTrue(all(row["label"] is None for row in payload["results"]))

    def test_a_mismatched_notice_url_is_refused(self) -> None:
        self._label(
            ["relevant", "relevant", "relevant"],
            edits={1: {"notice_url": "https://example.invalid/999"}},
        )

        with self.assertRaises(ValueError) as raised:
            snapshot.import_csv(self.csv_path)

        self.assertIn("out of step", str(raised.exception))

    def test_a_deleted_row_is_refused_rather_than_partially_applied(self) -> None:
        self._label(["relevant", "relevant"], drop_last=True)

        with self.assertRaises(ValueError) as raised:
            snapshot.import_csv(self.csv_path)

        self.assertIn("2 row(s) but the snapshot has 3", str(raised.exception))

    def test_a_csv_missing_the_label_column_is_refused(self) -> None:
        broken = self.directory / "broken.csv"
        with broken.open("w", encoding=snapshot.CSV_ENCODING, newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["rank", "notice_url"])
            writer.writeheader()
            writer.writerow({"rank": 1, "notice_url": "https://example.invalid/1"})

        with self.assertRaises(ValueError) as raised:
            snapshot.read_csv_labels(broken)

        self.assertIn("label", str(raised.exception))

    def test_a_csv_with_no_snapshot_beside_it_is_refused(self) -> None:
        orphan = self.directory / "orphan.csv"
        orphan.write_bytes(self.csv_path.read_bytes())

        with self.assertRaises(ValueError) as raised:
            snapshot.import_csv(orphan)

        self.assertIn("No snapshot found", str(raised.exception))


class CohortReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.payload = _payload(
            [
                {"label": "relevant", "flags": ["value_unknown"]},
                {"label": "relevant", "flags": ["value_unknown"]},
                {"label": "irrelevant", "flags": ["value_unknown"]},
                {"label": "irrelevant", "flags": ["trade_unmapped"]},
                {"label": "marginal", "flags": ["trade_unmapped"]},
                {"label": None, "flags": ["trade_unmapped"]},
            ]
        )

    def test_cohorts_are_split_on_the_trade_unmapped_flag(self) -> None:
        report = snapshot.precision_report(self.payload)

        self.assertEqual(report["cohorts"]["mapped"]["rows"], 3)
        self.assertEqual(report["cohorts"]["unmapped"]["rows"], 3)

    def test_each_cohort_reports_its_own_precision(self) -> None:
        report = snapshot.precision_report(self.payload)

        self.assertAlmostEqual(report["overall"]["precision"], 0.4)
        # Precision is stored rounded to 4 decimal places.
        self.assertAlmostEqual(report["cohorts"]["mapped"]["precision"], 2 / 3, places=4)
        self.assertEqual(report["cohorts"]["unmapped"]["precision"], 0.0)

    def test_unlabelled_rows_are_excluded_from_precision_but_counted(self) -> None:
        report = snapshot.precision_report(self.payload)

        unmapped = report["cohorts"]["unmapped"]
        self.assertEqual(unmapped["labelled"], 2)
        self.assertEqual(unmapped["breakdown"]["unlabelled"], 1)

    def test_a_cohort_with_no_labels_reports_none_not_zero(self) -> None:
        payload = _payload([{"flags": ["trade_unmapped"]}, {"flags": ["trade_unmapped"]}])

        report = snapshot.precision_report(payload)

        self.assertIsNone(report["cohorts"]["unmapped"]["precision"])
        self.assertEqual(report["cohorts"]["mapped"]["rows"], 0)

    def test_k_limits_the_rows_before_the_cohort_split(self) -> None:
        report = snapshot.precision_report(self.payload, k=3)

        self.assertEqual(report["k"], 3)
        self.assertEqual(report["cohorts"]["mapped"]["rows"], 3)
        self.assertEqual(report["cohorts"]["unmapped"]["rows"], 0)


class PrecisionTests(unittest.TestCase):
    def _payload(self, labels: list[str | None]) -> dict:
        return {
            "results": [
                {"rank": index, "label": label}
                for index, label in enumerate(labels, start=1)
            ]
        }

    def test_an_unlabelled_snapshot_reports_no_precision_rather_than_zero(self) -> None:
        measured = snapshot.precision_at_k(self._payload([None, None, None]))

        self.assertIsNone(measured["precision"])
        self.assertEqual(measured["labelled"], 0)

    def test_precision_counts_relevant_over_labelled(self) -> None:
        measured = snapshot.precision_at_k(
            self._payload(["relevant", "irrelevant", "relevant", "marginal"])
        )

        self.assertEqual(measured["labelled"], 4)
        self.assertEqual(measured["precision"], 0.5)

    def test_k_limits_the_rows_considered(self) -> None:
        measured = snapshot.precision_at_k(
            self._payload(["relevant", "relevant", "irrelevant", "irrelevant"]), k=2
        )

        self.assertEqual(measured["k"], 2)
        self.assertEqual(measured["precision"], 1.0)

    def test_partially_labelled_snapshots_only_count_what_is_labelled(self) -> None:
        measured = snapshot.precision_at_k(
            self._payload(["relevant", None, "irrelevant", None])
        )

        self.assertEqual(measured["labelled"], 2)
        self.assertEqual(measured["precision"], 0.5)

    def test_invalid_labels_are_ignored(self) -> None:
        measured = snapshot.precision_at_k(self._payload(["yes", "maybe"]))

        self.assertEqual(measured["labelled"], 0)
        self.assertIsNone(measured["precision"])


if __name__ == "__main__":
    unittest.main()
