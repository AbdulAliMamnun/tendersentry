import { formatClosing } from "@/lib/data";
import { flagLabel, sourceLabel, type FirmBoardRow } from "@/lib/boards";

/**
 * A firm's ranked board.
 *
 * A row shows a blocker only where the qualification engine produced one — a
 * verbatim quote checked against its page. Rows without one say nothing about
 * blockers, because an absence we have not verified is not a clearance.
 */
export function FirmBoard({ rows }: { rows: FirmBoardRow[] }) {
  if (rows.length === 0) {
    return (
      <div className="card p-7">
        <p className="text-sm text-grey">
          No opportunities matched your profile in this run. That usually means the
          filters are too tight rather than the market being empty — reply to your
          welcome email and we&rsquo;ll widen them with you.
        </p>
      </div>
    );
  }

  return (
    <div className="card overflow-hidden">
      <ul>
        {rows.map((row) => (
          <li
            key={`${row.rank}-${row.title}`}
            className="border-b border-rule px-5 py-4 last:border-b-0 sm:px-6"
          >
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0">
                <p className="text-[15px] font-medium leading-snug text-ink">
                  {row.title}
                </p>
                <p className="mt-1 text-xs text-grey-light">
                  {row.buyer ? `${row.buyer} · ` : ""}
                  Closes {formatClosing(row.closing_date)} · {sourceLabel(row.source)}
                </p>
              </div>
              <span
                className={`shrink-0 rounded-pill px-2.5 py-1 text-xs font-semibold ${
                  row.blocker
                    ? "bg-mist text-flag"
                    : "bg-teal-wash text-teal"
                }`}
              >
                {row.blocker ? "Don't bid" : `${row.score} fit`}
              </span>
            </div>

            {row.blocker ? (
              <>
                <p className="mt-2 text-sm font-medium text-flag">
                  {row.blocker.reason}
                </p>
                <p className="mt-1.5 text-sm italic leading-relaxed text-grey">
                  &ldquo;{row.blocker.quote}&rdquo;{" "}
                  <span className="whitespace-nowrap not-italic text-grey">
                    · p.{row.blocker.page}
                  </span>
                </p>
              </>
            ) : null}

            {row.flags.length > 0 ? (
              <p className="mt-2 text-xs text-grey-light">
                {row.flags.map(flagLabel).join(" · ")}
              </p>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}
