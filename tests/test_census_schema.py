import unittest

from census import schema, sources


def _record(slug: str, **overrides) -> dict:
    record = {
        "slug": slug,
        "name": slug.replace("-", " ").title(),
        "tier": "lower",
        "geographic_area": "Muskoka",
        "website_url": f"https://www.{slug}.ca/",
        "website_host": f"www.{slug}.ca",
        "population": 7652,
        "population_source": "statcan-98-10-0002-2021",
    }
    record.update(overrides)
    return record


class SlugTests(unittest.TestCase):
    def test_status_is_part_of_the_slug_so_shared_names_stay_distinct(self) -> None:
        # Six Ontario names belong to two governments each; dropping the status
        # merged them and silently lost one of every pair.
        pairs = (
            ("Hamilton, City of", "Hamilton, Township of"),
            ("Waterloo, Regional Municipality of", "Waterloo, City of"),
            ("Essex, County of", "Essex, Town of"),
            ("Perth, Town of", "Perth, County of"),
            ("Peterborough, County of", "Peterborough, City of"),
            ("Renfrew, County of", "Renfrew, Town of"),
        )
        for first, second in pairs:
            with self.subTest(pair=first):
                self.assertNotEqual(schema.slugify(first), schema.slugify(second))

    def test_slugs_are_readable(self) -> None:
        self.assertEqual(schema.slugify("Muskoka Lakes, Township of"), "muskoka-lakes-township")
        self.assertEqual(schema.slugify("Toronto, City of"), "toronto-city")
        self.assertEqual(
            schema.slugify("Waterloo, Regional Municipality of"), "waterloo-region"
        )

    def test_accents_and_punctuation_are_folded(self) -> None:
        self.assertEqual(
            schema.slugify("Mattice-Val Côté, Township of"), "mattice-val-cote-township"
        )
        self.assertEqual(schema.slugify("Burk’s Falls, Village of"), "burk-s-falls-village")

    def test_a_slug_does_not_depend_on_the_rest_of_the_roster(self) -> None:
        # Phase B builds notice ids from these, so a slug must never shift because
        # some other municipality was added.
        self.assertEqual(
            schema.slugify("Muskoka Lakes, Township of"),
            schema.slugify("Muskoka Lakes, Township of"),
        )


class NameMatchingTests(unittest.TestCase):
    def test_double_encoded_accents_from_the_source_csv_are_repaired(self) -> None:
        self.assertEqual(sources.repair_mojibake("Mattice-Val CÃ´tÃ©"), "Mattice-Val Côté")

    def test_repair_leaves_clean_text_alone(self) -> None:
        self.assertEqual(sources.repair_mojibake("Muskoka Lakes"), "Muskoka Lakes")

    def test_status_suffixes_are_stripped_for_matching(self) -> None:
        self.assertEqual(sources.normalize_name("Muskoka Lakes, Township of"), "muskoka lakes")
        self.assertEqual(
            sources.normalize_name("Muskoka, District Municipality of"), "muskoka"
        )

    def test_curly_apostrophes_match_straight_ones(self) -> None:
        self.assertEqual(
            sources.normalize_name("Burk’s Falls, Village of"),
            sources.normalize_name("Burk's Falls"),
        )

    def test_bilingual_statcan_names_are_indexed_on_both_sides(self) -> None:
        variants = sources.name_variants("Greater Sudbury / Grand Sudbury")

        self.assertIn("greater sudbury", variants)
        self.assertIn("grand sudbury", variants)

    def _index(self) -> dict:
        return {
            "divisions": {"hamilton": 569353, "northumberland": 89365, "perth": 81565},
            "division_codes": {
                "hamilton": "3525",
                "northumberland": "3514",
                "perth": "3531",
            },
            "subdivisions": {
                "greater sudbury": [("3553", 166004)],
                "grand sudbury": [("3553", 166004)],
                "muskoka lakes": [("3544", 7652)],
                "burk's falls": [("3549", 981)],
                # The collision: the city and the township share a bare name.
                "hamilton": [("3525", 569353), ("3514", 11059)],
                "perth": [("3509", 6469)],
            },
        }

    def test_population_joins_across_the_naming_conventions(self) -> None:
        records = [
            _record("greater-sudbury-city", name="Greater Sudbury, City of", population=None),
            _record("muskoka-lakes-township", name="Muskoka Lakes, Township of", population=None),
            _record("burks-falls-village", name="Burk’s Falls, Village of", population=None),
            _record("nowhere-township", name="Nowhere, Township of", population=None),
        ]

        coverage = sources.attach_population(records, self._index())

        self.assertEqual(coverage["matched"], 3)
        self.assertEqual(records[0]["population"], 166004)
        self.assertIsNone(records[3]["population"])

    def test_a_city_does_not_inherit_a_like_named_townships_population(self) -> None:
        # The City of Hamilton has 569,353 residents; Hamilton Township has 11,059.
        # A flat name index gave the city the township's figure.
        records = [
            _record(
                "hamilton-city",
                name="Hamilton, City of",
                tier="single",
                geographic_area="Hamilton",
                population=None,
            ),
            _record(
                "hamilton-township",
                name="Hamilton, Township of",
                tier="lower",
                geographic_area="Northumberland",
                population=None,
            ),
        ]

        sources.attach_population(records, self._index())

        self.assertEqual(records[0]["population"], 569353)
        self.assertEqual(records[1]["population"], 11059)

    def test_an_upper_tier_municipality_gets_its_division_not_its_town(self) -> None:
        # Perth County has 81,565 residents; the Town of Perth has 6,469.
        records = [
            _record("perth-county", name="Perth, County of", tier="upper", population=None),
            _record(
                "perth-town",
                name="Perth, Town of",
                tier="lower",
                geographic_area="Lanark",
                population=None,
            ),
        ]

        sources.attach_population(records, self._index())

        self.assertEqual(records[0]["population"], 81565)
        self.assertEqual(records[1]["population"], 6469)

    def test_an_unresolvable_collision_is_left_unset_rather_than_guessed(self) -> None:
        records = [
            _record(
                "hamilton-city",
                name="Hamilton, City of",
                tier="lower",
                geographic_area="Nowhere",
                population=None,
            )
        ]

        with self.assertLogs("census.sources", level="WARNING"):
            sources.attach_population(records, self._index())

        self.assertIsNone(records[0]["population"])


class RosterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = schema.connect(":memory:")
        self.addCleanup(self.connection.close)

    def test_the_roster_is_inserted_then_left_unchanged(self) -> None:
        records = [_record("muskoka-lakes-township"), _record("orillia-city")]

        first = schema.upsert_municipalities(self.connection, records)
        second = schema.upsert_municipalities(self.connection, records)

        self.assertEqual(first["inserted"], 2)
        self.assertEqual(second["unchanged"], 2)
        self.assertEqual(second["inserted"], 0)

    def test_refreshing_the_roster_preserves_classification_results(self) -> None:
        schema.upsert_municipalities(self.connection, [_record("muskoka-lakes-township")])
        schema.record_result(
            self.connection,
            "muskoka-lakes-township",
            {
                "classification": schema.CLASS_OWN_SITE_OPEN,
                "confidence": schema.CONFIDENCE_HIGH,
                "platform": None,
                "procurement_url": "https://www.muskokalakes.ca/tenders",
                "evidence_url": "https://www.muskokalakes.ca/tenders",
                "evidence_note": "91 tender-patterned documents",
                "cms_fingerprint": "escribe",
                "robots_ok": 1,
                "http_status": 200,
                "requests_made": 3,
            },
        )

        schema.upsert_municipalities(
            self.connection, [_record("muskoka-lakes-township", population=7700)]
        )

        row = self.connection.execute(
            "SELECT * FROM municipalities WHERE slug = 'muskoka-lakes-township'"
        ).fetchone()
        self.assertEqual(row["classification"], schema.CLASS_OWN_SITE_OPEN)
        self.assertEqual(row["population"], 7700)


class ResumeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = schema.connect(":memory:")
        self.addCleanup(self.connection.close)
        schema.upsert_municipalities(
            self.connection,
            [
                _record("a-township", population=100),
                _record("b-township", population=300),
                _record("c-township", population=200),
                _record("d-township", website_url=None, website_host=None),
            ],
        )

    def _classify(self, slug: str, classification: str) -> None:
        schema.record_result(
            self.connection,
            slug,
            {
                "classification": classification,
                "confidence": None,
                "platform": None,
                "procurement_url": None,
                "evidence_url": None,
                "evidence_note": "",
                "cms_fingerprint": None,
                "robots_ok": 1,
                "http_status": 200,
                "requests_made": 2,
            },
        )

    def test_everything_with_a_website_is_pending_at_the_start(self) -> None:
        pending = schema.pending_municipalities(self.connection)

        self.assertEqual([row["slug"] for row in pending], ["b-township", "c-township", "a-township"])

    def test_municipalities_without_a_website_are_never_queued(self) -> None:
        pending = schema.pending_municipalities(self.connection)

        self.assertNotIn("d-township", [row["slug"] for row in pending])

    def test_resuming_skips_what_is_already_classified(self) -> None:
        self._classify("b-township", schema.CLASS_OWN_SITE_OPEN)

        pending = schema.pending_municipalities(self.connection)

        self.assertEqual([row["slug"] for row in pending], ["c-township", "a-township"])

    def test_a_full_recheck_re_queues_everything(self) -> None:
        self._classify("b-township", schema.CLASS_OWN_SITE_OPEN)

        pending = schema.pending_municipalities(self.connection, resume=False)

        self.assertEqual(len(pending), 3)

    def test_a_class_can_be_re_queued_on_its_own(self) -> None:
        # How an improved link scorer gets applied to the ones it would now find.
        self._classify("a-township", schema.CLASS_NONE_FOUND)
        self._classify("b-township", schema.CLASS_OWN_SITE_OPEN)
        self._classify("c-township", schema.CLASS_NONE_FOUND)

        pending = schema.pending_municipalities(
            self.connection, recheck_classes=(schema.CLASS_NONE_FOUND,)
        )

        self.assertEqual({row["slug"] for row in pending}, {"a-township", "c-township"})

    def test_the_queue_is_ordered_by_population_so_a_partial_run_covers_the_most_people(
        self,
    ) -> None:
        pending = schema.pending_municipalities(self.connection, limit=2)

        self.assertEqual([row["slug"] for row in pending], ["b-township", "c-township"])


class DistributionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = schema.connect(":memory:")
        self.addCleanup(self.connection.close)
        schema.upsert_municipalities(
            self.connection,
            [
                _record("big-city", population=800),
                _record("small-township", population=200),
            ],
        )
        for slug, classification in (
            ("big-city", schema.CLASS_BIDS_AND_TENDERS),
            ("small-township", schema.CLASS_OWN_SITE_OPEN),
        ):
            schema.record_result(
                self.connection,
                slug,
                {
                    "classification": classification,
                    "confidence": schema.CONFIDENCE_HIGH,
                    "platform": None,
                    "procurement_url": None,
                    "evidence_url": None,
                    "evidence_note": "",
                    "cms_fingerprint": None,
                    "robots_ok": 1,
                    "http_status": 200,
                    "requests_made": 2,
                },
            )

    def test_population_share_tells_a_different_story_than_the_count(self) -> None:
        rows = {row["classification"]: row for row in schema.distribution(self.connection)}

        self.assertEqual(rows[schema.CLASS_BIDS_AND_TENDERS]["municipalities"], 1)
        self.assertEqual(rows[schema.CLASS_BIDS_AND_TENDERS]["share_of_municipalities"], 50.0)
        self.assertEqual(rows[schema.CLASS_BIDS_AND_TENDERS]["share_of_population"], 80.0)
        self.assertEqual(rows[schema.CLASS_OWN_SITE_OPEN]["share_of_population"], 20.0)

    def test_population_coverage_is_reported(self) -> None:
        coverage = schema.population_coverage(self.connection)

        self.assertEqual(coverage, {"total": 2, "matched": 2})


if __name__ == "__main__":
    unittest.main()
