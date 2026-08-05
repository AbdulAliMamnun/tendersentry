"""Name lookup: detection, disambiguation, fallback, and full-strength ranking.

The detection ordering is the whole point. The obvious heuristic — "no trade keywords,
therefore a name" — is backwards for this market, because a Québec construction firm's
name usually *is* trade words. Under a keyword rule `Excavation Bergeron` reads as a
description, derives a slug, and returns a cold-start board while that firm's real
bidding record sits unused, invisibly. These tests assert the index-first ordering that
prevents it.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import config
from tests import ts_harness


ARTIFACT = Path(config.PROJECT_ROOT) / "web" / "data" / "model" / "firms.json"

CASES = [
    "Groupe Colas Québec Inc.",
    "GROUPE COLAS QUEBEC INC",
    "Eurovia Québec Construction Inc.",
    "Les Services EXP inc.",
    "CIMA+ S.E.N.C.",
    "Construction ABC ltée inc.",
    "  Excavation   Bergeron  ",
]


class NormalizationTests(unittest.TestCase):
    """Must match `model.dataset.normalize_name` exactly, or the index never hits."""

    @classmethod
    def setUpClass(cls) -> None:
        if not ts_harness.available() or not ARTIFACT.is_file():
            raise unittest.SkipTest("toolchain or firms.json unavailable")
        cls.results = ts_harness.run(
            """
import { normalizeName } from './firmLookup.mjs';
process.stdout.write(JSON.stringify(input.cases.map((c) => normalizeName(c))));
""",
            {"cases": CASES},
        )

    def test_matches_the_python_normalizer(self) -> None:
        """Two implementations of one fold; a drift here silently empties the index."""
        from model.dataset import normalize_name

        self.assertEqual([normalize_name(case) for case in CASES], self.results)

    def test_case_and_accents_collapse_to_one_key(self) -> None:
        self.assertEqual(self.results[0], self.results[1])

    def test_stacked_legal_forms_are_stripped_repeatedly(self) -> None:
        self.assertEqual("construction abc", self.results[5])


class DetectionTests(unittest.TestCase):
    """The index is the detector. Patterns are only a tiebreaker."""

    @classmethod
    def setUpClass(cls) -> None:
        if not ts_harness.available() or not ARTIFACT.is_file():
            raise unittest.SkipTest("toolchain or firms.json unavailable")
        payload = json.loads(ARTIFACT.read_text(encoding="utf-8"))
        by_norm = {firm["normalized"]: firm for firm in payload["firms"]}
        unique = [n for n, ids in payload["index"].items() if len(ids) == 1 and n in by_norm]
        shared = [n for n, ids in payload["index"].items() if len(ids) > 1]
        cls.known = max(unique, key=lambda n: by_norm[n]["bids"])
        cls.shared = shared[0] if shared else None
        cls.results = ts_harness.run(
            """
import { lookupFirm, couldBeFirmName } from './firmLookup.mjs';
const out = {};
for (const [key, text] of Object.entries(input.cases)) {
  const r = lookupFirm(text);
  out[key] = {
    kind: r.kind,
    confidence: r.confidence ?? null,
    name: r.profile?.name ?? null,
    bids: r.profile?.bids ?? null,
    candidates: r.candidates ? r.candidates.map((c) => ({name: c.name, bids: c.bids})) : null,
    couldBe: couldBeFirmName(text),
  };
}
process.stdout.write(JSON.stringify(out));
""",
            {
                "cases": {
                    "known": cls.known,
                    "shared": cls.shared or cls.known,
                    "description": "watermain and sanitary sewer replacement in Ontario, $200K jobs",
                    "gibberish": "purple monkey dishwasher",
                    "long_sentence": (
                        "we are a civil contractor working across the greater Toronto area "
                        "on watermain sewer and road reconstruction for municipalities"
                    ),
                },
            },
        )

    def test_a_known_firm_name_resolves_from_the_index(self) -> None:
        result = self.results["known"]
        self.assertEqual("match", result["kind"])
        self.assertEqual("exact", result["confidence"])
        self.assertGreaterEqual(result["bids"], 5)

    def test_a_description_is_not_mistaken_for_a_firm(self) -> None:
        self.assertEqual("none", self.results["description"]["kind"])

    def test_a_long_sentence_is_never_tried_as_a_name(self) -> None:
        self.assertFalse(self.results["long_sentence"]["couldBe"])
        self.assertEqual("none", self.results["long_sentence"]["kind"])

    def test_a_dollar_figure_marks_an_input_as_a_description(self) -> None:
        self.assertFalse(self.results["description"]["couldBe"])

    def test_gibberish_falls_through_rather_than_fuzzy_matching(self) -> None:
        self.assertEqual("none", self.results["gibberish"]["kind"])

    def test_a_shared_name_asks_instead_of_guessing(self) -> None:
        """448 of 14,802 names are shared. Choosing one would be a guess."""
        if not self.shared:
            self.skipTest("no shared names in this artifact")
        result = self.results["shared"]
        self.assertEqual("ambiguous", result["kind"])
        self.assertGreater(len(result["candidates"]), 1)

    def test_disambiguation_candidates_lead_with_the_most_active(self) -> None:
        if not self.shared:
            self.skipTest("no shared names in this artifact")
        bids = [candidate["bids"] for candidate in self.results["shared"]["candidates"]]
        self.assertEqual(sorted(bids, reverse=True), bids)


class RankingTests(unittest.TestCase):
    """A known-active Québec firm must produce a sensible board at full strength."""

    @classmethod
    def setUpClass(cls) -> None:
        if not ts_harness.available() or not ARTIFACT.is_file():
            raise unittest.SkipTest("toolchain or firms.json unavailable")
        payload = json.loads(ARTIFACT.read_text(encoding="utf-8"))
        by_id = {firm["id"]: firm for firm in payload["firms"]}
        unique = {n: ids[0] for n, ids in payload["index"].items() if len(ids) == 1}
        cls.name = max(unique, key=lambda n: by_id[unique[n]]["bids"])
        cls.results = ts_harness.run(
            """
import { lookupFirm, summarize } from './firmLookup.mjs';
import { rankForFirm } from './demoRank.mjs';
const found = lookupFirm(input.name);
const r = rankForFirm(found.profile, { today: input.today, limit: 10 });
process.stdout.write(JSON.stringify({
  firm: found.profile.name,
  bids: found.profile.bids,
  considered: r.considered,
  rows: r.results.map((x) => ({title: x.title, fit: x.fit, band: x.scaleBand})),
  firmFeatures: found.profile.features,
  summary: summarize(found.profile),
}));
""",
            {"name": cls.name, "today": "2026-08-04"},
        )

    def test_the_board_is_full_and_ordered(self) -> None:
        rows = self.results["rows"]
        self.assertEqual(10, len(rows))
        fits = [row["fit"] for row in rows]
        self.assertEqual(sorted(fits, reverse=True), fits)

    def test_the_model_runs_at_full_strength_not_cold_start(self) -> None:
        """The whole point of the name path: real history, not the zero vector."""
        features = self.results["firmFeatures"]
        self.assertGreater(features["firm_interactions"], 0)
        self.assertLess(features["firm_days_since_last"], 3650)
        self.assertGreater(features["firm_distinct_buyers"], 0)

    def test_the_summary_is_aggregate_only(self) -> None:
        """The basis line may describe the record; it may not enumerate it."""
        summary = self.results["summary"]
        self.assertLessEqual(len(summary["categories"]), 3)
        self.assertLessEqual(len(summary["regions"]), 2)
        self.assertNotIn("amounts", summary)
        self.assertNotIn("ocids", summary)

    def test_every_row_carries_its_scale_provenance(self) -> None:
        for row in self.results["rows"]:
            self.assertTrue(row["band"])


if __name__ == "__main__":
    unittest.main()
