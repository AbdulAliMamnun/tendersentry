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
import type { FirmProfile } from "@/lib/firmLookup";

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
  scale_band: string;
  scale_source: string;
  scale_confidence: number;
};

/** Bands in ascending order, mirroring `model.scale.BANDS`. */
export const BANDS: readonly [string, number, number][] = [
  ["<$100K", 0, 100_000],
  ["$100\u2013500K", 100_000, 500_000],
  ["$500K\u20132M", 500_000, 2_000_000],
  ["$2\u201310M", 2_000_000, 10_000_000],
  [">$10M", 10_000_000, Infinity],
];

/** Which band an amount falls in, or "unknown". */
export function bandOf(amount: number | null): string {
  if (!amount || amount <= 0) return "unknown";
  for (const [name, low, high] of BANDS) {
    if (amount >= low && amount < high) return name;
  }
  return BANDS[BANDS.length - 1][0];
}

/** How many bands apart, or null when either side is unknown. */
export function bandDistance(a: string, b: string): number | null {
  const i = BANDS.findIndex((entry) => entry[0] === a);
  const j = BANDS.findIndex((entry) => entry[0] === b);
  return i < 0 || j < 0 ? null : Math.abs(i - j);
}

/**
 * Bounded adjustment for how well a notice's size matches the firm's declared one.
 *
 * **Applied after the model, never as a feature.** The estimate is derived from the
 * notice's title and so is the trade match; feeding one into a ranking that already
 * uses the other would make "size fit" a restatement of "trade fit" dressed as
 * independent evidence — the same failure mode as a leaked feature.
 *
 * An unknown band is neutral. We do not know how big most notices are, and a firm
 * should not be pushed away from work because our estimator had nothing to say.
 */
export function scaleModifier(noticeBand: string, declaredBand: string): number {
  const distance = bandDistance(noticeBand, declaredBand);
  if (distance === null) return 0;
  if (distance === 0) return SCALE_MATCH_BONUS;
  if (distance === 1) return 0;
  return SCALE_MISMATCH_PENALTY;
}

const SCALE_MATCH_BONUS = 8;
const SCALE_MISMATCH_PENALTY = -18;

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

/**
 * Trades that describe *upkeep* rather than construction. A notice carrying one of
 * these plus an incidental construction tag is usually a maintenance contract that
 * the mapping brushed against — "Grounds Maintenance" tagged `roadwork` alongside
 * `facility_maintenance`, `landscaping`, and `building_general`.
 */
const MAINTENANCE_SLUGS = new Set([
  "facility_maintenance",
  "landscaping",
  "snow_ice_management",
]);

/**
 * Minimum cosine for a notice to count as related to the firm's trades at all.
 *
 * Anchored on the cross-lingual calibration in `model/README.md`: French *égouts
 * pluviaux* scores 0.51 against English *watermain replacement* and 0.22 against
 * French *mobilier de bureau*. 0.35 sits between "unrelated" and "related across a
 * language boundary", so it survives the translation penalty while still rejecting a
 * notice with no vocabulary in common.
 *
 * **This floor is a backstop, not the main defence.** The pool is 65% French, so slug
 * centroids are French-dominated and English notices carry a systematic ~0.2 cosine
 * penalty — an English watermain job scores *below* an English janitorial contract.
 * Within one language the floor is meaningful; across languages it cannot be, which
 * is why eligibility is decided by trade agreement first. See the README.
 */
const RELEVANCE_FLOOR = 0.35;

/**
 * Absolute fit scale, replacing a min-max over the day's pool.
 *
 * Rescaling to the pool meant the best row always read 100, so a pool with nothing
 * relevant in it produced confident garbage — grounds maintenance at "100 fit". This
 * logistic is fixed: it maps a strong match (raw ≈ +1.5) to ~90 and a marginal one
 * (raw ≈ −6) to ~20, whatever else is in the pool that day. Being monotone in the raw
 * score, it preserves the model's ordering exactly.
 */
const FIT_CENTRE = -3.1;
const FIT_SCALE = 2.09;

function absoluteFit(rawScore: number): number {
  return 100 / (1 + Math.exp(-(rawScore - FIT_CENTRE) / FIT_SCALE));
}

/**
 * Whether a notice is genuinely in one of the firm's trades.
 *
 * Trade agreement, not cosine, is the primary gate: it comes from the deterministic
 * mapping, it is regression-tested, and — unlike the embedding — it is unaffected by
 * which language the notice was posted in.
 */
export function isOnTrade(tradeSlugs: string[], firmSlugs: string[]): boolean {
  const matched = tradeSlugs.filter((slug) => firmSlugs.includes(slug));
  if (!matched.length) return false;

  // An incidental construction tag on a maintenance contract is not the firm's work.
  // Strict minority, so a resurfacing job tagged `[roadwork, landscaping]` survives
  // while a groundskeeping contract tagged 1-of-4 does not.
  const upkeep = tradeSlugs.some((slug) => MAINTENANCE_SLUGS.has(slug));
  return !(upkeep && matched.length / tradeSlugs.length < 0.5);
}

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

/**
 * True when a tender is open to a firm working in `regions`.
 *
 * Exported because `scripts/export_model_service.py` mirrors this rule to count the
 * rankable pool per region for the manifest, and `tests/test_freshness_regions.py`
 * asserts the two agree. A silent drift would put a region count in the manifest that
 * the ranker disagrees with — the same defect class as the Python/TypeScript name
 * normalizer, which is tested the same way.
 */
export function regionAllows(tenderRegion: string | null, regions: string[]): boolean {
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
  /** Estimated or published size band, with the provenance the card must show. */
  scaleBand: string;
  scaleSource: string;
  scaleConfidence: number;
  /** Cosine between the firm centroid and this tender. The relevance signal. */
  similarity: number;
  /** Unbounded LambdaRank output. Kept for calibration; not sent to the browser. */
  rawScore: number;
};

export type RankResult = {
  reading: string;
  derived: Derived;
  results: RankedTender[];
  poolSize: number;
  considered: number;
  /** Candidates actually carrying one of the matched trades. */
  onTrade: number;
  /** The region filter actually applied, after the selector has had its say. */
  effectiveRegions: string[];
  regionSource: RegionSource;
  generatedAt: string;
};

/**
 * How many rows a full board shows. Doubles as the thinness test: if the firm's own
 * trades cannot fill the board, the pool is thin and the widget says so.
 *
 * This is deliberately not a tuned constant. The earlier version compared against a
 * free-standing 5 and let a query through with 7 "on-trade" notices, of which one was
 * a groundskeeping contract and one a lab-testing contract. Anchoring the test to
 * "could we fill the page" removes the free parameter and states something the
 * visitor can check for themselves by counting the rows.
 */
const BOARD_SIZE = 10;

export function isThin(result: RankResult): boolean {
  return result.derived.hit && result.onTrade < BOARD_SIZE;
}

export type RankOptions = {
  /** Injected so tests are not clock-dependent. ISO date, `YYYY-MM-DD`. */
  today?: string;
  limit?: number;
  /**
   * Pre-extracted fields, used when the second-tier LLM read a description the
   * keyword mapping could not. Ranking is identical either way — only the
   * understanding step differs, and the widget says which one ran.
   */
  derived?: Derived;
  /**
   * An explicit region choice from the selector, which overrides whatever the
   * description implied. `[]` means "all" — rank the unfiltered pool. Undefined means
   * the visitor has not chosen, so the derived region stands.
   *
   * Region detection already existed in the derivation step; the selector makes the
   * inferred field visible and correctable rather than adding new capability.
   */
  regionOverride?: string[];
};

/** Where the region actually in force came from, so the echo line can say. */
export type RegionSource = "derived" | "selected" | "none";

/**
 * How lopsided the shown board is by province.
 *
 * The pool is 1,297 SEAO notices against ~400 English ones, so an Ontario contractor
 * ranking the unfiltered pool can get a board that is almost entirely Québec and
 * reasonably conclude the tool is broken. It is not — it is the access asymmetry our
 * own research documents, and saying so is better than letting them guess.
 */
export function provinceSkew(
  rows: { region: string | null }[],
): { province: string; share: number } | null {
  if (!rows.length) return null;
  const counts = new Map<string, number>();
  for (const row of rows) {
    const codes = (row.region ?? "").split(",").map((code) => code.trim());
    const hasQC = codes.includes("QC");
    const hasON = codes.includes("ON");
    // Only rows that are unambiguously one province count toward a skew. National
    // ("CA") and multi-province notices belong to neither and dilute the share, which
    // makes this conservative rather than eager.
    const province = hasQC && !hasON ? "QC" : hasON && !hasQC ? "ON" : null;
    if (province) counts.set(province, (counts.get(province) ?? 0) + 1);
  }
  let best: { province: string; share: number } | null = null;
  for (const [province, count] of counts) {
    const share = count / rows.length;
    if (!best || share > best.share) best = { province, share };
  }
  return best;
}

/** Above this share of one province on an unfiltered board, we explain why. */
export const SKEW_THRESHOLD = 0.7;

/**
 * Decode an int8-quantized firm centroid.
 *
 * Quantization is what makes 15,271 firm vectors affordable to ship — 384 bytes each
 * instead of 1,536. `tests/test_profiles.py` measures how far it moves a ranking
 * rather than assuming it moves it nowhere.
 */
function decodeCentroid(profile: FirmProfile, dim: number): Float64Array {
  const bytes = Buffer.from(profile.centroid, "base64");
  const vector = new Float64Array(dim);
  for (let i = 0; i < dim && i < bytes.length; i += 1) {
    // Buffer bytes are unsigned; int8 needs the sign restored.
    const signed = bytes[i] > 127 ? bytes[i] - 256 : bytes[i];
    vector[i] = signed * profile.centroid_scale;
  }
  return vector;
}

/**
 * Cross-features for a real firm against one tender.
 *
 * This is the half a cold start cannot have: whether the firm has bid for this buyer
 * before, how much of its work is in this category, whether it operates in this
 * region. Mirrors `model.features.cross_features`.
 */
function crossFeatures(
  profile: FirmProfile,
  tender: PoolTender,
  similarity: number,
): Record<string, number> {
  const total = Math.max(profile.bids, 1);
  const categoryCount = tender.trade_slugs.reduce(
    (best, slug) => Math.max(best, profile.categories[slug] ?? 0),
    0,
  );
  const buyerCount = profile.buyers[tender.buyer] ?? 0;
  const regionCount = tender.region ? profile.regions[tender.region] ?? 0 : 0;

  const median = profile.features.firm_median_bid ?? 0;
  let valueFit = 0;
  let valueRatio = 0;
  if (median > 0 && tender.value && tender.value > 0) {
    const ratio = tender.value / median;
    valueRatio = Math.log10(ratio);
    valueFit = Math.exp(-0.5 * (valueRatio / 0.75) ** 2);
  }

  return {
    cross_category_share: categoryCount / total,
    cross_category_seen: categoryCount ? 1 : 0,
    cross_buyer_prior_bids: buyerCount,
    cross_buyer_prior_wins: Math.min(buyerCount, profile.wins),
    cross_buyer_seen: buyerCount ? 1 : 0,
    cross_region_share: regionCount / total,
    cross_region_seen: regionCount ? 1 : 0,
    cross_value_fit: valueFit,
    cross_value_ratio: valueRatio,
    cross_embedding_similarity: similarity,
  };
}

/**
 * Rank for a firm we hold a bidding record for.
 *
 * Unlike the description path this is the model at full strength: real history
 * features, a centroid built from tenders the firm actually bid on, and the cross
 * features that carry most of the leak-free model's gain. No trade gate applies —
 * the firm's own record defines relevance, and gating it by a keyword mapping would
 * discard the better evidence in favour of the weaker one.
 */
export function rankForFirm(
  profile: FirmProfile,
  options: RankOptions = {},
): RankResult {
  const today = options.today ?? new Date().toISOString().slice(0, 10);
  const limit = options.limit ?? BOARD_SIZE;
  const dim = POOL.embedding_dim;
  const firm = decodeCentroid(profile, dim);

  const candidates: number[] = [];
  POOL.tenders.forEach((tender, index) => {
    if (tender.closing_date && tender.closing_date < today) return;
    candidates.push(index);
  });

  const ranked = candidates.map((index) => {
    const tender = POOL.tenders[index];
    const similarity = cosine(firm, tenderVector(index));
    const values: Record<string, number> = {
      ...POOL.tender_features[index],
      ...profile.features,
      ...crossFeatures(profile, tender, similarity),
    };
    const rawScore = score(BOOSTER, vectorize(BOOSTER, values));
    return {
      title: tender.title,
      buyer: tender.buyer,
      region: tender.region,
      value: tender.value,
      closingDate: tender.closing_date,
      url: tender.url,
      source: tender.source,
      tradeSlugs: tender.trade_slugs,
      fit: Math.round(Math.min(100, Math.max(0, absoluteFit(rawScore))) * 10) / 10,
      scaleBand: tender.scale_band,
      scaleSource: tender.scale_source,
      scaleConfidence: tender.scale_confidence,
      similarity,
      rawScore,
    };
  });

  ranked.sort((a, b) => b.fit - a.fit || a.closingDate.localeCompare(b.closingDate));

  const derived: Derived = {
    slugs: Object.keys(profile.categories).slice(0, 3),
    regions: Object.keys(profile.regions).slice(0, 2),
    valueBand: null,
    hit: true,
  };

  return {
    reading: profile.name,
    derived,
    results: ranked.slice(0, limit),
    poolSize: POOL.tenders.length,
    considered: candidates.length,
    onTrade: ranked.length,
    effectiveRegions: derived.regions,
    regionSource: "derived",
    generatedAt: POOL.generated_at,
  };
}

/** Rank the open pool for one description. */
export function rank(description: string, options: RankOptions = {}): RankResult {
  const today = options.today ?? new Date().toISOString().slice(0, 10);
  const limit = options.limit ?? BOARD_SIZE;

  const derived = options.derived ?? derive(description);
  const firm = firmVector(derived.slugs);

  // An explicit selector choice wins over anything the description implied. On "all"
  // the visitor passes an empty array, which is a real instruction — rank everything —
  // and must not be confused with "they have not chosen yet".
  const explicit = options.regionOverride !== undefined;
  const effectiveRegions = explicit ? options.regionOverride! : derived.regions;
  const regionSource: RegionSource = explicit
    ? effectiveRegions.length
      ? "selected"
      : "none"
    : effectiveRegions.length
      ? "derived"
      : "none";

  const candidates: number[] = [];
  POOL.tenders.forEach((tender, index) => {
    if (tender.closing_date && tender.closing_date < today) return;
    if (!regionAllows(tender.region, effectiveRegions)) return;
    candidates.push(index);
  });

  // With no trade matched there is no firm vector, so every tender would score on its
  // own attributes alone and the "ranking" would be identical for any unrecognised
  // description. Returning nothing is the honest result; the caller shows why.
  if (!derived.hit) {
    return {
      reading: readingLine({ ...derived, regions: effectiveRegions }),
      derived,
      results: [],
      poolSize: POOL.tenders.length,
      considered: candidates.length,
      onTrade: 0,
      effectiveRegions,
      regionSource,
      generatedAt: POOL.generated_at,
    };
  }

  // Eligibility is decided before scoring. A notice outside the firm's trades has no
  // business on the board however the model happens to rank it, and gating first also
  // means the tree walk only runs on rows that can be shown.
  const declaredBand = bandOf(derived.valueBand);

  const eligible = candidates
    .map((index) => ({
      index,
      similarity: firm ? cosine(firm, tenderVector(index)) : 0,
    }))
    .filter((entry) => {
      const tender = POOL.tenders[entry.index];
      if (!isOnTrade(tender.trade_slugs, derived.slugs)) return false;
      if (entry.similarity < RELEVANCE_FLOOR) return false;
      // A *published* value the firm plainly does not work at is a hard filter: the
      // buyer stated the size, so there is no inference to be wrong about. An
      // estimate never filters — it only adjusts, because we might be wrong.
      if (declaredBand !== "unknown" && tender.scale_source === "published") {
        const distance = bandDistance(bandOf(tender.value), declaredBand);
        if (distance !== null && distance >= 2) return false;
      }
      return true;
    });

  const ranked = eligible.map(({ index, similarity }) => {
    const tender = POOL.tenders[index];
    const values: Record<string, number> = {
      ...POOL.tender_features[index],
      firm_days_since_last: COLD_START_DAYS_SINCE_LAST,
      cross_embedding_similarity: similarity,
    };
    const rawScore = score(BOOSTER, vectorize(BOOSTER, values));
    // Absolute, so no row can reach 100 merely by being the best of a bad pool. Both
    // adjustments are bounded and applied after the model, never inside it.
    const fit = Math.min(
      100,
      Math.max(
        0,
        absoluteFit(rawScore) +
          valueModifier(tender.value, derived.valueBand) +
          (declaredBand === "unknown" ? 0 : scaleModifier(tender.scale_band, declaredBand)),
      ),
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
      scaleBand: tender.scale_band,
      scaleSource: tender.scale_source,
      scaleConfidence: tender.scale_confidence,
      similarity,
      rawScore,
    };
  });

  ranked.sort((a, b) => b.fit - a.fit || a.closingDate.localeCompare(b.closingDate));

  return {
    // Built from the region actually applied, not from the description's. A selector
    // choice overrules the text, so echoing both would narrate a decision the visitor
    // already made and make the override look like it had not taken.
    reading: readingLine({ ...derived, regions: effectiveRegions }),
    derived,
    results: ranked.slice(0, limit),
    poolSize: POOL.tenders.length,
    considered: candidates.length,
    onTrade: ranked.length,
    effectiveRegions,
    regionSource,
    generatedAt: POOL.generated_at,
  };
}
