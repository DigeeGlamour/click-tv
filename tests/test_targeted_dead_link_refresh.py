"""A resolved fixture is left alone while its link works, and only then.

`resolved` is a memory of what was true when an attempt ran. The URLs behind
these feeds rotate and expire - the committed ledger redacts `token=` query
values for exactly that reason - so a fixture resolved at T-25 can be dead by
kickoff, which is the moment it matters. Treating `resolved: true` as permanent
means the one fixture nobody is looking for any more is the one that needs help.

THE MISTAKE THIS AVOIDS, AND WHY IT IS NOT HYPOTHETICAL.

The obvious rule - "if the card has no playable link, the link died" - is wrong
here, and measurably so. Every card in the published `data/upcoming.json` on
2026-09-04, all 124 of them, is:

    metadata_only: true · verification_status: "metadata_only"
    no url · no playback_id · available_link_count 0

That is by design: an Upcoming card announces a fixture, and its stream lives on
the Today Match card once it has one. Under the obvious rule every resolved
fixture would be reopened on every slot, forever - the exact opposite of "do not
rescan working streams".

So death requires a positive statement, not an absence:

    item is None            silence  - not in this trigger's list at all
    metadata_only: true     silence  - carries no stream by design
    no url, no playback_id  silence  - nothing is being claimed
    a route named, failing  DIED     - reopen it

Silence leaves `resolved` standing. Measured against the real published files
with that rule: 0 of 124 Upcoming cards read as dead, and the working Today card
reads as alive.

WHERE A DEAD STREAM CAN ACTUALLY BE SEEN.

A fixture promoted to Today Match takes its stream with it and leaves the
Upcoming list. `known_upcoming_fixtures` reads `data/upcoming.json`, so for a
while the rule above was real code nothing could reach: the only card that ever
names a route was in a file the planner never opened. The published data says so
plainly - every Upcoming card is an announcement, and the one working stream in
the repository is on a Today card, 300 minutes past its kickoff.

`known_today_refresh_candidates` closes that, and is deliberately narrow. Only
cards naming a playback_id or an http(s) url; `metadata_only` excluded outright,
which is what a Today card still waiting for its first link looks like; nothing
a card calls finished, judged through `live_protection.ENDED_STATUSES` so there
is one vocabulary rather than two.

The two lists are merged by `fixture_key` - the published card id, the thing that
survives the promotion - so a fixture briefly present in both is decided exactly
once, and the route-bearing card is the one that decides. The window is not
applied to a refresh, because a live match is hours past kickoff and a T+10 bound
would make the whole path unreachable again.

WHAT IS NOT HERE.

No new verification. `link_has_died` asks `has_valid_link`, which is the same
predicate `record_outcome` already uses to set `resolved`. No probe is made, no
request is sent, and nothing outside this module is consulted.
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
    attempt_bucket,
    fixture_key,
    has_valid_link,
    is_retry_eligible,
    known_today_refresh_candidates,
    link_has_died,
    load_ledger,
    plan_targeted_upcoming_scan,
    record_outcome,
    retry_skip_reason,
    save_ledger,
    select_targets,
)

UTC = datetime.timezone.utc
NOW = datetime.datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
WINDOW = 25
KICKOFF = NOW + datetime.timedelta(minutes=WINDOW)

UPCOMING_FILE = ROOT / "data" / "upcoming.json"
TODAY_FILE = ROOT / "data" / "today-match.json"


def base_card(**extra):
    card = {"id": "alpha-vs-beta", "name": "Alpha vs Beta",
            "start_time": KICKOFF.isoformat()}
    card.update(extra)
    return card


def working_card():
    return base_card(url="https://example.test/live.m3u8", verified=True,
                     verification_status="verified")


def announced_card():
    """What an Upcoming card really looks like: an announcement, no stream."""
    return base_card(metadata_only=True, verification_status="metadata_only",
                     status="UPCOMING", available_link_count=0)


def dead_card():
    """A card that names a route which no longer passes."""
    return base_card(url="https://example.test/live.m3u8",
                     verification_status="failed", available_link_count=0)


RESOLVED = {"attempted": True, "attempts": 1, "resolved": True,
            "resolved_at": NOW.isoformat(),
            "first_link_at": NOW.isoformat(),
            "last_success_at": NOW.isoformat()}


def ledger_with(record=None):
    return {"fixtures": {fixture_key(base_card()): dict(record or RESOLVED)}}


class SilenceIsNotDeath(unittest.TestCase):
    def test_a_fixture_not_in_this_triggers_list_is_not_reopened(self):
        self.assertFalse(link_has_died(None))

    def test_an_announcement_card_says_nothing_about_the_stream(self):
        self.assertFalse(link_has_died(announced_card()))

    def test_a_card_naming_no_route_says_nothing_either(self):
        self.assertFalse(link_has_died(base_card()))
        self.assertFalse(link_has_died(base_card(available_link_count=0)))

    def test_the_real_published_upcoming_cards_all_read_as_silent(self):
        """The measurement the rule exists for. Every one of them is
        metadata_only, so none may reopen a resolved fixture."""
        if not UPCOMING_FILE.exists():
            self.skipTest("no published upcoming.json in this checkout")
        items = json.loads(UPCOMING_FILE.read_text(encoding="utf-8"))["items"]
        if not items:
            self.skipTest("published upcoming.json is empty")
        dead = [item for item in items if link_has_died(item)]
        self.assertEqual([], dead, f"{len(dead)} of {len(items)} read as dead")

    def test_the_real_published_today_cards_are_judged_correctly(self):
        """A working Today card is alive; a LINK UPDATING one is silent, because
        it never had a link to lose - the normal ladder owns that fixture."""
        if not TODAY_FILE.exists():
            self.skipTest("no published today-match.json in this checkout")
        for item in json.loads(TODAY_FILE.read_text(encoding="utf-8"))["items"]:
            with self.subTest(card=str(item.get("name"))[:40]):
                if has_valid_link(item):
                    self.assertFalse(link_has_died(item), "a working card read as dead")
                else:
                    self.assertFalse(link_has_died(item),
                                     "a card with no route read as dead")


class AWorkingStreamIsNeverRescanned(unittest.TestCase):
    def test_a_verified_url_card_keeps_its_fixture_suppressed(self):
        card = working_card()
        key = fixture_key(card)
        self.assertEqual("resolved",
                         retry_skip_reason(ledger_with(), key, now=NOW, item=card))
        self.assertFalse(is_retry_eligible(ledger_with(), key, now=NOW, item=card))

    def test_a_playback_id_card_counts_as_a_working_link(self):
        """A published card carries the playback_id the proxy resolves, not a
        stream URL. Judging on the URL alone would rescan everything."""
        card = base_card(playback_id="pb-1", verification_status="verified_global")
        self.assertFalse(link_has_died(card))
        self.assertEqual("resolved", retry_skip_reason(
            ledger_with(), fixture_key(card), now=NOW, item=card))

    def test_an_announcement_card_also_keeps_it_suppressed(self):
        card = announced_card()
        self.assertEqual("resolved", retry_skip_reason(
            ledger_with(), fixture_key(card), now=NOW, item=card))

    def test_a_working_resolved_fixture_is_skipped_in_every_later_slot(self):
        card = working_card()
        ledger = ledger_with()
        for minutes in (0, 5, 10, 15, 20, 25, 30, 35):
            moment = NOW + datetime.timedelta(minutes=minutes)
            plan = select_targets([card], ledger, now=moment,
                                  window_minutes=WINDOW)
            with self.subTest(minutes=minutes):
                self.assertEqual(0, len(plan.targets))
                self.assertEqual(1, plan.already_resolved)
                self.assertEqual(0, plan.reopened_dead_link)


class ADeadStreamReopensTheHunt(unittest.TestCase):
    def test_a_named_route_that_fails_is_death(self):
        self.assertTrue(link_has_died(dead_card()))

    def test_a_named_route_whose_publishing_was_blocked_is_death(self):
        self.assertTrue(link_has_died(
            base_card(url="https://example.test/live.m3u8", verified=True,
                      publish_allowed=False)))

    def test_the_fixture_becomes_a_target_again(self):
        card = dead_card()
        plan = select_targets([card], ledger_with(), now=NOW,
                              window_minutes=WINDOW)
        self.assertEqual({fixture_key(card)}, plan.targets)
        self.assertEqual(0, plan.already_resolved)
        self.assertEqual(1, plan.reopened_dead_link,
                         "a reopened fixture must be distinguishable in the report")

    def test_a_reopen_still_costs_only_one_attempt_per_slot(self):
        """The slot rule is not bypassed by the reopen."""
        card = dead_card()
        key = fixture_key(card)
        ledger = ledger_with(dict(RESOLVED, last_attempt_bucket=attempt_bucket(NOW)))
        plan = select_targets([card], ledger, now=NOW, window_minutes=WINDOW)
        self.assertEqual(0, len(plan.targets))
        self.assertEqual(1, plan.same_bucket)

        later = NOW + datetime.timedelta(minutes=5)
        plan = select_targets([card], ledger, now=later, window_minutes=WINDOW)
        self.assertEqual({key}, plan.targets)

    def test_a_reopen_still_obeys_the_window(self):
        card = dead_card()
        past = KICKOFF + datetime.timedelta(minutes=11)
        plan = select_targets([card], ledger_with(), now=past,
                              window_minutes=WINDOW)
        self.assertEqual(0, len(plan.targets))
        self.assertEqual(1, plan.outside_window)
        self.assertEqual(0, plan.reopened_dead_link)

    def test_a_fixture_that_was_never_resolved_is_not_counted_as_reopened(self):
        """It is an ordinary target. `reopened_dead_link` must mean what it says."""
        plan = select_targets([dead_card()], {"fixtures": {}}, now=NOW,
                              window_minutes=WINDOW)
        self.assertEqual(1, len(plan.targets))
        self.assertEqual(0, plan.reopened_dead_link)


class TheHistorySurvivesTheRefresh(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.state = Path(self.folder.name) / "targeting.json"
        self.key = fixture_key(base_card())

    def tearDown(self):
        self.folder.cleanup()

    def _tick(self, minutes, listed, published=()):
        moment = NOW + datetime.timedelta(minutes=minutes)
        ledger = load_ledger(self.state)
        plan = select_targets([listed], ledger, now=moment, window_minutes=WINDOW)
        save_ledger(record_outcome(ledger, plan, list(published), now=moment),
                    self.state)
        return plan, moment

    def _entry(self):
        return json.loads(self.state.read_text(encoding="utf-8"))["fixtures"][self.key]

    def test_a_refresh_updates_one_entry_and_creates_no_second_one(self):
        self._tick(0, base_card(), [working_card()])
        self._tick(5, dead_card())
        self._tick(10, dead_card(), [working_card()])
        fixtures = json.loads(self.state.read_text(encoding="utf-8"))["fixtures"]
        self.assertEqual([self.key], list(fixtures),
                         "the refresh forked the fixture into a second entry")

    def test_the_first_link_time_is_never_overwritten_by_a_refresh(self):
        _, first = self._tick(0, base_card(), [working_card()])
        self._tick(5, dead_card())
        self._tick(10, dead_card(), [working_card()])
        self.assertEqual(first.isoformat(), self._entry()["first_link_at"])

    def test_a_new_usable_link_moves_the_last_success(self):
        self._tick(0, base_card(), [working_card()])
        self._tick(5, dead_card())
        _, found_again = self._tick(10, dead_card(), [working_card()])
        entry = self._entry()
        self.assertEqual(found_again.isoformat(), entry["last_success_at"])
        self.assertIs(True, entry["resolved"])

    def test_the_attempt_count_and_the_first_attempt_are_preserved(self):
        _, first = self._tick(0, base_card(), [working_card()])
        self._tick(5, dead_card())
        self._tick(10, dead_card(), [working_card()])
        entry = self._entry()
        self.assertEqual(3, entry["attempts"], "history was reset by the refresh")
        self.assertEqual(first.isoformat(), entry["attempted_at"])

    def test_losing_a_link_is_recorded_as_its_own_fact(self):
        """"never had a link" and "had one and lost it" are different problems,
        and a report that cannot tell them apart is not much of a report."""
        self._tick(0, base_card(), [working_card()])
        self.assertNotIn("link_lost_at", self._entry())
        _, lost = self._tick(5, dead_card())
        entry = self._entry()
        self.assertIs(False, entry["resolved"])
        self.assertEqual(lost.isoformat(), entry["link_lost_at"])

    def test_the_moment_it_was_lost_is_not_moved_by_later_empty_attempts(self):
        self._tick(0, base_card(), [working_card()])
        _, lost = self._tick(5, dead_card())
        self._tick(10, dead_card())
        self._tick(15, dead_card())
        self.assertEqual(lost.isoformat(), self._entry()["link_lost_at"])

    def test_a_recovered_link_still_remembers_that_it_died(self):
        """Found while verifying this step: the recovery branch dropped
        `link_lost_at`, which made a second success indistinguishable from an
        uneventful first one. A recovery is only legible if the loss is still
        on the record."""
        self._tick(0, base_card(), [working_card()])
        _, lost = self._tick(5, dead_card())
        _, found_again = self._tick(10, dead_card(), [working_card()])
        entry = self._entry()
        self.assertIs(True, entry["resolved"])
        self.assertEqual(lost.isoformat(), entry["link_lost_at"])
        self.assertEqual(found_again.isoformat(), entry["last_success_at"])

    def test_a_fixture_that_never_had_a_link_gets_no_lost_stamp(self):
        self._tick(0, base_card())
        self._tick(5, base_card())
        self.assertNotIn("link_lost_at", self._entry())

    def test_a_dead_link_is_hunted_across_the_slots_that_remain(self):
        self._tick(0, base_card(), [working_card()])
        reopened = []
        for minutes in (5, 10, 15, 20):
            plan, _ = self._tick(minutes, dead_card())
            reopened.append((len(plan.targets), plan.reopened_dead_link))
        self.assertEqual([(1, 1), (1, 0), (1, 0), (1, 0)], reopened,
                         "only the first of them was a reopen of a resolved entry")
        self.assertEqual(5, self._entry()["attempts"])


class TheNormalLadderIsUnaffected(unittest.TestCase):
    def test_an_unresolved_fixture_still_walks_its_slots(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder) / "targeting.json"
            card = announced_card()
            counts = []
            for minutes in (0, 5, 10):
                moment = NOW + datetime.timedelta(minutes=minutes)
                ledger = load_ledger(state)
                plan = select_targets([card], ledger, now=moment,
                                      window_minutes=WINDOW)
                counts.append(len(plan.targets))
                save_ledger(record_outcome(ledger, plan, [], now=moment), state)
            self.assertEqual([1, 1, 1], counts)
            entry = json.loads(state.read_text(encoding="utf-8"))["fixtures"][
                fixture_key(card)]
            self.assertEqual(3, entry["attempts"])
            self.assertEqual(0, sum(1 for _ in entry.get("link_lost_at", "")))

    def test_the_reason_vocabulary_did_not_change_for_unresolved_fixtures(self):
        ledger = {"fixtures": {fixture_key(base_card()): {
            "attempted": True, "attempts": 2, "resolved": False,
            "last_attempt_bucket": attempt_bucket(NOW)}}}
        key = fixture_key(base_card())
        self.assertEqual("same_bucket", retry_skip_reason(
            ledger, key, now=NOW, item=announced_card()))
        later = NOW + datetime.timedelta(minutes=5)
        self.assertEqual("", retry_skip_reason(
            ledger, key, now=later, item=announced_card()))

    def test_the_card_argument_is_optional_everywhere(self):
        """Callers that have no card still work, and get the safe answer."""
        key = fixture_key(base_card())
        self.assertEqual("resolved", retry_skip_reason(ledger_with(), key, now=NOW))
        self.assertFalse(is_retry_eligible(ledger_with(), key, now=NOW))


def today_card(**extra):
    """A promoted card: it names a route, so it can be judged."""
    card = base_card(url="https://example.test/live.m3u8", verified=True,
                     verification_status="verified_global",
                     status="LIVE_NOW", lifecycle_state="LIVE",
                     metadata_only=False, available_link_count=6)
    card.update(extra)
    return card


def dead_today_card(**extra):
    card = today_card(verification_status="failed", available_link_count=0)
    card.pop("verified", None)
    card.update(extra)
    return card


class TheTodayListIsWhereADeadStreamIsSeen(unittest.TestCase):
    """The candidate list itself: narrow on purpose."""

    def _write(self, folder, items):
        data = Path(folder) / "data"
        data.mkdir(parents=True, exist_ok=True)
        (data / "today-match.json").write_text(
            json.dumps({"type": "today_match", "count": len(items), "items": items}),
            encoding="utf-8")
        return data

    def test_the_real_today_file_offers_only_the_route_bearing_card(self):
        if not TODAY_FILE.exists():
            self.skipTest("no published today-match.json in this checkout")
        published = json.loads(TODAY_FILE.read_text(encoding="utf-8"))["items"]
        candidates = known_today_refresh_candidates(ROOT / "data")
        for card in candidates:
            with self.subTest(card=str(card.get("name"))[:40]):
                self.assertIsNot(True, card.get("metadata_only"))
        self.assertLessEqual(len(candidates), len(published))
        offered = {fixture_key(c) for c in candidates}
        for card in published:
            if card.get("metadata_only") is True:
                self.assertNotIn(fixture_key(card), offered,
                                 "an announcement card was offered for refresh")

    def test_an_announcement_card_on_today_is_not_a_candidate(self):
        """LINK UPDATING. It never had a link, so it cannot have lost one - and
        offering it here would quietly widen the ordinary ladder."""
        with tempfile.TemporaryDirectory() as folder:
            data = self._write(folder, [announced_card()])
            self.assertEqual([], known_today_refresh_candidates(data))

    def test_a_card_naming_no_route_is_not_a_candidate(self):
        with tempfile.TemporaryDirectory() as folder:
            data = self._write(folder, [base_card(status="LIVE_NOW")])
            self.assertEqual([], known_today_refresh_candidates(data))

    def test_a_finished_card_is_not_a_candidate(self):
        for field in ("status", "schedule_status", "lifecycle_state"):
            for value in ("ENDED", "FT", "FINISHED", "COMPLETED"):
                with self.subTest(field=field, value=value):
                    with tempfile.TemporaryDirectory() as folder:
                        data = self._write(folder, [dead_today_card(**{field: value})])
                        self.assertEqual([], known_today_refresh_candidates(data))

    def test_a_missing_or_unreadable_file_is_simply_no_candidates(self):
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder) / "data"
            data.mkdir()
            self.assertEqual([], known_today_refresh_candidates(data))
            (data / "today-match.json").write_text("{ not json", encoding="utf-8")
            self.assertEqual([], known_today_refresh_candidates(data))

    def test_the_same_card_twice_in_the_file_is_offered_once(self):
        with tempfile.TemporaryDirectory() as folder:
            data = self._write(folder, [dead_today_card(), dead_today_card()])
            self.assertEqual(1, len(known_today_refresh_candidates(data)))


class APromotedFixtureCanBeHuntedAgain(unittest.TestCase):
    """The eight things this wiring has to get right."""

    def _plan(self, listed, refresh, ledger, moment=NOW):
        return select_targets(listed, ledger, now=moment, window_minutes=WINDOW,
                              refresh_candidates=refresh)

    def test_1_a_healthy_today_card_is_not_a_target(self):
        plan = self._plan([], [today_card()], ledger_with())
        self.assertEqual(0, len(plan.targets))
        self.assertEqual(1, plan.refresh_considered)
        self.assertEqual(1, plan.refresh_healthy)
        self.assertEqual(1, plan.already_resolved)
        self.assertEqual(0, plan.reopened_dead_link)

    def test_2_the_same_card_with_a_dead_verdict_is_reopened(self):
        plan = self._plan([], [dead_today_card()], ledger_with())
        self.assertEqual({fixture_key(base_card())}, plan.targets)
        self.assertEqual(1, plan.reopened_dead_link)
        self.assertEqual(0, plan.refresh_healthy)

    def test_3_a_fixture_in_both_lists_is_one_target_decided_once(self):
        plan = self._plan([announced_card()], [dead_today_card()], ledger_with())
        self.assertEqual({fixture_key(base_card())}, plan.targets)
        self.assertEqual(1, len(plan.target_names), plan.target_names)
        self.assertEqual(1, plan.considered)
        self.assertEqual(1, plan.refresh_considered)
        self.assertEqual(1, plan.reopened_dead_link)

    def test_3b_the_route_bearing_card_decides_not_the_announcement(self):
        """The Upcoming card is silent about the stream, so consulting it would
        answer 'resolved, leave it alone' for a fixture that is dead."""
        plan = self._plan([announced_card()], [today_card()], ledger_with())
        self.assertEqual(0, len(plan.targets), "a healthy promoted card was hunted")
        self.assertEqual(1, plan.refresh_healthy)

    def test_4_a_card_that_never_had_a_link_is_not_reopened(self):
        """No ledger entry at all: nothing was found, so nothing was lost."""
        plan = self._plan([], [dead_today_card()], {"fixtures": {}})
        self.assertEqual(0, len(plan.targets))
        self.assertEqual(1, plan.refresh_unresolved)
        self.assertEqual(0, plan.reopened_dead_link)

    def test_4b_a_healthy_card_with_no_ledger_entry_is_not_scanned_either(self):
        """The safety that stops this becoming a scan of every live card."""
        plan = self._plan([], [today_card()], {"fixtures": {}})
        self.assertEqual(0, len(plan.targets))
        self.assertEqual(1, plan.refresh_unresolved)

    def test_5_a_refresh_costs_one_attempt_per_slot(self):
        spent = ledger_with(dict(RESOLVED, last_attempt_bucket=attempt_bucket(NOW)))
        plan = self._plan([], [dead_today_card()], spent)
        self.assertEqual(0, len(plan.targets))
        self.assertEqual(1, plan.same_bucket)
        self.assertEqual(0, plan.reopened_dead_link)

    def test_6_the_next_slot_retries(self):
        spent = ledger_with(dict(RESOLVED, last_attempt_bucket=attempt_bucket(NOW)))
        later = NOW + datetime.timedelta(minutes=5)
        plan = self._plan([], [dead_today_card()], spent, moment=later)
        self.assertEqual({fixture_key(base_card())}, plan.targets)
        self.assertEqual(1, plan.reopened_dead_link)

    def test_7_the_ordinary_ladder_still_stops_at_the_far_edge(self):
        """Unchanged: an unresolved fixture past T+10 is not a target, and no
        refresh candidate is involved in that decision."""
        past = KICKOFF + datetime.timedelta(minutes=11)
        plan = self._plan([announced_card()], [], {"fixtures": {}}, moment=past)
        self.assertEqual(0, len(plan.targets))
        self.assertEqual(1, plan.outside_window)
        self.assertEqual(0, plan.refresh_considered)

    def test_7b_a_refresh_is_deliberately_not_bound_by_the_far_edge(self):
        """A live match is hours past kickoff - the working card in the
        published data is 300 minutes past - so bounding the refresh at T+10
        would make this path unreachable, which was the fault."""
        hours_later = KICKOFF + datetime.timedelta(hours=3)
        plan = self._plan([], [dead_today_card()], ledger_with(), moment=hours_later)
        self.assertEqual({fixture_key(base_card())}, plan.targets)
        self.assertEqual(1, plan.reopened_dead_link)
        self.assertEqual(0, plan.outside_window)

    def test_a_failed_refresh_does_not_abandon_the_fixture(self):
        """Found while writing these tests. The first failed refresh writes
        `resolved: false`, and a rule asking only `is_resolved` then reads
        "nothing was ever found here" about a stream that was playing five
        minutes earlier - one attempt, then abandoned. The once-only fault
        again, wearing a new hat. The question is whether a link EVER existed."""
        after_a_failed_refresh = ledger_with({
            "attempted": True, "attempts": 2, "resolved": False,
            "first_link_at": NOW.isoformat(),
            "last_success_at": NOW.isoformat(),
            "link_lost_at": (NOW + datetime.timedelta(minutes=5)).isoformat(),
        })
        later = NOW + datetime.timedelta(minutes=10)
        plan = self._plan([], [dead_today_card()], after_a_failed_refresh,
                          moment=later)
        self.assertEqual({fixture_key(base_card())}, plan.targets)
        self.assertEqual(1, plan.reopened_dead_link)
        self.assertEqual(0, plan.refresh_unresolved)

    def test_a_fixture_with_no_link_history_at_all_is_still_left_alone(self):
        """The other side of that: `ever_had_a_link` must not become 'always'."""
        never = ledger_with({"attempted": True, "attempts": 3, "resolved": False})
        plan = self._plan([], [dead_today_card()], never)
        self.assertEqual(0, len(plan.targets))
        self.assertEqual(1, plan.refresh_unresolved)

    def test_8_the_card_data_itself_is_never_altered(self):
        listed, refresh = announced_card(), dead_today_card()
        before = (json.dumps(listed, sort_keys=True), json.dumps(refresh, sort_keys=True))
        self._plan([listed], [refresh], ledger_with())
        self.assertEqual(before, (json.dumps(listed, sort_keys=True),
                                  json.dumps(refresh, sort_keys=True)))

    def test_the_summary_reports_what_the_refresh_pass_did(self):
        plan = self._plan([], [dead_today_card()], ledger_with())
        summary = plan.summary()
        for field in ("reopened_dead_link", "refresh_candidates_considered",
                      "refresh_healthy_left_alone", "refresh_never_resolved_skipped"):
            self.assertIn(field, summary)
        self.assertEqual(1, summary["refresh_candidates_considered"])


class TheHistorySurvivesAPromotedRefresh(unittest.TestCase):
    def test_a_today_driven_refresh_keeps_every_earlier_fact(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder) / "targeting.json"
            key = fixture_key(base_card())

            first = NOW
            plan = select_targets([announced_card()], load_ledger(state),
                                  now=first, window_minutes=WINDOW)
            save_ledger(record_outcome(load_ledger(state), plan,
                                       [working_card()], now=first), state)

            lost = NOW + datetime.timedelta(minutes=5)
            plan = select_targets([], load_ledger(state), now=lost,
                                  window_minutes=WINDOW,
                                  refresh_candidates=[dead_today_card()])
            self.assertEqual({key}, plan.targets)
            save_ledger(record_outcome(load_ledger(state), plan, [], now=lost), state)

            found = NOW + datetime.timedelta(minutes=10)
            plan = select_targets([], load_ledger(state), now=found,
                                  window_minutes=WINDOW,
                                  refresh_candidates=[dead_today_card()])
            save_ledger(record_outcome(load_ledger(state), plan,
                                       [working_card()], now=found), state)

            fixtures = json.loads(state.read_text(encoding="utf-8"))["fixtures"]
            self.assertEqual([key], list(fixtures), "the refresh forked the fixture")
            entry = fixtures[key]
            self.assertEqual(3, entry["attempts"])
            self.assertEqual(first.isoformat(), entry["attempted_at"])
            self.assertEqual(first.isoformat(), entry["first_link_at"])
            self.assertEqual(found.isoformat(), entry["last_success_at"])
            self.assertEqual(lost.isoformat(), entry["link_lost_at"])
            self.assertIs(True, entry["resolved"])


class ThePlannerReadsBothFiles(unittest.TestCase):
    def test_the_today_file_reaches_the_plan_end_to_end(self):
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder) / "data"
            data.mkdir()
            (data / "upcoming.json").write_text(
                json.dumps({"items": [announced_card()]}), encoding="utf-8")
            (data / "today-match.json").write_text(
                json.dumps({"items": [dead_today_card()]}), encoding="utf-8")
            state = Path(folder) / "targeting.json"
            save_ledger(ledger_with(), state)

            plan = plan_targeted_upcoming_scan(
                data_dir=data, fixture_path=Path(folder) / "absent.json",
                state_path=state, now=NOW, window_minutes=WINDOW)

            self.assertEqual({fixture_key(base_card())}, plan.targets)
            self.assertEqual(1, plan.reopened_dead_link)
            self.assertEqual(1, plan.refresh_considered)

    def test_a_healthy_today_file_produces_no_work(self):
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder) / "data"
            data.mkdir()
            (data / "upcoming.json").write_text(
                json.dumps({"items": []}), encoding="utf-8")
            (data / "today-match.json").write_text(
                json.dumps({"items": [today_card()]}), encoding="utf-8")
            state = Path(folder) / "targeting.json"
            save_ledger(ledger_with(), state)

            plan = plan_targeted_upcoming_scan(
                data_dir=data, fixture_path=Path(folder) / "absent.json",
                state_path=state, now=NOW, window_minutes=WINDOW)

            self.assertEqual(0, len(plan.targets))
            self.assertFalse(plan.should_scan)
            self.assertEqual(1, plan.refresh_healthy)


if __name__ == "__main__":
    unittest.main()
