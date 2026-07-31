"""Normalize source-specific notice fields onto one shared vocabulary.

Normalization is deliberately lossy, so every parser also stores the source's own
wording in ``category_raw`` and its own state in ``status``'s source value. The
recommendation layer filters on the normalized columns and can always fall back
to the raw text when a mapping proves too coarse.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from datetime import datetime
from typing import Any, Iterable

import config


LOGGER = logging.getLogger(__name__)

CATEGORY_CONSTRUCTION = "construction"
CATEGORY_GOODS = "goods"
CATEGORY_SERVICES = "services"
CATEGORY_PROFESSIONAL_SERVICES = "professional_services"
CATEGORY_OTHER = "other"

CATEGORIES = (
    CATEGORY_CONSTRUCTION,
    CATEGORY_GOODS,
    CATEGORY_SERVICES,
    CATEGORY_PROFESSIONAL_SERVICES,
    CATEGORY_OTHER,
)

#: UNSPSC segments that mean construction work or structural materials.
CONSTRUCTION_UNSPSC_SEGMENTS = ("72", "30")

STATUS_OPEN = "open"
STATUS_PLANNED = "planned"
STATUS_CLOSED = "closed"
STATUS_AWARDED = "awarded"
STATUS_CANCELLED = "cancelled"
STATUS_UNKNOWN = "unknown"

PROVINCE_CODES = {
    "alberta": "AB",
    "british columbia": "BC",
    "colombie-britannique": "BC",
    "manitoba": "MB",
    "new brunswick": "NB",
    "nouveau-brunswick": "NB",
    "newfoundland and labrador": "NL",
    "newfoundland": "NL",
    "terre-neuve-et-labrador": "NL",
    "northwest territories": "NT",
    "nova scotia": "NS",
    "nouvelle-ecosse": "NS",
    "nunavut": "NU",
    "ontario": "ON",
    "prince edward island": "PE",
    "ile-du-prince-edouard": "PE",
    "quebec": "QC",
    "saskatchewan": "SK",
    "yukon": "YT",
}

PROVINCE_ABBREVIATIONS = {
    "ab", "bc", "mb", "nb", "nl", "ns", "nt", "nu", "on", "pe", "qc", "sk", "yt",
}

NATIONAL_MARKERS = ("canada", "national", "nationwide", "tout le canada")

#: Buyer-type hints, tested in order. Patterns are written against ``_fold`` output,
#: which is accent-stripped, so "Municipalité" is matched as "municipalit\w*".
BUYER_TYPE_PATTERNS = (
    (
        "education",
        r"\b(?:school board|school district|conseil scolaire|centre de services "
        r"scolaire|universit\w*|colleg\w*|college|cegep)\b",
    ),
    (
        "health",
        r"\b(?:health|hospital|hopital|sante|ciusss|cisss|hospice|chsld)\b",
    ),
    (
        "municipal",
        r"\b(?:city of|town of|township|municipalit\w*|ville de|village|"
        r"regional municipality|county of|comte|mrc|corporation of the)\b",
    ),
)


def normalize_category(
    category_raw: Any = "",
    title: Any = "",
    description: Any = "",
    classification_codes: Iterable[Any] = (),
    main_category: Any = "",
) -> str:
    """Map a source category, codes, and free text onto the shared vocabulary.

    Precedence is strict: the source's own category always wins, then a
    construction UNSPSC segment, then a construction keyword in the title or
    description. Free text only ever promotes *to* construction, and only for
    sources that publish no category at all (a Bids&Tenders listing row) — a
    tender the buyer filed as services stays services, because keyword promotion
    over CanadaBuys descriptions mislabels roughly two thirds of what it catches
    (street names matching "road", boilerplate matching "construction").
    """
    explicit = _explicit_category(f"{_fold(category_raw)} {_fold(main_category)}")
    if explicit is not None:
        return explicit
    if _has_construction_code(classification_codes):
        return CATEGORY_CONSTRUCTION
    if matched_keywords(title, description):
        return CATEGORY_CONSTRUCTION
    return CATEGORY_OTHER


def _explicit_category(combined: str) -> str | None:
    """Return the category the source itself declared, when it declared one."""
    if not combined.strip():
        return None
    if _has_construction_token(combined):
        return CATEGORY_CONSTRUCTION
    if re.search(r"services professionnels|professional services", combined):
        return CATEGORY_PROFESSIONAL_SERVICES
    # CanadaBuys packs "services related to goods" as the single token SRVTGD.
    if re.search(r"\bsrvt?gd\b|\bsrv\b|services|service", combined):
        return CATEGORY_SERVICES
    if re.search(r"\bgd\b|goods|approvisionnement|biens", combined):
        return CATEGORY_GOODS
    return None


def matched_keywords(title: Any = "", description: Any = "") -> list[str]:
    """Return the configured construction keywords present in the notice text."""
    searchable = _fold(f"{title}\n{description}")
    return [
        str(keyword)
        for keyword in config.CATEGORY_KEYWORDS
        if re.search(
            rf"(?<![a-z0-9_]){re.escape(str(keyword).casefold())}(?![a-z0-9_])",
            searchable,
        )
    ]


def normalize_status(
    status_raw: Any = "",
    closing_date: Any = None,
    now: datetime | None = None,
) -> str:
    """Map a source status onto the shared vocabulary, respecting the deadline."""
    raw = _fold(status_raw)
    status = STATUS_UNKNOWN
    if re.search(r"cancel|annul|withdraw|retir|terminat|r[ée]sili", raw):
        return STATUS_CANCELLED
    if re.search(r"award|adjug|octroy|complete|conclu", raw):
        return STATUS_AWARDED
    if re.search(r"\bplanned\b|\bplanifi", raw):
        status = STATUS_PLANNED
    elif re.search(r"open|active|ouvert|en cours|publish|publi", raw):
        status = STATUS_OPEN
    elif re.search(r"clos|ferm|expir|unsuccessful|infructueux", raw):
        return STATUS_CLOSED

    closing = parse_datetime(closing_date)
    if closing is not None and status in {STATUS_OPEN, STATUS_PLANNED, STATUS_UNKNOWN}:
        reference = now or datetime.now().astimezone()
        if not _is_future(closing, reference):
            return STATUS_CLOSED
    return status


def normalize_region(*values: Any) -> str | None:
    """Return sorted province codes found in the supplied region text."""
    text = _fold("\n".join(str(value) for value in values if value))
    if not text:
        return None

    codes: set[str] = set()
    for name, code in PROVINCE_CODES.items():
        if name in text:
            codes.add(code)
    for token in re.split(r"[^a-z]+", text):
        if token in PROVINCE_ABBREVIATIONS:
            codes.add(token.upper())
    if codes:
        return ",".join(sorted(codes))
    if any(marker in text for marker in NATIONAL_MARKERS):
        return "CA"
    return None


def normalize_buyer_type(buyer_name: Any = "", default: str = "unknown") -> str:
    """Infer a buyer type from the entity name, falling back to the default."""
    name = _fold(buyer_name)
    if not name:
        return default
    for buyer_type, pattern in BUYER_TYPE_PATTERNS:
        if re.search(pattern, name):
            return buyer_type
    return default


def parse_datetime(value: Any) -> datetime | None:
    """Parse an ISO-8601 date or datetime, returning None when unusable."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        LOGGER.debug("Could not parse datetime %r", text)
        return None


def iso_timestamp(value: Any) -> str | None:
    """Normalize a source date to ISO-8601, preserving the original on failure."""
    parsed = parse_datetime(value)
    if parsed is not None:
        return parsed.isoformat()
    text = str(value or "").strip()
    return text or None


def _has_construction_token(text: str) -> bool:
    return bool(
        re.search(
            r"\bcnst\b|\bworks\b|construction|travaux|b[âa]timent|"
            r"g[ée]nie civil|voirie",
            text,
        )
    )


def _has_construction_code(classification_codes: Iterable[Any]) -> bool:
    for code in classification_codes or ():
        digits = re.sub(r"\D", "", str(code))
        if len(digits) >= 2 and digits[:2] in CONSTRUCTION_UNSPSC_SEGMENTS:
            return True
    return False


def _is_future(closing: datetime, reference: datetime) -> bool:
    if closing.tzinfo is None:
        return closing > reference.replace(tzinfo=None)
    return closing > reference.astimezone(closing.tzinfo)


def _fold(value: Any) -> str:
    """Return casefolded, accent-stripped, whitespace-collapsed text."""
    text = unicodedata.normalize("NFKD", str(value or "")).casefold()
    text = "".join(
        character
        for character in text
        if not unicodedata.combining(character)
    )
    return re.sub(r"\s+", " ", text).strip()
