"""A finished match leaves Today Match at once, not when a timer runs down.

Section 21 retires a card immediately on an authoritative "finished" and waits
out a clock otherwise. Until now the only authority was the source playlists
themselves, so a match none of them mentioned any more sat in Today Match until
the clock expired. Measured on production at 2026-08-20T09:56: "Portland
Timbers vs San Diego FC" was published as live while TheSportsDB had it at
FT 3-1, and 12 of the 15 published cards had no verdict from any source in the
scan at all.

The sports API is already called every scan for artwork, and the same
searchevents response carries `strStatus`. It is read as an authority now, with
three deliberate limits:

  - Only cards this scan has no verdict for are asked about, so the source
    playlists always win and a busy scan spends nothing here.
  - Only a positive finished verdict is returned. A missing fixture, a rate
    limit, an unrecognised status and "still playing" are all the same thing to
    the caller: no verdict, existing behaviour unchanged.
  - A finished verdict is cached because it never becomes untrue; a
    not-finished one is not, because that is the one that changes.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scanner import events
from scanner.sports_poster_providers import (
    THESPORTSDB_DEAD_STATUSES,
    THESPORTSDB_FINISHED_STATUSES,
    _read_event_status,
)


def card(event_id, name):
    return {
        "id": event_id,
        "name": name,
        "status": "LIVE_NOW",
        "schedule_status": "LIVE_NOW",
        "source_id": "srhady-bingstream-live",
    }


class ReadingTheProvidersStatusTests(unittest.TestCase):
    def test_full_time_is_finished(self):
        verdict = _read_event_status({
            "strEvent": "Portland Timbers vs San Diego FC",
            "strStatus": "FT", "strPostponed": "no",
            "intHomeScore": "3", "intAwayScore": "1",
        })
        self.assertTrue(verdict["finished"])
        self.assertFalse(verdict["unplayable"])
        self.assertEqual(verdict["home_score"], "3")

    def test_every_finished_vocabulary_word_counts(self):
        for status in sorted(THESPORTSDB_FINISHED_STATUSES):
            with self.subTest(status=status):
                self.assertTrue(
                    _read_event_status({"strStatus": status})["finished"]
                )

    def test_not_started_is_not_finished(self):
        verdict = _read_event_status({"strStatus": "NS", "strPostponed": "no"})
        self.assertFalse(verdict["finished"])
        self.assertFalse(verdict["unplayable"])

    def test_a_postponed_or_cancelled_fixture_is_unplayable_not_finished(self):
        for status in sorted(THESPORTSDB_DEAD_STATUSES):
            with self.subTest(status=status):
                verdict = _read_event_status({"strStatus": status})
                self.assertTrue(verdict["unplayable"])
                self.assertFalse(verdict["finished"])

    def test_the_postponed_flag_alone_is_enough(self):
        verdict = _read_event_status({"strStatus": "", "strPostponed": "yes"})
        self.assertTrue(verdict["unplayable"])

    def test_an_empty_response_yields_no_verdict(self):
        self.assertEqual(_read_event_status({}), {})
        self.assertEqual(_read_event_status(None), {})
        self.assertEqual(_read_event_status({"strStatus": "", "strPostponed": "no"}), {})

    def test_an_unknown_status_is_reported_as_not_finished_rather_than_guessed(self):
        verdict = _read_event_status({"strStatus": "1H"})
        self.assertFalse(verdict["finished"])
        self.assertFalse(verdict["unplayable"])


class FixtureFinishedStatesTests(unittest.TestCase):
    def setUp(self):
        self.cache_path = Path(tempfile.mkdtemp()) / "fixture-status-cache.json"

    def _run(self, previous, known, statuses):
        calls = []

        def fake_status(home, away):
            calls.append((home, away))
            return statuses.get(f"{home}|{away}", {})

        with mock.patch(
            "scanner.sports_poster_providers.thesportsdb_event_status", fake_status
        ), mock.patch(
            "scanner.sports_poster_providers.provider_is_rate_limited",
            lambda url: False,
        ):
            verdicts, stats = events._fixture_finished_states(
                previous, known, cache_path=self.cache_path
            )
        return verdicts, stats, calls

    def test_a_finished_fixture_gets_an_authority_false(self):
        previous = [card("ptvsd", "Portland Timbers vs San Diego FC")]
        verdicts, stats, _ = self._run(
            previous, {},
            {"portland timbers|san diego fc": {"finished": True, "status": "FT",
                                               "home_score": "3", "away_score": "1"}},
        )
        self.assertEqual(verdicts, {"ptvsd": False})
        self.assertEqual(stats["finished"], 1)

    def test_a_postponed_fixture_also_retires(self):
        previous = [card("x", "Alpha vs Beta")]
        verdicts, stats, _ = self._run(
            previous, {},
            {"alpha|beta": {"finished": False, "unplayable": True, "status": "PST"}},
        )
        self.assertEqual(verdicts, {"x": False})
        self.assertEqual(stats["unplayable"], 1)

    def test_a_fixture_this_scan_already_answered_for_is_not_asked_about(self):
        previous = [card("ptvsd", "Portland Timbers vs San Diego FC")]
        verdicts, stats, calls = self._run(previous, {"ptvsd": True}, {})
        self.assertEqual(verdicts, {})
        self.assertEqual(calls, [])
        self.assertEqual(stats["asked"], 0)

    def test_a_source_verdict_of_false_is_also_left_alone(self):
        previous = [card("ptvsd", "Portland Timbers vs San Diego FC")]
        _, stats, calls = self._run(previous, {"ptvsd": False}, {})
        self.assertEqual(calls, [])
        self.assertEqual(stats["asked"], 0)

    def test_a_still_playing_fixture_gets_no_verdict(self):
        previous = [card("x", "Alpha vs Beta")]
        verdicts, stats, _ = self._run(
            previous, {}, {"alpha|beta": {"finished": False, "unplayable": False,
                                          "status": "NS"}},
        )
        self.assertEqual(verdicts, {})
        self.assertEqual(stats["no_verdict"], 1)

    def test_a_channel_card_with_no_two_sides_is_skipped(self):
        previous = [card("bing", "Bingstream"), card("ts", "T Sports")]
        verdicts, stats, calls = self._run(previous, {}, {})
        self.assertEqual(verdicts, {})
        self.assertEqual(calls, [])
        self.assertEqual(stats["asked"], 0)

    def test_a_finished_verdict_is_cached_and_reused_without_a_request(self):
        previous = [card("x", "Alpha vs Beta")]
        statuses = {"alpha|beta": {"finished": True, "status": "FT"}}
        verdicts, stats, calls = self._run(previous, {}, statuses)
        self.assertEqual(verdicts, {"x": False})
        self.assertEqual(len(calls), 1)

        verdicts, stats, calls = self._run(previous, {}, {})
        self.assertEqual(verdicts, {"x": False}, "the cache must still answer")
        self.assertEqual(calls, [], "and it must not ask again")
        self.assertEqual(stats["cache_hits"], 1)

    def test_a_not_finished_verdict_is_never_cached(self):
        previous = [card("x", "Alpha vs Beta")]
        self._run(previous, {}, {"alpha|beta": {"finished": False, "unplayable": False}})
        cache = json.loads(self.cache_path.read_text(encoding="utf-8")) \
            if self.cache_path.exists() else {"entries": {}}
        self.assertNotIn("alpha|beta", cache.get("entries", {}))

    def test_a_rate_limited_provider_is_not_asked_at_all(self):
        previous = [card("x", "Alpha vs Beta")]
        calls = []

        def fake_status(home, away):
            calls.append((home, away))
            return {"finished": True}

        with mock.patch(
            "scanner.sports_poster_providers.thesportsdb_event_status", fake_status
        ), mock.patch(
            "scanner.sports_poster_providers.provider_is_rate_limited",
            lambda url: True,
        ):
            verdicts, stats = events._fixture_finished_states(
                previous, {}, cache_path=self.cache_path
            )
        self.assertEqual(calls, [])
        self.assertEqual(verdicts, {})
        self.assertEqual(stats["no_verdict"], 1)

    def test_a_provider_exception_never_breaks_the_scan(self):
        previous = [card("x", "Alpha vs Beta")]

        def boom(home, away):
            raise RuntimeError("provider down")

        with mock.patch(
            "scanner.sports_poster_providers.thesportsdb_event_status", boom
        ), mock.patch(
            "scanner.sports_poster_providers.provider_is_rate_limited",
            lambda url: False,
        ):
            verdicts, stats = events._fixture_finished_states(
                previous, {}, cache_path=self.cache_path
            )
        self.assertEqual(verdicts, {})
        self.assertEqual(stats["no_verdict"], 1)

    def test_no_previous_cards_means_no_work(self):
        verdicts, stats = events._fixture_finished_states([], {}, cache_path=self.cache_path)
        self.assertEqual(verdicts, {})
        self.assertEqual(stats["asked"], 0)


class RetirementUsesTheVerdictImmediatelyTests(unittest.TestCase):
    """An authority "finished" must not wait for the unscheduled clock."""

    def test_authority_false_retires_a_schedule_less_card_at_once(self):
        from datetime import datetime, timezone

        from scanner.live_protection import protect_live_events

        now = datetime(2026, 8, 20, 10, 0, tzinfo=timezone.utc)
        state_path = Path(tempfile.mkdtemp()) / "protection.json"
        state_path.write_text(
            json.dumps({"updated_at": now.isoformat(), "misses": {}}), encoding="utf-8"
        )
        stuck = {
            "id": "fcsb", "name": "FCSB vs FC Botosani Liga I",
            "status": "CHANNEL_LIVE", "schedule_status": "CHANNEL_LIVE",
            "start_time": None, "end_time": None, "today_source_channel": True,
            "source_id": "srhady-bingstream-live",
        }
        items, stats = protect_live_events(
            [], [stuck], probe=lambda c: True,
            state_path=state_path, now=now,
            authority_states={"fcsb": False}, playing_event_ids=set(),
        )
        self.assertEqual(items, [], "a finished fixture goes even with a live probe")
        self.assertEqual(stats["released_ended"], 1)
        self.assertEqual(stats["released_unscheduled_expired"], 0)


class WiringTests(unittest.TestCase):
    def setUp(self):
        self.source = (ROOT / "scanner" / "events.py").read_text(encoding="utf-8")

    def test_the_combiner_is_used_at_the_protection_call_site(self):
        self.assertIn("authority_states=_event_authority_with_api(", self.source)

    def test_the_source_playlists_are_applied_before_the_api(self):
        combiner = self.source.split("def _event_authority_with_api(", 1)[1]
        combiner = combiner.split("def _playing_event_ids(", 1)[0]
        self.assertLess(
            combiner.index("_authority_states(candidates, previous_items)"),
            combiner.index("_fixture_finished_states("),
        )
        self.assertIn("states.update(api_states)", combiner)

    def test_the_stats_are_reported(self):
        self.assertIn('stats["fixture_status_api"] = api_stats', self.source)

    def test_the_cache_lives_under_state(self):
        self.assertEqual(events.FIXTURE_STATUS_CACHE_PATH.parent.name, "state")
        self.assertEqual(
            events.FIXTURE_STATUS_CACHE_PATH.name, "fixture-status-cache.json"
        )


if __name__ == "__main__":
    unittest.main()
