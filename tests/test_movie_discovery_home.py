"""PART 09: the movie home as one small static file.

home.json exists so opening the movie home costs a few kilobytes instead of
the catalogue. Three properties make that true and are tested here: the
rows are capped, the cards carry no playback configuration, and the whole
thing is static - the browser never calls TMDB, OMDb, Fanart or RapidAPI,
because the scanner already did.

The Featured row deserves its own note. The Featured/Hero system is a
separate plan that is NOT built yet, so nothing fills that row. Not the top
trending title, not the film with the most servers, not a random pick -
each of those is a fabricated editorial choice dressed up as a real one.
The row stays empty, says why in `featured_status`, and carries forward
whatever a real Featured builder writes there later.
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

from scanner import movie_discovery as md  # noqa: E402

NOW = dt.datetime(2026, 9, 11, 12, tzinfo=dt.timezone.utc)


def _ago(days):
    return (NOW - dt.timedelta(days=days)).isoformat()


def _movie(movie_id, **fields):
    base = {
        "id": movie_id,
        "name": f"Film {movie_id}",
        "category": "Mix",
        "logo": f"https://x.test/{movie_id}.jpg",
        "url": "https://secret.test/stream.mkv",
        "backups": [{"url": "https://secret.test/backup.mkv"}],
        "headers": {"Referer": "https://secret.test/"},
        "playback_id": "ctv_deadbeef",
        "proxy_mode": "direct_first",
    }
    base.update(fields)
    return base


def _catalog(*movies):
    return {"Mix": {"index": {}, "page_contents": {"page-001.json": {"items": list(movies)}}}}


class HomeShapeTests(unittest.TestCase):
    def test_every_row_is_present_even_when_empty(self):
        document = md.build_home(_catalog(), now=NOW)
        for row in ("featured", "trending", "just_added", "latest"):
            self.assertIn(row, document)
            self.assertIsInstance(document[row], list)
        self.assertEqual(document["counts"]["featured"], 0)

    def test_rows_are_capped_between_ten_and_fifteen(self):
        self.assertGreaterEqual(md.HOME_ROW_LIMIT, 10)
        self.assertLessEqual(md.HOME_ROW_LIMIT, 15)

        many = [_movie(f"film-{n:03d}", first_seen_at=_ago(1), release_date="2026-01-01") for n in range(100)]
        document = md.build_home(_catalog(*many), now=NOW)
        self.assertEqual(len(document["just_added"]), md.HOME_ROW_LIMIT)
        self.assertEqual(len(document["latest"]), md.HOME_ROW_LIMIT)

    def test_the_home_is_not_the_catalogue(self):
        many = [_movie(f"film-{n:03d}", first_seen_at=_ago(1), release_date="2026-01-01") for n in range(500)]
        document = md.build_home(_catalog(*many), now=NOW)
        listed = {item["id"] for row in ("trending", "just_added", "latest") for item in document[row]}
        self.assertLess(len(listed), 50, "a home row is a shelf, not a catalogue")
        self.assertLess(len(json.dumps(document)), 60_000, "home payload must stay small")

    def test_cards_carry_metadata_and_never_playback_configuration(self):
        movie = _movie(
            "film-a",
            year=2026,
            release_date="2026-09-01",
            first_seen_at=_ago(1),
            backdrop="https://x.test/back.jpg",
            rating=7.5,
            rating_source="IMDb",
            genres=["Action", "Crime"],
        )
        document = md.build_home(_catalog(movie), now=NOW)
        card = document["just_added"][0]
        self.assertEqual(card["poster"], "https://x.test/film-a.jpg")
        self.assertEqual(card["rating_source"], "IMDb")
        self.assertEqual(card["genres"], ["Action", "Crime"])
        self.assertEqual(card["category"], "Mix")

        serialized = json.dumps(document)
        for forbidden in md.FORBIDDEN_CARD_FIELDS:
            self.assertNotIn(f'"{forbidden}"', serialized, f"{forbidden} leaked into home.json")
        self.assertNotIn("secret.test", serialized)

    def test_a_click_has_everything_it_needs_to_resolve_the_real_record(self):
        """The card carries the id; the existing category pages carry the
        stream. That is the handoff, and it keeps playback in one place."""
        document = md.build_home(
            _catalog(_movie("film-a", first_seen_at=_ago(1))), now=NOW
        )
        self.assertEqual(document["just_added"][0]["id"], "film-a")


class TrendingRowTests(unittest.TestCase):
    def test_trending_ids_are_resolved_into_cards_with_rank_preserved(self):
        catalog = _catalog(
            _movie("film-a", genres=["Action"]),
            _movie("film-b", genres=["Crime"]),
        )
        trending = {
            "movies": [
                {"id": "film-b", "trending_rank": 1, "trend_sources": ["tmdb"]},
                {"id": "film-a", "trending_rank": 2, "trend_sources": ["tmdb"]},
            ]
        }
        document = md.build_home(catalog, trending_document=trending, now=NOW)
        self.assertEqual([item["id"] for item in document["trending"]], ["film-b", "film-a"])
        self.assertEqual(document["trending"][0]["trending_rank"], 1)
        self.assertEqual(document["trending"][0]["genres"], ["Crime"])

    def test_a_trending_id_no_longer_in_the_catalogue_is_dropped(self):
        trending = {"movies": [{"id": "gone", "trending_rank": 1}]}
        document = md.build_home(_catalog(_movie("film-a")), trending_document=trending, now=NOW)
        self.assertEqual(document["trending"], [])

    def test_a_missing_trending_file_leaves_an_empty_row_not_a_crash(self):
        document = md.build_home(_catalog(_movie("film-a")), trending_document={}, now=NOW)
        self.assertEqual(document["trending"], [])


class FeaturedTests(unittest.TestCase):
    def test_featured_is_empty_and_says_why_when_no_featured_system_exists(self):
        document = md.build_home(
            _catalog(_movie("film-a", first_seen_at=_ago(1))), now=NOW
        )
        self.assertEqual(document["featured"], [])
        self.assertEqual(document["featured_status"], "awaiting_featured_system")

    def test_featured_is_never_filled_from_trending_or_server_count(self):
        """The three tempting fakes, all refused."""
        catalog = _catalog(
            _movie("most-servers", available_link_count=9, first_seen_at=_ago(1)),
            _movie("top-trending", first_seen_at=_ago(2)),
        )
        trending = {"movies": [{"id": "top-trending", "trending_rank": 1}]}
        document = md.build_home(catalog, trending_document=trending, now=NOW)
        self.assertEqual(document["featured"], [])
        self.assertNotIn("most-servers", json.dumps(document["featured"]))

    def test_a_real_featured_entry_written_by_a_future_system_is_carried_forward(self):
        existing = {"featured": [{"id": "film-a", "name": "Film film-a", "poster": "https://x.test/a.jpg"}]}
        document = md.build_home(
            _catalog(_movie("film-a")), existing_home=existing, now=NOW
        )
        self.assertEqual([item["id"] for item in document["featured"]], ["film-a"])
        self.assertEqual(document["featured_status"], "carried_last_good")

    def test_a_carried_featured_entry_whose_film_is_gone_is_dropped(self):
        existing = {"featured": [{"id": "removed-film"}]}
        document = md.build_home(_catalog(_movie("film-a")), existing_home=existing, now=NOW)
        self.assertEqual(document["featured"], [], "a Featured slot must never point at nothing")

    def test_a_junk_featured_block_does_not_crash_the_build(self):
        for junk in ({"featured": "not-a-list"}, {"featured": [None, 5, "x"]}, {}):
            document = md.build_home(_catalog(_movie("film-a")), existing_home=junk, now=NOW)
            self.assertEqual(document["featured"], [])


class GenerateHomeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home_path = str(Path(self._tmp.name) / "discovery" / "home.json")
        self.trending_path = str(Path(self._tmp.name) / "discovery" / "trending.json")

    def test_generate_writes_atomically_and_reads_the_trending_file(self):
        Path(self.trending_path).parent.mkdir(parents=True, exist_ok=True)
        Path(self.trending_path).write_text(
            json.dumps({"movies": [{"id": "film-a", "trending_rank": 1}]}), encoding="utf-8"
        )
        summary = md.generate_home(
            _catalog(_movie("film-a", first_seen_at=_ago(1))),
            output_path=self.home_path,
            trending_path=self.trending_path,
            now=NOW,
        )
        self.assertEqual(summary["counts"]["trending"], 1)
        on_disk = json.loads(Path(self.home_path).read_text(encoding="utf-8"))
        self.assertEqual(on_disk["trending"][0]["id"], "film-a")
        self.assertEqual(list(Path(self.home_path).parent.glob(".*.tmp")), [])

    def test_featured_survives_a_rebuild(self):
        catalog = _catalog(_movie("film-a", first_seen_at=_ago(1)))
        md.generate_home(catalog, output_path=self.home_path, trending_path=self.trending_path, now=NOW)

        # A future Featured builder writes into the file...
        document = json.loads(Path(self.home_path).read_text(encoding="utf-8"))
        document["featured"] = [{"id": "film-a", "name": "Film film-a"}]
        Path(self.home_path).write_text(json.dumps(document), encoding="utf-8")

        # ...and the next scan must not wipe it.
        md.generate_home(catalog, output_path=self.home_path, trending_path=self.trending_path, now=NOW)
        rebuilt = json.loads(Path(self.home_path).read_text(encoding="utf-8"))
        self.assertEqual([item["id"] for item in rebuilt["featured"]], ["film-a"])

    def test_a_corrupt_existing_home_is_rebuilt_rather_than_fatal(self):
        Path(self.home_path).parent.mkdir(parents=True, exist_ok=True)
        Path(self.home_path).write_text("{not json", encoding="utf-8")
        summary = md.generate_home(
            _catalog(_movie("film-a", first_seen_at=_ago(1))),
            output_path=self.home_path,
            trending_path=self.trending_path,
            now=NOW,
        )
        self.assertEqual(summary["counts"]["just_added"], 1)

    def test_building_the_home_needs_no_external_api(self):
        with patch("scanner.provider_health.request_json") as request:
            md.build_home(_catalog(_movie("film-a", first_seen_at=_ago(1))), now=NOW)
            request.assert_not_called()


class NoBrowserSideApiTests(unittest.TestCase):
    """The live site must never hold a key or call a metadata provider."""

    PROVIDER_HOSTS = (
        "api.themoviedb.org",
        "omdbapi.com",
        "webservice.fanart.tv",
        "rapidapi.com",
        "v3-cinemeta.strem.io",
        "api.tvmaze.com",
        "graphql.anilist.co",
    )
    SECRET_NAMES = ("TMDB_API_KEY", "TMDB_API_TOKEN", "OMDB_API_KEY", "FANART_API_KEY", "RAPIDAPI_KEY")

    def _frontend_files(self):
        for folder in ("site", "dist"):
            base = ROOT / folder
            if not base.exists():
                continue
            for pattern in ("**/*.js", "**/*.html"):
                yield from base.glob(pattern)

    def test_no_frontend_file_calls_a_metadata_provider(self):
        offenders = []
        for path in self._frontend_files():
            text = path.read_text(encoding="utf-8", errors="replace")
            for host in self.PROVIDER_HOSTS:
                if host in text:
                    offenders.append(f"{path.relative_to(ROOT)} -> {host}")
        self.assertEqual(offenders, [], "the browser must never call a metadata API")

    def test_no_frontend_file_carries_a_provider_secret_name(self):
        offenders = []
        for path in self._frontend_files():
            text = path.read_text(encoding="utf-8", errors="replace")
            for secret in self.SECRET_NAMES:
                if secret in text:
                    offenders.append(f"{path.relative_to(ROOT)} -> {secret}")
        self.assertEqual(offenders, [])

    def test_no_discovery_file_carries_a_provider_secret(self):
        discovery = ROOT / "data" / "movies" / "discovery"
        for path in discovery.glob("*.json") if discovery.exists() else []:
            text = path.read_text(encoding="utf-8", errors="replace")
            for secret in self.SECRET_NAMES:
                self.assertNotIn(secret, text)
            self.assertNotIn("api_key", text)


if __name__ == "__main__":
    unittest.main()
