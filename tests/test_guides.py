"""The guides: metadata correctness, sitemap contents, and the original-data rule.

Content quality is not testable, but the invariants around it are — and the one that
matters most is structural: `/board` paths must never reach the sitemap, because those
paths *are* the credential.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import config


WEB = Path(config.PROJECT_ROOT) / "web"
GUIDES_TS = WEB / "lib" / "guides.ts"
SITEMAP_TS = WEB / "app" / "sitemap.ts"
ROBOTS_TS = WEB / "app" / "robots.ts"
ARTICLES = WEB / "components" / "guides"


def _slugs() -> list[str]:
    return re.findall(r'^\s*slug: "([a-z0-9-]+)",', GUIDES_TS.read_text(encoding="utf-8"), re.M)


class MetadataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = GUIDES_TS.read_text(encoding="utf-8")
        cls.slugs = _slugs()

    def test_six_guides_are_defined(self) -> None:
        self.assertEqual(6, len(self.slugs))
        self.assertEqual(len(set(self.slugs)), len(self.slugs))

    def test_every_guide_has_a_distinct_seo_title_and_description(self) -> None:
        titles = re.findall(r'seoTitle:\s*\n?\s*"([^"]+)"', self.source)
        self.assertEqual(6, len(titles))
        self.assertEqual(len(set(titles)), len(titles))

    def test_seo_titles_are_within_search_result_length(self) -> None:
        """Google truncates around 60 characters; a cut title reads as sloppy."""
        for title in re.findall(r'seoTitle:\s*\n?\s*"([^"]+)"', self.source):
            self.assertLessEqual(len(title), 70, title)

    def test_every_guide_records_the_phrase_it_targets(self) -> None:
        targets = re.findall(r'target: "([^"]+)"', self.source)
        self.assertEqual(6, len(targets))
        self.assertEqual(len(set(targets)), len(targets))

    def test_related_links_all_resolve(self) -> None:
        """A dead cross-link is a 404 a crawler will find before a human does."""
        for related in re.findall(r'related: \[([^\]]*)\]', self.source):
            for slug in re.findall(r'"([a-z0-9-]+)"', related):
                self.assertIn(slug, self.slugs)

    def test_every_guide_has_a_rendered_article(self) -> None:
        index = (ARTICLES / "index.ts").read_text(encoding="utf-8")
        for slug in self.slugs:
            self.assertIn(f'"{slug}"', index)


class SitemapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = SITEMAP_TS.read_text(encoding="utf-8")

    def test_board_paths_are_never_listed(self) -> None:
        """The token in a board URL is the credential; listing it hands it out.

        Checks the emitted URLs rather than the file text — the file mentions
        `/board` in the comment explaining why it is absent, which is documentation
        rather than a route.
        """
        urls = re.findall(r"url: `?\$?\{?[^`\n]*`?,", self.source)
        for url in urls:
            self.assertNotIn("/board", url)

    def test_the_public_pages_are_listed(self) -> None:
        for path in ("/census", "/check", "/guides"):
            self.assertIn(path, self.source)

    def test_guides_are_expanded_from_the_registry(self) -> None:
        """Hand-listing them would let a new guide ship unindexed."""
        self.assertIn("GUIDES.map", self.source)

    def test_robots_still_disallows_boards_and_points_at_the_sitemap(self) -> None:
        robots = ROBOTS_TS.read_text(encoding="utf-8")
        self.assertIn('disallow: "/board"', robots)
        self.assertIn("sitemap", robots)


class OriginalDataTests(unittest.TestCase):
    """Every article must carry at least one sourced, dated figure."""

    def test_each_article_cites_a_source_for_its_statistics(self) -> None:
        for slug in _slugs():
            matches = list(ARTICLES.glob("*.tsx"))
            body = "\n".join(
                path.read_text(encoding="utf-8")
                for path in matches
                if path.name != "Prose.tsx"
            )
            self.assertIn("source=", body)

    def test_every_stat_component_requires_a_source_prop(self) -> None:
        """Structural enforcement: a bare number cannot be the easy path."""
        prose = (ARTICLES / "Prose.tsx").read_text(encoding="utf-8")
        block = prose[prose.index("export function Stat") : prose.index("export function Stat") + 400]
        self.assertIn("source: string", block)
        self.assertNotIn("source?:", block)

    def test_articles_carry_no_fabricated_attribution(self) -> None:
        """No invented case studies, no testimonials, no 'experts say'."""
        banned = ("experts say", "industry leaders", "one contractor told us", "studies show")
        for path in ARTICLES.glob("*.tsx"):
            body = path.read_text(encoding="utf-8").lower()
            for phrase in banned:
                self.assertNotIn(phrase, body, f"{path.name} contains {phrase!r}")


if __name__ == "__main__":
    unittest.main()
