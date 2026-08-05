/**
 * In-memory sliding-window rate limiting.
 *
 * Deliberately not durable. This endpoint has no per-query cost — the scoring is a
 * tree walk over cached arrays — so the exposure is CPU, not spend, and a Redis
 * add-on would buy marginal protection for real config surface. Limits are per
 * instance; a burst spread across instances gets proportionally more headroom, which
 * is an accepted trade for a free demo.
 *
 * **Revisit this the moment the endpoint gains a per-query cost** — an external
 * embedding service would make durable, cross-instance limiting necessary rather than
 * optional.
 *
 * Generous for a human exploring phrasings; hostile to a loop.
 */

export type LimitRule = { windowMs: number; max: number; label: string };

export const PER_IP_RULES: LimitRule[] = [
  { windowMs: 60_000, max: 10, label: "minute" },
  { windowMs: 3_600_000, max: 60, label: "hour" },
];

export const GLOBAL_RULE: LimitRule = {
  windowMs: 86_400_000,
  max: 2_000,
  label: "day",
};

/**
 * The LLM tier gets its own, much stricter budget.
 *
 * Unlike the ranking path this one has a real per-query cost, so the limits are set
 * where a curious visitor never notices them and a loop stops immediately: three
 * unrecognised descriptions an hour is well past what exploring the demo produces.
 *
 * **This ceiling is approximate and deliberately so.** Counting is per instance, so the
 * true daily maximum is the cap times however many instances Vercel happens to run.
 * Shipping an approximate ceiling now buys real volume data; a durable counter costs
 * config surface to defend against a guess.
 *
 * **Follow-up trigger — make this durable (Redis or equivalent) when either holds:**
 * launch-week logs show LLM-tier volume approaching the daily cap on any instance, or
 * any single address shows a pattern that reads as abuse rather than exploration.
 * Until one of those fires, the approximation is the right trade.
 */
export const LLM_IP_RULE: LimitRule = { windowMs: 3_600_000, max: 3, label: "hour" };
export const LLM_GLOBAL_RULE: LimitRule = { windowMs: 86_400_000, max: 200, label: "day" };

/** Only sweep once the key count is large enough to be worth the pass. */
const SWEEP_THRESHOLD = 500;

/** Keys that count everyone, and so must survive the per-address sweep. */
const GLOBAL_KEYS = new Set(["global", "llm:global"]);

/**
 * How far back a key's history must be kept: the widest window it is judged against.
 * Pruning to anything shorter would let the hourly and daily rules under-count.
 */
function horizonFor(key: string): number {
  if (key === "global") return GLOBAL_RULE.windowMs;
  if (key === "llm:global") return LLM_GLOBAL_RULE.windowMs;
  if (key.startsWith("llm:")) return LLM_IP_RULE.windowMs;
  return Math.max(...PER_IP_RULES.map((rule) => rule.windowMs));
}

export type LimitDecision = {
  allowed: boolean;
  retryAfterSeconds: number;
  scope: "ip" | "global" | null;
};

/** Keeps hit timestamps per key and prunes them lazily. */
export class RateLimiter {
  private readonly hits = new Map<string, number[]>();
  private readonly now: () => number;

  constructor(now: () => number = () => Date.now()) {
    this.now = now;
  }

  /** Record and judge one request. */
  check(ip: string): LimitDecision {
    const timestamp = this.now();
    this.sweep(timestamp);

    for (const rule of PER_IP_RULES) {
      const decision = this.evaluate(`ip:${ip}`, rule, timestamp);
      if (!decision.allowed) return { ...decision, scope: "ip" };
    }
    const global = this.evaluate("global", GLOBAL_RULE, timestamp);
    if (!global.allowed) return { ...global, scope: "global" };

    // Only record once every rule has passed, so a rejected request does not deepen
    // the hole it is already in.
    this.record(`ip:${ip}`, timestamp);
    this.record("global", timestamp);
    return { allowed: true, retryAfterSeconds: 0, scope: null };
  }

  /**
   * Judge one rule without recording. Prunes to the widest window it is asked about,
   * so the per-minute check never discards hits the per-hour rule still needs.
   */
  private evaluate(key: string, rule: LimitRule, timestamp: number): LimitDecision {
    const stored = this.hits.get(key) ?? [];
    const inWindow = stored.filter((entry) => entry > timestamp - rule.windowMs);

    if (inWindow.length >= rule.max) {
      // The window frees up when its oldest hit ages out.
      const oldest = inWindow[0];
      return {
        allowed: false,
        retryAfterSeconds: Math.max(
          1,
          Math.ceil((oldest + rule.windowMs - timestamp) / 1000),
        ),
        scope: null,
      };
    }
    return { allowed: true, retryAfterSeconds: 0, scope: null };
  }

  /**
   * Append a hit and prune anything older than the longest window that key is judged
   * against. Without this the arrays grow without bound for a long-lived instance.
   */
  private record(key: string, timestamp: number): void {
    const existing = (this.hits.get(key) ?? []).filter(
      (entry) => entry > timestamp - horizonFor(key),
    );
    existing.push(timestamp);
    this.hits.set(key, existing);
  }

  /**
   * Judge and record a request against the LLM tier's own budget.
   *
   * Separate keyspace from `check`, so a visitor who has spent their LLM allowance can
   * still rank recognised descriptions all day — the cheap path is never gated by the
   * expensive one.
   */
  checkLlm(ip: string): LimitDecision {
    const timestamp = this.now();

    const perIp = this.evaluate(`llm:${ip}`, LLM_IP_RULE, timestamp);
    if (!perIp.allowed) return { ...perIp, scope: "ip" };

    const global = this.evaluate("llm:global", LLM_GLOBAL_RULE, timestamp);
    if (!global.allowed) return { ...global, scope: "global" };

    this.record(`llm:${ip}`, timestamp);
    this.record("llm:global", timestamp);
    return { allowed: true, retryAfterSeconds: 0, scope: null };
  }

  /**
   * Drop IP keys with nothing left in their widest window. Pruning the arrays is not
   * enough on its own — one key per distinct address would still accumulate, which is
   * exactly what a spray of forged `x-forwarded-for` values would produce.
   */
  private sweep(timestamp: number): void {
    if (this.hits.size < SWEEP_THRESHOLD) return;
    for (const [key, entries] of this.hits) {
      if (GLOBAL_KEYS.has(key)) continue;
      if (!entries.some((entry) => entry > timestamp - horizonFor(key))) {
        this.hits.delete(key);
      }
    }
  }

  /** Test seam: current hit count for a key within a window. */
  countFor(key: string, windowMs: number): number {
    const cutoff = this.now() - windowMs;
    return (this.hits.get(key) ?? []).filter((entry) => entry > cutoff).length;
  }
}

/** Process-wide limiter. One per serverless instance, by design. */
export const limiter = new RateLimiter();

/** Best-effort client address. */
export function clientIp(request: Request): string {
  const forwarded = request.headers.get("x-forwarded-for");
  if (forwarded) return forwarded.split(",")[0].trim();
  return request.headers.get("x-real-ip") ?? "unknown";
}
