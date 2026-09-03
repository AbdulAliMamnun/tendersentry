import Link from "next/link";

/**
 * Shared prose furniture for the guides, in the site's own type scale.
 *
 * Deliberately a handful of small components rather than a markdown pipeline: six
 * articles do not justify a new dependency, and typed components make it impossible
 * to ship a `Stat` without its source.
 */

export function P({ children }: { children: React.ReactNode }) {
  return <p className="mt-4 text-[15px] leading-relaxed text-grey">{children}</p>;
}

export function H2({ children }: { children: React.ReactNode }) {
  return (
    <h2 className="mt-10 text-[21px] font-semibold leading-snug text-ink">
      {children}
    </h2>
  );
}

export function H3({ children }: { children: React.ReactNode }) {
  return (
    <h3 className="mt-7 text-[17px] font-semibold leading-snug text-ink">{children}</h3>
  );
}

export function UL({ children }: { children: React.ReactNode }) {
  return (
    <ul className="mt-4 space-y-2 text-[15px] leading-relaxed text-grey">{children}</ul>
  );
}

export function LI({ children }: { children: React.ReactNode }) {
  return (
    <li className="relative pl-5 before:absolute before:left-0 before:text-grey before:content-['—']">
      {children}
    </li>
  );
}

/**
 * A figure with its provenance attached.
 *
 * The `source` prop is required on purpose. Every statistic in these articles is
 * traceable to an artifact in the repository and dated; a component that let you
 * render a bare number would make breaking that rule the path of least resistance.
 */
export function Stat({
  value,
  label,
  source,
}: {
  value: string;
  label: string;
  source: string;
}) {
  return (
    <div className="my-6 rounded-lg border border-rule bg-white px-5 py-4">
      <p className="text-[26px] font-semibold leading-none text-ink">{value}</p>
      <p className="mt-2 text-sm text-grey">{label}</p>
      <p className="mt-2 text-xs text-grey-light">{source}</p>
    </div>
  );
}

/**
 * A verbatim clause with its document and page. Never paraphrased.
 *
 * The rule is `flag` only where a bid is out. Today all six call sites are exactly
 * that — /product/compliance and ClausesThatDisqualify — so every one passes
 * `disqualifying`, and nothing looks different than before.
 *
 * The default is neutral anyway, because the next guide to quote an ordinary source
 * document should not inherit the alarm colour by accident. Flag is a claim about the
 * quote, so the caller states it rather than the component assuming it.
 */
export function Quote({
  children,
  cite,
  disqualifying = false,
}: {
  children: React.ReactNode;
  cite: string;
  disqualifying?: boolean;
}) {
  return (
    <figure
      className={`my-6 border-l-2 pl-5 ${disqualifying ? "border-flag" : "border-rule"}`}
    >
      <blockquote className="text-[15px] italic leading-relaxed text-grey">
        &ldquo;{children}&rdquo;
      </blockquote>
      <figcaption className="mt-2 text-xs text-grey-light">{cite}</figcaption>
    </figure>
  );
}

/** Where a number came from and when it was measured. */
export function Caveat({ children }: { children: React.ReactNode }) {
  return (
    <div className="my-6 rounded-lg border border-rule bg-mist px-5 py-4">
      <p className="text-sm leading-relaxed text-grey">{children}</p>
    </div>
  );
}

export function A({ href, children }: { href: string; children: React.ReactNode }) {
  const internal = href.startsWith("/");
  const className = "font-medium text-teal hover:opacity-80";
  return internal ? (
    <Link href={href} className={className}>
      {children}
    </Link>
  ) : (
    <a href={href} target="_blank" rel="noopener noreferrer" className={className}>
      {children}
    </a>
  );
}

export function Table({
  head,
  rows,
}: {
  head: string[];
  rows: (string | React.ReactNode)[][];
}) {
  return (
    <div className="my-6 overflow-x-auto">
      <table className="w-full min-w-[520px] border-collapse text-sm">
        <thead>
          <tr className="border-b border-rule">
            {head.map((cell) => (
              <th
                key={cell}
                className="px-3 py-2 text-left text-[11px] font-semibold uppercase tracking-[0.1em] text-ink"
              >
                {cell}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index} className="border-b border-rule last:border-b-0">
              {row.map((cell, cellIndex) => (
                <td key={cellIndex} className="px-3 py-2.5 align-top text-grey">
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
