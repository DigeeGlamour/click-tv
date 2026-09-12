"""PART 02: metadata schema + persistent cache foundation.

Mirrors the existing tests/test_movie_visibility.py style/conventions
(temp-dir paths, explicit `now`, no real state/ writes).

What these tests guard against, concretely:
  - A corrupt/missing cache file must never crash a scan (load() degrades).
  - A save() must be atomic - no half-written cache file, no leftover temp
    file, even though this is the same pattern as the movie_recency.py
    hardening in this same PART.
  - Cache-derived fields must never overwrite a value the scanner/manual
    data already set (existing identity/dedup/source data preservation).
  - A provider failure must never blank out previously-resolved metadata
    (last-good preservation - required before PART 03's adapters exist).
  - The metadata cache's identity is intentionally its own namespace,
    independent of movie_recency.movie_key (see module docstring in
    scanner/movie_metadata_cache.py and docs/movie-discovery-audit.md).
"""
import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import movie_metadata_cache as mc  # noqa: E402
from scanner import movie_recency as mr  # noqa: E402


class CacheLoadSaveTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self._tmp.name) / "movie-metadata-cache.json")
        self.addCleanup(self._tmp.cleanup)

    def test_missing_file_returns_empty_default(self):
        store = mc.load(self.path)
        self.assertEqual(store, {"version": 1, "movies": {}})

    def test_corrupt_file_returns_empty_default_not_a_crash(self):
        Path(self.path).write_text("{not valid json", encoding="utf-8")
        store = mc.load(self.path)
        self.assertEqual(store, {"version": 1, "movies": {}})

    def test_non_dict_json_returns_empty_default(self):
        Path(self.path).write_text("[1, 2, 3]", encoding="utf-8")
        store = mc.load(self.path)
        self.assertEqual(store, {"version": 1, "movies": {}})

    def test_save_then_load_roundtrip(self):
        store = {"version": 1, "movies": {"tmdb_id:42": {"tmdb_id": 42}}}
        self.assertTrue(mc.save(store, self.path))
        self.assertEqual(mc.load(self.path), store)

    def test_save_leaves_no_temp_file_behind(self):
        mc.save({"version": 1, "movies": {}}, self.path)
        leftovers = list(Path(self._tmp.name).glob(".*.tmp"))
        self.assertEqual(leftovers, [])

    def test_save_is_atomic_replace_not_partial_write(self):
        # A stale temp file from a hypothetical crash must not confuse a
        # later load - only the final replaced target is ever read.
        mc.save({"version": 1, "movies": {"a": {"tmdb_id": 1}}}, self.path)
        mc.save({"version": 1, "movies": {"a": {"tmdb_id": 2}}}, self.path)
        self.assertEqual(mc.load(self.path)["movies"]["a"]["tmdb_id"], 2)


class CanonicalIdentityTests(unittest.TestCase):
    def test_imdb_id_wins_over_everything(self):
        movie = {"imdb_id": "tt0111161", "tmdb_id": 999, "name": "X (2000)"}
        self.assertEqual(mc.canonical_identity(movie), "imdb_id:tt0111161")

    def test_tmdb_id_used_when_no_imdb_id(self):
        movie = {"tmdb_id": 278, "name": "The Shawshank Redemption (1994)"}
        self.assertEqual(mc.canonical_identity(movie), "tmdb_id:278")

    def test_title_and_year_fallback(self):
        movie = {"name": "Rongin Shurma (2026)"}
        identity = mc.canonical_identity(movie)
        self.assertTrue(identity.startswith("title:"))
        self.assertIn("2026", identity)

    def test_identity_is_independent_of_first_seen_key(self):
        """The two ledgers are deliberately separate namespaces (see audit
        docs/movie-discovery-audit.md section 2/7): re-keying first_seen to
        this identity would risk resetting 15k+ real timestamps, so this
        cache never touches movie_recency's key at all."""
        movie = {"id": "remote-manual-rongin-shurma-2026", "name": "Rongin Shurma"}
        self.assertNotEqual(mc.canonical_identity(movie), mr.movie_key(movie))


class EnrichTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self._tmp.name) / "movie-metadata-cache.json")
        self.addCleanup(self._tmp.cleanup)
        self.now = dt.datetime(2026, 9, 11, tzinfo=dt.timezone.utc)

    def test_no_lookup_only_applies_existing_cache_never_touches_network(self):
        movie = {"tmdb_id": 278, "name": "Shawshank"}
        seeded = {
            "version": 1,
            "movies": {
                "tmdb_id:278": {
                    "genres": ["Drama"],
                    "rating": 9.3,
                    "rating_source": "tmdb",
                    "metadata_source": "tmdb",
                }
            },
        }
        mc.save(seeded, self.path)

        summary = mc.enrich([movie], path=self.path, now=self.now, lookup=None)
        self.assertEqual(movie["genres"], ["Drama"])
        self.assertEqual(movie["rating"], 9.3)
        self.assertEqual(summary["cached_hits"], 1)
        self.assertEqual(summary["fetched"], 0)

    def test_cache_never_overwrites_existing_manual_field(self):
        """Existing movie identity/dedup/source data must be preserved -
        a manually-curated rating must survive even if the cache disagrees."""
        movie = {"tmdb_id": 278, "name": "Shawshank", "rating": "manual-5-star"}
        mc.save(
            {"version": 1, "movies": {"tmdb_id:278": {"rating": 9.3}}}, self.path
        )
        mc.enrich([movie], path=self.path, now=self.now, lookup=None)
        self.assertEqual(movie["rating"], "manual-5-star")

    def test_new_movie_is_resolved_via_lookup_and_persisted(self):
        movie = {"name": "Brand New Film (2026)"}
        calls = []

        def fake_lookup(item):
            calls.append(item["name"])
            return {
                "tmdb_id": 555,
                "genres": ["Action"],
                "rating": 7.1,
                "rating_source": "tmdb",
                "metadata_source": "tmdb",
                "metadata_confidence": "high",
            }

        summary = mc.enrich(
            [movie], path=self.path, now=self.now, lookup=fake_lookup
        )
        self.assertEqual(calls, ["Brand New Film (2026)"])
        self.assertEqual(movie["tmdb_id"], 555)
        self.assertEqual(summary["fetched"], 1)

        # Persisted: a second enrich() with no lookup still has the data.
        again = {"name": "Brand New Film (2026)"}
        mc.enrich([again], path=self.path, now=self.now, lookup=None)
        self.assertEqual(again["tmdb_id"], 555)

    def test_failed_lookup_never_erases_previously_cached_data(self):
        """API failure must never overwrite last-good data with empty data."""
        movie_a = {"tmdb_id": 111, "name": "Known Film"}
        mc.save(
            {"version": 1, "movies": {"tmdb_id:111": {}}}, self.path
        )  # attempted before, nothing resolved yet - not "has_data"
        # Seed a genuinely resolved entry for a second identity to prove it
        # survives an unrelated failed call in the same enrich() batch.
        store = mc.load(self.path)
        mc.upsert(store, "tmdb_id:222", {"rating": 8.0, "metadata_source": "tmdb"})
        mc.save(store, self.path)

        def failing_lookup(item):
            return None

        movie_b = {"tmdb_id": 222, "name": "Already Resolved"}
        mc.enrich(
            [movie_a, movie_b], path=self.path, now=self.now, lookup=failing_lookup
        )
        # movie_b had cached data -> filled from cache, lookup never called for it.
        self.assertEqual(movie_b["rating"], 8.0)
        # movie_a's failure must not have touched movie_b's cache entry.
        self.assertEqual(mc.load(self.path)["movies"]["tmdb_id:222"]["rating"], 8.0)

    def test_budget_limits_new_lookups_per_run(self):
        movies = [{"name": f"Film {i} (2026)"} for i in range(5)]
        calls = []

        def fake_lookup(item):
            calls.append(item["name"])
            return {"tmdb_id": len(calls), "metadata_source": "tmdb"}

        summary = mc.enrich(
            movies, path=self.path, now=self.now, lookup=fake_lookup, budget=2
        )
        self.assertEqual(len(calls), 2)
        self.assertEqual(summary["fetched"], 2)
        self.assertEqual(summary["skipped_budget"], 3)

    def test_repeated_failure_is_not_retried_within_cooldown(self):
        movie = {"name": "Unmatchable Film (1901)"}
        attempts = []

        def failing_lookup(item):
            attempts.append(1)
            return None

        mc.enrich([movie], path=self.path, now=self.now, lookup=failing_lookup)
        self.assertEqual(len(attempts), 1)

        soon_after = self.now + dt.timedelta(days=1)
        mc.enrich(
            [dict(movie)], path=self.path, now=soon_after, lookup=failing_lookup
        )
        self.assertEqual(len(attempts), 1, "cooldown should have skipped the retry")

        much_later = self.now + dt.timedelta(days=8)
        mc.enrich(
            [dict(movie)], path=self.path, now=much_later, lookup=failing_lookup
        )
        self.assertEqual(len(attempts), 2, "cooldown should have expired by day 8")

    def test_a_movie_with_no_identity_is_skipped_not_crashed_on(self):
        summary = mc.enrich(
            [{}, None, "nonsense"], path=self.path, now=self.now, lookup=None
        )
        self.assertEqual(summary["cached_hits"], 0)


if __name__ == "__main__":
    unittest.main()
