import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from match.engine import (
    _assemble_decision,
    _bid_phase_requirements,
    decide,
    evaluate_rule,
    judge_fuzzy,
    numeric,
)


class _SequenceCompletions:
    def __init__(self, responses: list[list[dict]]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        judgments = self.responses.pop(0)
        message = SimpleNamespace(content=json.dumps({"judgments": judgments}))
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message)],
            usage=None,
        )


def _fake_client(responses: list[list[dict]]):
    completions = _SequenceCompletions(responses)
    return SimpleNamespace(chat=SimpleNamespace(completions=completions)), completions


class MatchEngineTests(unittest.TestCase):
    def test_numeric_parses_amounts_and_rejects_percentages(self) -> None:
        self.assertEqual(numeric("$2,000,000"), 2_000_000)
        self.assertEqual(numeric("2M"), 2_000_000)
        self.assertIsNone(numeric("10% of bid price"))

    def test_certification_rule_fails_when_profile_lacks_requirement(self) -> None:
        requirement = {
            "id": "T-R001",
            "machine_checkable": True,
            "check_field": "certification",
            "check_value": "ISO 9001",
        }
        outcome = evaluate_rule(requirement, {"certifications": ["COR"]})
        self.assertEqual(outcome["outcome"], "fail")

    def test_submission_method_not_equal_uses_available_alternative(self) -> None:
        requirement = {
            "id": "T-R001",
            "machine_checkable": True,
            "check_field": "submission_method",
            "check_operator": "!=",
            "check_value": "in-person or courier",
        }
        profile = {"submission_capabilities": ["email", "portal", "physical"]}

        outcome = evaluate_rule(requirement, profile)

        self.assertEqual(outcome["outcome"], "pass")

    def test_rule_failure_takes_precedence_over_unknown(self) -> None:
        requirements = [
            {"id": "T-R001", "requirement_text": "failed requirement"},
            {"id": "T-R002", "requirement_text": "unknown requirement"},
        ]
        results = [
            {"requirement_id": "T-R001", "outcome": "fail"},
            {"requirement_id": "T-R002", "outcome": "unknown"},
        ]
        decision = _assemble_decision("T", requirements, results, fuzzy_used=True)

        self.assertEqual(decision["verdict"], "no_bid")
        self.assertEqual(decision["blockers"], ["T-R001"])
        self.assertEqual(decision["open_questions"], ["T-R002"])

    def test_only_bid_phase_requirements_are_counted(self) -> None:
        requirements = [
            {
                "id": "T-R001",
                "phase": "bid_phase_mandatory",
                "is_mandatory": True,
            },
            {
                "id": "T-R002",
                "phase": "contract_condition",
                "is_mandatory": True,
            },
            {
                "id": "T-R003",
                "phase": "not_a_requirement",
                "is_mandatory": True,
            },
        ]
        bid_phase = _bid_phase_requirements(requirements)

        self.assertEqual([item["id"] for item in bid_phase], ["T-R001"])

    def test_fuzzy_batches_retry_missing_and_track_provenance(self) -> None:
        requirements = [
            {
                "id": f"T-R{index:03d}",
                "requirement_text": f"Requirement {index}",
                "verbatim_quote": f"Quote {index}",
            }
            for index in range(12)
        ]
        first_batch = [
            {
                "requirement_id": f"T-R{index:03d}",
                "verdict": "satisfied",
                "rationale": "Supported.",
            }
            for index in range(9)
        ] + [
            {
                "requirement_id": "UNKNOWN",
                "verdict": "satisfied",
                "rationale": "Invalid id.",
            }
        ]
        client, completions = _fake_client(
            [
                first_batch,
                [
                    {
                        "requirement_id": "T-R009",
                        "verdict": "maybe",
                        "rationale": "Invalid verdict.",
                    }
                ],
                [
                    {
                        "requirement_id": "T-R010",
                        "verdict": "uncertain",
                        "rationale": "Profile is incomplete.",
                    }
                ],
                [],
            ]
        )

        with self.assertLogs("match.engine", "INFO") as messages:
            judgments = judge_fuzzy(requirements, {}, client)

        self.assertEqual(len(completions.calls), 4)
        sent_batch_sizes = [
            len(json.loads(call["messages"][1]["content"])["requirements"])
            for call in completions.calls
        ]
        self.assertTrue(all(size <= 10 for size in sent_batch_sizes))
        by_id = {item["requirement_id"]: item for item in judgments}
        self.assertEqual(by_id["T-R000"]["source"], "explicit")
        self.assertEqual(by_id["T-R009"]["source"], "coerced")
        self.assertEqual(by_id["T-R011"]["source"], "defaulted")
        self.assertIn(
            "sent 12, returned 12, accepted 10, coerced 1, defaulted 1",
            "\n".join(messages.output),
        )

    def test_rule_unknown_falls_through_to_fuzzy_with_context(self) -> None:
        client, completions = _fake_client(
            [[
                {
                    "requirement_id": "T-R001",
                    "verdict": "satisfied",
                    "rationale": "The profile confirms bonding.",
                },
                {
                    "requirement_id": "T-R002",
                    "verdict": "satisfied",
                    "rationale": "The profile confirms experience.",
                },
            ]]
        )
        requirements = [
            {
                "id": "T-R001",
                "phase": "bid_phase_mandatory",
                "is_mandatory": True,
                "machine_checkable": True,
                "check_field": "bonding_capacity",
                "check_operator": ">=",
                "check_value": 100,
                "requirement_text": "Maintain bonding capacity.",
                "verbatim_quote": "The bidder must maintain bonding capacity.",
            },
            {
                "id": "T-R002",
                "phase": "bid_phase_mandatory",
                "is_mandatory": True,
                "machine_checkable": False,
                "requirement_text": "Have similar experience.",
                "verbatim_quote": "The bidder must have similar experience.",
            },
        ]
        with tempfile.TemporaryDirectory() as temporary_directory:
            project_root = Path(temporary_directory)
            tender_dir = project_root / "data" / "tenders" / "T"
            tender_dir.mkdir(parents=True)
            (tender_dir / "requirements.json").write_text(
                json.dumps(requirements), encoding="utf-8"
            )
            (project_root / "data" / "profile.json").write_text(
                json.dumps({}), encoding="utf-8"
            )
            with patch("match.engine.config.PROJECT_ROOT", project_root):
                with patch("match.engine.config.DATA_DIR", "data/tenders"):
                    decision = decide("T", force=True, client=client)

        sent = json.loads(completions.calls[0]["messages"][1]["content"])
        sent_by_id = {
            item["requirement_id"]: item for item in sent["requirements"]
        }
        self.assertIn("bonding", sent_by_id["T-R001"]["rule_context"])
        self.assertEqual(decision["counts"]["passed"], 2)
        self.assertEqual(
            decision["judgments"],
            [
                {"id": "T-R001", "verdict": "satisfied", "source": "explicit"},
                {"id": "T-R002", "verdict": "satisfied", "source": "explicit"},
            ],
        )


if __name__ == "__main__":
    unittest.main()
