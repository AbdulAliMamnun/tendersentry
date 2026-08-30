"""Contract-scale estimation: bands, pattern rules, the price index, and the migration.

The estimator's job is to answer "how big is this job?" for the 99% of notices that
publish nothing — and to say `unknown` rather than guess when it cannot. These tests
assert both halves, because a band that is always produced is worse than no band.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from matchrec import schema as matchrec_schema
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


def _awards(count: int = 40) -> list[scale.Award]:
    """A corpus spanning two trades and two bands, big enough to clear MIN_CELL."""
    awards = []
    for index in range(count):
        big = index % 2 == 0
        awards.append(
            scale.Award(
                ocid=f"ocid-{index}",
                date="2024-03-15",
                amount=5_000_000.0 if big else 60_000.0,
                slug="roadwork" if big else "janitorial",
                buyer_type="municipal",
                region="QC",
                title=(
                    "Reconstruction de la rue Principale"
                    if big
                    else "Entretien menager edifice"
                ),
            )
        )
    return awards


class ArtifactTests(unittest.TestCase):
    """Round-trip: a loaded estimator must predict exactly what the fitted one did.

    The lookup tier is asserted here rather than the GBM tier because it is the one
    that survives without lightgbm installed; the GBM round-trip is covered by
    `test_the_booster_round_trips_when_lightgbm_is_available`, which skips when it
    is not.
    """

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.estimator = Path(self.temp.name) / "scale-estimator.json"
        self.booster = Path(self.temp.name) / "scale-estimator.lgb"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_the_lookup_round_trips_through_the_artifact(self) -> None:
        awards = _awards()
        original = scale.LookupEstimator().fit(awards)
        scale.save_estimators(original, None, awards, self.estimator, self.booster)

        loaded, gbm, payload = scale.load_estimators(self.estimator, self.booster)

        self.assertIsNone(gbm)
        self.assertEqual(original.cells, loaded.cells)
        self.assertEqual(original.counts, loaded.counts)
        self.assertEqual(original.global_median, loaded.global_median)
        self.assertEqual(original.min_cell, loaded.min_cell)
        for slug in ("roadwork", "janitorial", "unheard-of"):
            self.assertEqual(
                original.predict(slug, "municipal", "QC"),
                loaded.predict(slug, "municipal", "QC"),
            )

    def test_the_booster_round_trips_when_lightgbm_is_available(self) -> None:
        try:
            from model import embeddings as emb
        except ImportError:  # pragma: no cover - optional dependency
            self.skipTest("embeddings unavailable")
        try:
            import lightgbm  # noqa: F401
        except ImportError:  # pragma: no cover - optional dependency
            self.skipTest("lightgbm unavailable")

        awards = _awards()
        vectors = emb.embed([award.title for award in awards])
        original = scale.fit_gbm(awards, vectors)
        lookup = scale.LookupEstimator().fit(awards)
        scale.save_estimators(lookup, original, awards, self.estimator, self.booster)

        _, loaded, _ = scale.load_estimators(self.estimator, self.booster)

        self.assertIsNotNone(loaded)
        self.assertEqual(original.slug_order, loaded.slug_order)
        self.assertEqual(original.buyer_order, loaded.buyer_order)
        self.assertEqual(original.region_order, loaded.region_order)
        before = original.predict_many(
            [a.slug for a in awards],
            [a.buyer_type for a in awards],
            [a.region for a in awards],
            [a.title for a in awards],
            vectors,
        )
        after = loaded.predict_many(
            [a.slug for a in awards],
            [a.buyer_type for a in awards],
            [a.region for a in awards],
            [a.title for a in awards],
            vectors,
        )
        self.assertEqual(before, after)

    def test_the_artifact_carries_its_provenance(self) -> None:
        awards = _awards()
        scale.save_estimators(
            scale.LookupEstimator().fit(awards), None, awards, self.estimator, self.booster
        )
        payload = json.loads(self.estimator.read_text(encoding="utf-8"))

        self.assertEqual(scale.ARTIFACT_VERSION, payload["artifact_version"])
        self.assertIn("generated_at", payload)
        self.assertEqual(len(awards), payload["corpus"]["awards"])
        self.assertEqual(scale.CORPUS_START, payload["corpus"]["corpus_start"])
        self.assertEqual("bid_interactions", payload["corpus"]["source_table"])
        self.assertEqual(inflation.SERIES["table"], payload["deflator"]["table"])

    def test_a_lookup_only_refit_removes_a_stale_booster(self) -> None:
        """Otherwise the loader pairs a new slug order with an old booster."""
        awards = _awards()
        self.booster.write_text("stale booster", encoding="utf-8")
        scale.save_estimators(
            scale.LookupEstimator().fit(awards), None, awards, self.estimator, self.booster
        )
        self.assertFalse(self.booster.exists())


class MissingArtifactTests(unittest.TestCase):
    """A backfill without an artifact must stop, not quietly refit."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.missing = Path(self.temp.name) / "absent.json"
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        db.create_schema(self.connection)

    def tearDown(self) -> None:
        self.connection.close()
        self.temp.cleanup()

    def test_loading_an_absent_artifact_names_the_fix(self) -> None:
        with self.assertRaises(scale.MissingArtifact) as caught:
            scale.load_estimators(self.missing, self.missing)
        self.assertIn("--fit", str(caught.exception))

    def test_backfill_refuses_rather_than_refitting(self) -> None:
        with self.assertRaises(scale.MissingArtifact) as caught:
            scale.backfill(self.connection, estimator_path=self.missing)
        self.assertIn("--fit", str(caught.exception))

    def test_a_corrupt_artifact_fails_the_load(self) -> None:
        broken = Path(self.temp.name) / "broken.json"
        broken.write_text("{not json", encoding="utf-8")
        with self.assertRaises(scale.MissingArtifact):
            scale.load_estimators(broken, self.missing)

    def test_an_artifact_from_a_future_shape_is_refused(self) -> None:
        """A silently misread artifact is worse than a missing one."""
        future = Path(self.temp.name) / "future.json"
        future.write_text(
            json.dumps({"artifact_version": scale.ARTIFACT_VERSION + 1, "lookup": {}}),
            encoding="utf-8",
        )
        with self.assertRaises(scale.MissingArtifact) as caught:
            scale.load_estimators(future, self.missing)
        self.assertIn("--fit", str(caught.exception))


class IncrementalBackfillTests(unittest.TestCase):
    """The daily pass touches new notices only."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.estimator = Path(self.temp.name) / "scale-estimator.json"
        self.booster = Path(self.temp.name) / "scale-estimator.lgb"
        awards = _awards()
        scale.save_estimators(
            scale.LookupEstimator().fit(awards), None, awards, self.estimator, self.booster
        )

        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        db.create_schema(self.connection)
        # notice_trades belongs to matchrec, and the daily path creates it when
        # map_notices runs ahead of the backfill. The join here has always assumed it.
        matchrec_schema.ensure_schema(self.connection)
        db.migrate_scale_columns(self.connection)
        with self.connection:
            for index in range(4):
                self.connection.execute(
                    "INSERT INTO tenders (source, source_id, title, buyer_name, region, "
                    "documents_open, status, ingested_at, updated_at) "
                    "VALUES ('seao', ?, ?, 'Ville de Test', 'QC', 0, 'open', 'n', 'n')",
                    (f"t-{index}", "Reconstruction de la rue Principale"),
                )

    def tearDown(self) -> None:
        self.connection.close()
        self.temp.cleanup()

    def _bands(self) -> dict[int, tuple]:
        return {
            int(row["id"]): (row["scale_band"], row["scale_source"], row["scale_confidence"])
            for row in self.connection.execute(
                "SELECT id, scale_band, scale_source, scale_confidence FROM tenders"
            )
        }

    def _run(self, all_rows: bool = False) -> dict:
        return scale.backfill(
            self.connection,
            all_rows=all_rows,
            estimator_path=self.estimator,
            booster_path=self.booster,
        )

    def test_the_first_pass_bands_everything_unbanded(self) -> None:
        summary = self._run()
        self.assertEqual(4, summary["notices"])
        self.assertEqual("unbanded", summary["scope"])
        self.assertTrue(all(band is not None for band, _, _ in self._bands().values()))

    def test_a_second_pass_selects_nothing(self) -> None:
        self._run()
        before = self._bands()
        summary = self._run()

        self.assertEqual(0, summary["notices"])
        self.assertEqual(before, self._bands())

    def test_only_the_new_notice_is_banded(self) -> None:
        self._run()
        before = self._bands()
        with self.connection:
            self.connection.execute(
                "INSERT INTO tenders (source, source_id, title, buyer_name, region, "
                "documents_open, status, ingested_at, updated_at) "
                "VALUES ('seao', 'fresh', 'Entretien menager edifice', 'Ville', 'QC', "
                "0, 'open', 'n', 'n')"
            )

        summary = self._run()

        self.assertEqual(1, summary["notices"])
        after = self._bands()
        # Every pre-existing row is byte-identical; only the new id appears.
        self.assertEqual(before, {k: v for k, v in after.items() if k in before})
        self.assertEqual(1, len(set(after) - set(before)))

    def test_the_all_flag_forces_a_full_pass(self) -> None:
        self._run()
        summary = self._run(all_rows=True)
        self.assertEqual(4, summary["notices"])
        self.assertEqual("all", summary["scope"])

    def test_the_summary_cites_the_estimator_it_used(self) -> None:
        summary = self._run()
        self.assertIsNotNone(summary["estimator"]["generated_at"])
        self.assertEqual(40, summary["estimator"]["corpus_awards"])


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
