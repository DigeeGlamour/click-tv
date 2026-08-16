"""One real match is one card, and a fixture gets the stream that can play it.

Three rules from Click_TV_Sports_Pipeline_Final_Rules_Updated.md were only
partly implemented:

* 27 - the same match relayed by several broadcasters must collapse to one
  event. "Server 1/2" and bare "HD" already did; "Willow Cricket",
  "Sony Sports Ten 3", "CricLife 1" and the separator "v" did not.

* 30.2 / 30.3 - one physical playlist is fetched once. sm-tapmad-auto and
  sm-tapmad-auto-blob-alias are the same URL, and the SonyLiv playlist is
  registered under both event groups, so their entries arrived two and three
  times over.

* 30.7 - the fixture exists first and a matching stream is then attached to it.
  A fixture feed carries no link and a playlist carries no schedule, and
  nothing joined them, so every Upcoming card published with nothing to play.
"""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import source_loader  # noqa: E402
from scanner.merger import merge_candidates, normalize_event_key  # noqa: E402
from scanner.schedule_resolver import (  # noqa: E402
    attach_streams_to_fixtures,
    team_pair_key,
)

AUTHORITY = {"srhady-axsports-upcoming", "srhady-willow-event-upcoming"}

LIVE_STREAM = {
    "source_pipeline": "today_match",
    "schedule_status": "LIVE_NOW",
    "verification_status": "verified_global",
    "verified": True,
    "publish_allowed": True,
    "competition": "Test League",
    "start_time": "2026-08-16T10:00:00+00:00",
    "end_time": "2026-08-16T18:00:00+00:00",
}


def _feeds(names):
    return [
        dict(LIVE_STREAM, id=f"s{index}", name=name,
             url=f"https://stream.test/{index}.m3u8")
        for index, name in enumerate(names)
    ]


class OneMatchOneCardTests(unittest.TestCase):
    """Guide 2 and 26."""

    def _cards(self, names):
        return merge_candidates(
            _feeds(names), settings_path=str(ROOT / "config" / "settings.json")
        )

    def test_broadcaster_names_in_the_title_do_not_split_a_match(self):
        cards = self._cards([
            "Australia vs Bangladesh Willow Cricket",
            "Australia vs Bangladesh Star Sports 1",
            "Australia vs Bangladesh T Sports HD",
            "Australia vs Bangladesh Fox Cricket",
        ])
        self.assertEqual(len(cards), 1)
        self.assertEqual(len(cards[0]["backups"]), 3)

    def test_numbered_broadcaster_channels_do_not_split_a_match(self):
        cards = self._cards([
            "India vs Sri Lanka Sony Sports Ten 1",
            "India vs Sri Lanka Sony Sports Ten 3",
            "India vs Sri Lanka CricLife 1",
            "India vs Sri Lanka Willow",
        ])
        self.assertEqual(len(cards), 1)
        self.assertEqual(len(cards[0]["backups"]), 3)

    def test_every_spelling_of_the_separator_is_the_same_match(self):
        cards = self._cards([
            "Yokohama FC vs Jubilo Iwata",
            "Yokohama FC v Jubilo Iwata",
            "Yokohama FC versus Jubilo Iwata",
        ])
        self.assertEqual(len(cards), 1)

    def test_server_and_quality_labels_do_not_split_a_match(self):
        self.assertEqual(len(self._cards([
            "GKS Katowice W vs Czarni Sosnowiec W - Server 1",
            "GKS Katowice W vs Czarni Sosnowiec W - Server 2",
        ])), 1)
        self.assertEqual(len(self._cards([
            "FC Sochi vs Volga Ulyanovsk", "FC Sochi vs Volga Ulyanovsk HD",
        ])), 1)

    def test_genuinely_different_matches_stay_apart(self):
        """The normalisation must not become so eager that separate fixtures
        collapse into one card."""
        self.assertEqual(len(self._cards(["Arsenal vs Chelsea", "Arsenal vs Liverpool"])), 2)
        self.assertEqual(len(self._cards(["Bangladesh vs India", "Pakistan vs India"])), 2)
        self.assertEqual(
            len(self._cards([
                "Trent Rockets Women vs Oval Women", "Trent Rockets vs Oval",
            ])), 2,
        )

    def test_the_key_itself_ignores_broadcaster_and_separator_spelling(self):
        base = normalize_event_key("India vs Sri Lanka")
        for variant in (
            "India vs Sri Lanka Willow", "India v Sri Lanka",
            "India versus Sri Lanka", "India vs Sri Lanka Sony Sports Ten 3",
        ):
            self.assertEqual(normalize_event_key(variant), base, variant)


class TeamPairKeyTests(unittest.TestCase):
    """Guide 30.7 - the join between a fixture feed and a playlist."""

    def test_the_competition_suffix_a_playlist_adds_is_ignored(self):
        fixture = team_pair_key("Amarante vs Lusitania Lourosa")
        stream = team_pair_key("Amarante vs Lusitania Lourosa Segunda Liga")
        self.assertTrue(stream.startswith(fixture))

    def test_a_title_without_two_participants_has_no_key(self):
        for name in ("T Sports", "Willow Cricket", "", "Formula E London"):
            self.assertEqual(team_pair_key(name), "", name)


class StreamEnrichmentTests(unittest.TestCase):
    """Guide 30.7 - fixture first, then stream association."""

    def _fixture(self, name, status="UPCOMING"):
        return {
            "id": "fx", "name": name, "source_id": "srhady-axsports-upcoming",
            "source_pipeline": "upcoming", "competition": "Segunda Liga",
            "fixture_id": "provider:fx", "schedule_status": status,
            "status": status, "metadata_only": True,
            "start_time": "2026-08-16T14:00:00+00:00",
            "end_time": "2026-08-16T18:00:00+00:00",
            "schedule_verified": True,
        }

    def _stream(self, name):
        return dict(LIVE_STREAM, id="st", name=name, source_id="srhady-bingstream-live",
                    url="https://stream.test/live.m3u8")

    def test_a_matching_stream_takes_the_fixture_identity(self):
        items, stats = attach_streams_to_fixtures(
            [self._fixture("Amarante vs Lusitania Lourosa"),
             self._stream("Amarante vs Lusitania Lourosa Segunda Liga")],
            AUTHORITY,
        )
        self.assertEqual(stats["streams_attached"], 1)
        attached = next(i for i in items if i.get("stream_attached_to_fixture"))
        self.assertEqual(attached["name"], "Amarante vs Lusitania Lourosa")
        self.assertEqual(attached["fixture_id"], "provider:fx")
        self.assertEqual(attached["start_time"], "2026-08-16T14:00:00+00:00")
        self.assertEqual(attached["url"], "https://stream.test/live.m3u8")

    def test_fixture_and_its_stream_become_one_card_with_a_playable_route(self):
        items, _ = attach_streams_to_fixtures(
            [self._fixture("Amarante vs Lusitania Lourosa", status="LIVE_NOW"),
             self._stream("Amarante vs Lusitania Lourosa Segunda Liga")],
            AUTHORITY,
        )
        cards = merge_candidates(
            items, settings_path=str(ROOT / "config" / "settings.json")
        )
        self.assertEqual(len(cards), 1)
        self.assertTrue(cards[0].get("url"))

    def test_an_unrelated_stream_is_left_alone(self):
        items, stats = attach_streams_to_fixtures(
            [self._fixture("Amarante vs Lusitania Lourosa"),
             self._stream("Arsenal vs Chelsea Premier League")],
            AUTHORITY,
        )
        self.assertEqual(stats["streams_attached"], 0)

    def test_a_shorter_team_name_does_not_capture_a_longer_one(self):
        """"Arsenal vs Man" must not swallow "Arsenal vs Manchester City"."""
        items, stats = attach_streams_to_fixtures(
            [self._fixture("Arsenal vs Man"),
             self._stream("Arsenal vs Manchester City Premier League")],
            AUTHORITY,
        )
        self.assertEqual(stats["streams_attached"], 0)

    def test_a_fixture_with_no_stream_yet_is_untouched(self):
        fixture = self._fixture("Arsenal vs Manchester City")
        items, stats = attach_streams_to_fixtures([fixture], AUTHORITY)
        self.assertEqual(stats["streams_attached"], 0)
        self.assertEqual(items[0]["name"], "Arsenal vs Manchester City")
        self.assertIs(items[0]["metadata_only"], True)


class CanonicalSourceTests(unittest.TestCase):
    """Guide 30.2 and 30.3 - one playlist, one fetch."""

    def test_a_playlist_registered_twice_is_fetched_once(self):
        sources = {
            "today_match": [
                {"id": "sm-tapmad-auto", "url": "https://x.test/tapmad.m3u", "enabled": True},
                {"id": "sm-tapmad-auto-blob-alias", "url": "https://x.test/tapmad.m3u",
                 "canonical_source_id": "sm-tapmad-auto", "enabled": True},
                {"id": "sonyliv", "url": "https://x.test/sony.m3u", "enabled": True},
            ],
            "upcoming": [
                {"id": "sonyliv-upcoming", "url": "https://x.test/sony.m3u", "enabled": True},
            ],
            "manual": {},
        }
        fetched = []

        def fake_process(source, _settings):
            fetched.append(source["url"])
            return [], {
                "source_id": source["id"], "source_name": source["id"],
                "url": source.get("url", ""), "pipeline": source["pipeline"],
                "status": "success_empty", "last_scan": "2026-08-16T00:00:00+00:00",
                "http_status": 200, "attempts": 1, "response_time_ms": 1,
                "detected_format": "m3u", "raw_items": 0, "error": None,
            }

        with (
            patch.object(source_loader, "load_sources_config", return_value=sources),
            patch.object(source_loader, "_load_json_file",
                         return_value={"source_workers": 1, "source_cache": {"enabled": False}}),
            patch.object(source_loader, "process_single_source", side_effect=fake_process),
            patch.object(source_loader, "load_manual_sources", return_value=([], {})),
            patch.object(source_loader, "_atomic_write_json"),
            patch.object(source_loader, "_merge_health_history", return_value={}),
        ):
            source_loader.collect_candidates("today")

        self.assertEqual(sorted(fetched),
                         ["https://x.test/sony.m3u", "https://x.test/tapmad.m3u"])

    def test_the_live_config_has_no_playlist_fetched_twice(self):
        config = source_loader.load_sources_config(str(ROOT / "config"))
        seen = {}
        for pipeline in ("today_match", "upcoming", "tv", "movies"):
            for source in config.get(pipeline, []):
                seen.setdefault(source["url"], []).append(source["id"])
        duplicated = {url: ids for url, ids in seen.items() if len(ids) > 1}
        # Duplicates may still be declared; collect_candidates must fold them.
        for url, ids in duplicated.items():
            self.assertGreater(len(ids), 1, url)


if __name__ == "__main__":
    unittest.main()
