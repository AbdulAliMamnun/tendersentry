# TenderSentry — Project Specification

**Read this file before implementing any module spec in `specs/`.**

## What this product does

TenderSentry reads Canadian government tender packages (PDFs from CanadaBuys), extracts every
mandatory compliance requirement with a verbatim quote and true page number, matches requirements
against a company profile, and triages tenders into Bid / Don't bid / Review — with the specific
blocking requirement named and cited.

## The one invariant that must never break

> Every displayed requirement carries a verbatim quote and page number that verifiably exists in
> the source document.

Implementation consequence: extraction output is NEVER trusted directly. A provenance-verification
step attempts to locate each requirement's verbatim quote in the stored per-page text. If the quote
cannot be located anywhere in the document, the requirement is DROPPED (logged, never displayed).
If the quote is found on a different page than claimed, the page number is CORRECTED to the true
location ("page repair"). When in doubt, drop. False negatives are acceptable; false citations are not.

## Architecture (pipeline order)

```
CanadaBuys CSV → notice metadata → download PDFs        [ingest/]
→ per-page text extraction → provenance store            [extract/pages.py]
→ page-tagged chunks → map (per-chunk LLM extraction)    [extract/pipeline.py]
→ reduce (merge/dedupe) → verify quotes → drop/repair    [extract/pipeline.py]
→ requirement records (JSON on disk)
→ profile matching → hybrid qualification → decisions    [match/]
→ Streamlit UI: triage feed + compliance checklist       [ui/]
```

## Conventions

- Python 3.11+, no framework beyond what's in `requirements.txt` (streamlit, openai, pdfplumber,
  requests, pandas). Ask before adding dependencies.
- All configuration in `config.py` (already exists). Product name comes from `PRODUCT_NAME` —
  never hardcode the name in strings.
- Storage is flat JSON on disk under `data/` — no database.
- Every module gets a `if __name__ == "__main__":` block that runs a smoke test or CLI entry point.
- Log with the stdlib `logging` module, INFO level, to stderr. Never fail silently: skipped items,
  dropped requirements, and download failures must all be logged with a reason.
- Type hints on all public functions. Docstrings state what the function does and what it returns.
- Deterministic before clever: no retries-with-jitter frameworks, no async, no premature
  abstraction. This is a 5-day hackathon build that must be debuggable at 1 a.m.

## Data layout on disk

```
data/
  notices.json                  # list of notice metadata records from the CSV
  tenders/
    {tender_id}/
      raw/                      # downloaded attachment files as-is
      pages.json                # provenance store: {"1": "page text", "2": ...} per document
      requirements.json         # verified requirement records
      decision.json             # qualification verdict (added by match/)
```

`tender_id` = the CanadaBuys reference number, sanitized to filesystem-safe characters
(alphanumerics, dash, underscore only).

## Schemas (canonical — do not deviate without updating this file)

**Requirement record**
```json
{
  "id": "str, unique within tender",
  "tender_id": "str",
  "category": "eligibility | bid_security | submission | certification | insurance | evaluation | other_mandatory",
  "phase": "bid_phase_mandatory | contract_condition | not_a_requirement",
  "requirement_text": "str, plain-language restatement",
  "verbatim_quote": "str, exact text from the source PDF",
  "page_number": "int, true page after repair",
  "source_file": "str, filename within raw/",
  "section_ref": "str | null",
  "is_mandatory": "bool",
  "machine_checkable": "bool",
  "check_field": "str | null, e.g. bonding_capacity",
  "check_operator": "str | null, e.g. >=",
  "check_value": "str | number | null",
  "verification_status": "verified | page_repaired (dropped items never appear here)"
}
```

**Company profile** — see `data/profile.json` (created in match/ spec).

**Decision record**
```json
{
  "tender_id": "str",
  "verdict": "bid | no_bid | review",
  "blockers": ["requirement id", "..."],
  "rationale": "str",
  "confidence": "high | medium | low"
}
```

## Out of scope — do not build even if it seems helpful

Auth, billing, databases, multi-profile, MERX or any scraping beyond CanadaBuys open data,
French extraction, bid drafting, email, tests beyond smoke tests, CI, Docker.
