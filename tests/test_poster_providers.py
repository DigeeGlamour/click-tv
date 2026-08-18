from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from scanner.poster_providers import (
    anilist_poster_lookup,
    cinemeta_poster_lookup,
    fanart_movie_poster_lookup,
    omdb_poster_lookup,
    supplementary_poster_lookup,
    tvmaze_poster_lookup,
)


class OmdbPosterLookupTests(unittest.TestCase):
    def test_returns_empty_without_an_api_key(self) -> None:
        with patch.dict(os.environ, {"OMDB_API_KEY": ""}):
            self.assertEqual(omdb_poster_lookup("Inception", 2010), "")

    def test_returns_the_poster_field(self) -> None:
        with (
            patch.dict(os.environ, {"OMDB_API_KEY": "test-key"}),
            patch("scanner.poster_providers._get_json", return_value={"Poster": "https://example.test/p.jpg"}),
        ):
            self.assertEqual(omdb_poster_lookup("Inception", 2010), "https://example.test/p.jpg")

    def test_omdbs_own_not_available_marker_is_not_a_poster(self) -> None:
        with (
            patch.dict(os.environ, {"OMDB_API_KEY": "test-key"}),
            patch("scanner.poster_providers._get_json", return_value={"Poster": "N/A"}),
        ):
            self.assertEqual(omdb_poster_lookup("Some Title"), "")


class TvMazePosterLookupTests(unittest.TestCase):
    def test_prefers_the_original_image_over_medium(self) -> None:
        with patch(
            "scanner.poster_providers._get_json",
            return_value={"image": {"medium": "https://example.test/m.jpg", "original": "https://example.test/o.jpg"}},
        ):
            self.assertEqual(tvmaze_poster_lookup("Batman"), "https://example.test/o.jpg")

    def test_a_blank_title_is_never_sent_as_a_query(self) -> None:
        with patch("scanner.poster_providers._get_json") as get_json:
            self.assertEqual(tvmaze_poster_lookup("   "), "")
            get_json.assert_not_called()


class FanartPosterLookupTests(unittest.TestCase):
    """Fanart.tv has no search-by-title endpoint - it is id-only, so it
    contributes only when a tmdb_id already reached this call."""

    def test_returns_empty_without_a_usable_tmdb_id(self) -> None:
        with patch.dict(os.environ, {"FANART_API_KEY": "test-key"}):
            self.assertEqual(fanart_movie_poster_lookup(""), "")
            self.assertEqual(fanart_movie_poster_lookup("not-a-number"), "")

    def test_returns_the_first_movieposter_url(self) -> None:
        with (
            patch.dict(os.environ, {"FANART_API_KEY": "test-key"}),
            patch(
                "scanner.poster_providers._get_json",
                return_value={"movieposter": [{"url": "https://example.test/fanart.jpg"}]},
            ),
        ):
            self.assertEqual(fanart_movie_poster_lookup(27205), "https://example.test/fanart.jpg")


class CinemetaPosterLookupTests(unittest.TestCase):
    """Cinemeta, like Fanart.tv, is id-only - no title search."""

    def test_a_non_imdb_id_is_rejected_outright(self) -> None:
        with patch("scanner.poster_providers._get_json") as get_json:
            self.assertEqual(cinemeta_poster_lookup("27205"), "")
            get_json.assert_not_called()

    def test_returns_the_meta_poster(self) -> None:
        with patch(
            "scanner.poster_providers._get_json",
            return_value={"meta": {"poster": "https://example.test/cinemeta.jpg"}},
        ):
            self.assertEqual(cinemeta_poster_lookup("tt0137523"), "https://example.test/cinemeta.jpg")


class AniListPosterLookupTests(unittest.TestCase):
    def test_returns_the_largest_available_cover(self) -> None:
        with patch(
            "scanner.poster_providers._post_json",
            return_value={"data": {"Media": {"coverImage": {"extraLarge": "https://example.test/xl.jpg", "large": "https://example.test/l.jpg"}}}},
        ):
            self.assertEqual(anilist_poster_lookup("Naruto"), "https://example.test/xl.jpg")

    def test_no_match_is_empty_not_an_error(self) -> None:
        with patch("scanner.poster_providers._post_json", return_value={"data": {"Media": None}}):
            self.assertEqual(anilist_poster_lookup("Totally Unknown Title 12345"), "")


class SupplementaryChainTests(unittest.TestCase):
    """First non-empty result wins, tried in the documented order - id-based
    providers before the title-only ones, AniList last since a title-only
    match against an anime-only catalogue can coincidentally hit an
    unrelated same-named title."""

    def test_an_id_based_provider_short_circuits_the_title_based_ones(self) -> None:
        with (
            patch("scanner.poster_providers.fanart_movie_poster_lookup", return_value="https://example.test/fanart.jpg"),
            patch("scanner.poster_providers.omdb_poster_lookup") as omdb,
        ):
            result = supplementary_poster_lookup("Inception", 2010, tmdb_id=27205)
        self.assertEqual(result, "https://example.test/fanart.jpg")
        omdb.assert_not_called()

    def test_falls_all_the_way_to_anilist_when_nothing_else_matches(self) -> None:
        with (
            patch("scanner.poster_providers.fanart_movie_poster_lookup", return_value=""),
            patch("scanner.poster_providers.cinemeta_poster_lookup", return_value=""),
            patch("scanner.poster_providers.omdb_poster_lookup", return_value=""),
            patch("scanner.poster_providers.tvmaze_poster_lookup", return_value=""),
            patch("scanner.poster_providers.anilist_poster_lookup", return_value="https://example.test/ani.jpg"),
        ):
            result = supplementary_poster_lookup("Naruto")
        self.assertEqual(result, "https://example.test/ani.jpg")

    def test_a_provider_raising_never_breaks_the_chain(self) -> None:
        with (
            patch("scanner.poster_providers.fanart_movie_poster_lookup", side_effect=RuntimeError("boom")),
            patch("scanner.poster_providers.cinemeta_poster_lookup", return_value=""),
            patch("scanner.poster_providers.omdb_poster_lookup", return_value="https://example.test/omdb.jpg"),
        ):
            result = supplementary_poster_lookup("Inception", 2010, tmdb_id=27205)
        self.assertEqual(result, "https://example.test/omdb.jpg")

    def test_nothing_found_anywhere_is_an_empty_string_not_none(self) -> None:
        with (
            patch("scanner.poster_providers.fanart_movie_poster_lookup", return_value=""),
            patch("scanner.poster_providers.cinemeta_poster_lookup", return_value=""),
            patch("scanner.poster_providers.omdb_poster_lookup", return_value=""),
            patch("scanner.poster_providers.tvmaze_poster_lookup", return_value=""),
            patch("scanner.poster_providers.anilist_poster_lookup", return_value=""),
        ):
            result = supplementary_poster_lookup("Some Totally Unknown Title 12345")
        self.assertEqual(result, "")


if __name__ == "__main__":
    unittest.main()
