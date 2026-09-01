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
    "llmExtract": WEB_DIR / "lib" / "llmExtract.ts",
    "firmLookup": WEB_DIR / "lib" / "firmLookup.ts",
    "enrichment": WEB_DIR / "lib" / "enrichment.ts",
    "freshness": WEB_DIR / "lib" / "freshness.ts",
}

#: React components the harness can mount, transpiled with the JSX transform as well.
#:
#: Kept separate from MODULES because they need `--transforms typescript,jsx` and
#: because mounting one drags in its imports: a component listed here must have every
#: first-party module it imports listed too, or the staged file will fail to resolve.
COMPONENTS = {
    "Prose": WEB_DIR / "components" / "guides" / "Prose.tsx",
    "BidConfidence": WEB_DIR / "components" / "product" / "BidConfidence.tsx",
}

#: Bare specifiers replaced with stubs rather than resolved to the real package.
#:
#: These are Next.js build-time transforms, not runtime modules. `next/font/google`
#: especially: `Archivo()` runs in the Next compiler and returns a generated class
#: name, so calling it under plain Node throws. Stubbing is not weakening the test —
#: the font is a styling detail, and what is under test is which captions are visible.
_STUBS = {
    "next/font/google": (
        "const face = () => ({ className: '', style: { fontFamily: 'stub' } });\n"
        "export const Archivo = face;\n"
        "export const Inter = face;\n"
    ),
    "next/link": (
        "import {{ createElement }} from {react!r};\n"
        "export default function Link({{ href, children, ...rest }}) {{\n"
        "  return createElement('a', {{ href, ...rest }}, children);\n"
        "}}\n"
    ),
}


def _stub_name(specifier: str) -> str:
    return "__stub_" + re.sub(r"[^A-Za-z0-9]+", "_", specifier).strip("_")

#: Bare package imports the staged modules make. Resolved through Node from the site's
#: own node_modules, so the harness loads the real dependency the site ships with
#: rather than a stub — and so a version bump that breaks the import fails here.
_BARE_IMPORT = re.compile(r'^(import .*? from )"(@[\w./-]+|[a-z][\w./-]*)";', re.MULTILINE)


def _resolve_package(specifier: str) -> str:
    """Absolute entry-point path for a bare specifier, resolved by Node itself.

    Node's ESM loader will not import a bare specifier from a temp directory outside
    the package tree, and will not accept a bare directory path either — it wants the
    entry file. Asking Node to resolve it avoids guessing at package layout.
    """
    completed = subprocess.run(
        ["node", "-e", f"console.log(require.resolve({specifier!r}))"],
        cwd=str(WEB_DIR),
        capture_output=True,
        text=True,
        timeout=60,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"cannot resolve {specifier}: {completed.stderr[:200]}")
    return completed.stdout.strip()

_JSON_IMPORT = re.compile(
    r'^import\s+(\w+)\s+from\s+"@/(.+?\.json)";\s*$', re.MULTILINE
)
_ALIAS_IMPORT = re.compile(r'from\s+"@/lib/(\w+)"')
#: Component aliases flatten to the staged basename: every component lives in one
#: temp directory, so `@/components/guides/Prose` and `@/components/product/X` both
#: resolve to `./Prose.mjs` and `./X.mjs` beside the entry script.
_ALIAS_COMPONENT = re.compile(r'from\s+"@/components/(?:[\w-]+/)*(\w+)"')


def available() -> bool:
    """Whether Node and the transpiler are both present."""
    return shutil.which("node") is not None and SUCRASE.is_file()


def require(test: unittest.TestCase) -> None:
    """Skip a test when the toolchain is missing, rather than failing it."""
    if not available():
        test.skipTest("Node or sucrase unavailable; skipping TypeScript tests")


def _prepare(name: str, source: Path, destination: Path, jsx: bool = False) -> None:
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
    text = _ALIAS_COMPONENT.sub(r'from "./\1.mjs"', text)
    # Point bare package imports at the site's own node_modules, except the
    # build-time Next specifiers, which have no runtime module to resolve to.
    def _bare(match: re.Match) -> str:
        specifier = match.group(2)
        if specifier in _STUBS:
            return f'{match.group(1)}"./{_stub_name(specifier)}.mjs";'
        return f'{match.group(1)}"{_resolve_package(specifier)}";'

    text = _BARE_IMPORT.sub(_bare, text)

    if jsx:
        # sucrase emits classic-runtime `React.createElement`, and Next's files
        # never import React because the compiler uses the automatic runtime.
        # Prepending the import is simpler than switching runtimes, which would
        # emit a bare `react/jsx-runtime` import after the rewriting has run.
        text = f'import * as React from {_resolve_package("react")!r};\n' + text

    # sucrase's CLI only walks directories, so each module is staged in its own.
    staging = destination / f"_src_{name}"
    staging.mkdir(exist_ok=True)
    (staging / f"{name}.ts{'x' if jsx else ''}").write_text(text, encoding="utf-8")
    completed = subprocess.run(
        [
            str(SUCRASE),
            str(staging),
            "--out-dir",
            str(destination),
            "--transforms",
            "typescript,jsx" if jsx else "typescript",
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


def _dom_prelude() -> str:
    """Install a jsdom window as the globals React expects, before anything imports.

    React reads `document` at module scope, so this has to run before the component
    is imported — which is why it is a prelude rather than something a test calls.
    """
    jsdom = _resolve_package("jsdom")
    return (
        f"import {{ JSDOM }} from {jsdom!r};\n"
        "const __dom = new JSDOM('<!doctype html><html><body></body></html>', "
        "{ url: 'http://localhost/', pretendToBeVisual: true });\n"
        "globalThis.window = __dom.window;\n"
        "globalThis.document = __dom.window.document;\n"
        # Node 21+ defines navigator as a getter-only global, so it has to be
        # redefined rather than assigned.
        "Object.defineProperty(globalThis, 'navigator', "
        "{ value: __dom.window.navigator, configurable: true });\n"
        "globalThis.requestAnimationFrame = __dom.window.requestAnimationFrame"
        ".bind(__dom.window);\n"
        "globalThis.cancelAnimationFrame = __dom.window.cancelAnimationFrame"
        ".bind(__dom.window);\n"
        "for (const key of ['HTMLElement','Element','Node','Event','CustomEvent',"
        "'getComputedStyle','MutationObserver','DOMParser']) "
        "globalThis[key] = __dom.window[key];\n"
        # React 19 refuses to run act() without this, and Testing Library wraps every
        # render and event in act().
        "globalThis.IS_REACT_ACT_ENVIRONMENT = true;\n"
    )


def run(script: str, payload: dict | None = None, dom: bool = False) -> dict:
    """Execute `script` with every module staged alongside it; return its JSON output.

    The script may import `./derive.mjs`, `./demoRank.mjs`, and so on, and may read an
    `input` global holding `payload`.

    With `dom=True` a jsdom window is installed first and the React components in
    COMPONENTS are staged too, so a script can mount one and inspect what it rendered.
    """
    with tempfile.TemporaryDirectory() as directory:
        destination = Path(directory)
        for name, source in MODULES.items():
            _prepare(name, source, destination)
        if dom:
            react = _resolve_package("react")
            for specifier, template in _STUBS.items():
                body = template.format(react=react) if "{react!r}" in template else template
                (destination / f"{_stub_name(specifier)}.mjs").write_text(
                    body, encoding="utf-8"
                )
            for name, source in COMPONENTS.items():
                _prepare(name, source, destination, jsx=True)

        data_path = destination / "input.json"
        data_path.write_text(json.dumps(payload or {}), encoding="utf-8")

        entry = destination / "main.mjs"
        entry.write_text(
            (_dom_prelude() if dom else "")
            + "import { readFileSync } from 'node:fs';\n"
            + f"const input = JSON.parse(readFileSync({str(data_path)!r}, 'utf-8'));\n"
            + script,
            encoding="utf-8",
        )
        completed = subprocess.run(
            ["node", str(entry)], capture_output=True, text=True, timeout=180
        )
        if completed.returncode != 0:
            raise RuntimeError(f"node failed: {completed.stderr[:2000]}")
        return json.loads(completed.stdout)
