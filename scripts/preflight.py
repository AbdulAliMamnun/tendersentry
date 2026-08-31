"""Fail in seconds if the daily refresh cannot possibly complete.

The daily job's first real step downloads three weeks of SEAO weekly files. Discovering
a missing dependency *after* that — which is what happened, when the scale backfill hit
``ModuleNotFoundError: No module named 'lightgbm'`` several minutes in — wastes the
download, leaves the database half-advanced, and reports a failure whose cause is four
steps removed from where it surfaced.

**This imports the third-party names directly rather than importing the project's own
modules.** That distinction is the whole point. Importing ``model.scale`` would not have
caught the failure: ``lightgbm`` is imported inside ``load_estimators``, and
``sentence_transformers`` inside ``model.embeddings.embed``. Both are invisible until
the branch that needs them actually runs, which is exactly why they survived into a
scheduled job.

Paired with ``tests/test_daily_dependencies.py``, which re-derives this list from a
static walk of the daily path and fails if the two disagree. The test guards the list;
this guards the environment. Neither substitutes for the other: a test cannot catch a
package that is declared but fails to install on the runner, and a preflight cannot
catch an import added last night that nobody declared.
"""

from __future__ import annotations

import argparse
import importlib
import sys


#: Every third-party module the daily path imports, whether at module level or inside a
#: function. Ordered cheapest-first so an obvious miss reports before torch is loaded.
#:
#: Each entry is (import name, why it is needed). The import name is what `import`
#: takes, which is not always the distribution name — bs4/beautifulsoup4,
#: sentence_transformers/sentence-transformers.
DAILY_IMPORTS: tuple[tuple[str, str], ...] = (
    ("requests", "SEAO and CanadaBuys downloads"),
    ("bs4", "bidsandtenders and CanadaBuys HTML parsing"),
    ("pandas", "the CanadaBuys CSV and the StatCan deflator"),
    ("numpy", "feature matrices, centroids, the exported pool"),
    ("lightgbm", "model.scale --backfill, imported inside load_estimators"),
    ("sentence_transformers", "model.embeddings.embed, imported inside the function"),
)

#: Import name to the distribution that provides it, where they differ. Used only to
#: make the failure message actionable.
DISTRIBUTION = {
    "bs4": "beautifulsoup4",
    "sentence_transformers": "sentence-transformers",
    "sklearn": "scikit-learn",
}


def check(verbose: bool = True) -> list[tuple[str, str, str]]:
    """Import everything the daily path needs. Returns the failures."""
    missing: list[tuple[str, str, str]] = []
    for name, reason in DAILY_IMPORTS:
        try:
            module = importlib.import_module(name)
        except Exception as error:  # ImportError, but a broken install can raise others
            missing.append((name, reason, f"{type(error).__name__}: {error}"))
            if verbose:
                print(f"  MISSING  {name:24} {reason}")
            continue
        if verbose:
            version = getattr(module, "__version__", "?")
            print(f"  ok       {name:24} {version}")
    return missing


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify the daily refresh has every module it will need"
    )
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    if not args.quiet:
        print(f"preflight: {len(DAILY_IMPORTS)} module(s), python {sys.version.split()[0]}")
    missing = check(verbose=not args.quiet)

    if not missing:
        if not args.quiet:
            print("preflight: ok")
        return 0

    print("", file=sys.stderr)
    print("::error::The daily refresh cannot run: missing dependencies", file=sys.stderr)
    for name, reason, detail in missing:
        dist = DISTRIBUTION.get(name, name)
        print(f"  {name} ({dist}) — needed for {reason}", file=sys.stderr)
        print(f"      {detail}", file=sys.stderr)
    print("", file=sys.stderr)
    print(
        "Install from the pinned set the workflow uses:\n"
        "    pip install -r requirements-daily.txt\n"
        "If the module is genuinely new, add it to requirements-daily.txt AND to\n"
        "DAILY_IMPORTS in this file — tests/test_daily_dependencies.py asserts both.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
