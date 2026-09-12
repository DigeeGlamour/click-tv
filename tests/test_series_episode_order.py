"""Episodes come out numerically ascending, and no number is ever invented.

The staging TXT is written by hand, so a late upload is appended to the end of
its block: `Episode 10` can be written before `Episode 02`, and one season was
published with `Episode 09-12` ahead of `Episode 01-04`. Source order got the
episodes collected - it does not get to decide what a viewer sees.

The other half of the contract is the silence: an episode the source never
numbered keeps no number. A synthesised "Episode 03" is indistinguishable from
a real one once it is on the card, and it would pin that episode to a position
in the list for ever.
"""
import json
import tempfile
import unittest
from pathlib import Path

from scanner.series import (
    episode_number_range,
    order_season_episodes,
    prepare_manual_series,
)


def episode(label, url="https://r2.example/x.mkv", **extra):
    row = {"episode_label": label, "url": url,
           "resolution": "1080p", "resolution_height": 1080}
    row.update(extra)
    return row


def prepared(*items):
    """Run the real publisher's validator over a staging catalogue."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "working").mkdir()
        (root / "working" / "manual-series-catalog.json").write_text(
            json.dumps({"items": list(items)}), encoding="utf-8")
        return prepare_manual_series(project_root=root)


def show(seasons, **extra):
    row = {"name": "Order Test", "category": "Bangla", "year": 2026,
           "poster": "https://example.test/poster.webp", "seasons": seasons}
    row.update(extra)
    return prepared(row)["items"][0]


def labels(series, season_number):
    return [e["episode_label"] for e in series["episode_payloads"][season_number]]


class ReadingTheNumberTheSourceWrote(unittest.TestCase):
    def test_a_plain_key(self):
        self.assertEqual(episode_number_range("04"), (4, 4))

    def test_a_label(self):
        self.assertEqual(episode_number_range("Episode 12"), (12, 12))

    def test_a_batch_range(self):
        self.assertEqual(episode_number_range("01-07"), (1, 7))

    def test_a_spaced_range(self):
        self.assertEqual(episode_number_range("Episode 05 - 08"), (5, 8))

    def test_a_backwards_range_is_read_forwards(self):
        self.assertEqual(episode_number_range("08-05"), (5, 8))

    def test_the_key_outranks_the_title(self):
        # The explicit key is the source being deliberate; the title is prose.
        self.assertEqual(episode_number_range("04", "Episode 11 of 22"), (4, 4))

    def test_text_with_no_number_yields_none(self):
        self.assertEqual(episode_number_range("Behind the scenes"), (None, None))

    def test_nothing_at_all_yields_none(self):
        self.assertEqual(episode_number_range("", None), (None, None))

    def test_it_falls_through_to_the_next_candidate(self):
        self.assertEqual(episode_number_range("", "Episode 09"), (9, 9))


class EpisodesAreOrderedNumerically(unittest.TestCase):
    def test_episode_2_comes_before_episode_10(self):
        # The ordering bug this whole rule exists for: a string sort puts
        # "Episode 10" ahead of "Episode 02".
        series = show([{"number": 1, "episodes": [
            episode("Episode 10", "https://r2.example/j.mkv"),
            episode("Episode 02", "https://r2.example/b.mkv"),
            episode("Episode 01", "https://r2.example/a.mkv"),
        ]}])
        self.assertEqual(labels(series, 1),
                         ["Episode 01", "Episode 02", "Episode 10"])

    def test_episode_number_is_the_position_in_that_order(self):
        series = show([{"number": 1, "episodes": [
            episode("Episode 10", "https://r2.example/j.mkv"),
            episode("Episode 01", "https://r2.example/a.mkv"),
        ]}])
        numbers = [e["episode_number"] for e in series["episode_payloads"][1]]
        self.assertEqual(numbers, [1, 2])
        # ...and the real number the source published is still on the record,
        # so the card can show "Episode 10" rather than a rewritten "E02".
        self.assertEqual(
            [e["episode_start_number"] for e in series["episode_payloads"][1]],
            [1, 10])

    def test_a_shuffled_batch_season_is_put_back_in_order(self):
        # data/series/south-indian/batchmates-2026/season-01.json shipped in
        # this order.
        series = show([{"number": 1, "episodes": [
            episode("Episode 09-12", "https://r2.example/c.mkv"),
            episode("Episode 01-04", "https://r2.example/a.mkv"),
            episode("Episode 05-08", "https://r2.example/b.mkv"),
        ]}])
        self.assertEqual(labels(series, 1),
                         ["Episode 01-04", "Episode 05-08", "Episode 09-12"])

    def test_a_batch_link_leads_the_run_it_covers(self):
        series = show([{"number": 1, "episodes": [
            episode("Episode 01", "https://r2.example/a.mkv"),
            episode("Episode 01-07", "https://r2.example/batch.mkv"),
            episode("Episode 02", "https://r2.example/b.mkv"),
        ]}])
        self.assertEqual(labels(series, 1),
                         ["Episode 01-07", "Episode 01", "Episode 02"])

    def test_each_season_is_ordered_on_its_own(self):
        series = show([
            {"number": 2, "episodes": [
                episode("Episode 10", "https://r2.example/2j.mkv"),
                episode("Episode 03", "https://r2.example/2c.mkv"),
            ]},
            {"number": 1, "episodes": [
                episode("Episode 07", "https://r2.example/1g.mkv"),
                episode("Episode 02", "https://r2.example/1b.mkv"),
            ]},
        ])
        self.assertEqual([s["number"] for s in series["seasons"]], [1, 2])
        self.assertEqual(labels(series, 1), ["Episode 02", "Episode 07"])
        self.assertEqual(labels(series, 2), ["Episode 03", "Episode 10"])

    def test_the_latest_episode_is_the_highest_numbered_one(self):
        series = show([{"number": 1, "episodes": [
            episode("Episode 10", "https://r2.example/j.mkv"),
            episode("Episode 02", "https://r2.example/b.mkv"),
        ]}])
        self.assertEqual(series["latest_episode"], "Episode 10")


class UnnumberedEpisodesKeepNoNumber(unittest.TestCase):
    def test_no_number_is_stamped_onto_an_unnumbered_episode(self):
        series = show([{"number": 0, "episodes": [
            episode("Behind the scenes", "https://r2.example/s.mkv"),
        ]}])
        record = series["episode_payloads"][0][0]
        self.assertNotIn("episode_start_number", record)
        self.assertNotIn("episode_end_number", record)

    def test_they_sort_last_and_keep_their_own_order(self):
        series = show([{"number": 1, "episodes": [
            episode("Bloopers", "https://r2.example/x.mkv"),
            episode("Episode 02", "https://r2.example/b.mkv"),
            episode("Trailer", "https://r2.example/y.mkv"),
            episode("Episode 01", "https://r2.example/a.mkv"),
        ]}])
        self.assertEqual(labels(series, 1),
                         ["Episode 01", "Episode 02", "Bloopers", "Trailer"])

    def test_our_own_fallback_label_is_not_read_back_as_a_number(self):
        # With no label at all the publisher writes "Episode 01" from the
        # source position. Parsing that back would turn our placeholder into
        # the source's episode number.
        series = show([{"number": 1, "episodes": [
            episode(None, "https://r2.example/a.mkv"),
            episode(None, "https://r2.example/b.mkv"),
        ]}])
        for record in series["episode_payloads"][1]:
            self.assertNotIn("episode_start_number", record)


class TheOrderingIsStable(unittest.TestCase):
    def test_running_it_twice_changes_nothing(self):
        rows = [
            {"episode_label": "Episode 02", "episode_start_number": 2, "episode_end_number": 2},
            {"episode_label": "Episode 01", "episode_start_number": 1, "episode_end_number": 1},
            {"episode_label": "Extra"},
        ]
        once = [dict(row) for row in order_season_episodes([dict(r) for r in rows])]
        twice = order_season_episodes([dict(r) for r in once])
        self.assertEqual([r["episode_label"] for r in once],
                         [r["episode_label"] for r in twice])
        self.assertEqual([r["episode_number"] for r in twice], [1, 2, 3])

    def test_an_already_ordered_season_is_left_alone(self):
        series = show([{"number": 1, "episodes": [
            episode("Episode 01", "https://r2.example/a.mkv"),
            episode("Episode 02", "https://r2.example/b.mkv"),
            episode("Episode 03", "https://r2.example/c.mkv"),
        ]}])
        self.assertEqual(labels(series, 1),
                         ["Episode 01", "Episode 02", "Episode 03"])


class MergedDuplicateRecordsAreReordered(unittest.TestCase):
    """Two staging blocks for one show fold together - and re-sort."""

    def test_episodes_folded_in_later_do_not_sit_at_the_end(self):
        catalogue = prepared(
            {"name": "Split Show", "category": "Bangla", "year": 2026,
             "poster": "https://example.test/p.webp",
             "seasons": [{"number": 1, "episodes": [
                 episode("Episode 03", "https://r2.example/c.mkv")]}]},
            {"name": "Split Show", "category": "Bangla", "year": 2026,
             "poster": "https://example.test/p.webp",
             "seasons": [{"number": 1, "episodes": [
                 episode("Episode 01", "https://r2.example/a.mkv")]}]},
        )
        self.assertEqual(len(catalogue["items"]), 1)
        series = catalogue["items"][0]
        self.assertEqual(labels(series, 1), ["Episode 01", "Episode 03"])
        self.assertEqual(series["latest_episode"], "Episode 03")


if __name__ == "__main__":
    unittest.main()
