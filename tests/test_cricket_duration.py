"""A cricket match lasts as long as its format lasts.

FINAL_2 ধাপ ৩ sets five of the six lengths:

    T10 150 · T20 240 · ODI 480 · Test 480 (দিন-ভিত্তিক) · অজানা 300

`Hundred` it names as a token to detect but gives no length for. The length
comes from evidence already in this repository instead of from an opinion:
`config/event-fixtures.json` has carried `the-hundred-2026:
duration_minutes = 210` all along. A hundred balls an innings is not twenty
overs an innings, so it stays its own entry rather than being folded into
T20.

The Test entry means something different from the others. 480 minutes is a
DAY of a Test, not the Test - it decides how long a card keeps its place,
and it is never evidence the match has finished, since four more days of it
may remain. PROMPT 19 is what enforces that.
"""
import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scanner import schedule_resolver as sr                       # noqa: E402
from scanner import sport_filter as sf                            # noqa: E402
from scanner.event_lifecycle import CRICKET_FORMAT_MINUTES        # noqa: E402

UTC = timezone.utc
KICKOFF = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
UTC_ZONE = sr._zone("UTC", "UTC", "+00:00")


def card(name, competition="", sport="cricket", **extra):
    return dict({
        "name": name,
        "competition": competition,
        "group_title": sport,
        "source_id": "some-feed",
        "status": "UPCOMING",
        "url": "https://a.example/x.m3u8",
    }, **extra)


def resolve(item):
    return sr._provider_fixture_item(
        item, KICKOFF, KICKOFF, 4, source_timezone=UTC_ZONE)


def span(resolved):
    return round((datetime.fromisoformat(resolved["end_time"])
                  - datetime.fromisoformat(resolved["start_time"])
                  ).total_seconds() / 60)


class TheFinalValues(unittest.TestCase):
    def test_every_format_has_exactly_one_length(self):
        self.assertEqual(
            set(sf.CRICKET_FORMATS), set(CRICKET_FORMAT_MINUTES)
        )

    def test_the_five_final_2_names(self):
        self.assertEqual(150, CRICKET_FORMAT_MINUTES["T10"])
        self.assertEqual(240, CRICKET_FORMAT_MINUTES["T20"])
        self.assertEqual(480, CRICKET_FORMAT_MINUTES["ODI"])
        self.assertEqual(480, CRICKET_FORMAT_MINUTES["Test"])
        self.assertEqual(300, CRICKET_FORMAT_MINUTES["unknown"])

    def test_the_hundred_comes_from_the_config_that_already_knew(self):
        catalogue = json.loads(
            (ROOT / "config" / "event-fixtures.json").read_text(encoding="utf-8"))
        hundred = next(
            competition for competition in catalogue["competitions"]
            if competition["id"] == "the-hundred-2026"
        )
        self.assertEqual(210, hundred["duration_minutes"])
        self.assertEqual(
            hundred["duration_minutes"], CRICKET_FORMAT_MINUTES["Hundred"]
        )

    def test_the_hundred_is_not_quietly_a_t20(self):
        self.assertNotEqual(
            CRICKET_FORMAT_MINUTES["Hundred"], CRICKET_FORMAT_MINUTES["T20"]
        )


class EachFormatGetsItsOwnLength(unittest.TestCase):
    CASES = (
        ("Deccan Gladiators vs Northern Warriors", "Abu Dhabi T10", 150),
        ("Chennai vs Mumbai", "Indian Premier League", 240),
        ("England vs India 7th T20I", "", 240),
        ("Ireland vs Afghanistan 4th ODI", "", 480),
        ("Australia vs Bangladesh 1st Test", "Australia Bangladesh Tests 2026", 480),
        ("Welsh Fire Women vs London Spirit Women", "The Hundred 2026", 210),
        ("India Women Vs Pakistan Women", "Womens Asia Cup", 300),
    )

    def test_the_length_is_the_formats(self):
        for name, competition, minutes in self.CASES:
            with self.subTest(name=name):
                self.assertEqual(
                    minutes, sr._sport_end_minutes(card(name, competition))
                )

    def test_and_it_reaches_the_published_card(self):
        for name, competition, minutes in self.CASES:
            with self.subTest(name=name):
                resolved = resolve(card(name, competition))
                self.assertEqual("sport", resolved["end_time_source"])
                self.assertEqual(minutes, span(resolved))

    def test_the_generic_four_hours_is_no_longer_used_for_cricket(self):
        """240 still appears - but as T20's length, not as everything's."""
        for name, competition, minutes in self.CASES:
            resolved = resolve(card(name, competition))
            with self.subTest(name=name):
                self.assertNotEqual("assumed", resolved["end_time_source"])


class AnUnknownFormatIsNotGuessedAt(unittest.TestCase):
    def test_it_takes_the_unknown_length_not_a_formats(self):
        resolved = resolve(card("A vs B", "Womens Asia Cup 2026 - 10th Match"))
        self.assertEqual(300, span(resolved))
        self.assertNotEqual(CRICKET_FORMAT_MINUTES["T20"], span(resolved))
        self.assertNotEqual(CRICKET_FORMAT_MINUTES["ODI"], span(resolved))

    def test_ambiguous_evidence_lands_there_too(self):
        resolved = resolve(card("India vs England", "T20 and Test Tour"))
        self.assertEqual("unknown", sf.cricket_format(
            card("India vs England", "T20 and Test Tour"))["format"])
        self.assertEqual(300, span(resolved))

    def test_it_is_still_a_sport_estimate_not_the_generic_one(self):
        resolved = resolve(card("A vs B", "Womens Asia Cup"))
        self.assertEqual("sport", resolved["end_time_source"])
        self.assertNotEqual(240, span(resolved))


class AProviderEndAlwaysWins(unittest.TestCase):
    def test_over_every_format(self):
        stated = KICKOFF + timedelta(minutes=6240)      # a five-day Test
        for name, competition, _minutes in EachFormatGetsItsOwnLength.CASES:
            resolved = resolve(card(
                name, competition,
                end_time=stated.isoformat(), end_time_stated=True))
            with self.subTest(name=name):
                self.assertEqual("provider", resolved["end_time_source"])
                self.assertEqual(6240, span(resolved))


class ATestIsADayNotAVerdict(unittest.TestCase):
    def test_the_length_is_a_day_of_play(self):
        self.assertEqual(8 * 60, CRICKET_FORMAT_MINUTES["Test"])

    def test_a_test_estimate_is_never_labelled_provider(self):
        """The label is what PROMPT 19 will read to decide authority, so a
        Test day's arithmetic must never arrive wearing it."""
        resolved = resolve(card("Australia vs Bangladesh 1st Test",
                                "Australia Bangladesh Tests 2026"))
        self.assertEqual("sport", resolved["end_time_source"])
        self.assertNotEqual("provider", resolved["end_time_source"])

    def test_a_catalogue_test_keeps_its_real_multi_day_end(self):
        """The nine explicit ends in the catalogue are real Test finishes and
        outrank the per-day estimate entirely."""
        fixtures = sr.load_fixtures(ROOT / "config" / "event-fixtures.json")
        tests = [
            fixture for fixture in fixtures
            if fixture["end_source"] == "provider"
            and (fixture["end"] - fixture["start"]).total_seconds() > 24 * 3600
        ]
        self.assertTrue(tests)
        for fixture in tests:
            with self.subTest(name=fixture["name"]):
                days = (fixture["end"] - fixture["start"]).days
                self.assertGreaterEqual(days, 3)


class NothingElseMoved(unittest.TestCase):
    def test_football_is_untouched_by_this_step(self):
        resolved = resolve(card("Milan vs Juventus", "Italian Serie A",
                                sport="football"))
        self.assertEqual("sport", resolved["end_time_source"])
        self.assertEqual(150, span(resolved))

    def test_a_non_ball_sport_still_gets_the_generic_estimate(self):
        resolved = resolve(card("Cycling", "Cycling", sport="other"))
        self.assertEqual("assumed", resolved["end_time_source"])
        self.assertEqual(240, span(resolved))

    def test_the_classifier_itself_did_not_change(self):
        """PROMPT 17's answers must be the same answers."""
        self.assertEqual("T20", sf.cricket_format(
            card("Chennai vs Mumbai", "Indian Premier League"))["format"])
        self.assertEqual("unknown", sf.cricket_format(
            card("Milan vs Juventus", "Italian Serie A",
                 sport="football"))["format"])

    def test_the_duration_table_is_not_imported_into_sport_filter(self):
        source = (ROOT / "scanner" / "sport_filter.py").read_text(encoding="utf-8")
        self.assertNotIn("CRICKET_FORMAT_MINUTES", source)


class ATestDaysLengthEndsNothing(unittest.TestCase):
    """PROMPT 19 is what makes the Test entry safe: 480 minutes decides how
    long a card keeps its place, and can never decide that a five-day match
    has finished."""

    def test_the_estimate_is_not_an_authority(self):
        from scanner.event_lifecycle import verified_end_passed
        now = KICKOFF + timedelta(hours=20)
        base = {
            "schedule_verified": True,
            "end_time": (KICKOFF + timedelta(minutes=480)).isoformat(),
        }
        self.assertFalse(verified_end_passed(
            dict(base, end_time_source="sport"), now, grace_minutes=90))
        self.assertTrue(verified_end_passed(
            dict(base, end_time_source="provider"), now, grace_minutes=90))


if __name__ == "__main__":
    unittest.main()
