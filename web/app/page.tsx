import Link from "next/link";
import { Nav } from "@/components/Nav";
import { BetaForm } from "@/components/BetaForm";
import { Footer } from "@/components/Footer";
import { DemoRanker } from "@/components/DemoRanker";
import { formatNumber, stats } from "@/lib/data";
import { PRODUCTS } from "@/lib/products";
import { spelledThousands } from "@/lib/freshness";

/**
 * The homepage says what the company does, and then gets out of the way.
 *
 * What used to be here — a census band, a four-number stat strip, a how-it-works
 * triptych — was the interesting parts of the *research* standing where the product
 * should be. A contractor landing here needs to know what they get, not how much we
 * have measured. The research moved to /research, where it is the point rather than
 * an ornament, and the depth moved to the product pages.
 *
 * Target: understand the product without scrolling past the demo.
 */
const HERO_STEPS: { n: number; text: string; pending?: boolean }[] = [
  {
    n: 1,
    text: "We rank every open tender against the work your firm actually bids.",
  },
  { n: 2, text: "We show the range behind your number.", pending: true },
  {
    n: 3,
    text: "We quote the clause that would disqualify you, with its page.",
  },
];

/** Sentence case for a spelled number opening a sentence. */
function capitalise(word: string): string {
  return word.charAt(0).toUpperCase() + word.slice(1);
}

export default function HomePage() {
  return (
    <>
      <Nav />

      <main>
        {/* Hero: the problem, then the solution big, then the three things.
            The type scale used to run 11px eyebrow -> 38px headline -> 15px sub-line:
            a 2.5x jump with nothing between it, so the sentence carrying the whole
            offer read as a caption under the headline. The ladder is now
            1.4rem -> 6rem -> 1.2rem -> 0.875rem, and each step has a job. */}
        <section className="shell pb-14 pt-16 sm:pt-24">
          <p className="eyebrow">For Ontario &amp; Québec contractors</p>

          {/* The problem, before the claim. `rankable.count` from the manifest, not a
              hardcoded figure: it is what a visitor can actually be shown today, and
              spelling it keeps the sentence prose. */}
          <p className="mt-5 max-w-[46ch] text-[clamp(1.2rem,2.4vw,1.4rem)] leading-snug text-muted">
            {capitalise(spelledThousands())} thousand tenders are open across Ontario
            and Québec, and one clause on page 75 throws the bid out anyway.
          </p>

          <h1 className="mt-7 max-w-[14ch] text-[clamp(2.4rem,4.4vw,3.6rem)] font-extrabold leading-[1.02] tracking-[-0.03em] text-heading">
            Bid the right tenders. Skip the wrong ones.
          </h1>

          {/* Left-aligned inside a centred section on purpose: a numbered sequence
              needs a left edge to scan down, and centred the numbers stop doing their
              job. The cards below expand these three; the hero previews them. */}
          <ol className="mt-8 max-w-[38rem] space-y-2 text-[clamp(1.05rem,1.9vw,1.2rem)] leading-snug text-body">
            {HERO_STEPS.map((step) => (
              <li key={step.n} className="flex gap-3">
                <span className="shrink-0 font-bold tabular-nums text-muted">
                  {step.n}
                </span>
                <span>
                  {step.text}
                  {step.pending ? (
                    <span className="ml-2 whitespace-nowrap rounded-pill border border-hairline px-2 py-0.5 align-middle text-[11px] font-semibold text-muted">
                      In development
                    </span>
                  ) : null}
                </span>
              </li>
            ))}
          </ol>

          {/* Not a fourth item. It is the step we do not do, and numbering it would
              claim it. */}
          <p className="mt-4 max-w-[38rem] text-sm text-muted">
            The pricing itself is yours.
          </p>

          <div className="mt-8 flex flex-col items-start gap-3 sm:flex-row sm:items-center">
            <a href="#join" className="btn-primary w-full sm:w-auto">
              Join the beta
            </a>
            <Link href="/check" className="btn-outline w-full sm:w-auto">
              Check a tender free
            </Link>
            <span className="text-sm font-medium text-heading sm:ml-1">
              Free while in beta.
            </span>
          </div>
        </section>

        {/* The demo — the strongest thing on the page, so nothing sits above it. */}
        <section className="shell pb-16">
          <DemoRanker />
          <p className="mt-4 text-sm text-muted">
            Rankings run against today&rsquo;s open notices.{" "}
            <a href="#join" className="font-medium text-brand-red hover:opacity-80">
              Want yours? Join the beta
            </a>{" "}
            — free for Ontario &amp; Québec contractors.
          </p>
        </section>

        {/* The arc, in order, not a menu.
            These read as an unordered list of features before. They are a sequence: we
            find the work, you price it, we show the range, we catch the clause. Step 2
            is a line rather than a card on purpose — a card implies something to click,
            and the one part we do not do should not look like the three we do.
            Ordered by arcPosition rather than array order, so sitemap.ts and the
            target-phrase test keep the registry sequence they depend on. */}
        <section className="border-t border-hairline">
          <div className="shell py-16">
            <div className="grid gap-4 md:grid-cols-3">
              {PRODUCTS.filter((product) => product.arcPosition)
                .sort((a, b) => a.arcPosition! - b.arcPosition!)
                .map((product) => (
                  <Link
                    key={product.slug}
                    href={`/product/${product.slug}`}
                    className="card block px-5 py-6 transition hover:border-brand-red"
                  >
                    <div className="flex items-baseline gap-2">
                      <span className="text-[13px] font-bold tabular-nums text-muted">
                        {product.arcPosition}
                      </span>
                      <h2 className="text-[17px] font-semibold leading-snug text-heading">
                        {product.cardTitle}
                      </h2>
                    </div>
                    <p className="mt-2 text-[15px] leading-relaxed text-body">
                      {product.cardLine}
                    </p>
                    <p className="mt-4 text-sm font-medium text-brand-red">
                      {product.indexed ? "Learn more →" : "In development →"}
                    </p>
                  </Link>
                ))}
            </div>

            {/* Step 2. Full width, no border, no link — it is what makes the other
                three credible, and it is not something we sell. */}
            <p className="mt-4 flex items-baseline gap-2 border-l-2 border-hairline pl-4 text-[15px] leading-relaxed text-body">
              <span className="text-[13px] font-bold tabular-nums text-muted">2</span>
              <span>
                <span className="font-semibold text-heading">Price it.</span> Yours. Your
                takeoff, your subs, your read of the site. We don&rsquo;t estimate jobs
                and won&rsquo;t pretend we can.
              </span>
            </p>

            <p className="mt-8 text-[15px] leading-relaxed text-body">
              <Link
                href="/product/board"
                className="font-semibold text-heading hover:text-brand-red"
              >
                Your firm&rsquo;s board
              </Link>{" "}
              — where the three arrive together, updated weekly.
            </p>
          </div>
        </section>

        {/* One line of credibility, pointing at the research rather than reciting it. */}
        <section className="border-t border-hairline">
          <div className="shell py-10">
            <p className="text-sm leading-relaxed text-body">
              Built on {formatNumber(stats.notices_tracked)} tracked notices, 950,000
              historical bids, and a survey of all {stats.municipalities_mapped} Ontario
              municipalities.{" "}
              <Link
                href="/research"
                className="font-medium text-brand-red hover:opacity-80"
              >
                See the research →
              </Link>
            </p>
          </div>
        </section>

        {/* Free check. Left-aligned like everything else: the page reads as one
            document rather than a left hero with centred punctuation under it. */}
        <section className="border-t border-hairline">
          <div className="shell py-16">
            <h2 className="text-2xl font-semibold">Check one tender, free</h2>
            <p className="mt-4 max-w-2xl text-[15px] leading-relaxed text-body">
              Upload any Canadian tender package. Get the full compliance brief — every
              mandatory requirement, cited to its page — within 24 hours.
            </p>
            <Link href="/check" className="btn-red mt-8">
              Check a tender free
            </Link>
          </div>
        </section>
      </main>

      {/* The homepage declares its own anchor rather than inheriting one from
          Footer. Its three `#join` links land exactly where they always did; the
          difference is that the target is stated here instead of arriving with a
          shared component and colliding on pages that declare their own. */}
      {/* Declared here, not inherited. Footer used to bring a form to every
          page, which collided with the one this route already had. */}
      <div id="join" className="shell pb-14">
        <BetaForm />
      </div>
      <Footer />
    </>
  );
}
