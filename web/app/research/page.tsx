import type { Metadata } from "next";
import { SlimNav } from "@/components/Nav";
import { Footer } from "@/components/Footer";
import { MunicipalityLookup } from "@/components/MunicipalityLookup";
import { DistributionBar } from "@/components/DistributionBar";
import { ResearchFindings } from "@/components/ResearchFindings";
import { census, formatNumber, GITHUB_URL } from "@/lib/data";
import { SITE_URL } from "@/lib/guides";

export const metadata: Metadata = {
  title: "Research — What We've Measured About Canadian Public Tendering",
  description:
    "Original measurements of Canadian public procurement: where all 444 Ontario " +
    "municipalities publish, how well tender ranking works, what titles reveal about " +
    "contract size, and the Québec–Ontario data gap. Sourced and dated.",
  alternates: { canonical: `${SITE_URL}/research` },
  openGraph: {
    title: "TenderSentry Research",
    description:
      "Original measurements of Canadian public procurement, each with its method and " +
      "its caveats.",
    url: `${SITE_URL}/research`,
    type: "website",
  },
};

export default async function CensusPage({
  searchParams,
}: {
  searchParams: Promise<{ q?: string }>;
}) {
  const { q } = await searchParams;
  const rows = [...census.distribution].sort((a, b) => b.population - a.population);
  const openRow = census.distribution.find(
    (row) => row.classification === "own_site_open",
  );

  return (
    <>
      <SlimNav />

      <main className="shell py-14">
        {/* Header */}
        <header className="max-w-3xl">
          <p className="eyebrow">Research</p>
          <h1 className="mt-5 text-[30px] font-semibold leading-[1.2] sm:text-[34px]">
            What we&rsquo;ve measured
          </h1>
          <p className="mt-5 text-[15px] leading-relaxed text-body">
            Building a tender service means measuring things nobody had counted. This
            page is what we found — every figure with its method, its date, and its
            caveats, so it can be checked rather than taken on trust.
          </p>
          <p className="mt-4 text-[15px] leading-relaxed text-body">
            It opens with the Ontario Tender Access Census: the first public map of how
            every one of Ontario&rsquo;s 444 municipalities publishes its tenders, built
            by surveying each municipality&rsquo;s own website. More than half of
            Ontarians live in a municipality whose tenders sit behind a single private
            platform, and under 1% live in one that posts its tender documents openly.
          </p>
        </header>

        {/* Lookup, before any charts */}
        <section className="mt-10">
          <MunicipalityLookup
            municipalities={census.municipalities}
            initialQuery={q ?? ""}
          />
        </section>

        {/* Population-weighted bar */}
        <section className="mt-14">
          <h2 className="text-xl font-semibold">Weighted by population</h2>
          <p className="mt-2 max-w-2xl text-sm leading-relaxed text-body">
            Counting municipalities makes open publishing look more common than it is:
            the municipalities that post openly are overwhelmingly small townships.
          </p>
          <div className="mt-6">
            <DistributionBar rows={census.distribution} />
          </div>
          <p className="mt-2 text-xs text-muted">
            Excludes one municipality misattributed in the provincial register (0.7% of
            population); see caveats.
          </p>
        </section>

        {/* Distribution table */}
        <section className="mt-14">
          <h2 className="text-xl font-semibold">Full distribution</h2>
          <div className="mt-5 overflow-x-auto">
            <table className="w-full min-w-[620px] border-collapse text-sm">
              <thead>
                <tr className="border-b border-hairline text-left text-xs uppercase tracking-wide text-muted">
                  <th className="py-3 pr-4 font-medium">Where tenders are published</th>
                  <th className="py-3 pr-4 text-right font-medium">Municipalities</th>
                  <th className="py-3 pr-4 text-right font-medium">% of munis</th>
                  <th className="py-3 pr-4 text-right font-medium">Population</th>
                  <th className="py-3 text-right font-medium">% of pop</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => {
                  const figures = row.corrected ?? row;
                  return (
                    <tr key={row.classification} className="border-b border-hairline">
                      <td className="py-3 pr-4 text-heading">
                        {row.label}
                        {row.corrected ? <sup className="text-brand-red"> †</sup> : null}
                      </td>
                      <td className="py-3 pr-4 text-right tabular-nums">
                        {figures.municipalities}
                      </td>
                      <td className="py-3 pr-4 text-right tabular-nums text-body">
                        {figures.share_of_municipalities}%
                      </td>
                      <td className="py-3 pr-4 text-right tabular-nums">
                        {formatNumber(figures.population)}
                      </td>
                      <td className="py-3 text-right tabular-nums text-body">
                        {/* Sub-1% shares keep their precision; the rest read to one
                            decimal so the column lines up. */}
                        {figures.share_of_population < 1
                          ? `${figures.share_of_population}%`
                          : `${figures.share_of_population.toFixed(1)}%`}
                      </td>
                    </tr>
                  );
                })}
                <tr>
                  <td className="py-3 pr-4 font-semibold text-heading">Total</td>
                  <td className="py-3 pr-4 text-right font-semibold tabular-nums">
                    {census.totals.municipalities}
                  </td>
                  <td className="py-3 pr-4" />
                  <td className="py-3 pr-4 text-right font-semibold tabular-nums">
                    {formatNumber(census.totals.population)}
                  </td>
                  <td className="py-3" />
                </tr>
              </tbody>
            </table>
          </div>
          {openRow?.corrected ? (
            <p className="mt-4 max-w-3xl text-xs leading-relaxed text-muted">
              † {openRow.corrected.footnote}
            </p>
          ) : null}
        </section>

        {/* Everything else we have measured. */}
        <section className="mt-16">
          <h2 className="text-xl font-semibold">Beyond the census</h2>
          <p className="mt-2 max-w-2xl text-sm leading-relaxed text-body">
            Four more findings from building the ranking and estimation models. Each
            carries its number, how it was measured, and where it breaks down.
          </p>
          <ResearchFindings />
        </section>

        {/* Methodology */}
        <section className="mt-14">
          <h2 className="text-xl font-semibold">Methodology &amp; caveats</h2>
          <div className="mt-5 grid gap-4 md:grid-cols-2">
            <div className="card p-6">
              <h3 className="text-sm font-semibold">Sources</h3>
              <ul className="mt-3 space-y-2.5 text-sm leading-relaxed text-body">
                <li>
                  <strong className="font-medium text-heading">Register.</strong>{" "}
                  {census.sources.register.name}, dataset{" "}
                  <code className="text-xs">{census.sources.register.dataset_id}</code>,{" "}
                  {census.sources.register.licence}. Retrieved {census.retrieved}. It
                  carries each municipality&rsquo;s official website, so no domain was
                  guessed.
                </li>
                <li>
                  <strong className="font-medium text-heading">Population.</strong>{" "}
                  {census.sources.population.name}, matched for{" "}
                  {census.sources.population.matched} municipalities. Census divisions
                  and subdivisions are told apart by DGUID, because Ontario has six
                  names shared by two governments.
                </li>
              </ul>
            </div>

            <div className="card p-6">
              <h3 className="text-sm font-semibold">How we crawled</h3>
              <ul className="mt-3 space-y-2.5 text-sm leading-relaxed text-body">
                <li>
                  Identified as <code className="text-xs">TenderSentryBot</code>, at
                  least five seconds between requests to any one host.
                </li>
                <li>
                  robots.txt honoured; two municipalities disallow crawling and were
                  recorded without being fetched.
                </li>
                <li>
                  Procurement platforms were never contacted. We record only that a
                  municipality uses one.
                </li>
                <li>Public pages only. No logins, and no documents downloaded.</li>
              </ul>
            </div>

            <div className="card p-6 md:col-span-2">
              <h3 className="text-sm font-semibold">Known limits</h3>
              <ul className="mt-3 space-y-2.5 text-sm leading-relaxed text-body">
                <li>
                  <strong className="font-medium text-heading">
                    One register error, excluded.
                  </strong>{" "}
                  The province lists a neighbouring township&rsquo;s website against the
                  County of Frontenac. It is excluded from the openly-posted figures
                  above rather than silently repointed.
                </li>
                <li>
                  <strong className="font-medium text-heading">
                    Two cities block bots.
                  </strong>{" "}
                  Ottawa and Vaughan return 403 to our crawler, which says nothing about
                  whether they publish openly. They are counted as unread, not as absent.
                </li>
                <li>
                  <strong className="font-medium text-heading">
                    JavaScript-rendered pages are undercounted.
                  </strong>{" "}
                  Where a municipality&rsquo;s procurement page is drawn by scripts, this
                  survey cannot see it, so &ldquo;no procurement page found&rdquo; is a
                  floor rather than a finding. We did not run a headless browser.
                </li>
                <li>
                  <strong className="font-medium text-heading">
                    What open publishing is worth.
                  </strong>{" "}
                  A Québec civil contractor in our system sees more than four times the
                  ranked opportunities of an Ontario firm of comparable trades and size —
                  586 against 130 — because Québec publishes centrally and openly. The
                  gap is unchanged when the Québec firm is given the Ontario
                  firm&rsquo;s exact trade list, so it is the market, not the profile.
                </li>
              </ul>
              <p className="mt-5 text-sm">
                <a
                  href={`${GITHUB_URL}/blob/main/census/README.md`}
                  className="font-semibold text-brand-red hover:opacity-80"
                >
                  Full methodology on GitHub →
                </a>
              </p>
            </div>
          </div>
        </section>
      </main>

      <Footer />
    </>
  );
}
