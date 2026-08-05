"""Firm profiles: quantization fidelity, as-of discipline, and what may not ship.

The load-bearing test here is quantization. Shipping 11,227 firm centroids as float32
costs 16.5 MB before encoding; int8 costs 4.1 MB. That trade is only acceptable if the
boards it produces are the boards float32 would have produced, and quantization damage
is exactly the kind of thing that never surfaces in any other test — every ranking
still looks plausible.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import numpy as np

import config
from model import profiles


ARTIFACT = Path(config.PROJECT_ROOT) / "web" / "data" / "model" / "firms.json"

#: Quantization is accepted on *material* reordering, not on raw rank shift. Two
#: tenders whose true similarity differs by less than the quantization error are tied;
#: swapping them is arithmetic, not damage. See `profiles.quantization_divergence`.
MAX_COSINE_ERROR = 0.005


def _profile_from_record(record: dict) -> profiles.FirmProfile:
    import base64

    payload = base64.b64decode(record["centroid"])
    centroid = profiles.dequantize(payload, record["centroid_scale"], len(payload))
    return profiles.FirmProfile(
        canonical_id=record["id"],
        display_name=record["name"],
        normalized_name=record["normalized"],
        bids=record["bids"],
        wins=record["wins"],
        first_date=record["first"],
        last_date=record["last"],
        centroid=centroid,
    )


class QuantizationTests(unittest.TestCase):
    """int8 centroids must rank the way float32 centroids do."""

    def test_round_trip_preserves_direction(self) -> None:
        rng = np.random.default_rng(20260804)
        for _ in range(50):
            vector = rng.normal(size=384).astype(np.float32)
            payload, scale = profiles.quantize(vector)
            restored = profiles.dequantize(payload, scale, 384)
            # Cosine is the only property the ranking uses, so it is the one asserted.
            self.assertGreater(profiles.cosine(vector, restored), 0.9999)

    def test_quantization_is_384_bytes_regardless_of_magnitude(self) -> None:
        for magnitude in (1e-4, 1.0, 1e4):
            payload, _ = profiles.quantize(np.full(384, magnitude, dtype=np.float32))
            self.assertEqual(384, len(payload))

    def test_a_zero_vector_does_not_divide_by_zero(self) -> None:
        payload, scale = profiles.quantize(np.zeros(384, dtype=np.float32))
        self.assertEqual(384, len(payload))
        self.assertGreater(scale, 0)

    def test_ranking_divergence_against_float32_is_bounded(self) -> None:
        """The measurement the artifact size was approved on.

        Centroids are **rebuilt in float32 here rather than read from the artifact.**
        The shipped centroids have already been through int8, so quantizing them again
        is idempotent and reports a divergence of exactly zero — a number that looks
        like a perfect result and measures nothing. A centroid is the mean of the
        embeddings of the tenders a firm bid on, so averaging real pool embeddings
        produces vectors from the right distribution to test against.
        """
        pool_path = ARTIFACT.parent / "pool.json"
        if not pool_path.is_file():
            self.skipTest("no pool.json; run scripts.export_model_service")

        import base64

        pool = json.loads(pool_path.read_text(encoding="utf-8"))
        raw = base64.b64decode(pool["embeddings_base64"])
        vectors = np.frombuffer(raw, dtype=np.float32).reshape(-1, pool["embedding_dim"])

        # Centroids are built from tenders that share a trade, because that is what a
        # real firm centroid is. A mean over *random* tenders collapses toward the
        # global mean, leaving every cosine near-tied — under which any perturbation
        # reorders the list and the test measures tie-breaking noise rather than
        # quantization.
        by_slug: dict[str, list[int]] = {}
        for index, tender in enumerate(pool["tenders"]):
            for slug in tender["trade_slugs"]:
                by_slug.setdefault(slug, []).append(index)
        clusters = [members for members in by_slug.values() if len(members) >= 5]
        self.assertTrue(clusters, "pool has no trade clusters to build centroids from")

        rng = np.random.default_rng(7)
        sample = []
        for index in range(40):
            members = clusters[index % len(clusters)]
            # Firms at the floor have 5 bids; busy ones have thousands. Vary the count
            # so the test covers both the noisy and the smooth end.
            count = int(rng.integers(5, min(len(members), 200) + 1))
            picks = rng.choice(members, size=count, replace=False)
            centroid = np.asarray(vectors[picks].mean(axis=0), dtype=np.float32)
            sample.append(
                profiles.FirmProfile(
                    canonical_id=f"F-{index}",
                    display_name=f"Firm {index}",
                    normalized_name=f"firm {index}",
                    bids=count,
                    wins=0,
                    first_date=None,
                    last_date=None,
                    centroid=centroid,
                )
            )

        tenders = vectors[:400]
        report = profiles.quantization_divergence(sample, tenders)
        self.assertGreater(
            report["max_cosine_error"],
            0.0,
            "a divergence of exactly zero means the measurement is not measuring",
        )
        # Cosine error itself must stay tiny.
        self.assertLess(report["max_cosine_error"], 0.005, report)
        # And no *material* reordering: nothing separated by more than twice that
        # error may swap. Rank shifts among near-tied tenders are expected and are not
        # a defect — they would happen under any perturbation, float64 included.
        self.assertEqual(
            0,
            report["material_reorderings"],
            f"int8 reordered genuinely-separated tenders: {report}",
        )


class ArtifactTests(unittest.TestCase):
    """What the shipped profile may and may not contain."""

    @classmethod
    def setUpClass(cls) -> None:
        if not ARTIFACT.is_file():
            raise unittest.SkipTest("no firms.json; run scripts.export_model_service")
        cls.payload = json.loads(ARTIFACT.read_text(encoding="utf-8"))

    def test_no_profile_carries_a_procurement_list_or_a_bid_amount(self) -> None:
        """The ethics line, asserted rather than trusted to review.

        Aggregate facts about a named firm are the product. The list of contracts it
        bid on, and any amount attributed to it, are not — and the only way that stays
        true across future edits is a test that fails when it stops being true.
        """
        forbidden = {"ocids", "amounts", "bids_list", "interactions", "bid_amounts", "tenders"}
        for record in self.payload["firms"][:500]:
            leaked = forbidden & set(record)
            self.assertEqual(set(), leaked, f"{record['name']} leaked {leaked}")

    def test_every_profile_clears_the_minimum_bid_floor(self) -> None:
        for record in self.payload["firms"][:2000]:
            self.assertGreaterEqual(record["bids"], self.payload["min_bids"])

    def test_the_index_never_collapses_an_ambiguous_name(self) -> None:
        """448 names at this floor are shared. Collapsing them would be a guess."""
        shared = [name for name, ids in self.payload["index"].items() if len(ids) > 1]
        self.assertTrue(shared, "expected some shared names at this floor")
        for name in shared[:50]:
            self.assertGreater(len(set(self.payload["index"][name])), 1)

    def test_feature_vectors_are_complete(self) -> None:
        """A missing firm feature would be read as a zero and silently mis-rank."""
        expected = {
            "firm_interactions", "firm_wins", "firm_win_rate", "firm_distinct_categories",
            "firm_distinct_buyers", "firm_distinct_regions", "firm_category_concentration",
            "firm_median_bid", "firm_log_median_bid", "firm_bid_spread",
            "firm_days_since_last", "firm_active_days", "firm_bids_per_month",
            "firm_has_amounts",
        }
        for record in self.payload["firms"][:200]:
            self.assertEqual(expected, set(record["features"]))

    def test_profiles_describe_the_past_only(self) -> None:
        """As-of discipline: no profile may claim activity after the export date."""
        generated = self.payload.get("generated_at") or "9999"
        for record in self.payload["firms"][:1000]:
            if record["last"]:
                self.assertLess(record["last"], "2027-01-01")
                if generated != "9999":
                    self.assertLess(record["last"], generated[:10])


class NameIndexTests(unittest.TestCase):
    def test_the_index_groups_by_normalized_name(self) -> None:
        built = profiles.name_index(
            [
                profiles.FirmProfile("A", "Excavation Bergeron inc.", "excavation bergeron", 9, 3, None, None),
                profiles.FirmProfile("B", "EXCAVATION BERGERON LTEE", "excavation bergeron", 7, 1, None, None),
                profiles.FirmProfile("C", "Groupe Colas", "groupe colas", 50, 20, None, None),
            ]
        )
        self.assertEqual({"A", "B"}, set(built["excavation bergeron"]))
        self.assertEqual(["C"], built["groupe colas"])


if __name__ == "__main__":
    unittest.main()
