"""Is the data the site is serving actually current?

Every other test in this suite asks whether the code is correct. This one asks
whether the *data* is, which is the failure nobody notices: the pipeline keeps
working perfectly on a pool that stopped being true weeks ago. A ranked board of
notices that all closed last month is not a degraded product, it is a wrong one, and
until now nothing in the repo would have said so.

Two independent conditions, because they fail for different reasons and a single
check would miss one of them:

* **Age** — the pool has not been re-exported recently. Something is wrong with the
  refresh: the schedule is disabled, the job is failing, credentials expired.
* **Closed share** — the pool is being re-exported but the notices in it have aged
  out. The refresh is running and still not delivering usable data.

The first is about the machinery, the second about the result. A run can pass the
first and fail the second.

**These thresholds are not tuned to pass.** At the time of writing both fire — the
pool was exported 2026-08-04 and 1,595 of its 2,003 notices have closed. That is the
intended state: the test is red until the daily refresh runs for the first time, and
lowering a threshold to get a green suite would delete the only signal that the data
is stale.
"""

from __future__ import annotations

import json
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

import config


MODEL_DIR = Path(config.PROJECT_ROOT) / "web" / "data" / "model"
POOL_PATH = MODEL_DIR / "pool.json"
MANIFEST_PATH = MODEL_DIR / "manifest.json"

#: How old an export may be before it counts as stale.
#:
#: The refresh runs daily, so seven days is six consecutive missed runs — comfortably
#: past a transient failure and well short of the 60 days after which GitHub disables
#: an inactive schedule. It is a "nobody is watching" alarm, not a "yesterday's run
#: was slow" one.
MAX_EXPORT_AGE_DAYS = 7

#: How much of the pool may have closed before the board stops being useful.
#:
#: Above half, a ten-row board is mostly notices nobody can bid on. The demo already
#: filters closed notices at request time, so this does not mean the site shows dead
#: rows — it means the pool it filters from has thinned to the point where an
#: honest board is a short one.
MAX_CLOSED_SHARE = 0.50


def _load(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


class ExportAgeTests(unittest.TestCase):
    """Condition (a): is the refresh machinery running at all?"""

    def test_the_exported_pool_was_refreshed_recently(self) -> None:
        self.assertTrue(POOL_PATH.is_file(), f"no exported pool at {POOL_PATH}")
        pool = _load(POOL_PATH)

        stamp = pool.get("generated_at")
        self.assertIsNotNone(stamp, "pool.json carries no generated_at")
        exported = datetime.fromisoformat(str(stamp))
        if exported.tzinfo is None:
            exported = exported.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - exported).total_seconds() / 86400.0

        self.assertLessEqual(
            age_days,
            MAX_EXPORT_AGE_DAYS,
            f"\nThe exported pool is {age_days:.1f} days old "
            f"(limit {MAX_EXPORT_AGE_DAYS}).\n"
            f"  exported: {stamp}\n"
            f"  now:      {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n"
            "The daily refresh has not run successfully in that window. Check the "
            "Actions run history and whether the schedule is still enabled.\n"
            "Do not raise this threshold to make the suite green.",
        )

    def test_the_manifest_agrees_that_it_is_recent(self) -> None:
        """The manifest is written on every run, including one that changes nothing."""
        self.assertTrue(MANIFEST_PATH.is_file(), f"no manifest at {MANIFEST_PATH}")
        manifest = _load(MANIFEST_PATH)

        stamp = manifest.get("generated_at")
        self.assertIsNotNone(stamp, "manifest.json carries no generated_at")
        exported = datetime.fromisoformat(str(stamp))
        if exported.tzinfo is None:
            exported = exported.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - exported).total_seconds() / 86400.0

        self.assertLessEqual(
            age_days,
            MAX_EXPORT_AGE_DAYS,
            f"\nThe serving manifest is {age_days:.1f} days old "
            f"(limit {MAX_EXPORT_AGE_DAYS}).\n"
            f"  generated_at: {stamp}\n"
            "Do not raise this threshold to make the suite green.",
        )


class PoolUsabilityTests(unittest.TestCase):
    """Condition (b): is what was exported still worth ranking?"""

    @classmethod
    def setUpClass(cls) -> None:
        if not POOL_PATH.is_file():
            raise unittest.SkipTest(f"no exported pool at {POOL_PATH}")
        cls.pool = _load(POOL_PATH)
        today = date.today().isoformat()
        cls.tenders = cls.pool.get("tenders") or []
        cls.total = len(cls.tenders)
        cls.closed = sum(
            1
            for tender in cls.tenders
            # A notice with no closing date cannot be shown to have passed, so it is
            # not counted against the pool. Same direction of caution the ranker uses.
            if (tender.get("closing_date") or "9999-12-31") < today
        )

    def test_the_pool_is_not_mostly_closed(self) -> None:
        self.assertGreater(self.total, 0, "the exported pool is empty")
        share = self.closed / self.total

        self.assertLessEqual(
            share,
            MAX_CLOSED_SHARE,
            f"\n{self.closed} of {self.total} notices in the exported pool have "
            f"closed ({100 * share:.1f}%, limit {100 * MAX_CLOSED_SHARE:.0f}%).\n"
            f"  open today: {self.total - self.closed}\n"
            f"  exported:   {self.pool.get('generated_at')}\n"
            "The refresh may be running while still not delivering usable data. "
            "Check whether ingestion is returning new notices.\n"
            "Do not raise this threshold to make the suite green.",
        )

    def test_something_in_the_pool_is_still_open(self) -> None:
        """The floor case: a board with nothing to rank."""
        self.assertGreater(
            self.total - self.closed,
            0,
            f"\nEvery one of the {self.total} exported notices has closed. "
            "The demo filters closed notices at request time, so the live board is "
            "empty right now.",
        )


class ThresholdIntegrityTests(unittest.TestCase):
    """Guard the guard.

    The cheapest way to make this module pass is to weaken it, which would be
    indistinguishable from fixing the problem in a green CI log. These assertions
    make that edit visible as a deliberate change to a stated contract.
    """

    def test_the_age_threshold_has_not_been_loosened(self) -> None:
        self.assertLessEqual(
            MAX_EXPORT_AGE_DAYS,
            7,
            "MAX_EXPORT_AGE_DAYS was raised above 7. The refresh runs daily; a longer "
            "window means a broken schedule goes unnoticed for over a week.",
        )

    def test_the_closed_share_threshold_has_not_been_loosened(self) -> None:
        self.assertLessEqual(
            MAX_CLOSED_SHARE,
            0.50,
            "MAX_CLOSED_SHARE was raised above 0.50. Past half, the pool is mostly "
            "notices nobody can bid on.",
        )


if __name__ == "__main__":
    unittest.main()
