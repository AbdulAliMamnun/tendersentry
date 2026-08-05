/**
 * Decide whether a visitor typed a firm name or a description, and resolve it.
 *
 * **The index is the detector.** The obvious heuristic — "no trade keywords, therefore
 * a name" — is exactly backwards for this market: a Québec construction firm's name
 * usually *is* trade words. `Eurovia Québec Construction`, `Groupe Colas Québec`,
 * `Excavation Bergeron`, `Les Entreprises de Pavage`. Under a keyword rule every one
 * of them reads as a description, derives a plausible slug, and returns a cold-start
 * board while that firm's actual bidding record sits unused — confidently wrong, and
 * invisible to the person it is wrong for.
 *
 * A match against the entity index is far stronger evidence than any pattern, and it
 * costs one hash lookup. So the ordering is:
 *
 *   1. exact normalized match
 *   2. high-confidence prefix / token match
 *   3. company-marker + fuzzy match
 *   4. description path
 *
 * Ambiguity always asks. 448 of the 14,802 resolvable names are shared by more than
 * one firm; choosing between them would be guessing about which company someone means.
 *
 * Normalization mirrors `model.dataset.normalize_name` exactly — the same fold that
 * built the index, including the repeated legal-suffix strip.
 */

import firms from "@/data/model/firms.json";

/** Legal forms stripped from the tail, mirroring `model.dataset.LEGAL_SUFFIXES`. */
const LEGAL_SUFFIXES = [
  "societe en nom collectif a responsabilite limitee",
  "societe en nom collectif",
  "senc rl", "sencrl", "senc", "srl",
  "incorporee", "incorporated", "inc",
  "limitee", "ltee", "limited", "ltd", "lte",
  "corporation", "corporate", "corp",
  "compagnie", "company", "cie", "co",
  "enregistree", "enr",
  "sa", "sas", "sec", "spa", "llc", "lp", "plc",
];

/**
 * Markers that a string is a company name rather than a description. Used only as a
 * tiebreaker for near-matches — never on its own, because plenty of real firm names
 * carry none of these.
 */
const COMPANY_MARKERS =
  /\b(inc|ltee|ltée|ltd|limitee|limitée|limited|corp|corporation|senc|sencrl|srl|enr|cie|compagnie|company|co|llc|lp|group|groupe|entreprises?|construction|excavation|constructions)\b|&/i;

/** Longest input we will treat as a possible company name. */
const MAX_NAME_WORDS = 8;

export type FirmProfile = {
  id: string;
  name: string;
  normalized: string;
  bids: number;
  wins: number;
  first: string | null;
  last: string | null;
  regions: Record<string, number>;
  categories: Record<string, number>;
  buyers: Record<string, number>;
  features: Record<string, number>;
  centroid: string;
  centroid_scale: number;
};

const FIRMS = firms as unknown as {
  count: number;
  min_bids: number;
  embedding_dim: number;
  index: Record<string, string[]>;
  firms: FirmProfile[];
};

const BY_ID = new Map(FIRMS.firms.map((firm) => [firm.id, firm]));

/** Fold a firm name for comparison. Mirrors `model.dataset.normalize_name`. */
export function normalizeName(value: string): string {
  let text = (value || "")
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();

  // Repeated, so "groupe abc inc ltee" reduces to "groupe abc" the way the index did.
  let changed = true;
  while (changed && text) {
    changed = false;
    for (const suffix of LEGAL_SUFFIXES) {
      if (text.endsWith(` ${suffix}`)) {
        text = text.slice(0, -(suffix.length + 1)).trim();
        changed = true;
      }
    }
  }
  return text.replace(/\s+/g, " ").trim();
}

export type LookupOutcome =
  | { kind: "match"; profile: FirmProfile; confidence: "exact" | "prefix" | "fuzzy" }
  | { kind: "ambiguous"; candidates: FirmProfile[] }
  | { kind: "none" };

/** Resolve an input against the firm index, without guessing between candidates. */
export function lookupFirm(input: string, limit = 6): LookupOutcome {
  const normalized = normalizeName(input);
  if (!normalized) return { kind: "none" };

  // 1. Exact.
  const exact = FIRMS.index[normalized];
  if (exact?.length) return resolve(exact, "exact", limit);

  const words = normalized.split(" ");
  if (words.length > MAX_NAME_WORDS) return { kind: "none" };

  // 2. Prefix / token containment, but only when the typed name is specific enough
  //    that a prefix means something. A single short word matches half the index.
  if (normalized.length >= 6 && words.length >= 2) {
    const prefixed = Object.keys(FIRMS.index).filter(
      (key) => key.startsWith(`${normalized} `) || normalized.startsWith(`${key} `),
    );
    if (prefixed.length) {
      return resolve(prefixed.flatMap((key) => FIRMS.index[key]), "prefix", limit);
    }
  }

  // 3. Company marker plus a fuzzy match. The marker is what licenses the looser
  //    comparison; without it an unrecognised phrase is far more likely a description.
  if (COMPANY_MARKERS.test(input) && normalized.length >= 5) {
    const scored = Object.keys(FIRMS.index)
      .map((key) => ({ key, score: tokenOverlap(words, key.split(" ")) }))
      .filter((entry) => entry.score >= 0.75)
      .sort((a, b) => b.score - a.score)
      .slice(0, limit);
    if (scored.length) {
      return resolve(scored.flatMap((entry) => FIRMS.index[entry.key]), "fuzzy", limit);
    }
  }

  return { kind: "none" };
}

/** Jaccard overlap between two token lists. */
function tokenOverlap(left: string[], right: string[]): number {
  const a = new Set(left);
  const b = new Set(right);
  let shared = 0;
  for (const token of a) if (b.has(token)) shared += 1;
  const union = a.size + b.size - shared;
  return union === 0 ? 0 : shared / union;
}

/** Turn candidate ids into an outcome, ranking by activity and never guessing. */
function resolve(
  ids: string[],
  confidence: "exact" | "prefix" | "fuzzy",
  limit: number,
): LookupOutcome {
  const candidates = [...new Set(ids)]
    .map((id) => BY_ID.get(id))
    .filter((firm): firm is FirmProfile => Boolean(firm))
    // Most active first: the firm a visitor means is far more often the busy one, and
    // it puts the likeliest answer at the top of a disambiguation list.
    .sort((a, b) => b.bids - a.bids || (b.last ?? "").localeCompare(a.last ?? ""));

  if (!candidates.length) return { kind: "none" };
  if (candidates.length === 1) return { kind: "match", profile: candidates[0], confidence };
  return { kind: "ambiguous", candidates: candidates.slice(0, limit) };
}

/** Fetch one profile by id, for a visitor answering a disambiguation prompt. */
export function firmById(id: string): FirmProfile | null {
  return BY_ID.get(id) ?? null;
}

/**
 * Whether an input is worth trying as a firm name at all.
 *
 * Cheap pre-filter only. A long sentence is a description; everything shorter gets the
 * index lookup, because the index is the real evidence.
 */
export function couldBeFirmName(input: string): boolean {
  const words = normalizeName(input).split(" ").filter(Boolean);
  if (!words.length || words.length > MAX_NAME_WORDS) return false;
  // A dollar figure or a province name is description grammar, not a company name.
  if (/\$|\bjobs?\b|\bwe (?:do|build|install|lay)\b/i.test(input)) return false;
  return true;
}

/** Only aggregate facts. Never a procurement list, never an attributed amount. */
export type FirmSummary = {
  name: string;
  bids: number;
  sinceYear: string | null;
  categories: string[];
  buyerTypes: string[];
  regions: string[];
};

/** The header line: what the ranking is based on, in the firm's own record. */
export function summarize(profile: FirmProfile): FirmSummary {
  const top = (counts: Record<string, number>, n: number) =>
    Object.entries(counts)
      .sort((a, b) => b[1] - a[1])
      .slice(0, n)
      .map(([key]) => key);

  return {
    name: profile.name,
    bids: profile.bids,
    sinceYear: profile.first ? profile.first.slice(0, 4) : null,
    categories: top(profile.categories, 3),
    buyerTypes: [],
    regions: top(profile.regions, 2),
  };
}

export const FIRM_COUNT = FIRMS.count;
export const FIRM_MIN_BIDS = FIRMS.min_bids;
export const DISTINCT_NAMES = Object.keys(FIRMS.index).length;
