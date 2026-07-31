import unittest
from datetime import datetime, timezone

from matchrec import timeutil


class ParsingTests(unittest.TestCase):
    def test_a_naive_canadabuys_stamp_is_read_as_eastern_time(self) -> None:
        # 14:00 in Toronto during DST is 18:00 UTC.
        self.assertEqual(
            timeutil.utc_iso("2026-07-30T14:00:00"), "2026-07-30T18:00:00+00:00"
        )

    def test_the_eastern_assumption_follows_daylight_saving(self) -> None:
        # Same wall-clock time in January is 19:00 UTC, not 18:00.
        self.assertEqual(
            timeutil.utc_iso("2026-01-30T14:00:00"), "2026-01-30T19:00:00+00:00"
        )

    def test_a_seao_offset_stamp_is_respected_not_relocalized(self) -> None:
        self.assertEqual(
            timeutil.utc_iso("2026-08-27T11:00:00-04:00"), "2026-08-27T15:00:00+00:00"
        )

    def test_a_utc_stamp_round_trips(self) -> None:
        self.assertEqual(
            timeutil.utc_iso("2026-08-27T15:00:00Z"), "2026-08-27T15:00:00+00:00"
        )

    def test_unusable_values_are_none_not_exceptions(self) -> None:
        self.assertIsNone(timeutil.utc_iso(None))
        self.assertIsNone(timeutil.utc_iso(""))
        self.assertIsNone(timeutil.utc_iso("closing when filled"))

    def test_normalized_strings_sort_chronologically(self) -> None:
        # Uniform +00:00 offsets are what make ORDER BY closing_date_utc correct.
        naive = timeutil.utc_iso("2026-07-30T14:00:00")
        offset = timeutil.utc_iso("2026-07-30T09:00:00-04:00")
        self.assertLess(offset, naive)


class RunwayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)

    def test_hours_until_is_positive_before_and_negative_after(self) -> None:
        self.assertAlmostEqual(
            timeutil.hours_until("2026-07-30T18:00:00+00:00", self.now), 6.0
        )
        self.assertAlmostEqual(
            timeutil.hours_until("2026-07-30T06:00:00+00:00", self.now), -6.0
        )

    def test_days_until_matches_hours(self) -> None:
        self.assertAlmostEqual(
            timeutil.days_until("2026-08-02T12:00:00+00:00", self.now), 3.0
        )

    def test_a_missing_closing_date_has_no_runway(self) -> None:
        self.assertIsNone(timeutil.hours_until(None, self.now))
        self.assertIsNone(timeutil.days_until("", self.now))

    def test_runway_is_measured_across_source_conventions(self) -> None:
        # A naive Eastern stamp and its UTC equivalent must agree.
        naive = timeutil.hours_until("2026-07-30T14:00:00", self.now)
        explicit = timeutil.hours_until("2026-07-30T18:00:00+00:00", self.now)
        self.assertAlmostEqual(naive, explicit)


if __name__ == "__main__":
    unittest.main()
