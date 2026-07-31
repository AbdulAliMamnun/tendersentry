"""Ingest Québec SEAO notices from Données Québec into the unified tenders table.

Source: the "Système électronique d'appel d'offres (SEAO)" dataset on Données
Québec, published as weekly ``hebdo_YYYYMMDD_YYYYMMDD.json`` files in OCDS 1.1
(Open Contracting Data Standard) form. Resources are discovered through the
portal's CKAN API rather than hard-coded, because each weekly file has its own
resource UUID.

Licence: Creative Commons Attribution 4.0 (CC-BY 4.0). Commercial reuse is
permitted provided the source is attributed — any product surfacing this data must
credit "Système électronique d'appel d'offres (SEAO), Données Québec".

The dataset page warns that its own resource ordering is unreliable, so weekly
files are selected by the dates embedded in their filenames, never by list order.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

import config
from notices import db
from notices.normalize import (
    iso_timestamp,
    normalize_buyer_type,
    normalize_category,
    normalize_region,
    normalize_status,
)


LOGGER = logging.getLogger(__name__)

SOURCE = "seao"
USER_AGENT = "TenderSentry/1.0 (+https://www.donneesquebec.ca/)"
DOWNLOAD_TIMEOUT_SECONDS = 300
DEFAULT_WEEKS = 4
#: Weeks of history pulled the first time this source is ingested.
BACKFILL_WEEKS = 12
WEEKLY_FILENAME = re.compile(r"hebdo_(\d{8})_(\d{8})\.json$", re.IGNORECASE)
#: SEAO document links point at a portal consultation page, not a fetchable file.
DOCUMENTS_OPEN = False
DEFAULT_REGION = "QC"


def discover_weekly_resources(session: Any | None = None) -> list[dict]:
    """Return weekly OCDS resources, newest first, deduplicated by filename."""
    payload = _fetch_package(session)
    return weekly_resources(payload)


def weekly_resources(payload: dict) -> list[dict]:
    """Extract and sort weekly resources from a CKAN ``package_show`` payload."""
    resources = (payload.get("result") or {}).get("resources") or []
    weekly: dict[str, dict] = {}
    for resource in resources:
        if not isinstance(resource, dict):
            continue
        name = str(resource.get("name") or "")
        url = str(resource.get("url") or "")
        match = WEEKLY_FILENAME.search(name) or WEEKLY_FILENAME.search(url)
        if match is None or not url:
            continue
        start = _parse_compact_date(match.group(1))
        if start is None:
            LOGGER.warning("Skipping weekly resource with unparsable date: %s", name)
            continue
        filename = f"hebdo_{match.group(1)}_{match.group(2)}.json"
        weekly.setdefault(
            filename,
            {"name": filename, "url": url, "start_date": start.date().isoformat()},
        )
    ordered = sorted(weekly.values(), key=lambda item: item["start_date"], reverse=True)
    LOGGER.info("Discovered %d weekly SEAO resources", len(ordered))
    return ordered


def fetch_weekly_file(
    resource: dict, cache_dir: Path | str | None = None, session: Any | None = None
) -> Path:
    """Download one weekly file into the cache, reusing an existing copy."""
    directory = Path(cache_dir) if cache_dir else Path(config.SEAO_CACHE_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / str(resource["name"])
    if destination.is_file() and destination.stat().st_size > 0:
        LOGGER.info("Reusing cached SEAO file %s", destination.name)
        return destination

    client = session or requests
    temporary = destination.with_name(f".{destination.name}.part")
    LOGGER.info("Downloading SEAO file %s", destination.name)
    try:
        with client.get(
            resource["url"],
            headers={"User-Agent": USER_AGENT},
            timeout=DOWNLOAD_TIMEOUT_SECONDS,
            allow_redirects=True,
            stream=True,
        ) as response:
            response.raise_for_status()
            with temporary.open("wb") as output:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        output.write(chunk)
        if temporary.stat().st_size == 0:
            raise RuntimeError("empty response body")
        os.replace(temporary, destination)
    except (OSError, requests.RequestException, RuntimeError) as exc:
        if temporary.exists():
            temporary.unlink()
        raise RuntimeError(
            f"Could not download SEAO file {destination.name}: {exc}"
        ) from exc
    return destination


def parse_weekly_file(path: Path | str) -> list[dict]:
    """Parse one weekly OCDS file into unified notice records."""
    try:
        with Path(path).open(encoding="utf-8") as source:
            payload = json.load(source)
    except (OSError, json.JSONDecodeError) as exc:
        LOGGER.error("Could not read SEAO file %s: %s", path, exc)
        return []
    return parse_releases(payload)


def parse_releases(payload: dict) -> list[dict]:
    """Convert an OCDS release package into unified notice records.

    Releases are processed oldest first so that when one procurement appears
    several times in a file, the most recent release is the one that lands.
    """
    releases = payload.get("releases") if isinstance(payload, dict) else None
    if not isinstance(releases, list):
        LOGGER.warning("SEAO payload has no releases list")
        return []

    ordered = sorted(releases, key=lambda item: str(_get(item, "date") or ""))
    records: list[dict] = []
    skipped = 0
    for release in ordered:
        record = _build_record(release)
        if record is None:
            skipped += 1
            continue
        records.append(record)
    LOGGER.info(
        "Parsed %d SEAO releases into %d records (%d skipped)",
        len(ordered),
        len(records),
        skipped,
    )
    return records


def ingest(
    connection: sqlite3.Connection | None = None,
    weeks: int | None = None,
    cache_dir: Path | str | None = None,
    session: Any | None = None,
) -> dict[str, Any]:
    """Ingest the most recent weekly files, backfilling on the first run."""
    owned = connection is None
    connection = connection or db.connect()
    notes: list[str] = []
    try:
        window = weeks if weeks is not None else _default_window(connection)
        resources = discover_weekly_resources(session)
        if not resources:
            notes.append("no weekly SEAO resources were published")
            return {
                "source": SOURCE,
                "parsed": 0,
                "inserted": 0,
                "updated": 0,
                "unchanged": 0,
                "skipped": 0,
                "notes": notes,
            }

        selected = resources[: max(1, window)]
        LOGGER.info(
            "Ingesting %d of %d weekly SEAO files (%s to %s)",
            len(selected),
            len(resources),
            selected[-1]["name"],
            selected[0]["name"],
        )
        parsed = 0
        # One procurement can appear in several weekly files, so collapse the whole
        # window before writing: files are walked oldest first and releases within a
        # file are date-ordered, so the last record kept for an ocid is its newest
        # state. Writing per file instead would rewrite those rows older-state-then-
        # newer-state on every run, churning updated_at over an unchanged corpus.
        records_by_id: dict[str, dict] = {}
        for resource in reversed(selected):
            try:
                path = fetch_weekly_file(resource, cache_dir, session)
            except RuntimeError as exc:
                LOGGER.error("%s", exc)
                notes.append(f"download failed: {resource['name']}")
                continue
            records = parse_weekly_file(path)
            parsed += len(records)
            for record in records:
                records_by_id[str(record["source_id"])] = record

        LOGGER.info(
            "Collapsed %d releases into %d distinct procurements",
            parsed,
            len(records_by_id),
        )
        tally = db.upsert_notices(connection, list(records_by_id.values()))
    finally:
        if owned:
            connection.close()

    return {"source": SOURCE, "parsed": parsed, **tally, "notes": notes}


def _default_window(connection: sqlite3.Connection) -> int:
    """Backfill on a cold table, then keep the rolling window small."""
    existing = connection.execute(
        "SELECT COUNT(*) FROM tenders WHERE source = ?", (SOURCE,)
    ).fetchone()[0]
    if int(existing or 0) == 0:
        LOGGER.info("No SEAO rows stored yet; backfilling %d weeks", BACKFILL_WEEKS)
        return BACKFILL_WEEKS
    return DEFAULT_WEEKS


def _build_record(release: Any) -> dict | None:
    """Convert one OCDS release into a unified notice record."""
    if not isinstance(release, dict):
        return None
    tender = release.get("tender")
    if not isinstance(tender, dict):
        return None
    source_id = str(release.get("ocid") or "").strip()
    title = str(tender.get("title") or "").strip()
    if not source_id or not title:
        return None

    buyer = release.get("buyer") if isinstance(release.get("buyer"), dict) else {}
    buyer_name = str(
        buyer.get("name")
        or _get(tender.get("procuringEntity"), "name")
        or ""
    ).strip()
    buyer_party = _find_party(release, str(buyer.get("id") or ""))

    tender_period = tender.get("tenderPeriod")
    tender_period = tender_period if isinstance(tender_period, dict) else {}
    closing_date = iso_timestamp(tender_period.get("endDate"))
    posted_date = iso_timestamp(tender_period.get("startDate") or release.get("date"))

    categories = tender.get("additionalProcurementCategories")
    categories = categories if isinstance(categories, list) else []
    category_raw = " | ".join(str(item) for item in categories if item) or str(
        tender.get("mainProcurementCategory") or ""
    )
    amount, currency = _tender_value(tender)

    return {
        "source": SOURCE,
        "source_id": source_id,
        "title": title,
        "description": str(tender.get("description") or "").strip() or None,
        "buyer_name": buyer_name or None,
        "buyer_type": normalize_buyer_type(
            buyer_name, default=_buyer_type_from_party(buyer_party)
        ),
        "category_raw": category_raw or None,
        "category_normalized": normalize_category(
            category_raw=category_raw,
            title=title,
            description=tender.get("description") or "",
            classification_codes=_classification_codes(tender),
            main_category=tender.get("mainProcurementCategory") or "",
        ),
        "region": normalize_region(_get(buyer_party.get("address"), "region"))
        or DEFAULT_REGION,
        "estimated_value": amount,
        "currency": currency,
        "closing_date": closing_date,
        "posted_date": posted_date,
        "notice_url": _notice_url(tender),
        "documents_open": DOCUMENTS_OPEN,
        "status": _release_status(release, tender, closing_date),
    }


def _release_status(release: dict, tender: dict, closing_date: str | None) -> str:
    """Normalize the tender status, promoting to awarded when a contract exists."""
    status = normalize_status(tender.get("status"), closing_date)
    tags = {str(tag).casefold() for tag in (release.get("tag") or [])}
    if status != "cancelled" and any(
        "award" in tag or "contract" in tag for tag in tags
    ):
        return "awarded"
    return status


def _tender_value(tender: dict) -> tuple[float | None, str | None]:
    """Read the published contract value, when the release carries one."""
    value = tender.get("value")
    if not isinstance(value, dict):
        return None, None
    amount = value.get("amount")
    if amount is None or isinstance(amount, bool):
        return None, None
    try:
        return float(amount), str(value.get("currency") or "CAD")
    except (TypeError, ValueError):
        LOGGER.warning("Discarding non-numeric SEAO tender value %r", amount)
        return None, None


def _classification_codes(tender: dict) -> list[str]:
    items = tender.get("items")
    items = items if isinstance(items, list) else []
    codes = [_get(_get(item, "classification"), "id") for item in items]
    return [str(code) for code in codes if code]


def _notice_url(tender: dict) -> str | None:
    documents = tender.get("documents")
    documents = documents if isinstance(documents, list) else []
    for document in documents:
        url = str(_get(document, "url") or "").strip()
        if url.lower().startswith(("http://", "https://")):
            return url
    return None


def _find_party(release: dict, party_id: str) -> dict:
    parties = release.get("parties")
    parties = parties if isinstance(parties, list) else []
    candidates = [party for party in parties if isinstance(party, dict)]
    for party in candidates:
        if party_id and str(party.get("id") or "") == party_id:
            return party
    for party in candidates:
        roles = {str(role).casefold() for role in (party.get("roles") or [])}
        if "buyer" in roles:
            return party
    return {}


def _buyer_type_from_party(party: dict) -> str:
    """Read SEAO's own municipal flag before falling back to name matching."""
    details = party.get("details") if isinstance(party.get("details"), dict) else {}
    municipal = str(details.get("municipal") or "").strip()
    if municipal == "1":
        return "municipal"
    if municipal == "0":
        return "provincial"
    return "unknown"


def _fetch_package(session: Any | None = None) -> dict:
    client = session or requests
    response = client.get(
        config.SEAO_CKAN_PACKAGE_URL,
        params={"id": config.SEAO_PACKAGE_ID},
        headers={"User-Agent": USER_AGENT},
        timeout=60,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or not payload.get("success", True):
        raise RuntimeError("Données Québec returned an unsuccessful CKAN response")
    return payload


def _parse_compact_date(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%Y%m%d")
    except ValueError:
        return None


def _get(value: Any, key: str) -> Any:
    return value.get(key) if isinstance(value, dict) else None


def _main() -> None:
    parser = argparse.ArgumentParser(description="Ingest SEAO notices")
    parser.add_argument("--weeks", type=int, default=None)
    parser.add_argument("--cache-dir", default=None)
    parser.add_argument("--db", default=None)
    args = parser.parse_args()

    connection = db.connect(args.db)
    try:
        result = ingest(connection, weeks=args.weeks, cache_dir=args.cache_dir)
    finally:
        connection.close()
    print(
        f"{result['source']}: parsed {result['parsed']}, "
        f"inserted {result['inserted']}, updated {result['updated']}, "
        f"unchanged {result['unchanged']}, skipped {result['skipped']}"
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    _main()
