"""A fixture existing and a stream playing are two different facts.

PROMPT 13. `_admit_to_today` had one escape from the playability gate,
`_routed_early_without_a_link`, and it refuses a card that HAS a route - even
a dead one - and a card whose status is live. Both describe exactly the
fixture whose stream just failed verification, so it fell through to
`_is_playable` and the whole card was dropped as "unplayable", the fixture
with it.

The Upcoming path has never done this. `schedule_resolver` line 1010 drops the
dead link and keeps the card as `metadata_only` when a not-yet-started
fixture's stream is unusable. There was no `is_live` equivalent, so one real
fixture could exist on Upcoming and vanish from Today for the same reason.

Measured over 68,567 fixture rows in the stream-health history:

    published sightings                                   54,388
      of those carrying NO working stream                 48,429
    NOT published                                         14,179
      A. real fixture, every route FAILED                  3,337   198 fixtures
      C. had a verified route, unpublished for another      1,155
      D. not recognised as a fixture at all                9,687   316 rows

Class D is `Horse Racing`, `TENNIS | EVENTO`, `Cycling`, `GOLF EVENTO`,
`DARTS PDC` - non-ball broadcasts the sport filter discards before admission,
and they must stay discarded. Class A is the fault. And publishing a card with
nothing to play is already the ordinary case, 48,429 sightings of it: deleting
the fixture was the exception.

Sources on the unpublished rows: sm-sports-data 6,399, srhady-bingstream
5,636, sm-fancode 496, srhady-primevideo-sports 457,
0matbank-trysports-football-live 292. TrySports is the proven example, not the
class - so nothing here keys on a source id.
"""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scanner.events import (                                # noqa: E402
    _admit_to_today, _fixture_inside_its_own_window, _has_any_route,
    _is_a_scheduled_fixture, _is_playable, _is_today_fresh,
    _strip_every_route,
)
from scanner.event_lifecycle import (                       # noqa: E402
    DEFAULT_TODAY_ROUTING_MINUTES, END_PENDING, event_destination,
)

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)
ROUTING = 25
NO_LINK = 25
MAX_AGE = 12
GRACE = 20


def _dead_fixture(**overrides):
    """A real football fixture, an hour in, whose only route answers 404.

    Shaped from the row the stream-health report recorded: recognised fixture,
    one candidate route, one failed route, zero verified.
    """
    card = {
        "id": "alpha-vs-beta",
        "fixture_id": "provider:alpha-vs-beta|2026-09-10",
        "name": "Alpha FC vs Beta FC",
        "sport_type": "football",
        "participants": ["Alpha FC", "Beta FC"],
        "competition": "Pro League",
        "start_time": (NOW - timedelta(minutes=60)).isoformat(),
        "end_time": (NOW + timedelta(minutes=90)).isoformat(),
        "end_time_source": "sport",
        "schedule_verified": True,
        "time_verification": "provider_feed",
        "schedule_status": "LIVE_NOW",
        "status": "LIVE_NOW",
        "url": "https://dead.test/alpha.m3u8",
        "backups": ["https://dead.test/alpha-backup.m3u8"],
        "playback_id": "ctv_alpha_dead",
        "source_ids": ["0matbank-trysports-football-live"],
        "channels": [{"name": "TrySports", "url": "https://dead.test/a.m3u8"}],
        "verification_status": "failed",
        "verified": False,
    }
    card.update(overrides)
    return card


def _admit(card, now=NOW, **kwargs):
    options = dict(routing_minutes=ROUTING, no_link_grace_minutes=NO_LINK,
                   today_max_age_hours=MAX_AGE, post_match_grace_minutes=GRACE)
    options.update(kwargs)
    return _admit_to_today(card, now, **options)


class TheFixtureSurvivesItsStream(unittest.TestCase):
    def test_a_real_fixture_with_every_route_dead_is_kept(self):
        kept, reason, _dropped = _admit(_dead_fixture())
        self.assertIsNotNone(kept)
        self.assertEqual("admitted", reason)

    def test_it_says_its_link_is_being_looked_for(self):
        kept, _reason, _dropped = _admit(_dead_fixture())
        self.assertEqual("LINK_UPDATING", kept["schedule_status"])
        self.assertEqual("LINK_UPDATING", kept["status"])

    def test_nothing_dead_is_offered_as_playback(self):
        kept, _reason, _dropped = _admit(_dead_fixture())
        self.assertFalse(_is_playable(kept))
        self.assertTrue(kept["metadata_only"])
        for field in ("url", "stream_url", "link", "final_url", "backups",
                      "standby", "playback_id"):
            self.assertNotIn(field, kept, field)

    def test_it_works_for_a_card_carrying_no_channel_list(self):
        """The preservation was accidental before this: it survived only for a
        card that happened to carry `channels`, which `_has_any_route` counts
        and `_strip_every_route` deliberately keeps."""
        card = _dead_fixture()
        card.pop("channels")
        kept, reason, _dropped = _admit(card)
        self.assertEqual("admitted", reason)
        self.assertIsNotNone(kept)

    def test_it_stays_on_today_rather_than_moving_tab(self):
        kept, _reason, _dropped = _admit(_dead_fixture())
        self.assertEqual("today_match", event_destination(
            kept, NOW, DEFAULT_TODAY_ROUTING_MINUTES))

    def test_a_fixture_before_kickoff_with_a_dead_route_is_kept_too(self):
        card = _dead_fixture(
            start_time=(NOW + timedelta(minutes=10)).isoformat(),
            end_time=(NOW + timedelta(minutes=160)).isoformat())
        _kept, reason, _dropped = _admit(card)
        self.assertEqual("admitted", reason)

    def test_no_source_id_is_named_anywhere_in_the_rule(self):
        """TrySports is the proven example, not the class."""
        source = (ROOT / "scanner" / "events.py").read_text(encoding="utf-8")
        for needle in ("trysports", "0matbank"):
            self.assertNotIn(needle, source.lower(), needle)


class AndOnlyForAsLongAsItIsAFixture(unittest.TestCase):
    """Requirement 10. The bound is the fixture's own end, which already
    exists on the card - not another timeout."""

    def test_past_its_own_end_it_goes(self):
        card = _dead_fixture(end_time=(NOW - timedelta(minutes=1)).isoformat())
        kept, reason, _dropped = _admit(card)
        self.assertIsNone(kept)
        self.assertEqual("unplayable", reason)

    def test_a_day_later_it_is_long_gone(self):
        kept, _reason, _dropped = _admit(_dead_fixture(),
                                         now=NOW + timedelta(hours=13))
        self.assertIsNone(kept)

    def test_before_it_reaches_today_at_all_it_is_not_preserved(self):
        """Two hours from kickoff is an Upcoming fixture, and Upcoming has
        always kept it. Nothing here reaches back and puts it on Today."""
        card = _dead_fixture(
            start_time=(NOW + timedelta(minutes=120)).isoformat(),
            end_time=(NOW + timedelta(minutes=270)).isoformat())
        kept, reason, _dropped = _admit(card)
        self.assertIsNone(kept)
        self.assertEqual("unplayable", reason)

    def test_the_window_is_the_cards_own_two_timestamps(self):
        self.assertTrue(_fixture_inside_its_own_window(
            _dead_fixture(), NOW, ROUTING))
        self.assertFalse(_fixture_inside_its_own_window(
            _dead_fixture(end_time=(NOW - timedelta(minutes=1)).isoformat()),
            NOW, ROUTING))

    def test_a_card_with_no_end_time_is_not_preserved(self):
        card = _dead_fixture()
        card.pop("end_time")
        self.assertFalse(_fixture_inside_its_own_window(card, NOW, ROUTING))
        kept, reason, _dropped = _admit(card)
        self.assertIsNone(kept)
        self.assertEqual("unplayable", reason)

    def test_the_age_guard_still_reaches_a_preserved_card(self):
        kept, _reason, _dropped = _admit(_dead_fixture())
        self.assertFalse(_is_today_fresh(
            kept, NOW + timedelta(hours=13), MAX_AGE, NO_LINK, GRACE))


class NotEverythingWithADeadLinkIsAFixture(unittest.TestCase):
    """Requirement 3. Class D was 9,687 unpublished sightings on 316 rows and
    must stay unpublished."""

    def test_a_channel_row_is_not_a_fixture(self):
        card = _dead_fixture(today_source_channel=True)
        self.assertFalse(_is_a_scheduled_fixture(card))
        kept, reason, _dropped = _admit(card)
        self.assertIsNone(kept)
        self.assertEqual("unplayable", reason)

    def test_an_unverified_schedule_is_not_a_fixture(self):
        card = _dead_fixture(schedule_verified=False)
        self.assertFalse(_is_a_scheduled_fixture(card))
        self.assertIsNone(_admit(card)[0])

    def test_a_row_with_no_fixture_id_is_not_a_fixture(self):
        card = _dead_fixture(fixture_id="")
        self.assertFalse(_is_a_scheduled_fixture(card))
        self.assertIsNone(_admit(card)[0])

    def test_a_real_fixture_is_one(self):
        self.assertTrue(_is_a_scheduled_fixture(_dead_fixture()))


class TheEndRulesStillWin(unittest.TestCase):
    """Requirement 6. A dead route may keep a fixture; it may not keep a
    finished one, and it may not erase an end that was decided."""

    def test_an_authority_finish_keeps_its_own_status_and_grace(self):
        card = _dead_fixture(authority_status="FINISHED",
                             schedule_status="FT", status="FT")
        kept, reason, _dropped = _admit(card)
        self.assertEqual("admitted", reason)
        self.assertEqual("FT", kept["schedule_status"])
        self.assertEqual(END_PENDING, kept["lifecycle_state"])
        self.assertTrue(kept["ended_seen_at"])

    def test_and_then_the_grace_takes_it(self):
        card = _dead_fixture(authority_status="FINISHED",
                             schedule_status="FT", status="FT")
        kept, _reason, _dropped = _admit(card)
        gone, reason, _dropped = _admit(dict(kept),
                                        now=NOW + timedelta(minutes=21))
        self.assertIsNone(gone)
        self.assertEqual("authority_finished", reason)

    def test_a_dead_route_never_becomes_a_live_claim(self):
        kept, _reason, _dropped = _admit(_dead_fixture())
        self.assertNotEqual("LIVE_NOW", kept["schedule_status"])

    def test_an_expired_estimate_still_ends_it(self):
        card = _dead_fixture(
            start_time=(NOW - timedelta(hours=8)).isoformat(),
            end_time=(NOW - timedelta(hours=5, minutes=30)).isoformat())
        kept, reason, _dropped = _admit(card)
        self.assertIsNone(kept)
        self.assertIn(reason, ("estimate_expired", "unplayable"))


class NothingAboutTheCardsIdentityIsLost(unittest.TestCase):
    def setUp(self):
        self.before = _dead_fixture()
        self.kept, _reason, _dropped = _admit(_dead_fixture())

    def test_the_fixture_id_is_unchanged(self):
        self.assertEqual(self.before["fixture_id"], self.kept["fixture_id"])
        self.assertNotIn("previous_event_id", self.kept)

    def test_the_source_ids_survive(self):
        self.assertEqual(self.before["source_ids"], self.kept["source_ids"])

    def test_the_channel_identity_survives(self):
        self.assertEqual(self.before["channels"], self.kept["channels"])

    def test_the_schedule_and_the_teams_survive(self):
        for field in ("id", "name", "participants", "competition",
                      "start_time", "end_time", "end_time_source",
                      "sport_type"):
            self.assertEqual(self.before[field], self.kept[field], field)

    def test_no_dangling_playback_id_is_published(self):
        self.assertNotIn("playback_id", self.kept)

    def test_the_strip_keeps_what_finds_the_card_again(self):
        card = _dead_fixture()
        removed = _strip_every_route(card)
        self.assertTrue(removed)
        self.assertEqual(["0matbank-trysports-football-live"],
                         card["source_ids"])
        self.assertTrue(card["channels"])
        self.assertTrue(_has_any_route(card),
                        "channels are identity, not playback")


class WhenARouteComesBack(unittest.TestCase):
    """Requirement 9. The same card gains playback; no second card appears."""

    def test_the_same_card_becomes_playable_again(self):
        kept, _reason, _dropped = _admit(_dead_fixture())
        recovered = dict(
            kept, url="https://live.test/alpha.m3u8",
            playback_id="ctv_alpha_good", verification_status="verified",
            verified=True, schedule_status="LIVE_NOW", status="LIVE_NOW")
        recovered.pop("metadata_only", None)
        recovered.pop("route_failed_while_live", None)
        again, reason, _dropped = _admit(recovered)
        self.assertEqual("admitted", reason)
        self.assertTrue(_is_playable(again))
        self.assertEqual("LIVE_NOW", again["schedule_status"])

    def test_and_it_is_the_same_card(self):
        kept, _reason, _dropped = _admit(_dead_fixture())
        recovered = dict(
            kept, url="https://live.test/alpha.m3u8",
            verification_status="verified", verified=True)
        recovered.pop("metadata_only", None)
        recovered.pop("route_failed_while_live", None)
        again, _reason, _dropped = _admit(recovered)
        self.assertEqual(_dead_fixture()["fixture_id"], again["fixture_id"])
        self.assertEqual(_dead_fixture()["id"], again["id"])

    def test_a_working_backup_alone_is_enough_to_stay_playable(self):
        card = _dead_fixture(url="", verification_status="verified",
                             verified=True)
        kept, reason, _dropped = _admit(card)
        self.assertEqual("admitted", reason)
        self.assertTrue(_is_playable(kept))
        self.assertEqual(["https://dead.test/alpha-backup.m3u8"],
                         kept["backups"])


class WhatWasAlreadyWorkingIsUntouched(unittest.TestCase):
    def test_a_verified_card_is_admitted_exactly_as_before(self):
        card = _dead_fixture(verification_status="verified", verified=True)
        kept, reason, _dropped = _admit(card)
        self.assertEqual("admitted", reason)
        self.assertEqual("LIVE_NOW", kept["schedule_status"])
        self.assertEqual("https://dead.test/alpha.m3u8", kept["url"])
        self.assertNotIn("route_failed_while_live", kept)

    def test_the_no_link_card_still_takes_its_own_path(self):
        """A fixture routed across by the clock with no route at all is the
        case `_routed_early_without_a_link` was written for, and it keeps its
        own state rather than being relabelled."""
        card = _dead_fixture(
            start_time=(NOW + timedelta(minutes=10)).isoformat(),
            end_time=(NOW + timedelta(minutes=160)).isoformat(),
            schedule_status="STARTING_SOON", status="STARTING_SOON")
        for field in ("url", "backups", "playback_id", "channels"):
            card.pop(field, None)
        kept, reason, _dropped = _admit(card)
        self.assertEqual("admitted", reason)
        self.assertEqual("STARTING_SOON", kept["schedule_status"])
        self.assertNotIn("route_failed_while_live", kept)

    def test_a_stale_card_is_still_stale(self):
        card = _dead_fixture(
            verification_status="verified", verified=True,
            start_time=(NOW - timedelta(hours=13)).isoformat(),
            end_time=(NOW + timedelta(hours=1)).isoformat())
        kept, reason, _dropped = _admit(card)
        self.assertIsNone(kept)
        self.assertEqual("stale", reason)


if __name__ == "__main__":
    unittest.main()
