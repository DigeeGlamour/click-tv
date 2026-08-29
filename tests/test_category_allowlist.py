"""A curated category publishes only the names it lists.

The owner curates Indian by hand and asked, on 2026-08-30, for exactly
forty-eight names and nothing else - not as a card, not in the JSON. The
catalogue held 181 Indian cards at the time.

The mechanism has to live in configuration rather than in an edit to
data/channels/indian.json, because that file is generated: a scan rebuilds it
from source every few hours and any hand edit is gone. So the list sits in
config/channel-categories.json and scanner/channels.py enforces it on the way
into the published buckets.

These tests are mostly about the ways it must not spread. One category is
curated; the other seven take whatever the router hands them, and a filter that
quietly started applying to Bangla or Sports would be a far worse bug than the
one it fixes.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import category_allowlist as allowlist  # noqa: E402

CONFIG = ROOT / "config" / "channel-categories.json"


def _config(tmp, block):
    path = Path(tmp) / "channel-categories.json"
    path.write_text(json.dumps({"publish_allowlist": block}), encoding="utf-8")
    return str(path)


class MatchingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(allowlist.reset_cache)
        allowlist.reset_cache()
        self.path = _config(self.tmp.name, {"Indian": ["Colors Bangla", "B4U Movies"]})

    def test_an_exact_name_is_allowed(self):
        self.assertTrue(allowlist.is_allowed("Indian", "Colors Bangla", self.path))

    def test_case_does_not_matter(self):
        """The catalogue holds "COLORS BANGLA" and "B4U MOVIES"; the owner
        wrote them in title case. They are one name."""
        self.assertTrue(allowlist.is_allowed("Indian", "COLORS BANGLA", self.path))
        self.assertTrue(allowlist.is_allowed("Indian", "b4u movies", self.path))

    def test_whitespace_runs_do_not_matter(self):
        self.assertTrue(allowlist.is_allowed("Indian", " Colors   Bangla ", self.path))

    def test_a_different_spelling_is_not_the_same_name(self):
        """Deliberate. "Enter 10 Bangla", "Enter10 Bangla" and "Enterr 10
        Bangla" are duplicate cards of the "Enterr10 Bangla" that was asked
        for, and removing them is part of what the list is for."""
        self.assertFalse(allowlist.is_allowed("Indian", "Colors Bangla HD", self.path))
        self.assertFalse(allowlist.is_allowed("Indian", "ColorsBangla", self.path))

    def test_a_name_not_on_the_list_is_refused(self):
        self.assertFalse(allowlist.is_allowed("Indian", "Zee News", self.path))


class ScopeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(allowlist.reset_cache)
        allowlist.reset_cache()

    def test_an_unlisted_category_allows_everything(self):
        path = _config(self.tmp.name, {"Indian": ["Zee TV"]})
        for category in ("Bangla", "Sports", "Cartoon", "Other", "Islamic"):
            self.assertTrue(
                allowlist.is_allowed(category, "anything at all", path), category
            )
            self.assertFalse(allowlist.is_restricted(category, path))

    def test_an_empty_list_does_not_restrict(self):
        """An empty list would silently empty a category. It is read as absent."""
        path = _config(self.tmp.name, {"Indian": []})
        self.assertFalse(allowlist.is_restricted("Indian", path))
        self.assertTrue(allowlist.is_allowed("Indian", "Zee News", path))

    def test_a_missing_config_restricts_nothing(self):
        self.assertFalse(
            allowlist.is_restricted("Indian", str(Path(self.tmp.name) / "gone.json"))
        )

    def test_an_unreadable_config_restricts_nothing(self):
        path = Path(self.tmp.name) / "broken.json"
        path.write_text("{ not json", encoding="utf-8")
        self.assertFalse(allowlist.is_restricted("Indian", str(path)))
        self.assertTrue(allowlist.is_allowed("Indian", "Zee News", str(path)))


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(allowlist.reset_cache)
        allowlist.reset_cache()
        self.path = _config(self.tmp.name, {"Indian": ["Zee TV", "Star Plus"]})
        self.cards = [
            {"name": "Zee News"}, {"name": "Zee TV"},
            {"name": "Star Plus"}, {"name": "Republic Bharat"},
        ]

    def test_it_keeps_only_the_listed_cards_in_order(self):
        kept = allowlist.apply(self.cards, "Indian", self.path)
        self.assertEqual(["Zee TV", "Star Plus"], [c["name"] for c in kept])

    def test_it_names_what_it_refused(self):
        self.assertEqual(
            ["Zee News", "Republic Bharat"],
            allowlist.rejected(self.cards, "Indian", self.path),
        )

    def test_it_names_what_the_sources_did_not_deliver(self):
        kept = [{"name": "Zee TV"}]
        self.assertEqual(
            ["star plus"], allowlist.missing_from(kept, "Indian", self.path)
        )

    def test_an_unrestricted_category_is_returned_untouched(self):
        kept = allowlist.apply(self.cards, "Bangla", self.path)
        self.assertEqual(len(self.cards), len(kept))
        self.assertEqual([], allowlist.rejected(self.cards, "Bangla", self.path))


class TheCommittedConfigTests(unittest.TestCase):
    """What is actually configured, so a later edit cannot drift unnoticed."""

    def setUp(self):
        allowlist.reset_cache()
        self.addCleanup(allowlist.reset_cache)
        self.payload = json.loads(CONFIG.read_text(encoding="utf-8"))

    def test_only_indian_is_curated(self):
        block = self.payload.get("publish_allowlist") or {}
        self.assertEqual(["Indian"], list(block))

    def test_the_requested_names_are_all_there(self):
        names = allowlist.allowed_names("Indian")
        for wanted in ("star jalsha", "zee bangla", "sony sab", "joo music",
                       "sangeet bangla", "&tv", "9xm", "yrf music"):
            self.assertIn(wanted, names, wanted)

    def test_a_name_the_owner_did_not_ask_for_is_not_there(self):
        names = allowlist.allowed_names("Indian")
        for unwanted in ("zee news", "republic bharat", "aaj tak", "ndtv english",
                         "tv9 bangla", "enter 10 bangla"):
            self.assertNotIn(unwanted, names, unwanted)

    def test_every_listed_name_routes_to_indian(self):
        """A name the router sends elsewhere can never reach this filter, so
        listing it would silently do nothing. JOO MUSIC and Sangeet Bangla both
        routed to Other until they were added to the Indian identity registry."""
        from scanner.normalizer import Normalizer

        normalizer = Normalizer()
        stray = [
            name for name in sorted(allowlist.allowed_names("Indian"))
            if normalizer.detect_tv_category(name) != "Indian"
        ]
        self.assertEqual([], stray)

    def test_the_published_catalogue_holds_nothing_else(self):
        path = ROOT / "data" / "channels" / "indian.json"
        if not path.is_file():
            self.skipTest("no Indian catalogue")
        cards = json.loads(path.read_text(encoding="utf-8")).get("channels") or []
        extra = allowlist.rejected(cards, "Indian")
        self.assertEqual([], extra, f"{len(extra)} unlisted card(s) published")


class TheScannerEnforcesItTests(unittest.TestCase):
    """A list nothing reads is a comment."""

    def test_channels_py_filters_on_the_way_into_the_bucket(self):
        source = (ROOT / "scanner" / "channels.py").read_text(encoding="utf-8")
        self.assertIn("category_allowlist.is_allowed(", source)
        head, _, tail = source.partition("category_allowlist.is_allowed(")
        self.assertIn("continue", tail[:400])

    def test_a_refused_card_is_reported_rather_than_dropped_silently(self):
        source = (ROOT / "scanner" / "channels.py").read_text(encoding="utf-8")
        self.assertIn("rejected_by_allowlist", source)
        self.assertIn("category-allowlist.json", source)

    def test_it_is_not_re_routed_to_other(self):
        """Moving it would leave the card the owner asked to remove."""
        source = (ROOT / "scanner" / "channels.py").read_text(encoding="utf-8")
        _, _, tail = source.partition("category_allowlist.is_allowed(")
        block = tail[: tail.index("categorized[canonical_category].append")]
        self.assertNotIn('"Other"', block)


if __name__ == "__main__":
    unittest.main()
