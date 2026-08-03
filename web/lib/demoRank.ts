/**
 * Rank today's open tenders against a free-text firm description.
 *
 * Two stages, mirroring `matchrec`: a deterministic filter, then the model.
 *
 * **Stage 1 — filter.** Drop tenders whose closing date has passed, and — only when
 * the description names a province — tenders posted to a different one. National
 * ("CA") and region-unknown notices always survive, because excluding a notice for a
 * field it never published would hide real work.
 *
 * **Stage 2 — score.** The firm is a cold start: it has no bidding history, so every
 * `firm_*` and `cross_*` history feature is zero, exactly as the parity fixtures
 * assert. The one live signal is `cross_embedding_similarity`, computed between the
 * tender's MiniLM vector and a firm vector built as the centroid of the trade slugs
 * the description matched.
 *
 * Declared job size is applied *after* the model, as the same bounded ±10 modifier
 * `matchrec.scoring` uses — not as a feature. Feeding a declared median bid into
 * `firm_median_bid` while `firm_interactions` stays zero would hand the model a
 * combination it never saw in training, and a gradient-boosted tree given an
 * out-of-distribution row returns a confident number rather than an error.
 *
 * Everything here is arithmetic over cached arrays. Nothing calls out, and no
 * description is stored.
 */

import booster from "@/data/model/booster.json";
import pool from "@/data/model/pool.json";
import slugs from "@/data/model/slugs.json";
import { score, vectorize, type Booster } from "@/lib/booster";
import { derive, readingLine, type Derived } from "@/lib/derive";

const BOOSTER = booster as unknown as Booster;
const CENTROIDS = (slugs as { centroids: Record<string, number[]> }).centroids;

type PoolTender = {
  tender_id: number;
  source: string;
  title: string;
  buyer: string;
  buyer_type: string | null;
  region: string | null;
  value: number | null;
  closing_date: string;
  url: string | null;
  trade_slugs: string[];
  mapping_status: string;
};

const POOL = pool as unknown as {
  generated_at: string;
  count: number;
  embedding_dim: number;
  embeddings_base64: string;
  tenders: PoolTender[];
  tender_features: Record<string, number>[];
};

/** The recency value a firm with no observed history carries. */
const COLD_START_DAYS_SINCE_LAST = 3650;

/** Bounded value adjustment, matching `matchrec.scoring.value_modifier`. */
const VALUE_MAX_POINTS = 10;
const VALUE_SIGMA_RATIO = 0.6;

/**
 * Decode the pool embeddings once. 2,003 × 384 float32 is ~3 MB; doing this per
 * request would cost more than the ranking itself.
 */
let EMBEDDINGS: Float32Array | null = null;
function embeddings(): Float32Array {
  if (EMBEDDINGS === null) {
    const bytes = Buffer.from(POOL.embeddings_base64, "base64");
    EMBEDDINGS = new Float32Array(
      bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength),
    );
  }
  return EMBEDDINGS;
}

function tenderVector(index: number): Float32Array {
  const dim = POOL.embedding_dim;
  return embeddings().subarray(index * dim, (index + 1) * dim);
}

/** Mean of the matched slug centroids, L2-normalized. Null when nothing matched. */
function firmVector(matched: string[]): Float64Array | null {
  const present = matched.filter((slug) => CENTROIDS[slug]);
  if (!present.length) return null;

  const dim = POOL.embedding_dim;
  const mean = new Float64Array(dim);
  for (const slug of present) {
    const centroid = CENTROIDS[slug];
    for (let i = 0; i < dim; i += 1) mean[i] += centroid[i];
  }
  let norm = 0;
  for (let i = 0; i < dim; i += 1) {
    mean[i] /= present.length;
    norm += mean[i] * mean[i];
  }
  norm = Math.sqrt(norm);
  if (norm === 0) return null;
  for (let i = 0; i < dim; i += 1) mean[i] /= norm;
  return mean;
}

function cosine(firm: Float64Array, tender: Float32Array): number {
  let dot = 0;
  let tenderNorm = 0;
  for (let i = 0; i < firm.length; i += 1) {
    dot += firm[i] * tender[i];
    tenderNorm += tender[i] * tender[i];
  }
  tenderNorm = Math.sqrt(tenderNorm);
  // The firm vector is already unit length, so only the tender side needs dividing.
  return tenderNorm === 0 ? 0 : dot / tenderNorm;
}

/** True when a tender is open to a firm working in `regions`. */
function regionAllows(tenderRegion: string | null, regions: string[]): boolean {
  if (!regions.length) return true;
  if (!tenderRegion) return true;
  const codes = tenderRegion.split(",").map((code) => code.trim());
  // "CA" marks a nationally-open notice, not a region the firm has to match.
  if (codes.includes("CA")) return true;
  return codes.some((code) => regions.includes(code));
}

/**
 * Bounded ±10 on a declared typical job size, or 0 when either side is unknown.
 * Never a penalty for an absent signal — the same rule Stage 2 of matchrec follows.
 */
export function valueModifier(
  tenderValue: number | null,
  declared: number | null,
): number {
  if (!tenderValue || !declared || tenderValue <= 0 || declared <= 0) return 0;
  const sigma = Math.max(VALUE_SIGMA_RATIO * declared, 1);
  const fit = Math.exp(-0.5 * ((tenderValue - declared) / sigma) ** 2);
  return (2 * fit - 1) * VALUE_MAX_POINTS;
}

export type RankedTender = {
  title: string;
  buyer: string;
  region: string | null;
  value: number | null;
  closingDate: string;
  url: string | null;
  source: string;
  tradeSlugs: string[];
  fit: number;
};

export type RankResult = {
  reading: string;
  derived: Derived;
  results: RankedTender[];
  poolSize: number;
  considered: number;
  /** Candidates actually carrying one of the matched trades. */
  onTrade: number;
  generatedAt: string;
};

/**
 * Below this many on-trade notices the ranking is padding out a list rather than
 * finding work, and the widget says so. Ontario is the live case: its municipal
 * notices sit behind portals we monitor rather than harvest, so a province filter can
 * leave a genuinely thin pool.
 */
const THIN_POOL = 5;

export function isThin(result: RankResult): boolean {
  return result.derived.hit && result.onTrade < THIN_POOL;
}

export type RankOptions = {
  /** Injected so tests are not clock-dependent. ISO date, `YYYY-MM-DD`. */
  today?: string;
  limit?: number;
};

/** Rank the open pool for one description. */
export function rank(description: string, options: RankOptions = {}): RankResult {
  const today = options.today ?? new Date().toISOString().slice(0, 10);
  const limit = options.limit ?? 10;

  const derived = derive(description);
  const firm = firmVector(derived.slugs);

  const candidates: number[] = [];
  let onTrade = 0;
  POOL.tenders.forEach((tender, index) => {
    if (tender.closing_date && tender.closing_date < today) return;
    if (!regionAllows(tender.region, derived.regions)) return;
    candidates.push(index);
    if (tender.trade_slugs.some((slug) => derived.slugs.includes(slug))) onTrade += 1;
  });

  // With no trade matched there is no firm vector, so every tender would score on its
  // own attributes alone and the "ranking" would be identical for any unrecognised
  // description. Returning nothing is the honest result; the caller shows why.
  if (!derived.hit) {
    return {
      reading: readingLine(derived),
      derived,
      results: [],
      poolSize: POOL.tenders.length,
      considered: candidates.length,
      onTrade: 0,
      generatedAt: POOL.generated_at,
    };
  }

  const raw = candidates.map((index) => {
    const similarity = firm ? cosine(firm, tenderVector(index)) : 0;
    const values: Record<string, number> = {
      ...POOL.tender_features[index],
      firm_days_since_last: COLD_START_DAYS_SINCE_LAST,
      cross_embedding_similarity: similarity,
    };
    return score(BOOSTER, vectorize(BOOSTER, values));
  });

  // LambdaRank emits an unbounded relevance score; only its ordering is meaningful.
  // Rescaling to 0..100 makes the modifier comparable and gives the UI a bar to draw,
  // but the number is relative to today's pool and is labelled as such.
  const lowest = raw.length ? Math.min(...raw) : 0;
  const highest = raw.length ? Math.max(...raw) : 0;
  const span = highest - lowest;

  const ranked = candidates.map((index, position) => {
    const tender = POOL.tenders[index];
    const normalized = span > 0 ? (100 * (raw[position] - lowest)) / span : 50;
    const fit = Math.min(
      100,
      Math.max(0, normalized + valueModifier(tender.value, derived.valueBand)),
    );
    return {
      title: tender.title,
      buyer: tender.buyer,
      region: tender.region,
      value: tender.value,
      closingDate: tender.closing_date,
      url: tender.url,
      source: tender.source,
      tradeSlugs: tender.trade_slugs,
      fit: Math.round(fit * 10) / 10,
    };
  });

  ranked.sort((a, b) => b.fit - a.fit || a.closingDate.localeCompare(b.closingDate));

  return {
    reading: readingLine(derived),
    derived,
    results: ranked.slice(0, limit),
    poolSize: POOL.tenders.length,
    considered: candidates.length,
    onTrade,
    generatedAt: POOL.generated_at,
  };
}
