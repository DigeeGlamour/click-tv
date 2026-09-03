"""Three reader changes the owner asked for on 2026-09-03, each measured.

1. NO PLAYER ON UPCOMING, ON A PHONE.

Nothing on Upcoming can be played - every card there is a fixture whose
kickoff is still ahead - so on a phone the player was a black half-screen
between the tabs and the list. Measured in Chromium:

                      before                    after
    phone 390    player shown, first card 457   hidden, first card 184
    tablet 768   player shown, first card 671   hidden, first card 185
    desktop 1900 player shown, first card 140   unchanged

The two navigation rows stay; only #videoContainer and the NOW PLAYING bar
go. Hiding it only while idle was tried first and is close to useless once
change 2 below is in: something is decoding almost whenever a viewer reaches
this tab, so the player would still be there. The consequence is written down
rather than designed around - a match already playing keeps its sound, and
Today Match brings the player and its controls back in one tap.

2. THE FIRST TODAY MATCH PLAYS AGAIN ON A FIRST VISIT.

It had been changed to select-and-wait, and the reasons recorded for that
were real. The owner asked for the original behaviour back, so it is back:
measured after the change, a fresh load reaches readyState 4, paused false,
currentTime advancing, with the match title in the player. Before:
playbackActive false, no source, paused.

Passed userInitiated=false, so a browser that refuses to sound an unprompted
stream lands on the existing NotAllowedError path, keeps playing muted, and
gives the sound back on the first tap.

3. PRESSING THE CARD TURNS ITS PLAYING SERVER GREEN.

markActiveTodayChannel was only ever called from the pill's own click
handler, so pressing the CARD started a stream and turned nothing green -
with several cards each offering several servers, nothing said which one the
player was on. Measured before: pressing the card gave 0 green pills while
the card itself went active and playback started. After: exactly one, on that
card, naming the server that is playing.

It is derived from state now rather than applied by hand, so a re-render
keeps it.
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


class ThePhoneLosesTheDeadPlayerOnUpcoming(unittest.TestCase):
    def setUp(self):
        self.css = REFERENCE_CSS.read_text(encoding="utf-8")
        self.js = APP_JS.read_text(encoding="utf-8")

    def test_the_view_is_written_where_the_stylesheet_can_read_it(self):
        self.assertIn(
            "document.body.classList.toggle('upcoming-view', state.view === VIEW.UPCOMING)",
            self.js,
        )

    def test_the_player_and_its_meta_bar_are_the_only_things_hidden(self):
        block = re.search(
            r"body\.upcoming-view #videoContainer,\s*"
            r"body\.upcoming-view \.video-section > \.video-meta\{([^}]*)\}",
            self.css,
        )
        self.assertIsNotNone(block, "the mobile hide rule is gone")
        self.assertIn("display:none", block.group(1).replace(" ", ""))

    def test_the_navigation_rows_are_not_hidden_with_it(self):
        """They are siblings of the player inside .video-section, and they are
        how a viewer gets back to Today Match."""
        for kept in ("#mobileSubNavigation", "#mobileMainNavigation"):
            with self.subTest(element=kept):
                self.assertNotRegex(
                    self.css,
                    r"body\.upcoming-view[^{]*" + re.escape(kept),
                )

    def test_it_is_scoped_to_the_mobile_layout(self):
        """Desktop keeps the player in its own column beside the list, where
        it costs the list nothing."""
        pos = self.css.find("body.upcoming-view #videoContainer")
        self.assertGreater(pos, 0)
        opener = self.css.rfind("@media", 0, pos)
        self.assertGreater(opener, 0, "the hide rule is not inside a media query")
        self.assertIn("max-width: 1000px", self.css[opener:pos])

    def test_the_player_element_is_still_in_the_markup(self):
        """Hidden by the stylesheet, not removed - Today Match needs it."""
        self.assertIn('id="videoContainer"', INDEX.read_text(encoding="utf-8"))


class TheFirstMatchPlaysOnAFirstVisit(unittest.TestCase):
    def setUp(self):
        self.js = APP_JS.read_text(encoding="utf-8")
        block = re.search(
            r"if \(options\.initial && !state\.currentItem && "
            r"state\.currentItems\.length && kind !== VIEW\.UPCOMING\) \{(.*?)\n    \}",
            self.js, re.S,
        )
        self.assertIsNotNone(block, "the first-visit branch moved")
        self.body = block.group(1)

    def test_it_starts_playback_rather_than_only_selecting(self):
        self.assertIn("startPlayback(firstPlayable, false)", self.body)
        self.assertNotIn("selectWithoutPlaying", self.body)

    def test_it_says_the_gesture_was_not_the_viewers(self):
        """userInitiated=false is what routes an autoplay refusal to the muted
        fallback instead of surfacing as a playback error."""
        self.assertRegex(self.body, r"startPlayback\(firstPlayable,\s*false\)")

    def test_the_muted_fallback_it_relies_on_is_still_there(self):
        self.assertIn("NotAllowedError", self.js)
        self.assertRegex(self.js, r"NotAllowedError'\) \{[\s\S]{0,200}?video\.muted = true")

    def test_it_only_fires_on_a_real_first_load_and_never_on_upcoming(self):
        self.assertIn("options.initial", self.js)
        self.assertIn("kind !== VIEW.UPCOMING", self.js)

    def test_it_only_fires_when_nothing_is_already_playing(self):
        self.assertIn("!state.currentItem", self.js)

    def test_select_without_playing_is_kept_for_its_other_callers(self):
        """It is still the right answer when a viewer picks a card that cannot
        play; only the first-visit path stopped using it."""
        self.assertIn("function selectWithoutPlaying(item)", self.js)


class TheGreenFollowsWhatIsPlaying(unittest.TestCase):
    def setUp(self):
        self.js = APP_JS.read_text(encoding="utf-8")
        fn = re.search(r"function syncActiveTodayChannel\(\) \{(.*?)\n\}",
                       self.js, re.S)
        self.assertIsNotNone(fn, "syncActiveTodayChannel is gone")
        self.body = fn.group(1)

    def test_it_asks_the_same_function_the_player_asks(self):
        """activeChannelId answers for a card nobody has chosen a server on -
        its default, or its first - which is exactly what a card press plays."""
        self.assertIn("activeChannelId(playing)", self.body)

    def test_it_keys_the_card_off_what_is_playing(self):
        self.assertIn("state.currentItem", self.body)
        self.assertIn("_uid", self.body)

    def test_only_the_playing_card_can_hold_a_green(self):
        self.assertIn("isPlayingCard", self.body)
        self.assertRegex(self.body, r"classList\.toggle\('active-channel', active\)")

    def test_a_placeholder_pill_never_goes_green(self):
        """"চ্যানেল এখনো পাওয়া যায়নি" is not a server."""
        self.assertIn("classList.contains('muted')", self.body)

    def test_nothing_goes_green_when_nothing_is_playing(self):
        self.assertRegex(self.body, r"wantedChannel !== ''")

    def test_it_runs_wherever_the_active_state_is_refreshed(self):
        """updateActiveCards is called from renderCurrentList, from the
        reconcile pass and from startPlayback - so a card press, a pill press
        and a rebuilt list all end up agreeing."""
        fn = re.search(r"function updateActiveCards\(\) \{(.*?)\n\}", self.js, re.S)
        self.assertIsNotNone(fn, "updateActiveCards moved")
        self.assertIn("syncActiveTodayChannel()", fn.group(1))

    def test_the_click_handler_still_greens_immediately(self):
        """The optimistic green stays: a stream that takes a moment to decode
        should still look like the press registered."""
        fn = re.search(r"const activate = async \(pill\) => \{(.*?)\n  \};",
                       self.js, re.S)
        self.assertIsNotNone(fn, "the pill activate handler moved")
        self.assertIn("markActiveTodayChannel(pill)", fn.group(1))


if __name__ == "__main__":
    unittest.main()
