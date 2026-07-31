"""Polite HTTP for the census: robots, per-host rate limiting, platform blocklist.

Three hard rules are enforced here rather than left to callers, because a rule that
lives in a helper is a rule that gets forgotten at the one call site that matters:

1. No request is ever made to a procurement platform host, even when a municipal
   page links to one. Attempting it raises.
2. robots.txt is fetched once per host and obeyed; a disallowed path is not fetched.
3. At least ``MIN_REQUEST_INTERVAL_SECONDS`` passes between requests to the same
   host, measured across threads.
"""

from __future__ import annotations

import logging
import threading
import time
import urllib.robotparser
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlparse

import requests


LOGGER = logging.getLogger(__name__)

USER_AGENT = "TenderSentryBot"
MIN_REQUEST_INTERVAL_SECONDS = 5.0
REQUEST_TIMEOUT_SECONDS = 25
MAX_RESPONSE_BYTES = 3_000_000

#: Procurement platforms. Their listings are gated and their terms prohibit
#: scraping, so this census never touches them — it only records that a
#: municipality has sent its tenders there.
PLATFORM_HOSTS = (
    "bidsandtenders.ca",
    "bidsandtenders.com",
    "biddingo.com",
    "bidnetdirect.ca",
    "bidnetdirect.com",
    "merx.com",
    "bonfirehub.ca",
    "bonfirehub.com",
    "gobonfire.com",
    "ariba.com",
    "jaggaer.com",
    "questcdn.com",
    "ionwave.net",
    "procurementsolutions.ca",
)


class BlockedHostError(RuntimeError):
    """Raised when something tries to fetch a procurement platform."""


@dataclass
class FetchResult:
    """The outcome of one fetch, successful or not."""

    url: str
    final_url: str | None = None
    status: int | None = None
    text: str = ""
    error: str | None = None
    robots_ok: bool = True

    @property
    def ok(self) -> bool:
        return self.error is None and self.status is not None and 200 <= self.status < 300


def host_of(url: str) -> str:
    """Return the lowercase hostname of a URL, empty when unparseable."""
    return (urlparse(str(url or "")).hostname or "").casefold()


def is_platform_url(url: str) -> bool:
    """Whether a URL belongs to a procurement platform we must not touch."""
    host = host_of(url)
    return any(host == entry or host.endswith(f".{entry}") for entry in PLATFORM_HOSTS)


def platform_name(url: str) -> str | None:
    """Return which platform a URL belongs to, if any."""
    host = host_of(url)
    for entry in PLATFORM_HOSTS:
        if host == entry or host.endswith(f".{entry}"):
            return entry
    return None


class RateLimiter:
    """Enforce a minimum interval between requests to the same host."""

    def __init__(
        self,
        min_interval: float = MIN_REQUEST_INTERVAL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if min_interval < MIN_REQUEST_INTERVAL_SECONDS:
            raise ValueError(
                f"Municipal sites must be polled no faster than every "
                f"{MIN_REQUEST_INTERVAL_SECONDS}s per host"
            )
        self.min_interval = float(min_interval)
        self._clock = clock
        self._sleeper = sleeper
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, host: str) -> float:
        """Sleep until the next request to ``host`` is allowed; return seconds slept."""
        with self._lock:
            now = self._clock()
            previous = self._last.get(host)
            remaining = 0.0 if previous is None else self.min_interval - (now - previous)
            if remaining <= 0:
                self._last[host] = now
                return 0.0
            # Reserve the slot before releasing the lock so a second thread on the
            # same host queues behind this request rather than racing it.
            self._last[host] = now + remaining

        self._sleeper(remaining)
        return remaining


class PoliteFetcher:
    """A rate-limited, robots-respecting fetcher that refuses platform hosts."""

    def __init__(
        self,
        limiter: RateLimiter | None = None,
        session: Any | None = None,
        timeout: int = REQUEST_TIMEOUT_SECONDS,
        user_agent: str = USER_AGENT,
    ) -> None:
        self.limiter = limiter or RateLimiter()
        self.session = session or requests
        self.timeout = timeout
        self.user_agent = user_agent
        self._robots: dict[str, Any] = {}
        self._robots_lock = threading.Lock()
        self.request_count = 0

    def robots_allows(self, url: str) -> tuple[bool, str]:
        """Whether robots.txt permits fetching a URL, with a human-readable note."""
        host = host_of(url)
        if not host:
            return False, "unparseable url"
        parser = self._robots_for(url)
        if parser is None:
            return True, "no robots.txt"
        allowed = parser.can_fetch(self.user_agent, url)
        return allowed, "robots.txt allows" if allowed else "robots.txt disallows"

    def get(self, url: str, check_robots: bool = True) -> FetchResult:
        """Fetch one URL politely, returning a result rather than raising on HTTP."""
        if is_platform_url(url):
            raise BlockedHostError(
                f"Refusing to fetch procurement platform host: {host_of(url)}"
            )

        if check_robots:
            allowed, note = self.robots_allows(url)
            if not allowed:
                LOGGER.info("Skipping %s: %s", url, note)
                return FetchResult(url=url, error=note, robots_ok=False)

        host = host_of(url)
        self.limiter.wait(host)
        try:
            response = self.session.get(
                url,
                headers={"User-Agent": self.user_agent},
                timeout=self.timeout,
                allow_redirects=True,
            )
        except requests.RequestException as exc:
            LOGGER.info("Fetch failed for %s: %s", url, exc)
            return FetchResult(url=url, error=str(exc)[:200])

        self.request_count += 1
        final_url = str(getattr(response, "url", url) or url)
        if is_platform_url(final_url):
            # A municipal page can redirect straight onto a platform; that is a
            # classification signal, not something to read.
            return FetchResult(
                url=url,
                final_url=final_url,
                status=int(getattr(response, "status_code", 0) or 0),
                error="redirected to a procurement platform",
            )

        text = str(getattr(response, "text", "") or "")
        return FetchResult(
            url=url,
            final_url=final_url,
            status=int(getattr(response, "status_code", 0) or 0),
            text=text[:MAX_RESPONSE_BYTES],
        )

    def _robots_for(self, url: str) -> Any | None:
        host = host_of(url)
        with self._robots_lock:
            if host in self._robots:
                return self._robots[host]

        parsed = urlparse(url)
        robots_url = f"{parsed.scheme or 'https'}://{parsed.netloc}/robots.txt"
        parser: Any | None = None
        self.limiter.wait(host)
        try:
            response = self.session.get(
                robots_url,
                headers={"User-Agent": self.user_agent},
                timeout=self.timeout,
                allow_redirects=True,
            )
            self.request_count += 1
            status = int(getattr(response, "status_code", 0) or 0)
            if 200 <= status < 300:
                parser = urllib.robotparser.RobotFileParser()
                parser.parse(str(getattr(response, "text", "") or "").splitlines())
            else:
                # No robots.txt is permission by convention.
                parser = None
        except requests.RequestException as exc:
            LOGGER.debug("Could not read robots.txt for %s: %s", host, exc)
            parser = None

        with self._robots_lock:
            self._robots[host] = parser
        return parser
