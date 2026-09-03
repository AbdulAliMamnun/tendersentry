import Link from "next/link";

/**
 * The findings beyond the census, each with its number, its method, and a link to the
 * guide that works it through.
 *
 * Same rule as the guides: every figure sourced and dated. This page is the one people
 * will cite, so a number without a method next to it does not belong on it.
 */

type Finding = {
  title: string;
  headline: string;
  method: string;
  body: React.ReactNode;
  guide: { href: string; label: string };
};

const FINDINGS: Finding[] = [
  {
    title: "Ranking model",
    headline: "recall@10 0.219 — 2.3× the deterministic baseline",
    method:
      "Held-out temporal split at 2026-05-01, 400 evaluation firms ranked against the " +
      "full pool of ~4,996 candidates. Trained on 756,872 competitive bid interactions " +
      "across 11,182 firms. Model report 2026-08-02.",
    body: (
      <>
        <p>
          A gradient-boosted ranker trained on real bidding behaviour puts 21.9% of a
          firm&rsquo;s actual next-window bids in its top ten, against 9.4% for
          deterministic trade-and-region rules alone. Embedding similarity alone reaches
          14.2%; a compact linear model 16.5%.
        </p>
        <p>
          Splits are temporal, never random — a random split lets a model learn from bids
          placed after the ones it is asked to predict. Every firm-history feature takes
          an as-of date and excludes anything dated on or after it with a strict
          inequality.
        </p>
        <p>
          <strong>The cohort result is the interesting one, and it inverts the
          expectation.</strong> Firms with 20–49 observed bids rank best (recall@10
          0.250). The heaviest bidders — 50 or more, and 334 of the 400 firms evaluated —
          rank <em>worst</em> at 0.215, below even the 5–19 cohort at 0.222. More history
          does not mean more predictable: a firm bidding hundreds of times a year across
          many categories is genuinely harder to anticipate than a specialist, because
          its next bid is drawn from a much wider distribution.
        </p>
        <p>
          Two figures that are easy to mix up: on a later-window split (settled,
          2025-10-01) the same model reaches 2.8× the deterministic baseline, but at
          recall@10 0.217. <strong>The 0.219 and the 2.8× come from different
          splits</strong> — quoting them together overstates the result, so we quote the
          primary split throughout.
        </p>
        <p>
          Measured quality is <em>understated</em> by an amount we cannot quantify: only
          observed bids are labelled, so a tender a firm would have wanted but never saw
          counts against us.
        </p>
      </>
    ),
    guide: { href: "/guides/how-we-rank-tenders", label: "How we rank tenders for a firm" },
  },
  {
    title: "Contract-size estimation",
    headline: "41.7% exact band · 92.1% within one band",
    method:
      "187,870 past public contracts with known winning bid amounts, inflation-adjusted " +
      "to current dollars. Held-out temporal split at 2025-07-01: 168,541 train, 19,329 " +
      "test. Evaluated 2026-08-04.",
    body: (
      <>
        <p>
          Under 1% of open notices publish a value — 241 of 48,834 — so we learned
          contract size from what comparable work actually went for.
        </p>
        <p>
          <strong>The obvious method does not work, and that is the finding.</strong> A
          median lookup by trade × buyer type × region — built from 168,541 real
          contracts — scores 34.9% exact-band accuracy. Always guessing the single most
          common band scores <strong>34.4%</strong>. The lookup beats a constant by half
          a percentage point, and is actually worse on the within-one-band measure.
        </p>
        <p>
          Knowing the trade, the buyer type and the region tells you almost nothing about
          contract size. A municipal watermain job is $80K or $4M depending on how many
          metres of pipe, and none of those three fields carries that.
        </p>
        <p>
          What does carry it is the title. Adding a multilingual sentence embedding of the
          notice title lifts exact-band accuracy to 41.7% — a 7.3-point gain over
          guessing, entirely from wording. Scope plurals, facility nouns, phase markers
          and date ranges are what separate a programme from a job.
        </p>
        <p>
          41.7% exact means the specific band is wrong more often than right, so 92.1%
          within one band is the number worth trusting. Roughly 44% of open notices get no
          band at all, because we would rather say &ldquo;unknown&rdquo; than force one.
        </p>
      </>
    ),
    guide: {
      href: "/guides/what-a-200k-job-looks-like",
      label: "What a $200K job looks like vs a $2M job",
    },
  },
  {
    title: "Bilingual matching",
    headline: "A French tender title lands closer to its English equivalent than to unrelated French",
    method:
      "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2, 384 dimensions. " +
      "Cross-lingual cosine checks, 2026-08-02. Pool composition measured 2026-08-04.",
    body: (
      <>
        <p>
          SEAO publishes in French and CanadaBuys in English, and both have to share one
          vector space or half the corpus is noise. The model does carry meaning across
          the boundary: French <em>égouts pluviaux</em> scores 0.51 against English{" "}
          <em>watermain replacement</em> and 0.22 against French{" "}
          <em>mobilier de bureau</em>. Meaning beats language.
        </p>
        <p>
          <strong>But the effect is much weaker than same-language similarity, and that
          gap caused a real defect.</strong> Our open pool is roughly 65% French SEAO
          notices, so trade centroids built from it are French-dominated. Cosine against
          them partly measures how French a title is: English notices carry a systematic
          penalty of about 0.2 — enough that an English watermain notice scored{" "}
          <em>below</em> an English janitorial contract for a watermain firm.
        </p>
        <p>
          Within one language the similarity is meaningful. Across languages it cannot
          carry a threshold on its own. So eligibility is decided by trade agreement,
          which is language-independent, and the similarity floor is only a backstop.
          Per-language centroids would fix the confound properly and are a known
          follow-up rather than a solved problem.
        </p>
      </>
    ),
    guide: {
      href: "/guides/canadabuys-vs-seao-vs-municipal",
      label: "CanadaBuys vs SEAO vs municipal portals",
    },
  },
  {
    title: "Data access asymmetry",
    headline: "199,644 priced Québec awards. Nine from Ontario.",
    method:
      "TenderSentry bid-interaction database, 2004–2026, queried 2026-08-04. Same " +
      "pipeline, same period, same effort applied to both provinces.",
    body: (
      <>
        <p>
          Québec publishes public procurement as open data under CC-BY 4.0, including
          results: for a completed procurement, who bid and what they bid. Ontario
          publishes nothing systematically comparable.
        </p>
        <p>
          The consequence is not subtle. Of the contracts we hold with a known winning bid
          amount, <strong>199,644 are Québec and 9 are Ontario.</strong> That is not a
          sampling artifact or a gap we could close by trying harder — it is what each
          province&rsquo;s publishing policy produces.
        </p>
        <p>
          Everything on this site that learns from bidding behaviour learns it from
          Québec, because that is the only place the behaviour is recorded. Ontario firms
          get a weaker product for the same effort, and the ranking and size models are
          trained on one province and applied to another.
        </p>
        <p>
          We should be straight about our position: we benefit from Québec&rsquo;s
          openness and are constrained by Ontario&rsquo;s closure, so we are not a neutral
          party. The number is accurate either way, and you should weigh the argument
          knowing who is making it.
        </p>
      </>
    ),
    guide: {
      href: "/guides/quebec-publishes-ontario-doesnt",
      label: "Québec publishes everything. Ontario doesn't.",
    },
  },
];

export function ResearchFindings() {
  return (
    <div className="mt-8 space-y-10">
      {FINDINGS.map((finding) => (
        <section key={finding.title} className="border-t border-rule pt-8">
          <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-ink">
            {finding.title}
          </p>
          <h3 className="mt-3 text-[19px] font-semibold leading-snug text-ink">
            {finding.headline}
          </h3>
          <div className="mt-4 max-w-3xl space-y-4 text-[15px] leading-relaxed text-grey">
            {finding.body}
          </div>
          <p className="mt-5 max-w-3xl text-xs leading-relaxed text-grey-light">
            <span className="font-semibold uppercase tracking-[0.08em]">Method</span> ·{" "}
            {finding.method}
          </p>
          <Link
            href={finding.guide.href}
            className="mt-4 inline-block text-sm font-semibold text-teal hover:opacity-80"
          >
            {finding.guide.label} →
          </Link>
        </section>
      ))}
    </div>
  );
}
