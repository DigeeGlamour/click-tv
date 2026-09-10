"""An episode or a movie in the tree must be playable by the tree it is in.

The event lists already had this guarantee. Episodes and movies did not, and
they carry playback ids on the very same catalogue: measured on the repository,
328 episode ids across 75 season files and 1,667 movie ids across 21 pages.

What happened, from the published history of 2026-09-08:

    10:26:13  movies             wrote data/series/dubbed/muthu-engira-
                                 kaattaan-2026/season-01.json AND the three
                                 records that play it, into data/playback/
                                 bb.json and bc.json
    10:29:27  upcoming-targeted  the records are gone
    10:31:18  today              still gone

The event run never touched series. It regenerated the playback shards it
shares with every other scan mode, from the copy it checked out before the
movies run pushed, and the push step restored its own copy of those files
whole - because scripts/select-restorable-files.py asks who owns a generated
FILE, and a playback shard has no single owner. Sixty scans then failed on 13
published episodes that could not be played, and Today Match stood still at
2026-09-08 10:31 for a day and a half over a fault in a series file.

`scripts/merge-published-events.py::settle_catalogue_in_tree` was written for
exactly this failure and walks `SURFACES`, which is the two event tabs. This
adds the other two kinds, with a repair ordered by how strong the evidence is,
and with the rule that a legitimate episode is never deleted to make a check
pass.
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner.playback_profiles import (  # noqa: E402
    PlaybackProfileCollector,
    load_public_catalog_records,
    merge_public_catalog,
    stable_playback_id,
)
from scanner.series_catalogue import (  # noqa: E402
    PUBLISHED_SURFACES,
    missing_episode_profiles,
    missing_profiles,
    published_items,
    reconcile,
)

SCRIPT = ROOT / "scripts" / "merge-published-events.py"


def load_merge_module():
    spec = importlib.util.spec_from_file_location(
        "merge_published_events_settlement", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def episode_item(url, label="Episode 01", **extra):
    row = {
        "content_kind": "episode",
        "episode_label": label,
        "episode_title": label,
        "name": "A Show — %s" % label,
        "url": url,
        "stream_type": "media",
        "header_profile": "android_tv",
        "requires_headers": False,
        "inherit_manifest_query": False,
        "proxy_mode": "direct_first",
    }
    row.update(extra)
    row["playback_id"] = stable_playback_id(row)
    return row


def movie_item(url, name="A Film", **extra):
    row = {
        "content_kind": "movie",
        "name": name,
        "title": name,
        "url": url,
        "stream_type": "media",
        "header_profile": "android_tv",
        "requires_headers": False,
        "inherit_manifest_query": False,
        "proxy_mode": "direct_first",
    }
    row.update(extra)
    row["playback_id"] = stable_playback_id(row)
    return row


class Tree:
    """A `data/` directory holding published files and a catalogue."""

    def __init__(self, root: Path):
        self.data = root / "data"
        self.data.mkdir(parents=True, exist_ok=True)

    def season(self, items, series="A Show", category="Dubbed", slug="a-show",
               number=1):
        directory = self.data / "series" / category.lower().replace(" ", "-") / slug
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / ("season-%02d.json" % number)
        path.write_text(json.dumps({
            "series_name": series,
            "category": category,
            "season_number": number,
            "items": items,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def movie_page(self, items, category="dubbed", page=1):
        directory = self.data / "movies" / category
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / ("page-%03d.json" % page)
        path.write_text(json.dumps({
            "category": category, "items": items,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def catalogue(self, items, scan_mode="series"):
        collector = PlaybackProfileCollector(scan_mode, "2026-09-08T10:26:13Z")
        for item in items:
            collector.sanitize_item(dict(item), "test")
        merge_public_catalog(self.data, collector)
        return collector

    def empty_catalogue(self):
        shard_dir = self.data / "playback"
        shard_dir.mkdir(parents=True, exist_ok=True)
        (shard_dir / "aa.json").write_text(json.dumps({
            "schema_version": 1, "shard": "aa", "count": 0, "records": {},
        }), encoding="utf-8")


class TreeCase(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.tree = Tree(self.dir)
        self.merge = load_merge_module()

    def settle(self, theirs="HEAD"):
        return self.merge.settle_content_catalogue_in_tree(self.tree.data,
                                                           theirs)

    def missing(self):
        return missing_profiles(self.tree.data)


class TheSurfacesThatCarryAPlaybackId(TreeCase):
    def test_both_kinds_are_walked(self):
        kinds = {kind for kind, _ in PUBLISHED_SURFACES}
        self.assertEqual({"episode", "movie"}, kinds)

    def test_an_episode_is_found(self):
        self.tree.season([episode_item("https://a.example/e1.m3u8")])
        found = [row for row in published_items(self.tree.data)]
        self.assertEqual(1, len(found))
        self.assertEqual("episode", found[0][0])

    def test_a_movie_is_found(self):
        self.tree.movie_page([movie_item("https://a.example/m1.m3u8")])
        found = [row for row in published_items(self.tree.data)]
        self.assertEqual(1, len(found))
        self.assertEqual("movie", found[0][0])

    def test_a_published_episode_with_a_profile_is_not_missing(self):
        item = episode_item("https://a.example/e1.m3u8")
        self.tree.season([item])
        self.tree.catalogue([item])
        self.assertEqual([], self.missing())

    def test_a_published_movie_with_a_profile_is_not_missing(self):
        item = movie_item("https://a.example/m1.m3u8")
        self.tree.movie_page([item])
        self.tree.catalogue([item])
        self.assertEqual([], self.missing())

    def test_a_movie_without_one_is_reported(self):
        """Movies were never checked at all. 1,667 ids ride on the same
        catalogue as the episodes that broke."""
        item = movie_item("https://a.example/m1.m3u8")
        self.tree.movie_page([item])
        self.tree.empty_catalogue()
        rows = self.missing()
        self.assertEqual(1, len(rows))
        self.assertEqual("movie", rows[0]["kind"])
        self.assertEqual("A Film", rows[0]["series"])

    def test_the_episode_only_view_still_answers_for_series_py(self):
        movie = movie_item("https://a.example/m1.m3u8")
        self.tree.movie_page([movie])
        self.tree.empty_catalogue()
        self.assertEqual(1, len(self.missing()))
        self.assertEqual([], missing_episode_profiles(self.tree.data),
                         "scanner/series.py insists on episodes only, and must "
                         "not start failing on a movie it did not write")

    def test_an_unreadable_published_file_is_not_read_as_dangling(self):
        path = self.tree.season([episode_item("https://a.example/e1.m3u8")])
        path.write_text("{not json", encoding="utf-8")
        self.tree.empty_catalogue()
        self.assertEqual([], self.missing())


class TheRunThatBrokeIt(TreeCase):
    """catalogue regenerated by one run, content left by another."""

    def test_a_catalogue_rewritten_without_the_series_records_is_repaired(self):
        items = [episode_item("https://a.example/e%d.m3u8" % n,
                              "Episode %02d" % n) for n in (1, 2, 3)]
        self.tree.season(items)
        self.tree.catalogue(items)
        self.assertEqual([], self.missing())

        # An event run rewrites the shards from a copy that predates the
        # series write: every series record is gone, the season file is not.
        shutil.rmtree(self.tree.data / "playback")
        self.tree.catalogue([movie_item("https://a.example/unrelated.m3u8")],
                            scan_mode="events")
        self.assertEqual(3, len(self.missing()))

        self.settle()
        self.assertEqual([], self.missing())

    def test_the_repair_uses_the_cards_own_configuration(self):
        items = [episode_item("https://a.example/e1.m3u8")]
        self.tree.season(items)
        self.tree.empty_catalogue()
        self.assertTrue(all(row["recomputes"] for row in self.missing()))
        report = reconcile(self.tree.data)
        self.assertEqual(1, report["registered"])
        self.assertEqual([], self.missing())

    def test_content_regenerated_and_catalogue_regenerated_together_is_fine(self):
        items = [episode_item("https://a.example/e1.m3u8")]
        self.tree.season(items)
        self.tree.catalogue(items)
        self.assertEqual([], self.missing())
        self.assertEqual([], self.settle())

    def test_content_regenerated_but_catalogue_untouched(self):
        """The other order: a new episode published against an old catalogue."""
        old = episode_item("https://a.example/e1.m3u8")
        self.tree.catalogue([old])
        new = episode_item("https://a.example/e2.m3u8", "Episode 02")
        self.tree.season([old, new])
        self.assertEqual(1, len(self.missing()))
        self.settle()
        self.assertEqual([], self.missing())

    def test_the_thirteen_episode_class_in_general(self):
        """Not the thirteen: the class. Several series and a movie page, all
        of whose records a later run removed."""
        one = [episode_item("https://a.example/s1e%d.m3u8" % n,
                            "Episode %02d" % n) for n in (1, 2)]
        two = [episode_item("https://a.example/s2e%d.m3u8" % n,
                            "Episode %02d" % n) for n in (1, 2, 3)]
        films = [movie_item("https://a.example/f%d.m3u8" % n, "Film %d" % n)
                 for n in (1, 2)]
        self.tree.season(one, series="One", slug="one")
        self.tree.season(two, series="Two", slug="two", category="Premium")
        self.tree.movie_page(films)
        self.tree.empty_catalogue()
        self.assertEqual(7, len(self.missing()))
        self.settle()
        self.assertEqual([], self.missing())


class NothingIsDeletedToMakeACheckPass(TreeCase):
    def test_an_unrepairable_card_with_a_url_keeps_its_place(self):
        """The id cannot be served and cannot be rebuilt, but the card has a
        real route. The reference goes; the episode stays."""
        item = episode_item("https://a.example/e1.m3u8")
        item["playback_id"] = "ctv_" + "0" * 32
        path = self.tree.season([item])
        self.tree.empty_catalogue()
        self.assertEqual(1, len(self.missing()))
        self.settle()
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(1, len(payload["items"]),
                         "an episode was deleted to make the tree consistent")
        self.assertEqual("", payload["items"][0]["playback_id"])
        self.assertEqual("https://a.example/e1.m3u8",
                         payload["items"][0]["url"])
        self.assertEqual([], self.missing())

    def test_an_unrepairable_card_with_no_route_is_left_for_the_validator(self):
        """Neither playable nor removable. Reported, kept, and refused
        downstream - deleting content to turn a check green is the worse of
        the two failures."""
        item = episode_item("https://a.example/e1.m3u8")
        item["playback_id"] = "ctv_" + "1" * 32
        item.pop("url")
        path = self.tree.season([item])
        self.tree.empty_catalogue()
        self.settle()
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(1, len(payload["items"]))
        self.assertEqual(1, len(self.missing()),
                         "a genuinely dangling reference must still be visible "
                         "to the validator")

    def test_no_catalogue_at_all_settles_nothing(self):
        """CANNOT CHECK is not DEAD. A tree with no catalogue cannot answer
        the question, and answering it anyway would empty the site."""
        items = [episode_item("https://a.example/e%d.m3u8" % n,
                              "Episode %02d" % n) for n in (1, 2, 3)]
        path = self.tree.season(items)
        self.assertEqual([], self.settle())
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(3, len(payload["items"]))
        for item in payload["items"]:
            self.assertTrue(item["playback_id"])

    def test_an_unreadable_shard_stops_a_rewrite_instead_of_dropping_it(self):
        """`merge_public_catalog` rewrites every shard from what it could
        read, so a shard it could not read would be rewritten empty and its
        records lost for good."""
        items = [episode_item("https://a.example/e1.m3u8")]
        self.tree.catalogue(items)
        shard = sorted((self.tree.data / "playback").glob("*.json"))[0]
        shard.write_text("{not json", encoding="utf-8")

        unreadable = []
        load_public_catalog_records(self.tree.data, unreadable)
        self.assertEqual(1, len(unreadable))

        collector = PlaybackProfileCollector("events", "2026-09-08T10:29:27Z")
        collector.sanitize_item(movie_item("https://a.example/m1.m3u8"), "t")
        with self.assertRaises(RuntimeError) as caught:
            merge_public_catalog(self.tree.data, collector)
        self.assertIn("could not be read", str(caught.exception))

    def test_a_membership_test_may_still_ignore_an_unreadable_shard(self):
        """Only a rewrite has to care. Without the list argument the reader
        behaves exactly as it always did."""
        self.tree.catalogue([episode_item("https://a.example/e1.m3u8")])
        shard = sorted((self.tree.data / "playback").glob("*.json"))[0]
        shard.write_text("{not json", encoding="utf-8")
        self.assertEqual({}, load_public_catalog_records(self.tree.data))


class TheRepairIsExact(TreeCase):
    def test_a_card_never_receives_another_cards_profile(self):
        one = episode_item("https://a.example/e1.m3u8", "Episode 01")
        two = episode_item("https://a.example/e2.m3u8", "Episode 02")
        self.assertNotEqual(one["playback_id"], two["playback_id"])
        self.tree.season([one, two])
        self.tree.empty_catalogue()
        self.settle()
        records = load_public_catalog_records(self.tree.data)
        self.assertIn(one["playback_id"], records)
        self.assertIn(two["playback_id"], records)
        self.assertNotEqual(records[one["playback_id"]].get("url"),
                            records[two["playback_id"]].get("url"))
        self.assertEqual("https://a.example/e1.m3u8",
                         records[one["playback_id"]].get("url"))

    def test_records_already_in_the_catalogue_are_left_alone(self):
        keep = episode_item("https://a.example/keep.m3u8", "Episode 01")
        self.tree.catalogue([keep])
        before = dict(load_public_catalog_records(self.tree.data))
        broken = episode_item("https://a.example/e2.m3u8", "Episode 02")
        self.tree.season([keep, broken])
        self.settle()
        after = load_public_catalog_records(self.tree.data)
        self.assertEqual(before[keep["playback_id"]],
                         after[keep["playback_id"]])

    def test_no_duplicate_profile_is_created(self):
        item = episode_item("https://a.example/e1.m3u8")
        self.tree.season([item])
        self.tree.empty_catalogue()
        self.settle()
        first = load_public_catalog_records(self.tree.data)
        self.settle()
        second = load_public_catalog_records(self.tree.data)
        self.assertEqual(first, second, "settling twice changed the catalogue")
        self.assertEqual(1, len(second))

    def test_settling_is_idempotent_and_writes_nothing_when_clean(self):
        item = episode_item("https://a.example/e1.m3u8")
        self.tree.season([item])
        self.tree.catalogue([item])
        self.assertEqual([], self.settle())
        self.assertEqual([], self.settle())

    def test_an_unreferenced_backup_route_cannot_smuggle_a_record_in(self):
        item = episode_item("https://a.example/e1.m3u8")
        item["backups"] = ["https://a.example/backup.m3u8"]
        item["playback_id"] = stable_playback_id(item)
        self.tree.season([item])
        self.tree.empty_catalogue()
        self.settle()
        records = load_public_catalog_records(self.tree.data)
        self.assertEqual([item["playback_id"]], list(records))


class TheWiring(unittest.TestCase):
    SOURCE = SCRIPT.read_text(encoding="utf-8")

    def test_the_settlement_runs_on_every_push_path(self):
        main = self.SOURCE.split("def main(", 1)[1]
        self.assertIn("settle_catalogue_in_tree(ROOT / args.data_dir", main)
        self.assertIn("settle_content_catalogue_in_tree(ROOT / args.data_dir",
                      main)
        self.assertLess(main.index("settle_catalogue_in_tree("),
                        main.index("return status"),
                        "the settlement must not sit behind an early return")

    def test_the_push_step_calls_the_merge_script_before_committing(self):
        workflow = (ROOT / ".github" / "workflows" / "scan.yml").read_text(
            encoding="utf-8")
        block = workflow.split("rebase_keeping_our_generated_files() {", 1)[1]
        block = block.split("PYTHON_BIN=", 1)[0]
        self.assertIn("scripts/merge-published-events.py", block)
        self.assertLess(block.index("scripts/merge-published-events.py"),
                        block.index("git add -A data"),
                        "the settlement's writes have to reach the commit")

    def test_the_repair_order_is_evidence_first(self):
        block = self.SOURCE.split(
            "def settle_content_catalogue_in_tree(", 1)[1].split(
            "def _content_catalogue(", 1)[0]
        self.assertLess(block.index(".reconcile(data_dir)"),
                        block.index("records_from(theirs_ref"),
                        "a card that proves its own record must not need the "
                        "other side's tree")
        self.assertLess(block.index("records_from(theirs_ref"),
                        block.index("_clear_dangling_ids("))

    def test_the_content_settlement_never_deletes_an_item(self):
        block = self.SOURCE.split(
            "def _clear_dangling_ids(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn('item["playback_id"] = ""', block)
        for forbidden in ("items.remove", "del payload", "kept.append",
                          "items.pop"):
            self.assertNotIn(forbidden, block)


class TheRepositoryItselfIsConsistent(unittest.TestCase):
    """The invariant of section 2, asked of every kind rather than one."""

    def test_every_published_episode_and_movie_can_be_played(self):
        rows = missing_profiles(ROOT / "data")
        detail = ", ".join("%s / %s" % (row.get("series"), row.get("episode"))
                           for row in rows[:6])
        self.assertEqual(
            rows, [],
            "%d published card(s) cannot be played: %s" % (len(rows), detail))

    def test_there_are_movies_and_episodes_to_check(self):
        kinds = {kind for kind, _, _, _ in published_items(ROOT / "data")}
        self.assertEqual({"episode", "movie"}, kinds)


if __name__ == "__main__":
    unittest.main()
