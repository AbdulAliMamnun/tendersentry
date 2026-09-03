"""The palette's reservations, enforced.

`web/tailwind.config.ts` states three of them in prose. Prose is what let this site
ship a dark `/product/bid-confidence` for weeks under a comment saying there were no
dark sections anywhere, so each reservation that can be checked is checked here.

The one this file exists for is `grey-light`. Measured from glyph cores rendered in
Chrome, `#8B9296` is **3.16:1 on white** and **2.91:1 on mist** — under the 4.5:1
body-text threshold on both, and under even the 3:1 non-text threshold on mist. It is
a caption colour. It became the site's blanket replacement for the old `muted` token
in one sweep, which put it under a 22px hero sub-line, and that is exactly the drift
this test is here to stop happening again quietly.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"
SOURCES = sorted(
    p
    for p in list(WEB.glob("app/**/*.tsx")) + list(WEB.glob("components/**/*.tsx"))
    if "node_modules" not in p.parts
)

# Sizes at or under 13px, the ceiling the palette gives grey-light.
SMALL = re.compile(r"text-(?:xs|\[(?:9|10|11|12|13)px\])(?![\w-])")

# A className attribute, either "..." or {`...`}.
CLASSNAMES = re.compile(r'className=(?:"([^"]*)"|\{`([^`]*)`\})', re.S)

INTERPOLATION = re.compile(r"\$\{.*?\}", re.S)
QUOTED = re.compile(r'"([^"]*)"|\'([^\']*)\'')


def variants(value: str):
    """Every class set an element can actually render with.

    A template literal mixes static classes with a ternary: ``text-xs ${x ? "italic
    text-grey-light" : "text-grey"}``. Each branch has to be checked separately — one
    of them may be the offender — but the static part applies to *both*, so it is
    prepended to each. Checking a branch alone was the first version of this and it
    reported every such element as sizeless, which is a false positive that would have
    taught the next reader to distrust the test.
    """
    static = INTERPOLATION.sub(" ", value)
    branches = [g for pair in QUOTED.findall(value) for g in pair if g]
    return [static] + [f"{static} {branch}" for branch in branches] if branches else [static]


def classnames(source: str):
    """Every className value in a file, with its 1-indexed line number."""
    for match in CLASSNAMES.finditer(source):
        value = match.group(1) or match.group(2) or ""
        line = source.count("\n", 0, match.start()) + 1
        yield line, value


def strip_comments(source: str) -> str:
    """Comments describe the rule; they must not be mistaken for breaking it."""
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"^\s*//.*$", "", source, flags=re.M)


class GreyLightIsACaptionColour(unittest.TestCase):
    def test_grey_light_text_always_declares_a_size_of_13px_or_under(self) -> None:
        """Explicit, not inherited.

        A bare `text-grey-light` on an element that inherits 15px from its parent is
        the same defect as writing `text-base text-grey-light`, and is not visible to
        a reader of the line. Requiring the size to be stated on the same element is
        what makes the rule checkable at all.
        """
        offenders = []
        for path in SOURCES:
            source = strip_comments(path.read_text(encoding="utf-8"))
            for line, value in classnames(source):
                for branch in variants(value):
                    if "text-grey-light" not in branch:
                        continue
                    if not SMALL.search(branch):
                        offenders.append(
                            f"{path.relative_to(WEB)}:{line} — "
                            f"{' '.join(branch.split())[:90]}"
                        )
        self.assertEqual(
            offenders,
            [],
            "grey-light (#8B9296) measures 3.16:1 on white, under the 4.5:1 body-text "
            "threshold. It is for captions and tertiary text at 13px or under, and the "
            "size must be stated on the same element rather than inherited. Use "
            "text-grey (5.76:1) for anything body-sized:\n  "
            + "\n  ".join(offenders),
        )

    def test_grey_light_never_sits_on_mist(self) -> None:
        """2.91:1 — below the non-text threshold, let alone the text one."""
        offenders = []
        for path in SOURCES:
            source = strip_comments(path.read_text(encoding="utf-8"))
            for line, value in classnames(source):
                for branch in variants(value):
                    if "text-grey-light" in branch and "bg-mist" in branch:
                        offenders.append(f"{path.relative_to(WEB)}:{line}")
        self.assertEqual(
            offenders,
            [],
            "grey-light on mist measures 2.91:1. Use text-grey on mist: " + str(offenders),
        )

    def test_placeholders_are_not_grey_light(self) -> None:
        """Placeholder text is sized by its input, and every input here is 14px+."""
        offenders = []
        for path in list(SOURCES) + [WEB / "app" / "globals.css"]:
            source = strip_comments(path.read_text(encoding="utf-8"))
            if "placeholder:text-grey-light" in source:
                offenders.append(str(path.relative_to(WEB)))
        self.assertEqual(
            offenders,
            [],
            "Inputs on this site are 14–15px, so their placeholders are body-sized. "
            "Use placeholder:text-grey: " + str(offenders),
        )


class FlagIsReserved(unittest.TestCase):
    """`flag` marks disqualification and failure states, and nothing else."""

    # Every file permitted to spend it, with the reason it qualifies.
    PERMITTED = {
        "components/BoardCard.tsx": "the blocked row on the example board",
        "components/FirmBoard.tsx": "the blocked row on a real firm board",
        "components/guides/Prose.tsx": "the disqualifying-quote variant",
        "components/BetaForm.tsx": "a failed submission",
        "components/CheckForm.tsx": "a failed submission",
        "components/DemoRanker.tsx": "a failed or rate-limited request",
        "components/product/BidConfidence.tsx": "the tail past the contingency marker",
    }

    def test_no_other_file_uses_flag(self) -> None:
        used = {
            str(p.relative_to(WEB))
            for p in SOURCES
            if re.search(r"-flag(?![\w-])", strip_comments(p.read_text(encoding="utf-8")))
        }
        unexpected = sorted(used - set(self.PERMITTED))
        self.assertEqual(
            unexpected,
            [],
            "flag (#8E4034) is reserved for disqualification and failure states. A "
            "gated platform, an eyebrow, a link and a hover are none of those. If a "
            "new use really is one, add it to PERMITTED with its reason:\n  "
            + "\n  ".join(unexpected),
        )

    def test_the_charts_spend_no_flag(self) -> None:
        """A gated procurement platform is not a disqualified bid."""
        for name in ("components/CensusBand.tsx", "components/DistributionBar.tsx"):
            source = strip_comments((WEB / name).read_text(encoding="utf-8"))
            self.assertNotIn("8E4034", source.upper(), f"{name} spends the reserved colour")


class NoOldPalette(unittest.TestCase):
    """The warm palette was deleted, not aliased. Nothing should reference it."""

    RETIRED = ["faf9f7", "f0ede6", "292524", "57534e", "a8a29e", "a32d2d",
               "fcebeb", "477054", "eaf5ed", "d6d3d1", "c9bfae", "8a7f70",
               "a89c8a", "efece6", "f5f3ef", "e7e5e4", "f5f1e8", "6b5f4b"]

    def test_no_retired_hex_survives(self) -> None:
        offenders = []
        for path in list(SOURCES) + [WEB / "app" / "globals.css", WEB / "tailwind.config.ts"]:
            source = strip_comments(path.read_text(encoding="utf-8")).lower()
            for retired in self.RETIRED:
                if retired in source:
                    offenders.append(f"{path.relative_to(WEB)} — #{retired}")
        self.assertEqual(offenders, [], "warm palette survives:\n  " + "\n  ".join(offenders))

    def test_no_retired_token_name_survives(self) -> None:
        offenders = []
        for path in list(SOURCES) + [WEB / "app" / "globals.css"]:
            source = strip_comments(path.read_text(encoding="utf-8"))
            for token in ("brand-red", "fit-green", "hairline", "-heading", "-muted",
                          "bg-page", "bg-card", "text-body", "btn-red"):
                if token in source:
                    offenders.append(f"{path.relative_to(WEB)} — {token}")
        self.assertEqual(offenders, [], "retired token names:\n  " + "\n  ".join(offenders))


if __name__ == "__main__":
    unittest.main()
