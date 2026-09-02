"""One season written as many blocks is still one season.

The staging catalogue is built from a TXT source that writes a `Season:` header
per episode, so a season with eight episodes arrives as eight blocks all
carrying the same number. That is the format, not a mistake in the data - and
reading each block as its own season raised "Duplicate Season 2" and killed the
run: Live Signal Scanner #1180 died there on 2026-09-02 after an hour and
thirty-eight minutes of finished work, with 20517 routes already checked.

The real record, from
categories/Bangla_Movies/bangla_movies.txt in 0matbank/hopeful-research:

    Show name: Chokro 2
    Season: S02  Episode: Episode 01-07   (a batch link)
    Season: S02  Episode: Episode 01
    Season: S02  Episode: Episode 02
    ...            Episode: Episode 07
"""
import json
import tempfile
import unittest
from pathlib import Path

from scanner.series import prepare_manual_series


def episode(label, url):
    return {"episode_label": label, "url": url,
            "resolution": "1080p", "resolution_height": 1080}


def series(name="Chokro 2", seasons=None, **extra):
    row = {"name": name, "category": "Bangla", "year": 2026,
           "poster": "https://example.test/poster.webp",
           "seasons": seasons or []}
    row.update(extra)
    return row


def prepared(*items):
    """Run the real validator over a staging catalogue holding `items`."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "working").mkdir()
        (root / "working" / "manual-series-catalog.json").write_text(
            json.dumps({"items": list(items)}), encoding="utf-8")
        return prepare_manual_series(project_root=root)


CHOKRO_BLOCKS = [("Episode 01-07", "S02E01-07")] + [
    (f"Episode 0{n}", f"S02E0{n}") for n in range(1, 8)
]
CHOKRO = series(seasons=[
    {"number": 2, "episodes": [episode(label, f"https://r2.example/{tag}.mkv")]}
    for label, tag in CHOKRO_BLOCKS
])


class TheRecordThatKilledRun1180(unittest.TestCase):
    def test_it_no_longer_ends_the_scan(self):
        out = prepared(CHOKRO)
        self.assertEqual(out["series"], 1)

    def test_the_eight_blocks_become_one_season(self):
        show = prepared(CHOKRO)["items"][0]
        self.assertEqual([s["number"] for s in show["seasons"]], [2])
        self.assertEqual(show["total_seasons"], 1)

    def test_every_episode_is_kept(self):
        show = prepared(CHOKRO)["items"][0]
        self.assertEqual(show["seasons"][0]["count"], 8)
        self.assertEqual(show["total_episodes"], 8)

    def test_each_label_keeps_its_own_file(self):
        # The batch link and the individual files are different downloads, so
        # they are different episodes - and a label must not drift onto another
        # episode's URL.
        show = prepared(CHOKRO)["items"][0]
        pairs = [(e["episode_label"], e["url"].rsplit("/", 1)[-1])
                 for e in show["episode_payloads"][2]]
        self.assertEqual(pairs, [
            ("Episode 01-07", "S02E01-07.mkv"),
            ("Episode 01", "S02E01.mkv"),
            ("Episode 02", "S02E02.mkv"),
            ("Episode 03", "S02E03.mkv"),
            ("Episode 04", "S02E04.mkv"),
            ("Episode 05", "S02E05.mkv"),
            ("Episode 06", "S02E06.mkv"),
            ("Episode 07", "S02E07.mkv"),
        ])

    def test_the_episode_keys_stay_unique(self):
        show = prepared(CHOKRO)["items"][0]
        keys = [e["episode_key"] for e in show["episode_payloads"][2]]
        self.assertEqual(len(keys), len(set(keys)))

    def test_the_episodes_are_numbered_across_the_season(self):
        # Not within each block, or every block would produce an "episode 1".
        show = prepared(CHOKRO)["items"][0]
        numbers = [e["episode_number"] for e in show["episode_payloads"][2]]
        self.assertEqual(numbers, list(range(1, 9)))

    def test_the_latest_episode_is_the_last_one(self):
        show = prepared(CHOKRO)["items"][0]
        self.assertEqual(show["latest_episode"], "Episode 07")


class SeparateSeasonsStaySeparate(unittest.TestCase):
    def test_two_different_numbers_are_two_seasons(self):
        show = prepared(series(seasons=[
            {"number": 1, "episodes": [episode("Episode 01", "https://r2.example/a.mkv")]},
            {"number": 2, "episodes": [episode("Episode 01", "https://r2.example/b.mkv")]},
        ]))["items"][0]
        self.assertEqual([s["number"] for s in show["seasons"]], [1, 2])
        self.assertEqual(show["total_seasons"], 2)

    def test_seasons_are_ordered_by_number(self):
        show = prepared(series(seasons=[
            {"number": 3, "episodes": [episode("Episode 01", "https://r2.example/c.mkv")]},
            {"number": 1, "episodes": [episode("Episode 01", "https://r2.example/a.mkv")]},
            {"number": 3, "episodes": [episode("Episode 02", "https://r2.example/d.mkv")]},
        ]))["items"][0]
        self.assertEqual([s["number"] for s in show["seasons"]], [1, 3])
        self.assertEqual(show["seasons"][1]["count"], 2)

    def test_specials_keep_their_title(self):
        show = prepared(series(seasons=[
            {"number": 0, "episodes": [episode("Behind the scenes",
                                               "https://r2.example/s.mkv")]},
            {"number": 1, "episodes": [episode("Episode 01", "https://r2.example/a.mkv")]},
        ]))["items"][0]
        titles = {s["number"]: s["title"] for s in show["seasons"]}
        self.assertEqual(titles[0], "Specials")
        # A special is not a season of the show.
        self.assertEqual(show["total_seasons"], 1)


class TheSameEpisodeTwiceIsOneEpisode(unittest.TestCase):
    def test_a_repeated_label_in_another_block_is_folded(self):
        show = prepared(series(seasons=[
            {"number": 1, "episodes": [episode("Episode 01", "https://r2.example/a.mkv")]},
            {"number": 1, "episodes": [episode("Episode 01", "https://r2.example/a.mkv")]},
        ]))["items"][0]
        self.assertEqual(show["total_episodes"], 1)

    def test_the_first_spelling_of_it_wins(self):
        show = prepared(series(seasons=[
            {"number": 1, "episodes": [episode("Episode 01", "https://r2.example/first.mkv")]},
            {"number": 1, "episodes": [episode("Episode 01", "https://r2.example/second.mkv")]},
        ]))["items"][0]
        self.assertTrue(show["episode_payloads"][1][0]["url"].endswith("first.mkv"))


class ABrokenRecordStillReportsItself(unittest.TestCase):
    """Merging repeated seasons is not a licence to accept anything."""

    def test_a_series_with_no_seasons_is_refused(self):
        with self.assertRaises(ValueError):
            prepared(series(seasons=[]))

    def test_a_series_with_no_playable_episode_is_refused(self):
        with self.assertRaises(ValueError):
            prepared(series(seasons=[{"number": 1, "episodes": []}]))

    def test_a_nameless_series_is_refused(self):
        with self.assertRaises(ValueError):
            prepared({"category": "Bangla", "seasons": [
                {"number": 1, "episodes": [episode("Episode 01",
                                                   "https://r2.example/a.mkv")]}]})


if __name__ == "__main__":
    unittest.main()
