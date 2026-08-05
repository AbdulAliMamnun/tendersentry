import { A, Caveat, H2, H3, LI, P, Stat, Table, UL } from "@/components/guides/Prose";
import { census, formatNumber, stats } from "@/lib/data";

/**
 * Original data point: our own ingestion counts, and the per-source access reality
 * measured rather than described from documentation.
 */
export function CanadaBuysVsSeao() {
  const bt = census.distribution.find((r) => r.classification === "bids_and_tenders");
  const open = census.distribution.find((r) => r.classification === "own_site_open");

  return (
    <>
      <P>
        &ldquo;Canadian tenders&rdquo; is not one system. It is three, with different
        rules, different completeness, and — the part that actually decides your week —
        very different answers to the question <em>can I read the documents without an
        account?</em>
      </P>
      <P>
        We ingest from these sources daily, so what follows is measured from our own
        pipeline rather than paraphrased from anyone&rsquo;s about page.
      </P>

      <Stat
        value={formatNumber(stats.notices_tracked)}
        label="Tender notices currently in our database across CanadaBuys and SEAO, with municipal sources monitored separately."
        source="TenderSentry ingestion database, as of 2026-08-04."
      />

      <H2>The three systems</H2>

      <Table
        head={["Source", "Covers", "Open documents?", "Machine-readable?"]}
        rows={[
          [
            "CanadaBuys",
            "Federal departments and agencies, plus some provincial participation",
            "Yes — documents downloadable without an account",
            "Yes — OCDS-format open data",
          ],
          [
            "SEAO",
            "Québec public bodies: municipalities, school service centres, health, provincial",
            "Yes — including results and full bidder lists",
            "Yes — weekly OCDS releases under CC-BY 4.0",
          ],
          [
            "Municipal portals (ON)",
            "Ontario municipalities, mostly via bids&tenders and Biddingo",
            "Mostly no — registration typically required",
            "No public bulk feed",
          ],
        ]}
      />

      <H2>CanadaBuys: good data, federal scope</H2>

      <P>
        CanadaBuys is the federal government&rsquo;s tender service and the successor to
        Buyandsell. It publishes in the Open Contracting Data Standard, which means a
        notice arrives as structured data rather than as a web page you have to scrape:
        buyer, category, closing date, and links to the documents.
      </P>
      <P>
        The catch is scope. CanadaBuys covers federal procurement well and provincial or
        municipal procurement barely at all. If your work is municipal roads and
        watermains, most of CanadaBuys is not your market — and a large share of what it
        does carry for you is standing offers and supply arrangements with closing dates
        years out, which read as live opportunities and are not.
      </P>

      <H3>Practical note on categories</H3>
      <P>
        CanadaBuys publishes its own category on most notices. We learned to trust it: an
        early version of our pipeline promoted keyword matches over the published
        category and filed 106 notices as construction because the word
        &ldquo;road&rdquo; appeared in a <em>street address</em>. The source&rsquo;s own
        classification is better than your keyword list, every time.
      </P>

      <H2>SEAO: the most complete public procurement record in Canada</H2>

      <P>
        Québec&rsquo;s Système électronique d&rsquo;appel d&rsquo;offres publishes what no
        other Canadian jurisdiction does at this scale: not just the tenders, but{" "}
        <strong>who bid on them</strong>, and what they bid. Weekly OCDS releases, open
        licence, no account needed.
      </P>
      <P>
        That single design decision is why a large part of what this site can do exists at
        all. It is the difference between guessing what a firm does and reading it.
      </P>

      <Caveat>
        One caveat that shapes any analysis built on SEAO: 62% of its procurements record
        only a single bidder, and that firm won 98% of the time. Those are not competitive
        outcomes and treating them as bidding behaviour teaches you who <em>wins</em>
        rather than who <em>bids</em>. We exclude them, which leaves 756,872 of 950,607
        observed interactions.
      </Caveat>

      <H2>Ontario municipal portals: the access wall</H2>

      <P>
        Ontario has no SEAO. Municipalities publish individually, and mostly through
        commercial platforms. We counted:{" "}
        <strong>{bt?.municipalities} of {census.totals.municipalities}</strong>{" "}
        municipalities on bids&amp;tenders, carrying {bt?.share_of_population}% of the
        province&rsquo;s population, against{" "}
        <strong>{open?.corrected?.municipalities ?? open?.municipalities}</strong> that
        post open documents on their own site.
      </P>
      <P>
        The platforms are not doing anything wrong. They sell municipalities a service
        that works. But the aggregate effect is that Ontario&rsquo;s procurement record is
        not publicly analysable the way Québec&rsquo;s is — not because anyone decided it
        should not be, but because nobody decided it should.
      </P>
      <P>
        The full municipality-by-municipality map is on{" "}
        <A href="/research">the research page</A>, and the longer version of what that costs
        both sides is in{" "}
        <A href="/guides/quebec-publishes-ontario-doesnt">
          Québec publishes everything, Ontario doesn&rsquo;t
        </A>
        .
      </P>

      <H2>What to actually do</H2>

      <UL>
        <LI>
          <strong>Register on the platforms your buyers use.</strong> There is no way
          around it for the majority of Ontario&rsquo;s population. Our census tells you
          which platform each municipality is on, so you can register once rather than
          per-municipality.
        </LI>
        <LI>
          <strong>Read CanadaBuys through a filter.</strong> Most of it is not your
          market, and the long-horizon standing offers will waste your time if you treat
          closing date as urgency.
        </LI>
        <LI>
          <strong>If you work in Québec, use SEAO&rsquo;s results data.</strong> It tells
          you who you are bidding against and roughly what they bid — public, free, and
          almost nobody uses it.
        </LI>
        <LI>
          <strong>Watch the small-municipality tail.</strong> Forty-six Ontario
          municipalities post openly. That list is short enough to actually work.
        </LI>
      </UL>

      <P>
        None of this makes the fragmentation go away. It does mean you can decide where to
        spend registration effort with a map instead of a guess.
      </P>
    </>
  );
}
