"""The Live Sports / Live TV rail and the owner's popular-channel list.

The owner adds a popular channel by writing its name in
site/popular-channels.json - nothing else - so the file must stay a plain
list of names, and the page must read it from where it is published.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "site" / "assets" / "js" / "app.js").read_text(encoding="utf-8")


class ThePopularList(unittest.TestCase):
    def test_it_is_a_list_of_names(self):
        data = json.loads((ROOT / "site" / "popular-channels.json").read_text(encoding="utf-8"))
        self.assertTrue(data["channels"])
        for name in data["channels"]:
            self.assertIsInstance(name, str)
            self.assertTrue(name.strip())

    def test_the_page_reads_it_from_the_site_root(self):
        self.assertIn("const POPULAR_CHANNELS_URL = 'popular-channels.json';", APP)


class TheRail(unittest.TestCase):
    def test_both_live_sections_draw_the_grouped_rail(self):
        self.assertIn("renderLiveRail(root, state.activeMainGroup);", APP)
        for caption in ("'MATCH CENTER'", "'BY SPORT'", "'QUICK ACCESS'", "'TV CATEGORIES'"):
            self.assertIn(caption, APP)

    def test_24_7_sports_follows_upcoming(self):
        start = APP.index("railCaption(root, 'MATCH CENTER');")
        body = APP[start:APP.index("railCaption(root, 'BY SPORT');")]
        self.assertLess(body.index("'upcoming'"), body.index("'sports-channel'"))

    def test_the_live_rails_carry_no_user_card(self):
        start = APP.index("function renderLiveRail")
        body = APP[start:APP.index("async function loadRailEventStats")]
        self.assertNotIn("movie-rail-user", body)
        self.assertNotIn("Premium", body)



class SearchTagsAndRecent(unittest.TestCase):
    def test_tags_float_and_never_reach_movies(self):
        start = APP.index("async function showSearchTags")
        body = APP[start:APP.index("async function openSearchTag")]
        self.assertIn("movies-section", body)
        css = (ROOT / "site" / "assets" / "css" / "site-font.css").read_text(encoding="utf-8")
        self.assertIn("position:fixed", css[css.index(".search-tags-drop"):])

    def test_a_tag_only_names_a_published_channel(self):
        start = APP.index("async function showSearchTags")
        body = APP[start:APP.index("async function openSearchTag")]
        self.assertIn("published.has(popularNameKey(name))", body)

    def test_recent_is_on_the_live_tv_rail(self):
        self.assertIn("railButton(root, 'recent', 'Recent'", APP)
        self.assertIn("['recent', 'Recent']", APP)

if __name__ == "__main__":
    unittest.main()
