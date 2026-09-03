import type { Config } from "tailwindcss";

/**
 * White ground, teal accent, grey secondaries. Cool throughout; no warm stone, no
 * dark bands, and nothing on this site is red except where a bid is out.
 *
 *   white      #FFFFFF  page ground
 *   mist       #F4F6F6  quiet blocks, table headers, hover fills
 *   ink        #16191C  type
 *   grey       #5F676C  secondary type
 *   grey-light #8B9296  captions and tertiary type ONLY — see below
 *   rule       #DCE0E1  hairlines and borders
 *   teal       #0E5459  primary accent: links, buttons, fit scores, active nav, focus
 *   teal-mid   #14747B  hover, eyebrow
 *   teal-soft  #9FBFC2  decorative borders ONLY — see below
 *   teal-wash  #E8F0F0  chips and fills
 *   flag       #8E4034  disqualification and failure states ONLY — see below
 *
 * THREE RESERVATIONS, each measured rather than asserted. Ratios below were sampled
 * from glyph cores rendered in Chrome, not computed from these hex values.
 *
 * 1. `flag` marks disqualification and failure states, and nothing else. A blocked
 *    bid, the compliance quote that blocks it, the bid-confidence tail, and a form
 *    submission that failed. Not eyebrows, not links, not hovers, and not ordinal
 *    data: a gated procurement platform is not a disqualified bid, and spending the
 *    reserved colour on a chart is exactly how it stops being reserved. The chart
 *    ramp below therefore contains no flag. Failure states are inside the rule, not
 *    an exception to it — a rejected submission is the same semantic family as a bid
 *    that is out, and greying a validation error is an accessibility regression.
 *
 * 2. `grey-light` is for captions and tertiary text at 13px or under. It measures
 *    3.16:1 on white — under the 4.5:1 body-text threshold — and 2.91:1 on mist,
 *    under even the 3:1 non-text threshold, so it never appears on mist at all.
 *    Anything body-sized takes `grey` (5.76:1). `tests/test_palette.py` enforces both.
 *
 * 3. `teal-soft` is decorative borders only. At 1.96:1 on white it cannot carry
 *    meaning and cannot serve as a focus indicator; focus rings use `teal` (8.64:1).
 *
 * The old palette's tokens were deleted rather than aliased to new values, so a use
 * this sweep missed fails the build instead of quietly rendering warm stone. That is
 * the point: a rule written in a comment is a rule nothing executes. This file said
 * "there are no dark sections anywhere on this site" for every day that
 * /product/bid-confidence shipped a dark section, the same shape as the two drifted
 * `scaleLabel` copies before `web/lib/scale.ts` — a convention stated in prose and an
 * implementation free to ignore it. A constraint that matters belongs in something
 * that runs. Here that means spending the token instead of the literal, which makes
 * every hex outside this file a place the rule was bypassed.
 */
const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        white: "#FFFFFF",
        mist: "#F4F6F6",
        ink: "#16191C",
        grey: { DEFAULT: "#5F676C", light: "#8B9296" },
        rule: "#DCE0E1",
        teal: {
          DEFAULT: "#0E5459",
          mid: "#14747B",
          soft: "#9FBFC2",
          wash: "#E8F0F0",
        },
        flag: "#8E4034",

        /**
         * Ordinal fills for the two census charts, open to closed to unknown.
         *
         * The scales were nine and five hardcoded warm-stone hexes, coloured
         * arbitrarily; they are one ordered ramp now, which is also more honest about
         * what the data is. Teal means a notice is reachable, grey means a gated
         * platform, pale means we could not tell. `slate` and `pale` are the only two
         * steps the palette does not already supply — declared here so the charts
         * spend tokens like everything else, rather than literals.
         */
        chart: { slate: "#B4BABD", pale: "#C6CBCD" },
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
