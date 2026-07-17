"""Build and query the per-page provenance store for tender documents."""

from __future__ import annotations

import argparse
import json
import logging
import re
import unicodedata
from pathlib import Path
from typing import Iterator

import pdfplumber

import config


LOGGER = logging.getLogger(__name__)

CHARACTER_REPLACEMENTS = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2013": "-",
        "\u2014": "-",
        "\u00a0": " ",
    }
)


def extract_pages(tender_id: str, force: bool = False) -> dict:
    """Extract PDF text by page, persist pages.json, and return its contents."""
    safe_tender_id = _sanitize_tender_id(tender_id)
    if not safe_tender_id:
        raise ValueError("tender_id must contain a filesystem-safe character")

    tender_dir = _tenders_dir() / safe_tender_id
    raw_dir = tender_dir / "raw"
    pages_path = tender_dir / "pages.json"

    if pages_path.exists() and not force:
        try:
            with pages_path.open(encoding="utf-8") as source:
                cached = json.load(source)
            LOGGER.info("Reusing provenance store: %s", pages_path)
            return cached
        except (OSError, json.JSONDecodeError) as exc:
            LOGGER.warning(
                "Could not read cached provenance store %s; rebuilding: %s",
                pages_path,
                exc,
            )

    if not raw_dir.exists():
        LOGGER.warning("Tender raw directory does not exist: %s", raw_dir)
    tender_dir.mkdir(parents=True, exist_ok=True)

    pages_by_file: dict[str, dict[str, str]] = {}
    for document_path in sorted(raw_dir.iterdir()) if raw_dir.exists() else []:
        if not document_path.is_file():
            LOGGER.info("Skipping non-file item in raw directory: %s", document_path)
            continue
        if document_path.suffix.casefold() != ".pdf":
            LOGGER.info(
                "Skipping unsupported non-PDF document %s", document_path.name
            )
            continue

        try:
            file_pages: dict[str, str] = {}
            empty_pages = 0
            with pdfplumber.open(document_path) as pdf:
                for page_number, page in enumerate(pdf.pages, start=1):
                    page_text = page.extract_text() or ""
                    file_pages[str(page_number)] = page_text
                    if not page_text.strip():
                        empty_pages += 1
        except Exception as exc:
            LOGGER.error("Skipping unreadable PDF %s: %s", document_path.name, exc)
            continue

        pages_by_file[document_path.name] = file_pages
        page_count = len(file_pages)
        LOGGER.info(
            "Extracted %s: %d pages, %d empty pages",
            document_path.name,
            page_count,
            empty_pages,
        )
        if page_count and empty_pages / page_count > 0.5:
            LOGGER.warning(
                "%s is possibly scanned / low text yield: %d of %d pages are empty",
                document_path.name,
                empty_pages,
                page_count,
            )

    with pages_path.open("w", encoding="utf-8") as output:
        json.dump(pages_by_file, output, ensure_ascii=False, indent=2)
        output.write("\n")
    LOGGER.info("Wrote provenance store: %s", pages_path)
    return pages_by_file


def normalize(text: str) -> str:
    """Return canonical NFKC, punctuation-normalized, casefolded text."""
    normalized = unicodedata.normalize("NFKC", str(text))
    normalized = normalized.translate(CHARACTER_REPLACEMENTS)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized.casefold()


def find_quote(quote: str, pages: dict) -> tuple[str, str] | None:
    """Locate a quote exactly in a provenance mapping and return file and page."""
    normalized_quote = normalize(quote)
    if not normalized_quote:
        return None

    candidates = [normalized_quote]
    if len(normalized_quote) > 120:
        candidates.append(normalized_quote[:120])

    files = list(_iter_page_files(pages))
    for candidate in candidates:
        for source_file, file_pages in files:
            for page_number in _sorted_page_numbers(file_pages):
                if candidate in normalize(file_pages[page_number]):
                    return source_file, page_number

    for candidate in candidates:
        for source_file, file_pages in files:
            page_numbers = _sorted_page_numbers(file_pages)
            for index in range(len(page_numbers) - 1):
                first_page = page_numbers[index]
                second_page = page_numbers[index + 1]
                combined = normalize(
                    f"{file_pages[first_page]} {file_pages[second_page]}"
                )
                if candidate in combined:
                    return source_file, first_page
    return None


def _iter_page_files(pages: dict) -> Iterator[tuple[str, dict[str, str]]]:
    if not pages:
        return
    if all(isinstance(value, str) for value in pages.values()):
        yield "", pages
        return
    for source_file in sorted(pages):
        file_pages = pages[source_file]
        if isinstance(file_pages, dict):
            yield str(source_file), file_pages


def _sorted_page_numbers(file_pages: dict[str, str]) -> list[str]:
    def page_key(page_number: str) -> tuple[int, int | str]:
        value = str(page_number)
        try:
            return 0, int(value)
        except ValueError:
            return 1, value

    return sorted((str(page) for page in file_pages), key=page_key)


def _tenders_dir() -> Path:
    path = Path(config.DATA_DIR)
    if path.is_absolute():
        return path
    return Path(config.PROJECT_ROOT) / path


def _sanitize_tender_id(tender_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", str(tender_id)).strip("_-")


def _main() -> None:
    parser = argparse.ArgumentParser(description="Build a tender provenance store")
    parser.add_argument("tender_id")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    pages = extract_pages(args.tender_id, force=args.force)
    print(
        f"{args.tender_id}: {len(pages)} files, "
        f"{sum(len(file_pages) for file_pages in pages.values())} pages"
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    _main()
