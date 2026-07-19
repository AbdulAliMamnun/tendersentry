# Spec 02 — Extraction Pipeline (`extract/pages.py`, `extract/pipeline.py`)

Read `SPEC.md` first. This module implements the product's core invariant:

> Every displayed requirement carries a verbatim quote and page number that verifiably exists in
> the source document.

The pipeline: per-page text extraction → provenance store → page-tagged chunks → map (per-chunk
LLM extraction) → reduce (merge/dedupe) → verification (locate quote) → drop or page-repair →
requirement records on disk.

Order of trust: the provenance store is ground truth; the LLM is an untrusted proposer; the
verifier is the gatekeeper. Nothing the LLM says reaches disk without passing verification.

---

## Part A — `extract/pages.py` (provenance store)

### `extract_pages(tender_id: str) -> dict`
For every .pdf in `data/tenders/{tender_id}/raw/` (handle .docx too if trivial via python-docx;
otherwise log and skip non-PDF):
- Use pdfplumber. For each page, `page.extract_text() or ""`.
- Build: `{source_file: {"1": "page text", "2": ..., ...}}` — page numbers are 1-based strings,
  matching the printed PDF page position (pdfplumber's page order), not any internal numbering.
- Write to `data/tenders/{tender_id}/pages.json`.
- Log per file: page count, count of empty pages. If > 50% of a file's pages are empty, log a
  WARNING naming the file as "possibly scanned / low text yield".
- Idempotent: skip if pages.json exists unless `force=True`.

### `normalize(text: str) -> str`
Shared canonical normalization used by BOTH chunking and verification (single source of truth —
implement once, import everywhere):
- Unicode NFKC normalization
- Replace curly quotes/apostrophes with straight, en/em dashes with hyphen, non-breaking space
  with space
- Collapse all whitespace runs (spaces, tabs, newlines) to a single space
- casefold()

### `find_quote(quote: str, pages: dict) -> tuple[str, str] | None`
The heart of the hallucination guard. Given a claimed verbatim quote and one file's
`{page: text}` dict:
1. Normalize the quote and each page's text via `normalize()`.
2. Exact substring search page by page → return (source_file, page_number) on first hit.
3. If not found and the quote is > 120 normalized chars, retry with the first 120 chars (models
   sometimes paraphrase tails of long quotes).
4. Handle page-boundary spans: also search each concatenation of consecutive page pairs
   (page N + " " + page N+1); if found there, attribute to page N.
5. Return None if nothing matches. NO fuzzy matching beyond the above — similarity thresholds are
   how false citations sneak in. When in doubt, fail the match.

---

## Part B — `extract/pipeline.py`

### `chunk_pages(pages: dict, chunk_chars: int = 6000, overlap_pages: int = 1) -> list[dict]`
Build chunks that never lose page attribution:
- Walk pages in order per source file, packing consecutive pages into chunks up to ~chunk_chars
  (never split a single page across chunks; a single page longer than chunk_chars becomes its own
  chunk).
- Each chunk: `{"source_file": ..., "page_start": int, "page_end": int, "text": ...}` where text
  is the pages joined with explicit markers: `\n[PAGE 7]\n<page text>\n[PAGE 8]\n...`.
- Consecutive chunks overlap by `overlap_pages` so requirements straddling a chunk boundary are
  seen whole at least once.

### `extract_chunk(chunk: dict, client) -> list[dict]`  (MAP step)
One OpenAI chat call per chunk (model from `config.OPENAI_MODEL`, temperature 0,
`response_format={"type": "json_object"}`). System prompt requirements:
- Role: compliance analyst extracting MANDATORY requirements from a Canadian public tender.
- Extract only items a bidder MUST do/have/provide: eligibility conditions, certifications,
  bid security (type, form, amount), insurance types and limits, submission method/format/
  deadline rules, signatures, addenda acknowledgment, mandatory site meetings, evaluation
  criteria that are pass/fail. Ignore background, scope descriptions, and nice-to-haves.
- For each: category (from the schema's enum), requirement_text (plain-language restatement),
  verbatim_quote (EXACT text copied character-for-character from the chunk — instruct the model
  that quotes will be machine-verified against the source and unverifiable quotes are discarded),
  page_number (int, from the [PAGE N] markers), section_ref if visible, is_mandatory (bool),
  and the machine_checkable fields per SPEC.md (machine_checkable, check_field, check_operator,
  check_value) — machine_checkable=true ONLY for crisp numeric/boolean checks. `check_field` is
  closed to `certification | bonding_capacity | insurance_cgl | insurance_auto | region |
  submission_method`; `check_operator` is closed to `>= | <= | == | != | in`. Anything that
  cannot be represented using those enums must set machine_checkable=false and all check fields
  to null. Enforce compatible pairs: bonding/insurance use `>=`; certification/region use
  `== | in`; submission_method uses `== | != | in`.
- Return `{"requirements": [...]}`. On API error: one retry, then log and return [] for that
  chunk (never crash the run).

### `reduce_requirements(all_reqs: list[dict]) -> list[dict]`  (REDUCE step)
Merge chunk outputs (overlap causes duplicates):
- Two requirements are duplicates if their normalized verbatim_quotes are equal, or one
  normalized quote contains the other, or (same category AND same page_number AND normalized
  requirement_text equal).
- Keep the duplicate with the longer verbatim_quote. Assign ids: `{tender_id}-R{001...}`.
- Deterministic, no LLM in this step.

### `verify_requirements(reqs: list[dict], pages_by_file: dict) -> tuple[list[dict], list[dict]]`
The hallucination guard + page repair:
- For each requirement, run `find_quote` against its chunk's source file first, then (if not
  found) against every other file in the tender.
- Not found anywhere → DROP. Append to dropped list with reason "quote_not_found". Dropped items
  are written to `data/tenders/{tender_id}/dropped.json` for audit and NEVER surface in the UI.
- Found on the claimed page → verification_status = "verified".
- Found on a different page/file → overwrite page_number (and source_file) with the true
  location, verification_status = "page_repaired". Log each repair: claimed vs. true.
- Empty or whitespace-only quote → DROP, reason "empty_quote".
- Return (verified, dropped). Log the tally: extracted N, verified V, repaired R, dropped D.

### `run_extraction(tender_id: str, force: bool = False) -> dict`
Orchestrator: extract_pages → chunk → map over chunks → reduce → verify → write
`data/tenders/{tender_id}/requirements.json` (verified only) and `dropped.json`.
Return summary: `{"tender_id", "chunks", "extracted", "verified", "repaired", "dropped"}`.
Idempotent: skip tenders whose requirements.json exists unless force. Log OpenAI usage
(prompt/completion tokens) per tender so credit burn is visible.

### `__main__`
`python -m extract.pipeline [tender_id]` — run one tender, or all tenders under data/tenders/
if no argument. Print a final summary table: tender_id | chunks | extracted | verified |
repaired | dropped.

## API key handling
Read `OPENAI_API_KEY` from the environment (support a `.env` file via os.environ check +
manual parse or python-dotenv if added to requirements). Never hardcode. If the key is missing,
exit with a clear message before any processing.

## Acceptance criteria
1. `python -m extract.pipeline muskoka-lakes` produces requirements.json where EVERY record's
   verbatim_quote can be located in pages.json by find_quote (write a small self-check at the
   end of the run that re-verifies every written record and asserts 100% pass — this proves the
   invariant mechanically).
2. dropped.json exists (possibly empty) and every dropped record has a reason.
3. Page repairs are logged with claimed → true page.
4. Re-running without --force does no API calls.
5. The run never crashes on: a scanned page (empty text), an API error on one chunk, or a
   .doc file it can't parse — all are logged and skipped.

## Explicitly NOT in this module
No qualification/matching logic (spec 03), no UI (spec 04), no answer-key scoring harness (that
is a small separate script we'll add after first results).
