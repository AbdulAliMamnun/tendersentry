import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from matchrec import rank, schema, scoring, trades
from notices import db
from profiles import schema as profiles_schema


NOW = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)

FIRM = {
    "name": "Georgian Bay Civil Ltd.",
    "trades": ["water_wastewater", "bridge_structural"],
    "regions": ["ontario_any"],
    "value_min": 100_000,
    "value_max": 2_000_000,
    "buyer_type_preferences": ["municipal", "federal"],
    "past_projects": [
        {"name": "Culvert replacement", "type_slug": "bridge_structural", "value": 850_000}
    ],
}

NOTICES = [
    {
        "source": "canadabuys",
        "source_id": "match-strong",
        "title": "Watermain replacement on Queen Street",
        "category_raw": "*CNST",
        "category_normalized": "construction",
        "region": "ON",
        "buyer_type": "municipal",
        "closing_date": "2026-08-29T14:00:00",
        "estimated_value": 850_000,
        "status": "open",
    },
    {
        "source": "canadabuys",
        "source_id": "match-family",
        "title": "Road resurfacing, County Road 21",
        "category_raw": "*CNST",
        "category_normalized": "construction",
        "region": "ON",
        "buyer_type": "municipal",
        "closing_date": "2026-08-29T14:00:00",
        "status": "open",
    },
    {
        "source": "seao",
        "source_id": "wrong-province",
        "title": "Réfection de la rue Principale",
        "category_raw": "Travaux de construction",
        "category_normalized": "construction",
        "region": "QC",
        "buyer_type": "municipal",
        "closing_date": "2026-08-29T11:00:00-04:00",
        "status": "open",
    },
    {
        "source": "canadabuys",
        "source_id": "already-closed",
        "title": "Culvert replacement, Highway 11",
        "category_raw": "*CNST",
        "category_normalized": "construction",
        "region": "ON",
        "buyer_type": "municipal",
        "closing_date": "2026-06-01T14:00:00",
        "status": "closed",
    },
    {
        "source": "canadabuys",
        "source_id": "not-construction",
        "title": "Software licence renewal",
        "category_raw": "*GD",
        "category_normalized": "goods",
        "region": "ON",
        "buyer_type": "federal",
        "closing_date": "2026-08-29T14:00:00",
        "status": "open",
    },
    {
        "source": "canadabuys",
        "source_id": "too-expensive",
        "title": "Bridge rehabilitation, Trent River",
        "category_raw": "*CNST",
        "category_normalized": "construction",
        "region": "ON",
        "buyer_type": "municipal",
        "closing_date": "2026-08-29T14:00:00",
        "estimated_value": 25_000_000,
        "status": "open",
    },
]


class RankFirmTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = schema.connect(":memory:")
        self.addCleanup(self.connection.close)
        db.upsert_notices(self.connection, NOTICES)
        self.firm_id = profiles_schema.upsert_firm(self.connection, FIRM)
        self.mapping = trades.load_mapping()
        self.weights = scoring.load_weights()
        rank.prepare(self.connection, self.mapping)

    def rank(self, **kwargs) -> dict:
        return rank.rank_firm(
            self.connection,
            self.firm_id,
            weights=self.weights,
            mapping=self.mapping,
            now=NOW,
            **kwargs,
        )

    def _by_source_id(self, result: dict) -> dict:
        return {
            item["notice"]["source_id"]: item for item in result["scored"]
        }

    def test_preparation_fills_closing_date_utc_for_both_conventions(self) -> None:
        rows = {
            row["source_id"]: row["closing_date_utc"]
            for row in self.connection.execute(
                "SELECT source_id, closing_date_utc FROM tenders"
            )
        }

        self.assertEqual(rows["match-strong"], "2026-08-29T18:00:00+00:00")
        self.assertEqual(rows["wrong-province"], "2026-08-29T15:00:00+00:00")

    def test_only_relevant_notices_survive(self) -> None:
        result = self.rank()

        self.assertEqual(
            sorted(self._by_source_id(result)), ["match-family", "match-strong"]
        )

    def test_each_excluded_notice_records_an_auditable_reason(self) -> None:
        self.rank()

        rows = {
            row["source_id"]: json.loads(row["reasons"])
            for row in self.connection.execute(
                "SELECT t.source_id, e.reasons FROM firm_notice_exclusions e "
                "JOIN tenders t ON t.id = e.tender_id WHERE e.firm_id = ?",
                (self.firm_id,),
            )
        }

        self.assertIn("region_mismatch", rows["wrong-province"])
        self.assertIn("closed", rows["already-closed"])
        self.assertIn("non_construction", rows["not-construction"])
        self.assertIn("value_out_of_range", rows["too-expensive"])

    def test_the_strongest_match_ranks_first(self) -> None:
        result = self.rank()

        self.assertEqual(result["scored"][0]["notice"]["source_id"], "match-strong")

    def test_an_exact_trade_match_outscores_a_family_match(self) -> None:
        scored = self._by_source_id(self.rank())

        self.assertGreater(
            scored["match-strong"]["components"]["trade_match"]["points"],
            scored["match-family"]["components"]["trade_match"]["points"],
        )

    def test_the_value_modifier_is_stored_separately_from_the_base(self) -> None:
        scored = self._by_source_id(self.rank())

        strong = scored["match-strong"]
        self.assertEqual(strong["value_modifier"], 10.0)
        self.assertEqual(scored["match-family"]["value_modifier"], 0.0)
        self.assertEqual(
            strong["final_score"],
            min(100.0, strong["base_score"] + strong["value_modifier"]),
        )

    def test_filter_flags_survive_into_the_stored_row(self) -> None:
        self.rank()

        stored = {
            row["source_id"]: json.loads(row["flags"])
            for row in self.connection.execute(
                "SELECT t.source_id, s.flags FROM firm_notice_scores s "
                "JOIN tenders t ON t.id = s.tender_id WHERE s.firm_id = ?",
                (self.firm_id,),
            )
        }

        # match-family is a same-family match with no published value; both facts
        # have to reach the database, not just the console summary.
        self.assertEqual(
            stored["match-family"], ["trade_family_only", "value_unknown"]
        )
        self.assertEqual(stored["match-strong"], [])

    def test_the_component_breakdown_is_persisted_for_every_score(self) -> None:
        self.rank()

        rows = self.connection.execute(
            "SELECT components, flags, weights_version, mapping_version "
            "FROM firm_notice_scores WHERE firm_id = ?",
            (self.firm_id,),
        ).fetchall()

        self.assertEqual(len(rows), 2)
        for row in rows:
            components = json.loads(row["components"])
            self.assertEqual(sorted(components), sorted(scoring.BASE_COMPONENTS))
            self.assertEqual(row["weights_version"], self.weights["version"])
            self.assertEqual(row["mapping_version"], self.mapping.version)

    def test_re_scoring_unchanged_inputs_writes_nothing(self) -> None:
        self.rank()
        before = self.connection.execute(
            "SELECT tender_id, scored_at FROM firm_notice_scores WHERE firm_id = ?",
            (self.firm_id,),
        ).fetchall()

        result = self.rank()

        after = self.connection.execute(
            "SELECT tender_id, scored_at FROM firm_notice_scores WHERE firm_id = ?",
            (self.firm_id,),
        ).fetchall()
        self.assertEqual(result["persisted"]["scores"]["updated"], 0)
        self.assertEqual(result["persisted"]["scores"]["inserted"], 0)
        self.assertEqual(result["persisted"]["scores"]["unchanged"], 2)
        self.assertEqual([dict(row) for row in before], [dict(row) for row in after])

    def test_a_notice_that_stops_qualifying_loses_its_stored_score(self) -> None:
        self.rank()
        with self.connection:
            self.connection.execute(
                "UPDATE tenders SET status = 'awarded' WHERE source_id = 'match-strong'"
            )

        self.rank()

        remaining = [
            row["source_id"]
            for row in self.connection.execute(
                "SELECT t.source_id FROM firm_notice_scores s "
                "JOIN tenders t ON t.id = s.tender_id WHERE s.firm_id = ?",
                (self.firm_id,),
            )
        ]
        self.assertEqual(remaining, ["match-family"])

    def test_persist_can_be_disabled_for_dry_runs(self) -> None:
        result = self.rank(persist=False)

        self.assertEqual(result["persisted"], {})
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM firm_notice_scores").fetchone()[0],
            0,
        )

    def test_an_unknown_firm_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            rank.rank_firm(self.connection, 999, mapping=self.mapping, now=NOW)

    def test_top_components_names_the_two_biggest_contributors(self) -> None:
        result = self.rank()

        pairs = rank.top_components(result["scored"][0])

        self.assertEqual(len(pairs), 2)
        self.assertGreaterEqual(pairs[0][1], pairs[1][1])

    def test_the_export_payload_carries_provenance_and_breakdowns(self) -> None:
        result = self.rank()

        payload = rank.to_export(result, top=20)

        self.assertEqual(payload["firm"]["id"], self.firm_id)
        self.assertEqual(payload["weights_version"], self.weights["version"])
        self.assertEqual(payload["mapping_version"], self.mapping.version)
        self.assertEqual(payload["candidate_count"], 2)
        self.assertEqual(payload["results"][0]["rank"], 1)
        self.assertIn("trade_match", payload["results"][0]["components"])
        self.assertIn("value_detail", payload["results"][0])

    def test_the_export_honours_the_top_limit(self) -> None:
        payload = rank.to_export(self.rank(), top=1)

        self.assertEqual(len(payload["results"]), 1)


class PrepareTests(unittest.TestCase):
    def test_mapping_is_skipped_when_it_is_already_current(self) -> None:
        connection = schema.connect(":memory:")
        self.addCleanup(connection.close)
        db.upsert_notices(connection, NOTICES)
        mapping = trades.load_mapping()

        first = rank.prepare(connection, mapping)
        second = rank.prepare(connection, mapping)

        self.assertEqual(first["mapping"]["notices"], len(NOTICES))
        self.assertNotIn("status_counts", second["mapping"])

    def test_a_new_notice_triggers_re_mapping(self) -> None:
        connection = schema.connect(":memory:")
        self.addCleanup(connection.close)
        db.upsert_notices(connection, NOTICES)
        mapping = trades.load_mapping()
        rank.prepare(connection, mapping)

        db.upsert_notices(
            connection,
            [
                {
                    "source": "canadabuys",
                    "source_id": "new-arrival",
                    "title": "Sidewalk replacement",
                    "category_raw": "*CNST",
                }
            ],
        )
        result = rank.prepare(connection, mapping)

        self.assertEqual(result["mapping"]["inserted"], 1)

    def test_missing_tenders_table_is_a_clear_error(self) -> None:
        import sqlite3

        connection = sqlite3.connect(":memory:")
        self.addCleanup(connection.close)
        connection.row_factory = sqlite3.Row

        with self.assertRaises(RuntimeError) as raised:
            schema.ensure_schema(connection)

        self.assertIn("notices.ingest", str(raised.exception))


class ExportFileTests(unittest.TestCase):
    def test_the_exported_json_is_readable_from_disk(self) -> None:
        connection = schema.connect(":memory:")
        self.addCleanup(connection.close)
        db.upsert_notices(connection, NOTICES)
        firm_id = profiles_schema.upsert_firm(connection, FIRM)
        mapping = trades.load_mapping()
        rank.prepare(connection, mapping)
        result = rank.rank_firm(connection, firm_id, mapping=mapping, now=NOW)

        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        destination = directory / "export.json"
        with destination.open("w", encoding="utf-8") as handle:
            json.dump(rank.to_export(result, 20), handle)

        with destination.open(encoding="utf-8") as handle:
            reloaded = json.load(handle)
        self.assertEqual(reloaded["candidate_count"], 2)


if __name__ == "__main__":
    unittest.main()
