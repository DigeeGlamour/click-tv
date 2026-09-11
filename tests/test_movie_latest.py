"""PART 08: Latest, from real release dates only.

"Latest" answers when a film was RELEASED. That is the whole distinction
from Just Added, which answers when it arrived here, and the two are
deliberately built from different fields so they can disagree - because in
reality they usually do:

    released 2026-09-01, arrived 2026-09-08  ->  Latest YES, Just Added YES
    released 2015-04-20, arrived 2026-09-09  ->  Latest NO,  Just Added YES

Four things are explicitly forbidden from influencing this row, and each
has a test: first_seen_at, available_link_count, trend rank, and source
order. A fifth rule has no test because it has no code: a bare year is
never turned into a date. Inventing 2010-01-01 out of "2010" would be a
fabricated release date, so such films are excluded and counted instead.
"""
import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import movie_discovery as md  # noqa: E402

NOW = dt.datetime(2026, 9, 11, 12, tzinfo=dt.timezone.utc)


def _ago(days):
    return (NOW - dt.timedelta(days=days)).isoformat()


def _catalog(*movies):
    return {"Mix": {"index": {}, "page_contents": {"page-001.json": {"items": list(movies)}}}}


class ReleaseDateNormalisationTests(unittest.TestCase):
    def test_a_real_date_is_kept_as_yyyy_mm_dd(self):
        self.assertEqual(md.normalize_release_date("2010-07-16"), "2010-07-16")

    def test_a_timestamp_is_reduced_to_its_date(self):
        self.assertEqual(md.normalize_release_date("2010-07-16T00:00:00Z"), "2010-07-16")

    def test_a_bare_year_is_never_turned_into_a_date(self):
        """Inventing a day and month the provider never gave is exactly the
        fabrication the plan forbids."""
        self.assertEqual(md.normalize_release_date("2010"), "")
        self.assertEqual(md.normalize_release_date(2010), "")
        self.assertEqual(md.normalize_release_date("2010-07"), "")

    def test_an_impossible_date_is_rejected_rather_than_coerced(self):
        self.assertEqual(md.normalize_release_date("2026-02-31"), "")
        self.assertEqual(md.normalize_release_date("not-a-date"), "")

    def test_empty_input_is_empty_output(self):
        for value in ("", None, "   ", [], {}):
            self.assertEqual(md.normalize_release_date(value), "")


class LatestOrderingTests(unittest.TestCase):
    def test_newest_release_leads(self):
        document = md.build_latest(
            _catalog(
                {"id": "b", "release_date": "2024-05-05"},
                {"id": "c", "release_date": "2019-01-01"},
                {"id": "a", "release_date": "2026-08-30"},
            ),
            now=NOW,
        )
        self.assertEqual([item["id"] for item in document["items"]], ["a", "b", "c"])

    def test_same_year_different_dates_order_correctly(self):
        document = md.build_latest(
            _catalog(
                {"id": "january", "release_date": "2026-01-05"},
                {"id": "august", "release_date": "2026-08-30"},
                {"id": "march", "release_date": "2026-03-17"},
            ),
            now=NOW,
        )
        self.assertEqual(
            [item["id"] for item in document["items"]], ["august", "march", "january"]
        )

    def test_first_seen_at_does_not_define_latest(self):
        """A film added here yesterday but released in 2015 sits below one
        released this year that has been here for months."""
        document = md.build_latest(
            _catalog(
                {"id": "old-release-new-arrival", "release_date": "2015-04-20", "first_seen_at": _ago(0)},
                {"id": "new-release-old-arrival", "release_date": "2026-02-01", "first_seen_at": _ago(300)},
            ),
            now=NOW,
        )
        self.assertEqual(
            [item["id"] for item in document["items"]],
            ["new-release-old-arrival", "old-release-new-arrival"],
        )

    def test_available_link_count_does_not_define_latest(self):
        document = md.build_latest(
            _catalog(
                {"id": "one-server-newer", "release_date": "2026-05-05", "available_link_count": 1},
                {"id": "five-server-older", "release_date": "2018-05-05", "available_link_count": 5},
            ),
            now=NOW,
        )
        self.assertEqual([item["id"] for item in document["items"]][0], "one-server-newer")

    def test_trend_rank_does_not_define_latest(self):
        document = md.build_latest(
            _catalog(
                {"id": "trending-but-old", "release_date": "2001-01-01", "is_trending": True, "trending_rank": 1},
                {"id": "quiet-but-new", "release_date": "2026-07-07"},
            ),
            now=NOW,
        )
        self.assertEqual([item["id"] for item in document["items"]][0], "quiet-but-new")

    def test_source_order_does_not_define_latest(self):
        """The catalogue hands them over oldest-release-first here; the row
        must still come back newest-first."""
        document = md.build_latest(
            _catalog(
                {"id": "first-in-source", "release_date": "2001-01-01"},
                {"id": "second-in-source", "release_date": "2026-01-01"},
            ),
            now=NOW,
        )
        self.assertEqual([item["id"] for item in document["items"]][0], "second-in-source")

    def test_films_released_the_same_day_are_ordered_deterministically(self):
        document = md.build_latest(
            _catalog(
                {"id": "bbb", "release_date": "2026-06-06", "available_link_count": 5},
                {"id": "aaa", "release_date": "2026-06-06", "available_link_count": 1},
            ),
            now=NOW,
        )
        self.assertEqual([item["id"] for item in document["items"]], ["bbb", "aaa"])


class ExclusionPolicyTests(unittest.TestCase):
    def test_a_future_release_is_excluded_and_counted(self):
        future = (NOW + dt.timedelta(days=30)).date().isoformat()
        document = md.build_latest(
            _catalog(
                {"id": "out-now", "release_date": "2026-09-01"},
                {"id": "coming-soon", "release_date": future},
            ),
            now=NOW,
        )
        self.assertEqual([item["id"] for item in document["items"]], ["out-now"])
        self.assertEqual(document["excluded"]["future_release"], 1)

    def test_a_film_released_today_is_included(self):
        document = md.build_latest(
            _catalog({"id": "today", "release_date": NOW.date().isoformat()}), now=NOW
        )
        self.assertEqual(document["count"], 1)

    def test_a_film_with_no_exact_date_is_excluded_and_counted(self):
        document = md.build_latest(
            _catalog(
                {"id": "dated", "release_date": "2026-09-01"},
                {"id": "year-only", "year": 2010},
                {"id": "nothing"},
            ),
            now=NOW,
        )
        self.assertEqual([item["id"] for item in document["items"]], ["dated"])
        self.assertEqual(document["excluded"]["no_exact_release_date"], 2)

    def test_the_missing_date_policy_is_stated_in_the_output_itself(self):
        document = md.build_latest(_catalog({"id": "a", "release_date": "2026-01-01"}), now=NOW)
        self.assertEqual(document["missing_date_policy"], "excluded")
        self.assertEqual(document["signal"], "release_date")


class IndependenceFromJustAddedTests(unittest.TestCase):
    def test_an_old_film_added_today_is_just_added_but_not_latest(self):
        catalog = _catalog(
            {"id": "old-film", "release_date": "2015-04-20", "first_seen_at": _ago(0)}
        )
        just_added = md.build_just_added(catalog, now=NOW)
        latest = md.build_latest(catalog, now=NOW)
        self.assertEqual([i["id"] for i in just_added["items"]], ["old-film"])
        self.assertEqual([i["id"] for i in latest["items"]], ["old-film"])
        # Present in both lists, but for different reasons - and the old
        # film sits at the bottom of Latest once anything newer exists.
        newer = _catalog(
            {"id": "old-film", "release_date": "2015-04-20", "first_seen_at": _ago(0)},
            {"id": "new-film", "release_date": "2026-09-01", "first_seen_at": _ago(200)},
        )
        self.assertEqual([i["id"] for i in md.build_latest(newer, now=NOW)["items"]][0], "new-film")
        self.assertEqual([i["id"] for i in md.build_just_added(newer, now=NOW)["items"]], ["old-film"])

    def test_a_recent_release_that_arrived_long_ago_is_latest_but_not_just_added(self):
        catalog = _catalog(
            {"id": "early-arrival", "release_date": "2026-09-01", "first_seen_at": _ago(200)}
        )
        self.assertEqual(md.build_just_added(catalog, now=NOW)["items"], [])
        self.assertEqual([i["id"] for i in md.build_latest(catalog, now=NOW)["items"]], ["early-arrival"])

    def test_the_two_rows_use_different_signals(self):
        catalog = _catalog({"id": "a", "release_date": "2026-09-01", "first_seen_at": _ago(1)})
        self.assertEqual(md.build_just_added(catalog, now=NOW)["signal"], "first_seen_at")
        self.assertEqual(md.build_latest(catalog, now=NOW)["signal"], "release_date")


class LatestPayloadTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.output = str(Path(self._tmp.name) / "discovery" / "latest.json")

    def test_no_playback_data_reaches_the_file(self):
        movie = {
            "id": "film-a",
            "name": "Some Film",
            "release_date": "2026-09-01",
            "logo": "https://x.test/poster.jpg",
            "url": "https://secret.test/stream.mkv",
            "backups": [{"url": "https://secret.test/b.mkv"}],
            "headers": {"Referer": "https://secret.test/"},
            "playback_id": "ctv_deadbeef",
        }
        document = md.build_latest(_catalog(movie), now=NOW)
        serialized = json.dumps(document)
        for forbidden in md.FORBIDDEN_CARD_FIELDS:
            self.assertNotIn(f'"{forbidden}"', serialized)
        self.assertNotIn("secret.test", serialized)

    def test_the_row_is_capped(self):
        many = [{"id": f"film-{n:04d}", "release_date": "2026-01-01"} for n in range(200)]
        self.assertEqual(len(md.build_latest(_catalog(*many), now=NOW)["items"]), md.LATEST_LIMIT)

    def test_generate_writes_atomically(self):
        summary = md.generate_latest(
            _catalog({"id": "a", "release_date": "2026-09-01"}), output_path=self.output, now=NOW
        )
        self.assertEqual(summary["count"], 1)
        on_disk = json.loads(Path(self.output).read_text(encoding="utf-8"))
        self.assertEqual(on_disk["items"][0]["release_date"], "2026-09-01")
        self.assertEqual(list(Path(self.output).parent.glob(".*.tmp")), [])

    def test_an_empty_catalogue_produces_an_honest_empty_row(self):
        document = md.build_latest(_catalog(), now=NOW)
        self.assertEqual(document["items"], [])
        self.assertEqual(document["count"], 0)


if __name__ == "__main__":
    unittest.main()
