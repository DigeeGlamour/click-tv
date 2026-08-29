"""Creating a card for a channel that has none, and every way that must fail.

A scan creates cards, and normally that is the whole story. But a scan creates a
card only for a route its own network check accepted, and that check runs from a
US datacentre while the audience is in Bangladesh. Measured on 2026-08-29/30:
the SonyLIV URL the CI recorded as "HTTP 403: Forbidden" answered HTTP 200 with
a live manifest from Bangladesh minutes later, and the whole Sony family lost
its cards when hotstarplugx/plugsony.cstds.workers.dev were deleted while
stream.ottplus.bd carried the same channels at 720p throughout.

So a channel can be missing and perfectly playable, and this script puts it back
on evidence stronger than the check a scan makes: two independent 120 s browser
sessions through the site's own attempt plan.

The tests are almost entirely about refusals, because a script that writes cards
is exactly where a bad one would get in.
"""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "add_proven_channel", ROOT / "scripts" / "add-proven-channel.py"
)
add_proven_channel = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(add_proven_channel)

REPORT = {
    "window_seconds": 120.0,
    "browser_profile": "desktop_chrome",
    "results": [
        {
            "name": "Sony Max <- SONY MAX HD [smartplaytv-worker-stream]",
            "proven": True, "pass_count": 2,
            "observations": [
                {"attempt_route": "proxy", "playback_metrics": {
                    "media_progress_seconds": 119.9, "cumulative_stall_seconds": 0}},
                {"attempt_route": "proxy", "playback_metrics": {
                    "media_progress_seconds": 119.8, "cumulative_stall_seconds": 0}},
            ],
        },
        {
            "name": "One pass only",
            "proven": False, "pass_count": 1,
            "observations": [
                {"attempt_route": "proxy", "playback_metrics": {
                    "media_progress_seconds": 119.9, "cumulative_stall_seconds": 0}},
            ],
        },
    ],
}


class ProofGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "report.json"
        self.path.write_text(json.dumps(REPORT), encoding="utf-8")

    def test_a_two_session_pass_is_accepted(self):
        entry = add_proven_channel.proven_entry(
            str(self.path), "Sony Max <- SONY MAX HD [smartplaytv-worker-stream]"
        )
        self.assertIsNotNone(entry)
        self.assertEqual(2, entry["pass_count"])
        self.assertEqual([119.9, 119.8], entry["media_progress_seconds"])

    def test_one_pass_is_not_a_proof(self):
        self.assertIsNone(
            add_proven_channel.proven_entry(str(self.path), "One pass only")
        )

    def test_a_name_that_is_not_in_the_report_is_not_a_proof(self):
        self.assertIsNone(
            add_proven_channel.proven_entry(str(self.path), "Never measured")
        )

    def test_an_unreadable_report_is_not_a_proof(self):
        missing = Path(self.tmp.name) / "gone.json"
        self.assertIsNone(add_proven_channel.proven_entry(str(missing), "anything"))


class RefusalTests(unittest.TestCase):
    """Every path that must not produce a card."""

    def _run(self, **over):
        args = {
            "--channel": "Star Plus", "--category": "Indian",
            "--url": "https://stream.ottplus.bd/live/max_hd_abr/index.m3u8",
            "--proven-name": "Sony Max <- SONY MAX HD [smartplaytv-worker-stream]",
            "--report": self.report, "--source-id": "smartplaytv-worker-stream",
            "--data-dir": self.data,
        }
        args.update(over)
        argv = [item for pair in args.items() for item in pair] + ["--dry-run"]
        return add_proven_channel.main(argv)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        path = Path(self.tmp.name) / "report.json"
        path.write_text(json.dumps(REPORT), encoding="utf-8")
        self.report = str(path)
        # An empty catalogue of its own, so these tests say the same thing
        # whatever the live one currently holds. Pointed at the real one they
        # were order-dependent: the moment Sony Max was given a card, the
        # below-floor refusal test started passing through the "already has a
        # card" branch instead and stopped testing the floor at all.
        self.data = str(Path(self.tmp.name) / "channels")
        Path(self.data).mkdir()
        for category, filename in add_proven_channel.CATEGORY_FILE.items():
            (Path(self.data) / filename).write_text(
                json.dumps({"category": category, "count": 0, "channels": []}),
                encoding="utf-8",
            )
        patcher = mock.patch.object(add_proven_channel, "measure_height", return_value=720)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_an_unproven_route_is_refused(self):
        self.assertEqual(1, self._run(**{"--proven-name": "One pass only"}))

    def test_a_channel_not_on_the_category_allowlist_is_refused(self):
        """The curated list is not something a script may step around."""
        self.assertEqual(1, self._run(**{"--channel": "Zee News"}))

    def test_a_channel_the_router_sends_elsewhere_is_refused(self):
        """A card in the wrong category moves on the next scan, so writing one
        would be undone within hours and look like a bug in the scanner."""
        self.assertEqual(1, self._run(**{"--channel": "Star Plus",
                                         "--category": "Sports"}))

    def test_the_same_channel_in_another_category_is_removed(self):
        """JOO MUSIC routed to Other until it was added to the Indian identity
        registry. Writing the Indian card without removing the Other one put the
        identical URL on the site twice, which the catalogue's alias guard reads
        as two cards collapsing into one group with one of them to be lost."""
        other = Path(self.data) / "other.json"
        other.write_text(
            json.dumps({"category": "Other", "count": 1, "channels": [
                {"name": "Star Plus", "url": "https://x.test/a.m3u8"}]}),
            encoding="utf-8",
        )
        touched = add_proven_channel._remove_from_other_categories(
            self.data, "Indian", "Star Plus"
        )
        self.assertEqual([("Other", 0)], touched)
        left = json.loads(other.read_text(encoding="utf-8"))
        self.assertEqual([], left["channels"])
        self.assertEqual(0, left["count"])

    def test_it_leaves_the_target_category_alone(self):
        indian = Path(self.data) / "indian.json"
        indian.write_text(
            json.dumps({"category": "Indian", "count": 1, "channels": [
                {"name": "Star Plus", "url": "https://x.test/a.m3u8"}]}),
            encoding="utf-8",
        )
        add_proven_channel._remove_from_other_categories(
            self.data, "Indian", "Star Plus"
        )
        kept = json.loads(indian.read_text(encoding="utf-8"))["channels"]
        self.assertEqual(1, len(kept))

    def test_a_route_below_the_floor_with_no_exception_is_refused(self):
        with mock.patch.object(add_proven_channel, "measure_height", return_value=480):
            self.assertEqual(1, self._run())

    def test_a_proven_allowed_channel_is_accepted(self):
        self.assertEqual(0, self._run())


class TheFloorRuleTests(unittest.TestCase):
    def test_it_asks_the_verifier_rather_than_reimplementing_the_rule(self):
        """Two copies of the below-floor rule would eventually disagree, and the
        one that publishes is the one that matters."""
        source = (ROOT / "scripts" / "add-proven-channel.py").read_text(encoding="utf-8")
        self.assertIn("verifier._below_floor_exception(", source)

    def test_an_exception_is_written_onto_the_card_it_applies_to(self):
        source = (ROOT / "scripts" / "add-proven-channel.py").read_text(encoding="utf-8")
        self.assertIn('card["resolution_exception"] = True', source)
        self.assertIn('card["quality_policy_note"]', source)


if __name__ == "__main__":
    unittest.main()
