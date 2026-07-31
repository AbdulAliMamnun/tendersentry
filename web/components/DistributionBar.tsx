"use client";

import { useState } from "react";
import type { DistributionRow } from "@/lib/data";

const CLASS_COLORS: Record<string, string> = {
  bids_and_tenders: "#A32D2D",
  no_procurement_page_found: "#d6d3d1",
  fetch_failed: "#e7e5e4",
  own_site_notices: "#c9bfae",
  biddingo: "#8a7f70",
  bidnet_or_other_platform: "#a89c8a",
  own_site_open: "#477054",
  no_website_listed: "#efece6",
  robots_disallowed: "#f5f3ef",
};

/** Population-weighted stacked bar over all nine classes, with hover detail. */
export function DistributionBar({ rows }: { rows: DistributionRow[] }) {
  const [active, setActive] = useState<DistributionRow | null>(null);
  const total = rows.reduce((sum, row) => sum + row.population, 0) || 1;
  const ordered = [...rows].sort((a, b) => b.population - a.population);

  return (
    <div>
      <div
        className="flex h-10 w-full overflow-hidden rounded-control border border-hairline"
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
              backgroundColor: CLASS_COLORS[row.classification] ?? "#d6d3d1",
            }}
            className="h-full transition-opacity hover:opacity-80"
          />
        ))}
      </div>

      <div className="mt-3 min-h-[42px] text-sm">
        {active ? (
          <p className="text-body">
            <span className="font-semibold text-heading">{active.label}</span> ·{" "}
            {active.municipalities} municipalities ({active.share_of_municipalities}%) ·{" "}
            {active.population.toLocaleString("en-CA")} residents (
            {active.share_of_population}% of population)
          </p>
        ) : (
          <p className="text-muted">
            Hover a segment for detail. Width is share of population, not share of
            municipalities — a township and Ottawa are not equal inventory.
          </p>
        )}
      </div>
    </div>
  );
}
