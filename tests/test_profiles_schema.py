import unittest

from profiles import schema, vocabulary


def _firm(**overrides) -> dict:
    firm = {
        "name": "Georgian Bay Civil Ltd.",
        "trades": ["water_wastewater", "bridge_structural"],
        "regions": ["simcoe", "muskoka"],
        "value_min": 100_000,
        "value_max": 2_000_000,
        "bonding_single_project": 2_000_000,
        "bonding_aggregate": None,
        "insurance_cgl": 5_000_000,
        "insurance_auto": 2_000_000,
        "certifications": ["COR"],
        "submission_capabilities": ["email", "portal"],
        "buyer_type_preferences": ["municipal", "federal"],
        "bids_per_month_capacity": 4,
        "past_projects": [
            {
                "name": "Culvert replacement, Simcoe County",
                "type_slug": "bridge_structural",
                "value": 850_000,
                "buyer_type": "municipal",
                "year": 2024,
            }
        ],
        "import_notes": [],
    }
    firm.update(overrides)
    return firm


class SchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = schema.connect(":memory:")
        self.addCleanup(self.connection.close)

    def test_the_firms_table_carries_every_declared_column(self) -> None:
        columns = [
            row["name"]
            for row in self.connection.execute("PRAGMA table_info(firms)").fetchall()
        ]
        self.assertEqual(
            columns,
            ["id", *schema.FIRM_COLUMNS, "board_token", "created_at", "updated_at"],
        )

    def test_the_board_token_is_not_a_writable_firm_column(self) -> None:
        # FIRM_COLUMNS is what an upsert overwrites. A token in that list would be
        # replaced every time a profile was edited, killing a link already sent.
        self.assertNotIn("board_token", schema.FIRM_COLUMNS)

    def test_json_columns_round_trip_as_python_lists(self) -> None:
        firm_id = schema.upsert_firm(self.connection, _firm())

        stored = schema.get_firm(self.connection, firm_id)

        self.assertEqual(stored["trades"], ["water_wastewater", "bridge_structural"])
        self.assertEqual(stored["past_projects"][0]["value"], 850_000)
        self.assertEqual(stored["certifications"], ["COR"])

    def test_upserting_the_same_name_updates_rather_than_duplicates(self) -> None:
        first = schema.upsert_firm(self.connection, _firm())
        second = schema.upsert_firm(
            self.connection, _firm(trades=["roadwork"]), now="2026-07-30T09:00:00"
        )

        self.assertEqual(first, second)
        self.assertEqual(len(schema.list_firms(self.connection)), 1)
        stored = schema.get_firm(self.connection, first)
        self.assertEqual(stored["trades"], ["roadwork"])
        self.assertEqual(stored["updated_at"], "2026-07-30T09:00:00")

    def test_an_unreadable_json_column_degrades_to_empty(self) -> None:
        firm_id = schema.upsert_firm(self.connection, _firm())
        with self.connection:
            self.connection.execute(
                "UPDATE firms SET trades = 'not json' WHERE id = ?", (firm_id,)
            )

        with self.assertLogs("profiles.schema", level="WARNING"):
            stored = schema.get_firm(self.connection, firm_id)

        self.assertEqual(stored["trades"], [])

    def test_a_missing_firm_is_none(self) -> None:
        self.assertIsNone(schema.get_firm(self.connection, 999))


class ValidationTests(unittest.TestCase):
    def test_a_valid_firm_has_no_problems(self) -> None:
        self.assertEqual(schema.validate(_firm()), [])

    def test_trades_outside_the_vocabulary_are_rejected(self) -> None:
        problems = schema.validate(_firm(trades=["roadwork", "spaceship_repair"]))

        self.assertEqual(len(problems), 1)
        self.assertIn("spaceship_repair", problems[0])

    def test_regions_outside_the_vocabulary_are_rejected(self) -> None:
        problems = schema.validate(_firm(regions=["simcoe", "narnia"]))

        self.assertIn("narnia", problems[0])

    def test_buyer_preferences_use_the_firm_vocabulary_not_the_notice_one(self) -> None:
        # "health" is what notices carry; firms must say "hospital".
        problems = schema.validate(_firm(buyer_type_preferences=["health"]))

        self.assertTrue(problems)
        self.assertEqual(vocabulary.BUYER_TYPE_ALIASES["health"], "hospital")
        self.assertEqual(schema.validate(_firm(buyer_type_preferences=["hospital"])), [])

    def test_an_inverted_value_band_is_rejected(self) -> None:
        problems = schema.validate(_firm(value_min=3_000_000, value_max=1_000_000))

        self.assertIn("value_min is greater than value_max", problems)

    def test_a_past_project_type_slug_must_be_in_the_vocabulary(self) -> None:
        problems = schema.validate(
            _firm(past_projects=[{"name": "x", "type_slug": "not_a_trade"}])
        )

        self.assertIn("not_a_trade", problems[0])

    def test_a_nameless_firm_is_rejected(self) -> None:
        self.assertIn("name is required", schema.validate(_firm(name="  ")))

    def test_upsert_refuses_an_invalid_firm(self) -> None:
        connection = schema.connect(":memory:")
        self.addCleanup(connection.close)

        with self.assertRaises(ValueError):
            schema.upsert_firm(connection, _firm(trades=["nope"]))


class PastProjectValueTests(unittest.TestCase):
    def test_only_usable_positive_values_are_returned(self) -> None:
        firm = _firm(
            past_projects=[
                {"name": "a", "value": 850_000},
                {"name": "b", "value": None},
                {"name": "c", "value": "not a number"},
                {"name": "d", "value": 0},
                {"name": "e", "value": 1_200_000},
                "not a project",
            ]
        )

        self.assertEqual(schema.past_project_values(firm), [850_000.0, 1_200_000.0])

    def test_a_firm_with_no_projects_has_no_baseline(self) -> None:
        self.assertEqual(schema.past_project_values(_firm(past_projects=[])), [])


if __name__ == "__main__":
    unittest.main()
