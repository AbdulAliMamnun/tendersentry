# Guides

Static content pages under `/guides`, built to be found by contractors searching for
what they actually type — and to be worth reading when they arrive.

## The rule

**Every article carries at least one original data point**: a number that exists
because we built this pipeline, sourced to an artifact in this repository and dated.
There is no market for another explainer on how public procurement works. There is a
market for the only public count of where Ontario's 444 municipalities publish, or for
the finding that a median-lookup baseline barely beats a constant.

An article that cannot carry an original figure does not get written.

## Tone rules

- Every statistic traceable to a repo artifact and dated.
- No invented case studies, no fabricated testimonials, no "experts say", no "studies
  show". `tests/test_guides.py` fails the build on those phrases.
- State uncertainty where it exists. Scale estimates are 41.7% exact; the demo's
  cold-start behaviour is unmeasured; the deflator is the wrong sector. **The caveats
  are the differentiator** — anyone can publish a confident number, and a contractor who
  has been burned by one can tell.
- 800–1,500 words. Long enough to say something, short enough to be read on a phone
  between site visits.

## Structure

| File | Role |
|---|---|
| `web/lib/guides.ts` | Registry: slug, SEO title, description, target phrase, CTA, cross-links |
| `web/components/guides/*.tsx` | One component per article |
| `web/components/guides/Prose.tsx` | Shared furniture. `Stat` requires a `source` prop — a bare number cannot be the easy path |
| `web/app/guides/page.tsx` | Index |
| `web/app/guides/[slug]/page.tsx` | Article shell: metadata, JSON-LD, CTA, related links |
| `web/app/sitemap.ts` | Expands `GUIDES`, so a new article cannot ship unindexed |

Adding an article: add the registry entry, write the component, register it in
`components/guides/index.ts`. The sitemap, metadata, JSON-LD, and index pick it up.
`tests/test_guides.py` fails if the component is missing or a cross-link is dead.

## Published

| Article | Target phrase | Original data point |
|---|---|---|
| Where Ontario's tenders actually live | ontario municipal tenders | The census: 444 municipalities classified, 46 publishing openly |
| CanadaBuys vs SEAO vs municipal portals | canada tenders | Per-source access reality, measured from our own ingestion |
| The clauses that disqualify compliant bids | bid bond requirements ontario | 286 verified requirements; 12 caught fabrications |
| What a $200K job looks like vs a $2M job | construction contract sizes | Lookup 34.9% vs constant 34.4%; GBM 41.7% |
| Québec publishes everything. Ontario doesn't. | seao open data | 199,644 priced Québec awards vs 9 Ontario |
| How we rank tenders for a firm | tender ranking methodology | recall@10 0.219 full-pool, 2.3× the deterministic baseline |

## Backlog

Each of these has a candidate original data point. None is written until the figure is
real and dated.

- **What a bid bond actually costs** — instrument requirements across our corpus, by
  buyer type. Needs a count we do not yet have.
- **How long you actually get** — distribution of days between posting and close, by
  source and buyer type. Computable today from `tenders`.
- **The trades nobody bids** — SEAO categories with the thinnest bidder fields.
  Competitive intelligence for a firm choosing what to expand into.
- **Which municipalities answer** — from the census fetch log: who blocked our crawler,
  who returned nothing, who was well-formed. Sensitive; needs care not to read as a
  scoreboard of municipal competence.
- **What the fabrication rate teaches** — the 12 caught fabrications as a case for
  page-level verification in any extraction pipeline. Needs the taxonomy written up.
- **Standing offers are not opportunities** — how many "open" notices close years out,
  and why closing date is a bad urgency signal. Computable today.

## What these pages are not

They are not a blog and they are not a content-marketing programme. Six articles that
each say something true and unavailable elsewhere will outperform sixty that do not,
and they will not embarrass us in two years.
