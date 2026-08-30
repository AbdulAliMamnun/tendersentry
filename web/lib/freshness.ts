import manifest from "@/data/model/manifest.json";

/**
 * How current the data behind the site actually is.
 *
 * Two timestamps travel in the manifest and they answer different questions:
 *
 * * `generated_at` — when the export last ran.
 * * `max_ingested_at` — when a new notice last arrived.
 *
 * **The displayed date is the second one.** They diverge exactly when the pipeline
 * is broken in the way that matters: an export that ingests nothing still writes a
 * fresh `generated_at`, so showing that would have told visitors the data was
 * current today while the newest notice was a month old. A date on a page is read
 * as a promise, and that one would have been false.
 *
 * `generatedAt()` is kept for the freshness suite, which checks both — whether the
 * export ran, and separately whether anything came in. The number on the page and
 * the number `tests/test_freshness.py` fails over are the same number.
 */

type Manifest = { generated_at: string; max_ingested_at?: string | null };

const GENERATED_AT: string = (manifest as Manifest).generated_at;
const MAX_INGESTED_AT: string | null =
  (manifest as Manifest).max_ingested_at ?? null;

/** ISO timestamp of when the export last ran. Not what the page displays. */
export function generatedAt(): string {
  return GENERATED_AT;
}

/** ISO timestamp of when a notice last arrived, or null on a manifest without it. */
export function maxIngestedAt(): string | null {
  return MAX_INGESTED_AT;
}

/**
 * "July 31, 2026" — a date a contractor can read, in the site's locale.
 *
 * Day precision, not minutes: the refresh is daily, so an exact clock time would
 * imply a freshness the pipeline does not promise.
 *
 * Defaults to the ingest time, never to the export time. A manifest predating
 * `max_ingested_at` renders "an unknown date" rather than falling back to
 * `generated_at` — that fallback is the precise lie this function exists to stop
 * telling, and silence is the honest failure mode.
 */
export function dataAsOf(iso: string | null = MAX_INGESTED_AT): string {
  if (!iso) return "an unknown date";
  const when = new Date(iso);
  if (Number.isNaN(when.getTime())) return "an unknown date";
  return when.toLocaleDateString("en-CA", {
    year: "numeric",
    month: "long",
    day: "numeric",
    timeZone: "UTC",
  });
}

/**
 * Whole days since data last arrived, floored. Negative clock skew reads as 0.
 *
 * Defaults to the ingest time for the same reason `dataAsOf` does: the age a visitor
 * cares about is the data's, not the export process's.
 */
export function ageInDays(
  iso: string | null = MAX_INGESTED_AT,
  now: Date = new Date(),
): number {
  if (!iso) return Number.POSITIVE_INFINITY;
  const when = new Date(iso);
  if (Number.isNaN(when.getTime())) return Number.POSITIVE_INFINITY;
  return Math.max(0, Math.floor((now.getTime() - when.getTime()) / 86_400_000));
}
