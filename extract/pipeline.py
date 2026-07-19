"""Extract, reduce, and verify mandatory tender requirements."""

from __future__ import annotations

import argparse
import json
import logging
import re
from pathlib import Path
from typing import Any

from openai import OpenAI

import config
from extract.env import require_openai_api_key
from extract.pages import extract_pages, find_quote, normalize


LOGGER = logging.getLogger(__name__)

CATEGORIES = {
    "eligibility",
    "bid_security",
    "submission",
    "certification",
    "insurance",
    "evaluation",
    "other_mandatory",
}

PHASES = {
    "bid_phase_mandatory",
    "contract_condition",
    "not_a_requirement",
}

CHECK_FIELDS = {
    "certification",
    "bonding_capacity",
    "insurance_cgl",
    "insurance_auto",
    "region",
    "submission_method",
}

CHECK_OPERATORS = {">=", "<=", "==", "!=", "in"}

CHECK_FIELD_OPERATORS = {
    "certification": {"==", "in"},
    "bonding_capacity": {">="},
    "insurance_cgl": {">="},
    "insurance_auto": {">="},
    "region": {"==", "in"},
    "submission_method": {"==", "!=", "in"},
}

SYSTEM_PROMPT = """You are a compliance analyst extracting MANDATORY requirements from a Canadian public tender.

Extract only items a bidder MUST do, have, or provide: eligibility conditions; certifications; bid security including type, form, and amount; insurance types and limits; submission method, format, and deadline rules; signatures; addenda acknowledgment; mandatory site meetings; and evaluation criteria that are pass/fail.

Ignore background information, scope descriptions, optional or desirable items, and nice-to-haves.

For every requirement return:
- category: one of eligibility, bid_security, submission, certification, insurance, evaluation, other_mandatory
- requirement_text: a concise plain-language restatement
- verbatim_quote: EXACT text copied character-for-character from the supplied chunk
- page_number: an integer taken from the [PAGE N] marker containing the quote
- section_ref: the visible section reference, or null
- is_mandatory: a boolean
- machine_checkable: true ONLY for crisp numeric or boolean checks such as bonding >= an amount, a certification being held, or a required region; false for fuzzy checks
- check_field: when machine_checkable, EXACTLY one of certification, bonding_capacity, insurance_cgl, insurance_auto, region, submission_method; otherwise null
- check_operator: when machine_checkable, EXACTLY one of >=, <=, ==, !=, in; otherwise null
- check_value: the structured value when machine_checkable, otherwise null

If a requirement cannot be represented using those closed check_field and check_operator lists,
set machine_checkable=false and all three check fields to null. Never invent a check_field.
Use >= for bonding and insurance minimums; == or in for certifications and regions; and ==, !=,
or in for submission methods. Other field/operator pairings are not expressible.

Quotes are machine-verified against the source. Any quote that cannot be located exactly is discarded. Never paraphrase, repair, combine, or invent verbatim_quote text.

Return one JSON object with this shape: {"requirements": [...]}.
"""

PHASE_CLASSIFICATION_PROMPT = """You classify verified Canadian tender requirements by when they apply.

For every supplied requirement, return exactly one label:
- bid_phase_mandatory: the bidder must do, have, or provide this to submit a compliant bid.
- contract_condition: the obligation applies only after contract award or only to the winning contractor.
- not_a_requirement: background, descriptive, permissive, or otherwise not an obligation.

Use the requirement text, exact source quote, and category together. Do not invent ids and do
not omit requirements. Return one JSON object shaped as:
{"judgments": [{"requirement_id": "...", "phase": "..."}]}.
"""


def chunk_pages(
    pages: dict, chunk_chars: int = 6000, overlap_pages: int = 1
) -> list[dict]:
    """Pack consecutive page-tagged text into attributed, overlapping chunks."""
    if chunk_chars <= 0:
        raise ValueError("chunk_chars must be greater than zero")
    if overlap_pages < 0:
        raise ValueError("overlap_pages cannot be negative")

    chunks: list[dict] = []
    for source_file in sorted(pages):
        file_pages = pages[source_file]
        if not isinstance(file_pages, dict) or not file_pages:
            continue
        page_numbers = _sorted_page_numbers(file_pages)
        start_index = 0
        while start_index < len(page_numbers):
            selected: list[str] = []
            selected_length = 0
            cursor = start_index
            while cursor < len(page_numbers):
                page_number = page_numbers[cursor]
                page_block = f"\n[PAGE {page_number}]\n{file_pages[page_number]}"
                if selected and selected_length + len(page_block) > chunk_chars:
                    break
                selected.append(page_number)
                selected_length += len(page_block)
                cursor += 1
                if selected_length >= chunk_chars:
                    break

            text = "".join(
                f"\n[PAGE {page_number}]\n{file_pages[page_number]}"
                for page_number in selected
            )
            chunks.append(
                {
                    "source_file": str(source_file),
                    "page_start": int(selected[0]),
                    "page_end": int(selected[-1]),
                    "text": text,
                }
            )

            end_index = start_index + len(selected) - 1
            next_index = end_index + 1 - overlap_pages
            if next_index <= start_index:
                next_index = end_index + 1
            start_index = next_index
    return chunks


def extract_chunk(chunk: dict, client: Any) -> list[dict]:
    """Propose mandatory requirements from one chunk using one retried API call."""
    for attempt in range(2):
        try:
            response = client.chat.completions.create(
                model=config.OPENAI_MODEL,
                temperature=0,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            f"Source file: {chunk['source_file']}\n"
                            f"Pages: {chunk['page_start']}-{chunk['page_end']}\n"
                            f"{chunk['text']}"
                        ),
                    },
                ],
            )
            if hasattr(client, "record_usage"):
                client.record_usage(response)
            content = response.choices[0].message.content or "{}"
            payload = json.loads(content)
            requirements = payload.get("requirements", [])
            if not isinstance(requirements, list):
                raise ValueError("API response field 'requirements' is not a list")
            return [item for item in requirements if isinstance(item, dict)]
        except Exception as exc:
            if attempt == 0:
                LOGGER.warning(
                    "Extraction failed for %s pages %s-%s; retrying once: %s",
                    chunk.get("source_file", "<unknown>"),
                    chunk.get("page_start", "?"),
                    chunk.get("page_end", "?"),
                    exc,
                )
            else:
                LOGGER.error(
                    "Extraction failed for %s pages %s-%s after retry: %s",
                    chunk.get("source_file", "<unknown>"),
                    chunk.get("page_start", "?"),
                    chunk.get("page_end", "?"),
                    exc,
                )
    return []


def reduce_requirements(all_reqs: list[dict]) -> list[dict]:
    """Deterministically canonicalize, deduplicate, and identify requirements."""
    deduplicated: list[dict] = []
    for proposed in all_reqs:
        if not isinstance(proposed, dict):
            LOGGER.warning("Skipping non-object requirement proposal: %r", proposed)
            continue
        requirement = _canonical_requirement(proposed)
        duplicate_index = next(
            (
                index
                for index, existing in enumerate(deduplicated)
                if _requirements_are_duplicates(existing, requirement)
            ),
            None,
        )
        if duplicate_index is None:
            deduplicated.append(requirement)
            continue
        existing = deduplicated[duplicate_index]
        if len(requirement["verbatim_quote"]) > len(existing["verbatim_quote"]):
            deduplicated[duplicate_index] = requirement

    for index, requirement in enumerate(deduplicated, start=1):
        requirement["id"] = f"{requirement['tender_id']}-R{index:03d}"
    return deduplicated


def verify_requirements(
    reqs: list[dict], pages_by_file: dict
) -> tuple[list[dict], list[dict]]:
    """Verify exact quotes, repair provenance, and return verified and dropped lists."""
    verified: list[dict] = []
    dropped: list[dict] = []
    repaired_count = 0
    verified_count = 0

    for requirement in reqs:
        quote = str(requirement.get("verbatim_quote", ""))
        if not quote.strip():
            dropped.append({**requirement, "reason": "empty_quote"})
            LOGGER.warning("Dropping %s: empty_quote", requirement.get("id", "<unknown>"))
            continue

        claimed_file = str(requirement.get("source_file", ""))
        claimed_page = _as_int(requirement.get("page_number"), default=0)
        found = None
        if claimed_file in pages_by_file:
            found = find_quote(
                quote, {claimed_file: pages_by_file[claimed_file]}
            )
        if found is None:
            for source_file in sorted(pages_by_file):
                if source_file == claimed_file:
                    continue
                found = find_quote(
                    quote, {source_file: pages_by_file[source_file]}
                )
                if found is not None:
                    break

        if found is None:
            dropped.append({**requirement, "reason": "quote_not_found"})
            LOGGER.warning(
                "Dropping %s: quote_not_found", requirement.get("id", "<unknown>")
            )
            continue

        true_file, true_page_text = found
        true_page = int(true_page_text)
        checked = dict(requirement)
        if true_file == claimed_file and true_page == claimed_page:
            checked["verification_status"] = "verified"
            verified_count += 1
        else:
            checked["source_file"] = true_file
            checked["page_number"] = true_page
            checked["verification_status"] = "page_repaired"
            repaired_count += 1
            LOGGER.info(
                "Page repair %s: %s page %s -> %s page %s",
                checked.get("id", "<unknown>"),
                claimed_file,
                claimed_page,
                true_file,
                true_page,
            )
        verified.append(checked)

    LOGGER.info(
        "Verification tally: extracted %d, verified %d, repaired %d, dropped %d",
        len(reqs),
        verified_count,
        repaired_count,
        len(dropped),
    )
    return verified, dropped


def classify_requirement_phases(
    requirements: list[dict], client: Any
) -> list[dict]:
    """Classify all verified requirements in one guarded, fail-safe API call."""
    if not requirements:
        return []

    by_id = {str(item.get("id", "")): item for item in requirements}
    phases = {requirement_id: "bid_phase_mandatory" for requirement_id in by_id}
    payload = [
        {
            "requirement_id": requirement_id,
            "requirement_text": requirement.get("requirement_text", ""),
            "verbatim_quote": requirement.get("verbatim_quote", ""),
            "category": requirement.get("category", ""),
        }
        for requirement_id, requirement in by_id.items()
    ]
    try:
        response = client.chat.completions.create(
            model=config.OPENAI_MODEL,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": PHASE_CLASSIFICATION_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {"requirements": payload}, ensure_ascii=False
                    ),
                },
            ],
        )
        if hasattr(client, "record_usage"):
            client.record_usage(response)
        content = response.choices[0].message.content or "{}"
        proposed = json.loads(content).get("judgments", [])
        if not isinstance(proposed, list):
            raise ValueError("API response field 'judgments' is not a list")
    except Exception as exc:
        LOGGER.error(
            "Phase classification failed; defaulting all requirements to "
            "bid_phase_mandatory: %s",
            exc,
        )
        proposed = []

    for judgment in proposed:
        if not isinstance(judgment, dict):
            continue
        requirement_id = str(judgment.get("requirement_id", ""))
        if requirement_id not in by_id:
            LOGGER.warning(
                "Discarding phase judgment for unknown id %r", requirement_id
            )
            continue
        phase = str(judgment.get("phase", "")).strip().casefold()
        if phase not in PHASES:
            LOGGER.warning(
                "Invalid phase %r for %s; defaulting to bid_phase_mandatory",
                phase,
                requirement_id,
            )
            continue
        phases[requirement_id] = phase

    classified = [
        {**requirement, "phase": phases[str(requirement.get("id", ""))]}
        for requirement in requirements
    ]
    counts = {
        phase: sum(item["phase"] == phase for item in classified)
        for phase in sorted(PHASES)
    }
    LOGGER.info(
        "Phase classification tally: bid-phase %d, contract conditions %d, "
        "not requirements %d",
        counts["bid_phase_mandatory"],
        counts["contract_condition"],
        counts["not_a_requirement"],
    )
    return classified


def run_extraction(tender_id: str, force: bool = False) -> dict:
    """Run extraction through verification, persist results, and return a summary."""
    safe_tender_id = _sanitize_tender_id(tender_id)
    if not safe_tender_id:
        raise ValueError("tender_id must contain a filesystem-safe character")

    tender_dir = _tenders_dir() / safe_tender_id
    requirements_path = tender_dir / "requirements.json"
    dropped_path = tender_dir / "dropped.json"
    if requirements_path.exists() and not force:
        requirements = _read_json_list(requirements_path)
        dropped = _read_json_list(dropped_path) if dropped_path.exists() else []
        LOGGER.info("Skipping cached extraction for %s", safe_tender_id)
        return {
            "tender_id": safe_tender_id,
            "chunks": 0,
            "extracted": len(requirements) + len(dropped),
            "verified": sum(
                item.get("verification_status") == "verified"
                for item in requirements
            ),
            "repaired": sum(
                item.get("verification_status") == "page_repaired"
                for item in requirements
            ),
            "dropped": len(dropped),
        }

    api_key = require_openai_api_key()
    pages_by_file = extract_pages(safe_tender_id, force=force)
    chunks = chunk_pages(pages_by_file)
    client = _UsageTrackingClient(api_key)

    proposed_requirements: list[dict] = []
    for chunk in chunks:
        for proposal in extract_chunk(chunk, client):
            proposed_requirements.append(
                {
                    **proposal,
                    "tender_id": safe_tender_id,
                    "source_file": chunk["source_file"],
                }
            )

    reduced = reduce_requirements(proposed_requirements)
    verified, dropped = verify_requirements(reduced, pages_by_file)
    verified = classify_requirement_phases(verified, client)
    tender_dir.mkdir(parents=True, exist_ok=True)
    _write_json(requirements_path, verified)
    _write_json(dropped_path, dropped)
    _assert_verified_output(verified, pages_by_file)

    verified_count = sum(
        requirement["verification_status"] == "verified"
        for requirement in verified
    )
    repaired_count = sum(
        requirement["verification_status"] == "page_repaired"
        for requirement in verified
    )
    LOGGER.info(
        "OpenAI usage for %s: %d prompt tokens, %d completion tokens",
        safe_tender_id,
        client.prompt_tokens,
        client.completion_tokens,
    )
    return {
        "tender_id": safe_tender_id,
        "chunks": len(chunks),
        "extracted": len(reduced),
        "verified": verified_count,
        "repaired": repaired_count,
        "dropped": len(dropped),
    }


class _UsageTrackingClient:
    def __init__(self, api_key: str) -> None:
        self._client = OpenAI(api_key=api_key)
        self.chat = self._client.chat
        self.prompt_tokens = 0
        self.completion_tokens = 0

    def record_usage(self, response: Any) -> None:
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        self.prompt_tokens += int(getattr(usage, "prompt_tokens", 0) or 0)
        self.completion_tokens += int(
            getattr(usage, "completion_tokens", 0) or 0
        )


def _canonical_requirement(proposed: dict) -> dict:
    category = str(proposed.get("category", "other_mandatory"))
    if category not in CATEGORIES:
        LOGGER.warning(
            "Unknown requirement category %r; using other_mandatory", category
        )
        category = "other_mandatory"
    check_value = proposed.get("check_value")
    if not isinstance(check_value, (str, int, float)) or isinstance(
        check_value, bool
    ):
        check_value = None
    machine_checkable = _as_bool(proposed.get("machine_checkable"))
    check_field = _nullable_string(proposed.get("check_field"))
    check_operator = _nullable_string(proposed.get("check_operator"))
    if check_field is not None:
        check_field = check_field.casefold()
    if check_operator is not None:
        check_operator = check_operator.casefold()
    if machine_checkable and (
        check_field not in CHECK_FIELDS
        or check_operator not in CHECK_OPERATORS
        or check_operator not in CHECK_FIELD_OPERATORS.get(check_field, set())
        or check_value is None
    ):
        LOGGER.warning(
            "Downgrading unsupported machine check field=%r operator=%r value=%r",
            check_field,
            check_operator,
            check_value,
        )
        machine_checkable = False
    if not machine_checkable:
        check_field = None
        check_operator = None
        check_value = None
    return {
        "id": "",
        "tender_id": str(proposed.get("tender_id", "")),
        "category": category,
        "phase": "bid_phase_mandatory",
        "requirement_text": str(proposed.get("requirement_text", "")),
        "verbatim_quote": str(proposed.get("verbatim_quote", "")),
        "page_number": _as_int(proposed.get("page_number"), default=0),
        "source_file": str(proposed.get("source_file", "")),
        "section_ref": _nullable_string(proposed.get("section_ref")),
        "is_mandatory": _as_bool(proposed.get("is_mandatory")),
        "machine_checkable": machine_checkable,
        "check_field": check_field,
        "check_operator": check_operator,
        "check_value": check_value,
    }


def _requirements_are_duplicates(first: dict, second: dict) -> bool:
    first_quote = normalize(first["verbatim_quote"])
    second_quote = normalize(second["verbatim_quote"])
    quote_duplicate = (
        first_quote == second_quote
        or (
            bool(first_quote and second_quote)
            and (
                first_quote in second_quote
                or second_quote in first_quote
            )
        )
    )
    semantic_duplicate = (
        first["category"] == second["category"]
        and first["page_number"] == second["page_number"]
        and normalize(first["requirement_text"])
        == normalize(second["requirement_text"])
    )
    return quote_duplicate or semantic_duplicate


def _assert_verified_output(requirements: list[dict], pages_by_file: dict) -> None:
    for requirement in requirements:
        source_file = requirement["source_file"]
        assert source_file in pages_by_file, (
            f"Verified requirement {requirement['id']} has missing source file"
        )
        found = find_quote(
            requirement["verbatim_quote"],
            {source_file: pages_by_file[source_file]},
        )
        assert found is not None, (
            f"Verified requirement {requirement['id']} quote is not locatable"
        )
        assert found == (source_file, str(requirement["page_number"])), (
            f"Verified requirement {requirement['id']} provenance changed"
        )
    LOGGER.info(
        "Invariant self-check passed for %d written requirements", len(requirements)
    )


def _read_json_list(path: Path) -> list[dict]:
    try:
        with path.open(encoding="utf-8") as source:
            value = json.load(source)
    except (OSError, json.JSONDecodeError) as exc:
        LOGGER.warning("Could not read %s: %s", path, exc)
        return []
    return value if isinstance(value, list) else []


def _write_json(path: Path, value: Any) -> None:
    with path.open("w", encoding="utf-8") as output:
        json.dump(value, output, ensure_ascii=False, indent=2)
        output.write("\n")


def _nullable_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().casefold() == "true"


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _sorted_page_numbers(file_pages: dict) -> list[str]:
    return sorted((str(page) for page in file_pages), key=lambda page: int(page))


def _tenders_dir() -> Path:
    path = Path(config.DATA_DIR)
    if path.is_absolute():
        return path
    return Path(config.PROJECT_ROOT) / path


def _sanitize_tender_id(tender_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", str(tender_id)).strip("_-")


def _print_summary(rows: list[dict]) -> None:
    headers = [
        "tender_id",
        "chunks",
        "extracted",
        "verified",
        "repaired",
        "dropped",
    ]
    string_rows = [[str(row[header]) for header in headers] for row in rows]
    all_rows = [headers, *string_rows]
    widths = [
        max(len(row[index]) for row in all_rows) for index in range(len(headers))
    ]

    def format_row(row: list[str]) -> str:
        return " | ".join(
            value.ljust(widths[index]) for index, value in enumerate(row)
        )

    print(format_row(headers))
    print("-+-".join("-" * width for width in widths))
    for row in string_rows:
        print(format_row(row))


def _main() -> None:
    parser = argparse.ArgumentParser(description="Extract tender requirements")
    parser.add_argument("tender_id", nargs="?")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    try:
        require_openai_api_key()
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc

    if args.tender_id:
        tender_ids = [args.tender_id]
    else:
        tender_ids = [
            path.name for path in sorted(_tenders_dir().iterdir()) if path.is_dir()
        ] if _tenders_dir().exists() else []

    summaries = [
        run_extraction(tender_id, force=args.force) for tender_id in tender_ids
    ]
    _print_summary(summaries)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    _main()
