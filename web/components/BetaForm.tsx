"use client";

import { useState } from "react";

const JOB_SIZES = [
  "Under $250k",
  "$250k – $1M",
  "$1M – $5M",
  "Over $5M",
];

/**
 * Beta signup — a board request.
 *
 * Posts through the same intake route as the free check, tagged so board requests
 * are distinguishable from tender checks in the inbox. Trades and regions are free
 * text on purpose: a contractor should describe their work in their own words, and
 * mapping that onto the controlled vocabulary is our job, not theirs.
 */
export function BetaForm() {
  const [email, setEmail] = useState("");
  const [firm, setFirm] = useState("");
  const [trades, setTrades] = useState("");
  const [regions, setRegions] = useState("");
  const [jobSize, setJobSize] = useState("");
  const [state, setState] = useState<"idle" | "sending" | "done" | "error">("idle");
  const [message, setMessage] = useState("");

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setState("sending");
    try {
      const response = await fetch("/api/check", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          kind: "board",
          email,
          firm: firm.trim() || null,
          trades: trades.trim() || null,
          regions: regions.trim() || null,
          jobSize: jobSize || null,
        }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error ?? "Something went wrong");
      setState("done");
    } catch (error) {
      setState("error");
      setMessage(error instanceof Error ? error.message : "Something went wrong");
    }
  }

  if (state === "done") {
    return (
      <div className="md:max-w-md">
        <p className="text-[15px] font-semibold text-heading">
          Got it — we&rsquo;ll build your board.
        </p>
        <p className="mt-2 text-sm leading-relaxed text-body">
          We&rsquo;ll email <strong>{email}</strong> a private link to it. If anything
          about your trades or regions is ambiguous, we&rsquo;ll ask rather than guess.
        </p>
      </div>
    );
  }

  return (
    <form onSubmit={onSubmit} className="md:max-w-md md:text-left">
      <label htmlFor="beta-email" className="block text-sm font-medium text-heading">
        Get your firm&rsquo;s board
      </label>
      <p className="mt-1.5 text-sm text-body">
        Tell us what you build and where. Free while in beta.
      </p>

      <div className="mt-4 space-y-2.5">
        <input
          id="beta-email"
          type="email"
          required
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          placeholder="you@firm.ca *"
          aria-label="Your email"
          className="field"
        />
        <input
          type="text"
          value={firm}
          onChange={(event) => setFirm(event.target.value)}
          placeholder="Firm name"
          aria-label="Firm name"
          className="field"
        />
        <input
          type="text"
          value={trades}
          onChange={(event) => setTrades(event.target.value)}
          placeholder="Trades — e.g. watermains, culverts, road reconstruction"
          aria-label="Trades"
          className="field"
        />
        <input
          type="text"
          value={regions}
          onChange={(event) => setRegions(event.target.value)}
          placeholder="Regions — e.g. Simcoe, Muskoka, Grey"
          aria-label="Regions"
          className="field"
        />
        <select
          value={jobSize}
          onChange={(event) => setJobSize(event.target.value)}
          aria-label="Typical job size"
          className="field text-body"
        >
          <option value="">Typical job size (optional)</option>
          {JOB_SIZES.map((size) => (
            <option key={size} value={size}>
              {size}
            </option>
          ))}
        </select>
      </div>

      <button type="submit" className="btn-primary mt-4 w-full" disabled={state === "sending"}>
        {state === "sending" ? "Sending…" : "Request my board"}
      </button>

      {state === "error" ? (
        <p className="mt-2 text-sm text-brand-red">{message}</p>
      ) : null}
    </form>
  );
}
