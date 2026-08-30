"""The last three rules of the guide: 19, 29 and 30.8.

* 19 - a fixture spanning several days is one card. "1st Test",
  "1st Test Day 2" and "- 1st Test - Day 3" are the same match, while the 1st
  Test and the 2nd Test are not. The day labels used to sit where the fixture
  ordinal got trimmed, so day-labelled titles lost "1st"/"2nd" entirely and
  could have merged two different Tests together.

* 29 - primary selection. Verification already records response time,
  resolution, token expiry, DRM and headers, so ranking on them adds no
  request to a scan.

* 30.8 - when a fixture goes in-play it moves from Upcoming to Today Match
  reusing the card it already had, rather than appearing as a new one.
"""

import json
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner.events import _destination_for  # noqa: E402
from scanner.merger import merge_candidates, normalize_event_key  # noqa: E402
from scanner.schedule_resolver import (  # noqa: E402
    attach_streams_to_fixtures,
    enrich_event_candidates,
    reuse_published_event_ids,
)

SETTINGS = str(ROOT / "config" / "settings.json")
EMPTY_CATALOGUE = str(ROOT / "tests" / "fixtures" / "empty-event-fixtures.json")
AUTHORITY = {"srhady-axsports-upcoming"}
NOW = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)


class MultiDayIdentityTests(unittest.TestCase):
    """Guide 19 and 22."""

    def test_one_test_match_keeps_one_identity_across_its_days(self):
        keys = {normalize_event_key(name) for name in (
            "Australia vs Bangladesh 1st Test",
            "Australia vs Bangladesh 1st Test Day 2",
            "Australia vs Bangladesh - 1st Test - Day 3",
            "Day 4 - 1st Test - Australia vs Bangladesh",
            "Australia vs Bangladesh 1st Test - Session 2",
        )}
        self.assertEqual(len(keys), 1, keys)

    def test_the_first_and_second_test_never_merge(self):
        """The dangerous half of the same bug: a day-labelled title used to
        lose its ordinal, which would have folded two Tests into one card."""
        keys = {normalize_event_key(name) for name in (
            "Australia vs Bangladesh 1st Test",
            "Australia vs Bangladesh - 1st Test - Day 3",
            "Australia vs Bangladesh 2nd Test",
            "Australia vs Bangladesh - 2nd Test - Day 3",
        )}
        self.assertEqual(len(keys), 2, keys)

    def test_numbered_limited_overs_fixtures_stay_distinct(self):
        self.assertNotEqual(
            normalize_event_key("England vs Pakistan 1st ODI"),
            normalize_event_key("England vs Pakistan 3rd ODI"),
        )

    def test_day_labelled_non_cricket_events_are_one_card(self):
        self.assertEqual(
            normalize_event_key("Danish Golf Championship Day 4"),
            normalize_event_key("Danish Golf Championship Day 3"),
        )

    def test_a_multi_day_fixture_keeps_one_id_on_a_later_day(self):
        """The fixture id must not carry the calendar date for a format that
        legitimately runs past midnight, or a new card appears each morning."""
        def fixture_id(start):
            candidate = {
                "id": "raw", "name": "Australia vs Bangladesh 1st Test",
                "competition": "Test Series", "source_id": "srhady-axsports-upcoming",
                "source_pipeline": "upcoming", "allow_without_stream": True,
                "status": "NS", "start_time": start.isoformat(),
                "verification_status": "failed", "publish_allowed": False,
            }
            enriched, _ = enrich_event_candidates(
                [candidate], fixture_path=EMPTY_CATALOGUE, timezone_name="Asia/Dhaka",
                now=start - timedelta(hours=1), future_days=120,
                authority_source_ids=AUTHORITY,
            )
            return enriched[0]["fixture_id"]

        self.assertEqual(
            fixture_id(NOW + timedelta(hours=2)),
            fixture_id(NOW + timedelta(days=1, hours=2)),
        )


class PrimarySelectionTests(unittest.TestCase):
    """Guide 29."""

    def _primary(self, streams):
        base = {
            "source_pipeline": "today_match", "schedule_status": "LIVE_NOW",
            "verification_status": "verified_global", "verified": True,
            "publish_allowed": True, "name": "A vs B", "competition": "L",
            "start_time": "2026-08-16T10:00:00+00:00",
            "end_time": "2026-08-16T20:00:00+00:00", "resolution_height": 720,
        }
        cards = merge_candidates(
            [dict(base, **stream) for stream in streams], settings_path=SETTINGS
        )
        return cards[0]["url"]

    def test_direct_playback_wins_a_tie_against_proxy_only(self):
        self.assertEqual(self._primary([
            {"id": "p", "url": "https://a.test/proxied.m3u8",
             "proxy_mode": "proxy_only", "protected_source": True},
            {"id": "d", "url": "https://b.test/direct.m3u8",
             "proxy_mode": "direct_first"},
        ]), "https://b.test/direct.m3u8")

    def test_an_expired_token_ranks_below_a_live_one(self):
        self.assertEqual(self._primary([
            {"id": "x", "url": "https://a.test/expired.m3u8",
             "expires_at": int(time.time()) - 3600},
            {"id": "y", "url": "https://b.test/live.m3u8",
             "expires_at": int(time.time()) + 3600},
        ]), "https://b.test/live.m3u8")

    def test_drm_without_a_licence_route_ranks_below_a_complete_one(self):
        self.assertEqual(self._primary([
            {"id": "x", "url": "https://a.test/nolicence.m3u8",
             "drm": {"type": "widevine"}},
            {"id": "y", "url": "https://b.test/complete.m3u8",
             "drm": {"type": "widevine", "license_url": "https://l.test/lic"}},
        ]), "https://b.test/complete.m3u8")

    def test_quality_still_outranks_the_direct_preference(self):
        """Direct playback is a tie-breaker, not a reason to ship a worse
        picture."""
        self.assertEqual(self._primary([
            {"id": "x", "url": "https://a.test/low.m3u8",
             "proxy_mode": "direct_first", "resolution_height": 480},
            {"id": "y", "url": "https://b.test/high.m3u8", "proxy_mode": "proxy_only",
             "protected_source": True, "resolution_height": 1080},
        ]), "https://b.test/high.m3u8")

    def test_a_faster_response_wins_when_everything_else_matches(self):
        self.assertEqual(self._primary([
            {"id": "slow", "url": "https://a.test/slow.m3u8", "response_time_ms": 4000},
            {"id": "fast", "url": "https://b.test/fast.m3u8", "response_time_ms": 120},
        ]), "https://b.test/fast.m3u8")


class PromotionTests(unittest.TestCase):
    """Guide 30.8."""

    def _fixture(self, status, start):
        return {
            "id": "ax-fixture-9", "name": "Arsenal vs Chelsea",
            "competition": "Premier League", "source_id": "srhady-axsports-upcoming",
            "source_pipeline": "upcoming", "allow_without_stream": True,
            "status": status, "start_time": start.isoformat(),
            "verification_status": "failed", "publish_allowed": False,
        }

    def _stream(self, start):
        return {
            "id": "bingstream-77", "name": "Arsenal vs Chelsea Premier League",
            "source_id": "srhady-bingstream-live", "source_pipeline": "today_match",
            "url": "https://cdn.test/live.m3u8", "verification_status": "verified_global",
            "verified": True, "publish_allowed": True, "status": "LIVE",
            "start_time": start.isoformat(),
        }

    def _scan(self, candidates, data_root):
        enriched, _ = enrich_event_candidates(
            candidates, fixture_path=EMPTY_CATALOGUE, timezone_name="Asia/Dhaka",
            now=NOW, future_days=120, authority_source_ids=AUTHORITY,
        )
        enriched, _ = attach_streams_to_fixtures(enriched, AUTHORITY)
        cards = merge_candidates(enriched, settings_path=SETTINGS)
        reused = reuse_published_event_ids(cards, data_root)
        return cards, reused

    def test_a_fixture_moves_to_today_and_keeps_its_card(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_root = Path(temp_dir) / "data"
            data_root.mkdir()

            before, _ = self._scan(
                [self._fixture("NS", NOW + timedelta(hours=2))], data_root
            )
            # Routing reads the clock now - a fixture within 30 minutes of
            # kickoff belongs on Today Match - so this test's own NOW has to be
            # the clock it is measured against, not the real one.
            self.assertEqual(_destination_for(before[0], NOW), "upcoming")
            self.assertFalse(before[0].get("url"))

            (data_root / "upcoming.json").write_text(json.dumps({
                "count": 1,
                "items": [{"id": before[0]["id"], "name": before[0]["name"]}],
            }), encoding="utf-8")
            (data_root / "today-match.json").write_text(
                json.dumps({"count": 0, "items": []}), encoding="utf-8"
            )

            after, reused = self._scan([
                self._fixture("LIVE", NOW - timedelta(minutes=10)),
                self._stream(NOW - timedelta(minutes=10)),
            ], data_root)

        self.assertEqual(len(after), 1, "promotion must not create a second card")
        self.assertEqual(_destination_for(after[0], NOW), "today_match")
        self.assertTrue(after[0].get("url"))
        self.assertEqual(after[0]["id"], before[0]["id"])
        self.assertEqual(reused, 1)

    def test_an_in_play_fixture_that_gains_a_stream_leaves_the_upcoming_tab(self):
        """It was parked as LINK_UPDATING while it had no link. Once a working
        stream arrives it is playable and belongs on Today Match."""
        enriched, _ = enrich_event_candidates(
            [self._fixture("LIVE", NOW - timedelta(minutes=10)),
             self._stream(NOW - timedelta(minutes=10))],
            fixture_path=EMPTY_CATALOGUE, timezone_name="Asia/Dhaka",
            now=NOW, future_days=120, authority_source_ids=AUTHORITY,
        )
        attached, stats = attach_streams_to_fixtures(enriched, AUTHORITY)
        self.assertEqual(stats["streams_attached"], 1)
        promoted = next(i for i in attached if i.get("stream_attached_to_fixture"))
        self.assertEqual(promoted["schedule_status"], "LIVE_NOW")
        self.assertIs(promoted["promoted_from_upcoming"], True)

    def test_a_still_future_fixture_is_not_promoted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_root = Path(temp_dir) / "data"
            data_root.mkdir()
            cards, _ = self._scan(
                [self._fixture("NS", NOW + timedelta(days=2))], data_root
            )
        self.assertEqual(_destination_for(cards[0], NOW), "upcoming")

    def test_reuse_is_a_no_op_on_a_first_ever_scan(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_root = Path(temp_dir) / "data"
            data_root.mkdir()
            cards, reused = self._scan(
                [self._fixture("NS", NOW + timedelta(hours=2))], data_root
            )
        self.assertEqual(reused, 0)
        self.assertEqual(cards[0]["id"], "ax-fixture-9")


if __name__ == "__main__":
    unittest.main()
