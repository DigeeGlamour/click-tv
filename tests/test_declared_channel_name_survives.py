"""A name the feed declared per stream must not be thrown away.

Noise stripping exists for stream titles, where quality and region markers have
to come off. On a name the feed states outright it can take the whole name off:
`sm-sports-data` offered "FOOTBALL HD" for Falkirk FC Vs Rangers, "HD" came
off, "FOOTBALL" was correctly refused as a bare category, and the declaration
went into the bin with it - so the card published "Server-1" next to a real
"BeIN2 SPORTS", and the name the source gave was simply lost.

The same shape had already been fixed twice, for "T Sports" and for
"AX Sports". Step 1 of the priority order was the one place still not using the
fallback those fixes introduced.
"""
import unittest

from scanner import channel_resolver as cr
from scanner.channel_groups import build_event_channels


def resolved(name):
    return cr.resolve_channel_name(
        {"channel_name": name, "url": "https://example.test/a.m3u8"},
        aliases={},
    ).name


class ADeclaredNameSurvivesNoiseStripping(unittest.TestCase):
    #: Every one of these was refused before the fix, and every one is a real
    #: broadcaster label taken from the live sm-sports-data feed.
    RESCUED = [
        ("FOOTBALL HD", "FOOTBALL"),
        ("RM TV", "RM TV"),
        ("WWE HD", "WWE"),
        ("WWE SD", "WWE"),
        ("WWE LIVE FHD", "WWE LIVE"),
        ("D SPORTS + FHD", "D SPORTS +"),
    ]

    def test_each_declared_name_is_kept(self):
        for given, expected in self.RESCUED:
            with self.subTest(given=given):
                self.assertEqual(resolved(given), expected)

    def test_the_names_that_always_worked_still_work(self):
        for given, expected in (("T Sports", "T Sports"),
                                ("BeIN2 SPORTS FHD", "BeIN2 SPORTS"),
                                ("Sony Sports Ten 5", "Sony Sports Ten 5"),
                                ("FanCode", "FanCode")):
            with self.subTest(given=given):
                self.assertEqual(resolved(given), expected)

    def test_the_quality_marker_still_comes_off(self):
        # The band is shown separately by the card, so the name carries the
        # broadcaster and not the resolution.
        self.assertEqual(resolved("BeIN2 SPORTS FHD"), "BeIN2 SPORTS")
        self.assertEqual(resolved("FOOTBALL HD"), "FOOTBALL")


class ABareLabelIsStillRefused(unittest.TestCase):
    """The guard this fix reaches past must keep doing its job.

    A playlist group-title is very often "Sports" or "Cricket", and publishing
    that as a channel would invent a feed name the source never gave.
    """

    LABELS = ("Sports", "Cricket", "Football", "Live", "Live TV", "TV",
              "Channel", "Today Match", "Upcoming", "Highlights", "News",
              "Movies", "Other", "VIP", "Unknown", "N/A", "HD", "Live Sports")

    def test_none_of_them_becomes_a_channel(self):
        for label in self.LABELS:
            with self.subTest(label=label):
                self.assertEqual(resolved(label), "")


class TheCardStopsInventingServerNumbers(unittest.TestCase):
    def test_the_falkirk_card_names_both_of_its_routes(self):
        # The exact two streams the feed carried, and the card that published
        # "BeIN2 SPORTS" beside "Server-1".
        channels, stats = build_event_channels(
            "falkirk-fc-vs-rangers",
            "Falkirk FC Vs Rangers",
            [
                {"channel_name": "BeIN2 SPORTS FHD",
                 "url": "https://otte.cache.aiv-cdn.net/x/cenc.mpd",
                 "source_id": "sm-sports-data"},
                {"channel_name": "FOOTBALL HD",
                 "url": "https://tmaxapp.site/x/skysportsfootball.m3u8",
                 "source_id": "sm-sports-data"},
            ],
            aliases={},
        )
        self.assertEqual([c["name"] for c in channels],
                         ["BeIN2 SPORTS", "FOOTBALL"])
        self.assertEqual(stats["generic_server_channels"], 0)
        for channel in channels:
            self.assertEqual(channel["name_confidence"], "explicit")
            self.assertEqual(channel["name_source"], "channel_name")

    def test_a_stream_with_no_name_still_gets_a_server_number(self):
        # Server-N is the honest answer when the feed says nothing; it is only
        # wrong when a name was there to use.
        channels, stats = build_event_channels(
            "some-fixture", "A Team Vs B Team",
            [{"url": "https://example.test/one.m3u8", "source_id": "feed"},
             {"url": "https://example.test/two.m3u8", "source_id": "feed"}],
            aliases={},
        )
        self.assertTrue(stats["generic_server_channels"] >= 1)


if __name__ == "__main__":
    unittest.main()
