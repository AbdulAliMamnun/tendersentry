import json
import tempfile
import unittest
from pathlib import Path

from matchrec import filters, scoring


def _firm(**overrides) -> dict:
    firm = {
        "id": 1,
        "name": "Georgian Bay Civil Ltd.",
        "trades": ["water_wastewater", "bridge_structural"],
        "buyer_type_preferences": ["municipal", "federal"],
        "past_projects": [
            {"name": "a", "value": 400_000},
            {"name": "b", "value": 850_000},
            {"name": "c", "value": 1_200_000},
        ],
    }
    firm.update(overrides)
    return firm


def _context(**overrides) -> dict:
    context = {
        "days_to_close": 30.0,
        "hours_to_close": 720.0,
        "region_kind": filters.REGION_SINGLE_PROVINCE,
        "trade_affinity_kind": filters.TRADE_EXACT,
        "trade_overlap": 1,
        "matched_trades": ["water_wastewater"],
        "trade_evidence": filters.EVIDENCE_STRONG,
        "construction_coded": True,
    }
    context.update(overrides)
    return context


def _notice(**overrides) -> dict:
    notice = {"buyer_type": "municipal", "estimated_value": None}
    notice.update(overrides)
    return notice


class WeightsLoadingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def _write(self, payload: dict) -> Path:
        path = self.directory / "weights.json"
        with path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        return path

    def test_the_shipped_weights_load_and_sum_to_one_hundred(self) -> None:
        weights = scoring.load_weights()

        self.assertEqual(sum(weights["components"].values()), 100)
        self.assertEqual(weights["components"]["trade_match"], 45)
        self.assertEqual(weights["components"]["region_match"], 20)
        self.assertEqual(weights["components"]["buyer_type_preference"], 20)
        self.assertEqual(weights["components"]["recency_urgency"], 15)

    def test_weights_that_do_not_sum_to_one_hundred_are_rejected(self) -> None:
        path = self._write(
            {
                "components": {
                    "trade_match": 40,
                    "region_match": 20,
                    "buyer_type_preference": 20,
                    "recency_urgency": 15,
                },
                "recency_curve": [{"max_days": None, "score": 1.0}],
            }
        )

        with self.assertRaises(ValueError) as raised:
            scoring.load_weights(path)

        self.assertIn("sum to 100", str(raised.exception))

    def test_a_missing_component_is_rejected(self) -> None:
        path = self._write(
            {
                "components": {"trade_match": 60, "region_match": 40},
                "recency_curve": [{"max_days": None, "score": 1.0}],
            }
        )

        with self.assertRaises(ValueError) as raised:
            scoring.load_weights(path)

        self.assertIn("buyer_type_preference", str(raised.exception))

    def test_value_sweetspot_as_a_base_component_is_rejected(self) -> None:
        # Value fit is a modifier by design; smuggling it back into the base score
        # would make base scores non-comparable again.
        path = self._write(
            {
                "components": {
                    "trade_match": 35,
                    "value_sweetspot": 20,
                    "region_match": 15,
                    "buyer_type_preference": 15,
                    "recency_urgency": 15,
                },
                "recency_curve": [{"max_days": None, "score": 1.0}],
            }
        )

        with self.assertRaises(ValueError) as raised:
            scoring.load_weights(path)

        self.assertIn("value_sweetspot", str(raised.exception))

    def test_custom_weights_change_the_score(self) -> None:
        path = self._write(
            {
                "version": "custom",
                "components": {
                    "trade_match": 100,
                    "region_match": 0,
                    "buyer_type_preference": 0,
                    "recency_urgency": 0,
                },
                "trade_affinity": {"exact": 0.8, "family": 0.4, "unmapped": 0.3, "none": 0.0},
                "recency_curve": [{"max_days": None, "score": 1.0}],
            }
        )
        weights = scoring.load_weights(path)

        result = scoring.score_notice(_notice(), _firm(), _context(), weights)

        self.assertEqual(result["base_score"], 80.0)

    def test_reading_the_closing_floor_falls_back_to_the_default(self) -> None:
        weights = scoring.load_weights()
        self.assertEqual(scoring.min_hours_to_closing(weights), 24.0)

        bare = {"components": weights["components"], "recency_curve": weights["recency_curve"]}
        self.assertEqual(
            scoring.min_hours_to_closing(bare), filters.DEFAULT_MIN_HOURS_TO_CLOSING
        )


class ComponentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.weights = scoring.load_weights()

    def score(self, notice=None, firm=None, context=None) -> dict:
        return scoring.score_notice(
            notice or _notice(), firm or _firm(), context or _context(), self.weights
        )

    def test_every_component_reports_score_weight_points_and_a_reason(self) -> None:
        result = self.score()

        for name in scoring.BASE_COMPONENTS:
            with self.subTest(component=name):
                part = result["components"][name]
                self.assertIn("score", part)
                self.assertIn("weight", part)
                self.assertIn("points", part)
                self.assertTrue(part["detail"])
                self.assertAlmostEqual(part["points"], part["score"] * part["weight"], 2)

    def test_the_base_score_is_the_sum_of_component_points(self) -> None:
        result = self.score()

        self.assertAlmostEqual(
            result["base_score"],
            sum(part["points"] for part in result["components"].values()),
            2,
        )

    def test_a_perfect_match_scores_one_hundred(self) -> None:
        result = self.score(context=_context(trade_overlap=3, days_to_close=45))

        self.assertEqual(result["base_score"], 100.0)

    def test_an_unpreferred_buyer_scores_lower_than_a_preferred_one(self) -> None:
        preferred = self.score(_notice(buyer_type="municipal"))
        other = self.score(_notice(buyer_type="provincial"))
        unknown = self.score(_notice(buyer_type=None))

        self.assertGreater(
            preferred["components"]["buyer_type_preference"]["points"],
            unknown["components"]["buyer_type_preference"]["points"],
        )
        self.assertGreater(
            unknown["components"]["buyer_type_preference"]["points"],
            other["components"]["buyer_type_preference"]["points"],
        )

    def test_notice_buyer_types_are_translated_to_firm_vocabulary(self) -> None:
        firm = _firm(buyer_type_preferences=["hospital"])

        result = self.score(_notice(buyer_type="health"), firm)

        self.assertEqual(result["components"]["buyer_type_preference"]["score"], 1.0)


class MonotonicityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.weights = scoring.load_weights()

    def trade_points(self, **context) -> float:
        result = scoring.score_notice(
            _notice(), _firm(), _context(**context), self.weights
        )
        return result["components"]["trade_match"]["points"]

    def test_a_better_trade_match_never_scores_lower(self) -> None:
        none = self.trade_points(trade_affinity_kind=filters.TRADE_NONE, trade_overlap=0)
        unmapped = self.trade_points(
            trade_affinity_kind=filters.TRADE_UNMAPPED, trade_overlap=0
        )
        family = self.trade_points(
            trade_affinity_kind=filters.TRADE_FAMILY, trade_overlap=1
        )
        exact = self.trade_points(
            trade_affinity_kind=filters.TRADE_EXACT, trade_overlap=1
        )

        self.assertLessEqual(none, unmapped)
        self.assertLessEqual(unmapped, family)
        self.assertLess(family, exact)

    def test_more_overlapping_trades_never_score_lower(self) -> None:
        points = [
            self.trade_points(
                trade_affinity_kind=filters.TRADE_EXACT, trade_overlap=overlap
            )
            for overlap in range(1, 6)
        ]

        self.assertEqual(points, sorted(points))
        self.assertLessEqual(max(points), self.weights["components"]["trade_match"])

    def test_the_overlap_bonus_is_capped(self) -> None:
        many = self.trade_points(
            trade_affinity_kind=filters.TRADE_EXACT, trade_overlap=50
        )

        self.assertLessEqual(many, self.weights["components"]["trade_match"])

    def recency_points(self, days: float) -> float:
        result = scoring.score_notice(
            _notice(), _firm(), _context(days_to_close=days), self.weights
        )
        return result["components"]["recency_urgency"]["points"]

    def test_more_runway_never_scores_lower_up_to_the_cap(self) -> None:
        points = [self.recency_points(days) for days in (1.0, 3.0, 7.0, 15.0, 30.0, 89.0)]

        self.assertEqual(points, sorted(points))

    def test_runway_beyond_the_cap_scores_below_the_actionable_window(self) -> None:
        # A 2029 standing offer must not outrank an August tender.
        actionable = self.recency_points(30.0)
        standing_offer = self.recency_points(970.0)

        self.assertLess(standing_offer, actionable)
        self.assertLess(standing_offer, self.recency_points(15.0))
        self.assertGreater(standing_offer, self.recency_points(3.0))

    def test_the_cap_is_explained_in_the_component_detail(self) -> None:
        result = scoring.score_notice(
            _notice(), _firm(), _context(days_to_close=400.0), self.weights
        )

        self.assertIn("beyond the 90-day cap", result["components"]["recency_urgency"]["detail"])

    def test_a_short_runway_is_penalized_and_says_so(self) -> None:
        result = scoring.score_notice(
            _notice(), _firm(), _context(days_to_close=2.0), self.weights
        )

        self.assertIn("penalty", result["components"]["recency_urgency"]["detail"])

    def test_a_closer_value_fit_never_scores_lower(self) -> None:
        firm = _firm()
        baseline = 850_000
        modifiers = [
            scoring.value_modifier(
                _notice(estimated_value=baseline * factor), firm, self.weights
            )[0]
            for factor in (8.0, 4.0, 2.0, 1.0)
        ]

        self.assertEqual(modifiers, sorted(modifiers))


class EvidenceQualityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.weights = scoring.load_weights()

    def trade(self, **context) -> dict:
        result = scoring.score_notice(
            _notice(), _firm(), _context(**context), self.weights
        )
        return result["components"]["trade_match"]

    def test_a_description_only_trade_match_is_discounted(self) -> None:
        strong = self.trade(trade_evidence=filters.EVIDENCE_STRONG)
        weak = self.trade(trade_evidence=filters.EVIDENCE_DESCRIPTION)

        self.assertAlmostEqual(weak["points"], strong["points"] * 0.7, 2)
        self.assertIn("only in the description", weak["detail"])

    def test_the_discount_applies_to_family_matches_too(self) -> None:
        strong = self.trade(
            trade_affinity_kind=filters.TRADE_FAMILY,
            trade_evidence=filters.EVIDENCE_STRONG,
        )
        weak = self.trade(
            trade_affinity_kind=filters.TRADE_FAMILY,
            trade_evidence=filters.EVIDENCE_DESCRIPTION,
        )

        self.assertLess(weak["points"], strong["points"])

    def test_a_discounted_match_still_beats_no_match(self) -> None:
        weak = self.trade(trade_evidence=filters.EVIDENCE_DESCRIPTION)
        none = self.trade(
            trade_affinity_kind=filters.TRADE_NONE,
            trade_overlap=0,
            trade_evidence=filters.EVIDENCE_STRONG,
        )

        self.assertGreater(weak["points"], none["points"])

    def test_an_unmapped_notice_coded_outside_construction_earns_less_credit(self) -> None:
        construction = self.trade(
            trade_affinity_kind=filters.TRADE_UNMAPPED, construction_coded=True
        )
        service = self.trade(
            trade_affinity_kind=filters.TRADE_UNMAPPED, construction_coded=False
        )

        self.assertEqual(construction["score"], 0.3)
        self.assertEqual(service["score"], 0.15)
        self.assertIn("outside construction", service["detail"])

    def test_the_unmapped_split_never_inverts_the_affinity_ladder(self) -> None:
        service_unmapped = self.trade(
            trade_affinity_kind=filters.TRADE_UNMAPPED, construction_coded=False
        )
        none = self.trade(trade_affinity_kind=filters.TRADE_NONE, trade_overlap=0)
        family = self.trade(trade_affinity_kind=filters.TRADE_FAMILY)

        self.assertLessEqual(none["points"], service_unmapped["points"])
        self.assertLessEqual(service_unmapped["points"], family["points"])


class LongHorizonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.weights = scoring.load_weights()

    def flags(self, days: float) -> list[str]:
        return scoring.score_notice(
            _notice(), _firm(), _context(days_to_close=days), self.weights
        )["flags"]

    def test_a_distant_closing_date_is_flagged(self) -> None:
        self.assertIn(scoring.FLAG_LONG_HORIZON, self.flags(970.0))
        self.assertIn(scoring.FLAG_LONG_HORIZON, self.flags(181.0))

    def test_a_normal_closing_date_is_not_flagged(self) -> None:
        self.assertNotIn(scoring.FLAG_LONG_HORIZON, self.flags(30.0))
        self.assertNotIn(scoring.FLAG_LONG_HORIZON, self.flags(179.0))

    def test_the_threshold_comes_from_the_config(self) -> None:
        self.assertEqual(self.weights["long_horizon_days"], 180)


class ValueModifierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.weights = scoring.load_weights()

    def test_a_value_at_the_firms_median_earns_the_full_bonus(self) -> None:
        modifier, detail, flags = scoring.value_modifier(
            _notice(estimated_value=850_000), _firm(), self.weights
        )

        self.assertEqual(modifier, 10.0)
        self.assertIn("850,000", detail)
        self.assertEqual(flags, [])

    def test_a_wildly_mismatched_value_earns_the_full_penalty(self) -> None:
        modifier, _, _ = scoring.value_modifier(
            _notice(estimated_value=50_000_000), _firm(), self.weights
        )

        self.assertEqual(modifier, -10.0)

    def test_the_modifier_always_stays_within_ten_points(self) -> None:
        for value in (1, 1_000, 100_000, 850_000, 5_000_000, 10**9):
            with self.subTest(value=value):
                modifier, _, _ = scoring.value_modifier(
                    _notice(estimated_value=value), _firm(), self.weights
                )
                self.assertGreaterEqual(modifier, -10.0)
                self.assertLessEqual(modifier, 10.0)

    def test_an_absent_value_is_neutral_never_a_penalty(self) -> None:
        modifier, detail, flags = scoring.value_modifier(
            _notice(estimated_value=None), _firm(), self.weights
        )

        self.assertEqual(modifier, 0.0)
        self.assertEqual(flags, [])
        self.assertIn("no published value", detail)

    def test_a_firm_without_past_values_is_neutral_and_flagged(self) -> None:
        modifier, _, flags = scoring.value_modifier(
            _notice(estimated_value=850_000), _firm(past_projects=[]), self.weights
        )

        self.assertEqual(modifier, 0.0)
        self.assertEqual(flags, [scoring.FLAG_VALUE_BASELINE_UNKNOWN])

    def test_the_base_score_is_identical_whether_or_not_a_value_is_present(self) -> None:
        # Deliberately below the ceiling so the modifier is visible in full.
        context = _context(days_to_close=2.0)
        without = scoring.score_notice(
            _notice(estimated_value=None), _firm(), context, self.weights
        )
        with_value = scoring.score_notice(
            _notice(estimated_value=850_000), _firm(), context, self.weights
        )

        self.assertEqual(without["base_score"], with_value["base_score"])
        self.assertEqual(without["value_modifier"], 0.0)
        self.assertEqual(without["final_score"], without["base_score"])
        self.assertEqual(with_value["value_modifier"], 10.0)
        self.assertEqual(with_value["final_score"], with_value["base_score"] + 10.0)

    def test_the_modifier_cannot_push_a_score_past_one_hundred(self) -> None:
        # A high base plus a full bonus clamps rather than overflowing the scale.
        result = scoring.score_notice(
            _notice(estimated_value=850_000), _firm(), _context(), self.weights
        )

        self.assertEqual(result["base_score"], 91.0)
        self.assertEqual(result["value_modifier"], 10.0)
        self.assertEqual(result["final_score"], 100.0)

    def test_the_final_score_never_leaves_the_zero_to_one_hundred_range(self) -> None:
        perfect = scoring.score_notice(
            _notice(estimated_value=850_000),
            _firm(),
            _context(trade_overlap=3, days_to_close=45),
            self.weights,
        )

        self.assertEqual(perfect["base_score"], 100.0)
        self.assertEqual(perfect["final_score"], 100.0)


if __name__ == "__main__":
    unittest.main()
