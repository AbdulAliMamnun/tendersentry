"""Every module the daily refresh imports must be declared before a runner needs it.

The daily job died mid-run on `ModuleNotFoundError: No module named 'lightgbm'`, after
downloading three weeks of SEAO data. The dependency had never been in requirements.txt.
It stayed invisible because it is imported *inside* `load_estimators` rather than at
module top level — so nothing that merely imports the project's modules would have
noticed, and neither would a person reading the import block at the top of a file.

This test re-derives the answer instead of trusting anyone to remember it. It walks the
import graph from the eight daily-path entry points with `ast`, following first-party
modules recursively and collecting third-party imports **wherever they appear, including
inside function bodies**, then asserts that set is covered by both:

* `requirements-daily.txt` — what the runner installs, and
* `scripts.preflight.DAILY_IMPORTS` — what the preflight step checks before ingest.

The two are guarded together on purpose. A dependency in the requirements file but not
in the preflight fails late and expensively; one in the preflight but not the
requirements fails every run. Either way the two lists have drifted, and drift here is
what a scheduled job cannot detect for itself.
"""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

import config
from scripts import preflight


ROOT = Path(config.PROJECT_ROOT)
REQUIREMENTS = ROOT / "requirements-daily.txt"

#: The daily refresh, step by step, as .github/workflows/daily-refresh.yml runs it.
DAILY_ENTRY_POINTS = (
    "notices/ingest.py",
    "matchrec/schema.py",
    "matchrec/trades.py",
    "matchrec/rank.py",
    "model/scale.py",
    "scripts/export_model_service.py",
    "scripts/export_demo_board.py",
    "scripts/export_firm_boards.py",
)

#: Top-level packages that are this repository rather than a dependency.
FIRST_PARTY = {
    "config", "notices", "matchrec", "model", "profiles",
    "scripts", "ingest", "extract", "match", "census", "eval",
}

#: Import name -> distribution name, where they differ.
DISTRIBUTION = {
    "bs4": "beautifulsoup4",
    "sentence_transformers": "sentence-transformers",
    "sklearn": "scikit-learn",
}

#: Reached through `model.train`, which the exporter imports for LEAKY_FEATURES and
#: Split, but only ever *called* under --refit — which the daily job cannot run, because
#: the slim database has no corpus to refit from. Excluded deliberately rather than
#: silently: it is a real import edge that no daily run can traverse.
NOT_ON_THE_DAILY_PATH = {"sklearn"}


def _module_path(dotted: str) -> Path | None:
    candidate = ROOT / (dotted.replace(".", "/") + ".py")
    if candidate.is_file():
        return candidate
    package = ROOT / dotted.replace(".", "/") / "__init__.py"
    return package if package.is_file() else None


def third_party_imports() -> dict[str, set[str]]:
    """Walk the daily path and return {package: {"file [where]"}}.

    Deliberately static rather than importing anything: an import-based check can only
    see what the branches it happens to execute pull in, which is the blind spot that
    let lightgbm through.
    """
    stdlib = set(sys.stdlib_module_names)
    found: dict[str, set[str]] = {}
    seen: set[Path] = set()

    def visit(path: Path) -> None:
        if path in seen:
            return
        seen.add(path)
        tree = ast.parse(path.read_text(encoding="utf-8"))

        deferred: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for inner in ast.walk(node):
                    if isinstance(inner, (ast.Import, ast.ImportFrom)):
                        deferred.add(id(inner))

        relative = path.relative_to(ROOT).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:  # relative import, same package
                    continue
                base = node.module or ""
                # `from model import embeddings` names a MODULE, not an attribute, so
                # both the package and package.name have to be tried. Missing this is
                # how an earlier version of this walk reported three packages instead
                # of seven.
                names = [base] + [f"{base}.{alias.name}" for alias in node.names]
            else:
                continue

            where = "function-level" if id(node) in deferred else "top-level"
            for name in names:
                top = name.split(".")[0]
                if not top or top in stdlib:
                    continue
                if top in FIRST_PARTY:
                    child = _module_path(name)
                    if child is not None:
                        visit(child)
                else:
                    found.setdefault(top, set()).add(f"{relative} [{where}]")

    for entry in DAILY_ENTRY_POINTS:
        visit(ROOT / entry)
    return found


def declared_distributions() -> set[str]:
    """Distribution names pinned in requirements-daily.txt, normalised."""
    names: set[str] = set()
    for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        name = line.split("==")[0].split(">=")[0].split("[")[0].strip()
        names.add(name.lower().replace("_", "-"))
    return names


class DailyDependencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.imports = third_party_imports()
        cls.needed = set(cls.imports) - NOT_ON_THE_DAILY_PATH
        cls.declared = declared_distributions()
        cls.preflight = {name for name, _ in preflight.DAILY_IMPORTS}

    def test_the_walk_finds_something(self) -> None:
        """A walk that silently found nothing would pass every other test here."""
        self.assertGreaterEqual(len(self.needed), 6, f"only found {sorted(self.needed)}")
        self.assertIn("lightgbm", self.needed, "the walk missed the function-level import")
        self.assertIn("sentence_transformers", self.needed)

    def test_every_import_is_in_the_pinned_requirements(self) -> None:
        missing = []
        for package in sorted(self.needed):
            dist = DISTRIBUTION.get(package, package).lower().replace("_", "-")
            if dist not in self.declared:
                sites = ", ".join(sorted(self.imports[package]))
                missing.append(f"  {package} (install as '{dist}') — imported by {sites}")
        self.assertFalse(
            missing,
            "\nThe daily path imports modules that requirements-daily.txt does not "
            "install:\n" + "\n".join(missing)
            + "\n\nThe runner will fail partway through, after downloading SEAO data. "
            "Add them, pinned, and re-resolve the closure.",
        )

    def test_every_import_is_in_the_preflight(self) -> None:
        missing = []
        for package in sorted(self.needed):
            if package not in self.preflight:
                sites = ", ".join(sorted(self.imports[package]))
                missing.append(f"  {package} — imported by {sites}")
        self.assertFalse(
            missing,
            "\nThe daily path imports modules scripts/preflight.py does not check:\n"
            + "\n".join(missing)
            + "\n\nThe preflight would pass and the job would still die mid-run. Add "
              "them to DAILY_IMPORTS.",
        )

    def test_the_preflight_checks_nothing_the_daily_path_does_not_use(self) -> None:
        """A stale entry makes the preflight fail on a dependency nobody needs."""
        extra = self.preflight - set(self.imports)
        self.assertFalse(
            extra,
            f"\nscripts/preflight.py checks {sorted(extra)}, which the daily path no "
            "longer imports. Remove them, or the preflight blocks the job over a "
            "dependency that was dropped.",
        )

    def test_the_function_level_imports_are_recorded_as_such(self) -> None:
        """The two that caused this. If either becomes top-level the note is stale."""
        for package in ("lightgbm", "sentence_transformers"):
            sites = self.imports[package]
            self.assertTrue(
                all("function-level" in site for site in sites),
                f"{package} is now imported at top level somewhere: {sorted(sites)}. "
                "That is fine, but the reasoning in scripts/preflight.py and "
                "requirements-daily.txt describes it as function-level only.",
            )

    def test_the_daily_requirements_are_fully_pinned(self) -> None:
        """An unattended job must not be able to install a version nobody chose."""
        unpinned = []
        for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if not line or line.startswith("-"):
                continue
            if "==" not in line:
                unpinned.append(line)
        self.assertFalse(
            unpinned,
            f"\nUnpinned in requirements-daily.txt: {unpinned}\n"
            "This file is installed every morning by a job nobody watches. A "
            "transitive release is a cron that breaks on a day the data quietly "
            "stops refreshing.",
        )

    def test_the_daily_set_excludes_what_only_the_dev_path_needs(self) -> None:
        """streamlit, openai and pdfplumber are not the cron's business.

        openai especially: the daily job must make no API calls, and the cheapest way
        to keep that true is for the library not to be installed.
        """
        for absent in ("streamlit", "openai", "pdfplumber"):
            self.assertNotIn(absent, self.declared)


if __name__ == "__main__":
    unittest.main()
