"""A match found by taking the clock out of the rule is a question, not a fact.

PROMPT 12. `authority_shadow.match_one` lifts the kickoff constraint to find a
candidate and labels the result `kickoff_lifted`. Everything downstream read
the entry by its `result` field alone - "matched" - so a lifted candidate
could set `authority_status`, end a card through FINISHED, assert play through
LIVE, and count as an upstream family, which is how HIGH confidence is
reached. HIGH is the one confidence that applies a terminal state with no
confirmation at all.

Measured over 10,230 shadow rows and 101 revisions:

    same_fixture               9725 readings, kickoff delta median 0, max 30 min
    kickoff_lifted              270 readings, delta 1 hour to 25 hours
    ambiguous_kickoff_lifted     26 readings
    ambiguous                     8 readings

    carried by a lifted reading: UPCOMING 247, LIVE 24, FINISHED 10

Those 304 readings resolve to 12 distinct (card, authority fixture) pairs, and
not one is a proven different meeting: a Test whose authority record is dated
from day one, two FA Cup ties our feed dates a day early, four youth fixtures
two to four hours out, one of our own duplicate cards. Every one is plausibly
the SAME fixture with a disagreeing clock.

That is the point rather than a reprieve. A lifted match may be right, and
being right by luck is not evidence - `Motagua vs Alianza FC` reached
FINISHED at HIGH confidence on two lifted readings, which would have ended a
card outright had the readings belonged to another meeting.

These tests pin the line and the things that must not move with it: a verified
match still carries authority, near misses are still recorded for the identity
work, and no fixture_id, route or tab changes.
"""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scanner import authority_shadow                       # noqa: E402
from scanner.authority_shadow import VERIFIED_MATCH, match_one  # noqa: E402
from scanner.authority_status import (                     # noqa: E402
    CONFLICT, HIGH, LOW, NONE, TRUSTED_MATCH, read, stamp,
    trusted_kickoff,
)

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)


def _entry(status="FINISHED", *, matched_by=VERIFIED_MATCH, family="ESPN",
           kickoff_minutes=0, event_id="401900363", **extra):
    entry = {
        "result": "matched",
        "matched_by": matched_by,
        "verified": matched_by == VERIFIED_MATCH,
        "event_id": event_id,
        "upstream_family": family,
        "status": status,
        "status_raw": status.title(),
        "name": "Alpha FC vs Beta FC",
        "competition": "Cup",
        "kickoff": (NOW + timedelta(minutes=kickoff_minutes)).isoformat(),
        "kickoff_delta_minutes": float(kickoff_minutes),
    }
    entry.update(extra)
    return entry


def _row(**authorities):
    return {"authorities": dict(authorities), "verdict": "COMPARED",
            "conflict_reasons": []}


def _card(**overrides):
    card = {
        "id": "alpha-vs-beta",
        "fixture_id": "provider:alpha-vs-beta|2026-09-10",
        "name": "Alpha FC vs Beta FC",
        "sport_type": "football",
        "start_time": (NOW - timedelta(hours=2)).isoformat(),
        "end_time": (NOW + timedelta(minutes=30)).isoformat(),
        "schedule_status": "LIVE_NOW",
        "status": "LIVE_NOW",
        "url": "https://example.test/alpha.m3u8",
        "backups": ["https://example.test/backup.m3u8"],
        "playback_id": "ctv_alpha",
        "source_ids": ["feed-one"],
        "channels": [{"name": "Alpha TV"}],
    }
    card.update(overrides)
    return card


def _stamp(row, card=None, **kwargs):
    card = card if card is not None else _card()
    action = stamp(card, read(row), now=NOW, confirmations_required=3,
                   post_match_grace_minutes=20, **kwargs)
    return card, action


# ------------------------------------------------------------------ matcher
class WhatTheMatcherCallsAMatch(unittest.TestCase):
    """The matcher is unchanged. These pin what it answers, because the whole
    rule below is built on that answer."""

    @staticmethod
    def _authority(name="Alpha FC vs Beta FC", kickoff_hours=0,
                   competition="Cup"):
        return {
            "name": name,
            "kickoff": (NOW + timedelta(hours=kickoff_hours)).isoformat(),
            "competition": competition,
            "authority_event_id": "e%d" % (kickoff_hours + 100),
            "upstream_family": "ESPN",
            "status": "UPCOMING",
        }

    def _card_at(self, hours=0):
        return {"name": "Alpha FC vs Beta FC",
                "start_time": (NOW + timedelta(hours=hours)).isoformat(),
                "competition": "Cup",
                "participants": ["Alpha FC", "Beta FC"]}

    def test_a_compatible_kickoff_is_a_verified_match(self):
        found, how = match_one(self._card_at(0), [self._authority()])
        self.assertIsNotNone(found)
        self.assertEqual(VERIFIED_MATCH, how)

    def test_a_one_day_delta_is_only_a_lifted_match(self):
        found, how = match_one(self._card_at(0),
                               [self._authority(kickoff_hours=24)])
        self.assertIsNotNone(found)
        self.assertEqual("kickoff_lifted", how)

    def test_a_four_hour_delta_is_only_a_lifted_match(self):
        _found, how = match_one(self._card_at(0),
                                [self._authority(kickoff_hours=4)])
        self.assertEqual("kickoff_lifted", how)

    def test_two_lifted_candidates_are_ambiguous(self):
        _found, how = match_one(self._card_at(0), [
            self._authority(kickoff_hours=24),
            self._authority(kickoff_hours=-24, name="Alpha FC vs Beta FC"),
        ])
        self.assertEqual("ambiguous_kickoff_lifted", how)

    def test_a_meeting_more_than_36_hours_away_is_not_even_lifted(self):
        _found, how = match_one(self._card_at(0),
                                [self._authority(kickoff_hours=48)])
        self.assertEqual("", how)


# ------------------------------------------------------------ what it is worth
class ALiftedReadingIsNotAnAuthority(unittest.TestCase):
    def test_it_carries_no_status(self):
        evidence = read(_row(**{"espn-header": _entry(
            matched_by="kickoff_lifted", kickoff_minutes=-1440)}))
        self.assertFalse(evidence.matched)
        self.assertEqual("", evidence.status)
        self.assertEqual(NONE, evidence.confidence)

    def test_it_is_not_an_upstream_witness(self):
        evidence = read(_row(**{"espn-header": _entry(
            matched_by="kickoff_lifted", kickoff_minutes=-1440)}))
        self.assertEqual([], evidence.families)

    def test_two_lifted_readings_do_not_reach_high(self):
        """The measured case: `Motagua vs Alianza FC`, FINISHED at HIGH on two
        lifted readings. HIGH applies a terminal state with no confirmation."""
        evidence = read(_row(**{
            "espn-header": _entry(matched_by="kickoff_lifted", family="ESPN",
                                  kickoff_minutes=-144),
            "livescore": _entry(matched_by="kickoff_lifted", family="LiveScore",
                                kickoff_minutes=-144),
        }))
        self.assertNotEqual(HIGH, evidence.confidence)
        self.assertEqual(NONE, evidence.confidence)

    def test_a_lifted_finished_cannot_end_the_card(self):
        card, action = _stamp(_row(**{
            "espn-header": _entry("FINISHED", matched_by="kickoff_lifted",
                                  kickoff_minutes=-1440),
            "livescore": _entry("FINISHED", matched_by="kickoff_lifted",
                                family="LiveScore", kickoff_minutes=-1440),
        }))
        self.assertEqual("", action)
        self.assertNotIn("authority_status", card)
        self.assertNotIn("authority_finished_seen_at", card)
        self.assertNotIn("authority_remove_after", card)

    def test_a_lifted_live_cannot_assert_play(self):
        card, action = _stamp(_row(**{
            "espn-header": _entry("LIVE", matched_by="kickoff_lifted",
                                  kickoff_minutes=1440)}))
        self.assertEqual("", action)
        self.assertNotIn("authority_status", card)

    def test_a_lifted_upcoming_cannot_reach_the_start_window_rule(self):
        """PROMPT 11's rule already asked for `same_fixture`. It still does,
        and now the evidence never gets that far either."""
        card = _card(start_time=(NOW + timedelta(minutes=40)).isoformat())
        evidence = read(_row(**{"espn-header": _entry(
            "UPCOMING", matched_by="kickoff_lifted", kickoff_minutes=40)}))
        self.assertIsNone(trusted_kickoff(evidence))
        stamp(card, evidence, now=NOW, confirmations_required=3,
              post_match_grace_minutes=20)
        self.assertEqual("LIVE_NOW", card["schedule_status"])

    def test_an_ambiguous_lifted_reading_is_not_an_authority(self):
        evidence = read(_row(**{"espn-header": _entry(
            matched_by="ambiguous_kickoff_lifted", kickoff_minutes=-1440)}))
        self.assertEqual(NONE, evidence.confidence)

    def test_a_strictly_ambiguous_reading_is_not_an_authority_either(self):
        """`ambiguous` means two candidates passed the narrow rule. Two
        answers is not an answer, whatever the clocks say."""
        evidence = read(_row(**{"espn-header": _entry(
            matched_by="ambiguous", kickoff_minutes=0)}))
        self.assertEqual(NONE, evidence.confidence)

    def test_a_row_with_no_verified_field_falls_back_on_matched_by(self):
        """Shadow rows written before this change carry no `verified`. Their
        worth is read from `matched_by`, not assumed."""
        entry = _entry(matched_by="kickoff_lifted", kickoff_minutes=-1440)
        entry.pop("verified")
        self.assertEqual(NONE, read(_row(**{"espn-header": entry})).confidence)

    def test_and_an_old_verified_row_still_counts(self):
        entry = _entry(matched_by=VERIFIED_MATCH)
        entry.pop("verified")
        self.assertEqual(LOW, read(_row(**{"espn-header": entry})).confidence)


# ------------------------------------------------------------------- rematches
class RematchesAndNearMeetings(unittest.TestCase):
    """Requirement 5, each shape asked separately. All of them arrive as a
    lifted reading, so all of them are refused the same way - which is the
    point of refusing the KIND of match rather than enumerating the shapes."""

    def _refused(self, status="UPCOMING", **entry_kwargs):
        card, action = _stamp(_row(**{
            "espn-header": _entry(status, matched_by="kickoff_lifted",
                                  **entry_kwargs)}))
        self.assertEqual("", action)
        self.assertNotIn("authority_status", card)
        return card

    def test_the_same_teams_on_the_next_day(self):
        self._refused(kickoff_minutes=1440)

    def test_the_same_teams_on_the_previous_day(self):
        self._refused(kickoff_minutes=-1440)

    def test_the_same_teams_later_in_the_tournament(self):
        self._refused(kickoff_minutes=1440, round="Quarter Final")

    def test_a_home_and_away_reversal(self):
        self._refused(kickoff_minutes=1440, name="Beta FC vs Alpha FC",
                      home="Beta FC", away="Alpha FC")

    def test_the_second_leg_of_a_two_leg_tie(self):
        self._refused(kickoff_minutes=1440, round="2nd Leg")

    def test_the_cup_meeting_against_the_league_meeting(self):
        self._refused(kickoff_minutes=1440, competition="Domestic Cup")

    def test_the_womens_fixture_against_the_mens(self):
        self._refused(kickoff_minutes=1440, gender="women")

    def test_the_youth_fixture_against_the_senior_one(self):
        self._refused(kickoff_minutes=1440, name="Alpha FC U21 vs Beta FC U21")

    def test_the_reserve_fixture_against_the_first_team(self):
        self._refused(kickoff_minutes=1440,
                      name="Alpha FC Reserves vs Beta FC Reserves")

    def test_a_different_round_of_the_same_competition(self):
        self._refused(kickoff_minutes=1440, round="Round 2")

    def test_an_old_finished_meeting_cannot_own_the_new_one(self):
        card = self._refused("FINISHED", kickoff_minutes=-1440)
        self.assertNotIn("authority_finished_seen_at", card)
        self.assertNotIn("authority_remove_after", card)


# --------------------------------------------------------------- provider ids
class AProviderIdIsNotAnIdentification(unittest.TestCase):
    def test_the_same_event_id_does_not_rescue_an_incompatible_kickoff(self):
        """Requirement 3. The id agreeing is not the fixture agreeing - and
        the audit that opened the identity backlog already found provider id
        variants that conflict."""
        card, action = _stamp(_row(**{
            "espn-header": _entry("FINISHED", matched_by="kickoff_lifted",
                                  event_id="401900363", kickoff_minutes=-1440),
            "livescore": _entry("FINISHED", matched_by="kickoff_lifted",
                                family="LiveScore", event_id="401900363",
                                kickoff_minutes=-1440),
        }))
        self.assertEqual("", action)
        self.assertNotIn("authority_status", card)

    def test_different_ids_with_a_compatible_kickoff_still_count(self):
        card, action = _stamp(_row(**{
            "espn-header": _entry("FINISHED", event_id="401900363"),
            "livescore": _entry("FINISHED", family="LiveScore",
                                event_id="1845619"),
        }))
        self.assertEqual("applied", action)
        self.assertEqual("FINISHED", card["authority_status"])


# ------------------------------------------------------- verified still works
class AVerifiedMatchStillCarriesAuthority(unittest.TestCase):
    def test_two_verified_families_agreeing_apply_at_once(self):
        card, action = _stamp(_row(**{
            "espn-header": _entry("FINISHED", family="ESPN"),
            "livescore": _entry("FINISHED", family="LiveScore"),
        }))
        self.assertEqual("applied", action)
        self.assertEqual(HIGH, card["authority_end_confidence"])

    def test_one_verified_family_still_has_to_repeat_itself(self):
        _card_out, action = _stamp(_row(**{
            "espn-header": _entry("FINISHED")}))
        self.assertEqual("pending_confirmation", action)

    def test_a_verified_live_still_asserts_play(self):
        card, action = _stamp(_row(**{"espn-header": _entry("LIVE")}))
        self.assertEqual("live", action)
        self.assertEqual("LIVE", card["authority_status"])

    def test_a_verified_kickoff_is_still_trusted(self):
        self.assertIsNotNone(trusted_kickoff(
            read(_row(**{"espn-header": _entry("UPCOMING")}))))

    def test_a_verified_reading_beside_a_lifted_one_still_counts_alone(self):
        """One witness, not two: the lifted one is set aside, so a single
        verified family is LOW and has to repeat itself."""
        evidence = read(_row(**{
            "espn-header": _entry("FINISHED", family="ESPN"),
            "livescore": _entry("FINISHED", family="LiveScore",
                                matched_by="kickoff_lifted",
                                kickoff_minutes=-1440),
        }))
        self.assertEqual(["ESPN"], evidence.families)
        self.assertEqual(LOW, evidence.confidence)

    def test_a_lifted_reading_cannot_manufacture_a_disagreement_either(self):
        """A near miss does not vote. One verified FINISHED beside a lifted
        LIVE is one authority saying FINISHED, not a conflict."""
        evidence = read(_row(**{
            "espn-header": _entry("FINISHED", family="ESPN"),
            "livescore": _entry("LIVE", family="LiveScore",
                                matched_by="kickoff_lifted",
                                kickoff_minutes=-1440),
        }))
        self.assertNotEqual(CONFLICT, evidence.confidence)
        self.assertEqual("FINISHED", evidence.status)

    def test_mirrors_of_one_family_are_still_one_witness(self):
        evidence = read(_row(**{
            "espn-header": _entry("FINISHED", family="ESPN"),
            "espn-core": _entry("FINISHED", family="ESPN"),
        }))
        self.assertEqual(["ESPN"], evidence.families)
        self.assertEqual(LOW, evidence.confidence)


# ------------------------------------------------------------- still recorded
class TheReadingsAreKeptForTheIdentityWork(unittest.TestCase):
    def test_a_near_miss_is_recorded_on_the_card(self):
        card, _action = _stamp(_row(**{
            "espn-header": _entry("FINISHED", matched_by="kickoff_lifted",
                                  kickoff_minutes=-1440)}))
        self.assertEqual(1, len(card["authority_near_misses"]))
        recorded = card["authority_near_misses"][0]
        self.assertEqual("kickoff_lifted", recorded["matched_by"])
        self.assertEqual(-1440.0, recorded["kickoff_delta_minutes"])
        self.assertEqual("FINISHED", recorded["status"])

    def test_it_is_recorded_even_when_nothing_else_matched(self):
        """The card whose near misses matter most is the one no authority
        identified at all."""
        card, action = _stamp(_row(**{
            "espn-header": _entry(matched_by="kickoff_lifted",
                                  kickoff_minutes=1440),
            "livescore": {"result": "unmatched"},
        }))
        self.assertEqual("", action)
        self.assertIn("authority_near_misses", card)

    def test_a_stale_record_is_cleared_rather_than_accumulated(self):
        card = _card()
        card["authority_near_misses"] = [{"matched_by": "kickoff_lifted"}]
        stamp(card, read(_row(**{"espn-header": _entry("FINISHED")})),
              now=NOW, confirmations_required=3, post_match_grace_minutes=20)
        self.assertNotIn("authority_near_misses", card)

    def test_the_shadow_row_reports_the_near_miss_families_apart(self):
        card = {"name": "Alpha FC vs Beta FC", "id": "alpha-vs-beta",
                "start_time": NOW.isoformat(), "competition": "Cup",
                "participants": ["Alpha FC", "Beta FC"]}
        rows = {"espn-header": [{
            "name": "Alpha FC vs Beta FC",
            "kickoff": (NOW + timedelta(hours=24)).isoformat(),
            "competition": "Cup", "authority_event_id": "e1",
            "upstream_family": "ESPN", "status": "FINISHED",
        }]}
        row = authority_shadow.compare(card, rows, {})
        self.assertEqual([], row["authority_upstreams"])
        self.assertEqual(0, row["independent_authority_count"])
        self.assertEqual(["ESPN"], row["near_miss_upstreams"])
        self.assertEqual(1, row["near_miss_count"])
        self.assertFalse(row["authorities"]["espn-header"]["verified"])
        self.assertEqual("matched", row["authorities"]["espn-header"]["result"])


# ----------------------------------------------------------------- untouched
class NothingElseMoves(unittest.TestCase):
    def test_the_fixture_id_is_never_replaced(self):
        card, _action = _stamp(_row(**{
            "espn-header": _entry("FINISHED", matched_by="kickoff_lifted",
                                  kickoff_minutes=-1440)}))
        self.assertEqual("provider:alpha-vs-beta|2026-09-10",
                         card["fixture_id"])
        self.assertNotIn("previous_event_id", card)

    def test_the_routes_and_ids_survive_a_refusal(self):
        before = _card()
        card, _action = _stamp(_row(**{
            "espn-header": _entry("FINISHED", matched_by="kickoff_lifted",
                                  kickoff_minutes=-1440)}), card=_card())
        for field in ("url", "backups", "channels", "source_ids",
                      "playback_id", "id", "name", "start_time", "end_time",
                      "schedule_status", "status"):
            self.assertEqual(before[field], card[field], field)

    def test_the_clock_is_never_taken_from_a_near_miss(self):
        before = _card()
        card, _action = _stamp(_row(**{
            "espn-header": _entry("UPCOMING", matched_by="kickoff_lifted",
                                  kickoff_minutes=1440)}), card=_card())
        self.assertEqual(before["start_time"], card["start_time"])

    def test_a_refusal_writes_no_end_bookkeeping(self):
        card, _action = _stamp(_row(**{
            "espn-header": _entry("FINISHED", matched_by="kickoff_lifted",
                                  kickoff_minutes=-1440)}))
        for field in ("authority_end_status", "authority_end_confidence",
                      "authority_event_ids", "authority_finished_seen_at",
                      "authority_remove_after"):
            self.assertNotIn(field, card, field)

    def test_the_two_names_for_the_trusted_match_are_one_string(self):
        self.assertEqual(VERIFIED_MATCH, TRUSTED_MATCH)


if __name__ == "__main__":
    unittest.main()
