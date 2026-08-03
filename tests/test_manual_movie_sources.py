from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scanner.movies import load_manual_movies


REMOTE_TEXT = """Movie-1
Movie name: Remote Exact Movie
Movie Category: Wrong Source Label
Movie year: 2026

RESOLUTION 1: HD 1080P
STREAM Link 1: https://example.com/remote-exact-movie-2026-1080p.mkv
"""


class _FakeResponse:
    def __init__(self, content: str):
        self._io = io.BytesIO(content.encode("utf-8"))

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, size: int = -1):
        return self._io.read(size)


class RemoteManualMovieSourceTests(unittest.TestCase):
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
        }

    def test_remote_source_category_mapping_and_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "manual/movie-sources.json"
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(
                json.dumps(
                    {
                        "enabled": True,
                        "use_last_valid_cache": True,
                        "sources": [
                            {
                                "id": "remote-bangla",
                                "name": "Remote Bangla",
                                "category": "Bangla",
                                "url": "https://example.com/movies.txt",
                                "enabled": True,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with patch("urllib.request.urlopen", return_value=_FakeResponse(REMOTE_TEXT)):
                cards = load_manual_movies(**self._paths(root))

            self.assertEqual(len(cards), 1)
            self.assertEqual(cards[0]["name"], "Remote Exact Movie")
            self.assertEqual(cards[0]["year"], 2026)
            self.assertEqual(cards[0]["category"], "Bangla")
            self.assertEqual(cards[0]["manual_source_tier"], 2)
            self.assertTrue((root / "state/manual-movie-remote-cache.json").exists())

            with patch("urllib.request.urlopen", side_effect=OSError("offline")):
                cached_cards = load_manual_movies(**self._paths(root))
            self.assertEqual(len(cached_cards), 1)
            self.assertEqual(cached_cards[0]["name"], "Remote Exact Movie")

    def test_local_text_wins_duplicate_remote_movie(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "manual").mkdir(parents=True, exist_ok=True)
            (root / "manual/movies.txt").write_text(
                REMOTE_TEXT.replace("https://example.com/remote-exact-movie-2026-1080p.mkv", "https://local.example/movie.mkv"),
                encoding="utf-8",
            )
            (root / "manual/movie-sources.json").write_text(
                json.dumps({"enabled": True, "sources": [{"id": "r", "category": "Bangla", "url": "https://example.com/r.txt"}]}),
                encoding="utf-8",
            )
            with patch("urllib.request.urlopen", return_value=_FakeResponse(REMOTE_TEXT)):
                cards = load_manual_movies(**self._paths(root))
            self.assertEqual(len(cards), 1)
            self.assertEqual(cards[0]["url"], "https://local.example/movie.mkv")
            self.assertEqual(cards[0]["manual_source_tier"], 0)
            backup_urls = [item["url"] for item in cards[0]["backups"]]
            self.assertIn("https://example.com/remote-exact-movie-2026-1080p.mkv", backup_urls)


    def test_dynamic_directory_discovers_json_poster_and_new_categories(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_root = root / "private/categories"
            bangla_dir = source_root / "Bangla_Movies"
            tamil_dir = source_root / "South_Indian/Tamil_Movies"
            dubbed_dir = source_root / "Dubbed/Tamil_Dubbed"
            future_dir = source_root / "Future_Category"
            for directory in (bangla_dir, tamil_dir, dubbed_dir, future_dir):
                directory.mkdir(parents=True, exist_ok=True)

            poster = "https://image.tmdb.org/t/p/w500/example-poster.jpg"
            (bangla_dir / "bangla_movies.json").write_text(
                json.dumps(
                    {
                        "movies": [
                            {
                                "title": "Poster Movie",
                                "release_year": 2026,
                                "poster_url": poster,
                                "qualities": {
                                    "HD 1080P": "https://example.com/poster-movie-1080p.mkv"
                                },
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (tamil_dir / "tamil_movies.json").write_text(
                json.dumps(
                    [
                        {
                            "name": "Tamil Dynamic Movie",
                            "year": 2025,
                            "url": "https://example.com/tamil-dynamic-2025.mkv",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            (dubbed_dir / "dubbed_movies.txt").write_text(
                REMOTE_TEXT.replace("Remote Exact Movie", "Dynamic Dubbed Movie"),
                encoding="utf-8",
            )
            (future_dir / "future_movies.json").write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "name": "Unknown Future Category Movie",
                                "year": 2024,
                                "stream_url": "https://example.com/future-category-2024.mkv",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (bangla_dir / "history.txt").write_text(REMOTE_TEXT, encoding="utf-8")

            config_path = root / "manual/movie-sources.json"
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(
                json.dumps(
                    {
                        "version": 2,
                        "enabled": True,
                        "directory_sources": [
                            {
                                "id": "dynamic-private-repo",
                                "name": "Dynamic Private Repo",
                                "path": str(source_root),
                                "recursive": True,
                                "extensions": [".txt", ".json"],
                                "ignore_filenames": ["history.txt"],
                                "enabled": True,
                            }
                        ],
                        "sources": [],
                    }
                ),
                encoding="utf-8",
            )

            cards = load_manual_movies(**self._paths(root))
            by_name = {card["name"]: card for card in cards}

            self.assertEqual(len(cards), 4)
            self.assertEqual(by_name["Poster Movie"]["logo"], poster)
            self.assertEqual(by_name["Poster Movie"]["category"], "Bangla")
            self.assertEqual(by_name["Poster Movie"]["resolution"], "HD 1080P")
            self.assertEqual(by_name["Tamil Dynamic Movie"]["category"], "South Indian")
            self.assertEqual(by_name["Dynamic Dubbed Movie"]["category"], "Dubbed")
            self.assertEqual(by_name["Unknown Future Category Movie"]["category"], "Mix")

            report = json.loads(
                (root / "reports/manual-movie-sources.json").read_text(encoding="utf-8")
            )
            self.assertEqual(report["discovered_file_count"], 4)
            self.assertTrue(all(item["status"] == "fresh" for item in report["sources"]))

    def test_dynamic_json_item_category_overrides_folder_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_root = root / "private/categories/New_Automatic_Category"
            source_root.mkdir(parents=True, exist_ok=True)
            (source_root / "movies.json").write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "name": "Explicit English Movie",
                                "category": "English Movies",
                                "year": 2026,
                                "url": "https://example.com/explicit-english-2026.mkv",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            config_path = root / "manual/movie-sources.json"
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(
                json.dumps(
                    {
                        "enabled": True,
                        "directory_sources": [
                            {
                                "id": "dynamic",
                                "path": str(root / "private/categories"),
                                "recursive": True,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            cards = load_manual_movies(**self._paths(root))
            self.assertEqual(len(cards), 1)
            self.assertEqual(cards[0]["category"], "English")


    def test_repository_json_res_list_format_is_supported(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_root = root / "private/categories/Hindi_Movies"
            source_root.mkdir(parents=True, exist_ok=True)
            poster = "https://image.tmdb.org/t500/example.jpg"
            (source_root / "hindi_movies.json").write_text(
                json.dumps(
                    [
                        {
                            "name": "Repository JSON Movie",
                            "category": "Hindi Movies",
                            "year": "2026",
                            "poster": poster,
                            "res_list": [
                                {
                                    "resolution": "HD 1080P",
                                    "link": "https://example.com/repository-json-1080p.mkv",
                                },
                                {
                                    "resolution": "4K 2160P HEVC",
                                    "link": "https://example.com/repository-json-2160p.mkv",
                                },
                            ],
                        }
                    ]
                ),
                encoding="utf-8",
            )
            config_path = root / "manual/movie-sources.json"
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(
                json.dumps(
                    {
                        "enabled": True,
                        "directory_sources": [
                            {
                                "id": "repo-json",
                                "path": str(root / "private/categories"),
                                "recursive": True,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            cards = load_manual_movies(**self._paths(root))
            self.assertEqual(len(cards), 1)
            self.assertEqual(cards[0]["name"], "Repository JSON Movie")
            self.assertEqual(cards[0]["category"], "Hindi")
            self.assertEqual(cards[0]["logo"], "https://image.tmdb.org/t/p/w500/example.jpg")
            self.assertEqual(cards[0]["url"], "https://example.com/repository-json-1080p.mkv")
            self.assertEqual(cards[0]["resolution"], "HD 1080P")
            self.assertEqual(len(cards[0]["backups"]), 1)
            self.assertEqual(cards[0]["backups"][0]["resolution_height"], 2160)


if __name__ == "__main__":
    unittest.main()
