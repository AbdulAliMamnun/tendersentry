import { board, formatClosing } from "@/lib/data";

/**
 * The showpiece: a real ranked board, shown as an example before the visitor ranks
 * anything themselves.
 *
 * Both halves are real output. The green rows are the firm's top-scoring notices
 * from the recommendation engine; the red row is a blocker from the qualification
 * engine, whose quote was verified character-for-character against the source PDF
 * at the page shown. That blocker is the only visible compliance proof on the
 * homepage, so it stays.
 *
 * The header used to read "Your board — Georgian Bay Civil Ltd." The possessive read
 * as though the visitor were looking at their own results; it is named as an example
 * now, with the demo firm relegated to a sub-line.
 */
export function BoardCard() {
  const { firm, rows, blocker } = board;

  return (
    <div className="card overflow-hidden">
      <div className="flex flex-wrap items-end justify-between gap-3 border-b border-hairline px-5 py-4 sm:px-6">
        <div>
          <div className="flex items-center gap-2.5">
            <span className="live-dot h-2 w-2 rounded-full bg-fit-green" aria-hidden />
            <span className="text-[11px] font-semibold uppercase tracking-[0.12em] text-heading">
              Example — what a ranked board looks like
            </span>
          </div>
          <p className="mt-1 pl-[18px] text-xs text-muted">
            {firm.name}, a demo firm
          </p>
        </div>
        <span className="text-xs text-muted">updated 6:00 AM</span>
      </div>

      <ul>
        {rows.map((row) => (
          <li
            key={row.title}
            className="flex items-start justify-between gap-4 border-b border-hairline px-5 py-4 sm:px-6"
          >
            <div className="min-w-0">
              <p className="truncate text-[15px] font-medium text-heading">{row.title}</p>
              <p className="mt-1 text-xs text-muted">
                Closes {formatClosing(row.closing_date)}
              </p>
            </div>
            <span className="shrink-0 rounded-pill bg-fit-greenSoft px-2.5 py-1 text-xs font-semibold text-fit-green">
              {row.score} fit
            </span>
          </li>
        ))}

        <li className="px-5 py-4 sm:px-6">
          <div className="flex items-start justify-between gap-4">
            <p className="min-w-0 text-[15px] font-semibold text-heading">
              {blocker.title}
            </p>
            <span className="shrink-0 rounded-pill bg-brand-redSoft px-2.5 py-1 text-xs font-semibold text-brand-red">
              Don&rsquo;t bid
            </span>
          </div>
          <p className="mt-2 text-sm font-medium text-brand-red">{blocker.reason}</p>
          <p className="mt-2 text-sm italic leading-relaxed text-body">
            &ldquo;{blocker.quote}&rdquo;{" "}
            <span className="whitespace-nowrap not-italic text-muted">
              · p.{blocker.page}
            </span>
          </p>
        </li>
      </ul>
    </div>
  );
}
