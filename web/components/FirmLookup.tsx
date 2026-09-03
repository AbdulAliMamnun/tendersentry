"use client";

import { useState } from "react";

import { formatClosing } from "@/lib/data";
import { scaleLabel } from "@/lib/scale";

/**
 * Look up a firm's board from its public bidding record. **Beta surface only.**
 *
 * Framed throughout as looking up *your own* firm. We cannot enforce that — anyone
 * with the key can type any name — but the framing is not decoration: it sets what
 * this is for, and it is the honest description of the case we built it to serve.
 * The removal notice on every board it produces is the other half of that.
 *
 * Disambiguation never resolves itself. When a name is shared, the visitor picks;
 * choosing the busiest candidate for them would be a guess about which company they
 * meant, and being confidently wrong about a firm's identity is worse than asking.
 */

type Profile = {
  id: string;
  name: string;
  bids: number;
  sinceYear: string | null;
  categories: string[];
  regions: string[];
  lastActive: string | null;
};

type Row = {
  title: string;
  buyer: string;
  region: string | null;
  closingDate: string;
  url: string | null;
  source: string;
  fit: number;
  scaleBand: string;
  scaleSource: string;
};

type Response = {
  outcome: "match" | "ambiguous" | "none";
  profile?: Profile;
  candidates?: Profile[];
  results?: Row[];
  considered?: number;
  removalNotice?: string;
  message?: string;
  error?: string;
};

function basisLine(profile: Profile): string {
  const parts = [`${profile.bids.toLocaleString("en-CA")} bids`];
  if (profile.sinceYear) parts.push(`since ${profile.sinceYear}`);
  const trades = profile.categories.slice(0, 2).join(", ");
  if (trades) parts.push(`mostly ${trades.toLowerCase()}`);
  if (profile.regions.length) parts.push(`in ${profile.regions.join(", ")}`);
  return parts.join(" · ");
}

export function FirmLookup({ betaKey }: { betaKey: string }) {
  const [name, setName] = useState("");
  const [data, setData] = useState<Response | null>(null);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);

  async function submit(body: Record<string, string>) {
    if (busy) return;
    setBusy(true);
    setFailed(false);
    try {
      const response = await fetch(`/api/firm-lookup?key=${encodeURIComponent(betaKey)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const payload = (await response.json()) as Response;
      if (!response.ok) {
        setFailed(true);
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

  return (
    <section className="mt-12 border-t border-rule pt-10">
      <h2 className="text-[11px] font-semibold uppercase tracking-[0.12em] text-ink">
        Look up your firm
      </h2>
      <p className="mt-3 max-w-2xl text-sm leading-relaxed text-grey">
        If your firm has bid on Québec public procurement, we can rank today&rsquo;s
        market from your actual record rather than from a description — real bidding
        history, the buyers you have worked for, the sizes you work at.
      </p>

      <form
        onSubmit={(event) => {
          event.preventDefault();
          void submit({ name: name.trim() });
        }}
        className="mt-5 flex flex-col gap-3 sm:flex-row"
      >
        <input
          id="firm-name"
          value={name}
          maxLength={200}
          onChange={(event) => setName(event.target.value)}
          placeholder="Your firm's legal name — e.g. Construction ABC inc."
          className="w-full rounded-lg border border-rule bg-white px-4 py-3 text-[15px] text-ink outline-none placeholder:text-grey focus:border-teal"
        />
        <button
          type="submit"
          disabled={busy || name.trim().length < 2}
          className="btn-primary shrink-0 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy ? "Looking up…" : "Look up"}
        </button>
      </form>

      {failed && (
        <p className="mt-5 rounded-lg border border-rule px-5 py-4 text-sm leading-relaxed text-grey">
          Lookup is unavailable right now — nothing to do with what you typed.
        </p>
      )}

      {data?.outcome === "none" && (
        <div className="mt-5 rounded-lg border border-rule px-5 py-4">
          <p className="text-sm leading-relaxed text-grey">{data.message}</p>
        </div>
      )}

      {data?.outcome === "ambiguous" && data.candidates && (
        <div className="card mt-5 overflow-hidden">
          <p className="border-b border-rule px-5 py-3 text-sm text-grey sm:px-6">
            More than one firm files under that name. Which is yours?
          </p>
          <ul>
            {data.candidates.map((candidate) => (
              <li key={candidate.id} className="border-b border-rule last:border-b-0">
                <button
                  type="button"
                  onClick={() => void submit({ firmId: candidate.id })}
                  className="block w-full px-5 py-4 text-left hover:bg-white sm:px-6"
                >
                  <p className="text-[15px] font-medium text-ink">{candidate.name}</p>
                  <p className="mt-1 text-xs text-grey-light">
                    {basisLine(candidate)}
                    {candidate.lastActive ? ` · last active ${candidate.lastActive}` : ""}
                  </p>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {data?.outcome === "match" && data.profile && data.results && (
        <div className="card mt-5 overflow-hidden">
          <div className="border-b border-rule px-5 py-4 sm:px-6">
            <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-ink">
              Ranked from {data.profile.name}&rsquo;s public bidding record
            </p>
            {/* The basis, stated. Aggregate facts only — never which contracts. */}
            <p className="mt-1.5 text-xs text-grey-light">{basisLine(data.profile)}</p>
          </div>

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
                    {row.region ? ` · ${row.region}` : ""} · Closes{" "}
                    {formatClosing(row.closingDate)}
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

          {data.removalNotice && (
            <p className="border-t border-rule px-5 py-3 text-xs leading-relaxed text-grey-light sm:px-6">
              {data.removalNotice}
            </p>
          )}
        </div>
      )}
    </section>
  );
}
