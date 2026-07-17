"""Download and normalize open tender notices from CanadaBuys."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote, urljoin, urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup

import config


LOGGER = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (compatible; TenderNoticeIngest/1.0; "
    "+https://canadabuys.canada.ca/)"
)
CACHE_MAX_AGE_SECONDS = 24 * 60 * 60
DOWNLOAD_TIMEOUT_SECONDS = 60
SUPPORTED_EXTENSIONS = {".pdf", ".doc", ".docx", ".zip"}
ARCHIVE_DOCUMENT_EXTENSIONS = {".pdf", ".doc", ".docx"}
DOCUMENT_LINK_HINT = re.compile(
    r"attachment|document|download|bid[\s_-]*package", re.IGNORECASE
)


def fetch_notices_csv(dest_path: str) -> str:
    """Download the CanadaBuys open-notices CSV and return its local path."""
    destination = Path(dest_path)
    if destination.exists():
        age_seconds = time.time() - destination.stat().st_mtime
        if age_seconds < CACHE_MAX_AGE_SECONDS:
            LOGGER.info("Reusing fresh notices CSV: %s", destination)
            return str(destination)

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.part")
    LOGGER.info("Downloading open tender notices CSV to %s", destination)

    try:
        with requests.get(
            config.CANADABUYS_OPEN_TENDERS_URL,
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
            raise RuntimeError("CanadaBuys returned an empty CSV")
        os.replace(temporary, destination)
    except (OSError, requests.RequestException, RuntimeError) as exc:
        if temporary.exists():
            temporary.unlink()
        LOGGER.error("Could not download CanadaBuys notices CSV: %s", exc)
        raise RuntimeError(f"Could not download CanadaBuys notices CSV: {exc}") from exc

    LOGGER.info("Downloaded notices CSV: %s", destination)
    return str(destination)


def parse_notices(csv_path: str) -> list[dict]:
    """Parse, filter, normalize, and store notices from a CanadaBuys CSV."""
    dataframe = _read_csv(csv_path)
    headers = [str(column) for column in dataframe.columns]
    LOGGER.info("CanadaBuys CSV header: %s", headers)
    columns = _map_columns(headers)
    LOGGER.info(
        "Sample procurementCategory values: %s",
        _sample_column_values(dataframe, columns["procurement_category"]),
    )
    LOGGER.info(
        "Sample closing dates: %s",
        _sample_column_values(dataframe, columns["closing_date"]),
    )

    notices: list[dict] = []
    total_count = len(dataframe)
    region_count = 0
    category_count = 0
    cnst_count = 0
    keyword_count = 0
    future_count = 0
    attachment_count = 0
    closed_count = 0
    invalid_closing_count = 0
    naive_closing_count = 0
    aware_closing_count = 0
    now = datetime.now().astimezone()
    for row_number, (_, row) in enumerate(dataframe.iterrows(), start=2):
        regions = _join_values(row, columns["regions"])
        if not _matches_region(regions, str(config.REGION_FILTER)):
            continue
        region_count += 1

        title = _cell_text(row[columns["title"]])
        description = _cell_text(row[columns["description"]])
        procurement_category = _cell_text(row[columns["procurement_category"]])
        category_rule = _category_match_rule(
            procurement_category, title, description
        )
        if category_rule is None:
            continue
        category_count += 1
        if category_rule == "procurementCategory=CNST":
            cnst_count += 1
        else:
            keyword_count += 1

        closing_date = _cell_text(row[columns["closing_date"]])
        parsed_closing = _parse_closing_datetime(closing_date)
        if parsed_closing is None:
            invalid_closing_count += 1
            LOGGER.warning(
                "Date parse failure on CSV row %d: %r",
                row_number,
                closing_date,
            )
            continue
        if parsed_closing.tzinfo is None:
            naive_closing_count += 1
        else:
            aware_closing_count += 1
        try:
            closing_is_future = _is_future_closing(parsed_closing, now)
        except TypeError as exc:
            invalid_closing_count += 1
            LOGGER.warning(
                "Date comparison failure on CSV row %d for %r: %s",
                row_number,
                closing_date,
                exc,
            )
            continue
        if not closing_is_future:
            closed_count += 1
            continue
        future_count += 1

        attachment_urls = _split_attachment_urls(
            _cell_text(row[columns["attachments"]])
        )
        if attachment_urls:
            attachment_count += 1

        reference = _cell_text(row[columns["reference_number"]])
        tender_id = _sanitize_tender_id(reference)
        if not tender_id:
            LOGGER.warning("Skipping CSV row %d: missing reference number", row_number)
            continue

        LOGGER.info("Keeping notice %s: matched by %s", tender_id, category_rule)

        notices.append(
            {
                "tender_id": tender_id,
                "title": title,
                "publication_date": _cell_text(row[columns["publication_date"]]),
                "closing_date": closing_date,
                "regions": regions,
                "description": description,
                "attachment_urls": attachment_urls,
                "notice_url": _cell_text(row[columns["notice_url"]]),
            }
        )

    LOGGER.info(
        "Filter funnel: total rows=%d -> region %s=%d -> category matches=%d "
        "(CNST=%d, keyword fallback=%d) -> future closing=%d -> with attachments=%d",
        total_count,
        config.REGION_FILTER,
        region_count,
        category_count,
        cnst_count,
        keyword_count,
        future_count,
        attachment_count,
    )
    LOGGER.info(
        "Closing-date timezone profile after category filter: naive=%d, "
        "timezone-aware=%d, parse/comparison failures=%d",
        naive_closing_count,
        aware_closing_count,
        invalid_closing_count,
    )
    LOGGER.info("Excluded %d notices as closed", closed_count)

    notices_path = Path(config.NOTICES_PATH)
    notices_path.parent.mkdir(parents=True, exist_ok=True)
    with notices_path.open("w", encoding="utf-8") as output:
        json.dump(notices, output, ensure_ascii=False, indent=2)
        output.write("\n")
    LOGGER.info("Wrote %d filtered notices to %s", len(notices), notices_path)
    return notices


def fetch_attachments_from_notice_page(notice: dict) -> list[str]:
    """Find and return deduplicated document links from a notice web page."""
    notice_url = _effective_notice_url(notice)
    if not notice_url:
        LOGGER.warning("Cannot inspect notice page: missing notice_url")
        return []

    try:
        with requests.get(
            notice_url,
            headers={"User-Agent": USER_AGENT},
            timeout=DOWNLOAD_TIMEOUT_SECONDS,
            allow_redirects=True,
        ) as response:
            response.raise_for_status()
            page_url = response.url
            html = response.text
    except requests.RequestException as exc:
        LOGGER.warning("Could not inspect notice page %s: %s", notice_url, exc)
        return []

    document_urls: list[str] = []
    soup = BeautifulSoup(html, "html.parser")
    for link in soup.find_all("a", href=True):
        href = str(link.get("href", "")).strip()
        if not href or href.lower().startswith(("javascript:", "mailto:", "tel:")):
            continue
        resolved = urljoin(page_url, href)
        parsed = urlparse(resolved)
        if parsed.scheme not in {"http", "https"}:
            continue
        link_text = link.get_text(" ", strip=True)
        is_document_file = Path(parsed.path).suffix.casefold() in SUPPORTED_EXTENSIONS
        has_document_hint = bool(DOCUMENT_LINK_HINT.search(f"{link_text} {href}"))
        if is_document_file or has_document_hint:
            document_urls.append(resolved)

    deduplicated = list(dict.fromkeys(document_urls))
    LOGGER.info(
        "Found %d document link(s) on notice page %s",
        len(deduplicated),
        notice_url,
    )
    return deduplicated


def download_tender(notice: dict) -> dict:
    """Download one tender's supported attachments and return per-file outcomes."""
    tender_id = _sanitize_tender_id(_cell_text(notice.get("tender_id", "")))
    summary: dict[str, Any] = {
        "tender_id": tender_id,
        "downloaded": [],
        "skipped": [],
    }
    if not tender_id:
        reason = "missing_tender_id"
        LOGGER.error("Skipping tender download: %s", reason)
        summary["skipped"].append({"url": "", "reason": reason})
        return summary

    raw_dir = _configured_tenders_dir() / tender_id / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    attachment_urls = notice.get("attachment_urls", [])
    if not isinstance(attachment_urls, list):
        attachment_urls = [attachment_urls]
    urls = [
        _cell_text(url)
        for url in attachment_urls
        if _cell_text(url).lower().startswith(("http://", "https://"))
    ]
    url_source = "CSV"
    if not urls:
        url_source = "notice page"
        urls = fetch_attachments_from_notice_page(notice)
    LOGGER.info(
        "Tender %s attachment URLs came from %s (%d found)",
        tender_id,
        url_source,
        len(urls),
    )

    notice_url = _effective_notice_url(notice)
    if not urls:
        _record_manual_download(summary, notice_url, tender_id)
        return summary

    urls, language_skips = _prefer_english_urls(urls)
    summary["skipped"].extend(language_skips)

    direct_url_filenames = {
        _url_filename(url)
        for url in urls
        if Path(_url_filename(url)).suffix.casefold()
        in ARCHIVE_DOCUMENT_EXTENSIONS
    }
    archive_cache = {
        path.name
        for path in raw_dir.iterdir()
        if path.is_file()
        and path.suffix.casefold() in ARCHIVE_DOCUMENT_EXTENSIONS
        and path.name not in direct_url_filenames
        and path.stat().st_size > 0
    }

    download_failed = False
    for url in urls:
        outcome = _download_attachment(url, raw_dir, archive_cache)
        summary["downloaded"].extend(outcome["downloaded"])
        summary["skipped"].extend(outcome["skipped"])
        if outcome["skipped"]:
            download_failed = True

    summary["downloaded"] = list(dict.fromkeys(summary["downloaded"]))
    if download_failed:
        _record_manual_download(summary, notice_url, tender_id)
    return summary


def ingest_demo_set(limit: int = 10) -> None:
    """Ingest up to ``limit`` notices with attachments and print a summary table."""
    csv_path = fetch_notices_csv(str(config.OPEN_TENDERS_CSV_PATH))
    notices = parse_notices(csv_path)
    with_attachments: list[dict] = []
    zero_attachment_count = 0
    for notice in notices:
        if notice["attachment_urls"]:
            with_attachments.append(notice)
        else:
            zero_attachment_count += 1
    LOGGER.info(
        "%d filtered notices had zero attachment URLs", zero_attachment_count
    )
    without_attachments = [
        notice for notice in notices if not notice["attachment_urls"]
    ]
    selected = [*with_attachments, *without_attachments][: max(0, limit)]

    rows: list[list[str]] = []
    manual_download_urls: list[str] = []
    for notice in selected:
        try:
            result = download_tender(notice)
        except Exception as exc:  # Keep one malformed tender from stopping the demo set.
            LOGGER.exception(
                "Unexpected failure downloading tender %s: %s",
                notice["tender_id"],
                exc,
            )
            result = {
                "downloaded": [],
                "skipped": [
                    {"url": _effective_notice_url(notice), "reason": str(exc)},
                    {
                        "url": _effective_notice_url(notice),
                        "reason": "manual_download_needed",
                    },
                ],
            }
        for skipped in result["skipped"]:
            if (
                skipped["reason"] == "manual_download_needed"
                and skipped["url"]
                and skipped["url"] not in manual_download_urls
            ):
                manual_download_urls.append(skipped["url"])
        rows.append(
            [
                notice["tender_id"],
                _truncate(notice["title"], 60),
                notice["closing_date"],
                str(len(result["downloaded"])),
                str(len(result["skipped"])),
            ]
        )

    _print_summary(rows)
    _print_manual_downloads(manual_download_urls)


def _read_csv(csv_path: str) -> pd.DataFrame:
    try:
        return pd.read_csv(
            csv_path,
            encoding="utf-8-sig",
            dtype=str,
            keep_default_na=False,
        )
    except UnicodeDecodeError:
        LOGGER.info("UTF-8 decoding failed for %s; retrying as latin-1", csv_path)
        return pd.read_csv(
            csv_path,
            encoding="latin-1",
            dtype=str,
            keep_default_na=False,
        )


def _configured_tenders_dir() -> Path:
    path = Path(config.DATA_DIR)
    if path.is_absolute():
        return path
    return Path(config.PROJECT_ROOT) / path


def _effective_notice_url(notice: dict) -> str:
    notice_url = _cell_text(notice.get("notice_url", ""))
    if notice_url:
        return notice_url
    tender_id = _sanitize_tender_id(_cell_text(notice.get("tender_id", "")))
    if tender_id.casefold().startswith("cb-"):
        return (
            "https://canadabuys.canada.ca/en/tender-opportunities/"
            f"tender-notice/{tender_id}"
        )
    return ""


def _record_manual_download(
    summary: dict[str, Any], notice_url: str, tender_id: str
) -> None:
    LOGGER.warning(
        "Notice %s (%s): manual_download_needed",
        notice_url or "<missing notice_url>",
        tender_id,
    )
    marker = {"url": notice_url, "reason": "manual_download_needed"}
    if marker not in summary["skipped"]:
        summary["skipped"].append(marker)


def _map_columns(headers: list[str]) -> dict[str, Any]:
    normalized = {header: _normalize_header(header) for header in headers}

    def matching(*parts: str) -> list[str]:
        return [
            header
            for header, value in normalized.items()
            if all(part in value for part in parts)
        ]

    def first(candidates: Iterable[str]) -> str | None:
        return next(iter(candidates), None)

    reference = first(matching("referencenumber")) or first(matching("reference"))
    title = first(matching("title", "eng"))
    publication = first(matching("publicationdate")) or first(matching("publication"))
    closing = first(
        header
        for header, value in normalized.items()
        if "closingdate" in value or "cloture" in value
    )
    description = first(matching("tenderdescription", "eng")) or first(
        header
        for header in matching("description", "eng")
        if "gsin" not in normalized[header] and "unspsc" not in normalized[header]
    )
    attachments = first(matching("attachment", "eng"))
    notice_url = first(matching("noticeurl")) or first(matching("url", "eng"))
    procurement_category = first(matching("procurementcategory"))

    region_candidates = matching("region")
    english_regions = [
        header for header in region_candidates if "eng" in normalized[header]
    ]
    regions = english_regions or region_candidates
    mapped: dict[str, Any] = {
        "reference_number": reference,
        "title": title,
        "publication_date": publication,
        "closing_date": closing,
        "regions": regions,
        "procurement_category": procurement_category,
        "description": description,
        "attachments": attachments,
        "notice_url": notice_url,
    }
    missing = [name for name, value in mapped.items() if not value]
    if missing:
        LOGGER.error("CanadaBuys CSV header: %s", headers)
        raise ValueError(
            "Could not map required CSV concept(s): " + ", ".join(missing)
        )
    return mapped


def _normalize_header(header: str) -> str:
    return re.sub(r"[^a-z0-9]", "", header.casefold())


def _cell_text(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def _sample_column_values(
    dataframe: pd.DataFrame, column: str, limit: int = 5
) -> list[str]:
    samples: list[str] = []
    for raw_value in dataframe[column]:
        value = _cell_text(raw_value)
        if value and value not in samples:
            samples.append(value)
        if len(samples) == limit:
            break
    return samples


def _join_values(row: pd.Series, columns: Iterable[str]) -> str:
    values: list[str] = []
    for column in columns:
        value = _cell_text(row[column])
        if value and value not in values:
            values.append(value)
    return "\n".join(values)


def _matches_region(regions: str, region_filter: str) -> bool:
    wanted = region_filter.strip().casefold()
    if not wanted:
        return True
    region_text = regions.casefold()
    if wanted in {"ontario", "on"}:
        return "ontario" in region_text or bool(
            re.search(r"(?:^|[^a-z])on(?:$|[^a-z])", region_text)
        )
    return wanted in region_text


def _category_match_rule(
    procurement_category: str, title: str, description: str
) -> str | None:
    if "cnst" in procurement_category.casefold():
        return "procurementCategory=CNST"

    searchable_text = f"{title}\n{description}".casefold()
    matched_keywords = [
        str(keyword)
        for keyword in config.CATEGORY_KEYWORDS
        if re.search(
            rf"(?<![a-z0-9_]){re.escape(str(keyword).casefold())}(?![a-z0-9_])",
            searchable_text,
        )
    ]
    if matched_keywords:
        return f"keyword fallback ({', '.join(matched_keywords)})"
    return None


def _parse_closing_datetime(closing_date: str) -> datetime | None:
    if not closing_date:
        return None
    try:
        return datetime.fromisoformat(closing_date.replace("Z", "+00:00"))
    except ValueError:
        return None


def _is_future_closing(parsed: datetime, now: datetime) -> bool:
    if parsed.tzinfo is None:
        return parsed > now.replace(tzinfo=None)
    return parsed > now.astimezone(parsed.tzinfo)


def _split_attachment_urls(value: str) -> list[str]:
    urls: list[str] = []
    for part in re.split(r"[|,\r\n]+", value):
        candidate = part.strip()
        if candidate.lower().startswith(("http://", "https://")):
            urls.append(candidate)
    return list(dict.fromkeys(urls))


def _sanitize_tender_id(reference: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", reference).strip("_-")


def _sanitize_filename(filename: str) -> str:
    basename = Path(unquote(filename.replace("\\", "/"))).name
    basename = re.sub(r"\s+", "_", basename)
    basename = re.sub(r"[^A-Za-z0-9._-]+", "_", basename).lstrip(".")
    if not basename:
        return "attachment"
    if len(basename) <= 180:
        return basename
    suffix = "".join(Path(basename).suffixes)[-20:]
    return basename[: 180 - len(suffix)].rstrip("._-") + suffix


def _url_filename(url: str) -> str:
    return _sanitize_filename(Path(urlparse(url).path).name)


def _content_disposition_filename(header: str) -> str:
    extended = re.search(
        r"filename\*\s*=\s*(?:[^']*'[^']*')?([^;]+)", header, re.IGNORECASE
    )
    if extended:
        return _sanitize_filename(unquote(extended.group(1).strip().strip('"')))
    regular = re.search(r"filename\s*=\s*(?:\"([^\"]+)\"|([^;]+))", header, re.I)
    if regular:
        return _sanitize_filename((regular.group(1) or regular.group(2)).strip())
    return ""


def _download_attachment(
    url: str, raw_dir: Path, archive_cache: set[str]
) -> dict[str, list[Any]]:
    outcome: dict[str, list[Any]] = {"downloaded": [], "skipped": []}
    url_filename = _url_filename(url)
    url_extension = Path(url_filename).suffix.casefold()
    url_destination = raw_dir / url_filename
    if url_extension == ".zip" and archive_cache:
        LOGGER.info("Using cached files previously extracted from %s", url)
        outcome["downloaded"].extend(sorted(archive_cache))
        return outcome
    if url_extension in ARCHIVE_DOCUMENT_EXTENSIONS and _is_cached(url_destination):
        LOGGER.info("Using cached attachment %s", url_destination)
        outcome["downloaded"].append(url_filename)
        return outcome

    last_error: Exception | None = None
    portal_session = False
    for attempt in range(2):
        temporary: Path | None = None
        try:
            with requests.get(
                url,
                headers={"User-Agent": USER_AGENT},
                timeout=DOWNLOAD_TIMEOUT_SECONDS,
                allow_redirects=True,
                stream=True,
            ) as response:
                if response.status_code in {401, 403}:
                    portal_session = True
                response.raise_for_status()

                content_type = response.headers.get("Content-Type", "").lower()
                if "text/html" in content_type or "application/xhtml+xml" in content_type:
                    if re.search(r"login|sign[-_]?in|session", response.url, re.I):
                        portal_session = True
                        raise requests.RequestException("portal session required")
                    reason = "html_landing_page"
                    LOGGER.warning("Skipping attachment %s: %s", url, reason)
                    outcome["skipped"].append({"url": url, "reason": reason})
                    return outcome

                disposition_name = _content_disposition_filename(
                    response.headers.get("Content-Disposition", "")
                )
                filename = disposition_name or url_filename
                extension = Path(filename).suffix.casefold()
                if extension not in SUPPORTED_EXTENSIONS:
                    reason = f"unsupported_file_type:{extension or 'none'}"
                    LOGGER.warning("Skipping attachment %s: %s", url, reason)
                    outcome["skipped"].append({"url": url, "reason": reason})
                    return outcome

                if extension == ".zip" and archive_cache:
                    LOGGER.info("Using cached files previously extracted from %s", url)
                    outcome["downloaded"].extend(sorted(archive_cache))
                    return outcome

                destination = raw_dir / filename
                if extension != ".zip" and _is_cached(destination):
                    LOGGER.info("Using cached attachment %s", destination)
                    outcome["downloaded"].append(filename)
                    return outcome

                temporary = raw_dir / f".{filename}.part"
                with temporary.open("wb") as output:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            output.write(chunk)
                if temporary.stat().st_size == 0:
                    raise OSError("empty response body")
                os.replace(temporary, destination)

                if extension == ".zip":
                    return _extract_archive(destination, url, raw_dir)

                LOGGER.info("Downloaded %s to %s", url, destination)
                outcome["downloaded"].append(filename)
                return outcome
        except (OSError, requests.RequestException) as exc:
            last_error = exc
            if temporary is not None and temporary.exists():
                temporary.unlink()
            if attempt == 0:
                LOGGER.warning("Download failed for %s; retrying once: %s", url, exc)

    reason = "requires_portal_session" if portal_session else f"download_failed:{last_error}"
    LOGGER.error("Skipping attachment %s after retry: %s", url, reason)
    outcome["skipped"].append({"url": url, "reason": reason})
    return outcome


def _extract_archive(archive_path: Path, url: str, raw_dir: Path) -> dict[str, list[Any]]:
    outcome: dict[str, list[Any]] = {"downloaded": [], "skipped": []}
    try:
        with zipfile.ZipFile(archive_path) as archive:
            members = [
                member
                for member in archive.infolist()
                if not member.is_dir()
                and Path(member.filename).suffix.casefold()
                in ARCHIVE_DOCUMENT_EXTENSIONS
            ]
            members, language_skips = _prefer_english_archive_members(members, url)
            outcome["skipped"].extend(language_skips)

            for member in members:
                filename = _sanitize_filename(member.filename)
                destination = raw_dir / filename
                if _is_cached(destination):
                    LOGGER.info("Using cached archive member %s", destination)
                    outcome["downloaded"].append(filename)
                    continue
                temporary = raw_dir / f".{filename}.part"
                try:
                    with archive.open(member) as source, temporary.open("wb") as output:
                        shutil.copyfileobj(source, output)
                    if temporary.stat().st_size == 0:
                        raise OSError("empty archive member")
                    os.replace(temporary, destination)
                    LOGGER.info("Extracted %s from %s", filename, archive_path.name)
                    outcome["downloaded"].append(filename)
                except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
                    if temporary.exists():
                        temporary.unlink()
                    member_url = f"{url}#{member.filename}"
                    reason = f"archive_member_failed:{exc}"
                    LOGGER.error("Skipping archive member %s: %s", member_url, reason)
                    outcome["skipped"].append(
                        {"url": member_url, "reason": reason}
                    )

            unsupported = [
                member
                for member in archive.infolist()
                if not member.is_dir()
                and Path(member.filename).suffix.casefold()
                not in ARCHIVE_DOCUMENT_EXTENSIONS
            ]
            for member in unsupported:
                member_url = f"{url}#{member.filename}"
                reason = "unsupported_archive_file_type"
                LOGGER.warning("Skipping archive member %s: %s", member_url, reason)
                outcome["skipped"].append({"url": member_url, "reason": reason})
    except (OSError, zipfile.BadZipFile) as exc:
        reason = f"invalid_zip:{exc}"
        LOGGER.error("Skipping archive %s: %s", url, reason)
        outcome["skipped"].append({"url": url, "reason": reason})
    finally:
        try:
            archive_path.unlink()
        except OSError as exc:
            LOGGER.error("Could not delete extracted archive %s: %s", archive_path, exc)

    return outcome


def _is_cached(path: Path) -> bool:
    return path.exists() and path.is_file() and path.stat().st_size > 0


def _language_marker(filename: str) -> str | None:
    stem = Path(filename).stem.casefold()
    tokens = set(filter(None, re.split(r"[^a-z]+", stem)))
    if tokens & {"en", "eng", "english"}:
        return "en"
    if tokens & {"fr", "fra", "french", "francais"}:
        return "fr"
    return None


def _language_neutral_name(filename: str) -> str:
    stem = Path(filename).stem.casefold()
    stem = re.sub(
        r"(?:^|[^a-z])(en|eng|english|fr|fra|french|francais)(?=$|[^a-z])",
        "_",
        stem,
    )
    return re.sub(r"[^a-z0-9]+", "", stem) + Path(filename).suffix.casefold()


def _prefer_english_urls(urls: list[str]) -> tuple[list[str], list[dict[str, str]]]:
    english_keys = {
        _language_neutral_name(_url_filename(url))
        for url in urls
        if _language_marker(_url_filename(url)) == "en"
    }
    kept: list[str] = []
    skipped: list[dict[str, str]] = []
    for url in urls:
        filename = _url_filename(url)
        if (
            _language_marker(filename) == "fr"
            and _language_neutral_name(filename) in english_keys
        ):
            reason = "french_duplicate_of_english_attachment"
            LOGGER.info("Skipping attachment %s: %s", url, reason)
            skipped.append({"url": url, "reason": reason})
        else:
            kept.append(url)
    return kept, skipped


def _prefer_english_archive_members(
    members: list[zipfile.ZipInfo], url: str
) -> tuple[list[zipfile.ZipInfo], list[dict[str, str]]]:
    english_keys = {
        _language_neutral_name(_sanitize_filename(member.filename))
        for member in members
        if _language_marker(_sanitize_filename(member.filename)) == "en"
    }
    kept: list[zipfile.ZipInfo] = []
    skipped: list[dict[str, str]] = []
    for member in members:
        filename = _sanitize_filename(member.filename)
        if (
            _language_marker(filename) == "fr"
            and _language_neutral_name(filename) in english_keys
        ):
            reason = "french_duplicate_of_english_attachment"
            member_url = f"{url}#{member.filename}"
            LOGGER.info("Skipping archive member %s: %s", member_url, reason)
            skipped.append({"url": member_url, "reason": reason})
        else:
            kept.append(member)
    return kept, skipped


def _truncate(value: str, width: int) -> str:
    if len(value) <= width:
        return value
    return value[: width - 3] + "..."


def _print_summary(rows: list[list[str]]) -> None:
    headers = ["tender_id", "title", "closing date", "files", "skipped"]
    all_rows = [headers, *rows]
    widths = [max(len(row[index]) for row in all_rows) for index in range(len(headers))]

    def format_row(row: list[str]) -> str:
        return " | ".join(value.ljust(widths[index]) for index, value in enumerate(row))

    print(format_row(headers))
    print("-+-".join("-" * width for width in widths))
    for row in rows:
        print(format_row(row))


def _print_manual_downloads(urls: list[str]) -> None:
    print("\nmanual_download_needed:")
    if not urls:
        print("(none)")
        return
    for url in urls:
        print(url)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ingest_demo_set()
