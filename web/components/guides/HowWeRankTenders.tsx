import { A, Caveat, H2, H3, LI, P, Stat, Table, UL } from "@/components/guides/Prose";

/**
 * Original data point: our own held-out evaluation, including the biases. Written to
 * be checkable by someone hostile — which is the only kind of methodology page worth
 * publishing.
 */
export function HowWeRankTenders() {
  return (
    <>
      <P>
        This page exists so you can decide whether to believe our rankings. It has the
        method, the held-out numbers, and the biases we know about and have not solved.
      </P>

      <H2>Stage 1: deterministic filters</H2>

      <P>
        Before anything is scored, notices are removed for reasons that need no model: the
        tender has closed, it is outside the regions the firm works in, or it is outside
        their trades. These are rules, they are auditable, and a firm can see exactly why
        something was excluded.
      </P>
      <P>
        Trade matching runs on a hand-maintained vocabulary of 20 trades with English and
        French keyword sets — 557 rules at last count. It is deliberately deterministic:
        when it is wrong we can fix it with a rule and pin the fix with a test, which is
        not true of a model.
      </P>

      <H2>Stage 2: the learned ranker</H2>

      <P>
        Surviving notices are scored by a gradient-boosted ranker (LightGBM LambdaRank)
        trained on real bidding behaviour: 756,872 observed bids from Québec&rsquo;s public
        procurement record, across 11,182 firms.
      </P>
      <P>
        Thirty features in three groups — what the firm has done before, what the
        opportunity is, and how the two relate. The single most important is the cosine
        similarity between a multilingual sentence embedding of the notice title and a
        centroid of the tenders the firm has actually bid on. SEAO is French and
        CanadaBuys is English, so the embedding model has to share one vector space across
        both languages.
      </P>

      <H2>The numbers</H2>

      <Stat
        value="recall@10 0.219"
        label="Share of a firm's actual next-window bids that appear in our top ten, ranking each firm against the full pool of ~4,996 candidate tenders."
        source="Held-out temporal split at 2026-05-01, 400 evaluation firms. TenderSentry model report 2026-08-02."
      />

      <Table
        head={["Method", "recall@10", "MRR"]}
        rows={[
          ["Deterministic rules alone", "0.094", "0.210"],
          ["Embedding similarity alone", "0.142", "0.327"],
          ["Compact linear model", "0.165", "0.341"],
          ["Learned ranker (what we ship)", "0.219", "0.488"],
        ]}
      />

      <P>
        2.3× the deterministic baseline on recall@10 and 2.3× on MRR. We report the
        full-pool number because an earlier version of our evaluation sampled 200–400
        negatives per firm and inflated every metric roughly fourfold. Sampled-candidate
        metrics are not comparable to these, and you should treat any ranking claim that
        does not say which it used as unmeasured.
      </P>

      <H3>Splits are temporal, never random</H3>
      <P>
        Every evaluation trains on one time window and tests on a later one. A random
        split would let the model learn from bids placed <em>after</em> the ones it is
        asked to predict, which inflates scores and predicts nothing about live use. Every
        firm-history feature also takes an as-of date and excludes anything dated on or
        after it, with a strict inequality — a tender closing on a given day must not see
        bids placed that day.
      </P>

      <H2>Three biases we have not solved</H2>

      <Caveat>
        <strong>Only observed bids are labelled.</strong> A tender a firm would have
        wanted but never saw counts against us in evaluation, because there is no record
        of the interest. Our measured quality is therefore <em>understated</em> — by an
        amount we cannot quantify.
      </Caveat>

      <UL>
        <LI>
          <strong>Incumbency is self-reinforcing.</strong> Prior bids with a buyer is a
          strong feature, so a firm that has worked for a municipality is ranked toward
          working for it again. That reflects reality and it also entrenches it.
        </LI>
        <LI>
          <strong>Single-bidder procurements are excluded.</strong> 62% of SEAO
          procurements name only the winner, who won 98% of the time; training on them
          would teach who <em>wins</em>, not who <em>bids</em>. The blind spot: a firm may
          legitimately want sole-source-shaped work, and this data cannot teach us that.
        </LI>
        <LI>
          <strong>The training market is Québec.</strong> Nearly all labelled bidding
          behaviour is Québec, so behaviour is learned there and applied elsewhere.
        </LI>
      </UL>

      <H2>What the public demo does and does not do</H2>

      <P>
        The demo ranks from a description, which means it has no bidding history to work
        with. Every firm-history feature is zero and the firm vector is assembled from
        matched trades rather than from real bids. It is the same model, given much less.
        The demo says so on the page rather than presenting itself as the product.
      </P>
      <P>
        One consequence we publish because it is easy to miss: since both sides of the
        similarity feature derive from the same trade assignment in that mode, the demo
        leans harder on the keyword rules than the real product does.{" "}
        <strong>Demo behaviour is not model behaviour.</strong>
      </P>

      <H2>Scores are bid fit, never a chance of winning</H2>

      <P>
        The model predicts <em>bid propensity</em> — how likely this notice is to be
        something a firm like yours pursues. It says nothing about whether you would win,
        and we do not let the word &ldquo;probability&rdquo; near the output, because a
        number that looks like a win chance will be read as one.
      </P>

      <P>
        What this looks like from a contractor&rsquo;s side, without the machinery, is on{" "}
        <A href="/product/discovery">the discovery page</A>.
      </P>
      <P>
        Method aside: the ranking is only half the product. The other half is the
        qualification check that reads a document and quotes the clause that would
        disqualify you — covered in{" "}
        <A href="/guides/clauses-that-disqualify-compliant-bids">
          the clauses that disqualify compliant bids
        </A>
        .
      </P>
    </>
  );
}
