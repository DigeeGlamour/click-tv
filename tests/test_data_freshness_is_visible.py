"""A cached list must not look as current as a fresh one.

Report items [07] and [09]. Every published file carries `updated_at`, and the
page read it only to decide whether to re-render - `state.manifestVersion` and
nothing else. So a viewer had no way to tell a list the service worker served
from cache after a failed fetch from one the scan had just written, and the
JSON timestamp everyone relies on never reached the screen.

The scan runs every twenty minutes, so anything past forty is behind.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = (ROOT / "site" / "assets" / "js" / "app.js").read_text(encoding="utf-8")
INDEX = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "site" / "assets" / "css" / "event-cards.css").read_text(
    encoding="utf-8")
SMART = (ROOT / "site" / "assets" / "css" / "smart-filter.css").read_text(
    encoding="utf-8")


class TheAgeReachesTheScreen(unittest.TestCase):
    def test_the_page_has_somewhere_to_put_it(self):
        self.assertIn('id="dataFreshness"', INDEX)

    def test_it_sits_in_the_list_header_beside_the_count(self):
        # That header is the one smart-filter.css brings back on a phone for
        # event lists, so the label is readable where it matters most.
        header = INDEX[INDEX.index('id="sidebarCountText"'):]
        header = header[:header.index("</div>")]
        self.assertIn('id="dataFreshness"', header)
        self.assertIn(".sidebar-top-bar.card-list-meta", SMART)

    def test_it_starts_hidden_so_an_unknown_age_shows_nothing(self):
        slot = re.search(r'<span class="sidebar-data-age"[^>]*>', INDEX)
        self.assertIsNotNone(slot)
        self.assertIn("hidden", slot.group(0))

    def test_it_is_styled_and_never_below_eleven_pixels(self):
        self.assertIn(".sidebar-data-age{", CSS)
        sizes = [float(value) for value in re.findall(
            r"\.sidebar-data-age[^{]*\{[^}]*?font-size:([\d.]+)px", CSS)]
        self.assertTrue(sizes)
        for size in sizes:
            self.assertGreaterEqual(size, 11.0)

    def test_a_stale_reading_is_styled_apart(self):
        self.assertIn(".sidebar-data-age.stale{", CSS)


class TheLabelSaysWhatTheTimestampMeans(unittest.TestCase):
    def test_the_reader_is_wired_to_the_manifest_timestamp(self):
        block = APP[APP.index("function dataAgeMinutes()"):]
        block = block[:block.index("function dataAgeLabel(")]
        self.assertIn("state.manifest?.updated_at", block)
        self.assertIn("state.manifestVersion", block)

    def test_an_unparseable_timestamp_reports_nothing(self):
        block = APP[APP.index("function dataAgeMinutes()"):]
        block = block[:block.index("function dataAgeLabel(")]
        self.assertIn("Number.isFinite", block)
        self.assertIn("return null", block)

    def test_it_hides_itself_when_there_is_no_age_to_show(self):
        block = APP[APP.index("function renderDataFreshness()"):]
        block = block[:block.index("function refreshEventCardsForClock(")]
        self.assertIn("node.hidden = true", block)

    def test_the_label_is_bengali(self):
        block = APP[APP.index("function dataAgeLabel("):]
        block = block[:block.index("function renderDataFreshness(")]
        for phrase in ("এইমাত্র আপডেট", "মিনিট আগে আপডেট",
                       "ঘণ্টা আগে আপডেট", "দিন আগে আপডেট"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, block)

    def test_stale_is_said_in_bengali_too(self):
        block = APP[APP.index("function renderDataFreshness()"):]
        block = block[:block.index("function refreshEventCardsForClock(")]
        self.assertIn("পুরোনো", block)

    def test_the_threshold_allows_for_a_missed_scan_but_not_two(self):
        match = re.search(r"const DATA_AGE_STALE_MINUTES = (\d+);", APP)
        self.assertIsNotNone(match)
        minutes = int(match.group(1))
        # The scan cadence is twenty minutes; one skipped tick is normal.
        self.assertGreaterEqual(minutes, 21)
        self.assertLessEqual(minutes, 60)


class ItKeepsItselfUpToDate(unittest.TestCase):
    def test_it_is_rendered_on_the_clock_tick_that_already_runs(self):
        self.assertIn("setInterval(renderDataFreshness, 30000)", APP)

    def test_it_is_rendered_once_at_startup(self):
        # Otherwise the first thirty seconds show nothing at all.
        tick = APP.index("setInterval(refreshEventCardsForClock, 30000)")
        self.assertIn("renderDataFreshness();", APP[tick:tick + 400])

    def test_replacing_the_manifest_refreshes_the_label(self):
        # Both places that adopt a new manifest, including the snapshot pointer
        # the background refresh follows.
        stamps = [match.end() for match in re.finditer(
            r"state\.manifestVersion = String\(", APP)]
        self.assertEqual(len(stamps), 2)
        for position in stamps:
            self.assertIn("renderDataFreshness();", APP[position:position + 200])


if __name__ == "__main__":
    unittest.main()
