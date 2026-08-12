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

    def test_unconfirmed_knockout_rounds_are_labelled_teams_tba(self):
        fixtures = load_fixtures("config/event-fixtures.json")
        knockout_names = {
            item["name"] for item in fixtures
            if "Eliminator" in item["name"] or "Final" in item["name"]
        }
        self.assertEqual(
            knockout_names,
            {
                "The Hundred Women Eliminator - Teams TBA",
                "The Hundred Men Eliminator - Teams TBA",
                "The Hundred Women Final - Teams TBA",
                "The Hundred Men Final - Teams TBA",
            },
        )

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

    def test_ambiguous_neutral_same_team_event_is_not_guessed(self):
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
        self.assertFalse(any(item.get("url") for item in resolved))
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

    def test_generic_series_teams_attach_only_to_current_numbered_fixture(self):
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
        self.assertEqual(attached["name"], "Ireland vs Afghanistan 4th ODI")
        self.assertTrue(attached["fixture_id"].endswith("4th-odi"))

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
        self.assertFalse(any(item.get("url") for item in resolved))
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
                str(results_path), str(settings_path), "config/event-fixtures.json"
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


if __name__ == "__main__":
    unittest.main()
