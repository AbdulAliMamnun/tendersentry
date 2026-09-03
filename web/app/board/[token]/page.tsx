import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { FirmBoard } from "@/components/FirmBoard";
import { FirmLookup } from "@/components/FirmLookup";
import { formatGenerated, loadBoard } from "@/lib/boards";
import { GITHUB_URL } from "@/lib/data";
import { dataAsOf } from "@/lib/freshness";

const NOTIFY_ADDRESS = "hello@tendersentry.com";

/**
 * A firm's private board.
 *
 * Rendered on demand rather than prerendered: prerendering would require every
 * token to be enumerated at build time, which means committing them to a public
 * repository — publishing the only thing that protects these pages.
 *
 * `noindex` keeps boards out of search, and `no-referrer` keeps the token out of the
 * Referer header when someone follows a link to a tender notice.
 */
export const metadata: Metadata = {
  title: "Your board — TenderSentry",
  robots: { index: false, follow: false, nocache: true },
  referrer: "no-referrer",
};

export default async function BoardPage({
  params,
}: {
  params: Promise<{ token: string }>;
}) {
  const { token } = await params;
  const data = await loadBoard(token);
  if (!data) notFound();

  const { firm, board, generated_at, candidate_count } = data;

  // Name lookup is beta-only. The key lives server-side; a board page is already an
  // authenticated-by-possession surface, so this is the right place to expose it —
  // and the public demo never gets it.
  const betaKey = process.env.BETA_ACCESS_KEY ?? null;

  return (
    <main className="shell py-12">
      <header className="border-b border-rule pb-7">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-semibold">{firm.name}</h1>
          <span className="rounded-pill bg-white px-2.5 py-1 text-xs font-medium text-grey-light">
            Beta — updated weekly
          </span>
        </div>
        <p className="mt-2 text-sm text-grey">
          Your board · updated {formatGenerated(generated_at)}
          {/* Two different facts, and the distinction matters to a contractor: when
              this board was built, and how current the market behind it is. They
              diverge whenever a board is exported from an older pool. */}
          <span className="text-grey"> · data as of {dataAsOf()}</span>
        </p>
        <p className="mt-4 text-sm leading-relaxed text-grey">
          Showing the top {board.length} of {candidate_count.toLocaleString("en-CA")}{" "}
          opportunities that passed your filters
          {firm.trades.length > 0
            ? ` — ${firm.trades.map((trade) => trade.replace(/_/g, " ")).join(", ")}`
            : ""}
          {firm.regions.length > 0
            ? ` · ${firm.regions.map((region) => region.replace(/_/g, " ")).join(", ")}`
            : ""}
          .
        </p>
      </header>

      <section className="mt-8">
        <FirmBoard rows={board} />
      </section>

      <section className="card mt-8 p-6">
        <p className="text-sm font-medium text-ink">Is this board useful?</p>
        <p className="mt-2 text-sm leading-relaxed text-grey">
          Reply to your welcome email, or{" "}
          <a
            href={`mailto:${NOTIFY_ADDRESS}?subject=${encodeURIComponent(
              `Board feedback — ${firm.name}`,
            )}`}
            className="font-medium text-teal hover:opacity-80"
          >
            tell us what&rsquo;s missing
          </a>
          . Wrong trades, wrong regions, wrong size — all of it is tunable, and early
          corrections are worth more to us than polite silence.
        </p>
      </section>

      <section className="card mt-4 p-6">
        <p className="text-sm font-medium text-ink">
          Deciding on one of these?
        </p>
        <p className="mt-2 text-sm leading-relaxed text-grey">
          <Link href="/check" className="font-medium text-teal hover:opacity-80">
            Get the full compliance brief free
          </Link>{" "}
          — every mandatory requirement, cited to the page it appears on, within 24
          hours.
        </p>
      </section>

      <footer className="mt-10 border-t border-rule pt-6">
        <p className="text-xs leading-relaxed text-grey-light">
          <Link href="/" className="underline underline-offset-2 hover:text-grey">
            tendersentry.com
          </Link>{" "}
          ·{" "}
          <Link href="/research" className="underline underline-offset-2 hover:text-grey">
            The Ontario tender access census
          </Link>{" "}
          ·{" "}
          <a
            href={`${GITHUB_URL}/blob/main/census/README.md`}
            rel="noreferrer"
            className="underline underline-offset-2 hover:text-grey"
          >
            Methodology
          </a>
        </p>
        <p className="mt-3 text-xs text-grey-light">
          This board is private to your firm. The link is the access — please don&rsquo;t
          share it publicly.
        </p>
      </footer>

      {betaKey && <FirmLookup betaKey={betaKey} />}
    </main>
  );
}
