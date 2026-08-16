import unittest
import json
from pathlib import Path
from unittest.mock import patch

from scanner import source_loader
from scanner.planner import _pipeline_for_mode


class TodayEventSourceTests(unittest.TestCase):
    def test_every_configured_event_source_is_enabled_and_has_unique_id(self):
        sources = source_loader.load_sources_config("config")
        configured = sources["today_match"] + sources["upcoming"]
        self.assertTrue(configured)
        self.assertTrue(all(source.get("enabled") is True for source in configured))
        ids = [source["id"] for source in configured]
        self.assertEqual(len(ids), len(set(ids)))

    def test_agreed_event_sources_are_all_registered(self):
        sources = source_loader.load_sources_config("config")
        urls = {
            source["url"]
            for pipeline in ("today_match", "upcoming")
            for source in sources[pipeline]
        }
        self.assertTrue({
            "https://raw.githubusercontent.com/sm-monirulislam/Upcoming-and-Live-Sports-Data/refs/heads/main/Sports_data.m3u",
            "https://raw.githubusercontent.com/srhady/crichd-speical-live-event/refs/heads/main/Live_Events.m3u",
            "https://raw.githubusercontent.com/srhady/willow-event/refs/heads/main/live_sports.json",
        }.issubset(urls))

    def test_today_planner_accepts_upcoming_event_candidates(self):
        self.assertEqual(
            _pipeline_for_mode("today"),
            {"today_match", "upcoming"},
        )

    def test_today_collection_reads_both_event_source_groups(self):
        settings = {"source_workers": 1, "source_cache": {"enabled": False}}
        sources = {
            "today_match": [{"id": "today-source", "enabled": True}],
            "upcoming": [{"id": "upcoming-source", "enabled": True}],
            "manual": {},
        }

        def fake_load(path):
            return settings

        def fake_process(source, _settings):
            return [], {
                "source_id": source["id"],
                "source_name": source["id"],
                "url": "",
                "pipeline": source["pipeline"],
                "status": "success_empty",
                "last_scan": "2026-08-12T00:00:00+00:00",
                "http_status": 200,
                "attempts": 1,
                "response_time_ms": 1,
                "detected_format": "m3u",
                "raw_items": 0,
                "error": None,
            }

        with (
            patch.object(source_loader, "load_sources_config", return_value=sources),
            patch.object(source_loader, "_load_json_file", side_effect=fake_load),
            patch.object(source_loader, "process_single_source", side_effect=fake_process),
            patch.object(source_loader, "load_manual_sources", return_value=([], {})),
            patch.object(source_loader, "_atomic_write_json"),
            patch.object(source_loader, "_merge_health_history", return_value={}),
        ):
            payload = source_loader.collect_candidates("today")

        self.assertEqual(payload["active_pipelines"], ["today_match", "upcoming"])
        self.assertEqual(payload["source_count"], 2)

    def test_today_collection_submits_every_today_and_upcoming_source(self):
        settings = {"source_workers": 2, "source_cache": {"enabled": False}}
        sources = source_loader.load_sources_config("config")
        expected_ids = {
            source["id"]
            for pipeline in ("today_match", "upcoming")
            for source in sources[pipeline]
            if source.get("enabled") is True
        }
        submitted_ids = set()

        def fake_load(path):
            return settings

        def fake_process(source, _settings):
            submitted_ids.add(source["id"])
            return [], {
                "source_id": source["id"],
                "source_name": source["id"],
                "url": source.get("url", ""),
                "pipeline": source["pipeline"],
                "status": "success_empty",
                "last_scan": "2026-08-12T00:00:00+00:00",
                "http_status": 200,
                "attempts": 1,
                "response_time_ms": 1,
                "detected_format": source.get("format", "auto"),
                "raw_items": 0,
                "error": None,
            }

        with (
            patch.object(source_loader, "load_sources_config", return_value=sources),
            patch.object(source_loader, "_load_json_file", side_effect=fake_load),
            patch.object(source_loader, "process_single_source", side_effect=fake_process),
            patch.object(source_loader, "load_manual_sources", return_value=([], {})),
            patch.object(source_loader, "_atomic_write_json"),
            patch.object(source_loader, "_merge_health_history", return_value={}),
        ):
            payload = source_loader.collect_candidates("today")

        self.assertEqual(payload["source_count"], len(expected_ids))
        self.assertEqual(submitted_ids, expected_ids)

    def test_empty_304_cache_retries_without_conditional_headers(self):
        source = {
            "id": "duplicate-url-second-pipeline",
            "name": "Duplicate URL second pipeline",
            "url": "https://example.test/events.m3u",
            "pipeline": "upcoming",
            "enabled": True,
        }
        settings = {
            "source_timeout_seconds": 5,
            "source_cache": {"enabled": True},
            "network": {
                "retry_attempts": 1,
                "retry_delays_seconds": [],
                "retry_status_codes": [],
                "verify_ssl": True,
            },
        }
        response_meta = {"etag": '"new"', "last_modified": ""}
        playlist = '#EXTM3U\n#EXTINF:-1,Example Event\nhttps://media.test/live.m3u8\n'

        with (
            patch.object(
                source_loader,
                "_load_source_cache",
                return_value=({"etag": '"old"'}, []),
            ),
            patch.object(
                source_loader,
                "_fetch_url_with_retry",
                side_effect=[
                    (None, None, 304, 2, 1, {"etag": '"old"'}),
                    (playlist, None, 200, 3, 1, response_meta),
                ],
            ) as fetch,
            patch.object(source_loader, "_save_source_cache"),
        ):
            items, health = source_loader.process_single_source(source, settings)

        self.assertEqual(fetch.call_count, 2)
        first_headers = fetch.call_args_list[0].kwargs["headers"]
        second_headers = fetch.call_args_list[1].kwargs["headers"]
        self.assertEqual(first_headers["If-None-Match"], '"old"')
        self.assertNotIn("If-None-Match", second_headers)
        self.assertEqual(health["status"], "success")
        self.assertEqual(health["http_status"], 200)
        self.assertEqual(health["attempts"], 2)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["source_pipeline"], "upcoming")


if __name__ == "__main__":
    unittest.main()
