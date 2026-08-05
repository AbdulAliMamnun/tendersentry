"""Contract-scale estimation: bands, pattern rules, the price index, and the migration.

The estimator's job is to answer "how big is this job?" for the 99% of notices that
publish nothing — and to say `unknown` rather than guess when it cannot. These tests
assert both halves, because a band that is always produced is worse than no band.
"""

from __future__ import annotations

import sqlite3
import unittest

from model import inflation, scale
from notices import db


class BandTests(unittest.TestCase):
    def test_amounts_land_in_the_expected_bands(self) -> None:
        self.assertEqual("<$100K", scale.band_of(45_000))
        self.assertEqual("$100–500K", scale.band_of(200_000))
        self.assertEqual("$500K–2M", scale.band_of(1_200_000))
        self.assertEqual("$2–10M", scale.band_of(4_000_000))
        self.assertEqual(">$10M", scale.band_of(25_000_000))

    def test_boundaries_belong_to_the_upper_band(self) -> None:
        self.assertEqual("$100–500K", scale.band_of(100_000))
        self.assertEqual("$500K–2M", scale.band_of(500_000))

    def test_a_missing_or_nonsense_amount_is_unknown_not_the_smallest_band(self) -> None:
        """Filing an unpriced notice as "<$100K" would be a fabricated claim."""
        self.assertEqual("unknown", scale.band_of(None))
        self.assertEqual("unknown", scale.band_of(0))
        self.assertEqual("unknown", scale.band_of(-5))

    def test_band_distance_measures_separation_and_refuses_unknowns(self) -> None:
        self.assertEqual(0, scale.band_distance("$100–500K", "$100–500K"))
        self.assertEqual(3, scale.band_distance("<$100K", "$2–10M"))
        self.assertIsNone(scale.band_distance("unknown", "$2–10M"))


class PatternRuleTests(unittest.TestCase):
    """Tier 3: deterministic markers, in both languages."""

    def test_a_treatment_plant_reads_large(self) -> None:
        band, marker = scale.pattern_band(
            "Remplacement du système de prétraitement de la station d’épuration des "
            "eaux usées – Conception et construction"
        )
        self.assertEqual("$2–10M", band)
        self.assertIn("plant", marker)

    def test_multiple_streets_read_large(self) -> None:
        band, _ = scale.pattern_band(
            "Travaux de réfection de chaussée sur diverses rues de l'arrondissement"
        )
        self.assertEqual("$2–10M", band)

    def test_english_markers_fire_too(self) -> None:
        """Half the pool is English; a French-only rule silently never fires on it."""
        self.assertEqual("$2–10M", scale.pattern_band("Wastewater Treatment Plant Upgrade")[0])
        self.assertEqual("<$100K", scale.pattern_band("Culvert Replacement, Highway 11")[0])

    def test_a_culvert_or_minor_works_reads_small(self) -> None:
        self.assertEqual("<$100K", scale.pattern_band("Remplacement d'un ponceau")[0])
        self.assertEqual("<$100K", scale.pattern_band("Travaux mineurs de voirie")[0])

    def test_contradictory_markers_produce_nothing(self) -> None:
        """A title that reads both large and small is ambiguous, and guessing between
        them is exactly the confident nonsense the pipeline exists to prevent."""
        self.assertIsNone(
            scale.pattern_band("Travaux mineurs à la station d'épuration — diverses rues")
        )

    def test_an_ordinary_title_produces_nothing(self) -> None:
        self.assertIsNone(scale.pattern_band("Travaux de pavage rue Jeannotte"))


class InflationTests(unittest.TestCase):
    """The price index, and the sector caveat that must travel with it."""

    def test_the_series_is_the_one_we_documented(self) -> None:
        series = inflation.load()["series"]
        self.assertEqual("18-10-0289-01", series["table"])
        self.assertEqual("Quebec", series["geography"])
        self.assertEqual("2023=100", series["base_period"])

    def test_the_series_records_that_it_is_a_proxy(self) -> None:
        """A building index deflating civil work is defensible only if it says so."""
        caveat = inflation.load()["series"]["sector_caveat"]
        self.assertIn("proxy", caveat)
        self.assertIn("18-10-0022", caveat)
        self.assertIn("18-10-0096", caveat)

    def test_quarters_are_derived_correctly(self) -> None:
        self.assertEqual("2024-Q1", inflation.quarter_of("2024-01-15"))
        self.assertEqual("2024-Q4", inflation.quarter_of("2024-12-31"))

    def test_older_dollars_are_worth_more_today(self) -> None:
        """Construction inflation is large over this window; ignoring it would treat a
        2018 million-dollar job as the same size as a 2026 one."""
        adjusted = inflation.adjust(1_000_000, "2018-01-15")
        self.assertIsNotNone(adjusted)
        self.assertGreater(adjusted, 1_400_000)

    def test_a_date_before_the_series_cannot_be_adjusted(self) -> None:
        """Returning None forces the caller to decide, rather than handing back an
        unadjusted number that looks adjusted."""
        self.assertIsNone(inflation.adjust(1_000_000, "2009-06-01"))

    def test_a_date_past_the_series_uses_the_latest_quarter(self) -> None:
        """Publication lag is not a gap in coverage."""
        self.assertIsNotNone(inflation.adjust(1_000_000, "2030-01-01"))


class EstimateTests(unittest.TestCase):
    """Tier selection: published beats learned beats pattern beats nothing."""

    def setUp(self) -> None:
        self.lookup = scale.LookupEstimator(min_cell=2).fit(
            [
                scale.Award("a", "2024-01-01", 300_000, "water_wastewater", "municipal", "QC", "t"),
                scale.Award("b", "2024-02-01", 320_000, "water_wastewater", "municipal", "QC", "t"),
                scale.Award("c", "2024-03-01", 280_000, "water_wastewater", "municipal", "QC", "t"),
            ]
        )

    def test_a_published_value_overrides_every_estimate(self) -> None:
        result = scale.estimate_notice(
            {
                "title": "Station d'épuration — diverses rues",  # would read large
                "estimated_value": 45_000,
                "trade_slugs": ["water_wastewater"],
                "buyer_type": "municipal",
                "region": "QC",
            },
            self.lookup,
        )
        self.assertEqual("published", result.source)
        self.assertEqual("<$100K", result.band)
        self.assertEqual(1.0, result.confidence)

    def test_the_learned_tier_is_used_when_nothing_is_published(self) -> None:
        result = scale.estimate_notice(
            {
                "title": "Remplacement de conduites d'eau potable",
                "estimated_value": None,
                "trade_slugs": ["water_wastewater"],
                "buyer_type": "municipal",
                "region": "QC",
            },
            self.lookup,
        )
        self.assertEqual("estimated_model", result.source)
        self.assertEqual("$100–500K", result.band)

    def test_pattern_rules_catch_what_the_lookup_cannot(self) -> None:
        """No trade slug means no comparable cell, but the wording still says a lot."""
        result = scale.estimate_notice(
            {
                "title": "Construction d'une station de pompage",
                "estimated_value": None,
                "trade_slugs": [],
                "buyer_type": "municipal",
                "region": "QC",
            },
            self.lookup,
        )
        self.assertEqual("estimated_pattern", result.source)
        self.assertEqual("$2–10M", result.band)

    def test_no_signal_yields_unknown_rather_than_a_forced_band(self) -> None:
        result = scale.estimate_notice(
            {"title": "GASKET", "estimated_value": None, "trade_slugs": [],
             "buyer_type": "unknown", "region": None},
            None,
        )
        self.assertEqual("unknown", result.source)
        self.assertEqual("unknown", result.band)
        self.assertEqual(0.0, result.confidence)

    def test_a_broader_fallback_cell_carries_lower_confidence(self) -> None:
        """A median drawn from a looser comparison is a weaker claim and says so."""
        specific = scale.estimate_notice(
            {"title": "x", "estimated_value": None, "trade_slugs": ["water_wastewater"],
             "buyer_type": "municipal", "region": "QC"},
            self.lookup,
        )
        broad = scale.estimate_notice(
            {"title": "x", "estimated_value": None, "trade_slugs": ["water_wastewater"],
             "buyer_type": "health", "region": "AB"},
            self.lookup,
        )
        self.assertGreater(specific.confidence, broad.confidence)


class LookupTests(unittest.TestCase):
    def test_a_thin_cell_is_skipped_rather_than_quoted(self) -> None:
        """A median of four contracts is an anecdote, not a comparable."""
        lookup = scale.LookupEstimator(min_cell=10).fit(
            [
                scale.Award(str(i), "2024-01-01", 1_000_000, "roadwork", "municipal", "QC", "t")
                for i in range(4)
            ]
        )
        self.assertEqual({}, lookup.cells)
        # It still answers, from the global median, and the caller sees the weaker cell.
        self.assertEqual("global", lookup.predict("roadwork", "municipal", "QC")[1])


class MigrationTests(unittest.TestCase):
    """The additive migration follows the source-constraint convention."""

    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        db.create_schema(self.connection)

    def tearDown(self) -> None:
        self.connection.close()

    def test_the_columns_are_added(self) -> None:
        result = db.migrate_scale_columns(self.connection)
        self.assertTrue(result["migrated"])
        columns = {r["name"] for r in self.connection.execute("PRAGMA table_info(tenders)")}
        self.assertLessEqual({"scale_band", "scale_source", "scale_confidence"}, columns)

    def test_running_it_twice_changes_nothing(self) -> None:
        """Backfills call it every run, so it must be safe without a guard."""
        db.migrate_scale_columns(self.connection)
        second = db.migrate_scale_columns(self.connection)
        self.assertFalse(second["migrated"])
        self.assertEqual([], second["added"])

    def test_the_row_count_is_verified(self) -> None:
        before = self.connection.execute("SELECT COUNT(*) FROM tenders").fetchone()[0]
        result = db.migrate_scale_columns(self.connection)
        self.assertEqual(before, result["rows"])


if __name__ == "__main__":
    unittest.main()
