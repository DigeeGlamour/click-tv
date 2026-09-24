"""ধারা ৪.১ - one vantage refusing may stop a film ARRIVING, never make one DISAPPEAR.

`manual_movie_liveness.strict_publish` probes every manual link and drops any
film that did not answer. `_probe_manual_movie_source` reports a timeout, a
transport error and an unparseable body all as `"dead"`, with a five-second
budget against a CDN - so the rule was removing films from the site on evidence
the plan explicitly refuses:

    ✗ timeout / 5xx        ← সাময়িক
    ✗ 403 / geo-block      ← vantage restriction

Measured on the 2026-09-24 11:36 run: the private catalogue still listed the
films, their cards were on the site, and 46 of them were cut here. ধারা ৪.০ then
counted their streams as unexplained loss - "quarantine মানে অদৃশ্য নয়" - and
blocked the whole catalogue, 506 streams' worth.

The rule keeps its job. A film nobody has published yet still has to answer
before it appears; a film already on the site stays, with its last known links
and a note that says exactly why it is still there.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import movies  # noqa: E402

SETTINGS = {
    "manual_movie_liveness": {
        "enabled": True,
        "strict_publish": True,
        "workers": 2,
        "timeout_seconds": 2,
    }
}

DEAD = {"status": "dead", "http_status": 0, "segment_verified": False}
LIVE = {"status": "live", "http_status": 200, "segment_verified": True}


def _movie(identity="remote-manual-premam-2015", name="Premam (2015)"):
    return {
        "id": identity,
        "name": name,
        "url": "https://pub-35214751cbf1431ba7b6d74f519e61d2.r2.dev/premam.mkv",
        "stream_type": "mp4",
    }


class _Base(unittest.TestCase):
    def _run(self, movie, *, probe=DEAD, published=()):
        with patch("scanner.movies._probe_manual_movie_source",
                   return_value=dict(probe)), \
             patch("scanner.movies._published_movie_keys",
                   return_value=set(published)):
            return movies._annotate_manual_movie_liveness([movie], SETTINGS)


class TheStrictRuleStillStopsAFilmArriving(_Base):
    def test_a_film_nobody_has_published_is_refused(self):
        self.assertEqual(self._run(_movie(), published=()), [])

    def test_a_different_film_being_published_does_not_save_it(self):
        self.assertEqual(
            self._run(_movie(), published={"remote-manual-something-else"}), [])

    def test_a_live_film_is_published_as_before(self):
        published = self._run(_movie(), probe=LIVE, published=())
        self.assertEqual(len(published), 1)
        self.assertIn("passed media-depth verification",
                      published[0]["verification_note"])


class AFilmAlreadyOnTheSiteIsNotRemovedByOneRefusal(_Base):
    def test_it_stays(self):
        published = self._run(_movie(), published={"remote-manual-premam-2015"})
        self.assertEqual([item["id"] for item in published],
                         ["remote-manual-premam-2015"])

    def test_it_keeps_its_last_known_link(self):
        published = self._run(_movie(), published={"remote-manual-premam-2015"})
        self.assertTrue(published[0]["url"])

    def test_it_does_not_claim_to_have_been_verified(self):
        """The one thing worse than dropping it."""
        published = self._run(_movie(), published={"remote-manual-premam-2015"})
        note = published[0]["verification_note"]
        self.assertNotIn("passed media-depth verification", note)
        self.assertIn("no playback source answered", note)
        self.assertTrue(published[0]["retained_after_failed_check"])

    def test_the_probe_result_is_still_recorded_on_the_card(self):
        """Kept visible is not the same as kept quiet: the link ledger and the
        repair queue read this."""
        published = self._run(_movie(), published={"remote-manual-premam-2015"})
        self.assertEqual(published[0]["manual_liveness_status"], "dead")

    def test_a_key_is_matched_the_way_retention_matches_it(self):
        """Both sides have to mean the same card or neither rule works."""
        from scanner import movie_recency

        movie = _movie()
        self.assertEqual(movies._movie_key(movie),
                         movie_recency.movie_key(movie))


class TheLookupCannotCostAScan(unittest.TestCase):
    def test_an_unreadable_catalogue_leaves_the_old_behaviour(self):
        with patch("scanner.movie_baseline.published_movies",
                   side_effect=OSError("no catalogue")):
            self.assertEqual(movies._published_movie_keys(), set())

    def test_the_real_catalogue_answers_with_the_cards_it_has(self):
        keys = movies._published_movie_keys()
        if not keys:
            self.skipTest("no published movie catalogue in this checkout")
        self.assertNotIn("", keys)
        self.assertTrue(all(isinstance(key, str) for key in keys))


class TheRuleIsReadableInTheSource(unittest.TestCase):
    SOURCE = (ROOT / "scanner" / "movies.py").read_text(encoding="utf-8")

    def test_the_drop_is_conditional_on_never_having_been_published(self):
        body = self.SOURCE[self.SOURCE.index(
            "def _annotate_manual_movie_liveness("):]
        body = body[:body.index("\ndef ", 10)]
        strict = body.index("if strict_publish and not live_sources:")
        self.assertIn("already_published", body[strict:strict + 1400])
        self.assertLess(body.index("already_published = "), strict)


if __name__ == "__main__":
    unittest.main()
