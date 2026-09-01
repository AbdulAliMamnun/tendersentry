/**
 * How a contract-size band is worded wherever one is shown.
 *
 * There were two of these — `DemoRanker` and `FirmLookup` — and they had already
 * drifted: the lookup said "(estimated)" where the ranker said "(estimated from
 * similar contracts)", so the same notice described its own certainty differently
 * depending on which surface you were looking at. `model/README.md` recorded the
 * duplication as an open item; adding a third copy for the example board would have
 * made a known defect worse, so this is the one implementation and all three import it.
 *
 * The rule it enforces is the one that matters: **a band is an estimate unless
 * `scale_source` is "published", and it never appears without saying which.** The
 * buyer states a value on under 1% of notices; everything else is inferred from
 * comparable past contracts, and a number shown bare would be read as the buyer's.
 */

export type ScaleRow = {
  scaleBand?: string | null;
  scaleSource?: string | null;
};

export type ScaleLabel = { text: string; estimated: boolean };

export function scaleLabel(row: ScaleRow): ScaleLabel | null {
  const band = row.scaleBand;
  const source = row.scaleSource;
  if (!band || !source) return null;
  if (source === "published") {
    return { text: `${band} (published)`, estimated: false };
  }
  if (band === "unknown" || source === "unknown") {
    return { text: "size unknown", estimated: false };
  }
  const basis =
    source === "estimated_pattern"
      ? "estimated from the wording"
      : "estimated from similar contracts";
  return { text: `~${band} (${basis})`, estimated: true };
}
