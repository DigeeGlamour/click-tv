"""What a fixture authority may do to a card, and what it may only record.

The case this exists for was measured, not argued. On 2026-09-07 at 06:29Z
`Toluca vs Monterrey` was published `LIVE_NOW` / `lifecycle_state: LIVE`, three
hours after a 90-minute game, held there by `lifecycle_reason: "still live:
primary_playable, backup_playable"` - a link probe. ESPN (401914323,
STATUS_FULL_TIME, state `post`) and LiveScore (1870766, FT, Esid 6) both said
FINISHED, and nothing read them.

Nothing here hard-codes that fixture. The rule is general and the same 84 cards
went through it; one of them moved.

Two things every test in this file is really about:

  * witnesses are upstream families, never feeds, so the seven FanCode mirrors
    cannot vote seven times;
  * silence is not an end. An unmatched fixture, an unavailable authority, an
    UNKNOWN status and a disagreement between authorities each leave the card
    exactly where the existing rules left it.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scanner import (  # noqa: E402
    authority_status,
    event_archive,
    event_lifecycle,
    fixture_authority,
)
from scanner.event_lifecycle import (  # noqa: E402
    END_PENDING,
    ENDED,
    LIVE,
    LifecycleSignals,
    decide,
    has_strong_end_signal,
)
from scanner.events import _admit_to_today, _is_playable  # noqa: E402

NOW = dt.datetime(2026, 9, 7, 4, 30, tzinfo=dt.timezone.utc)
GRACE = 20
CONFIRMATIONS = 3


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def card(**kwargs):
    """A Today Match card shaped like the real one, three hours past kickoff."""
    base = {
        "id": "toluca-vs-monterrey",
        "fixture_id": "provider:toluca-vs-monterrey|leagues cup|2026-09-07",
        "name": "Toluca vs Monterrey",
        "competition": "Leagues Cup",
        "start_time": "2026-09-07T01:15:00+00:00",
        "end_time": "2026-09-07T05:15:00+00:00",
        "end_time_source": "assumed",
        "schedule_status": "LIVE_NOW",
        "status": "LIVE_NOW",
        "lifecycle_state": "LIVE",
        "schedule_verified": True,
        "sport_type": "football",
        "source_ids": ["srhady-bingstream", "srhady-axsports-live"],
        "url": "https://example.invalid/live.m3u8",
        "verified": True,
        "verification_status": "verified_global",
        "publish_allowed": True,
        "playback_id": "ctv_" + "a" * 32,
    }
    base.update(kwargs)
    return base


def entry(name="livescore", family="LiveScore", status=fixture_authority.FINISHED,
          raw="FT", code="6", event_id="1870766", **kwargs):
    row = {
        "result": "matched",
        "matched_by": "same_fixture",
        "event_id": event_id,
        "upstream_family": family,
        "name": "Toluca vs Monterrey",
        "status": status,
        "status_raw": raw,
        "status_code": code,
        "sport": "football",
        "kickoff": "2026-09-07T01:15:00+00:00",
        "kickoff_delta_minutes": 0.0,
    }
    row.update(kwargs)
    return row


def shadow_row(authorities, verdict="VERIFIED", reasons=None, **kwargs):
    row = {
        "fixture_id": "provider:toluca-vs-monterrey|leagues cup|2026-09-07",
        "card_id": "toluca-vs-monterrey",
        "name": "Toluca vs Monterrey",
        "authorities": authorities,
        "verdict": verdict,
        "conflict_reasons": reasons or [],
    }
    row.update(kwargs)
    return row


BOTH_FINISHED = {
    "livescore": entry(),
    "espn-header": entry("espn-header", "ESPN", event_id="401914323",
                         raw="FT", code="STATUS_FULL_TIME"),
}


def stamp(subject, authorities, **kwargs):
    evidence = authority_status.read(shadow_row(authorities, **kwargs))
    action = authority_status.stamp(
        subject, evidence, now=NOW, confirmations_required=CONFIRMATIONS,
        post_match_grace_minutes=GRACE)
    return action, evidence


def verdict_for(subject, *, now=NOW, playing=False, probe=True):
    """What the carried-forward path would decide, with the real function."""
    signals = LifecycleSignals(
        authority_live=event_lifecycle.authority_says_live(subject),
        strong_end=has_strong_end_signal(subject),
        primary_playable=probe,
        backup_playable=probe,
        currently_playing=playing,
        estimate_passed=False,
        consecutive_non_live_scans=0,
    )
    return decide(subject, signals, now=now,
                  confirmations_required=CONFIRMATIONS,
                  post_match_grace_minutes=GRACE)


# =======================================================================
# agreement and confidence
# =======================================================================

class AgreementTests(unittest.TestCase):
    def test_two_independent_authorities_finished_reaches_end_pending(self):
        subject = card()
        action, evidence = stamp(subject, BOTH_FINISHED)
        self.assertEqual(action, "applied")
        self.assertEqual(evidence.confidence, authority_status.HIGH)
        self.assertEqual(evidence.families, ["ESPN", "LiveScore"])
        self.assertEqual(subject["authority_status"], "FINISHED")
        self.assertEqual(verdict_for(subject).state, END_PENDING)

    def test_a_probe_calling_the_link_alive_no_longer_holds_it(self):
        """The exact hold that kept it: "still live: primary_playable"."""
        before = verdict_for(card(), probe=True)
        self.assertEqual(before.state, LIVE)
        subject = card()
        stamp(subject, BOTH_FINISHED)
        self.assertEqual(verdict_for(subject, probe=True).state, END_PENDING)

    def test_one_authority_alone_is_evidence_and_not_yet_proof(self):
        subject = card()
        action, evidence = stamp(subject, {"livescore": entry()})
        self.assertEqual(evidence.confidence, authority_status.LOW)
        self.assertEqual(action, "pending_confirmation")
        self.assertNotIn("authority_status", subject)
        self.assertEqual(verdict_for(subject).state, LIVE)

    def test_one_authority_repeating_itself_earns_the_end(self):
        subject = card()
        for expected in ("pending_confirmation", "pending_confirmation",
                         "applied"):
            action, _evidence = stamp(subject, {"livescore": entry()})
            self.assertEqual(action, expected, subject.get(
                "authority_finished_confirmations"))
        self.assertEqual(subject["authority_finished_confirmations"],
                         CONFIRMATIONS)
        self.assertEqual(verdict_for(subject).state, END_PENDING)

    def test_mirrors_of_one_upstream_are_one_witness(self):
        """Seven FanCode feeds agreeing is one upstream saying it once. This is
        the same rule with two authority rows sharing a family."""
        subject = card()
        action, evidence = stamp(subject, {
            "livescore": entry(),
            "livescore-mirror": entry("livescore-mirror", "LiveScore",
                                      event_id="1870766"),
        })
        self.assertEqual(evidence.families, ["LiveScore"])
        self.assertEqual(evidence.confidence, authority_status.LOW)
        self.assertEqual(action, "pending_confirmation")

    def test_the_witness_count_is_recorded_on_the_card(self):
        subject = card()
        stamp(subject, BOTH_FINISHED)
        self.assertEqual(subject["authority_independent_count"], 2)
        self.assertEqual(subject["authority_end_families"],
                         ["ESPN", "LiveScore"])


class DisagreementTests(unittest.TestCase):
    def test_authorities_disagreeing_ends_nothing(self):
        subject = card()
        action, evidence = stamp(subject, {
            "livescore": entry(status=fixture_authority.FINISHED),
            "espn-header": entry("espn-header", "ESPN",
                                 status=fixture_authority.LIVE, raw="2H",
                                 code="STATUS_SECOND_HALF"),
        })
        self.assertEqual(evidence.confidence, authority_status.CONFLICT)
        self.assertEqual(action, "conflict")
        self.assertNotIn("authority_status", subject)
        self.assertEqual(verdict_for(subject).state, LIVE)

    def test_a_disagreement_is_written_down(self):
        subject = card()
        stamp(subject, {
            "livescore": entry(status=fixture_authority.FINISHED),
            "espn-header": entry("espn-header", "ESPN",
                                 status=fixture_authority.LIVE),
        })
        self.assertIn("independent authorities disagree",
                      subject["authority_end_conflict"])
        self.assertEqual(subject["authority_end_confidence"],
                         authority_status.CONFLICT)


# =======================================================================
# silence
# =======================================================================

class SilenceTests(unittest.TestCase):
    def test_an_unmatched_fixture_is_left_exactly_as_it_was(self):
        """41 of the first 84 were UNVERIFIED, most of them domestic cricket
        leagues neither authority carries. UNVERIFIED is about our evidence."""
        subject = card()
        before = json.dumps(subject, sort_keys=True)
        action, _evidence = stamp(subject, {"livescore": {"result": "unmatched"},
                                            "espn-header": {"result": "unmatched"}},
                                  verdict="UNVERIFIED")
        self.assertEqual(action, "")
        self.assertEqual(json.dumps(subject, sort_keys=True), before)
        self.assertEqual(verdict_for(subject).state, LIVE)

    def test_an_unavailable_authority_is_left_exactly_as_it_was(self):
        subject = card()
        before = json.dumps(subject, sort_keys=True)
        action, _evidence = stamp(subject, {
            "livescore": {"result": fixture_authority.INCONCLUSIVE},
            "espn-header": {"result": fixture_authority.INCONCLUSIVE}})
        self.assertEqual(action, "")
        self.assertEqual(json.dumps(subject, sort_keys=True), before)

    def test_an_unknown_status_does_nothing(self):
        """LiveScore `ToFi` - to finish - arrives as UNKNOWN, and stays it."""
        subject = card()
        action, evidence = stamp(subject, {
            "livescore": entry(status=fixture_authority.UNKNOWN, raw="ToFi",
                               code="44")})
        self.assertEqual(action, "")
        self.assertNotIn("authority_status", subject)
        self.assertEqual(verdict_for(subject).state, LIVE)

    def test_a_card_with_no_authority_block_at_all_is_untouched(self):
        subject = card()
        before = json.dumps(subject, sort_keys=True)
        action, _evidence = stamp(subject, {})
        self.assertEqual(action, "")
        self.assertEqual(json.dumps(subject, sort_keys=True), before)


# =======================================================================
# the non-finished states
# =======================================================================

class NonFinishedStateTests(unittest.TestCase):
    def test_authority_live_confirms_live(self):
        subject = card()
        action, _evidence = stamp(subject, {
            "livescore": entry(status=fixture_authority.LIVE, raw="2H"),
            "espn-header": entry("espn-header", "ESPN",
                                 status=fixture_authority.LIVE, raw="2H")})
        self.assertEqual(action, "live")
        self.assertEqual(subject["authority_status"], "LIVE")
        self.assertIs(event_lifecycle.authority_says_live(subject), True)
        self.assertEqual(verdict_for(subject).state, LIVE)

    def test_a_stale_source_ft_cannot_retire_a_match_the_authorities_call_live(self):
        """One playlist carrying FT while the authorities say in progress is
        the case FINAL_2 ধাপ ৪ asks not to act on."""
        subject = card(schedule_status="FT", status="FT")
        stamp(subject, {
            "livescore": entry(status=fixture_authority.LIVE, raw="2H"),
            "espn-header": entry("espn-header", "ESPN",
                                 status=fixture_authority.LIVE, raw="2H")})
        verdict = verdict_for(subject)
        self.assertEqual(verdict.state, LIVE)
        self.assertIn("authority_live", verdict.protections)
        self.assertGreaterEqual(verdict.contradicted_end_confirmations, 1)

    def test_authority_upcoming_neither_promotes_nor_ends(self):
        subject = card()
        action, _evidence = stamp(subject, {
            "livescore": entry(status=fixture_authority.UPCOMING, raw="NS",
                               code="1"),
            "espn-header": entry("espn-header", "ESPN",
                                 status=fixture_authority.UPCOMING,
                                 raw="Scheduled", code="STATUS_SCHEDULED")})
        self.assertEqual(action, "recorded")
        self.assertNotIn("authority_status", subject)
        self.assertEqual(subject["authority_end_status"],
                         fixture_authority.UPCOMING)
        self.assertEqual(verdict_for(subject).state, LIVE)

    def test_postponed_is_recorded_and_never_applied(self):
        """POSTPONED sits in STRONG_END_STATUSES, so writing it into
        `authority_status` would end the card. Postponed means later."""
        subject = card()
        action, _evidence = stamp(subject, {
            "livescore": entry(status=fixture_authority.POSTPONED,
                               raw="Postp.", code="5"),
            "espn-header": entry("espn-header", "ESPN",
                                 status=fixture_authority.POSTPONED,
                                 raw="Postponed", code="STATUS_POSTPONED")})
        self.assertEqual(action, "recorded")
        self.assertNotIn("authority_status", subject)
        self.assertEqual(subject["authority_end_status"],
                         fixture_authority.POSTPONED)
        self.assertEqual(verdict_for(subject).state, LIVE)
        self.assertIn("POSTPONED", event_lifecycle.STRONG_END_STATUSES)

    def test_suspended_is_recorded_and_never_applied(self):
        """SUSPENDED sits in LIVE_STATUSES, so writing it would assert play.
        Suspended is not finished and is not a claim that play continues."""
        subject = card()
        action, _evidence = stamp(subject, {
            "livescore": entry(status=fixture_authority.SUSPENDED, raw="Susp"),
            "espn-header": entry("espn-header", "ESPN",
                                 status=fixture_authority.SUSPENDED,
                                 code="STATUS_SUSPENDED")})
        self.assertEqual(action, "recorded")
        self.assertNotIn("authority_status", subject)
        self.assertEqual(subject["authority_end_status"],
                         fixture_authority.SUSPENDED)
        self.assertIn("SUSPENDED", event_lifecycle.LIVE_STATUSES)

    def test_cancelled_is_terminal_and_keeps_its_own_word(self):
        subject = card()
        action, _evidence = stamp(subject, {
            "livescore": entry(status=fixture_authority.CANCELLED, raw="Canc.",
                               code="56"),
            "espn-header": entry("espn-header", "ESPN",
                                 status=fixture_authority.CANCELLED,
                                 code="STATUS_CANCELED")})
        self.assertEqual(action, "applied")
        self.assertEqual(subject["authority_status"], "CANCELLED")
        self.assertEqual(subject["authority_end_status"],
                         fixture_authority.CANCELLED)
        self.assertEqual(verdict_for(subject).state, END_PENDING)

    def test_abandoned_is_terminal_and_keeps_its_own_word(self):
        subject = card()
        action, _evidence = stamp(subject, {
            "livescore": entry(status=fixture_authority.ABANDONED,
                               raw="Aband.", code="17"),
            "espn-header": entry("espn-header", "ESPN",
                                 status=fixture_authority.ABANDONED,
                                 raw="Abandoned")})
        self.assertEqual(action, "applied")
        self.assertEqual(subject["authority_status"], "ABANDONED")
        self.assertEqual(subject["authority_end_status"],
                         fixture_authority.ABANDONED)
        self.assertEqual(verdict_for(subject).state, END_PENDING)

    def test_no_result_is_terminal_and_is_not_relabelled_full_time(self):
        """The match happened and produced none. Flattening it into FT would
        lose the only interesting part."""
        subject = card()
        action, _evidence = stamp(subject, {
            "livescore": entry(status=fixture_authority.NO_RESULT,
                               raw="No result"),
            "espn-header": entry("espn-header", "ESPN",
                                 status=fixture_authority.NO_RESULT,
                                 raw="No result")})
        self.assertEqual(action, "applied")
        self.assertEqual(subject["authority_status"], "NO_RESULT")
        self.assertEqual(subject["authority_end_status"],
                         fixture_authority.NO_RESULT)
        self.assertNotEqual(subject["authority_end_status"],
                            fixture_authority.FINISHED)

    def test_a_fixture_that_has_not_kicked_off_cannot_have_finished(self):
        """`same_fixture` matches on a 45-minute window, so a FINISHED against
        a future kickoff most likely means two different meetings of the same
        two clubs. Recorded as a disagreement with the clock, never applied."""
        subject = card(start_time="2026-09-07T22:00:00+00:00",
                       schedule_status="UPCOMING", status="UPCOMING")
        action, _evidence = stamp(subject, BOTH_FINISHED)
        self.assertEqual(action, "conflict")
        self.assertNotIn("authority_status", subject)
        self.assertIn("kickoff is still", subject["authority_end_conflict"])

    def test_a_fixture_past_kickoff_is_unaffected_by_that_guard(self):
        subject = card()
        action, _evidence = stamp(subject, BOTH_FINISHED)
        self.assertEqual(action, "applied")

    def test_an_authority_live_on_an_upcoming_card_does_not_promote_it(self):
        """Routing reads `schedule_status`, never `authority_status`, so T-25
        stays the only thing that moves a fixture across."""
        subject = card(start_time="2026-09-07T22:00:00+00:00",
                       schedule_status="UPCOMING", status="UPCOMING")
        stamp(subject, {
            "livescore": entry(status=fixture_authority.LIVE),
            "espn-header": entry("espn-header", "ESPN",
                                 status=fixture_authority.LIVE)})
        self.assertEqual(subject["authority_status"], "LIVE")
        self.assertEqual(
            event_lifecycle.event_destination(subject, NOW, 25), "upcoming")

    def test_every_terminal_state_maps_to_a_word_the_lifecycle_knows(self):
        for state in authority_status.TERMINAL_STATES:
            word = authority_status.LIFECYCLE_WORD[state]
            self.assertIn(word, event_lifecycle.STRONG_END_STATUSES, state)

    def test_no_record_only_state_has_a_lifecycle_word(self):
        for state in authority_status.RECORD_ONLY_STATES:
            self.assertNotIn(state, authority_status.LIFECYCLE_WORD, state)


# =======================================================================
# the grace, and what happens after it
# =======================================================================

class GraceTests(unittest.TestCase):
    def test_the_card_is_not_removed_on_the_first_sighting(self):
        subject = card()
        stamp(subject, BOTH_FINISHED)
        verdict = verdict_for(subject)
        self.assertEqual(verdict.state, END_PENDING)
        self.assertTrue(verdict.publish)

    def test_the_twenty_minute_grace_is_the_configured_one(self):
        settings = json.loads(read(os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "config", "settings.json")))
        self.assertEqual(
            settings["event_lifecycle"]["post_match_grace_minutes"], GRACE)

    def test_end_pending_becomes_ended_when_the_grace_expires(self):
        subject = card()
        stamp(subject, BOTH_FINISHED)
        seen = event_lifecycle.parse_time(subject["authority_finished_seen_at"])
        subject["ended_seen_at"] = subject["authority_finished_seen_at"]
        inside = verdict_for(subject, now=seen + dt.timedelta(minutes=GRACE - 1))
        outside = verdict_for(subject, now=seen + dt.timedelta(minutes=GRACE + 1))
        self.assertEqual(inside.state, END_PENDING)
        self.assertTrue(inside.publish)
        self.assertEqual(outside.state, ENDED)
        self.assertFalse(outside.publish)

    def test_the_grace_runs_from_the_first_sighting_not_the_latest_scan(self):
        subject = card()
        stamp(subject, BOTH_FINISHED)
        first = subject["authority_finished_seen_at"]
        later = dt.datetime.fromisoformat(first) + dt.timedelta(minutes=15)
        evidence = authority_status.read(shadow_row(BOTH_FINISHED))
        authority_status.stamp(subject, evidence, now=later,
                               confirmations_required=CONFIRMATIONS,
                               post_match_grace_minutes=GRACE)
        self.assertEqual(subject["authority_finished_seen_at"], first)

    def test_remove_after_is_recorded(self):
        subject = card()
        stamp(subject, BOTH_FINISHED)
        seen = event_lifecycle.parse_time(subject["authority_finished_seen_at"])
        self.assertEqual(
            event_lifecycle.parse_time(subject["authority_remove_after"]),
            seen + dt.timedelta(minutes=GRACE))

    def test_the_evidence_is_recorded_beside_the_decision(self):
        subject = card()
        stamp(subject, BOTH_FINISHED)
        joined = " ".join(subject["authority_end_evidence"])
        self.assertIn("ESPN", joined)
        self.assertIn("LiveScore", joined)
        self.assertIn("401914323", joined)
        self.assertIn("1870766", joined)


class SeenCardPathTests(unittest.TestCase):
    """`_admit_to_today`, for a card this scan actually saw."""

    def admit(self, subject, now=NOW):
        return _admit_to_today(
            subject, now, routing_minutes=25, no_link_grace_minutes=30,
            today_max_age_hours=12, post_match_grace_minutes=GRACE)

    def test_a_seen_card_holds_its_place_for_the_grace(self):
        subject = card()
        stamp(subject, BOTH_FINISHED)
        admitted, reason, _dropped = self.admit(subject)
        self.assertIsNotNone(admitted)
        self.assertEqual(reason, "admitted")
        self.assertEqual(admitted["lifecycle_state"], END_PENDING)

    def test_a_seen_card_leaves_once_the_grace_expires(self):
        subject = card()
        stamp(subject, BOTH_FINISHED)
        subject["ended_seen_at"] = subject["authority_finished_seen_at"]
        seen = event_lifecycle.parse_time(subject["ended_seen_at"])
        admitted, reason, _dropped = self.admit(
            subject, now=seen + dt.timedelta(minutes=GRACE + 1))
        self.assertIsNone(admitted)
        self.assertEqual(reason, "authority_finished")

    def test_a_seen_card_with_no_authority_stamp_takes_the_old_path(self):
        subject = card()
        admitted, reason, _dropped = self.admit(subject)
        self.assertIsNotNone(admitted)
        self.assertEqual(reason, "admitted")
        self.assertEqual(admitted["lifecycle_state"], LIVE)

    def test_a_source_strong_end_alone_does_not_take_the_authority_path(self):
        """The branch is guarded on `authority_status` being set, so a feed
        writing FT into `status` keeps the behaviour it always had."""
        subject = card(schedule_status="FT", status="FT")
        self.assertTrue(has_strong_end_signal(subject))
        admitted, reason, _dropped = self.admit(subject)
        self.assertIsNotNone(admitted)
        self.assertEqual(reason, "admitted")


# =======================================================================
# the stream against the authority
# =======================================================================

class StreamVersusAuthorityTests(unittest.TestCase):
    def test_a_playable_card_the_authorities_ended_is_recorded_as_a_conflict(self):
        subject = card()
        self.assertTrue(_is_playable(subject))
        stamp(subject, BOTH_FINISHED)
        self.assertIn("still carries a route",
                      subject["authority_end_conflict"])

    def test_a_card_with_no_route_records_no_such_conflict(self):
        subject = card(url="", verified=False, playback_id="", backups=[])
        stamp(subject, BOTH_FINISHED)
        self.assertNotIn("authority_end_conflict", subject)

    def test_a_viewer_watching_it_does_not_stop_the_end_but_keeps_the_card(self):
        """FINAL_2 ধাপ ৪: grace শেষ হলে card তালিকা থেকে যাবে, চলমান playback
        থামবে না. Retiring a card removes a row; it revokes no URL."""
        subject = card()
        stamp(subject, BOTH_FINISHED)
        verdict = verdict_for(subject, playing=True)
        self.assertEqual(verdict.state, END_PENDING)
        self.assertTrue(verdict.publish)
        self.assertIn("currently_playing", verdict.protections)

    def test_the_conflict_does_not_extend_the_grace(self):
        """Deterministic, as asked: the same window either way."""
        playable, bare = card(), card(url="", verified=False, playback_id="",
                                      backups=[])
        for subject in (playable, bare):
            stamp(subject, BOTH_FINISHED)
            subject["ended_seen_at"] = subject["authority_finished_seen_at"]
        seen = event_lifecycle.parse_time(playable["ended_seen_at"])
        for subject in (playable, bare):
            self.assertEqual(
                verdict_for(subject, now=seen + dt.timedelta(minutes=GRACE + 1)
                            ).state, ENDED)


# =======================================================================
# what must not change
# =======================================================================

class PreservationTests(unittest.TestCase):
    def test_the_fixture_id_is_never_replaced_by_an_authority_id(self):
        subject = card()
        stamp(subject, BOTH_FINISHED)
        self.assertEqual(
            subject["fixture_id"],
            "provider:toluca-vs-monterrey|leagues cup|2026-09-07")
        self.assertNotIn("1870766", subject["fixture_id"])
        self.assertNotIn("401914323", subject["fixture_id"])
        self.assertEqual(subject["id"], "toluca-vs-monterrey")

    def test_the_playback_reference_is_untouched(self):
        subject = card()
        stamp(subject, BOTH_FINISHED)
        self.assertEqual(subject["playback_id"], "ctv_" + "a" * 32)

    def test_the_badge_is_not_rewritten(self):
        """`schedule_status` drives the pill the viewer reads, and badge
        semantics is a separate open question. Only `lifecycle_state` moves."""
        subject = card()
        stamp(subject, BOTH_FINISHED)
        self.assertEqual(subject["schedule_status"], "LIVE_NOW")
        self.assertEqual(subject["status"], "LIVE_NOW")

    def test_the_kickoff_and_the_competition_are_untouched(self):
        subject = card()
        stamp(subject, BOTH_FINISHED)
        self.assertEqual(subject["start_time"], "2026-09-07T01:15:00+00:00")
        self.assertEqual(subject["competition"], "Leagues Cup")

    def test_source_ids_are_untouched(self):
        subject = card()
        stamp(subject, BOTH_FINISHED)
        self.assertEqual(subject["source_ids"],
                         ["srhady-bingstream", "srhady-axsports-live"])

    def test_an_authority_ended_card_reaches_the_archive(self):
        """The archive already collects a card carrying a strong end signal, so
        one the authorities retired is remembered without a new rule."""
        subject = card()
        stamp(subject, BOTH_FINISHED)
        self.assertTrue(has_strong_end_signal(subject))
        archive = {"fixtures": {}}
        stats = event_archive.archive_retired([subject], now=NOW,
                                              archive=archive)
        self.assertEqual(stats["added"], 1)

    def test_an_archived_fixture_cannot_come_back(self):
        subject = card()
        stamp(subject, BOTH_FINISHED)
        archive = {"fixtures": {}}
        event_archive.archive_retired([subject], now=NOW, archive=archive)
        kept, dropped = event_archive.drop_resurrected([card()], archive)
        self.assertEqual(kept, [])
        self.assertEqual(len(dropped), 1)

    def test_a_recorded_only_state_never_reaches_the_archive(self):
        subject = card()
        stamp(subject, {
            "livescore": entry(status=fixture_authority.POSTPONED),
            "espn-header": entry("espn-header", "ESPN",
                                 status=fixture_authority.POSTPONED)})
        archive = {"fixtures": {}}
        stats = event_archive.archive_retired(
            [subject] if has_strong_end_signal(subject) else [], now=NOW,
            archive=archive)
        self.assertEqual(stats["added"], 0)

    def test_the_upcoming_outage_hold_is_not_touched_by_any_of_this(self):
        """Partial-publish protection lives in scanner/source_outage.py and is
        not imported here. Asserted rather than assumed."""
        source = read(os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "scanner", "authority_status.py"))
        self.assertNotIn("source_outage", source)
        self.assertNotIn("hold_upcoming", source)


class WiringTests(unittest.TestCase):
    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def source(self):
        with open(os.path.join(self.ROOT, "scanner", "events.py"),
                  encoding="utf-8") as handle:
            return handle.read()

    def test_the_authorities_are_read_before_anything_routes_a_card(self):
        source = self.source()
        self.assertLess(source.index("authority_status.apply("),
                        source.index("    for card in merged:"))

    def test_they_are_read_once_and_reused_for_the_report(self):
        source = self.source()
        self.assertEqual(source.count("fixture_authority.collect("), 1)
        self.assertIn("authority_rows, authority_health", source)

    def test_a_targeted_trigger_applies_nothing(self):
        source = self.source()
        self.assertIn("targeted_scan = targeted_window_minutes > 0", source)
        self.assertIn("if authority_enabled and not targeted_scan:", source)

    def test_the_carried_forward_cards_are_stamped_too(self):
        source = self.source()
        self.assertIn("authority_status_carried", source)
        self.assertLess(source.index("authority_status_carried"),
                        source.index("today_items, protection_stats = protect_live_events("))

    def test_every_authority_step_is_wrapped(self):
        source = self.source()
        for key in ('schedule_stats["authority_status"] = {"error"',
                    'schedule_stats["authority_status_carried"] = {"error"',
                    'schedule_stats["fixture_authority_shadow"] = {"error"'):
            self.assertIn(key, source)


if __name__ == "__main__":
    unittest.main()
