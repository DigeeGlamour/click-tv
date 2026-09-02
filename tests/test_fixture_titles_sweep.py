"""Tidying a title at parse time never reaches a card carried through.

The adapters clean a name as they build it, and the published files still held

    Indore Hawks vs Chennai Strikers 2 Sep 2026     (Today Match)
    Mohali Kings vs Ludhiana Lions 2 Sep 2026       (Today Match)

on 2026-09-02, because a card restored from the source cache or carried over
from the previous publish is never parsed again. Same shape as the sport filter
missing 29 of 41 Today cards: the answer is a sweep over the final lists.

`TBC` was also a published Upcoming title, with `Uttar Pradesh T20 League,
2026` sitting in the card's own competition field.
"""
import unittest
from pathlib import Path

from scanner import fixture_titles

EVENTS = (Path(__file__).resolve().parent.parent
          / "scanner" / "events.py")


class TheTitlesThatWereOnThePage(unittest.TestCase):
    OBSERVED = [
        ("Indore Hawks vs Chennai Strikers 2 Sep 2026",
         "Indore Hawks vs Chennai Strikers"),
        ("Mohali Kings vs Ludhiana Lions 2 Sep 2026",
         "Mohali Kings vs Ludhiana Lions"),
    ]

    def test_each_one_loses_its_date(self):
        for given, expected in self.OBSERVED:
            with self.subTest(given=given):
                self.assertEqual(fixture_titles.tidy(given), expected)

    def test_the_sweep_fixes_them_in_place_and_says_so(self):
        cards = [{"name": given} for given, _ in self.OBSERVED]
        changed = fixture_titles.apply(cards)
        self.assertEqual([row["now"] for row in changed],
                         [expected for _, expected in self.OBSERVED])
        self.assertEqual([card["name"] for card in cards],
                         [expected for _, expected in self.OBSERVED])

    def test_what_the_feed_sent_is_kept(self):
        card = {"name": "Mohali Kings vs Ludhiana Lions 2 Sep 2026"}
        fixture_titles.apply([card])
        self.assertEqual(card["name_from_source"],
                         "Mohali Kings vs Ludhiana Lions 2 Sep 2026")


class APlaceholderIsNotAFixture(unittest.TestCase):
    def test_tbc_is_named_by_its_competition(self):
        self.assertEqual(
            fixture_titles.tidy("TBC", "Uttar Pradesh T20 League, 2026"),
            "Uttar Pradesh T20 League, 2026")

    def test_the_sweep_names_the_observed_card(self):
        card = {"name": "TBC", "competition": "Uttar Pradesh T20 League, 2026"}
        self.assertEqual(len(fixture_titles.apply([card])), 1)
        self.assertEqual(card["name"], "Uttar Pradesh T20 League, 2026")
        self.assertEqual(card["name_from_source"], "TBC")

    def test_the_other_spellings_count_too(self):
        for given in ("TBA", "tbd", "N/A", "n/a", "To Be Confirmed",
                      "to be announced", "Unknown", "Match", "vs", "  TBC  "):
            with self.subTest(given=given):
                self.assertTrue(fixture_titles.is_placeholder(given))

    def test_a_placeholder_with_nothing_to_fall_back_on_is_left_alone(self):
        # Better a vague title than a blank card.
        self.assertEqual(fixture_titles.tidy("TBC", ""), "TBC")
        card = {"name": "TBC"}
        self.assertEqual(fixture_titles.apply([card]), [])
        self.assertEqual(card["name"], "TBC")

    def test_a_real_team_containing_a_placeholder_word_is_safe(self):
        for given in ("Match Point FC vs Real Madrid",
                      "Unknown List XI vs Peru",
                      "Vs Nautico vs Bahia",
                      "Event Horizon vs Chelsea"):
            with self.subTest(given=given):
                self.assertFalse(fixture_titles.is_placeholder(given))
                self.assertEqual(fixture_titles.tidy(given, "Some Cup"), given)


class ARealNameIsNeverDamaged(unittest.TestCase):
    def test_a_clean_fixture_survives_the_sweep(self):
        cards = [{"name": name, "competition": "A League"} for name in (
            "England vs Pakistan", "Real Sociedad vs RC Celta",
            "VfL Osnabrück vs FC Bayern München", "Dazn 2 vs Dazn 4",
            "Sporting CP vs Estoril 1936",
        )]
        before = [card["name"] for card in cards]
        self.assertEqual(fixture_titles.apply(cards), [])
        self.assertEqual([card["name"] for card in cards], before)
        for card in cards:
            self.assertNotIn("name_from_source", card)

    def test_junk_in_the_list_is_ignored(self):
        self.assertEqual(fixture_titles.apply([None, "junk", {}, 7]), [])

    def test_a_nameless_card_is_left_nameless(self):
        card = {"competition": "A League"}
        self.assertEqual(fixture_titles.apply([card]), [])
        self.assertNotIn("name", card)


class ItRunsWhereTheListsAreFinal(unittest.TestCase):
    def test_the_sweep_is_wired_in_before_duplicates_are_folded(self):
        # A date on one copy of a fixture and not the other is how a duplicate
        # escapes, so tidying has to come first.
        source = EVENTS.read_text(encoding="utf-8")
        sweep = source.index("fixture_titles.apply(today_items)")
        fold = source.index("fixture_dedupe.fold(today_items")
        self.assertLess(sweep, fold,
                        "titles must be tidied before duplicates are folded")

    def test_both_published_lists_are_swept(self):
        source = EVENTS.read_text(encoding="utf-8")
        self.assertIn("fixture_titles.apply(today_items)", source)
        self.assertIn("fixture_titles.apply(upcoming_items)", source)


if __name__ == "__main__":
    unittest.main()
