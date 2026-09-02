"""A published episode card carries only its playback_id. The id must resolve.

The Worker reads `data/playback/` to turn an episode's id into a real URL and
headers, so an id that is not in the catalogue is a card that opens and plays
nothing. Run 33630856186 was refused over 137 of them:

    Bangla episode #2 playback_id catalogue-এ নেই: Chokro 2 — Episode 01

The season file and the catalogue record come out of the same `sanitize_item`
call, so they cannot disagree inside one run - and they did disagree in the
repository, because the season tree was published at 11:42:46 while all 256
committed shards still said 11:39:47. Only the validator noticed, at the very
end of a scan. This puts it in the suite instead.
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from scanner.playback_profiles import (
    PlaybackProfileCollector,
    load_public_catalog_records,
    merge_public_catalog,
    stable_playback_id,
)
from scanner.series_catalogue import missing_episode_profiles, reconcile

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data"


def episode(url, **extra):
    row = {"episode_label": "Episode 01", "url": url, "stream_type": "mp4"}
    row.update(extra)
    return row


def season_file(root, episodes, series="Chokro 2", category="Bangla", number=2):
    directory = root / "series" / category.lower() / "a-show"
    directory.mkdir(parents=True, exist_ok=True)
    items = []
    for item in episodes:
        row = dict(item)
        row["playback_id"] = stable_playback_id(row)
        items.append(row)
    (directory / f"season-{number:02d}.json").write_text(json.dumps({
        "series_name": series,
        "category": category,
        "season_number": number,
        "items": items,
    }), encoding="utf-8")
    return items


class TheRepositoryIsConsistent(unittest.TestCase):
    """The check the validator makes, made here where it is cheap."""

    def test_every_published_episode_has_a_playback_profile(self):
        missing = missing_episode_profiles(DATA)
        detail = ", ".join(
            f"{row['series']} / {row['episode']}" for row in missing[:6]
        )
        self.assertEqual(
            missing, [],
            f"{len(missing)} published episode(s) cannot be played: {detail}")

    def test_the_catalogue_actually_holds_records(self):
        # Guards the check above against passing on an empty catalogue.
        self.assertGreater(len(load_public_catalog_records(DATA)), 1000)

    def test_there_are_episodes_to_check(self):
        self.assertGreater(
            len(list((DATA / "series").glob("*/*/season-*.json"))), 10)


class RebuildingAProfileFromItsOwnCard(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.data = self.dir / "data"
        self.data.mkdir()

    def test_a_missing_episode_is_registered(self):
        rows = season_file(self.data, [episode("https://cdn.test/e01.mkv")])
        self.assertEqual(len(missing_episode_profiles(self.data)), 1)

        report = reconcile(self.data)
        self.assertEqual(report["registered"], 1)
        self.assertEqual(missing_episode_profiles(self.data), [])

        records = load_public_catalog_records(self.data)
        self.assertEqual(records[rows[0]["playback_id"]]["url"],
                         "https://cdn.test/e01.mkv")

    def test_the_headers_and_type_come_back_too(self):
        rows = season_file(self.data, [episode(
            "https://cdn.test/e01.m3u8",
            stream_type="hls",
            header_profile="r2",
            headers={"Referer": "https://cdn.test/"},
        )])
        reconcile(self.data)
        record = load_public_catalog_records(self.data)[rows[0]["playback_id"]]
        self.assertEqual(record["stream_type"], "hls")
        self.assertEqual(record["header_profile"], "r2")
        self.assertEqual(record["headers"], {"Referer": "https://cdn.test/"})

    def test_running_it_twice_changes_nothing(self):
        season_file(self.data, [episode("https://cdn.test/e01.mkv")])
        reconcile(self.data)
        first = load_public_catalog_records(self.data)

        again = reconcile(self.data)
        self.assertEqual(again["missing"], 0)
        self.assertEqual(again["registered"], 0)
        self.assertEqual(load_public_catalog_records(self.data), first)

    def test_a_record_already_there_is_left_exactly_alone(self):
        rows = season_file(self.data, [episode("https://cdn.test/e01.mkv"),
                                       episode("https://cdn.test/e02.mkv")])
        collector = PlaybackProfileCollector("series", "2026-01-01T00:00:00+00:00")
        collector.records[rows[0]["playback_id"]] = {
            "schema_version": 1, "status": "active",
            "url": "https://hand-fixed.test/e01.mkv", "headers": {},
            "drm": {}, "stream_type": "mp4", "header_profile": "",
            "inherit_manifest_query": False,
            "updated_at": "2026-01-01T00:00:00+00:00", "scan_mode": "series",
        }
        merge_public_catalog(self.data, collector)

        self.assertEqual(reconcile(self.data)["registered"], 1)
        records = load_public_catalog_records(self.data)
        self.assertEqual(records[rows[0]["playback_id"]]["url"],
                         "https://hand-fixed.test/e01.mkv")
        self.assertEqual(records[rows[1]["playback_id"]]["url"],
                         "https://cdn.test/e02.mkv")

    def test_two_episodes_on_one_route_share_one_record(self):
        # A batch link listed twice is one route, so 137 references can be
        # fewer than 137 records - which is what the repair actually reported.
        rows = season_file(self.data, [
            episode("https://cdn.test/batch.mkv", episode_label="Episode 01"),
            episode("https://cdn.test/batch.mkv", episode_label="Episode 02"),
        ])
        self.assertEqual(rows[0]["playback_id"], rows[1]["playback_id"])
        self.assertEqual(reconcile(self.data)["registered"], 1)
        self.assertEqual(missing_episode_profiles(self.data), [])


class ItRefusesToInventAnything(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.data = self.dir / "data"
        self.data.mkdir()

    def _season(self, items, series="Mystery"):
        directory = self.data / "series" / "bangla" / "a-show"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "season-01.json").write_text(json.dumps({
            "series_name": series, "category": "Bangla",
            "season_number": 1, "items": items,
        }), encoding="utf-8")

    def test_an_id_that_does_not_recompute_is_reported_not_registered(self):
        invented = "ctv_" + "0" * 32
        self._season([{"episode_label": "Episode 01",
                       "url": "https://cdn.test/e01.mkv",
                       "playback_id": invented}])

        report = reconcile(self.data)
        self.assertEqual(report["registered"], 0)
        self.assertEqual(len(report["unexplained"]), 1)
        self.assertEqual(report["unexplained"][0]["series"], "Mystery")
        self.assertNotIn(invented, load_public_catalog_records(self.data))

    def test_a_nested_backup_route_is_not_smuggled_in(self):
        # sanitize_item registers backups too; only ids a card asks for belong.
        backup = {"url": "https://cdn.test/backup.mkv"}
        season_file(self.data, [episode("https://cdn.test/e01.mkv",
                                        backups=[backup])])
        reconcile(self.data)
        records = load_public_catalog_records(self.data)
        self.assertEqual(len(records), 1)
        self.assertNotIn(stable_playback_id(backup), records)

    def test_an_episode_with_no_id_is_not_a_missing_profile(self):
        self._season([{"episode_label": "Episode 01"}], series="No Route")
        self.assertEqual(missing_episode_profiles(self.data), [])

    def test_an_unreadable_season_file_is_skipped_not_fatal(self):
        directory = self.data / "series" / "bangla" / "a-show"
        directory.mkdir(parents=True)
        (directory / "season-01.json").write_text("{ not json",
                                                  encoding="utf-8")
        self.assertEqual(missing_episode_profiles(self.data), [])

    def test_no_series_tree_at_all_is_fine(self):
        self.assertEqual(missing_episode_profiles(self.data), [])
        self.assertEqual(reconcile(self.data)["missing"], 0)


class ThePublisherChecksItself(unittest.TestCase):
    """The scan must not be able to publish this state again."""

    def test_the_publisher_reconciles_and_insists(self):
        import inspect

        from scanner import series

        source = inspect.getsource(series.publish_prepared_series)
        self.assertIn("missing_episode_profiles", source)
        self.assertIn("reconcile", source)
        self.assertIn("raise RuntimeError", source,
                      "an episode that cannot be rebuilt must stop the publish")


if __name__ == "__main__":
    unittest.main()
