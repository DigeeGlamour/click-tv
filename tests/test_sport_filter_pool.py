"""Pool is not football, whatever country the event is in."""
import unittest

from scanner.sport_filter import classify


class PoolTests(unittest.TestCase):
    def test_predator_championship_is_refused_even_when_a_feed_says_football(self):
        # 2026-10-22: published on Today Match as football because of "Spain".
        verdict = classify({"name": "Predator European Championships Spain", "sport_type": "football"})
        self.assertEqual(verdict["state"], "confirmed_other")

    def test_ball_formats_are_pool(self):
        self.assertEqual(classify({"name": "Mosconi Cup 9-Ball"})["state"], "confirmed_other")

    def test_a_country_fixture_is_still_football(self):
        verdict = classify({"name": "Spain vs France", "sport_type": "football"})
        self.assertEqual(verdict["state"], "confirmed_football")


if __name__ == "__main__":
    unittest.main()
