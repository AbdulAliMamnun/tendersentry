"""Resolve workflow_dispatch inputs to one unambiguous value each.

`dry_run` is declared `type: boolean` in the workflow, but the value that reaches an
expression arrives as a **string**. In GitHub's expression language every non-empty
string is truthy — including `"false"` — so a manual run with the box unchecked
evaluated `inputs.dry_run` as true, took the dry-run branch, and skipped the commit.
Unchecking the box could not produce a commit, and no input value could.

The deeper defect was the shape, not the semantics. The three call sites branched on
`inputs.dry_run` and `!inputs.dry_run`: two conditions that must remain exact opposites
forever, with nothing enforcing it, over a domain where truthiness is surprising. This
module resolves the input **once** and emits both derived flags from that single
boolean, so the workflow never negates anything and the two branches cannot drift apart
from each other.

Resolution, handling both semantics because either may arrive:

* absent, null, or empty  -> **not** a dry run. This is the scheduled path, where the
  `inputs` context does not exist and the job must commit.
* ``"false"`` or ``False`` -> not a dry run, case- and whitespace-insensitive.
* anything else            -> a dry run. Manual dispatch fails safe: an unrecognised
  value shows the diff rather than pushing on a guess.
"""

from __future__ import annotations

import argparse
import os
import sys


#: Values that mean "do not dry run", beyond absent and empty. Deliberately only the
#: one word: `type: boolean` emits nothing else, and accepting "0" or "no" would invent
#: a vocabulary the workflow UI cannot produce while widening what counts as consent
#: to push.
FALSE_WORDS = frozenset({"false"})


def resolve_dry_run(raw: object) -> bool:
    """Whether this run is a dry run. See the module docstring for the rules."""
    if raw is None:
        return False
    if isinstance(raw, bool):
        return raw
    text = str(raw).strip()
    if not text:
        return False
    return text.casefold() not in FALSE_WORDS


def flags(raw: object) -> dict[str, str]:
    """Both branch flags, derived from one boolean.

    `commit` is computed here rather than by negating `dry_run` in YAML. That is the
    entire point: the complement exists once, in a place that can be tested.
    """
    dry_run = resolve_dry_run(raw)
    return {
        "dry_run": "true" if dry_run else "false",
        "commit": "false" if dry_run else "true",
    }


def _main() -> int:
    parser = argparse.ArgumentParser(
        description="Resolve workflow inputs to unambiguous flags"
    )
    parser.add_argument(
        "--input",
        default=None,
        help="raw value; defaults to the DRY_RUN_RAW environment variable",
    )
    args = parser.parse_args()

    raw = args.input if args.input is not None else os.environ.get("DRY_RUN_RAW")
    resolved = flags(raw)

    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            for key, value in resolved.items():
                handle.write(f"{key}={value}\n")

    shown = "(absent)" if raw is None else repr(str(raw))
    print(
        f"dry_run input {shown} -> dry_run={resolved['dry_run']} "
        f"commit={resolved['commit']}"
    )
    if resolved["dry_run"] == "true":
        print("::notice::Dry run — the diff will be shown and nothing committed")
    else:
        print("::notice::Live run — changes will be committed and pushed")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
