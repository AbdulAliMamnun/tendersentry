"""Decide how a municipality publishes its tenders, from one procurement page.

Decision order matters. A platform marker beats everything, because a page can list
its own procurement policy in PDF while sending actual tenders to a gated platform —
Kincardine does exactly that, and a naive document count calls it an open poster.
Where the evidence is that ambiguous the class still lands on ``own_site_open`` but
with ``confidence = low``, so Phase B can process the confident ones first and treat
the rest as "verify by parsing" rather than fact.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from census import fetcher, schema


LOGGER = logging.getLogger(__name__)

#: Host fragment -> classification, checked against links and raw HTML.
PLATFORM_CLASSES = (
    ("bidsandtenders.ca", schema.CLASS_BIDS_AND_TENDERS, "bidsandtenders"),
    ("bidsandtenders.com", schema.CLASS_BIDS_AND_TENDERS, "bidsandtenders"),
    ("biddingo.com", schema.CLASS_BIDDINGO, "biddingo"),
    ("biddingo", schema.CLASS_BIDDINGO, "biddingo"),
    ("bidnetdirect", schema.CLASS_OTHER_PLATFORM, "bidnet"),
    ("merx.com", schema.CLASS_OTHER_PLATFORM, "merx"),
    ("bonfirehub", schema.CLASS_OTHER_PLATFORM, "bonfire"),
    ("gobonfire", schema.CLASS_OTHER_PLATFORM, "bonfire"),
    ("ariba.com", schema.CLASS_OTHER_PLATFORM, "ariba"),
    ("jaggaer", schema.CLASS_OTHER_PLATFORM, "jaggaer"),
    ("questcdn", schema.CLASS_OTHER_PLATFORM, "questcdn"),
    ("ionwave", schema.CLASS_OTHER_PLATFORM, "ionwave"),
)

DOCUMENT_EXTENSIONS = (".pdf", ".doc", ".docx", ".zip", ".xls", ".xlsx")

#: A document that looks like a tender package: "T-2026-31", "RFP 2026-04",
#: "ITT-2026-1", or plain words like tender/quotation in the name or link text.
TENDER_DOC_PATTERN = re.compile(
    r"(?:(?<![a-z])(?:t|q|rfp|rfq|rft|itt|itb|cr|pw)[-_ ]?\d{2,4}[-_ ]?\d{1,4})"
    r"|tender|request for proposal|request for quotation|quotation"
    r"|invitation to (?:bid|tender)|bid (?:package|document|opportunit)",
    re.IGNORECASE,
)

#: A document that is procurement *about* procurement, not a live opportunity.
POLICY_DOC_PATTERN = re.compile(
    r"policy|policies|by[-_ ]?law|terms and conditions|guideline|procedure"
    r"|standard|report|minutes|agenda|form|checklist|insurance|safety|manual"
    r"|code of conduct|strategy|plan\b",
    re.IGNORECASE,
)

#: Language that means the real opportunities live behind a login. Every phrase must
#: carry procurement context: a bare "register to" also matches the "Register to Vote"
#: link in a municipal site's navigation.
GATING_PATTERN = re.compile(
    r"create an? (?:vendor|supplier|bidder)? ?account"
    r"|vendor registration|supplier registration"
    r"|register (?:to bid|as a (?:vendor|supplier|bidder))"
    r"|registered vendors|bidding system|log ?in to view"
    r"|must be registered to (?:bid|submit)|sign in to (?:view|bid)"
    r"|plan ?takers|vendor portal|bidder portal",
    re.IGNORECASE,
)

#: Enough live-looking documents to call an open poster confidently.
CONFIDENT_TENDER_DOCS = 2

#: Above this many tender documents the page is self-evidently an open poster, and
#: registration language elsewhere on it no longer casts doubt.
OVERWHELMING_TENDER_DOCS = 5

CMS_FINGERPRINTS = (
    ("esolutionsgroup", "esolutionsgroup"),
    ("escribe", "escribe"),
    ("civicplus", "civicplus"),
    ("civiclive", "civiclive"),
    ("granicus", "granicus"),
    ("wordpress", "wordpress"),
    ("drupal", "drupal"),
    ("squarespace", "squarespace"),
    ("/media/", "media-hash-cms"),
)


def detect_platform(html: str, page_url: str = "") -> tuple[str, str] | None:
    """Return (classification, platform) when a page points at a platform."""
    soup = BeautifulSoup(html or "", "html.parser")
    for anchor in soup.find_all("a", href=True):
        resolved = urljoin(page_url, str(anchor.get("href", "")))
        for fragment, classification, platform in PLATFORM_CLASSES:
            if fragment in resolved.casefold():
                return classification, platform

    lowered = (html or "").casefold()
    for fragment, classification, platform in PLATFORM_CLASSES:
        if fragment in lowered:
            return classification, platform
    return None


def collect_documents(html: str, page_url: str) -> list[dict]:
    """Return downloadable document links with their anchor text."""
    soup = BeautifulSoup(html or "", "html.parser")
    documents: dict[str, dict] = {}
    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href", "")).strip()
        if not href:
            continue
        resolved = urljoin(page_url, href)
        path = urlparse(resolved).path.casefold()
        if not path.endswith(DOCUMENT_EXTENSIONS):
            continue
        if fetcher.is_platform_url(resolved):
            continue
        text = anchor.get_text(" ", strip=True)
        documents.setdefault(
            resolved,
            {
                "url": resolved,
                "text": text,
                "filename": urlparse(resolved).path.rsplit("/", 1)[-1],
            },
        )
    for document in documents.values():
        haystack = f"{document['filename']} {document['text']}"
        document["is_tender"] = bool(TENDER_DOC_PATTERN.search(haystack))
        document["is_policy"] = bool(POLICY_DOC_PATTERN.search(haystack))
    return list(documents.values())


def detect_cms(html: str) -> str | None:
    """Best-effort CMS fingerprint, used to group Phase B parsers."""
    lowered = (html or "").casefold()
    for fragment, label in CMS_FINGERPRINTS:
        if fragment in lowered:
            return label
    return None


def classify_page(html: str, page_url: str) -> dict:
    """Classify one procurement page and explain the verdict."""
    platform = detect_platform(html, page_url)
    if platform is not None:
        classification, name = platform
        return {
            "classification": classification,
            "platform": name,
            "confidence": schema.CONFIDENCE_HIGH,
            "evidence_note": f"page references {name}",
            "documents": [],
            "cms_fingerprint": detect_cms(html),
        }

    documents = collect_documents(html, page_url)
    tenders = [item for item in documents if item["is_tender"]]
    policies = [item for item in documents if item["is_policy"] and not item["is_tender"]]
    gated = bool(GATING_PATTERN.search(_text_of(html)))

    if tenders:
        confident = len(tenders) >= CONFIDENT_TENDER_DOCS and len(tenders) > len(
            policies
        )
        # Registration language only casts doubt while the document evidence is thin.
        doubtful = gated and len(tenders) < OVERWHELMING_TENDER_DOCS
        return {
            "classification": schema.CLASS_OWN_SITE_OPEN,
            "platform": None,
            "confidence": (
                schema.CONFIDENCE_HIGH
                if confident and not doubtful
                else schema.CONFIDENCE_LOW
            ),
            "evidence_note": (
                f"{len(tenders)} tender-patterned document(s), "
                f"{len(policies)} policy-shaped"
                + (", vendor registration language present" if gated else "")
            ),
            "documents": tenders,
            "cms_fingerprint": detect_cms(html),
        }

    if documents:
        # Documents exist but none look like a live opportunity: a procurement
        # policy page, not an open poster.
        return {
            "classification": schema.CLASS_OWN_SITE_NOTICES,
            "platform": None,
            "confidence": schema.CONFIDENCE_LOW,
            "evidence_note": (
                f"{len(documents)} document(s), none tender-patterned"
                + (", vendor registration language present" if gated else "")
            ),
            "documents": [],
            "cms_fingerprint": detect_cms(html),
        }

    if _mentions_opportunities(html):
        return {
            "classification": schema.CLASS_OWN_SITE_NOTICES,
            "platform": None,
            "confidence": schema.CONFIDENCE_LOW,
            "evidence_note": "notices described, no downloadable documents found"
            + (", vendor registration language present" if gated else ""),
            "documents": [],
            "cms_fingerprint": detect_cms(html),
        }

    return {
        "classification": schema.CLASS_NONE_FOUND,
        "platform": None,
        "confidence": None,
        "evidence_note": "page carried no tender notices or documents",
        "documents": [],
        "cms_fingerprint": detect_cms(html),
    }


def _mentions_opportunities(html: str) -> bool:
    text = _fold(_text_of(html))
    return any(
        term in text
        for term in (
            "tender",
            "request for proposal",
            "request for quotation",
            "bid opportunit",
            "current opportunit",
            "closing date",
        )
    )


def _text_of(html: str) -> str:
    without_scripts = re.sub(r"<(script|style).*?</\1>", " ", html or "", flags=re.S | re.I)
    return re.sub(r"<[^>]+>", " ", without_scripts)


def _fold(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).casefold()
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text)
