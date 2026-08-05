import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { BetaForm } from "@/components/BetaForm";
import { Footer } from "@/components/Footer";
import { SlimNav } from "@/components/Nav";
import { ARTICLES } from "@/components/guides";
import { GUIDES, SITE_URL, articleSchema, guideBySlug } from "@/lib/guides";

/** Fully static: every guide is known at build time. */
export function generateStaticParams() {
  return GUIDES.map((guide) => ({ slug: guide.slug }));
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ slug: string }>;
}): Promise<Metadata> {
  const { slug } = await params;
  const guide = guideBySlug(slug);
  if (!guide) return {};

  const url = `${SITE_URL}/guides/${guide.slug}`;
  return {
    title: guide.seoTitle,
    description: guide.description,
    alternates: { canonical: url },
    openGraph: {
      title: guide.seoTitle,
      description: guide.description,
      url,
      type: "article",
      publishedTime: guide.published,
      modifiedTime: guide.updated,
    },
  };
}

export default async function GuidePage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  const guide = guideBySlug(slug);
  const Article = ARTICLES[slug];
  if (!guide || !Article) notFound();

  const related = guide.related
    .map((relatedSlug) => guideBySlug(relatedSlug))
    .filter((entry): entry is NonNullable<typeof entry> => Boolean(entry));

  return (
    <>
      <SlimNav />
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(articleSchema(guide)) }}
      />
      <main className="shell py-12 sm:py-16">
        <article className="mx-auto max-w-[46rem]">
          <Link href="/guides" className="text-xs font-medium text-muted hover:text-brand-red">
            ← All guides
          </Link>
          <h1 className="mt-5 text-[28px] font-semibold leading-tight text-heading sm:text-[34px]">
            {guide.title}
          </h1>
          <p className="mt-3 text-xs text-muted">
            {guide.readingMinutes} min read · updated {guide.updated}
          </p>

          <div className="mt-8">
            <Article />
          </div>

          {/* Contextual CTA: the ranker for discovery pieces, the checker for
              compliance ones. A compliance reader wants a document checked, not a board. */}
          <div className="card mt-12 px-5 py-6 sm:px-6">
            {guide.cta === "check" ? (
              <>
                <h2 className="text-[17px] font-semibold text-heading">
                  Have us check a tender, free
                </h2>
                <p className="mt-2 text-[15px] leading-relaxed text-body">
                  Send one document. We return every requirement we find, each quoted and
                  cited to its page, so you can verify every call we make.
                </p>
                <Link href="/check" className="btn-primary mt-5 inline-block">
                  Check a tender free
                </Link>
              </>
            ) : (
              <>
                <h2 className="text-[17px] font-semibold text-heading">
                  See today&rsquo;s market ranked for your firm
                </h2>
                <p className="mt-2 text-[15px] leading-relaxed text-body">
                  Describe what you build and watch the open pool reorder — free, and no
                  account needed.
                </p>
                <Link href="/" className="btn-primary mt-5 inline-block">
                  Rank the live market
                </Link>
              </>
            )}
          </div>

          {related.length > 0 && (
            <div className="mt-12 border-t border-hairline pt-8">
              <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-heading">
                Related
              </p>
              <div className="mt-4 space-y-3">
                {related.map((entry) => (
                  <Link
                    key={entry.slug}
                    href={`/guides/${entry.slug}`}
                    className="block text-[15px] font-medium text-brand-red hover:opacity-80"
                  >
                    {entry.title}
                  </Link>
                ))}
              </div>
            </div>
          )}

          <div id="join" className="mt-12">
            <BetaForm />
          </div>
        </article>
      </main>
      <Footer />
    </>
  );
}
