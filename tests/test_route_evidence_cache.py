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
class SizeIsBoundedTests(unittest.TestCase):
    """The cache is committed by every scan, so its size is a repo problem.

    It reached 60.4 MB across 26,396 routes with retention unable to trim a
    single one - a scan probes 3,200 channels and 21,400 movies, so every route
    was inside the window. GitHub refuses a file above 100 MB, so the growth
    had a deadline rather than a limit.
    """

    def test_a_hard_route_ceiling_exists(self):
        self.assertGreater(cache.MAX_ROUTES, 0)
        self.assertLessEqual(cache.MAX_ROUTES, 50_000)

    def test_pruning_drops_the_least_recently_observed_routes(self):
        import datetime as dt

        now = dt.datetime(2026, 8, 27, tzinfo=dt.timezone.utc)
        routes = {}
        for index in range(cache.MAX_ROUTES + 50):
            moment = now - dt.timedelta(seconds=index)
            routes[f"host{index}.example.net/live/x.m3u8"] = [{
                "route_id": f"host{index}.example.net/live/x.m3u8",
                "observed_at": moment.isoformat(),
            }]
        pruned = cache._prune({"version": 1, "routes": routes}, now=now.timestamp())
        self.assertEqual(len(pruned["routes"]), cache.MAX_ROUTES)
        self.assertIn("host0.example.net/live/x.m3u8", pruned["routes"])
        self.assertNotIn(
            f"host{cache.MAX_ROUTES + 49}.example.net/live/x.m3u8",
            pruned["routes"],
        )

    def test_the_committed_cache_stays_under_the_github_warning(self):
        path = Path(cache.DEFAULT_PATH)
        if not path.is_file():
            self.skipTest("no cache committed")
        megabytes = path.stat().st_size / (1024 * 1024)
        self.assertLess(
            megabytes, 50,
            f"the cache is {megabytes:.1f} MB; GitHub warns at 50 and refuses "
            "at 100, and every scan commits it",
        )

    def test_it_is_written_without_indentation(self):
        source = (ROOT / "scanner" / "route_evidence_cache.py").read_text(
            encoding="utf-8"
        )
        # The call, not the file: the comment above it names indent=2 to say
        # what was replaced, and a substring search over the whole source read
        # that as the thing it was forbidding.
        call = source.split("json.dump(", 1)[-1].split(")", 1)[0]
        self.assertIn("separators", source)
        self.assertNotIn("indent", call)
