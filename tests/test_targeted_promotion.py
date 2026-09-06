"""A targeted trigger promotes a fixture that crossed the routing threshold.

The gap this closes, measured on real data on 2026-09-05: four fixtures
kicking off at 09:30 crossed T-25 at 09:05. The targeted trigger at 09:06
selected all four - the ladder was working - but only `Rotterdam Dockers vs
Edinburgh Castle Rockers`, the one whose link it found, was published to
Today Match. The other three stayed on Upcoming at T-24, T-19, T-14,
although `event_destination` said `today_match` for every one of them.

FINAL_2 is explicit that the crossing does not depend on a link:

    T-25, Upcoming থেকে সরে Today-তে (link থাক বা না থাক)

A targeted trigger fires every five minutes and a full scan runs twice a
day, so the trigger is usually the first thing to see a fixture cross at
all. If it does not act on that, the crossing happens hours late.
"""
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scanner import events  # noqa: E402
from scanner.event_lifecycle import event_destination  # noqa: E402
from scanner.lifecycle_config import lifecycle_settings  # noqa: E402
from scanner.targeted_scan import (  # noqa: E402
    fixture_key,
    known_today_refresh_candidates,
    ladder_candidates,
    select_targets,
    waiting_today_fixtures,
)

UTC = timezone.utc
NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)

#: What production routes on, so these tests move with the config.
_LIFECYCLE = lifecycle_settings(settings_path=ROOT / "config" / "settings.json")
ROUTING = _LIFECYCLE["move_to_today_minutes"]
GRACE = _LIFECYCLE["no_link_today_grace_minutes"]


def playable_card(minutes_to_kickoff, event_id="alpha-vs-beta", **extra):
    """What a fixture looks like once a link has been found and proven."""
    return waiting_card(
        minutes_to_kickoff,
        event_id,
        metadata_only=False,
        verification_status="verified_global",
        verified=True,
        available_link_count=1,
        url="https://a.example/%s.m3u8" % event_id,
        playback_id="ctv_%s" % event_id.replace("-", ""),
        status="LIVE_NOW",
        schedule_status="LIVE_NOW",
        **extra
    )


def waiting_card(minutes_to_kickoff, event_id="alpha-vs-beta", **extra):
    """A fixture with no link at all - LINK UPDATING, as published."""
    card = {
        "id": event_id,
        "fixture_id": "provider:%s|test league|2026-09-05" % event_id,
        "name": "%s" % event_id.replace("-vs-", " vs ").title(),
        "start_time": (
            NOW + timedelta(minutes=minutes_to_kickoff)
        ).isoformat(),
        "schedule_status": "LINK_UPDATING",
        "status": "LINK_UPDATING",
        "metadata_only": True,
        "verification_status": "metadata_only",
        "allow_without_stream": True,
        "publish_allowed": True,
        "available_link_count": 0,
        "source_pipeline": "upcoming",
        "category": "upcoming",
        "sport_type": "cricket",
        "competition": "Test League",
    }
    card.update(extra)
    return card


class TheAdmissionRuleIsSharedNotCopied(unittest.TestCase):
    """Both paths route through one function, so they cannot disagree."""

    def test_the_ordinary_scan_calls_the_shared_helper(self):
        source = (ROOT / "scanner" / "events.py").read_text(encoding="utf-8")
        self.assertEqual(
            3,
            source.count("_admit_to_today("),
            "expected the definition plus exactly the two callers",
        )

    def test_a_waiting_card_is_admitted_and_stamped(self):
        card = waiting_card(-5)
        admitted, reason, _ = events._admit_to_today(
            card,
            NOW,
            routing_minutes=ROUTING,
            no_link_grace_minutes=GRACE,
            today_max_age_hours=12,
        )
        self.assertIsNotNone(admitted)
        self.assertEqual("admitted", reason)
        self.assertEqual("today_match", admitted["category"])
        self.assertEqual("today_match", admitted["source_pipeline"])
        self.assertEqual("schedule_status_routing", admitted["routing_reason"])
        self.assertTrue(admitted["metadata_only"])
        self.assertTrue(admitted["allow_without_stream"])
        self.assertEqual("LINK_UPDATING", admitted["status"])

    def test_a_card_past_the_no_link_grace_is_refused(self):
        """Past the grace it is no longer "waiting", so the ordinary
        playability gate applies to it - which it cannot pass with no link.
        Unchanged from before: the same two lines refused it then."""
        admitted, reason, _ = events._admit_to_today(
            waiting_card(-(GRACE + 1)),
            NOW,
            routing_minutes=ROUTING,
            no_link_grace_minutes=GRACE,
            today_max_age_hours=12,
        )
        self.assertIsNone(admitted)
        self.assertEqual("unplayable", reason)

    def test_a_stale_card_is_refused_as_stale(self):
        admitted, reason, _ = events._admit_to_today(
            playable_card(-60 * 20),
            NOW,
            routing_minutes=ROUTING,
            no_link_grace_minutes=GRACE,
            today_max_age_hours=12,
        )
        self.assertIsNone(admitted)
        self.assertEqual("stale", reason)


class TheLadderKeepsHoldOfAPromotedFixture(unittest.TestCase):
    """Promotion must not end the hunt for the fixture it promoted."""

    def _data_dir(self, today, upcoming):
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (directory / "today-match.json").write_text(
            json.dumps({"items": today}), encoding="utf-8"
        )
        (directory / "upcoming.json").write_text(
            json.dumps({"items": upcoming}), encoding="utf-8"
        )
        return directory

    def test_a_waiting_today_card_is_a_ladder_candidate(self):
        directory = self._data_dir([waiting_card(-5)], [])
        waiting = waiting_today_fixtures(data_dir=directory)
        self.assertEqual(1, len(waiting))
        self.assertEqual("alpha-vs-beta", waiting[0]["id"])

    def test_a_today_card_with_a_route_is_not_one(self):
        """That card belongs to the dead-link refresh list, not this one."""
        directory = self._data_dir(
            [waiting_card(-5, metadata_only=False, playback_id="ctv_abc")], []
        )
        self.assertEqual([], waiting_today_fixtures(data_dir=directory))

    def test_the_two_today_lists_are_exact_complements(self):
        cards = [
            waiting_card(-5, event_id="no-route"),
            waiting_card(-5, event_id="has-route", metadata_only=False,
                         playback_id="ctv_abc"),
            waiting_card(-5, event_id="has-url", metadata_only=False,
                         url="https://a.example/x.m3u8"),
        ]
        directory = self._data_dir(cards, [])
        waiting = {c["id"] for c in waiting_today_fixtures(data_dir=directory)}
        refresh = {
            c["id"] for c in known_today_refresh_candidates(data_dir=directory)
        }
        self.assertEqual({"no-route"}, waiting)
        self.assertEqual({"has-route", "has-url"}, refresh)
        self.assertEqual(set(), waiting & refresh)
        self.assertEqual({c["id"] for c in cards}, waiting | refresh)

    def test_a_fixture_in_both_files_is_one_candidate(self):
        """Mid-move, a stale pair of files can list it twice."""
        directory = self._data_dir([waiting_card(-5)], [waiting_card(-5)])
        candidates = ladder_candidates(
            data_dir=directory, fixture_path=directory / "missing.json"
        )
        self.assertEqual(1, len(candidates))

    def test_the_hunt_continues_after_the_promotion(self):
        """The point of the whole exercise: promoted, still hunted."""
        promoted = waiting_card(-5)
        directory = self._data_dir([promoted], [])
        plan = select_targets(
            ladder_candidates(
                data_dir=directory, fixture_path=directory / "missing.json"
            ),
            {},
            now=NOW,
            window_minutes=ROUTING,
            after_kickoff_minutes=10,
            retry_interval_minutes=5,
            refresh_candidates=known_today_refresh_candidates(
                data_dir=directory
            ),
        )
        self.assertIn(fixture_key(promoted), plan.targets)

    def test_the_window_and_the_slot_gate_still_bound_it(self):
        promoted = waiting_card(-11)          # past the T+10 tail
        directory = self._data_dir([promoted], [])
        candidates = ladder_candidates(
            data_dir=directory, fixture_path=directory / "missing.json"
        )
        plan = select_targets(
            candidates, {}, now=NOW, window_minutes=ROUTING,
            after_kickoff_minutes=10, retry_interval_minutes=5,
        )
        self.assertEqual(set(), plan.targets)

        # And inside the window, one attempt per five-minute slot.
        inside = waiting_card(-5)
        directory = self._data_dir([inside], [])
        candidates = ladder_candidates(
            data_dir=directory, fixture_path=directory / "missing.json"
        )
        ledger = {
            "fixtures": {
                fixture_key(inside): {
                    "attempts": 1,
                    "last_attempt_bucket": "2026-09-05T12:00Z",
                }
            }
        }
        plan = select_targets(
            candidates, ledger, now=NOW, window_minutes=ROUTING,
            after_kickoff_minutes=10, retry_interval_minutes=5,
        )
        self.assertEqual(set(), plan.targets)
        self.assertEqual(1, plan.same_bucket)


class ATargetedTriggerPublishesTheCrossing(unittest.TestCase):
    """End to end through `process_events`, the way a trigger runs it."""

    def _run(self, upcoming, today=(), results=(), now=NOW, targeted_keys=None):
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (directory / "data").mkdir()
        (directory / "data" / "upcoming.json").write_text(
            json.dumps({"items": list(upcoming)}), encoding="utf-8"
        )
        (directory / "data" / "today-match.json").write_text(
            json.dumps({"items": list(today)}), encoding="utf-8"
        )
        results_path = directory / "results.json"
        results_path.write_text(
            json.dumps({"results": list(results)}), encoding="utf-8"
        )
        settings_path = directory / "settings.json"
        settings_path.write_text(json.dumps({
            "timezone": "UTC",
            "events": {"timezone": "UTC", "upcoming_future_days": 120},
            "event_lifecycle": {
                "move_to_today_minutes": ROUTING,
                "no_link_today_grace_minutes": GRACE,
            },
        }), encoding="utf-8")
        previous = os.getcwd()
        os.chdir(directory)
        try:
            return events.process_events(
                str(results_path),
                str(settings_path),
                str(directory / "fixtures.json"),
                now=now,
                targeted_window_minutes=ROUTING,
                targeted_keys=set() if targeted_keys is None else targeted_keys,
            )
        finally:
            os.chdir(previous)

    @staticmethod
    def _ids(payload, tab):
        return [str(item.get("id")) for item in payload[tab]["items"]]

    def test_before_the_threshold_it_stays_on_upcoming(self):
        result = self._run([waiting_card(ROUTING + 1)])
        self.assertEqual(["alpha-vs-beta"], self._ids(result, "upcoming"))
        self.assertEqual([], self._ids(result, "today_match"))

    def test_on_the_threshold_it_is_promoted_without_a_link(self):
        result = self._run([waiting_card(ROUTING)])
        self.assertEqual(["alpha-vs-beta"], self._ids(result, "today_match"))
        self.assertEqual([], self._ids(result, "upcoming"))

    def test_after_the_threshold_it_is_promoted_and_still_waiting(self):
        result = self._run([waiting_card(ROUTING - 1)])
        card = result["today_match"]["items"][0]
        self.assertEqual("alpha-vs-beta", card["id"])
        self.assertEqual(0, card.get("available_link_count"))
        self.assertTrue(card["metadata_only"])
        self.assertEqual("LINK_UPDATING", card["status"])
        self.assertEqual("today_match", card["category"])
        self.assertEqual("schedule_status_routing", card["routing_reason"])

    def test_the_identity_survives_the_move(self):
        before = waiting_card(ROUTING + 1)
        after = waiting_card(ROUTING - 1)
        upcoming_side = self._run([before])["upcoming"]["items"][0]
        today_side = self._run([after])["today_match"]["items"][0]
        self.assertEqual(upcoming_side["id"], today_side["id"])
        self.assertEqual(upcoming_side["fixture_id"], today_side["fixture_id"])

    def test_it_is_never_on_both_tabs(self):
        cards = [
            waiting_card(ROUTING + 5, event_id="far"),
            waiting_card(ROUTING - 1, event_id="crossed"),
            waiting_card(-5, event_id="started"),
        ]
        result = self._run(cards)
        today = self._ids(result, "today_match")
        upcoming = self._ids(result, "upcoming")
        self.assertEqual(set(), set(today) & set(upcoming))
        self.assertEqual(len(today), len(set(today)))
        self.assertEqual(len(upcoming), len(set(upcoming)))
        self.assertEqual({"crossed", "started"}, set(today))
        self.assertEqual({"far"}, set(upcoming))

    def test_a_card_already_on_today_is_not_duplicated(self):
        card = waiting_card(ROUTING - 1)
        promoted = dict(card, category="today_match",
                        source_pipeline="today_match")
        result = self._run([card], today=[promoted])
        self.assertEqual(["alpha-vs-beta"], self._ids(result, "today_match"))
        self.assertEqual([], self._ids(result, "upcoming"))

    def test_the_threshold_comes_from_the_config(self):
        """Move the one key and the crossing moves with it."""
        card = waiting_card(ROUTING + 5)
        self.assertEqual(["alpha-vs-beta"], self._ids(
            self._run([card]), "upcoming"))
        later = NOW + timedelta(minutes=6)
        self.assertEqual(["alpha-vs-beta"], self._ids(
            self._run([card], now=later), "today_match"))


class TheOrdinaryScanAndTheTriggerAgree(unittest.TestCase):
    """Requirement 8: one routing decision, not two."""

    def test_the_publish_path_routes_on_the_configured_threshold(self):
        source = (ROOT / "scanner" / "events.py").read_text(encoding="utf-8")
        self.assertIn(
            "pipeline = _destination_for(\n            card_copy, now, routing_minutes=routing_minutes\n        )",
            source,
            "the ordinary scan must route on the config value, not the default",
        )

    def test_the_trigger_asks_the_same_function(self):
        source = (ROOT / "scanner" / "events.py").read_text(encoding="utf-8")
        self.assertIn(
            "if _destination_for(\n                candidate, now, routing_minutes=routing_minutes\n            ) == \"today_match\":",
            source,
        )

    def test_they_agree_at_every_minute_around_the_threshold(self):
        for offset in range(ROUTING + 3, ROUTING - 4, -1):
            card = waiting_card(offset)
            with self.subTest(minutes_to_kickoff=offset):
                self.assertEqual(
                    "today_match" if offset <= ROUTING else "upcoming",
                    event_destination(card, NOW, routing_minutes=ROUTING),
                )

    def test_the_default_is_no_longer_what_runs(self):
        """It is 30 and production is 25; that difference is the proof."""
        self.assertEqual(30, events.DEFAULT_TODAY_ROUTING_MINUTES)
        self.assertEqual(25, ROUTING)
        card = waiting_card(28)
        self.assertEqual(
            "today_match", event_destination(card, NOW))
        self.assertEqual(
            "upcoming",
            event_destination(card, NOW, routing_minutes=ROUTING),
        )


class ALinkArrivingLaterUpdatesTheSameCard(unittest.TestCase):
    """Requirement 6, and the reason `setdefault` is not used blindly."""

    def test_the_rescanned_copy_wins_over_the_carried_one(self):
        source = (ROOT / "scanner" / "events.py").read_text(encoding="utf-8")
        self.assertIn("if event_id and event_id not in promoted:", source)

    def test_the_promoted_card_keeps_its_id_when_the_stream_arrives(self):
        waiting = waiting_card(-2)
        resolved = playable_card(-2)
        self.assertEqual(waiting["id"], resolved["id"])
        self.assertEqual(waiting["fixture_id"], resolved["fixture_id"])
        admitted, reason, _ = events._admit_to_today(
            resolved,
            NOW,
            routing_minutes=ROUTING,
            no_link_grace_minutes=GRACE,
            today_max_age_hours=12,
        )
        self.assertEqual("admitted", reason)
        self.assertEqual(waiting["id"], admitted["id"])
        self.assertEqual(1, admitted["available_link_count"])
        self.assertFalse(admitted["metadata_only"])


if __name__ == "__main__":
    unittest.main()
