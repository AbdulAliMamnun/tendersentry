import { createHash } from "crypto";

/**
 * Board lookup.
 *
 * Board files are named for the SHA-256 of the token, not the token, so the
 * repository never contains a working credential. The raw token arrives in the URL,
 * is hashed here, and is used only to find the file — it is never logged, never put
 * in a page, and never sent anywhere.
 */

export type BoardBlocker = {
  requirement_text: string;
  quote: string;
  page: number;
  reason: string;
};

export type FirmBoardRow = {
  rank: number;
  title: string;
  buyer: string;
  closing_date: string;
  score: number;
  source: string;
  flags: string[];
  blocker?: BoardBlocker;
};

export type FirmBoard = {
  firm: { name: string; trades: string[]; regions: string[] };
  generated_at: string;
  candidate_count: number;
  board: FirmBoardRow[];
};

/** The filename a token's board is stored under. */
export function boardKey(token: string): string {
  return createHash("sha256").update(token, "utf8").digest("hex");
}

/**
 * Load a board by token, or null when the token matches nothing.
 *
 * A miss is indistinguishable from a malformed token by design: the caller renders
 * the same 404 either way, so probing cannot reveal whether any board exists.
 */
export async function loadBoard(token: string): Promise<FirmBoard | null> {
  if (!/^[A-Za-z0-9_-]{20,128}$/.test(token)) return null;
  try {
    const data = await import(`@/data/boards/${boardKey(token)}.json`);
    return (data.default ?? data) as FirmBoard;
  } catch {
    return null;
  }
}

const SOURCE_LABELS: Record<string, string> = {
  canadabuys: "CanadaBuys",
  seao: "SEAO",
  municipal_site: "Municipal site",
  bidsandtenders: "bids&tenders",
};

export function sourceLabel(source: string): string {
  return SOURCE_LABELS[source] ?? source;
}

const FLAG_LABELS: Record<string, string> = {
  value_unknown: "no published value",
  trade_unmapped: "work type unclear",
  trade_family_only: "adjacent trade",
  long_horizon: "standing offer",
  region_unknown: "region unstated",
  value_baseline_unknown: "no size baseline",
};

export function flagLabel(flag: string): string {
  return FLAG_LABELS[flag] ?? flag.replace(/_/g, " ");
}

export function formatGenerated(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "recently";
  return date.toLocaleDateString("en-CA", {
    month: "long",
    day: "numeric",
    year: "numeric",
  });
}
