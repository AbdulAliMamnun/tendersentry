import unittest
from pathlib import Path

from census import fetcher, schema as census_schema
from notices import db, municipal_site


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "census"
MUSKOKA_URL = "https://www.muskokalakes.ca/township-hall/bids-and-tenders/"


class _FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class _FakeResponse:
    def __init__(self, text: str, status: int = 200, url: str = "") -> None:
        self.text = text
        self.status_code = status
        self.url = url


class _FakeSession:
    def __init__(self, pages: dict[str, object]) -> None:
        self.pages = pages
        self.calls: list[str] = []

    def get(self, url: str, **kwargs):
        self.calls.append(url)
        page = self.pages.get(url)
        if page is None:
            return _FakeResponse("", 404, url)
        if isinstance(page, _FakeResponse):
            page.url = page.url or url
            return page
        return _FakeResponse(str(page), 200, url)


def _client(pages: dict) -> tuple[fetcher.PoliteFetcher, _FakeSession]:
    clock = _FakeClock()
    limiter = fetcher.RateLimiter(clock=clock.time, sleeper=clock.sleep)
    session = _FakeSession(pages)
    return fetcher.PoliteFetcher(limiter=limiter, session=session), session


def _municipality(**overrides) -> dict:
    record = {
        "slug": "muskoka-lakes-township",
        "name": "Muskoka Lakes, Township of",
        "population": 7652,
        "classification": census_schema.CLASS_OWN_SITE_OPEN,
        "confidence": census_schema.CONFIDENCE_HIGH,
        "procurement_url": MUSKOKA_URL,
    }
    record.update(overrides)
    return record


class NoticeKeyTests(unittest.TestCase):
    def test_tender_identifiers_are_extracted_from_filenames(self) -> None:
        self.assertEqual(
            municipal_site.notice_key("t-2026-28-removal-of-fuel-tanks.pdf"), "t-2026-28"
        )
        self.assertEqual(
            municipal_site.notice_key("rfp-2025-34-engineering.pdf"), "rfp-2025-34"
        )

    def test_a_document_without_an_identifier_has_no_key(self) -> None:
        self.assertIsNone(municipal_site.notice_key("procurement-policy.pdf"))

    def test_addenda_are_recognized(self) -> None:
        self.assertTrue(municipal_site.is_addendum("t-2026-28-fuel-tanks-addendum-1.pdf"))
        self.assertTrue(municipal_site.is_addendum("rfp-2024-09-assessments-amendment.pdf"))
        self.assertFalse(municipal_site.is_addendum("t-2026-28-fuel-tanks.pdf"))


class ParseDocumentListTests(unittest.TestCase):
    def test_documents_are_grouped_into_one_notice_per_identifier(self) -> None:
        html = """
        <a href="/media/a/t-2026-28-fuel-tanks.pdf">T-2026-28 Fuel Tank Replacement</a>
        <a href="/media/b/t-2026-28-fuel-tanks-addendum-1.pdf">Addendum 1</a>
        <a href="/media/c/t-2026-31-granular.pdf">T-2026-31 Granular Supply</a>
        """

        notices = municipal_site.parse_document_list(html, MUSKOKA_URL)

        self.assertEqual([item["notice_id"] for item in notices], ["T-2026-28", "T-2026-31"])
        self.assertEqual(len(notices[0]["documents"]), 2)

    def test_the_package_names_the_notice_not_its_addendum(self) -> None:
        html = """
        <a href="/media/b/rfp-2024-09-building-condition-assessments-addendum-1.pdf">x</a>
        <a href="/media/a/rfp-2024-09-building-condition-assessments.pdf">y</a>
        """

        notices = municipal_site.parse_document_list(html, MUSKOKA_URL)

        self.assertNotIn("addendum", notices[0]["title"].casefold())

    def test_packages_sort_ahead_of_addenda(self) -> None:
        html = """
        <a href="/media/b/t-2026-28-work-addendum-1.pdf">Addendum</a>
        <a href="/media/a/t-2026-28-work.pdf">T-2026-28 Work</a>
        """

        notices = municipal_site.parse_document_list(html, MUSKOKA_URL)

        self.assertEqual(notices[0]["documents"][0]["kind"], "package")

    def test_policy_documents_are_not_treated_as_notices(self) -> None:
        html = '<a href="/media/a/procurement-policy-by-law.pdf">Procurement Policy</a>'

        self.assertEqual(municipal_site.parse_document_list(html, MUSKOKA_URL), [])

    def test_platform_hosted_documents_are_never_collected(self) -> None:
        html = (
            '<a href="https://town.bidsandtenders.ca/t-2026-01.pdf">T-2026-01</a>'
            '<a href="/media/a/t-2026-02-work.pdf">T-2026-02 Work</a>'
        )

        notices = municipal_site.parse_document_list(html, MUSKOKA_URL)

        self.assertEqual([item["notice_id"] for item in notices], ["T-2026-02"])


class PatternCoverageTests(unittest.TestCase):
    """The document-list pattern keys on links and tender ids, not on a CMS."""

    def test_a_wordpress_style_list_parses(self) -> None:
        html = """
        <div class="entry-content"><ul>
        <li><a href="/wp-content/uploads/2026/07/RFT-2026-12-Road-Reconstruction.pdf">
            RFT-2026-12 Road Reconstruction</a> &#8211; Closing: August 15, 2026</li>
        <li><a href="/wp-content/uploads/2026/07/RFT-2026-12-Road-Reconstruction-Addendum-1.pdf">
            Addendum 1</a></li>
        <li><a href="/wp-content/uploads/2026/06/RFQ-2026-08-Culvert-Replacement.pdf">
            RFQ-2026-08 Culvert Replacement</a> &#8211; Closing: July 30, 2026</li>
        </ul></div>
        """

        parsed = municipal_site.parse_notice_page(html, "https://www.example.ca/tenders/")

        self.assertEqual(parsed["pattern"], "document-list")
        self.assertEqual(
            [item["notice_id"] for item in parsed["notices"]],
            ["RFQ-2026-08", "RFT-2026-12"],
        )
        by_id = {item["notice_id"]: item for item in parsed["notices"]}
        self.assertEqual(len(by_id["RFT-2026-12"]["documents"]), 2)

    def test_a_table_layout_parses(self) -> None:
        html = """
        <table><tr><th>Number</th><th>Description</th><th>Closes</th></tr>
        <tr><td>T-2026-05</td>
            <td><a href="/docs/T-2026-05-sidewalk-program.pdf">Sidewalk Program</a></td>
            <td>Closing date: September 2, 2026</td></tr>
        </table>
        """

        parsed = municipal_site.parse_notice_page(html, "https://www.example.ca/bids")

        self.assertEqual(len(parsed["notices"]), 1)
        self.assertEqual(parsed["notices"][0]["notice_id"], "T-2026-05")


class ClosingDateTests(unittest.TestCase):
    def test_a_date_is_extracted_from_surrounding_noise(self) -> None:
        # Closing text is captured from a list item and can spill into the next one.
        self.assertEqual(
            municipal_site._parse_date("August 15, 2026 Addendum 1 RFQ-2026-08 C"),
            "2026-08-15T00:00:00",
        )

    def test_common_date_formats_parse(self) -> None:
        self.assertEqual(municipal_site._parse_date("July 30, 2026"), "2026-07-30T00:00:00")
        self.assertEqual(municipal_site._parse_date("2026-08-15"), "2026-08-15T00:00:00")

    def test_non_dates_are_none_rather_than_guessed(self) -> None:
        self.assertIsNone(municipal_site._parse_date("Unofficial Results"))
        self.assertIsNone(municipal_site._parse_date(""))
        self.assertIsNone(municipal_site._parse_date(None))

    def test_a_dated_notice_gets_a_real_status(self) -> None:
        html = (
            '<li><a href="/docs/t-2026-05-work.pdf">T-2026-05 Work</a> '
            "Closing: September 2, 2026</li>"
        )
        parsed = municipal_site.parse_notice_page(html, "https://www.example.ca/bids")

        records = municipal_site.to_notice_records(
            _municipality(), parsed, "https://www.example.ca/bids"
        )

        self.assertEqual(records[0]["record"]["closing_date"], "2026-09-02T00:00:00")
        self.assertIn(records[0]["record"]["status"], {"open", "closed"})


class RealPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        html = (FIXTURES / "muskoka_lakes_own_site_open.html").read_text(encoding="utf-8")
        cls.parsed = municipal_site.parse_notice_page(html, MUSKOKA_URL)

    def test_the_muskoka_page_parses_into_many_notices(self) -> None:
        self.assertEqual(self.parsed["pattern"], "document-list")
        self.assertGreater(len(self.parsed["notices"]), 20)

    def test_a_known_tender_is_found_with_its_addendum(self) -> None:
        notice = next(
            item for item in self.parsed["notices"] if item["notice_id"] == "T-2026-28"
        )

        self.assertIn("fuel", notice["title"].casefold())
        self.assertGreaterEqual(len(notice["documents"]), 2)
        self.assertTrue(
            any(document["kind"] == "addendum" for document in notice["documents"])
        )

    def test_every_document_url_is_absolute_and_off_platform(self) -> None:
        for notice in self.parsed["notices"]:
            for document in notice["documents"]:
                with self.subTest(url=document["url"]):
                    self.assertTrue(document["url"].startswith("https://"))
                    self.assertFalse(fetcher.is_platform_url(document["url"]))


class IngestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = census_schema.connect(":memory:")
        self.addCleanup(self.connection.close)
        db.migrate_source_constraint(self.connection)
        municipal_site.ensure_schema(self.connection)
        census_schema.upsert_municipalities(
            self.connection,
            [
                {
                    "slug": "muskoka-lakes-township",
                    "name": "Muskoka Lakes, Township of",
                    "tier": "lower",
                    "geographic_area": "Muskoka",
                    "website_url": "https://www.muskokalakes.ca/",
                    "website_host": "www.muskokalakes.ca",
                    "population": 7652,
                    "population_source": "statcan",
                }
            ],
        )
        self.html = (
            '<a href="/media/a/t-2026-28-fuel-tank-replacement.pdf">T-2026-28 Fuel Tanks</a>'
            '<a href="/media/b/t-2026-28-fuel-tank-replacement-addendum-1.pdf">Addendum 1</a>'
            '<a href="/media/c/t-2026-31-granular-supply.pdf">T-2026-31 Granular Supply</a>'
        )

    def _ingest(self, html: str | None = None, **overrides) -> dict:
        client, _ = _client({MUSKOKA_URL: html if html is not None else self.html})
        return municipal_site.ingest_municipality(
            self.connection, _municipality(**overrides), client
        )

    def test_notices_land_in_the_shared_tenders_table(self) -> None:
        outcome = self._ingest()

        self.assertEqual(outcome["status"], municipal_site.STATUS_PARSED)
        self.assertEqual(outcome["notices_found"], 2)
        self.assertEqual(
            db.count_by_source(self.connection)[municipal_site.SOURCE], 2
        )

    def test_source_ids_are_namespaced_by_municipality(self) -> None:
        self._ingest()

        ids = [
            row["source_id"]
            for row in self.connection.execute(
                "SELECT source_id FROM tenders WHERE source = ? ORDER BY source_id",
                (municipal_site.SOURCE,),
            )
        ]
        self.assertEqual(
            ids, ["muskoka-lakes-township:t-2026-28", "muskoka-lakes-township:t-2026-31"]
        )

    def test_document_links_are_stored_but_nothing_is_downloaded(self) -> None:
        outcome = self._ingest()

        rows = self.connection.execute(
            "SELECT url, kind FROM notice_documents ORDER BY url"
        ).fetchall()
        self.assertEqual(outcome["documents_found"], 3)
        self.assertEqual(len(rows), 3)
        self.assertIn("addendum", [row["kind"] for row in rows])

    def test_documents_open_follows_the_census_classification(self) -> None:
        self._ingest()
        open_flag = self.connection.execute(
            "SELECT documents_open FROM tenders WHERE source = ? LIMIT 1",
            (municipal_site.SOURCE,),
        ).fetchone()[0]

        self.assertEqual(open_flag, 1)

    def test_a_notices_only_municipality_stores_documents_open_false(self) -> None:
        self._ingest(classification=census_schema.CLASS_OWN_SITE_NOTICES)

        open_flag = self.connection.execute(
            "SELECT documents_open FROM tenders WHERE source = ? LIMIT 1",
            (municipal_site.SOURCE,),
        ).fetchone()[0]
        self.assertEqual(open_flag, 0)

    def test_an_undated_notice_is_unknown_rather_than_assumed_open(self) -> None:
        self._ingest()

        statuses = {
            row["status"]
            for row in self.connection.execute(
                "SELECT status FROM tenders WHERE source = ?", (municipal_site.SOURCE,)
            )
        }
        self.assertEqual(statuses, {"unknown"})

    def test_re_ingesting_the_same_page_writes_nothing_new(self) -> None:
        self._ingest()

        outcome = self._ingest()

        self.assertIn("inserted 0", outcome["note"])
        self.assertEqual(
            db.count_by_source(self.connection)[municipal_site.SOURCE], 2
        )
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM notice_documents").fetchone()[0],
            3,
        )

    def test_a_page_matching_no_pattern_is_flagged_for_a_parser(self) -> None:
        outcome = self._ingest("<h1>Contact purchasing for details</h1>")

        self.assertEqual(outcome["status"], municipal_site.STATUS_PARSER_NEEDED)
        self.assertEqual(outcome["notices_found"], 0)

    def test_a_failed_fetch_is_recorded_not_raised(self) -> None:
        client, _ = _client({})

        outcome = municipal_site.ingest_municipality(
            self.connection, _municipality(), client
        )

        self.assertEqual(outcome["status"], municipal_site.STATUS_FETCH_FAILED)

    def test_a_municipality_without_a_procurement_url_needs_a_parser(self) -> None:
        client, session = _client({})

        outcome = municipal_site.ingest_municipality(
            self.connection, _municipality(procurement_url=None), client
        )

        self.assertEqual(outcome["status"], municipal_site.STATUS_PARSER_NEEDED)
        self.assertEqual(session.calls, [])

    def test_parser_needed_is_ranked_by_population(self) -> None:
        census_schema.upsert_municipalities(
            self.connection,
            [
                {
                    "slug": "big-city",
                    "name": "Big City",
                    "tier": "single",
                    "geographic_area": "Somewhere",
                    "website_url": "https://big.example.ca/",
                    "website_host": "big.example.ca",
                    "population": 500_000,
                    "population_source": "statcan",
                }
            ],
        )
        self._ingest("<h1>nothing</h1>")
        client, _ = _client({"https://big.example.ca/tenders": "<h1>nothing</h1>"})
        municipal_site.ingest_municipality(
            self.connection,
            _municipality(
                slug="big-city",
                name="Big City",
                population=500_000,
                procurement_url="https://big.example.ca/tenders",
            ),
            client,
        )

        ranked = municipal_site.parser_needed(self.connection)

        self.assertEqual(ranked[0]["name"], "Big City")

    def test_the_platform_guardrail_holds_during_ingestion(self) -> None:
        client, _ = _client({})

        with self.assertRaises(fetcher.BlockedHostError):
            municipal_site.ingest_municipality(
                self.connection,
                _municipality(
                    procurement_url="https://muskoka.bidsandtenders.ca/Module/Tenders/en"
                ),
                client,
            )


if __name__ == "__main__":
    unittest.main()
