"""The scroll handle answers to the cursor, and the Movies header fits its row.

Two reader faults reported 2026-09-03, both measured in Chromium against the
published build before being changed.

THE HANDLE STUCK ON AFTER ONE CLICK, AND APPEARED ON PHONES.

`#sidebarList` carries tabindex="0", so a single click or tap anywhere in the
list focused it. The show-rule in reference-design.css listed `:focus-within`
beside `:hover`, so the handle then stayed drawn for as long as focus sat
there - with the cursor nowhere near it. On a phone the same tap was the only
thing that ever produced a handle, which is why one appeared there at all.

    cursor parked in the corner        OLD          NEW
    idle                              none         none
    cursor over the list              thin         thin
    cursor moved away                 none         none
    after one click                   thin   <--   none
    phone, after one tap              thin   <--   none

There were two copies of that rule in the file, at two different widths (4px
and 3px), and only fixing one would have left the behaviour unchanged.

THE MOVIES COUNT RAN OUT OF ITS ROW.

"29 Manual · 30/29 Movies loaded" wrapped onto two lines in the sidebar
header and collided with the freshness stamp sharing that row. Three facts in
one slot, one of them ("30/29") arithmetic a viewer has no use for once
everything known has loaded.

    OLD  count "29 Manual · 30/29 Movies loaded"  detail ""           freshness shown
    NEW  count "30 Movies"                        detail "29 Manual"  freshness hidden
"""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

APP_JS = ROOT / "site" / "assets" / "js" / "app.js"
INDEX = ROOT / "site" / "index.html"
REFERENCE_CSS = ROOT / "site" / "assets" / "css" / "reference-design.css"


class TheScrollHandleFollowsTheCursor(unittest.TestCase):
    def setUp(self):
        self.css = REFERENCE_CSS.read_text(encoding="utf-8")

    def test_no_sidebar_surface_reveals_the_handle_on_focus_within(self):
        """The whole bug. `:focus-within` stays true after a click, so the
        handle stayed drawn with the cursor gone."""
        for selector in (".sidebar-scroll-area:focus-within",
                         ".sidebar-list:focus-within"):
            with self.subTest(selector=selector):
                self.assertNotIn(selector, self.css)

    def test_the_cursor_still_reveals_it(self):
        """Removing the reveal altogether would be a different regression."""
        self.assertIn(".sidebar-scroll-area:hover", self.css)
        self.assertRegex(
            self.css, r"\.sidebar-scroll-area:hover[^{]*\{[^}]*scrollbar-width:thin"
        )

    def test_a_keyboard_user_keeps_a_handle(self):
        """`:focus-visible` does not match a mouse click or a touch tap, so it
        buys back keyboard scrolling without bringing the bug with it."""
        self.assertIn(".sidebar-list:focus-visible", self.css)

    def test_both_copies_of_the_rule_were_changed(self):
        """The file carried the same rule twice, at 4px and at 3px. Fixing one
        would have left the other revealing the handle."""
        reveals = re.findall(r"\.sidebar-(?:scroll-area|list):(hover|focus-\w+)", self.css)
        self.assertNotIn("focus-within", reveals)
        self.assertGreaterEqual(reveals.count("hover"), 2, reveals)

    def test_a_device_with_no_cursor_gets_no_handle(self):
        """It had none before: there is no cursor to reveal one with, and the
        tap that focused the list was the only thing that ever did."""
        block = re.search(r"@media \(hover: none\)\s*\{(.*?)\n\}", self.css, re.S)
        self.assertIsNotNone(block, "the touch-device rule is gone")
        body = block.group(1)
        self.assertIn(".sidebar-list", body)
        self.assertIn(".sidebar-scroll-area", body)
        self.assertIn("scrollbar-width:none", body.replace(" ", ""))
        self.assertRegex(body.replace(" ", ""), r"width:0!important")

    def test_it_is_hover_none_and_not_a_width_breakpoint(self):
        """A narrow window on a desktop still has a cursor and still gets its
        handle; a wide tablet has no cursor and does not."""
        block = re.search(r"@media \(hover: none\)", self.css)
        self.assertIsNotNone(block)


class TheMoviesHeaderFitsItsRow(unittest.TestCase):
    def setUp(self):
        self.js = APP_JS.read_text(encoding="utf-8")

    def test_the_long_sentence_is_no_longer_emitted(self):
        """Comment lines are skipped on purpose: the fix quotes the old string
        to say what it replaced, and that is documentation, not output."""
        code = [
            line for line in self.js.splitlines()
            if not line.lstrip().startswith(("//", "*", "/*"))
        ]
        offenders = [line.strip() for line in code if "Movies loaded" in line]
        self.assertEqual(offenders, [])

    def test_the_count_and_the_manual_figure_use_the_two_slots_that_exist(self):
        """`.sidebar-count-detail` is a second span beside the count, built for
        exactly this and already hidden on a narrow screen."""
        call = re.search(
            r"setSidebarCount\(\s*\n\s*more \?(.*?)\n\s*\);", self.js, re.S
        )
        self.assertIsNotNone(call, "the movie count call moved")
        body = call.group(1)
        self.assertIn("Movies", body)
        self.assertIn("Manual", body)

    def test_the_denominator_only_shows_while_there_is_more_to_come(self):
        """"30/29" is arithmetic nobody asked for."""
        self.assertRegex(self.js, r"const more = [^\n]*totalKnown > loaded")
        self.assertRegex(self.js, r"more \?\s*`\$\{loaded\}/\$\{totalKnown\} Movies`")
        self.assertRegex(self.js, r":\s*`\$\{loaded\} Movies`")

    def test_the_freshness_stamp_stays_off_the_movies_row(self):
        fn = re.search(r"function renderDataFreshness\(\) \{(.*?)\n\}", self.js, re.S)
        self.assertIsNotNone(fn, "renderDataFreshness moved")
        body = fn.group(1)
        self.assertIn("state.view === VIEW.MOVIE", body)
        # It must bail before writing any text, not after.
        self.assertLess(
            body.index("VIEW.MOVIE"),
            body.index("dataAgeMinutes()"),
            "the movie check runs too late to stop the stamp",
        )

    def test_a_live_list_keeps_its_stamp(self):
        """The age of a match list is the whole point of showing it; only the
        movie catalogue does not need one."""
        fn = re.search(r"function renderDataFreshness\(\) \{(.*?)\n\}",
                       self.js, re.S).group(1)
        self.assertNotIn("VIEW.EVENT", fn)
        self.assertNotIn("VIEW.UPCOMING", fn)

    def test_the_stamp_is_re_decided_when_the_view_changes(self):
        """It is per-view now. Without this, leaving Movies leaves it hidden
        until the next thirty-second tick."""
        render = re.search(r"function renderCurrentList\(reset = true, options = \{\}\) \{(.*?)\n  const totalKnown",
                           self.js, re.S)
        self.assertIsNotNone(render, "renderCurrentList moved")
        self.assertIn("renderDataFreshness()", render.group(1))

    def test_the_freshness_element_is_still_in_the_row(self):
        self.assertIn('id="dataFreshness"', INDEX.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
