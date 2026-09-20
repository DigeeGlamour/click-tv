"""ধাপ ৯ / S-07 - one stamp, so the movie surfaces cannot disagree silently.

S-07's risk in the plan's words: "ক্যাটালগ ও সার্চ ইনডেক্স আলাদা সময়ে লেখা হলে
UI পুরোনো ফল দেখাতে পারে". It is a one-star item because it has not been caught
happening - except it has, once, and the fix is already in this codebase:

> Discovery was written from the 377 anyway, so Movie Home advertised films the
> catalogue did not contain - 40 of 40 in Just Added and 4 of 5 in the Featured
> hero pointed at ids with no page behind them.

That guard fixed one path. A generation makes the whole class of it detectable.

The subtle rule tested hardest here is the three-state check: surfaces that all
predate the feature are consistent *with each other*, and calling that a
disagreement would raise a warning on the first run after this ships. A check
that cries wolf once is a check people learn to ignore. A *mix* of stamped and
unstamped is the real fault.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import movie_generation as mg  # noqa: E402


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


class AllocationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.path = Path(tempfile.mkdtemp()) / "movie-generation.json"

    def test_the_first_generation_is_one(self) -> None:
        self.assertEqual(mg.allocate(self.path)["generation"], 1)

    def test_it_is_monotonic(self) -> None:
        numbers = [mg.allocate(self.path)["generation"] for _ in range(4)]
        self.assertEqual(numbers, [1, 2, 3, 4])

    def test_it_is_persisted_before_anything_is_stamped_with_it(self) -> None:
        """A crash between allocating and publishing wastes a number, which
        costs nothing. Reusing one would let two catalogues claim to be the
        same generation, which is the one thing this must never allow."""
        mg.allocate(self.path)
        self.assertEqual(mg.load(self.path)["generation"], 1)
        self.assertEqual(mg.allocate(self.path)["generation"], 2)

    def test_a_damaged_state_file_restarts_rather_than_crashing(self) -> None:
        self.path.write_text("{not json", encoding="utf-8")
        self.assertEqual(mg.allocate(self.path)["generation"], 1)

    def test_an_unwritable_state_file_still_returns_a_generation(self) -> None:
        """An unwritable state file is not a reason to refuse to publish."""
        directory = Path(tempfile.mkdtemp())
        state = mg.allocate(directory)  # a directory, so the write fails
        self.assertEqual(state["generation"], 1)


class StampTests(unittest.TestCase):
    def test_stamping_adds_only(self) -> None:
        payload = {"count": 3, "items": [1, 2, 3]}
        mg.stamp(payload, 7, "2026-09-20T00:00:00+00:00")
        self.assertEqual(payload["count"], 3)
        self.assertEqual(payload["items"], [1, 2, 3])
        self.assertEqual(payload[mg.FIELD], 7)

    def test_a_bare_list_is_returned_untouched(self) -> None:
        """Inventing a wrapper would change the published shape."""
        payload = [1, 2, 3]
        self.assertIs(mg.stamp(payload, 7), payload)

    def test_every_page_is_stamped_not_just_the_index(self) -> None:
        """The failure guarded against is a page belonging to a different scan
        than the index counting it."""
        paginated = {
            "Bangla": {
                "index": {"slug": "bangla", "count": 2},
                "page_contents": {
                    "page-001.json": {"count": 1},
                    "page-002.json": {"count": 1},
                },
            }
        }
        mg.stamp_paginated(paginated, 12, "2026-09-20T00:00:00+00:00")
        self.assertEqual(paginated["Bangla"]["index"][mg.FIELD], 12)
        for page in paginated["Bangla"]["page_contents"].values():
            self.assertEqual(page[mg.FIELD], 12)

    def test_a_malformed_category_does_not_raise(self) -> None:
        mg.stamp_paginated({"Bangla": "not a mapping"}, 1)


class ConsistencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp()) / "data"
        self.files = [
            self.root / "movies" / "bangla" / "index.json",
            self.root / "movies" / "bangla" / "page-001.json",
            self.root / "movies" / "search-index.json",
            self.root / "movies" / "discovery" / "home.json",
            self.root / "movies" / "genres" / "drama.json",
        ]

    def _publish(self, generation=None, *, exclude=()):
        for path in self.files:
            payload = {"count": 1}
            if generation is not None and path not in exclude:
                mg.stamp(payload, generation, "2026-09-20T00:00:00+00:00")
            _write(path, payload)

    def test_surfaces_that_all_predate_the_feature_are_not_a_disagreement(self) -> None:
        """A check that cries wolf on the first run is one people ignore."""
        self._publish(None)
        report = mg.check(self.root)
        self.assertTrue(report["consistent"])
        self.assertTrue(report["not_yet_stamped"])
        self.assertIn("no generation yet", mg.describe(report))

    def test_one_consistent_publish_is_consistent(self) -> None:
        self._publish(461)
        report = mg.check(self.root)
        self.assertTrue(report["consistent"])
        self.assertEqual(report["current_generation"], 461)
        self.assertEqual(report["distinct_generations"], 1)

    def test_a_search_index_left_behind_is_caught_and_named(self) -> None:
        """The exact S-07 failure: the catalogue and the search index written
        at different moments."""
        self._publish(461)
        stale = self.root / "movies" / "search-index.json"
        _write(stale, mg.stamp({"count": 1}, 460, "2026-09-19T00:00:00+00:00"))

        report = mg.check(self.root)
        self.assertFalse(report["consistent"])
        self.assertEqual(report["distinct_generations"], 2)
        self.assertIn("movies/search-index.json", report["sample_mismatch"])
        self.assertIn("DISAGREE", mg.describe(report))

    def test_a_mix_of_stamped_and_unstamped_is_a_disagreement(self) -> None:
        """Then something was rewritten and something was not."""
        stale = self.root / "movies" / "genres" / "drama.json"
        self._publish(461, exclude={stale})
        report = mg.check(self.root)
        self.assertFalse(report["consistent"])
        self.assertEqual(report["unstamped_files"], 1)
        self.assertIn("movies/genres/drama.json", report["sample_mismatch"])

    def test_every_movie_surface_is_covered(self) -> None:
        """A file missing from the list is a file whose staleness nobody would
        notice, which is the whole failure S-07 describes."""
        self._publish(1)
        self.assertEqual(sum(mg.check(self.root)["generations"].values()),
                         len(self.files))

    def test_a_damaged_published_file_is_skipped_not_fatal(self) -> None:
        self._publish(1)
        (self.root / "movies" / "search-index.json").write_text(
            "{not json", encoding="utf-8")
        report = mg.check(self.root)
        self.assertTrue(report["consistent"])

    def test_an_empty_data_root_is_consistent_not_an_error(self) -> None:
        report = mg.check(Path(tempfile.mkdtemp()))
        self.assertTrue(report["consistent"])


class WiringTests(unittest.TestCase):
    MOVIES = (ROOT / "scanner" / "movies.py").read_text(encoding="utf-8")
    OUTPUT = (ROOT / "scanner" / "output.py").read_text(encoding="utf-8")

    def test_the_generation_is_allocated_before_the_derived_surfaces(self) -> None:
        """They have to carry the same number as the pages they describe."""
        allocate = self.MOVIES.index("_allocate_movie_generation(paginated)")
        discovery = self.MOVIES.index("_generate_discovery(paginated)")
        self.assertLess(allocate, discovery)

    def test_the_derived_surfaces_are_stamped_after_being_written(self) -> None:
        discovery = self.MOVIES.index("_generate_discovery(paginated)")
        stamp = self.MOVIES.index("_stamp_derived_movie_surfaces(generation)")
        self.assertLess(discovery, stamp)

    def test_nothing_is_stamped_when_the_output_is_being_preserved(self) -> None:
        """A preserved publish writes nothing, so stamping it would claim a new
        generation for files that did not move."""
        preserved = self.MOVIES.index("_movie_output_would_be_preserved(paginated, settings)")
        allocate = self.MOVIES.index("_allocate_movie_generation(paginated)")
        self.assertLess(preserved, allocate)

    def test_the_check_reaches_the_scan_summary(self) -> None:
        self.assertIn('"movie_generation": movie_generation_report', self.OUTPUT)
        self.assertIn(
            "movie: published surfaces disagree about their generation",
            self.OUTPUT,
        )

    def test_a_generation_failure_never_costs_a_scan(self) -> None:
        for source, marker in ((self.MOVIES, "movie generation skipped"),
                               (self.OUTPUT, "movie generation check skipped")):
            self.assertIn(marker, source)


if __name__ == "__main__":
    unittest.main()
