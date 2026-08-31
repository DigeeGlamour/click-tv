"""When a proven route becomes the primary, the card's quality must follow it.

Zee Bangla, 2026-08-29. Its published primary answered HTTP 500 from Bangladesh
in two consecutive probes and never started in two 120 s browser sessions. A
route from the same configured source - stream.ottplus.bd, a 1280x720 HLS master
- passed both sessions at 119.99 s and 119.87 s with zero stall, so it was made
the primary.

The card then read:

    url                 stream.ottplus.bd/live/zee_bangla_abr/index.m3u8   (720p)
    resolution          "SD"
    resolution_height   576
    resolution_exception true
    quality_policy_note "Below the 720p floor at 576p by a named per-channel
                         exception..."

Every one of those four lines described the route that had just been demoted.
The card advertised the wrong resolution and claimed a below-floor exemption it
no longer needed - and the exemption is the one thing in this project allowed to
publish under the 720p floor, so leaving it attached to the wrong route is worse
than cosmetic.

The floor itself is untouched. What changed is that the card reports the
resolution of the route it is actually serving, measured from the media.
"""
import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "add_proven_route", ROOT / "scripts" / "add-proven-route.py"
)
add_proven_route = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(add_proven_route)

MASTER_720 = (
    b"#EXTM3U\n#EXT-X-VERSION:3\n"
    b'#EXT-X-STREAM-INF:BANDWIDTH=1767020,RESOLUTION=1280x720,CODECS="avc1.4d401f"\n'
    b"live/zee_bangla_720/chunks.m3u8\n"
    b'#EXT-X-STREAM-INF:BANDWIDTH=1052475,RESOLUTION=854x480,CODECS="avc1.64001e"\n'
    b"live/zee_bangla_480/chunks.m3u8\n"
)
MASTER_480 = (
    b"#EXTM3U\n"
    b'#EXT-X-STREAM-INF:BANDWIDTH=900000,RESOLUTION=854x480,CODECS="avc1.64001e"\n'
    b"low/chunks.m3u8\n"
)


def _card():
    return {
        "name": "Zee Bangla",
        "url": "https://stream.ottplus.bd/live/zee_bangla_abr/index.m3u8",
        "resolution": "SD",
        "resolution_height": 576,
        "resolution_exception": True,
        "quality_below_preferred": True,
        "quality_policy_note": "Below the 720p floor at 576p by a named ...",
    }


class _Response:
    def __init__(self, body):
        self._body = body

    def read(self, limit=None):
        return self._body[:limit] if limit else self._body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class RetakeResolutionTests(unittest.TestCase):
    def retake(self, body, card=None):
        card = card if card is not None else _card()
        with mock.patch("urllib.request.urlopen", return_value=_Response(body)):
            add_proven_route._retake_resolution(card, "https://x.test/index.m3u8")
        return card

    def test_the_measured_height_replaces_the_old_one(self):
        card = self.retake(MASTER_720)
        self.assertEqual(720, card["resolution_height"])
        self.assertEqual("HD", card["resolution"])

    def test_a_route_at_the_floor_drops_the_exception(self):
        card = self.retake(MASTER_720)
        for field in add_proven_route.BELOW_FLOOR_FIELDS:
            self.assertNotIn(field, card)

    def test_a_route_below_the_floor_keeps_it(self):
        """The exception is what lets a proven SD route publish at all. It is
        removed because it became unnecessary, never because it is inconvenient."""
        card = self.retake(MASTER_480)
        self.assertEqual(480, card["resolution_height"])
        self.assertTrue(card["resolution_exception"])
        self.assertIn("quality_policy_note", card)

    def test_the_floor_itself_is_not_a_parameter_of_the_card(self):
        card = _card()
        with mock.patch("urllib.request.urlopen", return_value=_Response(MASTER_480)):
            add_proven_route._retake_resolution(card, "https://x.test/i.m3u8", floor=480)
        self.assertNotIn("resolution_exception", card)

    def test_an_unreadable_stream_changes_nothing(self):
        """Silence is not evidence. Overwriting a known value with a guess is
        exactly what the resolution work of 2026-08-28 set out to stop."""
        card = self.retake(b"not a playlist at all")
        self.assertEqual(576, card["resolution_height"])
        self.assertEqual("SD", card["resolution"])
        self.assertTrue(card["resolution_exception"])

    def test_a_network_failure_changes_nothing(self):
        card = _card()
        with mock.patch("urllib.request.urlopen", side_effect=OSError("no route")):
            add_proven_route._retake_resolution(card, "https://x.test/i.m3u8")
        self.assertEqual(576, card["resolution_height"])
        self.assertTrue(card["resolution_exception"])

    def test_the_swap_calls_it(self):
        source = (ROOT / "scripts" / "add-proven-route.py").read_text(encoding="utf-8")
        _, _, tail = source.partition("if args.make_primary:")
        self.assertIn("_retake_resolution(card, args.url)", tail[:2000])


class DemotedRouteTests(unittest.TestCase):
    """A demoted route is kept, unless a browser measured it unplayable.

    Dropping a route on a single bad answer is a mistake this project has made
    and fixed before, so the default is to keep it. Zee Bangla's old primary is
    the other case: HTTP 500 to two consecutive probes, zero seconds of media in
    two 120 s sessions, and a row in state/measured-playback-failures.json
    saying exactly that. Publishing it as a backup would give a viewer a second
    button that does nothing, and it would carry a 576p resolution whose
    below-floor exception now belongs to a different route - which is what made
    the Pages validator refuse the whole build.

    The URL is not lost either way: it stays in the measured-playback ledger and
    in the route-preference registry's superseded chain.
    """

    def _block(self):
        source = (ROOT / "scripts" / "add-proven-route.py").read_text(encoding="utf-8")
        _, _, tail = source.partition("if args.make_primary:")
        return tail[: tail.index('card["backups"] = [proven_entry] + backups')]

    def test_a_measured_dead_route_is_not_published_as_a_backup(self):
        block = self._block()
        self.assertIn("if demoted_reason:", block)
        self.assertIn("demoted_id = rev.normalize_source_identity(existing_url)", block)

    def test_a_duplicate_copy_of_it_goes_too(self):
        """The scanner records a route two sources both carry as the primary
        AND a backup. Dropping only the primary copy left the viewer the
        identical dead route, still badged Verified."""
        block = self._block()
        self.assertIn("== demoted_id", block)

    def test_its_url_and_reason_stay_on_the_card_for_an_audit(self):
        block = self._block()
        self.assertIn("demoted_route_public_template", block)
        self.assertIn("demoted_route_reason", block)

    def test_an_ordinary_route_is_still_kept(self):
        block = self._block()
        self.assertIn('card["backups"] = [existing_entry] + backups', block)

    def test_and_keeps_the_resolution_that_belonged_to_it(self):
        """It was the primary; the card's declared height described it. Without
        this the demoted entry reached the validator with no resolution at all."""
        block = self._block()
        self.assertIn('existing_entry["resolution_height"] = card.get("resolution_height")', block)

    def test_the_added_route_carries_a_badge(self):
        """Left unset it reached the card as `verification_badge: null` and the
        site drew a blank chip beside a route that had just passed two full
        120 s sessions."""
        source = (ROOT / "scripts" / "add-proven-route.py").read_text(encoding="utf-8")
        _, _, tail = source.partition("proven_entry = {")
        block = tail[: tail.index("}")]
        self.assertIn('"verification_badge": "Verified"', block)

    def test_and_a_measured_resolution(self):
        """A backup answers to the same 720p rule as a primary, and the Pages
        validator refused the whole build over one that reached it as unknown."""
        source = (ROOT / "scripts" / "add-proven-route.py").read_text(encoding="utf-8")
        self.assertIn('proven_entry["resolution_height"] = _proven_height', source)

    def test_the_reason_comes_from_the_ledger_not_from_this_script(self):
        source = (ROOT / "scripts" / "add-proven-route.py").read_text(encoding="utf-8")
        self.assertIn("playback_evidence.unproven_reason(existing_url)", source)


if __name__ == "__main__":
    unittest.main()
