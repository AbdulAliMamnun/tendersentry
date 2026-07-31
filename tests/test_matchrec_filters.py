import unittest
from datetime import datetime, timezone

from matchrec import filters, trades


NOW = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
FAR_FUTURE = "2026-09-30T15:00:00+00:00"


def _firm(**overrides) -> dict:
    firm = {
        "id": 1,
        "name": "Georgian Bay Civil Ltd.",
        "trades": ["water_wastewater", "bridge_structural"],
        "regions": ["ontario_any"],
        "value_min": 100_000,
        "value_max": 2_000_000,
        "buyer_type_preferences": ["municipal", "federal"],
        "past_projects": [],
    }
    firm.update(overrides)
    return firm


def _notice(**overrides) -> dict:
    notice = {
        "id": 1,
        "title": "Watermain replacement",
        "status": "open",
        "closing_date_utc": FAR_FUTURE,
        "region": "ON",
        "buyer_type": "municipal",
        "estimated_value": None,
        "trade_slugs": ["water_wastewater"],
        "mapping_status": "mapped",
    }
    notice.update(overrides)
    return notice


class ExclusionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mapping = trades.load_mapping()

    def evaluate(self, notice: dict, firm: dict | None = None) -> dict:
        return filters.evaluate(notice, firm or _firm(), self.mapping, NOW)

    def test_a_clean_match_survives_with_no_flags(self) -> None:
        verdict = self.evaluate(_notice(estimated_value=800_000))

        self.assertTrue(verdict["included"])
        self.assertEqual(verdict["reasons"], [])
        self.assertEqual(verdict["flags"], [])

    def test_a_closed_notice_is_excluded(self) -> None:
        verdict = self.evaluate(_notice(status="awarded"))

        self.assertFalse(verdict["included"])
        self.assertIn(filters.REASON_CLOSED, verdict["reasons"])
        self.assertIn("status=awarded", verdict["detail"])

    def test_an_undated_notice_is_excluded_as_unknown_not_as_closed(self) -> None:
        # Municipal pages publish notices with no closing date. Reporting those as
        # "closed" claimed knowledge we do not have, and hid why the municipal
        # ingest yielded zero candidates.
        verdict = self.evaluate(_notice(status="unknown", closing_date_utc=None))

        self.assertFalse(verdict["included"])
        self.assertIn(filters.REASON_CLOSING_DATE_UNKNOWN, verdict["reasons"])
        self.assertNotIn(filters.REASON_CLOSED, verdict["reasons"])
        self.assertEqual(verdict["reasons"][0], filters.REASON_CLOSING_DATE_UNKNOWN)

    def test_an_empty_status_is_treated_the_same_way(self) -> None:
        verdict = self.evaluate(_notice(status=""))

        self.assertIn(filters.REASON_CLOSING_DATE_UNKNOWN, verdict["reasons"])
        self.assertNotIn(filters.REASON_CLOSED, verdict["reasons"])

    def test_an_unknown_notice_stays_excluded(self) -> None:
        # Truthfully labelled, still never recommended.
        verdict = self.evaluate(_notice(status="unknown"))

        self.assertFalse(verdict["included"])

    def test_a_notice_closing_inside_24h_is_excluded(self) -> None:
        verdict = self.evaluate(_notice(closing_date_utc="2026-07-31T09:00:00+00:00"))

        self.assertFalse(verdict["included"])
        self.assertIn(filters.REASON_CLOSING_SOON, verdict["reasons"])

    def test_exclusion_details_do_not_embed_a_live_clock_reading(self) -> None:
        # A stored detail that changes every second rewrites the row on every run.
        notice = _notice(closing_date_utc="2026-07-31T09:00:00+00:00")

        early = filters.evaluate(notice, _firm(), self.mapping, NOW)
        later = filters.evaluate(
            notice, _firm(), self.mapping, NOW.replace(hour=13, minute=37)
        )

        self.assertEqual(early["detail"], later["detail"])
        self.assertNotIn("h to closing", early["detail"])

    def test_a_notice_just_past_the_24h_line_survives(self) -> None:
        verdict = self.evaluate(_notice(closing_date_utc="2026-07-31T13:00:00+00:00"))

        self.assertTrue(verdict["included"])

    def test_an_already_closed_deadline_is_excluded_even_when_status_says_open(self) -> None:
        # Belt and braces: status is computed at ingestion time and can go stale.
        verdict = self.evaluate(
            _notice(status="open", closing_date_utc="2026-07-29T09:00:00+00:00")
        )

        self.assertFalse(verdict["included"])
        self.assertIn(filters.REASON_CLOSING_SOON, verdict["reasons"])

    def test_a_notice_without_a_closing_date_is_excluded(self) -> None:
        verdict = self.evaluate(_notice(closing_date_utc=None))

        self.assertFalse(verdict["included"])
        self.assertIn(filters.REASON_NO_CLOSING_DATE, verdict["reasons"])

    def test_a_notice_outside_the_firms_provinces_is_excluded(self) -> None:
        verdict = self.evaluate(_notice(region="AB"))

        self.assertFalse(verdict["included"])
        self.assertIn(filters.REASON_REGION_MISMATCH, verdict["reasons"])
        self.assertIn("AB", verdict["detail"])

    def test_a_value_below_the_firms_floor_is_excluded(self) -> None:
        verdict = self.evaluate(_notice(estimated_value=25_000))

        self.assertFalse(verdict["included"])
        self.assertIn(filters.REASON_VALUE_OUT_OF_RANGE, verdict["reasons"])
        self.assertIn("below", verdict["detail"])

    def test_a_value_above_the_firms_ceiling_is_excluded(self) -> None:
        verdict = self.evaluate(_notice(estimated_value=9_000_000))

        self.assertFalse(verdict["included"])
        self.assertIn(filters.REASON_VALUE_OUT_OF_RANGE, verdict["reasons"])
        self.assertIn("above", verdict["detail"])

    def test_unrelated_trades_are_excluded(self) -> None:
        verdict = self.evaluate(_notice(trade_slugs=["snow_ice_management"]))

        self.assertFalse(verdict["included"])
        self.assertIn(filters.REASON_TRADE_MISMATCH, verdict["reasons"])

    def test_a_non_construction_notice_is_excluded(self) -> None:
        verdict = self.evaluate(
            _notice(mapping_status="non_construction", trade_slugs=[])
        )

        self.assertFalse(verdict["included"])
        self.assertIn(filters.REASON_NON_CONSTRUCTION, verdict["reasons"])

    def test_every_applicable_reason_is_recorded_not_just_the_first(self) -> None:
        verdict = self.evaluate(
            _notice(
                status="cancelled",
                region="AB",
                estimated_value=9_000_000,
                trade_slugs=["landscaping"],
            )
        )

        self.assertEqual(
            set(verdict["reasons"]),
            {
                filters.REASON_CLOSED,
                filters.REASON_REGION_MISMATCH,
                filters.REASON_VALUE_OUT_OF_RANGE,
                filters.REASON_TRADE_MISMATCH,
            },
        )


class KeepWithFlagTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mapping = trades.load_mapping()

    def evaluate(self, notice: dict, firm: dict | None = None) -> dict:
        return filters.evaluate(notice, firm or _firm(), self.mapping, NOW)

    def test_an_unknown_region_is_kept_and_flagged(self) -> None:
        verdict = self.evaluate(_notice(region=None))

        self.assertTrue(verdict["included"])
        self.assertIn(filters.FLAG_REGION_UNKNOWN, verdict["flags"])
        self.assertEqual(verdict["context"]["region_kind"], filters.REGION_UNKNOWN)

    def test_an_unknown_value_is_kept_and_flagged(self) -> None:
        verdict = self.evaluate(_notice(estimated_value=None))

        self.assertTrue(verdict["included"])
        self.assertIn(filters.FLAG_VALUE_UNKNOWN, verdict["flags"])

    def test_an_unmapped_work_type_is_kept_and_flagged(self) -> None:
        verdict = self.evaluate(_notice(mapping_status="unmapped", trade_slugs=[]))

        self.assertTrue(verdict["included"])
        self.assertIn(filters.FLAG_TRADE_UNMAPPED, verdict["flags"])
        self.assertEqual(
            verdict["context"]["trade_affinity_kind"], filters.TRADE_UNMAPPED
        )

    def test_a_same_family_trade_is_kept_and_flagged(self) -> None:
        # roadwork and water_wastewater are both civil: not an obvious mismatch.
        verdict = self.evaluate(_notice(trade_slugs=["roadwork"]))

        self.assertTrue(verdict["included"])
        self.assertIn(filters.FLAG_TRADE_FAMILY_ONLY, verdict["flags"])
        self.assertEqual(verdict["context"]["trade_affinity_kind"], filters.TRADE_FAMILY)

    def test_a_nationwide_notice_is_kept_as_a_partial_region_match(self) -> None:
        verdict = self.evaluate(_notice(region="CA"))

        self.assertTrue(verdict["included"])
        self.assertEqual(
            verdict["context"]["region_kind"], filters.REGION_MULTI_PROVINCE
        )

    def test_a_multi_province_notice_including_ontario_is_a_partial_match(self) -> None:
        verdict = self.evaluate(_notice(region="ON,QC"))

        self.assertTrue(verdict["included"])
        self.assertEqual(
            verdict["context"]["region_kind"], filters.REGION_MULTI_PROVINCE
        )

    def test_a_single_province_match_is_exact(self) -> None:
        verdict = self.evaluate(_notice(region="ON"))

        self.assertEqual(
            verdict["context"]["region_kind"], filters.REGION_SINGLE_PROVINCE
        )

    def test_federal_any_lets_a_firm_reach_other_provinces(self) -> None:
        firm = _firm(regions=["ontario_any", "federal_any"])

        verdict = self.evaluate(_notice(region="AB", buyer_type="federal"), firm)

        self.assertTrue(verdict["included"])

    def test_federal_any_does_not_open_up_other_provinces_municipal_work(self) -> None:
        firm = _firm(regions=["ontario_any", "federal_any"])

        verdict = self.evaluate(_notice(region="AB", buyer_type="municipal"), firm)

        self.assertFalse(verdict["included"])
        self.assertIn(filters.REASON_REGION_MISMATCH, verdict["reasons"])


class ContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mapping = trades.load_mapping()

    def test_the_context_reports_the_runway_scoring_will_use(self) -> None:
        verdict = filters.evaluate(
            _notice(closing_date_utc="2026-08-09T12:00:00+00:00"),
            _firm(),
            self.mapping,
            NOW,
        )

        self.assertAlmostEqual(verdict["context"]["days_to_close"], 10.0)
        self.assertAlmostEqual(verdict["context"]["hours_to_close"], 240.0)

    def test_overlapping_trades_are_counted_for_the_scoring_bonus(self) -> None:
        verdict = filters.evaluate(
            _notice(trade_slugs=["water_wastewater", "bridge_structural"]),
            _firm(),
            self.mapping,
            NOW,
        )

        self.assertEqual(verdict["context"]["trade_overlap"], 2)
        self.assertEqual(
            verdict["context"]["matched_trades"],
            ["water_wastewater", "bridge_structural"],
        )


if __name__ == "__main__":
    unittest.main()
