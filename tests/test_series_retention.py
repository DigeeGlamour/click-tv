"""An episode that was reaching viewers is not dropped in silence either.

`scanner/movies.py` has carried a published movie card through a scan that
could not find it since the grace rule was written. The series catalogue had
nothing of the kind: `publish_prepared_series` rebuilds `data/series` from
the payload it is handed, so a show that did not come back through staging
stopped existing.

This is how it was found. The no-loss gate blocked three movies publishes in
a row on 2026-10-17, and the third one's remaining loss was entirely this:

    run 06:12   unexplained live loss  393   every one a retired CDN host
    run 07:42   unexplained live loss  119   every one `dertg.b-cdn.net`,
                                             every one an `episode|...` line

    Series published   181 Series / 772 Episodes   (06:12)
    Series published   175 Series / 738 Episodes   (07:42)

Nothing was wrong with those episodes. What reaches the staging catalogue
depends on which movie cards retention happened to carry into the series
migration, and that moves between runs.

Measured against the published tree after the fix: an empty payload carries
all 772 episodes back across 174 shows, and a payload that already holds
them carries 0 and recognises all 772 as present.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import series_retention as sr  # noqa: E402

URL = "https://dertg.b-cdn.net/s3/upload/videos/2026/06/[Fibwatch.Com]Show.S01E01.1080p.mkv"
OTHER = "https://dertg.b-cdn.net/s3/upload/videos/2026/06/[Fibwatch.Com]Show.S01E02.1080p.mkv"


def _tree(tmp: Path, *, url=URL, episode_id="show-2026-s01-01"):
    """One published show on disk, in the shape the publisher writes."""
    season = tmp / "data" / "series" / "bangla" / "show-2026"
    season.mkdir(parents=True, exist_ok=True)
    (season / "season-01.json").write_text(json.dumps({
        "schema_version": 1,
        "series_id": "show-2026",
        "series_name": "Show",
        "category": "Bangla",
        "season_number": 1,
        "items": [{
            "id": episode_id,
            "name": "Show - Episode 01",
            "episode_key": "01",
            "episode_label": "Episode 01",
            "episode_number": 1,
            "season_number": 1,
            "series_id": "show-2026",
            "category": "Bangla",
            "url": url,
            "backups": [],
            "resolution_height": 1080,
        }],
    }), encoding="utf-8")
    return tmp


def _payload(*urls):
    return [{
        "name": "Show",
        "category": "Bangla",
        "seasons": [{"number": 1, "episodes": [
            {"episode_key": "01", "url": u, "resolution_height": 1080}
            for u in urls
        ]}],
    }]


class AMissingEpisodeComesBack(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        _tree(self.tmp)
        self.store = self.tmp / "state" / "series-retention.json"

    def _carry(self, items, **kw):
        return sr.carry(items, root=self.tmp, store_path=self.store, **kw)

    def test_an_empty_payload_gets_the_episode_back(self):
        items = []
        out = self._carry(items)
        self.assertEqual(out["carried_episodes"], 1)
        self.assertEqual(out["carried_shows"], 1)
        self.assertEqual(len(items), 1)

    def test_the_carried_show_keeps_its_name_and_category(self):
        items = []
        self._carry(items)
        self.assertEqual(items[0]["name"], "Show")
        self.assertEqual(items[0]["category"], "Bangla")

    def test_the_carried_episode_keeps_its_stream(self):
        items = []
        self._carry(items)
        episode = items[0]["seasons"][0]["episodes"][0]
        self.assertEqual(episode["url"], URL)

    def test_it_says_it_was_carried(self):
        items = []
        self._carry(items)
        episode = items[0]["seasons"][0]["episodes"][0]
        self.assertIs(episode[sr.CARRIED_FLAG], True)
        self.assertIn("retention_note", episode)

    def test_a_backup_becomes_a_link_the_builder_understands(self):
        """`_normalize_sources` prepends the episode itself when it has a
        `url`, and reads the rest out of `links`."""
        season = self.tmp / "data" / "series" / "bangla" / "show-2026" / "season-01.json"
        payload = json.loads(season.read_text(encoding="utf-8"))
        payload["items"][0]["backups"] = [{"url": OTHER, "resolution_height": 1080}]
        season.write_text(json.dumps(payload), encoding="utf-8")
        items = []
        self._carry(items)
        episode = items[0]["seasons"][0]["episodes"][0]
        self.assertEqual([link["url"] for link in episode["links"]], [OTHER])
        self.assertNotIn("backups", episode)


class AnEpisodeThisRunProducedIsLeftAlone(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        _tree(self.tmp)
        self.store = self.tmp / "state" / "series-retention.json"

    def test_nothing_is_carried_when_the_stream_is_already_there(self):
        items = _payload(URL)
        out = sr.carry(items, root=self.tmp, store_path=self.store)
        self.assertEqual(out["carried_episodes"], 0)
        self.assertEqual(out["still_present"], 1)
        self.assertEqual(len(items), 1)

    def test_a_different_stream_for_the_same_show_still_carries(self):
        items = _payload(OTHER)
        out = sr.carry(items, root=self.tmp, store_path=self.store)
        self.assertEqual(out["carried_episodes"], 1)

    def test_the_miss_counter_is_forgotten_once_it_returns(self):
        sr.carry([], root=self.tmp, store_path=self.store)
        self.assertTrue(json.loads(self.store.read_text(encoding="utf-8"))["absent"])
        sr.carry(_payload(URL), root=self.tmp, store_path=self.store)
        self.assertEqual(json.loads(self.store.read_text(encoding="utf-8"))["absent"], {})


class ThePatienceRunsOut(unittest.TestCase):
    """The same grace the movie catalogue gives a card. A show the source has
    genuinely finished with does not live for ever."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        _tree(self.tmp)
        self.store = self.tmp / "state" / "series-retention.json"

    def test_it_is_carried_for_exactly_the_grace(self):
        carried = []
        for _ in range(sr.GRACE_SCANS + 2):
            items = []
            out = sr.carry(items, root=self.tmp, store_path=self.store)
            carried.append(out["carried_episodes"])
        self.assertEqual(carried[:sr.GRACE_SCANS], [1] * sr.GRACE_SCANS)
        self.assertEqual(carried[sr.GRACE_SCANS:], [0, 0])

    def test_the_drop_is_counted_not_silent(self):
        for _ in range(sr.GRACE_SCANS):
            sr.carry([], root=self.tmp, store_path=self.store)
        out = sr.carry([], root=self.tmp, store_path=self.store)
        self.assertEqual(out["dropped_after_grace"], 1)

    def test_the_grace_is_the_movie_catalogues_own(self):
        from scanner.movie_retention import GRACE_SCANS as MOVIE_GRACE

        self.assertEqual(sr.GRACE_SCANS, MOVIE_GRACE)


class ItSurvivesAnEmptyOrBrokenTree(unittest.TestCase):
    def test_no_published_tree_carries_nothing(self):
        tmp = Path(tempfile.mkdtemp())
        items = []
        out = sr.carry(items, root=tmp, store_path=tmp / "s.json")
        self.assertEqual(out["carried_episodes"], 0)
        self.assertEqual(items, [])

    def test_a_season_file_that_is_not_json_is_skipped(self):
        tmp = Path(tempfile.mkdtemp())
        bad = tmp / "data" / "series" / "bangla" / "x"
        bad.mkdir(parents=True)
        (bad / "season-01.json").write_text("{not json", encoding="utf-8")
        self.assertEqual(sr.carry([], root=tmp, store_path=tmp / "s.json")["carried_episodes"], 0)

    def test_an_episode_with_no_stream_is_not_carried(self):
        tmp = Path(tempfile.mkdtemp())
        _tree(tmp, url="")
        self.assertEqual(sr.carry([], root=tmp, store_path=tmp / "s.json")["carried_episodes"], 0)


class TheSeriesPipelineAsksForIt(unittest.TestCase):
    SOURCE = (ROOT / "scanner" / "series.py").read_text(encoding="utf-8")

    def test_it_runs_before_the_duplicate_merge(self):
        """So a show that DID come back absorbs its missing episodes through
        the deduplication that already exists."""
        carry_at = self.SOURCE.index("series_retention.carry(staged, root=root)")
        merge_at = self.SOURCE.index("normalized = _merge_duplicate_series(staged)")
        self.assertLess(carry_at, merge_at)

    def test_a_failure_there_cannot_cost_a_scan(self):
        block = self.SOURCE[self.SOURCE.index("from scanner import series_retention"):]
        block = block[:block.index("normalized = _merge_duplicate_series")]
        self.assertIn("except Exception", block)
        self.assertIn("series retention skipped", block)

    def test_the_gate_sees_the_carried_episodes(self):
        """`episodes_from_prepared_series` reads the payload this returns, so
        carrying here answers INVARIANT 2 as well as the publish."""
        self.assertIn("def prepare_manual_series", self.SOURCE)
        self.assertIn("staged = [item for item in payload[\"items\"]", self.SOURCE)


if __name__ == "__main__":
    unittest.main()
