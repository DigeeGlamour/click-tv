"""ধাপ ৩খ - a card that MOVED is not a card that went missing.

The migration takes a proven episode card out of the movie catalogue and puts
it in the series catalogue. `movie_retention` then read the movie list, could
not find that card, and published it straight back as `stale_last_good` - so
every episode ended up on the site twice over, once inside its show and once as
the loose movie card ধাপ ৩খ exists to remove.

Measured on the 2026-09-24 16:30 run:

    series migration: 336 card(s) and 391 stream(s) moved into 110 show(s)
    ...and 313 episode-titled movie cards published anyway, every one of them
    carrying `retained_through_incomplete_scan: true`

It did second damage too. `mix` reported 184 of 937 previously published cards
"found", because 753 of the missing ones had been migrated - and a category
that looks 80% missing is judged a broken scan, which froze the whole of Mix.

So retention is now TOLD what moved. Never inferred: the only thing that can
mark a card as moved is the migration recording that it moved it.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import movie_retention as mrt  # noqa: E402
from scanner import movies  # noqa: E402

URL = "https://cdn.test/movies/reacher-s04e06.mkv"


class RetentionIsToldWhatMoved(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name) / "movies"
        (self.root / "mix").mkdir(parents=True)
        self.episode = {
            "id": "reacher-2026-s04e06-dual",
            "name": "Reacher (2026) S04E06 Dual",
            "url": URL,
        }
        self.others = [
            {"id": f"film-{index}", "name": f"Film {index}",
             "url": f"https://cdn.test/movies/f{index}.mkv"}
            for index in range(9)
        ]
        (self.root / "mix" / "page-001.json").write_text(
            json.dumps({"items": [self.episode] + self.others}), encoding="utf-8")
        self.path = str(Path(directory.name) / "retention.json")

    def _retain(self, incoming, **moved):
        return mrt.retain(
            incoming, "mix", root=str(self.root), path=self.path,
            now=dt.datetime(2026, 9, 24, tzinfo=dt.timezone.utc), **moved)

    def test_without_being_told_the_card_comes_straight_back(self):
        """The behaviour being fixed, kept as the thing to compare against."""
        kept, _summary = self._retain([dict(item) for item in self.others])
        self.assertIn("reacher-2026-s04e06-dual", [item["id"] for item in kept])

    def test_a_migrated_card_is_not_carried_back(self):
        kept, summary = self._retain(
            [dict(item) for item in self.others],
            moved_cards={"reacher-2026-s04e06-dual"})
        self.assertNotIn("reacher-2026-s04e06-dual", [item["id"] for item in kept])
        self.assertEqual(summary["moved_to_series"], 1)

    def test_its_streams_identify_it_when_the_id_does_not(self):
        """An id can be rebuilt between runs; the links that moved cannot."""
        kept, summary = self._retain(
            [dict(item) for item in self.others], moved_streams={URL})
        self.assertNotIn("reacher-2026-s04e06-dual", [item["id"] for item in kept])
        self.assertEqual(summary["moved_to_series"], 1)

    def test_a_migrated_card_does_not_make_the_scan_look_broken(self):
        """9 of 10 found, not 9 of 10 with one missing: the card that moved is
        not evidence that the scan failed to see the category."""
        _kept, summary = self._retain(
            [dict(item) for item in self.others],
            moved_cards={"reacher-2026-s04e06-dual"})
        self.assertTrue(summary["scan_complete"])

    def test_a_migrated_card_spends_no_grace_and_is_never_removed(self):
        for _ in range(mrt.GRACE_SCANS + 3):
            self._retain([dict(item) for item in self.others],
                         moved_cards={"reacher-2026-s04e06-dual"})
        store = json.loads(Path(self.path).read_text(encoding="utf-8"))
        self.assertNotIn("reacher-2026-s04e06-dual", store.get("absent") or {})
        self.assertNotIn("reacher-2026-s04e06-dual", store.get("removed") or {})

    def test_an_ordinary_missing_film_is_still_carried(self):
        """Only what moved is exempt. Everything else keeps its grace."""
        kept, summary = self._retain(
            [dict(item) for item in self.others[:-1]],
            moved_cards={"reacher-2026-s04e06-dual"})
        self.assertIn("film-8", [item["id"] for item in kept])
        self.assertEqual(summary["retained"], 1)

    def test_being_told_nothing_changes_nothing(self):
        kept, summary = self._retain([dict(item) for item in self.others],
                                     moved_cards=set(), moved_streams=set())
        self.assertIn("reacher-2026-s04e06-dual", [item["id"] for item in kept])
        self.assertEqual(summary.get("moved_to_series"), 0)


class TheMigrationRecordsWhatItTook(unittest.TestCase):
    def setUp(self) -> None:
        movies._MIGRATED_CARD_IDS.clear()
        movies._MIGRATED_STREAM_URLS.clear()
        self.addCleanup(movies._MIGRATED_CARD_IDS.clear)
        self.addCleanup(movies._MIGRATED_STREAM_URLS.clear)

    def _items(self):
        return [{
            "name": "Reacher",
            "seasons": [{
                "number": 4,
                "episodes": [{
                    "episode_key": "06",
                    "migrated_from_movie_id": "reacher-2026-s04e06-dual",
                    "merged_from_movie_ids": ["reacher-2026-s04e06-720p"],
                    "links": [{"url": URL}],
                }],
            }],
        }]

    def test_it_records_the_card_id_the_episode_carries(self):
        movies._remember_series_migration(self._items())
        self.assertIn("reacher-2026-s04e06-dual", movies._MIGRATED_CARD_IDS)

    def test_it_records_a_card_folded_into_another_episode(self):
        """Two cards for one numbered episode is one episode with two sources,
        and BOTH cards left the movie catalogue."""
        movies._remember_series_migration(self._items())
        self.assertIn("reacher-2026-s04e06-720p", movies._MIGRATED_CARD_IDS)

    def test_it_records_the_streams(self):
        movies._remember_series_migration(self._items())
        self.assertIn(URL, movies._MIGRATED_STREAM_URLS)

    def test_a_new_run_starts_from_nothing(self):
        movies._MIGRATED_CARD_IDS.add("left-over-from-last-time")
        movies._remember_series_migration(self._items())
        self.assertNotIn("left-over-from-last-time", movies._MIGRATED_CARD_IDS)

    def test_rubbish_is_survived_rather_than_raising(self):
        for payload in ([], [None], [{"seasons": None}],
                        [{"seasons": [{"episodes": [None]}]}]):
            with self.subTest(payload=payload):
                movies._remember_series_migration(payload)

    def test_the_migration_tells_retention_before_it_stages(self):
        """Order matters only in that both happen; this pins that the recording
        is not something a later edit can quietly drop."""
        import ast

        source = (ROOT / "scanner" / "movies.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        migrate = next(node for node in ast.walk(tree)
                       if isinstance(node, ast.FunctionDef)
                       and node.name == "_migrate_series_cards")
        called = {child.func.id for child in ast.walk(migrate)
                  if isinstance(child, ast.Call) and isinstance(child.func, ast.Name)}
        self.assertIn("_remember_series_migration", called)

    def test_retention_is_given_both_sets(self):
        import ast

        source = (ROOT / "scanner" / "movies.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        retain = next(node for node in ast.walk(tree)
                      if isinstance(node, ast.FunctionDef)
                      and node.name == "_retain_recent_dropouts")
        segment = ast.get_source_segment(source, retain) or ""
        self.assertIn("moved_cards=_MIGRATED_CARD_IDS", segment)
        self.assertIn("moved_streams=_MIGRATED_STREAM_URLS", segment)


if __name__ == "__main__":
    unittest.main()
