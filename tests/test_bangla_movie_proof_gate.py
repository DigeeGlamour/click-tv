"""The Bangla player-proof gate obeys the flag that switches it off.

`scanner/channels.py` has always required two flags together:

    if strict_player_publish and bangla_requires_player_proof:

`scanner/player_compatibility.py` documents the same thing in a comment -
"This gate is currently off (bangla_requires_player_proof = false)" - and the
shipped configuration does set it to false.

`scanner/movies.py` read only `strict_player_publish`, so in the movie
pipeline a gate the configuration had switched off ran anyway, and it ran
against a ledger of 45 proofs.

Measured on 2026-09-26 from `reports/manual-movie-integrity.json`, against
the owner's private source, which offers 94 Bangla films:

    Dubbed        97 /  97 published
    Hindi         82 /  82
    South Indian 142 / 142
    English       79 /  79
    Premium       25 /  25
    Bangla         0 /   0      <- every one dropped

Nothing is loosened by fixing this. `is_confirmed_player_failure` runs a few
lines earlier and still removes any film a real browser has been seen to fail
on; that gate is evidence of failure. This one demanded evidence of success
that almost no card can have, and it applied to one category only.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MOVIES = (ROOT / "scanner" / "movies.py").read_text(encoding="utf-8")
CHANNELS = (ROOT / "scanner" / "channels.py").read_text(encoding="utf-8")
SETTINGS = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))


class TheTwoPipelinesAgree(unittest.TestCase):
    GUARD = "if strict_player_publish and bangla_requires_player_proof:"

    def test_the_channel_pipeline_requires_both_flags(self):
        self.assertIn(self.GUARD, CHANNELS)

    def test_the_movie_pipeline_requires_both_flags(self):
        self.assertIn(self.GUARD, MOVIES)

    def test_the_movie_pipeline_reads_the_second_flag_from_settings(self):
        self.assertIn('bd_settings.get("bangla_requires_player_proof", False)', MOVIES)

    def test_neither_pipeline_gates_on_the_first_flag_alone(self):
        for name, source in (("movies", MOVIES), ("channels", CHANNELS)):
            self.assertNotIn(
                'if isinstance(bd_settings, dict) and bool('
                'bd_settings.get("strict_player_publish", False)):',
                source,
                name,
            )


class TheShippedConfigurationKeepsTheGateOff(unittest.TestCase):
    """If someone turns it back on, they turn it on for both pipelines at
    once and they do it on purpose - which is the point of the flag."""

    def setUp(self):
        self.bd = SETTINGS["bd_verification"]

    def test_the_flag_exists(self):
        self.assertIn("bangla_requires_player_proof", self.bd)

    def test_it_is_off(self):
        self.assertFalse(self.bd["bangla_requires_player_proof"])


class TheEvidenceBasedGateIsUntouched(unittest.TestCase):
    """A film a real browser has been seen to fail on is still removed. That
    gate proves failure; the one above demanded proof of success."""

    def test_confirmed_failures_are_still_filtered(self):
        self.assertIn('is_confirmed_player_failure(movie, "movie", failure_keys)', MOVIES)

    def test_it_runs_before_the_proof_gate(self):
        failure = MOVIES.index('is_confirmed_player_failure(movie, "movie", failure_keys)')
        proof = MOVIES.index("if strict_player_publish and bangla_requires_player_proof:")
        self.assertLess(failure, proof)

    def test_it_applies_to_every_category_not_just_bangla(self):
        block = MOVIES[MOVIES.index("mark_confirmed_player_failures(merged_movies"):]
        block = block[:block.index("bd_settings = settings.get")]
        self.assertNotIn("Bangla", block)


class TheGateStillWorksWhenTurnedOn(unittest.TestCase):
    def test_turning_it_on_still_marks_and_filters(self):
        block = MOVIES[MOVIES.index(
            "if strict_player_publish and bangla_requires_player_proof:"):]
        block = block[:block.index("# ধারা ৪.৬")]
        self.assertIn('mark_unproven_player_items(bangla_movies, "movie")', block)
        self.assertIn('is_player_proven(movie, "movie", proof_keys)', block)


if __name__ == "__main__":
    unittest.main()
