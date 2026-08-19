import os
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
        # A source may be turned off deliberately (2026-08-19: the three
        # Tapmad relay-slot mirrors, see their disabled_reason) - the guard
        # here is against silently forgetting to flip one back on by
        # accident, not against a documented, intentional disable.
        for source in configured:
            if source.get("enabled") is not True:
                self.assertTrue(
                    str(source.get("disabled_reason") or "").strip(),
                    f"{source.get('id')} is disabled without a disabled_reason",
                )
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

    def test_today_collection_submits_every_distinct_playlist_once(self):
        """Guide 30.2/30.3: one physical playlist is fetched once.

        sm-tapmad-auto and sm-tapmad-auto-blob-alias are the same URL, and the
        SonyLiv playlist is configured under both event groups. Every entry in
        them used to arrive two or three times over. Each distinct URL must be
        submitted exactly once, and no URL may be skipped.
        """
        settings = {"source_workers": 2, "source_cache": {"enabled": False}}
        sources = source_loader.load_sources_config("config")
        # collect_candidates submits every configured source (this test mocks
        # process_single_source itself, so a disabled one's own internal
        # "return early with status=disabled" never runs here) - dedup by
        # distinct URL happens regardless of the enabled flag.
        configured = [
            source
            for pipeline in ("today_match", "upcoming")
            for source in sources[pipeline]
        ]
        expected_urls = {source["url"] for source in configured}
        submitted_ids = set()
        submitted_urls = []

        def fake_load(path):
            return settings

        def fake_process(source, _settings):
            submitted_ids.add(source["id"])
            submitted_urls.append(source.get("url", ""))
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

        self.assertEqual(set(submitted_urls), expected_urls)
        self.assertEqual(
            len(submitted_urls), len(set(submitted_urls)),
            f"a playlist was fetched more than once: {submitted_urls}",
        )
        self.assertLess(
            len(submitted_ids), len(configured),
            "the duplicate alias entries should have been folded away",
        )

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


class TapmadRelaySlotDisabledTests(unittest.TestCase):
    """2026-08-19 incident: a Today Match card titled "Spain vs Belgium W |
    FIH Hockey World Cup 2026" decoded a completely different sport on
    playback. Traced to two Tapmad "auto playlist" mirrors that are not
    per-fixture sources at all - the file always holds exactly one #EXTINF
    entry that a human retitles by hand whenever they switch what they are
    watching on one shared premium account. That day it read
    "Sri Lanka vs India | India Tour of Sri Lanka 2026" over a URL path that
    still read ".../ZIMvsIND-.../master.m3u8" from a wholly unrelated earlier
    match, while an older scan's stale event ("Spain vs Belgium...") that
    this same reused URL had once been matched to was still being carried
    forward by live-event protection. Disabled at the source rather than
    patched downstream: a URL whose real-world content the maintainer swaps
    by hand cannot be trusted to keep meaning the fixture it was first seen
    under."""

    def test_the_tapmad_relay_slot_mirrors_are_disabled_with_a_reason(self):
        sources = source_loader.load_sources_config("config")
        by_id = {
            source["id"]: source
            for source in sources["today_match"]
            if source["id"] in {
                "sm-tapmad-auto", "srhady-tapmad-bd-live", "sm-tapmad-auto-blob-alias",
            }
        }
        self.assertEqual(len(by_id), 3, by_id.keys())
        for source_id, source in by_id.items():
            self.assertFalse(source.get("enabled"), source_id)
            self.assertTrue(str(source.get("disabled_reason") or "").strip(), source_id)


class PrivateSourceAuthenticationTests(unittest.TestCase):
    """A private repository's own index file needs an Authorization header to
    fetch at all, but the config that names it is checked into a public repo
    - the token itself can never live there, only a placeholder naming the
    environment variable a scan run is expected to export. That header must
    reach the request that downloads the index and nowhere else: source-level
    `headers` is also merged into every *stream's* own published/playback
    headers (see _merge_nested_headers), sent onward to third-party CDNs and
    exposed in the public playback catalogue - a repo-access token has no
    business in either place.
    """

    def test_env_placeholder_resolves_from_the_environment(self):
        with patch.dict(os.environ, {"TEST_PRIVATE_TOKEN": "secret-value-123"}):
            resolved = source_loader._resolve_fetch_headers(
                {"Authorization": "token ${TEST_PRIVATE_TOKEN}"}
            )
        self.assertEqual(resolved["Authorization"], "token secret-value-123")

    def test_an_unset_variable_resolves_to_empty_not_the_literal_placeholder(self):
        os.environ.pop("TEST_PRIVATE_TOKEN_UNSET", None)
        resolved = source_loader._resolve_fetch_headers(
            {"Authorization": "token ${TEST_PRIVATE_TOKEN_UNSET}"}
        )
        self.assertEqual(resolved["Authorization"], "token ")

    def test_fetch_headers_reach_the_index_request_but_not_a_published_stream(self):
        source = {
            "id": "private-sports-source",
            "name": "Private Sports Source",
            "url": "https://example.test/private/live.m3u",
            "pipeline": "today_match",
            "enabled": True,
            "fetch_headers": {"Authorization": "token ${TEST_PRIVATE_TOKEN}"},
        }
        settings = {
            "source_timeout_seconds": 5,
            "source_cache": {"enabled": False},
            "network": {
                "retry_attempts": 1,
                "retry_delays_seconds": [],
                "retry_status_codes": [],
                "verify_ssl": True,
            },
        }
        playlist = '#EXTM3U\n#EXTINF:-1,Example Event\nhttps://media.test/live.m3u8\n'

        with (
            patch.dict(os.environ, {"TEST_PRIVATE_TOKEN": "secret-value-123"}),
            patch.object(
                source_loader,
                "_fetch_url_with_retry",
                return_value=(playlist, None, 200, 3, 1, {}),
            ) as fetch,
        ):
            items, health = source_loader.process_single_source(source, settings)

        fetch_headers = fetch.call_args.kwargs["headers"]
        self.assertEqual(fetch_headers.get("Authorization"), "token secret-value-123")
        self.assertEqual(health["status"], "success")
        self.assertEqual(len(items), 1)
        self.assertNotIn("Authorization", items[0].get("headers") or {})

    def test_the_now_private_sports_sources_declare_the_placeholder(self):
        """0matbank/trysports was confirmed private (a raw fetch without
        auth now 404s, and the repository is invisible to an unauthenticated
        GitHub API call) - each of its three registered sources must name
        the placeholder, or a scan will start silently failing to fetch
        them the moment the token stops being unnecessary."""
        sources = source_loader.load_sources_config("config")
        trysports = [
            source for source in sources["today_match"] + sources["upcoming"]
            if str(source.get("id") or "").startswith("0matbank-trysports")
        ]
        self.assertEqual(len(trysports), 3, trysports)
        for source in trysports:
            self.assertEqual(
                source.get("fetch_headers", {}).get("Authorization"),
                "token ${PRIVATE_SPORTS_SOURCE_TOKEN}",
                source["id"],
            )


if __name__ == "__main__":
    unittest.main()
