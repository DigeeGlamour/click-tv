import unittest
from datetime import datetime, timezone
import json
import tempfile
from pathlib import Path

from scanner.events import process_events
from scanner.schedule_resolver import enrich_event_candidates, load_fixtures


class ScheduleResolverTests(unittest.TestCase):
    def test_authoritative_times_convert_to_correct_utc(self):
        fixtures = load_fixtures("config/event-fixtures.json")
        welsh = next(item for item in fixtures if item["name"] == "Welsh Fire Women vs London Spirit Women")
        ireland = next(item for item in fixtures if item["name"] == "Ireland vs Afghanistan 4th ODI")
        self.assertEqual(welsh["start"].isoformat(), "2026-08-12T10:30:00+00:00")
        self.assertEqual(ireland["start"].isoformat(), "2026-08-12T09:45:00+00:00")

    def test_knockout_names_follow_current_official_qualifiers(self):
        fixtures = load_fixtures("config/event-fixtures.json")
        knockout_names = {
            item["name"] for item in fixtures
            if "Eliminator" in item["name"] or "Final" in item["name"]
        }
        self.assertEqual(
            knockout_names,
            {
                "SunRisers Leeds Women vs Southern Brave Women - The Hundred Eliminator",
                "Manchester Super Giants Men vs SunRisers Leeds Men - The Hundred Eliminator",
                "The Hundred Women's Final - Trent Rockets Women vs Opponent TBA",
                "The Hundred Men's Final - Trent Rockets Men vs Opponent TBA",
            },
        )

    def test_multi_day_test_has_authoritative_end_time(self):
        fixtures = load_fixtures("config/event-fixtures.json")
        first_test = next(
            item for item in fixtures
            if item["name"] == "Australia vs Bangladesh 1st Test"
        )
        self.assertEqual(first_test["start"].isoformat(), "2026-08-13T00:30:00+00:00")
        self.assertEqual(first_test["end"].isoformat(), "2026-08-17T08:30:00+00:00")

        resolved, _ = enrich_event_candidates(
            [{
                "name": "Bangladesh tour of Australia 2026 - 1st Test - Australia vs Bangladesh",
                "url": "https://example.test/test.m3u8",
                "source_pipeline": "today_match",
            }],
            now=datetime(2026, 8, 14, 3, 0, tzinfo=timezone.utc),
            future_days=10,
        )
        attached = next(item for item in resolved if item.get("url"))
        self.assertEqual(attached["schedule_status"], "LIVE_NOW")

    def test_current_official_catalogue_covers_main_upcoming_feeds(self):
        fixtures = load_fixtures("config/event-fixtures.json")
        names = {item["name"] for item in fixtures}
        required = {
            "Jamaica Kingsmen vs Guyana Amazon Warriors - CPL 6th Match",
            "Saint Lucia Kings vs Antigua and Barbuda Falcons - CPL 7th Match",
            "SunRisers Leeds Women vs Southern Brave Women - The Hundred Eliminator",
            "Manchester Super Giants Men vs SunRisers Leeds Men - The Hundred Eliminator",
            "Ireland vs Afghanistan 5th ODI",
            "Sri Lanka vs India 1st Test",
            "Australia vs Bangladesh 2nd Test",
        }
        self.assertTrue(required.issubset(names), required - names)

    def test_mackay_uses_queensland_time_not_darwin_time(self):
        fixtures = load_fixtures("config/event-fixtures.json")
        second_test = next(
            item for item in fixtures
            if item["name"] == "Australia vs Bangladesh 2nd Test"
        )
        self.assertEqual(second_test["start"].isoformat(), "2026-08-22T00:00:00+00:00")
        self.assertEqual(second_test["end"].isoformat(), "2026-08-26T08:00:00+00:00")

    def test_wrong_source_times_are_corrected_and_auditable(self):
        candidates = [
            {
                "name": "Afghanistan tour of Ireland 2026 - 4th ODI - Ireland vs Afghanistan",
                "start_time": "3:15 PM BDT",
                "url": "",
                "metadata_only": True,
                "allow_without_stream": True,
                "source_pipeline": "upcoming",
            },
            {
                "name": "The Hundred Womens Competition 2026 - 31st Match - Welsh Fire Women vs London Spirit Women",
                "start_time": "3:50 PM BDT",
                "url": "",
                "metadata_only": True,
                "allow_without_stream": True,
                "source_pipeline": "upcoming",
            },
        ]
        resolved, stats = enrich_event_candidates(
            candidates,
            now=datetime(2026, 8, 12, 10, 0, tzinfo=timezone.utc),
            future_days=10,
        )
        ireland = next(item for item in resolved if item.get("fixture_id", "").endswith("4th-odi"))
        welsh = next(item for item in resolved if "welsh-fire-women" in item.get("fixture_id", ""))
        self.assertEqual(ireland["start_time"], "2026-08-12T09:45:00+00:00")
        self.assertEqual(welsh["start_time"], "2026-08-12T10:30:00+00:00")
        self.assertEqual(ireland["source_start_time"], "3:15 PM BDT")
        self.assertEqual(welsh["source_start_time"], "3:50 PM BDT")
        self.assertEqual(stats["corrected"], 2)

    def test_generic_stream_is_not_guessed_when_two_matches_overlap(self):
        generic = [{
            "name": "The Hundred 2026 - Willow Cricket",
            "url": "https://example.test/willow.m3u8",
            "source_pipeline": "upcoming",
        }]
        resolved, stats = enrich_event_candidates(
            generic,
            now=datetime(2026, 8, 12, 14, 30, tzinfo=timezone.utc),
            future_days=2,
        )
        self.assertFalse(any(item.get("url") for item in resolved))
        self.assertEqual(stats["ambiguous_suppressed"], 1)

    def test_women_only_generic_stream_attaches_when_one_women_match_is_active(self):
        generic = [{
            "name": "The Hundred W Vs The Hundred W - WILLOW FHD",
            "url": "https://example.test/willow.mpd",
            "source_pipeline": "upcoming",
        }]
        resolved, _ = enrich_event_candidates(
            generic,
            now=datetime(2026, 8, 12, 10, 40, tzinfo=timezone.utc),
            future_days=2,
        )
        attached = next(item for item in resolved if item.get("url"))
        self.assertEqual(attached["name"], "Welsh Fire Women vs London Spirit Women")
        self.assertEqual(attached["schedule_status"], "LIVE_NOW")

    def test_hmen_url_never_attaches_to_women_fixture(self):
        candidate = [{
            "name": "Welsh Fire vs London Spirit - 12 Aug 2026 | The Hundred 2026",
            "logo": "https://example.test/The_Hundred2026_mens_Welsh_Fire.jpg",
            "url": "https://example.test/live/DAI18-HMEN/master.m3u8",
            "source_pipeline": "today_match",
        }]
        resolved, _ = enrich_event_candidates(
            candidate,
            now=datetime(2026, 8, 12, 13, 0, tzinfo=timezone.utc),
            future_days=2,
        )
        attached = next(item for item in resolved if item.get("url"))
        self.assertEqual(attached["name"], "Welsh Fire Men vs London Spirit Men")

    def test_ambiguous_neutral_today_source_stays_channel_live_without_guessing(self):
        candidate = [{
            "name": "Welsh Fire vs London Spirit",
            "url": "https://example.test/generic.m3u8",
            "source_pipeline": "today_match",
        }]
        resolved, stats = enrich_event_candidates(
            candidate,
            now=datetime(2026, 8, 12, 13, 0, tzinfo=timezone.utc),
            future_days=2,
        )
        attached = next(item for item in resolved if item.get("url"))
        self.assertEqual(attached["name"], "Welsh Fire vs London Spirit")
        self.assertEqual(attached["schedule_status"], "CHANNEL_LIVE")
        self.assertTrue(attached["today_source_channel"])
        self.assertNotIn("start_time", attached)
        self.assertEqual(stats["unverified_suppressed"], 1)

    def test_ended_fixture_is_not_published(self):
        candidate = [{
            "name": "Welsh Fire Women vs London Spirit Women",
            "url": "https://example.test/women.m3u8",
            "source_pipeline": "today_match",
        }]
        resolved, _ = enrich_event_candidates(
            candidate,
            now=datetime(2026, 8, 12, 14, 0, 1, tzinfo=timezone.utc),
            future_days=2,
        )
        self.assertFalse(any(
            item.get("name") == "Welsh Fire Women vs London Spirit Women"
            for item in resolved
        ))

    def test_generic_channel_label_never_attaches_to_numbered_fixture(self):
        candidate = [{
            "name": "Afghanistan vs Ireland - Willow HD",
            "url": "https://example.test/willow-hd.m3u8",
            "source_pipeline": "today_match",
        }]
        resolved, _ = enrich_event_candidates(
            candidate,
            now=datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc),
            future_days=10,
        )
        attached = next(item for item in resolved if item.get("url"))
        self.assertEqual(attached["schedule_status"], "CHANNEL_LIVE")
        self.assertNotIn("fixture_id", attached)

    def test_wrong_willow_program_is_not_labelled_as_first_test(self):
        candidate = [{
            "name": "Australia vs Bangladesh Willow",
            "url": "https://example.test/reused-willow-channel.m3u8",
            "source_pipeline": "today_match",
        }]
        resolved, _ = enrich_event_candidates(
            candidate,
            now=datetime(2026, 8, 13, 10, 42, tzinfo=timezone.utc),
            future_days=10,
        )
        attached = next(item for item in resolved if item.get("url"))
        self.assertEqual(attached["name"], "Australia vs Bangladesh Willow")
        self.assertEqual(attached["schedule_status"], "CHANNEL_LIVE")

    def test_matching_ordinal_can_attach_to_numbered_fixture(self):
        candidate = [{
            "name": "Australia vs Bangladesh 1st Test - Willow HD",
            "url": "https://example.test/first-test.m3u8",
            "source_pipeline": "today_match",
        }]
        resolved, _ = enrich_event_candidates(
            candidate,
            now=datetime(2026, 8, 13, 10, 42, tzinfo=timezone.utc),
            future_days=10,
        )
        attached = next(item for item in resolved if item.get("url"))
        self.assertEqual(attached["name"], "Australia vs Bangladesh 1st Test")

    def test_same_ordinal_different_teams_never_match(self):
        candidate = [{
            "name": "England vs India 4th T20I 12 Aug 2026",
            "url": "https://example.test/england-india.m3u8",
            "source_pipeline": "today_match",
        }]
        resolved, stats = enrich_event_candidates(
            candidate,
            now=datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc),
            future_days=10,
        )
        attached = next(item for item in resolved if item.get("url"))
        self.assertEqual(attached["schedule_status"], "CHANNEL_LIVE")
        self.assertEqual(stats["unverified_suppressed"], 1)

    def test_process_events_preserves_resolved_schedule_fields(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            results_path = root / "results.json"
            settings_path = root / "settings.json"
            results_path.write_text(json.dumps({"results": [{
                "name": "Ireland vs Afghanistan 5th ODI",
                "start_time": "3:15 PM BDT",
                "url": "",
                "metadata_only": True,
                "allow_without_stream": True,
                "source_pipeline": "upcoming",
                "verification_status": "metadata_only",
                "publish_allowed": True,
            }]}), encoding="utf-8")
            settings_path.write_text(json.dumps({
                "timezone": "Asia/Dhaka",
                "events": {"timezone": "Asia/Dhaka", "upcoming_future_days": 120},
                "resolution": {"event_minimum_height": 720},
            }), encoding="utf-8")
            result = process_events(
                str(results_path),
                str(settings_path),
                "config/event-fixtures.json",
                now=datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc),
            )
            card = next(
                item for item in result["upcoming"]["items"]
                if item.get("fixture_id", "").endswith("5th-odi")
            )
            self.assertEqual(card["start_time"], "2026-08-15T09:45:00+00:00")
            self.assertTrue(card["schedule_verified"])
            self.assertEqual(card["time_verification"], "corrected")

    def test_unverified_generic_event_is_not_published(self):
        resolved, stats = enrich_event_candidates(
            [{
                "name": "ATP vs WTA beIN ENGLISH",
                "url": "https://example.test/tennis.m3u8",
                "source_pipeline": "upcoming",
            }],
            now=datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc),
            future_days=2,
        )
        self.assertFalse(any(
            item.get("name") == "ATP vs WTA beIN ENGLISH"
            for item in resolved
        ))
        self.assertEqual(stats["unverified_suppressed"], 1)

    def test_birmingham_italian_live_label_maps_to_official_session(self):
        resolved, _ = enrich_event_candidates(
            [{
                "name": "Birmingham: Sessione mattutina",
                "url": "https://example.test/athletics.m3u8",
                "source_pipeline": "upcoming",
            }],
            now=datetime(2026, 8, 13, 9, 35, tzinfo=timezone.utc),
            future_days=2,
        )
        attached = next(item for item in resolved if item.get("url"))
        self.assertEqual(attached["name"], "Birmingham 2026 Athletics - Morning Session")
        self.assertEqual(attached["status"], "LIVE_NOW")


if __name__ == "__main__":
    unittest.main()
