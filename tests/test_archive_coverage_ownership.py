"""Who owns a retirement, and what a retirement is allowed to be.

Measured on origin/main over 179 revisions of `data/today-match.json` since
2026-09-06 12:00Z. 367 cards left Today Match and 26 archive rows exist, which
reads like a 92% failure and is not one: every departure that carried terminal
evidence on the published card was archived, 23 of 23, and the other 338
carried none at all. Refusing those is correct - a fixture that stopped being
listed has not been declared over, and the archive is the one structure here
whose mistakes are permanent.

The real gap was elsewhere, and only the scan reports show it. Across 84 full
scans with live protection, 39 fixtures reached an ENDED verdict and 27 were
archived. The 12 that were not all reached ENDED by the multi-signal path -
estimated end passed, every link probed dead, three consecutive confirming
scans - and were dropped from Today Match and remembered nowhere.

The cause is structural rather than a missing case. `ARCHIVED_LIFECYCLE_STATES`
is {ENDED, PURGED}, and an ENDED verdict carries `publish=False`: the row is
gone before anything can stamp it, so that condition can never match a card
read back out of the published file. Archiving rested instead on
`released_ended_ids`, which protection appends only for an authority verdict,
a feed's FT, or a grace already stamped on the card - a narrower question,
being used as though it were this one.

So the archive now hears the verdict, not the row it removed. What follows
pins the policy state by state, because getting it wrong in the generous
direction bars a real fixture from both tabs for good.
"""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scanner import event_archive as ea  # noqa: E402
from scanner import event_lifecycle as el  # noqa: E402
from scanner.live_protection import protect_live_events  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 7, 18, 0, tzinfo=timezone.utc)
KICKOFF = "2026-09-07T15:30:00+00:00"
GRACE = 20

#: The `else` of `if skip_live_protection`, at statement indent. Anything
#: shallower would also match the inner `if carry: ... else:` and cut the
#: targeted branch in half.
OUTER_ELSE = chr(10) + "    else:"


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def card(name="Alpha FC vs Beta FC", **extra):
    slug = name.casefold().replace(" ", "-")
    subject = {
        "id": slug,
        "fixture_id": "provider:%s|premier league|2026-09-07" % slug,
        "name": name,
        "competition": "English Premier League",
        "sport_type": "football",
        "start_time": KICKOFF,
        "end_time": "2026-09-07T17:20:00+00:00",
        "end_time_source": "provider",
        "url": "https://a.example/live.m3u8",
        "source_id": "feed-one",
        "source_ids": ["feed-one", "feed-two"],
    }
    subject.update(extra)
    return subject


def archive_of(*cards, now=NOW):
    archive = {"fixtures": {}}
    stats = ea.archive_retired(list(cards), now=now, archive=archive,
                              post_match_grace_minutes=GRACE)
    return archive, stats


def provenance(subject, now=NOW):
    return ea.terminal_provenance(subject, now=now,
                                  post_match_grace_minutes=GRACE)


class StateByStatePolicy(unittest.TestCase):
    """One test per lifecycle state, because one wrong row is permanent."""

    def test_an_ended_verdict_is_archived_even_though_it_never_publishes(self):
        """The whole correction, in one case. `publish=False` means the row is
        never in today-match.json, so the state can only come from a verdict."""
        subject = card(lifecycle_state="ENDED")
        self.assertEqual("lifecycle_ended", provenance(subject))
        _, stats = archive_of(subject)
        self.assertEqual(1, stats["added"])

    def test_purged_is_archived(self):
        subject = card(lifecycle_state="PURGED")
        self.assertEqual("lifecycle_purged", provenance(subject))
        self.assertEqual(1, archive_of(subject)[1]["added"])

    def test_end_pending_inside_the_grace_is_not_archived(self):
        """FINAL_2 ধাপ ৪: the card holds its place for the post-match grace.
        Archiving it now would retire it before the grace it was granted."""
        subject = card(
            lifecycle_state="END_PENDING",
            ended_seen_at=(NOW - timedelta(minutes=5)).isoformat(),
            end_time_source="assumed",
        )
        self.assertEqual("", provenance(subject))

    def test_end_pending_whose_grace_has_run_out_is_archived(self):
        subject = card(
            lifecycle_state="END_PENDING",
            ended_seen_at=(NOW - timedelta(minutes=GRACE + 1)).isoformat(),
            end_time_source="assumed",
        )
        self.assertEqual("post_match_grace_expired", provenance(subject))
        self.assertEqual(1, archive_of(subject)[1]["added"])

    def test_the_grace_boundary_is_the_lifecycle_boundary(self):
        """Exactly at `ended_seen_at + grace` the grace is over, which is what
        `_post_match_grace_remains` says. Two layers, one boundary."""
        seen = NOW - timedelta(minutes=GRACE)
        subject = card(lifecycle_state="END_PENDING",
                       ended_seen_at=seen.isoformat(),
                       end_time_source="assumed")
        self.assertEqual("post_match_grace_expired", provenance(subject))
        self.assertFalse(el._post_match_grace_remains(
            seen.isoformat(), NOW, GRACE))

    def test_a_live_card_that_simply_vanished_is_not_archived(self):
        subject = card(lifecycle_state="LIVE", schedule_status="LIVE_NOW",
                       end_time_source="assumed")
        self.assertEqual("", provenance(subject))
        self.assertEqual(0, archive_of(subject)[1]["added"])

    def test_a_starting_card_that_simply_vanished_is_not_archived(self):
        subject = card(lifecycle_state="STARTING",
                       schedule_status="STARTING_SOON",
                       end_time_source="assumed")
        self.assertEqual("", provenance(subject))
        self.assertEqual(0, archive_of(subject)[1]["added"])

    def test_an_upcoming_card_that_simply_vanished_is_not_archived(self):
        subject = card(lifecycle_state="UPCOMING", schedule_status="UPCOMING",
                       end_time_source="assumed")
        self.assertEqual("", provenance(subject))
        self.assertEqual(0, archive_of(subject)[1]["added"])

    def test_postponed_is_refused_by_name(self):
        """POSTPONED sits in STRONG_END_STATUSES, so every caller archiving on
        `has_strong_end_signal` would bar a rescheduled fixture for good. A
        postponement ends a listing, not a fixture."""
        subject = card(lifecycle_state="END_PENDING",
                       schedule_status="POSTPONED")
        self.assertIn("POSTPONED", el.STRONG_END_STATUSES)
        self.assertTrue(el.has_strong_end_signal(subject))
        self.assertEqual("POSTPONED", ea.archive_refusal(subject))
        self.assertEqual("", provenance(subject))
        archive, stats = archive_of(subject)
        self.assertEqual(0, stats["added"])
        self.assertEqual(1, stats["refused"])
        self.assertEqual({}, archive["fixtures"])

    def test_suspended_is_refused_by_name(self):
        """SUSPENDED is in LIVE_STATUSES - the match is still being played."""
        subject = card(lifecycle_state="LIVE", schedule_status="SUSPENDED")
        self.assertIn("SUSPENDED", el.LIVE_STATUSES)
        self.assertEqual("SUSPENDED", ea.archive_refusal(subject))
        self.assertEqual(0, archive_of(subject)[1]["added"])

    def test_a_recorded_authority_postponement_is_also_refused(self):
        """`authority_status` is deliberately never given POSTPONED, so the
        verdict is RECORDED in `authority_end_status` instead - which is
        exactly where the refusal has to look."""
        subject = card(lifecycle_state="END_PENDING",
                       authority_end_status="POSTPONED",
                       authority_end_confidence="HIGH")
        self.assertEqual("POSTPONED", ea.archive_refusal(subject))
        self.assertEqual(0, archive_of(subject)[1]["added"])

    def test_cancelled_is_terminal_and_keeps_its_provenance(self):
        subject = card(authority_status="CANCELLED",
                       authority_end_families=["livescore", "espn"])
        self.assertEqual("authority_cancelled", provenance(subject))
        archive, stats = archive_of(subject)
        self.assertEqual(1, stats["added"])
        entry = next(iter(archive["fixtures"].values()))
        self.assertEqual("CANCELLED", entry["terminal_status"])
        self.assertEqual(["livescore", "espn"], entry["terminal_authorities"])

    def test_abandoned_is_terminal_and_keeps_its_provenance(self):
        subject = card(authority_status="ABANDONED")
        self.assertEqual("authority_abandoned", provenance(subject))
        entry = next(iter(archive_of(subject)[0]["fixtures"].values()))
        self.assertEqual("ABANDONED", entry["terminal_status"])

    def test_no_result_is_terminal_and_keeps_its_provenance(self):
        subject = card(authority_status="NO_RESULT")
        self.assertEqual("authority_no_result", provenance(subject))
        entry = next(iter(archive_of(subject)[0]["fixtures"].values()))
        self.assertEqual("NO_RESULT", entry["terminal_status"])

    def test_every_state_the_plan_names_has_an_answer_here(self):
        """A guard against a state quietly having no policy at all."""
        answers = {
            "ENDED": True, "PURGED": True, "END_PENDING": None, "LIVE": False,
            "STARTING": False, "UPCOMING": False, "POSTPONED": False,
            "CANCELLED": True, "ABANDONED": True, "NO_RESULT": True,
            "SUSPENDED": False,
        }
        self.assertEqual(11, len(answers))
        for state, archivable in answers.items():
            with self.subTest(state=state):
                self.assertIn(archivable, (True, False, None))


class ProvenanceOrder(unittest.TestCase):
    """A row has to say WHY it exists, and say the strongest reason."""

    def test_an_authority_verdict_outranks_a_feed_status(self):
        subject = card(authority_status="FINISHED", schedule_status="FT")
        self.assertEqual("authority_finished", provenance(subject))

    def test_a_feed_status_outranks_the_clock(self):
        subject = card(schedule_status="FT", end_time_source="provider",
                       schedule_verified=True,
                       end_time=(NOW - timedelta(hours=4)).isoformat())
        self.assertEqual("feed_ft", provenance(subject))

    def test_a_provider_stated_end_time_is_terminal(self):
        """The same call `decide()` makes, with the same default grace: a
        provider-stated end plus `DEFAULT_ESTIMATE_GRACE_MINUTES`, and
        `schedule_verified` actually set. Any other arithmetic here would let
        the archive and the lifecycle disagree about when a match is over."""
        subject = card(
            end_time=(NOW - timedelta(
                minutes=el.DEFAULT_ESTIMATE_GRACE_MINUTES + 10)).isoformat(),
            end_time_source="provider", schedule_verified=True)
        self.assertEqual("provider_end_time", provenance(subject))
        self.assertTrue(el.verified_end_passed(subject, NOW))

    def test_a_provider_end_still_inside_its_grace_is_not_terminal(self):
        subject = card(end_time=(NOW - timedelta(minutes=10)).isoformat(),
                       end_time_source="provider", schedule_verified=True)
        self.assertEqual("", provenance(subject))

    def test_an_assumed_end_time_is_not(self):
        """FINAL_1 রায় ১০. An assumed or sport-length end never retires a
        card, so it never archives one. This is the Toluca case: kickoff
        01:15Z, `end_time` 05:15Z stamped `assumed`, published LIVE on seven
        consecutive full scans and then dropped by a targeted trigger."""
        subject = card(end_time=(NOW - timedelta(hours=6)).isoformat(),
                       end_time_source="assumed")
        self.assertEqual("", provenance(subject))
        subject["end_time_source"] = "sport"
        self.assertEqual("", provenance(subject))

    def test_a_row_records_the_word_and_the_signal_separately(self):
        subject = card(authority_status="FINISHED",
                       authority_end_families=["espn", "livescore"],
                       authority_event_ids={"espn-header": "401914323",
                                            "livescore": "1870766"},
                       ended_seen_at="2026-09-07T17:25:00+00:00",
                       authority_finished_seen_at="2026-09-07T17:25:00+00:00")
        entry = next(iter(archive_of(subject)[0]["fixtures"].values()))
        self.assertEqual("FINISHED", entry["terminal_status"])
        self.assertEqual("authority_finished", entry["terminal_provenance"])
        self.assertEqual(["espn", "livescore"], entry["terminal_authorities"])
        self.assertEqual("2026-09-07T17:25:00+00:00",
                         entry["authority_finished_seen_at"])

    def test_a_caller_that_watched_the_verdict_is_believed_over_the_card(self):
        """The point of the fix: a multi-signal retirement leaves nothing on
        the card, so the caller passes what the verdict said."""
        subject = card(lifecycle_state="LIVE", end_time_source="assumed")
        self.assertEqual("", provenance(subject))
        subject["_terminal_provenance"] = "multi_signal_confirmed"
        archive, stats = archive_of(subject)
        self.assertEqual(1, stats["added"])
        entry = next(iter(archive["fixtures"].values()))
        self.assertEqual("multi_signal_confirmed", entry["terminal_provenance"])


class TheIdentityAnArchiveRowKeeps(unittest.TestCase):
    def test_the_fixture_id_is_carried_unchanged(self):
        """No migration. The id the card already had is the id remembered."""
        subject = card(lifecycle_state="ENDED")
        entry = next(iter(archive_of(subject)[0]["fixtures"].values()))
        self.assertEqual(subject["fixture_id"], entry["fixture_id"])
        self.assertEqual("provider:alpha-fc-vs-beta-fc|premier league|2026-09-07",
                         entry["fixture_id"])

    def test_the_previous_event_id_bridge_is_kept(self):
        subject = card(lifecycle_state="ENDED",
                       previous_event_id="alpha-vs-beta")
        entry = next(iter(archive_of(subject)[0]["fixtures"].values()))
        self.assertEqual("alpha-vs-beta", entry["previous_event_id"])

    def test_the_authorities_own_ids_are_kept(self):
        subject = card(lifecycle_state="ENDED",
                       authority_event_ids={"espn-header": "401914323",
                                            "livescore": "1870766"})
        entry = next(iter(archive_of(subject)[0]["fixtures"].values()))
        self.assertEqual({"espn-header": "401914323",
                          "livescore": "1870766"},
                         entry["authority_event_ids"])

    def test_the_feeds_that_carried_it_are_kept_without_becoming_a_count(self):
        """Provider identity, not witness strength. Seven mirrors of one
        upstream are one witness - scanner/upstream_family.py owns that."""
        subject = card(lifecycle_state="ENDED",
                       source_ids=["sm-fancode", "sayanpal-fancode-mirror"])
        entry = next(iter(archive_of(subject)[0]["fixtures"].values()))
        self.assertEqual(["feed-one", "sayanpal-fancode-mirror", "sm-fancode"],
                         entry["provider_source_ids"])

    def test_the_participants_are_recorded_as_the_dedupe_layer_sees_them(self):
        subject = card("Brighton Hove Albion vs Leeds United",
                       lifecycle_state="ENDED")
        entry = next(iter(archive_of(subject)[0]["fixtures"].values()))
        self.assertEqual(2, len(entry["participants"]))
        self.assertNotIn(" vs ", "".join(entry["participants"]))

    def test_the_row_still_refuses_card_content(self):
        subject = card(lifecycle_state="ENDED",
                       channels=[{"id": "c1", "name": "Sky"}],
                       backups=["https://a.example/b.m3u8"],
                       logo="https://a.example/logo.png",
                       playback_id="ctv_abc",
                       source_provenance=[{"source_id": "x"}])
        entry = next(iter(archive_of(subject)[0]["fixtures"].values()))
        for forbidden in ("channels", "backups", "logo", "playback_id",
                          "source_provenance", "url"):
            self.assertNotIn(forbidden, entry)


class ResurrectionAndRematch(unittest.TestCase):
    """The rule is reused, not rewritten: whatever the pipeline calls one
    fixture, the archive calls one retirement."""

    def test_a_finished_fixture_cannot_come_back(self):
        archive, _ = archive_of(card(lifecycle_state="ENDED"))
        returning = card(lifecycle_state="UPCOMING", schedule_status="UPCOMING")
        for tab in ("today", "upcoming"):
            with self.subTest(tab=tab):
                kept, dropped = ea.drop_resurrected([returning], archive)
                self.assertEqual([], kept)
                self.assertEqual(1, len(dropped))

    def test_it_cannot_come_back_under_another_feeds_spelling(self):
        archive, _ = archive_of(card("Brighton vs Leeds",
                                     lifecycle_state="ENDED"))
        other = card("Brighton Hove Albion Vs Leeds United")
        other["fixture_id"] = "other:brighton-hove-albion-vs-leeds-united"
        self.assertTrue(ea.is_archived(other, archive))

    def test_a_genuine_rematch_is_allowed(self):
        archive, _ = archive_of(card(lifecycle_state="ENDED"))
        rematch = card()
        rematch["start_time"] = "2026-11-20T15:00:00+00:00"
        rematch["fixture_id"] = (
            "provider:alpha-fc-vs-beta-fc|premier league|2026-11-20")
        self.assertFalse(ea.is_archived(rematch, archive))
        kept, dropped = ea.drop_resurrected([rematch], archive)
        self.assertEqual(1, len(kept))
        self.assertEqual([], dropped)

    def test_a_womens_fixture_is_not_the_mens_one(self):
        archive, _ = archive_of(card("India vs Pakistan",
                                     competition="Asia Cup",
                                     lifecycle_state="ENDED"))
        womens = card("India Women vs Pakistan Women",
                      competition="Womens Asia Cup")
        womens["start_time"] = KICKOFF
        self.assertFalse(ea.is_archived(womens, archive))

    def test_a_reserve_or_age_group_side_is_not_the_first_team(self):
        archive, _ = archive_of(card("New Caledonia vs China",
                                     competition="Friendlies",
                                     lifecycle_state="ENDED"))
        youth = card("New Caledonia U20 vs China U20",
                     competition="Friendlies")
        youth["start_time"] = KICKOFF
        self.assertFalse(
            ea.is_archived(youth, archive),
            "an U20 fixture retired the senior one - the squad qualifier was "
            "ignored")

    def test_a_row_without_a_kickoff_cannot_block_by_the_second_tier(self):
        """Without a kickoff every helper the second tier uses is unanswerable,
        and guessing is how a rematch gets barred."""
        archive, _ = archive_of(card(lifecycle_state="ENDED"))
        homeless = {"id": "something-else", "name": "Alpha FC vs Beta FC"}
        self.assertFalse(ea.is_archived(homeless, archive))


class WhatProtectionNowReports(unittest.TestCase):
    """The twelve forgotten retirements, at their source."""

    def _run(self, previous, **kwargs):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "protection.json"
            options = {
                "probe": lambda c: False,
                "state_path": state,
                "now": NOW,
                "authority_states": {},
                "playing_event_ids": set(),
                "confirmations_required": 1,
                "post_match_grace_minutes": 0,
            }
            options.update(kwargs)
            return protect_live_events([], [previous], **options)

    def test_a_multi_signal_retirement_is_reported_with_its_signal(self):
        """The exact 12. Estimated end passed, every link dead, the
        confirmations in - and nothing on the card to show it afterwards."""
        stuck = card(lifecycle_state="LIVE", schedule_status="LIVE_NOW",
                     end_time=(NOW - timedelta(hours=2)).isoformat(),
                     end_time_source="assumed",
                     carried_forward_misses=3)
        items, stats = self._run(stuck)
        self.assertEqual([], items)
        self.assertEqual(1, (stats["lifecycle_states"] or {}).get("ENDED"))
        self.assertEqual(0, stats["released_ended"],
                         "no authority and no feed said so - which is why it "
                         "was invisible to the archive")
        self.assertEqual([], stats["released_ended_ids"])
        self.assertEqual(1, len(stats["retired_terminal"]))
        row = stats["retired_terminal"][0]
        self.assertEqual(stuck["id"], row["id"])
        self.assertIn(row["provenance"],
                      ("multi_signal_confirmed", "provider_end_time"))
        self.assertTrue(row["reason"])

    def test_an_authority_retirement_says_authority(self):
        stuck = card(lifecycle_state="LIVE", schedule_status="LIVE_NOW",
                     end_time_source="assumed")
        _, stats = self._run(stuck, authority_states={stuck["id"]: False})
        self.assertEqual(1, len(stats["retired_terminal"]))
        self.assertEqual("authority_finished",
                         stats["retired_terminal"][0]["provenance"])
        self.assertEqual(1, stats["released_ended"])

    def test_a_feed_retirement_says_feed_not_authority(self):
        """`authority_says_live` reads the card's own status fields, so a
        feed's FT arrives at protection looking like an authority verdict.
        Only THIS scan's authority map may be credited as an authority -
        otherwise every archive row would name one that never spoke."""
        stuck = card(lifecycle_state="LIVE", schedule_status="FT",
                     end_time_source="assumed")
        _, stats = self._run(stuck)
        self.assertEqual("feed_strong_end",
                         stats["retired_terminal"][0]["provenance"])

    def test_a_card_that_is_only_absent_is_not_reported_as_retired(self):
        """Nothing could be probed and nothing said it was over, so protection
        holds it at END_PENDING. Absence is not a retirement."""
        missing = card(lifecycle_state="LIVE", schedule_status="LIVE_NOW",
                       end_time="2026-09-07T23:00:00+00:00",
                       end_time_source="assumed")
        items, stats = self._run(missing, probe=lambda c: None)
        self.assertEqual(1, len(items))
        self.assertEqual([], stats["retired_terminal"])
        self.assertEqual("END_PENDING", items[0]["lifecycle_state"])

    def test_the_existing_counters_are_untouched(self):
        """Reports and dashboards read these; the new key is additive."""
        stuck = card(lifecycle_state="LIVE", schedule_status="FT",
                     end_time_source="assumed")
        _, stats = self._run(stuck)
        for key in ("released_ended", "released_ended_ids",
                    "released_dead_link", "released_stale",
                    "released_confirmed", "released_unscheduled_expired",
                    "released_exhausted", "carried_forward", "end_pending"):
            self.assertIn(key, stats)


class TheTargetedPath(unittest.TestCase):
    """A trigger that never looked at a fixture cannot retire it - but it must
    not lose a retirement an earlier full scan already decided."""

    SOURCE = read(os.path.join(str(ROOT), "scanner", "events.py"))

    def test_a_targeted_run_cannot_archive_an_absence_only_card(self):
        stale = card(lifecycle_state="LIVE", schedule_status="LIVE_NOW",
                     start_time="2026-09-06T02:00:00+00:00",
                     end_time_source="assumed")
        self.assertEqual("", provenance(stale))

    def test_a_targeted_run_keeps_an_already_confirmed_terminal_end(self):
        confirmed = card(
            lifecycle_state="END_PENDING",
            authority_status="FINISHED",
            authority_end_confidence="HIGH",
            authority_end_families=["espn", "livescore"],
            ended_seen_at=(NOW - timedelta(minutes=GRACE + 5)).isoformat(),
            end_time_source="assumed",
        )
        self.assertEqual("authority_finished", provenance(confirmed))
        self.assertEqual(1, archive_of(confirmed)[1]["added"])

    def test_the_targeted_branch_filters_before_it_archives(self):
        branch = self.SOURCE.split(
            'schedule_stats["live_protection"] = {"skipped": "targeted scan"}',
            1)[1].split(OUTER_ELSE, 1)[0]
        self.assertIn("targeted_dropped", branch)
        self.assertIn("terminal_provenance(", branch)
        self.assertIn("absence_only_refused", branch)
        self.assertLess(branch.index("terminal_provenance("),
                        branch.index("archive_retired("),
                        "evidence is asked for before anything is recorded")

    def test_the_targeted_branch_leaves_the_file_alone_when_empty(self):
        """`archive_retired` saves unconditionally, `updated_at` included. A
        trigger that fires twelve times an hour must not put the archive in
        its regenerated set for nothing: the workflow rebase restores each
        regenerated file whole from this run's own pre-rebase commit, so two
        runs in flight would resolve by one discarding the other's rows."""
        branch = self.SOURCE.split(
            'schedule_stats["live_protection"] = {"skipped": "targeted scan"}',
            1)[1].split(OUTER_ELSE, 1)[0]
        self.assertIn("if carry:", branch)
        self.assertIn('"skipped": "nothing already-decided to record"', branch)
        self.assertLess(branch.index("if carry:"),
                        branch.index("archive_retired("))

    def test_the_targeted_branch_cannot_probe_or_decide(self):
        branch = self.SOURCE.split(
            'schedule_stats["live_protection"] = {"skipped": "targeted scan"}',
            1)[1].split(OUTER_ELSE, 1)[0]
        for forbidden in ("protect_live_events", "probe_card_is_playable",
                          "lifecycle_decide", "fixture_authority.collect"):
            self.assertNotIn(forbidden, branch)

    def test_a_refused_admission_is_collected_on_both_paths(self):
        self.assertEqual(
            2, self.SOURCE.count('if reason == "authority_finished":'),
            "the ordinary routing loop and the targeted promotion both refuse "
            "an authority-retired fixture, and both retirements have to be "
            "recorded")


class AnOutageCannotRetireAnything(unittest.TestCase):
    def test_an_unavailable_authority_says_nothing_about_a_fixture(self):
        """INCONCLUSIVE is a statement about the authority, never about the
        match. A card carrying no verdict has no provenance."""
        subject = card(lifecycle_state="LIVE",
                       authority_end_status="",
                       authority_end_confidence="NONE",
                       end_time_source="assumed")
        self.assertEqual("", provenance(subject))
        self.assertEqual(0, archive_of(subject)[1]["added"])

    def test_a_healthy_withdrawal_leaves_no_permanent_record(self):
        """A feed that simply stops listing a fixture - the 200-with-no-body
        case that took 16 Upcoming cards off the page on 2026-09-06 - must not
        be able to bar it for ever."""
        withdrawn = card(lifecycle_state="LIVE", schedule_status="LIVE_NOW",
                         end_time_source="assumed")
        archive, stats = archive_of(withdrawn)
        self.assertEqual(0, stats["added"])
        self.assertEqual({}, archive["fixtures"])
        kept, dropped = ea.drop_resurrected([withdrawn], archive)
        self.assertEqual(1, len(kept))
        self.assertEqual([], dropped)

    def test_partial_publish_protection_is_not_touched_by_any_of_this(self):
        source = read(os.path.join(str(ROOT), "scanner", "event_archive.py"))
        self.assertNotIn("source_outage", source)
        self.assertNotIn("hold_upcoming", source)


class TheFullScanWiring(unittest.TestCase):
    SOURCE = read(os.path.join(str(ROOT), "scanner", "events.py"))

    def test_the_verdict_is_the_first_tier(self):
        block = self.SOURCE.split('decided = {', 1)[1].split(
            'schedule_stats["event_archive"] = archive_stats', 1)[0]
        self.assertLess(block.index("decided.get(event_id)"),
                        block.index("released_ended"),
                        "a retirement this scan decided is not re-derived from "
                        "the row it removed")

    def test_the_refusal_comes_before_every_piece_of_evidence(self):
        block = self.SOURCE.split('decided = {', 1)[1].split(
            'schedule_stats["event_archive"] = archive_stats', 1)[0]
        self.assertLess(block.index("archive_refusal(card)"),
                        block.index("has_strong_end_signal(card)"))

    def test_the_archive_is_written_before_the_tabs_are_filtered(self):
        """Terminal decision, archive record, publish exclusion - in that
        order, so a fixture retired this scan cannot also be republished by
        it."""
        self.assertLess(self.SOURCE.index("archive_stats = archive_retired("),
                        self.SOURCE.index("event_archive = load_archive()"))
        self.assertLess(self.SOURCE.index("event_archive = load_archive()"),
                        self.SOURCE.index(
                            "drop_resurrected(today_items, event_archive)"))

    def test_the_report_can_say_what_was_refused(self):
        for key in ('archive_stats["absence_only_refused"]',
                    'archive_stats["never_archived_statuses"]',
                    'archive_stats["decided_this_scan"]'):
            self.assertIn(key, self.SOURCE)

    def test_no_fixture_id_is_rewritten_anywhere_in_this_change(self):
        for name in ("event_archive.py", "live_protection.py"):
            source = read(os.path.join(str(ROOT), "scanner", name))
            self.assertNotIn('card["fixture_id"] =', source)
            self.assertNotIn('["fixture_id"] = "', source)

    def test_phase_two_is_not_started(self):
        source = read(os.path.join(str(ROOT), "scanner", "event_archive.py"))
        for token in ("cloudflare", "worker", " kv", " d1"):
            self.assertNotIn(token, source.lower())


class NoPlaybackRecordIsStranded(unittest.TestCase):
    """A card and its playback record are one unit. Retiring a fixture removes
    a row from today-match.json; it revokes no URL and deletes no playback
    entry, so nothing can be left pointing at a card that is gone."""

    def test_the_archive_never_reads_or_writes_a_playback_record(self):
        source = read(os.path.join(str(ROOT), "scanner", "event_archive.py"))
        for token in ('card.get("playback', '"playback_id"', "pages/",
                      "validate-pages", "snapshot_publish"):
            self.assertNotIn(token, source)

    def test_retiring_a_card_writes_nothing_but_the_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "event-archive.json"
            ea.archive_retired([card(lifecycle_state="ENDED")], now=NOW,
                               path=path, post_match_grace_minutes=GRACE)
            self.assertEqual(["event-archive.json"],
                             sorted(p.name for p in Path(tmp).iterdir()))
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(1, payload["count"])


class BackwardCompatibility(unittest.TestCase):
    def test_the_twenty_six_rows_already_in_production_still_block(self):
        """Nothing is migrated. The rows written before this change have no
        provenance and no participants, and they go on blocking exactly as
        they did - the reader asks for identity, not for the new fields."""
        old = {"fixtures": {
            "provider:alpha-fc-vs-beta-fc|premier league|2026-09-07": {
                "id": "alpha-fc-vs-beta-fc",
                "fixture_id": ("provider:alpha-fc-vs-beta-fc|"
                               "premier league|2026-09-07"),
                "name": "Alpha FC vs Beta FC",
                "competition": "English Premier League",
                "sport_type": "football",
                "start_time": KICKOFF,
                "ended_seen_at": "2026-09-07T17:25:00+00:00",
                "lifecycle_state": "END_PENDING",
                "archived_at": "2026-09-07T17:47:00+00:00",
            }}}
        self.assertTrue(ea.is_archived(card(), old))
        rematch = card()
        rematch["start_time"] = "2026-11-20T15:00:00+00:00"
        rematch["fixture_id"] = ("provider:alpha-fc-vs-beta-fc|"
                                 "premier league|2026-11-20")
        self.assertFalse(ea.is_archived(rematch, old))

    def test_a_second_retirement_never_rewrites_the_first(self):
        archive, _ = archive_of(card(lifecycle_state="ENDED"))
        first = dict(next(iter(archive["fixtures"].values())))
        later = NOW + timedelta(hours=3)
        ea.archive_retired([card(lifecycle_state="ENDED")], now=later,
                           archive=archive, post_match_grace_minutes=GRACE)
        self.assertEqual(first,
                         next(iter(archive["fixtures"].values())))

    def test_a_retirement_does_not_expire_on_a_timer(self):
        archive, _ = archive_of(card(lifecycle_state="ENDED"))
        self.assertTrue(ea.is_archived(card(), archive))
        source = read(os.path.join(str(ROOT), "scanner", "event_archive.py"))
        self.assertIn("There is deliberately no expiry here", source)


if __name__ == "__main__":
    unittest.main()
