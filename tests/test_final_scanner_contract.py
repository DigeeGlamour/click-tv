import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scanner.events import _parse_datetime
from scanner.fast_pipeline import _apply_strict_player_visibility
from scanner.merger import merge_candidates, rank_and_select_streams
from scanner.normalizer import Normalizer
from scanner.movies import _deduplicate_movies_by_playback_url, _merge_manual_over_discovered, _movie_identity
from scanner.player_compatibility import load_failure_keys, mark_confirmed_player_failures, mark_unproven_player_items, playback_fingerprint
from scanner.parsers.json_parser import parse_json_content
from scanner.planner import plan_candidates
from scanner.playback_profiles import PlaybackProfileCollector
from scanner.verifier import _apply_resolution_policy


class FinalScannerContractTests(unittest.TestCase):
    def test_bangla_aliases_and_different_source_ids_merge_to_one_card(self):
        normalizer = Normalizer()
        names = [normalizer.clean_title("Somoy TV"), normalizer.clean_title("Somoy TV BK")]
        self.assertEqual(names, ["Somoy TV", "Somoy TV"])
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "settings.json"
            settings_path.write_text(json.dumps({"resolution": {"tv_minimum_height": 720}}), encoding="utf-8")
            cards = merge_candidates([
                {"id": "source-a", "name": names[0], "url": "https://a.example/live.m3u8", "source_pipeline": "tv", "category": "Bangla", "verified": True, "publish_allowed": True, "verification_status": "verified_global", "resolution_height": 720},
                {"id": "source-b", "name": names[1], "url": "https://b.example/live.m3u8", "source_pipeline": "TV", "category": "Bangla", "verified": True, "publish_allowed": True, "verification_status": "verified_global", "resolution_height": 1080},
            ], settings_path=str(settings_path))
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["available_link_count"], 2)

    def test_movie_pipeline_aliases_merge_same_title_and_year(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "settings.json"
            settings_path.write_text(json.dumps({"resolution": {"movie_minimum_height": 720}}), encoding="utf-8")
            cards = merge_candidates([
                {"id": "movie-a", "name": "Example Movie (2026)", "url": "https://a.example/movie.m3u8", "source_pipeline": "movies", "category": "Bangla", "verified": True, "publish_allowed": True, "verification_status": "verified_global", "resolution_height": 1080},
                {"id": "movie-b", "name": "Example Movie 2026", "url": "https://b.example/movie.m3u8", "source_pipeline": "VOD", "category": "Bangla", "verified": True, "publish_allowed": True, "verification_status": "verified_global", "resolution_height": 1080},
            ], settings_path=str(settings_path))
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["available_link_count"], 2)

    def test_cross_category_same_movie_route_produces_one_card(self):
        cards = _deduplicate_movies_by_playback_url([
            {
                "name": "Balan The Boy",
                "year": 2026,
                "category": "Hindi",
                "url": "https://media.example/balan.mkv",
                "manual_source": True,
                "manual_source_tier": 2,
            },
            {
                "name": "Balan",
                "year": 2026,
                "category": "South Indian",
                "url": "https://media.example/balan.mkv",
                "manual_source": True,
                "manual_source_tier": 2,
            },
        ])
        self.assertEqual(len(cards), 1)

    def test_dual_audio_suffix_does_not_create_second_movie(self):
        discovered = {
            "name": "Example Film (2026) Dual",
            "year": 2026,
            "category": "Mix",
            "url": "https://found.example/example.mkv",
            "verified": True,
        }
        manual = {
            "name": "Example Film",
            "year": 2026,
            "category": "Bangla",
            "url": "https://manual.example/example.mkv",
            "manual_source": True,
        }
        cards = _merge_manual_over_discovered([discovered], [manual])
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["url"], manual["url"])
        self.assertEqual(cards[0]["backups"][0]["url"], discovered["url"])

    def test_final_movie_pipeline_uses_canonical_title_year_identity(self):
        self.assertEqual(
            _movie_identity({"name": "Master", "year": 2026}),
            _movie_identity({"name": "Master (2026) Bengali Dubbed ORG"}),
        )
        self.assertEqual(
            _movie_identity({"name": "Demon Slayer: Kimetsu No Yaiba Infinity Castle", "year": 2025}),
            _movie_identity({"name": "Demon Slayer Kimetsu no Yaiba Infinity Castle (2025) Dual ORG"}),
        )

    def test_strict_player_visibility_retains_but_hides_unverified(self):
        items = [
            {"name": "Pending", "publish_allowed": True, "verified": False, "verification_status": "bd_protected_pending"},
            {"name": "Proven", "publish_allowed": True, "verified": True, "verification_status": "verified_global"},
        ]
        hidden = _apply_strict_player_visibility(items, {"bd_verification": {"strict_player_publish": True}})
        self.assertEqual(hidden, 1)
        self.assertFalse(items[0]["publish_allowed"])
        self.assertEqual(items[0]["player_visibility"], "hidden_unverified")
        self.assertTrue(items[1]["publish_allowed"])

    def test_explicit_player_denial_wins_over_network_verified(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "settings.json"
            settings_path.write_text(json.dumps({"resolution": {"tv_minimum_height": 720}}), encoding="utf-8")
            cards = merge_candidates([{
                "name": "Network Only",
                "url": "https://example.test/live.m3u8",
                "source_pipeline": "tv",
                "category": "Bangla",
                "verified": True,
                "publish_allowed": False,
                "verification_status": "failed_player_twice",
                "resolution_height": 1080,
            }], settings_path=str(settings_path))
        self.assertEqual(cards, [])

    def test_confirmed_player_failure_is_retained_but_marked_hidden(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            report_path = Path(temp_dir) / "confirmed.json"
            report_path.write_text(json.dumps({
                "records": [{
                    "kind": "movie",
                    "record": {"name": "Broken Film", "year": 2026},
                }],
            }), encoding="utf-8")
            item = {
                "name": "Broken Film",
                "year": 2026,
                "verified": True,
                "publish_allowed": True,
                "verification_status": "verified_global",
            }
            hidden = mark_confirmed_player_failures([item], "movie", report_path)
            self.assertEqual(hidden, 1)
            self.assertTrue(item["verified"])
            self.assertFalse(item["publish_allowed"])
            self.assertEqual(item["network_verification_status"], "verified_global")
            self.assertEqual(item["verification_status"], "failed_player_twice")
            self.assertIn(("movie", "broken film", "2026"), load_failure_keys(report_path))

    def test_route_change_requires_a_new_real_player_proof(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            ledger_path = Path(temp_dir) / "proof.json"
            proven = {
                "name": "Proven Channel",
                "category": "Bangla",
                "url": "https://media.example/working.m3u8",
                "proxy_mode": "auto",
                "stream_type": "hls",
                "publish_allowed": True,
            }
            ledger_path.write_text(json.dumps({
                "proofs": [{
                    "kind": "channel",
                    "name": proven["name"],
                    "fingerprint": playback_fingerprint(proven),
                }],
            }), encoding="utf-8")
            unchanged = dict(proven)
            changed = {**proven, "url": "https://media.example/new-unproven.m3u8"}
            self.assertEqual(mark_unproven_player_items([unchanged], "channel", ledger_path), 0)
            self.assertEqual(mark_unproven_player_items([changed], "channel", ledger_path), 1)
            self.assertFalse(changed["publish_allowed"])
            self.assertEqual(changed["verification_status"], "pending_player_proof")

    def test_backup_mirror_churn_does_not_invalidate_an_unchanged_proven_primary(self):
        """A real 2026-08-18 regression: ATN Bangla, NTV, RTV, Somoy TV and
        Jago News 24 all had an unchanged, still-verified_global primary
        stream, yet vanished from the published Bangla category because a
        *backup* mirror candidate flaked (timed out, 500'd) or a new one
        appeared between scans. The real-browser audit never decodes a
        backup - only the primary - so proof must track the primary alone."""
        with tempfile.TemporaryDirectory() as temp_dir:
            ledger_path = Path(temp_dir) / "proof.json"
            proven = {
                "name": "Proven Channel",
                "category": "Bangla",
                "url": "https://media.example/working.m3u8",
                "proxy_mode": "auto",
                "stream_type": "hls",
                "publish_allowed": True,
                "backups": [{"url": "https://mirror-a.example/backup.m3u8"}],
            }
            ledger_path.write_text(json.dumps({
                "proofs": [{
                    "kind": "channel",
                    "name": proven["name"],
                    "fingerprint": playback_fingerprint(proven),
                }],
            }), encoding="utf-8")

            backup_dropped = {**proven, "backups": []}
            backup_added = {
                **proven,
                "backups": [
                    {"url": "https://mirror-a.example/backup.m3u8"},
                    {"url": "https://mirror-b.example/new-backup.m3u8"},
                ],
            }
            self.assertEqual(
                mark_unproven_player_items([backup_dropped], "channel", ledger_path), 0
            )
            self.assertTrue(backup_dropped["publish_allowed"])
            self.assertEqual(
                mark_unproven_player_items([backup_added], "channel", ledger_path), 0
            )
            self.assertTrue(backup_added["publish_allowed"])

    def test_final_source_registry_contains_only_agreed_remote_sources(self):
        from scanner.source_loader import load_sources_config

        payload = load_sources_config("config")
        # Added by direct request: 0matbank/trysports (cricket live, football
        # live and upcoming) as an extra, lower-priority source - two land in
        # today_match, one in upcoming.
        self.assertEqual(len(payload["upcoming"]), 6)
        self.assertEqual(len(payload["today_match"]), 10)
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

    def test_0matbank_trysports_cricket_schema_is_read(self):
        """The new source's per-stream URL lives under "direct_stream_url"
        and its real channel name under "channel_name" - neither was in this
        parser's recognised key lists, so every stream from this source
        would otherwise have parsed as metadata with no playable URL and no
        broadcaster identity at all."""
        content = json.dumps({"matches": [{
            "id": "admin-willow-cricket",
            "title": "Willow Cricket",
            "category": "cricket",
            "status": "LIVE_NOW",
            "poster": "https://example.test/poster.webp",
            "headers": {"User-Agent": "UA/1.0", "Referer": "https://embed.st/"},
            "streams": [
                {"channel_name": "Willow Cricket (HD)", "hd": True,
                 "direct_stream_url": "https://example.test/willow-hd.m3u8"},
                {"channel_name": "Willow 2 (HD)", "hd": True,
                 "direct_stream_url": "https://example.test/willow-2.m3u8"},
            ],
        }]})
        items = parse_json_content(content, {
            "id": "0matbank-trysports-cricket-live",
            "pipeline": "today_match",
            "status_filter": ["LIVE"],
        })
        self.assertEqual(len(items), 2)
        self.assertEqual({item["url"] for item in items},
                          {"https://example.test/willow-hd.m3u8", "https://example.test/willow-2.m3u8"})
        self.assertEqual({item["provider"] for item in items}, {"Willow Cricket (HD)", "Willow 2 (HD)"})
        for item in items:
            self.assertEqual(item["headers"]["Referer"], "https://embed.st/")

    def test_0matbank_trysports_upcoming_schedule_is_read(self):
        """The schedule text names its own timezone explicitly -
        "(BD Time)" - rather than the bare trailing "BDT" every other
        pattern already tolerates; misreading it as the caller's default
        timezone instead would put every kickoff up to six hours off."""
        content = json.dumps({"matches": [{
            "id": "heidenheim-vs-bayern-munich",
            "title": "Heidenheim vs Bayern Munich",
            "category": "football",
            "status": "UPCOMING",
            "start_time_bd": "18 Aug 2026, 10:00 PM (BD Time)",
            "poster": "https://example.test/poster.webp",
        }]})
        items = parse_json_content(content, {
            "id": "0matbank-trysports-football-upcoming",
            "pipeline": "upcoming",
            "status_filter": ["UPCOMING"],
            "allow_without_stream": True,
        })
        self.assertEqual(len(items), 1)
        parsed = _parse_datetime(items[0]["start_time"], timezone.utc)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed, datetime(2026, 8, 18, 16, 0, tzinfo=timezone.utc))

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
                # Every "keep it anyway" rescue is switched off here on purpose:
                # this test pins the bare 720p minimum itself. The event rescue
                # has its own coverage in tests/test_event_resolution_policy.py.
                "preserve_unknown_working_event": False,
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
