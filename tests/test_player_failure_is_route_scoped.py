"""A browser failure belongs to the route that failed, not to the channel.

reports/confirmed-player-failures.json records the route: each entry keeps the
url, header profile, proxy mode and stream type, and playback_fingerprint
exists to key on exactly that. The filter that hid cards, though, matched on
the channel NAME alone - so one failure hid a channel permanently, however many
working routes appeared afterwards.

Measured on 2026-08-27: Ekattor TV and Bijoy TV had failed on their
stream.ottplus.live routes. Both were absent from the catalogue while that same
scan held a verified_global HD/720 route for each, and neither appeared in any
hidden-items report - so the loss was invisible as well as wrong.

The same argument is already written out in scanner/channels.py about the proof
ledger: "IPTV sources rotate their URLs constantly, so the fingerprint stops
matching within days". A failure keyed by name is that hazard with no way back.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import player_compatibility as pc  # noqa: E402

LEDGER = ROOT / "reports" / "confirmed-player-failures.json"

FAILED_ROUTE = {
    "name": "Ekattor TV",
    "url": "https://stream.ottplus.live/live/ekattor_tv_abr/index.m3u8",
    "header_profile": "android_tv",
    "proxy_mode": "direct_first",
    "stream_type": "hls",
    "requires_headers": False,
}


class RouteScopedTests(unittest.TestCase):
    def test_the_route_that_failed_stays_hidden(self):
        if not LEDGER.exists():
            self.skipTest("no failure ledger")
        self.assertTrue(
            pc.is_confirmed_player_failure(dict(FAILED_ROUTE), "channel"),
            "the exact route that failed in a browser must stay hidden",
        )

    def test_a_different_route_for_the_same_channel_is_eligible_again(self):
        if not LEDGER.exists():
            self.skipTest("no failure ledger")
        recovered = dict(
            FAILED_ROUTE,
            url="https://cache.example.net/hls/ekattor.m3u8",
        )
        self.assertFalse(
            pc.is_confirmed_player_failure(recovered, "channel"),
            "a channel must be able to recover on a route that never failed",
        )

    def test_an_unrelated_channel_is_untouched(self):
        if not LEDGER.exists():
            self.skipTest("no failure ledger")
        self.assertFalse(
            pc.is_confirmed_player_failure(
                {"name": "Zee Bangla", "url": "http://x.example.net/y.m3u8"},
                "channel",
            )
        )

    def test_the_ledger_records_a_fingerprint_for_every_entry(self):
        """Without fingerprints the filter has nothing route-shaped to match."""
        if not LEDGER.exists():
            self.skipTest("no failure ledger")
        names = pc.load_failure_keys()
        routes = pc.load_failed_routes()
        self.assertTrue(names)
        self.assertEqual(
            len(routes), len(names),
            "some ledger entries carry no usable route fingerprint",
        )

    def test_an_old_ledger_without_routes_keeps_hiding_by_name(self):
        """Degrade to the previous behaviour rather than stop hiding.

        A ledger written before fingerprints existed would otherwise let every
        recorded failure back in at once, which is the wrong direction to fail.
        """
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "old.json"
            path.write_text(
                json.dumps({
                    "records": [
                        {"kind": "channel", "record": {"name": "Ekattor TV"}}
                    ]
                }),
                encoding="utf-8",
            )
            keys = pc.load_failure_keys(path)
            routes = pc.load_failed_routes(path)
            self.assertEqual(routes, set())
            self.assertIn(("channel", "ekattor tv", ""), keys)

    def test_a_hidden_channel_is_recorded_rather_than_silently_dropped(self):
        """Hiding has to leave a trace, and it does.

        Ekattor TV and Bijoy TV are still absent from the catalogue after this
        change, and that is now the right answer rather than a lost channel:
        their only publishable route IS the one that failed twice in a real
        browser (stream.ottplus.live/.../ekattor_tv_abr). The ledger records
        each one with the policy that hid it, so the decision is auditable.

        What the change bought them is a way back - the moment a different
        route appears for either channel, the earlier test in this class shows
        it is eligible again.
        """
        if not LEDGER.exists():
            self.skipTest("no failure ledger")
        payload = json.loads(LEDGER.read_text(encoding="utf-8"))
        records = payload.get("records") or []
        self.assertTrue(records, "the ledger hides cards but records nothing")
        self.assertIn("browser", str(payload.get("policy") or "").lower())
        blob = json.dumps(records).lower()
        for name in ("ekattor", "bijoy"):
            self.assertIn(
                name, blob,
                f"{name} is hidden with no record of why",
            )

    def test_every_hidden_record_names_the_route_it_judged(self):
        if not LEDGER.exists():
            self.skipTest("no failure ledger")
        payload = json.loads(LEDGER.read_text(encoding="utf-8"))
        without_route = [
            (entry.get("record") or {}).get("name")
            for entry in payload.get("records") or []
            if isinstance(entry, dict)
            and not str((entry.get("record") or {}).get("url") or "").strip()
            and not str(
                (entry.get("record") or {}).get("playback_id") or ""
            ).strip()
        ]
        self.assertEqual(
            without_route[:5], [],
            f"{len(without_route)} hidden records name no route, so nothing "
            "can ever clear them",
        )


if __name__ == "__main__":
    unittest.main()
