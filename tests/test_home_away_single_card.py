"""A card that arrived once, in the wrong order, had nothing to correct it.

Report item [4]. Folding settles home and away when two spellings of one
fixture meet - that is where the question announces itself, and it is what
`_fix_home_away` was written for. But `Real Madrid vs Real Betis` reached the
page from one feed, with no duplicate beside it, for a LaLiga match played at
Betis. It was still there on 2026-09-02 after the duplicate work was done,
because nothing asks the question of a card that arrived alone.

`Real Sociedad vs RC Celta` in the same list was already right, and reversing a
fixture that was right is the same defect in the other direction - so silence
from the provider leaves the feed's order alone.
"""
import json
import tempfile
import unittest
import urllib.parse
from pathlib import Path

from scanner import fixture_dedupe, fixture_lookup


class TheCardThatWasOnThePage(unittest.TestCase):
    def setUp(self):
        self.cards = [
            {"name": "Real Madrid vs Real Betis",
             "start_time": "2026-09-04T19:00:00+00:00"},
            {"name": "Real Sociedad vs RC Celta",
             "start_time": "2026-09-03T19:00:00+00:00"},
        ]

    @staticmethod
    def resolver(home, away, date):
        # What LaLiga records for those two dates.
        return {"2026-09-04": "Real Betis",
                "2026-09-03": "Real Sociedad"}.get(date, "")

    def test_the_reversed_card_is_turned_round(self):
        changed = fixture_dedupe.correct_home_away(self.cards, self.resolver)
        self.assertEqual(len(changed), 1)
        self.assertEqual(self.cards[0]["name"], "Real Betis vs Real Madrid")
        self.assertTrue(self.cards[0]["home_away_corrected"])

    def test_the_original_order_is_kept_on_the_record(self):
        fixture_dedupe.correct_home_away(self.cards, self.resolver)
        self.assertEqual(self.cards[0]["name_before_home_away_fix"],
                         "Real Madrid vs Real Betis")

    def test_the_card_that_was_already_right_is_untouched(self):
        fixture_dedupe.correct_home_away(self.cards, self.resolver)
        self.assertEqual(self.cards[1]["name"], "Real Sociedad vs RC Celta")
        self.assertNotIn("home_away_corrected", self.cards[1])


class SilenceIsNotEvidence(unittest.TestCase):
    def test_no_answer_leaves_the_order_alone(self):
        card = {"name": "Arsenal vs Chelsea",
                "start_time": "2026-09-05T14:00:00+00:00"}
        self.assertEqual(
            fixture_dedupe.correct_home_away([card], lambda *a: ""), [])
        self.assertEqual(card["name"], "Arsenal vs Chelsea")

    def test_an_answer_naming_neither_side_is_ignored(self):
        # A provider that returns the wrong fixture must not rename this one.
        card = {"name": "Arsenal vs Chelsea",
                "start_time": "2026-09-05T14:00:00+00:00"}
        self.assertEqual(
            fixture_dedupe.correct_home_away([card], lambda *a: "Barcelona"),
            [])
        self.assertEqual(card["name"], "Arsenal vs Chelsea")

    def test_a_different_spelling_of_the_home_side_still_counts_as_it(self):
        # "Celta Vigo" and "RC Celta" are one club, so naming either as home
        # when it is already first must not flip the card.
        card = {"name": "RC Celta vs Real Sociedad",
                "start_time": "2026-09-03T19:00:00+00:00"}
        self.assertEqual(
            fixture_dedupe.correct_home_away([card], lambda *a: "Celta Vigo"),
            [])
        self.assertEqual(card["name"], "RC Celta vs Real Sociedad")

    def test_no_resolver_means_no_change(self):
        card = {"name": "Real Madrid vs Real Betis",
                "start_time": "2026-09-04T19:00:00+00:00"}
        self.assertEqual(fixture_dedupe.correct_home_away([card], None), [])
        self.assertEqual(card["name"], "Real Madrid vs Real Betis")

    def test_a_provider_that_throws_does_not_break_the_scan(self):
        def boom(*_args):
            raise RuntimeError("provider down")

        card = {"name": "Real Madrid vs Real Betis",
                "start_time": "2026-09-04T19:00:00+00:00"}
        self.assertEqual(fixture_dedupe.correct_home_away([card], boom), [])
        self.assertEqual(card["name"], "Real Madrid vs Real Betis")

    def test_a_card_with_no_date_is_never_asked(self):
        asked = []
        card = {"name": "Somebody vs Someone", "start_time": ""}
        fixture_dedupe.correct_home_away(
            [card], lambda *a: asked.append(a) or "")
        self.assertEqual(asked, [])

    def test_a_title_that_is_not_two_sides_is_never_asked(self):
        asked = []
        card = {"name": "Uttar Pradesh T20 League, 2026",
                "start_time": "2026-09-04T19:00:00+00:00"}
        fixture_dedupe.correct_home_away(
            [card], lambda *a: asked.append(a) or "")
        self.assertEqual(asked, [])

    def test_a_card_already_corrected_is_not_asked_twice(self):
        asked = []
        card = {"name": "Real Betis vs Real Madrid",
                "start_time": "2026-09-04T19:00:00+00:00",
                "home_away_corrected": True}
        fixture_dedupe.correct_home_away(
            [card], lambda *a: asked.append(a) or "Real Madrid")
        self.assertEqual(asked, [])
        self.assertEqual(card["name"], "Real Betis vs Real Madrid")

    def test_junk_in_the_list_is_ignored(self):
        self.assertEqual(
            fixture_dedupe.correct_home_away([None, "junk", {}, 7],
                                             lambda *a: "X"), [])


class TheLookupIsPaidForOnce(unittest.TestCase):
    """`home_side` costs two requests, so the answer has to be remembered."""

    def setUp(self):
        self.cache = Path(tempfile.mkdtemp()) / "lookups.json"

    def _provider(self):
        calls = []

        def fetch(url):
            calls.append(url)
            asked = urllib.parse.unquote(url)
            if "Real Betis_vs_Real Madrid" in asked and "d=2026-09-04" in asked:
                return json.dumps({"event": [{"dateEvent": "2026-09-04"}]})
            return json.dumps({"event": None})

        return fetch, calls

    def test_it_names_the_home_side(self):
        fetch, calls = self._provider()
        answers = fixture_lookup.resolve_home_sides(
            [("Real Madrid", "Real Betis", "2026-09-04")],
            fetch=fetch, cache_path=self.cache)
        self.assertEqual(
            fixture_lookup.home_side_from(answers, "Real Madrid", "Real Betis",
                                          "2026-09-04"),
            "Real Betis")
        self.assertEqual(len(calls), 2, "both orders have to be asked")

    def test_the_second_scan_asks_nothing(self):
        fetch, _ = self._provider()
        fixture_lookup.resolve_home_sides(
            [("Real Madrid", "Real Betis", "2026-09-04")],
            fetch=fetch, cache_path=self.cache)

        again, calls = self._provider()
        answers = fixture_lookup.resolve_home_sides(
            [("Real Madrid", "Real Betis", "2026-09-04")],
            fetch=again, cache_path=self.cache)
        self.assertEqual(calls, [])
        self.assertEqual(
            fixture_lookup.home_side_from(answers, "Real Madrid", "Real Betis",
                                          "2026-09-04"),
            "Real Betis")

    def test_the_answer_is_found_whichever_way_round_it_is_asked(self):
        fetch, _ = self._provider()
        answers = fixture_lookup.resolve_home_sides(
            [("Real Madrid", "Real Betis", "2026-09-04")],
            fetch=fetch, cache_path=self.cache)
        self.assertEqual(
            fixture_lookup.home_side_from(answers, "Real Betis", "Real Madrid",
                                          "2026-09-04"),
            "Real Betis")

    def test_an_unsettled_fixture_returns_nothing_rather_than_a_guess(self):
        def silent(_url):
            return json.dumps({"event": None})

        answers = fixture_lookup.resolve_home_sides(
            [("Arsenal", "Chelsea", "2026-09-05")],
            fetch=silent, cache_path=self.cache)
        self.assertEqual(
            fixture_lookup.home_side_from(answers, "Arsenal", "Chelsea",
                                          "2026-09-05"), "")

    def test_the_budget_is_far_below_the_sport_budget(self):
        self.assertLess(fixture_lookup.MAX_HOME_AWAY_LOOKUPS_PER_SCAN,
                        fixture_lookup.MAX_LOOKUPS_PER_SCAN)

    def test_the_budget_is_honoured(self):
        fetch, calls = self._provider()
        pairs = [(f"Team {n}", f"Other {n}", "2026-09-05") for n in range(40)]
        fixture_lookup.resolve_home_sides(pairs, fetch=fetch,
                                          cache_path=self.cache, budget=3)
        self.assertEqual(len(calls), 6, "three pairs, two requests each")

    def test_a_pair_missing_a_date_is_never_asked(self):
        fetch, calls = self._provider()
        fixture_lookup.resolve_home_sides([("Arsenal", "Chelsea", "")],
                                          fetch=fetch, cache_path=self.cache)
        self.assertEqual(calls, [])

    def test_home_away_answers_do_not_collide_with_sport_answers(self):
        fetch, _ = self._provider()
        fixture_lookup.resolve_home_sides(
            [("Real Madrid", "Real Betis", "2026-09-04")],
            fetch=fetch, cache_path=self.cache)
        cache = fixture_lookup.load_cache(self.cache)
        self.assertTrue(cache)
        for key in cache:
            self.assertTrue(key.startswith(fixture_lookup.HOME_AWAY_PREFIX))


class ItRunsInThePipeline(unittest.TestCase):
    def test_both_lists_are_swept_after_folding(self):
        source = (Path(__file__).resolve().parent.parent
                  / "scanner" / "events.py").read_text(encoding="utf-8")
        self.assertIn("fixture_dedupe.correct_home_away(today_items", source)
        self.assertIn("fixture_dedupe.correct_home_away(upcoming_items", source)
        self.assertLess(source.index("fixture_dedupe.fold(today_items"),
                        source.index("fixture_dedupe.correct_home_away("))

    def test_it_uses_the_cached_resolver(self):
        source = (Path(__file__).resolve().parent.parent
                  / "scanner" / "events.py").read_text(encoding="utf-8")
        self.assertIn("fixture_lookup.resolve_home_sides(pairs)", source)


if __name__ == "__main__":
    unittest.main()
