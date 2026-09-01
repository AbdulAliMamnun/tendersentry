"""Is the data the site is serving actually current?

Every other test in this suite asks whether the code is correct. This one asks
whether the *data* is, which is the failure nobody notices: the pipeline keeps
working perfectly on a pool that stopped being true weeks ago. A ranked board of
notices that all closed last month is not a degraded product, it is a wrong one, and
until now nothing in the repo would have said so.

Five conditions. Each catches something none of the others can, which is the only
reason to have five rather than one:

* **(a) export age** — the export stopped running. The schedule is disabled, the job
  is failing, credentials expired. *Machinery.*
* **(b) closed share** — the pool is being re-exported but its notices have aged out.
  *Result.*
* **(c) ingest age** — **ingestion stopped while the export kept running.** This is
  the failure the daily refresh exists to prevent, and until `max_ingested_at` was
  carried into the manifest it was undetectable: an export that ingests nothing still
  writes a perfectly fresh `generated_at`, so (a) stays green, and (b) only notices
  weeks later once enough notices have expired. *Intake.*
* **(d) rankable floor** — the pool has collapsed in absolute terms. Distinct from
  (b): a pool of 300 notices that are all open passes the closed-share check and is
  still too thin to rank against. *Volume.*
* **(e) per-region floor** — one *source* stopped while the other carried the total.
  If CanadaBuys broke and SEAO kept working, the total sails past (d) while an
  Ontario contractor loses every nationally-open notice. Only a per-region count
  sees it. *Source coverage.*

(a) and (c) look similar and are not. (a) is about the last write; (c) is about the
last arrival. The gap between them is precisely the failure that motivated this
module.

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

#: How stale intake may be before it counts as stopped.
#:
#: Same seven days as the export age, for the same reason, but measuring a different
#: thing. Note what `ingested_at` is: the moment a notice was first INSERTED, never
#: touched on update. So this fires if seven days pass with no *new* notice from
#: either source — across CanadaBuys and SEAO combined, which publish continuously.
#: A day of pure updates does not advance it, which is why the window is a week and
#: not a day.
MAX_INGEST_AGE_DAYS = 7

#: Rankable notices — open, not yet closed — below which the pool has collapsed.
#:
#: Derived from the pool's own history, reconstructed from the database: over the
#: reliable window the rankable pool ran 1,835–2,208. 750 sits 2.4x below the lowest
#: healthy observation and 1.8x above today's broken 408, so it separates a genuine
#: collapse from ordinary week-to-week variation without sitting near either.
MIN_RANKABLE = 750

#: Per-region floors, and they are not the same kind of number as each other.
#:
#: Counted the way the ranker counts (a null region or a `CA` code matches every
#: filter), the two regions behaved very differently over the same history:
#:
#:   QC-visible   1,609 – 2,119 healthy, 295 today   -> floor 600, 2.7x headroom
#:   ON-visible     152 –   436 healthy, 207 today   -> floor 120, see below
#:
#: **The Ontario floor is the soft one and should be read as provisional.** It is
#: derived from five sampled historical dates, and its margin is 21% below the
#: observed minimum of 152 — against 2.4x and 2.7x for the global and Québec floors.
#: It is deliberately set BELOW today's 207, because Ontario is not what is broken
#: today: any floor above 207 would also have failed on healthy days (2026-04-10 sat
#: at 152), making this module permanently red and training everyone to ignore it.
#: What it does catch is a CanadaBuys outage, which would drop Ontario near 45.
#:
#: Revisit it once the cron has produced a month of real daily variance. Five sampled
#: points are enough to rule out the obviously wrong values and not enough to know the
#: true floor.
MIN_RANKABLE_BY_REGION = {"ON": 120, "QC": 600}


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


class IngestAgeTests(unittest.TestCase):
    """Condition (c): is anything still arriving?

    The one an export cannot detect about itself. A refresh that runs perfectly and
    ingests nothing satisfies every other check here for weeks.
    """

    def test_new_notices_are_still_arriving(self) -> None:
        self.assertTrue(MANIFEST_PATH.is_file(), f"no manifest at {MANIFEST_PATH}")
        manifest = _load(MANIFEST_PATH)

        stamp = manifest.get("max_ingested_at")
        self.assertIsNotNone(
            stamp,
            "manifest carries no max_ingested_at; re-export with "
            "scripts.export_model_service so intake staleness is detectable",
        )
        arrived = datetime.fromisoformat(str(stamp))
        if arrived.tzinfo is None:
            arrived = arrived.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - arrived).total_seconds() / 86400.0

        self.assertLessEqual(
            age_days,
            MAX_INGEST_AGE_DAYS,
            f"\nNo new notice has arrived in {age_days:.1f} days "
            f"(limit {MAX_INGEST_AGE_DAYS}).\n"
            f"  newest ingest: {stamp}\n"
            f"  last export:   {manifest.get('generated_at')}\n"
            "The export may be running while ingestion is not — the failure the "
            "daily refresh exists to prevent. Check the ingest step of the most "
            "recent Actions run: it can exit 0 having fetched nothing.\n"
            "Do not raise this threshold to make the suite green.",
        )


class RankableVolumeTests(unittest.TestCase):
    """Conditions (d) and (e): is there enough left to rank against?"""

    @classmethod
    def setUpClass(cls) -> None:
        if not MANIFEST_PATH.is_file():
            raise unittest.SkipTest(f"no manifest at {MANIFEST_PATH}")
        cls.manifest = _load(MANIFEST_PATH)
        cls.rankable = cls.manifest.get("rankable")

    def test_the_manifest_reports_what_is_rankable(self) -> None:
        self.assertIsNotNone(
            self.rankable,
            "manifest carries no rankable block; the pool count alone describes "
            "notices nobody is served",
        )

    def test_enough_notices_are_still_rankable(self) -> None:
        self.assertIsNotNone(self.rankable, "manifest carries no rankable block")
        count = self.rankable["count"]

        self.assertGreaterEqual(
            count,
            MIN_RANKABLE,
            f"\nOnly {count} notices are rankable (floor {MIN_RANKABLE}).\n"
            f"  measured as of: {self.rankable['as_of']}\n"
            f"  pool size:      {self.manifest['pool']['count']}\n"
            f"  by region:      {self.rankable['by_region']}\n"
            "Healthy has been 1,835-2,208. This is what a visitor is ranked "
            "against, not what the pool contains.\n"
            "Do not lower this floor to make the suite green.",
        )

    def test_each_region_has_enough_to_rank(self) -> None:
        """Catches a single-source outage the global floor would mask."""
        self.assertIsNotNone(self.rankable, "manifest carries no rankable block")
        by_region = self.rankable["by_region"]

        failures = []
        for region, floor in sorted(MIN_RANKABLE_BY_REGION.items()):
            actual = by_region.get(region)
            if actual is None:
                failures.append(f"  {region}: not counted in the manifest")
            elif actual < floor:
                failures.append(f"  {region}: {actual} rankable, floor {floor}")

        self.assertFalse(
            failures,
            "\nA region has too little to rank against:\n"
            + "\n".join(failures)
            + f"\n  all regions: {self.rankable['count']}"
            + f"\n  measured as of: {self.rankable['as_of']}\n"
            "Counted the way the ranker counts, where a null region or a CA code "
            "matches every filter. One region collapsing while the total holds up "
            "means a source stopped, not that the market did.\n"
            "Do not lower these floors to make the suite green.",
        )


class HeroPremiseTests(unittest.TestCase):
    """The homepage's opening sentence rests on a fact, and the fact can move.

    It says thousands of tenders are open and that finding the few that fit "takes
    hours you don't have". That premise assumes there are too many to sift by hand.
    `spelledThousands()` throws below a thousand rather than switching to a numeral,
    because a page that reformats itself to stay grammatical while its claim stops
    being true is worse than one that breaks. This catches it earlier, and with the
    number.
    """

    def test_the_pool_still_supports_the_hero_sentence(self) -> None:
        if not MANIFEST_PATH.is_file():
            self.skipTest(f"no manifest at {MANIFEST_PATH}")
        rankable = (_load(MANIFEST_PATH).get("rankable") or {}).get("count")
        self.assertIsNotNone(rankable, "manifest carries no rankable count")
        self.assertGreaterEqual(
            rankable,
            1000,
            f"\nOnly {rankable} rankable notices. The homepage opens with "
            f"'{rankable // 1000} thousand tenders are open' and argues that sifting "
            "them by hand costs hours. Below a thousand that argument is gone.\n"
            "spelledThousands() will throw and fail the build. Rewrite the sentence "
            "rather than reformatting the number.",
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

    def test_the_ingest_age_threshold_has_not_been_loosened(self) -> None:
        self.assertLessEqual(
            MAX_INGEST_AGE_DAYS,
            7,
            "MAX_INGEST_AGE_DAYS was raised above 7. Both sources publish "
            "continuously; a longer window lets intake stop unnoticed for over a week.",
        )

    def test_the_rankable_floor_has_not_been_lowered(self) -> None:
        self.assertGreaterEqual(
            MIN_RANKABLE,
            750,
            "MIN_RANKABLE was lowered below 750. Healthy has been 1,835-2,208; "
            "below 750 the floor stops separating a collapse from normal variation.",
        )

    def test_the_regional_floors_have_not_been_lowered(self) -> None:
        # ON is knowingly the soft one — provisional, 21% below its observed minimum,
        # and due a revisit once the cron has produced a month of real variance. That
        # is a reason to re-derive it from better data, not to quietly lower it.
        self.assertGreaterEqual(MIN_RANKABLE_BY_REGION["ON"], 120)
        self.assertGreaterEqual(MIN_RANKABLE_BY_REGION["QC"], 600)


if __name__ == "__main__":
    unittest.main()
