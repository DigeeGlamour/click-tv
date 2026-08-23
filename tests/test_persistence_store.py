"""Cross-run storage for the persistence counter.

The store is what makes escalation possible at all: the locked window is 1800 s
with observations at least 120 s apart, which no single scan spans, so before
this existed the counter could never pass one and the escalation path was inert.

Every test here checks the same bias in a different place - a failure of this
store must lose evidence, never invent it, because losing evidence can only make
a channel harder to hide.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import persistence_store as store  # noqa: E402
from scanner import route_evidence as rev  # noqa: E402

T0 = 1_700_000_000.0

PASS_METRICS = {
    "announced_render_tracks": ["video", "audio"],
    "progressing_tracks": ["video", "audio"],
    "first_frame_seconds": 1.5,
    "startup_seconds": 2.0,
    "media_progress_seconds": 119.0,
    "cumulative_stall_seconds": 0.5,
    "fatal_errors": [],
}


class StoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self._tmp.name) / "route-persistence.json")

    def tearDown(self):
        self._tmp.cleanup()

    def _transients(self, count, spacing=600):
        for index in range(count):
            store.record(
                "r1",
                {
                    "observed_at": T0 + index * spacing,
                    "verdict": rev.ADVISORY_TRANSIENT_NETWORK,
                    "kind": "http_status",
                },
                path=self.path,
                now=T0 + index * spacing,
            )

    def test_evidence_survives_across_runs(self):
        # The whole reason the store exists.
        self._transients(3)
        state = store.state_for("r1", path=self.path, now=T0 + 1200)
        self.assertEqual(state["state"], rev.PERSISTENT_UNAVAILABLE_CANDIDATE)
        self.assertGreaterEqual(state["counter"], 2)

    def test_a_verified_full_pass_resets_the_counter(self):
        self._transients(3)
        store.record(
            "r1",
            {
                "observed_at": T0 + 1300,
                "verdict": rev.PROVEN,
                "kind": "full_playback_session",
                "window_seconds": 120.0,
                "playback_metrics": dict(PASS_METRICS),
            },
            path=self.path,
            now=T0 + 1300,
        )
        state = store.state_for("r1", path=self.path, now=T0 + 1300)
        self.assertEqual(state["state"], rev.UNKNOWN)
        self.assertEqual(state["counter"], 0)
        self.assertIsNotNone(state["reset_at"])

    def test_a_bare_pass_claim_does_not_reset(self):
        # A reset is re-verified from the numbers. Taking the stored verdict's
        # word for it is the same loose reading of "success" that let HTTP 200
        # stand in for working playback.
        self._transients(3)
        store.record(
            "r1",
            {
                "observed_at": T0 + 1300,
                "verdict": rev.PROVEN,
                "kind": "full_playback_session",
                "window_seconds": 120.0,
            },
            path=self.path,
            now=T0 + 1300,
        )
        state = store.state_for("r1", path=self.path, now=T0 + 1300)
        self.assertEqual(state["state"], rev.PERSISTENT_UNAVAILABLE_CANDIDATE)
        self.assertIsNone(state["reset_at"])

    def test_no_url_or_credential_is_ever_written(self):
        store.record(
            "r1",
            {
                "observed_at": T0,
                "verdict": rev.ADVISORY_TRANSIENT_NETWORK,
                "kind": "http_status",
                "url": "https://host.example.net/live/x.m3u8?token=SECRETVALUE",
                "final_origin": "https://host.example.net/live/x.m3u8",
                "headers": {"Authorization": "Bearer abc"},
            },
            path=self.path,
            now=T0,
        )
        blob = Path(self.path).read_text(encoding="utf-8")
        self.assertNotIn("SECRETVALUE", blob)
        self.assertNotIn("token", blob)
        self.assertNotIn("Authorization", blob)
        self.assertFalse(
            rev.evidence_contains_forbidden_material(json.loads(blob))
        )

    def test_an_unreadable_store_is_empty_not_an_error(self):
        Path(self.path).write_text("this is not json", encoding="utf-8")
        loaded = store.load(self.path)
        self.assertEqual(loaded["routes"], {})
        state = store.state_for("r1", path=self.path, now=T0)
        self.assertEqual(state["state"], rev.UNKNOWN)

    def test_a_missing_store_is_empty_not_an_error(self):
        state = store.state_for("nope", path=str(Path(self._tmp.name) / "absent.json"))
        self.assertEqual(state["state"], rev.UNKNOWN)

    def test_observations_without_a_timestamp_are_dropped(self):
        store.record(
            "r1",
            {"verdict": rev.ADVISORY_TRANSIENT_NETWORK, "kind": "http_status"},
            path=self.path,
            now=T0,
        )
        self.assertEqual(store.load(self.path)["routes"], {})

    def test_expired_evidence_is_pruned(self):
        self._transients(3)
        far_future = T0 + rev.PERSISTENCE_TTL_SECONDS + 5000
        state = store.state_for("r1", path=self.path, now=far_future)
        self.assertEqual(state["state"], rev.UNKNOWN)
        pruned = store.prune(store.load(self.path), now=far_future)
        self.assertEqual(pruned["routes"], {})

    def test_a_single_route_cannot_grow_without_bound(self):
        for index in range(store.MAX_OBSERVATIONS_PER_ROUTE + 25):
            store.record(
                "r1",
                {
                    "observed_at": T0 + index * 5,
                    "verdict": rev.ADVISORY_TRANSIENT_NETWORK,
                    "kind": "http_status",
                },
                path=self.path,
                now=T0 + index * 5,
            )
        kept = store.load(self.path)["routes"]["r1"]
        self.assertLessEqual(len(kept), store.MAX_OBSERVATIONS_PER_ROUTE)

    def test_routes_do_not_contaminate_each_other(self):
        self._transients(3)
        state = store.state_for("other-route", path=self.path, now=T0 + 1200)
        self.assertEqual(state["state"], rev.UNKNOWN)

    def test_non_escalatable_evidence_never_matures(self):
        for index in range(6):
            store.record(
                "r2",
                {
                    "observed_at": T0 + index * 600,
                    "verdict": rev.ADVISORY_VANTAGE_BLOCKED,
                    "kind": "http_status",
                },
                path=self.path,
                now=T0 + index * 600,
            )
        state = store.state_for("r2", path=self.path, now=T0 + 3000)
        self.assertEqual(state["state"], rev.UNKNOWN)
        self.assertEqual(state["escalatable_observations"], 0)

    def test_an_empty_route_id_is_ignored(self):
        store.record(
            "",
            {"observed_at": T0, "verdict": rev.ADVISORY_TRANSIENT_NETWORK},
            path=self.path,
            now=T0,
        )
        self.assertEqual(store.load(self.path)["routes"], {})


if __name__ == "__main__":
    unittest.main()
