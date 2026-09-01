"""What the Bid Confidence scroll story actually renders.

The captions shipped overlapping. The opacity array was computed correctly and never
passed to anything — legal TypeScript, invisible to a build, and invisible to every
test in this suite, because every test that touched this file read its *source text*
rather than mounting it. `tsc` was green and 820 tests were green, and all three
captions were stacked on top of each other in production.

So these tests mount the component and look at what comes out.

Two properties, and the first is the one that was broken:

* **Never two loud at once.** At any scroll position exactly one caption is visible.
  That is the invariant the opacity crossfade exists to maintain, and asserting it
  directly is the only thing that would have caught the omission — a source check for
  "is `opacity` referenced twice" would pass forever and mean nothing.
* **The reduced-motion branch renders three figures with three captions.** That branch
  has now been reasoned about twice and rendered correctly zero times. Nothing had ever
  executed it.

The scroll position is driven the way the browser drives it — a stubbed
`getBoundingClientRect` and a real `scroll` event — rather than by reaching into state.
That way the handler being attached and firing is under test too, not assumed.
"""

from __future__ import annotations

import unittest

from tests import ts_harness


#: Drives `measure()`: run = height - innerHeight = 3000 - 1000 = 2000, and
#: p = -top / run. So top = -p * 2000.
STORY_HEIGHT = 3000
VIEWPORT = 1000


def _script(reduced: bool, positions: list[float]) -> str:
    react = ts_harness._resolve_package("react")
    client = ts_harness._resolve_package("react-dom/client")
    return f"""
import * as React from {react!r};
import {{ createRoot }} from {client!r};
import {{ BidConfidence }} from './BidConfidence.mjs';

const REDUCED = {str(reduced).lower()};
const POSITIONS = {positions!r};

// The branch is chosen from this, exactly as in a browser.
window.matchMedia = (query) => ({{
  matches: REDUCED && query.includes('prefers-reduced-motion'),
  media: query,
  addEventListener() {{}}, removeEventListener() {{}},
  addListener() {{}}, removeListener() {{}},
}});
Object.defineProperty(window, 'innerHeight', {{ value: {VIEWPORT}, configurable: true }});

const container = document.createElement('div');
document.body.appendChild(container);

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));
const frame = () => new Promise((resolve) => requestAnimationFrame(() => resolve()));

globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const root = createRoot(container);
await React.act(async () => {{ root.render(React.createElement(BidConfidence)); }});
await flush();

// Captions are the only h2 inside the story; the page's other H2s are section
// headings outside it. Find them by their text so the query does not depend on
// class names, which are exactly what a refactor changes.
const HEADINGS = ['You bid one number.', 'It was always a range.', 'Your contingency covers'];
function captions() {{
  return Array.from(document.querySelectorAll('h2')).filter((node) =>
    HEADINGS.some((text) => (node.textContent || '').startsWith(text)));
}}

/** Rendered opacity of a caption: its own, or the nearest ancestor that sets one. */
function opacityOf(node) {{
  let current = node;
  while (current && current !== document.body) {{
    const raw = current.style && current.style.opacity;
    if (raw !== undefined && raw !== '') return Number(raw);
    current = current.parentElement;
  }}
  return 1;
}}

const story = document.querySelector('[data-story]');

async function at(p) {{
  if (story) {{
    const top = -p * ({STORY_HEIGHT} - {VIEWPORT});
    story.getBoundingClientRect = () => ({{
      top, bottom: top + {STORY_HEIGHT}, height: {STORY_HEIGHT},
      left: 0, right: 0, width: 800, x: 0, y: top, toJSON() {{ return {{}}; }},
    }});
    await React.act(async () => {{ window.dispatchEvent(new window.Event('scroll')); }});
    await frame();
    await flush();
  }}
  return captions().map((node) => Number(opacityOf(node).toFixed(3)));
}}

const result = {{
  captionCount: captions().length,
  svgCount: document.querySelectorAll('svg').length,
  storyFound: Boolean(story),
  initial: captions().map((n) => Number(opacityOf(n).toFixed(3))),
  byPosition: {{}},
}};
for (const p of POSITIONS) result.byPosition[String(p)] = await at(p);

root.unmount();
process.stdout.write(JSON.stringify(result));
"""


class AnimatedBranchTests(unittest.TestCase):
    """The branch most visitors get, driven through the real scroll handler."""

    @classmethod
    def setUpClass(cls) -> None:
        ts_harness.require(cls)
        cls.result = ts_harness.run(_script(False, [0.15, 0.5, 0.9]), dom=True)

    def test_the_scroll_story_mounts_with_three_captions(self) -> None:
        self.assertTrue(self.result["storyFound"], "no [data-story] element to drive")
        self.assertEqual(3, self.result["captionCount"])

    def test_exactly_one_caption_is_visible_at_every_position(self) -> None:
        """The invariant the crossfade exists for. This is what shipped broken."""
        for position, opacities in self.result["byPosition"].items():
            visible = [round(value, 3) for value in opacities if value > 0.5]
            self.assertEqual(
                1,
                len(visible),
                f"\nAt scroll position {position}, {len(visible)} captions are visible "
                f"(opacities {opacities}).\n"
                "Exactly one may be legible at a time — captions are pinned to the "
                "same coordinates, so two visible means two overlapping.",
            )

    def test_the_visible_caption_advances_with_the_scroll(self) -> None:
        """Not merely one-at-a-time: the right one, and in order."""
        order = []
        for position in ("0.15", "0.5", "0.9"):
            opacities = self.result["byPosition"][position]
            order.append(max(range(len(opacities)), key=lambda i: opacities[i]))
        self.assertEqual([0, 1, 2], order, f"captions surfaced out of order: {order}")


class ReducedMotionBranchTests(unittest.TestCase):
    """Reasoned about twice, rendered correctly zero times until now."""

    @classmethod
    def setUpClass(cls) -> None:
        ts_harness.require(cls)
        cls.result = ts_harness.run(_script(True, []), dom=True)

    def test_three_figures_and_three_captions_render(self) -> None:
        self.assertEqual(3, self.result["captionCount"])
        self.assertGreaterEqual(
            self.result["svgCount"],
            3,
            "the static branch must draw its own figure per state, not one shared frame",
        )

    def test_all_three_captions_are_readable_at_once(self) -> None:
        """Stacked and static: nothing is faded, because nothing crossfades."""
        self.assertEqual([1.0, 1.0, 1.0], self.result["initial"])

    def test_the_animated_runway_is_not_rendered(self) -> None:
        """No sticky scroll machinery for someone who asked not to have it."""
        self.assertFalse(self.result["storyFound"])


if __name__ == "__main__":
    unittest.main()
