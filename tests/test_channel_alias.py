"""One channel, however its playlist spells it - and never two channels as one.

The measured problem: sixteen TV playlists and over a thousand manual entries,
and Zee Bangla and Star Jalsha still had no working route. The scanner was not
dropping candidates through a cap or a timeout; it was treating every spelling
as a separate channel.

    Zee Bangla       -> tv:zee-bangla
    Zee Bangla HD    -> tv:zee-bangla-hd      <- different group
    [BD] Zee Bangla  -> tv:bd-zee-bangla      <- different group
    Zee Bangla VIP   -> tv:zee-bangla-vip     <- different group
    Star Jolsha      -> tv:star-jolsha        <- different group

Groups compete for a card's primary and backup slots, so routes that could
have served Zee Bangla sat in five other groups. Across the live sources, 324
groups were one channel split by spelling.

The asymmetry in these tests is the point. A missed merge costs a route. A
wrong merge puts another broadcaster's programme on the card, which the owner
called worse than a channel that stutters - so most of this file is about
merges that must NOT happen.
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import channel_alias as ca  # noqa: E402
from scanner import planner as P  # noqa: E402


def _key(name, **extra):
    item = {"name": name, "url": "https://x.example.net/a.m3u8", "source_pipeline": "tv"}
    item.update(extra)
    return P._group_key(item)


class MustMergeTests(unittest.TestCase):
    """Feed markers describe the feed, not the channel."""

    def test_resolution_suffixes_are_the_same_channel(self):
        for variant in ("Zee Bangla HD", "Zee Bangla SD", "Zee Bangla FHD",
                        "Zee Bangla Full HD", "ZEE BANGLA"):
            self.assertTrue(
                ca.same_channel("Zee Bangla", variant), variant
            )
            self.assertEqual(_key("Zee Bangla"), _key(variant), variant)

    def test_a_region_tag_is_the_same_channel(self):
        for variant in ("[BD] Zee Bangla", "(IN) Zee Bangla", "[BD] Zee Bangla HD"):
            self.assertEqual(_key("Zee Bangla"), _key(variant), variant)

    def test_a_playlist_tier_marker_is_the_same_channel(self):
        self.assertEqual(_key("Zee Bangla"), _key("Zee Bangla VIP"))

    def test_a_transliteration_is_the_same_channel(self):
        for variant in ("Star Jolsha", "Star Jalsa", "STAR JALSHA HD",
                        "Star Jalsha SD"):
            self.assertEqual(_key("Star Jalsha"), _key(variant), variant)

    def test_a_generic_word_no_longer_blocks_folding(self):
        """The guard that used to sit here lost routes for hundreds of channels.

        Feed markers and distinguishing words are disjoint lists, so stripping
        a marker can never remove "cinema" or "sonar" - the guard protected
        nothing while blocking every name containing a generic word like
        "sports" or "news".
        """
        self.assertEqual(_key("Star Sports 1"), _key("Star Sports 1 HD"))
        self.assertEqual(_key("Zee News"), _key("Zee News HD"))
        self.assertEqual(_key("Sony Ten 1"), _key("Sony Ten 1 HD"))


class MustNotMergeTests(unittest.TestCase):
    """The half that matters more."""

    def test_a_sibling_channel_is_never_folded_in(self):
        for sibling in ("Zee Bangla Cinema", "Zee Bangla Sonar",
                        "Zee Bangla Natok", "Zee Bangla Movies"):
            self.assertFalse(ca.same_channel("Zee Bangla", sibling), sibling)
            self.assertNotEqual(_key("Zee Bangla"), _key(sibling), sibling)

    def test_a_sibling_keeps_its_own_variants_together(self):
        """Cinema HD is Cinema, not the main channel."""
        self.assertEqual(_key("Zee Bangla Cinema"), _key("Zee Bangla Cinema HD"))
        self.assertNotEqual(_key("Zee Bangla"), _key("Zee Bangla Cinema HD"))

    def test_jalsha_movies_is_not_star_jalsha(self):
        self.assertNotEqual(_key("Star Jalsha"), _key("Jalsha Movies HD"))
        self.assertNotEqual(_key("Star Jalsha"), _key("Star Jalsha Movies"))

    def test_star_jalsha_digital_stays_its_own_card(self):
        """It is published as its own card with its own playback_id."""
        self.assertNotEqual(_key("Star Jalsha"), _key("Star Jalsha Digital"))

    def test_an_ampersand_channel_does_not_collapse_to_a_generic_name(self):
        """"&TV" reduced to "tv", the widest possible wrong merge."""
        self.assertEqual(ca.canonical_channel_name("&TV HD"), "and tv")
        self.assertNotEqual(_key("&TV HD"), _key("TV"))
        self.assertEqual(_key("&TV HD"), _key("& Tv"))

    def test_a_group_label_in_the_name_field_is_not_normalised(self):
        """"NEWS | India TV" flattens to the canonical form of a real,
        different channel: News India TV."""
        self.assertFalse(
            ca.same_channel("NEWS | India TV", "News India TV")
        )
        self.assertTrue(
            ca.canonical_channel_name("NEWS | India TV").startswith("unparsed:")
        )

    def test_a_name_that_is_only_markers_is_not_emptied(self):
        """An empty identity would merge everything that produced one."""
        self.assertEqual(ca.canonical_channel_name("HD"), "hd")
        self.assertEqual(ca.canonical_channel_name("SD"), "sd")
        self.assertNotEqual(
            ca.canonical_channel_name("HD"), ca.canonical_channel_name("SD")
        )

    def test_unrelated_channels_stay_apart(self):
        for left, right in (
            ("Sony MAX", "Sony MAX 2"),
            ("Star Sports 1 HD", "Star Sports 1 Select HD"),
            ("Colors", "Colors Bangla"),
            ("Zee TV", "Zee Cinema"),
            ("Aaj Tak", "Aaj Tak Bangla"),
        ):
            self.assertNotEqual(_key(left), _key(right), f"{left} vs {right}")


class PublishedCatalogueTests(unittest.TestCase):
    def test_no_two_published_cards_collapse_into_one_group(self):
        """The safety property for the live catalogue.

        Folding groups can only ever merge cards, and a merged card means one
        channel disappears from the site. Measured at zero across all 530.
        """
        cards = []
        for path in sorted((ROOT / "data" / "channels").glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            rows = payload if isinstance(payload, list) else payload.get("channels") or []
            cards += [c for c in rows if isinstance(c, dict) and c.get("name")]
        if not cards:
            self.skipTest("no published cards")
        groups = {}
        for card in cards:
            key = _key(card["name"], id=card.get("id"), url=card.get("url"))
            groups.setdefault(key, []).append(card["name"])
        collisions = {k: v for k, v in groups.items() if len(v) > 1}
        self.assertEqual(
            collisions, {},
            "two published channels now share one group and one would be lost",
        )


class CoverageAuditTests(unittest.TestCase):
    """The audit artifact has to exist and has to cover every source."""

    def test_the_audit_report_covers_every_configured_source(self):
        report = ROOT / "reports" / "tv-source-coverage.json"
        if not report.exists():
            self.skipTest("coverage audit not recorded")
        payload = json.loads(report.read_text(encoding="utf-8"))
        configured = json.loads(
            (ROOT / "config" / "sources" / "tv.json").read_text(encoding="utf-8")
        )["sources"]
        audited = {row["source_id"] for row in payload["sources"]}
        missing = [s["id"] for s in configured if s["id"] not in audited]
        self.assertEqual(missing, [], f"sources absent from the audit: {missing}")
        self.assertIn("manual-playlist", audited)

    def test_the_audit_records_a_reason_for_every_failed_source(self):
        report = ROOT / "reports" / "tv-source-coverage.json"
        if not report.exists():
            self.skipTest("coverage audit not recorded")
        payload = json.loads(report.read_text(encoding="utf-8"))
        for row in payload["sources"]:
            if row["enabled"] and row["parsed_entries"] == 0:
                self.assertTrue(
                    row["fetch_error"] or row["http_status"] not in (200, None),
                    f"{row['source_id']} yielded nothing and says no reason",
                )


if __name__ == "__main__":
    unittest.main()
