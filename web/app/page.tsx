import Link from "next/link";
import { Nav } from "@/components/Nav";
import { BetaForm } from "@/components/BetaForm";
import { Footer } from "@/components/Footer";
import { DemoRanker } from "@/components/DemoRanker";
import { formatNumber, stats } from "@/lib/data";
import { PRODUCTS } from "@/lib/products";

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
export default function HomePage() {
  return (
    <>
      <Nav />

      <main>
        {/* Hero */}
        <section className="shell pb-14 pt-16 text-center sm:pt-24">
          <p className="eyebrow">For Ontario &amp; Québec contractors</p>
          <h1 className="mx-auto mt-5 max-w-3xl text-[30px] font-semibold leading-[1.2] sm:text-[38px]">
            Bid the right tenders. Skip the wrong ones.
          </h1>
          {/* One sentence. The arc is the cards' job below — a hero that recites all
              four steps is a paragraph, and this one promised "four things" and then
              listed three. */}
          <p className="mx-auto mt-5 max-w-2xl text-[15px] leading-relaxed text-body sm:text-base">
            Every open tender in Ontario and Québec, ranked for your firm — and read
            for what would throw your bid out.
          </p>
          <div className="mt-8 flex flex-col items-center justify-center gap-3 sm:flex-row">
            <a href="#join" className="btn-primary w-full sm:w-auto">
              Join the beta
            </a>
            <Link href="/check" className="btn-outline w-full sm:w-auto">
              Check a tender free
            </Link>
          </div>
          {/* A term of the offer, so it sits where the offer is taken. In the sentence
              it blunted the ending and needed a weight change mid-line to separate two
              unrelated ideas. */}
          <p className="mt-4 text-sm font-medium text-heading">Free while in beta.</p>
        </section>

        {/* The demo — the strongest thing on the page, so nothing sits above it. */}
        <section className="shell pb-16">
          <DemoRanker />
          <p className="mt-4 text-center text-sm text-muted">
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

            <p className="mt-8 text-center text-[15px] leading-relaxed text-body">
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
          <div className="shell py-10 text-center">
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

        {/* Free check */}
        <section className="border-t border-hairline">
          <div className="shell py-16 text-center">
            <h2 className="text-2xl font-semibold">Check one tender, free</h2>
            <p className="mx-auto mt-4 max-w-2xl text-[15px] leading-relaxed text-body">
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
