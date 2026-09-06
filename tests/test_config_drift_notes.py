"""PROMPT 41/42/43 - three settings that described a system that had changed.

FINAL_1 P2 listed them and FINAL_2 ধাপ ৮ says what to do with each:

  * `allowed_sports` named thirteen sports; two are ever published.
  * the targeted note promised "30 gives six attempts before the whistle" - a
    guarantee the code never made, and at the time the code tried once.
  * `provider_event_hours` reads as a provider's end time and is arithmetic
    done here.

None of the three is dead, so none of them is deleted. A config that lies about
what it does is worse than one that does nothing, because someone eventually
acts on it.
"""
import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scanner import schedule_resolver  # noqa: E402
from scanner import sport_filter  # noqa: E402
from scanner.events import _payload  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SETTINGS = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
EVENTS = SETTINGS["events"]
LIFECYCLE = SETTINGS["event_lifecycle"]
NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)


class AllowedSportsSaysThePolicyItEnforces(unittest.TestCase):
    def test_it_names_the_two_sports_the_tabs_publish(self):
        self.assertEqual(["cricket", "football"], EVENTS["allowed_sports"])

    def test_it_is_a_live_gate_and_not_a_leftover(self):
        """It is the last thing a card passes before it is published, so it had
        to be aligned rather than removed."""
        items = [
            {"id": "a", "name": "Genoa Vs Como", "sport_type": "football"},
            {"id": "b", "name": "India Vs Pakistan", "sport_type": "cricket"},
            {"id": "c", "name": "Nadal Vs Alcaraz", "sport_type": "tennis"},
        ]
        payload = _payload([dict(item) for item in items], "today_match", 0, 0,
                           allowed_sports=EVENTS["allowed_sports"])
        self.assertEqual({"a", "b"}, {item["id"] for item in payload["items"]})

    def test_narrowing_it_cannot_drop_anything_the_filter_kept(self):
        """`sport_filter.apply` stamps sport_type on every card it keeps, and
        it only ever keeps cricket and football - so the narrowed list is a
        second guard, not a new way to lose a fixture.

        The assertion is about whatever `apply` keeps, not about a particular
        fixture surviving: its last stage consults a live fixture lookup, so
        naming two cards and demanding they publish makes this test answer to
        the network rather than to the gate it is checking.
        """
        cards = [
            {"id": "1", "name": "Genoa Vs Como", "competition": "Italian Serie A",
             "sport_type": "other"},
            {"id": "2", "name": "India Vs Pakistan", "competition": "Asia Cup",
             "sport_type": ""},
            {"id": "3", "name": "Chelsea vs Arsenal",
             "competition": "English Premier League", "sport_type": ""},
        ]
        kept, _ = sport_filter.apply([dict(card) for card in cards])
        for card in kept:
            self.assertIn(card["sport_type"], EVENTS["allowed_sports"],
                          card.get("name"))
        payload = _payload(kept, "today_match", 0, 0,
                           allowed_sports=EVENTS["allowed_sports"])
        self.assertEqual(len(kept), payload["count"],
                         "the narrowed list dropped a card the filter kept")

    def test_the_sport_filter_is_still_the_authority(self):
        """The list says which established sports may reach a tab. It cannot
        promote anything the filter refused."""
        kept, report = sport_filter.apply([
            {"id": "1", "name": "Las Vegas Aces Vs Seattle Storm",
             "competition": "WNBA", "sport_type": "cricket"},
        ])
        self.assertEqual([], kept)
        self.assertTrue(report["rejected"] or report["quarantined"])

    def test_the_note_explains_what_it_is_for(self):
        note = EVENTS["allowed_sports_note"]
        self.assertIn("_payload", note)
        self.assertIn("sport_filter", note)


class TheRetryNoteDescribesTheLadderThatExists(unittest.TestCase):
    def test_no_file_still_promises_a_fixed_number_of_attempts(self):
        for path in [ROOT / "scan.py"] + sorted(
                list((ROOT / "scanner").rglob("*.py"))
                + list((ROOT / "config").rglob("*.json"))):
            text = path.read_text(encoding="utf-8").casefold()
            for claim in ("six attempts", "gives six", "eight attempts"):
                with self.subTest(file=path.name, claim=claim):
                    self.assertNotIn(claim, text)

    def test_the_note_lives_with_the_numbers_it_describes(self):
        note = LIFECYCLE["_target_retry_note"]
        self.assertIn("move_to_today_minutes", note)
        self.assertIn("target_retry_until_min", note)
        self.assertIn("once per five-minute bucket", note)

    def test_it_states_the_count_is_a_consequence_not_a_promise(self):
        note = LIFECYCLE["_target_retry_note"].casefold()
        self.assertIn("consequence", note)
        self.assertIn("missed tick", note)

    def test_it_matches_the_values_the_ladder_actually_reads(self):
        from scanner.lifecycle_config import lifecycle_settings

        timings = lifecycle_settings(SETTINGS)
        self.assertEqual(25, timings["move_to_today_minutes"])
        self.assertEqual(5, timings["target_retry_interval_min"])
        self.assertEqual(10, timings["target_retry_until_min"])

    def test_the_two_ends_of_the_ladder_are_named_in_the_note(self):
        note = LIFECYCLE["_target_retry_note"]
        self.assertIn("before kickoff", note)
        self.assertIn("after it", note)


class ProviderEventHoursStopsClaimingProvenance(unittest.TestCase):
    def test_the_value_is_untouched(self):
        self.assertEqual(4, EVENTS["provider_event_hours"])
        self.assertEqual(4, schedule_resolver.DEFAULT_PROVIDER_EVENT_HOURS)

    def test_it_is_still_reached_and_still_marks_itself_assumed(self):
        """A fixture with no stated end and no known sport length."""
        resolved = schedule_resolver._provider_fixture_item(
            {"name": "Some Club Vs Another Club", "status": "UPCOMING",
             "source_id": "srhady-bingstream"},
            NOW + timedelta(hours=2), NOW, 4,
        )
        self.assertIsNotNone(resolved)
        self.assertEqual("assumed", resolved["end_time_source"])
        start = datetime.fromisoformat(resolved["start_time"])
        end = datetime.fromisoformat(resolved["end_time"])
        self.assertEqual(240, int((end - start).total_seconds() // 60))

    def test_a_stated_end_and_a_known_sport_both_still_win(self):
        stated = schedule_resolver._provider_fixture_item(
            {"name": "A Vs B", "status": "UPCOMING",
             "source_id": "srhady-bingstream", "end_time_stated": True,
             "end_time": (NOW + timedelta(hours=5)).isoformat()},
            NOW + timedelta(hours=2), NOW, 4,
        )
        self.assertEqual("provider", stated["end_time_source"])

        sport = schedule_resolver._provider_fixture_item(
            {"name": "India Vs Pakistan", "competition": "Asia Cup T20",
             "status": "UPCOMING", "source_id": "srhady-bingstream"},
            NOW + timedelta(hours=2), NOW, 4,
        )
        self.assertEqual("sport", sport["end_time_source"])

    def test_the_note_and_the_comment_both_say_assumed(self):
        note = EVENTS["provider_event_hours_note"].casefold()
        self.assertIn("assumed", note)
        self.assertIn("no provider states this", note)
        comment = (ROOT / "scanner" / "schedule_resolver.py").read_text(
            encoding="utf-8").split("DEFAULT_PROVIDER_EVENT_HOURS = 4", 1)[0]
        self.assertIn("generic assumed fallback", comment[-700:])

    def test_nothing_calls_it_a_provider_stated_end_any_more(self):
        for path in ((ROOT / "config" / "settings.json"),
                     (ROOT / "scanner" / "schedule_resolver.py")):
            text = path.read_text(encoding="utf-8").casefold()
            self.assertNotIn("provider states the end", text)


if __name__ == "__main__":
    unittest.main()
