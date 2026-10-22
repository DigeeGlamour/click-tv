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
        for caption in ("ম্যাচ সেন্টার", "খেলা অনুযায়ী", "স্পোর্টস টিভি",
                        "কুইক অ্যাক্সেস", "টিভি ক্যাটাগরি"):
            self.assertIn(caption, APP)

    def test_no_account_or_plan_is_claimed_on_the_live_rails(self):
        start = APP.index("function buildLiveRailStatusCard")
        body = APP[start:APP.index("async function loadRailEventStats")]
        self.assertNotIn("Premium", body)


if __name__ == "__main__":
    unittest.main()
