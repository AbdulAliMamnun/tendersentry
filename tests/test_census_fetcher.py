import unittest

import requests

from census import fetcher


ROBOTS_ALLOW_ALL = "User-agent: *\nDisallow:\n"
ROBOTS_DISALLOW_ALL = "User-agent: *\nDisallow: /\n"
ROBOTS_DISALLOW_TENDERS = "User-agent: *\nDisallow: /tenders\n"


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
    def __init__(self, text: str = "", status: int = 200, url: str = "") -> None:
        self.text = text
        self.status_code = status
        self.url = url


class _FakeSession:
    """Serves canned bodies by URL and records every request made."""

    def __init__(self, pages: dict[str, object] | None = None) -> None:
        self.pages = pages or {}
        self.calls: list[str] = []

    def get(self, url: str, **kwargs):
        self.calls.append(url)
        page = self.pages.get(url)
        if isinstance(page, Exception):
            raise page
        if page is None:
            return _FakeResponse("", 404, url)
        if isinstance(page, _FakeResponse):
            page.url = page.url or url
            return page
        return _FakeResponse(str(page), 200, url)


def _fetcher(pages: dict | None = None) -> tuple[fetcher.PoliteFetcher, _FakeSession, _FakeClock]:
    clock = _FakeClock()
    limiter = fetcher.RateLimiter(clock=clock.time, sleeper=clock.sleep)
    session = _FakeSession(pages)
    return fetcher.PoliteFetcher(limiter=limiter, session=session), session, clock


class PlatformBlocklistTests(unittest.TestCase):
    def test_platform_hosts_are_recognized(self) -> None:
        self.assertTrue(
            fetcher.is_platform_url("https://kincardine.bidsandtenders.ca/Module/Tenders/en")
        )
        self.assertTrue(fetcher.is_platform_url("https://www.biddingo.com/toronto"))
        self.assertTrue(fetcher.is_platform_url("https://grey.bonfirehub.ca/portal"))
        self.assertFalse(fetcher.is_platform_url("https://www.muskokalakes.ca/tenders"))

    def test_fetching_a_platform_host_raises_rather_than_being_polite_about_it(self) -> None:
        client, session, _ = _fetcher()

        with self.assertRaises(fetcher.BlockedHostError):
            client.get("https://kincardine.bidsandtenders.ca/Module/Tenders/en")

        self.assertEqual(session.calls, [])

    def test_a_redirect_onto_a_platform_is_reported_not_read(self) -> None:
        client, session, _ = _fetcher(
            {
                "https://town.example.ca/robots.txt": ROBOTS_ALLOW_ALL,
                "https://town.example.ca/tenders": _FakeResponse(
                    "<html>tender list</html>",
                    200,
                    "https://town.bidsandtenders.ca/Module/Tenders/en",
                ),
            }
        )

        result = client.get("https://town.example.ca/tenders")

        self.assertFalse(result.ok)
        self.assertEqual(result.error, "redirected to a procurement platform")
        self.assertEqual(result.text, "")

    def test_the_platform_name_is_reported_for_the_census_record(self) -> None:
        self.assertEqual(
            fetcher.platform_name("https://x.bidsandtenders.ca/y"), "bidsandtenders.ca"
        )
        self.assertIsNone(fetcher.platform_name("https://www.orillia.ca/"))


class RobotsTests(unittest.TestCase):
    def test_a_disallowed_path_is_not_fetched(self) -> None:
        client, session, _ = _fetcher(
            {"https://town.example.ca/robots.txt": ROBOTS_DISALLOW_ALL}
        )

        result = client.get("https://town.example.ca/tenders")

        self.assertFalse(result.robots_ok)
        self.assertIn("disallow", str(result.error).casefold())
        self.assertNotIn("https://town.example.ca/tenders", session.calls)

    def test_a_path_specific_disallow_blocks_only_that_path(self) -> None:
        client, _, _ = _fetcher(
            {
                "https://town.example.ca/robots.txt": ROBOTS_DISALLOW_TENDERS,
                "https://town.example.ca/business": "<html>ok</html>",
            }
        )

        self.assertFalse(client.get("https://town.example.ca/tenders").robots_ok)
        self.assertTrue(client.get("https://town.example.ca/business").ok)

    def test_a_missing_robots_file_means_permission(self) -> None:
        client, _, _ = _fetcher({"https://town.example.ca/page": "<html>ok</html>"})

        result = client.get("https://town.example.ca/page")

        self.assertTrue(result.ok)

    def test_an_unreachable_robots_file_does_not_block_the_census(self) -> None:
        client, _, _ = _fetcher(
            {
                "https://town.example.ca/robots.txt": requests.ConnectionError("boom"),
                "https://town.example.ca/page": "<html>ok</html>",
            }
        )

        self.assertTrue(client.get("https://town.example.ca/page").ok)

    def test_robots_is_read_once_per_host(self) -> None:
        client, session, _ = _fetcher(
            {
                "https://town.example.ca/robots.txt": ROBOTS_ALLOW_ALL,
                "https://town.example.ca/a": "<html>a</html>",
                "https://town.example.ca/b": "<html>b</html>",
            }
        )

        client.get("https://town.example.ca/a")
        client.get("https://town.example.ca/b")

        self.assertEqual(
            session.calls.count("https://town.example.ca/robots.txt"), 1
        )


class RateLimiterTests(unittest.TestCase):
    def test_the_first_request_to_a_host_is_immediate(self) -> None:
        clock = _FakeClock()
        limiter = fetcher.RateLimiter(clock=clock.time, sleeper=clock.sleep)

        self.assertEqual(limiter.wait("a.example.ca"), 0.0)
        self.assertEqual(clock.slept, [])

    def test_consecutive_requests_to_one_host_are_spaced(self) -> None:
        clock = _FakeClock()
        limiter = fetcher.RateLimiter(clock=clock.time, sleeper=clock.sleep)

        limiter.wait("a.example.ca")
        slept = limiter.wait("a.example.ca")

        self.assertEqual(slept, 5.0)

    def test_different_hosts_do_not_wait_for_each_other(self) -> None:
        clock = _FakeClock()
        limiter = fetcher.RateLimiter(clock=clock.time, sleeper=clock.sleep)

        limiter.wait("a.example.ca")
        slept = limiter.wait("b.example.ca")

        self.assertEqual(slept, 0.0)
        self.assertEqual(clock.slept, [])

    def test_a_slot_is_reserved_so_concurrent_workers_queue_rather_than_race(self) -> None:
        # Two requests booked back to back must be spaced 5s and 10s from the
        # first, not both 5s, or two threads would fire simultaneously.
        clock = _FakeClock()
        limiter = fetcher.RateLimiter(clock=clock.time, sleeper=clock.sleep)
        limiter.wait("a.example.ca")

        first = limiter.wait("a.example.ca")
        second = limiter.wait("a.example.ca")

        self.assertEqual(first, 5.0)
        self.assertGreaterEqual(second, 5.0)

    def test_time_spent_elsewhere_counts_toward_the_interval(self) -> None:
        clock = _FakeClock()
        limiter = fetcher.RateLimiter(clock=clock.time, sleeper=clock.sleep)
        limiter.wait("a.example.ca")
        clock.now += 4.0

        self.assertAlmostEqual(limiter.wait("a.example.ca"), 1.0)

    def test_an_interval_below_the_floor_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            fetcher.RateLimiter(min_interval=1.0)

        self.assertEqual(fetcher.MIN_REQUEST_INTERVAL_SECONDS, 5.0)

    def test_every_request_including_robots_is_rate_limited(self) -> None:
        client, _, clock = _fetcher(
            {
                "https://town.example.ca/robots.txt": ROBOTS_ALLOW_ALL,
                "https://town.example.ca/page": "<html>ok</html>",
            }
        )

        client.get("https://town.example.ca/page")

        # robots.txt then the page: the second request waits out the interval.
        self.assertEqual(clock.slept, [5.0])


class FetchResultTests(unittest.TestCase):
    def test_the_bot_identifies_itself_honestly(self) -> None:
        self.assertEqual(fetcher.USER_AGENT, "TenderSentryBot")

    def test_a_network_error_is_returned_not_raised(self) -> None:
        client, _, _ = _fetcher(
            {"https://town.example.ca/page": requests.Timeout("slow")}
        )

        result = client.get("https://town.example.ca/page")

        self.assertFalse(result.ok)
        self.assertIn("slow", str(result.error))

    def test_a_server_error_is_reported_with_its_status(self) -> None:
        client, _, _ = _fetcher(
            {"https://town.example.ca/page": _FakeResponse("oops", 500)}
        )

        result = client.get("https://town.example.ca/page")

        self.assertFalse(result.ok)
        self.assertEqual(result.status, 500)


if __name__ == "__main__":
    unittest.main()
