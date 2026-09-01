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
  /**
   * Whether the page belongs in the sitemap and in search.
   *
   * A product that does not exist yet should not be competing for search traffic:
   * the honest answer to anyone arriving from a query is "not built yet". The page
   * still exists and is linkable — it is how we ask people what they want from it —
   * but `sitemap.ts` filters on this and the route sets `robots: index: false` from
   * the same field, so the two can never say different things.
   */
  indexed: boolean;
  /**
   * True when the page body carries its own capture form.
   *
   * The shared route appends `BetaForm` ("request a board") to every product page.
   * A page asking a different question needs to ask it once, not beside a second
   * form asking something else.
   */
  ownCapture?: boolean;
  /**
   * Where this product sits in the arc the homepage tells, or absent if it is not a
   * step in it.
   *
   * A separate field rather than a reordering of PRODUCTS: `sitemap.ts` emits in array
   * order and `test_each_records_the_phrase_it_targets` asserts the array's exact
   * sequence, so shuffling for the homepage's benefit would ripple into both. The
   * homepage sorts on this; nothing else reads it.
   *
   * Step 2 — pricing — has no entry here or anywhere else. It is the contractor's
   * work, not ours, and the homepage says so in a line rather than a card.
   */
  arcPosition?: 1 | 3 | 4;
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
    indexed: true,
    arcPosition: 1,
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
    indexed: true,
    arcPosition: 4,
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
    indexed: true,
  },
  {
    slug: "bid-confidence",
    cardTitle: "Know what you're risking",
    cardLine:
      "The range behind your number, and what your contingency is actually buying you.",
    title: "Know what you're risking",
    seoTitle: "Bid Confidence — In Development",
    description:
      "A single bid price is one number standing in for a range of possible costs. " +
      "Bid Confidence shows that range and where your contingency actually sits on " +
      "it. In development — this page explains the idea and asks what you need.",
    target: "",
    updated: "2026-08-31",
    // Not built. Not indexed, and the route reads the same field for robots.
    indexed: false,
    ownCapture: true,
    arcPosition: 3,
  },
];

export function productBySlug(slug: string): Product | undefined {
  return PRODUCTS.find((product) => product.slug === slug);
}
