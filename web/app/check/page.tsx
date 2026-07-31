import type { Metadata } from "next";
import { SlimNav } from "@/components/Nav";
import { Footer } from "@/components/Footer";
import { CheckForm } from "@/components/CheckForm";

export const metadata: Metadata = {
  title: "Check a tender free — TenderSentry",
  description:
    "Upload any Canadian tender package and get a full compliance brief — every " +
    "mandatory requirement, cited to its page — within 24 hours.",
};

export default function CheckPage() {
  return (
    <>
      <SlimNav />

      <main className="shell py-14">
        <div className="mx-auto max-w-2xl">
          <p className="eyebrow">Free while in beta</p>
          <h1 className="mt-5 text-[30px] font-semibold leading-[1.2]">
            Check one tender, free
          </h1>
          <p className="mt-5 text-[15px] leading-relaxed text-body">
            Send us any Canadian tender package. We return the full compliance brief —
            every mandatory requirement, each one cited to the page it appears on, and a
            plain answer on whether your firm can submit at all — within 24 hours.
          </p>

          <div className="mt-9">
            <CheckForm />
          </div>

          <section className="mt-12 border-t border-hairline pt-8">
            <h2 className="text-sm font-semibold">What you get back</h2>
            <ul className="mt-4 space-y-3 text-sm leading-relaxed text-body">
              <li>
                <strong className="font-medium text-heading">
                  Every mandatory requirement,
                </strong>{" "}
                separated from the background text, and split into what you must do to
                submit versus what binds you only after award.
              </li>
              <li>
                <strong className="font-medium text-heading">
                  The exact sentence and page
                </strong>{" "}
                behind each one, checked character-for-character against your PDF. If a
                quote cannot be found on the page it claims, it is dropped rather than
                shown.
              </li>
              <li>
                <strong className="font-medium text-heading">
                  The blockers first —
                </strong>{" "}
                a fax-only submission clause or a bid-security form you cannot provide
                voids an otherwise winning bid, so those lead the brief.
              </li>
            </ul>
            <p className="mt-6 text-xs leading-relaxed text-muted">
              Briefs are prepared with a human in the loop during the beta, which is why
              they take up to 24 hours rather than seconds.
            </p>
          </section>
        </div>
      </main>

      <Footer />
    </>
  );
}
