"""A feed's clock is in the feed's own timezone, not always in Dhaka's.

Every naive time from every feed used to be stamped Bangladesh time. FanCode is
an Indian service publishing Indian Standard Time, so the half hour between
UTC+5:30 and UTC+6:00 made every FanCode fixture exactly thirty minutes early -
measured 2026-09-02 across five countries, and confirmed against LaLiga and
thesportsdb for `Real Sociedad vs RC Celta`, published 18:30 when both say
19:00.

A zone named inside the value was stripped and discarded too, so "5 PM IST"
was read as 5 PM in Dhaka.
"""
import unittest
from datetime import datetime, timedelta, timezone

from scanner.parsers.event_adapters import BDT, IST, PKT, _parse_clock


def utc_hhmm(value, **kwargs):
    parsed = _parse_clock(value, **kwargs)
    return parsed[11:16] if parsed else ""


class FanCodePublishesIndianTime(unittest.TestCase):
    #: The live feed's own `startTime` values, with the kickoff each one means.
    #: Every right-hand column is the time the competition itself publishes.
    OBSERVED = [
        ("05:30:00 PM 03-09-2026", "12:00", "Namibia vs Zimbabwe"),
        ("07:00:00 AM 04-09-2026", "01:30", "Saudi Arabia vs Bahrain"),
        ("12:15:00 AM 03-09-2026", "18:45", "VfL Osnabruck vs Bayern"),
        ("12:30:00 AM 04-09-2026", "19:00", "Real Sociedad vs RC Celta"),
        ("04:00:00 PM 04-09-2026", "10:30", "Yalla Shabab vs Majees Titans"),
    ]

    def test_each_fixture_lands_on_its_real_kickoff(self):
        for raw, expected, fixture in self.OBSERVED:
            with self.subTest(fixture=fixture):
                self.assertEqual(utc_hhmm(raw, tz=IST), expected)

    def test_reading_it_as_dhaka_is_half_an_hour_early(self):
        # The bug, stated as a test so the difference is on the record.
        for raw, expected, fixture in self.OBSERVED:
            with self.subTest(fixture=fixture):
                as_dhaka = utc_hhmm(raw)
                self.assertNotEqual(as_dhaka, expected)
                correct = datetime.strptime(expected, "%H:%M")
                wrong = datetime.strptime(as_dhaka, "%H:%M")
                self.assertEqual((correct - wrong) % timedelta(days=1),
                                 timedelta(minutes=30))


class TheOtherFeedsAreUnchanged(unittest.TestCase):
    """Most of these feeds really do publish Bangladesh time."""

    def test_bangladesh_stays_the_default(self):
        # bingstream's `bd_time`, which the field name says outright.
        self.assertEqual(utc_hhmm("1:00 AM 03-09-2026"), "19:00")

    def test_an_iso_offset_in_the_value_still_wins(self):
        self.assertEqual(utc_hhmm("2026-09-02T19:00:00+02:00"), "17:00")

    def test_a_unix_timestamp_is_untouched(self):
        # bingstream's own `start_at` for Sportivo Ameliano, which agrees with
        # thesportsdb and with what the card already published.
        self.assertEqual(_parse_clock("1788375600")[:16], "2026-09-02T19:00")

    def test_an_unreadable_value_is_empty(self):
        for value in ("", None, "next tuesday-ish", "  "):
            with self.subTest(value=value):
                self.assertEqual(_parse_clock(value), "")


class AZoneNamedInTheValueIsHonoured(unittest.TestCase):
    def test_ist_is_read_as_ist(self):
        self.assertEqual(utc_hhmm("5 PM IST"), "11:30")

    def test_bdt_is_read_as_bdt(self):
        self.assertEqual(utc_hhmm("5 PM BDT"), "11:00")

    def test_utc_is_read_as_utc(self):
        self.assertEqual(utc_hhmm("5 PM UTC"), "17:00")

    def test_the_named_zone_beats_the_caller(self):
        # A feed that usually publishes IST but labels one value BDT means BDT.
        self.assertEqual(utc_hhmm("5 PM BDT", tz=IST), "11:00")

    def test_a_pakistan_clock_is_five_hours(self):
        self.assertEqual(utc_hhmm("5 PM PKT"), "12:00")


class TheAdaptersPassTheirOwnZone(unittest.TestCase):
    def test_both_fancode_adapters_read_indian_time(self):
        import inspect

        from scanner.parsers import event_adapters

        for name in ("adapt_fancode", "adapt_sportlive_fancode"):
            with self.subTest(adapter=name):
                source = inspect.getsource(getattr(event_adapters, name))
                self.assertIn("tz=IST", source,
                              f"{name} reads FanCode's clock as Dhaka time")

    def test_the_zones_are_what_they_claim(self):
        self.assertEqual(IST.utcoffset(None), timedelta(hours=5, minutes=30))
        self.assertEqual(BDT.utcoffset(None), timedelta(hours=6))
        self.assertEqual(PKT.utcoffset(None), timedelta(hours=5))


if __name__ == "__main__":
    unittest.main()
