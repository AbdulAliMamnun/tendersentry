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

JUDGMENT_SYSTEM_PROMPT = """You are a bid qualification analyst.

For each supplied mandatory requirement, judge whether the firm profile satisfies it.
Use only information explicitly present in the profile. If needed information is absent,
return uncertain; never assume unstated capabilities.

Every judgment must contain requirement_id, verdict (satisfied, not_satisfied, or uncertain),
and a one-sentence rationale referring to the substance of that requirement. Judge only the
provided requirement ids. Return one JSON object shaped as {"judgments": [...]}.
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
        required = str(check_value or "").strip().casefold()
        operator = str(requirement.get("check_operator") or "").strip()
        available = {
            str(item).strip().casefold()
            for item in profile.get("submission_capabilities", [])
        }
        if not required or operator not in {"", "==", "in"}:
            return result(
                "unknown",
                "submission restriction cannot be inferred from profile capabilities",
            )
        return result(
            "pass" if required in available else "fail",
            f"requires {required}; profile supports {sorted(available)}",
        )

    LOGGER.warning(
        "Unknown machine-checkable field %r for %s", field, requirement_id
    )
    return result("unknown", f"unsupported check field: {field or '<missing>'}")


def judge_fuzzy(requirements: list[dict], profile: dict, client: Any) -> list[dict]:
    """Judge all fuzzy mandatory requirements in one guarded API call."""
    if not requirements:
        return []
    allowed = {str(item.get("id", "")): item for item in requirements}
    prompt_requirements = [
        {
            "requirement_id": requirement_id,
            "requirement_text": requirement.get("requirement_text", ""),
            "verbatim_quote": requirement.get("verbatim_quote", ""),
        }
        for requirement_id, requirement in allowed.items()
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
    except Exception as exc:
        LOGGER.error("Fuzzy qualification failed; defaulting to uncertain: %s", exc)
        proposed = []

    accepted: dict[str, dict] = {}
    for item in proposed:
        if not isinstance(item, dict):
            continue
        requirement_id = str(item.get("requirement_id", ""))
        if requirement_id not in allowed:
            LOGGER.warning("Discarding judgment for unknown id %r", requirement_id)
            continue
        verdict = str(item.get("verdict", "uncertain")).strip().casefold()
        if verdict not in {"satisfied", "not_satisfied", "uncertain"}:
            verdict = "uncertain"
        accepted[requirement_id] = {
            "requirement_id": requirement_id,
            "verdict": verdict,
            "rationale": str(item.get("rationale", "")).strip()
            or "The model did not provide a rationale.",
        }

    judgments: list[dict] = []
    for requirement_id in allowed:
        judgments.append(
            accepted.get(
                requirement_id,
                {
                    "requirement_id": requirement_id,
                    "verdict": "uncertain",
                    "rationale": "No valid judgment was returned for this requirement.",
                },
            )
        )
    return judgments


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
    mandatory = [item for item in requirements if item.get("is_mandatory") is True]

    if not mandatory:
        decision = _assemble_decision(safe_tender_id, mandatory, [], fuzzy_used=False)
        tender_dir.mkdir(parents=True, exist_ok=True)
        _write_json(decision_path, decision)
        LOGGER.info("OpenAI usage for %s: 0 prompt tokens, 0 completion tokens", safe_tender_id)
        return decision

    results = [
        evaluate_rule(requirement, profile)
        for requirement in mandatory
        if requirement.get("machine_checkable") is True
    ]
    fuzzy_requirements = [
        requirement
        for requirement in mandatory
        if requirement.get("machine_checkable") is not True
    ]

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

    decision = _assemble_decision(
        safe_tender_id,
        mandatory,
        results,
        fuzzy_used=bool(fuzzy_requirements),
    )
    allowed_ids = {str(item.get("id", "")) for item in requirements}
    assert set(decision["blockers"] + decision["open_questions"]) <= allowed_ids
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
        "counts": {
            "mandatory": len(requirements),
            "passed": sum(outcome == "pass" for outcome in outcomes.values()),
            "failed": len(blockers),
            "uncertain": uncertain_count,
        },
    }


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
