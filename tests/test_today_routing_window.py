"""A match reaches Today Match before it starts, not after.

The tab is named for the day but was routed purely on status, so a fixture at
20:00 sat on Upcoming at 19:55 and only crossed over once the scanner marked it
LIVE_NOW - after the whistle. A viewer opening the site at 19:45 pressed Today
Match and did not find the match they came for. That is the whole reason for
this change, and renaming the tab to "Live Now" would have described the bug
rather than fixed it.

The flow the owner asked for:

    more than 25 min away  ->  Upcoming
    25 min or less         ->  Today Match, counting down
    stream resolved        ->  Today Match, stream ready
    kicked off             ->  Today Match, LIVE
    kicked off, no stream  ->  Today Match, still looking - for 30 minutes
    nothing after that     ->  dropped
"""
import re
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import events  # noqa: E402
from scanner.lifecycle_config import lifecycle_settings  # noqa: E402
from scanner.event_lifecycle import (  # noqa: E402
    DEFAULT_TODAY_ROUTING_MINUTES,
    event_destination,
    minutes_to_kickoff,
)

#: What production routes on: config/settings.json -> event_lifecycle.
_LIFECYCLE = lifecycle_settings(settings_path=ROOT / "config" / "settings.json")
CONFIGURED_ROUTING_MINUTES = _LIFECYCLE["move_to_today_minutes"]
CONFIGURED_NO_LINK_GRACE = _LIFECYCLE["no_link_today_grace_minutes"]

NOW = datetime(2026, 8, 30, 14, 0, tzinfo=timezone.utc)


def fixture(minutes_away, status="STARTING_SOON", **fields):
    card = {
        "start_time": (NOW + timedelta(minutes=minutes_away)).isoformat(),
        "status": status,
        "_source_timezone": timezone.utc,
    }
    card.update(fields)
    return card


class RoutingCrossesBeforeKickoffTests(unittest.TestCase):
    def test_the_window_comes_from_config_and_is_twenty_five(self):
        """It was a constant, and a constant is where the drift started."""
        self.assertEqual(25, CONFIGURED_ROUTING_MINUTES)

    def test_the_constant_is_a_fallback_not_the_source_of_truth(self):
        """Still 30, and still what a caller with no settings to hand gets.
        A fallback is a way to survive a missing config, not a second
        opinion - so it stays at the old behaviour deliberately."""
        self.assertEqual(30, DEFAULT_TODAY_ROUTING_MINUTES)
        self.assertEqual(
            "today_match",
            event_destination(fixture(28), NOW),
            "the default argument is no longer the old 30-minute window",
        )

    def test_the_hunt_window_and_the_routing_threshold_are_the_same_number(self):
        """The guarantee this protects: a fixture must not arrive on Today
        Match before anything is looking for its link, or it sits there with
        nothing to show.

        Both were 30 and equal by hand, which is how they were able to drift.
        PROMPT 10 moved the hunt to config and left routing on its constant,
        opening a five-minute gap that was recorded at the time. PROMPT 11
        closes it: they are now the same key, so they cannot disagree.
        """
        from scanner.lifecycle_config import targeted_timings
        hunt = targeted_timings(
            settings_path=ROOT / "config" / "settings.json")["window_minutes"]
        self.assertEqual(hunt, CONFIGURED_ROUTING_MINUTES,
                         "the hunt and the routing threshold disagree again")
        self.assertEqual(0, CONFIGURED_ROUTING_MINUTES - hunt)

    def test_they_are_the_same_key_and_not_two_equal_numbers(self):
        """Equal values are what the old fault looked like from outside."""
        from scanner.lifecycle_config import lifecycle_settings
        for threshold in (17, 25, 41):
            with self.subTest(threshold=threshold):
                supplied = {"event_lifecycle": {"move_to_today_minutes": threshold}}
                resolved = lifecycle_settings(supplied)["move_to_today_minutes"]
                self.assertEqual(threshold, resolved)
                self.assertEqual(
                    "upcoming",
                    event_destination(fixture(threshold + 1), NOW,
                                      routing_minutes=resolved),
                )
                self.assertEqual(
                    "today_match",
                    event_destination(fixture(threshold), NOW,
                                      routing_minutes=resolved),
                )

    def test_the_owners_worked_example(self):
        """An 8pm match, at the configured threshold rather than the old one.

        The owner asked for 20 to 25 minutes; FINAL_2 chose 25 because a late
        GitHub tick would otherwise push a viewer past the 20-minute edge.
        The 7:30 PM row therefore reads "upcoming" now, where it used to read
        "today_match" - the change being made, not a regression.
        """
        for minutes_away, expected in (
            (180, "upcoming"),      # 5:00 PM
            (60, "upcoming"),       # 7:00 PM
            (30, "upcoming"),       # 7:30 PM  - was today_match at 30
            (26, "upcoming"),       # 7:34 PM
            (25, "today_match"),    # 7:35 PM  - the boundary
            (20, "today_match"),    # 7:40 PM
            (15, "today_match"),    # 7:45 PM
            (1, "today_match"),     # 7:59 PM
            (0, "today_match"),     # 8:00 PM
            (-30, "today_match"),   # 8:30 PM
        ):
            self.assertEqual(
                expected,
                event_destination(fixture(minutes_away), NOW,
                                  routing_minutes=CONFIGURED_ROUTING_MINUTES),
                f"a fixture {minutes_away} minutes from kickoff",
            )

    def test_link_updating_at_kickoff_is_not_upcoming(self):
        """A match at its own kickoff with the scanner still hunting is the last
        thing that should be filed under 'upcoming'."""
        self.assertEqual(
            "today_match",
            event_destination(fixture(0, status="LINK_UPDATING"), NOW),
        )

    def test_live_still_routes_live_whatever_the_clock(self):
        self.assertEqual(
            "today_match", event_destination(fixture(120, status="LIVE_NOW"), NOW)
        )

    def test_ended_still_leaves(self):
        self.assertEqual(
            "ended", event_destination(fixture(-200, status="ENDED"), NOW)
        )

    def test_a_fixture_with_no_clock_stays_where_its_status_says(self):
        """A channel-backed card carries no kickoff, and guessing one for it
        would move it on no evidence at all."""
        self.assertEqual(
            "upcoming", event_destination({"status": "UPCOMING"}, NOW)
        )

    def test_minutes_to_kickoff_reads_both_field_names(self):
        self.assertAlmostEqual(
            30.0, minutes_to_kickoff(fixture(30), NOW), places=1
        )
        self.assertIsNone(minutes_to_kickoff({}, NOW))


class AFixtureWithNoLinkDoesNotSitThereTests(unittest.TestCase):
    """Routing brings a fixture across half an hour early, so one whose feeds
    never carry a stream arrives on Today Match and would otherwise stay. It
    shows honestly - "still looking" - but only while that is still plausible."""

    def test_the_grace_comes_from_config_and_is_twenty_five(self):
        self.assertEqual(25, CONFIGURED_NO_LINK_GRACE)

    def test_the_constant_is_a_fallback_not_the_source_of_truth(self):
        """Still 30, and still what a caller with no settings gets. It is how
        a missing config reproduces the old behaviour instead of quietly
        adopting a new one."""
        self.assertEqual(30, events.TODAY_NO_LINK_GRACE_MINUTES)
        self.assertTrue(
            events._is_today_fresh(fixture(-28), NOW, 12),
            "the default argument no longer reproduces the old 30-minute grace",
        )

    def test_kept_while_the_hunt_is_still_plausible(self):
        for minutes_past in (0, 5, 10, 24, 25):
            with self.subTest(minutes_past=minutes_past):
                self.assertTrue(events._is_today_fresh(
                    fixture(-minutes_past), NOW, 12, CONFIGURED_NO_LINK_GRACE))

    def test_dropped_once_it_is_not(self):
        for minutes_past in (26, 31, 90):
            with self.subTest(minutes_past=minutes_past):
                self.assertFalse(events._is_today_fresh(
                    fixture(-minutes_past), NOW, 12, CONFIGURED_NO_LINK_GRACE))

    def test_the_boundary_follows_a_custom_config_value(self):
        """The number is a decision about the owner's site, not the code."""
        from scanner.lifecycle_config import lifecycle_settings
        for grace in (5, 25, 45):
            resolved = lifecycle_settings(
                {"event_lifecycle": {"no_link_today_grace_minutes": grace}}
            )["no_link_today_grace_minutes"]
            with self.subTest(grace=grace):
                self.assertEqual(grace, resolved)
                self.assertTrue(events._is_today_fresh(
                    fixture(-grace), NOW, 12, resolved))
                self.assertFalse(events._is_today_fresh(
                    fixture(-(grace + 1)), NOW, 12, resolved))

    def test_a_no_link_fixture_still_arrives_and_is_not_dropped_on_arrival(self):
        """The half that matters most: crossing at T-25 with nothing to play
        must not be the same thing as being thrown away."""
        arriving = fixture(25, status="LINK_UPDATING",
                           schedule_status="LINK_UPDATING")
        self.assertEqual("today_match", event_destination(
            arriving, NOW, routing_minutes=CONFIGURED_ROUTING_MINUTES))
        self.assertTrue(events._is_today_fresh(
            arriving, NOW, 12, CONFIGURED_NO_LINK_GRACE))

    def test_a_fixture_with_a_link_is_never_dropped_by_this_rule(self):
        """It is live and being watched; only the end-of-match rules retire it."""
        for extra in ({"playback_id": "ctv_abc"},
                      {"url": "https://a.example/x.m3u8"},
                      {"channels": [{"id": "c1"}]}):
            with self.subTest(**extra):
                self.assertTrue(events._is_today_fresh(
                    fixture(-90, **extra), NOW, 12, CONFIGURED_NO_LINK_GRACE))

    def test_a_fixture_that_has_not_started_is_untouched(self):
        self.assertTrue(events._is_today_fresh(
            fixture(20), NOW, 12, CONFIGURED_NO_LINK_GRACE))


class TheCardSaysWhichStateItIsInTests(unittest.TestCase):
    """The card was deliberately minimal, and that was right while everything
    on the tab was live. It stops being right the moment a fixture arrives 30
    minutes before kickoff, because a card that looks exactly like a live one
    and is not is telling the viewer something untrue."""

    APP = (ROOT / "site" / "assets" / "js" / "app.js").read_text(encoding="utf-8")
    CSS = (ROOT / "site" / "assets" / "css" / "event-channel-cards.css").read_text(
        encoding="utf-8"
    )

    def test_the_card_carries_a_state_badge(self):
        # The finalised card carries the state as the corner ribbon: red for
        # live, gold for a link still being looked for or a match about to
        # start. The state itself is still decided by todayCardState.
        self.assertIn("function todayCardState(item)", self.APP)
        self.assertIn("function todayRibbon(item)", self.APP)
        self.assertIn("'ribbon updating'", self.APP)

    def test_all_four_states_are_styled(self):
        for tone in ("live", "ready", "soon", "waiting"):
            self.assertIn(f".tm-state-{tone}", self.CSS)

    def test_ready_and_waiting_are_told_apart(self):
        """The difference matters to someone deciding whether to wait: a stream
        that is resolved, versus one still being looked for."""
        block = self.APP[self.APP.index("function todayCardState(item)"):]
        block = block[:block.index("\n}\n")]
        self.assertIn("isPlayable(item)", block)

    def test_the_headline_no_longer_calls_everything_live(self):
        """"27 Live" was true while the tab held only live matches. A viewer
        reading it and finding a countdown on the first card has been told
        something untrue by the one line that summarises the tab."""
        block = self.APP[self.APP.index("function setEventListCount()"):]
        block = block[:block.index("\nfunction ")]
        headline = block[block.index("setSidebarCount("):]
        headline = headline[:headline.index("return;")]
        # The headline names the tab; it does not claim anything about the
        # cards on it. Bengali, like every other line the viewer reads.
        self.assertIn("'আজকের ম্যাচ'", headline)
        self.assertNotIn("Live", headline,
                         "the headline still calls every card live")
        # Both counts move into the detail line, where they are true.
        self.assertIn("টি লাইভ", block)
        self.assertIn("টি ম্যাচ", block)


class TheClockOutranksASportPreferenceOnUpcomingTests(unittest.TestCase):
    """Sport ordering is an audience preference and it belongs on Today Match.
    On Upcoming it ranked above the clock, so a football match kicking off in
    five minutes sorted below every cricket fixture including tomorrow's - and
    the one question that tab answers is what is on next."""

    APP = (ROOT / "site" / "assets" / "js" / "app.js").read_text(encoding="utf-8")

    #: The flag was `sportBeatsTheClock` while sport ranked above status on
    #: Today Match. It no longer does - urgency decides first on both tabs and
    #: the sport preference orders matches inside a status - so the name says
    #: what it now controls.
    FLAG = "sportOrdersWithinStatus"

    def _sort_block(self):
        block = self.APP[self.APP.index(f"const {self.FLAG}"):]
        return block[:block.index("} else if (state.view === VIEW.MOVIE)")]

    def test_the_rule_is_split_by_tab(self):
        self.assertIn(f"const {self.FLAG} = state.view !== VIEW.UPCOMING;",
                      self.APP)

    def test_status_is_compared_before_any_sport_preference(self):
        # On Today Match a live football match used to sort below every cricket
        # fixture that had not started, because sport was asked first.
        block = self._sort_block()
        status = block.index("statusRank[eventUiStatus(a)]")
        first_sport = block.index("sportRank(a) - sportRank(b)")
        self.assertLess(status, first_sport,
                        "the sport preference is still asked before the status")

    def test_sport_orders_within_a_status_on_the_live_tab(self):
        block = self._sort_block()
        first = block.index("sportRank(a) - sportRank(b)")
        self.assertIn(f"if ({self.FLAG}) {{", block[:first])

    def test_the_clock_is_still_a_tie_break_for_sport_on_upcoming(self):
        self.assertIn(f"if (!{self.FLAG}) {{", self._sort_block())


class TappingAChannelOnAPhoneTests(unittest.TestCase):
    CSS = (ROOT / "site" / "assets" / "css" / "event-channel-cards.css").read_text(
        encoding="utf-8"
    )

    def test_the_small_screen_chip_is_no_longer_half_the_guidance(self):
        """25px with 9.5px text against Apple's 44px and Android's 48dp, and a
        mis-tap here switches the stream mid-match."""
        self.assertNotIn("min-height:25px", self.CSS)
        chip = [line for line in self.CSS.splitlines()
                if "event-channel-chip.tm-channel" in line and "min-height" in line]
        self.assertTrue(chip, "no sized rule for the channel chip")
        for line in chip:
            size = int(re.search(r"min-height:(\d+)px", line).group(1))
            self.assertGreaterEqual(size, 40, line.strip())

    def test_the_narrowest_phones_get_one_column_rather_than_smaller_text(self):
        self.assertIn("@media (max-width:420px)", self.CSS)
        narrowest = self.CSS[self.CSS.index("@media (max-width:420px)"):]
        self.assertIn("grid-template-columns:1fr", narrowest)


class ASingleChannelCardNamesItsBroadcasterTests(unittest.TestCase):
    """One channel renders one full-width button, in the chip the multi-channel
    strip already uses: play glyph, channel name, quality band."""

    APP = (ROOT / "site" / "assets" / "js" / "app.js").read_text(encoding="utf-8")
    CSS = (ROOT / "site" / "assets" / "css" / "event-channel-cards.css").read_text(
        encoding="utf-8"
    )

    def _minimal(self):
        # The minimal branch, sliced to where the full strip begins.
        block = self.APP[self.APP.index("if (minimal) {"):]
        return block[:block.index("const columns = Math.min(")]

    def test_one_channel_renders_a_button(self):
        block = self._minimal()
        self.assertIn("channels.length === 1", block)
        self.assertIn("tm-channel-solo", block)
        self.assertIn("<button", block)

    def test_it_reuses_the_existing_chip_class(self):
        """No new look - the owner asked for the existing style, reused."""
        self.assertIn("event-channel-chip tm-channel", self._minimal())

    def test_the_label_carries_the_play_glyph_and_the_quality(self):
        block = self._minimal()
        self.assertIn("\u25B6", block)
        self.assertIn("channelQualityBand(only)", block)

    def test_the_quality_band_reads_the_channel_own_stream(self):
        """The card-level copy can belong to a different channel once there is
        more than one."""
        band = self.APP[self.APP.index("function channelQualityBand(channel)"):]
        band = band.split(chr(10) + "}" + chr(10))[0]
        self.assertIn("channel?.streams", band)
        self.assertIn("movieQualityTitle", band)

    def test_the_button_spans_the_strip(self):
        self.assertIn(".event-channel-strip.tm-channels-one", self.CSS)
        one = self.CSS[self.CSS.index(".event-channel-strip.tm-channels-one"):]
        self.assertIn("grid-template-columns:1fr", one[:200])

    def test_two_or_more_channels_are_untouched(self):
        block = self._minimal()
        self.assertIn("const chips = channels.map((channel) => {", block)
        self.assertIn('class="event-channel-strip tm-channels"', block)



if __name__ == "__main__":
    unittest.main()
