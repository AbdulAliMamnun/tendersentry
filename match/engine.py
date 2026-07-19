"""Hybrid deterministic and LLM tender qualification engine."""

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


LOGGER = logging.getLogger(__name__)
MAX_JUDGMENT_BATCH = 10
SUBMISSION_METHODS = {"email", "portal", "physical", "fax", "mail", "in_person"}
PORTAL_HINTS = {
    "ariba",
    "biddingo",
    "bids&tenders",
    "bidsandtenders",
    "bonfire",
    "canadabuys",
    "merx",
}

JUDGMENT_SYSTEM_PROMPT = """You are a bid qualification analyst.

For each supplied mandatory requirement, judge whether the firm profile satisfies it.
Use only information explicitly present in the profile. If needed information is absent,
return uncertain; never assume unstated capabilities.

Every judgment must contain requirement_id, verdict (satisfied, not_satisfied, or uncertain),
and a one-sentence rationale referring to the substance of that requirement. Judge only the
provided requirement ids. A rule_context field may explain why deterministic evaluation was
inconclusive; make the best judgment possible from the profile and requirement, while remaining
uncertain when the profile lacks the needed facts. Return one JSON object shaped as
{"judgments": [...]} and include every supplied requirement exactly once.
"""


def numeric(value: Any) -> float | None:
    """Parse a currency-like number or magnitude, returning None for percentages."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().casefold()
    if not text or "%" in text or "percent" in text:
        return None
    match = re.search(r"(-?\d+(?:,\d{3})*(?:\.\d+)?)\s*(m|k|million|thousand)?", text)
    if match is None:
        return None
    number = float(match.group(1).replace(",", ""))
    suffix = match.group(2)
    if suffix in {"m", "million"}:
        number *= 1_000_000
    elif suffix in {"k", "thousand"}:
        number *= 1_000
    return number


def _submission_methods(value: Any, context: str = "") -> set[str]:
    """Canonicalize submission values, returning no method when inference is unsafe."""
    text = str(value or "").strip().casefold()
    if not text:
        return set()
    aliases = {
        "courier": "mail",
        "e-mail": "email",
        "facsimile": "fax",
        "hand delivery": "in_person",
        "hard copy": "physical",
        "in person": "in_person",
        "in-person": "in_person",
        "post": "mail",
    }
    methods: set[str] = set()
    for item in re.split(r"\s*(?:,|/|\bor\b)\s*", text):
        normalized = item.strip().replace("-", "_")
        method = aliases.get(item.strip(), normalized)
        if method in SUBMISSION_METHODS:
            methods.add(method)
    if methods:
        return methods

    combined = f"{text} {context}".casefold()
    if "@" in text or re.search(r"\be-?mail\b", combined):
        return {"email"}
    if any(hint in combined for hint in PORTAL_HINTS) or re.search(
        r"\b(?:procurement|bidding|submission) portal\b", combined
    ):
        return {"portal"}
    if re.search(r"\b(?:fax|facsimile)\b", combined):
        return {"fax"}
    if re.search(r"\b(?:courier|mail|post)\b", combined):
        return {"mail"}
    if re.search(r"\b(?:hand deliver|in[ -]person)\b", combined):
        return {"in_person"}
    if re.search(r"\b(?:hard copy|physical)\b", combined):
        return {"physical"}
    return set()


def _comparable_submission_methods(methods: set[str]) -> set[str]:
    """Expand the physical-delivery umbrella for capability comparison."""
    comparable = set(methods)
    if "physical" in methods:
        comparable.update({"in_person", "mail"})
    if methods & {"in_person", "mail"}:
        comparable.add("physical")
    return comparable


def evaluate_rule(requirement: dict, profile: dict) -> dict:
    """Evaluate one machine-checkable requirement against a company profile."""
    requirement_id = str(requirement.get("id", ""))
    field = str(requirement.get("check_field") or "").strip().casefold()
    check_value = requirement.get("check_value")

    def result(outcome: str, detail: str) -> dict:
        return {
            "requirement_id": requirement_id,
            "outcome": outcome,
            "detail": detail,
        }

    if not requirement.get("machine_checkable"):
        return result("unknown", "requirement is not marked machine-checkable")

    if field == "certification":
        required = str(check_value or "").strip()
        held = [str(item) for item in profile.get("certifications", [])]
        if not required:
            return result("unknown", "certification requirement has no check value")
        passes = required.casefold() in {item.casefold() for item in held}
        return result(
            "pass" if passes else "fail",
            f"requires {required}; profile holds {held}",
        )

    if field == "bonding_capacity":
        required = numeric(check_value)
        available = numeric(profile.get("bonding_capacity_cad"))
        if required is None or available is None:
            return result("unknown", "bonding amount is unavailable or percentage-based")
        return result(
            "pass" if available >= required else "fail",
            f"requires bonding capacity {required:g}; profile has {available:g}",
        )

    insurance_fields = {
        "insurance_cgl": "cgl_limit",
        "commercial general liability insurance": "cgl_limit",
        "insurance_auto": "auto_limit",
        "automobile liability insurance": "auto_limit",
    }
    if field in insurance_fields:
        profile_field = insurance_fields[field]
        required = numeric(check_value)
        available = numeric(profile.get("insurance", {}).get(profile_field))
        if required is None or available is None:
            return result("unknown", f"profile lacks a usable {profile_field} limit")
        return result(
            "pass" if available >= required else "fail",
            f"requires {profile_field} {required:g}; profile has {available:g}",
        )

    if field == "region":
        required_regions = (
            check_value if isinstance(check_value, list) else [check_value]
        )
        required = {str(item).strip().casefold() for item in required_regions if item}
        available = {
            str(item).strip().casefold() for item in profile.get("regions", [])
        }
        if not required:
            return result("unknown", "region requirement has no check value")
        overlap = required & available
        return result(
            "pass" if overlap else "fail",
            f"requires region {sorted(required)}; profile covers {sorted(available)}",
        )

    if field == "submission_method":
        context = " ".join(
            str(requirement.get(key) or "")
            for key in ("requirement_text", "verbatim_quote")
        )
        required = _comparable_submission_methods(
            _submission_methods(check_value, context)
        )
        operator = str(requirement.get("check_operator") or "").strip()
        available: set[str] = set()
        for item in profile.get("submission_capabilities", []):
            available.update(_submission_methods(item))
        available = _comparable_submission_methods(available)
        if not required or operator not in {"==", "!=", "in"}:
            return result(
                "unknown",
                "submission restriction cannot be inferred from profile capabilities",
            )
        if operator == "!=":
            allowed = available - required
            return result(
                "pass" if allowed else "fail",
                f"prohibits {sorted(required)}; profile can use {sorted(allowed)}",
            )
        return result(
            "pass" if required & available else "fail",
            f"requires one of {sorted(required)}; profile supports {sorted(available)}",
        )

    LOGGER.warning(
        "Unknown machine-checkable field %r for %s", field, requirement_id
    )
    return result("unknown", f"unsupported check field: {field or '<missing>'}")


def judge_fuzzy(requirements: list[dict], profile: dict, client: Any) -> list[dict]:
    """Judge requirements in batches of ten, retry omissions, and track provenance."""
    if not requirements:
        LOGGER.info(
            "Fuzzy judgment tally: sent 0, returned 0, accepted 0, "
            "coerced 0, defaulted 0"
        )
        return []
    allowed = {str(item.get("id", "")): item for item in requirements}
    accepted: dict[str, dict] = {}
    stats = {"returned": 0, "accepted": 0, "coerced": 0, "defaulted": 0}
    requirement_ids = list(allowed)

    for start in range(0, len(requirement_ids), MAX_JUDGMENT_BATCH):
        batch_ids = requirement_ids[start : start + MAX_JUDGMENT_BATCH]
        batch = [allowed[requirement_id] for requirement_id in batch_ids]
        proposed = _request_fuzzy_batch(batch, profile, client)
        _accept_fuzzy_judgments(proposed, set(batch_ids), accepted, stats)

        missing_ids = [item_id for item_id in batch_ids if item_id not in accepted]
        if missing_ids:
            LOGGER.warning(
                "Fuzzy batch omitted %d ids; retrying them once: %s",
                len(missing_ids),
                ", ".join(missing_ids),
            )
            retry_batch = [allowed[requirement_id] for requirement_id in missing_ids]
            proposed = _request_fuzzy_batch(retry_batch, profile, client)
            _accept_fuzzy_judgments(proposed, set(missing_ids), accepted, stats)

        for requirement_id in batch_ids:
            if requirement_id in accepted:
                continue
            accepted[requirement_id] = {
                "requirement_id": requirement_id,
                "verdict": "uncertain",
                "rationale": "No valid judgment was returned after one retry.",
                "source": "defaulted",
            }
            stats["defaulted"] += 1

    LOGGER.info(
        "Fuzzy judgment tally: sent %d, returned %d, accepted %d, "
        "coerced %d, defaulted %d",
        len(allowed),
        stats["returned"],
        stats["accepted"],
        stats["coerced"],
        stats["defaulted"],
    )
    return [accepted[requirement_id] for requirement_id in requirement_ids]


def _request_fuzzy_batch(
    requirements: list[dict], profile: dict, client: Any
) -> list[Any]:
    prompt_requirements = [
        {
            "requirement_id": str(requirement.get("id", "")),
            "requirement_text": requirement.get("requirement_text", ""),
            "verbatim_quote": requirement.get("verbatim_quote", ""),
            "rule_context": requirement.get("rule_context"),
        }
        for requirement in requirements
    ]
    try:
        response = client.chat.completions.create(
            model=config.OPENAI_MODEL,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": JUDGMENT_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {"profile": profile, "requirements": prompt_requirements},
                        ensure_ascii=False,
                    ),
                },
            ],
        )
        if hasattr(client, "record_usage"):
            client.record_usage(response)
        payload = json.loads(response.choices[0].message.content or "{}")
        proposed = payload.get("judgments", [])
        if not isinstance(proposed, list):
            raise ValueError("API response field 'judgments' is not a list")
        return proposed
    except Exception as exc:
        LOGGER.error("Fuzzy qualification batch failed: %s", exc)
        return []


def _accept_fuzzy_judgments(
    proposed: list[Any],
    requested_ids: set[str],
    accepted: dict[str, dict],
    stats: dict[str, int],
) -> None:
    stats["returned"] += len(proposed)
    for item in proposed:
        if not isinstance(item, dict):
            continue
        requirement_id = str(item.get("requirement_id", ""))
        if requirement_id not in requested_ids:
            LOGGER.warning("Discarding judgment for unknown id %r", requirement_id)
            continue
        if requirement_id in accepted:
            LOGGER.warning("Discarding duplicate judgment for %s", requirement_id)
            continue
        verdict = str(item.get("verdict", "uncertain")).strip().casefold()
        source = "explicit"
        if verdict not in {"satisfied", "not_satisfied", "uncertain"}:
            verdict = "uncertain"
            source = "coerced"
            stats["coerced"] += 1
        else:
            stats["accepted"] += 1
        accepted[requirement_id] = {
            "requirement_id": requirement_id,
            "verdict": verdict,
            "rationale": str(item.get("rationale", "")).strip()
            or "The model did not provide a rationale.",
            "source": source,
        }


def decide(
    tender_id: str,
    force: bool = False,
    client: Any | None = None,
) -> dict:
    """Create or reuse a qualification decision for one tender."""
    safe_tender_id = _sanitize_tender_id(tender_id)
    if not safe_tender_id:
        raise ValueError("tender_id must contain a filesystem-safe character")
    tender_dir = _tenders_dir() / safe_tender_id
    decision_path = tender_dir / "decision.json"
    if decision_path.exists() and not force:
        LOGGER.info("Reusing cached decision for %s; OpenAI usage: 0 tokens", safe_tender_id)
        return _read_json_object(decision_path)

    requirements = _read_json_list(tender_dir / "requirements.json")
    profile = _read_json_object(Path(config.PROJECT_ROOT) / "data" / "profile.json")
    mandatory = _bid_phase_requirements(requirements)

    if not mandatory:
        decision = _assemble_decision(safe_tender_id, mandatory, [], fuzzy_used=False)
        tender_dir.mkdir(parents=True, exist_ok=True)
        _write_json(decision_path, decision)
        LOGGER.info("OpenAI usage for %s: 0 prompt tokens, 0 completion tokens", safe_tender_id)
        return decision

    results: list[dict] = []
    judgment_provenance: dict[str, dict] = {}
    fuzzy_requirements: list[dict] = []
    for requirement in mandatory:
        requirement_id = str(requirement.get("id", ""))
        if requirement.get("machine_checkable") is not True:
            fuzzy_requirements.append(requirement)
            continue
        rule_result = evaluate_rule(requirement, profile)
        if rule_result["outcome"] == "unknown":
            fuzzy_requirements.append(
                {**requirement, "rule_context": rule_result["detail"]}
            )
            continue
        results.append(rule_result)
        judgment_provenance[requirement_id] = {
            "id": requirement_id,
            "verdict": {
                "pass": "satisfied",
                "fail": "not_satisfied",
            }[rule_result["outcome"]],
            "source": "rule",
        }

    usage_client: _UsageTrackingClient | None = None
    if fuzzy_requirements:
        if client is None:
            usage_client = _UsageTrackingClient(require_openai_api_key())
            client = usage_client
        judgments = judge_fuzzy(fuzzy_requirements, profile, client)
        for judgment in judgments:
            verdict = judgment["verdict"]
            results.append(
                {
                    "requirement_id": judgment["requirement_id"],
                    "outcome": {
                        "satisfied": "pass",
                        "not_satisfied": "fail",
                        "uncertain": "unknown",
                    }[verdict],
                    "detail": judgment["rationale"],
                }
            )
            judgment_provenance[judgment["requirement_id"]] = {
                "id": judgment["requirement_id"],
                "verdict": judgment["verdict"],
                "source": judgment["source"],
            }

    ordered_provenance = [
        judgment_provenance[str(requirement.get("id", ""))]
        for requirement in mandatory
    ]

    decision = _assemble_decision(
        safe_tender_id,
        mandatory,
        results,
        fuzzy_used=bool(fuzzy_requirements),
        judgments=ordered_provenance,
    )
    allowed_ids = {str(item.get("id", "")) for item in requirements}
    assert set(decision["blockers"] + decision["open_questions"]) <= allowed_ids
    assert {item["id"] for item in decision["judgments"]} == {
        str(item.get("id", "")) for item in mandatory
    }
    tender_dir.mkdir(parents=True, exist_ok=True)
    _write_json(decision_path, decision)
    LOGGER.info(
        "OpenAI usage for %s: %d prompt tokens, %d completion tokens",
        safe_tender_id,
        usage_client.prompt_tokens if usage_client else 0,
        usage_client.completion_tokens if usage_client else 0,
    )
    return decision


def _assemble_decision(
    tender_id: str,
    requirements: list[dict],
    results: list[dict],
    fuzzy_used: bool,
    judgments: list[dict] | None = None,
) -> dict:
    by_id = {str(item.get("id", "")): item for item in requirements}
    outcomes = {
        str(item.get("requirement_id", "")): str(item.get("outcome", "unknown"))
        for item in results
        if str(item.get("requirement_id", "")) in by_id
    }
    for requirement_id in by_id:
        outcomes.setdefault(requirement_id, "unknown")

    blockers = [item_id for item_id in by_id if outcomes[item_id] == "fail"]
    open_questions = [
        item_id for item_id in by_id if outcomes[item_id] == "unknown"
    ]
    if not requirements:
        verdict = "review"
        rationale = "No requirements extracted; manual review is required."
    elif blockers:
        verdict = "no_bid"
        rationale = _decision_rationale("Do not bid", blockers, by_id)
    elif open_questions:
        verdict = "review"
        rationale = _decision_rationale("Manual review required", open_questions, by_id)
    else:
        verdict = "bid"
        rationale = "Bid: the profile satisfies every extracted mandatory requirement."

    uncertain_count = len(open_questions)
    if not requirements or uncertain_count >= max(2, len(requirements) // 2):
        confidence = "low"
    elif fuzzy_used:
        confidence = "medium"
    else:
        confidence = "high"
    return {
        "tender_id": tender_id,
        "verdict": verdict,
        "blockers": blockers,
        "open_questions": open_questions,
        "rationale": rationale,
        "confidence": confidence,
        "judgments": judgments or [],
        "counts": {
            "mandatory": len(requirements),
            "passed": sum(outcome == "pass" for outcome in outcomes.values()),
            "failed": len(blockers),
            "uncertain": uncertain_count,
        },
    }


def _bid_phase_requirements(requirements: list[dict]) -> list[dict]:
    """Return only mandatory requirements that apply during bid submission."""
    return [
        item
        for item in requirements
        if item.get("is_mandatory") is True
        and item.get("phase") == "bid_phase_mandatory"
    ]


def _decision_rationale(prefix: str, ids: list[str], by_id: dict[str, dict]) -> str:
    decisive = [
        f"{item_id} ({by_id[item_id].get('requirement_text', '').strip()})"
        for item_id in ids[:3]
    ]
    suffix = f" and {len(ids) - 3} more" if len(ids) > 3 else ""
    return f"{prefix}: " + "; ".join(decisive) + suffix + "."


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
        self.completion_tokens += int(getattr(usage, "completion_tokens", 0) or 0)


def _read_json_list(path: Path) -> list[dict]:
    value = _read_json(path, default=[])
    return value if isinstance(value, list) else []


def _read_json_object(path: Path) -> dict:
    value = _read_json(path, default={})
    return value if isinstance(value, dict) else {}


def _read_json(path: Path, default: Any) -> Any:
    try:
        with path.open(encoding="utf-8") as source:
            return json.load(source)
    except (OSError, json.JSONDecodeError) as exc:
        LOGGER.warning("Could not read %s: %s", path, exc)
        return default


def _write_json(path: Path, value: Any) -> None:
    with path.open("w", encoding="utf-8") as output:
        json.dump(value, output, ensure_ascii=False, indent=2)
        output.write("\n")


def _tenders_dir() -> Path:
    path = Path(config.DATA_DIR)
    return path if path.is_absolute() else Path(config.PROJECT_ROOT) / path


def _sanitize_tender_id(tender_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", str(tender_id)).strip("_-")


def _print_summary(rows: list[tuple[dict, dict[str, dict]]]) -> None:
    headers = [
        "tender_id",
        "verdict",
        "mandatory",
        "passed",
        "failed",
        "uncertain",
        "blocker",
    ]
    rendered: list[list[str]] = []
    for decision, requirements_by_id in rows:
        blocker_id = decision.get("blockers", [None])[0] if decision.get("blockers") else None
        blocker_text = (
            str(requirements_by_id.get(blocker_id, {}).get("requirement_text", ""))
            if blocker_id
            else ""
        )
        if len(blocker_text) > 50:
            blocker_text = blocker_text[:47] + "..."
        counts = decision["counts"]
        rendered.append(
            [
                str(decision["tender_id"]),
                str(decision["verdict"]),
                str(counts["mandatory"]),
                str(counts["passed"]),
                str(counts["failed"]),
                str(counts["uncertain"]),
                blocker_text,
            ]
        )
    widths = [
        max([len(headers[index]), *(len(row[index]) for row in rendered)])
        for index in range(len(headers))
    ]

    def format_row(row: list[str]) -> str:
        return " | ".join(
            value.ljust(widths[index]) for index, value in enumerate(row)
        )

    print(format_row(headers))
    print("-+-".join("-" * width for width in widths))
    for row in rendered:
        print(format_row(row))


def _main() -> None:
    parser = argparse.ArgumentParser(description="Qualify tender requirements")
    parser.add_argument("tender_id", nargs="?")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    tenders_dir = _tenders_dir()
    if args.tender_id:
        tender_ids = [args.tender_id]
    else:
        tender_ids = [
            path.name
            for path in sorted(tenders_dir.iterdir())
            if path.is_dir() and (path / "requirements.json").exists()
        ] if tenders_dir.exists() else []

    rows: list[tuple[dict, dict[str, dict]]] = []
    for tender_id in tender_ids:
        decision = decide(tender_id, force=args.force)
        requirements = _read_json_list(
            tenders_dir / _sanitize_tender_id(tender_id) / "requirements.json"
        )
        rows.append((decision, {str(item.get("id", "")): item for item in requirements}))
    _print_summary(rows)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    _main()
