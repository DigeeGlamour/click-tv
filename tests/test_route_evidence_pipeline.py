"""Turning two measured vantages into records `may_hide` can actually read.

This is the piece that was missing. `may_hide` always wanted escalatable
evidence for the same route, from two independent vantages, in separate time
windows. Vantage independence is now measured, so what remained was something to
assemble observations into complete, scoped records - and to refuse to assemble
them when the inputs cannot support it.

The tests below are mostly about that refusal, because a partial record is worse
than no record: `evidence_is_complete` reads a missing field as `unknown`, so a
caller handed a partial record believes evidence exists when it does not.
"""
import datetime as dt
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import route_evidence as rev  # noqa: E402
from scanner import route_evidence_pipeline as rp  # noqa: E402

ROUTE = "https://tenant-a.akamaized.net/live/index.m3u8"
BASE = dt.datetime(2026, 8, 23, 10, 0, 0, tzinfo=dt.timezone.utc)

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
    """Records need a keyed tenant to be complete, so these run with a key."""

    def setUp(self):
        self._saved = os.environ.get(rev.HMAC_KEY_ENV)
        os.environ[rev.HMAC_KEY_ENV] = "k" * 32

    def tearDown(self):
        if self._saved is None:
            os.environ.pop(rev.HMAC_KEY_ENV, None)
        else:
            os.environ[rev.HMAC_KEY_ENV] = self._saved

    def _records(self, *offsets, metrics=None, profiles=None):
        key = rev.configured_hmac_key()
        out = []
        for offset in offsets:
            stamp = (BASE + dt.timedelta(seconds=offset)).isoformat()
            observation = {
                "playback_metrics": metrics if metrics is not None else dict(FAIL_METRICS),
                "browser_profile": "desktop_chrome",
                "failed_profiles": (
                    profiles if profiles is not None else list(rev.DECLARED_TARGET_MATRIX)
                ),
                "observed_at": stamp,
            }
            out.extend(
                rp.build_route_evidence(
                    ROUTE, scanner=dict(observation), proxy=dict(observation), hmac_key=key
                )
            )
        return out


class CompletenessTests(_Keyed):
    def test_a_record_is_built_for_each_vantage(self):
        records = self._records(0)
        self.assertEqual(len(records), 2)
        ids = {r["test_vantage"]["id"] for r in records}
        self.assertEqual(ids, {"scanner_egress", "proxy_egress"})

    def test_every_record_is_complete(self):
        for record in self._records(0):
            complete, missing = rev.evidence_is_complete(record)
            self.assertTrue(complete, missing)

    def test_no_record_carries_forbidden_material(self):
        for record in self._records(0):
            self.assertFalse(rev.evidence_contains_forbidden_material(record))

    def test_a_record_is_refused_without_a_keyed_tenant(self):
        # Without a key the tenant field is None, the record is incomplete, and a
        # partial record would read as `unknown` evidence that exists. Refusing
        # is the correct outcome, not a bug.
        os.environ.pop(rev.HMAC_KEY_ENV, None)
        record = rp.build_record(
            route_url=ROUTE, vantage=rp.SCANNER_VANTAGE, status=503
        )
        self.assertIsNone(record)

    def test_a_record_is_refused_without_a_route(self):
        self.assertIsNone(
            rp.build_record(route_url="", vantage=rp.SCANNER_VANTAGE, status=503)
        )

    def test_the_two_vantages_are_measurably_independent(self):
        records = self._records(0)
        self.assertTrue(
            rev.vantages_are_independent(
                records[0]["test_vantage"], records[1]["test_vantage"]
            )
        )


class SupportsHideTests(_Keyed):
    def test_two_observations_in_one_moment_are_not_two_windows(self):
        # The cache-window mistake, in its purest form: both records are built in
        # one call, microseconds apart, and would otherwise satisfy "separate
        # time windows" instantly.
        verdict = rp.evidence_supports_hide(self._records(0))
        self.assertFalse(verdict["supports_hide"])
        self.assertTrue(
            any("time windows" in m for m in verdict["missing"]), verdict["missing"]
        )

    def test_observations_inside_the_cache_ttl_are_not_two_windows(self):
        # Measured CDN cache TTLs were 17-23 s; the locked separation is 120 s.
        verdict = rp.evidence_supports_hide(self._records(0, 30))
        self.assertFalse(verdict["supports_hide"])

    def test_two_separated_windows_do_support_a_hide(self):
        verdict = rp.evidence_supports_hide(self._records(0, 200))
        self.assertTrue(verdict["supports_hide"], verdict["missing"])
        self.assertGreaterEqual(verdict["distinct_windows"], 2)
        self.assertTrue(verdict["independent_vantages"])

    def test_a_transport_only_observation_never_supports_a_hide(self):
        # A 503 with no browser run is environment-less, so it cannot reach
        # global scope and must not accumulate toward a hide.
        key = rev.configured_hmac_key()
        records = []
        for offset in (0, 200):
            stamp = (BASE + dt.timedelta(seconds=offset)).isoformat()
            records.extend(
                rp.build_route_evidence(
                    ROUTE,
                    scanner={"status": 503, "observed_at": stamp},
                    proxy={"status": 503, "observed_at": stamp},
                    hmac_key=key,
                )
            )
        verdict = rp.evidence_supports_hide(records)
        self.assertFalse(verdict["supports_hide"])

    def test_an_incomplete_matrix_never_supports_a_hide(self):
        # Global scope needs the whole declared matrix; three of four is not it.
        partial = list(rev.DECLARED_TARGET_MATRIX)[:3]
        verdict = rp.evidence_supports_hide(
            self._records(0, 200, profiles=partial)
        )
        self.assertFalse(verdict["supports_hide"])

    def test_a_vantage_block_never_supports_a_hide(self):
        metrics = dict(FAIL_METRICS, fatal_errors=[
            'mpegts NetworkError/HttpStatusCodeInvalid {"code":403}'
        ])
        verdict = rp.evidence_supports_hide(
            self._records(0, 200, metrics=metrics)
        )
        self.assertFalse(verdict["supports_hide"])

    def test_a_decoder_limit_never_supports_a_hide(self):
        metrics = dict(FAIL_METRICS, fatal_errors=["media element error code 3"])
        verdict = rp.evidence_supports_hide(
            self._records(0, 200, metrics=metrics)
        )
        self.assertFalse(verdict["supports_hide"])

    def test_a_pass_never_supports_a_hide(self):
        metrics = {
            "announced_render_tracks": ["video", "audio"],
            "progressing_tracks": ["video", "audio"],
            "first_frame_seconds": 1.5,
            "startup_seconds": 2.0,
            "media_progress_seconds": 119.0,
            "cumulative_stall_seconds": 0.5,
            "fatal_errors": [],
        }
        verdict = rp.evidence_supports_hide(
            self._records(0, 200, metrics=metrics)
        )
        self.assertFalse(verdict["supports_hide"])

    def test_the_report_names_what_is_missing(self):
        # A caller that cannot tell "non-escalatable" from "only one vantage"
        # cannot act sensibly, so the gap is always named.
        verdict = rp.evidence_supports_hide([])
        self.assertFalse(verdict["supports_hide"])
        self.assertTrue(verdict["missing"])

    def test_the_pipeline_never_decides_visibility(self):
        source = (ROOT / "scanner" / "route_evidence_pipeline.py").read_text(
            encoding="utf-8"
        )
        for forbidden in ("publish_allowed", "mark_unproven", "mark_confirmed"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
