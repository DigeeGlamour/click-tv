from __future__ import annotations

import io
import json
import tempfile
import unittest
import zipfile
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
    def __init__(self, content: str | bytes, resolved_url: str = "https://example.com/source"):
        raw = content if isinstance(content, bytes) else content.encode("utf-8")
        self._io = io.BytesIO(raw)
        self._resolved_url = resolved_url

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, size: int = -1):
        return self._io.read(size)

    def geturl(self):
        return self._resolved_url


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

            # Repository TXT is authoritative. Duplicate JSON/M3U formats are ignored.
            self.assertEqual(len(cards), 1)
            self.assertEqual(by_name["Dynamic Dubbed Movie"]["category"], "Dubbed")

            report = json.loads(
                (root / "reports/manual-movie-sources.json").read_text(encoding="utf-8")
            )
            self.assertEqual(report["discovered_file_count"], 4)
            status_by_path = {item["relative_path"]: item["status"] for item in report["sources"]}
            self.assertEqual(status_by_path["Dubbed/Tamil_Dubbed/dubbed_movies.txt"], "fresh")
            self.assertEqual(status_by_path["Bangla_Movies/bangla_movies.json"], "skipped_unparseable")
            self.assertEqual(status_by_path["South_Indian/Tamil_Movies/tamil_movies.json"], "skipped_unparseable")
            self.assertEqual(status_by_path["Future_Category/future_movies.json"], "skipped_unparseable")

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
            self.assertEqual(cards, [])


    def test_repository_json_res_list_format_is_ignored_when_txt_is_authoritative(self) -> None:
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
            self.assertEqual(cards, [])

    def test_github_repository_source_downloads_fresh_snapshot_and_deduplicates_formats(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "manual/movie-sources.json"
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(
                json.dumps(
                    {
                        "version": 3,
                        "enabled": True,
                        "use_last_valid_cache": False,
                        "repository_sources": [
                            {
                                "id": "latest-public-repository",
                                "name": "Latest Public Repository",
                                "repository": "0matbank/hopeful-research",
                                "ref": "main",
                                "root": "categories",
                                "token_env": "PRIVATE_MOVIE_SOURCE_TOKEN",
                                "recursive": True,
                                "extensions": [".txt"],
                                "ignore_filenames": ["history.txt"],
                                "require_fresh": True,
                                "require_all_files": True,
                            }
                        ],
                        "directory_sources": [],
                        "sources": [],
                    }
                ),
                encoding="utf-8",
            )

            txt_content = REMOTE_TEXT.replace(
                "Remote Exact Movie", "Latest Repository Movie"
            ).replace(
                "https://example.com/remote-exact-movie-2026-1080p.mkv",
                "https://example.com/latest-repository-movie-2026.mkv",
            )
            json_content = json.dumps(
                [
                    {
                        "name": "Latest Repository Movie",
                        "category": "Bangla Movies",
                        "year": 2026,
                        "poster": "https://image.tmdb.org/t/p/w500/latest.jpg",
                        "res_list": [
                            {
                                "resolution": "HD 1080P",
                                "link": "https://example.com/latest-repository-movie-2026.mkv",
                            }
                        ],
                    }
                ]
            )

            archive_io = io.BytesIO()
            with zipfile.ZipFile(archive_io, "w", zipfile.ZIP_DEFLATED) as archive:
                prefix = "0matbank-hopeful-research-abcdef0/categories/Bangla_Movies"
                archive.writestr(f"{prefix}/bangla_movies.txt", txt_content)
                archive.writestr(f"{prefix}/bangla_movies.json", json_content)
                archive.writestr(f"{prefix}/history.txt", txt_content)

            captured_headers = {}

            def fake_urlopen(request, timeout=0):
                captured_headers.update(dict(request.header_items()))
                return _FakeResponse(
                    archive_io.getvalue(),
                    "https://codeload.github.com/0matbank/hopeful-research/legacy.zip/abcdef0",
                )

            with patch.dict("os.environ", {}, clear=False):
                with patch("urllib.request.urlopen", side_effect=fake_urlopen):
                    cards = load_manual_movies(**self._paths(root))

            self.assertEqual(len(cards), 1)
            self.assertEqual(cards[0]["name"], "Latest Repository Movie")
            self.assertEqual(cards[0]["category"], "Bangla")
            self.assertEqual(cards[0]["verification_status"], "manual_trusted")
            self.assertTrue(cards[0]["skip_verification"])
            self.assertEqual(cards[0]["proxy_mode"], "direct_first")
            self.assertNotIn("Authorization", captured_headers)

            report = json.loads(
                (root / "reports/manual-movie-sources.json").read_text(encoding="utf-8")
            )
            self.assertEqual(report["version"], 4)
            self.assertEqual(report["discovered_file_count"], 1)
            self.assertEqual(report["repository_snapshots"][0]["revision"], "abcdef0")
            self.assertTrue(all(item["status"] == "fresh" for item in report["sources"]))

    def test_required_repository_failure_stops_partial_manual_publish(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "manual").mkdir(parents=True, exist_ok=True)
            (root / "manual/movies.txt").write_text(REMOTE_TEXT, encoding="utf-8")
            (root / "manual/movie-sources.json").write_text(
                json.dumps(
                    {
                        "version": 3,
                        "enabled": True,
                        "repository_sources": [
                            {
                                "id": "required-latest",
                                "repository": "0matbank/hopeful-research",
                                "ref": "main",
                                "root": "categories",
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



    def test_repository_skips_empty_files_but_keeps_valid_latest_movies(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "manual/movie-sources.json"
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(
                json.dumps(
                    {
                        "version": 3,
                        "enabled": True,
                        "use_last_valid_cache": False,
                        "repository_sources": [
                            {
                                "id": "latest-with-empty-placeholder",
                                "name": "Latest With Empty Placeholder",
                                "repository": "0matbank/hopeful-research",
                                "ref": "main",
                                "root": "categories",
                                "recursive": True,
                                "extensions": [".txt", ".json"],
                                "require_fresh": True,
                                "require_all_files": False,
                                "require_any_valid_file": True,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            archive_io = io.BytesIO()
            with zipfile.ZipFile(archive_io, "w", zipfile.ZIP_DEFLATED) as archive:
                prefix = "0matbank-hopeful-research-abcdef0/categories"
                archive.writestr(
                    f"{prefix}/Bangla_Movies/bangla_movies.txt",
                    REMOTE_TEXT.replace("Remote Exact Movie", "Valid Bangla Manual Movie"),
                )
                archive.writestr(
                    f"{prefix}/Dubbed/Hindi_Dubbed/hindi_dubbed_movies.txt",
                    "",
                )

            with patch(
                "urllib.request.urlopen",
                return_value=_FakeResponse(
                    archive_io.getvalue(),
                    "https://codeload.github.com/0matbank/hopeful-research/legacy.zip/abcdef0",
                ),
            ):
                cards = load_manual_movies(**self._paths(root))

            self.assertEqual(len(cards), 1)
            self.assertEqual(cards[0]["name"], "Valid Bangla Manual Movie")
            self.assertEqual(cards[0]["category"], "Bangla")
            self.assertEqual(cards[0]["verification_status"], "manual_trusted")
            self.assertTrue(cards[0]["skip_verification"])
            self.assertEqual(cards[0]["proxy_mode"], "direct_first")

            report = json.loads(
                (root / "reports/manual-movie-sources.json").read_text(encoding="utf-8")
            )
            by_path = {item["relative_path"]: item for item in report["sources"]}
            self.assertEqual(
                by_path["Bangla_Movies/bangla_movies.txt"]["status"],
                "fresh",
            )
            self.assertEqual(
                by_path["Dubbed/Hindi_Dubbed/hindi_dubbed_movies.txt"]["status"],
                "skipped_empty",
            )
            self.assertEqual(
                by_path["Dubbed/Hindi_Dubbed/hindi_dubbed_movies.txt"]["item_count"],
                0,
            )

    def test_repository_with_only_empty_files_still_stops_partial_publish(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "manual").mkdir(parents=True, exist_ok=True)
            (root / "manual/movies.txt").write_text(REMOTE_TEXT, encoding="utf-8")
            (root / "manual/movie-sources.json").write_text(
                json.dumps(
                    {
                        "version": 3,
                        "enabled": True,
                        "repository_sources": [
                            {
                                "id": "latest-empty-repository",
                                "name": "Latest Empty Repository",
                                "repository": "0matbank/hopeful-research",
                                "ref": "main",
                                "root": "categories",
                                "require_fresh": True,
                                "require_all_files": False,
                                "require_any_valid_file": True,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            archive_io = io.BytesIO()
            with zipfile.ZipFile(archive_io, "w", zipfile.ZIP_DEFLATED) as archive:
                prefix = "0matbank-hopeful-research-abcdef0/categories"
                archive.writestr(f"{prefix}/Bangla_Movies/bangla_movies.txt", "")
                archive.writestr(f"{prefix}/Hindi_Movies/hindi_movies.json", "[]")

            with patch(
                "urllib.request.urlopen",
                return_value=_FakeResponse(
                    archive_io.getvalue(),
                    "https://codeload.github.com/0matbank/hopeful-research/legacy.zip/abcdef0",
                ),
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "contained no parseable Movie or Series items",
                ):
                    load_manual_movies(**self._paths(root))


if __name__ == "__main__":
    unittest.main()
