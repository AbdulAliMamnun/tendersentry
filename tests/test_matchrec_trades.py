import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from matchrec import filters, schema, scoring, trades
from notices import db
from profiles import vocabulary


FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _notice(**overrides) -> dict:
    notice = {
        "title": "",
        "description": None,
        "category_raw": None,
        "category_normalized": None,
    }
    notice.update(overrides)
    return notice


class MappingFileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mapping = trades.load_mapping()

    def test_the_shipped_mapping_loads_and_validates(self) -> None:
        self.assertTrue(self.mapping.version)

    def test_every_rule_slug_is_in_the_controlled_vocabulary(self) -> None:
        payload = json.loads(trades.MAPPING_PATH.read_text(encoding="utf-8"))
        slugs = {rule["slug"] for rule in payload["rules"]}

        self.assertEqual(slugs - set(vocabulary.TRADE_SLUGS), set())
        self.assertEqual(set(vocabulary.TRADE_SLUGS) - slugs, set())

    def test_every_rule_carries_french_keywords(self) -> None:
        payload = json.loads(trades.MAPPING_PATH.read_text(encoding="utf-8"))

        for rule in payload["rules"]:
            with self.subTest(slug=rule["slug"]):
                self.assertTrue(rule.get("keywords_fr"), "no French keywords")
                self.assertTrue(rule.get("keywords_en"), "no English keywords")

    def test_a_typo_in_the_mapping_file_fails_loudly(self) -> None:
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        broken = directory / "broken.json"
        with broken.open("w", encoding="utf-8") as handle:
            json.dump(
                {
                    "version": "test",
                    "families": {},
                    "rules": [{"slug": "roadwrk", "keywords_en": ["road"]}],
                },
                handle,
            )

        with self.assertRaises(ValueError) as raised:
            trades.load_mapping(broken)

        self.assertIn("roadwrk", str(raised.exception))

    def test_families_group_related_trades(self) -> None:
        self.assertTrue(self.mapping.shares_family("roadwork", "water_wastewater"))
        self.assertTrue(self.mapping.shares_family("electrical", "building_general"))
        self.assertFalse(self.mapping.shares_family("roadwork", "electrical"))


class ClassificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mapping = trades.load_mapping()

    def classify(self, **kwargs) -> dict:
        return self.mapping.classify(_notice(**kwargs))

    def test_an_english_construction_title_maps_to_its_trade(self) -> None:
        result = self.classify(
            title="South Campus Watermain Replacement", category_raw="*CNST"
        )

        self.assertEqual(result["mapping_status"], "mapped")
        self.assertEqual(result["trade_slugs"][0], "water_wastewater")

    def test_a_french_construction_title_maps_to_its_trade(self) -> None:
        result = self.classify(
            title="Réfection de la rue Principale et des trottoirs",
            category_raw="Travaux de construction",
        )

        self.assertEqual(result["mapping_status"], "mapped")
        self.assertIn("roadwork", result["trade_slugs"])

    def test_accents_do_not_prevent_a_match(self) -> None:
        accented = self.classify(title="Réparation des structures de l'échangeur")
        plain = self.classify(title="Reparation des structures de l'echangeur")

        self.assertEqual(accented["trade_slugs"], plain["trade_slugs"])
        self.assertIn("bridge_structural", accented["trade_slugs"])

    def test_french_plurals_match_singular_keywords(self) -> None:
        # "travaux électriques" must reach the "electrique" keyword.
        result = self.classify(
            title="Travaux électriques divers", category_raw="Travaux de construction"
        )

        self.assertIn("electrical", result["trade_slugs"])

    def test_the_most_specific_rule_becomes_primary(self) -> None:
        result = self.classify(
            title="Construction d'un nouveau pont et travaux de bâtiment",
            category_raw="Travaux de construction",
        )

        self.assertEqual(result["trade_slugs"][0], "bridge_structural")
        self.assertIn("building_general", result["trade_slugs"])

    def test_word_boundaries_prevent_substring_matches(self) -> None:
        result = self.classify(title="Broadband expansion study")

        self.assertNotIn("roadwork", result["trade_slugs"])

    def test_a_goods_notice_cannot_map_to_a_construction_trade(self) -> None:
        # Military bridging equipment is goods, not bridge construction.
        result = self.classify(
            title="W8476-267064 - Close Support Bridge Systems (CSBS)",
            category_raw="*GD",
        )

        self.assertNotIn("bridge_structural", result["trade_slugs"])
        self.assertNotEqual(result["mapping_status"], "mapped")

    def test_a_goods_notice_can_still_be_granular_supply(self) -> None:
        result = self.classify(
            title="Supply and deliver granular material", category_raw="*GD"
        )

        self.assertEqual(result["mapping_status"], "mapped")
        self.assertIn("granular_supply", result["trade_slugs"])

    def test_the_goods_guard_never_applies_to_construction_notices(self) -> None:
        result = self.classify(
            title="Bridge rehabilitation",
            category_raw="*CNST",
            category_normalized="construction",
        )

        self.assertIn("bridge_structural", result["trade_slugs"])

    def test_clearly_unrelated_work_is_marked_non_construction(self) -> None:
        result = self.classify(
            title="Software licence renewal", category_raw="Approvisionnement (biens)"
        )

        self.assertEqual(result["mapping_status"], "non_construction")

    def test_a_construction_notice_is_never_marked_non_construction(self) -> None:
        # Even with a goods-ish keyword, a construction-marked notice falls back to
        # unmapped so a real tender can never be filtered out by that label.
        result = self.classify(
            title="Fourniture de mobilier",
            category_raw="Travaux de construction",
            category_normalized="construction",
        )

        self.assertEqual(result["mapping_status"], "unmapped")

    def test_dam_work_maps_to_water_infrastructure(self) -> None:
        result = self.classify(
            title="Stage 1 - Replacement of Esson Lake Dam, Ontario Waterways",
            category_raw="*CNST",
        )

        self.assertEqual(result["trade_slugs"][0], "water_wastewater")
        self.assertEqual(result["slug_sources"]["water_wastewater"], "title")

    def test_an_acronym_does_not_masquerade_as_dam_work(self) -> None:
        # "Digital Asset Management (DAM)" briefly mapped a software tender to
        # water infrastructure; dam terms are phrase-anchored because of it.
        result = self.classify(
            title="Commercial off-the-shelf software solution",
            description="Off-the-shelf solution for Digital Asset Management (DAM).",
            category_raw="*SRVTGD",
        )

        self.assertNotIn("water_wastewater", result["trade_slugs"])

    def test_unrecognized_work_is_unmapped_not_guessed(self) -> None:
        result = self.classify(
            title="Projet 055-005-24 RTB-24", category_normalized="construction"
        )

        self.assertEqual(result["mapping_status"], "unmapped")
        self.assertEqual(result["trade_slugs"], [])


class EvidenceLocationTests(unittest.TestCase):
    """Where a keyword matched decides which slug is primary."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.mapping = trades.load_mapping()

    def test_a_title_match_outranks_a_higher_priority_description_match(self) -> None:
        # bridge_structural has priority 10 against mechanical_hvac's 22, so before
        # evidence tracking it won on priority alone.
        result = self.mapping.classify(
            _notice(
                title="Replacement heating systems",
                description="Fee schedule includes Ingénieur de structures principal.",
            )
        )

        self.assertEqual(result["trade_slugs"][0], "mechanical_hvac")
        self.assertEqual(result["slug_sources"]["mechanical_hvac"], "title")
        self.assertEqual(result["slug_sources"]["bridge_structural"], "description")

    def test_a_category_label_naming_the_work_counts_as_strong_evidence(self) -> None:
        # Today's sources publish coarse category labels ("*CNST", "Travaux de
        # construction") that match no trade rule, so this path is exercised only by
        # a category that names the work. It is in the precedence chain for sources
        # that publish richer category text.
        result = self.mapping.classify(
            _notice(title="Projet 2026-14", category_raw="Travaux de voirie")
        )

        self.assertEqual(result["slug_sources"]["roadwork"], "category_raw")
        self.assertEqual(result["trade_slugs"][0], "roadwork")

    def test_description_only_slugs_still_appear_as_secondary(self) -> None:
        result = self.mapping.classify(
            _notice(
                title="Watermain replacement",
                description="Also includes sidewalk restoration and fencing.",
            )
        )

        self.assertEqual(result["trade_slugs"][0], "water_wastewater")
        self.assertIn("concrete_flatwork", result["trade_slugs"])
        self.assertEqual(result["slug_sources"]["concrete_flatwork"], "description")

    def test_construction_coding_is_recorded_for_the_unmapped_credit_split(self) -> None:
        construction = self.mapping.classify(
            _notice(title="Projet inconnu", category_normalized="construction")
        )
        service = self.mapping.classify(
            _notice(title="Unclassifiable engagement", category_raw="*SRV")
        )

        self.assertTrue(construction["construction_marked"])
        self.assertFalse(service["construction_marked"])


class HeatingSystemsRegressionTests(unittest.TestCase):
    """The notice that ranked #1 for the wrong reason.

    A *SRV engineering-consulting tender to replace heating systems, whose
    description carries "Ingénieur de structures" in a fee schedule. Before evidence
    tracking, that rate line made bridge_structural the primary slug and put the
    notice at the top of a civil contractor's list.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.mapping = trades.load_mapping()
        with (FIXTURES / "notice_heating_systems.json").open(encoding="utf-8") as handle:
            cls.notice = json.load(handle)
        cls.result = cls.mapping.classify(cls.notice)

    def test_the_fixture_still_contains_the_trap(self) -> None:
        self.assertIn("Heating Systems", self.notice["title"])
        self.assertIn("Ingénieur de structures", self.notice["description"])
        self.assertNotIn("structure", self.notice["title"].casefold())

    def test_the_work_the_title_describes_is_primary(self) -> None:
        self.assertEqual(self.result["trade_slugs"][0], "mechanical_hvac")

    def test_the_rate_table_mention_is_marked_description_only(self) -> None:
        self.assertEqual(
            self.result["slug_sources"]["bridge_structural"], "description"
        )

    def test_a_civil_firms_match_on_it_is_discounted(self) -> None:
        firm = {
            "trades": ["bridge_structural", "water_wastewater", "marine_shoreline"],
            "regions": ["ontario_any"],
            "buyer_type_preferences": ["federal"],
            "past_projects": [],
        }
        notice = {
            **self.notice,
            "trade_slugs": self.result["trade_slugs"],
            "slug_sources": self.result["slug_sources"],
            "mapping_status": self.result["mapping_status"],
            "construction_marked": self.result["construction_marked"],
            "closing_date_utc": "2026-09-01T18:00:00+00:00",
        }
        verdict = filters.evaluate(
            notice, firm, self.mapping, datetime(2026, 7, 30, 12, tzinfo=timezone.utc)
        )
        weights = scoring.load_weights()

        self.assertTrue(verdict["included"])
        self.assertEqual(
            verdict["context"]["trade_evidence"], filters.EVIDENCE_DESCRIPTION
        )
        scored = scoring.score_notice(notice, firm, verdict["context"], weights)
        full_credit = scoring.score_notice(
            notice,
            firm,
            {**verdict["context"], "trade_evidence": filters.EVIDENCE_STRONG},
            weights,
        )

        self.assertLess(scored["final_score"], full_credit["final_score"])
        self.assertAlmostEqual(
            scored["components"]["trade_match"]["points"],
            full_credit["components"]["trade_match"]["points"] * 0.7,
            2,
        )


class MapNoticesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = schema.connect(":memory:")
        self.addCleanup(self.connection.close)
        db.upsert_notices(
            self.connection,
            [
                {
                    "source": "canadabuys",
                    "source_id": "a",
                    "title": "South Campus Watermain Replacement",
                    "category_raw": "*CNST",
                    "category_normalized": "construction",
                },
                {
                    "source": "canadabuys",
                    "source_id": "b",
                    "title": "Projet inconnu",
                    "category_raw": "*CNST",
                    "category_normalized": "construction",
                },
                {
                    "source": "canadabuys",
                    "source_id": "c",
                    "title": "Software licence renewal",
                    "category_raw": "*GD",
                    "category_normalized": "goods",
                },
            ],
        )
        self.mapping = trades.load_mapping()

    def test_every_notice_is_classified_and_stored(self) -> None:
        result = trades.map_notices(self.connection, self.mapping)

        self.assertEqual(result["notices"], 3)
        self.assertEqual(result["inserted"], 3)
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM notice_trades").fetchone()[0], 3
        )

    def test_coverage_is_measured_over_construction_notices_only(self) -> None:
        result = trades.map_notices(self.connection, self.mapping)

        self.assertEqual(result["construction_total"], 2)
        self.assertEqual(result["construction_mapped"], 1)
        self.assertEqual(result["construction_coverage_pct"], 50.0)

    def test_re_mapping_unchanged_notices_writes_nothing(self) -> None:
        trades.map_notices(self.connection, self.mapping)

        again = trades.map_notices(self.connection, self.mapping)

        self.assertEqual(again["inserted"], 0)
        self.assertEqual(again["updated"], 0)
        self.assertEqual(again["unchanged"], 3)

    def test_unmapped_categories_are_reportable_for_extending_the_mapping(self) -> None:
        trades.map_notices(self.connection, self.mapping)

        rows = trades.unmapped_categories(self.connection, limit=5)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["category_raw"], "*CNST")
        self.assertEqual(rows[0]["total"], 1)


if __name__ == "__main__":
    unittest.main()
