"""A fixture's name is the two teams. The date and the status have their own fields.

Observed on the front page 2026-09-02: `Indore Hawks vs Chennai Strikers 2 Sep
2026` and `Mohali Kings vs Ludhiana Lions 2 Sep 2026` carried a date the card
already shows from `start_time`, and `Costa Rica vs Bulgaria Live` and `Peru vs
Afghanistan Live` read as though "Live" were part of the away side's name while
the card was already showing a LIVE badge from `status`.
"""
import unittest

from scanner.parsers.event_adapters import _match_name, _tidy_fixture_title


class ADateBelongsToStartTime(unittest.TestCase):
    OBSERVED = [
        ("Indore Hawks vs Chennai Strikers 2 Sep 2026",
         "Indore Hawks vs Chennai Strikers"),
        ("Mohali Kings vs Ludhiana Lions 2 Sep 2026",
         "Mohali Kings vs Ludhiana Lions"),
    ]

    def test_each_observed_title_loses_its_date(self):
        for given, expected in self.OBSERVED:
            with self.subTest(given=given):
                self.assertEqual(_match_name(given), expected)

    def test_the_other_date_spellings_go_too(self):
        for given in ("India vs Sri Lanka 03/09/2026",
                      "India vs Sri Lanka Sep 3, 2026",
                      "India vs Sri Lanka 3 September 2026",
                      "India vs Sri Lanka, 3 Sep 2026"):
            with self.subTest(given=given):
                self.assertEqual(_match_name(given), "India vs Sri Lanka")


class AStatusBelongsToStatus(unittest.TestCase):
    OBSERVED = [
        ("Costa Rica vs Bulgaria Live", "Costa Rica vs Bulgaria"),
        ("Peru vs Afghanistan Live", "Peru vs Afghanistan"),
    ]

    def test_each_observed_title_loses_its_status(self):
        for given, expected in self.OBSERVED:
            with self.subTest(given=given):
                self.assertEqual(_match_name(given), expected)

    def test_quality_and_broadcast_words_go_too(self):
        for given in ("Real Madrid vs Malaga HD", "Real Madrid vs Malaga FHD",
                      "Real Madrid vs Malaga 4K",
                      "Real Madrid vs Malaga FULL MATCH",
                      "Real Madrid vs Malaga LIVE NOW",
                      "Real Madrid vs Malaga | Highlights"):
            with self.subTest(given=given):
                self.assertEqual(_match_name(given), "Real Madrid vs Malaga")

    def test_both_at_once(self):
        self.assertEqual(_match_name("Mohali Kings vs Ludhiana Lions 2 Sep 2026 Live"),
                         "Mohali Kings vs Ludhiana Lions")


class ARealNameIsNeverDamaged(unittest.TestCase):
    def test_a_clean_fixture_is_returned_unchanged(self):
        for given in ("England vs Pakistan", "Real Sociedad vs RC Celta",
                      "Dublin Guardians vs Rotterdam Dockers",
                      "VfL Osnabrück vs FC Bayern München"):
            with self.subTest(given=given):
                self.assertEqual(_match_name(given), given)

    def test_a_team_whose_name_contains_a_number_keeps_it(self):
        for given in ("Dazn 2 vs Dazn 4", "Willow 2 vs Willow",
                      "Sporting CP vs Estoril 1936"):
            with self.subTest(given=given):
                self.assertEqual(_match_name(given), given)

    def test_a_title_that_is_not_a_fixture_is_left_alone(self):
        # Nothing to protect here, but stripping must not invent a fixture or
        # empty the name out.
        for given in ("TBC", "Live", "Top End T20 Series 2026"):
            with self.subTest(given=given):
                self.assertEqual(_tidy_fixture_title(given), given)

    def test_stripping_never_empties_the_name(self):
        self.assertEqual(_tidy_fixture_title("2 Sep 2026"), "2 Sep 2026")
        self.assertEqual(_tidy_fixture_title(""), "")

    def test_stripping_never_leaves_one_side_only(self):
        # If the result no longer reads as a fixture, the original stands.
        self.assertIn("vs", _tidy_fixture_title("A vs B 2 Sep 2026"))

    def test_the_prefix_trim_still_works_with_it(self):
        self.assertEqual(_match_name("Series - 1st Test - England vs Pakistan Live"),
                         "England vs Pakistan")

class EveryAdapterExitIsCleaned(unittest.TestCase):
    """_match_name only tidies the branch that already reads as "A vs B".

    A name assembled from `team_1`/`team_2`, or taken straight from a title,
    never went through it - so five titles were still carrying a date or a
    trailing "Live" after the first attempt. `_record` is the one exit every
    adapter uses, so the cleaning belongs there.
    """

    def test_the_record_builder_cleans_the_name(self):
        import inspect

        from scanner.parsers import event_adapters

        source = inspect.getsource(event_adapters._record)
        self.assertIn('"name": _tidy_fixture_title(name)', source)

    def test_a_name_built_from_two_team_fields_is_cleaned(self):
        # The shape adapt_sportlive_fancode falls back to.
        from scanner.parsers.event_adapters import _tidy_fixture_title
        assembled = "Fazilka Falcons vs Amritsar Soormas 2 Sep 2026"
        self.assertEqual(_tidy_fixture_title(assembled),
                         "Fazilka Falcons vs Amritsar Soormas")

    def test_the_observed_five_are_all_clean(self):
        from scanner.parsers.event_adapters import _tidy_fixture_title
        for given, expected in (
            ("Indore Hawks vs Chennai Strikers 2 Sep 2026",
             "Indore Hawks vs Chennai Strikers"),
            ("Fazilka Falcons vs Amritsar Soormas 2 Sep 2026",
             "Fazilka Falcons vs Amritsar Soormas"),
            ("Costa Rica vs Bulgaria Live", "Costa Rica vs Bulgaria"),
            ("Oman vs Slovenia Live", "Oman vs Slovenia"),
            ("Peru vs Afghanistan Live", "Peru vs Afghanistan"),
        ):
            with self.subTest(given=given):
                self.assertEqual(_tidy_fixture_title(given), expected)



if __name__ == "__main__":
    unittest.main()
