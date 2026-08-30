"""The freshness line the site shows, against the manifest it claims to read.

**The displayed date is `max_ingested_at`, not `generated_at`.** The two diverge
exactly when the pipeline is broken in the way that matters: an export that ingests
nothing still writes a fresh `generated_at`, so sourcing the page from it told
visitors the data was current today while the newest notice was a month old. A date on
a page is read as a promise; that one was false.

The number on the page and the number `tests/test_freshness.py` fails the suite over
must be the same number. If they can drift, the site can display a confident date
while the suite that guards it looks somewhere else — worse than showing nothing.
"""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

import config
from tests import ts_harness


MANIFEST = Path(config.PROJECT_ROOT) / "web" / "data" / "model" / "manifest.json"
WEB = Path(config.PROJECT_ROOT) / "web"


def _readable(iso: str) -> str:
    """The date as `dataAsOf` renders it: UTC calendar day, long month, en-CA."""
    when = datetime.fromisoformat(iso).astimezone(timezone.utc)
    return f"{when.strftime('%B')} {when.day}, {when.year}"


class FreshnessModuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not ts_harness.available() or not MANIFEST.is_file():
            raise unittest.SkipTest("toolchain or manifest unavailable")
        cls.result = ts_harness.run(
            """
import { generatedAt, maxIngestedAt, dataAsOf, ageInDays } from './freshness.mjs';
process.stdout.write(JSON.stringify({
  generatedAt: generatedAt(),
  maxIngestedAt: maxIngestedAt(),
  formatted: dataAsOf(),
  fixed: dataAsOf('2026-08-04T23:23:02+00:00'),
  invalid: dataAsOf('not a date'),
  missing: dataAsOf(null),
  ageFromFixed: ageInDays('2026-08-04T00:00:00+00:00', new Date('2026-08-30T00:00:00Z')),
  ageNegative: ageInDays('2027-01-01T00:00:00+00:00', new Date('2026-08-30T00:00:00Z')),
  ageInvalid: ageInDays('not a date') === Infinity,
  ageMissing: ageInDays(null) === Infinity,
}));
"""
        )
        cls.manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    def test_it_reads_the_manifest_the_suite_guards(self) -> None:
        """Same source of truth as tests/test_freshness.py, not a parallel one."""
        self.assertEqual(self.manifest["generated_at"], self.result["generatedAt"])
        self.assertEqual(self.manifest["max_ingested_at"], self.result["maxIngestedAt"])

    def test_the_displayed_date_is_when_data_arrived_not_when_it_was_written(
        self,
    ) -> None:
        """The whole point of the change: generated_at would say today."""
        expected = _readable(self.manifest["max_ingested_at"])
        self.assertEqual(expected, self.result["formatted"])

    def test_the_displayed_date_is_not_the_export_date(self) -> None:
        """A guard against a well-meaning revert to the fresher-looking field."""
        if self.manifest["max_ingested_at"][:10] == self.manifest["generated_at"][:10]:
            self.skipTest("ingest and export happen to fall on the same day")
        self.assertNotEqual(
            _readable(self.manifest["generated_at"]), self.result["formatted"]
        )

    def test_a_manifest_without_the_field_says_nothing_rather_than_lying(self) -> None:
        """Falling back to generated_at is the exact lie this change removed."""
        self.assertEqual("an unknown date", self.result["missing"])
        self.assertTrue(self.result["ageMissing"])

    def test_the_date_is_rendered_for_a_reader_not_a_machine(self) -> None:
        self.assertEqual("August 4, 2026", self.result["fixed"])

    def test_an_unreadable_stamp_does_not_render_as_a_confident_date(self) -> None:
        """Better to say nothing is known than to print "Invalid Date" or a fallback."""
        self.assertEqual("an unknown date", self.result["invalid"])

    def test_age_counts_whole_days(self) -> None:
        self.assertEqual(26, self.result["ageFromFixed"])

    def test_clock_skew_reads_as_zero_rather_than_negative(self) -> None:
        self.assertEqual(0, self.result["ageNegative"])

    def test_an_unreadable_stamp_reads_as_infinitely_old(self) -> None:
        """So a broken timestamp trips a staleness check instead of passing it."""
        self.assertTrue(self.result["ageInvalid"])


class DisplaySurfaceTests(unittest.TestCase):
    """The line is actually on the pages that promised it."""

    def test_the_demo_shows_it(self) -> None:
        source = (WEB / "components" / "DemoRanker.tsx").read_text(encoding="utf-8")
        self.assertIn("dataAsOf", source)
        self.assertIn("Data as of", source)

    def test_the_board_pages_show_it(self) -> None:
        source = (WEB / "app" / "board" / "[token]" / "page.tsx").read_text(
            encoding="utf-8"
        )
        self.assertIn("dataAsOf", source)
        self.assertIn("data as of", source)

    def test_both_read_the_manifest_rather_than_hardcoding_a_date(self) -> None:
        """A hardcoded date is the failure this feature exists to prevent."""
        for path in (
            WEB / "components" / "DemoRanker.tsx",
            WEB / "app" / "board" / "[token]" / "page.tsx",
        ):
            source = path.read_text(encoding="utf-8")
            self.assertIn('from "@/lib/freshness"', source, f"{path.name}")


if __name__ == "__main__":
    unittest.main()
