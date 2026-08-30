"""Today Match is a live surface, and Upcoming is for matches that have not started.

Two defects, both found on 2026-08-30 by reading what was actually published.

1. Today Match had become an archive of everything that had ever been live.
   435 cards, of which 5 had started in the last six hours; 158 had started more
   than a week earlier. `Sri Lanka vs India 1st Test` carried an authoritative
   end_time of 2026-08-19 and was still published as LIVE_NOW on 2026-08-30.
   Every publish reported filtered_stale=0, and the count only ever grew:
   316, 352, 363, 374, 421, 422, 444, 447, 467.

   The freshness rule itself was correct - `_is_today_fresh` returns False for
   that card when asked. It was never asked. The targeted trigger, which runs
   every five minutes, republished the previous Today Match file verbatim: the
   Upcoming half of that carry-through was freshness-checked, the Today Match
   half was not. So the tab renders as empty, because nothing in it is on today.

2. A fixture that had already kicked off sat on Upcoming with LINK UPDATING on
   it for up to three hours. Measured at 08:04 UTC: a match that started at
   06:00 and one that started at 06:30 were both still there. Three hours is the
   wrong resolution for the decision - the -15 minute targeted trigger runs
   every five minutes, so the window only needs to be long enough for a feed to
   publish a link a little after the whistle.

3. And the routes the delivery path cannot fetch came back. Twenty-six bare-IP
   backups reappeared in today-match.json hours after being swept out, because a
   carried-forward card is never re-verified - so the verifier's guard, which
   fires when a route is verified, never saw them.
"""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import events  # noqa: E402

NOW = datetime(2026, 8, 30, 8, 4, 0, tzinfo=timezone.utc)


def card(**fields):
    base = {"_source_timezone": timezone.utc}
    base.update(fields)
    return base


class TodayMatchIsNotAnArchiveTests(unittest.TestCase):
    def test_the_fixture_that_exposed_this_is_not_fresh(self):
        """Its own authoritative end_time is eleven days in the past."""
        self.assertFalse(events._is_today_fresh(
            card(name="Sri Lanka vs India 1st Test",
                 start_time="2026-08-15T04:30:00+00:00",
                 end_time="2026-08-19T12:30:00+00:00",
                 schedule_verified=True, status="LIVE_NOW"),
            NOW, 12,
        ))

    def test_a_match_on_now_is_fresh(self):
        self.assertTrue(events._is_today_fresh(
            card(start_time=(NOW - timedelta(hours=2)).isoformat(),
                 end_time=(NOW + timedelta(hours=3)).isoformat(),
                 schedule_verified=True),
            NOW, 12,
        ))

    def test_an_unscheduled_card_survives_on_age_alone(self):
        """No start time at all is not evidence of staleness - a channel-backed
        card carries no fixture clock and must not be deleted for it."""
        self.assertTrue(events._is_today_fresh(card(name="Some Channel"), NOW, 12))

    def test_the_carry_through_now_applies_the_clock(self):
        """The regression itself: the targeted path used to append
        `dict(previous)` with no check at all."""
        source = (ROOT / "scanner" / "events.py").read_text(encoding="utf-8")
        carry = source[source.index("for previous in previous_today_published:"):]
        carry = carry[:carry.index("for event_id, card in promoted.items():")]
        self.assertIn("_is_today_fresh(carried, now, today_max_age_hours)", carry)
        self.assertIn("today_stale += 1", carry)


class UpcomingDropsWhatHasAlreadyStartedTests(unittest.TestCase):
    def test_a_match_two_hours_past_kickoff_is_gone(self):
        self.assertFalse(events._is_upcoming_fresh(
            card(name="TBC", start_time="2026-08-30T06:00:00+00:00"),
            NOW, 45, 120,
        ))

    def test_a_match_ten_minutes_past_kickoff_still_gets_its_chance(self):
        """The trigger runs every five minutes; a feed that publishes a link
        just after the whistle must still be caught."""
        self.assertTrue(events._is_upcoming_fresh(
            card(start_time=(NOW - timedelta(minutes=10)).isoformat()),
            NOW, 45, 120,
        ))

    def test_a_future_fixture_stays(self):
        self.assertTrue(events._is_upcoming_fresh(
            card(start_time=(NOW + timedelta(days=3)).isoformat()), NOW, 45, 120,
        ))

    def test_an_hours_value_from_an_older_config_is_still_honoured(self):
        """3 means three hours, not three minutes. A caller upgrading from the
        hours key must not silently get a 180x tighter window."""
        started_two_hours_ago = card(start_time=(NOW - timedelta(hours=2)).isoformat())
        self.assertTrue(events._is_upcoming_fresh(started_two_hours_ago, NOW, 3, 120))
        self.assertFalse(events._is_upcoming_fresh(started_two_hours_ago, NOW, 45, 120))

    def test_the_configured_window_is_minutes(self):
        import json
        settings = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(45, settings["events"]["upcoming_past_grace_minutes"])


class UndeliverableRoutesDoNotComeBackTests(unittest.TestCase):
    def test_a_bare_ip_backup_is_stripped(self):
        item = {"backups": [
            {"url": "https://good.example/a.m3u8"},
            {"url": "http://193.47.62.41:8080/a.m3u8"},
        ]}
        self.assertEqual(1, events._strip_undeliverable_routes(item))
        self.assertEqual(1, len(item["backups"]))
        self.assertEqual("https://good.example/a.m3u8", item["backups"][0]["url"])

    def test_the_other_url_spellings_are_seen(self):
        for key in ("url", "stream_url", "link"):
            item = {"backups": [{key: "http://23.237.104.106/a.m3u8"}]}
            self.assertEqual(1, events._strip_undeliverable_routes(item), key)

    def test_a_plain_string_backup_is_seen(self):
        item = {"backups": ["http://23.237.104.106/a.m3u8", "https://ok.example/a"]}
        self.assertEqual(1, events._strip_undeliverable_routes(item))

    def test_a_good_card_is_left_exactly_alone(self):
        backups = [{"url": "https://a.example/1"}, {"url": "https://b.example/2"}]
        item = {"backups": list(backups)}
        self.assertEqual(0, events._strip_undeliverable_routes(item))
        self.assertEqual(backups, item["backups"])

    def test_no_backups_does_not_raise(self):
        for value in ({}, {"backups": None}, {"backups": "not a list"}):
            self.assertEqual(0, events._strip_undeliverable_routes(dict(value)))

    def test_it_runs_on_the_carried_paths_too(self):
        """The point of the fix: a card the verifier never sees still gets the
        rule applied."""
        source = (ROOT / "scanner" / "events.py").read_text(encoding="utf-8")
        self.assertGreaterEqual(source.count("_strip_undeliverable_routes("), 5)


if __name__ == "__main__":
    unittest.main()
