from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scanner.series import prepare_manual_series, publish_prepared_series


class SeriesPublisherTests(unittest.TestCase):
    def _catalog(self):
        return {
            "schema_version": 1,
            "repository_snapshots": [{"repository": "0matbank/hopeful-research", "revision": "abc123"}],
            "items": [
                {
                    "name": "Range Show",
                    "category": "Disney Hotstar",
                    "year": 2026,
                    "poster": "https://image.example/show.jpg",
                    "manual_source": True,
                    "seasons": [
                        {
                            "number": 1,
                            "episodes": [
                                {
                                    "episode_label": "Episode 01-07",
                                    "episode_key": "01-07",
                                    "links": [
                                        {"resolution": "4K 2160P HEVC", "url": "https://example.com/show-4k.mkv"},
                                        {"resolution": "HD 1080P", "url": "https://example.com/show-1080p.mkv"},
                                        {"resolution": "HD 720P", "url": "https://example.com/show-720p.mkv"},
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ],
        }

    def test_publish_lazy_premium_tree_and_direct_first_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            catalog = root / "working/manual-series-catalog.json"
            catalog.parent.mkdir(parents=True)
            catalog.write_text(json.dumps(self._catalog()), encoding="utf-8")

            prepared = prepare_manual_series(root)
            self.assertEqual(prepared["series"], 1)
            self.assertEqual(prepared["episodes"], 1)
            summary = publish_prepared_series(prepared, root)
            self.assertEqual(summary["categories"]["Premium"], 1)

            manifest = json.loads((root / "data/series/manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["total_series"], 1)
            self.assertEqual(manifest["categories"]["Premium"]["index"], "data/series/premium/index.json")

            category = json.loads((root / "data/series/premium/index.json").read_text(encoding="utf-8"))
            series_path = root / category["items"][0]["series_manifest"]
            series_index = json.loads(series_path.read_text(encoding="utf-8"))
            season_path = root / series_index["seasons"][0]["path"]
            season = json.loads(season_path.read_text(encoding="utf-8"))
            episode = season["items"][0]
            self.assertEqual(episode["episode_label"], "Episode 01-07")
            self.assertEqual(episode["proxy_mode"], "direct_first")
            self.assertEqual(episode["verification_status"], "manual_trusted")
            self.assertEqual(episode["resolution_height"], 1080)
            self.assertTrue(any(item["resolution_height"] == 2160 for item in episode["backups"]))

    def test_invalid_catalog_does_not_replace_previous_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            catalog = root / "working/manual-series-catalog.json"
            catalog.parent.mkdir(parents=True)
            catalog.write_text(json.dumps(self._catalog()), encoding="utf-8")
            publish_prepared_series(prepare_manual_series(root), root)
            original = (root / "data/series/manifest.json").read_text(encoding="utf-8")

            broken = self._catalog()
            broken["items"][0]["seasons"][0]["episodes"][0]["links"] = [{"url": "not-a-url"}]
            catalog.write_text(json.dumps(broken), encoding="utf-8")
            with self.assertRaises(ValueError):
                prepare_manual_series(root)
            self.assertEqual((root / "data/series/manifest.json").read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()
