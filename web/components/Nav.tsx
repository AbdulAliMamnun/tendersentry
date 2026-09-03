import Link from "next/link";

import { PRODUCTS } from "@/lib/products";

/** Full site navigation. Sign in is a placeholder until accounts exist. */
export function Nav() {
  return (
    <header className="border-b border-rule">
      <nav className="shell flex h-16 items-center justify-between gap-6">
        <Link href="/" className="text-[15px] font-semibold text-ink">
          TenderSentry
        </Link>
        <div className="flex items-center gap-5 text-sm">
          {/* CSS-only disclosure: a dropdown for three links does not need JavaScript,
              and a group-hover menu keeps this a server component. */}
          <div className="group relative hidden sm:block">
            <button
              type="button"
              className="text-grey hover:text-ink"
              aria-haspopup="true"
            >
              Product ▾
            </button>
            <div className="invisible absolute left-0 top-full z-10 w-56 rounded-lg border border-rule bg-white py-2 opacity-0 shadow-sm transition group-hover:visible group-hover:opacity-100 group-focus-within:visible group-focus-within:opacity-100">
              {PRODUCTS.map((product) => (
                <Link
                  key={product.slug}
                  href={`/product/${product.slug}`}
                  className="block px-4 py-2 text-grey hover:bg-white hover:text-ink"
                >
                  {product.cardTitle}
                </Link>
              ))}
            </div>
          </div>
          <Link href="/research" className="hidden text-grey hover:text-ink sm:inline">
            Research
          </Link>
          <Link href="/guides" className="hidden text-grey hover:text-ink sm:inline">
            Guides
          </Link>
          <Link href="/check" className="text-grey hover:text-ink">
            Check a tender
          </Link>
          <button
            type="button"
            disabled
            title="Accounts open after the beta"
            className="cursor-not-allowed rounded-control border border-rule px-3.5 py-2
              text-sm font-medium text-grey"
          >
            Sign in
          </button>
        </div>
      </nav>
    </header>
  );
}

/** Slim navigation for interior pages. */
export function SlimNav() {
  return (
    <header className="border-b border-rule">
      <nav className="shell flex h-16 items-center justify-between gap-6 text-sm">
        <Link href="/" className="font-medium text-grey hover:text-ink">
          ← Home
        </Link>
        <div className="flex items-center gap-5">
          <Link href="/research" className="hidden text-grey hover:text-ink sm:inline">
            Research
          </Link>
          <Link href="/guides" className="hidden text-grey hover:text-ink sm:inline">
            Guides
          </Link>
          <Link href="/check" className="text-grey hover:text-ink">
            Check a tender
          </Link>
          {/* A route change, not a same-page fragment: from /product/* or /guides/*
              this leaves the current page. As an <a> it forced a full reload. The
              bare "#join" anchors on the homepage are correctly plain <a>, because
              they scroll rather than navigate. */}
          <Link href="/#join" className="font-semibold text-teal hover:opacity-80">
            Join the beta
          </Link>
        </div>
      </nav>
    </header>
  );
}
