"""Upcoming lists fixtures; the play button belongs to Today Match.

The intended shape: a fixture appears on Upcoming with its kickoff and nothing
to press, the scan starts hunting a link thirty minutes before that kickoff,
and a fixture that has one crosses to Today Match. Some feeds ship the fixture
and its stream in one record, hours early, which put a working play button on
the wrong tab - measured on 2026-09-02 at 08:38 UTC, ten of 122 Upcoming cards
carried a verified link for matches four to sixteen hours away.
"""
import unittest
from datetime import datetime, timedelta, timezone

from scanner.events import _hold_back_early_link
from scanner.event_lifecycle import DEFAULT_TODAY_ROUTING_MINUTES as WINDOW


def card(minutes_away, **extra):
    """A fixture with a link, `minutes_away` from kickoff."""
    row = {
        "name": "Real Madrid Vs Ajax",
        "competition": "UEFA Women's Champions League",
        "logo": "https://img.example/team.png",
        "start_time": (datetime.now(timezone.utc)
                       + timedelta(minutes=minutes_away)).isoformat(),
        "url": "https://rmtv.akamaized.net/hls/live/2043153/rmtv-es-web/master.m3u8",
        "header_profile": "android_tv",
        "proxy_mode": "direct_first",
        "stream_type": "hls",
        "playback_id": "ctv_7eea9c10ec4a6b6a9726e26860550d46",
        "channels": [{"name": "Real Madrid TV Spain"}, {"name": "RM TV"}],
        "backups": [{"url": "https://backup.example/x.m3u8"}],
        "channel_count": 2,
        "default_channel_id": "real-madrid-vs-ajax--rm-tv",
        "available_link_count": 2,
        "verification_status": "verified_global",
        "sport_type": "football",
    }
    row.update(extra)
    return row


class OutsideTheWindowTheLinkWaits(unittest.TestCase):
    def test_a_fixture_hours_away_publishes_without_its_stream(self):
        row = card(570)
        self.assertTrue(_hold_back_early_link(row, 570, WINDOW))
        self.assertEqual(row["url"], "")
        self.assertTrue(row["metadata_only"])
        self.assertEqual(row["available_link_count"], 0)
        self.assertEqual(row["verification_status"], "metadata_only")

    def test_every_playable_field_goes_together(self):
        # Leaving one behind is how a card ends up with a channel chip that
        # leads nowhere.
        row = card(570)
        _hold_back_early_link(row, 570, WINDOW)
        for field in ("channels", "backups", "channel_count",
                      "default_channel_id", "playback_id", "header_profile",
                      "proxy_mode", "stream_type"):
            with self.subTest(field=field):
                self.assertNotIn(field, row)

    def test_the_fixture_itself_is_untouched(self):
        row = card(570)
        _hold_back_early_link(row, 570, WINDOW)
        self.assertEqual(row["name"], "Real Madrid Vs Ajax")
        self.assertEqual(row["competition"], "UEFA Women's Champions League")
        self.assertEqual(row["logo"], "https://img.example/team.png")
        self.assertEqual(row["sport_type"], "football")
        self.assertTrue(row["start_time"])

    def test_it_says_the_link_is_waiting_rather_than_missing(self):
        row = card(570)
        _hold_back_early_link(row, 570, WINDOW)
        self.assertTrue(row["link_held_until_window"])


class InsideTheWindowTheLinkStays(unittest.TestCase):
    """The risk worth guarding: a card about to play must not lose its stream."""

    def test_a_fixture_inside_the_window_keeps_everything(self):
        row = card(WINDOW - 1)
        self.assertFalse(_hold_back_early_link(row, WINDOW - 1, WINDOW))
        self.assertTrue(row["url"])
        self.assertEqual(len(row["channels"]), 2)
        self.assertEqual(row["available_link_count"], 2)

    def test_the_boundary_belongs_to_the_window(self):
        row = card(WINDOW)
        self.assertFalse(_hold_back_early_link(row, WINDOW, WINDOW))
        self.assertTrue(row["url"])

    def test_a_match_already_underway_keeps_its_stream(self):
        row = card(-45)
        self.assertFalse(_hold_back_early_link(row, -45, WINDOW))
        self.assertTrue(row["url"])

    def test_an_unknown_kickoff_is_left_alone(self):
        # No clock means no window to be outside of.
        row = card(570, start_time="")
        self.assertFalse(_hold_back_early_link(row, None, WINDOW))
        self.assertTrue(row["url"])
        self.assertEqual(len(row["channels"]), 2)


class ACardWithNoLinkIsNotDisturbed(unittest.TestCase):
    def test_a_metadata_only_fixture_is_returned_unchanged(self):
        row = {"name": "Rishikesh Dragons vs Herbertpur Knight Riders",
               "url": "", "metadata_only": True, "available_link_count": 0,
               "verification_status": "metadata_only"}
        before = dict(row)
        self.assertFalse(_hold_back_early_link(row, 900, WINDOW))
        self.assertEqual(row, before)

    def test_a_fixture_with_channels_but_no_url_is_still_held(self):
        # Six of the ten had an empty url and named channels, which renders a
        # chip that leads nowhere.
        row = card(900, url="")
        self.assertTrue(_hold_back_early_link(row, 900, WINDOW))
        self.assertNotIn("channels", row)


class TheShapeMatchesTheCardsAlreadyPublished(unittest.TestCase):
    def test_a_held_card_looks_like_the_other_upcoming_cards(self):
        # 112 of 122 Upcoming cards already had exactly this shape; a held-back
        # card has to be indistinguishable from them or the front end has two
        # cases to render instead of one.
        row = card(570)
        _hold_back_early_link(row, 570, WINDOW)
        self.assertEqual(
            {"url": row["url"], "metadata_only": row["metadata_only"],
             "available_link_count": row["available_link_count"],
             "verification_status": row["verification_status"]},
            {"url": "", "metadata_only": True, "available_link_count": 0,
             "verification_status": "metadata_only"},
        )


if __name__ == "__main__":
    unittest.main()
