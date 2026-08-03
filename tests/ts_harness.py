"""Run the site's TypeScript modules from the Python test suite.

The serving path is TypeScript but its correctness is a data question, so the tests
that matter live here alongside the model. Rather than adding a second test runner and
a second CI story, this transpiles the real shipped files with `sucrase` — already
present as a Next dependency — and executes them under Node.

Two rewrites are needed because the modules are written for Next's resolver, not
Node's: the `@/` path alias, and JSON imports. Nothing else is touched, so what runs
is the code that ships.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import config


WEB_DIR = Path(config.PROJECT_ROOT) / "web"
SUCRASE = WEB_DIR / "node_modules" / ".bin" / "sucrase"

#: Modules the harness can load, mapped to their source paths.
MODULES = {
    "booster": WEB_DIR / "lib" / "booster.ts",
    "derive": WEB_DIR / "lib" / "derive.ts",
    "demoRank": WEB_DIR / "lib" / "demoRank.ts",
    "rateLimit": WEB_DIR / "lib" / "rateLimit.ts",
}

_JSON_IMPORT = re.compile(
    r'^import\s+(\w+)\s+from\s+"@/(.+?\.json)";\s*$', re.MULTILINE
)
_ALIAS_IMPORT = re.compile(r'from\s+"@/lib/(\w+)"')


def available() -> bool:
    """Whether Node and the transpiler are both present."""
    return shutil.which("node") is not None and SUCRASE.is_file()


def require(test: unittest.TestCase) -> None:
    """Skip a test when the toolchain is missing, rather than failing it."""
    if not available():
        test.skipTest("Node or sucrase unavailable; skipping TypeScript tests")


def _prepare(name: str, source: Path, destination: Path) -> None:
    """Transpile one module and rewrite its imports for plain Node."""
    text = source.read_text(encoding="utf-8")

    # JSON imports become explicit reads: Node requires an import attribute that
    # sucrase does not emit, and the absolute path keeps the temp dir self-contained.
    def replace_json(match: re.Match) -> str:
        binding, relative = match.group(1), match.group(2)
        absolute = str(WEB_DIR / relative)
        # A module may import several JSON files; each needs its own binding for the
        # reader, since ESM forbids redeclaring one.
        reader = f"__read_{binding}"
        return (
            f"import {{ readFileSync as {reader} }} from 'node:fs';\n"
            f"const {binding} = JSON.parse({reader}({absolute!r}, 'utf-8'));"
        )

    text = _JSON_IMPORT.sub(replace_json, text)
    text = _ALIAS_IMPORT.sub(r'from "./\1.mjs"', text)

    # sucrase's CLI only walks directories, so each module is staged in its own.
    staging = destination / f"_src_{name}"
    staging.mkdir(exist_ok=True)
    (staging / f"{name}.ts").write_text(text, encoding="utf-8")
    completed = subprocess.run(
        [
            str(SUCRASE),
            str(staging),
            "--out-dir",
            str(destination),
            "--transforms",
            "typescript",
            "--out-extension",
            "mjs",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"sucrase failed on {name}: {completed.stderr[:400]}")
    shutil.rmtree(staging)


def run(script: str, payload: dict | None = None) -> dict:
    """Execute `script` with every module staged alongside it; return its JSON output.

    The script may import `./derive.mjs`, `./demoRank.mjs`, and so on, and may read an
    `input` global holding `payload`.
    """
    with tempfile.TemporaryDirectory() as directory:
        destination = Path(directory)
        for name, source in MODULES.items():
            _prepare(name, source, destination)

        data_path = destination / "input.json"
        data_path.write_text(json.dumps(payload or {}), encoding="utf-8")

        entry = destination / "main.mjs"
        entry.write_text(
            "import { readFileSync } from 'node:fs';\n"
            f"const input = JSON.parse(readFileSync({str(data_path)!r}, 'utf-8'));\n"
            + script,
            encoding="utf-8",
        )
        completed = subprocess.run(
            ["node", str(entry)], capture_output=True, text=True, timeout=180
        )
        if completed.returncode != 0:
            raise RuntimeError(f"node failed: {completed.stderr[:2000]}")
        return json.loads(completed.stdout)
