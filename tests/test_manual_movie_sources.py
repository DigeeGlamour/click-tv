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


if __name__ == "__main__":
    unittest.main()
