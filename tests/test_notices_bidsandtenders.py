import unittest
from pathlib import Path
from urllib.parse import urlparse

import requests

from notices import bidsandtenders as bt
from notices import db


FIXTURES = Path(__file__).resolve().parent / "fixtures"
STATIC_ROWS = FIXTURES / "bidsandtenders_static_rows.html"
LIVE_LISTING = FIXTURES / "bidsandtenders_listing_live.html"
PAGE = {
    "buyer_name": "Municipality of Kincardine",
    "url": "https://kincardine.bidsandtenders.ca/Module/Tenders/en",
    "region": "ON",
}


class _FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


class _FakeResponse:
    def __init__(self, text: str, error: Exception | None = None) -> None:
        self.text = text
        self._error = error

    def raise_for_status(self) -> None:
        if self._error is not None:
            raise self._error


class _FakeSession:
    def __init__(self, text: str = "", error: Exception | None = None) -> None:
        self.text = text
        self.error = error
        self.calls: list[dict] = []

    def get(self, url: str, **kwargs):
        self.calls.append({"url": url, **kwargs})
        if self.error is not None:
            raise self.error
        return _FakeResponse(self.text)


class RateLimiterTests(unittest.TestCase):
    def test_the_first_request_is_immediate(self) -> None:
        clock = _FakeClock()
        limiter = bt.RateLimiter(clock=clock.time, sleeper=clock.sleep)

        self.assertEqual(limiter.wait(), 0.0)
        self.assertEqual(clock.slept, [])

    def test_consecutive_requests_are_spaced_by_five_seconds(self) -> None:
        clock = _FakeClock()
        limiter = bt.RateLimiter(clock=clock.time, sleeper=clock.sleep)

        limiter.wait()
        slept = limiter.wait()

        self.assertEqual(slept, 5.0)
        self.assertEqual(clock.slept, [5.0])

    def test_time_already_spent_elsewhere_counts_toward_the_interval(self) -> None:
        clock = _FakeClock()
        limiter = bt.RateLimiter(clock=clock.time, sleeper=clock.sleep)

        limiter.wait()
        clock.now += 4.0
        slept = limiter.wait()

        self.assertEqual(slept, 1.0)

    def test_no_sleep_is_needed_once_the_interval_has_passed(self) -> None:
        clock = _FakeClock()
        limiter = bt.RateLimiter(clock=clock.time, sleeper=clock.sleep)

        limiter.wait()
        clock.now += 30.0

        self.assertEqual(limiter.wait(), 0.0)

    def test_an_interval_below_the_floor_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            bt.RateLimiter(min_interval=1.0)

        self.assertEqual(bt.MIN_REQUEST_INTERVAL_SECONDS, 5.0)


class ListingRegistryTests(unittest.TestCase):
    def test_every_registered_page_is_a_public_https_listing_url(self) -> None:
        self.assertTrue(bt.LISTING_PAGES)
        for page in bt.LISTING_PAGES:
            url = page["url"]
            with self.subTest(url=url):
                self.assertTrue(url.startswith("https://"))
                self.assertTrue(
                    urlparse(url).hostname.endswith("bidsandtenders.ca")
                )
                self.assertTrue(url.endswith("/Module/Tenders/en"))
                self.assertTrue(page["buyer_name"])

    def test_no_registered_page_points_at_an_authenticated_area(self) -> None:
        forbidden = ("login", "signin", "account", "submission", "vendor", "detail")
        for page in bt.LISTING_PAGES:
            with self.subTest(url=page["url"]):
                lowered = page["url"].casefold()
                self.assertFalse(any(part in lowered for part in forbidden))

    def test_the_user_agent_identifies_the_bot_honestly(self) -> None:
        self.assertEqual(bt.USER_AGENT, "TenderSentryBot")


class ParseListingTests(unittest.TestCase):
    def test_static_rows_become_metadata_only_records(self) -> None:
        html = STATIC_ROWS.read_text(encoding="utf-8")

        records = bt.parse_listing_html(html, PAGE)

        self.assertEqual(len(records), 2)
        first = records[0]
        self.assertEqual(first["title"], "Chatsworth Road Reconstruction")
        self.assertEqual(
            first["source_id"],
            "kincardine:6f2c8a41-0e21-4f8b-9a0d-71b0c4f8e3aa",
        )
        self.assertEqual(first["closing_date"], "2026-08-18T14:00:00")
        self.assertEqual(first["buyer_name"], "Municipality of Kincardine")
        self.assertEqual(first["buyer_type"], "municipal")
        self.assertEqual(first["region"], "ON")
        self.assertEqual(first["category_normalized"], "construction")
        self.assertEqual(
            first["notice_url"],
            "https://kincardine.bidsandtenders.ca/Module/Tenders/en/Tender/Detail/"
            "6f2c8a41-0e21-4f8b-9a0d-71b0c4f8e3aa",
        )

    def test_a_query_string_detail_id_is_understood(self) -> None:
        records = bt.parse_listing_html(STATIC_ROWS.read_text(encoding="utf-8"), PAGE)

        self.assertEqual(
            records[1]["source_id"],
            "kincardine:9c1d77e2-5a3b-4d10-88f0-2b6e9a45c7d1",
        )
        self.assertEqual(records[1]["closing_date"], "2026-09-02T00:00:00")

    def test_repeated_rows_login_links_and_filler_rows_are_ignored(self) -> None:
        records = bt.parse_listing_html(STATIC_ROWS.read_text(encoding="utf-8"), PAGE)

        titles = [record["title"] for record in records]
        self.assertNotIn("Login to view your submissions", titles)
        self.assertEqual(len(titles), len(set(titles)))

    def test_no_record_ever_claims_open_documents_or_a_value(self) -> None:
        for record in bt.parse_listing_html(
            STATIC_ROWS.read_text(encoding="utf-8"), PAGE
        ):
            self.assertFalse(record["documents_open"])
            self.assertIsNone(record["estimated_value"])
            self.assertIsNone(record["description"])

    def test_the_live_javascript_rendered_page_yields_nothing(self) -> None:
        html = LIVE_LISTING.read_text(encoding="utf-8")

        self.assertIn("repeater-canvas", html)
        self.assertEqual(bt.parse_listing_html(html, PAGE), [])

    def test_empty_html_is_survivable(self) -> None:
        self.assertEqual(bt.parse_listing_html("", PAGE), [])


class IngestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = db.connect(":memory:")
        self.addCleanup(self.connection.close)
        clock = _FakeClock()
        self.limiter = bt.RateLimiter(clock=clock.time, sleeper=clock.sleep)
        self.clock = clock

    def test_a_javascript_rendered_page_is_reported_not_worked_around(self) -> None:
        session = _FakeSession(LIVE_LISTING.read_text(encoding="utf-8"))

        with self.assertLogs("notices.bidsandtenders", level="WARNING"):
            result = bt.ingest(
                self.connection, [PAGE], session=session, limiter=self.limiter
            )

        self.assertEqual(result["parsed"], 0)
        self.assertEqual(result["inserted"], 0)
        self.assertEqual(result["notes"], [f"{bt.NO_DATA_NOTE}: {PAGE['url']}"])
        self.assertEqual(db.count_by_source(self.connection), {})

    def test_static_rows_are_stored_and_re_ingestion_is_idempotent(self) -> None:
        session = _FakeSession(STATIC_ROWS.read_text(encoding="utf-8"))

        first = bt.ingest(
            self.connection, [PAGE], session=session, limiter=self.limiter
        )
        second = bt.ingest(
            self.connection, [PAGE], session=session, limiter=self.limiter
        )

        self.assertEqual(first["inserted"], 2)
        self.assertEqual(second["unchanged"], 2)
        self.assertEqual(second["inserted"], 0)

    def test_every_request_is_rate_limited_and_identifies_itself(self) -> None:
        session = _FakeSession(STATIC_ROWS.read_text(encoding="utf-8"))
        pages = [PAGE, {**PAGE, "url": "https://orillia.bidsandtenders.ca/Module/Tenders/en"}]

        bt.ingest(self.connection, pages, session=session, limiter=self.limiter)

        self.assertEqual(len(session.calls), 2)
        self.assertEqual(self.clock.slept, [5.0])
        for call in session.calls:
            self.assertEqual(call["headers"]["User-Agent"], "TenderSentryBot")

    def test_a_failed_fetch_is_noted_and_does_not_stop_the_run(self) -> None:
        session = _FakeSession(error=requests.RequestException("boom"))

        result = bt.ingest(
            self.connection, [PAGE], session=session, limiter=self.limiter
        )

        self.assertEqual(result["notes"], [f"fetch_failed: {PAGE['url']}"])

    def test_a_non_https_page_is_never_requested(self) -> None:
        session = _FakeSession(STATIC_ROWS.read_text(encoding="utf-8"))

        result = bt.ingest(
            self.connection,
            [{**PAGE, "url": "http://kincardine.bidsandtenders.ca/Module/Tenders/en"}],
            session=session,
            limiter=self.limiter,
        )

        self.assertEqual(session.calls, [])
        self.assertIn("skipped non-https listing url", result["notes"][0])


if __name__ == "__main__":
    unittest.main()
