"""ধাপ ৩খ - a carried card is shown to classification and the migration too.

Classification (ধারা ৪.৬) and the series migration run in `process_movies`,
over the catalogue while it is still one list. Retention runs later, inside
`paginate_movie_list`, and it is what puts a carried card back. So the passes
that decide "this is an episode, it belongs in the series catalogue" had never
once seen a carried card - and a card is carried precisely when the scan could
not reach its URL.

Measured on 2026-09-26 against the published catalogue:

    movie cards whose title states a season and episode      313
      classified (series_show_key set)                         0
      verification_status                      stale_last_good 313
      retained_after_failed_scan                          True 313

    movie cards that were classified                         117
      every one of them fresh (verified_global, not carried)

313 episode cards were stuck as separate movie cards permanently, and the
fault could not clear on its own: the card is carried because its link is
unreachable, and being carried is what hid it from the step that would have
moved it. With the cards lent to the pass, the migration plans 347 cards into
91 shows with 0 skipped.

The lending is the delicate part. Retention owns the grace count that decides
when a film the source has dropped finally goes; a probe that stayed in the
list would be published with no grace count at all and would live for ever.
So every probe that the migration does not take is removed again before
grouping.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import movies  # noqa: E402

SOURCE = (ROOT / "scanner" / "movies.py").read_text(encoding="utf-8")


class TheProbesAreTheCardsTheScanDidNotFind(unittest.TestCase):
    def test_a_card_the_scan_did_find_is_not_lent(self):
        published = movies._carried_candidates([])
        self.assertTrue(published, "the repo ships a published catalogue")
        again = movies._carried_candidates(published)
        self.assertEqual(again, [])

    def test_every_probe_is_marked(self):
        for probe in movies._carried_candidates([])[:50]:
            self.assertIs(probe[movies._CARRIED_PROBE], True)

    def test_a_probe_is_a_copy_so_the_page_on_disk_is_untouched(self):
        probes = movies._carried_candidates([])
        probes[0]["name"] = "mutated"
        self.assertNotEqual(movies._carried_candidates([])[0]["name"], "mutated")

    def test_each_card_is_lent_once(self):
        from scanner import movie_retention

        probes = movies._carried_candidates([])
        keys = [movie_retention._item_key(p) for p in probes]
        self.assertEqual(len(keys), len(set(keys)))

    def test_every_probe_carries_a_category(self):
        for probe in movies._carried_candidates([])[:50]:
            self.assertTrue(str(probe.get("category") or "").strip())

    def test_a_failure_here_cannot_take_a_scan_down(self):
        block = SOURCE[SOURCE.index("def _carried_candidates("):]
        block = block[:block.index("\n\ndef ")]
        self.assertIn("except Exception", block)
        self.assertIn("return []", block)


class TheCarriedEpisodeCardsNowClassify(unittest.TestCase):
    """The 313 the viewer was seeing as separate cards."""

    @classmethod
    def setUpClass(cls):
        cls.probes = movies._carried_candidates([])
        movies._annotate_classification(cls.probes)

    def test_an_explicit_episode_title_is_recognised(self):
        explicit = [
            p for p in self.probes
            if p.get("series_evidence_tier") == "explicit_episode"
        ]
        self.assertGreaterEqual(len(explicit), 250)

    def test_they_are_grouped_into_shows_not_left_alone(self):
        keyed = {p.get("series_show_key") for p in self.probes
                 if p.get("series_show_key")}
        self.assertGreater(len(keyed), 50)

    def test_a_show_with_many_episodes_is_one_show(self):
        counts = {}
        for probe in self.probes:
            key = probe.get("series_show_key")
            if key:
                counts[key] = counts.get(key, 0) + 1
        self.assertGreater(max(counts.values()), 20)

    def test_no_episode_number_is_invented(self):
        """The plan's rule. A card whose title states no episode never gets
        one attached here."""
        for probe in self.probes:
            if probe.get("series_evidence_tier") == "explicit_episode":
                continue
            self.assertIsNone(probe.get("episode_number"))


class RetentionStaysTheOwner(unittest.TestCase):
    def test_probes_are_removed_before_grouping(self):
        lend = SOURCE.index("merged_movies = list(merged_movies) + probes")
        drop = SOURCE.index(
            "if not (isinstance(movie, dict) and movie.get(_CARRIED_PROBE))")
        group = SOURCE.index("grouped_movies: Dict[str, List[Dict[str, Any]]] = {")
        self.assertLess(lend, drop)
        self.assertLess(drop, group)

    def test_the_lending_happens_before_classification(self):
        lend = SOURCE.index("probes = _carried_candidates(merged_movies)")
        classify = SOURCE.index("_annotate_classification(merged_movies)")
        self.assertLess(lend, classify)

    def test_the_removal_happens_after_the_migration(self):
        migrate = SOURCE.index("merged_movies = _migrate_series_cards(merged_movies, settings)")
        drop = SOURCE.index(
            "if not (isinstance(movie, dict) and movie.get(_CARRIED_PROBE))")
        self.assertLess(migrate, drop)

    def test_retention_is_still_what_publishes_a_carried_card(self):
        self.assertIn("prepared = _retain_recent_dropouts(prepared, category_slug)", SOURCE)

    def test_a_migrated_card_is_registered_so_retention_lets_it_go(self):
        """`_MIGRATED_CARD_IDS` is how ধাপ ৩খ tells retention that a card
        moved rather than went missing."""
        self.assertIn("_remember_series_migration(plan[\"series_items\"])", SOURCE)
        self.assertIn("moved_cards=_MIGRATED_CARD_IDS", SOURCE)


if __name__ == "__main__":
    unittest.main()
