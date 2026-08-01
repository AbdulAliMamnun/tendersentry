"use client";

import { Analytics } from "@vercel/analytics/react";
import { usePathname } from "next/navigation";

/**
 * Analytics everywhere except boards.
 *
 * A board's path *is* its access token, and analytics records paths — so leaving it
 * enabled would file every customer's credential in a dashboard.
 */
export function SiteAnalytics() {
  const pathname = usePathname();
  if (pathname?.startsWith("/board")) return null;
  return <Analytics />;
}
