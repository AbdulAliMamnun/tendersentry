"""Monitor public Bids&Tenders listing pages for notice-level metadata only.

This module is deliberately the most conservative ingester in the package:

* Only the public listing page of each municipality is ever requested — never a
  login page, a vendor area, a bid document, or any authenticated URL.
* Requests are spaced at least ``MIN_REQUEST_INTERVAL_SECONDS`` apart (5s) across
  the whole run, and identify themselves honestly as ``TenderSentryBot``.
* Only notice-level metadata is parsed (title, buyer, closing date, notice URL),
  and every row is stored with ``documents_open = False``.

Status verified 2026-07-29: these listing pages are JavaScript-rendered. The served
HTML is a page shell — the opportunity grid contains no rows until a client-side
request populates it. This module therefore parses static HTML only and reports
``js_rendered_no_data`` for pages that yield nothing, by design. It does not drive a
headless browser and does not call the site's internal grid endpoint; both were
considered and explicitly ruled out. If a municipality ever serves static rows or a
public feed, the parser below will pick it up with no further changes.
"""

from __future__ import annotations

import argparse
import logging
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from notices import db
from notices.normalize import iso_timestamp, normalize_category, normalize_status


LOGGER = logging.getLogger(__name__)

SOURCE = "bidsandtenders"
USER_AGENT = "TenderSentryBot"
MIN_REQUEST_INTERVAL_SECONDS = 5.0
REQUEST_TIMEOUT_SECONDS = 30
NO_DATA_NOTE = "js_rendered_no_data"

#: Public Ontario municipal listing pages. Kept small and explicit on purpose;
#: every URL below was confirmed to serve a public listing page (HTTP 200) on
#: 2026-07-29. Municipalities that redirect to an error page were left out rather
#: than guessed at.
LISTING_PAGES: tuple[dict[str, str], ...] = (
    {
        "buyer_name": "Municipality of Kincardine",
        "url": "https://kincardine.bidsandtenders.ca/Module/Tenders/en",
        "region": "ON",
    },
    {
        "buyer_name": "Town of Saugeen Shores",
        "url": "https://saugeenshores.bidsandtenders.ca/Module/Tenders/en",
        "region": "ON",
    },
    {
        "buyer_name": "City of Orillia",
        "url": "https://orillia.bidsandtenders.ca/Module/Tenders/en",
        "region": "ON",
    },
    {
        "buyer_name": "Town of Midland",
        "url": "https://midland.bidsandtenders.ca/Module/Tenders/en",
        "region": "ON",
    },
)

DETAIL_LINK_HINT = re.compile(r"/Tender/Detail|tenderid=", re.IGNORECASE)
CLOSING_FORMATS = (
    "%B %d, %Y %I:%M:%S %p",
    "%B %d, %Y %I:%M %p",
    "%B %d, %Y",
    "%b %d, %Y %I:%M %p",
    "%b %d, %Y",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
)


class RateLimiter:
    """Enforce a minimum interval between requests, with an injectable clock."""

    def __init__(
        self,
        min_interval: float = MIN_REQUEST_INTERVAL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if min_interval < MIN_REQUEST_INTERVAL_SECONDS:
            raise ValueError(
                "Bids&Tenders requests must stay at least "
                f"{MIN_REQUEST_INTERVAL_SECONDS}s apart"
            )
        self.min_interval = float(min_interval)
        self._clock = clock
        self._sleeper = sleeper
        self._last_request: float | None = None

    def wait(self) -> float:
        """Sleep until the next request is allowed and return the seconds slept."""
        now = self._clock()
        slept = 0.0
        if self._last_request is not None:
            remaining = self.min_interval - (now - self._last_request)
            if remaining > 0:
                self._sleeper(remaining)
                slept = remaining
                now = self._clock()
        self._last_request = now
        return slept


def fetch_listing(
    url: str, limiter: RateLimiter, session: Any | None = None
) -> str | None:
    """Fetch one public listing page politely, returning None on any failure."""
    limiter.wait()
    client = session or requests
    try:
        response = client.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT_SECONDS,
            allow_redirects=True,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        LOGGER.warning("Could not fetch listing page %s: %s", url, exc)
        return None
    return response.text


def parse_listing_html(html: str, page: dict, page_url: str | None = None) -> list[dict]:
    """Parse notice-level metadata from a listing page's static HTML."""
    soup = BeautifulSoup(html or "", "html.parser")
    base_url = page_url or str(page.get("url") or "")
    host_label = _host_label(base_url)
    records: list[dict] = []
    seen: set[str] = set()

    for row in soup.find_all("tr"):
        link = next(
            (
                candidate
                for candidate in row.find_all("a", href=True)
                if DETAIL_LINK_HINT.search(str(candidate.get("href", "")))
            ),
            None,
        )
        if link is None:
            continue
        title = link.get_text(" ", strip=True)
        if not title:
            continue
        href = urljoin(base_url, str(link.get("href", "")))
        detail_id = _detail_id(href) or title
        source_id = f"{host_label}:{detail_id}" if host_label else detail_id
        if source_id in seen:
            continue
        seen.add(source_id)

        cells = [
            cell.get_text(" ", strip=True) for cell in row.find_all(["td", "th"])
        ]
        closing_date = next(
            (parsed for parsed in (_parse_closing(cell) for cell in cells) if parsed),
            None,
        )
        buyer_name = str(page.get("buyer_name") or "")
        records.append(
            {
                "source": SOURCE,
                "source_id": source_id,
                "title": title,
                # Listing pages carry no usable summary, and this module never
                # opens the detail page or its documents.
                "description": None,
                "buyer_name": buyer_name or None,
                "buyer_type": "municipal",
                "category_raw": None,
                "category_normalized": normalize_category(title=title),
                "region": str(page.get("region") or "ON"),
                "estimated_value": None,
                "currency": None,
                "closing_date": closing_date,
                "posted_date": None,
                "notice_url": href,
                "documents_open": False,
                "status": normalize_status("open", closing_date),
            }
        )
    return records


def ingest(
    connection: sqlite3.Connection | None = None,
    pages: Iterable[dict] | None = None,
    session: Any | None = None,
    limiter: RateLimiter | None = None,
) -> dict[str, Any]:
    """Fetch every registered listing page and upsert the metadata found."""
    selected = list(pages if pages is not None else LISTING_PAGES)
    limiter = limiter or RateLimiter()
    owned = connection is None
    connection = connection or db.connect()
    notes: list[str] = []
    records: list[dict] = []

    try:
        for page in selected:
            url = str(page.get("url") or "")
            if not url.lower().startswith("https://"):
                notes.append(f"skipped non-https listing url: {url or '<missing>'}")
                continue
            html = fetch_listing(url, limiter, session)
            if html is None:
                notes.append(f"fetch_failed: {url}")
                continue
            page_records = parse_listing_html(html, page, url)
            if not page_records:
                LOGGER.warning("%s: %s", NO_DATA_NOTE, url)
                notes.append(f"{NO_DATA_NOTE}: {url}")
                continue
            LOGGER.info("Parsed %d notices from %s", len(page_records), url)
            records.extend(page_records)

        tally = db.upsert_notices(connection, records)
    finally:
        if owned:
            connection.close()

    return {"source": SOURCE, "parsed": len(records), **tally, "notes": notes}


def _host_label(url: str) -> str:
    host = urlparse(url).hostname or ""
    return host.split(".")[0] if host else ""


def _detail_id(href: str) -> str:
    parsed = urlparse(href)
    query = parse_qs(parsed.query)
    for key, values in query.items():
        if key.casefold() in {"id", "tenderid"} and values:
            return str(values[0]).strip()
    segments = [segment for segment in parsed.path.split("/") if segment]
    return segments[-1] if segments else ""


def _parse_closing(text: str) -> str | None:
    """Parse a listing cell's closing date, ignoring weekday and timezone noise."""
    candidate = re.sub(r"\((?:[A-Z]{2,5})\)", "", str(text or "")).strip()
    candidate = re.sub(r"^[A-Za-z]+day,?\s*", "", candidate).strip(" ,")
    if not candidate:
        return None
    for pattern in CLOSING_FORMATS:
        try:
            return datetime.strptime(candidate, pattern).isoformat()
        except ValueError:
            continue
    return iso_timestamp(candidate) if re.search(r"\d{4}-\d{2}-\d{2}", candidate) else None


def _main() -> None:
    parser = argparse.ArgumentParser(
        description="Monitor public Bids&Tenders listing pages"
    )
    parser.add_argument("--db", default=None)
    parser.add_argument("--html", default=None, help="parse a saved listing page")
    args = parser.parse_args()

    connection = db.connect(args.db)
    try:
        if args.html:
            page = dict(LISTING_PAGES[0])
            records = parse_listing_html(Path(args.html).read_text(encoding="utf-8"), page)
            result = {
                "source": SOURCE,
                "parsed": len(records),
                **db.upsert_notices(connection, records),
                "notes": [] if records else [f"{NO_DATA_NOTE}: {args.html}"],
            }
        else:
            result = ingest(connection)
    finally:
        connection.close()

    print(
        f"{result['source']}: parsed {result['parsed']}, "
        f"inserted {result['inserted']}, updated {result['updated']}, "
        f"unchanged {result['unchanged']}, skipped {result['skipped']}"
    )
    for note in result["notes"]:
        print(f"  note: {note}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    _main()
