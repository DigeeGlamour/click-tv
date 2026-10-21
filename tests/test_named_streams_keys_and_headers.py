"""sm-sportsdata's ClearKey and per-stream headers reach the published stream.

Played on the live site on 2026-10-21: Canada U17 v Norway U17, Japan U17 v
France U17, Como v Manchester United and Lille v Galatasaray were Live with
one encrypted Prime Video link each, and none played - the feed wrote the key
as `kid` + `key`, the adapter read only `drm_key`, and the player had no key.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner.parsers import event_adapters as adapters  # noqa: E402

KID = "385ceb9714b75e0cef61254f80b31002"
KEY = "18dce92a2891fee68d21ede5173230f8"


def _servers(stream):
    payload = {"matches": [{
        "status": "LIVE", "Category": "Football", "event_name": "Canada U17 Vs Norway U17",
        "eventInfo": {"teamA": "Canada U17", "teamB": "Norway U17",
                      "eventName": "U17 Women's World Championship",
                      "startTime": "21/10/2026 09:00:00 PM"},
        "streams": [dict(stream, channel_name="TSN5 SPORTS FHD")],
    }]}
    record = adapters.adapt_named_streams(payload, "sm-sports-data")[0]
    return [server for channel in record["channels"] for server in channel["servers"]]


class TheKeyIsRead(unittest.TestCase):
    def test_separate_kid_and_key_fields(self):
        server = _servers({"stream_url": "https://cdn.example/enc/cenc.mpd",
                           "kid": KID, "key": KEY})[0]
        self.assertEqual(server["drm"]["type"], "clearkey")
        self.assertEqual(server["drm"]["keys"], [{"kid": KID, "k": KEY}])

    def test_the_old_drm_key_form_still_works(self):
        server = _servers({"stream_url": "https://cdn.example/enc/cenc.mpd",
                           "drm_key": f"{KID}:{KEY}"})[0]
        self.assertEqual(server["drm"]["keys"], [{"kid": KID, "k": KEY}])

    def test_no_key_is_no_drm(self):
        server = _servers({"stream_url": "https://cdn.example/live.m3u8"})[0]
        self.assertFalse(server["drm"])


class TheStreamsOwnHeadersAreKept(unittest.TestCase):
    def test_referer_and_user_agent(self):
        server = _servers({"stream_url": "https://edge.example/hls/a.m3u8",
                           "referer": "https://streame.center",
                           "user_agent": "Mozilla/5.0 Test"})[0]
        self.assertEqual(server["headers"].get("Referer"), "https://streame.center")
        self.assertEqual(server["headers"].get("User-Agent"), "Mozilla/5.0 Test")


if __name__ == "__main__":
    unittest.main()
