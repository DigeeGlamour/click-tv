"""The Web Series tab shows every category's shows, not just the last one.

`seriesModule.mergeCategoryItems` REPLACES the series in `currentItems`
rather than adding to them - it rebuilds the list as

    [manual movies, these series, other movies]

filtering out every series already present. That is correct for its other
caller, which browses one category at a time: the previous category's shows
should not linger.

`loadMovieWebSeries` called it once per category inside the loop, so each
category discarded the one before it and the tab rendered only the last.

Measured on 2026-10-17 against the published catalogue, with the page driven
in Chromium:

    data/series/manifest.json   total_series 67
      Bangla 1, Hindi 1, English 1, Dubbed 23, South Indian 13, Premium 28
    Web Series tab rendered     28   <- exactly Premium, the last in the loop

39 of 67 shows - 58% of the series catalogue - could not be reached by a
viewer at all. After collecting first and merging once: 67 rendered, 67
distinct, rail reads "67 Series".

Every item carries `_seriesCategorySlug`, which is what the merge builds
`_uid` from, so shows from different categories keep distinct ids when they
arrive in one call.
"""
from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

APP_JS = (ROOT / "site" / "assets" / "js" / "app.js").read_text(encoding="utf-8")
SERIES_JS = (ROOT / "site" / "assets" / "js" / "series.js").read_text(encoding="utf-8")


def _loader() -> str:
    body = APP_JS[APP_JS.index("async function loadMovieWebSeries() {"):]
    return body[:body.index("\n}\n")]


class TheTabCollectsBeforeItMerges(unittest.TestCase):
    def test_the_merge_is_not_inside_the_loop(self):
        body = _loader()
        loop = body.index("for (const [, slug] of MOVIE_ORDER)")
        merge = body.index("seriesModule.mergeCategoryItems(")
        close = body.index("\n  }", loop)
        self.assertGreater(merge, close, "the merge must run after the loop")

    def test_the_loop_accumulates(self):
        self.assertIn("everySeries.push(...seriesItems)", _loader())

    def test_the_merge_is_called_once_with_everything(self):
        body = _loader()
        self.assertEqual(body.count("mergeCategoryItems("), 1)
        self.assertIn("mergeCategoryItems(everySeries)", body)

    def test_one_missing_category_still_does_not_empty_the_tab(self):
        """The rule the original loop was written for, kept."""
        body = _loader()
        self.assertIn("failures += 1", body)
        self.assertIn("catch (error)", body)

    def test_nothing_is_merged_when_there_is_nothing(self):
        self.assertIn("if (everySeries.length) seriesModule.mergeCategoryItems(", _loader())


class TheMergeStillReplacesForItsOtherCaller(unittest.TestCase):
    """The single-category browse must keep its old behaviour: the previous
    category's shows go away."""

    def test_it_still_drops_the_series_already_in_the_list(self):
        body = SERIES_JS[SERIES_JS.index("function mergeCategoryItems("):]
        body = body[:body.index("\n  }")]
        self.assertIn("filter((item) => !isSeriesItem(item))", body)

    def test_the_uid_is_built_from_each_items_own_category(self):
        """What makes merging several categories at once safe."""
        self.assertIn(
            "item._uid = `series:${safeText(item._seriesCategorySlug, activeCategorySlug)}:${item.id}`",
            SERIES_JS,
        )

    def test_each_item_carries_that_slug(self):
        self.assertIn("_seriesCategorySlug: slug,", SERIES_JS)


class EveryVisibleCategoryIsInTheOrderTheTabWalks(unittest.TestCase):
    """A show in a category the loop never visits is unreachable, which is
    the same fault by another route."""

    MANIFEST = ROOT / "data" / "series" / "manifest.json"

    def setUp(self):
        if not self.MANIFEST.exists():
            self.skipTest("no published series manifest in this checkout")
        self.manifest = json.loads(self.MANIFEST.read_text(encoding="utf-8"))

    def test_the_counts_add_up_to_the_total(self):
        total = sum(c.get("count", 0) for c in self.manifest["categories"].values())
        self.assertEqual(total, self.manifest["total_series"])

    def test_every_category_with_shows_is_a_slug_the_loop_visits(self):
        order = set(re.findall(r"Object\.freeze\(\['([a-z-]+)',", APP_JS))
        for name, entry in self.manifest["categories"].items():
            if entry.get("count", 0) <= 0 or entry.get("visible") is False:
                continue
            self.assertIn(entry["slug"], order, f"{name} is unreachable")


if __name__ == "__main__":
    unittest.main()
