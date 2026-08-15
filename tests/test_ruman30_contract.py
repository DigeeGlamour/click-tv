import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from scanner.movies import (
    _annotate_manual_movie_liveness,
    _deduplicate_movies_by_playback_url,
    _movie_identity,
    _probe_manual_movie_source,
)


ROOT = Path(__file__).resolve().parent.parent


class ManualMovieMediaDepthTests(unittest.TestCase):
    def test_release_and_dubbed_tokens_share_one_title_year_identity(self):
        pairs = (
            ({"name": "Master", "year": 2026}, {"name": "Master (2026) Bengali Dubbed ORG"}),
            ({"name": "KD – The Devil", "year": 2026}, {"name": "KD The Devil 2026 Hindi Dubbed"}),
        )
        for left, right in pairs:
            self.assertEqual(_movie_identity(left), _movie_identity(right))

    def test_hls_requires_a_readable_media_segment(self):
        responses = {
            "https://media.example/master.m3u8": (
                200,
                b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=900000\nvideo/index.m3u8\n",
                "application/vnd.apple.mpegurl",
            ),
            "https://media.example/video/index.m3u8": (
                200,
                b"#EXTM3U\n#EXTINF:6,\nseg-1.ts\n",
                "application/vnd.apple.mpegurl",
            ),
            "https://media.example/video/seg-1.ts": (200, b"\x47" * 512, "video/mp2t"),
        }

        with patch("scanner.movies._request_probe_bytes", side_effect=lambda url, *a, **k: responses[url]):
            result = _probe_manual_movie_source(
                {"url": "https://media.example/master.m3u8", "stream_type": "hls"}, 3
            )

        self.assertEqual("live", result["status"])
        self.assertTrue(result["segment_verified"])

    def test_strict_mode_removes_manual_movie_when_segment_is_dead(self):
        settings = {
            "manual_movie_liveness": {
                "enabled": True,
                "strict_publish": True,
                "workers": 2,
                "timeout_seconds": 2,
            }
        }
        movie = {"name": "Dead Movie", "url": "https://dead.example/movie.m3u8", "stream_type": "hls"}
        with patch(
            "scanner.movies._probe_manual_movie_source",
            return_value={"status": "dead", "http_status": 200, "segment_verified": False},
        ):
            published = _annotate_manual_movie_liveness([movie], settings)
        self.assertEqual([], published)

    def test_all_configurations_are_checked_and_live_backup_can_become_primary(self):
        settings = {
            "manual_movie_liveness": {
                "enabled": True,
                "strict_publish": True,
                "workers": 2,
                "timeout_seconds": 2,
            }
        }
        movie = {
            "name": "Backup Movie",
            "url": "https://dead.example/movie.m3u8",
            "stream_type": "hls",
            "backups": [
                {
                    "url": "https://live.example/movie.m3u8",
                    "stream_type": "hls",
                    "headers": {"Cookie": "session=test"},
                }
            ],
        }

        def result_for(source, _timeout):
            live = source["url"].startswith("https://live.example")
            return {
                "status": "live" if live else "dead",
                "http_status": 200,
                "segment_verified": live,
                "response_time_ms": 10,
            }

        with patch("scanner.movies._probe_manual_movie_source", side_effect=result_for) as probe:
            published = _annotate_manual_movie_liveness([movie], settings)
        self.assertEqual(2, probe.call_count)
        self.assertEqual("https://live.example/movie.m3u8", published[0]["url"])
        self.assertEqual("session=test", published[0]["headers"]["Cookie"])

    def test_same_url_one_card_but_distinct_credentials_remain_as_backup(self):
        url = "https://media.example/same.m3u8"
        movies = [
            {"name": "Movie One", "url": url, "manual_source": True, "headers": {"Cookie": "a=1"}},
            {"name": "Movie One Copy", "url": url, "headers": {"Cookie": "a=2"}},
        ]
        output = _deduplicate_movies_by_playback_url(movies)
        self.assertEqual(1, len(output))
        cookies = [output[0].get("headers", {}).get("Cookie")]
        cookies.extend(item.get("headers", {}).get("Cookie") for item in output[0].get("backups", []))
        self.assertEqual({"a=1", "a=2"}, set(cookies))


class RuntimeContractTests(unittest.TestCase):
    def test_live_upcoming_source_never_leaks_into_today_match(self):
        from scanner.schedule_resolver import enrich_event_candidates

        resolved, _ = enrich_event_candidates(
            [{
                "name": "Welsh Fire Women vs London Spirit Women",
                "url": "https://example.test/live.m3u8",
                "source_pipeline": "upcoming",
            }],
            now=datetime(2026, 8, 12, 10, 40, tzinfo=timezone.utc),
            future_days=2,
        )
        live = next(item for item in resolved if item.get("url"))
        self.assertEqual("LIVE_NOW", live["schedule_status"])
        self.assertEqual("upcoming", live["source_pipeline"])

    def test_today_card_requires_confirmed_protocol_verification(self):
        from scanner.events import _is_playable

        self.assertFalse(_is_playable({"url": "https://example.test/live.m3u8"}))
        self.assertFalse(_is_playable({
            "url": "https://example.test/live.m3u8",
            "verified": True,
            "verification_status": "retryable_pending",
        }))
        self.assertTrue(_is_playable({
            "url": "https://example.test/live.m3u8",
            "verified": True,
            "verification_status": "verified_global",
        }))

    def test_event_catalogue_auto_refresh_and_truthful_channel_live_status(self):
        source = (ROOT / "site/assets/js/app.js").read_text(encoding="utf-8")
        self.assertIn("const EVENT_CATALOG_REFRESH_MS = 60000", source)
        self.assertIn("setInterval(refreshActiveEventCatalogue, EVENT_CATALOG_REFRESH_MS)", source)
        self.assertIn("configured === 'LIVE_NOW' ? 'LIVE_NOW' : 'CHANNEL_LIVE'", source)
        self.assertNotIn("minutes >= -360", source)

    def test_non_event_startup_is_faster_but_event_buffers_are_unchanged(self):
        source = (ROOT / "site/assets/js/app.js").read_text(encoding="utf-8")
        self.assertIn("if (mode === NETWORK_MODE.BALANCED) return 1.8;", source)
        self.assertIn("if (mode === NETWORK_MODE.STABLE) return 3.4;", source)
        self.assertIn("if (mode === NETWORK_MODE.BALANCED) return 2000;", source)
        self.assertIn("if (mode === NETWORK_MODE.STABLE) return 4000;", source)
        self.assertIn("if (mode === NETWORK_MODE.BALANCED) return 1.2;", source)
        self.assertIn("if (mode === NETWORK_MODE.STABLE) return 3.0;", source)
        self.assertIn("maxBufferLength: isEvent ? 8 : 8", source)
        self.assertIn("maxBufferLength: isEvent ? 5 : 6", source)
        self.assertIn("maxBufferLength: isEvent ? 12 : 16", source)

    def test_notice_final_override_animates_after_legacy_mobile_contract(self):
        source = (ROOT / "site/assets/css/reference-design.css").read_text(encoding="utf-8")
        legacy = source.rindex("RUMAN-29: final authoritative mobile contract")
        final = source.rindex("RUMAN-30 AUTHORITATIVE NOTICE")
        self.assertGreater(final, legacy)
        block = source[final:]
        self.assertIn("font-size:14px!important", block)
        self.assertIn("font-size:12.5px!important", block)
        self.assertIn("animation:clicktv-r30-notice 18s linear infinite!important", block)
        self.assertNotIn("transform:translateX(0)!important;animation:clicktv-r30-notice", block)

    def test_drm_uses_the_active_session_attempt(self):
        source = (ROOT / "site/assets/js/app.js").read_text(encoding="utf-8")
        branch = source[source.index("async function initShaka"):source.index("async function initMpegTs")]
        self.assertIn("const attempt = session?.currentAttempt", branch)
        self.assertIn("attempt?.proxy", branch)

    def test_direct_first_does_not_reorder_from_saved_proxy_preference(self):
        source = (ROOT / "site/assets/js/app.js").read_text(encoding="utf-8")
        planner = source[source.index("function buildAttemptPlan"):source.index("function devicePerformanceClass")]
        self.assertNotIn("routePreferences", planner)
        self.assertIn("rankHealthyProxies(healthTarget, false)", planner)
        self.assertNotIn("slice(0, 2)", planner)

    def test_header_retry_is_not_mislabelled_as_proxy_verification(self):
        source = (ROOT / "scanner/bd_verifier.py").read_text(encoding="utf-8")
        self.assertIn('"verification_route": "direct_header_retry"', source)
        self.assertIn('verified_status = "verified_global" if direct_retry else "verified_proxy"', source)

    def test_scheduled_event_refresh_is_automatic(self):
        workflow = (ROOT / ".github/workflows/scan.yml").read_text(encoding="utf-8")
        self.assertIn('cron: "2,17,32,47 * * * *"', workflow)
        self.assertIn('cron: "9,39 * * * *"', workflow)

    def test_uncertain_movies_are_not_publishable(self):
        settings = (ROOT / "config/settings.json").read_text(encoding="utf-8")
        self.assertIn('"publish_uncertain_movies": false', settings)
        self.assertIn('"strict_publish": true', settings)


if __name__ == "__main__":
    unittest.main()
