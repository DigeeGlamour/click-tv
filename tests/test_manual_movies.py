from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scanner.movies import load_manual_movies, paginate_movie_list


class ManualMovieTests(unittest.TestCase):
    def _load(self, root: Path, **kwargs):
        return load_manual_movies(
            manual_movies_path=kwargs.get("manual_movies_path", root / "manual/movies.json"),
            manual_movies_text_path=kwargs.get("manual_movies_text_path", root / "manual/movies.txt"),
            poster_cache_path=root / "state/manual-movie-posters.json",
            generated_movies_root=root / "data/movies",
            remote_sources_path=root / "manual/movie-sources.json",
            remote_cache_path=root / "state/manual-movie-remote-cache.json",
            conflict_report_path=root / "reports/manual-movie-conflicts.json",
            missing_poster_report_path=root / "reports/manual-movie-poster-missing.json",
            source_report_path=root / "reports/manual-movie-sources.json",
        )

    def test_manual_metadata_is_preserved_and_verification_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            text_path = root / "manual/movies.txt"
            text_path.parent.mkdir(parents=True, exist_ok=True)
            text_path.write_text(
                """Movie-1
Movie name: Original Name Exactly
Movie Category: Bangla Movies
Movie year: 2026

RESOLUTION 1: HD 1080P
STREAM Link 1: https://example.com/different-name-2025-720p.mkv
""",
                encoding="utf-8",
            )

            cards = self._load(root)
            self.assertEqual(len(cards), 1)
            card = cards[0]
            self.assertEqual(card["name"], "Original Name Exactly")
            self.assertEqual(card["year"], 2026)
            self.assertEqual(card["resolution"], "HD 1080P")
            self.assertTrue(card["manual_source"])
            self.assertTrue(card["skip_verification"])
            self.assertEqual(card["verification_status"], "manual_trusted")

    def test_latest_year_first_and_manual_first_inside_same_year(self) -> None:
        movies = [
            {"id": "auto-2026", "name": "Auto", "year": 2026, "verification_status": "verified_global"},
            {"id": "remote-2025", "name": "Remote", "year": 2025, "manual_source": True, "manual_source_tier": 2, "manual_position": 1, "verification_status": "manual_trusted"},
            {"id": "local-2024", "name": "Local Older", "year": 2024, "manual_source": True, "manual_source_tier": 0, "manual_position": 2, "verification_status": "manual_trusted"},
            {"id": "local-2026", "name": "Local Newer", "year": 2026, "manual_source": True, "manual_source_tier": 0, "manual_position": 1, "verification_status": "manual_trusted"},
        ]
        payload = paginate_movie_list(movies, "Bangla", page_size=100)
        items = payload["page_contents"]["page-001.json"]["items"]
        self.assertEqual(
            [item["id"] for item in items],
            ["local-2026", "auto-2026", "remote-2025", "local-2024"],
        )

    def test_compatible_manual_link_becomes_primary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            text_path = root / "manual/movies.txt"
            text_path.parent.mkdir(parents=True, exist_ok=True)
            text_path.write_text(
                """Movie-1
Movie name: Multi Link
Movie Category: Bangla Movies
Movie year: 2026

RESOLUTION 1: 4K 2160P AV1
STREAM Link 1: https://example.com/multi-2160p-av1.mkv

RESOLUTION 2: HD 1080P
STREAM Link 2: https://example.com/multi-1080p.mkv
""",
                encoding="utf-8",
            )
            cards = self._load(root)
            self.assertEqual(cards[0]["url"], "https://example.com/multi-1080p.mkv")
            self.assertEqual(cards[0]["backups"][0]["url"], "https://example.com/multi-2160p-av1.mkv")

    def test_cached_exact_poster_is_reused(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manual_path = root / "manual/movies.json"
            cache_path = root / "state/manual-movie-posters.json"
            manual_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            manual_path.write_text(
                json.dumps({"items": [{"name": "Cached Poster Movie", "year": 2026, "links": ["https://example.com/cached.mkv"]}]}),
                encoding="utf-8",
            )
            cache_path.write_text(
                json.dumps({"version": 1, "posters": {"cached-poster:2026": "https://image.tmdb.org/t/p/w500/cached.jpg"}}),
                encoding="utf-8",
            )
            cards = self._load(root, manual_movies_path=manual_path)
            self.assertEqual(cards[0]["logo"], "https://image.tmdb.org/t/p/w500/cached.jpg")


if __name__ == "__main__":
    unittest.main()
