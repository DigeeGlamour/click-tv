"""A direct channel and a fixture are two identity domains, not one.

PROMPT 15, and a production bug rather than a hypothetical. On 2026-09-10 the
Tapmad direct feed and `srhady-tapmad-bd` both carried

    https://saseries.akamaized.net/hls/live/2110097/ZIMvsIND-3827e1/master.m3u8

while the direct feed titled that row "Rotterdam Dockers vs Glasgow Cosmic"
and srhady titled it "Belfast Wolves vs Amsterdam Flames". The published
`reports/fixture-stream-health.json` for the 14:49 scan names
`sm-tapmad-channels` inside the `source_ids` of BOTH `belfast-wolves-vs-
amsterdam-flames` and `england-vs-pakistan` - fixtures it had supplied no
fixture evidence for - and no direct-channel candidate survived that scan at
all. Replayed against the real feeds, three separate stages did it:

    planner._exact_stream_key   URL equality folded the two candidates into
                                one, and the survivor carried the other's
                                provenance. The direct row was then gone.
    resolver enrich             `_is_exact_event` is true of a title with
                                "vs" in it, so `_best_fixture` matched a
                                catalogue fixture and `_apply_fixture` stamped
                                the channel with a fixture id, a kickoff and
                                `schedule_verified: True`.
    merger grouping             `fixture_identity_key` answers from the name,
                                so the channel and the fixture of that name
                                became ONE card - the channel's route a backup
                                of the fixture, `entry_type` gone.

Then, inside the same afternoon, the feed rewrote that row's title from
"Rotterdam Dockers vs Glasgow Cosmic" to "Belfast Wolves vs Amsterdam Flames"
under an unchanged id and an unchanged URL. A name that moves like that is
display metadata. It is not evidence about what is playing, and it can never
be evidence about which fixture a stream belongs to.

So: a URL identifies a ROUTE. Route dedupe stays inside one identity domain.
"""
import copy
import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scanner import direct_channel                          # noqa: E402
from scanner import fixture_dedupe                          # noqa: E402
from scanner import planner                                 # noqa: E402
from scanner import schedule_resolver                       # noqa: E402
from scanner import upstream_family                         # noqa: E402
from scanner.merger import merge_candidates                 # noqa: E402
from scanner.parsers import event_adapters                  # noqa: E402

SETTINGS = str(ROOT / "config" / "settings.json")

#: The physical URL both feeds carried on 2026-09-10, verbatim.
SHARED_URL = (
    "https://saseries.akamaized.net/hls/live/2110097/ZIMvsIND-3827e1/master.m3u8"
)

FIXTURE_TITLE = "Belfast Wolves vs Amsterdam Flames"
DIRECT_TITLE = "Belfast Wolves vs Amsterdam Flames | European T20 Premier League 2026"
OLD_DIRECT_TITLE = "Rotterdam Dockers vs Glasgow Cosmic | European T20 Premier League 2026"

LIVE = {
    "source_pipeline": "today_match",
    "schedule_status": "LIVE_NOW",
    "verification_status": "verified_global",
    "verified": True,
    "publish_allowed": True,
    "competition": "European T20 Premier League 2026",
    "start_time": "2026-09-10T13:15:00+00:00",
    "end_time": "2026-09-10T17:15:00+00:00",
}


def fixture_candidate(**overrides):
    """A real fixture from a fixture-authority feed."""
    card = dict(
        LIVE,
        id="s-fixture",
        name=FIXTURE_TITLE,
        url=SHARED_URL,
        source_id="srhady-tapmad-bd",
        fixture_id="etpl-2026:belfast-wolves-vs-amsterdam-flames",
        schedule_verified=True,
    )
    card.update(overrides)
    return card


def direct_candidate(**overrides):
    """A direct-channel row: a name, a logo, a group, a URL. No clock."""
    card = dict(
        LIVE,
        id="sm-tapmad-channels-1",
        name=DIRECT_TITLE,
        url=SHARED_URL,
        source_id="sm-tapmad-channels",
        entry_type=direct_channel.ENTRY_TYPE,
        channel_identity="tapmad-16707",
        schedule_status=direct_channel.CHANNEL_STATUS,
        status=direct_channel.CHANNEL_STATUS,
        today_source_channel=True,
        schedule_verified=False,
    )
    card.pop("start_time", None)
    card.pop("end_time", None)
    card.update(overrides)
    return card


class TheDomainIsReadFromTheDataTests(unittest.TestCase):
    """Requirement 2. Two domains, and the answer comes from `entry_type`."""

    def test_a_direct_channel_row_is_in_the_direct_domain(self):
        self.assertTrue(direct_channel.is_direct_channel(direct_candidate()))
        self.assertEqual(
            direct_channel.identity_domain(direct_candidate()),
            direct_channel.DIRECT_DOMAIN,
        )

    def test_a_fixture_is_in_the_fixture_domain(self):
        self.assertFalse(direct_channel.is_direct_channel(fixture_candidate()))
        self.assertEqual(
            direct_channel.identity_domain(fixture_candidate()),
            direct_channel.FIXTURE_DOMAIN,
        )

    def test_the_two_domains_are_not_the_same_string(self):
        self.assertNotEqual(
            direct_channel.FIXTURE_DOMAIN, direct_channel.DIRECT_DOMAIN)

    def test_nothing_else_decides_the_domain(self):
        # Not the source id, not the host, not the title. A feed declares
        # `entry_type` in its registry entry; anything else is a guess.
        for card in (
            dict(fixture_candidate(), source_id="sm-tapmad-channels"),
            dict(fixture_candidate(), url=SHARED_URL),
            dict(fixture_candidate(), name=DIRECT_TITLE),
            dict(fixture_candidate(), today_source_channel=True),
        ):
            self.assertFalse(direct_channel.is_direct_channel(card))

    def test_the_identity_is_the_channel_and_never_the_title(self):
        first = direct_candidate(name=OLD_DIRECT_TITLE)
        second = direct_candidate(name=DIRECT_TITLE)
        self.assertEqual(
            direct_channel.identity_key(first),
            direct_channel.identity_key(second),
        )
        self.assertEqual(direct_channel.identity_key(first), "tapmad-16707")

    def test_a_row_with_no_stated_identity_still_gets_one(self):
        row = direct_candidate()
        row.pop("channel_identity")
        row.pop("identity", None)
        row["id"] = ""
        self.assertTrue(direct_channel.identity_key(row))

    def test_a_non_dict_is_not_a_direct_channel(self):
        self.assertFalse(direct_channel.is_direct_channel(None))
        self.assertFalse(direct_channel.is_direct_channel("direct_channel"))
        self.assertEqual(direct_channel.identity_key(None), "")


class APlannerGroupIsOneDomainTests(unittest.TestCase):
    """Requirement 1 and 2. The verification group is a fixture's routes."""

    def test_a_direct_channel_is_grouped_by_its_channel(self):
        key = planner._group_key(direct_candidate())
        self.assertIn(direct_channel.DIRECT_DOMAIN, key)
        self.assertIn("tapmad-16707", key)

    def test_a_fixture_like_name_does_not_put_it_in_a_fixture_group(self):
        # The whole bug in one assertion: the title reads "A vs B".
        self.assertNotEqual(
            planner._group_key(direct_candidate()),
            planner._group_key(fixture_candidate()),
        )

    def test_the_group_survives_a_retitle(self):
        self.assertEqual(
            planner._group_key(direct_candidate(name=OLD_DIRECT_TITLE)),
            planner._group_key(direct_candidate(name=DIRECT_TITLE)),
        )

    def test_two_direct_channels_are_two_groups(self):
        self.assertNotEqual(
            planner._group_key(direct_candidate(channel_identity="tapmad-16707")),
            planner._group_key(direct_candidate(channel_identity="tapmad-15935")),
        )

    def test_a_fixture_group_is_unchanged_by_this(self):
        self.assertEqual(
            planner._group_key(fixture_candidate()),
            "today_match:belfast-wolves-vs-amsterdam-flames",
        )

    def test_two_relays_of_one_fixture_still_share_a_group(self):
        self.assertEqual(
            planner._group_key(fixture_candidate(source_id="srhady-willow-event")),
            planner._group_key(fixture_candidate(source_id="srhady-bingstream")),
        )


class URLEqualityDoesNotCrossTheDomainsTests(unittest.TestCase):
    """Requirement 3 and 7. Same URL, two domains, two candidates."""

    def test_the_exact_stream_key_differs_across_the_domains(self):
        self.assertNotEqual(
            planner._exact_stream_key(direct_candidate()),
            planner._exact_stream_key(fixture_candidate()),
        )

    def test_the_same_url_inside_one_domain_is_still_one_route(self):
        # The dedupe is not weakened, only bounded. Two relays of one fixture
        # offering the identical URL remain one candidate.
        self.assertEqual(
            planner._exact_stream_key(fixture_candidate(source_id="a")),
            planner._exact_stream_key(fixture_candidate(source_id="b")),
        )
        self.assertEqual(
            planner._exact_stream_key(direct_candidate(source_id="a")),
            planner._exact_stream_key(direct_candidate(source_id="b")),
        )

    def _planned(self, candidates):
        result = planner.plan_candidates(copy.deepcopy(candidates), mode="today")
        return result[0] if isinstance(result, tuple) else result.get("candidates")

    def test_both_candidates_reach_the_verifier(self):
        planned = self._planned([fixture_candidate(), direct_candidate()])
        domains = sorted(direct_channel.identity_domain(item) for item in planned)
        self.assertEqual(
            domains, [direct_channel.DIRECT_DOMAIN, direct_channel.FIXTURE_DOMAIN])

    def test_the_fixture_group_gains_no_direct_source(self):
        planned = self._planned([fixture_candidate(), direct_candidate()])
        for item in planned:
            if direct_channel.is_direct_channel(item):
                continue
            self.assertNotIn(
                "sm-tapmad-channels", list(item.get("source_ids") or []))
            for record in item.get("source_provenance") or []:
                self.assertNotEqual(record.get("source_id"), "sm-tapmad-channels")

    def test_the_direct_group_gains_no_fixture_source(self):
        planned = self._planned([fixture_candidate(), direct_candidate()])
        for item in planned:
            if not direct_channel.is_direct_channel(item):
                continue
            self.assertNotIn(
                "srhady-tapmad-bd", list(item.get("source_ids") or []))

    def test_the_verification_group_of_the_direct_route_is_its_own(self):
        planned = self._planned([fixture_candidate(), direct_candidate()])
        for item in planned:
            group = str(item.get("_verification_group") or "")
            if direct_channel.is_direct_channel(item):
                self.assertIn(direct_channel.DIRECT_DOMAIN, group)
            else:
                self.assertNotIn(direct_channel.DIRECT_DOMAIN, group)

    def test_a_direct_channel_never_becomes_a_fixtures_route(self):
        # `fixture_stream_health` reads `_verification_group` as the fixture a
        # route belongs to, which is how the 14:49 report came to name
        # sm-tapmad-channels inside two fixtures.
        planned = self._planned([fixture_candidate(), direct_candidate()])
        fixture_groups = {
            str(item.get("_verification_group") or "")
            for item in planned if not direct_channel.is_direct_channel(item)
        }
        direct_groups = {
            str(item.get("_verification_group") or "")
            for item in planned if direct_channel.is_direct_channel(item)
        }
        self.assertTrue(fixture_groups)
        self.assertTrue(direct_groups)
        self.assertFalse(fixture_groups & direct_groups)


class TheResolverNeverGivesAChannelAFixtureTests(unittest.TestCase):
    """Requirement 2 and 5. No fixture_id, no kickoff, no authority."""

    NOW = datetime(2026, 9, 10, 14, 45, tzinfo=timezone.utc)

    def setUp(self):
        # A catalogue holding exactly the fixture the channel's title names -
        # which is what made this reachable. Written per test, so the
        # repository's own catalogue is never involved.
        self.catalogue = ROOT / "tests" / "_tmp_direct_isolation_fixtures.json"
        self.catalogue.write_text(json.dumps({
            "version": 1,
            "display_timezone": "Asia/Dhaka",
            "competitions": [{
                "id": "etpl-2026",
                "name": "European T20 Premier League 2026",
                "timezone": "UTC",
                "utc_offset": "+00:00",
                "duration_minutes": 240,
                "fixtures": [{"name": FIXTURE_TITLE,
                              "start": "2026-09-10T15:15:00"}],
            }],
        }), encoding="utf-8")
        self.addCleanup(self.catalogue.unlink)

    def _resolve(self, candidates):
        return schedule_resolver.enrich_event_candidates(
            copy.deepcopy(candidates),
            fixture_path=str(self.catalogue),
            now=self.NOW,
        )

    def _direct_rows(self, output):
        return [card for card in output
                if str(card.get("source_id") or "") == "sm-tapmad-channels"]

    def test_the_title_does_not_earn_a_fixture_id(self):
        output, _ = self._resolve([direct_candidate()])
        rows = self._direct_rows(output)
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0].get("fixture_id"))

    def test_the_title_does_not_earn_a_kickoff(self):
        output, _ = self._resolve([direct_candidate()])
        row = self._direct_rows(output)[0]
        self.assertIsNone(row.get("start_time"))
        self.assertIsNone(row.get("end_time"))

    def test_the_schedule_is_never_marked_verified(self):
        output, _ = self._resolve([direct_candidate()])
        self.assertIs(self._direct_rows(output)[0].get("schedule_verified"), False)

    def test_it_publishes_as_a_channel(self):
        output, _ = self._resolve([direct_candidate()])
        row = self._direct_rows(output)[0]
        self.assertEqual(row.get("schedule_status"), direct_channel.CHANNEL_STATUS)
        self.assertIs(row.get("today_source_channel"), True)

    def test_the_domain_survives_the_resolver(self):
        output, _ = self._resolve([direct_candidate()])
        self.assertTrue(
            direct_channel.is_direct_channel(self._direct_rows(output)[0]))

    def test_no_fixture_counts_it_as_a_match(self):
        _, stats = self._resolve([direct_candidate()])
        self.assertEqual(stats.get("matched"), 0)
        self.assertEqual(stats.get("direct_channel_kept"), 1)

    def test_an_unusable_direct_row_is_counted_and_dropped(self):
        _, stats = self._resolve([direct_candidate(url="")])
        self.assertEqual(stats.get("direct_channel_kept"), 0)
        self.assertEqual(stats.get("direct_channel_unusable"), 1)

    def test_it_is_never_offered_to_another_fixture_as_a_broadcaster(self):
        pool = []
        schedule_resolver.enrich_event_candidates(
            [direct_candidate()],
            fixture_path=str(self.catalogue),
            now=self.NOW,
            attachment_pool=pool,
        )
        self.assertEqual(pool, [])

    def test_a_row_the_feed_ended_is_not_published(self):
        output, _ = self._resolve([direct_candidate(schedule_status="ENDED",
                                                    status="ENDED")])
        self.assertEqual(self._direct_rows(output), [])

    def test_the_fixture_beside_it_is_resolved_exactly_as_before(self):
        output, stats = self._resolve([fixture_candidate()])
        rows = [card for card in output
                if str(card.get("source_id") or "") == "srhady-tapmad-bd"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(stats.get("matched"), 1)
        self.assertTrue(rows[0].get("fixture_id"))


class TheMergeKeepsTwoCardsTests(unittest.TestCase):
    """Requirement 5, 6 and 7. No enrichment without strong evidence."""

    def _cards(self, candidates):
        return merge_candidates(copy.deepcopy(candidates), settings_path=SETTINGS)

    def test_a_channel_and_a_fixture_are_two_cards(self):
        cards = self._cards([fixture_candidate(), direct_candidate()])
        self.assertEqual(len(cards), 2)

    def test_the_fixture_does_not_take_the_channels_route_as_a_backup(self):
        cards = self._cards([
            fixture_candidate(url="https://willow.test/fixture.m3u8"),
            direct_candidate(),
        ])
        fixture = next(card for card in cards
                       if not direct_channel.is_direct_channel(card))
        urls = {str(row.get("url") or "")
                for row in fixture.get("backups") or []}
        self.assertNotIn(SHARED_URL, urls)

    def test_the_fixture_source_ids_gain_nothing(self):
        cards = self._cards([
            fixture_candidate(url="https://willow.test/fixture.m3u8"),
            direct_candidate(),
        ])
        fixture = next(card for card in cards
                       if not direct_channel.is_direct_channel(card))
        self.assertNotIn("sm-tapmad-channels", list(fixture.get("source_ids") or []))

    def test_the_channel_source_ids_gain_nothing(self):
        cards = self._cards([
            fixture_candidate(url="https://willow.test/fixture.m3u8"),
            direct_candidate(),
        ])
        channel = next(card for card in cards
                       if direct_channel.is_direct_channel(card))
        self.assertEqual(list(channel.get("source_ids") or []),
                         ["sm-tapmad-channels"])

    def test_the_domain_and_the_identity_travel_with_the_card(self):
        cards = self._cards([fixture_candidate(), direct_candidate()])
        channel = next(card for card in cards
                       if direct_channel.is_direct_channel(card))
        self.assertEqual(channel.get("entry_type"), direct_channel.ENTRY_TYPE)
        self.assertEqual(channel.get("channel_identity"), "tapmad-16707")

    def test_a_fixture_card_is_not_given_an_entry_type(self):
        cards = self._cards([fixture_candidate(), direct_candidate()])
        fixture = next(card for card in cards
                       if not direct_channel.is_direct_channel(card))
        self.assertNotIn("entry_type", fixture)
        self.assertNotIn("channel_identity", fixture)

    def test_the_channel_card_carries_no_fixture_identity(self):
        cards = self._cards([direct_candidate()])
        self.assertEqual(len(cards), 1)
        self.assertFalse(str(cards[0].get("fixture_id") or ""))

    def test_two_direct_identities_remain_two_cards(self):
        cards = self._cards([
            direct_candidate(id="d1", channel_identity="tapmad-16707"),
            direct_candidate(id="d2", channel_identity="tapmad-15935",
                             name="England vs Pakistan | Pakistan Tour of England 2026",
                             url="https://saseries.akamaized.net/hls/live/"
                                 "2110097/2353jkiL-tapmad/master.m3u8"),
        ])
        self.assertEqual(len(cards), 2)
        self.assertEqual(sum(len(card.get("backups") or []) for card in cards), 0)

    def test_one_broadcaster_never_becomes_another_ones_backup(self):
        cards = self._cards([
            direct_candidate(id="d1", channel_identity="tapmad-16707"),
            direct_candidate(id="d2", channel_identity="willow-1",
                             name="Willow Cricket",
                             url="https://willow.test/channel.m3u8"),
        ])
        self.assertEqual(len(cards), 2)
        for card in cards:
            self.assertEqual(card.get("backups") or [], [])

    def test_two_relays_of_one_fixture_are_still_one_card(self):
        cards = self._cards([
            fixture_candidate(id="f1", source_id="srhady-willow-event",
                              url="https://willow.test/a.m3u8"),
            fixture_candidate(id="f2", source_id="srhady-bingstream",
                              url="https://bing.test/b.m3u8"),
        ])
        self.assertEqual(len(cards), 1)
        self.assertEqual(len(cards[0].get("backups") or []), 1)


class TheFixtureDedupeRefusesAChannelTests(unittest.TestCase):
    """Requirement 6. `_absorb` is what moves `source_ids` between cards."""

    def test_a_direct_channel_names_no_participants(self):
        self.assertIsNone(fixture_dedupe.sides(direct_candidate()))

    def test_a_fixture_still_names_its_participants(self):
        self.assertEqual(
            fixture_dedupe.sides(fixture_candidate()),
            ("belfast wolves", "amsterdam flames"),
        )

    def test_the_pair_is_never_the_same_fixture(self):
        left = dict(fixture_candidate(), name=FIXTURE_TITLE)
        right = dict(direct_candidate(), name=FIXTURE_TITLE,
                     start_time=left["start_time"])
        self.assertFalse(fixture_dedupe.same_fixture(left, right))
        self.assertFalse(fixture_dedupe.same_fixture(right, left))

    def test_the_fold_keeps_both_cards(self):
        left = dict(fixture_candidate(), name=FIXTURE_TITLE,
                    source_ids=["srhady-tapmad-bd"])
        right = dict(direct_candidate(), name=FIXTURE_TITLE,
                     start_time=left["start_time"],
                     source_ids=["sm-tapmad-channels"])
        kept, report = fixture_dedupe.fold([left, right])
        self.assertEqual(len(kept), 2)
        self.assertEqual(report, [])

    def test_the_fold_moves_no_source_ids(self):
        left = dict(fixture_candidate(), name=FIXTURE_TITLE,
                    source_ids=["srhady-tapmad-bd"])
        right = dict(direct_candidate(), name=FIXTURE_TITLE,
                     start_time=left["start_time"],
                     source_ids=["sm-tapmad-channels"])
        fixture_dedupe.fold([left, right])
        self.assertEqual(left["source_ids"], ["srhady-tapmad-bd"])
        self.assertEqual(right["source_ids"], ["sm-tapmad-channels"])

    def test_two_spellings_of_one_fixture_still_fold(self):
        left = dict(fixture_candidate(), id="f1", name="Belfast Wolves vs Amsterdam Flames",
                    source_ids=["srhady-willow-event"])
        right = dict(fixture_candidate(), id="f2", name="Belfast Wolves vs Amsterdam Flames SC",
                     source_ids=["srhady-bingstream"])
        kept, report = fixture_dedupe.fold([left, right])
        self.assertEqual(len(kept), 1)
        self.assertEqual(len(report), 1)


class TheRetitleCannotMoveAFixtureTests(unittest.TestCase):
    """Requirement 8. Measured live: one id, one URL, two titles in a day."""

    def _cards(self, title):
        return merge_candidates(
            [fixture_candidate(url="https://willow.test/fixture.m3u8"),
             direct_candidate(name=title)],
            settings_path=SETTINGS,
        )

    def test_the_channel_keeps_its_identity_across_the_retitle(self):
        first = self._cards(OLD_DIRECT_TITLE)
        second = self._cards(DIRECT_TITLE)
        for cards in (first, second):
            channel = next(card for card in cards
                           if direct_channel.is_direct_channel(card))
            self.assertEqual(channel.get("channel_identity"), "tapmad-16707")

    def test_neither_title_reaches_the_fixture(self):
        for title in (OLD_DIRECT_TITLE, DIRECT_TITLE):
            fixture = next(card for card in self._cards(title)
                           if not direct_channel.is_direct_channel(card))
            self.assertEqual(fixture.get("name"), FIXTURE_TITLE)
            self.assertNotIn("sm-tapmad-channels",
                             list(fixture.get("source_ids") or []))

    def test_the_fixture_id_does_not_move(self):
        for title in (OLD_DIRECT_TITLE, DIRECT_TITLE):
            fixture = next(card for card in self._cards(title)
                           if not direct_channel.is_direct_channel(card))
            self.assertEqual(fixture.get("fixture_id"),
                             "etpl-2026:belfast-wolves-vs-amsterdam-flames")

    def test_the_route_stays_on_the_channel_under_both_titles(self):
        for title in (OLD_DIRECT_TITLE, DIRECT_TITLE):
            channel = next(card for card in self._cards(title)
                           if direct_channel.is_direct_channel(card))
            self.assertEqual(channel.get("url"), SHARED_URL)

    def test_the_two_titles_are_one_planner_group(self):
        self.assertEqual(
            planner._group_key(direct_candidate(name=OLD_DIRECT_TITLE)),
            planner._group_key(direct_candidate(name=DIRECT_TITLE)),
        )


class PlayabilityDecidesTheStandaloneCardTests(unittest.TestCase):
    """Requirement 4. A verified route publishes; a dead one does not."""

    NOW = datetime(2026, 9, 10, 14, 45, tzinfo=timezone.utc)

    def _admit(self, card):
        from scanner import events
        return events._admit_to_today(
            copy.deepcopy(card), self.NOW,
            routing_minutes=25, no_link_grace_minutes=25,
            today_max_age_hours=12, post_match_grace_minutes=20,
        )

    def test_a_verified_channel_publishes_as_channel_live(self):
        admitted, reason, _ = self._admit(direct_candidate(
            verification_status="verified_global", verified=True))
        self.assertEqual(reason, "admitted")
        self.assertEqual(admitted.get("schedule_status"),
                         direct_channel.CHANNEL_STATUS)

    def test_a_403_channel_is_not_published_as_playable(self):
        admitted, reason, _ = self._admit(direct_candidate(
            verification_status="failed", verified=False,
            verification_error="HTTP 403: Forbidden"))
        self.assertIsNone(admitted)
        self.assertEqual(reason, "unplayable")

    def test_a_dead_channel_is_not_held_the_way_a_fixture_is(self):
        # PROMPT 13 keeps a real fixture whose routes died inside its own
        # window. A channel has no window and no fixture identity to come back
        # to, so it is simply not published.
        card = direct_candidate(verification_status="failed", verified=False)
        admitted, reason, _ = self._admit(card)
        self.assertIsNone(admitted)
        self.assertEqual(reason, "unplayable")

    def test_a_dead_channel_is_never_attached_to_a_fixture(self):
        cards = merge_candidates(
            [fixture_candidate(url="https://willow.test/fixture.m3u8"),
             direct_candidate(verification_status="failed", verified=False,
                              publish_allowed=False)],
            settings_path=SETTINGS,
        )
        fixture = next(card for card in cards
                       if not direct_channel.is_direct_channel(card))
        urls = {str(row.get("url") or "") for row in fixture.get("backups") or []}
        self.assertNotIn(SHARED_URL, urls)
        self.assertNotIn("sm-tapmad-channels", list(fixture.get("source_ids") or []))

    def test_the_fixtures_own_routes_are_untouched(self):
        cards = merge_candidates(
            [fixture_candidate(id="f1", source_id="srhady-willow-event",
                               url="https://willow.test/a.m3u8"),
             fixture_candidate(id="f2", source_id="srhady-bingstream",
                               url="https://bing.test/b.m3u8"),
             direct_candidate(verification_status="failed", verified=False,
                              publish_allowed=False)],
            settings_path=SETTINGS,
        )
        fixture = next(card for card in cards
                       if not direct_channel.is_direct_channel(card))
        self.assertEqual(len(fixture.get("backups") or []), 1)
        self.assertEqual(sorted(fixture.get("source_ids") or []),
                         ["srhady-willow-event"])


class Prompt14PolicyStillHoldsTests(unittest.TestCase):
    """Requirement 9 and the sport policy, unchanged by the isolation."""

    def test_the_allowed_sports_are_still_cricket_and_football(self):
        settings = json.loads(Path(SETTINGS).read_text(encoding="utf-8"))
        self.assertEqual(
            sorted(direct_channel.allowed_sports(settings)),
            ["cricket", "football"],
        )

    def test_an_excluded_sport_is_still_reported_rather_than_deleted(self):
        rows = [
            {"id": "c-1", "name": "A vs B", "group": "Cricket",
             "url": "https://x.test/1.m3u8"},
            {"id": "t-1", "name": "C vs D", "group": "Tennis",
             "url": "https://x.test/2.m3u8"},
        ]
        published, diagnostics = direct_channel.classify(
            rows, source_id="sm-tapmad-channels",
            allowed=("cricket", "football"))
        self.assertEqual(len(published), 1)
        self.assertEqual(diagnostics["rows"], 2)
        self.assertEqual(diagnostics["excluded"], 1)
        self.assertEqual(diagnostics["excluded_rows"][0]["reason"],
                         direct_channel.EXCLUDED_BY_SPORT)

    def test_one_identity_with_two_urls_is_still_primary_plus_backups(self):
        rows = [
            {"id": "c-1", "name": "A vs B", "group": "Cricket",
             "url": "https://x.test/1.m3u8"},
            {"id": "c-1", "name": "A vs B", "group": "Cricket",
             "url": "https://x.test/2.m3u8"},
        ]
        published, diagnostics = direct_channel.classify(
            rows, source_id="s", allowed=("cricket",))
        self.assertEqual(len(published), 1)
        self.assertEqual(published[0]["backups"], ["https://x.test/2.m3u8"])
        self.assertEqual(diagnostics["backups_attached"], 1)

    def test_the_same_url_twice_under_one_identity_is_kept_once(self):
        rows = [
            {"id": "c-1", "name": "A vs B", "group": "Cricket",
             "url": "https://x.test/1.m3u8"},
            {"id": "c-1", "name": "A vs B", "group": "Cricket",
             "url": "https://x.test/1.m3u8"},
        ]
        published, diagnostics = direct_channel.classify(
            rows, source_id="s", allowed=("cricket",))
        self.assertEqual(published[0]["backups"], [])
        self.assertEqual(diagnostics["duplicate_urls_folded"], 1)

    def test_the_direct_feed_is_still_not_an_authority_witness(self):
        self.assertNotIn(
            "sm-tapmad-channels",
            schedule_resolver.DEFAULT_FIXTURE_AUTHORITY_SOURCES,
        )
        self.assertEqual(
            upstream_family.family_for("sm-tapmad-channels"),
            upstream_family.family_for("srhady-tapmad-bd"),
        )

    def test_the_direct_registry_is_not_the_fixture_authority_registry(self):
        registry = json.loads(
            (ROOT / "config" / "sources" / "today-match.json").read_text(
                encoding="utf-8"))
        ids = {str(row.get("id")) for row in registry["sources"]}
        self.assertNotIn("sm-tapmad-channels", ids)


class TheFixturePipelineIsUnaffectedTests(unittest.TestCase):
    """Requirement 12. This source is not critical to Today Match."""

    def test_the_direct_source_going_dark_changes_no_fixture(self):
        with_channel = merge_candidates(
            [fixture_candidate(url="https://willow.test/fixture.m3u8"),
             direct_candidate()], settings_path=SETTINGS)
        without = merge_candidates(
            [fixture_candidate(url="https://willow.test/fixture.m3u8")],
            settings_path=SETTINGS)
        left = next(card for card in with_channel
                    if not direct_channel.is_direct_channel(card))
        right = without[0]
        for field in ("id", "name", "fixture_id", "url", "source_ids",
                      "schedule_status", "start_time", "end_time"):
            self.assertEqual(left.get(field), right.get(field), field)

    def test_an_empty_direct_feed_parses_to_nothing_and_raises_nothing(self):
        self.assertEqual(
            event_adapters.adapt_tapmad_direct({}, "sm-tapmad-channels"), [])
        self.assertEqual(
            event_adapters.adapt_tapmad_direct({"response": []},
                                               "sm-tapmad-channels"), [])

    def test_the_real_two_row_feed_still_parses_to_two_direct_channels(self):
        payload = {"response": [
            {"id": "tapmad-15935",
             "name": "England vs Pakistan | Pakistan Tour of England 2026",
             "logo": "https://cdn.test/a.jpg", "group": "Cricket",
             "url": "https://saseries.akamaized.net/hls/live/2110097/"
                    "2353jkiL-tapmad/master.m3u8"},
            {"id": "tapmad-16707", "name": DIRECT_TITLE,
             "logo": "https://cdn.test/b.jpg", "group": "Cricket",
             "url": SHARED_URL},
        ]}
        records = event_adapters.adapt_tapmad_direct(
            payload, "sm-tapmad-channels")
        self.assertEqual(len(records), 2)
        for record in records:
            self.assertEqual(record.get("entry_type"),
                             direct_channel.ENTRY_TYPE)
            self.assertTrue(record.get("channel_identity"))
            self.assertFalse(str(record.get("start_time") or ""))
            self.assertFalse(str(record.get("status_raw") or ""))

    def test_the_two_real_rows_are_two_planner_groups(self):
        payload = {"response": [
            {"id": "tapmad-15935", "name": "England vs Pakistan | Tour 2026",
             "logo": "", "group": "Cricket",
             "url": "https://saseries.akamaized.net/hls/live/2110097/"
                    "2353jkiL-tapmad/master.m3u8"},
            {"id": "tapmad-16707", "name": DIRECT_TITLE, "logo": "",
             "group": "Cricket", "url": SHARED_URL},
        ]}
        records = event_adapters.adapt_tapmad_direct(
            payload, "sm-tapmad-channels")
        flat = event_adapters.flatten_records(
            records, {"id": "sm-tapmad-channels", "pipeline": "direct_channel"})
        keys = {planner._group_key(item) for item in flat}
        self.assertEqual(len(keys), 2)
        for key in keys:
            self.assertIn(direct_channel.DIRECT_DOMAIN, key)


class ThePublishedCardIdDoesNotCrossDomainsTests(unittest.TestCase):
    """Requirement 6 and 8. Two name-keyed memories, both domain-scoped now.

    `reuse_published_event_ids` and `load_previous_primary_keys` file a card
    under its name so a fixture keeps one identity from Upcoming to Today. A
    direct channel titled "A vs B" normalises to the SAME key: measured,
    `_event_identity_name` answers `belfast-wolves-vs-amsterdam-flames` for
    both titles. Unscoped, the channel would be handed the fixture's published
    card id - two cards answering to one identity, which is what
    `validate-pages` refuses as a duplicate event id.
    """

    def setUp(self):
        self.root = ROOT / "tests" / "_tmp_direct_isolation_data"
        self.root.mkdir(exist_ok=True)
        (self.root / "today-match.json").write_text(json.dumps({
            "items": [{"id": "belfast-wolves-vs-amsterdam-flames",
                       "name": FIXTURE_TITLE,
                       "primary_stream_key": "fixture-fingerprint"}]
        }), encoding="utf-8")
        (self.root / "upcoming.json").write_text(
            json.dumps({"items": []}), encoding="utf-8")

        def _clean():
            for path in self.root.glob("*.json"):
                path.unlink()
            self.root.rmdir()

        self.addCleanup(_clean)

    def test_a_channel_is_not_handed_a_fixtures_published_id(self):
        channel = dict(direct_candidate(), id="sm-tapmad-channels-1")
        reused = schedule_resolver.reuse_published_event_ids(
            [channel], data_root=str(self.root))
        self.assertEqual(reused, 0)
        self.assertEqual(channel["id"], "sm-tapmad-channels-1")
        self.assertNotIn("promoted_card", channel)

    def test_a_fixture_still_reuses_its_published_id(self):
        fixture = dict(fixture_candidate(), id="freshly-minted")
        reused = schedule_resolver.reuse_published_event_ids(
            [fixture], data_root=str(self.root))
        self.assertEqual(reused, 1)
        self.assertEqual(fixture["id"], "belfast-wolves-vs-amsterdam-flames")

    def test_the_two_together_keep_two_ids(self):
        fixture = dict(fixture_candidate(), id="freshly-minted")
        channel = dict(direct_candidate(), id="sm-tapmad-channels-1")
        schedule_resolver.reuse_published_event_ids(
            [fixture, channel], data_root=str(self.root))
        self.assertNotEqual(fixture["id"], channel["id"])
        self.assertEqual(fixture["id"], "belfast-wolves-vs-amsterdam-flames")

    def test_a_channel_reuses_its_own_id_across_a_retitle(self):
        (self.root / "today-match.json").write_text(json.dumps({
            "items": [{"id": "tapmad-channel-card",
                       "name": OLD_DIRECT_TITLE,
                       "entry_type": direct_channel.ENTRY_TYPE,
                       "channel_identity": "tapmad-16707"}]
        }), encoding="utf-8")
        channel = dict(direct_candidate(name=DIRECT_TITLE), id="freshly-minted")
        reused = schedule_resolver.reuse_published_event_ids(
            [channel], data_root=str(self.root))
        self.assertEqual(reused, 1)
        self.assertEqual(channel["id"], "tapmad-channel-card")

    def test_the_remembered_primary_is_filed_by_channel_not_by_title(self):
        from scanner.merger import load_previous_primary_keys
        remembered = load_previous_primary_keys(data_root=str(self.root))
        self.assertIn("belfast-wolves-vs-amsterdam-flames", remembered)
        self.assertNotIn("direct_channel:tapmad-16707", remembered)

    def test_a_channel_does_not_read_a_fixtures_remembered_primary(self):
        from scanner.merger import _primary_memory_key
        self.assertNotEqual(
            _primary_memory_key(direct_candidate()),
            _primary_memory_key(fixture_candidate()),
        )


class NoFixtureMetadataReachesAChannelTests(unittest.TestCase):
    """Requirement 2 and 5. Two more matchers that read the name.

    `_apply_streamed_enrichment` hangs a provider fixture's artwork, its embed
    backups and its `provider_event_id` on the card whose name matches, and
    `_authority_states` falls back to the name so a card whose id was reminted
    still finds its own live/ended verdict. A direct channel titled "A vs B"
    matches both, and it is neither that fixture nor a fixture at all.
    """

    def test_no_provider_fixture_enriches_a_channel(self):
        from scanner import events
        channel = dict(direct_candidate(), id="channel-card")
        provider = {
            "name": FIXTURE_TITLE,
            "provider_event_id": "streamed-123",
            "provider_artwork": ["https://cdn.test/poster.jpg"],
            "sport_type": "cricket",
            "competition": "European T20 Premier League 2026",
            "start_time": "2026-09-10T13:15:00+00:00",
        }
        stats = events._apply_streamed_enrichment([channel], [provider])
        self.assertEqual(stats["matched"], 0)
        self.assertNotIn("provider_event_id", channel)
        self.assertNotIn("provider_enriched", channel)

    def test_a_real_fixture_is_still_enriched(self):
        from scanner import events
        fixture = dict(fixture_candidate(), id="fixture-card")
        provider = {
            "name": FIXTURE_TITLE,
            "provider_event_id": "streamed-123",
            "provider_artwork": ["https://cdn.test/poster.jpg"],
            "sport_type": "cricket",
            "competition": "European T20 Premier League 2026",
            "start_time": "2026-09-10T13:15:00+00:00",
        }
        stats = events._apply_streamed_enrichment([fixture], [provider])
        self.assertEqual(stats["matched"], 1)
        self.assertEqual(fixture.get("provider_event_id"), "streamed-123")

    def test_a_channel_is_never_a_provider_fixture(self):
        from scanner import events
        fixture = dict(fixture_candidate(), id="fixture-card")
        channel_provider = dict(direct_candidate(), provider_event_id="x-1",
                                provider_artwork=["https://cdn.test/a.jpg"])
        stats = events._apply_streamed_enrichment([fixture], [channel_provider])
        self.assertEqual(stats["matched"], 0)
        self.assertNotIn("provider_event_id", fixture)

    def test_an_authority_verdict_does_not_reach_a_channel_by_name(self):
        from scanner import events
        candidate = dict(fixture_candidate(), id="fixture-card",
                         source_says_ended=True)
        channel = dict(direct_candidate(), id="channel-card")
        states = events._authority_states([candidate], [channel])
        self.assertEqual(states, {})

    def test_the_fixture_still_reads_its_own_verdict_by_name(self):
        from scanner import events
        candidate = dict(fixture_candidate(), id="reminted",
                         source_says_ended=True)
        previous = dict(fixture_candidate(), id="fixture-card")
        states = events._authority_states([candidate], [previous])
        self.assertEqual(states, {"fixture-card": False})

    def test_a_channel_supplies_no_authority_verdict(self):
        from scanner import events
        channel = dict(direct_candidate(), id="channel-card",
                       source_says_ended=True)
        previous = dict(fixture_candidate(), id="fixture-card")
        states = events._authority_states([channel], [previous])
        self.assertEqual(states, {})


if __name__ == "__main__":
    unittest.main()
