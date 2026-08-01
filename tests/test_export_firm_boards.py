import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from matchrec import schema as matchrec_schema
from notices import db
from profiles import schema as profiles_schema
from scripts import export_firm_boards


FIRM_ONE = {
    "name": "Georgian Bay Civil Ltd.",
    "trades": ["water_wastewater", "bridge_structural"],
    "regions": ["ontario_any"],
    "value_min": 100_000,
    "value_max": 2_000_000,
    # The figures a board must never expose.
    "bonding_single_project": 2_000_000,
    "bonding_aggregate": 4_000_000,
    "insurance_cgl": 5_000_000,
    "insurance_auto": 2_000_000,
    "certifications": ["COR"],
    "submission_capabilities": ["email", "portal"],
    "buyer_type_preferences": ["municipal"],
    "past_projects": [{"name": "Culvert replacement", "value": 850_000}],
}

FIRM_TWO = {
    **FIRM_ONE,
    "name": "Constructions Rivière-du-Nord inc.",
    "regions": ["quebec"],
    "bonding_single_project": 3_000_000,
}

DECISION = {
    "tender_id": "cb-blocked",
    "verdict": "no_bid",
    "blockers": ["cb-blocked-R001"],
}

REQUIREMENTS = [
    {
        "id": "cb-blocked-R001",
        "requirement_text": "Submit offers by fax.",
        "verbatim_quote": "The only acceptable facsimile is 1-877-558-2349.",
        "page_number": 2,
        "check_value": "fax",
    }
]


class ExportFirmBoardsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.tenders = self.root / "tenders"
        self.tenders.mkdir()
        self.enterContext(patch.object(export_firm_boards, "TENDERS_DIR", self.tenders))

        self.db_path = self.root / "boards.db"
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
            ]
            + [
                {
                    "source": "canadabuys",
                    "source_id": "cb-blocked",
                    "title": "Crane rental standing offer",
                    "buyer_name": "Defence Construction Canada",
                    "closing_date": "2026-09-05T14:00:00",
                    "status": "open",
                }
            ],
        )
        self.firm_one = profiles_schema.upsert_firm(connection, FIRM_ONE)
        self.firm_two = profiles_schema.upsert_firm(connection, FIRM_TWO)

        rows = connection.execute("SELECT id FROM tenders ORDER BY id").fetchall()
        with connection:
            for firm_id in (self.firm_one, self.firm_two):
                for position, row in enumerate(rows):
                    connection.execute(
                        "INSERT INTO firm_notice_scores (firm_id, tender_id, "
                        "base_score, value_modifier, final_score, components, flags, "
                        "weights_version, mapping_version, scored_at) "
                        "VALUES (?, ?, ?, 0, ?, '{}', ?, 'w', 'm', 'now')",
                        (
                            firm_id,
                            row["id"],
                            90 - position,
                            90 - position,
                            json.dumps(["value_unknown"]),
                        ),
                    )
        self.tokens = {
            int(row["id"]): str(row["board_token"])
            for row in connection.execute("SELECT id, board_token FROM firms")
        }
        connection.close()

        self.out = self.root / "boards"
        self.written = export_firm_boards.export(self.out, 25, self.db_path)

    def _board(self, firm_id: int) -> dict:
        key = profiles_schema.board_token_hash(self.tokens[firm_id])
        return json.loads((self.out / f"{key}.json").read_text(encoding="utf-8"))

    def test_one_file_per_firm_is_written(self) -> None:
        self.assertEqual(len(self.written), 2)
        self.assertEqual(len(list(self.out.glob("*.json"))), 2)

    def test_files_are_named_for_the_hash_never_the_token(self) -> None:
        # The repository is public; a filename that was the token would publish it.
        for path in self.out.glob("*.json"):
            with self.subTest(path=path.name):
                self.assertRegex(path.stem, r"^[0-9a-f]{64}$")
                for token in self.tokens.values():
                    self.assertNotIn(token, path.name)

    def test_no_token_appears_inside_any_board_file(self) -> None:
        for path in self.out.glob("*.json"):
            text = path.read_text(encoding="utf-8")
            for token in self.tokens.values():
                with self.subTest(path=path.name):
                    self.assertNotIn(token, text)

    def test_a_board_carries_only_display_identity(self) -> None:
        board = self._board(self.firm_one)

        self.assertEqual(sorted(board["firm"]), ["name", "regions", "trades"])
        self.assertEqual(board["firm"]["name"], "Georgian Bay Civil Ltd.")

    def test_no_bonding_or_insurance_figures_reach_the_web(self) -> None:
        for firm_id in (self.firm_one, self.firm_two):
            text = json.dumps(self._board(firm_id))
            for forbidden in (
                "bonding",
                "insurance",
                "bonding_single_project",
                "insurance_cgl",
                "2000000",
                "3000000",
                "4000000",
                "5000000",
            ):
                with self.subTest(firm=firm_id, forbidden=forbidden):
                    self.assertNotIn(forbidden, text)

    def test_no_email_address_appears_in_any_board(self) -> None:
        pattern = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
        for path in self.out.glob("*.json"):
            with self.subTest(path=path.name):
                self.assertIsNone(pattern.search(path.read_text(encoding="utf-8")))

    def test_no_internal_identifiers_are_exposed(self) -> None:
        board = self._board(self.firm_one)

        self.assertNotIn("id", board["firm"])
        for row in board["board"]:
            self.assertEqual(
                sorted(row),
                sorted(set(export_firm_boards.ROW_FIELDS) | ({"blocker"} & set(row))),
            )

    def test_one_firms_board_never_contains_another_firms_data(self) -> None:
        first = json.dumps(self._board(self.firm_one))
        second = json.dumps(self._board(self.firm_two))

        self.assertNotIn("Rivière-du-Nord", first)
        self.assertNotIn("Georgian Bay", second)

    def test_rows_are_ranked_and_capped(self) -> None:
        board = export_firm_boards.export(self.out, 2, self.db_path)

        payload = self._board(self.firm_one)
        self.assertEqual(len(payload["board"]), 2)
        self.assertEqual([row["rank"] for row in payload["board"]], [1, 2])
        self.assertGreaterEqual(
            payload["board"][0]["score"], payload["board"][1]["score"]
        )
        self.assertTrue(board)

    def test_flags_travel_with_a_row(self) -> None:
        board = self._board(self.firm_one)

        self.assertIn("value_unknown", board["board"][0]["flags"])

    def test_a_board_reports_how_many_candidates_it_is_drawn_from(self) -> None:
        self.assertEqual(self._board(self.firm_one)["candidate_count"], 4)


class BlockerEvidenceTests(unittest.TestCase):
    """Blocker sections appear only where the engine verified a quote."""

    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.tenders = self.root / "tenders"
        self.tenders.mkdir()
        self.enterContext(patch.object(export_firm_boards, "TENDERS_DIR", self.tenders))

    def _write_decision(self, tender_id: str, decision: dict, requirements: list) -> None:
        directory = self.tenders / tender_id
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "decision.json").write_text(json.dumps(decision), encoding="utf-8")
        (directory / "requirements.json").write_text(
            json.dumps(requirements), encoding="utf-8"
        )

    def test_evidence_is_attached_when_the_engine_produced_it(self) -> None:
        self._write_decision("cb-blocked", DECISION, REQUIREMENTS)

        blocker = export_firm_boards.verified_blocker("cb-blocked")

        self.assertIsNotNone(blocker)
        self.assertEqual(blocker["page"], 2)
        self.assertIn("facsimile", blocker["quote"])

    def test_a_notice_the_engine_never_saw_yields_nothing(self) -> None:
        # Not "no blockers found" — nothing at all, so the page can stay silent.
        self.assertIsNone(export_firm_boards.verified_blocker("cb-never-analysed"))

    def test_a_reviewed_but_unblocked_notice_yields_nothing(self) -> None:
        self._write_decision(
            "cb-clear", {**DECISION, "tender_id": "cb-clear", "verdict": "review",
                         "blockers": []}, REQUIREMENTS
        )

        self.assertIsNone(export_firm_boards.verified_blocker("cb-clear"))

    def test_a_blocker_without_a_verified_quote_is_not_shown(self) -> None:
        self._write_decision(
            "cb-noquote",
            {**DECISION, "tender_id": "cb-noquote"},
            [{**REQUIREMENTS[0], "verbatim_quote": ""}],
        )

        self.assertIsNone(export_firm_boards.verified_blocker("cb-noquote"))


if __name__ == "__main__":
    unittest.main()
