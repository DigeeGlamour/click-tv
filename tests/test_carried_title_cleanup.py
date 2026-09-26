"""A carried card is still a published card, so it gets the same title pass.

`_finalize_movie_presentation` cleans the list the scan built.
`_retain_recent_dropouts` runs afterwards and adds cards read straight off the
previous pages - so a film that has been carried since before the title pass
existed keeps its scene name for ever. On any given run about a third of this
catalogue is carried, and on 2026-09-26 the site was publishing:

    Kalinga 2024 Bengali ORG
    Tron Ares (2025) AMZN ESub
    Furiosa a Mad Max Saga 2024 AMZN Dual Audio Hindi English
    War 2019 BluRay

97 titles like that, 82 of them carried cards the cleaner had never been shown.
The cleaner handles every one of them correctly - it was simply never asked.

Only what is DISPLAYED changes. The id is untouched, so retention's own ledger,
the no-loss gate and anything a viewer saved still point at the same card.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import movies  # noqa: E402


def _carried(name, **extra):
    card = {"id": "some-id", "name": name, "retained_after_failed_scan": True}
    card.update(extra)
    return card


class ACarriedTitleIsCleaned(unittest.TestCase):
    def test_the_measured_titles_are_all_fixed(self):
        cards = [
            _carried("Kalinga 2024 Bengali ORG"),
            _carried("Tron Ares (2025) AMZN ESub"),
            _carried("War 2019 BluRay"),
            _carried("Furiosa a Mad Max Saga 2024 AMZN Dual Audio Hindi English"),
            _carried("Drishyam 3 (2026) Hindi Dubbed HQ"),
        ]
        self.assertEqual(movies._clean_carried_titles(cards), 5)
        self.assertEqual([card["name"] for card in cards],
                         ["Kalinga", "Tron Ares", "War",
                          "Furiosa a Mad Max Saga", "Drishyam 3"])

    def test_the_year_the_name_loses_is_recovered(self):
        card = _carried("War 2019 BluRay")
        movies._clean_carried_titles([card])
        self.assertEqual(card["year"], 2019)

    def test_a_year_already_known_is_not_overwritten(self):
        card = _carried("War 2019 BluRay", year=2020)
        movies._clean_carried_titles([card])
        self.assertEqual(card["year"], 2020)

    def test_the_change_is_recorded_on_the_card(self):
        card = _carried("War 2019 BluRay")
        movies._clean_carried_titles([card])
        self.assertTrue(card["title_cleaned_on_carry"])

    def test_the_id_never_moves(self):
        """What identifies the card is what retention, the gate and a saved
        watchlist all key on."""
        card = _carried("War 2019 BluRay", id="war-2019-bluray")
        movies._clean_carried_titles([card])
        self.assertEqual(card["id"], "war-2019-bluray")


class OnlyCarriedCardsAreTouched(unittest.TestCase):
    def test_a_fresh_card_is_left_alone(self):
        """It has already been through the real pass, which knows about
        collisions across the whole list."""
        card = {"id": "x", "name": "Some Film 2024 BluRay"}
        self.assertEqual(movies._clean_carried_titles([card]), 0)
        self.assertEqual(card["name"], "Some Film 2024 BluRay")

    def test_a_title_the_cleaner_cannot_improve_is_returned_untouched(self):
        card = _carried("Mayashalik")
        self.assertEqual(movies._clean_carried_titles([card]), 0)
        self.assertEqual(card["name"], "Mayashalik")

    def test_a_season_marker_survives(self):
        """Cutting at a bare season marker turns four rows of one show into
        four rows with one name, which the published-output validator refuses."""
        card = _carried("Reacher S01")
        movies._clean_carried_titles([card])
        self.assertEqual(card["name"], "Reacher S01")

    def test_rubbish_is_survived_rather_than_raising(self):
        for cards in ([], [None], [{}], [{"retained_after_failed_scan": True}]):
            with self.subTest(cards=cards):
                self.assertEqual(movies._clean_carried_titles(cards), 0)


class ItRunsWhereTheCollisionGuardCanSeeIt(unittest.TestCase):
    SOURCE = (ROOT / "scanner" / "movies.py").read_text(encoding="utf-8")

    def test_retention_calls_it(self):
        body = self.SOURCE[self.SOURCE.index("def _retain_recent_dropouts("):]
        body = body[:body.index("\ndef ", 10)]
        self.assertIn("_clean_carried_titles(kept)", body)

    def test_the_collision_guard_still_runs_after_retention(self):
        """A rename that made two cards indistinguishable has to be undone, and
        the guard that does it reads the list AFTER retention has added to it."""
        paginate = self.SOURCE[self.SOURCE.index("if retain_recent_dropouts:"):]
        paginate = paginate[:paginate.index("_collisions[") + 200]
        self.assertLess(paginate.index("_retain_recent_dropouts("),
                        paginate.index("_resolve_presentation_collisions("))

    def test_it_cannot_fail_a_scan(self):
        body = self.SOURCE[self.SOURCE.index("def _retain_recent_dropouts("):]
        body = body[:body.index("\ndef ", 10)]
        self.assertIn("except Exception", body)


if __name__ == "__main__":
    unittest.main()
