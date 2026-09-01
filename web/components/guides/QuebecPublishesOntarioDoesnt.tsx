import { A, Caveat, H2, LI, P, Stat, Table, UL } from "@/components/guides/Prose";
import { census } from "@/lib/data";

/**
 * Original data point: the 199,644-vs-9 split, which is the sharpest single number we
 * have for what open data is worth.
 */
export function QuebecPublishesOntarioDoesnt() {
  const bt = census.distribution.find((r) => r.classification === "bids_and_tenders");
  const open = census.distribution.find((r) => r.classification === "own_site_open");

  return (
    <>
      <P>
        Two provinces, two policies, one number that shows what the difference is worth.
      </P>

      <Stat
        value="199,644 vs 9"
        label="Public contracts with a known winning bid amount that we hold for Québec, against Ontario. Same pipeline, same effort, same period."
        source="TenderSentry bid-interaction database, 2004–2026, queried 2026-08-04."
      />

      <P>
        That is not a sampling artifact or a gap we could close by trying harder. It is
        what each province&rsquo;s publishing policy produces.
      </P>

      <H2>What Québec does</H2>

      <P>
        SEAO — the Système électronique d&rsquo;appel d&rsquo;offres — publishes Québec
        public procurement as open data under CC-BY 4.0, in the Open Contracting Data
        Standard, in weekly releases anyone can download without an account.
      </P>
      <P>
        Crucially, it publishes <strong>results</strong>: for a completed procurement, who
        bid and what they bid. Not just the winner — the field.
      </P>
      <P>
        The consequence is that questions which are simply unanswerable elsewhere become
        arithmetic. Who competes for municipal watermain work in the Montérégie? How many
        firms typically bid a $2M road reconstruction? Has this buyer ever awarded to
        someone outside its region? Those are queries, not research projects.
      </P>

      <H2>What Ontario does</H2>

      <P>
        Ontario has no equivalent. Municipalities publish individually, and mostly not
        openly. We checked every one of the province&rsquo;s{" "}
        {census.totals.municipalities} municipalities on {census.retrieved}:
      </P>

      <Table
        head={["Where tenders live", "Municipalities", "Share of population"]}
        rows={[
          ["On bids&tenders", String(bt?.municipalities), `${bt?.share_of_population}%`],
          [
            "Open documents on their own site",
            String(open?.corrected?.municipalities ?? open?.municipalities),
            `${open?.corrected?.share_of_population ?? open?.share_of_population}%`,
          ],
        ]}
      />

      <P>
        {bt?.share_of_population}% of Ontario&rsquo;s population is served by
        municipalities on a single commercial platform;{" "}
        {open?.corrected?.share_of_population ?? open?.share_of_population}% by
        municipalities that post documents openly. And results — who bid, what they bid —
        are not published in any systematic form at all.
      </P>
      <P>
        The full municipality-by-municipality breakdown is on{" "}
        <A href="/research">the research page</A>.
      </P>

      <H2>What each side actually loses</H2>

      <UL>
        <LI>
          <strong>Ontario contractors lose competitive information.</strong> A Québec firm
          can see how many bidders a comparable job drew. An Ontario firm prices in the
          dark and finds out afterwards, if at all.
        </LI>
        <LI>
          <strong>Ontario municipalities lose a benchmark.</strong> A purchasing manager
          in Québec can check what neighbouring municipalities paid for similar work. In
          Ontario that comparison requires phone calls.
        </LI>
        <LI>
          <strong>Ontario loses the tools.</strong> Everything on this site that learns
          from bidding behaviour learns it from Québec, because that is the only place the
          behaviour is recorded. Ontario firms get a weaker product for the same money —
          not by our choice.
        </LI>
        <LI>
          <strong>Québec loses some privacy, deliberately.</strong> This is the real
          trade. A Québec firm&rsquo;s bidding is public in a way an Ontario firm&rsquo;s
          is not. Reasonable people can disagree about whether that is the right
          settlement; Québec made the call explicitly, and it is the transparent one.
        </LI>
      </UL>

      <Caveat>
        We should be straight about our own position: we benefit from Québec&rsquo;s
        openness and we are constrained by Ontario&rsquo;s closure, so we are not a
        neutral party. The 199,644-vs-9 number is accurate either way, and you should
        weigh the argument knowing who is making it.
      </Caveat>

      <H2>The part that is fixable</H2>

      <P>
        Ontario does not need to build SEAO to close most of this gap. Two changes would
        do most of the work:
      </P>
      <UL>
        <LI>
          <strong>Publish award results in a common format.</strong> Winner and value at
          minimum; bidder list ideally. Most municipalities already report awards to
          council — the data exists, in minutes, unstructured.
        </LI>
        <LI>
          <strong>Require a machine-readable notice feed as a platform condition.</strong>{" "}
          Municipalities buy these platforms. A procurement requirement that the platform
          expose an open feed of the municipality&rsquo;s own notices would cost the
          municipality nothing.
        </LI>
      </UL>

      <P>
        Neither requires new legislation or a provincial portal. Both are procurement
        decisions municipalities already make, one contract renewal at a time.
      </P>
      <P>
        Meanwhile: if you are an Ontario firm,{" "}
        <A href="/guides/where-ontario-tenders-live">the census</A> is the map of where to
        register, and{" "}
        <A href="/guides/what-a-200k-job-looks-like">the scale piece</A> covers what you
        can still infer from a notice that publishes nothing.
      </P>
    </>
  );
}
