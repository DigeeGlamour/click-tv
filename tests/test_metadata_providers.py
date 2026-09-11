"""PART 03: multi-provider metadata adapters + fallback orchestration.

No test here makes a real network call - every provider's HTTP layer
(_get_json/_post_json) is patched, per the project's existing convention
(see tests/test_manual_movie_integrity.py's use of unittest.mock.patch on
scanner.movies._tmdb_request_json). Field mappings used below were verified
live against the real APIs on 2026-09-11 (see scanner/metadata_providers.py
docstrings); these tests pin that verified shape so a provider-side
response-format change is caught here.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import metadata_providers as mp  # noqa: E402


def _env(**overrides):
    """Patch os.environ for one test, clearing provider keys by default so
    a missing-key path is the default unless a test opts in."""
    base = {
        "TMDB_API_KEY": "",
        "TMDB_API_TOKEN": "",
        "OMDB_API_KEY": "",
        "FANART_API_KEY": "",
        "RAPIDAPI_KEY": "",
        "RAPIDAPI_MOVIES_HOST": "",
    }
    base.update(overrides)
    return patch.dict(os.environ, base, clear=False)


class TmdbMetadataTests(unittest.TestCase):
    def test_no_credentials_returns_none_without_any_request(self):
        with _env(), patch.object(mp, "_get_json") as get_json:
            self.assertIsNone(mp.tmdb_metadata("Inception", 2010))
            get_json.assert_not_called()

    def test_match_builds_full_normalized_record(self):
        search_payload = {
            "results": [
                {
                    "id": 27205,
                    "title": "Inception",
                    "release_date": "2010-07-16",
                    "vote_average": 8.8,
                    "vote_count": 34000,
                    "backdrop_path": "/searchbackdrop.jpg",
                }
            ]
        }
        detail_payload = {
            "imdb_id": "tt1375666",
            "release_date": "2010-07-16",
            "genres": [{"name": "Action"}, {"name": "Sci-Fi"}],
            "vote_average": 8.8,
            "vote_count": 34000,
            "backdrop_path": "/detailbackdrop.jpg",
        }

        def fake_get_json(provider, url, headers=None):
            return detail_payload if "/movie/27205" in url else search_payload

        with _env(TMDB_API_KEY="key"), patch.object(mp, "_get_json", side_effect=fake_get_json):
            result = mp.tmdb_metadata("Inception", 2010)

        self.assertEqual(result["tmdb_id"], 27205)
        self.assertEqual(result["imdb_id"], "tt1375666")
        self.assertEqual(result["release_date"], "2010-07-16")
        self.assertEqual(sorted(result["genres"]), ["Action", "Sci-Fi"])
        self.assertEqual(result["rating"], 8.8)
        self.assertEqual(result["rating_source"], "TMDB")
        self.assertTrue(result["backdrop"].endswith("/detailbackdrop.jpg"))
        self.assertEqual(result["metadata_source"], "tmdb")

    def test_low_similarity_match_is_rejected(self):
        search_payload = {"results": [{"id": 1, "title": "Completely Unrelated Film", "vote_average": 5}]}
        with _env(TMDB_API_KEY="key"), patch.object(mp, "_get_json", return_value=search_payload):
            self.assertIsNone(mp.tmdb_metadata("Inception", 2010))

    def test_empty_results_returns_none(self):
        with _env(TMDB_API_KEY="key"), patch.object(mp, "_get_json", return_value={"results": []}):
            self.assertIsNone(mp.tmdb_metadata("Inception", 2010))


class OmdbMetadataTests(unittest.TestCase):
    def test_no_key_returns_none(self):
        with _env(), patch.object(mp, "_get_json") as get_json:
            self.assertIsNone(mp.omdb_metadata("Inception", 2010))
            get_json.assert_not_called()

    def test_response_false_returns_none(self):
        with _env(OMDB_API_KEY="key"), patch.object(mp, "_get_json", return_value={"Response": "False"}):
            self.assertIsNone(mp.omdb_metadata("Nonexistent Movie"))

    def test_full_record_parsed_correctly(self):
        payload = {
            "Response": "True",
            "imdbID": "tt1375666",
            "Released": "16 Jul 2010",
            "Genre": "Action, Adventure, Sci-Fi",
            "imdbRating": "8.8",
            "imdbVotes": "2,811,614",
        }
        with _env(OMDB_API_KEY="key"), patch.object(mp, "_get_json", return_value=payload):
            result = mp.omdb_metadata("Inception", 2010)
        self.assertEqual(result["imdb_id"], "tt1375666")
        self.assertEqual(result["release_date"], "2010-07-16")
        self.assertEqual(result["genres"], ["Action", "Adventure", "Sci-Fi"])
        self.assertEqual(result["rating"], 8.8)
        self.assertEqual(result["rating_votes"], 2811614)
        self.assertEqual(result["rating_source"], "IMDb")
        self.assertEqual(result["metadata_source"], "omdb")

    def test_na_rating_is_not_fabricated_as_zero(self):
        payload = {"Response": "True", "imdbID": "tt1", "imdbRating": "N/A", "imdbVotes": "N/A"}
        with _env(OMDB_API_KEY="key"), patch.object(mp, "_get_json", return_value=payload):
            result = mp.omdb_metadata("Some Film")
        self.assertNotIn("rating", result)
        self.assertNotIn("rating_votes", result)


class CinemetaMetadataTests(unittest.TestCase):
    def test_non_imdb_id_returns_none(self):
        with patch.object(mp, "_get_json") as get_json:
            self.assertIsNone(mp.cinemeta_metadata("not-an-imdb-id"))
            get_json.assert_not_called()

    def test_year_only_release_info_is_not_used_as_release_date(self):
        """Never fabricate a full release date out of a bare year."""
        payload = {"meta": {"genres": ["Action"], "releaseInfo": "2010", "imdbRating": "8.8"}}
        with patch.object(mp, "_get_json", return_value=payload):
            result = mp.cinemeta_metadata("tt1375666")
        self.assertNotIn("release_date", result)
        self.assertEqual(result["genres"], ["Action"])
        self.assertEqual(result["rating"], 8.8)

    def test_full_date_release_info_is_used(self):
        payload = {"meta": {"releaseInfo": "2010-07-16", "background": "https://x.test/bg.jpg"}}
        with patch.object(mp, "_get_json", return_value=payload):
            result = mp.cinemeta_metadata("tt1375666")
        self.assertEqual(result["release_date"], "2010-07-16")
        self.assertEqual(result["backdrop"], "https://x.test/bg.jpg")


class FanartMetadataTests(unittest.TestCase):
    def test_missing_key_or_id_returns_none(self):
        with _env():
            self.assertIsNone(mp.fanart_metadata(27205))
        with _env(FANART_API_KEY="key"):
            self.assertIsNone(mp.fanart_metadata("not-numeric"))

    def test_only_backdrop_is_ever_returned_never_rating_or_genre(self):
        """Fanart must never become a release/rating/trending contributor."""
        payload = {"moviebackground": [{"url": "https://x.test/bg.jpg"}]}
        with _env(FANART_API_KEY="key"), patch.object(mp, "_get_json", return_value=payload):
            result = mp.fanart_metadata(27205)
        self.assertEqual(result, {"backdrop": "https://x.test/bg.jpg", "metadata_source": "fanart"})


class MoviesDatabaseMetadataTests(unittest.TestCase):
    def test_no_key_returns_none(self):
        with _env(), patch.object(mp, "_get_json") as get_json:
            self.assertIsNone(mp.moviesdatabase_metadata("Inception", 2010))
            get_json.assert_not_called()

    def test_server_side_timeout_null_results_returns_none(self):
        """Real, observed provider behaviour: {"results": null} on a backend timeout."""
        with _env(RAPIDAPI_KEY="key"), patch.object(mp, "_get_json", return_value={"results": None}):
            self.assertIsNone(mp.moviesdatabase_metadata("The Dark Knight", 2008))

    def test_full_three_call_flow_builds_normalized_record(self):
        search_payload = {
            "results": [
                {
                    "id": "tt1375666",
                    "titleText": {"text": "Inception"},
                    "releaseYear": {"year": 2010},
                    "releaseDate": {"day": 16, "month": 7, "year": 2010},
                }
            ]
        }
        genres_payload = {"results": {"genres": {"genres": [{"text": "Action"}, {"text": "Sci-Fi"}]}}}
        ratings_payload = {"results": {"averageRating": 8.8, "numVotes": 2811614}}

        def fake_get_json(provider, url, headers=None):
            if "/ratings" in url:
                return ratings_payload
            if "info=genres" in url:
                return genres_payload
            return search_payload

        with _env(RAPIDAPI_KEY="key"), patch.object(mp, "_get_json", side_effect=fake_get_json):
            result = mp.moviesdatabase_metadata("Inception", 2010)

        self.assertEqual(result["imdb_id"], "tt1375666")
        self.assertEqual(result["release_date"], "2010-07-16")
        self.assertEqual(result["genres"], ["Action", "Sci-Fi"])
        self.assertEqual(result["rating"], 8.8)
        self.assertEqual(result["rating_votes"], 2811614)
        self.assertEqual(result["metadata_source"], "moviesdatabase")


class TvmazeMetadataTests(unittest.TestCase):
    def test_bare_year_premiered_is_not_used_as_release_date(self):
        payload = {"genres": ["Drama"], "premiered": "2008", "rating": {"average": 9.2}}
        with patch.object(mp, "_get_json", return_value=payload):
            result = mp.tvmaze_metadata("Breaking Bad")
        self.assertNotIn("release_date", result)
        self.assertEqual(result["rating"], 9.2)

    def test_full_date_premiered_is_used(self):
        payload = {"premiered": "2008-01-20"}
        with patch.object(mp, "_get_json", return_value=payload):
            result = mp.tvmaze_metadata("Breaking Bad")
        self.assertEqual(result["release_date"], "2008-01-20")


class AnilistMetadataTests(unittest.TestCase):
    def test_api_down_degrades_to_none(self):
        """Confirmed live 2026-09-11: AniList can return a GraphQL error body
        with no `data` at all. Must never crash."""
        with patch.object(mp, "_post_json", return_value={"errors": [{"message": "disabled"}]}):
            self.assertIsNone(mp.anilist_metadata("Naruto"))

    def test_score_is_normalized_to_a_0_to_10_scale(self):
        payload = {"data": {"Media": {"averageScore": 88, "genres": ["Action"]}}}
        with patch.object(mp, "_post_json", return_value=payload):
            result = mp.anilist_metadata("Naruto")
        self.assertEqual(result["rating"], 8.8)
        self.assertEqual(result["rating_source"], "AniList")


class ResolveMetadataOrchestratorTests(unittest.TestCase):
    def test_no_title_returns_none(self):
        self.assertIsNone(mp.resolve_metadata({}))
        self.assertIsNone(mp.resolve_metadata("not a dict"))

    def test_tmdb_success_short_circuits_lower_priority_providers(self):
        tmdb_result = {
            "tmdb_id": 1, "imdb_id": "tt1", "release_date": "2010-07-16",
            "genres": ["Action"], "rating": 8.8, "rating_votes": 100,
            "rating_source": "tmdb", "backdrop": "https://x.test/b.jpg",
            "metadata_source": "tmdb", "metadata_confidence": "high",
        }
        with _env(), \
             patch.object(mp, "tmdb_metadata", return_value=tmdb_result), \
             patch.object(mp, "omdb_metadata") as omdb, \
             patch.object(mp, "cinemeta_metadata") as cinemeta, \
             patch.object(mp, "moviesdatabase_metadata") as mdb, \
             patch.object(mp, "fanart_metadata") as fanart:
            result = mp.resolve_metadata({"name": "Inception", "year": 2010})

        omdb.assert_not_called()
        cinemeta.assert_not_called()
        mdb.assert_not_called()
        fanart.assert_not_called()
        self.assertEqual(result["metadata_source"], "tmdb")
        self.assertEqual(result["tmdb_id"], 1)

    def test_fallback_chain_fills_gaps_without_overwriting_higher_priority_fields(self):
        """TMDB gives an id + release_date but nothing else; OMDb must only
        fill the missing genres/rating, never touch what TMDB already set."""
        tmdb_partial = {
            "tmdb_id": 1, "release_date": "2010-07-16", "metadata_source": "tmdb",
        }
        omdb_full = {
            "imdb_id": "tt1", "release_date": "1999-01-01",  # must NOT win - TMDB already set it
            "genres": ["Action"], "rating": 8.8, "rating_source": "imdb",
            "metadata_source": "omdb",
        }
        with _env(), \
             patch.object(mp, "tmdb_metadata", return_value=tmdb_partial), \
             patch.object(mp, "omdb_metadata", return_value=omdb_full), \
             patch.object(mp, "cinemeta_metadata", return_value=None), \
             patch.object(mp, "moviesdatabase_metadata", return_value=None), \
             patch.object(mp, "fanart_metadata", return_value=None):
            result = mp.resolve_metadata({"name": "Inception", "year": 2010})

        self.assertEqual(result["release_date"], "2010-07-16", "TMDB's field must win over OMDb's")
        self.assertEqual(result["genres"], ["Action"], "OMDb should fill the gap TMDB left")
        self.assertEqual(result["metadata_source"], "tmdb", "first contributing (highest-priority) source")
        self.assertIn("metadata_sources", result)
        self.assertEqual(result["metadata_sources"], ["tmdb", "omdb"])

    def test_all_providers_fail_returns_none(self):
        with _env(), \
             patch.object(mp, "tmdb_metadata", return_value=None), \
             patch.object(mp, "omdb_metadata", return_value=None), \
             patch.object(mp, "cinemeta_metadata", return_value=None), \
             patch.object(mp, "moviesdatabase_metadata", return_value=None), \
             patch.object(mp, "fanart_metadata", return_value=None):
            self.assertIsNone(mp.resolve_metadata({"name": "Totally Obscure Film"}))

    def test_tvmaze_only_consulted_for_series_kind(self):
        with _env(), \
             patch.object(mp, "tmdb_metadata", return_value=None), \
             patch.object(mp, "omdb_metadata", return_value=None), \
             patch.object(mp, "moviesdatabase_metadata", return_value=None), \
             patch.object(mp, "tvmaze_metadata") as tvmaze:
            mp.resolve_metadata({"name": "Some Movie"}, kind="movie")
            tvmaze.assert_not_called()

            tvmaze.return_value = {"genres": ["Drama"], "metadata_source": "tvmaze"}
            result = mp.resolve_metadata({"name": "Some Show"}, kind="series")
            tvmaze.assert_called_once()
            self.assertEqual(result["metadata_source"], "tvmaze")

    def test_anilist_only_consulted_for_anime_kind(self):
        with _env(), \
             patch.object(mp, "tmdb_metadata", return_value=None), \
             patch.object(mp, "omdb_metadata", return_value=None), \
             patch.object(mp, "moviesdatabase_metadata", return_value=None), \
             patch.object(mp, "anilist_metadata") as anilist:
            mp.resolve_metadata({"name": "Some Movie"}, kind="movie")
            anilist.assert_not_called()

            anilist.return_value = {"genres": ["Action"], "metadata_source": "anilist"}
            result = mp.resolve_metadata({"name": "Some Anime"}, kind="anime")
            anilist.assert_called_once()
            self.assertEqual(result["metadata_source"], "anilist")

    def test_a_provider_raising_never_breaks_resolution(self):
        """A bug in one adapter must not take the whole lookup down."""
        def boom(*args, **kwargs):
            raise RuntimeError("provider bug")

        omdb_full = {"genres": ["Action"], "rating": 5.0, "metadata_source": "omdb"}
        with _env(), \
             patch.object(mp, "tmdb_metadata", side_effect=boom), \
             patch.object(mp, "omdb_metadata", return_value=omdb_full), \
             patch.object(mp, "cinemeta_metadata", return_value=None), \
             patch.object(mp, "moviesdatabase_metadata", return_value=None), \
             patch.object(mp, "fanart_metadata", return_value=None):
            result = mp.resolve_metadata({"name": "Some Film"})
        self.assertEqual(result["metadata_source"], "omdb")


if __name__ == "__main__":
    unittest.main()
