"""Build the municipality roster from authoritative open data.

Roster: the Ministry of Municipal Affairs and Housing "Municipalities" dataset on
data.ontario.ca (Open Government Licence – Ontario). Its ``Municipality`` column is
an HTML anchor whose href is the municipality's official website, which is why this
census never has to guess a domain.

Population: Statistics Canada table 98-10-0002 (population and dwelling counts by
census subdivision, 2021 Census). Joined on normalized name — upper-tier counties and
regions are census *divisions* rather than subdivisions, so both geography levels are
read and the match rate is reported rather than assumed.
"""

from __future__ import annotations

import io
import logging
import re
import unicodedata
import zipfile
from pathlib import Path
from typing import Any

import pandas as pd
import requests

import config
from census import schema


LOGGER = logging.getLogger(__name__)

CKAN_PACKAGE_URL = "https://data.ontario.ca/api/3/action/package_show"
MUNICIPALITIES_PACKAGE_ID = "62e83cbc-0731-4d66-abdc-2f2b31bcd76c"

STATCAN_TABLE_URL = (
    "https://www150.statcan.gc.ca/t1/wds/rest/getFullTableDownloadCSV/98100002/en"
)
STATCAN_POPULATION_COLUMN = (
    "Population and dwelling counts (13): Population, 2021 [1]"
)
#: DGUID prefixes for Ontario census subdivisions (municipalities) and census
#: divisions (counties, regions, districts).
ONTARIO_CSD_PREFIX = "2021A000535"
ONTARIO_CD_PREFIX = "2021A000335"

USER_AGENT = "TenderSentry/1.0 (+https://data.ontario.ca/)"
TIMEOUT_SECONDS = 120

CACHE_DIR = Path(config.PROJECT_ROOT) / "data" / "census"

TIER_LABELS = {
    "upper tier": "upper",
    "lower tier": "lower",
    "single tier": "single",
}

#: Legal-status suffixes to strip before matching a name against StatCan. Multi-word
#: statuses come first so "District Municipality of" is consumed whole.
STATUS_SUFFIX = re.compile(
    r",\s*(?:district municipality|regional municipality|united counties|"
    r"separated town|township|town|city|municipality|village|county|district|"
    r"borough)\s*(?:of)?\s*$",
    re.IGNORECASE,
)


def fetch_municipality_csv(cache_dir: Path | str | None = None) -> Path:
    """Download the MMAH municipalities CSV, discovering it through CKAN."""
    directory = Path(cache_dir) if cache_dir else CACHE_DIR
    directory.mkdir(parents=True, exist_ok=True)

    response = requests.get(
        CKAN_PACKAGE_URL,
        params={"id": MUNICIPALITIES_PACKAGE_ID},
        headers={"User-Agent": USER_AGENT},
        timeout=TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    resources = (response.json().get("result") or {}).get("resources") or []
    english = [
        item
        for item in resources
        if str(item.get("format", "")).upper() == "CSV"
        and "_fr_" not in str(item.get("url", ""))
    ]
    if not english:
        raise RuntimeError("The MMAH municipalities dataset published no English CSV")

    url = str(english[0]["url"])
    destination = directory / Path(url).name
    if destination.is_file() and destination.stat().st_size > 0:
        LOGGER.info("Reusing cached municipality list %s", destination.name)
        return destination

    LOGGER.info("Downloading municipality list %s", destination.name)
    data = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_SECONDS)
    data.raise_for_status()
    destination.write_bytes(data.content)
    return destination


def parse_municipalities(csv_path: Path | str) -> list[dict]:
    """Parse the MMAH CSV into roster records, extracting the official website."""
    frame = pd.read_csv(
        Path(csv_path), dtype=str, keep_default_na=False, encoding="utf-8-sig"
    )
    records: list[dict] = []
    without_site = 0
    for _, row in frame.iterrows():
        cell = repair_mojibake(str(row.get("Municipality", "")))
        name = _anchor_text(cell)
        website = _anchor_href(cell)
        if not website:
            without_site += 1
        records.append(
            {
                "slug": schema.slugify(name),
                "name": name,
                "tier": TIER_LABELS.get(
                    str(row.get("Municipal status", "")).strip().casefold(), "unknown"
                ),
                "geographic_area": str(row.get("Geographic area", "")).strip() or None,
                "website_url": website or None,
                "website_host": _host(website) if website else None,
                "population": None,
                "population_source": None,
            }
        )

    LOGGER.info(
        "Parsed %d municipalities (%d without a listed website)",
        len(records),
        without_site,
    )
    return records


def fetch_population_table(cache_dir: Path | str | None = None) -> Path:
    """Download and cache the StatCan population table."""
    directory = Path(cache_dir) if cache_dir else CACHE_DIR
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / "98100002.csv"
    if destination.is_file() and destination.stat().st_size > 0:
        LOGGER.info("Reusing cached population table %s", destination.name)
        return destination

    LOGGER.info("Resolving the StatCan population table download")
    pointer = requests.get(
        STATCAN_TABLE_URL, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_SECONDS
    )
    pointer.raise_for_status()
    payload = pointer.json()
    if str(payload.get("status")) != "SUCCESS":
        raise RuntimeError(f"StatCan refused the table request: {payload}")

    archive = requests.get(
        str(payload["object"]), headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_SECONDS
    )
    archive.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(archive.content)) as bundle:
        member = next(
            name
            for name in bundle.namelist()
            if name.endswith(".csv") and "MetaData" not in name
        )
        destination.write_bytes(bundle.read(member))
    LOGGER.info("Cached population table %s", destination.name)
    return destination


def population_index(csv_path: Path | str) -> dict[str, Any]:
    """Index Ontario populations by geography level, keeping same-named places apart.

    StatCan publishes bare names, so "Hamilton" is three rows: the census division,
    the City of Hamilton, and Hamilton Township in Northumberland. Only the DGUID
    tells them apart — division codes are ``2021A00033xx``, subdivision codes carry
    their parent division — so the index preserves that structure and lets the caller
    disambiguate by tier and county. Flattening it silently gave the City of Hamilton
    the township's 11,059 residents.
    """
    frame = pd.read_csv(
        Path(csv_path), dtype=str, keep_default_na=False, encoding="utf-8-sig"
    )
    if STATCAN_POPULATION_COLUMN not in frame.columns:
        raise RuntimeError(
            "The StatCan table does not carry the expected population column"
        )

    divisions: dict[str, int] = {}
    division_codes: dict[str, str] = {}
    subdivisions: dict[str, list[tuple[str, int]]] = {}

    ontario = frame[
        frame["DGUID"].str.startswith((ONTARIO_CSD_PREFIX, ONTARIO_CD_PREFIX))
    ]
    for _, row in ontario.iterrows():
        value = str(row[STATCAN_POPULATION_COLUMN]).replace(",", "").strip()
        if not value.isdigit():
            continue
        population = int(value)
        dguid = str(row["DGUID"])
        keys = name_variants(row["GEO"])

        if dguid.startswith(ONTARIO_CD_PREFIX):
            code = dguid[len("2021A0003") :]
            for key in keys:
                divisions.setdefault(key, population)
                division_codes.setdefault(key, code)
            continue

        # Subdivision: the four digits after the province identify its division.
        parent = dguid[len("2021A0005") : len("2021A0005") + 4]
        for key in keys:
            subdivisions.setdefault(key, []).append((parent, population))

    LOGGER.info(
        "Read %d Ontario divisions and %d named subdivisions",
        len(divisions),
        len(subdivisions),
    )
    return {
        "divisions": divisions,
        "division_codes": division_codes,
        "subdivisions": subdivisions,
    }


def attach_population(records: list[dict], index: dict[str, Any]) -> dict[str, int]:
    """Join populations onto roster records and report the match rate.

    Upper-tier municipalities are census divisions; everything else is a
    subdivision. Where a name belongs to several subdivisions, the roster's
    geographic area picks the right one.
    """
    divisions = index["divisions"]
    division_codes = index["division_codes"]
    subdivisions = index["subdivisions"]

    matched = 0
    ambiguous = 0
    for record in records:
        keys = name_variants(record["name"])
        upper_tier = str(record.get("tier")) == "upper"
        value: int | None = None

        if upper_tier:
            value = next((divisions[key] for key in keys if key in divisions), None)

        unresolved = False
        if value is None:
            candidates = [
                candidate for key in keys for candidate in subdivisions.get(key, [])
            ]
            if len(candidates) == 1:
                value = candidates[0][1]
            elif candidates:
                # Several subdivisions share this name: the county the register
                # places it in resolves which.
                wanted = next(
                    (
                        division_codes[key]
                        for key in name_variants(record.get("geographic_area"))
                        if key in division_codes
                    ),
                    None,
                )
                chosen = [item for item in candidates if item[0] == wanted]
                if chosen:
                    value = chosen[0][1]
                else:
                    ambiguous += 1
                    unresolved = True
                    LOGGER.warning(
                        "Ambiguous population for %s in %s; leaving it unset",
                        record["name"],
                        record.get("geographic_area"),
                    )

        # A single-tier city is its own census division, so the division figure is
        # the right answer for it. Never fall back this way for a lower-tier
        # municipality — that hands a township its county's population — and never
        # after a collision we could not resolve.
        if value is None and not unresolved and str(record.get("tier")) == "single":
            value = next((divisions[key] for key in keys if key in divisions), None)

        if value is None:
            continue
        record["population"] = value
        record["population_source"] = "statcan-98-10-0002-2021"
        matched += 1
    LOGGER.info(
        "Population matched for %d of %d municipalities (%.1f%%)",
        matched,
        len(records),
        100.0 * matched / len(records) if records else 0.0,
    )
    return {"matched": matched, "total": len(records)}


def build_roster(cache_dir: Path | str | None = None) -> tuple[list[dict], dict]:
    """Fetch both sources and return joined roster records with the match rate."""
    records = parse_municipalities(fetch_municipality_csv(cache_dir))
    try:
        index = population_index(fetch_population_table(cache_dir))
        coverage = attach_population(records, index)
    except (OSError, RuntimeError, requests.RequestException) as exc:
        LOGGER.error("Population data unavailable; continuing without it: %s", exc)
        coverage = {"matched": 0, "total": len(records), "error": str(exc)}
    return records, coverage


def repair_mojibake(text: str) -> str:
    """Undo double-encoded UTF-8, which the MMAH CSV ships for accented names.

    "Mattice-Val Côté" arrives as "Mattice-Val CÃ´tÃ©": the original bytes were read
    as latin-1 and re-encoded, so reversing that round trip restores them.
    """
    value = str(text or "")
    if "Ã" not in value and "Â" not in value:
        return value
    try:
        return value.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value


def normalize_name(value: Any) -> str:
    """Fold a municipality name for cross-source matching."""
    text = repair_mojibake(str(value or ""))
    text = text.replace("’", "'").replace("‘", "'")
    text = unicodedata.normalize("NFKD", text).casefold()
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = STATUS_SUFFIX.sub("", text)
    # StatCan disambiguates with a bracketed type, e.g. "Blue Mountains (T)".
    text = re.sub(r"\s*\([^)]*\)\s*$", "", text)
    text = re.sub(r"\s+", " ", text).strip(" .,")
    return text


def name_variants(value: Any) -> list[str]:
    """Normalized forms a name may be indexed under.

    StatCan publishes bilingual names as "Greater Sudbury / Grand Sudbury", so each
    side is a legitimate key alongside the whole string.
    """
    normalized = normalize_name(value)
    variants = [normalized]
    if " / " in str(value or ""):
        variants.extend(
            normalize_name(part) for part in str(value).split(" / ") if part.strip()
        )
    variants.append(normalized.replace("-", " "))
    return [variant for variant in dict.fromkeys(variants) if variant]


def _anchor_text(cell: str) -> str:
    match = re.search(r">([^<]+)<", cell)
    if match:
        return match.group(1).strip()
    return re.sub(r"<[^>]+>", "", cell).strip()


def _anchor_href(cell: str) -> str:
    match = re.search(r'href="([^"]+)"', cell)
    if not match:
        return ""
    url = match.group(1).strip()
    return url if url.lower().startswith(("http://", "https://")) else ""


def _host(url: str) -> str:
    from urllib.parse import urlparse

    return (urlparse(url).hostname or "").casefold()
