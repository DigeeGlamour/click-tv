"""Which route a channel leads with, kept outside the generated cards.

The Zee Bangla fix was written straight into data/channels/indian.json, and the
script that wrote it says in its own comment that the next scan rebuilds cards
from their sources and erases whatever was written on them. So that fix had a
shelf life of one scan - the same mistake that undid the seven restored channels,
repeated one step further along. Codex caught it before a scan did.

`sustained_proof` answers "may this channel be hidden?". This registry answers
"which of its routes should lead?", which is the half that decides whether a
viewer sees a working stream or a stuttering one.
"""
import json
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import route_evidence as rev  # noqa: E402
from scanner import route_preference as rp  # noqa: E402

PROVEN_URL = "https://good.example.net/live/index.m3u8"
BROKEN_URL = "https://broken.example.net/live/stream.ts"
EVIDENCE = {
    "pass_count": 2,
    "window_seconds": 120.0,
    "media_progress_seconds": [173.59, 172.08],
    "cumulative_stall_seconds": [0, 0],
    "browser_profile": "desktop_chrome",
    "evidence_report": "reports/zee-confirm-playback.json",
}


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self._tmp.name) / "route-preference.json")

    def tearDown(self):
        self._tmp.cleanup()

    def test_a_proven_route_is_recorded(self):
        written, why = rp.record(
            "channel", "Test Ch", PROVEN_URL, EVIDENCE, path=self.path
        )
        self.assertTrue(written, why)
        registry = rp.load(self.path)
        self.assertEqual(len(registry["preferred"]), 1)

    def test_one_pass_is_refused(self):
        # Same floor as every other promotion here: two independent sessions.
        written, why = rp.record(
            "channel", "Test Ch", PROVEN_URL,
            dict(EVIDENCE, pass_count=1), path=self.path,
        )
        self.assertFalse(written)
        self.assertIn("required", why)

    def test_a_claim_without_a_window_is_refused(self):
        evidence = {k: v for k, v in EVIDENCE.items() if k != "window_seconds"}
        written, _ = rp.record(
            "channel", "Test Ch", PROVEN_URL, evidence, path=self.path
        )
        self.assertFalse(written)

    def test_no_raw_url_is_stored(self):
        rp.record("channel", "Test Ch", PROVEN_URL, EVIDENCE, path=self.path)
        blob = Path(self.path).read_text(encoding="utf-8")
        self.assertNotIn(PROVEN_URL, blob)
        self.assertFalse(
            rev.evidence_contains_forbidden_material(json.loads(blob))
        )

    def test_an_unreadable_registry_expresses_no_preference(self):
        Path(self.path).write_text("not json", encoding="utf-8")
        self.assertEqual(rp.load(self.path)["preferred"], {})


class PromotionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self._tmp.name) / "route-preference.json")
        rp.record("channel", "Test Ch", PROVEN_URL, EVIDENCE, path=self.path)
        self.registry = rp.load(self.path)

    def tearDown(self):
        self._tmp.cleanup()

    def _hosts(self, streams):
        return [urllib.parse.urlsplit(s["url"]).hostname for s in streams]

    def test_the_proven_route_is_moved_to_the_front(self):
        streams = [{"url": BROKEN_URL}, {"url": PROVEN_URL}]
        out, promoted = rp.promote_preferred(
            streams, "channel", "Test Ch", self.registry
        )
        self.assertTrue(promoted)
        self.assertEqual(self._hosts(out)[0], "good.example.net")

    def test_nothing_is_lost_when_promoting(self):
        streams = [{"url": BROKEN_URL}, {"url": PROVEN_URL}]
        out, _ = rp.promote_preferred(
            streams, "channel", "Test Ch", self.registry
        )
        self.assertEqual(len(out), len(streams))
        self.assertEqual(set(self._hosts(out)), set(self._hosts(streams)))

    def test_a_route_that_is_already_first_is_left_alone(self):
        streams = [{"url": PROVEN_URL}, {"url": BROKEN_URL}]
        out, promoted = rp.promote_preferred(
            streams, "channel", "Test Ch", self.registry
        )
        self.assertFalse(promoted)
        self.assertEqual(self._hosts(out)[0], "good.example.net")

    def test_a_route_the_scan_did_not_find_is_never_added(self):
        # A route this scan could not see is a route it cannot vouch for.
        streams = [{"url": BROKEN_URL}]
        out, promoted = rp.promote_preferred(
            streams, "channel", "Test Ch", self.registry
        )
        self.assertFalse(promoted)
        self.assertEqual(len(out), 1)

    def test_a_channel_with_no_preference_is_untouched(self):
        streams = [{"url": BROKEN_URL}, {"url": PROVEN_URL}]
        out, promoted = rp.promote_preferred(
            streams, "channel", "Other Ch", self.registry
        )
        self.assertFalse(promoted)
        self.assertEqual(out, streams)

    def test_a_rotating_cache_buster_still_matches(self):
        # Otherwise the preference silently stops applying the first time the
        # source rotates its token - which is exactly how the fingerprint ledger
        # lost six channels.
        streams = [{"url": BROKEN_URL}, {"url": PROVEN_URL + "?_t=99999"}]
        out, promoted = rp.promote_preferred(
            streams, "channel", "Test Ch", self.registry
        )
        self.assertTrue(promoted)
        self.assertEqual(self._hosts(out)[0], "good.example.net")

    def test_it_can_only_promote_never_hide(self):
        """No code path here WRITES visibility.

        Checked as "never assigns", not "never mentions". The first version of
        this test forbade the substring outright, and that was wrong twice: it
        flagged the module's own prose saying it cannot hide, and then it
        flagged the health check reading `publish_allowed` in order to DECLINE
        promoting a route the scan had denied. Reading a hide flag to hold back
        is the opposite of hiding, and a test that cannot tell those apart
        blocks the correct fix.
        """
        import ast  # noqa: PLC0415

        path = ROOT / "scanner" / "route_preference.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))

        forbidden = {
            "publish_allowed", "player_visibility", "verification_status",
        }
        writes = []

        for node in ast.walk(tree):
            # item["publish_allowed"] = ...  /  obj.publish_allowed = ...
            targets = []
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
                targets = [node.target]
            for target in targets:
                if (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.slice, ast.Constant)
                    and target.slice.value in forbidden
                ):
                    writes.append(f"line {node.lineno}: subscript store "
                                  f"{target.slice.value}")
                if isinstance(target, ast.Attribute) and target.attr in forbidden:
                    writes.append(f"line {node.lineno}: attribute store "
                                  f"{target.attr}")
            # item.update(publish_allowed=...) / dict(publish_allowed=...)
            if isinstance(node, ast.Call):
                for keyword in node.keywords:
                    if keyword.arg in forbidden:
                        writes.append(f"line {node.lineno}: keyword "
                                      f"{keyword.arg}")

        self.assertEqual(writes, [], f"module writes visibility: {writes}")

        # And it must not call anything that hides on its behalf.
        code = ast.unparse(tree)
        for call in ("may_hide", "mark_unproven", "mark_confirmed",
                     "model_permits_hide"):
            self.assertNotIn(call, code, f"code calls {call}")

    def test_the_health_check_only_reads_visibility(self):
        # The counterpart: reading is allowed and required. If this stops being
        # true the stale-proof guard has been removed.
        source = (ROOT / "scanner" / "route_preference.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('stream.get("publish_allowed") is False', source)

    def test_promotion_returns_the_same_streams(self):
        # The strongest form of "never removes": the output is a permutation.
        streams = [{"url": BROKEN_URL}, {"url": PROVEN_URL}, {"url": "https://c/x"}]
        out, _ = rp.promote_preferred(
            streams, "channel", "Test Ch", self.registry
        )
        self.assertEqual(sorted(map(repr, out)), sorted(map(repr, streams)))


class MergerIntegrationTests(unittest.TestCase):
    """The registry has to be consulted where the primary is actually chosen."""

    def test_the_merger_consults_the_registry(self):
        source = (ROOT / "scanner" / "merger.py").read_text(encoding="utf-8")
        self.assertIn("route_preference", source)
        self.assertIn("promote_preferred", source)

    def test_promotion_outranks_the_incumbent_hold(self):
        # An incumbent that has not passed the acceptance must not keep its place
        # over one that has, or the hysteresis rule would undo the promotion on
        # every scan.
        source = (ROOT / "scanner" / "merger.py").read_text(encoding="utf-8")
        promote_at = source.index("promote_preferred")
        hold_at = source.index("if previous_primary_identity:")
        self.assertLess(promote_at, hold_at)
        self.assertIn('previous_primary_identity = ""', source)

    def test_the_live_registry_names_zee_bangla(self):
        registry = rp.load()
        if not registry["preferred"]:
            self.skipTest("no live preferences recorded")
        channels = {v.get("channel") for v in registry["preferred"].values()}
        self.assertIn("Zee Bangla", channels)


if __name__ == "__main__":
    unittest.main()
