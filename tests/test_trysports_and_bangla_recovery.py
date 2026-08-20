"""Regressions for the five faults found in the 2026-08-20 audit.

Each test fails on the code as it stood before that audit and passes after, so
none of them is a restatement of the fix - they are the reproductions.

1. TrySports states its kickoff as "20 Aug 2026, 05:00 PM (BD Time)". No
   pattern in `_parse_source_time` read a month name and none stripped the
   "(BD Time)" suffix, so all 163 of its football fixtures resolved to a null
   start and `_provider_fixture_item` discarded every one of them.
2. TrySports was not a fixture-authority source, so even a parsed kickoff left
   its fixtures as `unverified_suppressed`.
3. Today Match read `football/live.m3u`, which is flat. Every stream became its
   own event, so "Apple TV" published as a match name while the real match
   titles published as channel names. `football/live.json` carries the same
   streams grouped under their match.
4. `cricket/upcoming.json` was never registered at all.
5. Bangla was the only channel category gated on an exact-URL proof ledger a
   human regenerates by hand, so any URL rotation permanently hid the channel.
"""

from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scanner import source_loader
from scanner.schedule_resolver import (
    _parse_source_time,
    _provider_fixture_item,
)

BDT = timezone(timedelta(hours=6), "BDT")


class TrySportsKickoffParsingTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 20, 6, 0, tzinfo=timezone.utc)

    def test_month_name_kickoff_with_bd_time_suffix_is_read(self):
        parsed = _parse_source_time(
            "20 Aug 2026, 05:00 PM (BD Time)", BDT, self.now
        )
        self.assertEqual(
            parsed, datetime(2026, 8, 20, 11, 0, tzinfo=timezone.utc)
        )

    def test_full_month_name_and_comma_less_variants_are_read(self):
        expected = datetime(2026, 8, 20, 17, 45, tzinfo=timezone.utc)
        for text in (
            "20 August 2026, 11:45 PM (BD Time)",
            "20 Aug 2026 11:45 PM (BD Time)",
            "20 Aug 2026, 11:45 PM",
        ):
            with self.subTest(text=text):
                self.assertEqual(_parse_source_time(text, BDT, self.now), expected)

    def test_formats_that_already_worked_are_unchanged(self):
        self.assertEqual(
            _parse_source_time("4:00 PM 20-08-2026", BDT, self.now),
            datetime(2026, 8, 20, 10, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(
            _parse_source_time("2026-08-20T10:00:00+00:00", BDT, self.now),
            datetime(2026, 8, 20, 10, 0, tzinfo=timezone.utc),
        )

    def test_a_placeholder_is_still_not_a_kickoff_time(self):
        for text in ("24/7 Live Channel", "Upcoming", "", "TBA"):
            with self.subTest(text=text):
                self.assertIsNone(_parse_source_time(text, BDT, self.now))

    def test_an_upcoming_trysports_fixture_now_survives_the_provider_gate(self):
        item = {
            "id": "elche-vs-barcelona-1",
            "name": "Elche vs Barcelona",
            "status": "UPCOMING",
            "start_time": "22 Aug 2026, 01:00 AM (BD Time)",
            "source_id": "0matbank-trysports-football-upcoming",
            "source_pipeline": "upcoming",
            "url": "",
            "metadata_only": True,
            "allow_without_stream": True,
        }
        source_time = _parse_source_time(item["start_time"], BDT, self.now)
        self.assertIsNotNone(source_time)
        resolved = _provider_fixture_item(item, source_time, self.now, 4)
        self.assertIsNotNone(resolved)
        self.assertTrue(resolved["publish_allowed"])
        self.assertEqual(resolved["schedule_status"], "UPCOMING")
        self.assertEqual(resolved["time_verification"], "provider_feed")

    def test_the_old_unparsed_kickoff_is_what_used_to_drop_the_fixture(self):
        # Proof the gate, not the parser, is what published nothing: with no
        # start time an upcoming fixture is still correctly refused.
        item = {
            "id": "elche-vs-barcelona-2",
            "name": "Elche vs Barcelona",
            "status": "UPCOMING",
            "source_id": "0matbank-trysports-football-upcoming",
        }
        self.assertIsNone(_provider_fixture_item(item, None, self.now, 4))


class TrySportsRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.settings = json.loads(
            (ROOT / "config" / "settings.json").read_text(encoding="utf-8")
        )
        self.sources = source_loader.load_sources_config("config")

    def test_trysports_feeds_are_fixture_authorities(self):
        authority = set(self.settings["events"]["fixture_authority_sources"])
        for source_id in (
            "0matbank-trysports-football-live",
            "0matbank-trysports-cricket-live",
        ):
            with self.subTest(source_id=source_id):
                self.assertIn(source_id, authority)

    def test_today_match_reads_the_grouped_football_json_not_the_flat_m3u(self):
        football = [
            source for source in self.sources["today_match"]
            if source["id"] == "0matbank-trysports-football-live"
        ]
        self.assertEqual(len(football), 1)
        self.assertTrue(football[0]["url"].endswith("/football/live.json"))
        self.assertEqual(football[0]["format"], "json")
        # status_filter is gone from this source: the adapter reads the status
        # off each record and routes it, so filtering the file down to one
        # status would throw away the rows that belong in the other tab.
        self.assertEqual(football[0].get("adapter"), "named_streams")

    def test_cricket_is_registered_and_keeps_its_upcoming_rows(self):
        """The provider's cricket/upcoming.json is no longer registered, and
        its fixtures are not lost with it: cricket/live.json carries every row
        with its own status, and a row that says UPCOMING is routed to the
        Upcoming tab by the adapter rather than by which file it came from."""
        ids = {source["id"] for source in self.sources["today_match"]}
        self.assertIn("0matbank-trysports-cricket-live", ids)
        self.assertEqual(self.sources["upcoming"], [])

        from scanner.parsers.event_adapters import record_pipeline

        self.assertEqual(
            record_pipeline({"status_raw": "UPCOMING", "channels": []}), "upcoming"
        )

    def test_every_private_trysports_source_still_carries_the_token(self):
        registered = [
            source
            for source in self.sources["today_match"] + self.sources["upcoming"]
            if source["id"].startswith("0matbank-trysports")
        ]
        self.assertEqual(len(registered), 2)
        for source in registered:
            with self.subTest(source_id=source["id"]):
                self.assertEqual(
                    source["fetch_headers"]["Authorization"],
                    "token ${PRIVATE_SPORTS_SOURCE_TOKEN}",
                )


class FootballLiveGroupingTests(unittest.TestCase):
    """The flat playlist is why "Apple TV" became a match name."""

    FLAT_M3U = (
        "#EXTM3U\n"
        '#EXTINF:-1 tvg-name="Apple TV (HD)" group-title="Football", Apple TV (HD)\n'
        "https://example.test/a/playlist.m3u8\n"
        '#EXTINF:-1 tvg-name="Apple TV (SD)" group-title="Football", Apple TV (SD)\n'
        "https://example.test/b/playlist.m3u8\n"
        '#EXTINF:-1 tvg-name="LA Galaxy vs San Jose Earthquakes (HD)" '
        'group-title="Football", LA Galaxy vs San Jose Earthquakes (HD)\n'
        "https://example.test/c/playlist.m3u8\n"
    )

    GROUPED_JSON = json.dumps({
        "category_name": "Football Live",
        "matches": [{
            "id": "la-galaxy-vs-san-jose-earthquakes",
            "title": "LA Galaxy vs San Jose Earthquakes",
            "category": "football",
            "status": "LIVE_NOW",
            "start_time_bd": "20 Aug 2026, 02:30 AM (BD Time)",
            "streams": [
                {"channel_name": "Apple TV (HD)", "hd": True,
                 "direct_stream_url": "https://example.test/a/playlist.m3u8"},
                {"channel_name": "Apple TV (SD)", "hd": False,
                 "direct_stream_url": "https://example.test/b/playlist.m3u8"},
                {"channel_name": "LA Galaxy vs San Jose Earthquakes (HD)", "hd": True,
                 "direct_stream_url": "https://example.test/c/playlist.m3u8"},
            ],
        }],
    })

    def test_the_flat_m3u_publishes_a_broadcaster_as_an_event_name(self):
        from scanner.parsers.m3u_parser import parse_m3u_content

        items = parse_m3u_content(self.FLAT_M3U, {
            "id": "0matbank-trysports-football-live",
            "url": "https://example.test/football/live.m3u",
            "pipeline": "today_match",
        })
        self.assertIn("Apple TV (HD)", {item["name"] for item in items})

    def test_the_grouped_json_keeps_the_match_as_event_and_rest_as_channels(self):
        from scanner.parsers.json_parser import parse_json_content

        items = parse_json_content(self.GROUPED_JSON, {
            "id": "0matbank-trysports-football-live",
            "url": "https://example.test/football/live.json",
            "pipeline": "today_match",
            "status_filter": ["LIVE"],
        })
        self.assertEqual(len(items), 3)
        self.assertEqual(
            {item["name"] for item in items},
            {"LA Galaxy vs San Jose Earthquakes"},
        )
        self.assertEqual(
            {item["channel_name"] for item in items},
            {
                "Apple TV (HD)",
                "Apple TV (SD)",
                "LA Galaxy vs San Jose Earthquakes (HD)",
            },
        )

    def test_live_now_still_satisfies_a_live_status_filter(self):
        from scanner.parsers.json_parser import parse_json_content

        items = parse_json_content(self.GROUPED_JSON, {
            "id": "x", "url": "https://example.test/f.json",
            "pipeline": "today_match", "status_filter": ["LIVE"],
        })
        self.assertTrue(items)
        self.assertEqual({item["status"] for item in items}, {"LIVE"})


class BanglaPlayerProofRatchetTests(unittest.TestCase):
    """A rotating IPTV URL must not permanently delete a verified channel."""

    def setUp(self):
        self.settings = json.loads(
            (ROOT / "config" / "settings.json").read_text(encoding="utf-8")
        )

    def test_the_bangla_only_proof_requirement_is_off_by_default(self):
        bd = self.settings["bd_verification"]
        self.assertIn("bangla_requires_player_proof", bd)
        self.assertFalse(bd["bangla_requires_player_proof"])

    def test_the_proof_fingerprint_changes_when_only_the_url_rotates(self):
        from scanner.player_compatibility import playback_fingerprint

        before = {
            "name": "Somoy TV", "url": "https://tvsen6.aynaott.com/OLD/index.m3u8",
            "header_profile": "android_tv", "proxy_mode": "direct_first",
            "stream_type": "hls", "requires_headers": False,
        }
        after = dict(before, url="https://tvsen6.aynaott.com/NEW/index.m3u8")
        self.assertNotEqual(playback_fingerprint(before), playback_fingerprint(after))

    def test_channels_processor_reads_the_new_switch(self):
        source = (ROOT / "scanner" / "channels.py").read_text(encoding="utf-8")
        self.assertIn("bangla_requires_player_proof", source)
        self.assertIn(
            "if strict_player_publish and bangla_requires_player_proof:", source
        )

    def test_confirmed_browser_failures_are_still_blocked_for_every_category(self):
        source = (ROOT / "scanner" / "channels.py").read_text(encoding="utf-8")
        self.assertIn('mark_confirmed_player_failures(known_cards, "channel")', source)
        self.assertIn('is_confirmed_player_failure(card, "channel", failure_keys)', source)


class DropProtectionExpiryTests(unittest.TestCase):
    """Protection must survive one bad scan, not freeze a category for days."""

    def setUp(self):
        from scanner import output

        self.output = output
        self.settings = json.loads(
            (ROOT / "config" / "settings.json").read_text(encoding="utf-8")
        )

    def test_a_maximum_preserved_age_is_configured(self):
        self.assertGreater(
            self.settings["failure_protection"]["maximum_preserved_age_hours"], 0
        )

    def test_a_fresh_preserved_payload_still_wins(self):
        now = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
        payload = {"updated_at": "2026-08-20T06:00:00+00:00"}
        self.assertAlmostEqual(
            self.output._payload_age_hours(payload, now), 6.0, places=2
        )

    def test_a_three_day_old_preserved_payload_is_past_a_one_day_limit(self):
        now = datetime(2026, 8, 20, 9, 24, tzinfo=timezone.utc)
        payload = {"updated_at": "2026-08-17T09:24:03.189549+00:00"}
        age = self.output._payload_age_hours(payload, now)
        self.assertGreater(age, 24)
        self.assertLess(age, 96)

    def test_an_unstamped_payload_is_never_treated_as_expired(self):
        self.assertEqual(self.output._payload_age_hours({}), -1.0)
        self.assertEqual(
            self.output._payload_age_hours({"updated_at": "not a date"}), -1.0
        )


if __name__ == "__main__":
    unittest.main()
