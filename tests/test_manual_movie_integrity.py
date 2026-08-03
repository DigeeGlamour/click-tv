from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scanner.movies import (
    _parse_manual_movies_text,
    _tmdb_poster_lookup,
    _valid_poster_url,
    process_movies,
)


BANGLA_FIXTURE = """Movie-1
Movie name: Bangla Four K
Movie Category: Bangla Movies
Movie year: 2026
RESOLUTION 1: HD 1080P
STREAM Link 1: https://example.com/bangla-1080p.mkv
RESOLUTION 2: 4K 2160P
STREAM Link 2: https://example.com/bangla-2160p.mkv

================================================================================
Movie-2
Movie name: Bangla Older
Movie Category: Bangla Movies
Movie year: 2025
RESOLUTION 1: HD 1080P
STREAM Link 1: https://example.com/bangla-older.mkv
"""

ENGLISH_FIXTURE = """Movie-1
Movie name: English Poster Missing
Movie Category: English Movies
Movie year: 2026
Movie poster: N/A
RESOLUTION 1: HD 1080P
STREAM Link 1: https://example.com/english-1080p.mkv

================================================================================
Movie-2
Movie name: English Older
Movie Category: English Movies
Movie year: 2024
RESOLUTION 1: HD 1080P
STREAM Link 1: https://example.com/english-older.mkv
"""

HINDI_FIXTURE = """Movie-1
Movie name: Hindi New
Movie Category: Hindi Movies
Movie year: 2026
RESOLUTION 1: HD 1080P
STREAM Link 1: https://example.com/hindi-new.mkv

================================================================================
Movie-2
Movie name: Hindi Older
Movie Category: Hindi Movies
Movie year: 2023
RESOLUTION 1: HD 1080P
STREAM Link 1: https://example.com/hindi-older.mkv
"""


class ManualMovieIntegrityTests(unittest.TestCase):
    @staticmethod
    def _fixture_files() -> list[tuple[str, str]]:
        return [
            ("Bangla_Movies/bangla_movies.txt", BANGLA_FIXTURE),
            ("English_Movies/english_movies.txt", ENGLISH_FIXTURE),
            ("Hindi_Movies/hindi_movies.txt", HINDI_FIXTURE),
            ("Dubbed/Hindi_Dubbed/hindi_dubbed_movies.txt", ""),
        ]

    def test_dynamic_snapshot_publishes_every_manual_movie_without_verification(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "manual").mkdir(parents=True, exist_ok=True)
            (root / "working").mkdir(parents=True, exist_ok=True)
            (root / "config").mkdir(parents=True, exist_ok=True)
            (root / "manual/movies.json").write_text(
                json.dumps({"enabled": True, "items": []}), encoding="utf-8"
            )
            (root / "manual/movies.txt").write_text("", encoding="utf-8")
            (root / "manual/movie-sources.json").write_text(
                json.dumps(
                    {
                        "version": 3,
                        "enabled": True,
                        "use_last_valid_cache": False,
                        "repository_sources": [
                            {
                                "id": "fixture",
                                "name": "Fixture latest",
                                "repository": "owner/repository",
                                "ref": "main",
                                "root": "categories",
                                "require_fresh": True,
                                "require_any_valid_file": True,
                                "require_all_files": False,
                            }
                        ],
                        "directory_sources": [],
                        "sources": [],
                    }
                ),
                encoding="utf-8",
            )
            (root / "working/bd-results.json").write_text(
                json.dumps({"results": []}), encoding="utf-8"
            )
            (root / "config/settings.json").write_text(
                json.dumps({"movie_page_size": 100}), encoding="utf-8"
            )

            snapshot = {
                "repository": "owner/repository",
                "ref": "main",
                "root": "categories",
                "revision": "fixture-revision",
                "file_count": 4,
            }

            old_cwd = Path.cwd()
            os.chdir(root)
            try:
                with patch(
                    "scanner.movies._github_repository_snapshot_files",
                    return_value=(self._fixture_files(), snapshot),
                ):
                    output = process_movies(
                        bd_results_path="working/bd-results.json",
                        settings_path="config/settings.json",
                        manual_movies_path="manual/movies.json",
                        manual_movies_text_path="manual/movies.txt",
                        remote_sources_path="manual/movie-sources.json",
                    )
            finally:
                os.chdir(old_cwd)

            expected = {"Bangla": 2, "English": 2, "Hindi": 2}
            for category, expected_count in expected.items():
                index = output[category]["index"]
                self.assertEqual(index["manual_trusted_count"], expected_count)
                items = [
                    item
                    for page in output[category]["page_contents"].values()
                    for item in page["items"]
                    if item.get("verification_status") == "manual_trusted"
                ]
                self.assertEqual(len(items), expected_count)
                self.assertTrue(all(item.get("skip_verification") is True for item in items))
                self.assertTrue(all(item.get("proxy_mode") == "direct_first" for item in items))

            integrity = json.loads(
                (root / "reports/manual-movie-integrity.json").read_text(encoding="utf-8")
            )
            self.assertEqual(integrity["status"], "passed")
            self.assertEqual(integrity["source_unique_total"], 6)
            self.assertEqual(integrity["published_manual_total"], 6)

            sources = json.loads(
                (root / "reports/manual-movie-sources.json").read_text(encoding="utf-8")
            )
            empty_entries = [
                item
                for item in sources["sources"]
                if item.get("relative_path", "").endswith("hindi_dubbed_movies.txt")
            ]
            self.assertEqual(len(empty_entries), 1)
            self.assertEqual(empty_entries[0]["status"], "skipped_empty")

    def test_4k_link_is_preserved_with_resolution_metadata(self) -> None:
        items = _parse_manual_movies_text(BANGLA_FIXTURE)
        movie = next(item for item in items if item["name"] == "Bangla Four K")
        heights = [
            int(link.get("resolution_height") or 0)
            for link in movie.get("links", [])
        ]
        self.assertIn(2160, heights)
        self.assertIn(1080, heights)

    def test_tmdb_search_accepts_exact_title_with_one_year_streaming_difference(self) -> None:
        movie_result = {
            "results": [
                {
                    "id": 1,
                    "title": "You Always",
                    "original_title": "You Always",
                    "release_date": "2025-01-01",
                    "poster_path": "/poster.jpg",
                    "popularity": 10,
                }
            ]
        }
        with patch("scanner.movies._tmdb_request_json", return_value=movie_result):
            poster = _tmdb_poster_lookup("You, Always", 2026)
        self.assertEqual(poster, "https://image.tmdb.org/t/p/w500/poster.jpg")

    def test_malformed_tmdb_source_poster_is_normalized(self) -> None:
        self.assertEqual(
            _valid_poster_url("https://image.tmdb.org/t500/example.jpg"),
            "https://image.tmdb.org/t/p/w500/example.jpg",
        )


if __name__ == "__main__":
    unittest.main()
