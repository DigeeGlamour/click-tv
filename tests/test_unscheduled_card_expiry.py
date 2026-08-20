"""A Today Match card with no schedule must not be carried forever.

`estimate_passed` is measured from the card's own end time. A today_match
source that names a channel but no kickoff produces a "CHANNEL_LIVE" fallback
card with neither a start nor an end, so `estimated_end` returned None,
`estimate_passed` was permanently False, and `decide`'s multi-signal retirement
path - which requires `estimate_passed and every link dead and N confirmations`
- could never be reached at any miss count.

Measured on production data, 2026-08-20T07:15 (run #583):

    Batman Petrolspor vs Boluspor 1 Lig    225 misses  2.5 days  probe dead
    FCSB vs FC Botosani Liga I             225 misses  2.5 days  probe dead
    Sao Caetano vs Santo Andre Copa Paul.  225 misses  2.5 days  probe dead
    Palestino vs Huachipato Primera Div.   203 misses  2.2 days  probe dead
    Shanghai Shenhua vs Beijing Guoan      157 misses  1.8 days  probe dead
    Atlanta United II vs Chicago Fire II   137 misses  1.6 days  probe dead

All six were still publishing in Today Match with verification_status
"verified_global" and a green "Verified" badge, and an independent probe of
every published stream through the playback proxy found 0 of them playable.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scanner.event_lifecycle import (
    ENDED,
    END_PENDING,
    LifecycleSignals,
    decide,
    estimate_passed,
    estimated_end,
)
from scanner.live_protection import DEFAULT_UNSCHEDULED_CARRY_HOURS, protect_live_events

NOW = datetime(2026, 8, 20, 7, 30, tzinfo=timezone.utc)


def unscheduled_card(name: str, event_id: str) -> dict:
    """A bingstream-style fallback card: named channel, no kickoff at all."""
    return {
        "id": event_id,
        "name": name,
        "status": "CHANNEL_LIVE",
        "schedule_status": "CHANNEL_LIVE",
        "start_time": None,
        "end_time": None,
        "today_source_channel": True,
        "source_id": "srhady-bingstream-live",
        "verification_status": "verified_global",
        "verified": True,
        "publish_allowed": True,
    }


class NoScheduleIsNoClockTests(unittest.TestCase):
    """The original defect, stated directly against `decide`."""

    def test_a_card_with_no_schedule_has_no_estimated_end(self):
        card = unscheduled_card("Batman Petrolspor vs Boluspor 1 Lig", "bp-vs-b")
        self.assertIsNone(estimated_end(card))
        self.assertFalse(estimate_passed(card, NOW, 90))

    def test_without_the_clock_signal_no_miss_count_ever_retires_the_card(self):
        card = unscheduled_card("Batman Petrolspor vs Boluspor 1 Lig", "bp-vs-b")
        for misses in (3, 50, 225, 10_000):
            with self.subTest(misses=misses):
                verdict = decide(
                    card,
                    LifecycleSignals(
                        primary_playable=False,
                        backup_playable=False,
                        estimate_passed=False,
                        consecutive_non_live_scans=misses,
                    ),
                    now=NOW,
                    confirmations_required=3,
                )
                self.assertEqual(verdict.state, END_PENDING)
                self.assertTrue(verdict.publish)

    def test_with_the_clock_signal_supplied_the_same_card_retires(self):
        card = unscheduled_card("Batman Petrolspor vs Boluspor 1 Lig", "bp-vs-b")
        verdict = decide(
            card,
            LifecycleSignals(
                primary_playable=False,
                backup_playable=False,
                estimate_passed=True,
                consecutive_non_live_scans=3,
            ),
            now=NOW,
            confirmations_required=3,
        )
        self.assertEqual(verdict.state, ENDED)
        self.assertFalse(verdict.publish)


class UnscheduledCarryExpiryTests(unittest.TestCase):
    """The fallback clock, exercised through `protect_live_events` itself."""

    def setUp(self):
        self.state_path = Path(tempfile.mkdtemp()) / "live-event-protection.json"

    def _run(self, previous, misses, *, dead=True, hours=None, now=NOW):
        self.state_path.write_text(
            json.dumps({"updated_at": now.isoformat(), "misses": misses}),
            encoding="utf-8",
        )
        kwargs = {}
        if hours is not None:
            kwargs["unscheduled_carry_hours"] = hours
        return protect_live_events(
            [],
            previous,
            probe=lambda card: not dead,
            state_path=self.state_path,
            now=now,
            authority_states={},
            playing_event_ids=set(),
            **kwargs,
        )

    def test_a_long_dead_unscheduled_card_is_retired(self):
        card = unscheduled_card("Batman Petrolspor vs Boluspor 1 Lig", "bp-vs-b")
        misses = {
            "bp-vs-b": {
                "count": 225,
                "first_missed_at": "2026-08-17T20:15:39.726032+00:00",
                "last_probe": "dead",
                "lifecycle_state": "END_PENDING",
                "name": card["name"],
            }
        }
        items, stats = self._run([card], misses)
        self.assertEqual(items, [])
        self.assertEqual(stats["released_unscheduled_expired"], 1)
        self.assertEqual(stats["lifecycle_states"].get(ENDED), 1)

    def test_a_freshly_missed_unscheduled_card_is_still_carried(self):
        card = unscheduled_card("Reading vs Wycombe EFL Trophy", "rw")
        misses = {
            "rw": {
                "count": 1,
                "first_missed_at": (NOW - timedelta(minutes=20)).isoformat(),
                "last_probe": "dead",
                "lifecycle_state": "END_PENDING",
                "name": card["name"],
            }
        }
        items, stats = self._run([card], misses)
        self.assertEqual(len(items), 1)
        self.assertEqual(stats["released_unscheduled_expired"], 0)
        self.assertEqual(stats["carried_forward"], 1)

    def test_the_very_first_miss_never_expires_immediately(self):
        card = unscheduled_card("Reading vs Wycombe EFL Trophy", "rw")
        items, stats = self._run([card], {})
        self.assertEqual(len(items), 1)
        self.assertEqual(stats["released_unscheduled_expired"], 0)

    def test_a_playable_unscheduled_card_is_kept_however_old(self):
        """The fallback is a clock, not a verdict. Still-live protection wins."""
        card = unscheduled_card("Shanghai Shenhua vs Beijing Guoan", "ssbg")
        misses = {
            "ssbg": {
                "count": 157,
                "first_missed_at": "2026-08-18T13:11:00+00:00",
                "last_probe": "alive",
                "lifecycle_state": "LIVE",
                "name": card["name"],
            }
        }
        items, stats = self._run([card], misses, dead=False)
        self.assertEqual(len(items), 1)
        self.assertEqual(stats["released_unscheduled_expired"], 0)
        self.assertEqual(stats["probe_alive"], 1)

    def test_a_viewer_watching_it_keeps_it_however_old(self):
        card = unscheduled_card("Batman Petrolspor vs Boluspor 1 Lig", "bp-vs-b")
        misses = {
            "bp-vs-b": {
                "count": 225,
                "first_missed_at": "2026-08-17T20:15:39+00:00",
                "last_probe": "dead",
                "lifecycle_state": "END_PENDING",
                "name": card["name"],
            }
        }
        self.state_path.write_text(
            json.dumps({"updated_at": NOW.isoformat(), "misses": misses}),
            encoding="utf-8",
        )
        items, stats = protect_live_events(
            [], [card], probe=lambda card: False,
            state_path=self.state_path, now=NOW,
            authority_states={}, playing_event_ids={"bp-vs-b"},
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(stats["protected_playing"], 1)

    def test_a_scheduled_card_inside_its_grace_is_untouched_by_the_fallback(self):
        """A real end time 50 minutes ago is still inside the 90-minute grace."""
        card = {
            "id": "lag-vs-sj",
            "name": "Los Angeles Galaxy vs San Jose Earthquakes",
            "status": "LIVE_NOW",
            "schedule_status": "LIVE_NOW",
            "start_time": "2026-08-20T02:30:00+00:00",
            "end_time": "2026-08-20T06:30:00+00:00",
            "source_id": "srhady-bingstream-live",
        }
        misses = {
            "lag-vs-sj": {
                "count": 12,
                "first_missed_at": "2026-08-20T04:37:00+00:00",
                "last_probe": "dead",
                "lifecycle_state": "END_PENDING",
                "name": card["name"],
            }
        }
        items, stats = self._run([card], misses)
        self.assertEqual(len(items), 1)
        self.assertEqual(stats["released_unscheduled_expired"], 0)

    def test_the_carry_window_is_configurable(self):
        card = unscheduled_card("Reading vs Wycombe EFL Trophy", "rw")
        misses = {
            "rw": {
                "count": 30,
                "first_missed_at": (NOW - timedelta(hours=2)).isoformat(),
                "last_probe": "dead",
                "lifecycle_state": "END_PENDING",
                "name": card["name"],
            }
        }
        kept, _ = self._run([card], misses, hours=6)
        self.assertEqual(len(kept), 1, "6h window must still carry a 2h-old miss")
        gone, stats = self._run([card], misses, hours=1)
        self.assertEqual(gone, [], "1h window must retire a 2h-old miss")
        self.assertEqual(stats["released_unscheduled_expired"], 1)


class LifecycleSettingsAreReadTests(unittest.TestCase):
    """settings.event_lifecycle used to be declared but never read."""

    def setUp(self):
        self.settings = json.loads(
            (ROOT / "config" / "settings.json").read_text(encoding="utf-8")
        )

    def test_settings_declare_the_carry_window(self):
        lifecycle = self.settings["event_lifecycle"]
        self.assertIn("unscheduled_carry_hours", lifecycle)
        self.assertGreaterEqual(lifecycle["unscheduled_carry_hours"], 1)

    def test_events_passes_all_three_lifecycle_values_through(self):
        source = (ROOT / "scanner" / "events.py").read_text(encoding="utf-8")
        for argument in (
            "grace_minutes=",
            "confirmations_required=",
            "unscheduled_carry_hours=",
        ):
            with self.subTest(argument=argument):
                self.assertIn(argument, source)

    def test_the_default_matches_the_documented_window(self):
        self.assertEqual(DEFAULT_UNSCHEDULED_CARRY_HOURS, 3)


if __name__ == "__main__":
    unittest.main()
