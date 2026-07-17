# Spec 01 — CanadaBuys Ingestion (`ingest/canadabuys.py`)

Read `SPEC.md` first. This module gets tender notices and their document packages onto disk.
Everything downstream depends on it, so favor robustness over completeness: skip-and-log beats crash.

## Data source

CanadaBuys publishes open procurement data as downloadable CSVs:
https://canadabuys.canada.ca/en/procurement-data

Use the **open tender notices** CSV. IMPORTANT: do not assume exact column names — download the
CSV first, print the header, and map columns defensively by fuzzy match on these concepts
(CanadaBuys uses bilingual column names like `title-titre-eng`):

| Concept            | Look for header containing            |
|--------------------|---------------------------------------|
| reference number   | `referenceNumber` or `reference`      |
| title (English)    | `title` + `eng`                       |
| publication date   | `publicationDate` or `publication`    |
| closing date       | `ClosingDate` or `Cloture`            |
| regions            | `region`                              |
| category codes     | `unspsc` and/or `gsin`                |
| description (Eng)  | `Description` + `eng`                 |
| attachments (Eng)  | `attachment` + `eng`                  |
| notice URL (Eng)   | `noticeURL` or (`URL` + `eng`)        |

If a concept cannot be mapped, log the full header row and raise a clear error naming the missing
concept — do not guess silently.

## Functions to implement

### `fetch_notices_csv(dest_path: str) -> str`
Download the open-notices CSV to `dest_path`. If the file already exists and is < 24h old, reuse it
(log that it was reused). Return the path. Use a browser-like User-Agent header; some government
servers reject default python-requests agents.

### `parse_notices(csv_path: str) -> list[dict]`
Load with pandas (try utf-8, fall back to latin-1). Map columns per the table above. Filter rows to
the demo slice using `config.REGION_FILTER` and `config.CATEGORY_FILTER`:
- Region: case-insensitive substring match against the regions column ("Ontario" should match
  "Ontario", "ON", or lists containing Ontario — inspect actual values and handle what's there).
- Category: match against UNSPSC/GSIN codes AND title/description keywords. For construction,
  UNSPSC segment 72 (building/construction services) and 30 (structures/building materials) are the
  relevant families; also keyword-match title/description on: construction, paving, roofing, HVAC,
  renovation, bridge, watermain, sewer, road. Keep the keyword list in `config.py` as
  `CATEGORY_KEYWORDS`.

Return a list of normalized notice dicts:
```json
{"tender_id": "...", "title": "...", "publication_date": "...", "closing_date": "...",
 "regions": "...", "description": "...", "attachment_urls": ["..."], "notice_url": "..."}
```
The attachments cell typically packs multiple URLs into one field (pipe, comma, or newline
separated) — inspect and split accordingly; strip whitespace; keep only http(s) URLs.
Write the full list to `data/notices.json` and return it.

### `download_tender(notice: dict) -> dict`
Create `data/tenders/{tender_id}/raw/`. Download each attachment URL:
- Follow redirects. Timeout 60s per file. One retry on failure, then log and skip.
- Derive the filename from the URL path or Content-Disposition header; sanitize it.
- Skip non-document files (keep .pdf, .doc, .docx, .zip; log anything else skipped).
- If a .zip is downloaded, extract it into `raw/` (flat), then delete the zip. Keep only
  pdf/doc/docx from the archive.
- Record per-file outcome. Return a summary dict:
  `{"tender_id": ..., "downloaded": [filenames], "skipped": [{"url":..., "reason":...}]}`.
Idempotent: if a file already exists with size > 0, skip re-downloading (log as cached).

### `ingest_demo_set(limit: int = 10) -> None`
Orchestrator: fetch CSV → parse → take up to `limit` notices that have at least one attachment →
download each → print a summary table to stdout: tender_id, title (truncated 60 chars), closing
date, number of files downloaded, number skipped. This is the `__main__` entry point.

## Acceptance criteria

Running `python -m ingest.canadabuys` from the repo root:
1. Downloads the CSV (or reuses a fresh cached copy) and writes `data/notices.json`.
2. Downloads document packages for up to 10 filtered notices into `data/tenders/{id}/raw/`.
3. Prints the summary table; every failure appears in logs with a reason; exit code 0 even when
   some downloads fail (only the CSV being unreachable is fatal).
4. Re-running immediately does no re-downloading (all cached) and finishes in seconds.

## Known risks to handle gracefully

- Attachments hosted on redirect chains or third-party portals that require a session: after the
  single retry, log the URL with reason "requires_portal_session" and move on. The human will
  hand-download stragglers into `raw/` — the pipeline must treat hand-placed files identically.
- Mixed-language duplicate attachments (same doc in EN and FR): if filenames clearly indicate
  language, prefer EN; when unsure, keep both.
- HTML "attachment" links that are landing pages, not files: detect via Content-Type; log and skip.
