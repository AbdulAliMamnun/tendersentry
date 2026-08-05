import { A, Caveat, H2, H3, LI, P, Stat, Table, UL } from "@/components/guides/Prose";

/**
 * Original data point: the scale-estimator evaluation — including the finding that the
 * obvious approach barely beats a constant, which is the part nobody publishes.
 */
export function WhatA200kJobLooksLike() {
  return (
    <>
      <P>
        Here is a question that should be easy and is not: looking at a tender notice,
        how big is the job?
      </P>
      <P>
        For almost every notice, the document does not say.{" "}
        <strong>Under 1% publish a value</strong> — 241 of the 48,834 notices we hold.
        Everything else is a title, a buyer, and a closing date. So we tried to learn
        size from what past contracts actually went for.
      </P>

      <Stat
        value="187,870"
        label="Past public contracts with a known winning bid amount, inflation-adjusted and used to learn what tender wording implies about contract size."
        source="TenderSentry scale estimator, evaluated 2026-08-04. Source data: SEAO open contracting releases, CC-BY 4.0."
      />

      <H2>The obvious approach barely works</H2>

      <P>
        The natural first move is a lookup table: group past contracts by trade, buyer
        type, and region, take the median of each cell, and quote it. It is transparent,
        explainable, and easy to defend.
      </P>
      <P>
        It is also, on a held-out time window, almost worthless.
      </P>

      <Table
        head={["Method", "Exact band", "Within one band"]}
        rows={[
          ["Always guess the most common band ($100–500K)", "34.4%", "89.8%"],
          ["Median lookup: trade × buyer type × region", "34.9%", "89.2%"],
          ["Wording patterns alone", "4.1%", "9.1%"],
          ["Learned model (title + trade + buyer + region)", "41.7%", "92.1%"],
        ]}
      />

      <P>
        Read the first two rows again. A lookup table built from 168,541 real contracts
        beats <em>always saying &ldquo;$100–500K&rdquo;</em> by half a percentage point —
        and it is actually worse on the within-one-band measure.
      </P>
      <P>
        That is not a tuning failure. It is the finding: <strong>knowing the trade,
        the buyer type, and the region tells you almost nothing about contract size.</strong>{" "}
        A municipal watermain job is $80K or $4M depending on how many metres of pipe,
        and none of those three fields carries that.
      </P>

      <H2>What does carry it: the title</H2>

      <P>
        The model that beats the constant does so by reading the title. Adding a
        multilingual sentence embedding of the notice title lifts exact-band accuracy from
        34.4% to 41.7% — a 7.3-point gain over guessing, entirely from wording.
      </P>

      <H3>What a big job says about itself</H3>
      <UL>
        <LI>
          <strong>Plurals and scope words.</strong> &ldquo;diverses rues&rdquo; — various
          streets — is a programme, not a job.
        </LI>
        <LI>
          <strong>Facilities rather than components.</strong> &ldquo;station
          d&rsquo;épuration&rdquo;, &ldquo;treatment plant&rdquo;, &ldquo;centrale&rdquo;.
          A plant is an order of magnitude above a pipe run.
        </LI>
        <LI>
          <strong>Phases and lots.</strong> &ldquo;Phase 2&rdquo;, &ldquo;lot 3&rdquo; —
          a job large enough to be cut up.
        </LI>
        <LI>
          <strong>Multi-year spans and standing offers.</strong> A date range in the title
          is a programme value, not a project value.
        </LI>
      </UL>

      <H3>And a small one</H3>
      <UL>
        <LI>
          <strong>A single named object.</strong> One culvert, one street, one structure.
        </LI>
        <LI>
          <strong>Study, inspection, expertise.</strong> Professional services on a
          specific question price in the tens of thousands.
        </LI>
        <LI>
          <strong>&ldquo;Travaux mineurs&rdquo;, &ldquo;minor works&rdquo;.</strong> The
          document is telling you directly.
        </LI>
      </UL>

      <H2>The honest limits</H2>

      <Caveat>
        41.7% exact-band accuracy means the specific band is <em>wrong more often than
        it is right</em>. The number worth trusting is 92.1% within one band. That is why
        every estimated size we show is marked as an estimate, with its basis attached,
        and why we say &ldquo;size unknown&rdquo; for the 44% of open notices where we
        have no usable signal rather than forcing a band.
      </Caveat>

      <P>
        Two further limits, stated because they bound what any of this is worth:
      </P>
      <UL>
        <LI>
          <strong>The corpus is Québec.</strong> 199,644 of 199,714 priced awards are
          Québec; Ontario has nine. An estimate on an Ontario notice is an inference from
          Québec comparables. See{" "}
          <A href="/guides/quebec-publishes-ontario-doesnt">why that is</A>.
        </LI>
        <LI>
          <strong>The inflation adjustment uses the wrong sector.</strong> We restate
          amounts in current dollars with StatCan&rsquo;s building construction price
          index (table 18-10-0289-01, Québec non-residential, 2023=100) because Statistics
          Canada publishes no active <em>engineering</em> construction index — 18-10-0022
          ended in 2019 and 18-10-0096 in 1993. The adjustment matters a great deal
          ($1.00 in early 2018 is $1.62 today), and it is a proxy.
        </LI>
      </UL>

      <H2>What to do with this</H2>

      <P>
        The practical takeaway is not our accuracy number. It is that{" "}
        <strong>the title is the most informative thing on an unpriced notice</strong>,
        and most people skim it. Before you open a document, read the title for scope
        words, facility nouns, phase markers, and plurals. You will sort a list of
        notices by likely size faster than any lookup table can.
      </P>
      <P>
        You can see the estimator working on today&rsquo;s open notices — with every band
        labelled by where it came from — on <A href="/">the ranker</A>.
      </P>
    </>
  );
}
