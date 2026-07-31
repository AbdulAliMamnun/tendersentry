"use client";

import { useState } from "react";

/** Beta signup. Posts to the same intake route as the free check. */
export function BetaForm() {
  const [email, setEmail] = useState("");
  const [state, setState] = useState<"idle" | "sending" | "done" | "error">("idle");
  const [message, setMessage] = useState("");

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setState("sending");
    try {
      const response = await fetch("/api/check", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, kind: "beta" }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error ?? "Something went wrong");
      setState("done");
      setMessage("You're on the list. We'll be in touch shortly.");
    } catch (error) {
      setState("error");
      setMessage(error instanceof Error ? error.message : "Something went wrong");
    }
  }

  if (state === "done") {
    return (
      <div className="md:max-w-sm">
        <p className="text-[15px] font-semibold text-heading">Thanks — you&rsquo;re in.</p>
        <p className="mt-2 text-sm text-body">{message}</p>
      </div>
    );
  }

  return (
    <form onSubmit={onSubmit} className="md:max-w-sm">
      <label htmlFor="beta-email" className="block text-sm font-medium text-heading">
        Join the beta
      </label>
      <div className="mt-3 flex flex-col gap-2 sm:flex-row md:justify-end">
        <input
          id="beta-email"
          type="email"
          required
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          placeholder="you@firm.ca"
          className="field sm:w-56"
        />
        <button type="submit" className="btn-primary" disabled={state === "sending"}>
          {state === "sending" ? "Sending…" : "Request access"}
        </button>
      </div>
      {state === "error" ? (
        <p className="mt-2 text-sm text-brand-red">{message}</p>
      ) : null}
    </form>
  );
}
