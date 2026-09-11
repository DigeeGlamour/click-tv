"""PART 05: real genre, honest rating labels, and artwork fallback.

Three separate promises are tested here, and each one has a way of being
quietly broken:

  - GENRE is real or absent. Five providers spell the same genre five ways,
    so "Science Fiction", "Sci-Fi" and "Science-Fiction" must land in one
    bucket; and a movie with no provider genres must end up with none
    rather than a plausible-looking guess.

  - RATING carries the source it actually came from. A TMDB user score
    presented as an IMDb rating is the specific dishonesty the plan calls
    out, so the label travels with the value everywhere.

  - ARTWORK never costs a movie its place. A film with no poster and no
    backdrop is still a film; the artwork chain may return nothing, and the
    movie must survive that untouched.
"""
import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import metadata_providers as mp  # noqa: E402
from scanner import movie_genre_index as mgi  # noqa: E402
from scanner import movie_genres as mg  # noqa: E402
from scanner import movie_metadata_cache as mc  # noqa: E402


class CanonicalGenreTests(unittest.TestCase):
    def test_the_frontend_set_is_exactly_the_eight_from_the_reference(self):
        self.assertEqual(
            list(mg.FRONTEND_GENRES),
            ["Action", "Comedy", "Horror", "Romance", "Thriller", "Animation", "Sci-Fi", "Crime"],
        )
        self.assertEqual(len(mg.GENRE_SLUGS), 8)
        self.assertEqual(mg.GENRE_SLUGS["Sci-Fi"], "sci-fi")

    def test_every_provider_spelling_of_science_fiction_lands_in_one_bucket(self):
        for spelling in ("Science Fiction", "Sci-Fi", "Science-Fiction", "scifi", "SciFi"):
            self.assertEqual(
                mg.canonical_genres([spelling]), ["Sci-Fi"], f"failed on {spelling!r}"
            )

    def test_tvmaze_anime_is_animation(self):
        self.assertEqual(mg.canonical_genres(["Anime"]), ["Animation"])

    def test_a_compound_provider_genre_expands_to_real_genres(self):
        self.assertEqual(mg.canonical_genres(["Action & Adventure"]), ["Action", "Adventure"])
        self.assertEqual(mg.canonical_genres(["Sci-Fi & Fantasy"]), ["Sci-Fi", "Fantasy"])

    def test_an_unknown_but_real_genre_is_kept_not_discarded(self):
        """The backend is not lossy just because the frontend is selective."""
        self.assertEqual(mg.canonical_genres(["Documentary"]), ["Documentary"])
        self.assertEqual(mg.canonical_genres(["mecha"]), ["Mecha"])

    def test_duplicates_across_spellings_collapse_once(self):
        self.assertEqual(
            mg.canonical_genres(["Sci-Fi", "Science Fiction", "Action"]), ["Sci-Fi", "Action"]
        )

    def test_empty_and_junk_input_produces_no_genre_rather_than_a_guess(self):
        self.assertEqual(mg.canonical_genres([]), [])
        self.assertEqual(mg.canonical_genres(["", None, "   "]), [])
        self.assertEqual(mg.canonical_genres("Action"), [], "a bare string is not a genre list")

    def test_frontend_genres_filters_to_the_eight_in_reference_order(self):
        self.assertEqual(
            mg.frontend_genres(["Thriller", "Documentary", "Action", "Drama"]),
            ["Action", "Thriller"],
        )


class ProviderGenreNormalisationTests(unittest.TestCase):
    """The canonical spelling is applied at the provider, before caching."""

    def test_tmdb_science_fiction_is_stored_as_sci_fi(self):
        search = {"results": [{"id": 1, "title": "Some Film", "release_date": "2020-01-01"}]}
        detail = {"genres": [{"name": "Science Fiction"}, {"name": "Action"}]}

        def fake_get_json(provider, url, headers=None):
            return detail if "/movie/1" in url else search

        with patch.dict("os.environ", {"TMDB_API_KEY": "k"}, clear=False), patch.object(
            mp, "_get_json", side_effect=fake_get_json
        ):
            result = mp.tmdb_metadata("Some Film", 2020)
        self.assertEqual(result["genres"], ["Sci-Fi", "Action"])

    def test_omdb_comma_string_becomes_a_canonical_list(self):
        payload = {"Response": "True", "imdbID": "tt1", "Genre": "Action, Sci-Fi, Crime"}
        with patch.dict("os.environ", {"OMDB_API_KEY": "k"}, clear=False), patch.object(
            mp, "_get_json", return_value=payload
        ):
            result = mp.omdb_metadata("Some Film")
        self.assertEqual(result["genres"], ["Action", "Sci-Fi", "Crime"])

    def test_a_movie_with_no_provider_genres_gets_none(self):
        payload = {"Response": "True", "imdbID": "tt1", "Genre": "N/A"}
        with patch.dict("os.environ", {"OMDB_API_KEY": "k"}, clear=False), patch.object(
            mp, "_get_json", return_value=payload
        ):
            result = mp.omdb_metadata("Some Film")
        self.assertNotIn("genres", result, "no genre is better than an invented one")


class RatingLabelTests(unittest.TestCase):
    def test_tmdb_score_is_labelled_tmdb_and_never_imdb(self):
        search = {"results": [{"id": 1, "title": "Some Film", "release_date": "2020-01-01"}]}
        detail = {"vote_average": 7.4, "vote_count": 100}

        def fake_get_json(provider, url, headers=None):
            return detail if "/movie/1" in url else search

        with patch.dict("os.environ", {"TMDB_API_KEY": "k"}, clear=False), patch.object(
            mp, "_get_json", side_effect=fake_get_json
        ):
            result = mp.tmdb_metadata("Some Film", 2020)
        self.assertEqual(result["rating"], 7.4)
        self.assertEqual(result["rating_source"], "TMDB")
        self.assertNotEqual(result["rating_source"], "IMDb")

    def test_omdb_and_cinemeta_imdb_ratings_are_labelled_imdb(self):
        omdb_payload = {"Response": "True", "imdbID": "tt1", "imdbRating": "8.1", "imdbVotes": "10"}
        with patch.dict("os.environ", {"OMDB_API_KEY": "k"}, clear=False), patch.object(
            mp, "_get_json", return_value=omdb_payload
        ):
            self.assertEqual(mp.omdb_metadata("Some Film")["rating_source"], "IMDb")

        with patch.object(mp, "_get_json", return_value={"meta": {"imdbRating": "8.1"}}):
            self.assertEqual(mp.cinemeta_metadata("tt1")["rating_source"], "IMDb")

    def test_anilist_score_is_labelled_anilist_on_a_ten_point_scale(self):
        payload = {"data": {"Media": {"averageScore": 88}}}
        with patch.object(mp, "_post_json", return_value=payload):
            result = mp.anilist_metadata("Some Anime")
        self.assertEqual(result["rating"], 8.8)
        self.assertEqual(result["rating_source"], "AniList")

    def test_tvmaze_score_is_labelled_tvmaze(self):
        with patch.object(mp, "_get_json", return_value={"rating": {"average": 9.2}}):
            self.assertEqual(mp.tvmaze_metadata("Some Show")["rating_source"], "TVMaze")

    def test_a_rating_never_travels_without_its_source(self):
        """The pairing is the whole point: a number with no provenance is
        indistinguishable from an invented one."""
        cases = [
            (mp.omdb_metadata, ({"Response": "True", "imdbID": "tt1", "imdbRating": "8.1"},), "_get_json"),
            (mp.cinemeta_metadata, ({"meta": {"imdbRating": "8.1"}},), "_get_json"),
            (mp.tvmaze_metadata, ({"rating": {"average": 9.2}},), "_get_json"),
        ]
        with patch.dict("os.environ", {"OMDB_API_KEY": "k"}, clear=False):
            for func, (payload,), helper in cases:
                with patch.object(mp, helper, return_value=payload):
                    result = func("tt1" if func is mp.cinemeta_metadata else "Some Title")
                self.assertIn("rating", result)
                self.assertIn("rating_source", result, f"{func.__name__} dropped the label")

    def test_no_rating_is_ever_synthesised_when_the_provider_has_none(self):
        payload = {"Response": "True", "imdbID": "tt1", "imdbRating": "N/A", "imdbVotes": "N/A"}
        with patch.dict("os.environ", {"OMDB_API_KEY": "k"}, clear=False), patch.object(
            mp, "_get_json", return_value=payload
        ):
            result = mp.omdb_metadata("Some Film")
        self.assertNotIn("rating", result)
        self.assertNotIn("rating_source", result)


class BackdropPriorityTests(unittest.TestCase):
    def _resolve(self, **provider_returns):
        defaults = {
            "tmdb_metadata": None,
            "omdb_metadata": None,
            "cinemeta_metadata": None,
            "moviesdatabase_metadata": None,
            "fanart_metadata": None,
        }
        defaults.update(provider_returns)
        patches = [patch.object(mp, name, return_value=value) for name, value in defaults.items()]
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        return mp.resolve_metadata({"name": "Some Film", "year": 2020})

    def test_tmdb_artwork_outranks_fanart_and_cinemeta(self):
        result = self._resolve(
            tmdb_metadata={"tmdb_id": 1, "backdrop": "https://x.test/tmdb.jpg", "metadata_source": "tmdb"},
            cinemeta_metadata={"backdrop": "https://x.test/cinemeta.jpg", "metadata_source": "cinemeta"},
        )
        self.assertEqual(result["backdrop"], "https://x.test/tmdb.jpg")
        self.assertEqual(result["backdrop_source"], "tmdb")

    def test_fanart_outranks_cinemeta_when_tmdb_has_no_artwork(self):
        result = self._resolve(
            tmdb_metadata={"tmdb_id": 1, "imdb_id": "tt1", "metadata_source": "tmdb"},
            cinemeta_metadata={"backdrop": "https://x.test/cinemeta.jpg", "metadata_source": "cinemeta"},
            fanart_metadata={"backdrop": "https://x.test/fanart.jpg", "metadata_source": "fanart"},
        )
        self.assertEqual(result["backdrop"], "https://x.test/fanart.jpg")
        self.assertEqual(result["backdrop_source"], "fanart")

    def test_cinemeta_is_used_when_nothing_better_exists(self):
        result = self._resolve(
            omdb_metadata={"imdb_id": "tt1", "metadata_source": "omdb"},
            cinemeta_metadata={"backdrop": "https://x.test/cinemeta.jpg", "metadata_source": "cinemeta"},
        )
        self.assertEqual(result["backdrop_source"], "cinemeta")

    def test_no_artwork_anywhere_is_simply_no_backdrop(self):
        result = self._resolve(omdb_metadata={"imdb_id": "tt1", "genres": ["Action"], "metadata_source": "omdb"})
        self.assertNotIn("backdrop", result)
        self.assertIsNotNone(result, "the metadata itself still resolved")


class ArtworkFallbackAtTheCacheTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = str(Path(self._tmp.name) / "movie-metadata-cache.json")
        self.now = dt.datetime(2026, 9, 11, tzinfo=dt.timezone.utc)

    def test_a_cached_backdrop_is_never_replaced(self):
        mc.save(
            {"version": 1, "movies": {"tmdb_id:1": {"backdrop": "https://x.test/cached.jpg"}}},
            self.path,
        )
        movie = {"tmdb_id": 1, "name": "Some Film", "logo": "https://x.test/poster.jpg"}
        mc.enrich([movie], path=self.path, now=self.now, lookup=None)
        self.assertEqual(movie["backdrop"], "https://x.test/cached.jpg")

    def test_the_poster_is_the_last_resort_backdrop_and_says_so(self):
        movie = {"name": "Some Film", "logo": "https://x.test/poster.jpg"}
        summary = mc.enrich([movie], path=self.path, now=self.now, lookup=None)
        self.assertEqual(movie["backdrop"], "https://x.test/poster.jpg")
        self.assertEqual(movie["backdrop_source"], "poster")
        self.assertEqual(summary["poster_backdrops"], 1)

    def test_the_poster_fallback_is_never_written_into_the_cache(self):
        """Caching it would make a stretched poster permanent: fill-only
        merging would then refuse the real backdrop when it arrives."""
        movie = {"tmdb_id": 1, "name": "Some Film", "logo": "https://x.test/poster.jpg"}
        mc.enrich([movie], path=self.path, now=self.now, lookup=None)
        self.assertEqual(mc.load(self.path)["movies"], {})

        # And a real backdrop arriving later still wins on the next movie object.
        mc.save(
            {"version": 1, "movies": {"tmdb_id:1": {"backdrop": "https://x.test/real.jpg"}}},
            self.path,
        )
        later = {"tmdb_id": 1, "name": "Some Film", "logo": "https://x.test/poster.jpg"}
        mc.enrich([later], path=self.path, now=self.now, lookup=None)
        self.assertEqual(later["backdrop"], "https://x.test/real.jpg")

    def test_a_movie_with_no_artwork_at_all_is_kept_untouched(self):
        movie = {"name": "Some Film"}
        movies = [movie]
        mc.enrich(movies, path=self.path, now=self.now, lookup=None)
        self.assertEqual(len(movies), 1, "artwork failure must never drop a movie")
        self.assertNotIn("backdrop", movie)

    def test_an_artwork_lookup_that_raises_never_drops_the_movie(self):
        def exploding_lookup(movie):
            raise RuntimeError("artwork provider down")

        movies = [{"name": "Some Film (2026)"}]
        mc.enrich(movies, path=self.path, now=self.now, lookup=exploding_lookup)
        self.assertEqual(len(movies), 1)
        self.assertEqual(movies[0]["name"], "Some Film (2026)")


class GenreIndexTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = str(Path(self._tmp.name) / "genres")

    def _payload(self, items):
        return {"Mix": {"index": {}, "page_contents": {"page-001.json": {"items": items}}}}

    def test_a_multi_genre_movie_appears_in_every_one_of_its_genres(self):
        payload = self._payload([{"id": "film-a", "genres": ["Action", "Crime", "Thriller"]}])
        indexes = mgi.build(payload)
        for slug in ("action", "crime", "thriller"):
            self.assertEqual(indexes[slug]["items"], ["film-a"], f"missing from {slug}")
        self.assertEqual(indexes["comedy"]["items"], [])

    def test_backend_only_genres_get_no_index_file(self):
        payload = self._payload([{"id": "film-a", "genres": ["Documentary", "Drama"]}])
        indexes = mgi.build(payload)
        self.assertEqual(sorted(indexes), sorted(mg.GENRE_SLUGS.values()))
        self.assertEqual(sum(doc["count"] for doc in indexes.values()), 0)

    def test_the_index_holds_ids_only_not_duplicated_cards(self):
        payload = self._payload(
            [{"id": "film-a", "name": "A", "logo": "x", "url": "y", "genres": ["Action"]}]
        )
        indexes = mgi.build(payload)
        self.assertEqual(indexes["action"]["items"], ["film-a"])
        serialized = json.dumps(indexes["action"])
        self.assertNotIn("logo", serialized)
        self.assertNotIn("url", serialized)

    def test_catalogue_order_is_preserved_so_new_arrivals_lead(self):
        payload = self._payload(
            [
                {"id": "newest", "genres": ["Action"]},
                {"id": "older", "genres": ["Action"]},
            ]
        )
        self.assertEqual(mgi.build(payload)["action"]["items"], ["newest", "older"])

    def test_the_same_movie_is_not_listed_twice_in_one_genre(self):
        payload = {
            "Mix": {"page_contents": {"page-001.json": {"items": [{"id": "film-a", "genres": ["Action"]}]}}},
            "Bangla": {"page_contents": {"page-001.json": {"items": [{"id": "film-a", "genres": ["Action"]}]}}},
        }
        self.assertEqual(mgi.build(payload)["action"]["items"], ["film-a"])

    def test_provider_spellings_are_indexed_under_the_canonical_genre(self):
        payload = self._payload([{"id": "film-a", "genres": ["Science Fiction"]}])
        self.assertEqual(mgi.build(payload)["sci-fi"]["items"], ["film-a"])

    def test_indexes_are_written_atomically_with_no_temp_files_left(self):
        payload = self._payload([{"id": "film-a", "genres": ["Action"]}])
        summary = mgi.generate(payload, root=self.root)
        self.assertEqual(summary["written"], 8)
        self.assertEqual(list(Path(self.root).glob(".*.tmp")), [])
        on_disk = json.loads(Path(self.root, "action.json").read_text(encoding="utf-8"))
        self.assertEqual(on_disk["genre"], "Action")
        self.assertEqual(on_disk["items"], ["film-a"])

    def test_an_empty_rebuild_never_overwrites_a_good_index(self):
        """The plan's NEVER rule, at the genre index: a provider outage must
        not blank a genre that was fine yesterday."""
        mgi.generate(self._payload([{"id": "film-a", "genres": ["Action"]}]), root=self.root)
        summary = mgi.generate(self._payload([]), root=self.root)
        self.assertGreaterEqual(summary["preserved"], 1)
        on_disk = json.loads(Path(self.root, "action.json").read_text(encoding="utf-8"))
        self.assertEqual(on_disk["items"], ["film-a"], "last-good index must survive")

    def test_a_movie_without_genres_is_simply_not_indexed(self):
        payload = self._payload([{"id": "film-a"}, {"id": "film-b", "genres": []}])
        self.assertEqual(mgi.build(payload)["action"]["items"], [])


if __name__ == "__main__":
    unittest.main()
