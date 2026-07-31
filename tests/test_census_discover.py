import unittest

from census import discover, fetcher


BASE = "https://www.town.example.ca/"


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


class ScoringTests(unittest.TestCase):
    def test_procurement_words_score_above_the_threshold(self) -> None:
        self.assertGreaterEqual(
            discover.score_link("/township-hall/bids-and-tenders/", "Bids and Tenders"),
            discover.STRONG_SCORE,
        )
        self.assertGreaterEqual(
            discover.score_link("/business/procurement", "Procurement"),
            discover.MINIMUM_SCORE,
        )

    def test_the_real_paths_from_four_ontario_sites_all_score(self) -> None:
        # None of these is a path anyone would have guessed.
        for href, text in (
            ("/township-hall/bids-and-tenders/", "Bids and Tenders"),
            ("/our-services/bids-and-tenders/", "Bids and Tenders"),
            (
                "/build-and-invest/business-opportunities-and-resources/procurement/",
                "Procurement",
            ),
            ("/government/budget-finances-purchasing/bids-tenders-contracts", "Bids"),
        ):
            with self.subTest(href=href):
                self.assertGreaterEqual(
                    discover.score_link(href, text), discover.MINIMUM_SCORE
                )

    def test_budget_and_policy_links_are_pushed_below_the_real_one(self) -> None:
        # Grey County's homepage shows five budget links before the tenders page.
        budget = discover.score_link(
            "/government/budget-finances-purchasing/annual-budget", "2025 Budget"
        )
        tenders = discover.score_link(
            "/government/budget-finances-purchasing/bids-tenders-contracts",
            "Bids, Tenders and Contracts",
        )

        self.assertLess(budget, tenders)

    def test_careers_and_news_do_not_qualify(self) -> None:
        self.assertLess(discover.score_link("/careers/job-postings", "Jobs"), discover.MINIMUM_SCORE)
        self.assertLess(discover.score_link("/news/latest", "News"), discover.MINIMUM_SCORE)


class HarvestTests(unittest.TestCase):
    def test_links_are_scored_resolved_and_ordered(self) -> None:
        html = """
        <a href="/news">News</a>
        <a href="/business/procurement">Procurement</a>
        <a href="/township-hall/bids-and-tenders/">Bids and Tenders</a>
        """

        links = discover.harvest_links(html, BASE)

        self.assertEqual(
            links[0]["url"], "https://www.town.example.ca/township-hall/bids-and-tenders/"
        )
        self.assertTrue(all(item["score"] >= discover.MINIMUM_SCORE for item in links))
        self.assertNotIn("/news", [item["url"] for item in links])

    def test_platform_links_are_flagged_rather_than_followed(self) -> None:
        html = '<a href="https://town.bidsandtenders.ca/Module/Tenders/en">Bid opportunities</a>'

        links = discover.harvest_links(html, BASE)

        self.assertTrue(links[0]["is_platform"])
        self.assertFalse(links[0]["same_host"])

    def test_offsite_links_are_marked(self) -> None:
        html = '<a href="https://other.example.org/tenders">Tenders</a>'

        links = discover.harvest_links(html, BASE)

        self.assertFalse(links[0]["same_host"])


class FindProcurementPageTests(unittest.TestCase):
    def test_the_homepage_link_is_followed(self) -> None:
        client, session = _client(
            {
                BASE: '<a href="/township-hall/bids-and-tenders/">Bids and Tenders</a>',
                "https://www.town.example.ca/township-hall/bids-and-tenders/": "<h1>Tenders</h1>",
            }
        )

        found = discover.find_procurement_page(client, BASE)

        self.assertIsNotNone(found["page"])
        self.assertEqual(
            found["page"]["url"],
            "https://www.town.example.ca/township-hall/bids-and-tenders/",
        )

    def test_a_platform_link_on_the_homepage_short_circuits_without_fetching_it(self) -> None:
        client, session = _client(
            {BASE: '<a href="https://town.bidsandtenders.ca/x">Bid opportunities</a>'}
        )

        found = discover.find_procurement_page(client, BASE)

        self.assertIsNone(found["page"])
        self.assertIn("bidsandtenders", found["platform_link"])
        self.assertNotIn("https://town.bidsandtenders.ca/x", session.calls)

    def test_a_hub_page_is_hopped_through_to_the_real_listing(self) -> None:
        client, _ = _client(
            {
                BASE: '<a href="/doing-business">Doing Business</a>',
                "https://www.town.example.ca/doing-business": (
                    '<a href="/doing-business/tenders">Tenders and Bid Opportunities</a>'
                ),
                "https://www.town.example.ca/doing-business/tenders": "<h1>Open tenders</h1>",
            }
        )

        found = discover.find_procurement_page(client, BASE)

        self.assertEqual(
            found["page"]["url"], "https://www.town.example.ca/doing-business/tenders"
        )

    def test_fallback_paths_are_tried_only_when_the_homepage_says_nothing(self) -> None:
        client, session = _client(
            {
                BASE: "<a href='/parks'>Parks</a>",
                "https://www.town.example.ca/tenders": "<h1>Tender notices</h1>",
            }
        )

        found = discover.find_procurement_page(client, BASE)

        self.assertEqual(found["page"]["url"], "https://www.town.example.ca/tenders")
        self.assertIn("https://www.town.example.ca/tenders", session.calls)

    def test_a_site_with_nothing_procurement_reports_none_found(self) -> None:
        client, _ = _client({BASE: "<a href='/parks'>Parks</a>"})

        found = discover.find_procurement_page(client, BASE)

        self.assertIsNone(found["page"])
        self.assertIn("no procurement link", found["note"])

    def test_a_robots_disallowed_site_is_reported_and_left_alone(self) -> None:
        client, session = _client(
            {
                "https://www.town.example.ca/robots.txt": "User-agent: *\nDisallow: /\n",
                BASE: "<a href='/tenders'>Tenders</a>",
            }
        )

        found = discover.find_procurement_page(client, BASE)

        self.assertFalse(found["robots_ok"])
        self.assertIsNone(found["page"])
        self.assertNotIn(BASE, session.calls)

    def test_an_unreachable_homepage_is_reported_not_raised(self) -> None:
        client, _ = _client({})

        found = discover.find_procurement_page(client, BASE)

        self.assertIsNone(found["page"])
        self.assertIn("404", str(found["note"]))

    def test_discovery_stays_within_a_handful_of_requests(self) -> None:
        client, session = _client(
            {
                BASE: '<a href="/township-hall/bids-and-tenders/">Bids and Tenders</a>',
                "https://www.town.example.ca/township-hall/bids-and-tenders/": "<h1>Tenders</h1>",
            }
        )

        discover.find_procurement_page(client, BASE)

        # robots.txt, homepage, procurement page.
        self.assertLessEqual(len(session.calls), 3)


if __name__ == "__main__":
    unittest.main()
