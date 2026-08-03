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

/** Only sweep once the key count is large enough to be worth the pass. */
const SWEEP_THRESHOLD = 500;

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
    const horizon =
      key === "global"
        ? GLOBAL_RULE.windowMs
        : Math.max(...PER_IP_RULES.map((rule) => rule.windowMs));
    const existing = (this.hits.get(key) ?? []).filter(
      (entry) => entry > timestamp - horizon,
    );
    existing.push(timestamp);
    this.hits.set(key, existing);
  }

  /**
   * Drop IP keys with nothing left in their widest window. Pruning the arrays is not
   * enough on its own — one key per distinct address would still accumulate, which is
   * exactly what a spray of forged `x-forwarded-for` values would produce.
   */
  private sweep(timestamp: number): void {
    if (this.hits.size < SWEEP_THRESHOLD) return;
    const horizon = Math.max(...PER_IP_RULES.map((rule) => rule.windowMs));
    for (const [key, entries] of this.hits) {
      if (key === "global") continue;
      if (!entries.some((entry) => entry > timestamp - horizon)) {
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
