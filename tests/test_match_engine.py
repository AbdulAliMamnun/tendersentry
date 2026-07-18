import unittest

from match.engine import _assemble_decision, evaluate_rule, numeric


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


if __name__ == "__main__":
    unittest.main()
