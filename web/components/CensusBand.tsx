"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import type { Bucket } from "@/lib/data";

const BUCKET_COLORS: Record<string, string> = {
  bids_and_tenders: "#A32D2D",
  unknown: "#d6d3d1",
  notices_gated: "#c9bfae",
  other_platforms: "#8a7f70",
  open: "#477054",
};

/**
 * The homepage census band: one stacked bar, a five-item legend, and a lookup that
 * hands off to the census section of /research.
 */
export function CensusBand({ buckets }: { buckets: Bucket[] }) {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const total = buckets.reduce((sum, bucket) => sum + bucket.share_of_population, 0);

  function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    const target = query.trim()
      ? `/research?q=${encodeURIComponent(query.trim())}`
      : "/research";
    router.push(target);
  }

  return (
    <div className="card p-6 sm:p-7">
      <p className="text-xs font-medium text-muted">
        Share of Ontario&rsquo;s population, by where their municipality posts tenders
      </p>

      <div
        className="mt-4 flex h-3 w-full overflow-hidden rounded-pill"
        role="img"
        aria-label="Distribution of Ontario's population by tender publishing channel"
      >
        {buckets.map((bucket) => (
          <div
            key={bucket.key}
            title={`${bucket.label}: ${bucket.share_of_population.toFixed(1)}%`}
            style={{
              width: `${(bucket.share_of_population / total) * 100}%`,
              backgroundColor: BUCKET_COLORS[bucket.key] ?? "#d6d3d1",
            }}
          />
        ))}
      </div>

      <ul className="mt-5 space-y-2.5">
        {buckets.map((bucket) => (
          <li key={bucket.key} className="flex items-center gap-2.5 text-sm">
            <span
              className="h-2.5 w-2.5 shrink-0 rounded-sm"
              style={{ backgroundColor: BUCKET_COLORS[bucket.key] ?? "#d6d3d1" }}
              aria-hidden
            />
            <span className="text-body">{bucket.label}</span>
            <span className="ml-auto font-semibold tabular-nums text-heading">
              {bucket.share_of_population < 1
                ? "<1%"
                : `${Math.round(bucket.share_of_population)}%`}
            </span>
          </li>
        ))}
      </ul>

      <form onSubmit={onSubmit} className="mt-6 flex flex-col gap-2 sm:flex-row">
        <input
          type="text"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Look up your municipality…"
          aria-label="Look up your municipality"
          className="field flex-1"
        />
        <button type="submit" className="btn-outline">
          Look up
        </button>
      </form>
    </div>
  );
}
