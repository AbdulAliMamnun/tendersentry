import { board, formatClosing } from "@/lib/data";
import { dataAsOf, maxIngestedAt } from "@/lib/freshness";
import { scaleLabel } from "@/lib/scale";

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
      <div className="flex flex-wrap items-end justify-between gap-3 border-b border-rule px-5 py-4 sm:px-6">
        <div>
          <div className="flex items-center gap-2.5">
            <span className="live-dot h-2 w-2 rounded-full bg-teal" aria-hidden />
            <span className="text-[11px] font-semibold uppercase tracking-[0.12em] text-ink">
              Example — what a ranked board looks like
            </span>
          </div>
          {/* "a demo firm" left a reader to work out which half was invented. It
              matters more now that a row carries a size: the firm is made up, the
              notices and the quoted clause are not. Say which is which. */}
          <p className="mt-1 pl-[18px] text-xs text-grey-light">
            <span className="font-medium">{firm.name}</span> is a fictional firm.
            The notices, sizes and the quoted clause are real.
          </p>
        </div>
        {/* Derived, not asserted. This read "updated 6:00 AM" — a hardcoded string
            that claimed a freshness nothing produced, and claimed it over the red row
            too, whose evidence is from July. It now comes from the same manifest field
            as the "data as of" line on the ranker, so the two surfaces cannot state
            different things. Date only: the refresh is daily, and a clock time would
            promise a precision the pipeline does not have. Nothing renders when the
            field is absent, rather than a fallback that would be a guess. */}
        {maxIngestedAt() ? (
          <span className="text-xs text-grey-light">updated {dataAsOf()}</span>
        ) : null}
      </div>

      <ul>
        {rows.map((row) => (
          <li
            key={row.title}
            className="flex items-start justify-between gap-4 border-b border-rule px-5 py-4 sm:px-6"
          >
            <div className="min-w-0">
              <p className="truncate text-[15px] font-medium text-ink">{row.title}</p>
              <p className="mt-1 text-xs text-grey-light">
                Closes {formatClosing(row.closing_date)}
              </p>
              {/* The range behind the number — step 3 of the arc the hero promises.
                  Same function the live ranker uses, so the example cannot describe a
                  band differently from the thing it is an example of. */}
              {(() => {
                const scale = scaleLabel({
                  scaleBand: row.scale_band,
                  scaleSource: row.scale_source,
                });
                if (!scale) return null;
                return (
                  <p
                    className={`mt-1 text-xs ${
                      scale.estimated ? "italic text-grey-light" : "text-grey"
                    }`}
                  >
                    {scale.text}
                  </p>
                );
              })()}
            </div>
            <span className="shrink-0 rounded-pill bg-teal-wash px-2.5 py-1 text-xs font-semibold text-teal">
              {row.score} fit
            </span>
          </li>
        ))}

        <li className="px-5 py-4 sm:px-6">
          <div className="flex items-start justify-between gap-4">
            <p className="min-w-0 text-[15px] font-semibold text-ink">
              {blocker.title}
            </p>
            <span className="shrink-0 rounded-pill bg-mist px-2.5 py-1 text-xs font-semibold text-flag">
              Don&rsquo;t bid
            </span>
          </div>
          <p className="mt-2 text-sm font-medium text-flag">{blocker.reason}</p>
          <p className="mt-2 text-sm italic leading-relaxed text-grey">
            &ldquo;{blocker.quote}&rdquo;{" "}
            {/* The date belongs with the evidence it qualifies. Green rows above are
                live; this quote was checked against the source PDF on a fixed date and
                without it a point-in-time example reads as current. */}
            <span className="whitespace-nowrap not-italic text-grey">
              · p.{blocker.page} · verified {dataAsOf(blocker.extracted_at)}
            </span>
          </p>
        </li>
      </ul>
    </div>
  );
}
