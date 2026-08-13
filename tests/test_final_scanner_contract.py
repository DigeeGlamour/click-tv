import json
import os
import tempfile
import unittest
from datetime import timedelta, timezone
from pathlib import Path

from scanner.events import _parse_datetime
from scanner.merger import merge_candidates, rank_and_select_streams
from scanner.movies import _merge_manual_over_discovered
from scanner.parsers.json_parser import parse_json_content
from scanner.planner import plan_candidates
from scanner.playback_profiles import PlaybackProfileCollector
from scanner.verifier import _apply_resolution_policy


class FinalScannerContractTests(unittest.TestCase):
    def test_final_source_registry_contains_only_agreed_remote_sources(self):
        payload = json.loads(Path("config/sources.json").read_text(encoding="utf-8"))
        self.assertEqual(len(payload["upcoming"]), 5)
        self.assertEqual(len(payload["today_match"]), 6)
        self.assertEqual(len(payload["tv"]), 11)
        self.assertEqual(len(payload["movies"]), 2)
        self.assertEqual(
            {entry["id"] for entry in payload["movies"]},
            {"sm-movie-combined", "bollywood-movies-collector"},
        )
        self.assertEqual(payload["tv"][1]["id"], "sm-roarzone-auto-update")
        self.assertEqual(payload["tv"][1]["priority"], 148)
        self.assertEqual(
            payload["tv"][1]["url"],
            "https://raw.githubusercontent.com/sm-monirulislam/RoarZone-Auto-Update-playlist/refs/heads/main/RoarZone.m3u",
        )

    def test_same_url_with_different_cookie_or_drm_survives(self):
        base = {
            "name": "Channel One",
            "url": "https://example.test/live.m3u8?token=one",
            "source_pipeline": "tv",
            "verified": True,
            "publish_allowed": True,
            "verification_status": "verified_global",
            "resolution_height": 1080,
        }
        first = {**base, "source_id": "one", "headers": {"Cookie": "session=a"}}
        second = {**base, "source_id": "two", "headers": {"Cookie": "session=b"}}
        third = {**base, "source_id": "three", "drm": {"license_type": "clearkey", "license_key": "kid:key"}}
        primary, backups = rank_and_select_streams([first, second, third])
        self.assertIsNotNone(primary)
        self.assertEqual(len(backups), 2)

    def test_playback_ids_include_credential_values(self):
        collector = PlaybackProfileCollector("channels", "2026-08-09T00:00:00+00:00")
        a = collector.sanitize_item({"url": "https://example.test/live.m3u8", "headers": {"Cookie": "a"}})
        b = collector.sanitize_item({"url": "https://example.test/live.m3u8", "headers": {"Cookie": "b"}})
        self.assertNotEqual(a["playback_id"], b["playback_id"])
        self.assertEqual(len(collector.records), 2)

    def test_720p_minimum_applies_to_tv_movie_and_events(self):
        settings = {
            "resolution": {
                "tv_minimum_height": 720,
                "movie_minimum_height": 720,
                "event_minimum_height": 720,
                "allow_unknown_tv_resolution": False,
                "allow_unknown_movie_resolution": False,
                "allow_unknown_event_resolution": False,
                "manual_can_override_resolution": False,
                "preserve_working_bd_below_minimum": False,
                "preserve_unknown_working_tv": False,
            }
        }
        for pipeline in ("tv", "movies", "today_match", "upcoming"):
            accepted, status, _ = _apply_resolution_policy({"source_pipeline": pipeline}, settings, 480)
            self.assertFalse(accepted, pipeline)
            self.assertEqual(status, "rejected_low_quality")
            accepted, status, _ = _apply_resolution_policy({"source_pipeline": pipeline}, settings, 0)
            self.assertFalse(accepted, pipeline)
            self.assertEqual(status, "quarantine")
            accepted, _, _ = _apply_resolution_policy({"source_pipeline": pipeline}, settings, 720)
            self.assertTrue(accepted, pipeline)

    def test_merger_never_publishes_unknown_resolution_pending_stream(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "settings.json"
            settings_path.write_text(
                json.dumps({"resolution": {"tv_minimum_height": 720}}),
                encoding="utf-8",
            )
            cards = merge_candidates(
                [
                    {
                        "id": "unknown",
                        "name": "Unknown Quality",
                        "url": "https://example.test/live.m3u8",
                        "source_pipeline": "tv",
                        "category": "Sports",
                        "verification_status": "geo_pending",
                        "publish_allowed": True,
                    }
                ],
                settings_path=str(settings_path),
            )
            self.assertEqual(cards, [])

    def test_willow_dynamic_server_maps_and_time_only_are_supported(self):
        content = json.dumps(
            {
                "Matches": [
                    {
                        "match_id": "abc",
                        "title": "Team A vs Team B",
                        "status": "UPCOMING",
                        "time": "Live at 11 PM BDT",
                        "stream_url_alpha": {
                            "Amazon Server": "https://example.test/a.mpd",
                            "Akamai Server": "https://example.test/b.mpd",
                        },
                        "drm_key": "kid:key",
                    }
                ]
            }
        )
        items = parse_json_content(
            content,
            {
                "id": "willow",
                "pipeline": "upcoming",
                "status_filter": ["UPCOMING"],
                "allow_without_stream": True,
            },
        )
        self.assertEqual(len(items), 2)
        self.assertTrue(all(item["url"] for item in items))
        self.assertEqual({item["provider"] for item in items}, {"Amazon Server", "Akamai Server"})
        self.assertIsNotNone(_parse_datetime(items[0]["start_time"], timezone(timedelta(hours=6))))
        self.assertIsNotNone(_parse_datetime("Tomorrow 3:45 PM BDT", timezone(timedelta(hours=6))))
        self.assertIsNotNone(_parse_datetime("Wed, Aug 12 3:45 PM BDT", timezone(timedelta(hours=6))))

    def test_axsports_full_json_schema_keeps_live_and_upcoming_streams(self):
        content = json.dumps({"matches": [
            {
                "id": 47165,
                "status": "LIVE",
                "name": "Birmingham: Sessione mattutina",
                "bd_time": "3:30 PM 13-08-2026",
                "league_name": "Sessione mattutina",
                "referer": "https://iframe.example.test",
                "link_live": [{
                    "display_name": "FHD",
                    "stream_link": "https://example.test/1410_abr.m3u8",
                    "videoURL": "https://example.test/1410/720p/chunks.m3u8?token=valid",
                }],
            },
            {
                "id": 37228,
                "status": "NS",
                "name": "Kingsmen vs Warriors",
                "bd_time": "6:30 AM 14-08-2026",
                "league_name": "CPL",
                "link_live": [{
                    "display_name": "HD",
                    "stream_link": "https://example.test/490_abr.m3u8",
                }],
            },
        ]})
        items = parse_json_content(content, {
            "id": "axsports",
            "pipeline": "upcoming",
            "status_filter": ["LIVE", "UPCOMING"],
            "allow_without_stream": True,
        })
        self.assertEqual(len(items), 2)
        self.assertEqual({item["status"] for item in items}, {"LIVE", "UPCOMING"})
        live = next(item for item in items if item["status"] == "LIVE")
        self.assertEqual(live["start_time"], "3:30 PM 13-08-2026")
        self.assertEqual(live["competition"], "Sessione mattutina")
        self.assertEqual(live["headers"].get("Referer"), "https://iframe.example.test")
        self.assertIn("token=valid", live["url"])

    def test_exhaustive_planner_keeps_every_unique_setup_and_provenance(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "config").mkdir()
            (root / "reports").mkdir()
            (root / "data").mkdir()
            (root / "config/settings.json").write_text(
                json.dumps({"planning": {"exhaustive_verification": True}}),
                encoding="utf-8",
            )
            candidates = []
            for index in range(15):
                candidates.append(
                    {
                        "id": "same-channel",
                        "name": "Same Channel",
                        "url": f"https://example.test/live.m3u8?token={index}",
                        "headers": {},
                        "drm": {},
                        "source_pipeline": "tv",
                        "source_id": f"source-{index}",
                        "category": "Sports",
                    }
                )
            candidates.append({**candidates[0], "source_id": "alias-source"})
            old_cwd = os.getcwd()
            os.chdir(root)
            try:
                planned, summary = plan_candidates(candidates, "channels")
            finally:
                os.chdir(old_cwd)
            self.assertEqual(len(planned), 15)
            self.assertEqual(summary["dropped"]["per_item_cap"], 0)
            self.assertEqual(summary["dropped"]["global_cap"], 0)
            first = next(item for item in planned if item["url"].endswith("token=0"))
            self.assertEqual(set(first["source_ids"]), {"source-0", "alias-source"})

    def test_manual_movie_stays_primary_and_discovered_becomes_backup(self):
        manual = {
            "id": "manual-film",
            "name": "Example Film",
            "year": 2026,
            "url": "https://manual.test/film-1080p.mkv",
            "resolution_height": 1080,
            "manual_source": True,
            "verification_status": "manual_trusted",
            "backups": [],
        }
        discovered = {
            "id": "found-film",
            "name": "Example Film (2026)",
            "year": 2026,
            "url": "https://found.test/film-1080p.m3u8",
            "resolution_height": 1080,
            "verification_status": "verified_global",
            "verified": True,
            "backups": [],
        }
        merged = _merge_manual_over_discovered([discovered], [manual])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["url"], manual["url"])
        self.assertEqual(merged[0]["backups"][0]["url"], discovered["url"])


if __name__ == "__main__":
    unittest.main()
