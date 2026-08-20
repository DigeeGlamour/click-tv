"""Fixtures come from the feeds that publish fixtures; routing comes from status.

Two architectural mistakes emptied both event tabs:

* `config/event-fixtures.json` holds a handful of hand-written competitions and
  was the *only* accepted proof that a match exists, so 414 of 444 candidates
  were suppressed - every AX Sports football fixture among them. AX Sports and
  Willow publish real fixture lists (participants, competition, kickoff, status)
  and are now authoritative in their own right.

* Today Match vs Upcoming was decided by which source file an entry came from.
  A live fixture configured under "upcoming" was therefore filed as Upcoming and
  then dropped for having started in the past, which is how
  `Sri Lanka vs India 1st Test` and its five working streams disappeared from
  both tabs. Routing now reads the schedule status.
"""

import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner.events import _destination_for  # noqa: E402
from scanner.parsers.json_parser import _normalize_status  # noqa: E402
from scanner.planner import _pipeline_for_mode  # noqa: E402
from scanner.schedule_resolver import (  # noqa: E402
    DEFAULT_FIXTURE_AUTHORITY_SOURCES,
    _parse_source_time,
    _zone,
    enrich_event_candidates,
)

AUTHORITY = "srhady-axsports-upcoming"
NOW = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)


def _candidate(**overrides) -> dict:
    item = {
        "id": "fixture-1",
        "name": "Arsenal vs Manchester City",
        "competition": "Premier League",
        "source_id": AUTHORITY,
        "source_pipeline": "upcoming",
        "allow_without_stream": True,
        "url": "https://stream.test/live.m3u8",
        "verification_status": "verified_global",
        "verified": True,
        "publish_allowed": True,
        "status": "UPCOMING",
        "start_time": (NOW + timedelta(hours=3)).isoformat(),
    }
    item.update(overrides)
    return item


def _enrich(items):
    """Enrich against an empty local catalogue.

    The real config/event-fixtures.json would also emit its own metadata cards
    for every listed competition, which says nothing about whether the provider
    path works. An empty catalogue isolates exactly that.
    """
    return enrich_event_candidates(
        items,
        fixture_path=str(ROOT / "tests" / "fixtures" / "empty-event-fixtures.json"),
        timezone_name="Asia/Dhaka",
        now=NOW,
        future_days=120,
        authority_source_ids={AUTHORITY},
    )


class StatusNormalisationTests(unittest.TestCase):
    def test_in_play_period_codes_read_as_live(self):
        for code in ("1H", "2H", "HT", "ET", "BT", "P", "LIVE"):
            self.assertEqual(_normalize_status(code), "LIVE", code)

    def test_not_started_codes_read_as_upcoming(self):
        for code in ("NS", "Not Started", "SCHEDULED", "UPCOMING"):
            self.assertEqual(_normalize_status(code), "UPCOMING", code)

    def test_finished_codes_read_as_completed(self):
        for code in ("FT", "AET", "PEN", "FINISHED", "ENDED"):
            self.assertEqual(_normalize_status(code), "COMPLETED", code)

    def test_postponed_is_not_confused_with_upcoming(self):
        """A postponed or abandoned fixture has no usable kickoff, so it must
        never reach the Upcoming tab as if it were scheduled."""
        for code in ("PST", "TBD", "CANC", "ABD"):
            self.assertEqual(_normalize_status(code), "UNSCHEDULED", code)


class KickoffTimeParsingTests(unittest.TestCase):
    def test_the_clock_before_the_date_is_understood(self):
        """AX Sports writes "12:30 AM 17-08-2026". Every one of its fixtures was
        discarded while this format was unreadable."""
        zone = _zone("Asia/Dhaka", "Asia/Dhaka", "+06:00")
        parsed = _parse_source_time("12:30 AM 17-08-2026", zone, NOW)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed, datetime(2026, 8, 16, 18, 30, tzinfo=timezone.utc))

    def test_the_date_before_the_clock_still_works(self):
        zone = _zone("Asia/Dhaka", "Asia/Dhaka", "+06:00")
        self.assertIsNotNone(_parse_source_time("17-08-2026 12:30 AM", zone, NOW))
        self.assertIsNotNone(_parse_source_time("2026-08-17T00:30:00+06:00", zone, NOW))


class FixtureAuthorityTests(unittest.TestCase):
    def test_the_two_feeds_from_the_guide_are_authoritative_by_default(self):
        self.assertEqual(
            DEFAULT_FIXTURE_AUTHORITY_SOURCES,
            frozenset({"srhady-axsports-upcoming", "srhady-willow-event-upcoming"}),
        )

    def test_an_authority_fixture_publishes_without_the_local_catalogue(self):
        enriched, stats = _enrich([_candidate()])
        self.assertEqual(stats["provider_fixture"], 1)
        self.assertEqual(stats["unverified_suppressed"], 0)
        self.assertEqual(len(enriched), 1)
        self.assertEqual(enriched[0]["schedule_status"], "UPCOMING")
        self.assertTrue(enriched[0]["schedule_verified"])

    def test_a_stream_only_source_still_cannot_invent_a_fixture(self):
        """Guide 30.1: stream playlists say how to play, not what exists."""
        enriched, stats = _enrich([
            _candidate(source_id="srhady-bingstream-live", source_pipeline="today_match")
        ])
        self.assertEqual(stats["provider_fixture"], 0)
        self.assertEqual(stats["unverified_suppressed"], 1)
        for item in enriched:
            self.assertNotEqual(item.get("schedule_status"), "UPCOMING")

    def test_an_in_play_fixture_is_live_even_if_it_started_hours_ago(self):
        """A Test day or a long race must not read as ended just because a
        guessed four-hour window has elapsed; the feed says it is playing."""
        enriched, _ = _enrich([
            _candidate(status="LIVE", start_time=(NOW - timedelta(hours=9)).isoformat())
        ])
        self.assertEqual(len(enriched), 1)
        self.assertEqual(enriched[0]["schedule_status"], "LIVE_NOW")

    def test_a_finished_fixture_is_not_published(self):
        for status in ("FT", "COMPLETED", "ENDED"):
            enriched, _ = _enrich([_candidate(status=status)])
            self.assertEqual(enriched, [], status)

    def test_a_not_started_fixture_whose_kickoff_passed_is_dropped_as_stale(self):
        enriched, _ = _enrich([
            _candidate(start_time=(NOW - timedelta(hours=30)).isoformat())
        ])
        self.assertEqual(enriched, [])

    def test_a_fixture_without_a_kickoff_time_is_not_placed_on_the_timeline(self):
        enriched, stats = _enrich([_candidate(start_time="")])
        self.assertEqual(enriched, [])
        self.assertEqual(stats["provider_rejected"], 1)

    def test_a_pending_fixture_keeps_its_card_but_loses_its_dead_link(self):
        """`allow_without_stream`: a match that has not started carries a
        placeholder link that rightly fails verification. The fixture is real,
        the link is not - so the card stays and the dead URL goes."""
        enriched, _ = _enrich([
            _candidate(verification_status="failed", verified=False, publish_allowed=False)
        ])
        self.assertEqual(len(enriched), 1)
        card = enriched[0]
        self.assertNotIn("url", card)
        self.assertIs(card["metadata_only"], True)
        self.assertIs(card["publish_allowed"], True)
        self.assertEqual(card["verification_status"], "metadata_only")

    def test_same_teams_on_another_date_are_separate_fixtures(self):
        """Guide 30.9: identity is participants + competition + date, never the
        title alone, or a rematch and a multi-day Test collapse into one card."""
        enriched, _ = _enrich([
            _candidate(start_time=(NOW + timedelta(hours=3)).isoformat()),
            _candidate(id="fixture-2", start_time=(NOW + timedelta(days=4)).isoformat()),
        ])
        self.assertEqual(len({item["fixture_id"] for item in enriched}), 2)


class RoutingTests(unittest.TestCase):
    def test_a_live_event_goes_to_today_whatever_source_it_came_from(self):
        for status in ("LIVE_NOW", "LIVE", "CHANNEL_LIVE"):
            card = {"schedule_status": status, "source_pipeline": "upcoming"}
            self.assertEqual(_destination_for(card), "today_match", status)

    def test_a_not_started_event_goes_to_upcoming_whatever_source_it_came_from(self):
        for status in ("UPCOMING", "STARTING_SOON", "LINK_UPDATING"):
            card = {"schedule_status": status, "source_pipeline": "today_match"}
            self.assertEqual(_destination_for(card), "upcoming", status)

    def test_an_ended_event_is_published_nowhere(self):
        card = {"schedule_status": "ENDED", "source_pipeline": "today_match"}
        self.assertEqual(_destination_for(card), "ended")

    def test_the_source_group_is_only_used_when_no_status_resolved(self):
        card = {"source_pipeline": "today_match"}
        self.assertEqual(_destination_for(card), "today_match")

    def test_both_event_modes_plan_both_event_source_groups(self):
        """An Upcoming scan that saw only "upcoming" sources would republish
        Today Match from a partial pool and delete its live cards."""
        for mode in ("today", "upcoming"):
            self.assertEqual(
                _pipeline_for_mode(mode), {"today_match", "upcoming"}, mode
            )


class LiveProjectConfigTests(unittest.TestCase):
    def test_settings_declare_the_fixture_authority_feeds(self):
        settings = json.loads(
            (ROOT / "config" / "settings.json").read_text(encoding="utf-8")
        )
        configured = settings["events"]["fixture_authority_sources"]
        # Every event feed is an authority now. All eleven state a status per
        # record - the reason the registry no longer needs an -upcoming twin of
        # each source - so any of them can be the one that says a fixture has
        # started or finished.
        registered = {
            source["id"]
            for source in json.loads(
                (ROOT / "config" / "sources" / "today-match.json")
                .read_text(encoding="utf-8")
            )["sources"]
        }
        self.assertEqual(len(registered), 11)
        self.assertEqual(set(configured), registered)


if __name__ == "__main__":
    unittest.main()
