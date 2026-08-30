"""A card whose route stops working repairs itself on the next scan.

The loop, with no service, no KV and no new worker in it:

  1. scripts/verify-delivery-path.py asks the LIVE playback proxies for every
     published route, with the site origin, exactly as the player does. A route
     the proxy will never serve is written to
     state/measured-playback-failures.json.
  2. scanner/merger.py ranks that ledger above every other signal, source
     priority included.
  3. The next scan therefore prefers an alternate the sources already carry.

Step 2 is the part that was missing, and it is why a broken card stayed broken.
Verification is an HTTP request from a GitHub runner, so a route that answers
200 scores the top tier whether or not a viewer can watch it. Measured on
2026-08-30: Disney Channel published rgkkw.live, which answers 200 and produced
0.12 seconds of video across two 120-second Chrome sessions, while
sm-aynaott-auto-update offered tvsen7.aynaott.com/disney in the same scan - HTTP
200, a real master playlist, never disproved. The two tied on tier, source
priority broke the tie, and the dead one won. Every scan. Forever.
"""
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import merger  # noqa: E402
from scanner import playback_evidence  # noqa: E402

DEAD = "http://rgkkw.live:80/live/1Aoen7elp5/IgMJ60tmAa/19741.ts"
ALIVE = "https://tvsen7.aynaott.com/disney/index.m3u8"


def stream(url, **fields):
    base = {
        "url": url,
        "verification_status": "verified_global",
        "verified": True,
        "source_priority": 0,
        "resolution_height": 720,
    }
    base.update(fields)
    return base


class TheLedgerOutranksEverythingTests(unittest.TestCase):
    def test_a_measured_dead_route_loses_to_an_untested_one(self):
        """Even when the dead one is on a far higher-priority playlist, which is
        exactly the shape of the real case."""
        dead = stream(DEAD, source_priority=9000)
        alive = stream(ALIVE, source_priority=10)
        with TemporaryDirectory() as folder:
            path = Path(folder) / "ledger.json"
            playback_evidence.record(
                DEAD, "produced no video in two 120s sessions", sessions=2,
                media_progress_seconds=[0.12, 0], window_seconds=120.0,
                evidence_report="test", vantage="bd_residential", path=str(path),
            )
            original = playback_evidence.load
            playback_evidence.load = lambda p=None: original(str(path))
            try:
                self.assertEqual(0, merger._measured_unplayable(dead))
                self.assertEqual(1, merger._measured_unplayable(alive))
                self.assertGreater(
                    merger._stream_quality_score(alive),
                    merger._stream_quality_score(dead),
                )
            finally:
                playback_evidence.load = original

    def test_the_ledger_key_is_the_first_element_of_the_score(self):
        """Ahead of the verification tier, because a route a browser could not
        play is not a lower-confidence route - it is not a route."""
        self.assertEqual(
            merger._measured_unplayable(stream(ALIVE)),
            merger._stream_quality_score(stream(ALIVE))[0],
        )

    def test_an_untested_route_is_never_demoted(self):
        """The ledger only ever holds URLs something was actually pointed at."""
        self.assertEqual(
            1, merger._measured_unplayable(stream("https://never-tested.example/a.m3u8"))
        )

    def test_the_other_url_spellings_are_read(self):
        for key in ("url", "stream_url", "link"):
            self.assertEqual(
                "https://x.example/a", merger._stream_url_for_evidence({key: "https://x.example/a"})
            )

    def test_ranking_never_raises_on_a_broken_ledger(self):
        original = playback_evidence.unproven_reason

        def explode(_url, path=None):
            raise ValueError("corrupt ledger")

        playback_evidence.unproven_reason = explode
        try:
            self.assertEqual(1, merger._measured_unplayable(stream(ALIVE)))
        finally:
            playback_evidence.unproven_reason = original


class TheDeliveryCheckIsHonestAboutEvidenceTests(unittest.TestCase):
    """It writes what it measured and nothing stronger."""

    def _source(self):
        return (ROOT / "scripts" / "verify-delivery-path.py").read_text(encoding="utf-8")

    def test_only_a_permanent_refusal_is_recorded(self):
        """A timeout, a 429 or a 5xx is a bad minute, not a dead route. This
        project has already deleted working channels by treating one as the
        other."""
        source = self._source()
        self.assertIn("AMBIGUOUS_STATUSES", source)
        for code in ("429", "503", "567"):
            self.assertIn(code, source.split("AMBIGUOUS_STATUSES")[1][:300])

    def test_it_never_clears_a_browser_measurement(self):
        """The proxy returning a manifest means the bytes arrive, not that a
        viewer can watch. On this script's first run it cleared eighteen rows,
        every one of them a real Chrome session."""
        source = self._source()
        clear = source[source.index("for row in ok:"):]
        self.assertIn("vantage_of", clear)
        self.assertIn("!= VANTAGE", clear)

    def test_one_unhealthy_proxy_cannot_demote_a_working_route(self):
        source = self._source()
        self.assertIn("refused by one proxy", source)

    def test_it_sends_the_headers_a_route_needs(self):
        """The catalogue stores them and several hosts refuse without them.
        toffeelive answers 403 to a bare request and 200 to the same request
        carrying its profile. Fetching without them and calling that evidence
        demoted 100 routes in one run - 97 of them working backups of Bangla
        channels. With the headers it is 3."""
        source = self._source()
        self.assertIn('headers.update(', source)
        self.assertIn('row.get("headers")', source)

    def test_an_https_route_gets_its_direct_path_checked(self):
        """The proxy is not the only way in. app.js resolves an https route to
        `direct_first` and only forces http:// through the proxy, because an
        HTTPS page cannot load mixed content. So a proxy refusal alone does not
        prove an https route is unreachable."""
        source = self._source()
        self.assertIn('startswith("https://")', source)
        self.assertIn("ask_directly", source)

    def test_a_refusal_aimed_at_this_script_is_not_a_verdict(self):
        """If the origin header ever stops arriving, every answer becomes a 403
        about the origin. Reading those as dead routes would demote the whole
        catalogue in one run."""
        self.assertIn("PROXY_OWN_REFUSALS", self._source())

    def test_it_asks_the_proxy_the_way_the_player_does(self):
        """buildProxyUrl in app.js uses /hls?id= whenever the source has a
        playback_id, and only falls back to ?url= without one. The id form makes
        the proxy load the route's stored headers server-side; `&profile=` alone
        does not carry what some of them need - Ananda TV's toffeelive backup
        answers 200 by id and 403 by url+profile. Asking the wrong way was about
        to record 102 working routes as dead."""
        source = self._source()
        self.assertIn('/hls?id=', source)
        self.assertIn('row.get("playback_id")', source)

    def test_a_single_use_url_is_reported_but_not_recorded(self):
        """The ledger is keyed on the exact URL and these hosts re-sign on every
        scan, so the row could never match again - it would be pure growth. It
        also invites a wrong answer: Movie Bangla and Asian TV were both about
        to be recorded on a 403 to their signed primary, and both played the
        full sixty seconds in real Chrome."""
        source = self._source()
        self.assertIn("carries_a_token", source)
        self.assertIn("SIGNED_PARAMETERS", source)

    def test_a_proxy_pinned_route_is_not_rescued_by_a_direct_fetch(self):
        """A signed URL is pinned to the proxy on purpose - handing it to the
        page would publish the credential - so the player will never take the
        direct path for it, and a direct success says nothing about what the
        viewer gets."""
        self.assertIn('row.get("proxy_only")', self._source())

    def test_the_ledger_cannot_grow_without_limit(self):
        from scanner import playback_evidence as evidence
        store = {"routes": {f"r{i}": {"reason": "x"} for i in range(evidence.MAX_ROUTES + 40)}}
        for i in range(10):
            store["routes"][f"r{i}"]["superseded_by"] = {"verdict": "proven"}
        evidence._trim(store)
        self.assertEqual(evidence.MAX_ROUTES, len(store["routes"]))
        self.assertEqual(
            0, sum(1 for row in store["routes"].values() if row.get("superseded_by")),
            "a superseded row is spent before a standing verdict",
        )

    def test_the_site_origin_is_sent(self):
        """Without it every answer is a 403 about the origin rather than an
        answer about the route, and the check would call the whole catalogue
        dead."""
        self.assertIn('"Origin": SITE_ORIGIN', self._source())


class TheCommittedLedgerIsIntactTests(unittest.TestCase):
    def test_browser_rows_were_not_superseded_by_the_http_check(self):
        path = ROOT / "state" / "measured-playback-failures.json"
        if not path.is_file():
            self.skipTest("no ledger committed")
        routes = json.loads(path.read_text(encoding="utf-8")).get("routes") or {}
        wrongly_cleared = [
            key for key, row in routes.items()
            if isinstance(row, dict)
            and row.get("vantage", "").startswith(("bd_", "bangladesh"))
            and isinstance(row.get("superseded_by"), dict)
            and row["superseded_by"].get("vantage") == "delivery_path_proxy"
        ]
        self.assertEqual([], wrongly_cleared)


if __name__ == "__main__":
    unittest.main()
