import unittest
from datetime import datetime, timedelta, timezone

from notices.normalize import (
    iso_timestamp,
    normalize_buyer_type,
    normalize_category,
    normalize_region,
    normalize_status,
)


NOW = datetime(2026, 7, 29, 12, 0, tzinfo=timezone(timedelta(hours=-4)))


class CategoryTests(unittest.TestCase):
    def test_canadabuys_category_tokens_map_to_the_shared_vocabulary(self) -> None:
        self.assertEqual(normalize_category(category_raw="*CNST"), "construction")
        self.assertEqual(normalize_category(category_raw="*GD"), "goods")
        self.assertEqual(normalize_category(category_raw="*SRV"), "services")
        self.assertEqual(normalize_category(category_raw="*SRVTGD"), "services")

    def test_seao_categories_map_including_professional_services(self) -> None:
        self.assertEqual(
            normalize_category(
                category_raw="Travaux de construction", main_category="works"
            ),
            "construction",
        )
        self.assertEqual(
            normalize_category(
                category_raw="Services professionnels", main_category="services"
            ),
            "professional_services",
        )
        self.assertEqual(
            normalize_category(
                category_raw="Approvisionnement (biens)", main_category="goods"
            ),
            "goods",
        )

    def test_the_sources_own_category_is_never_overridden_by_free_text(self) -> None:
        # A CanadaBuys services notice whose description happens to mention a road
        # or the word "construction" must stay services.
        self.assertEqual(
            normalize_category(
                category_raw="*SRV",
                title="Local Internet Access Services",
                description="Fibre delivered to 12 Bayfield Road; see construction notes.",
                classification_codes=["72000000"],
            ),
            "services",
        )

    def test_construction_unspsc_segments_apply_when_no_category_is_published(
        self,
    ) -> None:
        self.assertEqual(
            normalize_category(category_raw="", classification_codes=["72000000"]),
            "construction",
        )
        self.assertEqual(
            normalize_category(category_raw="", classification_codes=["30111500"]),
            "construction",
        )
        self.assertEqual(
            normalize_category(category_raw="", classification_codes=["22100000"]),
            "other",
        )

    def test_keyword_fallback_promotes_construction_for_uncategorized_sources(
        self,
    ) -> None:
        self.assertEqual(
            normalize_category(title="DeJong Bridge rehabilitation"), "construction"
        )
        self.assertEqual(
            normalize_category(description="Watermain replacement on Queen Street"),
            "construction",
        )

    def test_free_text_only_ever_promotes_construction_never_goods_or_services(
        self,
    ) -> None:
        # Keyword promotion exists to protect construction recall. A source that
        # publishes no category of its own (a Bids&Tenders listing row) therefore
        # lands in "other" rather than being guessed at from its title.
        self.assertEqual(normalize_category(title="Elder Services Program"), "other")
        self.assertEqual(normalize_category(), "other")


class StatusTests(unittest.TestCase):
    def test_source_states_map_to_the_shared_vocabulary(self) -> None:
        future = "2026-09-01T14:00:00-04:00"
        self.assertEqual(normalize_status("Open", future, NOW), "open")
        self.assertEqual(normalize_status("active", future, NOW), "open")
        self.assertEqual(normalize_status("Planned", future, NOW), "planned")
        self.assertEqual(normalize_status("cancelled", future, NOW), "cancelled")
        self.assertEqual(normalize_status("annulé", future, NOW), "cancelled")
        self.assertEqual(normalize_status("unsuccessful", future, NOW), "closed")

    def test_an_open_notice_past_its_deadline_is_closed(self) -> None:
        self.assertEqual(
            normalize_status("Open", "2025-11-26T13:00:00", NOW), "closed"
        )

    def test_a_cancelled_notice_stays_cancelled_after_its_deadline(self) -> None:
        self.assertEqual(
            normalize_status("cancelled", "2025-11-26T13:00:00", NOW), "cancelled"
        )

    def test_a_missing_deadline_leaves_the_source_state_intact(self) -> None:
        self.assertEqual(normalize_status("Open", None, NOW), "open")
        self.assertEqual(normalize_status("", None, NOW), "unknown")

    def test_naive_and_aware_deadlines_are_both_comparable(self) -> None:
        self.assertEqual(normalize_status("Open", "2026-09-01T14:00:00", NOW), "open")
        self.assertEqual(
            normalize_status("Open", "2026-09-01T14:00:00-04:00", NOW), "open"
        )


class RegionTests(unittest.TestCase):
    def test_province_names_and_codes_become_sorted_codes(self) -> None:
        self.assertEqual(normalize_region("*Ontario (except NCR)"), "ON")
        self.assertEqual(normalize_region("Québec"), "QC")
        self.assertEqual(normalize_region("QC"), "QC")
        self.assertEqual(normalize_region("*Ontario\n*Quebec"), "ON,QC")

    def test_nationwide_notices_collapse_to_ca(self) -> None:
        self.assertEqual(normalize_region("*Canada"), "CA")

    def test_a_named_province_wins_over_the_national_marker(self) -> None:
        self.assertEqual(normalize_region("*Canada\n*Ontario (except NCR)"), "ON")

    def test_unrecognized_and_empty_regions_are_null(self) -> None:
        self.assertIsNone(normalize_region(""))
        self.assertIsNone(normalize_region("Region 4"))


class BuyerTypeTests(unittest.TestCase):
    def test_entity_names_reveal_the_buyer_type(self) -> None:
        self.assertEqual(
            normalize_buyer_type("The Corporation of the Town of Midland"), "municipal"
        )
        self.assertEqual(normalize_buyer_type("Municipalité de Saint-Majorique"), "municipal")
        self.assertEqual(normalize_buyer_type("Santé Québec Chaudière-Appalaches"), "health")
        self.assertEqual(
            normalize_buyer_type("Université du Québec à Montréal"), "education"
        )

    def test_unmatched_names_keep_the_callers_default(self) -> None:
        self.assertEqual(
            normalize_buyer_type("Defence Construction Canada", default="federal"),
            "federal",
        )
        self.assertEqual(normalize_buyer_type("", default="provincial"), "provincial")


class TimestampTests(unittest.TestCase):
    def test_iso_timestamps_are_normalized_and_bad_input_is_preserved(self) -> None:
        self.assertEqual(iso_timestamp("2026-07-28T14:00:00"), "2026-07-28T14:00:00")
        self.assertEqual(iso_timestamp("2026-01-23"), "2026-01-23T00:00:00")
        self.assertEqual(iso_timestamp("not a date"), "not a date")
        self.assertIsNone(iso_timestamp(""))


if __name__ == "__main__":
    unittest.main()
