import type { Config } from "tailwindcss";

/**
 * Warm editorial, light throughout. There are no dark sections anywhere on this
 * site: #292524 is type and primary buttons, never a background band.
 *
 * That sentence was here, unchanged, for every day `/product/bid-confidence`
 * shipped a full-height `#14171A` panel with gold and orange on it. The page was
 * ported from a mockup and kept the mockup's identity — its own font, its own
 * palette — and nothing objected, because a rule written in a comment is a rule
 * nothing executes. The same shape as the two drifted `scaleLabel` copies before
 * `web/lib/scale.ts`: a convention stated in prose, an implementation free to
 * ignore it, and the drift visible only to someone who happened to look.
 *
 * The general lesson, since it will recur: a constraint that matters belongs in
 * something that runs. Where the constraint is expressible as a token, that means
 * spending the token instead of a literal — the palette below is the enforcement
 * mechanism, and every hex outside this file is a place it was bypassed.
 */
const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        page: "#faf9f7",
        card: "#ffffff",
        hairline: "#f0ede6",
        heading: "#292524",
        body: "#57534e",
        muted: "#a8a29e",
        brand: { red: "#A32D2D", redSoft: "#FCEBEB" },
        fit: { green: "#477054", greenSoft: "#eaf5ed" },
      },
      borderRadius: { card: "16px", pill: "999px", control: "12px" },
      maxWidth: { shell: "1040px" },
      fontFamily: {
        sans: [
          "-apple-system", "BlinkMacSystemFont", "Segoe UI", "Roboto",
          "Helvetica Neue", "Arial", "sans-serif",
        ],
      },
    },
  },
  plugins: [],
};
export default config;
