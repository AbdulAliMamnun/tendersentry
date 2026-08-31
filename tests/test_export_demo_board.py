import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from matchrec import schema as matchrec_schema
from notices import db
from profiles import schema as profiles_schema
from scripts import export_demo_board


FIRM = {
    "name": "Georgian Bay Civil Ltd.",
    "trades": ["water_wastewater"],
    "regions": ["ontario_any"],
    "submission_capabilities": ["email", "portal"],
    "buyer_type_preferences": ["municipal"],
    "past_projects": [],
}

DECISION = {
    "tender_id": "cb-757-46105229",
    "verdict": "no_bid",
    "blockers": ["cb-757-46105229-R042"],
    "open_questions": [],
}

REQUIREMENTS = [
    {
        "id": "cb-757-46105229-R042",
        "requirement_text": "Submit offers by fax to 1-877-558-2349.",
        "verbatim_quote": (
            "The only acceptable facsimile for responses to the RFSO is 1-877-558-2349."
        ),
        "page_number": 2,
        "check_field": "submission_method",
        "check_value": "fax",
    }
]


def _tender_dir(root: Path, tender_id: str) -> Path:
    directory = root / tender_id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "decision.json").write_text(json.dumps(DECISION), encoding="utf-8")
    (directory / "requirements.json").write_text(
        json.dumps(REQUIREMENTS), encoding="utf-8"
    )
    (directory / "dropped.json").write_text(json.dumps([{}, {}, {}]), encoding="utf-8")
    return directory


class BlockerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        _tender_dir(self.root, "cb-757-46105229")
        self.enterContext(patch.object(export_demo_board, "TENDERS_DIR", self.root))

    def test_a_real_blocker_is_found_with_its_quote_and_page(self) -> None:
        blocker = export_demo_board.select_blocker()

        self.assertEqual(blocker["page"], 2)
        self.assertIn("facsimile", blocker["quote"])
        self.assertEqual(blocker["tender_id"], "cb-757-46105229")

    def test_a_fax_blocker_is_preferred_when_one_exists(self) -> None:
        other = _tender_dir(self.root, "aa-other-tender")
        (other / "requirements.json").write_text(
            json.dumps(
                [
                    {
                        **REQUIREMENTS[0],
                        "id": "aa-other-tender-R001",
                        "requirement_text": "Deliver in person to the clerk.",
                        "verbatim_quote": "Bids must be delivered to the clerk's office.",
                        "check_value": "physical",
                    }
                ]
            ),
            encoding="utf-8",
        )
        (other / "decision.json").write_text(
            json.dumps({**DECISION, "tender_id": "aa-other-tender",
                        "blockers": ["aa-other-tender-R001"]}),
            encoding="utf-8",
        )

        blocker = export_demo_board.select_blocker(preferred_check_value="fax")

        self.assertEqual(blocker["tender_id"], "cb-757-46105229")

    def test_a_blocker_without_a_quote_is_never_used(self) -> None:
        (self.root / "cb-757-46105229" / "requirements.json").write_text(
            json.dumps([{**REQUIREMENTS[0], "verbatim_quote": ""}]), encoding="utf-8"
        )

        self.assertIsNone(export_demo_board.select_blocker())


class BlockerTitleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(export_demo_board, "TENDERS_DIR", self.root))

    def test_the_solicitation_number_is_derived_from_the_package_filename(self) -> None:
        raw = self.root / "cb-757-46105229" / "raw"
        raw.mkdir(parents=True)
        (raw / "rfso-5p300-26-0001-a.pdf").write_bytes(b"%PDF")

        self.assertEqual(
            export_demo_board.blocker_title("cb-757-46105229"), "RFSO 5P300-26-0001"
        )

    def test_a_stored_title_wins_over_the_filename(self) -> None:
        connection = matchrec_schema.connect(":memory:")
        self.addCleanup(connection.close)
        db.upsert_notices(
            connection,
            [{"source": "canadabuys", "source_id": "cb-757-46105229",
              "title": "Crane rental standing offer"}],
        )

        self.assertEqual(
            export_demo_board.blocker_title("cb-757-46105229", connection),
            "Crane rental standing offer",
        )

    def test_a_tender_with_no_title_anywhere_still_reads_as_a_label(self) -> None:
        self.assertEqual(
            export_demo_board.blocker_title("muskoka-lakes"), "MUSKOKA LAKES"
        )


class ReasonTests(unittest.TestCase):
    def test_the_fax_reason_reads_as_the_app_words_it(self) -> None:
        reason = export_demo_board.blocker_reason(
            {
                "quote": "The only acceptable facsimile for responses is 1-877-558-2349.",
                "requirement_text": "Submit offers by fax.",
                "check_value": "fax",
            },
            {"submission_capabilities": ["email", "portal"]},
        )

        self.assertEqual(
            reason, "Requires fax submission — this firm submits electronically only."
        )

    def test_a_physical_delivery_reason_names_that_method(self) -> None:
        reason = export_demo_board.blocker_reason(
            {
                "quote": "Bids must be delivered to the clerk's office.",
                "requirement_text": "Physical delivery required.",
                "check_value": "physical",
            },
            {"submission_capabilities": ["email", "portal"]},
        )

        self.assertIn("physical delivery", reason)
        self.assertIn("electronically only", reason)


class ExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        _tender_dir(self.root, "cb-757-46105229")
        self.enterContext(patch.object(export_demo_board, "TENDERS_DIR", self.root))

        self.db_path = self.root / "test.db"
        connection = matchrec_schema.connect(self.db_path)
        db.upsert_notices(
            connection,
            [
                {
                    "source": "canadabuys",
                    "source_id": f"cb-{index}",
                    "title": f"Watermain replacement, phase {index}",
                    "buyer_name": "Township of Test",
                    "closing_date": "2026-09-01T14:00:00",
                    "status": "open",
                }
                for index in range(1, 4)
            ],
        )
        self.firm_id = profiles_schema.upsert_firm(connection, FIRM)
        rows = connection.execute("SELECT id FROM tenders ORDER BY id").fetchall()
        with connection:
            for position, row in enumerate(rows):
                connection.execute(
                    "INSERT INTO firm_notice_scores (firm_id, tender_id, base_score, "
                    "value_modifier, final_score, components, flags, weights_version, "
                    "mapping_version, scored_at) VALUES (?, ?, ?, 0, ?, '{}', '[]', "
                    "'w', 'm', 'now')",
                    (self.firm_id, row["id"], 90 - position, 90 - position),
                )
        connection.close()

    def test_the_board_carries_rows_and_a_real_blocker(self) -> None:
        written = export_demo_board.export(
            firm_id=self.firm_id, rows=2, out_dir=self.root / "out",
            db_path=self.db_path,
        )
        board = json.loads(written["demo-board.json"].read_text(encoding="utf-8"))

        self.assertEqual(board["firm"]["name"], "Georgian Bay Civil Ltd.")
        self.assertEqual(len(board["rows"]), 2)
        self.assertEqual(board["rows"][0]["score"], 90)
        self.assertIn("facsimile", board["blocker"]["quote"])
        self.assertEqual(board["blocker"]["page"], 2)
        self.assertIn("fax submission", board["blocker"]["reason"])

    def test_rows_are_ordered_by_score(self) -> None:
        written = export_demo_board.export(
            firm_id=self.firm_id, rows=3, out_dir=self.root / "out",
            db_path=self.db_path,
        )
        board = json.loads(written["demo-board.json"].read_text(encoding="utf-8"))

        scores = [row["score"] for row in board["rows"]]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_stats_are_counted_from_the_data_not_asserted(self) -> None:
        written = export_demo_board.export(
            firm_id=self.firm_id, out_dir=self.root / "out", db_path=self.db_path
        )
        stats = json.loads(written["stats.json"].read_text(encoding="utf-8"))

        self.assertEqual(stats["notices_tracked"], 3)
        self.assertEqual(stats["requirements_verified"], 1)
        self.assertEqual(stats["fabrications_caught"], 3)
        self.assertIn("municipalities_mapped", stats)

    def test_the_firm_flag_selects_which_board_is_exported(self) -> None:
        connection = matchrec_schema.connect(self.db_path)
        second = profiles_schema.upsert_firm(
            connection, {**FIRM, "name": "Constructions Rivière-du-Nord inc."}
        )
        connection.close()

        written = export_demo_board.export(
            firm_id=second, out_dir=self.root / "out", db_path=self.db_path
        )
        board = json.loads(written["demo-board.json"].read_text(encoding="utf-8"))

        self.assertEqual(board["firm"]["name"], "Constructions Rivière-du-Nord inc.")

    def test_an_unknown_firm_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            export_demo_board.export(
                firm_id=999, out_dir=self.root / "out", db_path=self.db_path
            )

    def test_a_board_is_never_emitted_without_verified_evidence(self) -> None:
        """The invariant is unchanged; where the evidence comes from is not.

        This used to blank the decision file, because `build_board` globbed
        `data/tenders/`. It no longer does — that directory is gitignored and absent
        on every runner, which is why the daily job failed here every time. The
        evidence is now the committed artifact, so removing *that* is what must refuse.
        """
        with patch.object(
            export_demo_board, "BLOCKER_PATH", self.root / "no-such-blocker.json"
        ):
            with self.assertRaises(RuntimeError) as caught:
                export_demo_board.export(
                    firm_id=self.firm_id, out_dir=self.root / "out", db_path=self.db_path
                )
        self.assertIn("--refresh-blocker", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
