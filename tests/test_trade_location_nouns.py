"""A keyword must say what work is performed, never only where it happens.

`chemin` was in the roadwork rule as a bare keyword, so *"Déneigement des chemins
municipaux"* — snow clearing — classified as roadwork. That is false on its own terms:
the notice is not road construction because it mentions a road, any more than an
electrical job at 285 chemin Principal is. The falseness is in the classification, not
in whatever a downstream ranking gate then does with it.

**These tests pin the rule, not the symptom.** The symptom was a paving query returning
snow-clearing contracts in its top three, and that is asserted elsewhere
(`tests/test_demo_rank.py`). What is asserted here is the property that caused it: a
notice describing one kind of work must not acquire a second trade purely because a road,
sidewalk, park or building was named. Re-adding the bare keyword fails
`test_no_bare_location_noun_is_a_keyword` directly, by name, whatever the ranking
happens to do that week.

The counterexamples matter as much as the positives. Removing a location noun outright
would trade a false positive for a batch of false negatives — *"Réfection chemin
Kilmar"* is genuinely roadwork — so the fix is phrase forms bound to a work word, and
the tests below assert both halves.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import config
from matchrec import trades


MAPPING_PATH = Path(config.PROJECT_ROOT) / "matchrec" / "trade_mapping.json"

#: Bare nouns that name a place or a surface rather than an activity, mapped to the
#: slug that must not be reachable from them alone.
#:
#: Only `chemin` has been removed so far. The rest are recorded because the same audit
#: found them with the same shape and measured them misfiring on real notices — `ecole`
#: sources building_general alone on 382 notices whose work is facility maintenance,
#: `parc` sources landscaping alone on 257 whose work is engineering. They are listed
#: as `KNOWN_UNFIXED` rather than asserted, so the inventory survives outside the
#: conversation that produced it and nobody has to re-derive it.
FORBIDDEN_BARE = {
    "chemin": "roadwork",
}

KNOWN_UNFIXED = {
    "ecole": "building_general",
    "batiment": "building_general",
    "edifice": "building_general",
    "pavillon": "building_general",
    "parc": "landscaping",
    "pont": "bridge_structural",
    "chaussee": "roadwork",
    "voirie": "roadwork",
    "trottoir": "concrete_flatwork",
    "quai": "marine_shoreline",
}


def _keywords(slug: str) -> list[str]:
    payload = json.loads(MAPPING_PATH.read_text(encoding="utf-8"))
    for rule in payload["rules"]:
        if rule["slug"] == slug:
            return rule.get("keywords_en", []) + rule.get("keywords_fr", [])
    raise AssertionError(f"no rule for slug {slug}")


class BareLocationNounTests(unittest.TestCase):
    """The rule itself, asserted against the shipped mapping file."""

    def test_no_bare_location_noun_is_a_keyword(self) -> None:
        """Fails the moment someone re-adds the bare form."""
        for noun, slug in FORBIDDEN_BARE.items():
            self.assertNotIn(
                noun,
                _keywords(slug),
                f"\n'{noun}' is back as a bare keyword on '{slug}'.\n"
                "It names where work happens, not what work is performed, so it "
                "classifies by geography: every notice mentioning that word acquires "
                f"'{slug}' whatever the job actually is.\n"
                f"If a '{noun}' notice needs to reach '{slug}', add a phrase form "
                f"bound to a work word — 'refection de {noun}' — not the bare noun.",
            )

    def test_the_phrase_forms_that_replaced_it_are_present(self) -> None:
        """Removing the noun without replacing it would be the opposite defect."""
        roadwork = _keywords("roadwork")
        for phrase in ("refection de chemin", "reconstruction du chemin", "travaux de chemin"):
            self.assertIn(phrase, roadwork, f"missing phrase form {phrase!r}")


class SnowClearingTests(unittest.TestCase):
    """The case that started it: snow clearing on a road is not roadwork."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.mapping = trades.load_mapping()

    def _slugs(self, title: str) -> list[str]:
        return self.mapping.classify({"title": title})["trade_slugs"]

    def test_snow_clearing_on_roads_is_not_roadwork(self) -> None:
        for title in (
            "Déneigement des chemins municipaux",
            "Contrat de déneigement et de déglaçage des chemins municipaux",
            "Déneigement des chemins municipaux - secteur A 26-27, 27-28, 28-29",
            "Déneigement et entretien des chemins d'hiver",
            "Services de déneigement de chemins et de stationnements",
        ):
            slugs = self._slugs(title)
            self.assertIn("snow_ice_management", slugs, f"snow not detected: {title}")
            self.assertNotIn(
                "roadwork",
                slugs,
                f"\nsnow clearing classified as roadwork: {title}\n  slugs: {slugs}",
            )

    def test_work_at_a_road_address_does_not_become_roadwork(self) -> None:
        """The starkest form: the road is only an address."""
        for title in (
            "Travaux d'installation de 2 bornes de recharge au 285 Chemin Principal",
            "Fournir, installer et certifier du câblage au 10e étage du 200 chemin Sainte-Foy",
        ):
            self.assertNotIn("roadwork", self._slugs(title), title)

    def test_other_trades_on_a_road_keep_only_their_own_trade(self) -> None:
        slugs = self._slugs("Prolongement de l'égout sanitaire sur le chemin de Chambly")
        self.assertIn("water_wastewater", slugs)
        self.assertNotIn("roadwork", slugs)


class GenuineRoadworkStillClassifiesTests(unittest.TestCase):
    """The other half. A narrower rule that loses real roadwork is not an improvement."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.mapping = trades.load_mapping()

    def _slugs(self, title: str) -> list[str]:
        return self.mapping.classify({"title": title})["trade_slugs"]

    def test_road_work_on_a_chemin_is_still_roadwork(self) -> None:
        for title in (
            "Réfection chemin Kilmar GR-CH-29.17-16",
            "Travaux de réfection de chemin",
            "Reconstruction du chemin des Hautes-Terres",
            "Travaux de construction d'un chemin d'accès",
            "Déplacement du chemin Montcerf",
        ):
            self.assertIn(
                "roadwork",
                self._slugs(title),
                f"\ngenuine roadwork lost: {title}\n"
                "The phrase forms are meant to keep exactly these.",
            )

    def test_the_vocabulary_gaps_the_bare_keyword_was_hiding(self) -> None:
        """These were only ever classified by accident of the word 'chemin'.

        Removing it exposed them, which is why the fix adds work verbs as well as
        phrase forms. Without these the change would have traded one wrong
        classification for ~50 silent false negatives.
        """
        for title, expected in (
            ("Asphaltage - Chemins municipaux 2026", "roadwork"),
            ("Scellement de fissures 26-TP-006", "roadwork"),
            ("Rechargement granulaire - Chemins Laperle et Curtis", "roadwork"),
            ("Traitement de surface double, chemin du 1er Rang", "roadwork"),
            ("Nivelage des chemins", "roadwork"),
        ):
            self.assertIn(expected, self._slugs(title), title)


class MappingVersionTests(unittest.TestCase):
    def test_the_version_moved_with_the_vocabulary(self) -> None:
        """A vocabulary change under an unchanged version ships silently.

        rank.prepare re-maps only when the stored version differs, and the serving
        manifest records it — so an edit without a bump leaves stale slugs in the
        database and a manifest that misstates what produced them.
        """
        version = json.loads(MAPPING_PATH.read_text(encoding="utf-8"))["version"]
        self.assertNotEqual("2026-07-30.3", version, "mapping edited without a version bump")


if __name__ == "__main__":
    unittest.main()
