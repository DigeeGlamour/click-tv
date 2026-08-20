"""One channel is one button, and the button says which channel it is.

The four rules these tests hold to, as stated by the product owner:

  1. A source that names a match may carry several different channels for it -
     those become several small buttons on the card.
  2. The same match and the same channel name arriving from different sources is
     one button: one primary link and the rest as backups.
  3. A Today Match source that names only a channel and no match is added as a
     channel. Same channel with different links -> one primary plus backups.
     Different channels -> different cards, with their logos.
  4. Cricket is always ordered first.

Two defects were found against rules 1-3 on 2026-08-20.

`_keeps_a_word` drops any alphabetic fragment of two letters or less, because
"Al Adalah vs Al Fayha" used to leave "Al Al" behind. That also removed the
"T" from "T Sports": strip_stream_noise returned the bare category word
"Sports", looks_like_channel rejected it, and the channel never resolved. Three
sources carrying one T Sports feed for one match therefore published as three
buttons reading "Server-1", "Server-2", "Server-3" - rule 2 inverted into
exactly the duplicate-button complaint that started this. T Sports is pinned in
config/settings.json and aliased in config/channel-aliases.json, so the
evidence to name it correctly was already in the repository.

Separately, the group label kept the quality tag of whichever variant sorted
first. normalize_channel_name drops quality from the comparison key, which is
what correctly folds "Willow (HD)" and "Willow (SD)" into one channel with a
primary and a backup - but the button then read "Willow (HD)" while failing
over to SD, and the real trysports cricket feed published as "Willow (HD)",
"Willow 2 (HD)", "Willow Sports (HD)", where "(HD)" is the only thing three
different channels appeared to have in common.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scanner.channel_groups import build_event_channels
from scanner.channel_resolver import (
    display_channel_label,
    load_alias_map,
    normalize_channel_name,
    resolve_channel_name,
)
from scanner.merger import sport_sort_index

MATCH = "Sri Lanka vs India 1st Test"


def stream(channel_name, url, *, resolution="HD", height=720, source_id="s1"):
    return {
        "name": MATCH,
        "channel_name": channel_name,
        "url": url,
        "resolution": resolution,
        "resolution_height": height,
        "verification_status": "verified_global",
        "verified": True,
        "publish_allowed": True,
        "stream_type": "hls",
        "source_id": source_id,
        "headers": {},
    }


class QualityIsNotPartOfAChannelName(unittest.TestCase):
    def test_a_bracketed_quality_tag_is_dropped(self):
        cases = {
            "Willow (HD)": "Willow",
            "Willow 2 (SD)": "Willow 2",
            "Willow Sports (HD)": "Willow Sports",
            "Apple TV (HD)": "Apple TV",
            "beIN Sports 5 [4K]": "beIN Sports 5",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(display_channel_label(raw), expected)

    def test_a_trailing_quality_word_is_dropped(self):
        cases = {
            "Star Sports 1 HD": "Star Sports 1",
            "Sony Ten 3 FHD": "Sony Ten 3",
            "T Sports 720p": "T Sports",
            "HD Star Jalsha": "Star Jalsha",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(display_channel_label(raw), expected)

    def test_a_feed_number_is_never_mistaken_for_a_quality_tag(self):
        for raw in ("Sony Ten 1", "Sony Ten 3", "T Sports 2", "Star Sports 1"):
            with self.subTest(raw=raw):
                self.assertEqual(display_channel_label(raw), raw)

    def test_a_name_that_is_only_a_quality_tag_is_left_alone(self):
        for raw in ("HD", "SD", "FHD", "4K"):
            with self.subTest(raw=raw):
                self.assertEqual(display_channel_label(raw), raw)

    def test_an_ordinary_name_is_untouched(self):
        for raw in ("Zee Bangla", "Willow", "T Sports", "Somoy TV"):
            with self.subTest(raw=raw):
                self.assertEqual(display_channel_label(raw), raw)


class CuratedAliasBeatsTheHeuristicCleaner(unittest.TestCase):
    def setUp(self):
        self.aliases = load_alias_map()

    def test_the_cleaner_alone_still_destroys_t_sports(self):
        """The defect, stated so the fix cannot be mistaken for the cause."""
        from scanner.channel_resolver import _keeps_a_word, strip_stream_noise

        self.assertFalse(_keeps_a_word("T"))
        self.assertEqual(strip_stream_noise("T Sports", MATCH), "Sports")

    def test_t_sports_resolves_anyway(self):
        resolved = resolve_channel_name(
            {"channel_name": "T Sports", "name": MATCH}, MATCH, self.aliases
        )
        self.assertTrue(resolved.resolved)
        self.assertEqual(resolved.name, "T Sports")
        self.assertEqual(resolved.confidence, "explicit")

    def test_a_quality_tagged_alias_still_resolves_to_the_base_name(self):
        for raw in ("T Sports HD", "T Sports 720p", "tsports"):
            with self.subTest(raw=raw):
                resolved = resolve_channel_name(
                    {"channel_name": raw, "name": MATCH}, MATCH, self.aliases
                )
                self.assertEqual(resolved.name, "T Sports")

    def test_a_numbered_feed_of_a_curated_brand_keeps_its_number(self):
        resolved = resolve_channel_name(
            {"channel_name": "T Sports 2", "name": MATCH}, MATCH, self.aliases
        )
        self.assertTrue(resolved.resolved)
        self.assertEqual(resolved.name, "T Sports 2")
        self.assertNotEqual(
            normalize_channel_name("T Sports 2"), normalize_channel_name("T Sports")
        )

    def test_a_bare_category_is_still_refused(self):
        for raw in ("Sports", "Server 1", "Backup", "Live"):
            with self.subTest(raw=raw):
                resolved = resolve_channel_name(
                    {"channel_name": raw, "name": MATCH}, MATCH, self.aliases
                )
                self.assertFalse(resolved.resolved, raw)

    def test_the_alias_file_really_carries_t_sports(self):
        self.assertEqual(self.aliases.get("t-sports"), "T Sports")
        self.assertEqual(self.aliases.get("tsports"), "T Sports")


class RuleTwoOneChannelIsOneButton(unittest.TestCase):
    def setUp(self):
        self.aliases = load_alias_map()

    def test_one_channel_from_three_sources_is_one_button(self):
        streams = [
            stream("T Sports", "https://a.example/x.m3u8", source_id="srhady-toffee-bd"),
            stream("T Sports", "https://b.example/y.m3u8", source_id="sm-toffee-auto-update"),
            stream("T Sports", "https://c.example/z.m3u8", source_id="abusaeeidx-toffee-navigator"),
        ]
        channels, stats = build_event_channels(
            "sl-vs-ind", MATCH, streams, aliases=self.aliases
        )
        self.assertEqual([entry["name"] for entry in channels], ["T Sports"])
        self.assertEqual(
            [entry["role"] for entry in channels[0]["streams"]],
            ["primary", "backup", "backup"],
        )
        self.assertEqual(stats["channels"], 1)
        self.assertEqual(stats["generic_server_channels"], 0)

    def test_it_is_not_three_server_numbered_buttons(self):
        streams = [
            stream("T Sports", f"https://{host}.example/x.m3u8", source_id=host)
            for host in ("a", "b", "c")
        ]
        channels, _ = build_event_channels(
            "sl-vs-ind", MATCH, streams, aliases=self.aliases
        )
        names = [entry["name"] for entry in channels]
        self.assertNotIn("Server-1", names)
        self.assertEqual(len(names), 1)


class RuleThreeQualityVariantsFoldIntoOneChannel(unittest.TestCase):
    """The real trysports cricket/live.json shape, verbatim."""

    def setUp(self):
        self.aliases = load_alias_map()
        self.streams = [
            stream(name, f"https://lb.strmd.st/{index}/playlist.m3u8",
                   resolution="HD" if hd else "SD", height=720 if hd else 480)
            for index, (name, hd) in enumerate((
                ("Willow (HD)", True),
                ("Willow 2 (HD)", True),
                ("Willow Sports (HD)", True),
                ("Willow (SD)", False),
                ("Willow 2 (SD)", False),
                ("Willow Sports (SD)", False),
            ))
        ]

    def test_six_streams_become_three_named_buttons(self):
        channels, stats = build_event_channels(
            "willow-cricket", "Willow Cricket", self.streams, aliases=self.aliases
        )
        self.assertEqual(
            sorted(entry["name"] for entry in channels),
            ["Willow", "Willow 2", "Willow Sports"],
        )
        self.assertEqual(stats["channels"], 3)
        self.assertEqual(stats["variants"], 6)

    def test_each_button_carries_its_hd_primary_and_sd_backup(self):
        channels, _ = build_event_channels(
            "willow-cricket", "Willow Cricket", self.streams, aliases=self.aliases
        )
        for entry in channels:
            with self.subTest(channel=entry["name"]):
                roles = [item["role"] for item in entry["streams"]]
                self.assertEqual(roles, ["primary", "backup"])
                self.assertEqual(entry["streams"][0]["resolution"], "HD")
                self.assertEqual(entry["streams"][1]["resolution"], "SD")

    def test_no_button_label_carries_a_quality_tag(self):
        channels, _ = build_event_channels(
            "willow-cricket", "Willow Cricket", self.streams, aliases=self.aliases
        )
        for entry in channels:
            with self.subTest(channel=entry["name"]):
                self.assertNotIn("(HD)", entry["name"])
                self.assertNotIn("(SD)", entry["name"])


class RuleOneDifferentChannelsAreDifferentButtons(unittest.TestCase):
    def test_four_broadcasters_are_four_buttons(self):
        aliases = load_alias_map()
        streams = [
            stream(name, f"https://x.example/{index}.m3u8")
            for index, name in enumerate(
                ("T Sports", "Willow", "Star Sports 1", "Sony Ten 3")
            )
        ]
        channels, stats = build_event_channels("sl", MATCH, streams, aliases=aliases)
        self.assertEqual(
            sorted(entry["name"] for entry in channels),
            ["Sony Ten 3", "Star Sports 1", "T Sports", "Willow"],
        )
        self.assertEqual(stats["channels"], 4)

    def test_two_feeds_of_one_brand_stay_two_buttons(self):
        aliases = load_alias_map()
        streams = [
            stream("Sony Ten 1", "https://x.example/1.m3u8"),
            stream("Sony Ten 3", "https://x.example/3.m3u8"),
        ]
        channels, _ = build_event_channels("sl", MATCH, streams, aliases=aliases)
        self.assertEqual(
            sorted(entry["name"] for entry in channels), ["Sony Ten 1", "Sony Ten 3"]
        )


class RuleFourCricketIsOrderedFirst(unittest.TestCase):
    def test_cricket_sorts_ahead_of_football_and_the_rest(self):
        self.assertLess(sport_sort_index("cricket"), sport_sort_index("football"))
        self.assertLess(sport_sort_index("football"), sport_sort_index("tennis"))
        self.assertLess(sport_sort_index("football"), sport_sort_index("other"))

    def test_the_case_of_the_sport_name_does_not_matter(self):
        self.assertEqual(sport_sort_index("Cricket"), sport_sort_index("cricket"))

    def test_events_sorts_on_that_index(self):
        source = (ROOT / "scanner" / "events.py").read_text(encoding="utf-8")
        self.assertIn("sport_rank = sport_sort_index(", source)
        self.assertIn("return sport_rank, start_time, competition, name", source)


if __name__ == "__main__":
    unittest.main()
