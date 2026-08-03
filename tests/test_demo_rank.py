"""The live demo's serving path: derivation, limiting, and ranking behaviour.

These run the *shipped* TypeScript under Node rather than a Python re-implementation,
because a second implementation that agreed with itself would prove nothing. See
`tests/ts_harness.py` for how the modules are staged.

`tests/test_service_parity.py` covers the other half — that the TS tree walker and
LightGBM produce the same numbers. Together they mean a ranking shown on the site is
the ranking the model produces.
"""

from __future__ import annotations

import unittest

from tests import ts_harness


#: Pinned so the tests do not change meaning as notices close.
TODAY = "2026-08-02"


class DeriveTests(unittest.TestCase):
    """The description parser. This is the demo's entire understanding step."""

    @classmethod
    def setUpClass(cls) -> None:
        if not ts_harness.available():
            raise unittest.SkipTest("Node or sucrase unavailable")
        cls.results = ts_harness.run(
            """
import { derive, parseValue } from './derive.mjs';
const out = {};
for (const [key, text] of Object.entries(input.cases)) out[key] = derive(text);
out.__values = input.values.map((v) => parseValue(v));
process.stdout.write(JSON.stringify(out));
""",
            {
                "cases": {
                    "watermain": "We replace watermain and sanitary sewer in Ontario",
                    "french": "Entrepreneur en pavage et travaux routiers au Québec",
                    "gibberish": "purple monkey dishwasher",
                    "accented": "Réfection d'égout et d'aqueduc à Montréal",
                    "preposition": "We take on projects across Canada",
                    "postal": "Paving contractor, ON and QC",
                    "multi": "Rooftop HVAC units and electrical retrofits",
                },
                "values": [
                    "$2M jobs",
                    "500 000 $",
                    "around $250K",
                    "contrats de 1,5 M$",
                    "no figure here",
                ],
            },
        )

    def test_english_trade_terms_map_to_their_slug(self) -> None:
        self.assertIn("water_wastewater", self.results["watermain"]["slugs"])
        self.assertTrue(self.results["watermain"]["hit"])

    def test_french_trade_terms_map_to_the_same_slugs_as_english(self) -> None:
        self.assertIn("roadwork", self.results["french"]["slugs"])
        self.assertEqual(["QC"], self.results["french"]["regions"])

    def test_accents_do_not_prevent_a_match(self) -> None:
        # "égout"/"aqueduc" must reach water_wastewater despite the diacritics.
        self.assertIn("water_wastewater", self.results["accented"]["slugs"])
        self.assertIn("QC", self.results["accented"]["regions"])

    def test_an_unrecognised_description_is_recorded_as_a_miss(self) -> None:
        self.assertEqual([], self.results["gibberish"]["slugs"])
        self.assertFalse(self.results["gibberish"]["hit"])

    def test_the_english_preposition_on_is_not_read_as_ontario(self) -> None:
        # "take on projects" must not put the firm in Ontario.
        self.assertEqual([], self.results["preposition"]["regions"])

    def test_uppercase_postal_codes_are_read_as_provinces(self) -> None:
        self.assertEqual(["QC", "ON"], sorted(self.results["postal"]["regions"])[::-1])

    def test_several_trades_in_one_description_all_surface(self) -> None:
        slugs = self.results["multi"]["slugs"]
        self.assertIn("mechanical_hvac", slugs)
        self.assertIn("electrical", slugs)

    def test_dollar_figures_resolve_across_formats(self) -> None:
        two_m, five_hundred_k, quarter_m, one_point_five_m, absent = self.results[
            "__values"
        ]
        self.assertEqual(2_000_000, two_m)
        self.assertEqual(500_000, five_hundred_k)
        self.assertEqual(250_000, quarter_m)
        self.assertEqual(1_500_000, one_point_five_m)
        self.assertIsNone(absent)


class RankingTests(unittest.TestCase):
    """End-to-end: a description in, an ordered slice of the open pool out."""

    @classmethod
    def setUpClass(cls) -> None:
        if not ts_harness.available():
            raise unittest.SkipTest("Node or sucrase unavailable")
        cls.results = ts_harness.run(
            """
import { rank, isThin, valueModifier } from './demoRank.mjs';
const out = {};
for (const [key, text] of Object.entries(input.cases)) {
  const r = rank(text, { today: input.today, limit: 10 });
  out[key] = {
    reading: r.reading, hit: r.derived.hit, slugs: r.derived.slugs,
    considered: r.considered, onTrade: r.onTrade, thin: isThin(r),
    results: r.results,
  };
}
out.__modifier = {
  match: valueModifier(1_000_000, 1_000_000),
  mismatch: valueModifier(50_000_000, 100_000),
  unknownTender: valueModifier(null, 1_000_000),
  unknownFirm: valueModifier(1_000_000, null),
};
// Past every closing date in the pool — standing offers run to 2100.
out.__closed = rank(input.cases.water, { today: '2101-01-01', limit: 10 }).considered;
process.stdout.write(JSON.stringify(out));
""",
            {
                "today": TODAY,
                "cases": {
                    "water": "watermain and sanitary sewer replacement, aqueduc et égout",
                    "road": "asphalt paving and road resurfacing, pavage",
                    "furniture": "we supply office furniture and desks",
                    "gibberish": "purple monkey dishwasher",
                    "quebec": "pavage et travaux routiers au Québec",
                },
            },
        )

    def _titles(self, key: str) -> list[str]:
        return [row["title"].lower() for row in self.results[key]["results"]]

    def test_a_watermain_firm_gets_water_work_not_office_furniture(self) -> None:
        """The headline claim of the demo, asserted directly."""
        titles = self._titles("water")
        self.assertTrue(titles, "watermain description returned no results")

        watery = sum(
            1
            for title in titles
            if any(
                term in title
                for term in ("eau", "égout", "water", "aqueduc", "épuration", "sewer")
            )
        )
        self.assertGreaterEqual(
            watery,
            5,
            f"only {watery}/10 top results are water work: {titles}",
        )
        self.assertFalse(
            any("furniture" in title or "mobilier" in title for title in titles),
            f"office furniture surfaced for a watermain firm: {titles}",
        )

    def test_a_paving_firm_and_a_watermain_firm_get_different_boards(self) -> None:
        # If the description did not drive the ranking, these would be identical.
        overlap = set(self._titles("water")) & set(self._titles("road"))
        self.assertLessEqual(
            len(overlap),
            2,
            f"paving and watermain boards share {len(overlap)}/10 rows",
        )

    def test_a_paving_description_ranks_paving_notices_first(self) -> None:
        top = self._titles("road")[:3]
        self.assertTrue(
            any(
                term in title
                for title in top
                for term in ("pavage", "chaussée", "voirie", "resurfaçage", "paving")
            ),
            f"no paving notice in the top three: {top}",
        )

    def test_an_unrecognised_description_returns_nothing_rather_than_noise(self) -> None:
        """A list built from a zero firm vector would be the same for any gibberish."""
        self.assertFalse(self.results["gibberish"]["hit"])
        self.assertEqual([], self.results["gibberish"]["results"])

    def test_naming_a_province_narrows_the_pool(self) -> None:
        self.assertLess(
            self.results["quebec"]["considered"],
            self.results["road"]["considered"],
            "declaring Québec did not filter anything out",
        )

    def test_closed_notices_are_never_ranked(self) -> None:
        self.assertEqual(
            0,
            self.results["__closed"],
            "notices past their closing date survived the filter",
        )

    def test_the_value_modifier_is_bounded_and_never_penalises_absence(self) -> None:
        modifier = self.results["__modifier"]
        self.assertAlmostEqual(10.0, modifier["match"], places=6)
        self.assertAlmostEqual(-10.0, modifier["mismatch"], places=6)
        # An unknown on either side must not move the ranking in either direction.
        self.assertEqual(0, modifier["unknownTender"])
        self.assertEqual(0, modifier["unknownFirm"])

    def test_every_row_carries_what_the_card_renders(self) -> None:
        for row in self.results["water"]["results"]:
            self.assertTrue(row["title"])
            self.assertTrue(row["closingDate"] >= TODAY)
            self.assertGreaterEqual(row["fit"], 0)
            self.assertLessEqual(row["fit"], 100)


class RateLimitTests(unittest.TestCase):
    """The limiter, driven by an injected clock so the suite never sleeps."""

    @classmethod
    def setUpClass(cls) -> None:
        if not ts_harness.available():
            raise unittest.SkipTest("Node or sucrase unavailable")
        cls.results = ts_harness.run(
            """
import { RateLimiter, PER_IP_RULES, GLOBAL_RULE } from './rateLimit.mjs';
let clock = 1_000_000;
const limiter = new RateLimiter(() => clock);
const out = {};

// Burst to the per-minute ceiling.
const minute = PER_IP_RULES[0].max;
out.allowedInBurst = 0;
for (let i = 0; i < minute + 3; i += 1) {
  if (limiter.check('1.1.1.1').allowed) out.allowedInBurst += 1;
}
out.blocked = limiter.check('1.1.1.1');

// A different address is unaffected by the first one's burst.
out.otherIp = limiter.check('2.2.2.2').allowed;

// Once the window rolls past, the first address is served again.
clock += 61_000;
out.afterWindow = limiter.check('1.1.1.1').allowed;

// The hourly ceiling must bind even when no single minute is ever busy. Pacing at
// 50s puts at most two requests in any minute-window, well under that rule, but 72
// attempts inside one rolling hour — more than the hourly ceiling allows.
const hourly = new RateLimiter(() => clock);
const start = clock;
let hourlyAllowed = 0;
let hourlyAttempts = 0;
while (clock - start < PER_IP_RULES[1].windowMs) {
  hourlyAttempts += 1;
  if (hourly.check('4.4.4.4').allowed) hourlyAllowed += 1;
  clock += 50_000;
}
out.hourlyAllowed = hourlyAllowed;
out.hourlyAttempts = hourlyAttempts;
out.hourlyMax = PER_IP_RULES[1].max;

// A rejected request must not be recorded, or the block would extend itself.
const fresh = new RateLimiter(() => clock);
for (let i = 0; i < 50; i += 1) fresh.check('3.3.3.3');
out.recordedForRejected = fresh.countFor('ip:3.3.3.3', 60_000);
out.globalMax = GLOBAL_RULE.max;
process.stdout.write(JSON.stringify(out));
""",
        )

    def test_a_burst_is_cut_off_at_the_per_minute_ceiling(self) -> None:
        self.assertEqual(10, self.results["allowedInBurst"])

    def test_a_blocked_request_is_told_when_to_come_back(self) -> None:
        blocked = self.results["blocked"]
        self.assertFalse(blocked["allowed"])
        self.assertEqual("ip", blocked["scope"])
        self.assertGreater(blocked["retryAfterSeconds"], 0)
        self.assertLessEqual(blocked["retryAfterSeconds"], 60)

    def test_one_address_hitting_the_limit_does_not_block_another(self) -> None:
        self.assertTrue(self.results["otherIp"])

    def test_the_window_slides_rather_than_locking_out(self) -> None:
        self.assertTrue(self.results["afterWindow"])

    def test_the_hourly_ceiling_binds_across_minute_windows(self) -> None:
        """A caller pacing itself under the per-minute rule still hits the hourly one."""
        self.assertGreater(self.results["hourlyAttempts"], self.results["hourlyMax"])
        self.assertEqual(self.results["hourlyMax"], self.results["hourlyAllowed"])

    def test_a_rejected_request_is_not_counted_against_the_window(self) -> None:
        """Otherwise a caller in a loop could never climb out of its own block."""
        self.assertEqual(10, self.results["recordedForRejected"])


if __name__ == "__main__":
    unittest.main()
