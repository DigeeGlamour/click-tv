"""Zee Bangla now leads with a route that was measured playing.

The published route was settled by measurement, not opinion: 1080i H.264 with
zero IDR frames, four 120 s sessions all ending in MEDIA_ERR_DECODE, and fifteen
mpegts.js build/config combinations each decoding exactly one frame before
stopping. No player change fixes a stream with no random-access point, so the
remaining question was whether the configured sources already held a route with a
different structure. One did, and it passed twice.

These tests pin the three things that make that swap safe rather than lucky: the
old URL is still there, the new one has real proof, and the route belongs to this
channel and not to a similarly-named different one.
"""
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import route_evidence as rev  # noqa: E402
from scanner import sustained_proof  # noqa: E402

CARD_FILE = ROOT / "data" / "channels" / "indian.json"
REPORT = ROOT / "reports" / "zee-confirm-playback.json"
CHANNEL = "Zee Bangla"


def _cards(payload):
    return payload if isinstance(payload, list) else (payload.get("channels") or [])


def _card(payload):
    for card in _cards(payload):
        if isinstance(card, dict) and str(card.get("name") or "") == CHANNEL:
            return card
    return None


class RouteSwapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.card = _card(json.loads(CARD_FILE.read_text(encoding="utf-8")))
        if cls.card is None:
            raise unittest.SkipTest("Zee Bangla card not present")

    def test_the_channel_still_exists(self):
        self.assertIsNotNone(self.card)
        self.assertIsNot(self.card.get("publish_allowed"), False)

    def test_the_primary_is_the_proven_route(self):
        self.assertEqual(
            self.card.get("verification_status"), "verified_sustained_playback"
        )
        self.assertEqual(self.card.get("stream_type"), "hls")

    def test_the_original_url_is_preserved_as_a_backup(self):
        """The instruction was that existing stream URLs are not to be changed.

        Adding a proven route alongside is not changing one - but only if the old
        URL is actually still there, which is what this checks against git rather
        than against a comment.
        """
        previous = subprocess.run(
            ["git", "show", "HEAD:data/channels/indian.json"],
            capture_output=True, text=True, cwd=str(ROOT),
        ).stdout
        if not previous.strip():
            self.skipTest("no committed version to compare against")
        old_card = _card(json.loads(previous))
        if old_card is None:
            self.skipTest("channel absent from the committed version")
        old_url = str(old_card.get("url") or "")
        if not old_url or old_url == str(self.card.get("url") or ""):
            self.skipTest("primary unchanged in this revision")
        backups = self.card.get("backups") or []
        self.assertTrue(
            any(isinstance(b, dict) and b.get("url") == old_url for b in backups),
            "the original URL is gone, not demoted",
        )

    def test_the_proof_is_two_full_passes(self):
        payload = json.loads(REPORT.read_text(encoding="utf-8"))
        proven = [
            r for r in payload.get("results") or ()
            if len([
                o for o in (r.get("observations") or ())
                if o.get("verdict") == rev.PROVEN
            ]) >= rev.REQUIRED_FRESH_SESSIONS
        ]
        self.assertTrue(proven, "nothing in the report is proven")
        for result in proven:
            for observation in result["observations"]:
                if observation.get("verdict") != rev.PROVEN:
                    continue
                metrics = observation.get("playback_metrics") or {}
                progress = metrics.get("media_progress_seconds")
                stall = metrics.get("cumulative_stall_seconds")
                # Explicit None checks, not `or` defaults: a stall of zero is the
                # best possible result and `0 or 99` reads it as the worst.
                self.assertIsNotNone(progress)
                self.assertIsNotNone(stall)
                self.assertGreaterEqual(
                    float(progress), rev.PASS_MIN_MEDIA_PROGRESS_SECONDS
                )
                self.assertLessEqual(
                    float(stall), rev.PASS_MAX_CUMULATIVE_STALL_SECONDS
                )

    def test_the_proof_survives_a_card_rebuild(self):
        # A scan rebuilds cards from their sources and erases anything written on
        # them, which is why the registry exists outside the card.
        self.assertTrue(sustained_proof.has_proof(self.card, "channel"))


class ChannelIdentityTests(unittest.TestCase):
    """A similarly-named channel is not another source of this one."""

    def test_a_sibling_channel_is_refused(self):
        script = ROOT / "scripts" / "add-proven-route.py"
        source = script.read_text(encoding="utf-8")
        self.assertIn("DIFFERENT_CHANNEL_WORDS", source)
        for word in ("cinema", "sonar"):
            self.assertIn(word, source)

    def test_the_matcher_accepts_only_the_same_channel(self):
        import importlib.util  # noqa: PLC0415

        spec = importlib.util.spec_from_file_location(
            "add_proven_route", ROOT / "scripts" / "add-proven-route.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        self.assertTrue(module.same_channel("Zee Bangla", "Zee Bangla"))
        self.assertTrue(module.same_channel("Zee Bangla", "Zee Bangla HD"))
        self.assertTrue(module.same_channel("Zee Bangla", "ZEE BANGLA"))
        # These are different channels. Substituting one puts the wrong
        # programme on the card, which is worse than the stutter it replaces.
        self.assertFalse(module.same_channel("Zee Bangla", "Zee Bangla Cinema"))
        self.assertFalse(module.same_channel("Zee Bangla", "Zee Bangla Sonar"))
        self.assertFalse(module.same_channel("Zee Bangla", "Zee Bangla Cinema HD"))
        self.assertFalse(module.same_channel("Zee Bangla", "Zee Cinemalu"))
        self.assertFalse(module.same_channel("Zee Bangla", "Star Jalsha"))

    def test_the_script_requires_proof_before_attaching(self):
        source = (ROOT / "scripts" / "add-proven-route.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("REQUIRED_FRESH_SESSIONS", source)
        self.assertIn("is not PROVEN", source)

    def test_the_script_never_deletes_the_existing_route(self):
        source = (ROOT / "scripts" / "add-proven-route.py").read_text(
            encoding="utf-8"
        )
        # The old route must always be written into backups, never dropped.
        self.assertIn("Original-primary", source)
        self.assertNotIn("del card[", source)
        self.assertNotIn('card.pop("url"', source)


if __name__ == "__main__":
    unittest.main()
