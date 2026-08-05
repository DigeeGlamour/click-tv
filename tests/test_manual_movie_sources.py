from __future__ import annotations

import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from scanner.movies import (
    _parse_manual_catalog_text,
    load_manual_movies,
)


MOVIE_AND_SERIES_TEXT = """Movie-1
Movie name: Manual Movie
Category: Bangla Movies
Year: 2026
Poster: https://image.example/movie.jpg

RESOLUTION 1: HD 1080P
STREAM Link 1: https://example.com/manual-movie-1080p.mkv

================================================================================
Movie-2
Show name: Manual Show
Category: Bangla Movies
Year: 2026
Poster: https://image.example/show.jpg

Season: S02

Episode: Episode 01-07
  Resolution-1: HD 1080P
  Link-1: https://example.com/manual-show-s02e01-07-1080p.mkv

Episode: Episode 08
  Resolution-1: 4K 2160P HEVC
  Link-1: https://example.com/manual-show-s02e08-2160p-hevc.mkv
  Resolution-2: HD 1080P
  Link-2: https://example.com/manual-show-s02e08-1080p.mkv
"""


class _FakeResponse:
    def __init__(self, content: bytes, resolved_url: str):
        self._io = io.BytesIO(content)
        self._resolved_url = resolved_url

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, size: int = -1):
        return self._io.read(size)

    def geturl(self):
        return self._resolved_url


class ManualMovieSourceTests(unittest.TestCase):
    def _paths(self, root: Path):
        return {
            "manual_movies_path": root / "manual/movies.json",
            "manual_movies_text_path": root / "manual/movies.txt",
            "poster_cache_path": root / "state/manual-movie-posters.json",
            "generated_movies_root": root / "data/movies",
            "remote_sources_path": root / "manual/movie-sources.json",
            "remote_cache_path": root / "state/manual-movie-remote-cache.json",
            "conflict_report_path": root / "reports/manual-movie-conflicts.json",
            "missing_poster_report_path": root / "reports/manual-movie-poster-missing.json",
            "source_report_path": root / "reports/manual-movie-sources.json",
            "series_catalog_path": root / "working/manual-series-catalog.json",
        }

    def test_mixed_txt_separates_movie_and_series_and_preserves_range(self) -> None:
        movies, series = _parse_manual_catalog_text(MOVIE_AND_SERIES_TEXT)
        self.assertEqual([item["name"] for item in movies], ["Manual Movie"])
        self.assertEqual([item["name"] for item in series], ["Manual Show"])
        season = series[0]["seasons"][0]
        self.assertEqual(season["number"], 2)
        self.assertEqual(
            [episode["episode_label"] for episode in season["episodes"]],
            ["Episode 01-07", "Episode 08"],
        )
        self.assertEqual(len(season["episodes"][1]["links"]), 2)

    def test_repository_scans_txt_only_and_stages_series(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "manual").mkdir(parents=True)
            config = {
                "version": 4,
                "enabled": True,
                "use_last_valid_cache": False,
                "repository_sources": [
                    {
                        "id": "repo",
                        "name": "Repo",
                        "repository": "0matbank/hopeful-research",
                        "ref": "main",
                        "root": "categories",
                        "extensions": [".txt"],
                        "ignore_filenames": ["history.txt", "readme.txt"],
                        "require_fresh": True,
                        "require_all_files": False,
                        "require_any_valid_file": True,
                    }
                ],
            }
            (root / "manual/movie-sources.json").write_text(json.dumps(config), encoding="utf-8")

            archive_io = io.BytesIO()
            with zipfile.ZipFile(archive_io, "w", zipfile.ZIP_DEFLATED) as archive:
                prefix = "repo-abcdef0/categories/Bangla_Movies"
                archive.writestr(f"{prefix}/bangla_movies.txt", MOVIE_AND_SERIES_TEXT)
                archive.writestr(f"{prefix}/bangla_movies.json", json.dumps({"items": [{"name": "Duplicate JSON"}]}))
                archive.writestr(f"{prefix}/bangla_movies.m3u", "#EXTM3U")
                archive.writestr(f"{prefix}/history.txt", MOVIE_AND_SERIES_TEXT)

            with patch(
                "urllib.request.urlopen",
                return_value=_FakeResponse(
                    archive_io.getvalue(),
                    "https://codeload.github.com/0matbank/hopeful-research/legacy.zip/abcdef0",
                ),
            ):
                cards = load_manual_movies(**self._paths(root))

            self.assertEqual(len(cards), 1)
            self.assertEqual(cards[0]["name"], "Manual Movie")
            self.assertEqual(cards[0]["category"], "Bangla")
            self.assertEqual(cards[0]["verification_status"], "manual_trusted")
            self.assertEqual(cards[0]["proxy_mode"], "direct_first")

            staged = json.loads((root / "working/manual-series-catalog.json").read_text(encoding="utf-8"))
            self.assertEqual(staged["count"], 1)
            self.assertEqual(staged["items"][0]["name"], "Manual Show")
            self.assertEqual(staged["repository_snapshots"][0]["revision"], "abcdef0")

            report = json.loads((root / "reports/manual-movie-sources.json").read_text(encoding="utf-8"))
            self.assertEqual(report["version"], 4)
            self.assertEqual(report["discovered_file_count"], 1)
            self.assertEqual(report["total_movie_items"], 1)
            self.assertEqual(report["total_series_items"], 1)
            self.assertEqual(report["sources"][0]["format"], "txt")

    def test_path_category_mapping_including_premium_and_unknown_mix(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_root = root / "categories"
            samples = {
                "Dubbed/Hindi_Dubbed/dubbed.txt": "Dubbed Movie",
                "South_Indian/Tamil/tamil.txt": "Tamil Movie",
                "OTT/Disney_Hotstar/premium.txt": "Premium Movie",
                "Future_Category/future.txt": "Future Movie",
            }
            for relative, title in samples.items():
                path = source_root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    MOVIE_AND_SERIES_TEXT.split("================================================================================", 1)[0]
                    .replace("Manual Movie", title),
                    encoding="utf-8",
                )
            (root / "manual").mkdir(parents=True)
            (root / "manual/movie-sources.json").write_text(
                json.dumps(
                    {
                        "enabled": True,
                        "directory_sources": [
                            {
                                "id": "local",
                                "path": str(source_root),
                                "recursive": True,
                                "extensions": [".txt"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            cards = load_manual_movies(**self._paths(root))
            by_name = {card["name"]: card for card in cards}
            self.assertEqual(by_name["Dubbed Movie"]["category"], "Dubbed")
            self.assertEqual(by_name["Tamil Movie"]["category"], "South Indian")
            self.assertEqual(by_name["Premium Movie"]["category"], "Premium")
            self.assertEqual(by_name["Future Movie"]["category"], "Mix")

    def test_required_repository_failure_stops_publish(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "manual").mkdir(parents=True)
            (root / "manual/movie-sources.json").write_text(
                json.dumps(
                    {
                        "enabled": True,
                        "repository_sources": [
                            {
                                "id": "required",
                                "repository": "0matbank/hopeful-research",
                                "ref": "main",
                                "root": "categories",
                                "extensions": [".txt"],
                                "require_fresh": True,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with patch("urllib.request.urlopen", side_effect=OSError("offline")):
                with self.assertRaisesRegex(RuntimeError, "Required latest movie repository"):
                    load_manual_movies(**self._paths(root))


if __name__ == "__main__":
    unittest.main()
