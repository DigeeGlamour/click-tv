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
        harmless to the viewer, but not to scripts/validate-pages.py - it
        rejects any playback_id absent from the catalogue outright, url or
        no url ("Mix movie page 2 item #54 playback_id catalogue-এ নেই:
        Kannur Squad (2023) Dual ORG" was exactly this, a real url present
        the whole time). The stale field must be cleared, not merely
        tolerated, or the item survives here and still fails there."""
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
        self.assertFalse(payload["channels"][0].get("playback_id"))
        self.assertEqual(payload["channels"][0]["url"], "https://ok.test/live.m3u8")

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


class RebaseDuplicatedOrMiscountedCollectionTests(unittest.TestCase):
    """2026-08-19 production incident: `git rebase -X theirs origin/main`
    left data/channels/sports.json with Willow, Willow 2, Star Sports 1
    Hindi and Star Sports 2 each appearing twice - a plain line-based merge
    of a big reordered JSON array keeping both sides' copy of an entry that
    only moved - and left Indian/Cartoon's declared "count" one off from
    their real (undisturbed, non-duplicated) array length, exactly per this
    script's own docstring about the scalar count field being a real merge
    conflict independent of the collection it describes. Every scheduled
    scan failed the Pages validator on this for hours because nothing ever
    deduplicated the collection or unconditionally re-synced its count."""

    def test_a_duplicated_channel_id_from_a_rebase_merge_is_collapsed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data = root / "data"
            _write(data / "channels" / "sports.json", {
                "count": 2,
                "channels": [
                    {"id": "t-sports", "name": "T Sports", "url": "https://ok.test/t.m3u8"},
                    {"id": "willow", "name": "Willow", "url": "https://ok.test/willow.m3u8"},
                    {"id": "willow", "name": "Willow", "url": "https://ok.test/willow.m3u8"},
                ],
            })

            _run(root)

            payload = json.loads((data / "channels" / "sports.json").read_text(encoding="utf-8"))
            names = [c["name"] for c in payload["channels"]]
            self.assertEqual(names.count("Willow"), 1)
            self.assertEqual(payload["count"], len(payload["channels"]))

    def test_a_stale_count_with_no_duplicates_still_gets_resynced(self):
        """Indian/Cartoon's own failure mode: nothing to drop or dedupe, the
        collection itself is exactly right - only the scalar count lags."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data = root / "data"
            _write(data / "channels" / "indian.json", {
                "count": 195,
                "channels": [
                    {"id": f"ch-{i}", "name": f"Channel {i}", "url": f"https://ok.test/{i}.m3u8"}
                    for i in range(194)
                ],
            })

            _run(root)

            payload = json.loads((data / "channels" / "indian.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["count"], 194)
            self.assertEqual(len(payload["channels"]), 194)

    def test_two_different_channels_sharing_one_slugified_id_are_both_kept(self):
        """A live, real-data near-miss: running an earlier id-only version of
        this dedupe against production data deleted "Aaj Tak Bangla" because
        it shares a slugified id ("aaj-tak") with the unrelated "Aaj Tak" -
        a pre-existing id collision with nothing to do with any rebase."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data = root / "data"
            _write(data / "channels" / "other.json", {
                "count": 2,
                "channels": [
                    {"id": "aaj-tak", "name": "Aaj Tak", "url": "https://ok.test/aajtak.m3u8"},
                    {"id": "aaj-tak", "name": "Aaj Tak Bangla", "url": "https://ok.test/bangla.m3u8"},
                ],
            })

            _run(root)

            payload = json.loads((data / "channels" / "other.json").read_text(encoding="utf-8"))
            names = sorted(c["name"] for c in payload["channels"])
            self.assertEqual(names, ["Aaj Tak", "Aaj Tak Bangla"])
            self.assertEqual(payload["count"], 2)

    def test_same_id_and_name_with_a_different_url_is_still_collapsed(self):
        """The actual production incident's own shape: two "Star Sports 2"
        cards from two different backup-worthy sources, same id and name,
        different url. Unlike Aaj Tak above (different name), this must
        collapse to one - the validator rejects two cards for one channel
        name regardless of whether their url happens to differ."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data = root / "data"
            _write(data / "channels" / "sports.json", {
                "count": 2,
                "channels": [
                    {"id": "star-sports-2", "name": "Star Sports 2", "url": "https://a.test/s2.m3u8"},
                    {"id": "star-sports-2", "name": "Star Sports 2", "url": "https://b.test/s2-mirror.m3u8"},
                ],
            })

            _run(root)

            payload = json.loads((data / "channels" / "sports.json").read_text(encoding="utf-8"))
            self.assertEqual([c["name"] for c in payload["channels"]], ["Star Sports 2"])
            self.assertEqual(payload["count"], 1)


class AllowedHostsStaleCountTests(unittest.TestCase):
    """data/allowed-hosts.json is derived from the same channel/movie files
    the tests above correct, and its own declared count went stale by the
    exact same rebase mechanism - one file later in the same incident
    ("data/allowed-hosts.json count mismatch" alongside the channel
    failures). Re-deriving it fresh, not patching the scalar in isolation,
    is what actually keeps it honest about the corrected data."""

    def test_a_stale_allowed_hosts_count_is_rederived_from_current_data(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data = root / "data"
            _write(data / "channels" / "sports.json", {
                "count": 1,
                "channels": [
                    {"id": "a", "name": "A", "url": "https://host-a.test/live.m3u8"},
                ],
            })
            _write(data / "allowed-hosts.json", {
                "updated_at": "2020-01-01T00:00:00+00:00",
                "count": 5,
                "hosts": ["host-a.test", "stale-b.test", "stale-c.test", "stale-d.test", "stale-e.test"],
            })

            _run(root)

            payload = json.loads((data / "allowed-hosts.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["hosts"], ["host-a.test"])
            self.assertEqual(payload["count"], 1)


if __name__ == "__main__":
    unittest.main()
