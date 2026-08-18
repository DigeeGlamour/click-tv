# -*- coding: utf-8 -*-
"""The twelve production defects found by auditing the deployed site.

Every test here was written against something real: a card that published twice on
clicktv.pages.dev, an id that changed between tabs, a `category` that disagreed
with the file it sat in, a league the sport filter hid, a channels[] that was empty
on every card, an artwork field that was published and then ignored, an internal
server key that reached the screen as a channel name, and a live buffer measured in
seconds against four-second segments.

The style is deliberate: assert the requirement, not the implementation detail, so
that a future change which keeps the promise keeps the test.
"""
from __future__ import annotations

import json
import re
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scanner.channel_resolver import (  # noqa: E402
    load_source_broadcasters,
    resolve_channel_name,
    load_alias_map,
)
from scanner.event_lifecycle import event_destination  # noqa: E402
from scanner.events import (  # noqa: E402
    _append_embed_channels,
    _payload,
    _relabel_generic_channels,
    _stamp_final_routing,
)
from scanner.live_protection import (  # noqa: E402
    _absorb_carried_card,
    _rebuild_card_channels,
    _reconcile_layer,
)
from scanner.merger import (  # noqa: E402
    authoritative_fixture_window,
    event_sport,
    kickoffs_compatible,
    normalize_event_key,
    same_real_fixture,
    _normalized_competition,
)
from scanner.schedule_resolver import (  # noqa: E402
    _competition_round_fixture,
    load_fixtures,
    reuse_published_event_ids,
)
from scanner.streamed_provider import (  # noqa: E402
    StreamedSettings,
    normalize_embed_streams,
    normalize_match,
)

APP = (ROOT / "site/assets/js/app.js").read_text(encoding="utf-8")
CHANNEL_CSS = (ROOT / "site/assets/css/event-channel-cards.css").read_text(encoding="utf-8")


def _css_rules(text: str) -> str:
    """The stylesheet with its comments removed, so a comment cannot satisfy a test."""
    return re.sub(r"/\*.*?\*/", " ", text, flags=re.S)


# ---------------------------------------------------------------- problem 1 / 12
class OneRealFixtureIsOneCard(unittest.TestCase):
    """Production published `Sri Lanka vs India 1st Test` four times at once."""

    def test_a_series_prefix_does_not_mint_a_second_card(self):
        # "India tour of Sri Lanka 2026 1st Test Sri Lanka vs India" was a second
        # card for a fixture already published as "Sri Lanka vs India 1st Test",
        # because the "A vs B" extraction anchors at the start of the title and
        # swallowed the whole series name into the left-hand side.
        self.assertEqual(
            normalize_event_key("India tour of Sri Lanka 2026 1st Test Sri Lanka vs India"),
            normalize_event_key("Sri Lanka vs India 1st Test"),
        )
        self.assertEqual(
            normalize_event_key("Copa America 2026 Brazil vs Argentina"),
            normalize_event_key("Brazil vs Argentina"),
        )

    def test_a_year_inside_a_team_name_is_not_a_series_prefix(self):
        """The prefix strip must not be able to eat a participant."""
        for name in ("TSG 1899 Hoffenheim vs Bayern Munchen",
                     "Schalke 04 vs 1860 Munich",
                     "PSV 1913 vs Ajax"):
            key = normalize_event_key(name)
            self.assertIn("vs", key, name)
            left = key.split("-vs-")[0]
            self.assertGreaterEqual(len(left), 4, f"{name} lost its home side: {key}")

    def test_two_different_rounds_stay_two_cards(self):
        self.assertNotEqual(
            normalize_event_key("Sri Lanka vs India 1st Test"),
            normalize_event_key("Sri Lanka vs India 2nd Test"),
        )

    def test_a_round_in_the_competition_field_is_not_a_competition(self):
        """A provider with no series field puts the round there instead.

        The catalogue said "India Tour of Sri Lanka 2026" and the playlist said
        "1st Test"; comparing those as competitions made one live Test contradict
        itself and publish twice.
        """
        self.assertEqual(_normalized_competition({"competition": "1st Test"}), "")
        self.assertEqual(_normalized_competition({"competition": "Day 3"}), "")
        self.assertEqual(_normalized_competition({"competition": "Group A"}), "")
        # A real competition is still a real competition.
        self.assertEqual(_normalized_competition({"competition": "Coppa Italia"}), "coppa italia")
        self.assertIn("world", _normalized_competition({"competition": "World Series"}))
        self.assertNotEqual(_normalized_competition({"competition": "Premier League"}), "")

    def test_a_multi_day_test_is_one_card_across_its_days(self):
        """Day 3 of a Test starts two days after day 1 - and is the same match.

        Kickoff tolerance is 90 minutes, so comparing the two kickoffs said
        "different fixtures". The catalogue's own [start, end] is what settles it,
        which is exactly why config/event-fixtures.json carries an explicit `end`.
        """
        catalogue_day_one = {
            "name": "Sri Lanka vs India 1st Test",
            "competition": "India Tour of Sri Lanka 2026",
            "fixture_id": "india-sri-lanka-tests-2026:sri-lanka-vs-india-1st-test",
            "start_time": "2026-08-15T04:30:00+00:00",
            "end_time": "2026-08-19T12:30:00+00:00",
            "time_verification": "official_catalogue",
        }
        relay_day_three = {
            "name": "Sri Lanka vs India",
            "competition": "1st Test",
            "fixture_id": "provider:sri-lanka-vs-india|1st test|2026-08-17",
            "start_time": "2026-08-17T04:30:00+00:00",
            "end_time": "2026-08-17T10:21:52+00:00",
            "time_verification": "provider_feed",
        }
        self.assertIsNotNone(authoritative_fixture_window(catalogue_day_one))
        self.assertTrue(kickoffs_compatible(catalogue_day_one, relay_day_three))
        self.assertTrue(same_real_fixture(catalogue_day_one, relay_day_three))

    def test_only_the_catalogue_may_widen_identity_with_a_long_window(self):
        """A provider's guess at an end time must not merge two real matches."""
        guessed = {
            "name": "Alpha vs Beta",
            "fixture_id": "provider:alpha-vs-beta|league|2026-08-15",
            "start_time": "2026-08-15T04:30:00+00:00",
            "end_time": "2026-08-19T12:30:00+00:00",
            "time_verification": "provider_feed",
        }
        self.assertIsNone(authoritative_fixture_window(guessed))
        other_date = {
            "name": "Alpha vs Beta",
            "start_time": "2026-08-17T04:30:00+00:00",
        }
        self.assertFalse(kickoffs_compatible(guessed, other_date))
        self.assertFalse(same_real_fixture(guessed, other_date))

    def test_a_window_shorter_than_a_day_is_not_a_multi_day_window(self):
        same_day = {
            "name": "Alpha vs Beta",
            "fixture_id": "series-2026:alpha-vs-beta",
            "start_time": "2026-08-15T04:30:00+00:00",
            "end_time": "2026-08-15T12:30:00+00:00",
            "time_verification": "official_catalogue",
        }
        self.assertIsNone(authoritative_fixture_window(same_day))

    def test_the_merge_groups_by_destination_not_by_source_feed(self):
        """Grouping and routing have to agree, or one match lands twice in one tab.

        Routing decides Today vs Upcoming from the schedule status, so a live
        fixture configured under an "upcoming" feed was grouped away from the same
        fixture under a "today" feed - and then routed into Today beside it.
        """
        source = (ROOT / "scanner/merger.py").read_text(encoding="utf-8")
        self.assertIn("event_destination(c)", source)
        self.assertIn("bucket", source)
        live_from_upcoming_feed = {"source_pipeline": "upcoming", "schedule_status": "LIVE_NOW"}
        self.assertEqual(event_destination(live_from_upcoming_feed), "today_match")


class AParticipantLessLabelBindsToItsFixture(unittest.TestCase):
    """"Day 3 1st Test 17 Aug 2026 | India Tour of Sri Lanka 2026" - no teams."""

    @classmethod
    def setUpClass(cls):
        cls.fixtures = load_fixtures(ROOT / "config/event-fixtures.json")
        cls.now = datetime(2026, 8, 17, 20, 0, tzinfo=timezone.utc)

    def test_the_competition_name_counts_as_one_of_its_aliases(self):
        """It did not, so a title spelling the series exactly still missed it."""
        for fixture in self.fixtures:
            self.assertIn(
                re.sub(r"[^a-z0-9 ]+", " ", fixture["competition"].casefold()).strip(),
                " ".join(fixture["competition_aliases"]) + " ",
                fixture["competition"],
            )

    def test_a_day_label_resolves_to_the_fixture_it_is_carrying(self):
        item = {"name": "Day 3 1st Test 17 Aug 2026 | India Tour of Sri Lanka 2026",
                "url": "https://example.invalid/live.m3u8"}
        match = _competition_round_fixture(item, self.fixtures, self.now)
        self.assertIsNotNone(match)
        self.assertEqual(match["name"], "Sri Lanka vs India 1st Test")

    def test_the_round_has_to_agree(self):
        item = {"name": "Day 3 4th Test 17 Aug 2026 | India Tour of Sri Lanka 2026",
                "url": "https://example.invalid/live.m3u8"}
        self.assertIsNone(_competition_round_fixture(item, self.fixtures, self.now))

    def test_a_label_outside_the_live_window_is_refused(self):
        """A channel label is reused between matches; only the window proves which."""
        item = {"name": "Day 3 1st Test | India Tour of Sri Lanka 2026",
                "url": "https://example.invalid/live.m3u8"}
        long_after = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
        self.assertIsNone(_competition_round_fixture(item, self.fixtures, long_after))

    def test_a_title_with_real_participants_is_left_to_team_scoring(self):
        item = {"name": "Sri Lanka vs India 1st Test", "url": "https://example.invalid/x.m3u8"}
        self.assertIsNone(_competition_round_fixture(item, self.fixtures, self.now))


# -------------------------------------------------------------------- problem 2
class EventIdContinuity(unittest.TestCase):
    def test_reuse_never_hands_a_card_an_id_another_card_owns(self):
        """Production published both `sri-lanka-vs-india-1st-test` and
        `sri-lanka-vs-india` for one fixture, because the second card's name
        matched a previously published entry whose id the first card had minted."""
        published = {
            "type": "today_match",
            "items": [{"id": "sri-lanka-vs-india", "name": "Sri Lanka vs India"}],
        }
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "today-match.json").write_text(json.dumps(published), encoding="utf-8")
            (root / "upcoming.json").write_text(json.dumps({"items": []}), encoding="utf-8")
            items = [
                {"id": "sri-lanka-vs-india", "name": "Sri Lanka vs India 1st Test"},
                {"id": "sri-lanka-vs-india-other", "name": "Sri Lanka vs India"},
            ]
            reuse_published_event_ids(items, data_root=root)
            ids = [item["id"] for item in items]
            self.assertEqual(len(ids), len(set(ids)), f"duplicate ids after reuse: {ids}")

    def test_a_genuine_promotion_still_keeps_its_published_id(self):
        import tempfile

        published = {"items": [{"id": "arsenal-vs-chelsea", "name": "Arsenal vs Chelsea"}]}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "upcoming.json").write_text(json.dumps(published), encoding="utf-8")
            items = [{"id": "arsenal-vs-chelsea-premier-league", "name": "Arsenal vs Chelsea"}]
            reused = reuse_published_event_ids(items, data_root=root)
            self.assertEqual(reused, 1)
            self.assertEqual(items[0]["id"], "arsenal-vs-chelsea")
            self.assertTrue(items[0]["promoted_card"])


# -------------------------------------------------------------------- problem 3
class FinalRoutingOwnsTheCategory(unittest.TestCase):
    def test_a_live_card_from_an_upcoming_feed_is_labelled_today_match(self):
        """Production shipped `event_type: today_match` beside `category: upcoming`."""
        card = {"source_pipeline": "upcoming", "category": "upcoming",
                "schedule_status": "LIVE_NOW"}
        _stamp_final_routing(card, "today_match")
        self.assertEqual(card["category"], "today_match")
        self.assertEqual(card["source_pipeline"], "today_match")
        self.assertEqual(card["event_type"], "today_match")

    def test_the_feed_it_came_from_is_still_recorded(self):
        card = {"source_pipeline": "upcoming", "category": "upcoming"}
        _stamp_final_routing(card, "today_match")
        self.assertEqual(card["original_source_pipeline"], "upcoming")
        self.assertEqual(card["routing_changed_from"], "upcoming")

    def test_an_unmoved_card_is_not_marked_as_rerouted(self):
        card = {"source_pipeline": "today_match", "category": "today_match"}
        _stamp_final_routing(card, "today_match")
        self.assertNotIn("routing_changed_from", card)


# -------------------------------------------------------------------- problem 6
class SportClassification(unittest.TestCase):
    """These published as sport_type "other", so the Football tab hid them."""

    FOOTBALL = (
        "De Graafschap vs Jong AZ Eerste Divisie",
        "Jong Ajax vs Emmen Eerste Divisie",
        "All Boys vs Nueva Chicago Primera Nacional",
        "Centro Espanol vs Central Cordoba Primera C",
        "Fram Reykjavik vs Stjarnan Urvalsdeild",
        "Grindavik vs Grotta 1 Deild",
        "Leiknir R vs Aegir 1 Deild",
        "Sportivo Belgrano vs Defensores de Belgrano Torneo Federal A",
    )

    def test_the_leagues_the_filter_was_hiding_are_football(self):
        for name in self.FOOTBALL:
            self.assertEqual(event_sport({"name": name}), "football", name)

    def test_hockey_is_not_football(self):
        """"FIH Hockey World Cup" matched football's bare "Cup"."""
        self.assertEqual(event_sport({"name": "Germany vs Belgium | FIH Hockey World Cup 2026"}), "hockey")
        self.assertEqual(event_sport({"name": "Boston vs Toronto NHL"}), "hockey")

    def test_cricket_still_outranks_football(self):
        self.assertEqual(event_sport({"name": "Sri Lanka vs India 1st Test"}), "cricket")
        self.assertEqual(event_sport({"name": "Jamaica Kingsmen vs Guyana Amazon Warriors - CPL 6th Match"}), "cricket")

    def test_an_ambiguous_fixture_is_not_given_an_invented_sport(self):
        """"England vs Pakistan" with no competition could be either code."""
        self.assertEqual(event_sport({"name": "England vs Pakistan"}), "other")


# ----------------------------------------------------------------- problem 7 / 12
class EveryCardCanNameItsBroadcasters(unittest.TestCase):
    """channels[] was empty on every production card - 0 of 30 showed a strip."""

    @classmethod
    def setUpClass(cls):
        cls.aliases = load_alias_map(ROOT / "config/channel-aliases.json")

    def test_each_event_feed_declares_the_broadcaster_it_relays(self):
        """Only a genuine, independently-recognizable broadcaster is declared.

        "Bingstream", "AX Sports", "CricketLive" and "CricHD" were declared here
        once, and every one of them is just the feed maintainer's own GitHub
        repo/aggregator name - not a channel a viewer would recognize on air. A
        viewer correctly called this out: it read as a fabricated brand sitting
        next to real ones like Willow and SonyLIV. Removed; the genuinely real
        ones stay.
        """
        declared = load_source_broadcasters(ROOT / "config")
        self.assertGreaterEqual(len(declared), 4, declared)
        fake = {"bingstream", "ax sports", "cricketlive", "crichd"}
        for source_id, name in declared.items():
            self.assertTrue(name.strip(), source_id)
            self.assertNotIn("hady", name.casefold(), "a maintainer is not a broadcaster")
            self.assertNotRegex(name.casefold(), r"^(playlist|events?|sports?|data)$")
            self.assertNotIn(name.casefold(), fake,
                             f"{name!r} is a feed/aggregator name, not a broadcaster")
        # The genuinely real, independently-existing brands are still declared.
        for real in ("Tapmad", "SonyLIV", "Willow"):
            self.assertIn(real, declared.values(), declared)

    def test_a_stream_joins_a_channel_from_the_feed_it_arrived_in(self):
        for source_id in ("srhady-tapmad-bd-live", "sm-tapmad-auto", "srhady-willow-event-upcoming"):
            resolved = resolve_channel_name({"source_id": source_id},
                                            "Sri Lanka vs India 1st Test", self.aliases)
            self.assertTrue(resolved.resolved, source_id)
            self.assertTrue(resolved.name.strip(), source_id)

    def test_a_removed_fake_declaration_resolves_to_nothing_not_a_new_lie(self):
        """"Bingstream" is a repository name, not a channel. Removing its
        declaration must leave the stream honestly unnamed - not silently
        replaced by some other guess - when nothing else on the stream names it.
        """
        resolved = resolve_channel_name(
            {"source_id": "srhady-bingstream-live"},
            "Sri Lanka vs India 1st Test", self.aliases,
        )
        self.assertFalse(resolved.resolved)

    def test_a_title_that_names_its_channel_still_wins_over_the_feed(self):
        resolved = resolve_channel_name(
            {"source_id": "srhady-bingstream-live", "name": "Sri Lanka vs India Willow HD"},
            "Sri Lanka vs India 1st Test", self.aliases)
        self.assertEqual(resolved.normalized, "willow")

    def test_a_multi_broadcaster_feed_declares_nothing(self):
        """A wrong channel name is worse than none - section 12."""
        declared = load_source_broadcasters(ROOT / "config")
        self.assertNotIn("sm-sports-data-upcoming", declared)
        resolved = resolve_channel_name({"source_id": "sm-sports-data-upcoming"},
                                       "Alpha vs Beta", self.aliases)
        self.assertFalse(resolved.resolved)

    def test_an_over_trimmed_declaration_is_not_thrown_away(self):
        """"AX Sports" cleans down to the bare category "Sports" and was dropped."""
        resolved = resolve_channel_name({"broadcaster": "AX Sports"}, "Alpha vs Beta", self.aliases)
        self.assertTrue(resolved.resolved)
        self.assertIn("ax", resolved.normalized)

    def test_a_carried_card_gets_its_channels_rebuilt(self):
        """A card carried forward by live protection never passes through the merge,
        so the longest-running live fixtures published with no channels[] at all."""
        layer = _reconcile_layer()
        self.assertIsNotNone(layer, "the reconcile layer must be available")
        card = {
            "id": "sri-lanka-vs-india-1st-test",
            "name": "Sri Lanka vs India 1st Test",
            "playback_id": "ctv_" + "a" * 32,
            "source_id": "srhady-bingstream-live",
            "verified": True,
            "backups": [
                {"name": "Backup-1", "playback_id": "ctv_" + "b" * 32, "source_id": "sm-tapmad-auto"},
                {"name": "Backup-2", "playback_id": "ctv_" + "c" * 32, "source_id": "srhady-sonyliv-live"},
            ],
        }
        added = _rebuild_card_channels(card, layer)
        self.assertEqual(added, 3, card.get("channels"))
        names = [channel["name"] for channel in card["channels"]]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(card["channel_count"], 3)
        # Section 27: naming a default here would reorder a working playback plan.
        self.assertEqual(card.get("default_channel_id"), "")

    def test_a_grouping_the_merge_already_made_is_never_overwritten(self):
        layer = _reconcile_layer()
        card = {"id": "x", "name": "Alpha vs Beta", "playback_id": "ctv_" + "d" * 32,
                "channels": [{"id": "x--willow", "name": "Willow"}]}
        self.assertEqual(_rebuild_card_channels(card, layer), 0)
        self.assertEqual(len(card["channels"]), 1)

    def test_a_rebuilt_channel_leaks_no_url_header_or_key(self):
        layer = _reconcile_layer()
        card = {"id": "e", "name": "Alpha vs Beta", "playback_id": "ctv_" + "e" * 32,
                "source_id": "srhady-bingstream-live",
                "url": "https://secret.invalid/live.m3u8?token=abc",
                "headers": {"Cookie": "session=zzz"}}
        _rebuild_card_channels(card, layer)
        blob = json.dumps(card.get("channels") or [])
        for forbidden in ("secret.invalid", "token=abc", "Cookie", "session=zzz", "headers"):
            self.assertNotIn(forbidden, blob, forbidden)

    def test_absorbing_a_channel_grouped_carried_card_keeps_its_broadcaster_names(self):
        """Production regression: a long-running Test match already sorted into
        named channels (Willow, CricLife 1, Sony Sports Ten...) got carried
        forward across a scan, then reconciled into a freshly-routed host card
        that only had one generic stream of its own. Absorption tried to name
        ONE channel from the carried card's own title - "Sri Lanka vs India
        1st Test" - which section 12 correctly refuses to resolve, so every
        real broadcaster name was dropped and the raw streams survived only as
        anonymous Backup-N entries. The published card lost every named
        channel it had, down to just the host's own generic one."""
        layer = _reconcile_layer()
        canonical = {
            "id": "sri-lanka-vs-india", "name": "Sri Lanka vs India",
            "playback_id": "ctv_" + "a" * 32,
            "channels": [{
                "id": "sri-lanka-vs-india--server-1", "name": "Server-1",
                "normalized_name": "server-1", "name_confidence": "generic",
                "primary_stream_id": "sri-lanka-vs-india--server-1--1",
                "stream_count": 1, "backup_count": 0,
                "streams": [{
                    "id": "sri-lanka-vs-india--server-1--1", "role": "primary",
                    "playback_type": "native", "variant_key": "sv_host",
                    "playback_id": "ctv_" + "a" * 32,
                }],
            }],
            "channel_count": 1, "backups": [],
            "default_channel_id": "sri-lanka-vs-india--server-1",
        }
        carried = {
            "id": "sri-lanka-vs-india-1st-test", "name": "Sri Lanka vs India 1st Test",
            "playback_id": "ctv_" + "b" * 32,
            "channels": [
                {"id": "sri-lanka-vs-india-1st-test--willow", "name": "Willow",
                 "normalized_name": "willow", "name_confidence": "explicit",
                 "primary_stream_id": "x--1", "stream_count": 1, "backup_count": 0,
                 "streams": [{"id": "x--1", "role": "primary", "playback_type": "native",
                              "variant_key": "sv_willow", "playback_id": "ctv_" + "c" * 32}]},
                {"id": "sri-lanka-vs-india-1st-test--criclife-1", "name": "CricLife 1",
                 "normalized_name": "criclife-1", "name_confidence": "explicit",
                 "primary_stream_id": "y--1", "stream_count": 1, "backup_count": 0,
                 "streams": [{"id": "y--1", "role": "primary", "playback_type": "native",
                              "variant_key": "sv_criclife", "playback_id": "ctv_" + "d" * 32}]},
            ],
            "channel_count": 2,
        }
        absorbed = _absorb_carried_card(canonical, carried, layer)
        self.assertEqual(absorbed, 2)
        names = {channel["name"] for channel in canonical["channels"]}
        self.assertEqual(names, {"Server-1", "Willow", "CricLife 1"})
        self.assertEqual(canonical["channel_count"], 3)

    def test_absorbing_the_same_carried_channel_twice_does_not_duplicate_it(self):
        layer = _reconcile_layer()
        canonical = {
            "id": "x", "name": "Alpha vs Beta", "playback_id": "ctv_" + "a" * 32,
            "channels": [{
                "id": "x--willow", "name": "Willow", "normalized_name": "willow",
                "name_confidence": "explicit", "primary_stream_id": "x--willow--1",
                "stream_count": 1, "backup_count": 0,
                "streams": [{"id": "x--willow--1", "role": "primary", "playback_type": "native",
                             "variant_key": "sv_shared", "playback_id": "ctv_" + "b" * 32}],
            }],
            "channel_count": 1, "backups": [],
        }
        carried = {
            "id": "y", "name": "Alpha vs Beta", "playback_id": "ctv_" + "b" * 32,
            "channels": [{
                "id": "y--willow", "name": "Willow", "normalized_name": "willow",
                "name_confidence": "explicit", "primary_stream_id": "y--willow--1",
                "stream_count": 1, "backup_count": 0,
                "streams": [{"id": "y--willow--1", "role": "primary", "playback_type": "native",
                             "variant_key": "sv_shared", "playback_id": "ctv_" + "b" * 32}],
            }],
            "channel_count": 1,
        }
        _absorb_carried_card(canonical, carried, layer)
        self.assertEqual(canonical["channel_count"], 1)


# ----------------------------------------------------------------- problem 4 / 5
class StreamedEnrichmentActuallyContributes(unittest.TestCase):
    SETTINGS = {"streamed_provider": {"enabled": True, "base_url": "https://p.example",
                                      "images_base": "https://p.example/api/images"}}

    def setUp(self):
        self.settings = StreamedSettings.from_settings(self.SETTINGS)

    def test_the_poster_is_addressed_by_both_team_badges(self):
        """The live API sends no `poster` field, so a poster built from one was
        never requested and every card fell through to two initials."""
        candidate = normalize_match({
            "id": "m1", "title": "Lanus vs Independiente", "category": "football",
            "date": 1786996800000,
            "teams": {"home": {"name": "Lanus", "badge": "HOMEBADGE"},
                      "away": {"name": "Independiente", "badge": "AWAYBADGE"}},
        }, self.settings)
        self.assertEqual(candidate["provider_poster_url"],
                         "https://p.example/api/images/poster/HOMEBADGE/AWAYBADGE.webp")
        self.assertEqual(candidate["home_badge_url"],
                         "https://p.example/api/images/badge/HOMEBADGE.webp")
        self.assertEqual(candidate["provider_artwork"][0], candidate["provider_poster_url"])

    def test_a_full_path_poster_from_the_live_api_is_not_re_wrapped(self):
        """The live endpoint does send its own `poster` field after all - a
        complete path such as "/api/images/proxy/<token>.webp", not a bare
        badge id. Running that back through the "{base}/poster/{id}.webp"
        template nested one path inside another and appended a second
        ".webp", so a match arriving this way still fell through to initials
        even though the provider was handing back a working poster."""
        candidate = normalize_match({
            "id": "ppv-india-vs-sri-lanka-1-st-test",
            "title": "India vs. Sri Lanka - 1st Test", "category": "cricket",
            "date": 1787027400000, "popular": True,
            "teams": {"home": {"name": "India", "badge": ""},
                      "away": {"name": "Sri Lanka - 1st Test", "badge": ""}},
            "poster": "/api/images/proxy/GwZg7AZpYEZgHCA.webp",
        }, self.settings)
        self.assertEqual(candidate["provider_poster_url"],
                         "https://p.example/api/images/proxy/GwZg7AZpYEZgHCA.webp")
        self.assertEqual(candidate["provider_artwork"][0], candidate["provider_poster_url"])

    def test_no_badges_means_no_artwork_claimed(self):
        candidate = normalize_match({
            "id": "m2", "title": "Alpha vs Beta", "category": "cricket", "date": 1786996800000,
            "teams": {"home": {"name": "Alpha"}, "away": {"name": "Beta"}},
        }, self.settings)
        self.assertNotIn("provider_artwork", candidate)
        self.assertNotIn("provider_poster_url", candidate)

    def test_an_embed_is_never_labelled_with_an_internal_server_key(self):
        """The card offered a chip reading "admin 1" - the provider's own server
        name, which is internal plumbing and names no broadcaster at all."""
        streams = normalize_embed_streams(
            [{"source": "admin", "id": "a1", "streamNo": 1, "hd": True,
              "embedUrl": "https://p.example/embed/a1"},
             {"source": "delta", "id": "d1", "streamNo": 2, "hd": False,
              "embedUrl": "https://p.example/embed/d1"}],
            self.settings)
        self.assertEqual(len(streams), 2)
        for stream in streams:
            self.assertNotIn("admin", stream["name"].casefold())
            self.assertNotIn("delta", stream["name"].casefold())
            self.assertIn("streamed", stream["name"].casefold())
        # The internal key stays in the data for reports, just not as a name.
        self.assertEqual(streams[0]["provider_source"], "admin")

    def test_the_shipped_configuration_points_somewhere_real(self):
        settings = json.loads((ROOT / "config/settings.json").read_text(encoding="utf-8"))
        provider = settings["streamed_provider"]
        if provider.get("enabled"):
            self.assertTrue(provider["base_url"].startswith("https://"))
            self.assertTrue(provider["images_base"].startswith("https://"))

    def test_enrichment_matches_a_fixture_by_its_participants(self):
        """The provider's "Sri Lanka vs India" never met the catalogue's
        "Sri Lanka vs India 1st Test", so its poster and embed went unused."""
        source = (ROOT / "scanner/events.py").read_text(encoding="utf-8")
        self.assertIn("participant_fold_key", source)
        self.assertIn("same_real_fixture(card, candidate)", source)
        self.assertIn("matched_by_participants", source)


# -------------------------------------------------------------------- problem 9
class LiveBufferIsMeasuredInSegments(unittest.TestCase):
    """Reproduced on the deployed site: four freezes of 6.4s, 8.1s, 8.1s and 5.1s
    on `Sri Lanka vs India 1st Test` in the "Fast Start" profile, buffer draining
    to 0.05s - because the event profile holds 5 seconds against 4-second
    segments, which is one and a quarter fragments."""

    def test_the_reserve_is_taken_from_the_playlists_own_segment_length(self):
        self.assertIn("function applySegmentAwareLiveBuffer", APP)
        self.assertIn("details.targetduration", APP)
        self.assertIn("LIVE_MIN_BUFFER_SEGMENTS", APP)

    def test_it_is_applied_when_the_playlist_arrives(self):
        self.assertIn("Hls.Events.LEVEL_LOADED", APP)
        self.assertRegex(APP, r"LEVEL_LOADED[^)]*\)[^{]*\{[^}]*applySegmentAwareLiveBuffer",
                         "the level-loaded handler must apply the buffer")

    def test_it_only_ever_widens_the_reserve(self):
        """A stream that never had the problem must keep its fast start."""
        body = APP.split("function applySegmentAwareLiveBuffer", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("< floor", body)
        self.assertIn("< maxFloor", body)
        self.assertNotIn("= floor;", body.replace("maxBufferLength = floor;", ""))
        # Only a raise is written, guarded by a comparison against the current value.
        for guard in ("Number(hls.config.maxBufferLength || 0) < floor",
                      "Number(hls.config.maxMaxBufferLength || 0) < maxFloor"):
            self.assertIn(guard, body)

    def test_edge_chasing_is_dropped_for_long_segments(self):
        body = APP.split("function applySegmentAwareLiveBuffer", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("lowLatencyMode = false", body)
        self.assertIn("liveSyncDurationCount = 3", body)

    def test_three_segments_of_a_four_second_stream_is_at_least_twelve_seconds(self):
        body = APP.split("const LIVE_MIN_BUFFER_SEGMENTS", 1)[1].split("\n\n", 1)[0]
        minimum = int(re.search(r"LIVE_MIN_BUFFER_SEGMENTS\s*=\s*(\d+)", "LIVE_MIN_BUFFER_SEGMENTS" + body).group(1))
        self.assertGreaterEqual(minimum, 3)
        self.assertGreaterEqual(minimum * 4, 12)


# -------------------------------------------------------------------- problem 8
class TheFixtureAndItsChannelsAreOneCard(unittest.TestCase):
    """Production rendered a finished card with a coloured bottom edge and then a
    separate rounded box underneath - two detached boxes for one event."""

    def test_the_seam_between_the_row_and_the_strip_is_removed(self):
        rules = _css_rules(CHANNEL_CSS)
        self.assertIn(":has(.event-channel-strip)", rules)
        self.assertIn("border-bottom-left-radius:0!important", rules)
        self.assertIn("border-bottom-color:transparent!important", rules)

    def test_the_outer_edge_is_drawn_once_around_the_pair(self):
        rules = _css_rules(CHANNEL_CSS)
        shell = re.search(
            r"\.event-card-shell:has\(\.event-channel-strip\)\{([^}]*)\}", rules)
        self.assertIsNotNone(shell)
        self.assertIn("border-radius:14px!important", shell.group(1))
        self.assertIn("overflow:hidden!important", shell.group(1))

    def test_the_locked_row_geometry_is_untouched(self):
        """Section 2. Only corner radii and a border colour may change - neither
        participates in layout, so the 152px row keeps its exact metrics."""
        rules = _css_rules(CHANNEL_CSS)
        row = re.findall(r"\.event-card-shell:has\(\.event-channel-strip\)\s*>\s*\.event-ref-card\{([^}]*)\}", rules)
        self.assertEqual(len(row), 1)
        for forbidden in ("height", "width", "padding", "margin", "grid-template",
                          "border-bottom-width", "box-sizing", "position"):
            self.assertNotIn(forbidden, row[0], forbidden)

    def test_a_browser_without_has_is_not_left_with_a_doubled_border(self):
        self.assertIn("@supports not selector(:has(*))", _css_rules(CHANNEL_CSS))

    def test_the_artwork_tile_is_a_thumbnail_not_a_slab(self):
        """The initials block read heavier than the fixture title beside it."""
        rules = _css_rules(CHANNEL_CSS)
        versus = re.search(r"\.event-art-versus span\{([^}]*)\}", rules)
        self.assertIsNotNone(versus)
        size = float(re.search(r"font-size:([\d.]+)px", versus.group(1)).group(1))
        self.assertLessEqual(size, 14.0, "the initials must not outweigh the title")

    def test_two_real_crests_are_drawn_with_a_vs_between_them(self):
        self.assertIn("event-art-crests", APP)
        self.assertIn("data-event-badge=\"home\"", APP)
        self.assertIn("data-event-badge=\"away\"", APP)
        rules = _css_rules(CHANNEL_CSS)
        crest = re.search(r"\.event-art-crests img\{([^}]*)\}", rules)
        self.assertIsNotNone(crest)
        self.assertIn("object-fit:contain!important", crest.group(1))

    def test_artwork_falls_through_every_real_picture_before_initials(self):
        self.assertIn("function eventArtworkChain", APP)
        self.assertIn("data-art-fallbacks", APP)
        chain = APP.split("function eventArtworkChain", 1)[1].split("\nfunction ", 1)[0]
        for field in ("logo", "provider_poster_url", "artwork_candidates"):
            self.assertIn(field, chain, field)

    def test_a_half_broken_crest_pair_is_replaced_whole(self):
        """One team showing and the other missing is not an honest card."""
        self.assertIn("crests.replaceWith", APP)

    def test_every_chip_states_both_counts(self):
        """Section 5 lists Primary *and* Backups, so a zero is still shown.

        Omitting "0 Backups" read tidier and was wrong: it is what the design
        document asks for, twenty-four viewport assertions check for it, and the
        crowding it was meant to relieve is the column floor's job.
        """
        summary = APP.split("function channelChipSummary", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("} Primary`", summary)
        self.assertIn("'Backup' : 'Backups'", summary)
        self.assertNotIn("if (backups > 0)", summary)

    def test_the_strip_width_floor_keeps_the_summary_readable(self):
        """Three chips in a 375px sidebar ellipsised to "1 Pri... 0 Ba...")."""
        floors = [int(v) for v in re.findall(
            r"repeat\(auto-fit,minmax\((\d+)px,1fr\)\)", _css_rules(CHANNEL_CSS))]
        self.assertTrue(floors)
        self.assertGreaterEqual(min(floors), 150)


class EveryPublishedChannelIsActuallyReachable(unittest.TestCase):
    """Verified live: "Sri Lanka vs India 1st Test" published 8 channels, but
    clicking CricLife 1, Sony Sports Ten 1 or Willow Cricket silently kept
    playing whatever the primary already was - the exact "you put the same
    link in every channel" report.

    The event-level primary+backups list the player draws attempts from is
    capped (MAX_PUBLISHED_BACKUPS on the scanner side), but a fixture can have
    more channels than that cap allows through. orderSourcesByChannel() can
    only ever *reorder* that capped list; it cannot add a channel's stream
    that was never in it at all, so a channel beyond the cap could be selected
    and had no way to ever actually play - the app just kept running the
    stream it already had, which looks exactly like every channel shares one
    link. Reproduced end-to-end with the real production card and its real
    channels[]/streams[] shape via headless Chromium and this file's own
    test conventions - a source-content assertion mirrors the runtime browser
    proof already done, so a regression here fails offline too.
    """

    def test_the_attempt_pool_is_widened_before_channel_ordering(self):
        source = APP.split("function buildAttemptPlan(item)", 1)[0]
        self.assertIn("function sourcesReachableForEveryChannel", source)
        body = source.split("function sourcesReachableForEveryChannel", 1)[1]
        # Every channel's own stream must be considered, not only the ones
        # that happen to already be in the event-level source list.
        self.assertIn("eventChannels(item)", body)
        self.assertIn("channel.streams", body)

    def test_build_attempt_plan_actually_calls_the_widened_pool(self):
        body = APP.split("function buildAttemptPlan(item) {", 1)[1].split(chr(10) + "function ", 1)[0]
        self.assertIn("sourcesReachableForEveryChannel(", body)
        # The widened pool has to feed the SAME variable orderSourcesByChannel
        # then reorders, or the widening does nothing.
        self.assertIn("orderSourcesByChannel(item, rankedSources)", body)

    def test_an_embed_stream_is_never_folded_into_the_native_attempt_pool(self):
        """Section 27 still holds after the widening - an embed is a last
        resort, never a candidate the native attempt planner can pick up."""
        body = APP.split("function sourcesReachableForEveryChannel", 1)[1].split(chr(10) + "function ", 1)[0]
        self.assertIn("stream.playback_type === 'embed'", body)
        self.assertIn("return", body.split("stream.playback_type === 'embed'", 1)[1][:40])


class TheStickyRouteMemoryCannotOverrideAChannelSwitch(unittest.TestCase):
    """The widening fix alone was NOT enough - reported and reproduced live.

    Verified on the real deployed site by tracing buildAttemptPlan step by
    step: sourcesReachableForEveryChannel, channelStreamOrder and
    orderSourcesByChannel all correctly put the selected channel's stream
    first, every time - but buildAttemptPlan then re-sorted that already-
    correct list by state.routePreferences[itemPlaybackKey(item)], a single
    "this stream worked last time" memory slot keyed by the WHOLE EVENT, not
    by channel. That memory belongs to an older, single-source stickiness
    feature written before multi-channel selection existed: once any one
    channel's stream had ever succeeded, every later channel switch for that
    same event was silently re-sorted back to it. Reproduced by seeding the
    exact production condition (a remembered preference for one channel's
    stream) and confirming every other channel still resolved to it; the same
    scenario, rerun after this fix, resolves each channel to itself.
    """

    def test_the_preference_is_checked_against_the_active_channel(self):
        body = APP.split("function buildAttemptPlan(item) {", 1)[1].split(chr(10) + "function ", 1)[0]
        self.assertIn("activeChannelId(item)", body)
        self.assertIn("belongsToActiveChannel", body)
        # The override must actually happen - a preference outside the active
        # channel is discarded, not merely noted.
        self.assertIn("preferred = null", body)

    def test_a_single_channel_item_keeps_its_original_stickiness(self):
        """A movie or plain TV feed has no channels[] at all - channels.length
        is 0, so the guard must not touch its behaviour."""
        body = APP.split("function buildAttemptPlan(item) {", 1)[1].split(chr(10) + "function ", 1)[0]
        guard = body.split("const channels = eventChannels(item);", 1)[1].split("const sources = ", 1)[0]
        self.assertIn("channels.length", guard)


class AnHonestFallbackChannelGetsItsOwnBrand(unittest.TestCase):
    """"Server-1"/"Streamed-1" read as backend plumbing to a viewer, per
    direct request for a nicer, user-friendly alternative. Chosen and
    ordered by the same request: Click Live, Click Plus, Click Max, Click
    Ultra, Click Prime, Click Pro, Click Edge, Click X, then Click One,
    Click Go, Click Now, Click Play if a card genuinely has more than eight."""

    def test_native_and_embed_generic_channels_share_one_sequence_in_order(self):
        card = {
            "id": "evt-1",
            "channels": [
                {"id": "evt-1--willow", "name": "Willow", "name_confidence": "explicit"},
                {"id": "evt-1--server-1", "name": "Server-1", "name_confidence": "generic"},
                {"id": "evt-1--server-2", "name": "Server-2", "name_confidence": "generic"},
                {"id": "evt-1--streamed-1", "name": "Streamed 1", "name_confidence": "generic"},
            ],
        }
        _relabel_generic_channels(card)
        names = [channel["name"] for channel in card["channels"]]
        self.assertEqual(names, ["Willow", "Click Live", "Click Plus", "Click Max"])

    def test_a_named_channel_is_never_relabelled(self):
        card = {"channels": [{"id": "x", "name": "Sony Sports Ten 3", "name_confidence": "explicit"}]}
        _relabel_generic_channels(card)
        self.assertEqual(card["channels"][0]["name"], "Sony Sports Ten 3")

    def test_more_generic_channels_than_the_named_series_still_publishes_something(self):
        card = {"channels": [
            {"id": f"x{i}", "name": f"Server-{i}", "name_confidence": "generic"}
            for i in range(1, 14)
        ]}
        _relabel_generic_channels(card)
        names = [channel["name"] for channel in card["channels"]]
        self.assertEqual(len(names), len(set(names)), names)
        self.assertEqual(names[-1], "Click 13")

    def test_a_streamed_embed_channel_is_marked_generic_not_explicit(self):
        """The provider's own embed API carries no broadcaster field at all,
        so every embed channel it produces is an honest-fallback placeholder
        - never a genuinely resolved name - even though passing its
        auto-generated label through as an explicit channel_name would
        otherwise earn it "explicit" confidence from the resolver."""
        card = {
            "id": "evt-1", "name": "Alpha vs Beta", "playback_id": "ctv_" + "a" * 32,
            "channels": [{"id": "evt-1--willow", "name": "Willow", "name_confidence": "explicit",
                          "streams": [{"playback_id": "ctv_" + "a" * 32}]}],
            "embed_backups": [
                {"name": "Streamed 1", "provider": "streamed", "embed_url": "https://embed.st/a/1"},
            ],
        }
        _append_embed_channels(card)
        embed_channel = next(c for c in card["channels"] if c["id"] != "evt-1--willow")
        self.assertEqual(embed_channel["name_confidence"], "generic")

    def test_an_already_published_embed_channel_is_refreshed_not_frozen(self):
        """Production regression: a card carried forward scan after scan (a
        long-running Test match the live playlist did not re-list this
        round) keeps whatever channels[] it already published. An id already
        present there used to mean "skip it, already there" - which also
        skipped ever refreshing it, so a card that got its embed channels
        before the generic-confidence fix landed kept showing them under
        their old raw "Streamed 1"/"Streamed 2" names forever after, on
        every later scan, because this function never got a chance to
        re-mark them."""
        card = {
            "id": "evt-1", "name": "Alpha vs Beta", "playback_id": "ctv_" + "a" * 32,
            "channels": [
                {"id": "evt-1--willow", "name": "Willow", "name_confidence": "explicit",
                 "streams": [{"playback_id": "ctv_" + "a" * 32}]},
                {"id": "evt-1--streamed-1", "name": "Streamed 1", "name_confidence": "explicit",
                 "renderer": "embed", "streams": [{"embed_url": "https://embed.st/old/1"}]},
            ],
            "embed_backups": [
                {"name": "Streamed 1", "provider": "streamed", "embed_url": "https://embed.st/a/1"},
            ],
        }
        _append_embed_channels(card)
        stale = next(c for c in card["channels"] if c["id"] == "evt-1--streamed-1")
        self.assertEqual(stale["name_confidence"], "generic")
        _relabel_generic_channels(card)
        self.assertEqual(stale["name"], "Click Live")

    def test_payload_relabels_every_items_generic_channels(self):
        items = [{
            "id": "evt-1", "name": "Alpha vs Beta",
            "channels": [{"id": "evt-1--server-1", "name": "Server-1", "name_confidence": "generic"}],
        }]
        payload = _payload(items, "today_match", filtered_stale=0, filtered_unplayable=0)
        self.assertEqual(payload["items"][0]["channels"][0]["name"], "Click Live")


if __name__ == "__main__":
    unittest.main()
