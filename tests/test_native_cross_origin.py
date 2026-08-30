"""A proxied media response has to be fetched in CORS mode, or Chrome drops it.

Chrome's Opaque Response Blocking rejects a cross-origin no-cors media response
it cannot sniff. Matroska is not a container its sniffer knows, so a .mkv served
to a plain `<video src>` is refused with ERR_BLOCKED_BY_ORB and the element
reports MEDIA_ELEMENT_ERROR: Format error - which reads like a broken file and
is not one.

Measured on 2026-08-30 against the deployed site with the owner's own full UI
audit: 1,029 of 1,248 published movies failed, every one of them with "Browser
blocked the media response (ORB; invalid or cross-origin media response)". A
70-item re-run reproduced it exactly along one line: 42 of 42 direct routes
passed, 28 of 28 proxied routes failed.

Isolated in real Chrome on a bare page, three URLs, twelve seconds each:

    plain <video src>, r2 direct            ERR_BLOCKED_BY_ORB
    plain <video src>, r2 via our proxy     ERR_BLOCKED_BY_ORB
    plain <video src>, bunny via our proxy  ERR_BLOCKED_BY_ORB
    crossOrigin='anonymous', r2 via proxy   plays, 1920x1080, 10.40 s
    crossOrigin='anonymous', bunny proxy    plays, 1280x720, 10.81 s
    crossOrigin='anonymous', r2 direct      ERR_FAILED

The last line is why this cannot simply be switched on everywhere: our proxy
sends `Access-Control-Allow-Origin`, and the storage origins do not. Setting the
attribute on a direct route turns a working stream into a CORS failure.
"""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

APP = ROOT / "site" / "assets" / "js" / "app.js"


class NativeCrossOriginTests(unittest.TestCase):
    def setUp(self):
        self.source = APP.read_text(encoding="utf-8")

    def test_the_rule_exists(self):
        self.assertIn("function applyNativeCrossOrigin(", self.source)
        self.assertIn("function isOwnPlaybackProxyUrl(", self.source)

    def test_the_native_player_applies_it(self):
        _, _, tail = self.source.partition("async function initNative(")
        head = tail[: tail.index("state.playerType")]
        self.assertIn("applyNativeCrossOrigin(url)", head)

    def test_it_is_only_set_for_our_own_proxy(self):
        _, _, tail = self.source.partition("function applyNativeCrossOrigin(")
        body = tail[: tail.index("\n}")]
        self.assertIn("isOwnPlaybackProxyUrl(url)", body)
        self.assertIn("setAttribute('crossorigin', 'anonymous')", body)
        self.assertIn("removeAttribute('crossorigin')", body)

    def test_a_direct_route_still_has_the_attribute_removed(self):
        """Setting it on a storage origin that sends no ACAO breaks a working
        stream - measured, ERR_FAILED on r2 direct."""
        _, _, tail = self.source.partition("function applyNativeCrossOrigin(")
        body = tail[: tail.index("\n}")]
        self.assertLess(
            body.index("setAttribute('crossorigin'"),
            body.index("removeAttribute('crossorigin')"),
            "the else branch must be the removal",
        )
        self.assertIn("else", body)

    def test_the_proxy_check_compares_origins_not_substrings(self):
        """A substring test would match any URL that merely contained a proxy
        host, including one supplied inside a query parameter."""
        _, _, tail = self.source.partition("function isOwnPlaybackProxyUrl(")
        body = tail[: tail.index("\n}")]
        self.assertIn("new URL(", body)
        self.assertIn(".origin ===", body)
        self.assertIn("playbackProxyList()", body)

    def test_it_never_guesses_when_the_url_will_not_parse(self):
        _, _, tail = self.source.partition("function isOwnPlaybackProxyUrl(")
        body = tail[: tail.index("\n}")]
        self.assertIn("catch", body)
        self.assertIn("return false", body)

    def test_the_movie_audio_companion_follows_the_same_rule(self):
        """It carries the audio track for a movie; without this the picture
        plays and the sound does not."""
        _, _, tail = self.source.partition("movieAudioCompanion.src = attempt.url")
        head = self.source[: self.source.index("movieAudioCompanion.src = attempt.url")]
        window = head[-700:]
        self.assertIn("isOwnPlaybackProxyUrl(attempt.url)", window)
        self.assertIn("crossorigin", window)

    def test_no_other_media_src_assignment_was_left_behind(self):
        """Any future `<video>`/`<audio>` src set on a proxied URL needs the
        same treatment; this catches a new one being added without it."""
        assignments = re.findall(r"^\s*(?:video|movieAudioCompanion)\.src = .+$",
                                 self.source, flags=re.MULTILINE)
        self.assertEqual(
            2, len(assignments),
            "a new media src assignment appeared: %s" % assignments,
        )


if __name__ == "__main__":
    unittest.main()
