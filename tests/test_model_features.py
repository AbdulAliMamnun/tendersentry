import unittest

import numpy as np

from model import features


def _interaction(**overrides) -> features.Interaction:
    base = {
        "canonical_id": "FO-1",
        "ocid": "ocds-1",
        "date": "2026-01-15",
        "won": 0,
        "bid_amount": 100_000.0,
        "buyer_id": "OP-1",
        "category": "Travaux de construction",
        "region": "QC",
        "title": "Réfection de la rue Principale",
    }
    base.update(overrides)
    return features.Interaction(**base)


class AsOfLeakageTests(unittest.TestCase):
    """The rule the whole model depends on: history never sees the future."""

    def setUp(self) -> None:
        self.interactions = [
            _interaction(ocid="past-1", date="2026-01-10"),
            _interaction(ocid="past-2", date="2026-02-10", won=1),
            _interaction(ocid="same-day", date="2026-03-01"),
            _interaction(ocid="future-1", date="2026-04-10"),
            _interaction(ocid="future-2", date="2026-06-10", won=1),
        ]

    def test_history_excludes_everything_after_the_cutoff(self) -> None:
        history = features.build_histories(self.interactions, as_of="2026-03-01")["FO-1"]

        self.assertEqual(history.interactions, 2)
        self.assertEqual(history.ocids, {"past-1", "past-2"})

    def test_an_interaction_on_the_cutoff_is_excluded(self) -> None:
        # Strict inequality: a tender closing today must not see today's bids.
        history = features.build_histories(self.interactions, as_of="2026-03-01")["FO-1"]

        self.assertNotIn("same-day", history.ocids)

    def test_wins_after_the_cutoff_do_not_inflate_the_win_rate(self) -> None:
        history = features.build_histories(self.interactions, as_of="2026-03-01")["FO-1"]

        computed = features.firm_features(history, "2026-03-01")
        self.assertEqual(history.wins, 1)
        self.assertAlmostEqual(computed["firm_win_rate"], 0.5)

    def test_a_later_cutoff_sees_strictly_more(self) -> None:
        early = features.build_histories(self.interactions, as_of="2026-03-01")["FO-1"]
        late = features.build_histories(self.interactions, as_of="2026-07-01")["FO-1"]

        self.assertLess(early.interactions, late.interactions)
        self.assertTrue(early.ocids.issubset(late.ocids))

    def test_a_firm_with_no_prior_activity_is_absent_not_zeroed(self) -> None:
        histories = features.build_histories(self.interactions, as_of="2026-01-01")

        self.assertNotIn("FO-1", histories)

    def test_features_for_an_unseen_firm_are_defined(self) -> None:
        computed = features.firm_features(None, "2026-01-01")

        self.assertEqual(computed["firm_interactions"], 0.0)
        self.assertEqual(computed["firm_win_rate"], 0.0)
        # An unseen firm is stale, not fresh — otherwise recency would reward it.
        self.assertGreater(computed["firm_days_since_last"], 1000)

    def test_undated_interactions_are_never_counted(self) -> None:
        histories = features.build_histories(
            [_interaction(ocid="undated", date="")], as_of="2026-06-01"
        )

        self.assertEqual(histories, {})


class FirmFeatureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.history = features.build_histories(
            [
                _interaction(ocid="a", date="2026-01-01", category="Travaux de construction"),
                _interaction(ocid="b", date="2026-02-01", category="Travaux de construction", won=1),
                _interaction(ocid="c", date="2026-03-01", category="Services professionnels",
                             buyer_id="OP-2", bid_amount=900_000.0),
            ],
            as_of="2026-06-01",
        )["FO-1"]

    def test_counts_and_rates(self) -> None:
        computed = features.firm_features(self.history, "2026-06-01")

        self.assertEqual(computed["firm_interactions"], 3.0)
        self.assertAlmostEqual(computed["firm_win_rate"], 1 / 3)
        self.assertEqual(computed["firm_distinct_categories"], 2.0)
        self.assertEqual(computed["firm_distinct_buyers"], 2.0)

    def test_concentration_is_higher_for_a_specialist(self) -> None:
        specialist = features.build_histories(
            [_interaction(ocid=str(i), date="2026-01-01") for i in range(5)],
            as_of="2026-06-01",
        )["FO-1"]

        self.assertGreater(
            features.firm_features(specialist, "2026-06-01")["firm_category_concentration"],
            features.firm_features(self.history, "2026-06-01")["firm_category_concentration"],
        )

    def test_recency_grows_as_the_cutoff_moves_away(self) -> None:
        near = features.firm_features(self.history, "2026-03-15")["firm_days_since_last"]
        far = features.firm_features(self.history, "2026-06-01")["firm_days_since_last"]

        self.assertLess(near, far)


class CrossFeatureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.history = features.build_histories(
            [
                _interaction(ocid="a", date="2026-01-01", bid_amount=100_000.0),
                _interaction(ocid="b", date="2026-02-01", bid_amount=120_000.0),
            ],
            as_of="2026-06-01",
        )["FO-1"]

    def test_a_familiar_buyer_is_recognized(self) -> None:
        computed = features.cross_features(
            self.history, {"buyer_id": "OP-1", "category": None, "region": None, "value": None}
        )

        self.assertEqual(computed["cross_buyer_seen"], 1.0)
        self.assertEqual(computed["cross_buyer_prior_bids"], 2.0)

    def test_an_unfamiliar_buyer_scores_zero(self) -> None:
        computed = features.cross_features(
            self.history, {"buyer_id": "OP-999", "category": None, "region": None, "value": None}
        )

        self.assertEqual(computed["cross_buyer_seen"], 0.0)

    def test_category_share_reflects_the_firms_mix(self) -> None:
        computed = features.cross_features(
            self.history,
            {"category": "Travaux de construction", "buyer_id": None, "region": None, "value": None},
        )

        self.assertAlmostEqual(computed["cross_category_share"], 1.0)

    def test_value_fit_peaks_at_the_firms_usual_size(self) -> None:
        tender = {"category": None, "buyer_id": None, "region": None, "value": 110_000.0}
        far = {**tender, "value": 25_000_000.0}

        near_fit = features.cross_features(self.history, tender)["cross_value_fit"]
        far_fit = features.cross_features(self.history, far)["cross_value_fit"]

        self.assertGreater(near_fit, 0.9)
        self.assertLess(far_fit, 0.1)

    def test_value_fit_is_zero_when_the_notice_publishes_no_value(self) -> None:
        # True of over 99% of SEAO notices, so this is the common case.
        computed = features.cross_features(
            self.history, {"category": None, "buyer_id": None, "region": None, "value": None}
        )

        self.assertEqual(computed["cross_value_fit"], 0.0)
        self.assertEqual(computed["cross_value_ratio"], 0.0)

    def test_embedding_similarity_is_used_when_supplied(self) -> None:
        vector = np.array([1.0, 0.0, 0.0])
        same = features.cross_features(self.history, {}, vector, vector)
        orthogonal = features.cross_features(
            self.history, {}, vector, np.array([0.0, 1.0, 0.0])
        )

        self.assertAlmostEqual(same["cross_embedding_similarity"], 1.0)
        self.assertAlmostEqual(orthogonal["cross_embedding_similarity"], 0.0)

    def test_similarity_defaults_to_zero_without_embeddings(self) -> None:
        computed = features.cross_features(self.history, {})

        self.assertEqual(computed["cross_embedding_similarity"], 0.0)


class FeatureRegistryTests(unittest.TestCase):
    def test_every_feature_belongs_to_an_ablatable_group(self) -> None:
        for name in features.feature_names():
            with self.subTest(feature=name):
                self.assertIn(features.group_of(name), features.FEATURE_GROUPS)

    def test_all_three_groups_are_represented(self) -> None:
        groups = {features.group_of(name) for name in features.feature_names()}

        self.assertEqual(groups, set(features.FEATURE_GROUPS))

    def test_feature_names_are_unique_and_stable(self) -> None:
        names = features.feature_names()

        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(names, features.feature_names())


class CompetitiveSubsetTests(unittest.TestCase):
    def test_single_bidder_procurements_are_excluded_by_default(self) -> None:
        import sqlite3

        from model import dataset

        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        dataset.ensure_schema(connection)
        with connection:
            connection.executemany(
                "INSERT INTO bid_interactions (canonical_id, ocid, bid_amount, won, "
                "interaction_date) VALUES (?, ?, NULL, ?, '2026-01-01')",
                [
                    ("FO-1", "solo", 1),
                    ("FO-1", "contested", 0),
                    ("FO-2", "contested", 1),
                ],
            )
        self.addCleanup(connection.close)

        competitive = features.load_interactions(connection)
        everything = features.load_interactions(connection, competitive_only=False)

        self.assertEqual({item.ocid for item in competitive}, {"contested"})
        self.assertEqual(len(everything), 3)


if __name__ == "__main__":
    unittest.main()
