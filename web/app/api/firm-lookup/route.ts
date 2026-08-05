import { NextResponse } from "next/server";

import { rankForFirm } from "@/lib/demoRank";
import { firmById, lookupFirm, summarize, type FirmProfile } from "@/lib/firmLookup";
import { clientIp, limiter } from "@/lib/rateLimit";

/**
 * Firm-name lookup. **Gated: beta cohort only, never the public demo.**
 *
 * Every record behind this is public. The profile is not: "3,028 bids since 2017,
 * mostly roadwork for municipalities in the Montérégie" is a thing we assembled, about
 * a private company that never asked to be assembled. Individually-public records
 * aggregated into a competitive profile is a different product from a demo, with a
 * different risk surface — the firm cannot opt out, cannot see who looked, and did not
 * choose to be indexed. So this sits behind the beta form, framed as looking up your
 * own firm, with a removal line on every board it produces.
 *
 * What it will never return, regardless of who is asking:
 *
 *   - the list of procurements a named firm bid on
 *   - any bid amount attributed to a named firm
 *   - anything below the aggregate level
 *
 * `tests/test_profiles.py` asserts the artifact cannot carry those in the first place,
 * so the guarantee does not rest on this handler being careful.
 */

/** Beta access. Absent from the environment, the route does not exist. */
function authorized(request: Request): boolean {
  const expected = process.env.BETA_ACCESS_KEY;
  if (!expected) return false;
  const provided =
    request.headers.get("x-beta-key") ??
    new URL(request.url).searchParams.get("key") ??
    "";
  return provided.length > 0 && provided === expected;
}

/** Aggregate facts only — the basis line, and nothing that identifies a contract. */
function publicProfile(profile: FirmProfile) {
  const summary = summarize(profile);
  return {
    id: profile.id,
    name: summary.name,
    bids: summary.bids,
    sinceYear: summary.sinceYear,
    categories: summary.categories,
    regions: summary.regions,
    lastActive: profile.last ? profile.last.slice(0, 7) : null,
  };
}

export async function POST(request: Request) {
  if (!authorized(request)) {
    // Deliberately indistinguishable from a route that does not exist.
    return NextResponse.json({ error: "Not found." }, { status: 404 });
  }

  const ip = clientIp(request);
  const decision = limiter.check(ip);
  if (!decision.allowed) {
    return NextResponse.json(
      { error: "That's a lot of lookups for one visitor." },
      { status: 429, headers: { "Retry-After": String(decision.retryAfterSeconds) } },
    );
  }

  let query: string;
  let chosenId: string | null = null;
  try {
    const payload = (await request.json()) as { name?: unknown; firmId?: unknown };
    query = String(payload.name ?? "").slice(0, 200);
    chosenId = payload.firmId ? String(payload.firmId) : null;
  } catch {
    return NextResponse.json({ error: "Invalid request." }, { status: 400 });
  }

  // Answering a disambiguation prompt: the visitor already told us which firm.
  if (chosenId) {
    const profile = firmById(chosenId);
    if (!profile) return NextResponse.json({ error: "Unknown firm." }, { status: 404 });
    return NextResponse.json(board(profile, "chosen"));
  }

  if (query.trim().length < 2) {
    return NextResponse.json({ error: "Enter a firm name." }, { status: 400 });
  }

  const outcome = lookupFirm(query);

  // Normalized name only — never the raw input, and never who asked. Enough to see an
  // abuse pattern, not enough to be a log of who is researching whom.
  console.log(
    JSON.stringify({
      event: "firm_lookup",
      outcome: outcome.kind,
      candidates: outcome.kind === "ambiguous" ? outcome.candidates.length : 1,
    }),
  );

  if (outcome.kind === "none") {
    return NextResponse.json({
      outcome: "none",
      // Coverage is Québec: 199,644 of 199,714 priced awards are QC, Ontario has 9.
      // An Ontario firm missing here is the expected case, not a failure of search.
      message:
        "We don't hold a bidding record for that name. Our record comes from Québec's " +
        "open procurement data — Ontario publishes almost nothing comparable, so most " +
        "Ontario firms won't be found. Describe what you build instead and we'll rank " +
        "from that.",
    });
  }

  if (outcome.kind === "ambiguous") {
    return NextResponse.json({
      outcome: "ambiguous",
      candidates: outcome.candidates.map(publicProfile),
    });
  }

  return NextResponse.json(board(outcome.profile, outcome.confidence));
}

function board(profile: FirmProfile, confidence: string) {
  const result = rankForFirm(profile);
  return {
    outcome: "match",
    confidence,
    profile: publicProfile(profile),
    results: result.results.map(({ similarity, rawScore, ...row }) => {
      void similarity;
      void rawScore;
      return row;
    }),
    considered: result.considered,
    generatedAt: result.generatedAt,
    // Shown on every profile-derived board. A firm that wants out should not have to
    // find a contact page to ask.
    removalNotice:
      "Built from public procurement records. If this is your firm and you'd rather " +
      "not appear, contact us and we'll remove it.",
  };
}
