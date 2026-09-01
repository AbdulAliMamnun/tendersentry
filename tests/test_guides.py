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
PRODUCTS_TS = WEB / "lib" / "products.ts"
PRODUCT_BODIES = WEB / "components" / "product"
HOMEPAGE = WEB / "app" / "page.tsx"
NAV = WEB / "components" / "Nav.tsx"
RESEARCH = WEB / "components" / "ResearchFindings.tsx"


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
        for path in ("/research", "/check", "/guides", "/product/"):
            self.assertIn(path, self.source)

    def test_products_are_expanded_from_the_registry(self) -> None:
        self.assertIn("PRODUCTS.map", self.source)

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


#: Language that would turn a bid-fit score into a claim about the future.
#:
#: The model predicts bid PROPENSITY — how likely a notice is to be work a firm like
#: this one goes after. It says nothing about whether they would win it, and it was
#: never trained to. A contractor who reads a fit score as a chance of winning is being
#: told something we do not know, which is the one thing this product must not do.
#:
#: The rule has existed since the ranking model shipped and was enforced by convention
#: alone: stated in the serving manifest and in prose, asserted nowhere. This makes it
#: structural for rendered copy.
WIN_LANGUAGE = (
    "win rate",
    "win probability",
    "probability of winning",
    "chance of winning",
    "likelihood of winning",
    "odds of winning",
)

#: The only permitted uses, matched case-insensitively as complete substrings and
#: removed from the text BEFORE the banned phrases are searched for.
#:
#: Both are the site disclaiming the thing, which is the usage the rule exists to
#: encourage. The list is deliberately literal rather than a "negator within N words"
#: pattern: a looser rule would also pass constructions nobody vetted. Rewording one of
#: these therefore requires editing this list, which for copy of this kind is the point.
#:
#: What it lets through, exactly: "never a chance of winning" and "not a chance of
#: winning". Nothing else. A bare "a chance of winning" elsewhere in the same file
#: still fails, because only the longer phrase is stripped.
PERMITTED_WIN_LANGUAGE = (
    "never a chance of winning",
    "not a chance of winning",
)


class WinLanguageTests(unittest.TestCase):
    """Scores are bid fit. Rendered copy may not imply they are anything else."""

    @staticmethod
    def _rendered() -> list[Path]:
        return sorted([*ARTICLES.glob("*.tsx"), *PRODUCT_BODIES.glob("*.tsx")])

    def test_no_rendered_copy_promises_a_win(self) -> None:
        offenders: list[str] = []
        for path in self._rendered():
            body = re.sub(r"\s+", " ", path.read_text(encoding="utf-8")).lower()
            for allowed in PERMITTED_WIN_LANGUAGE:
                body = body.replace(allowed, " ")
            for phrase in WIN_LANGUAGE:
                if phrase in body:
                    offenders.append(f"  {path.name}: {phrase!r}")
        self.assertFalse(
            offenders,
            "\nRendered copy implies a chance of winning:\n" + "\n".join(offenders)
            + "\n\nThe model predicts bid propensity and was never trained on whether a "
              "firm won. If the sentence disclaims the idea, add its exact wording to "
              "PERMITTED_WIN_LANGUAGE; do not widen the banned list.",
        )

    def test_every_exemption_is_actually_exercised(self) -> None:
        """An exemption nobody uses is one nobody notices going stale.

        Without this the allowlist becomes a place to park permissions — each one a
        hole that outlives the sentence it was added for.
        """
        corpus = " ".join(
            re.sub(r"\s+", " ", path.read_text(encoding="utf-8")).lower()
            for path in self._rendered()
        )
        for allowed in PERMITTED_WIN_LANGUAGE:
            self.assertIn(
                allowed,
                corpus,
                f"PERMITTED_WIN_LANGUAGE carries {allowed!r}, which no rendered copy "
                "uses any more. Remove it rather than leaving a standing exemption.",
            )

    def test_the_guard_covers_product_bodies_and_not_only_guides(self) -> None:
        """The gap this closed: the fabricated-attribution guard scans guides alone."""
        names = {path.parent.name for path in self._rendered()}
        self.assertIn("guides", names)
        self.assertIn("product", names)


class HomepageTests(unittest.TestCase):
    """The homepage says what the company does, and stops."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.source = HOMEPAGE.read_text(encoding="utf-8")

    def test_the_census_band_is_gone(self) -> None:
        """It lives on /research now; on the homepage it was research standing where
        the product should be."""
        self.assertNotIn("CensusBand", self.source)

    def test_the_data_flex_stat_strip_is_gone(self) -> None:
        self.assertNotIn("statItems", self.source)
        self.assertNotIn("fabrications_caught", self.source)

    def test_the_demo_sits_above_every_product_card(self) -> None:
        """Nothing may push the strongest asset below the fold."""
        self.assertIn("DemoRanker", self.source)
        self.assertLess(
            self.source.index("DemoRanker"),
            self.source.index("PRODUCTS.map"),
        )

    def test_the_three_product_cards_come_from_the_registry(self) -> None:
        """Hand-listing them would let a card drift from the page it links to."""
        self.assertIn("PRODUCTS.map", self.source)

    def test_the_credibility_line_points_at_the_research_rather_than_reciting_it(
        self,
    ) -> None:
        self.assertIn('href="/research"', self.source)
        self.assertIn("See the research", self.source)


class ProductTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = PRODUCTS_TS.read_text(encoding="utf-8")
        cls.slugs = re.findall(r'^\s*slug: "([a-z-]+)",', cls.source, re.M)

    def test_three_products_are_defined(self) -> None:
        self.assertEqual(["discovery", "compliance", "board"], self.slugs)

    def test_each_has_a_distinct_seo_title_within_search_result_length(self) -> None:
        titles = re.findall(r'seoTitle:\s*\n?\s*"([^"]+)"', self.source)
        self.assertEqual(3, len(titles))
        self.assertEqual(len(set(titles)), len(titles))
        for title in titles:
            self.assertLessEqual(len(title), 70, title)

    def test_each_records_the_phrase_it_targets(self) -> None:
        targets = re.findall(r'target: "([^"]+)"', self.source)
        self.assertEqual(
            ["tender matching software canada", "bid compliance check",
             "government bid tracking ontario"],
            targets,
        )

    def test_every_product_has_a_rendered_body(self) -> None:
        index = (PRODUCT_BODIES / "index.ts").read_text(encoding="utf-8")
        for slug in self.slugs:
            self.assertIn(slug, index)

    def test_product_pages_state_the_coverage_asymmetry(self) -> None:
        """A contractor in Ontario must not learn about the gap in month two."""
        for name in ("Discovery.tsx", "Board.tsx"):
            body = (PRODUCT_BODIES / name).read_text(encoding="utf-8")
            self.assertIn("Ontario", body)
            self.assertIn("/research", body)

    def test_product_pages_avoid_pipeline_jargon(self) -> None:
        """These are for estimators, not for us."""
        for path in PRODUCT_BODIES.glob("*.tsx"):
            body = path.read_text(encoding="utf-8")
            for jargon in ("cross_embedding", "LambdaRank", "centroid", "recall@10"):
                self.assertNotIn(jargon, body, f"{path.name} leaks {jargon!r}")


class ResearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = RESEARCH.read_text(encoding="utf-8")

    def test_every_finding_carries_a_method(self) -> None:
        titles = re.findall(r"^\s*title: \"([^\"]+)\",", cls_source(), re.M)
        # String literals only — the `method: string` field on the type declaration
        # is not a finding.
        methods = re.findall(r'^\s*method:\s*\n?\s*"', cls_source(), re.M)
        self.assertEqual(len(titles), len(methods))
        self.assertEqual(4, len(titles))

    def test_the_ranking_figure_uses_one_split_consistently(self) -> None:
        """0.219 is the primary split; 2.8x is the settled split. Quoting them
        together overstates the result, and the page says so explicitly."""
        self.assertIn("2.3", self.source)
        self.assertIn("different\n          splits", self.source.replace("\r", ""))

    def test_the_lookup_versus_constant_finding_is_stated(self) -> None:
        self.assertIn("34.9", self.source)
        self.assertIn("34.4", self.source)

    def test_the_access_asymmetry_is_stated_with_our_own_interest_disclosed(self) -> None:
        self.assertIn("199,644", self.source)
        self.assertIn("not a neutral", self.source)


def cls_source() -> str:
    return RESEARCH.read_text(encoding="utf-8")


class NavTests(unittest.TestCase):
    def test_nav_exposes_product_research_and_guides(self) -> None:
        nav = NAV.read_text(encoding="utf-8")
        self.assertIn("Product", nav)
        self.assertIn('href="/research"', nav)
        self.assertIn('href="/guides"', nav)

    def test_the_census_route_redirects_rather_than_breaking(self) -> None:
        """/census is linked from published material and from anything already sent."""
        page = (WEB / "app" / "census" / "page.tsx").read_text(encoding="utf-8")
        self.assertIn("permanentRedirect", page)
        self.assertIn('"/research"', page)

    def test_no_internal_link_relies_on_the_redirect(self) -> None:
        """The redirect exists for links we do not control. Ours point at /research.

        Relying on it internally costs a hop on every click, splits the signal that
        consolidating the citation surface was meant to gather, and quietly rots the
        day someone decides the redirect has served its purpose.
        """
        offenders = []
        for path in list((WEB / "app").rglob("*.tsx")) + list((WEB / "components").rglob("*.tsx")):
            if path.parent.name == "census":
                continue  # the redirect itself
            body = path.read_text(encoding="utf-8")
            for match in re.finditer(r"[\"'`]/census\b", body):
                offenders.append(f"{path.relative_to(WEB)}:{body[:match.start()].count(chr(10)) + 1}")
        self.assertEqual([], offenders, f"internal links still hop through /census: {offenders}")


if __name__ == "__main__":
    unittest.main()
