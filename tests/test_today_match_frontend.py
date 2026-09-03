"""Three Today Match faults the owner reported on 2026-09-03, each measured.

All three were in the reader, not the scanner, and each was measured in a real
Chromium at 1900x950 against the published site before being fixed.

1. THE HEADER WAS PAINTED UNDER THE CARDS.

   `.sidebar-scroll-area` is a COLUMN flex container. `#sidebarList` inside it
   is `flex:0 0 auto` and cannot give way, so `.sidebar-top-bar` was the only
   item in that column able to shrink - and `min-height:0` let it shrink the
   whole way. Measured with twelve cards: the bar computed to height 0 while
   its own content needed 24, so "আজকের ম্যাচ" and its count overflowed
   their own box and were drawn 8px inside the first row of cards.

       before   bar height 0    count-detail bottom 122   first card top 114
       after    bar height 24   count-detail bottom 122   first card top 138

2. THE LIST EMPTIED ITSELF ABOUT THIRTY SECONDS AFTER A FIRST VISIT.

   `setupReturnToTabRefresh` passed `state.view` to `selectMainView`, which
   switches on the string a chip passes. The two vocabularies only overlap by
   luck: VIEW.UPCOMING is 'upcoming' and matches, VIEW.EVENT is 'event' and
   the branch it needs is 'today-match'. So Today Match fell through to the
   channel branch, looked for `manifest.channels['Today Match']`, found
   nothing and printed "এই বিভাগের JSON path পাওয়া যায়নি" over an
   emptied list on the first tab switch after thirty seconds.

   Reproduced against the published site: 12 cards before, 0 cards and that
   message after. It came back on the next scroll only because
   state.currentItems was never cleared, which is why it read as a flicker.

3. A MATCH THAT HAD ALREADY STARTED SAID IT HAD NOT.

   Kashi Rudras vs Noida Kings, kickoff 14:00 UTC (8:00 PM BDT),
   metadata_only with `channels: []`. At 8:41 PM the preview still announced
   "Upcoming Match" over "Stream link will be added before the match starts",
   and the card still promised "চ্যানেল শীঘ্রই যোগ হবে" - a promise about
   the future, forty-one minutes into the past. Both sentences were fixed
   text in the markup with nothing reading the clock.

These assert the source, the way the other frontend contracts in this suite
do, because the fault in each case was a line that could be deleted without
any Python test noticing.
"""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

APP_JS = ROOT / "site" / "assets" / "js" / "app.js"
INDEX = ROOT / "site" / "index.html"
CARDS_CSS = ROOT / "site" / "assets" / "css" / "final-match-cards.css"
APP_CSS = ROOT / "site" / "assets" / "css" / "app.css"


class TheHeaderKeepsItsHeight(unittest.TestCase):
    def setUp(self):
        self.css = CARDS_CSS.read_text(encoding="utf-8")

    def test_the_today_header_does_not_shrink(self):
        """Without this the bar is the only thing in a column flex container
        that CAN shrink, and it shrinks to nothing."""
        rule = re.search(
            r"\.sidebar-section\.event-list-mode:not\(\.upcoming-mode\)\s+"
            r"\.sidebar-top-bar\.card-list-meta\s*\{([^}]*)\}",
            self.css,
        )
        self.assertIsNotNone(rule, "the no-shrink rule for the Today header is gone")
        body = rule.group(1).replace(" ", "")
        self.assertTrue(
            "flex:0 0 auto".replace(" ", "") in body or "flex-shrink:0" in body,
            f"the header can shrink again: {body}",
        )

    def test_the_reason_it_could_shrink_still_holds(self):
        """The base rule that made the header the only shrinkable item.

        Above 1001px this is no longer the thing doing the work:
        reference-design.css hands the scroll to the list there, as
        `flex:1 1 auto`, so the list CAN give way and the header is held open
        by its own `flex:0 0 auto` in that same block - asserted in
        tests/test_sidebar_scroll_and_header.py. Below 1001px this base rule
        is still what stops the collapse, so it is still asserted here.
        """
        self.assertRegex(
            APP_CSS.read_text(encoding="utf-8"),
            r"\.sidebar-scroll-area\s*>\s*\.sidebar-list\s*\{[^}]*flex:\s*0\s+0\s+auto",
        )

    def test_the_scroll_area_is_still_a_column_flex_container(self):
        self.assertRegex(
            APP_CSS.read_text(encoding="utf-8"),
            r"\.sidebar-scroll-area\s*\{[^}]*flex-direction:\s*column",
        )

    def test_upcoming_is_not_touched_by_it(self):
        """The owner asked for Today Match only. Upcoming already stops the
        collapse with its own 34px min-height."""
        self.assertIn(":not(.upcoming-mode)", self.css)
        self.assertRegex(
            self.css,
            r"\.sidebar-section\.upcoming-mode\s+\.sidebar-top-bar"
            r"\.card-list-meta\s*\{[^}]*min-height:34px",
        )


class TheTabReturnUsesTheRightKey(unittest.TestCase):
    def setUp(self):
        self.js = APP_JS.read_text(encoding="utf-8")

    def test_state_view_is_never_passed_straight_to_select_main_view(self):
        """The whole bug in one expression."""
        self.assertNotIn("selectMainView(state.view", self.js)

    def test_the_map_exists_and_sends_the_event_view_to_today_match(self):
        block = re.search(r"const VIEW_SELECT_KEYS = Object\.freeze\(\{(.*?)\}\)",
                          self.js, re.S)
        self.assertIsNotNone(block, "the VIEW -> select key map is gone")
        body = block.group(1)
        self.assertRegex(body, r"\[VIEW\.EVENT\]:\s*'today-match'")
        self.assertRegex(body, r"\[VIEW\.UPCOMING\]:\s*'upcoming'")
        self.assertRegex(body, r"\[VIEW\.CHANNEL\]:\s*'channel'")
        # 'favorite' vs 'favorites' was the same class of mismatch.
        self.assertRegex(body, r"\[VIEW\.FAVORITE\]:\s*'favorites'")

    def test_every_key_in_the_map_is_a_branch_select_main_view_actually_has(self):
        """A map that agrees with itself and not with the function it feeds
        would fail exactly as silently as the bug did."""
        block = re.search(r"const VIEW_SELECT_KEYS = Object\.freeze\(\{(.*?)\}\)",
                          self.js, re.S).group(1)
        keys = re.findall(r":\s*'([a-z-]+)'", block)
        self.assertTrue(keys)
        body = re.search(
            r"async function selectMainView\(view, category, options = \{\}\) \{(.*?)\n\}",
            self.js, re.S,
        )
        self.assertIsNotNone(body, "selectMainView moved")
        for key in keys:
            with self.subTest(key=key):
                if key == "channel":
                    # The final else is the channel branch; it has no literal.
                    continue
                self.assertIn(f"view === '{key}'", body.group(1))

    def test_an_unmapped_view_skips_the_refresh_rather_than_guessing(self):
        refresh = re.search(r"function setupReturnToTabRefresh\(\) \{(.*?)\n\}",
                            self.js, re.S)
        self.assertIsNotNone(refresh)
        self.assertRegex(refresh.group(1), r"selectKeyForView\(state\.view\)")
        self.assertRegex(refresh.group(1), r"if \(!key\) return;")


class AStartedMatchSaysSo(unittest.TestCase):
    def setUp(self):
        self.js = APP_JS.read_text(encoding="utf-8")
        self.html = INDEX.read_text(encoding="utf-8")

    def test_the_preview_status_and_note_are_addressable(self):
        """Both were fixed text in the markup with no id to reach them by."""
        self.assertIn('id="eventPreviewStatus"', self.html)
        self.assertIn('id="eventPreviewNote"', self.html)

    def test_the_preview_answers_to_the_clock(self):
        preview = re.search(r"function showEventPreview\(item\) \{(.*?)\n\}",
                            self.js, re.S)
        self.assertIsNotNone(preview, "showEventPreview moved")
        body = preview.group(1)
        self.assertIn("eventStartedAgoText(item)", body)
        self.assertIn("eventPreviewStatus", body)
        self.assertIn("eventPreviewNote", body)
        self.assertIn("Match Started", body)
        # The sentence that was false after kickoff must now be one of two
        # branches, never the only thing the panel can say.
        self.assertIn("Kickoff has passed", body)

    def test_the_elapsed_phrase_reads_as_a_sentence(self):
        """`eventLivePhaseText` answers 'just now' under a minute, and
        "Started just now ago" is not English."""
        fn = re.search(r"function eventStartedAgoText\(item\) \{(.*?)\n\}",
                       self.js, re.S)
        self.assertIsNotNone(fn, "eventStartedAgoText is gone")
        body = fn.group(1)
        self.assertIn("just now", body)
        self.assertIn("eventLivePhaseText(item)", body)

    def test_the_card_stops_promising_a_channel_after_kickoff(self):
        pills = re.search(r"function todayChannelPillsHtml\(item\) \{(.*?)\n\}",
                          self.js, re.S)
        self.assertIsNotNone(pills, "todayChannelPillsHtml moved")
        body = pills.group(1)
        self.assertIn("eventLivePhaseText(item)", body)
        self.assertIn("চ্যানেল এখনো পাওয়া যায়নি", body)
        # The forward-looking wording survives, for a match that really is
        # still ahead.
        self.assertIn("চ্যানেল শীঘ্রই যোগ হবে", body)

    def test_a_countdown_and_an_elapsed_time_are_never_shown_together(self):
        preview = re.search(r"function showEventPreview\(item\) \{(.*?)\n\}",
                            self.js, re.S).group(1)
        self.assertRegex(preview, r"countdown \? '' : eventStartedAgoText\(item\)")


class TheMasonryKeepsTheViewersPlace(unittest.TestCase):
    """Scrolling down through Today Match kept snapping back to the top.

    `layoutTodayMasonry` set every card to `grid-row-end:auto` before
    measuring it. Measured at 1900x950 with the list scrolled to 600px:

        before the reset   scrollTop 600   scrollHeight 1577
        during the reset   scrollTop   0   scrollHeight  836   <- collapsed
        after restoring    scrollTop   0   scrollHeight 1577   <- 600px lost

    The browser clamps scrollTop to whatever still fits, and restoring the
    spans cannot give it back. It fired on every poster that finished
    loading, every appended page and every resize, which is what the jumping
    was.

    The reset was never needed. These cards are `align-self:start` with
    `height:auto`, so a card's box is its own content height whether or not a
    span is allocating grid space for it - measured across twelve cards, the
    heights with spans applied and with them reset are identical to the tenth
    of a pixel. With the reset gone the resulting layout is identical too:
    two columns, no overlapping pairs, the same vertical gaps.
    """

    def setUp(self):
        self.js = APP_JS.read_text(encoding="utf-8")
        found = re.search(
            r"function layoutTodayMasonry\(\) \{(.*?)\n\}", self.js, re.S
        )
        self.assertIsNotNone(found, "layoutTodayMasonry moved")
        self.body = found.group(1)

    def test_it_never_collapses_the_grid_to_measure_it(self):
        """The one line that threw the scroll position away."""
        self.assertNotIn("gridRowEnd = 'auto'", self.body)
        self.assertNotIn('gridRowEnd = "auto"', self.body)

    def test_it_still_assigns_a_span_from_the_measured_height(self):
        self.assertIn("getBoundingClientRect().height", self.body)
        self.assertIn("gridRowEnd = next", self.body)
        self.assertIn("span ${span}", self.body)

    def test_it_only_writes_when_the_span_actually_changes(self):
        """An unconditional write invalidates layout for every card on every
        scroll tick that reaches here."""
        self.assertIn("if (card.style.gridRowEnd !== next)", self.body)

    def test_the_property_that_makes_the_reset_unnecessary_is_still_set(self):
        """If a card ever stops being start-aligned with an auto height, its
        box stops being its content height and the measurement above becomes
        wrong. That is the precondition, so it is asserted here."""
        css = CARDS_CSS.read_text(encoding="utf-8")
        rule = re.search(
            r"#sidebarList\.today-grid > \.poster-card\s*\{([^}]*)\}", css
        )
        self.assertIsNotNone(rule, "the Today card rule moved")
        body = rule.group(1).replace(" ", "")
        self.assertIn("align-self:start", body)
        self.assertIn("height:auto", body)

    def test_the_grid_still_uses_one_pixel_rows(self):
        """The span arithmetic is written against `grid-auto-rows:1px`."""
        css = CARDS_CSS.read_text(encoding="utf-8")
        rule = re.search(
            r"#sidebarList\.today-grid\s*\{([^}]*)\}", css
        )
        self.assertIsNotNone(rule)
        self.assertIn("grid-auto-rows:1px", rule.group(1).replace(" ", ""))


if __name__ == "__main__":
    unittest.main()
