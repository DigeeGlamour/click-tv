"""Popular on Click TV counts watching, not clicking - and says where it came from.

Three ways a popularity row goes wrong, and one test class each.

It measures the wrong thing: `play_start` fires the instant someone presses
play, so a row built on it ranks misleading posters and mis-taps. Only a
qualified play - thirty seconds of real playback - counts here.

It can be inflated: one client reporting the same event in a loop, or a bot,
must move the number by one and no further.

It lies about itself: an internal list presented as international trending
tells every viewer something false. The label and the signal note travel
inside the published file so no UI can rename it by accident.
"""
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from scanner import movie_popularity as mp


def at(day, hour=12):
    return dt.datetime(2026, 9, day, hour, tzinfo=dt.timezone.utc)


def ms(day, hour=12):
    return int(at(day, hour).timestamp() * 1000)


def event(item, kind=mp.QUALIFIED_EVENT, day=10, session="s1", **extra):
    row = {"event_type": kind, "item_id": item, "ts": ms(day), "session_id": session}
    row.update(extra)
    return row


class WhatSurvivesSanitising(unittest.TestCase):
    def test_a_known_event_survives(self):
        clean = mp.sanitize_event(event("film-a"))
        self.assertEqual(clean["item_id"], "film-a")
        self.assertEqual(clean["event_type"], mp.QUALIFIED_EVENT)
        self.assertEqual(clean["day"], "2026-09-10")

    def test_an_unknown_event_type_is_dropped(self):
        self.assertIsNone(mp.sanitize_event(event("film-a", kind="play_1000_hours")))

    def test_an_event_with_no_item_is_dropped(self):
        self.assertIsNone(mp.sanitize_event(event("")))

    def test_an_event_with_no_timestamp_is_dropped(self):
        self.assertIsNone(mp.sanitize_event({"event_type": "play_30s", "item_id": "x"}))

    def test_a_stream_url_never_survives(self):
        clean = mp.sanitize_event(event(
            "film-a",
            url="https://cdn.example/secret.mkv",
            backups=["https://cdn.example/b.mkv"],
            headers={"Authorization": "Bearer x"},
            api_key="abc123",
            ip="203.0.113.4",
        ))
        serialised = json.dumps(clean)
        for leaked in ("cdn.example", "Bearer", "abc123", "203.0.113.4", "url", "headers"):
            self.assertNotIn(leaked, serialised)

    def test_only_a_day_is_kept_not_a_moment(self):
        # A millisecond timestamp is a fingerprint; a popularity count needs a
        # day and nothing finer.
        clean = mp.sanitize_event(event("film-a", day=10))
        self.assertEqual(clean["day"], "2026-09-10")
        self.assertNotIn("ts", clean)

    def test_a_series_event_keeps_its_episode_identity(self):
        clean = mp.sanitize_event({
            "event_type": "play_30s", "item_id": "show-s01e02", "ts": ms(10),
            "content_type": "episode", "series_id": "show",
            "season_number": 1, "episode_number": 2,
        })
        self.assertEqual(clean["series_id"], "show")
        self.assertEqual(clean["season_number"], 1)
        self.assertEqual(clean["episode_number"], 2)

    def test_an_unknown_content_type_falls_back_to_movie(self):
        clean = mp.sanitize_event(event("film-a", content_type="hovercraft"))
        self.assertEqual(clean["content_type"], "movie")


class CountingIsNarrow(unittest.TestCase):
    def test_the_same_session_reporting_forty_times_counts_once(self):
        store = mp.aggregate_events(
            [event("film-a") for _ in range(40)], now=at(10))
        self.assertEqual(store["days"]["2026-09-10"]["film-a"][mp.QUALIFIED_EVENT], 1)

    def test_different_sessions_each_count(self):
        store = mp.aggregate_events(
            [event("film-a", session=f"s{n}") for n in range(5)], now=at(10))
        self.assertEqual(store["days"]["2026-09-10"]["film-a"][mp.QUALIFIED_EVENT], 5)

    def test_the_same_session_on_another_day_counts_again(self):
        store = mp.aggregate_events(
            [event("film-a", day=10), event("film-a", day=11)], now=at(11))
        self.assertEqual(store["days"]["2026-09-10"]["film-a"][mp.QUALIFIED_EVENT], 1)
        self.assertEqual(store["days"]["2026-09-11"]["film-a"][mp.QUALIFIED_EVENT], 1)

    def test_play_start_is_counted_separately_and_never_as_a_play(self):
        store = mp.aggregate_events([
            event("film-a", kind="play_start", session="s1"),
            event("film-a", kind="play_start", session="s2"),
            event("film-a", kind="play_start", session="s3"),
        ], now=at(10))
        counts = store["days"]["2026-09-10"]["film-a"]
        self.assertEqual(counts["play_start"], 3)
        self.assertNotIn(mp.QUALIFIED_EVENT, counts)

    def test_earlier_counts_are_carried_forward(self):
        first = mp.aggregate_events([event("film-a", session="s1")], now=at(10))
        second = mp.aggregate_events(
            [event("film-a", session="s2")], existing=first, now=at(10))
        self.assertEqual(second["days"]["2026-09-10"]["film-a"][mp.QUALIFIED_EVENT], 2)

    def test_no_session_id_survives_into_the_aggregate(self):
        store = mp.aggregate_events([event("film-a", session="secret-session")], now=at(10))
        self.assertNotIn("secret-session", json.dumps(store))

    def test_old_days_are_dropped(self):
        stale = at(10) - dt.timedelta(days=mp.AGGREGATE_RETENTION_DAYS + 5)
        existing = {"days": {stale.date().isoformat(): {"film-old": {"play_30s": 9}}}}
        store = mp.aggregate_events([], existing=existing, now=at(10))
        self.assertNotIn(stale.date().isoformat(), store["days"])


class ThePublishedRow(unittest.TestCase):
    def store(self, **plays):
        events = []
        for item, count in plays.items():
            events.extend(event(item, session=f"{item}-{n}") for n in range(count))
        return mp.aggregate_events(events, now=at(10))

    def catalogue(self, *ids):
        return {item: {"name": item.title(), "category": "Mix", "logo": "p.jpg"} for item in ids}

    def test_it_ranks_by_qualified_plays(self):
        store = self.store(alpha=9, beta=4, gamma=6)
        document = mp.build_popular(
            store, catalogue=self.catalogue("alpha", "beta", "gamma"), now=at(10))
        self.assertEqual([row["id"] for row in document["items"]], ["alpha", "gamma", "beta"])

    def test_a_single_viewer_is_not_a_popularity_signal(self):
        store = self.store(lonely=1)
        document = mp.build_popular(store, catalogue=self.catalogue("lonely"), now=at(10))
        self.assertEqual(document["items"], [])

    def test_a_withdrawn_title_is_not_offered(self):
        store = self.store(alpha=9, withdrawn=9)
        document = mp.build_popular(store, catalogue=self.catalogue("alpha"), now=at(10))
        self.assertEqual([row["id"] for row in document["items"]], ["alpha"])
        self.assertEqual(document["excluded_inactive"], 1)

    def test_it_only_looks_back_over_the_window(self):
        old = mp.aggregate_events(
            [event("ancient", session=f"s{n}", day=1) for n in range(9)], now=at(10))
        document = mp.build_popular(old, catalogue=self.catalogue("ancient"), now=at(10))
        self.assertEqual(document["items"], [])

    def test_it_carries_the_real_record_fields(self):
        store = self.store(alpha=5)
        document = mp.build_popular(store, catalogue=self.catalogue("alpha"), now=at(10))
        row = document["items"][0]
        self.assertEqual(row["name"], "Alpha")
        self.assertEqual(row["category"], "Mix")

    def test_no_stream_field_reaches_the_published_row(self):
        store = self.store(alpha=5)
        catalogue = {"alpha": {
            "name": "Alpha", "url": "https://cdn.example/a.mkv",
            "backups": ["https://cdn.example/b.mkv"], "playback_id": "ctv_x",
            "headers": {"Authorization": "Bearer x"},
        }}
        document = mp.build_popular(store, catalogue=catalogue, now=at(10))
        serialised = json.dumps(document)
        for leaked in ("cdn.example", "playback_id", "Bearer", "backups"):
            self.assertNotIn(leaked, serialised)

    def test_nothing_watched_produces_an_empty_list_not_filler(self):
        document = mp.build_popular({"days": {}}, catalogue={}, now=at(10))
        self.assertEqual(document["items"], [])
        self.assertEqual(document["count"], 0)


class ItSaysWhatItIs(unittest.TestCase):
    def test_the_label_is_click_tv_not_trending(self):
        document = mp.build_popular({"days": {}}, now=at(10))
        self.assertEqual(document["label"], "Popular on Click TV")
        self.assertNotIn("trending", document["label"].lower())

    def test_the_signal_names_its_own_source(self):
        document = mp.build_popular({"days": {}}, now=at(10))
        self.assertEqual(document["signal"], "internal_qualified_plays")
        self.assertIn("NOT an", document["signal_note"])
        self.assertIn("international", document["signal_note"].lower())

    def test_the_window_and_threshold_are_published_with_the_numbers(self):
        document = mp.build_popular({"days": {}}, now=at(10))
        self.assertEqual(document["window_days"], mp.POPULAR_WINDOW_DAYS)
        self.assertEqual(document["minimum_qualified_plays"], mp.MINIMUM_QUALIFIED_PLAYS)


class WritingItDown(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = str(Path(self._tmp.name) / "popular.json")

    def test_a_real_row_is_written(self):
        document = {"items": [{"id": "alpha", "qualified_plays": 5}], "count": 1}
        self.assertTrue(mp.write_popular(document, self.path))
        with open(self.path, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["items"][0]["id"], "alpha")

    def test_an_empty_result_never_blanks_a_good_file(self):
        # An analytics outage must not erase a row that was right yesterday.
        mp.write_popular({"items": [{"id": "alpha", "qualified_plays": 5}], "count": 1}, self.path)
        self.assertFalse(mp.write_popular({"items": [], "count": 0}, self.path))
        with open(self.path, encoding="utf-8") as handle:
            self.assertEqual(len(json.load(handle)["items"]), 1)

    def test_an_empty_result_is_written_when_there_was_nothing_before(self):
        self.assertTrue(mp.write_popular({"items": [], "count": 0}, self.path))

    def test_a_corrupt_aggregate_reads_as_empty(self):
        path = str(Path(self._tmp.name) / "aggregate.json")
        Path(path).write_text("{not json", encoding="utf-8")
        self.assertEqual(mp.load_aggregate(path)["days"], {})

    def test_an_aggregate_round_trips(self):
        path = str(Path(self._tmp.name) / "aggregate.json")
        store = mp.aggregate_events([event("film-a")], now=at(10))
        self.assertTrue(mp.save_aggregate(store, path))
        self.assertEqual(
            mp.load_aggregate(path)["days"]["2026-09-10"]["film-a"][mp.QUALIFIED_EVENT], 1)


if __name__ == "__main__":
    unittest.main()
