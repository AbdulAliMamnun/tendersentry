import Link from "next/link";

/** Full site navigation. Sign in is a placeholder until accounts exist. */
export function Nav() {
  return (
    <header className="border-b border-hairline">
      <nav className="shell flex h-16 items-center justify-between gap-6">
        <Link href="/" className="text-[15px] font-semibold text-heading">
          TenderSentry
        </Link>
        <div className="flex items-center gap-5 text-sm">
          <a href="/#how-it-works" className="hidden text-body hover:text-heading sm:inline">
            How it works
          </a>
          <Link href="/census" className="hidden text-body hover:text-heading sm:inline">
            Census
          </Link>
          <Link href="/check" className="text-body hover:text-heading">
            Check a tender
          </Link>
          <button
            type="button"
            disabled
            title="Accounts open after the beta"
            className="cursor-not-allowed rounded-control border border-hairline px-3.5 py-2
              text-sm font-medium text-muted"
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
    <header className="border-b border-hairline">
      <nav className="shell flex h-16 items-center justify-between gap-6 text-sm">
        <Link href="/" className="font-medium text-body hover:text-heading">
          ← Home
        </Link>
        <div className="flex items-center gap-5">
          <Link href="/check" className="text-body hover:text-heading">
            Check a tender
          </Link>
          <a href="/#join" className="font-semibold text-brand-red hover:opacity-80">
            Join the beta
          </a>
        </div>
      </nav>
    </header>
  );
}
