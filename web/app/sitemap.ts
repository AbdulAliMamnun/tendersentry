import type { MetadataRoute } from "next";

import { GUIDES, SITE_URL } from "@/lib/guides";

/**
 * The real sitemap.
 *
 * `/board` is deliberately absent and always will be: those paths *are* the
 * credential, so listing them would hand out what the tokens protect. `/check` is
 * included — it is a public entry point — but nothing under a token ever is.
 */
export default function sitemap(): MetadataRoute.Sitemap {
  const guides = GUIDES.map((guide) => ({
    url: `${SITE_URL}/guides/${guide.slug}`,
    lastModified: new Date(guide.updated),
    changeFrequency: "monthly" as const,
    priority: 0.7,
  }));

  return [
    { url: SITE_URL, lastModified: new Date(), changeFrequency: "daily", priority: 1 },
    {
      url: `${SITE_URL}/census`,
      lastModified: new Date("2026-07-31"),
      changeFrequency: "monthly",
      priority: 0.9,
    },
    { url: `${SITE_URL}/check`, changeFrequency: "monthly", priority: 0.8 },
    {
      url: `${SITE_URL}/guides`,
      lastModified: new Date(GUIDES[0].updated),
      changeFrequency: "weekly",
      priority: 0.8,
    },
    ...guides,
  ];
}
