/**
 * The three product pages: the depth that left the homepage.
 *
 * Written in the language a contractor uses, not the language the pipeline uses. A
 * page that explains "cross_embedding_similarity" to an estimator has failed, however
 * accurate it is. The methodology lives in /research and /guides, where someone who
 * wants it will look for it.
 *
 * Same tone rules as the guides: every figure sourced and dated, no invented claims,
 * and where something is uncertain or not yet built, it says so.
 */

export type Product = {
  slug: string;
  /** Homepage card. */
  cardTitle: string;
  cardLine: string;
  /** On-page H1. */
  title: string;
  seoTitle: string;
  description: string;
  target: string;
  updated: string;
};

export const PRODUCTS: Product[] = [
  {
    slug: "discovery",
    cardTitle: "Find the right tenders",
    cardLine:
      "Every open notice in Ontario and Québec, ranked against what your firm actually does.",
    title: "Find the right tenders",
    seoTitle: "Tender Matching Software Canada — Ranked for Your Firm",
    description:
      "Every open public tender in Ontario and Québec, ranked against your firm's " +
      "trades, regions and job sizes. Describe your firm or look up your bidding record.",
    target: "tender matching software canada",
    updated: "2026-08-04",
  },
  {
    slug: "compliance",
    cardTitle: "Know before you bid",
    cardLine:
      "Every mandatory requirement pulled out and quoted, with the page number, before you price anything.",
    title: "Know before you bid",
    seoTitle: "Bid Compliance Check — Every Requirement Quoted and Cited",
    description:
      "Upload a tender package and get every mandatory requirement quoted verbatim " +
      "with its page number, and the clauses that would disqualify your firm flagged.",
    target: "bid compliance check",
    updated: "2026-08-04",
  },
  {
    slug: "board",
    cardTitle: "Your firm's board",
    cardLine:
      "A private, weekly-updated list of what to bid — and what not to — built for your firm.",
    title: "Your firm's board",
    seoTitle: "Government Bid Tracking Ontario — Your Firm's Private Board",
    description:
      "A private board of ranked opportunities for your firm, updated weekly, with " +
      "disqualifying clauses surfaced before you spend estimating time.",
    target: "government bid tracking ontario",
    updated: "2026-08-04",
  },
];

export function productBySlug(slug: string): Product | undefined {
  return PRODUCTS.find((product) => product.slug === slug);
}
