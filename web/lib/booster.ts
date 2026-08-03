/**
 * A dependency-free evaluator for a LightGBM booster.
 *
 * There is no usable LightGBM runtime for Node, and neither a Python function nor a
 * ~50 MB ONNX runtime is worth carrying for what a gradient-boosted tree actually is:
 * nested threshold comparisons. This walks the dumped tree structures directly.
 *
 * Parity with the Python model is asserted by `tests/test_service_parity.py`, which
 * scores the same fixture vectors through both and requires agreement to 1e-6. That
 * test is the load-bearing wall of the serving path — if it fails, nothing here can
 * be trusted, because a wrong score looks exactly like a right one.
 */

export type TreeNode = {
  split_feature?: number;
  threshold?: number;
  decision_type?: string;
  default_left?: boolean;
  left_child?: TreeNode;
  right_child?: TreeNode;
  leaf_value?: number;
};

export type Booster = {
  feature_names: string[];
  trees: { tree_structure: TreeNode }[];
  num_trees: number;
  objective: string;
};

/**
 * Evaluate one tree.
 *
 * LightGBM's convention: go left when `value <= threshold`. A missing value follows
 * `default_left`. Both are reproduced exactly rather than approximated, because a
 * subtly wrong traversal still returns a number.
 */
function walk(node: TreeNode, featureValues: Float64Array): number {
  let current = node;
  while (current.leaf_value === undefined) {
    const index = current.split_feature;
    if (index === undefined || current.left_child === undefined || current.right_child === undefined) {
      return 0;
    }
    const value = featureValues[index];
    const threshold = current.threshold ?? 0;
    const goLeft = Number.isNaN(value)
      ? current.default_left !== false
      : value <= threshold;
    current = goLeft ? current.left_child : current.right_child;
  }
  return current.leaf_value;
}

/** Sum every tree's contribution. LambdaRank output is a raw ranking score. */
export function score(booster: Booster, featureValues: Float64Array): number {
  let total = 0;
  for (const tree of booster.trees) {
    total += walk(tree.tree_structure, featureValues);
  }
  return total;
}

/**
 * Order feature values to match the booster's own feature order.
 *
 * The manifest calls this authoritative for a reason: reading features in the wrong
 * order produces plausible-looking nonsense, not an error, so the mapping is done
 * once here from names rather than by positional assumption anywhere else.
 */
export function vectorize(
  booster: Booster,
  values: Record<string, number>,
): Float64Array {
  const vector = new Float64Array(booster.feature_names.length);
  booster.feature_names.forEach((name, index) => {
    vector[index] = values[name] ?? 0;
  });
  return vector;
}
