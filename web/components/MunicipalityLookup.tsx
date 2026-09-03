"use client";

import { useMemo, useState } from "react";
import type { Municipality } from "@/lib/data";

const PILL_STYLES: Record<string, string> = {
  own_site_open: "bg-teal-wash text-teal",
  own_site_notices: "bg-teal-wash text-teal-mid",
  bids_and_tenders: "bg-mist text-grey",
  biddingo: "bg-mist text-grey",
  bidnet_or_other_platform: "bg-mist text-grey",
};

const TIER_LABELS: Record<string, string> = {
  upper: "Upper tier",
  lower: "Lower tier",
  single: "Single tier",
};

function pillClass(classification: string): string {
  return PILL_STYLES[classification] ?? "bg-white text-grey";
}

/** Client-side search across all 444 municipalities. */
export function MunicipalityLookup({
  municipalities,
  initialQuery = "",
}: {
  municipalities: Municipality[];
  initialQuery?: string;
}) {
  const [query, setQuery] = useState(initialQuery);

  const results = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return [];
    return municipalities
      .filter(
        (item) =>
          item.name.toLowerCase().includes(needle) ||
          item.area.toLowerCase().includes(needle),
      )
      .slice(0, 12);
  }, [municipalities, query]);

  return (
    <div className="card p-6 sm:p-7">
      <label htmlFor="lookup" className="block text-sm font-medium text-ink">
        Look up a municipality
      </label>
      <p className="mt-1.5 text-sm text-grey">
        All {municipalities.length} Ontario municipalities, searchable by name or county.
      </p>
      <input
        id="lookup"
        type="text"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        placeholder="Muskoka Lakes, Ottawa, Simcoe…"
        className="field mt-4"
        autoComplete="off"
      />

      {query.trim() ? (
        <div className="mt-5">
          {results.length === 0 ? (
            <p className="text-sm text-grey">
              No municipality matches &ldquo;{query.trim()}&rdquo;.
            </p>
          ) : (
            <ul className="divide-y divide-rule">
              {results.map((item) => (
                <li
                  key={item.slug}
                  className="flex flex-wrap items-center gap-x-3 gap-y-2 py-3"
                >
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium text-ink">
                      {item.name}
                    </p>
                    <p className="mt-0.5 text-xs text-grey-light">
                      {TIER_LABELS[item.tier] ?? item.tier}
                      {item.area ? ` · ${item.area}` : ""}
                      {item.population
                        ? ` · ${item.population.toLocaleString("en-CA")} residents`
                        : " · population unmatched"}
                    </p>
                  </div>
                  <span
                    className={`shrink-0 rounded-pill px-2.5 py-1 text-xs font-semibold ${pillClass(
                      item.classification,
                    )}`}
                  >
                    {item.label}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : null}
    </div>
  );
}
