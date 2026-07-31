"""Find a municipality's procurement page by reading its homepage.

Path guessing was the obvious approach and it does not work: of four Ontario sites
checked by hand, none used /tenders, /bids, /procurement or /rfp, yet all four linked
their procurement page from the homepage. So links are harvested and scored, and the
fixed paths survive only as a fallback for sites whose homepage says nothing.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Iterable
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from census import fetcher


LOGGER = logging.getLogger(__name__)

#: Terms that suggest a procurement page, and what each is worth. Weighted so that
#: "bids-tenders-contracts" beats the five "budget-finances-purchasing" links Grey
#: County shows first.
KEYWORD_WEIGHTS = (
    ("bids and tenders", 14),
    ("bids-and-tenders", 14),
    ("bid opportunit", 12),
    ("tender", 10),
    ("request for proposal", 9),
    ("request for quotation", 9),
    (" rfp", 8),
    ("rfp", 6),
    ("rfq", 6),
    ("procurement", 7),
    ("bidding", 6),
    ("bid", 4),
    ("purchasing", 3),
    ("business opportunit", 5),
    # Worth following as a hub, not as an answer: some municipalities file tenders
    # only under "Doing Business", so it must clear MINIMUM_SCORE while staying
    # below STRONG_SCORE so the hub hop still runs.
    ("doing business", 5),
    ("supplier", 3),
    ("vendor", 3),
)

#: Terms that mean a link is something else wearing similar words.
NEGATIVE_WEIGHTS = (
    ("budget", -6),
    ("by-law", -5),
    ("bylaw", -5),
    ("policy", -4),
    ("policies", -4),
    ("minutes", -5),
    ("agenda", -5),
    ("council", -3),
    ("career", -6),
    ("employment", -6),
    ("job", -5),
    ("news", -4),
    ("surplus", -3),
    ("auction", -3),
    ("tax", -3),
    ("forbidden", -10),
)

#: Consulted only when the homepage yields nothing.
FALLBACK_PATHS = (
    "/tenders",
    "/bids",
    "/procurement",
    "/bids-and-tenders",
    "/business/tenders",
    "/rfp",
)

#: A link must score at least this to be worth a request.
MINIMUM_SCORE = 4

#: Pages scoring at least this are treated as the procurement page itself rather
#: than a hub worth hopping through.
STRONG_SCORE = 10


def score_link(href: str, text: str) -> int:
    """Score how likely a link is to lead to tender notices."""
    haystack = f" {_fold(href)} {_fold(text)} "
    score = 0
    for term, weight in KEYWORD_WEIGHTS:
        if term in haystack:
            score += weight
    for term, weight in NEGATIVE_WEIGHTS:
        if term in haystack:
            score += weight
    return score


def harvest_links(html: str, base_url: str) -> list[dict]:
    """Return scored, deduplicated candidate links from a page, best first."""
    soup = BeautifulSoup(html or "", "html.parser")
    candidates: dict[str, dict] = {}
    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href", "")).strip()
        if not href or href.lower().startswith(("javascript:", "mailto:", "tel:", "#")):
            continue
        resolved = urljoin(base_url, href)
        parsed = urlparse(resolved)
        if parsed.scheme not in {"http", "https"}:
            continue
        text = anchor.get_text(" ", strip=True)
        score = score_link(href, text)
        if score < MINIMUM_SCORE:
            continue
        # A link straight to a platform is the answer, not a page to fetch.
        existing = candidates.get(resolved)
        if existing is None or score > existing["score"]:
            candidates[resolved] = {
                "url": resolved,
                "text": text,
                "score": score,
                "is_platform": fetcher.is_platform_url(resolved),
                "same_host": _same_host(resolved, base_url),
            }
    ordered = sorted(
        candidates.values(), key=lambda item: (-item["score"], len(item["url"]))
    )
    return ordered


def find_procurement_page(
    client: fetcher.PoliteFetcher, website_url: str, max_hops: int = 1
) -> dict:
    """Locate a municipality's procurement page, returning the evidence trail."""
    trail: list[dict] = []
    home = client.get(website_url)
    trail.append(
        {
            "url": website_url,
            "status": home.status,
            "error": home.error,
            "stage": "homepage",
        }
    )
    if not home.robots_ok:
        return {
            "page": None,
            "robots_ok": False,
            "trail": trail,
            "note": home.error or "robots.txt disallows",
        }
    if not home.ok:
        return {
            "page": None,
            "robots_ok": True,
            "trail": trail,
            "note": home.error or f"homepage returned {home.status}",
        }

    base = home.final_url or website_url
    candidates = harvest_links(home.text, base)

    platform_links = [item for item in candidates if item["is_platform"]]
    if platform_links:
        # The homepage links a platform directly: classification without a fetch.
        return {
            "page": None,
            "robots_ok": True,
            "trail": trail,
            "platform_link": platform_links[0]["url"],
            "note": "homepage links a procurement platform",
        }

    followable = [
        item for item in candidates if item["same_host"] and not item["is_platform"]
    ]
    for candidate in followable[:2]:
        result = client.get(candidate["url"])
        trail.append(
            {
                "url": candidate["url"],
                "status": result.status,
                "error": result.error,
                "stage": "candidate",
                "score": candidate["score"],
            }
        )
        if result.error == "redirected to a procurement platform":
            return {
                "page": None,
                "robots_ok": True,
                "trail": trail,
                "platform_link": result.final_url,
                "note": "procurement link redirects to a platform",
            }
        if not result.ok:
            continue

        page = {
            "url": result.final_url or candidate["url"],
            "html": result.text,
            "score": candidate["score"],
        }
        if candidate["score"] >= STRONG_SCORE or max_hops <= 0:
            return {"page": page, "robots_ok": True, "trail": trail}

        # A weak-scoring hub ("Doing Business") may list the real page one hop down.
        deeper = [
            item
            for item in harvest_links(result.text, page["url"])
            if item["same_host"]
            and not item["is_platform"]
            and item["score"] > candidate["score"]
            and item["url"] != page["url"]
        ]
        if not deeper:
            return {"page": page, "robots_ok": True, "trail": trail}

        hop = client.get(deeper[0]["url"])
        trail.append(
            {
                "url": deeper[0]["url"],
                "status": hop.status,
                "error": hop.error,
                "stage": "hub-hop",
                "score": deeper[0]["score"],
            }
        )
        if hop.ok:
            return {
                "page": {
                    "url": hop.final_url or deeper[0]["url"],
                    "html": hop.text,
                    "score": deeper[0]["score"],
                },
                "robots_ok": True,
                "trail": trail,
            }
        return {"page": page, "robots_ok": True, "trail": trail}

    fallback = _try_fallback_paths(client, base, trail)
    if fallback is not None:
        return {"page": fallback, "robots_ok": True, "trail": trail}

    return {
        "page": None,
        "robots_ok": True,
        "trail": trail,
        "note": "no procurement link found on the homepage",
    }


def _try_fallback_paths(
    client: fetcher.PoliteFetcher, base_url: str, trail: list[dict]
) -> dict | None:
    """Try the conventional paths, for homepages that link nothing useful."""
    parsed = urlparse(base_url)
    root = f"{parsed.scheme}://{parsed.netloc}"
    for path in FALLBACK_PATHS:
        result = client.get(root + path)
        trail.append(
            {
                "url": root + path,
                "status": result.status,
                "error": result.error,
                "stage": "fallback-path",
            }
        )
        if result.ok and _looks_like_procurement(result.text):
            return {
                "url": result.final_url or (root + path),
                "html": result.text,
                "score": MINIMUM_SCORE,
            }
    return None


def _looks_like_procurement(html: str) -> bool:
    text = _fold(re.sub(r"<[^>]+>", " ", html or ""))
    return any(
        term in text
        for term in ("tender", "request for proposal", "bid opportunit", "procurement")
    )


def _same_host(url: str, base_url: str) -> bool:
    first = fetcher.host_of(url).removeprefix("www.")
    second = fetcher.host_of(base_url).removeprefix("www.")
    return bool(first) and first == second


def _fold(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).casefold()
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[\s_]+", " ", text.replace("-", " "))


def keywords() -> Iterable[str]:
    """The positive keyword vocabulary, for reporting and tests."""
    return (term for term, _ in KEYWORD_WEIGHTS)
