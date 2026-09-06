"""Which format of cricket a fixture is - classification only.

FINAL_2 ধাপ ৩ needs this before it can give cricket a sensible length: the
duration table calls cricket one sport at eight hours, written for a Test,
and on 2026-09-05 every one of the 25 cricket cards on the site was T20 or
shorter. Applying a duration is PROMPT 18; this step only reads the format.

The rules reuse what `sport_filter` already does - whole-word tokens through
`_word`, gathered from the same name/competition/sport fields the sport
verdict uses.

`unknown` is a first-class answer. Four of the five unknowns in the live
scan are `Women's Asia Cup`, which is played as both T20 and ODI in
different years, so the tournament name is deliberately not evidence.
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scanner import sport_filter as sf  # noqa: E402


def card(name, competition="", sport="cricket", **extra):
    return dict({
        "name": name,
        "competition": competition,
        "group_title": sport,
    }, **extra)


def fmt(*args, **kwargs):
    return sf.cricket_format(card(*args, **kwargs))["format"]


class TheVocabulary(unittest.TestCase):
    def test_exactly_six_answers(self):
        self.assertEqual(
            ("T10", "T20", "ODI", "Test", "Hundred", "unknown"),
            sf.CRICKET_FORMATS,
        )

    def test_a_verdict_carries_its_evidence(self):
        verdict = sf.cricket_format(card("England vs India 7th T20I"))
        self.assertEqual("T20", verdict["format"])
        self.assertEqual("t20i", verdict["evidence"])
        self.assertEqual("token", verdict["reason"])


class EachFormatIsRecognised(unittest.TestCase):
    def test_test_matches(self):
        for name, competition in (
            ("Australia vs Bangladesh 1st Test", "Australia Bangladesh Tests 2026"),
            ("England vs Pakistan 2nd Test", ""),
            ("India vs Australia", "World Test Championship Final"),
            ("Yorkshire vs Surrey", "County Championship Division One"),
            ("Mumbai vs Delhi", "Ranji Trophy"),
        ):
            with self.subTest(name=name):
                self.assertEqual("Test", fmt(name, competition))

    def test_one_day_internationals(self):
        for name, competition in (
            ("Ireland vs Afghanistan 4th ODI", ""),
            ("England W vs Ireland W", "3rd ODI"),
            ("India vs Sri Lanka", "One Day International Series"),
            ("Somerset vs Kent", "Royal London Cup"),
        ):
            with self.subTest(name=name):
                self.assertEqual("ODI", fmt(name, competition))

    def test_twenty_over_cricket(self):
        for name, competition in (
            ("England vs India 7th T20I", ""),
            ("Chennai vs Mumbai", "Indian Premier League"),
            ("Sydney Sixers vs Perth Scorchers", "Big Bash League"),
            ("Barbados Tridents Vs Trinbago Knight Riders", "Caribbean Premier League"),
            ("Lahore vs Karachi", "Pakistan Super League"),
            ("Fazilka Falcons vs Bathinda Royals", "Sher-E-Punjab T20 League"),
            ("Dublin Guardians vs Amsterdam Flames", "European T20 Premier League 2026"),
        ):
            with self.subTest(name=name):
                self.assertEqual("T20", fmt(name, competition))

    def test_ten_over_cricket(self):
        for name, competition in (
            ("Deccan Gladiators vs Northern Warriors", "Abu Dhabi T10"),
            ("A vs B", "Lanka T10 League"),
        ):
            with self.subTest(name=name):
                self.assertEqual("T10", fmt(name, competition))

    def test_the_hundred_is_its_own_format(self):
        for name, competition in (
            ("Welsh Fire Women vs London Spirit Women", "The Hundred 2026"),
            ("Oval Invincibles vs Southern Brave", "The Hundred"),
        ):
            with self.subTest(name=name):
                self.assertEqual("Hundred", fmt(name, competition))
        # And it is not swept into T20 by its 100-ball nature.
        self.assertNotEqual("T20", fmt("A vs B", "The Hundred 2026"))


class ATestIsNeverSomethingShorter(unittest.TestCase):
    """The mistake with the worst consequence: five days read as four hours."""

    def test_a_test_is_not_t20(self):
        for competition in ("Australia Bangladesh Tests 2026", "The Ashes",
                            "World Test Championship", "County Championship"):
            with self.subTest(competition=competition):
                self.assertNotIn(fmt("A vs B", competition), ("T20", "T10"))

    def test_a_test_is_not_odi(self):
        self.assertNotEqual("ODI", fmt("Sri Lanka vs India 1st Test"))

    def test_the_word_test_needs_its_own_boundary(self):
        """`contest` and `protest` must not read as a Test match."""
        for name in ("A vs B Contest", "Protest Cup", "Greatest XI vs Rest"):
            with self.subTest(name=name):
                self.assertNotEqual("Test", fmt(name, "Some Cricket League"))


class AmbiguityStaysUnknown(unittest.TestCase):
    def test_two_formats_at_once_is_not_a_coin_toss(self):
        verdict = sf.cricket_format(
            card("India vs England", "T20 and Test Tour of India"))
        self.assertEqual("unknown", verdict["format"])
        self.assertIn("ambiguous", verdict["reason"])
        self.assertIn("Test", verdict["reason"])
        self.assertIn("T20", verdict["reason"])

    def test_a_tournament_played_in_more_than_one_format(self):
        """Asia Cup, World Cup and Champions Trophy are each played as both
        T20 and ODI in different years. Four of the five real unknowns are
        exactly this."""
        for competition in ("Womens Asia Cup", "Asia Cup 2026",
                            "ICC World Cup", "Champions Trophy",
                            "Womens Asia Cup 2026 - 10th Match"):
            with self.subTest(competition=competition):
                self.assertEqual("unknown", fmt("A vs B", competition))

    def test_generic_cricket_invents_nothing(self):
        verdict = sf.cricket_format(card("Some Team vs Other Team",
                                         "Local Cricket Cup"))
        self.assertEqual("unknown", verdict["format"])
        self.assertEqual("no format token", verdict["reason"])

    def test_a_card_with_no_competition_at_all(self):
        self.assertEqual("unknown", fmt("England vs Pakistan", ""))


class OnlyCricketGetsAFormat(unittest.TestCase):
    def test_a_football_card_never_does(self):
        verdict = sf.cricket_format(
            card("Milan vs Juventus", "Italian Serie A", sport="football"))
        self.assertEqual("unknown", verdict["format"])
        self.assertEqual("not cricket", verdict["reason"])

    def test_a_stamped_card_is_taken_at_its_word(self):
        stamped = card("A vs B", "T20 Cup", sport="",
                       sport_class=sf.CONFIRMED_FOOTBALL)
        self.assertEqual("unknown", sf.cricket_format(stamped)["format"])
        stamped_cricket = card("A vs B", "T20 Cup", sport="",
                               sport_class=sf.CONFIRMED_CRICKET)
        self.assertEqual("T20", sf.cricket_format(stamped_cricket)["format"])


class ItIsDeterministic(unittest.TestCase):
    def test_the_same_card_always_answers_the_same(self):
        item = card("Sydney Sixers vs Perth Scorchers", "Big Bash League")
        answers = {sf.cricket_format(item)["format"] for _ in range(25)}
        self.assertEqual({"T20"}, answers)

    def test_case_and_spacing_do_not_matter(self):
        for competition in ("BIG BASH LEAGUE", "big  bash   league",
                            "Big Bash League"):
            with self.subTest(competition=competition):
                self.assertEqual("T20", fmt("A vs B", competition))

    def test_it_does_not_mutate_the_card(self):
        item = card("England vs India 7th T20I")
        before = dict(item)
        sf.cricket_format(item)
        self.assertEqual(before, item)


class ClassificationIsAllThisModuleDoes(unittest.TestCase):
    """It reads the format. Turning that into a length is PROMPT 18, and
    lives in the duration module - which is what keeps this file free of
    clocks entirely."""

    def test_the_duration_step_asks_this_module_for_the_format(self):
        source = (ROOT / "scanner" / "schedule_resolver.py").read_text(
            encoding="utf-8")
        self.assertIn('sport_filter.cricket_format(item)["format"]', source)

    def test_a_classified_fixture_reaches_a_length(self):
        from scanner import schedule_resolver as sr
        self.assertEqual(("football", "cricket"), sr.SPORT_DERIVED_ENDS)
        self.assertEqual(240, sr._sport_end_minutes(
            card("Fazilka Falcons vs Bathinda Royals", "Sher-E-Punjab T20 League")
        ))

    def test_this_module_still_carries_no_durations(self):
        """The lengths live with the other lengths, keyed by these names."""
        source = (ROOT / "scanner" / "sport_filter.py").read_text(encoding="utf-8")
        for token in ("timedelta", "duration_minutes", "SPORT_DURATION",
                      "CRICKET_FORMAT_MINUTES"):
            self.assertNotIn(token, source)


if __name__ == "__main__":
    unittest.main()
