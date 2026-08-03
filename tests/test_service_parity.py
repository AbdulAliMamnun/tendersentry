"""The TypeScript scorer must agree with the Python model to 1e-6.

This is the load-bearing test of the serving path. A gradient-boosted tree evaluated
with a subtly wrong traversal — the wrong inequality, the wrong missing-value
default, features read in the wrong order — still returns a plausible number. Nothing
downstream would notice. So parity is asserted directly, by scoring the same vectors
through both implementations and comparing.

Skips (rather than fails) when the serving artifacts or Node are absent, so the suite
still runs on a machine that has not exported a model.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import numpy as np

import config


ARTIFACT_DIR = Path(config.PROJECT_ROOT) / "web" / "data" / "model"
BOOSTER_PATH = ARTIFACT_DIR / "booster.json"
SCORER_PATH = Path(config.PROJECT_ROOT) / "web" / "lib" / "booster.ts"

#: Deterministic feature vectors spanning the space: zeros, ones, a cold-start firm,
#: a heavy bidder, and pseudo-random draws.
FIXTURE_SEED = 7


def _node_available() -> bool:
    return shutil.which("node") is not None


def _fixture_vectors(feature_names: list[str], count: int = 24) -> np.ndarray:
    rng = np.random.default_rng(FIXTURE_SEED)
    width = len(feature_names)
    rows = [
        np.zeros(width),
        np.ones(width),
        np.full(width, 0.5),
    ]
    # A cold-start firm: no history, stale recency — the shape the demo actually sends.
    cold = np.zeros(width)
    for index, name in enumerate(feature_names):
        if name == "firm_days_since_last":
            cold[index] = 3650.0
    rows.append(cold)
    while len(rows) < count:
        rows.append(rng.random(width) * rng.choice([1.0, 10.0, 1000.0]))
    return np.vstack(rows)


class BoosterParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not BOOSTER_PATH.is_file():
            raise unittest.SkipTest(
                "No serving artifacts; run python3 -m scripts.export_model_service"
            )
        if not _node_available():
            raise unittest.SkipTest("Node is not available to run the TS scorer")
        cls.booster = json.loads(BOOSTER_PATH.read_text(encoding="utf-8"))
        cls.feature_names = cls.booster["feature_names"]

    def _python_scores(self, vectors: np.ndarray) -> np.ndarray:
        """Score with the tree structures directly, mirroring LightGBM's traversal."""

        def walk(node: dict, row: np.ndarray) -> float:
            while "leaf_value" not in node:
                index = node["split_feature"]
                value = row[index]
                threshold = node["threshold"]
                go_left = value <= threshold
                node = node["left_child"] if go_left else node["right_child"]
            return float(node["leaf_value"])

        return np.array(
            [
                sum(walk(tree["tree_structure"], row) for tree in self.booster["trees"])
                for row in vectors
            ]
        )

    def _node_scores(self, vectors: np.ndarray) -> np.ndarray:
        """Score by executing the shipped TypeScript, transpiled on the fly."""
        source = SCORER_PATH.read_text(encoding="utf-8")
        # Strip TypeScript annotations the Node runtime will not parse. The logic is
        # untouched; only types are removed.
        stripped = source
        for marker in ("export type TreeNode = {", "export type Booster = {"):
            start = stripped.find(marker)
            if start != -1:
                end = stripped.find("};", start)
                stripped = stripped[:start] + stripped[end + 2 :]
        stripped = (
            stripped.replace("export function", "function")
            .replace(": TreeNode", "")
            .replace(": Booster", "")
            .replace(": Float64Array", "")
            .replace(": number", "")
            .replace(": string[]", "")
            .replace(": Record<string, number>", "")
            .replace("): number", ")")
            .replace("): Float64Array", ")")
            .replace("current.leaf_value === undefined", "current.leaf_value === undefined")
        )

        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "score.mjs"
            payload = Path(directory) / "input.json"
            payload.write_text(
                json.dumps({"booster": self.booster, "vectors": vectors.tolist()}),
                encoding="utf-8",
            )
            script.write_text(
                stripped
                + "\n"
                + "import { readFileSync } from 'node:fs';\n"
                + f"const input = JSON.parse(readFileSync({str(payload)!r}, 'utf-8'));\n"
                + "const out = input.vectors.map((v) => score(input.booster, "
                + "Float64Array.from(v)));\n"
                + "process.stdout.write(JSON.stringify(out));\n",
                encoding="utf-8",
            )
            completed = subprocess.run(
                ["node", str(script)], capture_output=True, text=True, timeout=120
            )
            if completed.returncode != 0:
                self.fail(f"TS scorer failed: {completed.stderr[:400]}")
            return np.array(json.loads(completed.stdout))

    def test_the_typescript_scorer_matches_python_to_1e6(self) -> None:
        vectors = _fixture_vectors(self.feature_names)

        python = self._python_scores(vectors)
        node = self._node_scores(vectors)

        self.assertEqual(python.shape, node.shape)
        largest = float(np.max(np.abs(python - node)))
        self.assertLess(
            largest,
            1e-6,
            f"TS and Python scores diverge by {largest:.3e}; the serving path cannot "
            "be trusted until they agree",
        )

    def test_a_cold_start_vector_scores_identically(self) -> None:
        # The exact shape the demo endpoint sends for a firm with no history.
        width = len(self.feature_names)
        cold = np.zeros((1, width))
        for index, name in enumerate(self.feature_names):
            if name == "firm_days_since_last":
                cold[0, index] = 3650.0

        self.assertLess(
            float(np.max(np.abs(self._python_scores(cold) - self._node_scores(cold)))),
            1e-6,
        )

    def test_the_booster_declares_its_feature_order(self) -> None:
        self.assertTrue(self.feature_names)
        self.assertEqual(len(self.feature_names), len(set(self.feature_names)))

    def test_no_leaked_feature_is_served(self) -> None:
        from model import train

        for name in train.LEAKY_FEATURES:
            self.assertNotIn(name, self.feature_names)


if __name__ == "__main__":
    unittest.main()
