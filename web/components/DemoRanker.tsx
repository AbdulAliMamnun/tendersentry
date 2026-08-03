"use client";

import Link from "next/link";
import { useState } from "react";

import { BoardCard } from "@/components/BoardCard";
import { formatClosing } from "@/lib/data";

/**
 * The live demo: describe a firm, watch the open market re-rank itself.
 *
 * Every state is designed so a visitor is never shown a ranking that does not mean
 * what it looks like. An unrecognised description says so and shows the sample board
 * instead of an arbitrary list; a thin pool says how thin; a failure falls back to the
 * sample board rather than an error page.
 */

type Row = {
  title: string;
  buyer: string;
  region: string | null;
  value: number | null;
  closingDate: string;
  url: string | null;
  source: string;
  tradeSlugs: string[];
  fit: number;
};

type Response = {
  reading: string;
  hit: boolean;
  results: Row[];
  considered: number;
  onTrade: number;
  thin: boolean;
  regions: string[];
  poolSize: number;
  error?: string;
};

const EXAMPLES = [
  "Watermain and sanitary sewer replacement, jobs around $2M",
  "Entrepreneur en pavage et travaux routiers, Montérégie",
  "Rooftop HVAC and electrical retrofits for school boards",
];

const PLACEHOLDER = "Describe your firm — what you build, where, and typical job size";

function sourceLabel(source: string): string {
  if (source === "seao") return "SEAO";
  if (source === "canadabuys") return "CanadaBuys";
  return source;
}

export function DemoRanker() {
  const [description, setDescription] = useState("");
  const [data, setData] = useState<Response | null>(null);
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  async function submit(text: string) {
    const value = text.trim();
    if (value.length < 3 || busy) return;

    setBusy(true);
    setMessage(null);
    setFailed(false);
    try {
      const response = await fetch("/api/demo-rank", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ description: value }),
      });
      const payload = (await response.json()) as Response;
      if (!response.ok) {
        // A rate limit is worth explaining; anything else falls back to the sample.
        if (response.status === 429) {
          setMessage(payload.error ?? "Try again in a moment.");
        } else {
          setFailed(true);
        }
        setData(null);
      } else {
        setData(payload);
      }
    } catch {
      setFailed(true);
      setData(null);
    } finally {
      setBusy(false);
    }
  }

  const showSample = data === null || !data.hit;

  return (
    <div>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void submit(description);
        }}
        className="card p-5 sm:p-6"
      >
        <label
          htmlFor="firm-description"
          className="text-[11px] font-semibold uppercase tracking-[0.12em] text-heading"
        >
          Rank the live market for your firm
        </label>
        <div className="mt-3 flex flex-col gap-3 sm:flex-row">
          <input
            id="firm-description"
            value={description}
            maxLength={500}
            onChange={(event) => setDescription(event.target.value)}
            placeholder={PLACEHOLDER}
            className="w-full rounded-lg border border-hairline bg-white px-4 py-3 text-[15px] text-heading outline-none placeholder:text-muted focus:border-brand-red"
          />
          <button
            type="submit"
            disabled={busy || description.trim().length < 3}
            className="btn-primary shrink-0 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {busy ? "Ranking…" : "Rank"}
          </button>
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-2">
          <span className="text-xs text-muted">Try:</span>
          {EXAMPLES.map((example) => (
            <button
              key={example}
              type="button"
              onClick={() => {
                setDescription(example);
                void submit(example);
              }}
              className="rounded-pill border border-hairline px-2.5 py-1 text-xs text-body hover:border-brand-red hover:text-brand-red"
            >
              {example}
            </button>
          ))}
        </div>

        <p className="mt-4 text-xs leading-relaxed text-muted">
          Demo ranks by description fit. Your full board also uses bidding history and
          compliance checks. Your description isn&rsquo;t stored — only which trades it
          matched, so we can tell where the matching falls short.
        </p>
      </form>

      {message && (
        <p className="mt-4 text-center text-sm text-brand-red">{message}</p>
      )}

      {data && data.hit && (
        <div className="card mt-5 overflow-hidden">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-hairline px-5 py-4 sm:px-6">
            <div className="flex items-center gap-2.5">
              <span
                className="live-dot h-2 w-2 rounded-full bg-fit-green"
                aria-hidden
              />
              <span className="text-[11px] font-semibold uppercase tracking-[0.12em] text-heading">
                Reading: {data.reading}
              </span>
            </div>
            <span className="text-xs text-muted">
              {data.considered.toLocaleString()} open notices ranked
            </span>
          </div>

          {data.thin && (
            <div className="border-b border-hairline bg-brand-redSoft px-5 py-3 sm:px-6">
              <p className="text-sm leading-relaxed text-brand-red">
                Only {data.onTrade} open{" "}
                {data.onTrade === 1 ? "notice matches" : "notices match"} those trades in{" "}
                {data.regions.join(" & ") || "the pool"} right now — the ranking below is
                thin, and it isn&rsquo;t the whole market.
              </p>
              {/* The demo's weakest moment is also the census finding, so it links to
                  the evidence rather than apologising. */}
              <p className="mt-1.5 text-sm leading-relaxed text-body">
                Most Ontario municipal tenders sit behind gated portals.{" "}
                <Link
                  href="/census"
                  className="font-medium text-brand-red hover:opacity-80"
                >
                  See why &rarr;
                </Link>{" "}
                Your board also draws on monitored municipal sources and your own
                uploads —{" "}
                <a href="#join" className="font-medium text-brand-red hover:opacity-80">
                  join the beta
                </a>
                .
              </p>
            </div>
          )}

          <ul>
            {data.results.map((row, index) => (
              <li
                key={`${row.title}-${index}`}
                className="flex items-start justify-between gap-4 border-b border-hairline px-5 py-4 last:border-b-0 sm:px-6"
              >
                <div className="min-w-0">
                  <p className="text-[15px] font-medium leading-snug text-heading">
                    {row.url ? (
                      <a
                        href={row.url}
                        target="_blank"
                        rel="noopener noreferrer nofollow"
                        className="hover:text-brand-red"
                      >
                        {row.title}
                      </a>
                    ) : (
                      row.title
                    )}
                  </p>
                  <p className="mt-1 text-xs text-muted">
                    {row.buyer}
                    {row.region ? ` · ${row.region}` : ""} ·{" "}
                    {sourceLabel(row.source)} · Closes {formatClosing(row.closingDate)}
                  </p>
                </div>
                <span className="shrink-0 rounded-pill bg-fit-greenSoft px-2.5 py-1 text-xs font-semibold text-fit-green">
                  {Math.round(row.fit)} fit
                </span>
              </li>
            ))}
          </ul>

          <p className="border-t border-hairline px-5 py-3 text-xs leading-relaxed text-muted sm:px-6">
            Fit is relative to today&rsquo;s open pool — it ranks these notices against
            each other, and is a measure of bid fit, not a chance of winning.
          </p>
        </div>
      )}

      {data && !data.hit && (
        <p className="mt-5 rounded-lg border border-hairline px-5 py-4 text-sm leading-relaxed text-body">
          We couldn&rsquo;t recognise a trade in that. Naming the work directly —
          &ldquo;watermain replacement&rdquo;, &ldquo;pavage&rdquo;, &ldquo;roofing&rdquo;
          — is what the matching keys off. Rather than show you an arbitrary list,
          here&rsquo;s a real firm&rsquo;s board instead.
        </p>
      )}

      {failed && (
        <p className="mt-5 rounded-lg border border-hairline px-5 py-4 text-sm leading-relaxed text-body">
          Live ranking is unavailable right now. Here&rsquo;s a real firm&rsquo;s board
          instead.
        </p>
      )}

      {showSample && (
        <div className="mt-5">
          <BoardCard />
        </div>
      )}
    </div>
  );
}
