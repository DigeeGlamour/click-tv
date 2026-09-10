"""A card no source lists any more is bounded by a clock, not by a tab filter.

PROMPT 10. A card present in a scan goes through `_admit_to_today`; a card
that has vanished from every source is carried by `protect_live_events` and
met no clock at all. `decide()` path 2c - the estimate bound - is gated on
`seen_in_this_scan`, deliberately, because absence may be an outage; and the
only thing that ever removed an absent card was `events._is_today_fresh` on
the targeted path. That is a tab filter, and it runs on the one scan mode
that publishes nothing at all when it has no fixture to chase: 312 of 341
targeted runs took that exit.

Measured on the published history of `data/today-match.json`, per appearance
so that a return never counts as carry:

    before the targeted filter existed   262.3h max, 301 cards past 24h
    with it in force                      15.8h max, 3 of 342 past the
                                          configured 12h, none past 24h
    across the catalogue freeze           41.7h, when nothing swept at all

and the reason published on almost every long one was
`still live: primary_playable, backup_playable`. A route proves the LINK, not
the match - a channel that broadcasts all day answers a probe for ever.

So absence gets the same estimate bound, with the absence itself bounded by
`confirmations_required` consecutive scans of it. These tests pin the
narrowness as hard as the bound: what it retires is the CARD, it says nothing
about the FIXTURE, and one missed fetch can never reach it.
"""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scanner.event_archive import (                        # noqa: E402
    NEVER_ARCHIVED_STATUSES, archive_entry, archive_refusal,
    terminal_provenance,
)
from scanner.event_lifecycle import (                      # noqa: E402
    END_PENDING, ENDED, LIVE, LifecycleSignals, apply_verdict, decide,
    estimate_passed,
)

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)


def _absent(**overrides):
    """A carried football card whose sport estimate expired hours ago."""
    card = {
        "id": "alpha-vs-beta",
        "fixture_id": "fixture-alpha-beta",
        "name": "Alpha FC vs Beta FC",
        "sport_type": "football",
        "participants": ["Alpha FC", "Beta FC"],
        "start_time": (NOW - timedelta(hours=9)).isoformat(),
        "end_time": (NOW - timedelta(hours=6, minutes=30)).isoformat(),
        "end_time_source": "sport",
        "schedule_verified": True,
        "status": "LIVE_NOW",
        "schedule_status": "LIVE_NOW",
        "lifecycle_state": LIVE,
    }
    card.update(overrides)
    return card


def _signals(**overrides):
    """What `protect_live_events` passes for an absent card whose links still
    answer: the case the bound exists for."""
    values = dict(
        authority_live=None,
        strong_end=False,
        primary_playable=True,
        backup_playable=True,
        currently_playing=False,
        estimate_passed=True,
        consecutive_non_live_scans=3,
        seen_in_this_scan=False,
    )
    values.update(overrides)
    return LifecycleSignals(**values)


def _verdict(card=None, now=NOW, grace=20, **signal_overrides):
    return decide(card if card is not None else _absent(),
                  _signals(**signal_overrides), now=now,
                  confirmations_required=3, post_match_grace_minutes=grace)


class ALivePlaybackRouteIsNotALiveMatch(unittest.TestCase):
    """The class the measurement found: 301 cards past 24 hours, held by a
    probe."""

    def test_a_playable_route_no_longer_holds_an_absent_card_for_ever(self):
        verdict = _verdict()
        self.assertEqual(END_PENDING, verdict.state)
        self.assertNotIn("primary_playable", verdict.protections)

    def test_the_grace_runs_and_then_the_card_goes(self):
        card = _absent(ended_seen_at=(NOW - timedelta(hours=1)).isoformat())
        self.assertEqual(ENDED, _verdict(card).state)

    def test_it_is_the_listing_that_expired_and_the_reason_says_so(self):
        card = _absent(ended_seen_at=(NOW - timedelta(hours=1)).isoformat())
        self.assertIn("listing expired", _verdict(card).reason)

    def test_a_dead_route_reaches_the_same_answer_by_its_own_path(self):
        """The multi-signal path already handled this and is untouched."""
        card = _absent(ended_seen_at=(NOW - timedelta(hours=1)).isoformat())
        verdict = _verdict(card, primary_playable=False, backup_playable=False)
        self.assertEqual(ENDED, verdict.state)


class AbsenceIsStillNotEvidenceOnItsOwn(unittest.TestCase):
    """Requirement 6. The gate on 2c was protecting this, and it still is."""

    def test_one_missed_fetch_changes_nothing(self):
        verdict = _verdict(consecutive_non_live_scans=1)
        self.assertEqual(LIVE, verdict.state)
        self.assertIn("primary_playable", verdict.protections)

    def test_two_missed_fetches_change_nothing(self):
        self.assertEqual(LIVE, _verdict(consecutive_non_live_scans=2).state)

    def test_the_third_is_the_one_that_counts(self):
        self.assertEqual(END_PENDING,
                         _verdict(consecutive_non_live_scans=3).state)

    def test_the_confirmation_count_is_the_configured_one(self):
        """Not a number of its own: the same `confirmations_required` the
        multi-signal path uses."""
        verdict = decide(_absent(), _signals(consecutive_non_live_scans=5),
                         now=NOW, confirmations_required=9,
                         post_match_grace_minutes=20)
        self.assertEqual(LIVE, verdict.state)

    def test_a_card_that_comes_back_is_not_bounded_by_this_at_all(self):
        """A returning card is `seen_in_this_scan`, and 2c owns that case."""
        verdict = _verdict(seen_in_this_scan=True, consecutive_non_live_scans=0)
        self.assertEqual(END_PENDING, verdict.state)
        self.assertTrue(verdict.estimated_only)

    def test_an_absent_card_inside_its_estimate_is_kept(self):
        card = _absent(
            start_time=(NOW - timedelta(minutes=30)).isoformat(),
            end_time=(NOW + timedelta(hours=2)).isoformat(),
        )
        self.assertFalse(estimate_passed(card, NOW))
        self.assertEqual(LIVE, _verdict(card, estimate_passed=False).state)


class AnAuthorityStillOutranksTheClock(unittest.TestCase):
    def test_an_authority_calling_it_live_vetoes_the_bound(self):
        verdict = _verdict(authority_live=True)
        self.assertEqual(LIVE, verdict.state)
        self.assertIn("authority_live", verdict.protections)

    def test_an_authority_calling_it_finished_ends_it_on_its_own_terms(self):
        verdict = _verdict(authority_live=False)
        self.assertEqual(END_PENDING, verdict.state)
        self.assertFalse(verdict.estimated_only)

    def test_a_provider_stated_end_is_not_relabelled_as_an_estimate(self):
        card = _absent(end_time_source="provider",
                       end_time=(NOW - timedelta(hours=2)).isoformat())
        verdict = _verdict(card)
        self.assertEqual(END_PENDING, verdict.state)
        self.assertFalse(verdict.estimated_only)

    def test_a_viewer_watching_holds_the_card(self):
        self.assertEqual(LIVE, _verdict(currently_playing=True).state)


class TheFixtureIsNeverDeclaredFinished(unittest.TestCase):
    """The whole difference between "our clock ran out" and "it is over"."""

    def test_the_verdict_says_it_was_only_an_estimate(self):
        card = _absent(ended_seen_at=(NOW - timedelta(hours=1)).isoformat())
        self.assertTrue(_verdict(card).estimated_only)

    def test_the_card_records_the_basis_as_an_estimate(self):
        card = _absent(ended_seen_at=(NOW - timedelta(hours=1)).isoformat())
        stamped = apply_verdict(card, _verdict(card))
        self.assertEqual("estimate", stamped.get("lifecycle_end_basis"))

    def test_the_archive_refuses_to_remember_it(self):
        card = _absent(ended_seen_at=(NOW - timedelta(hours=1)).isoformat())
        stamped = apply_verdict(card, _verdict(card))
        self.assertEqual("", terminal_provenance(stamped, now=NOW,
                                                 post_match_grace_minutes=20))

    def test_so_the_same_fixture_can_come_back(self):
        """Nothing is written that a later scan would have to argue with."""
        card = _absent(ended_seen_at=(NOW - timedelta(hours=1)).isoformat())
        stamped = apply_verdict(card, _verdict(card))
        entry = archive_entry(stamped, NOW, provenance="",
                              post_match_grace_minutes=20)
        self.assertEqual("", entry["terminal_provenance"])

    def test_the_fixture_id_is_not_touched(self):
        card = _absent(ended_seen_at=(NOW - timedelta(hours=1)).isoformat())
        stamped = apply_verdict(card, _verdict(card))
        self.assertEqual("fixture-alpha-beta", stamped.get("fixture_id"))
        self.assertNotIn("previous_event_id", stamped)


class AMultiDayFixtureIsNotEndedByOneDay(unittest.TestCase):
    def test_a_test_match_inside_its_days_estimate_is_kept(self):
        card = _absent(
            sport_type="cricket", competition="1st Test",
            name="Sri Lanka vs India 1st Test",
            start_time=(NOW - timedelta(hours=4)).isoformat(),
            end_time=None, end_time_source=None,
        )
        self.assertFalse(estimate_passed(card, NOW))
        self.assertEqual(LIVE, _verdict(card, estimate_passed=False).state)

    def test_and_when_its_listing_does_expire_nothing_is_recorded(self):
        """So day two is not barred by day one - the archive holds no row."""
        card = _absent(
            sport_type="cricket", competition="1st Test",
            name="Sri Lanka vs India 1st Test",
            ended_seen_at=(NOW - timedelta(hours=1)).isoformat(),
        )
        stamped = apply_verdict(card, _verdict(card))
        self.assertTrue(_verdict(card).estimated_only)
        self.assertEqual("", terminal_provenance(stamped, now=NOW,
                                                 post_match_grace_minutes=20))


class APostponedOrSuspendedFixtureKeepsItsFuture(unittest.TestCase):
    """Preserved as this system means it: never archived, never recorded as a
    finish, free to be listed again. Kept on the tab for ever is the fault
    being fixed, not the protection.
    """

    def test_postponed_and_suspended_are_never_archived(self):
        for status in sorted(NEVER_ARCHIVED_STATUSES):
            card = _absent(authority_status=status,
                           ended_seen_at=(NOW - timedelta(hours=1)).isoformat())
            self.assertTrue(archive_refusal(card), status)

    def test_a_postponed_fixture_is_a_strong_end_not_an_estimate(self):
        card = _absent(authority_status="POSTPONED",
                       ended_seen_at=(NOW - timedelta(hours=1)).isoformat())
        verdict = _verdict(card, strong_end=True)
        self.assertEqual(ENDED, verdict.state)
        self.assertFalse(verdict.estimated_only)

    def test_a_suspended_listing_that_expires_is_still_not_a_finish(self):
        card = _absent(authority_status="SUSPENDED",
                       ended_seen_at=(NOW - timedelta(hours=1)).isoformat())
        stamped = apply_verdict(card, _verdict(card))
        self.assertTrue(archive_refusal(stamped))
        self.assertEqual("", terminal_provenance(stamped, now=NOW,
                                                 post_match_grace_minutes=20))


class NothingElseMoved(unittest.TestCase):
    def test_a_seen_card_still_takes_the_path_it_took_before(self):
        verdict = _verdict(seen_in_this_scan=True)
        self.assertEqual(END_PENDING, verdict.state)
        self.assertIn("estimated end has long passed", verdict.reason)

    def test_the_bound_is_below_a_provider_end_in_the_order_of_authority(self):
        card = _absent(end_time_source="provider",
                       end_time=(NOW - timedelta(hours=3)).isoformat(),
                       ended_seen_at=(NOW - timedelta(hours=1)).isoformat())
        self.assertIn("verified end time", _verdict(card).reason)

    def test_a_strong_end_still_wins_outright(self):
        card = _absent(ended_seen_at=(NOW - timedelta(hours=1)).isoformat())
        verdict = _verdict(card, strong_end=True)
        self.assertEqual("authoritative finished status", verdict.reason)

    def test_an_upcoming_card_is_not_reached_by_any_of_this(self):
        card = _absent(
            start_time=(NOW + timedelta(hours=3)).isoformat(),
            end_time=(NOW + timedelta(hours=5)).isoformat(),
            lifecycle_state="UPCOMING",
        )
        self.assertEqual(LIVE, _verdict(card, estimate_passed=False).state)


class TheWholeCarryPathEndToEnd(unittest.TestCase):
    """Through the real `protect_live_events`, not through `decide()` alone -
    because the bound is only worth anything if the signals the carry path
    actually builds reach it."""

    def _protect(self, card, probe, now, scans=1):
        import tempfile
        from scanner.live_protection import protect_live_events
        state = Path(tempfile.mkdtemp()) / "state.json"
        kept, stats = [card], {}
        for _ in range(scans):
            if not kept:
                break
            kept, stats = protect_live_events(
                [], kept, probe=probe, now=now, state_path=state,
                grace_minutes=90, authority_states={},
                post_match_grace_minutes=20, confirmations_required=3,
            )
        return kept, stats

    @staticmethod
    def _playable_but_absent():
        """The measured class: absent from every feed, route still answering,
        sport estimate hours past."""
        return {
            "id": "gamma-vs-delta",
            "fixture_id": "fixture-gamma-delta",
            "name": "Gamma FC vs Delta FC",
            "sport_type": "football",
            "start_time": (NOW - timedelta(hours=9)).isoformat(),
            "end_time": (NOW - timedelta(hours=6, minutes=30)).isoformat(),
            "end_time_source": "sport",
            "schedule_verified": True,
            "status": "LIVE_NOW",
            "schedule_status": "LIVE_NOW",
            "playback_id": "ctv_gamma",
            "available_link_count": 1,
        }

    def test_a_route_that_answers_holds_it_for_two_scans_and_no_more(self):
        kept, _stats = self._protect(
            self._playable_but_absent(), lambda _c: True, NOW, scans=2)
        self.assertEqual(1, len(kept))
        self.assertEqual(LIVE, kept[0]["lifecycle_state"])

    def test_the_third_scan_moves_it_to_end_pending_on_the_estimate(self):
        kept, _stats = self._protect(
            self._playable_but_absent(), lambda _c: True, NOW, scans=3)
        self.assertEqual(1, len(kept))
        self.assertEqual(END_PENDING, kept[0]["lifecycle_state"])
        self.assertEqual("estimate", kept[0].get("lifecycle_end_basis"))

    def test_and_the_grace_takes_it_off_the_tab(self):
        card = self._playable_but_absent()
        kept, _stats = self._protect(card, lambda _c: True, NOW, scans=3)
        later = NOW + timedelta(minutes=25)
        kept, stats = self._protect(kept[0], lambda _c: True, later)
        self.assertEqual([], kept)
        self.assertEqual(1, stats["released_listing_expired"])

    def test_the_departure_is_counted_apart_from_a_real_finish(self):
        """`released_ended` means an authority or a feed said so. A listing
        that expired must never be found there."""
        card = self._playable_but_absent()
        kept, _stats = self._protect(card, lambda _c: True, NOW, scans=3)
        _kept, stats = self._protect(
            kept[0], lambda _c: True, NOW + timedelta(minutes=25))
        self.assertEqual(0, stats["released_ended"])
        self.assertEqual([], stats["released_ended_ids"])

    def test_nothing_downstream_is_told_a_fixture_finished(self):
        """The full scan records `retired_terminal`, not the card, so this is
        the list that had to be kept clean."""
        card = self._playable_but_absent()
        kept, _stats = self._protect(card, lambda _c: True, NOW, scans=3)
        _kept, stats = self._protect(
            kept[0], lambda _c: True, NOW + timedelta(minutes=25))
        self.assertEqual([], stats["retired_terminal"])

    def test_a_real_finish_on_the_same_path_is_still_recorded(self):
        """The withholding is about the estimate, not about the path. An FT
        holds its own post-match grace first, like every other end."""
        card = dict(self._playable_but_absent(), authority_status="FT")
        kept, stats = self._protect(card, lambda _c: True, NOW)
        self.assertEqual(END_PENDING, kept[0]["lifecycle_state"])
        self.assertNotIn("lifecycle_end_basis", kept[0])
        _kept, stats = self._protect(
            kept[0], lambda _c: True, NOW + timedelta(minutes=25))
        self.assertEqual(1, len(stats["retired_terminal"]))
        self.assertEqual("feed_strong_end",
                         stats["retired_terminal"][0]["provenance"])
        self.assertEqual(0, stats["released_listing_expired"])

    def test_a_fixture_still_inside_its_estimate_survives_every_scan(self):
        """The guard that keeps a real match on the tab: nothing about a
        missed fetch shortens a fixture that has not run its length."""
        card = dict(self._playable_but_absent(),
                    start_time=(NOW - timedelta(minutes=20)).isoformat(),
                    end_time=(NOW + timedelta(hours=2)).isoformat())
        kept, _stats = self._protect(card, lambda _c: True, NOW, scans=6)
        self.assertEqual(1, len(kept))
        self.assertEqual(LIVE, kept[0]["lifecycle_state"])

    def test_a_card_a_source_lists_again_is_never_carried_at_all(self):
        import tempfile
        from scanner.live_protection import protect_live_events
        card = self._playable_but_absent()
        kept, stats = protect_live_events(
            [card], [card], probe=lambda _c: True, now=NOW,
            state_path=Path(tempfile.mkdtemp()) / "state.json",
            grace_minutes=90, authority_states={},
            post_match_grace_minutes=20, confirmations_required=3)
        self.assertEqual(1, len(kept))
        self.assertEqual(0, stats["carried_forward"])


class WhatTheRecordKeepingIsToldAboutIt(unittest.TestCase):
    """The retirement is real; the finish is not. Both have to be true at
    once, and the ordering in `terminal_provenance` is where that is decided."""

    @staticmethod
    def _retired_on_the_estimate():
        card = _absent(ended_seen_at=(NOW - timedelta(hours=1)).isoformat())
        return apply_verdict(card, _verdict(card))

    def test_our_own_ended_stamp_is_not_evidence_when_it_rests_on_a_guess(self):
        card = self._retired_on_the_estimate()
        self.assertEqual(ENDED, card["lifecycle_state"])
        self.assertEqual("", terminal_provenance(
            card, now=NOW, post_match_grace_minutes=20))

    def test_a_real_authority_end_on_the_same_card_still_counts(self):
        """The ordering skips OUR stamp when it rests on a guess. It must not
        skip somebody else's word on the same card."""
        card = dict(self._retired_on_the_estimate(),
                    authority_status="FINISHED")
        self.assertEqual("estimate", card["lifecycle_end_basis"])
        self.assertEqual("authority_finished", terminal_provenance(
            card, now=NOW, post_match_grace_minutes=20))

    def test_a_feeds_own_full_time_on_the_same_card_still_counts(self):
        card = dict(self._retired_on_the_estimate(), fixture_status="FT")
        self.assertEqual("estimate", card["lifecycle_end_basis"])
        self.assertEqual("feed_ft", terminal_provenance(
            card, now=NOW, post_match_grace_minutes=20))

    def test_a_provider_stated_end_on_the_same_card_still_counts(self):
        card = dict(self._retired_on_the_estimate(),
                    end_time_source="provider",
                    end_time=(NOW - timedelta(hours=3)).isoformat())
        self.assertEqual("estimate", card["lifecycle_end_basis"])
        self.assertEqual("provider_end_time", terminal_provenance(
            card, now=NOW, post_match_grace_minutes=20))

    def test_and_once_a_real_end_arrives_the_basis_is_cleared_anyway(self):
        """`apply_verdict` drops the basis for any verdict that is not
        estimate-only, so the lifecycle stamp becomes evidence again."""
        card = self._retired_on_the_estimate()
        real = apply_verdict(card, _verdict(card, strong_end=True))
        self.assertNotIn("lifecycle_end_basis", real)
        self.assertEqual("lifecycle_ended", terminal_provenance(
            real, now=NOW, post_match_grace_minutes=20))

    def test_a_targeted_run_cannot_turn_this_into_a_terminal_record(self):
        """A targeted trigger records what a full scan already decided. An
        estimate is not a decision it may record."""
        from scanner.event_archive import archive_retired
        card = self._retired_on_the_estimate()
        import tempfile
        stats = archive_retired(
            [card], now=NOW, post_match_grace_minutes=20,
            path=Path(tempfile.mkdtemp()) / "event-archive.json")
        self.assertEqual(0, stats["added"])

    def test_but_a_retirement_a_full_scan_decided_is_still_recorded(self):
        from scanner.event_archive import archive_retired
        import tempfile
        card = _absent(authority_status="FT",
                       ended_seen_at=(NOW - timedelta(hours=1)).isoformat(),
                       lifecycle_state=ENDED)
        stats = archive_retired(
            [card], now=NOW, post_match_grace_minutes=20,
            path=Path(tempfile.mkdtemp()) / "event-archive.json")
        self.assertEqual(1, stats["added"])


if __name__ == "__main__":
    unittest.main()
