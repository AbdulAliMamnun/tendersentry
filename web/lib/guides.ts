/**
 * The guides: articles that exist to be found, and to be worth finding.
 *
 * **The rule every article obeys: it carries at least one fact only we can state**,
 * sourced to an artifact in this repository and dated. There is no market for another
 * summary of how public procurement works. There is a market for the only public count
 * of where Ontario's 444 municipalities actually publish, or for the finding that a
 * median-lookup baseline barely beats a constant.
 *
 * **Tone rules, enforced by review rather than by code.** Every statistic traceable to
 * an artifact and dated. No invented case studies, no fabricated testimonials, no
 * "experts say". Where a number is uncertain — scale estimates, unmeasured cold-start
 * behaviour — the article says so. The caveats are the differentiator: anyone can
 * publish a confident number, and a contractor who has been burned by one can tell.
 */

export type Guide = {
  slug: string;
  title: string;
  /** The `<title>` tag. Distinct from the on-page H1 when search intent differs. */
  seoTitle: string;
  description: string;
  summary: string;
  /** Search phrase this is written to answer. Recorded so the backlog stays honest. */
  target: string;
  published: string;
  updated: string;
  readingMinutes: number;
  /** Which CTA fits: the ranker for discovery pieces, the checker for compliance. */
  cta: "demo" | "check";
  related: string[];
};

export const GUIDES: Guide[] = [
  {
    slug: "where-ontario-tenders-live",
    title: "Where Ontario's tenders actually live",
    seoTitle: "Ontario Municipal Tenders: Where All 444 Municipalities Publish",
    description:
      "We checked every one of Ontario's 444 municipalities to find where they publish " +
      "tenders. Only 46 post open documents on their own site. Here is the full map.",
    summary:
      "All 444 municipalities, checked one by one. 36% are on bids&tenders; 10% publish " +
      "openly on their own site; 17% could not be found at all.",
    target: "ontario municipal tenders",
    published: "2026-08-04",
    updated: "2026-08-04",
    readingMinutes: 6,
    cta: "demo",
    related: ["quebec-publishes-ontario-doesnt", "canadabuys-vs-seao-vs-municipal"],
  },
  {
    slug: "canadabuys-vs-seao-vs-municipal",
    title: "CanadaBuys vs SEAO vs municipal portals",
    seoTitle: "Canada Tenders: CanadaBuys, SEAO and Municipal Portals Compared",
    description:
      "A practical map of where Canadian public tenders are published, what each source " +
      "actually gives you, and which ones you can reach without an account.",
    summary:
      "Three systems, three access realities. What each publishes, what it withholds, " +
      "and where the gaps are.",
    target: "canada tenders",
    published: "2026-08-04",
    updated: "2026-08-04",
    readingMinutes: 7,
    cta: "demo",
    related: ["where-ontario-tenders-live", "quebec-publishes-ontario-doesnt"],
  },
  {
    slug: "clauses-that-disqualify-compliant-bids",
    title: "The clauses that disqualify compliant bids",
    seoTitle: "Bid Bond Requirements Ontario: The Clauses That Disqualify Good Bids",
    description:
      "Bid security instruments, submission methods and physical-delivery traps — with " +
      "real clauses quoted from real Ontario tender documents, cited to the page.",
    summary:
      "Three clause families that reject technically compliant bids, quoted from the " +
      "documents they appear in, with page numbers.",
    target: "bid bond requirements ontario",
    published: "2026-08-04",
    updated: "2026-08-04",
    readingMinutes: 8,
    cta: "check",
    related: ["how-we-rank-tenders", "canadabuys-vs-seao-vs-municipal"],
  },
  {
    slug: "what-a-200k-job-looks-like",
    title: "What a $200K job looks like vs a $2M job",
    seoTitle: "Construction Contract Sizes: What Tender Wording Reveals About Value",
    description:
      "Under 1% of tender notices publish a value. We learned contract size from 187,870 " +
      "past awards — and found the obvious approach barely beats guessing.",
    summary:
      "Size variance within a trade is enormous. Categorical lookup scores 34.9% against " +
      "34.4% for a constant. The title is where the signal is.",
    target: "construction contract sizes",
    published: "2026-08-04",
    updated: "2026-08-04",
    readingMinutes: 7,
    cta: "demo",
    related: ["how-we-rank-tenders", "quebec-publishes-ontario-doesnt"],
  },
  {
    slug: "quebec-publishes-ontario-doesnt",
    title: "Québec publishes everything. Ontario doesn't.",
    seoTitle: "SEAO Open Data: Why Québec's Tender Records Beat Ontario's",
    description:
      "SEAO publishes full bidder lists as open data. Ontario publishes almost nothing " +
      "comparable. We hold 199,644 priced Québec awards and nine from Ontario.",
    summary:
      "One province's open data makes analysis possible. The other's gated portals make " +
      "the same analysis impossible. Here is what each side loses.",
    target: "seao open data",
    published: "2026-08-04",
    updated: "2026-08-04",
    readingMinutes: 7,
    cta: "demo",
    related: ["where-ontario-tenders-live", "what-a-200k-job-looks-like"],
  },
  {
    slug: "how-we-rank-tenders",
    title: "How we rank tenders for a firm",
    seoTitle: "How TenderSentry Ranks Tenders: Methodology, Metrics and Limits",
    description:
      "Deterministic filters, multilingual embeddings, and a learned ranker trained on " +
      "756,872 real bids — with the metrics and the biases stated plainly.",
    summary:
      "The full method, the held-out numbers, and the three biases we know about and " +
      "have not solved.",
    target: "tender ranking methodology",
    published: "2026-08-04",
    updated: "2026-08-04",
    readingMinutes: 9,
    cta: "demo",
    related: ["what-a-200k-job-looks-like", "clauses-that-disqualify-compliant-bids"],
  },
];

export function guideBySlug(slug: string): Guide | undefined {
  return GUIDES.find((guide) => guide.slug === slug);
}

export const SITE_URL = "https://tendersentry.com";

/** Article JSON-LD. Publisher is us; there is no fictional author byline. */
export function articleSchema(guide: Guide) {
  return {
    "@context": "https://schema.org",
    "@type": "Article",
    headline: guide.title,
    description: guide.description,
    datePublished: guide.published,
    dateModified: guide.updated,
    author: { "@type": "Organization", name: "TenderSentry", url: SITE_URL },
    publisher: { "@type": "Organization", name: "TenderSentry", url: SITE_URL },
    mainEntityOfPage: `${SITE_URL}/guides/${guide.slug}`,
    inLanguage: "en-CA",
  };
}
