import unittest
from pathlib import Path

from notices import db
from notices.canadabuys import ingest, map_columns, parse_notices


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "canadabuys_sample.csv"


class ColumnMappingTests(unittest.TestCase):
    def test_bilingual_headers_map_to_english_columns(self) -> None:
        headers = [
            "title-titre-eng",
            "title-titre-fra",
            "referenceNumber-numeroReference",
            "tenderClosingDate-appelOffresDateCloture",
            "tenderStatus-appelOffresStatut-eng",
            "gsin-nibs",
            "gsinDescription-nibsDescription-eng",
            "unspsc",
            "unspscDescription-eng",
            "tenderDescription-descriptionAppelOffres-eng",
            "tenderDescription-descriptionAppelOffres-fra",
            "noticeURL-URLavis-eng",
            "noticeURL-URLavis-fra",
        ]

        mapped = map_columns(headers)

        self.assertEqual(mapped["title"], "title-titre-eng")
        self.assertEqual(mapped["notice_url"], "noticeURL-URLavis-eng")
        self.assertEqual(
            mapped["description"], "tenderDescription-descriptionAppelOffres-eng"
        )

    def test_code_columns_are_not_satisfied_by_their_label_columns(self) -> None:
        mapped = map_columns(
            [
                "referenceNumber-numeroReference",
                "title-titre-eng",
                "tenderClosingDate-appelOffresDateCloture",
                "unspsc",
                "unspscDescription-eng",
                "gsin-nibs",
                "gsinDescription-nibsDescription-eng",
            ]
        )

        self.assertEqual(mapped["unspsc"], "unspsc")
        self.assertEqual(mapped["gsin"], "gsin-nibs")

    def test_a_missing_required_concept_names_itself_in_the_error(self) -> None:
        with self.assertRaises(ValueError) as raised:
            map_columns(["title-titre-eng", "someOtherColumn"])

        message = str(raised.exception)
        self.assertIn("reference_number", message)
        self.assertIn("closing_date", message)

    def test_missing_optional_concepts_only_warn(self) -> None:
        with self.assertLogs("notices.canadabuys", level="WARNING"):
            mapped = map_columns(
                [
                    "referenceNumber-numeroReference",
                    "title-titre-eng",
                    "tenderClosingDate-appelOffresDateCloture",
                ]
            )

        self.assertIsNone(mapped["attachments"])


class ParseNoticesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.records = parse_notices(FIXTURE)
        cls.by_id = {record["source_id"]: record for record in cls.records}

    def test_every_fixture_row_becomes_one_record(self) -> None:
        self.assertEqual(len(self.records), 3)
        self.assertEqual(
            sorted(self.by_id),
            ["MX-443728062417", "MX-444085720077", "cb-604-41302770"],
        )

    def test_construction_notice_is_fully_normalized(self) -> None:
        record = self.by_id["cb-604-41302770"]

        self.assertEqual(record["source"], "canadabuys")
        self.assertEqual(record["title"], "HEAVY CONTRUCTION EQUIPMENT RENTAL")
        self.assertEqual(record["category_raw"], "*CNST")
        self.assertEqual(record["category_normalized"], "construction")
        self.assertEqual(record["buyer_name"], "Department of National Defence (DND)")
        self.assertEqual(record["buyer_type"], "federal")
        self.assertEqual(record["region"], "CA")
        self.assertEqual(record["closing_date"], "2026-07-28T14:00:00")
        self.assertEqual(record["posted_date"], "2026-07-08T00:00:00")
        # CanadaBuys-hosted notices often publish no notice URL of their own.
        self.assertIsNone(record["notice_url"])

    def test_a_published_notice_url_is_kept_verbatim(self) -> None:
        self.assertEqual(
            self.by_id["MX-443728062417"]["notice_url"],
            "https://www.merx.com/public/solicitations/3651536406/abstract?language=EN",
        )

    def test_every_category_is_kept_not_only_construction(self) -> None:
        self.assertEqual(
            self.by_id["MX-443728062417"]["category_normalized"], "services"
        )
        self.assertEqual(self.by_id["MX-444085720077"]["category_normalized"], "goods")

    def test_documents_open_reflects_the_attachment_column(self) -> None:
        self.assertTrue(self.by_id["cb-604-41302770"]["documents_open"])
        self.assertFalse(self.by_id["MX-443728062417"]["documents_open"])

    def test_the_csv_publishes_no_value_so_the_column_stays_null(self) -> None:
        for record in self.records:
            self.assertIsNone(record["estimated_value"])
            self.assertIsNone(record["currency"])

    def test_status_uses_the_shared_vocabulary(self) -> None:
        for record in self.records:
            self.assertIn(
                record["status"],
                {"open", "planned", "closed", "awarded", "cancelled", "unknown"},
            )


class IngestTests(unittest.TestCase):
    def test_ingesting_the_fixture_twice_is_idempotent(self) -> None:
        connection = db.connect(":memory:")
        self.addCleanup(connection.close)

        first = ingest(connection, csv_path=FIXTURE)
        second = ingest(connection, csv_path=FIXTURE)

        self.assertEqual(first["inserted"], 3)
        self.assertEqual(second, {**first, "inserted": 0, "unchanged": 3})
        self.assertEqual(db.count_by_source(connection), {"canadabuys": 3})


if __name__ == "__main__":
    unittest.main()
