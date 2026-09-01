"""Defects that only exist in the rendered page, not in any one source file.

Two components each contributed an `id="join"` to the same page — `Footer` and the
guide/product route — and neither file was wrong on its own. A source grep sees three
correct-looking declarations; only the assembled page shows the collision. This is the
second rendering problem to pass a green suite, so the instrument here is the output
rather than the source.

Parses the prerendered HTML under `.next/server/app/`, which is what actually ships.

WHAT THIS DOES NOT COVER. Only prerendered routes emit HTML. `/board/[token]` and
`/research` are server-rendered on demand and produce none, so they are outside these
assertions entirely — a duplicate id on either would not be caught here. The API
routes, `robots.txt` and `sitemap.xml` emit no HTML either, which is why the covered
count is 15 and not the 24 "static pages" the build reports; that number counts route
segments, most of which are not documents.

The tests skip when no build output is present, because the suite has to pass on a
machine that has not run `npm run build`. A skip here is a real gap rather than a
formality — nothing about these defects is visible without a build — so the count of
covered pages is asserted too, and a route quietly dropping out of prerendering fails
rather than silently shrinking what is checked.
"""

from __future__ import annotations

import html
import re
import unittest
from collections import Counter
from pathlib import Path

import config


BUILD_DIR = Path(config.PROJECT_ROOT) / "web" / ".next" / "server" / "app"

#: Prerendered documents at the time of writing: the homepage, /check, /guides,
#: /census, _not-found, three product pages plus bid-confidence, and six guides.
EXPECTED_PAGES = 15

_ID = re.compile(r'\sid="([^"]+)"')
_EMAIL_INPUT = re.compile(r'<input\b[^>]*\btype="email"', re.IGNORECASE)


def _pages() -> list[Path]:
    if not BUILD_DIR.is_dir():
        return []
    return sorted(BUILD_DIR.rglob("*.html"))


def _route(path: Path) -> str:
    relative = path.relative_to(BUILD_DIR).with_suffix("").as_posix()
    return "/" if relative == "index" else f"/{relative}"


class RenderedPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.pages = _pages()
        if not cls.pages:
            raise unittest.SkipTest(
                f"No build output at {BUILD_DIR}. These assertions only exist in "
                "rendered HTML, so without `npm run build` they check nothing — this "
                "skip is a gap, not a pass."
            )

    def test_the_expected_pages_are_prerendered(self) -> None:
        """A route dropping out of prerendering shrinks coverage silently otherwise."""
        self.assertEqual(
            EXPECTED_PAGES,
            len(self.pages),
            f"\n{len(self.pages)} prerendered pages, expected {EXPECTED_PAGES}:\n"
            + "\n".join(f"  {_route(p)}" for p in self.pages)
            + "\nIf a route became dynamic on purpose, lower EXPECTED_PAGES and say so. "
            "If it did not, these tests are now checking less than they claim to.",
        )

    def test_no_page_contains_a_duplicate_id(self) -> None:
        """The class, not the instance: any repeated id, whatever its value.

        Duplicate ids are invalid HTML, and fragment navigation and getElementById
        both silently take the first in document order — so the second element is
        unreachable and nothing reports it.
        """
        offenders: list[str] = []
        for page in self.pages:
            counts = Counter(_ID.findall(page.read_text(encoding="utf-8")))
            for value, count in sorted(counts.items()):
                if count > 1:
                    offenders.append(f"  {_route(page)}: id={value!r} appears {count}×")
        self.assertFalse(
            offenders,
            "\nDuplicate ids in rendered pages:\n" + "\n".join(offenders)
            + "\n\nA browser resolves a fragment to the FIRST match, so every later "
            "element with that id is unreachable. Two components each declaring one "
            "is the usual cause, and neither file looks wrong on its own.",
        )

    def test_no_page_renders_two_email_inputs(self) -> None:
        """Same class as the duplicate id, same cause, and visible to a reader.

        Nine pages rendered the beta form twice — once mid-article and again in the
        footer, with the same heading, sub-line and button within a screen of
        scrolling. `/check` and `/product/bid-confidence` did it differently: each had
        its own funnel and inherited the footer's form on top, which is two different
        asks on one page rather than the same one twice.

        No source file contained either. A shared component contributed one and the
        route contributed the other.
        """
        offenders: list[str] = []
        for page in self.pages:
            found = len(_EMAIL_INPUT.findall(page.read_text(encoding="utf-8")))
            if found > 1:
                offenders.append(f"  {_route(page)}: {found} email inputs")
        self.assertFalse(
            offenders,
            "\nPages asking for an email address more than once:\n"
            + "\n".join(offenders)
            + "\n\nA page makes one ask. If a route needs a form it declares one; "
            "Footer deliberately provides none, so a second input means something "
            "started inheriting again.",
        )

    def test_the_footer_contributes_no_form_to_any_page(self) -> None:
        """The property, not the symptom.

        `ownCapture` was added to stop the product route rendering a second form, and
        it only solved half the problem: it guarded the page-level one while the
        footer's arrived from somewhere the flag could not see. Asserting the footer
        stays empty is what keeps the other half fixed.
        """
        source = (
            Path(config.PROJECT_ROOT) / "web" / "components" / "Footer.tsx"
        ).read_text(encoding="utf-8")
        # Comments stripped first: the file explains why the form was removed, and the
        # assertion is about what it renders, not what it says about its own history.
        code = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
        code = re.sub(r"//.*", "", code)
        for marker in ("<BetaForm", 'from "@/components/BetaForm"'):
            self.assertNotIn(
                marker,
                code,
                "Footer renders a form again. Every page gets a Footer, so this puts "
                "one on pages that declare their own and on pages that deliberately "
                "have none.",
            )

    def test_every_page_that_declares_a_join_anchor_has_exactly_one(self) -> None:
        """The specific instance, kept alongside the general rule.

        The general test above would catch a regression here too, but this names the
        anchor so a failure says what broke rather than only that something did.
        """
        for page in self.pages:
            body = page.read_text(encoding="utf-8")
            count = body.count('id="join"')
            self.assertLessEqual(
                count,
                1,
                f"{_route(page)} declares id=\"join\" {count} times. Footer gave this "
                "id up so that each page declares its own; something has taken it back.",
            )

    def test_pages_with_join_links_provide_the_target(self) -> None:
        """A `#join` link with no matching id scrolls nowhere and reports nothing."""
        for page in self.pages:
            body = page.read_text(encoding="utf-8")
            unescaped = html.unescape(body)
            # Same-page fragments only. `/#join` is a cross-route link to the homepage
            # and is answered by the homepage's anchor, not this page's.
            if re.search(r'href="#join"', unescaped):
                self.assertIn(
                    'id="join"',
                    body,
                    f"{_route(page)} links to #join but declares no such target",
                )


if __name__ == "__main__":
    unittest.main()
