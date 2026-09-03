"use client";

import { useState } from "react";
import type { DistributionRow } from "@/lib/data";

/**
 * The same open-to-closed ramp as the homepage band, over nine classes.
 *
 * Three families, each monotone: teal where a notice is reachable, grey where a gated
 * platform stands between the buyer and the bidder, pale where we could not determine
 * anything. No `flag` — see CensusBand. `chart.slate` and `chart.pale` are the only
 * two steps the palette did not already supply; they are declared as tokens in
 * tailwind.config.ts rather than invented here.
 */
const CLASS_COLORS: Record<string, string> = {
  own_site_open: "#0E5459", // teal
  own_site_notices: "#14747B", // teal-mid
  bids_and_tenders: "#5F676C", // grey
  biddingo: "#8B9296", // grey-light
  bidnet_or_other_platform: "#B4BABD", // chart-slate
  no_procurement_page_found: "#C6CBCD", // chart-pale
  fetch_failed: "#DCE0E1", // rule
  no_website_listed: "#E8F0F0", // teal-wash
  robots_disallowed: "#F4F6F6", // mist
};

/** Population-weighted stacked bar over all nine classes, with hover detail. */
export function DistributionBar({ rows }: { rows: DistributionRow[] }) {
  const [active, setActive] = useState<DistributionRow | null>(null);
  const total = rows.reduce((sum, row) => sum + row.population, 0) || 1;
  const ordered = [...rows].sort((a, b) => b.population - a.population);

  return (
    <div>
      <div
        className="flex h-10 w-full overflow-hidden rounded-control border border-rule"
        onMouseLeave={() => setActive(null)}
        role="img"
        aria-label="Ontario's population by how their municipality publishes tenders"
      >
        {ordered.map((row) => (
          <button
            key={row.classification}
            type="button"
            onMouseEnter={() => setActive(row)}
            onFocus={() => setActive(row)}
            title={`${row.label}: ${row.share_of_population}% of population`}
            aria-label={`${row.label}: ${row.share_of_population}% of population`}
            style={{
              width: `${(row.population / total) * 100}%`,
              backgroundColor: CLASS_COLORS[row.classification] ?? "#DCE0E1",
            }}
            className="h-full transition-opacity hover:opacity-80"
          />
        ))}
      </div>

      <div className="mt-3 min-h-[42px] text-sm">
        {active ? (
          <p className="text-grey">
            <span className="font-semibold text-ink">{active.label}</span> ·{" "}
            {active.municipalities} municipalities ({active.share_of_municipalities}%) ·{" "}
            {active.population.toLocaleString("en-CA")} residents (
            {active.share_of_population}% of population)
          </p>
        ) : (
          <p className="text-grey">
            Hover a segment for detail. Width is share of population, not share of
            municipalities — a township and Ottawa are not equal inventory.
          </p>
        )}
      </div>
    </div>
  );
}
