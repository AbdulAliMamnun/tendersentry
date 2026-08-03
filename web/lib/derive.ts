/**
 * Read a firm description into structured fields.
 *
 * This is the demo's whole understanding step: no model runs at request time, so the
 * ranking is only as good as what this extracts. That is a deliberate trade — see the
 * manifest notes — and it is why the widget echoes back what was understood.
 *
 * The matching mirrors `matchrec.trades`: accent-folded, casefolded, word-boundary
 * anchored, with an optional plural on the final word so "égouts" reaches "égout".
 */

import mapping from "@/data/model/mapping.json";

export type Derived = {
  slugs: string[];
  regions: string[];
  valueBand: number | null;
  hit: boolean;
};

type Rule = {
  slug: string;
  priority?: number;
  keywords_en?: string[];
  keywords_fr?: string[];
};

const RULES = (mapping as { rules: Rule[] }).rules;

/** Casefold, strip accents, collapse whitespace — the same fold used at training. */
export function fold(value: string): string {
  return (value || "")
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[‘’`]/g, "'")
    .replace(/\s+/g, " ")
    .trim();
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * One matcher per keyword. Built once at module load: rebuilding ~500 regexes per
 * request would dominate the latency budget.
 */
const MATCHERS: { slug: string; priority: number; pattern: RegExp }[] = [];
for (const rule of RULES) {
  const terms = [...(rule.keywords_en ?? []), ...(rule.keywords_fr ?? [])];
  for (const term of terms) {
    const folded = fold(term);
    if (!folded) continue;
    const parts = folded.split(" ").map(escapeRegExp);
    parts[parts.length - 1] += parts[parts.length - 1].endsWith("s") ? "" : "s?";
    MATCHERS.push({
      slug: rule.slug,
      priority: rule.priority ?? 100,
      pattern: new RegExp(`(?<![a-z0-9])${parts.join("\\s+")}(?![a-z0-9])`),
    });
  }
}

// Place names only. A bare "on" or "qc" would fire on the English preposition and on
// abbreviations inside unrelated words, so provinces are spelled out or postal-cased.
const QUEBEC = /\b(quebec|montreal|laval|gatineau|sherbrooke|saguenay|outaouais|monteregie|laurentides|estrie|mauricie|abitibi|gaspesie|beauce|rive[- ]sud|rive[- ]nord)\b/;
const ONTARIO = /\b(ontario|toronto|ottawa|hamilton|london|windsor|sudbury|simcoe|muskoka|durham|peel|halton|niagara|waterloo|kingston|barrie|thunder bay|gta|golden horseshoe)\b/;
// Postal abbreviations are only trusted when they appear uppercased in the raw text.
const QUEBEC_CODE = /\b(QC|Qc)\b/;
const ONTARIO_CODE = /\b(ON|Ont)\b/;

/**
 * Resolve a number written in either convention.
 *
 * English groups with commas ("1,500,000"); French groups with spaces and uses the
 * comma as a decimal separator ("1,5 M$"). Stripping commas unconditionally turns
 * "1,5 M$" into fifteen million — a tenfold error in the one field a contractor is
 * most likely to write in French.
 */
function normalizeNumber(raw: string): string {
  const compact = raw.replace(/\s/g, "").replace(/\.$/, "").replace(/,$/, "");
  // A lone comma trailed by one or two digits is a decimal separator; grouping
  // commas always come in threes.
  if (/^\d+,\d{1,2}$/.test(compact)) return compact.replace(",", ".");
  return compact.replace(/,/g, "");
}

/**
 * Read a dollar figure, honouring k/m suffixes and French "M$".
 * "$500K", "500 000 $", "1,5 M$", "around $2 million" all resolve.
 */
export function parseValue(text: string): number | null {
  const folded = fold(text);
  const patterns = [
    /(\d[\d\s.,]*)\s*(?:m\$|\$m|million|millions|m\b)/,
    /(\d[\d\s.,]*)\s*(?:k\$|\$k|thousand|k\b)/,
    /\$\s*(\d[\d\s.,]*)/,
    /(\d[\d\s.,]*)\s*\$/,
  ];
  const multipliers = [1_000_000, 1_000, 1, 1];
  for (let index = 0; index < patterns.length; index += 1) {
    const match = folded.match(patterns[index]);
    if (!match) continue;
    const numeric = Number.parseFloat(normalizeNumber(match[1]));
    if (Number.isFinite(numeric) && numeric > 0) {
      return numeric * multipliers[index];
    }
  }
  return null;
}

/** Extract everything derivable from a free-text description. */
export function derive(description: string): Derived {
  const folded = fold(description);

  const matched = new Map<string, number>();
  for (const matcher of MATCHERS) {
    if (!matcher.pattern.test(folded)) continue;
    const current = matched.get(matcher.slug);
    if (current === undefined || matcher.priority < current) {
      matched.set(matcher.slug, matcher.priority);
    }
  }
  const slugs = [...matched.entries()]
    .sort((a, b) => a[1] - b[1] || a[0].localeCompare(b[0]))
    .map(([slug]) => slug);

  const regions: string[] = [];
  if (QUEBEC.test(folded) || QUEBEC_CODE.test(description)) regions.push("QC");
  if (ONTARIO.test(folded) || ONTARIO_CODE.test(description)) regions.push("ON");

  return {
    slugs,
    regions,
    valueBand: parseValue(description),
    // A no-hit is the number that decides whether a real embedding service is worth
    // paying for, so it is recorded explicitly rather than inferred from an empty list.
    hit: slugs.length > 0,
  };
}

const SLUG_LABELS: Record<string, string> = {
  roadwork: "roadwork",
  sitework: "sitework",
  granular_supply: "granular supply",
  bridge_structural: "bridges & structures",
  concrete_flatwork: "concrete flatwork",
  water_wastewater: "watermain & sewer",
  utilities_underground: "underground utilities",
  building_general: "general building",
  building_envelope: "building envelope",
  electrical: "electrical",
  mechanical_hvac: "mechanical & HVAC",
  demolition_abatement: "demolition",
  landscaping: "landscaping",
  marine_shoreline: "marine & shoreline",
  fencing_guiderail: "fencing & guiderail",
  snow_ice_management: "winter maintenance",
  environmental_remediation: "environmental",
  engineering_survey: "engineering & survey",
  equipment_rental: "equipment rental",
  facility_maintenance: "facility maintenance",
};

const REGION_LABELS: Record<string, string> = { ON: "Ontario", QC: "Québec" };

function formatValue(value: number): string {
  if (value >= 1_000_000) {
    const millions = value / 1_000_000;
    return `~$${millions % 1 === 0 ? millions.toFixed(0) : millions.toFixed(1)}M jobs`;
  }
  if (value >= 1_000) return `~$${Math.round(value / 1000)}K jobs`;
  return `~$${Math.round(value)} jobs`;
}

/** The "Reading: …" line. Shows exactly what drove the ranking. */
export function readingLine(derived: Derived): string {
  const parts: string[] = [];
  if (derived.slugs.length) {
    parts.push(
      derived.slugs
        .slice(0, 3)
        .map((slug) => SLUG_LABELS[slug] ?? slug.replace(/_/g, " "))
        // Comma, not "&" — several labels already contain one.
        .join(", "),
    );
  }
  if (derived.regions.length) {
    parts.push(derived.regions.map((code) => REGION_LABELS[code] ?? code).join(" & "));
  }
  if (derived.valueBand) parts.push(formatValue(derived.valueBand));
  return parts.join(" · ");
}
