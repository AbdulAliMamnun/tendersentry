import json
import tempfile
import unittest
from pathlib import Path

from profiles import import_legacy, schema, vocabulary


LEGACY = {
    "firm_name": "Georgian Bay Civil Ltd.",
    "certifications": ["COR"],
    "bonding_capacity_cad": 2000000,
    "insurance": {"cgl_limit": 5000000, "auto_limit": 2000000},
    "regions": ["Ontario"],
    "past_projects": [
        {
            "name": "Culvert replacement, Simcoe County",
            "value_cad": 850000,
            "year": 2024,
            "type": "civil",
        },
        {
            "name": "Pumphouse upgrade, Orillia",
            "value_cad": 1200000,
            "year": 2023,
            "type": "civil",
        },
        {
            "name": "Shoreline stabilization, Tay Twp",
            "value_cad": 400000,
            "year": 2022,
            "type": "civil",
        },
    ],
    "staff_designations": ["P.Eng on staff"],
    "submission_capabilities": ["email", "portal"],
}


class BuildFirmTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.firm = import_legacy.build_firm(LEGACY)

    def test_directly_mappable_fields_are_carried_over_unchanged(self) -> None:
        self.assertEqual(self.firm["name"], "Georgian Bay Civil Ltd.")
        self.assertEqual(self.firm["bonding_single_project"], 2_000_000)
        self.assertEqual(self.firm["insurance_cgl"], 5_000_000)
        self.assertEqual(self.firm["insurance_auto"], 2_000_000)
        self.assertEqual(self.firm["submission_capabilities"], ["email", "portal"])

    def test_the_engineer_designation_becomes_a_certification(self) -> None:
        self.assertIn("P.Eng on staff", self.firm["certifications"])
        self.assertIn("COR", self.firm["certifications"])

    def test_past_projects_are_typed_from_their_names(self) -> None:
        by_name = {
            project["name"]: project["type_slug"]
            for project in self.firm["past_projects"]
        }

        self.assertEqual(
            by_name["Culvert replacement, Simcoe County"], "bridge_structural"
        )
        self.assertEqual(by_name["Pumphouse upgrade, Orillia"], "water_wastewater")
        self.assertEqual(
            by_name["Shoreline stabilization, Tay Twp"], "marine_shoreline"
        )

    def test_trades_are_derived_from_the_typed_projects(self) -> None:
        self.assertEqual(
            self.firm["trades"],
            ["bridge_structural", "marine_shoreline", "water_wastewater"],
        )

    def test_project_values_survive_for_the_value_modifier_baseline(self) -> None:
        self.assertEqual(
            schema.past_project_values(self.firm), [850_000.0, 1_200_000.0, 400_000.0]
        )

    def test_the_result_passes_schema_validation(self) -> None:
        self.assertEqual(schema.validate(self.firm), [])

    def test_every_inferred_field_is_recorded_as_a_todo(self) -> None:
        todos = " ".join(
            note for note in self.firm["import_notes"] if note.startswith("TODO:")
        )

        for expected in ("trades", "regions", "buyer_type_preferences", "value_min",
                         "bonding_aggregate", "bids_per_month_capacity"):
            with self.subTest(field=expected):
                self.assertIn(expected, todos)

    def test_inferred_values_are_the_documented_defaults(self) -> None:
        self.assertEqual(self.firm["regions"], [vocabulary.REGION_ONTARIO_ANY])
        self.assertEqual(self.firm["buyer_type_preferences"], ["municipal", "federal"])
        self.assertEqual(self.firm["value_min"], 100_000.0)
        self.assertEqual(self.firm["value_max"], 2_000_000.0)
        self.assertIsNone(self.firm["bonding_aggregate"])

    def test_an_untypeable_project_is_flagged_rather_than_guessed(self) -> None:
        firm = import_legacy.build_firm(
            {
                "firm_name": "Test",
                "past_projects": [{"name": "Miscellaneous works", "value_cad": 10}],
            }
        )

        self.assertIsNone(firm["past_projects"][0]["type_slug"])
        self.assertTrue(
            any("could not be typed" in note for note in firm["import_notes"])
        )

    def test_an_out_of_vocabulary_submission_capability_is_dropped_with_a_note(self) -> None:
        firm = import_legacy.build_firm(
            {"firm_name": "Test", "submission_capabilities": ["email", "carrier pigeon"]}
        )

        self.assertEqual(firm["submission_capabilities"], ["email"])
        self.assertTrue(any("carrier pigeon" in note for note in firm["import_notes"]))


class ImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = schema.connect(":memory:")
        self.addCleanup(self.connection.close)
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.profile_path = directory / "profile.json"
        with self.profile_path.open("w", encoding="utf-8") as handle:
            json.dump(LEGACY, handle)

    def test_the_legacy_profile_lands_as_firm_one(self) -> None:
        firm = import_legacy.import_legacy(self.connection, self.profile_path)

        self.assertEqual(firm["id"], 1)
        self.assertEqual(firm["name"], "Georgian Bay Civil Ltd.")

    def test_re_importing_updates_the_same_firm(self) -> None:
        first = import_legacy.import_legacy(self.connection, self.profile_path)
        second = import_legacy.import_legacy(self.connection, self.profile_path)

        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(schema.list_firms(self.connection)), 1)

    def test_an_unreadable_profile_raises_rather_than_creating_a_blank_firm(self) -> None:
        with self.assertLogs("profiles.import_legacy", level="ERROR"):
            with self.assertRaises(RuntimeError):
                import_legacy.import_legacy(
                    self.connection, self.profile_path.parent / "missing.json"
                )

        self.assertEqual(schema.list_firms(self.connection), [])


if __name__ == "__main__":
    unittest.main()
