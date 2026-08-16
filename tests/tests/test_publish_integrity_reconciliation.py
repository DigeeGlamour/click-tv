"""Cross-file integrity must survive a rebase between two scan machines.

Three scanners push to the same branch. `git rebase -X theirs` resolves each
conflicting file on its own, with no idea that data/channels/*.json and
data/playback-sources.json describe each other. Two failures follow, and both
were real Cloudflare Pages build errors:

  * A channel keeps a `playback_id` whose catalogue record came from the other
    run and was dropped. The card has no url and no record - nothing to play -
    yet it shipped. ("Sports channel #45 playback_id catalogue-এ নেই: Willow")

  * A movie manifest keeps one run's `total_pages` while the index and its page
    files came from the other. ("Bangla manifest total_pages mismatch")

scripts/reconcile-generated-counts.py runs after the rebase and before the
push, and has to repair both.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SCRIPT = ROOT / "scripts" / "reconcile-generated-counts.py"


def _write(path: Path, payload) -> None:
    # Match the on-disk formatting the scanner writes, so a no-op run really is
    # byte-for-byte unchanged rather than merely re-indented.
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _run(root: Path):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(root)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


class OrphanedPlaybackIdTests(unittest.TestCase):
    def test_a_channel_with_no_url_and_no_catalogue_record_is_dropped(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data = root / "data"
            _write(data / "playback-sources.json", {
                "schema_version": 1,
                "count": 1,
                "records": {"ctv_" + "a" * 32: {"url": "https://ok.test/live.m3u8"}},
            })
            _write(data / "channels" / "sports.json", {
                "count": 3,
                "channels": [
                    {"name": "Good", "playback_id": "ctv_" + "a" * 32},
                    {"name": "Willow", "playback_id": "ctv_" + "b" * 32},
                    {"name": "Direct", "url": "https://ok.test/direct.m3u8"},
                ],
            })
            _write(data / "manifest.json", {"channels": {
                "Sports": {"count": 3, "visible": True, "url": "data/channels/sports.json"}
            }})

            _run(root)

            sports = json.loads((data / "channels" / "sports.json").read_text(encoding="utf-8"))
            names = [c["name"] for c in sports["channels"]]

        self.assertEqual(names, ["Good", "Direct"])
        self.assertEqual(sports["count"], 2)

    def test_an_orphan_that_still_has_its_own_url_is_kept(self):
        """The url is the playable route; a stale playback_id beside it is
        harmless and must not cost the viewer a working channel."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data = root / "data"
            _write(data / "playback-sources.json", {"count": 0, "records": {}})
            _write(data / "channels" / "bangla.json", {
                "count": 1,
                "channels": [{
                    "name": "Has URL",
                    "playback_id": "ctv_" + "c" * 32,
                    "url": "https://ok.test/live.m3u8",
                }],
            })
            _write(data / "manifest.json", {})

            _run(root)

            payload = json.loads((data / "channels" / "bangla.json").read_text(encoding="utf-8"))

        self.assertEqual(len(payload["channels"]), 1)

    def test_a_metadata_only_fixture_card_is_kept(self):
        """An announced match publishes deliberately without a link; its stream
        is added at kickoff. Dropping it would empty the Upcoming tab."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data = root / "data"
            _write(data / "playback-sources.json", {"count": 0, "records": {}})
            _write(data / "upcoming.json", {
                "count": 1,
                "items": [{"name": "Final", "metadata_only": True,
                           "playback_id": "ctv_" + "d" * 32}],
            })
            _write(data / "manifest.json", {})

            _run(root)

            payload = json.loads((data / "upcoming.json").read_text(encoding="utf-8"))

        self.assertEqual(len(payload["items"]), 1)


class MovieTotalPagesTests(unittest.TestCase):
    def _catalogue(self, data: Path):
        _write(data / "playback-sources.json", {"count": 0, "records": {}})

    def test_stale_manifest_total_pages_is_corrected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data = root / "data"
            self._catalogue(data)
            _write(data / "movies" / "hindi" / "index.json", {
                "slug": "hindi",
                "count": 40,
                "total_pages": 3,
                "pages": [
                    {"page": 1, "file": "page-001.json", "count": 20},
                    {"page": 2, "file": "page-002.json", "count": 20},
                    {"page": 3, "file": "page-003.json", "count": 0},
                ],
            })
            # The manifest kept the other run's smaller figure.
            _write(data / "manifest.json", {"movies": {
                "Hindi": {"count": 40, "total_pages": 2, "visible": True,
                          "index": "data/movies/hindi/index.json"},
            }})

            _run(root)

            manifest = json.loads((data / "manifest.json").read_text(encoding="utf-8"))

        self.assertEqual(manifest["movies"]["Hindi"]["total_pages"], 3)
        self.assertEqual(manifest["movies"]["Hindi"]["count"], 40)

    def test_an_index_is_left_alone_when_nothing_was_dropped(self):
        """Rebuilding an index nothing touched risks corrupting counts the
        scanner computed correctly - the earlier bug that turned every movie
        category's count into 0."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data = root / "data"
            self._catalogue(data)
            index = {
                "slug": "mix", "count": 1084, "total_pages": 11,
                "pages": [{"page": n, "file": f"page-{n:03d}.json", "count": 98}
                          for n in range(1, 12)],
            }
            _write(data / "movies" / "mix" / "index.json", index)
            _write(data / "manifest.json", {"movies": {
                "Mix": {"count": 1084, "total_pages": 11, "visible": True,
                        "index": "data/movies/mix/index.json"},
            }})

            _run(root)

            after = json.loads((data / "movies" / "mix" / "index.json").read_text(encoding="utf-8"))

        self.assertEqual(after["count"], 1084)
        self.assertEqual(after["total_pages"], 11)

    def test_a_movie_page_losing_an_item_rebuilds_its_index(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data = root / "data"
            self._catalogue(data)
            _write(data / "movies" / "bangla" / "page-001.json", {
                "page": 1,
                "count": 2,
                "movies": [
                    {"name": "Keep", "url": "https://ok.test/a.mp4"},
                    {"name": "Orphan", "playback_id": "ctv_" + "e" * 32},
                ],
            })
            _write(data / "movies" / "bangla" / "index.json", {
                "slug": "bangla", "count": 2, "total_pages": 1,
                "pages": [{"page": 1, "file": "page-001.json", "count": 2}],
            })
            _write(data / "manifest.json", {"movies": {
                "Bangla": {"count": 2, "total_pages": 1, "visible": True,
                           "index": "data/movies/bangla/index.json"},
            }})

            _run(root)

            page = json.loads((data / "movies" / "bangla" / "page-001.json").read_text(encoding="utf-8"))
            index = json.loads((data / "movies" / "bangla" / "index.json").read_text(encoding="utf-8"))
            manifest = json.loads((data / "manifest.json").read_text(encoding="utf-8"))

        self.assertEqual([m["name"] for m in page["movies"]], ["Keep"])
        self.assertEqual(index["count"], 1)
        self.assertEqual(index["pages"][0]["count"], 1)
        self.assertEqual(manifest["movies"]["Bangla"]["count"], 1)


class CleanRepositoryIsUntouchedTests(unittest.TestCase):
    def test_running_against_consistent_data_changes_nothing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data = root / "data"
            _write(data / "playback-sources.json", {
                "count": 1, "records": {"ctv_" + "f" * 32: {"url": "https://ok.test/x.m3u8"}},
            })
            _write(data / "channels" / "news.json", {
                "count": 1, "channels": [{"name": "N", "playback_id": "ctv_" + "f" * 32}],
            })
            _write(data / "manifest.json", {"channels": {
                "News": {"count": 1, "visible": True, "url": "data/channels/news.json"},
            }})
            snapshot = {
                p: p.read_bytes() for p in sorted(data.rglob("*.json"))
            }

            output = _run(root)

            unchanged = all(p.read_bytes() == b for p, b in snapshot.items())

        self.assertTrue(unchanged)
        self.assertIn("nothing to reconcile", output)


if __name__ == "__main__":
    unittest.main()
