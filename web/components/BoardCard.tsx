import { board, formatClosing } from "@/lib/data";

/**
 * The showpiece: a real ranked board for the demo firm.
 *
 * Both halves are real output. The green rows are the firm's top-scoring notices
 * from the recommendation engine; the red row is a blocker from the qualification
 * engine, whose quote was verified character-for-character against the source PDF
 * at the page shown.
 */
export function BoardCard() {
  const { firm, rows, blocker } = board;

  return (
    <div className="card overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-hairline px-5 py-4 sm:px-6">
        <div className="flex items-center gap-2.5">
          <span className="live-dot h-2 w-2 rounded-full bg-fit-green" aria-hidden />
          <span className="text-[11px] font-semibold uppercase tracking-[0.12em] text-heading">
            Your board — {firm.name}{" "}
            <span className="font-normal text-muted">(demo)</span>
          </span>
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
