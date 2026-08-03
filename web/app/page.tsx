import Link from "next/link";
import { Nav } from "@/components/Nav";
import { Footer } from "@/components/Footer";
import { CensusBand } from "@/components/CensusBand";
import { DemoRanker } from "@/components/DemoRanker";
import { census, formatNumber, stats } from "@/lib/data";

const HOW_IT_WORKS = [
  {
    icon: "◎",
    title: "Watch everything",
    line: "Federal, Québec, and open Ontario sources. Daily.",
  },
  {
    icon: "◈",
    title: "Ranked to your firm",
    line: "Trades, size, bonding, regions. Every score explained.",
  },
  {
    icon: "❝",
    title: "Proof, not promises",
    line: "Every requirement cited to its page. Verified against the PDF.",
  },
];

export default function HomePage() {
  const statItems = [
    { value: formatNumber(stats.notices_tracked), label: "notices tracked" },
    { value: formatNumber(stats.requirements_verified), label: "requirements verified" },
    {
      value: formatNumber(stats.fabrications_caught),
      label: "fabrications caught",
      red: true,
    },
    { value: formatNumber(stats.municipalities_mapped), label: "municipalities mapped" },
  ];

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
            TenderSentry watches the whole market, ranks what fits your firm, and proves
            every disqualifying clause — the sentence, and the page it&rsquo;s on.
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

        {/* Live ranker — the sample board is its empty and fallback state */}
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

        {/* Stat strip */}
        <section className="border-y border-hairline">
          <div className="shell grid grid-cols-2 gap-y-8 py-10 text-center md:grid-cols-4">
            {statItems.map((item) => (
              <div key={item.label}>
                <p
                  className={`text-2xl font-semibold tabular-nums ${
                    item.red ? "text-brand-red" : "text-heading"
                  }`}
                >
                  {item.value}
                </p>
                <p className="mt-1 text-xs text-muted">{item.label}</p>
              </div>
            ))}
          </div>
        </section>

        {/* How it works */}
        <section id="how-it-works" className="shell py-16">
          <div className="grid gap-4 md:grid-cols-3">
            {HOW_IT_WORKS.map((item) => (
              <div key={item.title} className="card p-7 text-center">
                <span
                  className="mx-auto flex h-11 w-11 items-center justify-center rounded-control
                    bg-page text-lg text-brand-red"
                  aria-hidden
                >
                  {item.icon}
                </span>
                <h3 className="mt-4 text-[15px] font-semibold">{item.title}</h3>
                <p className="mt-2 text-sm leading-relaxed text-body">{item.line}</p>
              </div>
            ))}
          </div>
        </section>

        {/* Census band */}
        <section className="shell grid items-center gap-10 border-t border-hairline py-16 md:grid-cols-2 md:gap-14">
          <div>
            <h2 className="text-2xl font-semibold">Where tenders actually live</h2>
            <p className="mt-4 text-[15px] leading-relaxed text-body">
              Québec publishes every tender openly. Ontario doesn&rsquo;t — so we
              surveyed all 444 municipalities: 51% of local tenders sit behind one
              private platform, under 1% are openly posted.
            </p>
            <p className="mt-3 text-sm text-muted">Other provinces next.</p>
            <Link
              href="/census"
              className="mt-6 inline-block text-sm font-semibold text-brand-red hover:opacity-80"
            >
              Explore the census →
            </Link>
          </div>
          <CensusBand buckets={census.buckets} />
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
