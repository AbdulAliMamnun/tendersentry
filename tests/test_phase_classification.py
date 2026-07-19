import json
import unittest
from types import SimpleNamespace

from extract.pipeline import _canonical_requirement, classify_requirement_phases


class _FakeCompletions:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        message = SimpleNamespace(content=json.dumps(self.payload))
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message)],
            usage=None,
        )


class PhaseClassificationTests(unittest.TestCase):
    def test_canonical_requirement_downgrades_invented_check_field(self) -> None:
        with self.assertLogs("extract.pipeline", "WARNING"):
            requirement = _canonical_requirement(
                {
                    "machine_checkable": True,
                    "check_field": "crane_age",
                    "check_operator": "<=",
                    "check_value": 15,
                }
            )

        self.assertFalse(requirement["machine_checkable"])
        self.assertIsNone(requirement["check_field"])
        self.assertIsNone(requirement["check_operator"])
        self.assertIsNone(requirement["check_value"])

    def test_canonical_requirement_downgrades_incompatible_operator(self) -> None:
        with self.assertLogs("extract.pipeline", "WARNING"):
            requirement = _canonical_requirement(
                {
                    "machine_checkable": True,
                    "check_field": "submission_method",
                    "check_operator": "<=",
                    "check_value": 15,
                }
            )

        self.assertFalse(requirement["machine_checkable"])
        self.assertIsNone(requirement["check_field"])

    def test_one_call_guards_ids_and_defaults_missing_judgments(self) -> None:
        completions = _FakeCompletions(
            {
                "judgments": [
                    {"requirement_id": "T-R001", "phase": "contract_condition"},
                    {"requirement_id": "UNKNOWN", "phase": "not_a_requirement"},
                ]
            }
        )
        client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
        requirements = [
            {
                "id": "T-R001",
                "requirement_text": "Provide insurance after award.",
                "verbatim_quote": "The Contractor shall provide insurance.",
                "category": "insurance",
            },
            {
                "id": "T-R002",
                "requirement_text": "Submit the signed form.",
                "verbatim_quote": "Bidders must submit the signed form.",
                "category": "submission",
            },
        ]

        with self.assertLogs("extract.pipeline", "WARNING") as messages:
            classified = classify_requirement_phases(requirements, client)

        self.assertEqual(len(completions.calls), 1)
        self.assertEqual(completions.calls[0]["temperature"], 0)
        self.assertEqual(
            completions.calls[0]["response_format"], {"type": "json_object"}
        )
        self.assertEqual(classified[0]["phase"], "contract_condition")
        self.assertEqual(classified[1]["phase"], "bid_phase_mandatory")
        self.assertIn("unknown id", "\n".join(messages.output))


if __name__ == "__main__":
    unittest.main()
