"""One region rule, two implementations, asserted to agree.

`regionAllows` decides which notices a region filter shows, and it exists twice: in
TypeScript because the ranking runs in the browser-facing path, and in Python because
the manifest's rankable counts are written by the exporter. Nothing but this module
keeps them in step, and a drift would be quiet in the worst way — the manifest would
publish a per-region count, the freshness test would assert a floor against it, and
the ranker would serve a different number to the visitor. Every layer would look
healthy and the one that matters would be wrong.

Same defect class and same remedy as the `normalizeName` parity test in
tests/test_firm_lookup.py: run the real shipped function through Node and compare.

The two rules that carry the semantics — and that a naive reimplementation gets wrong
— are the ones with the most cases below. A null region matches every filter, and a
`CA` code marks a nationally-open notice rather than a region a firm has to match. Get
either backwards and Ontario's rankable count moves by a factor of four.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import config
from scripts import export_model_service as export_service
from tests import ts_harness


MANIFEST = Path(config.PROJECT_ROOT) / "web" / "data" / "model" / "manifest.json"
POOL = Path(config.PROJECT_ROOT) / "web" / "data" / "model" / "pool.json"

#: (tender region, requested regions). Ordinary cases, both special rules, the empty
#: filter, whitespace as it appears in real SEAO rows, and codes that share a prefix.
CASES: list[tuple[str | None, list[str]]] = [
    ("ON", ["ON"]),
    ("ON", ["QC"]),
    ("QC", ["QC"]),
    ("ON,QC", ["ON"]),
    ("ON,QC", ["QC"]),
    ("ON, QC", ["QC"]),
    (" ON , QC ", ["ON"]),
    ("AB,BC,MB,NB,NL,NS,NT,NU,ON,PE,QC,SK,YT", ["ON"]),
    ("NB,NL,NS,PE", ["ON"]),
    # "CA" is national: it matches whatever was asked for.
    ("CA", ["ON"]),
    ("CA", ["QC"]),
    ("CA,QC", ["ON"]),
    ("ON,CA", ["QC"]),
    # A null region also matches, rather than being excluded for lack of information.
    (None, ["ON"]),
    (None, ["QC"]),
    (None, []),
    # An empty filter is "all regions", so everything passes.
    ("ON", []),
    ("QC", []),
    ("CA", []),
    # Neither side matches.
    ("AB", ["ON"]),
    ("BC,NS", ["ON", "QC"]),
    ("", ["ON"]),
    # Both requested at once, which is what the "all" selector does not do but the
    # underlying function must still answer correctly.
    ("ON", ["ON", "QC"]),
    ("AB", ["ON", "QC"]),
]


class RegionParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not ts_harness.available() or not POOL.is_file():
            raise unittest.SkipTest("toolchain or pool.json unavailable")
        cls.typescript = ts_harness.run(
            """
import { regionAllows } from './demoRank.mjs';
process.stdout.write(JSON.stringify(
  input.cases.map(([region, regions]) => regionAllows(region, regions))
));
""",
            {"cases": [[region, regions] for region, regions in CASES]},
        )

    def test_python_matches_the_shipped_typescript(self) -> None:
        python = [
            export_service.region_allows(region, regions) for region, regions in CASES
        ]
        for (region, regions), ts, py in zip(CASES, self.typescript, python):
            self.assertEqual(
                ts,
                py,
                f"disagreement on region={region!r} filter={regions!r}: "
                f"TypeScript says {ts}, Python says {py}",
            )

    def test_a_national_notice_matches_every_filter(self) -> None:
        """The rule worth stating twice: CA is not a province to match against."""
        self.assertTrue(export_service.region_allows("CA", ["ON"]))
        self.assertTrue(export_service.region_allows("CA", ["QC"]))
        self.assertTrue(export_service.region_allows("CA,QC", ["ON"]))

    def test_an_unknown_region_is_included_rather_than_dropped(self) -> None:
        self.assertTrue(export_service.region_allows(None, ["ON"]))
        self.assertTrue(export_service.region_allows("", ["ON"]))


class RankableCountTests(unittest.TestCase):
    """The manifest's counts must describe the pool it shipped beside."""

    @classmethod
    def setUpClass(cls) -> None:
        if not (MANIFEST.is_file() and POOL.is_file()):
            raise unittest.SkipTest("serving artifacts unavailable")
        cls.manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        cls.pool = json.loads(POOL.read_text(encoding="utf-8"))

    def test_the_manifest_carries_the_rankable_block(self) -> None:
        rankable = self.manifest.get("rankable")
        self.assertIsNotNone(rankable, "manifest carries no rankable block")
        self.assertIn("count", rankable)
        self.assertIn("as_of", rankable)
        self.assertIn("by_region", rankable)

    def test_the_counts_reproduce_from_the_shipped_pool(self) -> None:
        """Recomputed, not trusted: a stale block would misreport the floors."""
        recomputed = export_service.rankable_counts(
            self.pool["tenders"], self.manifest["rankable"]["as_of"]
        )
        self.assertEqual(self.manifest["rankable"]["count"], recomputed["count"])
        self.assertEqual(
            self.manifest["rankable"]["by_region"], recomputed["by_region"]
        )

    def test_rankable_never_exceeds_the_pool(self) -> None:
        self.assertLessEqual(self.manifest["rankable"]["count"], self.pool["count"])

    def test_every_tracked_region_is_counted(self) -> None:
        self.assertEqual(
            set(export_service.TRACKED_REGIONS),
            set(self.manifest["rankable"]["by_region"]),
        )

    def test_the_manifest_records_when_data_last_arrived(self) -> None:
        self.assertIn("max_ingested_at", self.manifest)
        self.assertIsNotNone(self.manifest["max_ingested_at"])


if __name__ == "__main__":
    unittest.main()
