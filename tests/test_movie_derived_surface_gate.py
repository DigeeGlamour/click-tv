"""The surfaces that DESCRIBE the catalogue may not outrun the catalogue.

This is the failure, measured on the first real movies run after ধাপ ১৩:

    catalogue ids          1,663     (pages preserved by the no-loss gate)
    discovery ids             39     written from the NEW payload
    of those, with no page    35
    search-index ids       1,887 -> 562 with no page

Movie Home advertised 35 films a viewer could not open, and search offered 562.

`scanner/movies.py` already carried a guard for exactly this shape, written
after the same thing happened on 2026-09-17. It asked one question - "will the
sudden-drop protection keep the previous pages?" - and that was one of the
**two** ways the pages can be preserved. The other is ধারা ৪.০'s no-loss gate,
and it had not answered yet: it needs the paginated payload and the prepared
series, so it runs in scan.py after process_movies returns. On that run the
drop guard did not trip at all, because the incoming catalogue was BIGGER
(1,831 against 1,663). It was the gate that blocked.

So the fix is not a second copy of the question. It is not answering it early:
the derived surfaces are written after the publish decision is known, by
whoever knows it.
"""
from __future__ import annotations

import ast
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MOVIES = (ROOT / "scanner" / "movies.py").read_text(encoding="utf-8")
SCAN = (ROOT / "scan.py").read_text(encoding="utf-8")


def _function(source: str, name: str):
    tree = ast.parse(source)
    return next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def _calls(node) -> set:
    found = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            func = child.func
            if isinstance(func, ast.Name):
                found.add(func.id)
            elif isinstance(func, ast.Attribute):
                found.add(func.attr)
    return found


DERIVED_WRITERS = (
    "_generate_discovery",
    "_generate_trending",
    "_generate_genre_indexes",
    "_stamp_derived_movie_surfaces",
    "_allocate_movie_generation",
)


class DeferringThemIsPossibleAtAll(unittest.TestCase):
    def test_process_movies_takes_the_deferral(self):
        self.assertIn("defer_derived: bool = False", MOVIES)

    def test_a_deferred_run_writes_no_derived_surface(self):
        """The whole point: nothing that describes the catalogue is written
        before somebody has decided the catalogue is being published."""
        from scanner import movies

        body = _function(MOVIES, "process_movies")
        # The deferral must return before any writer is reached. Proven by
        # reading the branch rather than by trusting the ordering of lines.
        source = ast.get_source_segment(MOVIES, body) or ""
        head = source[:source.index("publish_derived_surfaces(")]
        self.assertIn("if defer_derived:", head)
        self.assertIn("return paginated", head[head.index("if defer_derived:"):])
        self.assertTrue(hasattr(movies, "publish_derived_surfaces"))

    def test_every_writer_now_lives_behind_the_decision(self):
        published = _calls(_function(MOVIES, "publish_derived_surfaces"))
        for writer in DERIVED_WRITERS:
            with self.subTest(writer=writer):
                self.assertIn(writer, published)

    def test_process_movies_itself_reaches_them_only_through_that_door(self):
        """A caller that does not run the gate still gets its surfaces - the
        deferral is opt-in, so nothing outside scan.py changes behaviour."""
        direct = _calls(_function(MOVIES, "process_movies"))
        for writer in DERIVED_WRITERS:
            with self.subTest(writer=writer):
                self.assertNotIn(writer, direct)
        self.assertIn("publish_derived_surfaces", direct)

    def test_the_drop_guard_is_still_asked(self):
        """The older of the two preservation paths keeps its guard."""
        self.assertIn(
            "_movie_output_would_be_preserved",
            _calls(_function(MOVIES, "publish_derived_surfaces")),
        )


class TheRealCallerDefersAndThenDecides(unittest.TestCase):
    def test_the_scan_defers(self):
        self.assertIn("process_movies(defer_derived=True)", SCAN)

    def test_it_publishes_them_only_after_the_gate_is_built(self):
        gate = SCAN.index("movie_coverage_report = _build_movie_coverage(")
        # The CALL, not the def - `.index` finds the definition first, and the
        # definition is allowed to live anywhere in the file.
        publish = SCAN.index(
            "_publish_movie_derived_surfaces(" + chr(10) + "            publish_derived_surfaces")
        self.assertLess(gate, publish)

    def test_a_blocked_gate_keeps_the_previous_surfaces(self):
        helper = _function(SCAN, "_publish_movie_derived_surfaces")
        source = ast.get_source_segment(SCAN, helper) or ""
        self.assertIn("publish_blocked", source)
        blocked_at = source.index("if blocked:")
        publish_at = source.index("publish(movies_data)")
        self.assertLess(blocked_at, publish_at)
        self.assertIn("return", source[blocked_at:publish_at])

    def test_it_cannot_fail_a_scan(self):
        self.assertIn("movie derived surfaces skipped", SCAN)


class NothingAdvertisesAFilmWithNoPage(unittest.TestCase):
    """The invariant itself, measured against whatever is on disk now.

    A regression test in the literal sense: this is the exact query that found
    the live breakage, so it will find the next one.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.catalogue = set()
        pages = sorted((ROOT / "data" / "movies").glob("*/page-*.json"))
        for page in pages:
            try:
                payload = json.loads(page.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            for item in payload.get("items") or []:
                if isinstance(item, dict) and item.get("id"):
                    cls.catalogue.add(item["id"])
        cls.pages = pages

    def _movie_ids(self, relative):
        """Every MOVIE id a surface advertises.

        Three shapes to read, and getting any of them wrong makes this pass
        without measuring anything:

          * discovery rows are objects carrying `id`
          * the search index mixes `type: "movie"` with `type: "series"` - the
            61 series entries have a series page, not a movie page, so folding
            them in would invent 61 failures
          * genre files hold bare id strings
        """
        path = ROOT / "data" / "movies" / relative
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        found = set()

        # A genre page is `{"genre": ..., "items": ["<id>", ...]}` - bare
        # strings, and the only surface where a string IS an id. Reading
        # strings anywhere else picks up `genres: ["Drama", ...]` off a search
        # row and invents failures named after genres.
        if isinstance(payload, dict) and relative.startswith("genres/"):
            return {
                item for item in payload.get("items") or ()
                if isinstance(item, str) and item
            }

        def walk(node):
            if isinstance(node, dict):
                kind = str(node.get("type") or node.get("content_kind") or "")
                if node.get("id") and kind in ("", "movie"):
                    found.add(node["id"])
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk(payload)
        return found

    def test_there_is_a_catalogue_to_measure_against(self):
        if not self.pages:
            self.skipTest("no published movie catalogue in this checkout")
        self.assertTrue(self.catalogue)

    def test_discovery_advertises_only_films_that_have_a_page(self):
        if not self.catalogue:
            self.skipTest("no published movie catalogue in this checkout")
        ids = self._movie_ids("discovery/home.json")
        if ids is None:
            self.skipTest("no discovery surface published")
        dangling = sorted(ids - self.catalogue)
        self.assertEqual(
            dangling[:10], [],
            f"{len(dangling)} Just Added / Featured id(s) have no page behind "
            "them",
        )

    def test_the_search_index_offers_only_films_that_have_a_page(self):
        if not self.catalogue:
            self.skipTest("no published movie catalogue in this checkout")
        ids = self._movie_ids("search-index.json")
        if ids is None:
            self.skipTest("no search index published")
        dangling = sorted(ids - self.catalogue)
        self.assertEqual(
            dangling[:10], [],
            f"{len(dangling)} search result(s) have no page behind them",
        )


    def test_every_genre_page_lists_only_films_that_have_a_page(self):
        if not self.catalogue:
            self.skipTest("no published movie catalogue in this checkout")
        pages = sorted((ROOT / "data" / "movies" / "genres").glob("*.json"))
        if not pages:
            self.skipTest("no genre surfaces published")
        listed = 0
        for page in pages:
            ids = self._movie_ids(f"genres/{page.name}")
            listed += len(ids or ())
            dangling = sorted((ids or set()) - self.catalogue)
            with self.subTest(genre=page.name):
                self.assertEqual(dangling[:10], [], f"{page.name}: "
                                 f"{len(dangling)} id(s) with no page")
        self.assertTrue(listed, "the genre surfaces list nothing at all")


if __name__ == "__main__":
    unittest.main()
