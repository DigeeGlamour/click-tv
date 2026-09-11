"""PART 06: real international trending.

The claim being made on the site is "this is trending internationally", and
every test here defends one part of that claim being true:

  - The signal is external (TMDB's weekly ranking), not our own server
    count. `available_link_count` is held to a tie-breaker worth hundredths
    of a point, and there is a test that a title with five servers cannot
    overtake one TMDB ranked above it.
  - Movie and TV are ranked separately, because they are ranked separately
    at the source.
  - Only titles Click TV can actually play are shown. A trending row that
    cannot be clicked is worse than a short one.
  - The rank is TMDB's rank. We renumber only to close the gaps left by
    titles we cannot play; we never re-sort.
  - An outage never empties the row, and week-old data stops calling itself
    international rather than being dressed up as current.
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

from scanner import movie_trending as mt  # noqa: E402


def _catalog(*movies):
    return {"Mix": {"index": {}, "page_contents": {"page-001.json": {"items": list(movies)}}}}


def _snapshot(movies=(), tv=(), fetched_at="2026-09-11T00:00:00+00:00"):
    return {
        "fetched_at": fetched_at,
        "sources": ["tmdb"],
        "movies": [dict(entry, kind="movie") for entry in movies],
        "tv": [dict(entry, kind="tv") for entry in tv],
    }


def _entry(rank, tmdb_id=None, title="Some Film", year="2026"):
    return {"external_rank": rank, "tmdb_id": tmdb_id, "title": title, "year": year}


class MatchingTests(unittest.TestCase):
    def test_an_external_hit_with_no_stream_here_is_not_shown(self):
        index = mt.build_catalog_index(_catalog({"id": "film-a", "tmdb_id": 111, "name": "Have It"}))
        matched = mt.match_entries(
            [_entry(1, tmdb_id=111, title="Have It"), _entry(2, tmdb_id=999, title="Do Not Have It")],
            index,
        )
        self.assertEqual([item["id"] for item in matched], ["film-a"])

    def test_matching_works_on_tmdb_id(self):
        index = mt.build_catalog_index(_catalog({"id": "film-a", "tmdb_id": 27205, "name": "Whatever"}))
        matched = mt.match_entries([_entry(1, tmdb_id=27205, title="Inception")], index)
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0]["tmdb_id"], 27205)

    def test_matching_falls_back_to_title_and_year(self):
        index = mt.build_catalog_index(_catalog({"id": "film-a", "name": "Some Film", "year": 2026}))
        matched = mt.match_entries([_entry(1, title="Some Film", year="2026")], index)
        self.assertEqual(len(matched), 1)

    def test_a_title_with_no_year_is_never_matched_on_title_alone(self):
        """Two films share a title far more often than a remake trends."""
        index = mt.build_catalog_index(_catalog({"id": "film-a", "name": "Some Film"}))
        matched = mt.match_entries([_entry(1, title="Some Film", year="")], index)
        self.assertEqual(matched, [])

    def test_a_year_mismatch_does_not_match_a_remake(self):
        index = mt.build_catalog_index(_catalog({"id": "old-one", "name": "Some Film", "year": 1998}))
        matched = mt.match_entries([_entry(1, title="Some Film", year="2026")], index)
        self.assertEqual(matched, [])

    def test_one_movie_cannot_occupy_two_trending_slots(self):
        index = mt.build_catalog_index(
            _catalog({"id": "film-a", "tmdb_id": 111, "name": "Some Film", "year": 2026})
        )
        matched = mt.match_entries(
            [_entry(1, tmdb_id=111, title="Some Film"), _entry(2, title="Some Film", year="2026")],
            index,
        )
        self.assertEqual(len(matched), 1)


class RankAndScoreTests(unittest.TestCase):
    def test_the_external_rank_order_is_preserved(self):
        index = mt.build_catalog_index(
            _catalog(
                {"id": "third", "tmdb_id": 3},
                {"id": "first", "tmdb_id": 1},
                {"id": "second", "tmdb_id": 2},
            )
        )
        matched = mt.match_entries(
            [_entry(1, tmdb_id=1), _entry(2, tmdb_id=2), _entry(3, tmdb_id=3)], index
        )
        self.assertEqual([item["id"] for item in matched], ["first", "second", "third"])
        self.assertEqual([item["trending_rank"] for item in matched], [1, 2, 3])

    def test_ranks_close_up_around_titles_we_cannot_play(self):
        index = mt.build_catalog_index(_catalog({"id": "film-a", "tmdb_id": 5}))
        matched = mt.match_entries(
            [_entry(1, tmdb_id=1), _entry(2, tmdb_id=2), _entry(3, tmdb_id=5)], index
        )
        self.assertEqual(matched[0]["trending_rank"], 1, "renumbered to close the gap")
        self.assertEqual(matched[0]["external_rank"], 3, "but TMDB's own rank is kept")

    def test_available_link_count_can_never_outrank_a_real_trend_position(self):
        """The specific thing this PART exists to stop."""
        index = mt.build_catalog_index(
            _catalog(
                {"id": "one-server-hit", "tmdb_id": 1, "available_link_count": 1},
                {"id": "five-server-old", "tmdb_id": 2, "available_link_count": 5},
            )
        )
        matched = mt.match_entries([_entry(1, tmdb_id=1), _entry(2, tmdb_id=2)], index)
        self.assertEqual(matched[0]["id"], "one-server-hit")
        self.assertGreater(
            matched[0]["trend_score"],
            matched[1]["trend_score"],
            "a better TMDB rank must always win regardless of server count",
        )

    def test_link_count_only_breaks_a_tie_worth_hundredths(self):
        index = mt.build_catalog_index(_catalog({"id": "film-a", "tmdb_id": 1, "available_link_count": 5}))
        matched = mt.match_entries([_entry(1, tmdb_id=1)], index)
        bonus = matched[0]["trend_score"] - 100.0
        self.assertGreater(bonus, 0)
        self.assertLessEqual(bonus, 0.05)

    def test_a_missing_or_junk_link_count_never_raises(self):
        index = mt.build_catalog_index(_catalog({"id": "film-a", "tmdb_id": 1, "available_link_count": "lots"}))
        matched = mt.match_entries([_entry(1, tmdb_id=1)], index)
        self.assertEqual(len(matched), 1)


class SeparationTests(unittest.TestCase):
    def test_movie_and_tv_trending_are_fetched_and_kept_separately(self):
        calls = []

        def fake_request(provider, url, headers=None, body=None, timeout=None, path=None):
            calls.append(url)
            if "/trending/movie/week" in url:
                return {"results": [{"id": 1, "title": "A Film", "release_date": "2026-01-01"}]}
            if "/trending/tv/week" in url:
                return {"results": [{"id": 2, "name": "A Show", "first_air_date": "2026-01-01"}]}
            return {}

        with patch.dict("os.environ", {"TMDB_API_KEY": "k"}, clear=False), patch.object(
            mt.provider_health, "request_json", side_effect=fake_request
        ):
            snapshot = mt.fetch_external()

        self.assertTrue(any("/trending/movie/week" in url for url in calls))
        self.assertTrue(any("/trending/tv/week" in url for url in calls))
        self.assertEqual(len(snapshot["movies"]), 1)
        self.assertEqual(len(snapshot["tv"]), 1)
        self.assertEqual(snapshot["movies"][0]["kind"], "movie")
        self.assertEqual(snapshot["tv"][0]["kind"], "tv")

    def test_the_document_keeps_movies_and_tv_in_separate_lists(self):
        index = mt.build_catalog_index(_catalog({"id": "film-a", "tmdb_id": 1}, {"id": "show-a", "tmdb_id": 2}))
        document = mt.build_document(
            _snapshot(movies=[_entry(1, tmdb_id=1)], tv=[_entry(1, tmdb_id=2)]),
            index,
            now=dt.datetime(2026, 9, 11, 1, tzinfo=dt.timezone.utc),
        )
        self.assertEqual([item["id"] for item in document["movies"]], ["film-a"])
        self.assertEqual([item["id"] for item in document["tv"]], ["show-a"])

    def test_no_tmdb_credential_means_no_request_and_no_snapshot(self):
        with patch.dict("os.environ", {"TMDB_API_KEY": "", "TMDB_API_TOKEN": ""}, clear=False):
            with patch.object(mt.provider_health, "request_json") as request:
                self.assertIsNone(mt.fetch_external())
                request.assert_not_called()


class FreshnessTests(unittest.TestCase):
    def setUp(self):
        self.now = dt.datetime(2026, 9, 11, 12, tzinfo=dt.timezone.utc)

    def _at(self, hours_ago):
        return (self.now - dt.timedelta(hours=hours_ago)).isoformat()

    def test_under_72_hours_is_fresh(self):
        state, _ = mt.freshness_for({"fetched_at": self._at(10)}, self.now)
        self.assertEqual(state, "fresh")

    def test_three_to_seven_days_is_usable_but_flagged_stale(self):
        state, _ = mt.freshness_for({"fetched_at": self._at(96)}, self.now)
        self.assertEqual(state, "stale")

    def test_past_a_week_is_expired(self):
        state, _ = mt.freshness_for({"fetched_at": self._at(24 * 8)}, self.now)
        self.assertEqual(state, "expired")

    def test_an_expired_snapshot_drops_the_international_claim_and_shortens(self):
        entries = [_entry(rank, tmdb_id=rank) for rank in range(1, 21)]
        index = mt.build_catalog_index(_catalog(*[{"id": f"film-{n}", "tmdb_id": n} for n in range(1, 21)]))
        document = mt.build_document(
            _snapshot(movies=entries, fetched_at=self._at(24 * 9)), index, now=self.now
        )
        self.assertEqual(document["freshness"], "expired")
        self.assertFalse(document["international_claim"])
        self.assertFalse(document["trend_external_fresh"])
        self.assertEqual(len(document["movies"]), mt.EXPIRED_LIST_LIMIT)

    def test_a_stale_snapshot_is_still_published_but_not_marked_fresh(self):
        index = mt.build_catalog_index(_catalog({"id": "film-a", "tmdb_id": 1}))
        document = mt.build_document(
            _snapshot(movies=[_entry(1, tmdb_id=1)], fetched_at=self._at(96)), index, now=self.now
        )
        self.assertEqual(document["freshness"], "stale")
        self.assertFalse(document["trend_external_fresh"])
        self.assertTrue(document["international_claim"])
        self.assertEqual(len(document["movies"]), 1)


class GenerateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state_path = str(Path(self._tmp.name) / "movie-trending-cache.json")
        self.output_path = str(Path(self._tmp.name) / "discovery" / "trending.json")
        self.now = dt.datetime(2026, 9, 11, 12, tzinfo=dt.timezone.utc)
        self.catalog = _catalog({"id": "film-a", "tmdb_id": 1, "available_link_count": 2})

    def _generate(self, fetch):
        return mt.generate(
            self.catalog,
            state_path=self.state_path,
            output_path=self.output_path,
            now=self.now,
            fetch=fetch,
        )

    def _output(self):
        return json.loads(Path(self.output_path).read_text(encoding="utf-8"))

    def test_a_successful_run_writes_trending_json_and_saves_last_good(self):
        summary = self._generate(lambda now: _snapshot(movies=[_entry(1, tmdb_id=1)], fetched_at=self.now.isoformat()))
        self.assertEqual(summary["matched"], 1)
        self.assertTrue(summary["fresh_fetch"])

        document = self._output()
        self.assertEqual(document["movies"][0]["id"], "film-a")
        self.assertEqual(document["freshness"], "fresh")
        self.assertTrue(document["trend_external_fresh"])

        state = mt.load_state(self.state_path)
        self.assertTrue(state.get("last_good_tmdb_at"))
        self.assertEqual(len(state["snapshot"]["movies"]), 1)

    def test_a_failed_fetch_falls_back_to_the_last_good_snapshot(self):
        self._generate(lambda now: _snapshot(movies=[_entry(1, tmdb_id=1)], fetched_at=self.now.isoformat()))
        summary = self._generate(lambda now: None)
        self.assertEqual(summary["matched"], 1, "the last-good snapshot still matched")
        self.assertFalse(summary["fresh_fetch"])
        self.assertEqual(self._output()["movies"][0]["id"], "film-a")

    def test_a_failed_fetch_never_overwrites_good_trending_json_with_nothing(self):
        """The plan's NEVER rule, at the trending output."""
        self._generate(lambda now: _snapshot(movies=[_entry(1, tmdb_id=1)], fetched_at=self.now.isoformat()))
        before = self._output()

        # Catalogue now matches nothing AND the fetch fails.
        summary = mt.generate(
            _catalog({"id": "unrelated", "tmdb_id": 424242}),
            state_path=self.state_path,
            output_path=self.output_path,
            now=self.now,
            fetch=lambda now: None,
        )
        self.assertIn("kept last-good", summary["skipped"])
        self.assertEqual(self._output(), before, "the good document must be untouched")

    def test_a_raising_fetch_is_treated_as_a_failure_not_a_crash(self):
        def boom(now):
            raise RuntimeError("tmdb down")

        summary = self._generate(boom)
        self.assertIn("skipped", summary)

    def test_with_no_snapshot_at_all_nothing_is_written(self):
        summary = self._generate(lambda now: None)
        self.assertIn("skipped", summary)
        self.assertFalse(Path(self.output_path).exists())

    def test_a_fresh_fetch_that_matches_nothing_is_written_honestly(self):
        summary = mt.generate(
            _catalog({"id": "unrelated", "tmdb_id": 424242}),
            state_path=self.state_path,
            output_path=self.output_path,
            now=self.now,
            fetch=lambda now: _snapshot(movies=[_entry(1, tmdb_id=1)], fetched_at=self.now.isoformat()),
        )
        self.assertEqual(summary["matched"], 0)
        self.assertEqual(self._output()["movies"], [], "an empty honest answer, not a preserved stale one")

    def test_the_output_is_lightweight_ids_and_ranks_not_whole_cards(self):
        self._generate(lambda now: _snapshot(movies=[_entry(1, tmdb_id=1)], fetched_at=self.now.isoformat()))
        item = self._output()["movies"][0]
        self.assertEqual(
            sorted(item),
            ["external_rank", "id", "is_trending", "name", "tmdb_id", "trend_score", "trend_sources", "trending_rank"],
        )
        serialized = json.dumps(self._output())
        for leaked in ("url", "backups", "headers", "playback_id"):
            self.assertNotIn(leaked, serialized)

    def test_no_temp_files_are_left_behind(self):
        self._generate(lambda now: _snapshot(movies=[_entry(1, tmdb_id=1)], fetched_at=self.now.isoformat()))
        self.assertEqual(list(Path(self.output_path).parent.glob(".*.tmp")), [])

    def test_a_corrupt_state_file_does_not_crash_the_run(self):
        Path(self.state_path).write_text("{not json", encoding="utf-8")
        summary = self._generate(lambda now: _snapshot(movies=[_entry(1, tmdb_id=1)], fetched_at=self.now.isoformat()))
        self.assertEqual(summary["matched"], 1)


class UnverifiedSourceTests(unittest.TestCase):
    def test_moviesdatabase_popularity_stays_disabled_until_verified(self):
        """Verified 2026-09-11: the popularity list answers HTTP 200 with
        entries: 0. An empty list is not a ranking, so it is not wired in."""
        self.assertFalse(mt.MOVIESDATABASE_POPULARITY_VERIFIED)
        source = Path(ROOT / "scanner" / "movie_trending.py").read_text(encoding="utf-8")
        self.assertNotIn("most_pop_movies", source.split('"""', 2)[2] if source.count('"""') > 1 else source)

    def test_trend_sources_only_ever_claims_what_actually_contributed(self):
        index = mt.build_catalog_index(_catalog({"id": "film-a", "tmdb_id": 1}))
        document = mt.build_document(
            _snapshot(movies=[_entry(1, tmdb_id=1)]),
            index,
            now=dt.datetime(2026, 9, 11, 1, tzinfo=dt.timezone.utc),
        )
        self.assertEqual(document["trend_sources"], ["tmdb"])
        self.assertEqual(document["movies"][0]["trend_sources"], ["tmdb"])


if __name__ == "__main__":
    unittest.main()
