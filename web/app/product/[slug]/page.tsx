import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { BetaForm } from "@/components/BetaForm";
import { Footer } from "@/components/Footer";
import { SlimNav } from "@/components/Nav";
import { PRODUCT_BODIES } from "@/components/product";
import { SITE_URL } from "@/lib/guides";
import { PRODUCTS, productBySlug } from "@/lib/products";

/** Fully static: three pages, all known at build time. */
export function generateStaticParams() {
  return PRODUCTS.map((product) => ({ slug: product.slug }));
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ slug: string }>;
}): Promise<Metadata> {
  const { slug } = await params;
  const product = productBySlug(slug);
  if (!product) return {};

  const url = `${SITE_URL}/product/${product.slug}`;
  return {
    title: product.seoTitle,
    description: product.description,
    alternates: { canonical: url },
    openGraph: {
      title: product.seoTitle,
      description: product.description,
      url,
      type: "website",
    },
  };
}

export default async function ProductPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  const product = productBySlug(slug);
  const Body = PRODUCT_BODIES[slug];
  if (!product || !Body) notFound();

  const others = PRODUCTS.filter((entry) => entry.slug !== slug);

  return (
    <>
      <SlimNav />
      <main className="shell py-12 sm:py-16">
        <article className="mx-auto max-w-[46rem]">
          <p className="eyebrow">Product</p>
          <h1 className="mt-4 text-[28px] font-semibold leading-tight text-heading sm:text-[34px]">
            {product.title}
          </h1>

          <div className="mt-8">
            <Body />
          </div>

          <div className="mt-12 border-t border-hairline pt-8">
            <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-heading">
              The rest of the product
            </p>
            <div className="mt-4 space-y-3">
              {others.map((entry) => (
                <Link
                  key={entry.slug}
                  href={`/product/${entry.slug}`}
                  className="block text-[15px] font-medium text-brand-red hover:opacity-80"
                >
                  {entry.cardTitle}
                </Link>
              ))}
            </div>
          </div>

          <div id="join" className="mt-12">
            <BetaForm />
          </div>
        </article>
      </main>
      <Footer />
    </>
  );
}
