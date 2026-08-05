import { CanadaBuysVsSeao } from "@/components/guides/CanadaBuysVsSeao";
import { ClausesThatDisqualify } from "@/components/guides/ClausesThatDisqualify";
import { HowWeRankTenders } from "@/components/guides/HowWeRankTenders";
import { QuebecPublishesOntarioDoesnt } from "@/components/guides/QuebecPublishesOntarioDoesnt";
import { WhatA200kJobLooksLike } from "@/components/guides/WhatA200kJobLooksLike";
import { WhereOntarioTendersLive } from "@/components/guides/WhereOntarioTendersLive";

/** Slug to article body. A guide without an entry here fails the build, not the visitor. */
export const ARTICLES: Record<string, () => React.JSX.Element> = {
  "where-ontario-tenders-live": WhereOntarioTendersLive,
  "canadabuys-vs-seao-vs-municipal": CanadaBuysVsSeao,
  "clauses-that-disqualify-compliant-bids": ClausesThatDisqualify,
  "what-a-200k-job-looks-like": WhatA200kJobLooksLike,
  "quebec-publishes-ontario-doesnt": QuebecPublishesOntarioDoesnt,
  "how-we-rank-tenders": HowWeRankTenders,
};
