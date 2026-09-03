"use client";

import Link from "next/link";
import { useState } from "react";

import { BoardCard } from "@/components/BoardCard";
import { formatClosing } from "@/lib/data";
import { scaleLabel } from "@/lib/scale";
import { dataAsOf } from "@/lib/freshness";

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
  scaleBand: string;
  scaleSource: string;
  scaleConfidence: number;
};

type Response = {
  reading: string;
  /** True when the second-tier LLM read the description the keyword mapping missed. */
  interpreted?: boolean;
  /** Set when the visitor stated a job size, so the card can say what we did with it. */
  declaredSize?: string | null;
  hit: boolean;
  results: Row[];
  considered: number;
  onTrade: number;
  thin: boolean;
  regions: string[];
  regionSource?: "derived" | "selected" | "none";
  skew?: { province: string; share: number } | null;
  poolSize: number;
  error?: string;
};

type RegionChoice = "all" | "ON" | "QC";

const REGION_OPTIONS: { value: RegionChoice; label: string }[] = [
  // Shortened from "All (Ontario & Québec)": at full length the control truncated the
  // example placeholder, and the example is the thing that gets good answers.
  { value: "all", label: "All regions" },
  { value: "ON", label: "Ontario" },
  { value: "QC", label: "Québec" },
];

const PROVINCE_NAMES: Record<string, string> = { ON: "Ontario", QC: "Québec" };

const EXAMPLES = [
  "Watermain and sanitary sewer replacement, jobs around $2M",
  "Entrepreneur en pavage et travaux routiers, Montérégie",
  "Rooftop HVAC and electrical retrofits for school boards",
];

/**
 * A whole example answer rather than a description of the field.
 *
 * Showing the shape of a good answer is what gets good answers: trade, buyer type,
 * place, and a size range in one sentence. A placeholder that describes the field
 * ("what you build, where, and typical job size") tells someone what to think about;
 * this tells them what a finished answer looks like.
 */
const PLACEHOLDER =
  "e.g. We do watermain and sewer replacement for municipalities around Barrie, jobs $300K–$1.5M";

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
  // Once the visitor has asked a question, every answer belongs to them. The example
  // board is scenery for the empty page, not a consolation prize for a failed attempt.
  const [attempted, setAttempted] = useState(false);
  // The selector shows the region in force, whether the visitor picked it or the
  // description implied it. `regionTouched` is what separates the two: until they
  // touch it, the control is a readout of what was inferred and the description keeps
  // winning; after, it is an instruction that overrides the text.
  const [region, setRegion] = useState<RegionChoice>("all");
  const [regionTouched, setRegionTouched] = useState(false);

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
        body: JSON.stringify({
          description: value,
          region: regionTouched ? region : null,
        }),
      });
      const payload = (await response.json()) as Response;
      if (!response.ok) {
        if (response.status === 429) {
          // Say how long, not "in a moment" — the server already computed it.
          const seconds = Number(response.headers.get("Retry-After"));
          const wait = Number.isFinite(seconds) && seconds > 0 ? seconds : null;
          setMessage(
            `${payload.error ?? "Too many requests."}${
              wait ? ` Try again in ${wait} second${wait === 1 ? "" : "s"}.` : ""
            }`,
          );
        } else {
          setFailed(true);
        }
        setData(null);
      } else {
        setData(payload);
        // Show what was inferred, so the visitor can see it and override it. This is a
        // display update, not a choice — the control stays untouched.
        if (!regionTouched && payload.regionSource === "derived" && payload.regions.length === 1) {
          const inferred = payload.regions[0];
          if (inferred === "ON" || inferred === "QC") setRegion(inferred);
        }
      }
    } catch {
      setFailed(true);
      setData(null);
    } finally {
      setBusy(false);
      setAttempted(true);
    }
  }

  // Only before the first attempt. After one, the visitor asked about their own firm,
  // and answering with a stranger's board is noise — the explanation is the answer.
  const showExample = !attempted;

  return (
    <div>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void submit(description);
        }}
        className="card p-5 sm:p-6"
      >
        <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
          <label
            htmlFor="firm-description"
            className="text-[11px] font-semibold uppercase tracking-[0.12em] text-ink"
          >
            What does your firm build, and what size jobs do you want?
          </label>
          {/* Freshness as a stated fact rather than something a visitor has to
              assume. The same timestamp tests/test_freshness.py fails the suite over. */}
          <span className="text-[11px] text-grey-light">Data as of {dataAsOf()}</span>
        </div>
        <div className="mt-3 flex flex-col gap-3 sm:flex-row">
          <input
            id="firm-description"
            value={description}
            maxLength={500}
            onChange={(event) => setDescription(event.target.value)}
            placeholder={PLACEHOLDER}
            className="w-full rounded-lg border border-rule bg-white px-4 py-3 text-[15px] text-ink outline-none placeholder:text-grey focus:border-teal"
          />
          <label htmlFor="firm-region" className="sr-only">
            Region
          </label>
          <select
            id="firm-region"
            value={region}
            onChange={(event) => {
              setRegion(event.target.value as RegionChoice);
              setRegionTouched(true);
            }}
            className="shrink-0 rounded-lg border border-rule bg-white px-3 py-3 text-[15px] text-ink outline-none focus:border-teal sm:w-auto"
          >
            {REGION_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
          <button
            type="submit"
            disabled={busy || description.trim().length < 3}
            className="btn-primary shrink-0 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {busy ? "Ranking…" : "Rank"}
          </button>
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-2">
          <span className="text-xs text-grey-light">Try:</span>
          {EXAMPLES.map((example) => (
            <button
              key={example}
              type="button"
              onClick={() => {
                setDescription(example);
                void submit(example);
              }}
              className="rounded-pill border border-rule px-2.5 py-1 text-xs text-grey hover:border-teal hover:text-teal"
            >
              {example}
            </button>
          ))}
        </div>

        {/* The hint that used to appear only after a failed match. It is advice about
            how to write the input, so it belongs beside the input. */}
        <p className="mt-3 text-xs leading-relaxed text-grey-light">
          Name the work directly — &ldquo;watermain replacement&rdquo;,
          &ldquo;pavage&rdquo;, &ldquo;roofing&rdquo;.
        </p>
        {/* One line. Everything else waits until there are results to explain. */}
        <p className="mt-2 text-xs leading-relaxed text-grey-light">
          Description isn&rsquo;t stored — only which trades it matched.
        </p>
      </form>

      {message && (
        <p className="mt-4 text-center text-sm text-flag">{message}</p>
      )}

      {data && data.hit && (
        <div className="card mt-5 overflow-hidden">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-rule px-5 py-4 sm:px-6">
            <div className="flex items-center gap-2.5">
              <span
                className="live-dot h-2 w-2 rounded-full bg-teal"
                aria-hidden
              />
              <span className="text-[11px] font-semibold uppercase tracking-[0.12em] text-ink">
                {/* Say when a field was inferred rather than read from the words, and
                    where the region in force came from. */}
                Reading{data.interpreted ? " (interpreted)" : ""}: {data.reading}
                {data.regionSource === "selected"
                  ? " (you selected)"
                  : data.regionSource === "derived" && data.regions.length
                    ? " (from your description)"
                    : ""}
              </span>
            </div>
            <span className="text-xs text-grey-light">
              {data.onTrade.toLocaleString()} on-trade of{" "}
              {data.considered.toLocaleString()} open notices
            </span>
          </div>

          {data.declaredSize && (
            <p className="border-b border-rule px-5 py-2.5 text-xs leading-relaxed text-grey-light sm:px-6">
              You said {data.declaredSize} — showing fit against published and estimated
              contract sizes; estimates are ours, not the buyer&rsquo;s.
            </p>
          )}

          {data.thin && (
            <div className="border-b border-rule bg-mist px-5 py-3 sm:px-6">
              <p className="text-sm leading-relaxed text-ink">
                {data.onTrade === 0 ? (
                  <>
                    Nothing open matches those trades in{" "}
                    {data.regions.join(" & ") || "the pool"} right now — so there is
                    nothing honest to rank, and we&rsquo;d rather show you that than a
                    list of near-misses.
                  </>
                ) : (
                  <>
                    Only {data.onTrade} open{" "}
                    {data.onTrade === 1 ? "notice matches" : "notices match"} those
                    trades in {data.regions.join(" & ") || "the pool"} right now — every
                    one is below, and it isn&rsquo;t the whole market.
                  </>
                )}
              </p>
              {/* The demo's weakest moment is also the census finding, so it links to
                  the evidence rather than apologising. */}
              <p className="mt-1.5 text-sm leading-relaxed text-grey">
                Most Ontario municipal tenders sit behind gated portals.{" "}
                <Link
                  href="/research"
                  className="font-medium text-teal hover:opacity-80"
                >
                  See why &rarr;
                </Link>{" "}
                Your board also draws on monitored municipal sources and your own
                uploads —{" "}
                <a href="#join" className="font-medium text-teal hover:opacity-80">
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
                className="flex items-start justify-between gap-4 border-b border-rule px-5 py-4 last:border-b-0 sm:px-6"
              >
                <div className="min-w-0">
                  <p className="text-[15px] font-medium leading-snug text-ink">
                    {row.url ? (
                      <a
                        href={row.url}
                        target="_blank"
                        rel="noopener noreferrer nofollow"
                        className="hover:text-teal"
                      >
                        {row.title}
                      </a>
                    ) : (
                      row.title
                    )}
                  </p>
                  <p className="mt-1 text-xs text-grey-light">
                    {row.buyer}
                    {row.region ? ` · ${row.region}` : ""} ·{" "}
                    {sourceLabel(row.source)} · Closes {formatClosing(row.closingDate)}
                  </p>
                  {(() => {
                    const scale = scaleLabel(row);
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
                  {Math.round(row.fit)} fit
                </span>
              </li>
            ))}
          </ul>

          {data.results.length > 0 && (
            <div className="space-y-2 border-t border-rule px-5 py-3 text-xs leading-relaxed text-grey-light sm:px-6">
              <p>
                Fit is an absolute score, not a rank within this list — a low number
                means a weak match, not fifth place. It measures bid fit, never a chance
                of winning.
              </p>
              {data.skew && (
                <p>
                  Most matches here are in{" "}
                  {PROVINCE_NAMES[data.skew.province] ?? data.skew.province} —
                  Ontario&rsquo;s municipal tenders are largely behind gated portals.{" "}
                  <Link href="/research" className="font-medium text-teal hover:opacity-80">
                    Why →
                  </Link>
                </p>
              )}
              <p>
                Contract sizes marked estimated are inferred from historical bids on
                similar work — check the notice for the buyer&rsquo;s own figures.
              </p>
              <p>
                This demo ranks by description fit alone. Your full board also uses
                bidding history and compliance checks.
              </p>
            </div>
          )}
        </div>
      )}

      {data && !data.hit && (
        <p className="mt-5 rounded-lg border border-rule px-5 py-4 text-sm leading-relaxed text-grey">
          We couldn&rsquo;t recognise a trade in that. Try naming the work more
          directly and we&rsquo;ll rank the live market against it.
        </p>
      )}

      {failed && (
        <p className="mt-5 rounded-lg border border-rule px-5 py-4 text-sm leading-relaxed text-grey">
          Live ranking is unavailable right now — nothing to do with what you typed.
          Try again in a moment.
        </p>
      )}

      {showExample && (
        <div className="mt-5">
          <BoardCard />
        </div>
      )}
    </div>
  );
}
