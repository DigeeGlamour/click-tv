"""Click_TV_Live_Sports_Advanced_Fix_Requirements_FINAL.md - the requirements
that live in the workflow and in the browser bundle.

These are contract tests: they assert the behaviour each requirement asks for is
present in the shipping files, and that the hard locks were respected. The
runtime behaviour itself is exercised by the Playwright suites.
"""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")



def _cron_minute_interval(cron: str) -> int:
    """Minutes between firings of a cron whose minute field is a list or step.

    Written because these tests used to pin the literal cron string, and the
    requirement is a frequency, not a spelling. The crons were moved off :00
    and */5 - the busiest minutes on GitHub's shared scheduler, where a run
    that cannot start is dropped rather than queued - and every one of these
    tests failed on an offset that changed nothing about how often it runs.
    """
    minute = cron.split()[0]
    if minute.startswith("*/"):
        return int(minute[2:])
    if "/" in minute:
        return int(minute.split("/")[1])
    if "," in minute:
        points = sorted(int(p) for p in minute.split(","))
        gaps = {b - a for a, b in zip(points, points[1:])}
        gaps.add(60 - points[-1] + points[0])
        return min(gaps)
    return 60


class Requirement4ScanArchitecture(unittest.TestCase):
    """Today every 20 minutes; Upcoming only targeted, 15 minutes ahead."""

    @classmethod
    def setUpClass(cls):
        cls.workflow = read(".github/workflows/scan.yml")
        cls.scan = read("scan.py")
        cls.events = read("scanner/events.py")
        cls.output = read("scanner/output.py")

    def test_today_match_runs_every_twenty_minutes(self):
        crons = re.findall(r'- cron: "([^"]+)"', self.workflow)
        every_20 = [c for c in crons if _cron_minute_interval(c) == 20]
        self.assertTrue(every_20, "no cron fires every 20 minutes")
        self.assertNotIn('- cron: "2,17,32,47 * * * *"', self.workflow)

    def test_a_frequent_trigger_drives_the_targeted_upcoming_scan(self):
        crons = re.findall(r'- cron: "([^"]+)"', self.workflow)
        every_5 = [c for c in crons if _cron_minute_interval(c) == 5]
        self.assertTrue(every_5, "no cron fires every 5 minutes")
        self.assertIn('MODE="upcoming-targeted"', self.workflow)

    def test_the_targeted_mode_is_a_real_scanner_mode(self):
        self.assertIn('"upcoming-targeted"', self.scan)
        self.assertIn("targeted_window_minutes=targeted_window", self.scan)
        self.assertIn("targeted_window_minutes: int = 0", self.events)

    def test_the_window_is_fifteen_minutes(self):
        """The literal lives in scanner/targeted_scan.py now, because the
        window and the resolved-fixture ledger belong to the same module."""
        self.assertIn("DEFAULT_WINDOW_MINUTES = 15", read("scanner/targeted_scan.py"))
        self.assertIn("TARGETED_WINDOW_MINUTES", self.scan)

    def test_the_targeted_mode_publishes_as_upcoming_not_as_everything(self):
        """Without this alias the mode falls through to "all" and republishes
        every category from a partial pool - exactly the data loss that
        requirements 4 and 15 exist to prevent."""
        self.assertIn('"upcoming-targeted": "upcoming"', self.output)


class Requirement7And8And14PlaybackContinuity(unittest.TestCase):
    """Playback is pinned, the list is diffed, the playing stream is sticky."""

    @classmethod
    def setUpClass(cls):
        cls.app = read("site/assets/js/app.js")

    def _body(self, name: str) -> str:
        return self.app.split(f"function {name}(")[1].split("\nfunction ")[0]

    def test_the_session_is_pinned_when_playback_starts(self):
        self.assertIn("function pinPlaybackSession(", self.app)
        self.assertIn("pinPlaybackSession(item);", self.app.split("async function startPlayback(")[1][:600])

    def test_a_refresh_preserves_the_session_and_diffs_the_list(self):
        refresh = self.app.split("async function refreshActiveEventCatalogue(")[1].split("\nfunction ")[0]
        self.assertIn("preservePlayingSession(", refresh)
        self.assertIn("reconcileEventCards()", refresh)

    def test_the_playing_card_keeps_its_dom_node(self):
        reconcile = self._body("reconcileEventCards")
        self.assertIn("isPinnedSession(item)", reconcile)
        self.assertIn("target.appendChild(previous)", reconcile)

    def test_a_healthy_playing_stream_is_not_swapped(self):
        preserve = self._body("preservePlayingSession")
        self.assertIn("match.url = pinned.url", preserve)
        self.assertIn("_pinned_primary", preserve)

    def test_an_event_that_left_the_catalogue_keeps_its_card(self):
        self.assertIn("_carried_pinned_session", self._body("preservePlayingSession"))


class Requirement9And10Playback(unittest.TestCase):
    """Low-resolution-first startup, and heat mitigations that remove nothing."""

    @classmethod
    def setUpClass(cls):
        cls.app = read("site/assets/js/app.js")
        cls.css = read("site/assets/css/event-cards.css")

    def test_live_playback_starts_at_the_lowest_level(self):
        self.assertIn("startLevel: isMovie ? -1 : 0", self.app)

    def test_mobile_starts_low_and_desktop_starts_mid(self):
        stages = self.app.split("function liveStartupQualityStages(")[1].split("\nfunction ")[0]
        self.assertIn("[360, 480, 720]", stages)
        self.assertIn("[480, 720, 1080]", stages)

    def test_manual_quality_control_still_exists(self):
        self.assertIn("state.selectedManualQuality", self.app)
        self.assertIn("buildQualityMenu(", self.app)

    def test_background_work_stops_behind_a_hidden_tab(self):
        for name in ("refreshEventCardsForClock", "refreshActiveEventCatalogue"):
            body = self.app.split(f"function {name}(")[1][:420]
            self.assertIn("document.hidden", body, name)

    def test_decorative_effects_stand_down_during_playback(self):
        self.assertIn("markPlaybackActive(", self.app)
        self.assertIn("body.playback-active", self.css)

    def test_no_feature_was_removed_to_achieve_it(self):
        """Requirement 10 is explicit that nothing may be deleted."""
        for feature in ("marquee-text", "playing-equalizer", "pulse-dot", "event-sport-badge"):
            self.assertIn(feature, self.css)


class Requirement11And12And13CardUi(unittest.TestCase):
    """Sport order, one Bangla status, artwork inside a safe area."""

    @classmethod
    def setUpClass(cls):
        cls.app = read("site/assets/js/app.js")
        cls.css = read("site/assets/css/event-cards.css")

    def test_the_ui_sorts_cricket_then_football_then_the_rest(self):
        sort_body = self.app.split("function applyFilterAndSort(")[1].split("\nfunction ")[0]
        self.assertIn("sportRank", sort_body)
        self.assertIn("'cricket'", sort_body)
        self.assertIn("'football'", sort_body)

    def test_the_filter_menu_uses_the_same_order(self):
        counts = self.app.split("function eventSportCounts(")[1].split("\nfunction ")[0]
        self.assertIn("cricket", counts)
        self.assertIn("football", counts)

    def test_the_countdown_speaks_bangla_with_bengali_numerals(self):
        for token in ("শুরু হবে", "মিনিট পর", "ঘণ্টা", "BANGLA_DIGITS"):
            self.assertIn(token, self.app)

    def test_an_upcoming_card_carries_one_status_not_two(self):
        card = self.app.split("function createEventCard(")[1].split("\nfunction ")[0]
        self.assertIn("countdown || statusLabel", card)

    def test_the_metadata_row_is_one_compact_line(self):
        self.assertIn("function eventMetaRowTextBn(", self.app)
        self.assertIn("স্ট্রিমের অপেক্ষায়", self.app)

    def test_artwork_is_contained_in_a_padded_safe_area(self):
        self.assertIn("object-fit:contain!important", self.css)
        # The Today Match v2 poster is the one deliberate exception: a full-
        # width banner is meant to fill its frame the way the supplied
        # reference does, not letterbox like the small locked-row tile this
        # rule protects. Scoped strictly to .tm-poster, so the original
        # small-tile artwork (event-card-art, the whole Upcoming row) still
        # never crops.
        self.assertNotIn("event-card-art img{\n  width:100%!important;height:100%!important;object-fit:cover", self.css)
        self.assertIn(".tm-poster img{", self.css)

    def test_the_artwork_fallback_chain_is_intact(self):
        self.assertIn("event-art-versus", self.css)
        self.assertIn("event-art-fallback", self.css)
        self.assertIn("function eventArtFallbackHtml(", self.app)


class HardLocks(unittest.TestCase):
    """The player and the page frame stay exactly where they were."""

    @classmethod
    def setUpClass(cls):
        cls.sheets = {
            "event-cards.css": read("site/assets/css/event-cards.css"),
            "smart-filter.css": read("site/assets/css/smart-filter.css"),
        }

    def test_no_new_stylesheet_touches_the_player_or_the_frame(self):
        forbidden = (
            ".video-container", "#videoPlayer", "#videoContainer", ".video-section",
            ".youtube-layout", ".app-header", ".desktop-category-rail",
            ".final-main-nav", ".now-playing-bar",
        )
        for name, sheet in self.sheets.items():
            for selector in forbidden:
                self.assertNotIn(selector, sheet, f"{name} must not restyle {selector}")

    def test_the_player_controls_are_all_still_in_the_page(self):
        index = read("site/index.html")
        for element in (
            'id="videoPlayer"', 'id="playerControls"', 'id="qualityBtn"',
            'id="networkBtn"', 'id="fullscreenBtn"', 'id="muteBtn"',
        ):
            self.assertIn(element, index)


if __name__ == "__main__":
    unittest.main()
