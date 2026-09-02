"""`Bundesliga` on its own reads as the German one. The card has to say which.

Every case here was on the site on 2026-09-02. None of the cards was wrong
about the fixture; each was wrong about which country's competition it belongs
to, which is the thing a viewer reads the label for.
"""
import unittest

from scanner import competition_labels as cl


class TheTeamsNameTheCountry(unittest.TestCase):
    OBSERVED = [
        ("Bundesliga", "Austria Vienna vs WSG Wattens", "Austrian Bundesliga"),
        ("Bundesliga", "Red Bull Salzburg vs Rapid Vienna", "Austrian Bundesliga"),
        ("Premier League", "Dinamo Minsk vs Naftan",
         "Belarusian Premier League"),
        ("Premier League", "ML Vitebsk vs Belshina",
         "Belarusian Premier League"),
        ("Cup", "Znicz Pruszkow vs Cracovia Krakow", "Polish Cup"),
        ("Cup", "Luzino vs Wisla Plock", "Polish Cup"),
        ("Serie A", "Flamengo vs Mirassol", "Brazilian Serie A"),
        ("Primera Division", "Coquimbo Unido vs Nublense",
         "Chilean Primera Division"),
        ("Championship", "Millwall vs Wrexham", "EFL Championship"),
    ]

    def test_each_observed_label_names_its_country(self):
        for label, fixture, expected in self.OBSERVED:
            with self.subTest(fixture=fixture):
                self.assertEqual(cl.clarify(label, fixture), expected)

    def test_the_famous_league_is_still_named_correctly(self):
        # Disambiguating must not rename the league everyone means.
        for label, fixture, expected in (
            ("Bundesliga", "Bayern Munich vs Dortmund", "German Bundesliga"),
            ("Premier League", "Arsenal vs Chelsea", "English Premier League"),
            ("Serie A", "Napoli vs Como", "Italian Serie A"),
            ("Primera Division", "Boca Juniors vs River Plate",
             "Argentine Primera Division"),
        ):
            with self.subTest(fixture=fixture):
                self.assertEqual(cl.clarify(label, fixture), expected)

    def test_an_accent_does_not_hide_the_team(self):
        self.assertEqual(cl.clarify("Primera División",
                                    "Coquimbo Unido vs Ñublense"),
                         "Chilean Primera Division")

    def test_the_season_survives_the_rename(self):
        self.assertEqual(cl.clarify("Bundesliga 2026/27",
                                    "Austria Vienna vs LASK"),
                         "Austrian Bundesliga 2026/27")
        self.assertEqual(cl.clarify("Serie A - Matchday 4",
                                    "Flamengo vs Mirassol"),
                         "Brazilian Serie A - Matchday 4")


class AGuessIsWorseThanAVagueTruth(unittest.TestCase):
    def test_an_unrecognised_pair_leaves_the_label_alone(self):
        self.assertEqual(cl.clarify("Bundesliga", "Unknown FC vs Other FC"), "")

    def test_a_label_that_is_already_specific_is_untouched(self):
        for label in ("Austrian Bundesliga", "LaLiga", "J-League Cup",
                      "Copa Argentina", "European T20 Premier League"):
            with self.subTest(label=label):
                self.assertEqual(cl.clarify(label, "A Team vs B Team"), "")

    def test_no_fixture_name_means_no_rename(self):
        self.assertEqual(cl.clarify("Bundesliga", ""), "")
        self.assertEqual(cl.clarify("Bundesliga", "Not A Fixture"), "")

    def test_no_label_means_no_rename(self):
        self.assertEqual(cl.clarify("", "Austria Vienna vs LASK"), "")
        self.assertEqual(cl.clarify(None, "Austria Vienna vs LASK"), "")

    def test_a_marker_inside_a_longer_word_does_not_count(self):
        # "inter" identifies Serie A, but not inside "Intercity".
        self.assertEqual(cl.clarify("Serie A", "Intercity vs Someone"), "")


class ApplyingItToCards(unittest.TestCase):
    def test_it_records_what_it_renamed(self):
        cards = [
            {"name": "Austria Vienna vs WSG Wattens", "competition": "Bundesliga"},
            {"name": "Napoli vs Como", "competition": "Italian Serie A"},
        ]
        changed = cl.apply(cards)
        self.assertEqual(len(changed), 1)
        self.assertEqual(cards[0]["competition"], "Austrian Bundesliga")
        self.assertEqual(cards[0]["competition_from_source"], "Bundesliga")
        self.assertEqual(changed[0]["was"], "Bundesliga")

    def test_a_card_it_cannot_place_keeps_its_label(self):
        cards = [{"name": "Unknown FC vs Other FC", "competition": "Bundesliga"}]
        self.assertEqual(cl.apply(cards), [])
        self.assertEqual(cards[0]["competition"], "Bundesliga")
        self.assertNotIn("competition_from_source", cards[0])

    def test_junk_in_the_list_is_ignored(self):
        self.assertEqual(cl.apply([None, "junk", {}]), [])


if __name__ == "__main__":
    unittest.main()
