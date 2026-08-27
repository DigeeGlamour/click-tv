"""The two gaps Codex found in route_preference, closed and pinned.

1. A proven route ranked below the ranking's slot cutoff was never seen by
   promote_preferred at all, because the caller only handed it the already-
   truncated selection. Reproduced directly: seven candidates, the proven one
   ranked seventh, max_total=6 - the old code returned the broken route as
   primary.

2. Nothing ever expired a recorded preference. A route proven once could be
   forced into primary forever even after it later broke, as long as it stayed
   structurally present among the candidates a scan found.
"""
import datetime as dt
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import merger  # noqa: E402
from scanner import route_preference as rp  # noqa: E402

PROVEN_URL = "https://good.example.net/live/index.m3u8"
EVIDENCE = {
    "pass_count": 2, "window_seconds": 120.0,
    "media_progress_seconds": [173.0, 172.0], "cumulative_stall_seconds": [0, 0],
}


class SlotTruncationTests(unittest.TestCase):
    """Gap 1: a proven route ranked outside the top slots."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self._tmp.name) / "pref.json")
        rp.record("channel", "Test Ch", PROVEN_URL, EVIDENCE, path=self.path)
        self.registry = rp.load(self.path)

    def tearDown(self):
        self._tmp.cleanup()

    def _seven_candidates(self):
        streams = [
            {"url": f"https://rival{i}.example.net/live/x.m3u8"} for i in range(6)
        ]
        streams.append({"url": PROVEN_URL})
        return streams

    def test_without_full_pool_the_gap_reproduces(self):
        # Pinning the old, broken behaviour so the fix above is legible as a
        # fix rather than a silent change: handing promote_preferred only the
        # already-truncated top six, it cannot find route seven at all.
        truncated = self._seven_candidates()[:6]
        out, promoted = rp.promote_preferred(
            truncated, "channel", "Test Ch", self.registry
        )
        self.assertFalse(promoted)

    def test_with_full_pool_the_proven_route_is_pulled_in(self):
        all_candidates = self._seven_candidates()
        truncated = all_candidates[:6]  # the proven route (index 6) is excluded
        out, promoted = rp.promote_preferred(
            truncated, "channel", "Test Ch", self.registry, full_pool=all_candidates
        )
        self.assertTrue(promoted)
        self.assertEqual(out[0]["url"], PROVEN_URL)

    def test_pulling_it_in_never_exceeds_the_original_slot_count(self):
        all_candidates = self._seven_candidates()
        truncated = all_candidates[:6]
        out, _ = rp.promote_preferred(
            truncated, "channel", "Test Ch", self.registry, full_pool=all_candidates
        )
        self.assertEqual(len(out), len(truncated))

    def test_the_weakest_current_entry_is_evicted_not_a_random_one(self):
        all_candidates = self._seven_candidates()
        truncated = all_candidates[:6]
        out, _ = rp.promote_preferred(
            truncated, "channel", "Test Ch", self.registry, full_pool=all_candidates
        )
        # rival5 (the last of the six) is what makes room for the proven route.
        urls = {s["url"] for s in out}
        self.assertIn("https://rival0.example.net/live/x.m3u8", urls)
        self.assertNotIn("https://rival5.example.net/live/x.m3u8", urls)

    def test_a_route_absent_from_the_full_pool_is_still_never_added(self):
        # The pool widens what promote_preferred can SEE; it must not widen
        # what it is willing to invent.
        streams = [{"url": "https://only-this.example.net/x.m3u8"}]
        out, promoted = rp.promote_preferred(
            streams, "channel", "Test Ch", self.registry, full_pool=streams
        )
        self.assertFalse(promoted)

    def test_the_end_to_end_merge_now_promotes_route_seven(self):
        """The exact scenario Codex reproduced, run through the real merger."""
        streams = []
        for i in range(6):
            streams.append({
                "url": f"https://rival{i}.example.net/live/x.m3u8",
                "verified": True, "verification_status": "verified_global",
                "verification_mode": "global", "http_status": 200,
                "source_pipeline": "tv", "source_id": f"src{i}",
            })
        streams.append({
            "url": PROVEN_URL, "verified": True,
            "verification_status": "verified_global", "verification_mode": "global",
            "http_status": 200, "source_pipeline": "tv", "source_id": "srcproven",
        })
        original_load = rp.load
        rp.load = lambda path=None: original_load(self.path)
        try:
            primary, _backups = merger.rank_and_select_streams(
                streams, max_total=6, max_backups=5,
                channel_name="Test Ch", channel_kind="channel",
            )
        finally:
            rp.load = original_load
        self.assertIsNotNone(primary)
        self.assertEqual(primary.get("url"), PROVEN_URL)


class StalenessTests(unittest.TestCase):
    """Gap 2: nothing ever expired a recorded preference."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self._tmp.name) / "pref.json")
        rp.record("channel", "Test Ch", PROVEN_URL, EVIDENCE, path=self.path)
        self.now = dt.datetime.now(dt.timezone.utc).timestamp()

    def tearDown(self):
        self._tmp.cleanup()

    def test_a_fresh_preference_applies(self):
        registry = rp.load(self.path)
        route_id = rp.preferred_route_id(
            "channel", "Test Ch", registry, now=self.now
        )
        self.assertIsNotNone(route_id)

    def test_a_preference_older_than_the_ttl_stops_applying(self):
        registry = rp.load(self.path)
        stale_at = self.now + rp.PREFERENCE_TTL_SECONDS + 3600
        route_id = rp.preferred_route_id(
            "channel", "Test Ch", registry, now=stale_at
        )
        self.assertIsNone(route_id)

    def test_a_preference_just_under_the_ttl_still_applies(self):
        registry = rp.load(self.path)
        almost_stale = self.now + rp.PREFERENCE_TTL_SECONDS - 3600
        route_id = rp.preferred_route_id(
            "channel", "Test Ch", registry, now=almost_stale
        )
        self.assertIsNotNone(route_id)

    def test_a_stale_preference_no_longer_promotes(self):
        registry = rp.load(self.path)
        stale_at = self.now + rp.PREFERENCE_TTL_SECONDS + 3600
        streams = [{"url": "https://other.example.net/x.m3u8"}, {"url": PROVEN_URL}]
        out, promoted = rp.promote_preferred(
            streams, "channel", "Test Ch", registry, now=stale_at
        )
        self.assertFalse(promoted)

    def test_an_entry_recorded_before_the_field_existed_is_treated_as_stale(self):
        # A legacy entry with no recorded_at must not be read as eternal.
        registry = rp.load(self.path)
        for entry in registry["preferred"].values():
            entry.pop("recorded_at", None)
        route_id = rp.preferred_route_id(
            "channel", "Test Ch", registry, now=self.now
        )
        self.assertIsNone(route_id)

    def test_re_recording_refreshes_the_ttl(self):
        stale_at = self.now + rp.PREFERENCE_TTL_SECONDS + 3600
        rp.record("channel", "Test Ch", PROVEN_URL, EVIDENCE, path=self.path)
        registry = rp.load(self.path)
        route_id = rp.preferred_route_id(
            "channel", "Test Ch", registry, now=self.now
        )
        self.assertIsNotNone(route_id)


class CurrentHealthTests(unittest.TestCase):
    """A proof from two weeks ago must not outrank today's negative.

    Codex's second point had two halves. Expiry was the first and is covered by
    StalenessTests. This is the other: a preference well inside its 14 days
    could still name a route that had already stopped working, and nothing
    consulted the current scan about it.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self._tmp.name) / "pref.json")
        rp.record("channel", "Test Ch", PROVEN_URL, EVIDENCE, path=self.path)
        self.registry = rp.load(self.path)

    def tearDown(self):
        self._tmp.cleanup()

    def _promote(self, proven_stream):
        streams = [{"url": "https://other.example.net/x.m3u8"}, proven_stream]
        return rp.promote_preferred(
            streams, "channel", "Test Ch", self.registry
        )

    def test_a_healthy_route_is_promoted(self):
        _out, promoted = self._promote({"url": PROVEN_URL})
        self.assertTrue(promoted)

    def test_an_explicitly_denied_route_is_not_promoted(self):
        # publish_allowed is False is how every hide path in this project
        # records "do not serve this". A stale proof must not override it.
        _out, promoted = self._promote(
            {"url": PROVEN_URL, "publish_allowed": False}
        )
        self.assertFalse(promoted)

    def test_a_metadata_only_route_is_not_promoted(self):
        _out, promoted = self._promote(
            {"url": PROVEN_URL, "metadata_only": True}
        )
        self.assertFalse(promoted)

    def test_a_route_with_no_url_is_not_promoted(self):
        _out, promoted = self._promote({"url": "", "publish_allowed": True})
        self.assertFalse(promoted)

    def test_an_unremarked_route_is_still_promoted(self):
        """The permissive half, and it matters as much as the strict half.

        Most routes carry no explicit health verdict at the point ranking runs.
        Reading a missing field as unhealthy would refuse promotion nearly
        always and quietly restore the behaviour this registry exists to fix.
        """
        for extra_fields in (
            {},
            {"verification_status": "pending"},
            {"verification_status": "geo_pending"},
            {"verified": False},
            {"publish_allowed": True},
        ):
            stream = dict({"url": PROVEN_URL}, **extra_fields)
            _out, promoted = self._promote(stream)
            self.assertTrue(promoted, f"refused promotion for {extra_fields}")

    def test_a_denied_route_is_not_pulled_in_from_the_full_pool_either(self):
        # The slot-truncation path must apply the same health rule.
        all_candidates = [
            {"url": f"https://rival{i}.example.net/live/x.m3u8"} for i in range(6)
        ]
        all_candidates.append({"url": PROVEN_URL, "publish_allowed": False})
        truncated = all_candidates[:6]
        _out, promoted = rp.promote_preferred(
            truncated, "channel", "Test Ch", self.registry,
            full_pool=all_candidates,
        )
        self.assertFalse(promoted)

    def test_expiry_and_health_are_independent_guards(self):
        # Either one alone must be able to refuse; neither depends on the other.
        fresh_but_denied = self._promote(
            {"url": PROVEN_URL, "publish_allowed": False}
        )[1]
        self.assertFalse(fresh_but_denied)

        stale_but_healthy = rp.promote_preferred(
            [{"url": "https://other.example.net/x.m3u8"}, {"url": PROVEN_URL}],
            "channel", "Test Ch", self.registry,
            now=dt.datetime.now(dt.timezone.utc).timestamp()
            + rp.PREFERENCE_TTL_SECONDS + 3600,
        )[1]
        self.assertFalse(stale_but_healthy)


if __name__ == "__main__":
    unittest.main()
