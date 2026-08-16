"""Manifest/catalogue counts must always match the file they describe.

GitHub Actions, a local PC clone, and Google Colab can each scan and push
around the same time. `git rebase -X theirs` resolves a real file conflict
(two runs both rewrote state/stream-history.json) correctly, but a scalar
count field sharing a JSON file with a large collection is a different shape
of problem: the collection often merges cleanly line-by-line while the single
count line genuinely conflicts and gets resolved to only one side. The
collection and its declared count then disagree, and
scripts/validate-pages.py's "count mismatch" checks are exactly what catches
that in production.
"""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner.output import _reconcile_manifest_counts  # noqa: E402


class ManifestReconciliationTests(unittest.TestCase):
    def _make_tree(self, root: Path) -> None:
        (root / "channels").mkdir(parents=True)
        (root / "movies" / "hindi").mkdir(parents=True)

    def test_channel_count_is_recomputed_from_the_actual_file(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temp_dir:
            data_root = Path(temp_dir) / "data"
            self._make_tree(data_root)
            (data_root / "channels" / "bangla.json").write_text(
                json.dumps({"count": 7, "channels": [{"n": i} for i in range(7)]})
            )
            manifest = {
                "channels": {
                    "Bangla": {"count": 26, "visible": True, "url": "data/channels/bangla.json"}
                }
            }
            _reconcile_manifest_counts(manifest, data_root)
            self.assertEqual(manifest["channels"]["Bangla"]["count"], 7)
            self.assertTrue(manifest["channels"]["Bangla"]["visible"])

    def test_movie_count_is_recomputed_from_the_index_file(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temp_dir:
            data_root = Path(temp_dir) / "data"
            self._make_tree(data_root)
            (data_root / "movies" / "hindi" / "index.json").write_text(
                json.dumps({"count": 55})
            )
            manifest = {
                "movies": {
                    "Hindi": {"count": 999, "visible": True, "index": "data/movies/hindi/index.json"}
                }
            }
            _reconcile_manifest_counts(manifest, data_root)
            self.assertEqual(manifest["movies"]["Hindi"]["count"], 55)

    def test_event_counts_recompute_and_visibility_follows(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temp_dir:
            data_root = Path(temp_dir) / "data"
            self._make_tree(data_root)
            (data_root / "today-match.json").write_text(json.dumps({"count": 5, "items": []}))
            (data_root / "upcoming.json").write_text(
                json.dumps({"count": 0, "items": [{"n": 1}, {"n": 2}, {"n": 3}]})
            )
            manifest = {
                "today_match": {"count": 5, "visible": True, "url": "data/today-match.json"},
                "upcoming": {"count": 0, "visible": False, "url": "data/upcoming.json"},
            }
            _reconcile_manifest_counts(manifest, data_root)
            self.assertEqual(manifest["today_match"]["count"], 0)
            self.assertFalse(manifest["today_match"]["visible"])
            self.assertEqual(manifest["upcoming"]["count"], 3)
            self.assertTrue(manifest["upcoming"]["visible"])

    def test_reconciliation_is_a_no_op_when_already_consistent(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temp_dir:
            data_root = Path(temp_dir) / "data"
            self._make_tree(data_root)
            (data_root / "channels" / "sports.json").write_text(
                json.dumps({"count": 3, "channels": [{"n": 1}, {"n": 2}, {"n": 3}]})
            )
            manifest = {
                "channels": {
                    "Sports": {"count": 3, "visible": True, "url": "data/channels/sports.json"}
                }
            }
            _reconcile_manifest_counts(manifest, data_root)
            self.assertEqual(manifest["channels"]["Sports"]["count"], 3)


class PlaybackCatalogReconciliationScriptTests(unittest.TestCase):
    """scripts/reconcile-generated-counts.py is what the workflows call
    after a rebase. Exercise it exactly the way they do: as a subprocess
    against real files on disk, not as an imported function."""

    def test_script_fixes_a_stale_playback_sources_count(self):
        import subprocess
        import tempfile

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "data").mkdir()

            manifest = {"channels": {}, "movies": {}}
            (root / "data" / "manifest.json").write_text(json.dumps(manifest))

            # The exact shape of drift a git merge leaves: records merged in,
            # the scalar count line resolved to the stale, smaller value.
            (root / "data" / "playback-sources.json").write_text(json.dumps({
                "schema_version": 1,
                "count": 2,
                "records": {"ctv_a": {"url": "x"}, "ctv_b": {"url": "y"}, "ctv_c": {"url": "z"}},
            }))

            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "reconcile-generated-counts.py"), str(root)],
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            fixed = json.loads((root / "data" / "playback-sources.json").read_text())
            self.assertEqual(fixed["count"], 3)


if __name__ == "__main__":
    unittest.main()
