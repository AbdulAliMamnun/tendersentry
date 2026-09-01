import type { Metadata } from "next";
import Link from "next/link";

import { BetaForm } from "@/components/BetaForm";
import { Footer } from "@/components/Footer";
import { SlimNav } from "@/components/Nav";
import { GUIDES, SITE_URL } from "@/lib/guides";

export const metadata: Metadata = {
  title: "Guides — TenderSentry",
  description:
    "Practical guides to Canadian public tendering, each built on original data: " +
    "where Ontario's 444 municipalities publish, what disqualifies compliant bids, " +
    "and what tender wording reveals about contract size.",
  alternates: { canonical: `${SITE_URL}/guides` },
  openGraph: {
    title: "TenderSentry Guides",
    description:
      "Guides to Canadian public tendering, each carrying a data point you cannot get " +
      "anywhere else.",
    url: `${SITE_URL}/guides`,
    type: "website",
  },
};

export default function GuidesIndex() {
  return (
    <>
      <SlimNav />
      <main className="shell py-14 sm:py-20">
        <p className="eyebrow">Guides</p>
        <h1 className="mt-4 max-w-2xl text-[28px] font-semibold leading-tight text-heading sm:text-[34px]">
          What we learned building this, written down
        </h1>
        <p className="mt-5 max-w-2xl text-[15px] leading-relaxed text-body sm:text-base">
          Every guide here carries at least one number that does not exist anywhere else
          — measured from our own pipeline, sourced, and dated. Where a figure is
          uncertain, it says so.
        </p>

        <div className="mt-10 space-y-4">
          {GUIDES.map((guide) => (
            <Link
              key={guide.slug}
              href={`/guides/${guide.slug}`}
              className="card block px-5 py-5 transition hover:border-brand-red sm:px-6"
            >
              <h2 className="text-[17px] font-semibold leading-snug text-heading">
                {guide.title}
              </h2>
              <p className="mt-2 text-[15px] leading-relaxed text-body">{guide.summary}</p>
              <p className="mt-3 text-xs text-muted">
                {guide.readingMinutes} min read · updated {guide.updated}
              </p>
            </Link>
          ))}
        </div>
      </main>
      {/* Declared here, not inherited. Footer used to bring a form to every
          page, which collided with the one this route already had. */}
      <div id="join" className="shell pb-14">
        <BetaForm />
      </div>
      <Footer />
    </>
  );
}
