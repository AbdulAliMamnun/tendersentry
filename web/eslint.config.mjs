import js from "@eslint/js";
import next from "@next/eslint-plugin-next";
import reactHooks from "eslint-plugin-react-hooks";
import tseslint from "typescript-eslint";

/**
 * Flat config, run through the ESLint CLI.
 *
 * There was no lint configuration at all before this: `package.json` carried a `lint`
 * script pointing at `next lint`, ESLint itself was never installed, and running the
 * script dropped into an interactive setup prompt rather than checking anything. So
 * the repository has never been linted, and `next lint` is deprecated and removed in
 * Next 16 — wiring that up would have been building on a stated end date.
 *
 * `@next/eslint-plugin-next` directly rather than `eslint-config-next`: the latter
 * still pulls `@rushstack/eslint-patch`, which throws on ESLint 9 ("Failed to patch
 * ESLint because the calling module was not recognized"). The plugin is the same rules
 * without the legacy shim.
 *
 * The rule that earns this on its own is `no-unused-vars`. A computed value that is
 * never read is how the Bid Confidence captions shipped overlapping: the opacity array
 * was calculated correctly and never passed to anything, which is legal TypeScript and
 * invisible to a build. `noUnusedLocals` in tsconfig catches the same class at compile
 * time; this catches it in the editor, before a build runs.
 */
export default tseslint.config(
  { ignores: [".next/**", "node_modules/**", "next-env.d.ts"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    plugins: { "@next/next": next, "react-hooks": reactHooks },
    rules: {
      ...next.configs.recommended.rules,
      ...next.configs["core-web-vitals"].rules,
      // The dependency-array rule is why this plugin is here rather than only the
      // TypeScript ones: a scroll effect that closes over a stale value is the same
      // shape of bug as an unused one, and neither the compiler nor a test would see it.
      "react-hooks/exhaustive-deps": "warn",
      "react-hooks/rules-of-hooks": "error",
      // Underscore-prefixed names are the deliberate escape hatch: a parameter that
      // exists to hold a position, or a destructured value dropped on purpose.
      "@typescript-eslint/no-unused-vars": [
        "error",
        {
          argsIgnorePattern: "^_",
          varsIgnorePattern: "^_",
          caughtErrorsIgnorePattern: "^_",
        },
      ],
    },
  },
);
