"""The movie browse/search index (PARTs 12-13).

One small file covering the whole catalogue, so "every Action film" and
"every film matching this text" are both answerable in the browser without
loading a single category page - and without the browser ever calling a
metadata API.

The index is card-shaped and deliberately playback-free. A result carries
an id; clicking it resolves the real published record and hands that to the
existing player. That separation is what keeps stream configuration in one
place instead of duplicated across every index the site ships.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import movie_discovery as md  # noqa: E402


def _catalog(*movies):
    return {"Mix": {"index": {}, "page_contents": {"page-001.json": {"items": list(movies)}}}}


class SearchIndexShapeTests(unittest.TestCase):
    def test_the_index_carries_what_a_card_and_a_search_need(self):
        document = md.build_search_index(_catalog({
            "id": "film-a",
            "name": "Some Film",
            "category": "Bangla",
            "year": 2026,
            "logo": "https://x.test/poster.jpg",
            "genres": ["Action", "Crime"],
            "rating": 7.5,
            "rating_source": "IMDb",
            "first_seen_at": "2026-09-01T00:00:00+00:00",
        }))
        entry = document["items"][0]
        self.assertEqual(entry["id"], "film-a")
        self.assertEqual(entry["poster"], "https://x.test/poster.jpg")
        self.assertEqual(entry["genres"], ["Action", "Crime"])
        self.assertEqual(entry["rating_source"], "IMDb")
        self.assertEqual(entry["type"], "movie")

    def test_no_stream_url_token_or_header_is_ever_indexed(self):
        document = md.build_search_index(_catalog({
            "id": "film-a",
            "name": "Some Film",
            "url": "https://secret.test/stream.mkv",
            "backups": [{"url": "https://secret.test/backup.mkv"}],
            "headers": {"Referer": "https://secret.test/", "Authorization": "Bearer nope"},
            "playback_id": "ctv_deadbeef",
            "proxy_mode": "direct_first",
            "drm": {"clearkey": "secret"},
        }))
        serialized = json.dumps(document)
        for forbidden in md.FORBIDDEN_CARD_FIELDS:
            self.assertNotIn(f'"{forbidden}"', serialized, f"{forbidden} leaked into the index")
        self.assertNotIn("secret.test", serialized)
        self.assertNotIn("Bearer", serialized)

    def test_absent_fields_are_omitted_rather_than_serialised_as_null(self):
        entry = md.build_search_index(_catalog({"id": "film-a", "name": "Bare"}))["items"][0]
        self.assertNotIn("rating", entry)
        self.assertNotIn("genres", entry)
        self.assertNotIn("year", entry)

    def test_a_title_without_an_id_cannot_be_indexed(self):
        document = md.build_search_index(_catalog({"name": "No Id"}, {"id": "film-a"}))
        self.assertEqual([e["id"] for e in document["items"]], ["film-a"])

    def test_one_film_is_indexed_once_even_across_categories(self):
        payload = {
            "Mix": {"page_contents": {"p": {"items": [{"id": "film-a", "name": "A"}]}}},
            "Bangla": {"page_contents": {"p": {"items": [{"id": "film-a", "name": "A"}]}}},
        }
        self.assertEqual(md.build_search_index(payload)["count"], 1)


class SearchIndexWriteTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = str(Path(self._tmp.name) / "search-index.json")

    def test_generate_writes_atomically(self):
        summary = md.generate_search_index(_catalog({"id": "film-a", "name": "A"}), output_path=self.path)
        self.assertEqual(summary["count"], 1)
        self.assertEqual(json.loads(Path(self.path).read_text(encoding="utf-8"))["count"], 1)
        self.assertEqual(list(Path(self.path).parent.glob(".*.tmp")), [])

    def test_an_empty_rebuild_never_blanks_a_good_index(self):
        """An empty index would make every genre and every search look empty,
        which is a far louder failure than a stale one."""
        md.generate_search_index(_catalog({"id": "film-a", "name": "A"}), output_path=self.path)
        summary = md.generate_search_index(_catalog(), output_path=self.path)
        self.assertTrue(summary["preserved"])
        self.assertEqual(json.loads(Path(self.path).read_text(encoding="utf-8"))["count"], 1)

    def test_a_first_build_on_an_empty_catalogue_writes_an_honest_empty_index(self):
        summary = md.generate_search_index(_catalog(), output_path=self.path)
        self.assertFalse(summary["preserved"])
        self.assertEqual(json.loads(Path(self.path).read_text(encoding="utf-8"))["count"], 0)


if __name__ == "__main__":
    unittest.main()
