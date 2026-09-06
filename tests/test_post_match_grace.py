"""A finished match stops being live before it stops being listed.

FINAL_2 ধাপ ৪ asks for an intermediate state. `decide()` used to answer a
strong FT with `ENDED, publish=False` on the spot, so the card vanished the
instant a feed said the match was over - out from under whoever had just
been watching it.

    LIVE / READY
      -> END_PENDING   first genuine end signal; `ended_seen_at` written
      -> ENDED         ended_seen_at + post_match_grace_minutes has passed

The stamp is written once. A fixture that is finished on one scan is
finished on the next, and if every scan wrote its own "now" the removal
would be pushed another grace period into the future each time - the card
would never leave at all.
"""
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scanner.event_lifecycle import (                      # noqa: E402
    DEFAULT_POST_MATCH_GRACE_MINUTES, END_PENDING, ENDED, LIVE,
    LifecycleSignals, apply_verdict, decide,
)
from scanner.lifecycle_config import lifecycle_settings    # noqa: E402

UTC = timezone.utc
T0 = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
GRACE = lifecycle_settings(
    settings_path=ROOT / "config" / "settings.json")["post_match_grace_minutes"]


def signals(**extra):
    base = dict(
        authority_live=None,
        strong_end=False,
        primary_playable=True,
        backup_playable=None,
        currently_playing=False,
        estimate_passed=False,
        consecutive_non_live_scans=0,
        seen_in_this_scan=True,
    )
    base.update(extra)
    return LifecycleSignals(**base)


def finished_card(**extra):
    return dict({
        "id": "alpha-vs-beta",
        "name": "Alpha vs Beta",
        "status": "FT",
        "schedule_verified": True,
        "start_time": (T0 - timedelta(hours=2)).isoformat(),
    }, **extra)


def at(card, offset, grace=None, **signal_extra):
    return decide(
        card, signals(strong_end=True, **signal_extra),
        now=T0 + offset,
        post_match_grace_minutes=GRACE if grace is None else grace,
    )


class TheConfigDecides(unittest.TestCase):
    def test_the_grace_is_twenty_minutes_from_config(self):
        self.assertEqual(20, GRACE)

    def test_the_fallback_is_the_old_immediate_behaviour(self):
        """Zero, so a missing config retires at once exactly as before -
        rather than inventing a window nobody configured."""
        self.assertEqual(0, DEFAULT_POST_MATCH_GRACE_MINUTES)
        verdict = at(finished_card(), timedelta(0), grace=0)
        self.assertEqual(ENDED, verdict.state)

    def test_a_custom_value_moves_the_boundary(self):
        for grace in (5, 20, 45, 90):
            card = apply_verdict(
                finished_card(), at(finished_card(), timedelta(0), grace=grace))
            with self.subTest(grace=grace):
                self.assertEqual(END_PENDING, at(
                    card, timedelta(minutes=grace - 1), grace=grace).state)
                self.assertEqual(ENDED, at(
                    card, timedelta(minutes=grace), grace=grace).state)


class TheFirstEndSignalOpensTheWindow(unittest.TestCase):
    def test_a_strong_end_becomes_end_pending_not_ended(self):
        verdict = at(finished_card(), timedelta(0))
        self.assertEqual(END_PENDING, verdict.state)
        self.assertTrue(verdict.publish)

    def test_the_card_is_still_published(self):
        card = apply_verdict(finished_card(), at(finished_card(), timedelta(0)))
        self.assertEqual(END_PENDING, card["lifecycle_state"])
        self.assertTrue(card["end_pending"])

    def test_ended_seen_at_is_written(self):
        verdict = at(finished_card(), timedelta(0))
        self.assertEqual(T0.isoformat(), verdict.ended_seen_at)
        card = apply_verdict(finished_card(), verdict)
        self.assertEqual(T0.isoformat(), card["ended_seen_at"])

    def test_an_authority_saying_not_live_opens_it_too(self):
        verdict = decide(
            finished_card(), signals(authority_live=False), now=T0,
            post_match_grace_minutes=GRACE)
        self.assertEqual(END_PENDING, verdict.state)
        self.assertEqual(T0.isoformat(), verdict.ended_seen_at)

    def test_a_provider_stated_end_also_holds_rather_than_vanishing(self):
        card = {
            "id": "alpha-vs-beta",
            "schedule_verified": True,
            "end_time": (T0 - timedelta(hours=3)).isoformat(),
            "end_time_source": "provider",
            "start_time": (T0 - timedelta(hours=6)).isoformat(),
        }
        verdict = decide(card, signals(), now=T0, post_match_grace_minutes=GRACE)
        self.assertEqual(END_PENDING, verdict.state)
        self.assertTrue(verdict.publish)
        self.assertEqual(T0.isoformat(), verdict.ended_seen_at)


class TheStampIsWrittenOnce(unittest.TestCase):
    def test_a_second_scan_does_not_move_it(self):
        card = apply_verdict(finished_card(), at(finished_card(), timedelta(0)))
        for offset in (1, 5, 12, 19):
            with self.subTest(offset=offset):
                verdict = at(card, timedelta(minutes=offset))
                self.assertEqual(T0.isoformat(), verdict.ended_seen_at)

    def test_repeated_signals_cannot_hold_the_card_for_ever(self):
        """The failure this prevents: re-stamping on every scan would push
        removal another grace period away, indefinitely."""
        card = finished_card()
        for offset in range(0, 25):
            card = apply_verdict(card, at(card, timedelta(minutes=offset)))
        self.assertEqual(ENDED, card["lifecycle_state"])
        self.assertEqual(T0.isoformat(), card["ended_seen_at"])

    def test_an_unreadable_stamp_is_replaced(self):
        for junk in ("", "   ", "later", "not a time"):
            with self.subTest(junk=junk):
                verdict = at(finished_card(ended_seen_at=junk), timedelta(0))
                self.assertEqual(T0.isoformat(), verdict.ended_seen_at)

    def test_the_stamp_survives_the_card_looking_live_again(self):
        card = apply_verdict(finished_card(), at(finished_card(), timedelta(0)))
        alive = decide(card, signals(strong_end=False), now=T0 + timedelta(minutes=5),
                       post_match_grace_minutes=GRACE)
        self.assertEqual(LIVE, alive.state)
        carried = apply_verdict(card, alive)
        self.assertEqual(T0.isoformat(), carried["ended_seen_at"])
        # and when FT returns, the original window is still the one that counts
        self.assertEqual(ENDED, at(carried, timedelta(minutes=20)).state)


class TheBoundary(unittest.TestCase):
    def setUp(self):
        self.card = apply_verdict(
            finished_card(), at(finished_card(), timedelta(0)))

    def test_visible_right_up_to_the_last_second(self):
        for offset in (timedelta(0), timedelta(minutes=5),
                       timedelta(minutes=19),
                       timedelta(minutes=19, seconds=59)):
            with self.subTest(offset=str(offset)):
                verdict = at(self.card, offset)
                self.assertEqual(END_PENDING, verdict.state)
                self.assertTrue(verdict.publish)

    def test_gone_at_exactly_twenty_minutes(self):
        verdict = at(self.card, timedelta(minutes=GRACE))
        self.assertEqual(ENDED, verdict.state)
        self.assertFalse(verdict.publish)

    def test_and_stays_gone(self):
        for offset in (21, 60, 600):
            with self.subTest(offset=offset):
                self.assertEqual(ENDED, at(self.card, timedelta(minutes=offset)).state)

    def test_the_retired_card_is_not_sent_to_upcoming(self):
        card = apply_verdict(self.card, at(self.card, timedelta(minutes=GRACE)))
        self.assertEqual(ENDED, card["lifecycle_state"])
        for field in ("category", "source_pipeline", "event_type"):
            self.assertNotEqual("upcoming", card.get(field))

    def test_the_id_never_changes_through_the_whole_window(self):
        ids = {self.card["id"]}
        card = self.card
        for offset in range(0, 25, 4):
            card = apply_verdict(card, at(card, timedelta(minutes=offset)))
            ids.add(card["id"])
        self.assertEqual({"alpha-vs-beta"}, ids)


class AnEstimateIsNotAnEndSignal(unittest.TestCase):
    def test_a_sport_estimate_alone_does_not_open_the_window(self):
        card = {
            "id": "alpha-vs-beta",
            "schedule_verified": True,
            "start_time": (T0 - timedelta(hours=5)).isoformat(),
            "end_time": (T0 - timedelta(hours=1)).isoformat(),
            "end_time_source": "sport",
        }
        verdict = decide(card, signals(), now=T0, post_match_grace_minutes=GRACE)
        self.assertNotEqual(END_PENDING, verdict.state)
        self.assertEqual("", verdict.ended_seen_at)

    def test_nor_does_an_assumed_one(self):
        card = {
            "id": "alpha-vs-beta",
            "schedule_verified": True,
            "start_time": (T0 - timedelta(hours=5)).isoformat(),
            "end_time": (T0 - timedelta(hours=1)).isoformat(),
            "end_time_source": "assumed",
        }
        verdict = decide(card, signals(), now=T0, post_match_grace_minutes=GRACE)
        self.assertEqual("", verdict.ended_seen_at)

    def test_a_viewer_watching_still_outranks_everything_but_a_real_end(self):
        verdict = decide(
            finished_card(), signals(currently_playing=True, strong_end=False),
            now=T0, post_match_grace_minutes=GRACE)
        self.assertEqual(LIVE, verdict.state)


class ACardWithNothingToPlayIsNotInconclusive(unittest.TestCase):
    """The regression PROMPT 19 opened, caught on live data.

    Requiring a STATED end before `verified_end_passed` will retire a card
    was right, and it left a hole: a card with no link and no authority
    used to be caught by that rule (because `schedule_verified` is stamped
    on everything) and afterwards fell through to "no usable link verdict -
    holding, not retiring", which holds for ever.

    Measured on 2026-09-05, in a real published scan:
    `Selaqui Strikers vs Herbertpur Knight Riders` was on Today Match 33
    hours after its own kickoff, LINK_UPDATING, no link, no authority.

    The fix is a statement of fact rather than a new policy: a card that
    SAYS it has no stream, and has no route to probe, is not "we could not
    tell". It then takes the ordinary path - its estimated end must have
    passed AND several consecutive scans must agree - which is a gentler
    retirement than the immediate one it used to get.
    """

    def _protect(self, card, probe, now, **extra):
        import tempfile
        from scanner.live_protection import protect_live_events
        return protect_live_events(
            [], [card], probe=probe, now=now,
            state_path=Path(tempfile.mkdtemp()) / "state.json",
            grace_minutes=90, authority_states={},
            post_match_grace_minutes=GRACE, confirmations_required=3,
            **extra
        )

    @staticmethod
    def _stale():
        return {
            "id": "selaqui-strikers-vs-herbertpur-knight-riders",
            "name": "Selaqui Strikers vs Herbertpur Knight Riders",
            "start_time": (T0 - timedelta(hours=33)).isoformat(),
            "end_time": (T0 - timedelta(hours=29)).isoformat(),
            "status": "LINK_UPDATING",
            "schedule_status": "LINK_UPDATING",
            "schedule_verified": True,
            "metadata_only": True,
            "available_link_count": 0,
        }

    def test_it_is_retired_once_the_scans_agree(self):
        card = self._stale()
        for scan in range(1, 4):
            kept, _stats = self._protect(card, lambda _c: None, T0)
            if not kept:
                self.assertEqual(3, scan)
                return
            card = kept[0]
        self.fail("a card 33 hours past kickoff with no link was never retired")

    def test_it_is_held_while_the_scans_have_not_agreed_yet(self):
        """Not immediate - one bad scan cannot retire anything."""
        kept, _stats = self._protect(self._stale(), lambda _c: None, T0)
        self.assertEqual(1, len(kept))
        self.assertEqual(END_PENDING, kept[0]["lifecycle_state"])

    def test_a_card_still_waiting_for_its_first_link_is_safe(self):
        """Ten minutes past kickoff, LINK UPDATING, nothing found yet. Its
        estimated end has not passed, so nothing here touches it."""
        waiting = dict(self._stale(),
                       start_time=(T0 - timedelta(minutes=10)).isoformat(),
                       end_time=(T0 + timedelta(hours=4)).isoformat())
        kept, _stats = self._protect(waiting, lambda _c: None, T0)
        self.assertEqual(1, len(kept))

    def test_a_card_with_a_working_link_is_never_touched_by_this(self):
        playable = dict(self._stale(),
                        metadata_only=False, available_link_count=1,
                        playback_id="ctv_abc",
                        start_time=(T0 - timedelta(hours=1)).isoformat(),
                        end_time=(T0 + timedelta(hours=2)).isoformat(),
                        status="LIVE_NOW", schedule_status="LIVE_NOW")
        kept, _stats = self._protect(playable, lambda _c: True, T0)
        self.assertEqual(1, len(kept))
        self.assertEqual(LIVE, kept[0]["lifecycle_state"])

    def test_an_inconclusive_probe_on_a_card_that_HAS_a_route_still_holds(self):
        """The older invariant is untouched: "cannot tell" is not "dead"
        when there was something to tell about."""
        routed = dict(self._stale(),
                      metadata_only=False, playback_id="ctv_abc",
                      available_link_count=1)
        kept, stats = self._protect(routed, lambda _c: None, T0)
        self.assertEqual(1, len(kept))
        self.assertEqual(1, stats["probe_inconclusive"])


class NothingLaterIsStarted(unittest.TestCase):
    def test_no_archive_or_tombstone_exists(self):
        """PROMPT 24."""
        source = (ROOT / "scanner" / "event_lifecycle.py").read_text(encoding="utf-8")
        for token in ("archive", "tombstone", "resurrect"):
            self.assertNotIn(token, source.lower())

    def test_the_contradictory_signal_policy_is_untouched(self):
        """PROMPT 22 - `confirmations_required` still means what it did."""
        from scanner.event_lifecycle import DEFAULT_CONFIRMATIONS_REQUIRED
        self.assertEqual(3, DEFAULT_CONFIRMATIONS_REQUIRED)

    def test_currently_playing_semantics_are_untouched(self):
        """PROMPT 23."""
        verdict = decide(
            {"id": "x"}, signals(currently_playing=True), now=T0,
            post_match_grace_minutes=GRACE)
        self.assertEqual(LIVE, verdict.state)
        self.assertIn("currently_playing", verdict.protections)


class TheTargetedTriggerHonoursTheSameGrace(unittest.TestCase):
    """Both paths must agree about when a match is over.

    Measured 2026-09-06. `Alaves vs Osasuna`, 16:30 kickoff, football, end
    19:00:

        18:59:31  upcoming-targeted   published
        19:03:51  upcoming-targeted   dropped, filtered_stale=2
        19:09:17  today               published again, END_PENDING

    The full scan was holding it for exactly this grace. The trigger's
    carry-through was retiring it at end_time with none, so a card the viewer
    could still be watching flickered off the page and back four minutes later.
    """

    from scanner import events  # noqa: PLC0415 - a test module, read once

    NOW = datetime(2026, 9, 6, 19, 3, tzinfo=UTC)

    def card(self, end_offset_minutes):
        return {
            "id": "alaves-vs-osasuna",
            "name": "Alaves vs Osasuna",
            "schedule_status": "LIVE_NOW",
            "start_time": (self.NOW - timedelta(minutes=153)).isoformat(),
            "end_time": (self.NOW + timedelta(minutes=end_offset_minutes)).isoformat(),
            "url": "https://a.test/x.m3u8",
            "playback_id": "ctv_" + "a" * 32,
        }

    def fresh(self, item, grace):
        return self.events._is_today_fresh(item, self.NOW, 12, 25, grace)

    def test_without_a_grace_the_card_goes_the_moment_the_clock_says_so(self):
        """The behaviour before, kept as the default so no other caller moved."""
        self.assertFalse(self.fresh(self.card(-3), 0))

    def test_with_the_grace_it_is_still_published(self):
        self.assertTrue(self.fresh(self.card(-3), GRACE))

    def test_and_it_goes_when_the_grace_runs_out(self):
        self.assertFalse(self.fresh(self.card(-(GRACE + 1)), GRACE))

    def test_the_boundary_is_the_grace_exactly(self):
        self.assertTrue(self.fresh(self.card(-(GRACE - 1)), GRACE))
        self.assertFalse(self.fresh(self.card(-GRACE), GRACE))

    def test_an_authoritative_ended_still_goes_at_once(self):
        """The grace is for a clock running out, not for a feed saying FT."""
        item = self.card(-1)
        item["schedule_status"] = "ENDED"
        self.assertFalse(self.fresh(item, GRACE))

    def test_the_carry_through_passes_the_configured_value(self):
        source = (ROOT / "scanner" / "events.py").read_text(encoding="utf-8")
        self.assertIn('lifecycle_timings["post_match_grace_minutes"],', source)

    def test_the_default_keeps_every_other_caller_where_it_was(self):
        import inspect
        signature = inspect.signature(self.events._is_today_fresh)
        self.assertEqual(
            signature.parameters["post_match_grace_minutes"].default, 0)


if __name__ == "__main__":
    unittest.main()
