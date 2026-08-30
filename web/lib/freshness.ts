import manifest from "@/data/model/manifest.json";

/**
 * When the data behind the site was last exported.
 *
 * Read from the serving manifest rather than from any individual artifact, because
 * the manifest is the one file every export rewrites — including a run that ingested
 * nothing. That makes it a *last verified* stamp rather than a *last changed* one,
 * which is the honest thing to show a visitor: "we checked today and this is what
 * the market looks like" is true on a quiet day; "the data changed today" would not
 * be.
 *
 * It is deliberately a displayed fact and not an assumption. `tests/test_freshness.py`
 * fails the suite when this value goes stale, so the number on the page and the
 * number the suite guards are the same number.
 */

const GENERATED_AT: string = (manifest as { generated_at: string }).generated_at;

/** ISO timestamp of the export currently shipped. */
export function generatedAt(): string {
  return GENERATED_AT;
}

/**
 * "29 August 2026" — a date a contractor can read, in the site's locale.
 *
 * Day precision, not minutes: the refresh is daily, so an exact clock time would
 * imply a freshness the pipeline does not promise.
 */
export function dataAsOf(iso: string = GENERATED_AT): string {
  const when = new Date(iso);
  if (Number.isNaN(when.getTime())) return "an unknown date";
  return when.toLocaleDateString("en-CA", {
    year: "numeric",
    month: "long",
    day: "numeric",
    timeZone: "UTC",
  });
}

/** Whole days since the export, floored. Negative clock skew reads as 0. */
export function ageInDays(iso: string = GENERATED_AT, now: Date = new Date()): number {
  const when = new Date(iso);
  if (Number.isNaN(when.getTime())) return Number.POSITIVE_INFINITY;
  return Math.max(0, Math.floor((now.getTime() - when.getTime()) / 86_400_000));
}
