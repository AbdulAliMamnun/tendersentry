import { NextResponse } from "next/server";

import { SKEW_THRESHOLD, isThin, provinceSkew, rank } from "@/lib/demoRank";
import { derive } from "@/lib/derive";
import { extractWithLlm, llmAvailable, toDerived } from "@/lib/llmExtract";
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

/** The visitor's own words for the size they gave, echoed back to them. */
function formatDeclaredSize(value: number | null): string | null {
  if (!value) return null;
  if (value >= 1_000_000) {
    const millions = value / 1_000_000;
    return `~$${millions % 1 === 0 ? millions.toFixed(0) : millions.toFixed(1)}M jobs`;
  }
  if (value >= 1_000) return `~$${Math.round(value / 1000)}K jobs`;
  return `~$${Math.round(value)} jobs`;
}

/** Long enough for a real description, short enough to bound the work. */
const MAX_DESCRIPTION = 500;

/** A ranking that takes longer than this is a bug, not a slow request. */
const TIME_BUDGET_MS = 3000;

export async function POST(request: Request) {
  const decision = limiter.check(clientIp(request));
  if (!decision.allowed) {
    return NextResponse.json(
      {
        // No "try again" here — the widget appends the exact wait from Retry-After.
        error:
          decision.scope === "global"
            ? "The demo has hit its daily limit. Try again tomorrow, or request a board."
            : "That's a lot of ranking for one visitor.",
      },
      {
        status: 429,
        headers: { "Retry-After": String(decision.retryAfterSeconds) },
      },
    );
  }

  let description: string;
  let regionOverride: string[] | undefined;
  try {
    const payload = (await request.json()) as {
      description?: unknown;
      region?: unknown;
    };
    description = String(payload.description ?? "").slice(0, MAX_DESCRIPTION);
    // "all" and an absent value are different: the first is an explicit instruction to
    // rank everything, the second means the description's own region still stands.
    const choice = payload.region === undefined || payload.region === null
      ? null
      : String(payload.region);
    if (choice === "all") regionOverride = [];
    else if (choice === "ON" || choice === "QC") regionOverride = [choice];
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
    // Tier 1: the deterministic mapping, unchanged. Zero cost, no network.
    let derived = derive(description);
    let tier: "keyword" | "llm" | "llm_capped" | "llm_miss" = "keyword";

    // Tier 2 fires only on a keyword miss, which is what structurally bounds volume.
    if (!derived.hit && llmAvailable()) {
      const budget = limiter.checkLlm(clientIp(request));
      if (!budget.allowed) {
        // Over budget: fall through to the no-hit message rather than degrade quietly.
        tier = "llm_capped";
      } else {
        const extracted = await extractWithLlm(description);
        if (extracted) {
          derived = toDerived(extracted, description);
          tier = "llm";
        } else {
          tier = "llm_miss";
        }
      }
    }

    const result = rank(description, { derived, regionOverride });
    const elapsed = Date.now() - started;

    if (elapsed > TIME_BUDGET_MS && tier === "keyword") {
      console.warn(`demo-rank exceeded budget: ${elapsed}ms`);
    }
    // Slugs, tier, and the hit flag only. Never the description text.
    console.log(
      JSON.stringify({
        event: "demo_rank",
        tier,
        hit: result.derived.hit,
        slugs: result.derived.slugs,
        regions: result.effectiveRegions,
        region_source: result.regionSource,
        has_value: result.derived.valueBand !== null,
        considered: result.considered,
        on_trade: result.onTrade,
        ms: elapsed,
      }),
    );

    return NextResponse.json({
      reading: result.reading,
      // The visitor is told when a field was inferred rather than read.
      interpreted: tier === "llm",
      declaredSize: formatDeclaredSize(result.derived.valueBand),
      hit: result.derived.hit,
      // similarity and rawScore are calibration internals; the browser gets the
      // display fields and the absolute fit only.
      results: result.results.map(({ similarity, rawScore, ...row }) => {
        void similarity;
        void rawScore;
        return row;
      }),
      considered: result.considered,
      onTrade: result.onTrade,
      thin: isThin(result),
      regions: result.effectiveRegions,
      regionSource: result.regionSource,
      // Only meaningful on an unfiltered board: a Québec-heavy list is expected when
      // the visitor asked for Québec, and needs no explanation.
      skew:
        result.regionSource === "none"
          ? (() => {
              const worst = provinceSkew(result.results);
              return worst && worst.share > SKEW_THRESHOLD ? worst : null;
            })()
          : null,
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
