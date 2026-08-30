"""Whether the shipped ranking model may be reused, and on what evidence.

The export used to refit the GBM every run, which cost ~15 minutes and quietly
detached the served model from the report that describes it. Reuse is now the
default, so the question "is this booster still the right one?" has to be answered
by something more honest than a file timestamp. These tests pin that answer: what
counts as current, what does not, and what is deliberately excluded from the
decision.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from model import embeddings, train
from scripts import export_model_service as export_service


def _manifest(directory: Path, **overrides) -> dict:
    """A manifest that agrees with the code, before any override is applied."""
    names = export_service.served_feature_names()
    booster = {
        "feature_names": names,
        "trees": [],
        "num_trees": 300,
        "objective": "lambdarank",
    }
    booster_path = directory / "booster.json"
    with booster_path.open("w", encoding="utf-8") as handle:
        json.dump(booster, handle)

    payload = {
        "generated_at": "2026-08-04T23:23:02+00:00",
        "serving_cutoff": export_service.SERVING_CUTOFF,
        "embedding_model": embeddings.MODEL_NAME,
        "mapping_version": "2026-07-30.3",
        "feature_order": names,
        "leaky_features_excluded": list(train.LEAKY_FEATURES),
        "pool": {"count": 2003, "sources": ["canadabuys", "seao"]},
        "model": {
            "trees": 300,
            "training_rows": 4849772,
            "training_firms": 11182,
            "booster_sha256": export_service.file_sha256(booster_path),
        },
    }
    payload.update(overrides)
    with (directory / "manifest.json").open("w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    return payload


class BoosterCurrencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _check(self) -> tuple[bool, str | None]:
        return export_service.booster_is_current(
            self.directory, export_service.SERVING_CUTOFF
        )

    def test_a_matching_manifest_is_current(self) -> None:
        _manifest(self.directory)
        current, reason = self._check()
        self.assertTrue(current, reason)
        self.assertIsNone(reason)

    def test_a_permuted_feature_order_is_rejected(self) -> None:
        """The manifest's own warning: a scorer reading features in a different
        order produces plausible-looking nonsense rather than an error."""
        names = export_service.served_feature_names()
        swapped = list(names)
        swapped[0], swapped[1] = swapped[1], swapped[0]
        _manifest(self.directory, feature_order=swapped)

        current, reason = self._check()
        self.assertFalse(current)
        self.assertIn("feature_order", reason)

    def test_a_shortened_feature_order_is_rejected(self) -> None:
        names = export_service.served_feature_names()
        _manifest(self.directory, feature_order=names[:-1])
        current, reason = self._check()
        self.assertFalse(current)
        self.assertIn("feature_order", reason)

    def test_a_leaky_feature_change_is_rejected(self) -> None:
        _manifest(self.directory, leaky_features_excluded=[])
        current, reason = self._check()
        self.assertFalse(current)
        self.assertIn("leaky_features_excluded", reason)

    def test_a_different_embedding_model_is_rejected(self) -> None:
        _manifest(self.directory, embedding_model="some/other-model")
        current, reason = self._check()
        self.assertFalse(current)
        self.assertIn("embedding_model", reason)

    def test_a_different_cutoff_is_rejected(self) -> None:
        _manifest(self.directory, serving_cutoff="2020-01-01")
        current, reason = self._check()
        self.assertFalse(current)
        self.assertIn("serving_cutoff", reason)

    def test_a_missing_hash_is_rejected(self) -> None:
        payload = _manifest(self.directory)
        del payload["model"]["booster_sha256"]
        with (self.directory / "manifest.json").open("w", encoding="utf-8") as handle:
            json.dump(payload, handle)

        current, reason = self._check()
        self.assertFalse(current)
        self.assertIn("booster_sha256", reason)

    def test_an_edited_booster_is_caught_by_the_hash(self) -> None:
        _manifest(self.directory)
        booster_path = self.directory / "booster.json"
        booster = json.loads(booster_path.read_text(encoding="utf-8"))
        booster["trees"] = [{"tampered": True}]
        with booster_path.open("w", encoding="utf-8") as handle:
            json.dump(booster, handle)

        current, reason = self._check()
        self.assertFalse(current)
        self.assertIn("hashes to", reason)

    def test_a_tree_count_disagreement_is_rejected(self) -> None:
        payload = _manifest(self.directory)
        payload["model"]["trees"] = 299
        with (self.directory / "manifest.json").open("w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        current, reason = self._check()
        self.assertFalse(current)
        self.assertIn("trees", reason)

    def test_an_absent_manifest_is_not_current(self) -> None:
        current, reason = self._check()
        self.assertFalse(current)
        self.assertIn("no manifest", reason)

    def test_an_absent_booster_is_not_current(self) -> None:
        _manifest(self.directory)
        (self.directory / "booster.json").unlink()
        current, reason = self._check()
        self.assertFalse(current)
        self.assertIn("no booster", reason)


class DeliberateExclusionTests(unittest.TestCase):
    """Three things that change constantly and invalidate nothing.

    Each of these would be a defensible-sounding thing to check, and checking any
    of them would force a retrain for no reason. They are excluded on purpose, so
    the exclusions are asserted rather than left to be re-litigated later.
    """

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _assert_still_current(self, **overrides) -> None:
        _manifest(self.directory, **overrides)
        current, reason = export_service.booster_is_current(
            self.directory, export_service.SERVING_CUTOFF
        )
        self.assertTrue(current, reason)

    def test_a_new_pool_does_not_invalidate_the_model(self) -> None:
        self._assert_still_current(pool={"count": 17, "sources": ["seao"]})

    def test_a_mapping_version_bump_does_not_invalidate_the_model(self) -> None:
        """It changes what the model is fed, not whether the model is valid."""
        self._assert_still_current(mapping_version="2099-01-01.9")

    def test_an_old_generated_at_does_not_invalidate_the_model(self) -> None:
        self._assert_still_current(generated_at="2001-01-01T00:00:00+00:00")


class RefusalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_export_refuses_a_stale_booster_and_names_refit(self) -> None:
        """And refuses before opening the database, so it costs nothing."""
        with self.assertRaises(export_service.StaleBooster) as caught:
            export_service.export(out_dir=self.directory, db_path=":memory:")
        self.assertIn("--refit", str(caught.exception))

    def test_adopt_refuses_a_booster_that_genuinely_disagrees(self) -> None:
        names = export_service.served_feature_names()
        _manifest(self.directory, feature_order=names[:-1])
        with self.assertRaises(export_service.StaleBooster) as caught:
            export_service.adopt(self.directory)
        self.assertIn("--refit", str(caught.exception))

    def test_adopt_records_the_hash_without_touching_the_booster(self) -> None:
        payload = _manifest(self.directory)
        del payload["model"]["booster_sha256"]
        with (self.directory / "manifest.json").open("w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        before = (self.directory / "booster.json").read_bytes()

        manifest = export_service.adopt(self.directory)

        self.assertIn("booster_sha256", manifest["model"])
        self.assertEqual("adopted", manifest["model_source"])
        self.assertEqual(before, (self.directory / "booster.json").read_bytes())
        current, reason = export_service.booster_is_current(
            self.directory, export_service.SERVING_CUTOFF
        )
        self.assertTrue(current, reason)


class CarriedFirmsTests(unittest.TestCase):
    """firms.json is a retrain artifact; a daily run must not touch it.

    Not "rewritten with identical content" — untouched. A file rewritten every day
    stops being traceable to the run that computed it, and its mtime starts lying
    about how old the profiles inside it are.
    """

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.firms = self.directory / "firms.json"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_firms(self, **extra) -> None:
        payload = {
            "count": 3,
            "min_bids": 5,
            "embedding_dim": 384,
            "index": {"acme": ["c1"], "beta": ["c2"], "gamma": ["c3"]},
            "firms": [],
        }
        payload.update(extra)
        with self.firms.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle)

    def test_a_carried_artifact_reports_its_own_build_date(self) -> None:
        self._write_firms(as_of="2026-08-04")
        carried = export_service._carried_firms(self.firms, {})

        self.assertEqual("carried-forward", carried["source"])
        self.assertEqual("2026-08-04", carried["built_at"])
        self.assertEqual(3, carried["count"])
        self.assertEqual(3, carried["distinct_names"])

    def test_built_at_never_becomes_the_date_it_was_carried(self) -> None:
        """The whole point: a carried artifact claiming today would be a lie."""
        self._write_firms(as_of="2026-08-04")
        carried = export_service._carried_firms(
            self.firms, {"generated_at": "2026-12-25T00:00:00+00:00"}
        )
        self.assertEqual("2026-08-04", carried["built_at"])

    def test_an_artifact_predating_the_field_falls_back_and_says_so(self) -> None:
        self._write_firms()
        carried = export_service._carried_firms(
            self.firms, {"generated_at": "2026-08-04T23:23:02+00:00"}
        )
        self.assertEqual("2026-08-04T23:23:02+00:00", carried["built_at"])
        self.assertIn("predates its own as_of field", carried["built_at_source"])

    def test_a_prior_built_at_is_preferred_to_the_manifest_timestamp(self) -> None:
        self._write_firms()
        carried = export_service._carried_firms(
            self.firms,
            {
                "generated_at": "2026-12-25T00:00:00+00:00",
                "firms": {"built_at": "2026-08-04"},
            },
        )
        self.assertEqual("2026-08-04", carried["built_at"])
        self.assertNotIn("built_at_source", carried)

    def test_a_stand_in_timestamp_keeps_its_caveat_across_repeated_carries(self) -> None:
        """Otherwise the second carry silently promotes it to a real build date."""
        self._write_firms()
        first = export_service._carried_firms(
            self.firms, {"generated_at": "2026-08-04T23:23:02+00:00"}
        )
        second = export_service._carried_firms(self.firms, {"firms": first})

        self.assertEqual(first["built_at"], second["built_at"])
        self.assertEqual(first["built_at_source"], second["built_at_source"])

    def test_a_missing_artifact_refuses_and_names_refit(self) -> None:
        with self.assertRaises(export_service.StaleBooster) as caught:
            export_service._carried_firms(self.firms, {})
        self.assertIn("--refit", str(caught.exception))


class ProfileSerializationTests(unittest.TestCase):
    def test_as_of_is_stamped_when_given_and_absent_otherwise(self) -> None:
        from model import profiles

        self.assertNotIn("as_of", profiles.serialize([]))
        self.assertEqual("2026-08-30", profiles.serialize([], as_of="2026-08-30")["as_of"])


class ShippedArtifactTests(unittest.TestCase):
    """The committed artifacts must satisfy their own guard."""

    def setUp(self) -> None:
        if not (export_service.OUT_DIR / "manifest.json").is_file():
            self.skipTest("serving artifacts are not present")

    def test_the_shipped_booster_is_current(self) -> None:
        current, reason = export_service.booster_is_current(
            export_service.OUT_DIR, export_service.SERVING_CUTOFF
        )
        self.assertTrue(current, reason)

    def test_the_manifest_names_the_report_that_describes_the_model(self) -> None:
        manifest = export_service._read_manifest(export_service.OUT_DIR)
        evaluation = manifest.get("evaluation") or {}
        report = Path(export_service.config.PROJECT_ROOT) / evaluation.get("report", "")

        self.assertTrue(report.is_file(), f"missing report {evaluation.get('report')}")
        # The link must not claim the report scored this booster.
        self.assertIn("did not score this booster", evaluation["relationship"])
        body = json.loads(report.read_text(encoding="utf-8"))
        models = {
            name
            for split in body.get("splits", [])
            for name in (split.get("models") or {})
        }
        # No `or {...}` fallback: an empty set here means the report shape moved and
        # the assertion stopped testing anything, which must fail rather than pass.
        self.assertIn(evaluation["model"], models)
        self.assertIn(
            evaluation["primary_split"],
            {split.get("split", {}).get("name") for split in body.get("splits", [])},
        )


if __name__ == "__main__":
    unittest.main()
