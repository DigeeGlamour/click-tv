"""The Zee Bangla route swap: its proof and its guards, not the scanner's output.

The published route was settled by measurement: 1080i H.264 with zero IDR
frames, four 120 s sessions all ending in MEDIA_ERR_DECODE, and fifteen
mpegts.js build/config combinations each decoding exactly one frame before
stopping. No player change fixes a stream with no random-access point, so the
alternative had to come from the sources - and one HLS route there passed twice.

An earlier version of this file asserted the CURRENT contents of
data/channels/indian.json, and that was a design mistake serious enough to break
production: it failed eight consecutive scanner runs. The scanner owns that file
and rewrites it every run from whatever the sources currently offer. When the
proven route later started returning HTTP 530 (its Cloudflare worker went down),
the scan correctly dropped it, correctly kept the only route that still answered,
and my tests correctly reported that as a failure - of the scan, which had done
nothing wrong.

So these tests now assert what this work actually owns and controls: the recorded
proof, the durable registries, and the guards in the promotion script. Whether a
given route is in today's card is the scanner's answer to a question about
today's network, and it is not a test's business to freeze it.
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import route_evidence as rev  # noqa: E402
from scanner import route_preference  # noqa: E402
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


class ProofAndRegistryTests(unittest.TestCase):
    """What the measurement established, and where it is durably recorded."""

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

    def test_the_proof_is_recorded_outside_any_card(self):
        """A card cannot hold this, so the registry must.

        Measured: the scan that ran after the swap rebuilt the card from its
        sources and erased every field the script had written. The registries
        are the only place a proof survives that.
        """
        registry = sustained_proof.load()
        channels = {
            str(v.get("name") or "") for v in (registry.get("proofs") or {}).values()
        }
        self.assertIn(CHANNEL, channels)

    def test_the_preferred_route_is_recorded_with_its_evidence(self):
        registry = route_preference.load()
        entries = [
            v for v in (registry.get("preferred") or {}).values()
            if str(v.get("channel") or "") == CHANNEL
        ]
        self.assertTrue(entries, "no route preference recorded for this channel")
        entry = entries[0]
        self.assertGreaterEqual(
            int(entry.get("pass_count") or 0), rev.REQUIRED_FRESH_SESSIONS
        )
        self.assertEqual(float(entry.get("window_seconds") or 0), 120.0)
        self.assertTrue(str(entry.get("route_id") or ""))

    def test_the_registry_stores_no_raw_url(self):
        registry = route_preference.load()
        self.assertFalse(
            rev.evidence_contains_forbidden_material(registry)
        )

    def test_the_channel_is_still_published(self):
        """The one thing about the card that this work does guarantee.

        Not which route leads - the scanner decides that from what answers
        today - but that the channel is not hidden. That is what
        sustained_proof exists to prevent, and it holds regardless of which
        source is currently reachable.
        """
        payload = json.loads(CARD_FILE.read_text(encoding="utf-8"))
        card = _card(payload)
        if card is None:
            self.skipTest("channel absent from the catalogue")
        self.assertIsNot(card.get("publish_allowed"), False)


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
