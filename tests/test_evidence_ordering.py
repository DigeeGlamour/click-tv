"""Evidence reaches the model before any hide path can act on an item.

The first version of this integration supplied evidence at the end of the
pipeline. bd_verifier.verify_bd_stream hides items during verification - three
call sites, all earlier - so those decisions were taken while the model had
nothing to reason with. Every gate there returned "no per-route evidence for
this item; caller decision stands", which is a gate that cannot refuse anything.

Codex found this by reading the line numbers. These tests pin the ordering so it
cannot silently invert again, and pin the behaviour that ordering buys.
"""
import datetime as dt
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import fast_pipeline as fp  # noqa: E402
from scanner import route_evidence as rev  # noqa: E402
from scanner import route_evidence_cache as cache  # noqa: E402
from scanner import route_evidence_pipeline as pipeline  # noqa: E402
from scanner import visibility_audit as va  # noqa: E402

SOURCE = (ROOT / "scanner" / "fast_pipeline.py").read_text(encoding="utf-8")
ROUTE = "https://tenant-a.akamaized.net/live/index.m3u8"


def _line_of(needle, start=0):
    index = SOURCE.index(needle, start)
    return SOURCE[:index].count("\n") + 1


class OrderingTests(unittest.TestCase):
    def test_the_preload_exists(self):
        self.assertIn("_load_cached_evidence_before_verification", SOURCE)

    def test_evidence_is_preloaded_before_bd_verifier_can_hide(self):
        pipeline_start = SOURCE.index("def run_fast_verification_pipeline")
        preload = _line_of(
            "_load_cached_evidence_before_verification()", pipeline_start
        )
        first_hide = _line_of("verify_bd_stream(", pipeline_start)
        self.assertLess(
            preload, first_hide,
            f"evidence is supplied at line {preload}, after bd_verifier hides "
            f"at line {first_hide}",
        )

    def test_fresh_observations_are_still_added_after_verification(self):
        # This scan's own observations belong to the NEXT run. One scan cannot
        # both observe an item and use that observation to second-guess itself.
        pipeline_start = SOURCE.index("def run_fast_verification_pipeline")
        first_hide = _line_of("verify_bd_stream(", pipeline_start)
        fresh = _line_of("_supply_scan_evidence(final_results)")
        self.assertGreater(fresh, first_hide)

    def test_historical_records_are_not_supplied_twice(self):
        window = SOURCE[SOURCE.index("def _supply_scan_evidence("):]
        window = window[: window.index("\ndef ")]
        self.assertIn("supply_evidence(fresh)", window)
        self.assertNotIn("historical + fresh", window)


class GateBehaviourTests(unittest.TestCase):
    """What the ordering actually buys: a gate that can refuse."""

    # Relative to now, deliberately. A fixed date (2026-08-20) sat exactly on
    # the cache's retention boundary the moment that window was shortened to 7
    # days, and both tests failed with "0 not greater than 0" - measuring the
    # retention constant instead of the ordering they exist to check. Two hours
    # ago is inside any retention window this cache would sensibly have.
    BASE = (
        dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=2)
    ).timestamp()

    def setUp(self):
        self._saved_key = os.environ.get(rev.HMAC_KEY_ENV)
        os.environ[rev.HMAC_KEY_ENV] = "k" * 32
        self._tmp = tempfile.TemporaryDirectory()
        self._saved_path = cache.DEFAULT_PATH
        cache.DEFAULT_PATH = str(Path(self._tmp.name) / "cache.json")
        va.clear_evidence()

    def tearDown(self):
        va.clear_evidence()
        cache.DEFAULT_PATH = self._saved_path
        self._tmp.cleanup()
        if self._saved_key is None:
            os.environ.pop(rev.HMAC_KEY_ENV, None)
        else:
            os.environ[rev.HMAC_KEY_ENV] = self._saved_key

    def _seed(self, fatal):
        key = rev.configured_hmac_key()
        metrics = {
            "announced_render_tracks": ["video", "audio"],
            "progressing_tracks": [],
            "first_frame_seconds": None,
            "startup_seconds": None,
            "media_progress_seconds": 0.0,
            "cumulative_stall_seconds": 120,
            "fatal_errors": list(fatal),
            "recovered_to_pass_floor": False,
        }
        records = []
        for offset in (0, 200):
            stamp = dt.datetime.fromtimestamp(
                self.BASE + offset, tz=dt.timezone.utc
            ).isoformat()
            observation = {
                "playback_metrics": metrics,
                "browser_profile": "desktop_chrome",
                "failed_profiles": list(rev.DECLARED_TARGET_MATRIX),
                "observed_at": stamp,
            }
            records.extend(
                pipeline.build_route_evidence(
                    ROUTE,
                    scanner=dict(observation),
                    proxy=dict(observation),
                    hmac_key=key,
                )
            )
        cache.append(records, path=cache.DEFAULT_PATH, now=self.BASE + 200)
        return {"name": "X", "url": ROUTE, "backups": []}

    def test_a_403_from_a_previous_scan_blocks_bd_verifier(self):
        """The founding case: a channel 403ing from a datacentre egress.

        Before the ordering fix bd_verifier would have hidden this item with the
        gate reporting no evidence, because the evidence arrived afterwards.
        """
        item = self._seed(['HttpStatusCodeInvalid {"code":403}'])
        self.assertGreater(fp._load_cached_evidence_before_verification(), 0)
        allowed, why = va.model_permits_hide(
            "bd_verifier.confirmed_permanent_http", item
        )
        self.assertFalse(allowed, why)

    def test_a_real_route_failure_still_permits_the_hide(self):
        item = self._seed(["source produced no data"])
        self.assertGreater(fp._load_cached_evidence_before_verification(), 0)
        allowed, why = va.model_permits_hide(
            "bd_verifier.confirmed_permanent_http", item
        )
        self.assertTrue(allowed, why)

    def test_without_a_key_nothing_is_preloaded(self):
        os.environ.pop(rev.HMAC_KEY_ENV, None)
        self.assertEqual(fp._load_cached_evidence_before_verification(), 0)

    def test_an_empty_cache_preloads_nothing(self):
        self.assertEqual(fp._load_cached_evidence_before_verification(), 0)

    def test_a_broken_cache_cannot_break_the_scan(self):
        Path(cache.DEFAULT_PATH).write_text("not json", encoding="utf-8")
        self.assertEqual(fp._load_cached_evidence_before_verification(), 0)


if __name__ == "__main__":
    unittest.main()
