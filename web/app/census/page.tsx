import { permanentRedirect } from "next/navigation";

/**
 * The census is now the opening section of /research rather than a page of its own.
 *
 * A permanent redirect rather than a deletion: /census is linked from the repository,
 * from earlier commits, and from anywhere we have already pointed people. Breaking
 * those to tidy a route would be the wrong trade.
 */
export default function CensusRedirect() {
  permanentRedirect("/research");
}
