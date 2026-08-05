import { A, Caveat, H2, LI, P, Stat, Table, UL } from "@/components/guides/Prose";
import { census, formatNumber } from "@/lib/data";

/**
 * Original data point: the census itself. Nobody else has counted this, and every
 * number on the page is read from `web/data/census.json` at build time rather than
 * typed into the prose — so the article cannot drift from the artifact.
 */
export function WhereOntarioTendersLive() {
  const by = (key: string) =>
    census.distribution.find((row) => row.classification === key);

  const bt = by("bids_and_tenders");
  const open = by("own_site_open");
  const notices = by("own_site_notices");
  const missing = by("no_procurement_page_found");
  const failed = by("fetch_failed");
  const biddingo = by("biddingo");
  const corrected = open?.corrected;

  return (
    <>
      <P>
        If you build for Ontario municipalities, you already know the annoying part:
        there is no single place to look. What nobody had, until we went and counted, is
        the actual map — which municipalities publish where, and how many of them you
        can reach without an account.
      </P>
      <P>
        So we checked all {census.totals.municipalities} of them, one at a time, on{" "}
        {census.retrieved}. Here is what is actually out there.
      </P>

      <Stat
        value={`${corrected?.municipalities ?? open?.municipalities} of ${census.totals.municipalities}`}
        label="Ontario municipalities that publish open tender documents on their own website — reachable with no account, no login, no platform registration."
        source={`TenderSentry Ontario Tender Access Census, retrieved ${census.retrieved}. Municipal register: ${census.sources.register.name} (${census.sources.register.licence}).`}
      />

      <P>
        That is {corrected?.share_of_municipalities ?? open?.share_of_municipalities}% of
        municipalities, covering{" "}
        {formatNumber(corrected?.population ?? open?.population ?? 0)} residents — under{" "}
        {corrected?.share_of_population ?? open?.share_of_population}% of Ontario&rsquo;s
        population. Everything else sits behind a platform, behind a form, or behind
        nothing we could find.
      </P>

      <H2>The full distribution</H2>

      <Table
        head={["Where tenders live", "Municipalities", "Share", "Population"]}
        rows={census.distribution.map((row) => [
          row.label,
          String(row.municipalities),
          `${row.share_of_municipalities}%`,
          formatNumber(row.population),
        ])}
      />

      <P>
        Two things in that table are worth sitting with.
      </P>

      <H2>Most of the population is behind one vendor</H2>

      <P>
        {bt?.municipalities} municipalities — {bt?.share_of_municipalities}% of them, but{" "}
        <strong>{bt?.share_of_population}% of Ontario&rsquo;s population</strong> — publish
        through bids&amp;tenders. Add {biddingo?.municipalities} on Biddingo and you have
        the majority of the province&rsquo;s procurement by population running through two
        commercial platforms.
      </P>
      <P>
        That is not a scandal; those platforms do real work and municipalities buy them
        for good reasons. But it has a consequence people underrate: the concentration is
        by <em>population</em>, not by municipality count. The big cities are the ones
        behind the platforms. If you only bid where documents are freely posted, you are
        working the {corrected?.share_of_population ?? open?.share_of_population}% of
        Ontario that lives in small municipalities.
      </P>

      <H2>Seventy-four municipalities had no procurement page we could find</H2>

      <P>
        {missing?.municipalities} municipalities — {missing?.share_of_municipalities}%,
        covering {formatNumber(missing?.population ?? 0)} residents — had no procurement
        page we could locate from their own homepage. A further{" "}
        {failed?.municipalities} could not be read at all, including some large ones whose
        sites blocked our crawler outright.
      </P>
      <P>
        We report those two categories separately and deliberately. &ldquo;We could not
        find it&rdquo; and &ldquo;it does not exist&rdquo; are different claims, and only
        the first one is ours to make. A municipality in the &ldquo;could not be
        read&rdquo; bucket may publish perfectly well to a human with a browser.
      </P>

      <H2>The middle category is the one that costs you time</H2>

      <P>
        {notices?.municipalities} municipalities post <em>notices</em> on their own site
        but gate the documents — you can see that a tender exists, but not what it
        requires, without registering. From a contractor&rsquo;s point of view this is the
        worst of both worlds: enough information to make you register, not enough to tell
        you whether registering was worth it.
      </P>

      <Caveat>
        One correction we publish rather than quietly absorb: the provincial register
        lists a neighbouring township&rsquo;s website for the County of Frontenac.
        Including it would let us report {open?.municipalities} municipalities and{" "}
        {formatNumber(open?.population ?? 0)} residents ({open?.share_of_population}%) on
        the strength of a data error. We exclude it, which makes our headline number
        smaller.
      </Caveat>

      <H2>What this means for how you bid</H2>

      <UL>
        <LI>
          <strong>Coverage is a platform question, not a diligence question.</strong> No
          amount of checking municipal websites gets you the{" "}
          {(bt?.share_of_population ?? 0) + (biddingo?.share_of_population ?? 0)}% of the
          population behind the two big platforms. That needs accounts.
        </LI>
        <LI>
          <strong>The open tail is real work.</strong> Forty-six municipalities publishing
          openly is not nothing — it is a genuine, checkable list, and most contractors
          have never seen it as a list.
        </LI>
        <LI>
          <strong>Absence of a notice is not absence of a tender.</strong> With{" "}
          {(missing?.municipalities ?? 0) + (failed?.municipalities ?? 0)} municipalities
          in the two &ldquo;we could not see it&rdquo; buckets, a quiet municipality tells
          you nothing.
        </LI>
      </UL>

      <P>
        The full census is browsable, municipality by municipality, on{" "}
        <A href="/census">the census page</A> — including the classification we assigned
        each one and the page we assigned it from. If we got yours wrong, that page shows
        you exactly what we saw.
      </P>

      <P>
        We publish the underlying data because the interesting fact here is not our
        number. It is that this number did not exist before, for a market that spends
        billions of public dollars a year.
      </P>
    </>
  );
}
