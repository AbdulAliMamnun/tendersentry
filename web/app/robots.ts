import type { MetadataRoute } from "next";

import { SITE_URL } from "@/lib/guides";

/**
 * Boards are private-by-link, not a search surface. They carry `noindex` too — this
 * is the belt to that suspenders, and keeps well-behaved crawlers from requesting
 * URLs whose paths are themselves the credential.
 */
export default function robots(): MetadataRoute.Robots {
  return {
    rules: [{ userAgent: "*", allow: ["/", "/guides"], disallow: "/board" }],
    sitemap: `${SITE_URL}/sitemap.xml`,
  };
}
