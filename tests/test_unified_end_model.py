"""One clock per kind of evidence, and the same one on every path.

There were three, and they disagreed about a fixture at the same instant.

  events._is_today_fresh        dropped a Today card once `end_time +
                                post_match_grace` had passed - whatever the
                                end time's SOURCE. No lifecycle state, no
                                reason, no authority veto, nothing recorded.
                                Called with a grace of 0 on the admission
                                path and 20 on the targeted carry-through, so
                                it disagreed with itself as well.
  event_lifecycle.verified_end_passed
                                requires the end to be provider-STATED, and
                                took `DEFAULT_ESTIMATE_GRACE_MINUTES` (90) as
                                its default because it began life inside the
                                estimate path - so a provider-ended fixture
                                reached END_PENDING at end + 90 and left at
                                end + 110, while the filter above removed the
                                card at end + 20.
  event_lifecycle.estimate_passed
                                any end + 90, a supporting signal only,
                                needing dead links and three confirmations
                                before it retires anything - and reachable
                                only from `live_protection`, which sees a
                                card that is ABSENT. A fixture its feed keeps
                                listing never reached it at all.

Measured over 367 real departures since 2026-09-06 12:00Z: 86 were decided by
the filter's arithmetic, and NOT ONE departure in 367 had a provider-stated
end that had passed. The filter was standing in for a lifecycle rule that did
not exist, and being the fastest and the weakest of the three, it won.

The model now:

  provider-STATED end       authoritative. `PROVIDER_END_GRACE_MINUTES` is 0 -
                            the fixture said when it finishes - and the only
                            cushion is FINAL_2's post-match grace.
  sport/assumed ESTIMATE    not authoritative, and never relabelled as one. It
                            is a bound, not a finish: past the estimate plus
                            `estimate_grace_minutes`, with no fixture
                            authority calling the match live, the CARD is
                            retired and the FIXTURE is not declared over.
                            `estimated_only` says so, and nothing records it
                            as a retirement.
  absent card               untouched. Requirement 6 still owns it: absence
                            may be an outage, and only an authority or a probe
                            proving every link dead retires it.
"""
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scanner import event_archive as ea  # noqa: E402
from scanner import events as ev  # noqa: E402
from scanner import event_lifecycle as el  # noqa: E402
from scanner.live_protection import protect_live_events  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
KICKOFF = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)
GRACE = 20
ESTIMATE_GRACE = 90
MAX_AGE = 12
NO_LINK = 25
ROUTING = 25


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def card(sport="football", competition="English Premier League",
         end_minutes=150, source="sport", **extra):
    subject = {
        "id": "alpha-vs-beta",
        "fixture_id": "provider:alpha-vs-beta|premier league|2026-09-07",
        "name": "Alpha FC vs Beta FC",
        "competition": competition,
        "sport_type": sport,
        "start_time": KICKOFF.isoformat(),
        "start_at": KICKOFF.isoformat(),
        "end_time": (KICKOFF + timedelta(minutes=end_minutes)).isoformat(),
        "end_time_source": source,
        "schedule_verified": True,
        "schedule_status": "LIVE_NOW",
        "status": "LIVE_NOW",
        "url": "https://a.example/live.m3u8",
        "available_link_count": 1,
        "verification_status": "verified_global",
        "verified": True,
        "playback_id": "ctv_alphabeta",
    }
    subject.update(extra)
    return subject


def admit(subject, at, **kwargs):
    options = {
        "routing_minutes": ROUTING,
        "no_link_grace_minutes": NO_LINK,
        "today_max_age_hours": MAX_AGE,
        "post_match_grace_minutes": GRACE,
        "estimate_grace_minutes": ESTIMATE_GRACE,
    }
    options.update(kwargs)
    return ev._admit_to_today(dict(subject), at, **options)


def fresh(subject, at, grace=GRACE):
    probe = dict(subject)
    probe["_source_timezone"] = timezone.utc
    return ev._is_today_fresh(probe, at, MAX_AGE, NO_LINK, grace)


def carried(subject, at, probe=lambda _c: True, **kwargs):
    options = {
        "probe": probe,
        "now": at,
        "authority_states": {},
        "playing_event_ids": set(),
        "grace_minutes": ESTIMATE_GRACE,
        "post_match_grace_minutes": GRACE,
    }
    options.update(kwargs)
    with tempfile.TemporaryDirectory() as tmp:
        options["state_path"] = Path(tmp) / "protection.json"
        kept, stats = protect_live_events([], [dict(subject)], **options)
    return kept, stats


class WhatCountsAsAnAuthoritativeEnd(unittest.TestCase):
    def test_a_provider_stated_end_needs_no_grace_of_its_own(self):
        """The fixture said when it finishes. `PROVIDER_END_GRACE_MINUTES` is
        0 and the post-match grace is the only cushion, which is FINAL_2."""
        self.assertEqual(0, el.PROVIDER_END_GRACE_MINUTES)
        stated = card(source="provider")
        at = KICKOFF + timedelta(minutes=151)
        self.assertTrue(el.verified_end_passed(
            stated, at, el.PROVIDER_END_GRACE_MINUTES))
        admitted, reason, _ = admit(stated, at)
        self.assertIsNotNone(admitted)
        self.assertEqual("admitted", reason)
        self.assertEqual("END_PENDING", admitted["lifecycle_state"])
        self.assertNotIn("lifecycle_end_basis", admitted,
                         "a provider end is not an estimate")

    def test_a_provider_stated_end_goes_when_its_grace_runs_out(self):
        stated = card(source="provider")
        stated["ended_seen_at"] = (
            KICKOFF + timedelta(minutes=150)).isoformat()
        admitted, reason, _ = admit(
            stated, KICKOFF + timedelta(minutes=150 + GRACE + 1))
        self.assertIsNone(admitted)
        self.assertEqual("estimate_expired", reason)

    def test_an_authority_finished_owns_the_status(self):
        """The existing path, untouched by any of this: an authority verdict
        is a strong end signal and reaches `decide()` as one."""
        ended = card(authority_status="FINISHED")
        self.assertTrue(el.has_strong_end_signal(ended))
        admitted, reason, _ = admit(ended, KICKOFF + timedelta(minutes=100))
        self.assertIsNotNone(admitted)
        self.assertEqual("END_PENDING", admitted["lifecycle_state"])
        self.assertNotIn("lifecycle_end_basis", admitted)

    def test_an_authority_finished_beats_a_working_stream(self):
        """FINAL_1: a live URL proves the ROUTE. If an authority says the
        match is over, the authority owns the status."""
        ended = card(authority_status="FINISHED")
        ended["ended_seen_at"] = (
            KICKOFF + timedelta(minutes=95)).isoformat()
        admitted, reason, _ = admit(
            ended, KICKOFF + timedelta(minutes=95 + GRACE + 1))
        self.assertIsNone(admitted)
        self.assertEqual("authority_finished", reason)

    def test_only_the_authority_field_speaks_for_an_authority(self):
        """A playlist's own `LIVE_NOW` is a statement about a listing. Reading
        it as an authority is what published `Toluca vs Monterrey` live for
        six hours; `authority_status` is the only field an authority writes."""
        self.assertIsNone(el.fixture_authority_says_live(
            {"schedule_status": "LIVE_NOW", "status": "LIVE_NOW"}))
        self.assertTrue(el.fixture_authority_says_live(
            {"authority_status": "LIVE"}))
        self.assertFalse(el.fixture_authority_says_live(
            {"authority_status": "FINISHED"}))
        self.assertIsNone(el.fixture_authority_says_live({}))


class AnEstimateIsNeverAnFT(unittest.TestCase):
    def test_a_sport_estimate_is_not_a_provider_end(self):
        guessed = card(source="sport")
        at = KICKOFF + timedelta(minutes=400)
        self.assertFalse(el.verified_end_passed(guessed, at, 0))
        self.assertFalse(el.end_time_is_provider_stated(guessed))

    def test_an_assumed_estimate_is_not_a_provider_end(self):
        guessed = card(source="assumed")
        at = KICKOFF + timedelta(minutes=400)
        self.assertFalse(el.verified_end_passed(guessed, at, 0))

    def test_a_missing_source_reads_as_assumed_not_as_provider(self):
        bare = card()
        bare.pop("end_time_source")
        self.assertEqual("assumed", el.end_time_provenance(bare))
        self.assertFalse(el.verified_end_passed(
            bare, KICKOFF + timedelta(minutes=400), 0))

    def test_an_estimate_retires_the_card_and_says_it_was_an_estimate(self):
        guessed = card(source="sport")
        at = KICKOFF + timedelta(minutes=150 + ESTIMATE_GRACE + 1)
        admitted, reason, _ = admit(guessed, at)
        self.assertIsNotNone(admitted)
        self.assertEqual("END_PENDING", admitted["lifecycle_state"])
        self.assertEqual("estimate", admitted["lifecycle_end_basis"])
        self.assertIn("estimated end", admitted["lifecycle_reason"])

    def test_an_estimate_retirement_is_never_recorded_as_one(self):
        """The card goes; the fixture is not declared finished. Recording it
        would bar day two of a Test - 480 minutes is a DAY."""
        retired = card(source="sport", lifecycle_state="END_PENDING",
                       lifecycle_end_basis="estimate")
        retired["ended_seen_at"] = (
            KICKOFF + timedelta(minutes=240)).isoformat()
        at = KICKOFF + timedelta(minutes=240 + GRACE + 1)
        self.assertEqual("", ea.terminal_provenance(
            retired, now=at, post_match_grace_minutes=GRACE))
        archive = {"fixtures": {}}
        stats = ea.archive_retired([retired], now=at, archive=archive,
                                   post_match_grace_minutes=GRACE)
        self.assertEqual(0, stats["added"])
        self.assertEqual({}, archive["fixtures"])

    def test_a_real_end_arriving_later_outranks_the_estimate_flag(self):
        """The flag is not a permanent exemption: an authority or a feed
        speaking afterwards is answered by its own branch, which is reached
        first."""
        retired = card(lifecycle_state="END_PENDING",
                       lifecycle_end_basis="estimate",
                       authority_status="FINISHED")
        retired["ended_seen_at"] = KICKOFF.isoformat()
        at = KICKOFF + timedelta(minutes=300)
        self.assertEqual("authority_finished", ea.terminal_provenance(
            retired, now=at, post_match_grace_minutes=GRACE))

    def test_the_estimate_verdict_carries_the_flag_not_just_a_reason(self):
        verdict = el.decide(
            card(), el.LifecycleSignals(estimate_passed=True,
                                        seen_in_this_scan=True),
            now=KICKOFF + timedelta(minutes=400),
            post_match_grace_minutes=0)
        self.assertTrue(verdict.estimated_only)
        self.assertFalse(verdict.publish)
        stamped = el.apply_verdict(card(), verdict)
        self.assertEqual("estimate", stamped["lifecycle_end_basis"])

    def test_the_flag_is_cleared_when_a_real_end_replaces_it(self):
        was = el.apply_verdict(
            card(lifecycle_end_basis="estimate"),
            el.LifecycleVerdict("END_PENDING", True, "authoritative"))
        self.assertNotIn("lifecycle_end_basis", was)


class AWorkingStreamIsNotAMatch(unittest.TestCase):
    def test_a_playable_route_cannot_extend_a_fixture_for_ever(self):
        """The bound requirement B asks for. Most of these links are 24-hour
        channel feeds; they answer for ever, and the match does not."""
        subject = card(source="sport")
        for offset in (300, 600, 60 * 30):
            with self.subTest(offset=offset):
                admitted, reason, _ = admit(
                    subject, KICKOFF + timedelta(minutes=offset))
                state = "" if admitted is None else admitted["lifecycle_state"]
                self.assertIn(state or reason,
                              ("END_PENDING", "estimate_expired", "stale"),
                              "a route kept a fixture alive past every bound")

    def test_no_link_verdict_is_passed_on_the_admission_path(self):
        """Nothing probes there, and a route that exists is not a probe
        result. Asserted on the source, because passing one by accident is
        how the bound would be undone."""
        source = read(os.path.join(str(ROOT), "scanner", "events.py"))
        branch = source.split("elif (verified_end_passed(", 1)[1].split(
            "if _routed_early_without_a_link(", 1)[0]
        self.assertNotIn("primary_playable", branch)
        self.assertNotIn("backup_playable", branch)
        self.assertIn("seen_in_this_scan=True", branch)
        self.assertIn("fixture_authority_says_live(card)", branch)

    def test_a_viewer_watching_still_holds_the_card(self):
        """Player availability is not touched: a session in progress is the
        strongest protection there is, and it beats an estimate."""
        verdict = el.decide(
            card(), el.LifecycleSignals(estimate_passed=True,
                                        seen_in_this_scan=True,
                                        currently_playing=True),
            now=KICKOFF + timedelta(minutes=400),
            post_match_grace_minutes=GRACE)
        self.assertEqual("LIVE", verdict.state)
        self.assertTrue(verdict.publish)
        self.assertIn("currently_playing", verdict.protections)

    def test_an_authority_calling_it_live_vetoes_the_estimate(self):
        verdict = el.decide(
            card(authority_status="LIVE"),
            el.LifecycleSignals(authority_live=True, estimate_passed=True,
                                seen_in_this_scan=True, primary_playable=True),
            now=KICKOFF + timedelta(minutes=600),
            post_match_grace_minutes=GRACE)
        self.assertEqual("LIVE", verdict.state)
        self.assertFalse(verdict.estimated_only)


class AnAbsentCardKeepsItsOldTreatment(unittest.TestCase):
    """Requirement 6, and the reason the bound is gated on
    `seen_in_this_scan`: absence may be a source outage."""

    def test_an_absent_card_past_its_estimate_is_still_carried(self):
        kept, _ = carried(card(source="sport"),
                          KICKOFF + timedelta(minutes=600))
        self.assertEqual(1, len(kept))
        self.assertEqual("LIVE", kept[0]["lifecycle_state"])

    def test_the_bound_never_fires_for_an_absent_card(self):
        verdict = el.decide(
            card(), el.LifecycleSignals(estimate_passed=True,
                                        seen_in_this_scan=False,
                                        primary_playable=True),
            now=KICKOFF + timedelta(minutes=600),
            post_match_grace_minutes=GRACE)
        self.assertEqual("LIVE", verdict.state)
        self.assertFalse(verdict.estimated_only)

    def test_an_authority_unavailable_says_nothing_about_the_fixture(self):
        """No `authority_status` at all is not "finished" and not "live". It
        does not veto the bound and it does not trigger it either."""
        subject = card(source="sport")
        self.assertIsNone(el.fixture_authority_says_live(subject))
        admitted, _, _ = admit(subject, KICKOFF + timedelta(minutes=100))
        self.assertIsNotNone(admitted)
        self.assertEqual("LIVE", admitted["lifecycle_state"])

    def test_a_dead_link_absent_card_still_needs_its_confirmations(self):
        kept, stats = carried(card(source="sport"),
                              KICKOFF + timedelta(minutes=600),
                              probe=lambda _c: False,
                              confirmations_required=3)
        self.assertEqual(1, len(kept))
        self.assertEqual("END_PENDING", kept[0]["lifecycle_state"])
        self.assertEqual([], stats["retired_terminal"])


class EveryDurationTheTableHolds(unittest.TestCase):
    """One test per format, because a wrong length ends a match early."""

    def _expiry(self, sport, competition, minutes):
        subject = card(sport=sport, competition=competition,
                       end_minutes=minutes, source="sport")
        just_inside = KICKOFF + timedelta(minutes=minutes + ESTIMATE_GRACE - 1)
        just_outside = KICKOFF + timedelta(minutes=minutes + ESTIMATE_GRACE + 1)
        self.assertFalse(el.estimate_passed(subject, just_inside,
                                            ESTIMATE_GRACE))
        self.assertTrue(el.estimate_passed(subject, just_outside,
                                           ESTIMATE_GRACE))
        return subject, just_inside, just_outside

    def test_football(self):
        self.assertEqual(150, el.SPORT_DURATION_MINUTES["football"])
        subject, inside, outside = self._expiry(
            "football", "English Premier League", 150)
        self.assertEqual("LIVE", admit(subject, inside)[0]["lifecycle_state"])
        self.assertEqual("END_PENDING",
                         admit(subject, outside)[0]["lifecycle_state"])

    def test_t10(self):
        self.assertEqual(150, el.CRICKET_FORMAT_MINUTES["T10"])
        self._expiry("cricket", "Abu Dhabi T10", 150)

    def test_t20(self):
        self.assertEqual(240, el.CRICKET_FORMAT_MINUTES["T20"])
        self._expiry("cricket", "Sher-E-Punjab T20", 240)

    def test_odi(self):
        self.assertEqual(480, el.CRICKET_FORMAT_MINUTES["ODI"])
        self._expiry("cricket", "ODI Series", 480)

    def test_the_hundred(self):
        self.assertEqual(210, el.CRICKET_FORMAT_MINUTES["Hundred"])
        self._expiry("cricket", "The Hundred", 210)

    def test_unknown_cricket_is_neither_a_t20_nor_an_odi(self):
        self.assertEqual(300, el.CRICKET_FORMAT_MINUTES["unknown"])
        self._expiry("cricket", "Some Local Cup", 300)

    def test_an_unknown_sport_falls_back_and_is_still_bounded(self):
        subject = card(sport="", competition="", end_minutes=240,
                       source="assumed")
        self.assertTrue(el.estimate_passed(
            subject, KICKOFF + timedelta(minutes=240 + ESTIMATE_GRACE + 1),
            ESTIMATE_GRACE))

    def test_a_test_day_is_a_day_and_not_the_match(self):
        """480 minutes is a DAY of a Test. The card may leave when the day is
        over; the FIXTURE may not be recorded as finished, or day two is
        barred for good."""
        self.assertEqual(480, el.CRICKET_FORMAT_MINUTES["Test"])
        day_one = card(sport="cricket", competition="Test Series",
                       end_minutes=480, source="sport")
        # Not ended while the day is still running, nor inside the grace.
        for offset in (100, 480, 480 + ESTIMATE_GRACE - 1):
            with self.subTest(offset=offset):
                admitted, _, _ = admit(
                    day_one, KICKOFF + timedelta(minutes=offset))
                self.assertEqual("LIVE", admitted["lifecycle_state"])
        # The card leaves after the day, and nothing remembers it.
        at = KICKOFF + timedelta(minutes=480 + ESTIMATE_GRACE + 1)
        admitted, _, _ = admit(day_one, at)
        self.assertEqual("estimate", admitted["lifecycle_end_basis"])
        self.assertEqual("", ea.terminal_provenance(
            admitted, now=at, post_match_grace_minutes=GRACE))

    def test_day_two_of_that_test_is_admitted_normally(self):
        """Its own kickoff, so its own clock - and no archive row standing in
        the way, which is what makes this work."""
        day_two = card(sport="cricket", competition="Test Series",
                       end_minutes=480, source="sport")
        day_two["start_time"] = (KICKOFF + timedelta(days=1)).isoformat()
        day_two["start_at"] = day_two["start_time"]
        day_two["end_time"] = (
            KICKOFF + timedelta(days=1, minutes=480)).isoformat()
        admitted, reason, _ = admit(
            day_two, KICKOFF + timedelta(days=1, minutes=60))
        self.assertEqual("admitted", reason)
        self.assertEqual("LIVE", admitted["lifecycle_state"])


class TheTabFilterNoLongerEndsMatches(unittest.TestCase):
    def test_an_assumed_end_retires_nothing_here(self):
        for source in ("assumed", "sport"):
            for offset in (151, 300, 600):
                with self.subTest(source=source, offset=offset):
                    self.assertTrue(fresh(
                        card(source=source),
                        KICKOFF + timedelta(minutes=offset)))

    def test_a_provider_end_is_honoured_here_with_the_same_grace(self):
        stated = card(source="provider")
        self.assertTrue(fresh(stated, KICKOFF + timedelta(minutes=150 + 19)))
        self.assertFalse(fresh(stated, KICKOFF + timedelta(minutes=150 + 21)))

    def test_a_running_grace_protects_the_card_from_the_age_guard(self):
        retiring = card(source="sport", lifecycle_state="END_PENDING")
        retiring["ended_seen_at"] = (
            KICKOFF + timedelta(hours=20)).isoformat()
        self.assertTrue(fresh(retiring, KICKOFF + timedelta(hours=20,
                                                            minutes=1)))

    def test_an_expired_grace_drops_the_card(self):
        retiring = card(source="sport", lifecycle_state="END_PENDING")
        retiring["ended_seen_at"] = KICKOFF.isoformat()
        self.assertFalse(fresh(retiring, KICKOFF + timedelta(minutes=GRACE)))

    def test_an_end_pending_card_with_no_stamp_still_faces_the_age_guard(self):
        """Short-circuiting on the state alone exempted an unstamped
        END_PENDING card from `today_max_age_hours` and from the no-link
        grace, which is an unbounded card - the fault at the other end."""
        holding = card(source="sport", lifecycle_state="END_PENDING")
        self.assertFalse(fresh(holding, KICKOFF + timedelta(hours=MAX_AGE + 1)))

    def test_the_age_guard_and_the_no_link_grace_are_untouched(self):
        self.assertFalse(fresh(card(), KICKOFF + timedelta(hours=MAX_AGE + 1)))
        no_route = card()
        for field in ("url", "available_link_count", "playback_id"):
            no_route.pop(field, None)
        self.assertFalse(fresh(no_route,
                               KICKOFF + timedelta(minutes=NO_LINK + 1)))

    def test_a_feed_saying_ended_still_goes_at_once(self):
        self.assertFalse(fresh(card(schedule_status="ENDED"),
                               KICKOFF + timedelta(minutes=10)))


class TheTwoPathsAgree(unittest.TestCase):
    """No more targeted removes, full restores, targeted removes."""

    def test_the_targeted_path_cannot_invent_a_terminal_end(self):
        """It has no probe, no authority and no lifecycle. An estimate that
        expired an hour ago retires nothing there - the full scan reaches that
        verdict, states what it rests on, and stamps it."""
        guessed = card(source="sport")
        at = KICKOFF + timedelta(minutes=150 + ESTIMATE_GRACE + 30)
        self.assertTrue(fresh(guessed, at),
                        "the trigger decided an end it had no evidence for")
        admitted, _, _ = admit(guessed, at)
        self.assertEqual("END_PENDING", admitted["lifecycle_state"])
        self.assertEqual("estimate", admitted["lifecycle_end_basis"])

    def test_the_targeted_path_finishes_what_a_full_scan_started(self):
        guessed = card(source="sport")
        at = KICKOFF + timedelta(minutes=150 + ESTIMATE_GRACE + 1)
        admitted, _, _ = admit(guessed, at)
        stamped = dict(admitted)
        self.assertTrue(fresh(stamped, at))
        self.assertTrue(fresh(stamped, at + timedelta(minutes=GRACE - 1)))
        self.assertFalse(fresh(stamped, at + timedelta(minutes=GRACE)))

    def test_the_same_card_at_the_same_instant_gets_the_same_answer(self):
        """The flicker, stated as a property. Whatever the source and whatever
        the offset, a card the filter keeps is a card admission publishes, and
        a card the filter drops is one admission refuses or retires."""
        for source in ("sport", "assumed", "provider"):
            for offset in (10, 100, 151, 240, 400, 700):
                at = KICKOFF + timedelta(minutes=offset)
                subject = card(source=source)
                kept = fresh(subject, at)
                admitted, reason, _ = admit(subject, at)
                with self.subTest(source=source, offset=offset):
                    if not kept:
                        self.assertTrue(
                            admitted is None
                            or admitted["lifecycle_state"] in ("END_PENDING",
                                                               "ENDED"),
                            "the filter dropped a card admission publishes "
                            "as live - %s at +%d" % (source, offset))
                    else:
                        self.assertNotEqual(
                            "stale", reason,
                            "admission called stale what the filter keeps - "
                            "%s at +%d" % (source, offset))

    def test_both_callers_pass_the_same_grace(self):
        source = read(os.path.join(str(ROOT), "scanner", "events.py"))
        self.assertIn(
            '''    if not _is_today_fresh(
        card, now, today_max_age_hours, no_link_grace_minutes,
        post_match_grace_minutes,
    ):''', source,
            "the admission path defaulted to a grace of 0 while the targeted "
            "carry-through passed 20")


class NothingElseMoved(unittest.TestCase):
    def test_the_post_match_grace_is_still_twenty_and_still_configured(self):
        from scanner.lifecycle_config import lifecycle_settings
        import json
        with open(os.path.join(str(ROOT), "config", "settings.json"),
                  encoding="utf-8") as handle:
            settings = json.load(handle)
        self.assertEqual(20, lifecycle_settings(settings)[
            "post_match_grace_minutes"])
        self.assertEqual(90, lifecycle_settings(settings)[
            "estimate_grace_minutes"])

    def test_no_fixture_id_is_rewritten(self):
        guessed = card(source="sport")
        admitted, _, _ = admit(
            guessed, KICKOFF + timedelta(minutes=150 + ESTIMATE_GRACE + 1))
        self.assertEqual(guessed["fixture_id"], admitted["fixture_id"])

    def test_no_playback_record_is_stranded(self):
        """Retiring a card removes a row. It revokes no URL and deletes no
        playback entry, so nothing can be left pointing at a card that is
        gone."""
        for name in ("event_lifecycle.py", "event_archive.py"):
            source = read(os.path.join(str(ROOT), "scanner", name))
            self.assertNotIn("playback_id", source)

    def test_an_estimate_retirement_cannot_resurrect_a_fixture(self):
        """Nothing is archived, so nothing has to be un-archived - and the
        refusal re-derives itself instead, which is why that is safe."""
        guessed = card(source="sport")
        at = KICKOFF + timedelta(minutes=150 + ESTIMATE_GRACE + 1)
        admitted, _, _ = admit(guessed, at)
        stamped = dict(admitted)
        later = at + timedelta(minutes=GRACE + 1)
        gone, reason, _ = admit(stamped, later)
        self.assertIsNone(gone)
        self.assertEqual("estimate_expired", reason)
        archive = {"fixtures": {}}
        ea.archive_retired([stamped], now=later, archive=archive,
                           post_match_grace_minutes=GRACE)
        kept, dropped = ea.drop_resurrected([dict(guessed)], archive)
        self.assertEqual(1, len(kept))
        self.assertEqual([], dropped)

    def test_partial_publish_protection_is_not_touched(self):
        for name in ("event_lifecycle.py",):
            source = read(os.path.join(str(ROOT), "scanner", name))
            self.assertNotIn("source_outage", source)
            self.assertNotIn("hold_upcoming", source)

    def test_the_existing_protection_counters_are_untouched(self):
        _, stats = carried(card(source="sport"),
                           KICKOFF + timedelta(minutes=200))
        for key in ("carried_forward", "released_ended", "released_stale",
                    "released_dead_link", "released_confirmed",
                    "released_unscheduled_expired", "end_pending",
                    "retired_terminal"):
            self.assertIn(key, stats)

    def test_phase_two_is_not_started(self):
        source = read(os.path.join(str(ROOT), "scanner", "event_lifecycle.py"))
        for token in ("cloudflare", "worker", " kv", " d1"):
            self.assertNotIn(token, source.lower())


if __name__ == "__main__":
    unittest.main()
