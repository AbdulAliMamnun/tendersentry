import { NextResponse } from "next/server";

import { isThin, rank } from "@/lib/demoRank";
import { clientIp, limiter } from "@/lib/rateLimit";

/**
 * Rank the open-tender pool against a visitor's description of their firm.
 *
 * Everything runs in-process: a keyword mapping, a cosine, and a tree walk over
 * cached arrays. No model is loaded at request time and nothing is called out to, so
 * a request costs CPU and nothing else.
 *
 * **The description is never stored and never logged.** What is recorded is the
 * matched trade slugs and whether anything matched at all — the only number that can
 * tell us whether the deterministic mapping is good enough or whether a real
 * embedding service is worth paying for.
 */

/** Long enough for a real description, short enough to bound the work. */
const MAX_DESCRIPTION = 500;

/** A ranking that takes longer than this is a bug, not a slow request. */
const TIME_BUDGET_MS = 3000;

export async function POST(request: Request) {
  const decision = limiter.check(clientIp(request));
  if (!decision.allowed) {
    return NextResponse.json(
      {
        error:
          decision.scope === "global"
            ? "The demo has hit its daily limit. Try again tomorrow, or request a board."
            : "That's a lot of ranking. Give it a minute and try again.",
      },
      {
        status: 429,
        headers: { "Retry-After": String(decision.retryAfterSeconds) },
      },
    );
  }

  let description: string;
  try {
    const payload = (await request.json()) as { description?: unknown };
    description = String(payload.description ?? "").slice(0, MAX_DESCRIPTION);
  } catch {
    return NextResponse.json({ error: "Invalid request." }, { status: 400 });
  }

  if (description.trim().length < 3) {
    return NextResponse.json(
      { error: "Describe what your firm builds, where, and typical job size." },
      { status: 400 },
    );
  }

  const started = Date.now();
  try {
    const result = rank(description);
    const elapsed = Date.now() - started;

    if (elapsed > TIME_BUDGET_MS) {
      console.warn(`demo-rank exceeded budget: ${elapsed}ms`);
    }
    // Slugs and the hit flag only. Never the description text.
    console.log(
      JSON.stringify({
        event: "demo_rank",
        hit: result.derived.hit,
        slugs: result.derived.slugs,
        regions: result.derived.regions,
        has_value: result.derived.valueBand !== null,
        considered: result.considered,
        on_trade: result.onTrade,
        ms: elapsed,
      }),
    );

    return NextResponse.json({
      reading: result.reading,
      hit: result.derived.hit,
      results: result.results,
      considered: result.considered,
      onTrade: result.onTrade,
      thin: isThin(result),
      regions: result.derived.regions,
      poolSize: result.poolSize,
      generatedAt: result.generatedAt,
    });
  } catch (error) {
    // The widget falls back to the static sample board, so the visitor still sees
    // something real. The failure is recorded here rather than shown to them.
    console.error("demo-rank failed", error);
    return NextResponse.json(
      { error: "Ranking is unavailable right now." },
      { status: 503 },
    );
  }
}
