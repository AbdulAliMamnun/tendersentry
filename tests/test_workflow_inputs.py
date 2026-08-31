"""Resolving `dry_run`, the input that could not be turned off.

`dry_run` is declared `type: boolean`, but the value reaching a workflow expression is
a **string**, and in GitHub's expression language every non-empty string is truthy —
`"false"` included. So `if: inputs.dry_run` was true with the box unchecked, the
dry-run branch ran, and `if: !inputs.dry_run` skipped the commit. No input value could
produce a commit on a manual dispatch.

Only manual dispatch was affected. On a schedule the `inputs` context does not exist,
so the value is absent, falsy under either semantics, and the job would have committed
correctly.

**What these tests can and cannot prove.** A workflow `if:` cannot be unit-tested — it
is evaluated by GitHub, not by anything here. What is testable is the decision itself,
which is why the resolution moved out of YAML and into `resolve_dry_run`. These tests
prove that function maps every input the workflow can deliver to the intended branch,
and that the two flags are exact complements by construction. They do **not** prove the
commit branch works end to end; only a real run that commits proves that.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import config
from scripts import workflow_inputs


class ResolveDryRunTests(unittest.TestCase):
    """The four cases the workflow can actually deliver, and then some."""

    def test_absent_is_not_a_dry_run(self) -> None:
        """The scheduled path. `inputs` does not exist, and cron must commit."""
        self.assertFalse(workflow_inputs.resolve_dry_run(None))
        self.assertFalse(workflow_inputs.resolve_dry_run(""))
        self.assertFalse(workflow_inputs.resolve_dry_run("   "))

    def test_the_string_false_is_not_a_dry_run(self) -> None:
        """The bug. Truthy as a bare string; false once resolved."""
        self.assertFalse(workflow_inputs.resolve_dry_run("false"))

    def test_a_real_boolean_false_is_not_a_dry_run(self) -> None:
        """If GitHub ever does deliver a boolean, the same answer comes out."""
        self.assertFalse(workflow_inputs.resolve_dry_run(False))

    def test_true_in_either_form_is_a_dry_run(self) -> None:
        self.assertTrue(workflow_inputs.resolve_dry_run("true"))
        self.assertTrue(workflow_inputs.resolve_dry_run(True))

    def test_case_and_whitespace_do_not_change_the_answer(self) -> None:
        for raw in ("False", "FALSE", " false ", "\tFalse\n"):
            self.assertFalse(workflow_inputs.resolve_dry_run(raw), repr(raw))

    def test_an_unexpected_value_fails_safe(self) -> None:
        """Manual dispatch shows the diff rather than pushing on a guess."""
        for raw in ("yes", "no", "0", "1", "maybe", "TRUE-ish", "null"):
            self.assertTrue(workflow_inputs.resolve_dry_run(raw), repr(raw))


class FlagComplementTests(unittest.TestCase):
    """The defect was the shape: two conditions that had to stay opposites."""

    def test_the_two_flags_are_always_exact_complements(self) -> None:
        for raw in (None, "", "false", False, "true", True, "yes", "0", " FALSE "):
            resolved = workflow_inputs.flags(raw)
            self.assertNotEqual(
                resolved["dry_run"],
                resolved["commit"],
                f"{raw!r} produced dry_run and commit that are not opposites",
            )
            self.assertIn(resolved["dry_run"], ("true", "false"))
            self.assertIn(resolved["commit"], ("true", "false"))

    def test_exactly_one_branch_runs_for_every_input(self) -> None:
        """Neither both branches nor neither — the failure was 'neither commits'."""
        for raw in (None, "", "false", False, "true", True, "unexpected"):
            resolved = workflow_inputs.flags(raw)
            running = [k for k, v in resolved.items() if v == "true"]
            self.assertEqual(1, len(running), f"{raw!r} selected {running}")

    def test_both_branches_are_reachable(self) -> None:
        """The claim being made: each branch has an input that selects it."""
        self.assertEqual("true", workflow_inputs.flags("true")["dry_run"])
        self.assertEqual("true", workflow_inputs.flags("false")["commit"])
        self.assertEqual("true", workflow_inputs.flags(None)["commit"])


class CommandLineTests(unittest.TestCase):
    """What the workflow step actually invokes."""

    def _run(self, raw: str | None) -> tuple[str, dict[str, str]]:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "github_output"
            output.touch()
            env = {
                "PATH": "/usr/bin:/bin",
                "GITHUB_OUTPUT": str(output),
                "PYTHONPATH": str(config.PROJECT_ROOT),
            }
            if raw is not None:
                env["DRY_RUN_RAW"] = raw
            completed = subprocess.run(
                [sys.executable, "-m", "scripts.workflow_inputs"],
                cwd=str(config.PROJECT_ROOT),
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )
            written = dict(
                line.split("=", 1)
                for line in output.read_text(encoding="utf-8").splitlines()
                if "=" in line
            )
        return completed.stdout, written

    def test_it_writes_both_flags_to_the_step_output(self) -> None:
        _, written = self._run("false")
        self.assertEqual({"dry_run": "false", "commit": "true"}, written)

    def test_an_unset_variable_resolves_to_a_committing_run(self) -> None:
        """DRY_RUN_RAW is unset on a schedule, because inputs does not exist."""
        _, written = self._run(None)
        self.assertEqual({"dry_run": "false", "commit": "true"}, written)

    def test_it_says_which_mode_it_chose(self) -> None:
        """An operator reading the log should not have to infer it."""
        stdout, _ = self._run("true")
        self.assertIn("Dry run", stdout)
        stdout, _ = self._run("false")
        self.assertIn("Live run", stdout)


class WorkflowWiringTests(unittest.TestCase):
    """The YAML must branch on the resolved value and never negate it."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.source = (
            Path(config.PROJECT_ROOT) / ".github" / "workflows" / "daily-refresh.yml"
        ).read_text(encoding="utf-8")

    def test_no_condition_reads_the_raw_input(self) -> None:
        for bad in ("if: steps.stage.outputs.changed == 'true' && inputs.dry_run",
                    "!inputs.dry_run"):
            self.assertNotIn(
                bad,
                self.source,
                "a condition still branches on the raw input, which is truthy as the "
                "string 'false'",
            )

    def test_every_branch_reads_the_resolved_flags(self) -> None:
        self.assertEqual(2, self.source.count("steps.flags.outputs.dry_run == 'true'"))
        self.assertEqual(1, self.source.count("steps.flags.outputs.commit == 'true'"))

    def test_the_resolution_step_exists_and_runs_first(self) -> None:
        resolve = self.source.index("python -m scripts.workflow_inputs")
        first_use = self.source.index("steps.flags.outputs")
        self.assertLess(resolve, first_use, "the flags are used before they are set")

    def test_the_raw_input_is_passed_as_an_environment_variable(self) -> None:
        self.assertIn("DRY_RUN_RAW: ${{ inputs.dry_run }}", self.source)


if __name__ == "__main__":
    unittest.main()
