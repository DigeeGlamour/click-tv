"""The retry window runs from T-window to T+10, one attempt per five minutes.

TWO FAULTS, ONE WINDOW.

The window used to be `[now, now + window]` on a fixture's kickoff, so it closed
at kickoff exactly. A source that publishes its link when play starts could not
be heard, and the last chance to find a link always fell before the first moment
anyone would want one. On 2026-09-04 the published Today Match carried
`Selaqui Strikers vs Herbertpur Knight Riders`, 105 minutes past kickoff, with
`available_link_count: 0` and the badge still reading LINK UPDATING - a card
nothing was any longer looking for.

Ten minutes past kickoff, not more, because that is what the rest of the system
already agrees on: `config/settings.json` keeps a kicked-off fixture on the
Upcoming tab for `upcoming_past_grace_minutes = 10`, and `data/upcoming.json` is
the list `known_upcoming_fixtures` reads. Hunting past the point where the
fixture leaves that file would be hunting for something not in the list.

WHAT IS AND IS NOT PROMISED ABOUT THE NUMBER OF ATTEMPTS.

Every five-minute slot inside the window may spend one attempt, so a fixture
seen from T-25 through T+10 can receive eight. That is a consequence, not a
requirement, and nothing here asserts it. This repository's own measurements
recorded real gaps of 4, 11, 29, 33, 37, 56, 62, 89, 209 and 246 minutes against
crons asking for one every five - GitHub delays a scheduled event under load and
drops it once the delay reaches the next occurrence. So the tests below pin what
survives that: eligibility is decided per slot from the clock, a missed tick
costs exactly the slot it missed, and a delayed tick that lands in a slot already
spent is not a second attempt.

`after_kickoff_minutes=0` reproduces the old behaviour exactly, which is tested
too - it is what makes the parameter a real one rather than a constant in
disguise.
"""
import datetime
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner.targeted_scan import (  # noqa: E402
    DEFAULT_RETRY_AFTER_KICKOFF_MINUTES,
    attempt_bucket,
    fixture_key,
    load_ledger,
    record_outcome,
    save_ledger,
    select_targets,
)

UTC = datetime.timezone.utc
#: A slot boundary, so every five-minute step below lands in its own slot.
NOW = datetime.datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
WINDOW = 25
KICKOFF = NOW + datetime.timedelta(minutes=WINDOW)


def card(minutes_until_kickoff, **extra):
    item = {
        "id": "alpha-vs-beta",
        "name": "Alpha vs Beta",
        "start_time": (NOW + datetime.timedelta(minutes=minutes_until_kickoff)).isoformat(),
    }
    item.update(extra)
    return item


def playable(item):
    return dict(item, url="https://example.test/live.m3u8", verified=True,
                verification_status="verified")


def plan_at(moment, items, ledger, window=WINDOW, after=None):
    kwargs = {"now": moment, "window_minutes": window}
    if after is not None:
        kwargs["after_kickoff_minutes"] = after
    return select_targets(items, ledger, **kwargs)


class TheBoundariesOfTheWindow(unittest.TestCase):
    """Kickoff is fixed; the clock is what moves, exactly as it does in production."""

    def setUp(self):
        self.item = card(WINDOW)          # kickoff at 12:25
        self.empty = {"fixtures": {}}

    def _eligible_at(self, minutes_from_now):
        moment = NOW + datetime.timedelta(minutes=minutes_from_now)
        plan = plan_at(moment, [self.item], self.empty)
        return plan, moment

    def test_the_seven_boundaries_the_owner_asked_for(self):
        """T-25, T-20, T-5, T+0, T+5, T+10 are in; T+11 is out."""
        expected = [
            (-1, "T-26", False),   # one minute before the window opens
            (0, "T-25", True),
            (5, "T-20", True),
            (20, "T-5", True),
            (25, "T+0", True),
            (30, "T+5", True),
            (35, "T+10", True),
            (36, "T+11", False),
        ]
        for minutes_from_now, label, should_target in expected:
            with self.subTest(boundary=label):
                plan, moment = self._eligible_at(minutes_from_now)
                self.assertEqual(
                    1 if should_target else 0, len(plan.targets),
                    "%s (clock %s, kickoff %s) was %s"
                    % (label, moment.strftime("%H:%M"), KICKOFF.strftime("%H:%M"),
                       "not a target" if should_target else "a target"),
                )
                self.assertEqual(0 if should_target else 1, plan.outside_window)

    def test_the_window_opens_exactly_at_the_configured_width(self):
        """A minute earlier is outside it, whatever the width is set to."""
        for width in (15, 20, 25, 30):
            with self.subTest(window=width):
                item = card(width)
                inside = plan_at(NOW, [item], {"fixtures": {}}, window=width)
                item_late = card(width + 1)
                outside = plan_at(NOW, [item_late], {"fixtures": {}}, window=width)
                self.assertEqual(1, len(inside.targets))
                self.assertEqual(0, len(outside.targets))

    def test_ten_minutes_past_kickoff_is_the_default(self):
        self.assertEqual(10, DEFAULT_RETRY_AFTER_KICKOFF_MINUTES)

    def test_the_past_edge_is_a_parameter_not_a_constant(self):
        """`after_kickoff_minutes=0` is the old behaviour, and it still works -
        which is how this is known to be one window and not two code paths."""
        five_past = NOW + datetime.timedelta(minutes=WINDOW + 5)
        old = plan_at(five_past, [self.item], {"fixtures": {}}, after=0)
        new = plan_at(five_past, [self.item], {"fixtures": {}}, after=10)
        self.assertEqual(0, len(old.targets), "after=0 should close at kickoff")
        self.assertEqual(1, len(new.targets))
        self.assertEqual(0, old.after_kickoff_minutes)
        self.assertEqual(10, new.after_kickoff_minutes)

    def test_a_negative_past_edge_cannot_open_the_window_backwards(self):
        at_kickoff = NOW + datetime.timedelta(minutes=WINDOW)
        plan = plan_at(at_kickoff, [self.item], {"fixtures": {}}, after=-30)
        self.assertEqual(0, plan.after_kickoff_minutes)
        self.assertEqual(1, len(plan.targets), "kickoff itself is still inside")

    def test_the_plan_reports_both_edges(self):
        plan = plan_at(NOW, [self.item], {"fixtures": {}})
        summary = plan.summary()
        self.assertEqual(WINDOW, summary["window_minutes"])
        self.assertEqual(10, summary["after_kickoff_minutes"])
        self.assertEqual(NOW - datetime.timedelta(minutes=10), plan.kickoff_from)
        self.assertEqual(NOW + datetime.timedelta(minutes=WINDOW), plan.kickoff_to)


class TheLadderWalksTheSlotsItActuallyReaches(unittest.TestCase):
    """A ledger on disk, a clock that moves, and no assumption about ticks."""

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.state = Path(self.folder.name) / "targeting.json"
        self.item = card(WINDOW)
        self.key = fixture_key(self.item)

    def tearDown(self):
        self.folder.cleanup()

    def _tick(self, minutes_from_now, published=()):
        moment = NOW + datetime.timedelta(minutes=minutes_from_now)
        ledger = load_ledger(self.state)
        plan = plan_at(moment, [self.item], ledger)
        save_ledger(record_outcome(ledger, plan, list(published), now=moment), self.state)
        return plan, moment

    def _entry(self):
        return json.loads(self.state.read_text(encoding="utf-8"))["fixtures"].get(self.key)

    def test_every_slot_reached_spends_one_attempt(self):
        """Eight slots exist between T-25 and T+10. This walks all eight because
        it can; production reaches whichever ones GitHub delivers."""
        slots = []
        for minutes_from_now in range(0, 36, 5):
            plan, moment = self._tick(minutes_from_now)
            slots.append((attempt_bucket(moment), len(plan.targets)))
        self.assertEqual([1] * 8, [count for _, count in slots], slots)
        self.assertEqual(8, len({bucket for bucket, _ in slots}), "slots collided")
        self.assertEqual(8, self._entry()["attempts"])

    def test_a_delayed_tick_inside_a_spent_slot_is_not_a_second_attempt(self):
        self._tick(0)
        for seconds in (30, 90, 180, 299):
            moment = NOW + datetime.timedelta(seconds=seconds)
            ledger = load_ledger(self.state)
            plan = plan_at(moment, [self.item], ledger)
            with self.subTest(seconds=seconds):
                self.assertEqual(0, len(plan.targets))
                self.assertEqual(1, plan.same_bucket)
        self.assertEqual(1, self._entry()["attempts"])

    def test_missed_ticks_cost_their_own_slots_and_nothing_else(self):
        """Only three of the eight slots are reached - T-25, T-5 and T+10. Each
        of the three is still a target, and the count is three, not eight."""
        reached = []
        for minutes_from_now in (0, 20, 35):
            plan, moment = self._tick(minutes_from_now)
            reached.append(len(plan.targets))
        self.assertEqual([1, 1, 1], reached)
        self.assertEqual(3, self._entry()["attempts"])

    def test_a_fixture_first_seen_halfway_through_still_gets_its_slots(self):
        """Nothing rewinds. It gets the slots that remain, however many that is."""
        counts = []
        for minutes_from_now in (20, 25, 30, 35):
            plan, _ = self._tick(minutes_from_now)
            counts.append(len(plan.targets))
        self.assertEqual([1, 1, 1, 1], counts)
        self.assertEqual(4, self._entry()["attempts"])

    def test_the_ladder_stops_the_moment_a_link_is_found(self):
        self._tick(0)
        self._tick(5)
        self.assertEqual(2, self._entry()["attempts"])
        self._tick(10, published=[playable(self.item)])
        self.assertTrue(self._entry()["resolved"])

        for minutes_from_now in (15, 20, 25, 30, 35):
            plan, _ = self._tick(minutes_from_now)
            with self.subTest(minutes_from_now=minutes_from_now):
                self.assertEqual(0, len(plan.targets))
                self.assertEqual(1, plan.already_resolved)
                self.assertEqual(0, plan.same_bucket)
        self.assertEqual(3, self._entry()["attempts"], "a resolved fixture was re-attempted")

    def test_past_the_far_edge_no_new_attempt_is_made(self):
        for minutes_from_now in range(0, 36, 5):
            self._tick(minutes_from_now)
        spent = self._entry()["attempts"]

        for minutes_from_now in (36, 40, 55, 100):
            plan, _ = self._tick(minutes_from_now)
            with self.subTest(minutes_from_now=minutes_from_now):
                self.assertEqual(0, len(plan.targets))
                self.assertEqual(1, plan.outside_window)
                self.assertEqual(0, plan.same_bucket, "it is the window, not the slot")
        self.assertEqual(spent, self._entry()["attempts"])

    def test_an_unresolved_fixture_that_ran_out_of_window_is_left_as_it_was(self):
        """No link, out of time. The card is untouched rather than altered - the
        Today Match lifecycle owns it from here, not this planner."""
        self._tick(0)
        before = dict(self._entry())
        self._tick(36)
        self.assertEqual(before, self._entry())


class NothingFromTheEarlierStepsWasDisturbed(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.state = Path(self.folder.name) / "targeting.json"
        self.item = card(WINDOW)
        self.key = fixture_key(self.item)

    def tearDown(self):
        self.folder.cleanup()

    def _tick(self, minutes_from_now, published=()):
        moment = NOW + datetime.timedelta(minutes=minutes_from_now)
        ledger = load_ledger(self.state)
        plan = plan_at(moment, [self.item], ledger)
        save_ledger(record_outcome(ledger, plan, list(published), now=moment), self.state)
        return moment

    def _entry(self):
        return json.loads(self.state.read_text(encoding="utf-8"))["fixtures"][self.key]

    def test_the_first_attempt_timestamp_never_moves(self):
        first = self._tick(0)
        for minutes_from_now in (5, 10, 30, 35):
            self._tick(minutes_from_now)
        self.assertEqual(first.isoformat(), self._entry()["attempted_at"])

    def test_the_last_attempt_fields_follow_the_clock(self):
        self._tick(0)
        last = self._tick(35)
        entry = self._entry()
        self.assertEqual(last.isoformat(), entry["last_attempt_at"])
        self.assertEqual(attempt_bucket(last), entry["last_attempt_bucket"])

    def test_first_link_at_is_still_carried_and_last_success_still_moves(self):
        self._tick(0)
        found = self._tick(5, published=[playable(self.item)])
        self.assertEqual(found.isoformat(), self._entry()["first_link_at"])
        self.assertEqual(found.isoformat(), self._entry()["last_success_at"])

    def test_a_legacy_ledger_entry_inside_the_widened_window_is_eligible(self):
        """An entry with an attempt and no slot recorded - written before those
        fields existed - is a target once, including past kickoff now."""
        legacy = {"fixtures": {self.key: {"attempted": True, "attempts": 2,
                                          "resolved": False}}}
        five_past = NOW + datetime.timedelta(minutes=WINDOW + 5)
        plan = plan_at(five_past, [self.item], legacy)
        self.assertEqual(1, len(plan.targets))
        self.assertEqual(0, plan.already_attempted)

    def test_a_legacy_resolved_entry_is_still_left_alone_past_kickoff(self):
        legacy = {"fixtures": {self.key: {"attempts": 1, "resolved": True}}}
        five_past = NOW + datetime.timedelta(minutes=WINDOW + 5)
        plan = plan_at(five_past, [self.item], legacy)
        self.assertEqual(0, len(plan.targets))
        self.assertEqual(1, plan.already_resolved)

    def test_a_fixture_with_no_kickoff_at_all_is_never_a_target(self):
        nowhere = {"id": "no-clock", "name": "No Clock"}
        plan = plan_at(NOW, [nowhere], {"fixtures": {}})
        self.assertEqual(0, len(plan.targets))
        self.assertEqual(1, plan.outside_window)

    def test_unrelated_fixtures_are_still_left_out(self):
        plan = plan_at(NOW, [card(WINDOW), card(600, id="far-away")],
                       {"fixtures": {}})
        self.assertEqual(1, len(plan.targets))
        self.assertEqual(1, plan.outside_window)
        self.assertEqual(2, plan.considered)


class TheCandidateFilterFollowsTheSameWindow(unittest.TestCase):
    """`accepts` admits a source record by its own kickoff when the name differs.

    It reads the plan's two edges, so widening the window widens this too - which
    is required, not incidental: a source that writes "AUS v BAN" for a match
    that has just started must still be admitted.
    """

    def setUp(self):
        self.plan = plan_at(NOW, [card(WINDOW)], {"fixtures": {}})

    def _candidate(self, minutes_from_now):
        return {"name": "Totally Different Wording",
                "start_time": (NOW + datetime.timedelta(minutes=minutes_from_now)).isoformat()}

    def test_a_candidate_that_has_just_kicked_off_is_admitted(self):
        self.assertTrue(self.plan.accepts(self._candidate(-5)))

    def test_a_candidate_eleven_minutes_past_kickoff_is_not(self):
        self.assertFalse(self.plan.accepts(self._candidate(-11)))

    def test_a_candidate_inside_the_forward_window_is_admitted(self):
        self.assertTrue(self.plan.accepts(self._candidate(WINDOW)))

    def test_a_candidate_beyond_it_is_not(self):
        self.assertFalse(self.plan.accepts(self._candidate(WINDOW + 1)))

    def test_a_plan_with_no_targets_admits_nothing(self):
        idle = plan_at(NOW, [card(600, id="far-away")], {"fixtures": {}})
        self.assertFalse(idle.accepts(self._candidate(0)))


if __name__ == "__main__":
    unittest.main()
