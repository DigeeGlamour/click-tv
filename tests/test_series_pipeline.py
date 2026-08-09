from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scanner.movies import _parse_manual_catalog_text
from scanner.series import prepare_manual_series, publish_prepared_series


SAMPLE_CATALOG = """
Movie-1
=========================
Show name: Chokro 2
Movie Category: Bangla
Movie Year: 2026
Poster URL: https://example.com/chokro.jpg

Season: 1
Episode: Episode 01
Resolution-1: 1080p
Link-1: https://example.com/chokro-s01e01.mkv

Episode: Episode 02-03
Resolution-1: 4K HEVC
Link-1: https://example.com/chokro-s01e02-e03-2160p-hevc.mkv

Season: 2
Episode: Episode 01
Resolution-1: 1080p
Link-1: https://example.com/chokro-s02e01.mkv

Movie-2
=========================
Show name: Hotstar Originals
Movie Category: Disney Hotstar
Movie Year: 2025
Poster URL:

Season: 1
Episode: Episode 01
Resolution-1: 1080p
Link-1: https://example.com/hotstar-s01e01.m3u8
""".strip()


class SeriesPipelineTests(unittest.TestCase):
    def test_mixed_txt_parser_keeps_series_and_combined_episode(self) -> None:
        movies, series = _parse_manual_catalog_text(SAMPLE_CATALOG)

        self.assertEqual(movies, [])
        self.assertEqual(len(series), 2)
        chokro = series[0]
        self.assertEqual(chokro["name"], "Chokro 2")
        self.assertEqual(chokro["year"], 2026)
        self.assertEqual(len(chokro["seasons"]), 2)
        self.assertEqual(chokro["seasons"][0]["episodes"][1]["episode_label"], "Episode 02-03")
        self.assertEqual(
            chokro["seasons"][0]["episodes"][1]["links"][0]["resolution_height"],
            2160,
        )

        premium = series[1]
        self.assertEqual(premium["category"], "Disney Hotstar")
        self.assertEqual(premium["seasons"][0]["episodes"][0]["links"][0]["url"], "https://example.com/hotstar-s01e01.m3u8")

    def test_prepare_and_publish_current_series_schema(self) -> None:
        _, series = _parse_manual_catalog_text(SAMPLE_CATALOG)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            staging = root / "working/manual-series-catalog.json"
            staging.parent.mkdir(parents=True)
            staging.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "repository_snapshots": [{"revision": "abcdef0"}],
                        "items": series,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            prepared = prepare_manual_series(project_root=root)
            self.assertEqual(prepared["series"], 2)
            self.assertEqual(prepared["episodes"], 4)

            result = publish_prepared_series(prepared, project_root=root)
            self.assertEqual(result["series"], 2)
            self.assertEqual(result["episodes"], 4)

            manifest_path = root / "data/series/manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["total_series"], 2)
            self.assertEqual(manifest["total_episodes"], 4)
            self.assertEqual(manifest["categories"]["Bangla"]["count"], 1)
            self.assertEqual(manifest["categories"]["Premium"]["count"], 1)

            bangla_index = json.loads((root / "data/series/bangla/index.json").read_text(encoding="utf-8"))
            series_id = bangla_index["items"][0]["id"]
            detail_path = root / f"data/series/bangla/{series_id}/index.json"
            detail = json.loads(detail_path.read_text(encoding="utf-8"))
            self.assertEqual(len(detail["seasons"]), 2)

            season_path = root / f"data/series/bangla/{series_id}/season-01.json"
            season = json.loads(season_path.read_text(encoding="utf-8"))
            self.assertEqual(season["count"], 2)
            combined = season["items"][1]
            self.assertEqual(combined["episode_label"], "Episode 02-03")
            self.assertEqual(combined["proxy_mode"], "direct_first")
            self.assertTrue(combined["skip_verification"])


if __name__ == "__main__":
    unittest.main()
