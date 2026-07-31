import json
import tempfile
import unittest
from pathlib import Path

from census import schema as census_schema
from scripts import export_census


def _municipality(slug: str, **overrides) -> dict:
    record = {
        "slug": slug,
        "name": slug.replace("-", " ").title(),
        "tier": "lower",
        "geographic_area": "Muskoka",
        "website_url": f"https://www.{slug}.ca/",
        "website_host": f"www.{slug}.ca",
        "population": 10_000,
        "population_source": "statcan",
    }
    record.update(overrides)
    return record


def _result(classification: str, **overrides) -> dict:
    result = {
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
    }
    result.update(overrides)
    return result


class ExportCensusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.db_path = self.root / "census.db"
        connection = census_schema.connect(self.db_path)
        census_schema.upsert_municipalities(
            connection,
            [
                _municipality("frontenac-county", tier="upper", population=161_780),
                _municipality("muskoka-lakes-township", population=7_652),
                _municipality("orillia-city", tier="single", population=33_411),
                _municipality("big-city", tier="single", population=800_000),
                _municipality("no-site-township", website_url=None, population=500),
            ],
        )
        for slug, classification in (
            ("frontenac-county", census_schema.CLASS_OWN_SITE_OPEN),
            ("muskoka-lakes-township", census_schema.CLASS_OWN_SITE_OPEN),
            ("orillia-city", census_schema.CLASS_BIDS_AND_TENDERS),
            ("big-city", census_schema.CLASS_BIDS_AND_TENDERS),
            ("no-site-township", census_schema.CLASS_NO_WEBSITE),
        ):
            census_schema.record_result(connection, slug, _result(classification))
        connection.close()

        self.payload = json.loads(
            export_census.export(self.root / "out", self.db_path).read_text(
                encoding="utf-8"
            )
        )

    def test_every_municipality_ships_for_client_side_lookup(self) -> None:
        self.assertEqual(len(self.payload["municipalities"]), 5)
        names = {item["name"] for item in self.payload["municipalities"]}
        self.assertIn("Muskoka Lakes Township", names)

    def test_each_municipality_carries_a_readable_class_label(self) -> None:
        by_slug = {item["slug"]: item for item in self.payload["municipalities"]}

        self.assertEqual(
            by_slug["muskoka-lakes-township"]["label"],
            "Open documents on their own site",
        )
        self.assertEqual(by_slug["orillia-city"]["label"], "On bids&tenders")

    def test_the_distribution_reports_counts_and_population_shares(self) -> None:
        rows = {row["classification"]: row for row in self.payload["distribution"]}

        platform = rows[census_schema.CLASS_BIDS_AND_TENDERS]
        self.assertEqual(platform["municipalities"], 2)
        self.assertEqual(platform["population"], 833_411)

    def test_the_open_class_ships_frontenac_corrected_figures(self) -> None:
        open_row = next(
            row
            for row in self.payload["distribution"]
            if row["classification"] == census_schema.CLASS_OWN_SITE_OPEN
        )

        # Raw: two municipalities, 169,432 residents. Corrected: one, 7,652.
        self.assertEqual(open_row["municipalities"], 2)
        self.assertEqual(open_row["corrected"]["municipalities"], 1)
        self.assertEqual(open_row["corrected"]["population"], 7_652)
        self.assertIn("Frontenac", open_row["corrected"]["footnote"])

    def test_the_homepage_buckets_use_the_corrected_open_figure(self) -> None:
        buckets = {bucket["key"]: bucket for bucket in self.payload["buckets"]}

        self.assertEqual(buckets["open"]["population"], 7_652)
        self.assertEqual(buckets["bids_and_tenders"]["population"], 833_411)

    def test_every_class_lands_in_exactly_one_bucket(self) -> None:
        bucketed = [name for _, _, classes in export_census.BUCKETS for name in classes]

        self.assertEqual(len(bucketed), len(set(bucketed)))
        self.assertEqual(
            set(bucketed),
            set(census_schema.CLASSIFICATIONS) - {census_schema.CLASS_PENDING},
        )

    def test_the_bucket_labels_are_the_homepage_wording(self) -> None:
        labels = [bucket["label"] for bucket in self.payload["buckets"]]

        self.assertIn("notices visible, documents gated", labels)
        self.assertIn("bids&tenders", labels)

    def test_source_provenance_ships_with_the_data(self) -> None:
        sources = self.payload["sources"]

        self.assertEqual(
            sources["register"]["dataset_id"], "62e83cbc-0731-4d66-abdc-2f2b31bcd76c"
        )
        self.assertIn("Open Government Licence", sources["register"]["licence"])
        self.assertIn("98-10-0002", sources["population"]["name"])

    def test_totals_match_the_register(self) -> None:
        self.assertEqual(self.payload["totals"]["municipalities"], 5)
        # 161,780 + 7,652 + 33,411 + 800,000 + 500
        self.assertEqual(self.payload["totals"]["population"], 1_003_343)


if __name__ == "__main__":
    unittest.main()
