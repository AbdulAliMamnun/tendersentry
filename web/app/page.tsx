import Link from "next/link";
import { Nav } from "@/components/Nav";
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
          <p className="mx-auto mt-5 max-w-2xl text-[15px] leading-relaxed text-body sm:text-base">
            TenderSentry watches every open public tender in Ontario and Québec, ranks
            the ones your firm can actually win, and shows you the clauses that would
            disqualify your bid — quoted, with page numbers.{" "}
            <span className="font-medium text-heading">Free while in beta.</span>
          </p>
          <div className="mt-8 flex flex-col items-center justify-center gap-3 sm:flex-row">
            <a href="#join" className="btn-primary w-full sm:w-auto">
              Join the beta
            </a>
            <Link href="/check" className="btn-outline w-full sm:w-auto">
              Check a tender free
            </Link>
          </div>
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

        {/* Three product cards. One line each; the depth lives on the pages. */}
        <section className="border-t border-hairline">
          <div className="shell grid gap-4 py-16 md:grid-cols-3">
            {PRODUCTS.map((product) => (
              <Link
                key={product.slug}
                href={`/product/${product.slug}`}
                className="card block px-5 py-6 transition hover:border-brand-red"
              >
                <h2 className="text-[17px] font-semibold leading-snug text-heading">
                  {product.cardTitle}
                </h2>
                <p className="mt-2 text-[15px] leading-relaxed text-body">
                  {product.cardLine}
                </p>
                <p className="mt-4 text-sm font-medium text-brand-red">Learn more →</p>
              </Link>
            ))}
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

      <Footer />
    </>
  );
}
