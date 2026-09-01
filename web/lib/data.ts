/**
 * Build-time data, produced by the Python export scripts.
 *
 * Nothing here is authored by hand: `scripts/export_demo_board.py` and
 * `scripts/export_census.py` read the live database and the verified extraction
 * artifacts, so a number shown on the site is a number the pipeline counted.
 */
import boardJson from "@/data/demo-board.json";
import statsJson from "@/data/stats.json";
import censusJson from "@/data/census.json";

export type BoardRow = {
  title: string;
  buyer: string;
  closing_date: string;
  source: string;
  score: number;
  /** Always paired with its source; see lib/scale.ts. */
  scale_band: string;
  scale_source: string;
};

export type Board = {
  firm: { id: number; name: string };
  rows: BoardRow[];
  candidate_count: number;
  blocker: {
    tender_id: string;
    requirement_text: string;
    quote: string;
    page: number;
    title: string;
    reason: string;
    /**
     * When the quote was extracted and checked against the source PDF.
     *
     * Displayed beside the page number, because the red row is a point-in-time
     * example and an undated one reads as current. The green rows beside it are
     * live; this one cites a tender that has never been in the pool.
     */
    extracted_at: string;
    source_file: string;
    /** sha256 of the PDF the quote was verified against. */
    source_sha256: string;
  };
};

export type Stats = {
  notices_tracked: number;
  requirements_verified: number;
  fabrications_caught: number;
  municipalities_mapped: number;
};

export type Municipality = {
  slug: string;
  name: string;
  tier: string;
  area: string;
  population: number | null;
  classification: string;
  label: string;
  confidence: string | null;
  platform: string | null;
  url: string | null;
};

export type DistributionRow = {
  classification: string;
  label: string;
  municipalities: number;
  share_of_municipalities: number;
  population: number;
  share_of_population: number;
  population_unknown: number;
  corrected?: {
    municipalities: number;
    share_of_municipalities: number;
    population: number;
    share_of_population: number;
    footnote: string;
  };
};

export type Bucket = {
  key: string;
  label: string;
  population: number;
  share_of_population: number;
};

export type Census = {
  retrieved: string;
  totals: { municipalities: number; population: number; population_matched: number };
  distribution: DistributionRow[];
  buckets: Bucket[];
  municipalities: Municipality[];
  sources: {
    register: { name: string; dataset_id: string; licence: string; url: string };
    population: { name: string; matched: string; url: string };
  };
};

export const board = boardJson as Board;
export const stats = statsJson as Stats;
export const census = censusJson as Census;

export const GITHUB_URL = "https://github.com/AbdulAliMamnun/tendersentry";

export function formatNumber(value: number): string {
  return value.toLocaleString("en-CA");
}

export function formatClosing(iso: string): string {
  if (!iso) return "date unavailable";
  const date = new Date(`${iso}T12:00:00Z`);
  return date.toLocaleDateString("en-CA", {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  });
}
