"""Cross-scan persistence for evidence records, and why the old TTL is wrong here.

A scan is one process. Anything built and kept only in that process's memory
cannot be combined with a later scan's observation, so "two separate time
windows" was structurally unreachable no matter how the pipeline was wired
within one run. This module is what carries a record from one scan to the next.
"""
import datetime as dt
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import route_evidence as rev  # noqa: E402
from scanner import route_evidence_cache as cache  # noqa: E402
from scanner import route_evidence_pipeline as pipeline  # noqa: E402

BASE = dt.datetime(2026, 8, 23, 10, 0, 0, tzinfo=dt.timezone.utc).timestamp()
ROUTE = "https://tenant-a.akamaized.net/live/index.m3u8"

FAIL_METRICS = {
    "announced_render_tracks": ["video", "audio"],
    "progressing_tracks": [],
    "first_frame_seconds": None,
    "startup_seconds": None,
    "media_progress_seconds": 0.0,
    "cumulative_stall_seconds": 120,
    "fatal_errors": ["source produced no data"],
    "recovered_to_pass_floor": False,
}


class _Keyed(unittest.TestCase):
    def setUp(self):
        import os  # noqa: PLC0415

        self._saved = os.environ.get(rev.HMAC_KEY_ENV)
        os.environ[rev.HMAC_KEY_ENV] = "k" * 32
        self._tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self._tmp.name) / "cache.json")

    def tearDown(self):
        import os  # noqa: PLC0415

        self._tmp.cleanup()
        if self._saved is None:
            os.environ.pop(rev.HMAC_KEY_ENV, None)
        else:
            os.environ[rev.HMAC_KEY_ENV] = self._saved

    def _scan(self, offset_seconds):
        key = rev.configured_hmac_key()
        stamp = dt.datetime.fromtimestamp(
            BASE + offset_seconds, tz=dt.timezone.utc
        ).isoformat()
        observation = {
            "playback_metrics": dict(FAIL_METRICS),
            "browser_profile": "desktop_chrome",
            "failed_profiles": list(rev.DECLARED_TARGET_MATRIX),
            "observed_at": stamp,
        }
        return pipeline.build_route_evidence(
            ROUTE, scanner=dict(observation), proxy=dict(observation), hmac_key=key
        )


class PersistenceTests(_Keyed):
    def test_a_record_written_by_one_run_is_read_by_the_next(self):
        cache.append(self._scan(0), path=self.path, now=BASE)
        later = cache.all_records(path=self.path, now=BASE + 6 * 3600)
        self.assertTrue(later)

    def test_two_real_scan_cadences_apart_still_support_a_hide(self):
        """The actual measured channel-scan gap, not a synthetic one."""
        cache.append(self._scan(0), path=self.path, now=BASE)
        second_run_time = BASE + 6 * 3600
        prior = cache.all_records(path=self.path, now=second_run_time)
        combined = prior + self._scan(6 * 3600)
        verdict = pipeline.evidence_supports_hide(combined)
        self.assertTrue(verdict["supports_hide"], verdict["missing"])
        self.assertGreaterEqual(verdict["distinct_windows"], 2)
        self.assertTrue(verdict["independent_vantages"])
        cache.append(self._scan(6 * 3600), path=self.path, now=second_run_time)

    def test_bare_transport_evidence_alone_still_never_supports_a_hide(self):
        # Persistence changes what CAN be combined, not what the model accepts.
        # A transport-only record never reaches global scope.
        key = rev.configured_hmac_key()

        def transport_scan(offset):
            stamp = dt.datetime.fromtimestamp(
                BASE + offset, tz=dt.timezone.utc
            ).isoformat()
            return pipeline.build_route_evidence(
                ROUTE, scanner={"status": 503, "observed_at": stamp}, hmac_key=key
            )

        cache.append(transport_scan(0), path=self.path, now=BASE)
        prior = cache.all_records(path=self.path, now=BASE + 6 * 3600)
        combined = prior + transport_scan(6 * 3600)
        verdict = pipeline.evidence_supports_hide(combined)
        self.assertFalse(verdict["supports_hide"])

    def test_the_locked_persistence_ttl_would_have_been_too_short(self):
        # 1800 s (Phase 0b's persistence.ttl_seconds, a different mechanism)
        # is far shorter than the real ~6 h channel-scan gap - documenting why
        # this module uses its own, longer retention window instead.
        self.assertGreater(cache.RETENTION_SECONDS, 6 * 3600)
        self.assertGreater(cache.RETENTION_SECONDS, rev.PERSISTENCE_TTL_SECONDS)

    def test_expired_records_are_pruned(self):
        cache.append(self._scan(0), path=self.path, now=BASE)
        far_future = BASE + cache.RETENTION_SECONDS + 3600
        self.assertEqual(cache.all_records(path=self.path, now=far_future), [])

    def test_a_single_route_cannot_grow_without_bound(self):
        for i in range(cache.MAX_RECORDS_PER_ROUTE + 10):
            cache.append(self._scan(i), path=self.path, now=BASE + i)
        loaded = cache.load(self.path)
        for records in loaded["routes"].values():
            self.assertLessEqual(len(records), cache.MAX_RECORDS_PER_ROUTE)

    def test_an_unreadable_cache_is_empty_not_an_error(self):
        Path(self.path).write_text("not json", encoding="utf-8")
        self.assertEqual(cache.all_records(path=self.path), [])

    def test_no_credential_can_reach_the_cache_file(self):
        cache.append(self._scan(0), path=self.path, now=BASE)
        blob = Path(self.path).read_text(encoding="utf-8")
        import json as _json  # noqa: PLC0415

        self.assertFalse(
            rev.evidence_contains_forbidden_material(_json.loads(blob))
        )


class ScanIntegrationTests(unittest.TestCase):
    """The scan itself now reads and feeds both vantage and both time axes."""

    def test_fast_pipeline_uses_the_proxy_vantage(self):
        source = (ROOT / "scanner" / "fast_pipeline.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("proxy_http_status", source)
        self.assertIn("proxy=proxy_observation", source)

    def test_fast_pipeline_persists_across_runs(self):
        source = (ROOT / "scanner" / "fast_pipeline.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("route_evidence_cache", source)
        self.assertIn("cache.append(", source)
        self.assertIn("cache.all_records(", source)


if __name__ == "__main__":
    unittest.main()
