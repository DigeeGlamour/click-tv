"""`html.movie-playback-context` means the player owns the page, not "a movie".

The browse shell deliberately gives up its width cap and pays the centring
back as left padding, so the category rail's scroll handle reaches the true
right edge of the window. That whole block in `site/assets/css/app.css` is
written

    html:not(.movie-playback-context) body.movie-portal  .youtube-layout...
    html:not(.movie-playback-context) body.movie-detail-view .youtube-layout...

and its own comment says "Desktop only, and never during playback".

The flag was set from `isMoviePlaybackContext()` alone, which is true for the
whole Movie section - browse included, because it reads
`state.view === VIEW.MOVIE`. Nothing cleared it on the way back, and the only
two callers are inside the player, so in practice the class meant "a movie
has been played at some point this session". Once it was on, the browse shell
lost the uncapped rule and fell back to the 1580px / 2040px cap.

Measured in Chromium against the real page and the real catalogue:

    viewport   browse   playback   after Back to Movies
      1440      1440      1440       1440   (cap wider than the window)
      1920      1920      1920       1824   (-96)
      2560      2560      2560       2040   (-520)

That is the squeeze the owner reported on a 2K monitor: the page narrowed on
the way back to the view it had just left. 1440 hid it completely, which is
why it survived every desktop check.

The portal and the detail view own the page exactly when the player does not
- `setMoviePortalMode` is what takes Movie Home off screen when Play takes the
page - so the body is the honest source for the second half of the question.
After the fix all three widths return to the browse width exactly.
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

APP_JS = (ROOT / "site" / "assets" / "js" / "app.js").read_text(encoding="utf-8")
APP_CSS = (ROOT / "site" / "assets" / "css" / "app.css").read_text(encoding="utf-8")


class TheFlagAsksWhetherThePlayerOwnsThePage(unittest.TestCase):
    def test_there_is_one_place_that_sets_it(self):
        toggles = re.findall(
            r"classList\.toggle\(\s*['\"]movie-playback-context['\"]", APP_JS)
        self.assertEqual(len(toggles), 1, "exactly one writer for this flag")

    def test_it_is_not_set_from_the_section_alone(self):
        self.assertNotIn(
            "classList.toggle('movie-playback-context', isMovie)", APP_JS)

    def test_the_body_decides_whether_the_player_owns_the_page(self):
        body = APP_JS[APP_JS.index("function syncMoviePlaybackContextClass("):]
        body = body[:body.index("\n}")]
        self.assertIn("movie-portal", body)
        self.assertIn("movie-detail-view", body)
        self.assertIn("isMovie", body)

    def test_the_helper_defaults_to_the_live_predicate(self):
        """Called from the portal with no argument, it must still work."""
        self.assertIn(
            "function syncMoviePlaybackContextClass(isMovie = isMoviePlaybackContext())",
            APP_JS,
        )


class TheWayBackClearsIt(unittest.TestCase):
    """The player is the only thing that ever called the updater, so coming
    back had to clear the flag itself or it would never be cleared."""

    def test_the_portal_syncs_when_it_takes_the_page(self):
        body = APP_JS[APP_JS.index("function setMoviePortalMode(on) {"):]
        body = body[:body.index("\n}")]
        self.assertIn("if (on) {", body)
        self.assertIn("syncMoviePlaybackContextClass();", body)

    def test_the_detail_view_syncs_too(self):
        body = APP_JS[APP_JS.index("function setSeriesDetailMode(active) {"):]
        body = body[:body.index("\n}")]
        self.assertIn("syncMoviePlaybackContextClass();", body)

    def test_the_portal_still_puts_its_furniture_away_when_it_leaves(self):
        """The early return on the `on` branch must not skip the teardown
        that runs when the portal is switched OFF."""
        body = APP_JS[APP_JS.index("function setMoviePortalMode(on) {"):]
        body = body[:body.index("\n}")]
        self.assertIn("hideMovieHeroPanel();", body)
        self.assertIn("hideContinueWatchingRow();", body)
        self.assertIn("setMovieGenreBarVisible(false);", body)


class TheStylesheetStillAsksTheQuestionThisAnswers(unittest.TestCase):
    """If the CSS stops keying off the flag this test should be revisited
    rather than silently passing."""

    def test_the_uncapped_browse_shell_is_still_guarded_by_the_flag(self):
        self.assertIn(
            "html:not(.movie-playback-context) body.movie-portal "
            ".youtube-layout.final-shell:has(.sidebar-section.side-panel)",
            APP_CSS,
        )

    def test_the_playback_shell_is_still_scoped_by_the_body(self):
        self.assertIn(
            "html.movie-playback-context body:not(.movie-portal):not(.movie-detail-view) "
            ".youtube-layout.final-shell",
            APP_CSS,
        )

    def test_the_capped_browse_rule_is_still_there_for_narrow_desktops(self):
        self.assertIn(
            "body.movie-portal .youtube-layout.final-shell:has(.sidebar-section.side-panel)",
            APP_CSS,
        )


if __name__ == "__main__":
    unittest.main()
