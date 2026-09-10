"""A playlist calling an entry LIVE is a statement about a listing.

PROMPT 11. `authority_status.stamp` said of an UPCOMING authority that "a
future kickoff against a source calling it live is a conflict for the report,
and the bounded start-window rules already own that decision". Those rules did
not exist, so an authority saying UPCOMING was recorded and nothing read it.

What the shadow history actually holds, over 9,844 matched authority readings
and 437 publishes:

    card says LIVE, authority says UPCOMING   49 sightings, 16 fixtures
      minutes to the authority's own kickoff  min -122, median -45, max +10
    card on Today earlier than T-25            0
    card live more than 20 min before its
      own kickoff                              0

So the dominant case is a STALE authority UPCOMING, a median of 45 minutes
after the authority's own kickoff - the opposite of a premature live - and the
rule has to be powerless there. Hence "our own kickoff is still future" as a
condition, which no stale reading can satisfy.

And the kickoff evidence, which decides what may be trusted at all:

    same_fixture     9548 readings, delta median 0, max 30 min
    kickoff_lifted    270 readings, delta min 60, median 1440, max 1440

A whole day apart is two different meetings of the same two clubs, not a
corrected kickoff. Only `same_fixture` is trusted.

These tests pin the narrowness as hard as the rule: the tab, the routes, the
ids and the fixture_id are untouched, and only the status the card CLAIMS is
corrected - to `STARTING_SOON`, which already exists everywhere.
"""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scanner import authority_status                       # noqa: E402
from scanner.authority_status import (                     # noqa: E402
    START_WINDOW_STATUS, TRUSTED_MATCH, read, stamp, start_window,
    trusted_kickoff,
)
from scanner.event_lifecycle import (                      # noqa: E402
    DEFAULT_TODAY_ROUTING_MINUTES, ROUTE_LIVE_STATUSES, event_destination,
)

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)
T25 = 25


def _card(minutes_to_kickoff=40, status="LIVE_NOW", **overrides):
    """A card a source is calling live, with a kickoff still ahead of it."""
    kickoff = NOW + timedelta(minutes=minutes_to_kickoff)
    card = {
        "id": "alpha-vs-beta",
        "fixture_id": "provider:alpha-vs-beta|2026-09-10",
        "name": "Alpha FC vs Beta FC",
        "sport_type": "football",
        "start_time": kickoff.isoformat(),
        "start_at": kickoff.isoformat(),
        "end_time": (kickoff + timedelta(minutes=150)).isoformat(),
        "end_time_source": "sport",
        "schedule_status": status,
        "status": status,
        "schedule_verified": True,
        "time_verification": "provider_feed",
        "url": "https://example.test/alpha.m3u8",
        "backups": ["https://example.test/alpha-backup.m3u8"],
        "playback_id": "ctv_alpha",
        "source_ids": ["feed-one", "feed-two"],
        "channels": [{"name": "Alpha TV", "url": "https://example.test/a.m3u8"}],
    }
    card.update(overrides)
    return card


def _entry(status="UPCOMING", *, family="ESPN", matched_by=TRUSTED_MATCH,
           kickoff_minutes=40, event_id="401900363"):
    kickoff = NOW + timedelta(minutes=kickoff_minutes)
    return {
        "result": "matched",
        "matched_by": matched_by,
        "event_id": event_id,
        "upstream_family": family,
        "status": status,
        "status_raw": status.title(),
        "kickoff": kickoff.isoformat(),
        "kickoff_delta_minutes": 0.0,
    }


def _row(**authorities):
    return {"authorities": dict(authorities), "verdict": "COMPARED",
            "conflict_reasons": []}


def _evidence(**authorities):
    return read(_row(**authorities))


class ASourceLiveBeforeAnybodysKickoff(unittest.TestCase):
    def test_far_before_t25_the_claim_is_corrected(self):
        card = _card(minutes_to_kickoff=180)
        acted = start_window(card, _evidence(**{
            "espn-header": _entry(kickoff_minutes=180)}), now=NOW)
        self.assertEqual(START_WINDOW_STATUS, acted)
        self.assertEqual(START_WINDOW_STATUS, card["schedule_status"])
        self.assertEqual("LIVE_NOW", card["start_window_claimed"])

    def test_just_outside_the_t25_boundary_it_is_corrected(self):
        card = _card(minutes_to_kickoff=T25 + 1)
        self.assertEqual(START_WINDOW_STATUS, start_window(
            card, _evidence(**{"espn-header": _entry(kickoff_minutes=T25 + 1)}),
            now=NOW))

    def test_inside_t25_it_is_still_corrected_but_stays_on_today(self):
        """FINAL_2 owns the tab and this rule does not touch it: at T-25 the
        card belongs on Today Match whether or not it has started."""
        card = _card(minutes_to_kickoff=T25 - 1)
        self.assertEqual(START_WINDOW_STATUS, start_window(
            card, _evidence(**{"espn-header": _entry(kickoff_minutes=T25 - 1)}),
            now=NOW))
        self.assertEqual("today_match", event_destination(
            card, NOW, DEFAULT_TODAY_ROUTING_MINUTES))

    def test_at_the_kickoff_minute_nothing_is_corrected(self):
        card = _card(minutes_to_kickoff=0)
        self.assertEqual("", start_window(
            card, _evidence(**{"espn-header": _entry(kickoff_minutes=0)}),
            now=NOW))
        self.assertEqual("LIVE_NOW", card["schedule_status"])

    def test_after_kickoff_nothing_is_corrected(self):
        card = _card(minutes_to_kickoff=-30)
        self.assertEqual("", start_window(
            card, _evidence(**{"espn-header": _entry(kickoff_minutes=-30)}),
            now=NOW))
        self.assertEqual("LIVE_NOW", card["schedule_status"])


class AStaleAuthorityUpcomingIsPowerless(unittest.TestCase):
    """The measured case: 49 of 49 sightings were a median of 45 minutes AFTER
    the authority's own kickoff. A rule that acted on those would be holding a
    started match off Today on a reading that had simply not been refreshed."""

    def test_an_authority_upcoming_past_its_own_kickoff_changes_nothing(self):
        card = _card(minutes_to_kickoff=-45)
        self.assertEqual("", start_window(
            card, _evidence(**{"espn-header": _entry(kickoff_minutes=-45)}),
            now=NOW))
        self.assertIn(card["schedule_status"], ROUTE_LIVE_STATUSES)

    def test_the_widest_measured_staleness_changes_nothing(self):
        card = _card(minutes_to_kickoff=-122)
        self.assertEqual("", start_window(
            card, _evidence(**{"espn-header": _entry(kickoff_minutes=-122)}),
            now=NOW))

    def test_our_clock_alone_can_veto_the_correction(self):
        """Our kickoff has passed, the authority still says it has not. The
        card has started as far as this system knows, so it keeps its claim."""
        card = _card(minutes_to_kickoff=-5)
        self.assertEqual("", start_window(
            card, _evidence(**{"espn-header": _entry(kickoff_minutes=120)}),
            now=NOW))


class OnlyAKickoffWeCanTrust(unittest.TestCase):
    def test_a_same_fixture_match_is_trusted(self):
        self.assertIsNotNone(trusted_kickoff(
            _evidence(**{"espn-header": _entry()})))

    def test_a_lifted_kickoff_is_not_trusted(self):
        """270 readings, a day out at the median. Two meetings, not one."""
        evidence = _evidence(**{"espn-header": _entry(
            matched_by="kickoff_lifted", kickoff_minutes=1440)})
        self.assertIsNone(trusted_kickoff(evidence))

    def test_and_so_it_corrects_nothing(self):
        card = _card(minutes_to_kickoff=40)
        self.assertEqual("", start_window(card, _evidence(**{
            "espn-header": _entry(matched_by="kickoff_lifted",
                                  kickoff_minutes=1440)}), now=NOW))
        self.assertEqual("LIVE_NOW", card["schedule_status"])

    def test_a_lifted_match_beside_a_verified_one_neither_helps_nor_hurts(self):
        """PROMPT 12 settled what a lifted reading is worth: nothing.

        This test read "one lifted match spoils the pair" and asserted no
        trusted kickoff at all. That threw away a verified identification
        because a second source was confused - and a near miss does not vote,
        in either direction. The verified reading now stands alone: it is the
        trusted kickoff, and it is one witness rather than two.
        """
        evidence = _evidence(**{
            "espn-header": _entry(family="ESPN"),
            "livescore": _entry(family="LiveScore",
                                matched_by="kickoff_lifted",
                                kickoff_minutes=1440),
        })
        self.assertEqual(NOW + timedelta(minutes=40),
                         trusted_kickoff(evidence))
        self.assertEqual(["ESPN"], evidence.families)
        self.assertEqual(1, len(evidence.near_misses))

    def test_a_lifted_match_on_its_own_is_no_kickoff_at_all(self):
        evidence = _evidence(**{
            "livescore": _entry(family="LiveScore",
                                matched_by="kickoff_lifted",
                                kickoff_minutes=1440)})
        self.assertIsNone(trusted_kickoff(evidence))

    def test_an_unmatched_authority_is_not_a_kickoff(self):
        evidence = read(_row(**{"espn-header": {"result": "unmatched"}}))
        self.assertIsNone(trusted_kickoff(evidence))

    def test_the_earliest_trusted_kickoff_is_the_one_used(self):
        """A fixture is never held as not-started for longer than any
        authority claims."""
        evidence = _evidence(**{
            "espn-header": _entry(family="ESPN", kickoff_minutes=40),
            "livescore": _entry(family="LiveScore", kickoff_minutes=10),
        })
        self.assertEqual(NOW + timedelta(minutes=10), trusted_kickoff(evidence))

    def test_a_kickoff_the_authority_did_not_state_is_not_invented(self):
        entry = _entry()
        entry.pop("kickoff")
        self.assertIsNone(trusted_kickoff(_evidence(**{"espn-header": entry})))


class TheProviderKickoffIsNotOverwritten(unittest.TestCase):
    """No fixture_id migration, and no clock migration either. The rule reads
    both kickoffs and requires both to be future; it replaces neither."""

    def test_a_provider_kickoff_earlier_than_the_authority_is_kept(self):
        card = _card(minutes_to_kickoff=10)
        before = card["start_time"]
        start_window(card, _evidence(**{
            "espn-header": _entry(kickoff_minutes=40)}), now=NOW)
        self.assertEqual(before, card["start_time"])

    def test_a_provider_kickoff_later_than_the_authority_is_kept(self):
        card = _card(minutes_to_kickoff=40)
        before = card["start_time"]
        start_window(card, _evidence(**{
            "espn-header": _entry(kickoff_minutes=10)}), now=NOW)
        self.assertEqual(before, card["start_time"])

    def test_the_fixture_id_is_never_replaced_by_an_authority_id(self):
        card = _card(minutes_to_kickoff=40)
        stamp(card, _evidence(**{"espn-header": _entry()}), now=NOW,
              confirmations_required=3, post_match_grace_minutes=20)
        self.assertEqual("provider:alpha-vs-beta|2026-09-10",
                         card["fixture_id"])
        self.assertNotIn("previous_event_id", card)

    def test_only_one_clock_being_future_is_not_enough(self):
        """Provider says 12:00, authority says 15:00, and it is 14:34. Our own
        kickoff has passed, so the card keeps its claim - the authority's later
        kickoff does not reach back and un-start it."""
        card = _card(minutes_to_kickoff=-154)
        self.assertEqual("", start_window(card, _evidence(**{
            "espn-header": _entry(kickoff_minutes=26)}), now=NOW))


class AnAuthoritySayingSomethingElse(unittest.TestCase):
    def test_an_authority_saying_live_never_reaches_the_rule(self):
        card = _card(minutes_to_kickoff=40)
        action = stamp(card, _evidence(**{"espn-header": _entry("LIVE")}),
                       now=NOW, confirmations_required=3,
                       post_match_grace_minutes=20)
        self.assertEqual("live", action)
        self.assertEqual("LIVE", card["authority_status"])
        self.assertEqual("LIVE_NOW", card["schedule_status"])

    def test_an_authority_saying_live_against_a_source_upcoming(self):
        """The reverse conflict. It is recorded, and it does not promote."""
        card = _card(minutes_to_kickoff=40, status="UPCOMING")
        stamp(card, _evidence(**{"espn-header": _entry("LIVE")}), now=NOW,
              confirmations_required=3, post_match_grace_minutes=20)
        self.assertEqual("UPCOMING", card["schedule_status"])
        self.assertEqual("LIVE", card["authority_status"])

    def test_authorities_disagreeing_correct_nothing(self):
        card = _card(minutes_to_kickoff=40)
        action = stamp(card, _evidence(**{
            "espn-header": _entry("UPCOMING", family="ESPN"),
            "livescore": _entry("LIVE", family="LiveScore"),
        }), now=NOW, confirmations_required=3, post_match_grace_minutes=20)
        self.assertEqual("conflict", action)
        self.assertEqual("LIVE_NOW", card["schedule_status"])

    def test_no_authority_at_all_corrects_nothing(self):
        card = _card(minutes_to_kickoff=40)
        action = stamp(card, read(_row(**{
            "espn-header": {"result": "unmatched"},
            "livescore": {"result": "unmatched"},
        })), now=NOW, confirmations_required=3, post_match_grace_minutes=20)
        self.assertEqual("", action)
        self.assertEqual("LIVE_NOW", card["schedule_status"])

    def test_an_unknown_status_corrects_nothing(self):
        card = _card(minutes_to_kickoff=40)
        stamp(card, _evidence(**{"espn-header": _entry("UNKNOWN")}), now=NOW,
              confirmations_required=3, post_match_grace_minutes=20)
        self.assertEqual("LIVE_NOW", card["schedule_status"])


class MirrorsOfOneUpstreamAreOneWitness(unittest.TestCase):
    def test_two_mirrors_of_one_family_agree_as_one(self):
        evidence = _evidence(**{
            "espn-header": _entry(family="ESPN"),
            "espn-core": _entry(family="ESPN", event_id="401900363"),
        })
        self.assertEqual(["ESPN"], evidence.families)

    def test_and_they_still_supply_a_trusted_kickoff(self):
        evidence = _evidence(**{
            "espn-header": _entry(family="ESPN"),
            "espn-core": _entry(family="ESPN"),
        })
        self.assertIsNotNone(trusted_kickoff(evidence))


class NothingAboutTheCardIsLost(unittest.TestCase):
    """Requirement 7. A status correction is not a takedown."""

    def setUp(self):
        self.before = _card(minutes_to_kickoff=40)
        self.card = _card(minutes_to_kickoff=40)
        start_window(self.card, _evidence(**{"espn-header": _entry()}),
                     now=NOW)

    def test_the_primary_route_survives(self):
        self.assertEqual(self.before["url"], self.card["url"])

    def test_the_backups_survive(self):
        self.assertEqual(self.before["backups"], self.card["backups"])

    def test_the_channel_buttons_survive(self):
        self.assertEqual(self.before["channels"], self.card["channels"])

    def test_the_source_ids_survive(self):
        self.assertEqual(self.before["source_ids"], self.card["source_ids"])

    def test_the_playback_id_survives(self):
        self.assertEqual(self.before["playback_id"], self.card["playback_id"])

    def test_the_identity_and_the_clock_survive(self):
        for field in ("id", "fixture_id", "name", "start_time", "end_time",
                      "end_time_source", "sport_type"):
            self.assertEqual(self.before[field], self.card[field], field)

    def test_exactly_four_fields_changed(self):
        changed = {key for key in set(self.before) | set(self.card)
                   if self.before.get(key) != self.card.get(key)}
        self.assertEqual(
            {"schedule_status", "status", "start_window_claimed",
             "authority_start_conflict"}, changed)


class TheTabIsNotThisRulesBusiness(unittest.TestCase):
    def test_a_corrected_card_inside_t25_stays_on_today(self):
        card = _card(minutes_to_kickoff=20)
        start_window(card, _evidence(**{
            "espn-header": _entry(kickoff_minutes=20)}), now=NOW)
        self.assertEqual("today_match", event_destination(
            card, NOW, DEFAULT_TODAY_ROUTING_MINUTES))

    def test_a_corrected_card_outside_t25_is_upcoming(self):
        card = _card(minutes_to_kickoff=120)
        start_window(card, _evidence(**{
            "espn-header": _entry(kickoff_minutes=120)}), now=NOW)
        self.assertEqual("upcoming", event_destination(
            card, NOW, DEFAULT_TODAY_ROUTING_MINUTES))

    def test_a_card_is_never_in_both_tabs(self):
        for minutes in (T25 - 1, T25, T25 + 1, 60, 120):
            card = _card(minutes_to_kickoff=minutes)
            start_window(card, _evidence(**{
                "espn-header": _entry(kickoff_minutes=minutes)}), now=NOW)
            self.assertIn(event_destination(
                card, NOW, DEFAULT_TODAY_ROUTING_MINUTES),
                ("today_match", "upcoming"), minutes)

    def test_a_metadata_only_card_still_reaches_today_at_t25(self):
        """Requirement: at T-25 a fixture may enter Today with no playable
        link at all. Nothing here changes that."""
        card = _card(minutes_to_kickoff=20, status="LINK_UPDATING",
                     metadata_only=True)
        for field in ("url", "backups", "playback_id"):
            card.pop(field, None)
        self.assertEqual("", start_window(card, _evidence(**{
            "espn-header": _entry(kickoff_minutes=20)}), now=NOW))
        self.assertEqual("today_match", event_destination(
            card, NOW, DEFAULT_TODAY_ROUTING_MINUTES))


class TheRunCanBeChecked(unittest.TestCase):
    def test_the_report_counts_the_correction_apart_from_a_recording(self):
        cards = [_card(minutes_to_kickoff=40)]
        rows, stats = authority_status.apply(
            cards, {"espn-header": []}, {},
            now=NOW, confirmations_required=3, post_match_grace_minutes=20)
        self.assertIn("start_window", stats)
        self.assertIn("start_window_fixtures", stats)
        self.assertEqual(len(rows), 1)

    def test_the_conflict_is_written_where_a_person_can_read_it(self):
        card = _card(minutes_to_kickoff=40)
        start_window(card, _evidence(**{"espn-header": _entry()}), now=NOW)
        self.assertIn("Alpha", card["name"])
        self.assertIn("LIVE_NOW", card["authority_start_conflict"])
        self.assertIn("ESPN", card["authority_start_conflict"])

    def test_a_card_that_already_says_starting_soon_is_left_alone(self):
        card = _card(minutes_to_kickoff=40, status=START_WINDOW_STATUS)
        self.assertEqual("", start_window(card, _evidence(**{
            "espn-header": _entry()}), now=NOW))
        self.assertNotIn("start_window_claimed", card)


if __name__ == "__main__":
    unittest.main()
