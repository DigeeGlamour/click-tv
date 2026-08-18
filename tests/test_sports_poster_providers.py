from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from scanner.sports_poster_providers import (
    highlightly_match_artwork,
    sportmonks_team_badge,
    thesportsdb_best_poster,
    thesportsdb_event_artwork,
    thesportsdb_team_badge,
    supplementary_sports_poster_lookup,
)


class TheSportsDbTests(unittest.TestCase):
    def test_missing_either_side_is_never_looked_up(self):
        with patch("scanner.sports_poster_providers._get_json") as get_json:
            self.assertEqual(thesportsdb_event_artwork("Arsenal", ""), {})
        get_json.assert_not_called()

    def test_reads_poster_thumbnail_banner_and_both_badges_from_one_call(self):
        with patch(
            "scanner.sports_poster_providers._get_json",
            return_value={"event": [{
                "strPoster": "https://example.test/poster.jpg",
                "strThumb": "https://example.test/thumb.jpg",
                "strHomeTeamBadge": "https://example.test/home.png",
                "strAwayTeamBadge": "https://example.test/away.png",
                "strLeagueBadge": "https://example.test/league.png",
            }]},
        ):
            artwork = thesportsdb_event_artwork("Arsenal", "Chelsea")
        self.assertEqual(artwork["poster"], "https://example.test/poster.jpg")
        self.assertEqual(artwork["home_badge"], "https://example.test/home.png")
        self.assertNotIn("banner", artwork)  # absent field is omitted, not ""

    def test_priority_is_poster_then_thumbnail_then_banner_then_badges(self):
        with patch(
            "scanner.sports_poster_providers._get_json",
            return_value={"event": [{
                "strThumb": "https://example.test/thumb.jpg",
                "strHomeTeamBadge": "https://example.test/home.png",
            }]},
        ):
            self.assertEqual(thesportsdb_best_poster("Arsenal", "Chelsea"), "https://example.test/thumb.jpg")

    def test_no_event_found_is_an_empty_dict(self):
        with patch("scanner.sports_poster_providers._get_json", return_value={"event": None}):
            self.assertEqual(thesportsdb_event_artwork("Arsenal", "Chelsea"), {})

    def test_team_badge_prefers_strbadge_over_strlogo(self):
        with patch(
            "scanner.sports_poster_providers._get_json",
            return_value={"teams": [{"strBadge": "https://example.test/badge.png", "strLogo": "https://example.test/logo.png"}]},
        ):
            self.assertEqual(thesportsdb_team_badge("Arsenal"), "https://example.test/badge.png")


class HighlightlyTests(unittest.TestCase):
    def test_returns_empty_without_an_api_key(self):
        with patch.dict(os.environ, {"HIGHLIGHTLY_API_KEY": ""}):
            self.assertEqual(highlightly_match_artwork("Arsenal", "Chelsea", "football", "2026-08-18"), {})

    def test_returns_empty_without_a_date_since_the_endpoint_requires_one(self):
        with patch.dict(os.environ, {"HIGHLIGHTLY_API_KEY": "test-key"}):
            self.assertEqual(highlightly_match_artwork("Arsenal", "Chelsea", "football", ""), {})

    def test_matches_by_team_name_regardless_of_case(self):
        with (
            patch.dict(os.environ, {"HIGHLIGHTLY_API_KEY": "test-key"}),
            patch(
                "scanner.sports_poster_providers._get_json",
                return_value={"data": [{
                    "homeTeam": {"name": "ARSENAL", "logo": "https://example.test/home.png"},
                    "awayTeam": {"name": "chelsea", "logo": "https://example.test/away.png"},
                    "league": {"logo": "https://example.test/league.png"},
                }]},
            ),
        ):
            artwork = highlightly_match_artwork("Arsenal", "Chelsea", "football", "2026-08-18")
        self.assertEqual(artwork["home_badge"], "https://example.test/home.png")

    def test_a_match_for_different_teams_is_not_returned(self):
        with (
            patch.dict(os.environ, {"HIGHLIGHTLY_API_KEY": "test-key"}),
            patch(
                "scanner.sports_poster_providers._get_json",
                return_value={"data": [{
                    "homeTeam": {"name": "Liverpool", "logo": "https://example.test/x.png"},
                    "awayTeam": {"name": "Everton", "logo": "https://example.test/y.png"},
                }]},
            ),
        ):
            self.assertEqual(highlightly_match_artwork("Arsenal", "Chelsea", "football", "2026-08-18"), {})

    def test_an_unsupported_sport_has_no_base_url_and_is_skipped(self):
        with patch.dict(os.environ, {"HIGHLIGHTLY_API_KEY": "test-key"}):
            with patch("scanner.sports_poster_providers._get_json") as get_json:
                self.assertEqual(highlightly_match_artwork("A", "B", "badminton", "2026-08-18"), {})
            get_json.assert_not_called()


class SportmonksTests(unittest.TestCase):
    """Authenticates correctly with the key handed over, but the free plan
    covers only two leagues - confirmed live, a search for a team outside
    those returned nothing, so this contributes for almost nothing Click TV
    actually carries in practice."""

    def test_returns_empty_without_an_api_token(self):
        with patch.dict(os.environ, {"SPORTMONKS_API_TOKEN": ""}):
            self.assertEqual(sportmonks_team_badge("Arsenal"), "")

    def test_returns_the_first_teams_image_path(self):
        with (
            patch.dict(os.environ, {"SPORTMONKS_API_TOKEN": "test-token"}),
            patch("scanner.sports_poster_providers._get_json", return_value={"data": [{"image_path": "https://example.test/badge.png"}]}),
        ):
            self.assertEqual(sportmonks_team_badge("FC Copenhagen"), "https://example.test/badge.png")


class SupplementarySportsChainTests(unittest.TestCase):
    def test_thesportsdb_short_circuits_highlightly_and_sportmonks(self):
        with (
            patch("scanner.sports_poster_providers.thesportsdb_best_poster", return_value="https://example.test/tsdb.jpg"),
            patch("scanner.sports_poster_providers.highlightly_match_artwork") as highlightly,
        ):
            result = supplementary_sports_poster_lookup("Arsenal", "Chelsea")
        self.assertEqual(result, "https://example.test/tsdb.jpg")
        highlightly.assert_not_called()

    def test_a_provider_raising_never_breaks_the_chain(self):
        with (
            patch("scanner.sports_poster_providers.thesportsdb_best_poster", side_effect=RuntimeError("boom")),
            patch("scanner.sports_poster_providers.highlightly_match_artwork", return_value={"home_badge": "https://example.test/hl.png"}),
        ):
            result = supplementary_sports_poster_lookup("Arsenal", "Chelsea")
        self.assertEqual(result, "https://example.test/hl.png")


if __name__ == "__main__":
    unittest.main()
