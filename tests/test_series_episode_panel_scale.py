"""ধাপ ১৩ / A-07 - the episode panel at the sizes ধাপ ৩খ actually produced.

    Bachelor Point-এ ৩৭ এপিসোড আসবে। প্যানেল এখন ৮টিতে পরীক্ষিত।
    D-01-এর পর ৩৭ ও ২৩ এপিসোডের শো নিয়ে প্যানেল ও সিজন স্ট্রিপ আবার মাপতে হবে।

**The measured sizes, now that ধাপ ৩খ has run.** Read off the published
catalogue rather than taken from the plan's estimate:

    31  Sultan Salahuddin Ayyubi  S1     <- the largest season on the site
    25  Resort                    S1
    19  Resort                    S1 (second block)
    16  Batchmates                S1

So the plan's "37 and 23" is "31 and 25" in reality - close enough that the
question it was asking is the right one, and different enough to be worth
writing down.

**What this file can and cannot do.** There is no browser here, so this does
not measure pixels. It measures the *structural* properties that decide
whether a panel of 31 rows works at all - a cap in the renderer, a clipped
container, a season strip that cannot scroll - because those are the failures
that turn "tested at 8" into "broken at 31", and each of them is visible in
the source.
"""
from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SERIES_JS = (ROOT / "site" / "assets" / "js" / "series.js").read_text(
    encoding="utf-8")
SERIES_CSS = (ROOT / "site" / "assets" / "css" / "series.css").read_text(
    encoding="utf-8")

#: The two the plan named, and the two the catalogue actually has.
PLANNED_SIZES = (37, 23)
MEASURED_SIZES = (31, 25)


def _rule(css: str, selector: str) -> str:
    """The body of the first rule for `selector`, or "" when there is none."""
    pattern = re.compile(
        r"(?:^|[},])\s*" + re.escape(selector) + r"\s*(?:,[^{]*)?\{([^}]*)\}",
        re.MULTILINE,
    )
    match = pattern.search(css)
    return match.group(1) if match else ""


class TheCatalogueIsTheSizeTheMeasurementAssumes(unittest.TestCase):
    """A panel test against sizes the site does not have is a test of nothing,
    so the sizes are read from the catalogue and asserted to be real."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.season_sizes = []
        for index_file in (ROOT / "data" / "series").glob("*/*/index.json"):
            payload = json.loads(index_file.read_text(encoding="utf-8"))
            for season in payload.get("seasons") or []:
                path = season.get("path")
                if not path:
                    continue
                season_file = ROOT / path
                if not season_file.exists():
                    continue
                items = json.loads(season_file.read_text(encoding="utf-8"))
                cls.season_sizes.append(len(items.get("items") or []))

    def test_the_catalogue_has_a_season_far_past_the_eight_it_was_tested_at(self):
        """The premise of A-07. If this fails, the panel no longer needs
        re-measuring and this file should say so instead of pretending."""
        self.assertTrue(self.season_sizes, "no published seasons were found")
        self.assertGreaterEqual(max(self.season_sizes), 20)

    def test_the_largest_season_is_within_reach_of_the_plans_estimate(self):
        """The plan predicted 37 for Bachelor Point. The catalogue produced 31
        for Sultan Salahuddin Ayyubi. Same order of magnitude - which is what
        makes the plan's question still the right one."""
        self.assertGreaterEqual(max(self.season_sizes), max(MEASURED_SIZES))
        self.assertLessEqual(max(self.season_sizes), max(PLANNED_SIZES) + 20)

    def test_there_is_a_second_large_season_not_just_one_outlier(self):
        big = sorted(self.season_sizes, reverse=True)
        self.assertGreaterEqual(big[1], 20)


class TheRendererDrawsEveryEpisode(unittest.TestCase):
    def test_the_episode_loop_has_no_cap(self):
        """The failure this guards: a `.slice(0, 20)` that was invisible while
        the largest show had 8 episodes."""
        loop = SERIES_JS[SERIES_JS.index("activeEpisodes.forEach((episode)"):]
        loop = loop[:loop.index("region.appendChild") if "region.appendChild" in loop
                    else 4000]
        self.assertNotIn(".slice(", loop)

    def test_the_active_episode_list_is_not_truncated_when_it_is_built(self):
        built = SERIES_JS[:SERIES_JS.index("activeEpisodes.forEach((episode)")]
        # `.slice()` with no arguments is a copy, which is fine; `.slice(0, N)`
        # is a cap, which is not.
        caps = re.findall(r"activeEpisodes\s*=\s*[^;]*\.slice\(\s*\d", built)
        self.assertEqual(caps, [])

    def test_every_episode_gets_its_own_focusable_row(self):
        """TV remotes walk the DOM. One row per episode is what makes the
        31st reachable at all."""
        self.assertIn("tv-focusable", SERIES_JS)
        self.assertIn("row.dataset.episodeUid", SERIES_JS)


class TheListCanGrowWithoutBeingClipped(unittest.TestCase):
    def test_the_episode_list_has_no_fixed_height(self):
        """A height that fits 8 rows hides rows 9 to 31 with no scrollbar and
        no error - the exact failure mode A-07 is asking about."""
        body = _rule(SERIES_CSS, ".series-episode-list")
        self.assertTrue(body, ".series-episode-list has no rule at all")
        for property_name in ("height:", "max-height:"):
            self.assertNotIn(property_name, body)

    def test_the_episode_list_does_not_hide_its_overflow(self):
        body = _rule(SERIES_CSS, ".series-episode-list")
        self.assertNotIn("overflow: hidden", body)

    def test_the_playback_context_list_is_also_unclipped(self):
        """The two-column variant used beside the player - 31 episodes is 16
        rows there, which is still more than 8."""
        body = _rule(
            SERIES_CSS,
            "html.movie-playback-context body:not(.movie-portal) .series-episode-list",
        )
        self.assertTrue(body)
        for property_name in ("height:", "max-height:", "overflow: hidden"):
            self.assertNotIn(property_name, body)

    def test_a_row_does_not_depend_on_a_fixed_list_height(self):
        """`min-height` on the row is fine and grows the list; a fixed
        `height` on the row would make a long list misalign instead."""
        body = _rule(SERIES_CSS, ".series-episode-card")
        self.assertIn("min-height:", body)
        self.assertFalse(re.search(r"(?<!min-)(?<!max-)\bheight:", body))


class TheSeasonStripSurvivesManySeasons(unittest.TestCase):
    def test_it_scrolls_sideways_rather_than_wrapping(self):
        body = _rule(SERIES_CSS, ".series-season-strip")
        self.assertIn("overflow-x: auto", body)

    def test_it_has_no_fixed_height_either(self):
        body = _rule(SERIES_CSS, ".series-season-strip")
        self.assertNotIn("max-height:", body)


class TheEpisodeNumberFitsWhatThisCatalogueProduces(unittest.TestCase):
    """31 episodes means two-digit badges everywhere, and ধাপ ৩খ also produces
    RANGE badges ("01-05") for pack cards. Both share one 58px column."""

    def test_the_number_column_is_wide_enough_to_be_measured(self):
        body = _rule(SERIES_CSS, ".series-episode-card")
        match = re.search(r"grid-template-columns:\s*(\d+)px", body)
        self.assertTrue(match, "the number column is no longer a fixed width")
        self.assertGreaterEqual(int(match.group(1)), 48)

    def test_a_range_badge_has_a_rule_of_its_own(self):
        """Without it "01-05" sets the column width for every other row."""
        self.assertIn(".series-episode-number.range", SERIES_CSS)

    def test_the_renderer_marks_a_range_badge(self):
        self.assertIn("badge.includes('-')", SERIES_JS)


class TheEpisodeNameFromPhaseTwelveHasSomewhereToGo(unittest.TestCase):
    """ধাপ ১২ adds `episode_name`. A panel measured at 8 rows of
    "Episode 01" is being asked to hold 31 rows of real titles."""

    def test_the_copy_column_takes_the_remaining_width(self):
        body = _rule(SERIES_CSS, ".series-episode-card")
        self.assertIn("minmax(0,1fr)", body.replace(" ", "").replace(
            "minmax(0,1fr)", "minmax(0,1fr)"))

    def test_a_long_title_is_truncated_rather_than_reflowing_the_row(self):
        """`minmax(0, 1fr)` is what lets it; without the 0 a long name pushes
        the play control off the card."""
        body = _rule(SERIES_CSS, ".series-episode-card")
        self.assertRegex(body, r"minmax\(\s*0\s*,\s*1fr\s*\)")


if __name__ == "__main__":
    unittest.main()
