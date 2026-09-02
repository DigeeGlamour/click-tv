"""One match, one card - and two different matches, two cards.

Every folding case here was observed in data/today-match.json or
data/upcoming.json; every non-folding case is the near miss that makes a
looser rule dangerous.
"""
import unittest

from scanner import fixture_dedupe as fd


KICKOFF = "2026-08-30T18:45:00+00:00"
LATER = "2026-08-30T20:00:00+00:00"


def card(name, kickoff=KICKOFF, **extra):
    row = {"name": name, "start_time": kickoff}
    row.update(extra)
    return row


def folds(left, right, kickoff=KICKOFF, other=None):
    return fd.same_fixture(card(left, kickoff), card(right, other or kickoff))


class TheSpellingsSeenInProduction(unittest.TestCase):
    OBSERVED = [
        ("Cagliari vs Inter", "Cagliari Vs Inter Milan"),
        ("Argentinos JRS vs Aldosivi", "Argentinos Juniors Vs Aldosivi"),
        ("Deportivo vs Valencia", "Deportivo de A Coruna Vs Valencia"),
        ("Independ Rivadavia vs Racing Club",
         "Independiente Rivadavia Vs Racing Club"),
    ]

    def test_each_observed_pair_folds(self):
        for left, right in self.OBSERVED:
            with self.subTest(left=left):
                self.assertTrue(folds(left, right))

    def test_the_order_of_the_two_cards_does_not_matter(self):
        for left, right in self.OBSERVED:
            with self.subTest(left=left):
                self.assertEqual(folds(left, right), folds(right, left))

    def test_a_dropped_suffix_folds(self):
        self.assertTrue(folds("Al Wahda FC vs Baniyas", "Al Wahda vs Baniyas SC"))

    def test_a_common_abbreviation_folds(self):
        self.assertTrue(folds("Man Utd vs Arsenal", "Man United vs Arsenal"))


class TwoDifferentMatchesStayApart(unittest.TestCase):
    """The near misses. Each shares a kickoff and one side."""

    APART = [
        ("Manchester United vs Arsenal", "Manchester City vs Arsenal"),
        ("Real Madrid vs Barcelona", "Real Sociedad vs Barcelona"),
        # "san" is the letters of "santos" in order, which is why letters-in-
        # order alone is not an abbreviation test.
        ("San Lorenzo vs Boca", "Santos vs Boca"),
        ("Sporting vs Porto", "Sporting Gijon vs Sevilla"),
        ("Inter vs Milan", "Inter Milan vs Roma"),
    ]

    def test_each_pair_stays_apart(self):
        for left, right in self.APART:
            with self.subTest(left=left):
                self.assertFalse(folds(left, right))

    def test_a_different_kickoff_never_folds(self):
        self.assertFalse(folds("Cagliari vs Inter", "Cagliari Vs Inter Milan",
                               other=LATER))

    def test_a_missing_kickoff_never_folds(self):
        # Without a kickoff the anchor is gone, and one shared side is not
        # enough on its own.
        self.assertFalse(fd.same_fixture(card("A Team vs B Team", ""),
                                         card("A Team vs B Team", "")))

    def test_a_title_that_is_not_a_fixture_never_folds(self):
        self.assertFalse(folds("Top End T20 Series 2026", "Top End T20 Series"))
        self.assertIsNone(fd.sides(card("Day 4 2nd Test")))


class AbbreviationRules(unittest.TestCase):
    def test_an_abbreviation_drops_its_vowels(self):
        self.assertTrue(fd._is_initialism("jrs", "juniors"))
        self.assertTrue(fd._is_initialism("utd", "united"))
        self.assertFalse(fd._is_initialism("san", "santos"))

    def test_a_short_token_is_not_a_truncation(self):
        # "San" starts "Santos" but is too short to be evidence of anything.
        self.assertFalse(fd._same_token("san", "santos"))
        self.assertTrue(fd._same_token("independ", "independiente"))

    def test_a_longer_form_agrees_only_on_shared_tokens(self):
        self.assertTrue(fd.same_side("deportivo", "deportivo de a coruna"))
        self.assertFalse(fd.same_side("manchester united", "manchester city"))


class FoldingKeepsTheRicherCard(unittest.TestCase):
    def test_the_card_with_more_routes_survives(self):
        thin = card("Cagliari vs Inter", url="https://a.test/x.m3u8")
        rich = card("Cagliari Vs Inter Milan", url="https://b.test/y.m3u8",
                    channels=[{"name": "One"}, {"name": "Two"}],
                    backups=[{"url": "https://c.test/z.m3u8"}])
        kept, report = fd.fold([thin, rich])
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["name"], "Cagliari Vs Inter Milan")
        self.assertEqual(report[0]["folded"], "Cagliari vs Inter")

    def test_the_folded_card_gives_up_its_route_rather_than_losing_it(self):
        thin = card("Cagliari vs Inter", url="https://a.test/x.m3u8")
        rich = card("Cagliari Vs Inter Milan", url="https://b.test/y.m3u8",
                    channels=[{"name": "One"}])
        kept, _ = fd.fold([thin, rich])
        urls = {str(b.get("url")) for b in kept[0]["backups"]}
        self.assertIn("https://a.test/x.m3u8", urls)

    def test_channels_and_sources_are_combined(self):
        left = card("Cagliari vs Inter", url="https://a.test/x.m3u8",
                    channels=[{"name": "One"}], source_ids=["feed-a"])
        right = card("Cagliari Vs Inter Milan", url="https://b.test/y.m3u8",
                     channels=[{"name": "Two"}], source_ids=["feed-b"],
                     backups=[{"url": "https://c.test/z.m3u8"}])
        kept, _ = fd.fold([left, right])
        self.assertEqual({c["name"] for c in kept[0]["channels"]}, {"One", "Two"})
        self.assertEqual(set(kept[0]["source_ids"]), {"feed-a", "feed-b"})

    def test_a_route_already_present_is_not_duplicated(self):
        shared = "https://a.test/x.m3u8"
        left = card("Cagliari vs Inter", url=shared)
        right = card("Cagliari Vs Inter Milan", url=shared,
                     channels=[{"name": "One"}])
        kept, _ = fd.fold([left, right])
        urls = [str(b.get("url")) for b in kept[0].get("backups") or []]
        self.assertEqual(urls.count(shared), 0)

    def test_two_different_matches_both_survive(self):
        kept, report = fd.fold([
            card("Manchester United vs Arsenal"),
            card("Manchester City vs Arsenal"),
        ])
        self.assertEqual(len(kept), 2)
        self.assertEqual(report, [])

    def test_junk_in_the_list_is_ignored(self):
        kept, _ = fd.fold([None, "junk", card("A Team vs B Team")])
        self.assertEqual(len(kept), 1)

    def test_three_spellings_of_one_fixture_become_one_card(self):
        kept, report = fd.fold([
            card("Cagliari vs Inter"),
            card("Cagliari Vs Inter Milan"),
            card("Cagliari FC vs Inter"),
        ])
        self.assertEqual(len(kept), 1)
        self.assertEqual(len(report), 2)


if __name__ == "__main__":
    unittest.main()
