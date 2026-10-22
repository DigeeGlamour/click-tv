"""One link a feed hangs under two matches on air together belongs to neither.

2026-10-22: sm-sports-data put one rutube SKY CRICKET link under three live
cricket matches; the Australia Women card played England v Sri Lanka.
"""
import json
import unittest

from scanner.parsers import event_adapters as ea

SKY = "https://bl.rutube.ru/livestream/7c13a5/index.m3u8?s=abc&e=1"


def match(name, status, start, streams):
    team_a, team_b = name.split(" Vs ")
    return {"status": status, "Category": "Cricket", "event_name": name,
            "eventInfo": {"teamA": team_a, "teamB": team_b, "startTime": start},
            "streams": streams}


def stream(label, url):
    return {"channel_name": label, "stream_url": url}


def parse(rows, source_id="sm-test"):
    return ea.parse_event_source(json.dumps({"matches": rows}),
                                 {"id": source_id, "adapter": "named_streams"})


def channels(records, name):
    record = next(r for r in records if r["name"].casefold() == name.casefold())
    return [c["channel_name"] for c in record["channels"]], record["metadata_only"]


class SharedLinkTests(unittest.TestCase):
    def test_generic_channel_under_three_live_matches_is_dropped_from_all(self):
        records = parse([
            match("Australia Women Vs New Zealand Women", "LIVE", "22/10/2026 02:15:00 PM",
                  [stream("SKY CRICKET FHD", SKY), stream("WILLOW FHD", "https://w.example/a.mpd")]),
            match("England Vs Sri Lanka", "LIVE", "22/10/2026 02:00:00 PM",
                  [stream("WILLOW FHD", "https://w.example/b.mpd"), stream("SKY CRICKET FHD", SKY)]),
            match("South Africa Vs Australia", "LIVE", "22/10/2026 01:45:00 PM",
                  [stream("SKY CRICKET FHD", SKY)]),
        ])
        self.assertEqual(channels(records, "Australia Women Vs New Zealand Women"), (["WILLOW"], False))
        self.assertEqual(channels(records, "England Vs Sri Lanka"), (["WILLOW"], False))
        # Nothing else to play: kept as a fixture, never given the wrong match.
        self.assertEqual(channels(records, "South Africa Vs Australia"), ([], True))

    def test_one_channel_through_two_cdns_is_one_link(self):
        """WILLOW SPORTS, 2026-10-22: same channel id, bom vs lhr CDN."""
        records = parse([
            match("South Africa Vs Australia", "LIVE", "22/10/2026 01:45:00 PM",
                  [stream("WILLOW SPORTS HD", "https://otte.cache.aiv-cdn.net/bom-nitro/live/clients/dash/enc/tll6uwepxa/out/v1/a7f67cbb33df/cenc.mpd"),
                   stream("WILLOW FHD", "https://w.example/enc/2jx5hrl31t/out/v1/9b12/cenc.mpd")]),
            match("Australia Women Vs New Zealand Women", "LIVE", "22/10/2026 02:15:00 PM",
                  [stream("WILLOW SPORTS", "https://live-pv-ta.amazon.fastly-edge.com/lhr-nitro/live/clients/dash/enc/tll6uwepxa/out/v1/a7f67cbb33df/cenc.mpd"),
                   stream("WILLOW FHD", "https://w.example/enc/upctcqjn8t/out/v1/81ce/cenc.mpd")]),
        ])
        self.assertEqual(channels(records, "South Africa Vs Australia"), (["WILLOW"], False))
        self.assertEqual(channels(records, "Australia Women Vs New Zealand Women"), (["WILLOW"], False))

    def test_rotating_token_does_not_hide_a_shared_channel(self):
        records = parse([
            match("A Vs B", "LIVE", "22/10/2026 02:00:00 PM",
                  [stream("SKY", "https://bl.rutube.ru/livestream/7c13a51576b9ff26/index.m3u8?s=one")]),
            match("C Vs D", "LIVE", "22/10/2026 02:00:00 PM",
                  [stream("SKY", "https://bl.rutube.ru/livestream/7c13a51576b9ff26/index.m3u8?s=two")]),
        ])
        self.assertEqual(channels(records, "A Vs B"), ([], True))

    def test_per_match_links_are_kept(self):
        records = parse([
            match("A Vs B", "LIVE", "22/10/2026 02:00:00 PM", [stream("WILLOW", "https://w.example/1.mpd")]),
            match("C Vs D", "LIVE", "22/10/2026 02:00:00 PM", [stream("WILLOW", "https://w.example/2.mpd")]),
        ])
        self.assertEqual(channels(records, "A Vs B"), (["WILLOW"], False))
        self.assertEqual(channels(records, "C Vs D"), (["WILLOW"], False))

    def test_same_link_for_matches_days_apart_is_kept(self):
        url = "https://f.example/fancode.mpd"
        records = parse([
            match("A Vs B", "LIVE", "22/10/2026 02:00:00 PM", [stream("FANCODE", url)]),
            match("E Vs F", "UPCOMING", "25/10/2026 02:00:00 PM", [stream("FANCODE", url)]),
        ])
        self.assertEqual(channels(records, "A Vs B"), (["FANCODE"], False))

    def test_query_tells_portal_channels_apart(self):
        records = parse([
            match("A Vs B", "LIVE", "22/10/2026 02:00:00 PM",
                  [stream("WILLOW HD", "http://p.example/live.php?stream=1")]),
            match("C Vs D", "LIVE", "22/10/2026 02:00:00 PM",
                  [stream("WILLOW HD", "http://p.example/live.php?stream=2")]),
        ])
        self.assertEqual(channels(records, "A Vs B"), (["WILLOW"], False))

    def test_same_match_twice_in_one_feed_is_not_a_conflict(self):
        records = parse([
            match("England Vs Sri Lanka", "LIVE", "22/10/2026 02:00:00 PM", [stream("SKY", SKY)]),
            match("Sri Lanka Vs England", "LIVE", "22/10/2026 02:00:00 PM", [stream("SKY", SKY)]),
        ])
        self.assertEqual(channels(records, "England Vs Sri Lanka"), (["SKY"], False))

    def test_ended_match_does_not_count(self):
        records = parse([
            match("A Vs B", "LIVE", "22/10/2026 02:00:00 PM", [stream("SKY", SKY)]),
            match("C Vs D", "FINISHED", "22/10/2026 01:00:00 PM", [stream("SKY", SKY)]),
        ])
        self.assertEqual(channels(records, "A Vs B"), (["SKY"], False))


if __name__ == "__main__":
    unittest.main()
