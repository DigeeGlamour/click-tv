"""Two runs publishing at once must not cost a fixture.

Whole-file restore stopped the duplicates and started the opposite fault,
because a run publishes the list it checked out when it started:

    1b0adbc0d  17:09:38  upcoming-targeted   94 fixtures   <- the trigger's base
    9045f6e11  17:08:29  today              102 fixtures   <- a full scan
    53f5e3604  17:14:02  upcoming-targeted   94 fixtures   <- all 8 gone

The targeted list was byte for byte its own checkout: it kept 0 of the 8 the
full scan had found. Over 31 publishes on 2026-09-06, 41 fixtures flickered in
and out this way, one of them absent for 19 minutes.

The replay below uses those three commits. The rest states the merge rule.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import unittest
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

SCRIPT = os.path.join(ROOT, "scripts", "merge-published-events.py")
GIT = __import__("shutil").which("git")

BASE, THEIRS, OURS = "1b0adbc0d", "9045f6e11", "53f5e3604"


def load_module():
    spec = importlib.util.spec_from_file_location("merge_published_events", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


merge = load_module()


def have(commit):
    if GIT is None:
        return False
    return subprocess.run([GIT, "cat-file", "-e", "%s^{commit}" % commit],
                          cwd=ROOT, capture_output=True).returncode == 0


def card(event_id, **overrides):
    item = {"id": event_id, "name": event_id.replace("-vs-", " vs ").title(),
            "fixture_id": "provider:%s|test league|2026-09-06" % event_id,
            "start_time": "2026-09-09T12:00:00+00:00"}
    item.update(overrides)
    return item


class TheMergeRule(unittest.TestCase):
    """base, ours, theirs -> one list, keyed on the fixture."""

    def merge(self, base, ours, theirs):
        return merge.three_way(base, ours, theirs)

    def test_a_fixture_only_they_found_is_kept(self):
        merged, stats = self.merge([], [card("a")], [card("a"), card("b")])
        self.assertEqual([item["id"] for item in merged], ["a", "b"])
        self.assertEqual(stats["they_added"], 1)

    def test_a_fixture_this_run_retired_is_not_offered_back(self):
        """In the base and in theirs, absent from ours: a decision, not a gap."""
        merged, stats = self.merge([card("a"), card("b")], [card("a")],
                                   [card("a"), card("b")])
        self.assertEqual([item["id"] for item in merged], ["a"])
        self.assertEqual(stats["we_retired"], 1)

    def test_a_fixture_this_run_rescanned_keeps_this_run_s_copy(self):
        merged, _ = self.merge(
            [card("a", url="")],
            [card("a", url="https://ours.test/a.m3u8")],
            [card("a", url="https://theirs.test/a.m3u8")])
        self.assertEqual(merged[0]["url"], "https://ours.test/a.m3u8")

    def test_a_fixture_this_run_only_carried_takes_their_rescan(self):
        """Ours is identical to the base - we did not look at it. Theirs is
        different, so theirs is the copy with a scan behind it."""
        merged, stats = self.merge(
            [card("a", url="")], [card("a", url="")],
            [card("a", url="https://theirs.test/a.m3u8")])
        self.assertEqual(merged[0]["url"], "https://theirs.test/a.m3u8")
        self.assertEqual(stats["theirs_rescanned"], 1)

    def test_a_fixture_neither_side_touched_stays_once(self):
        merged, _ = self.merge([card("a")], [card("a")], [card("a")])
        self.assertEqual([item["id"] for item in merged], ["a"])

    def test_publication_order_is_ours_then_what_they_added(self):
        merged, _ = self.merge([], [card("x"), card("y")],
                               [card("z"), card("x")])
        self.assertEqual([item["id"] for item in merged], ["x", "y", "z"])

    def test_two_unkeyable_cards_are_not_the_same_card(self):
        left = {"name": "Something Live"}
        right = {"name": "Something Else Live"}
        self.assertNotEqual(merge.key_of(left), merge.key_of(right))

    def test_the_key_is_the_scanners_own_fixture_key(self):
        from scanner.targeted_scan import fixture_key
        item = card("alpha-vs-beta")
        self.assertEqual(merge.key_of(item), "k:" + fixture_key(item))


class TheClockGate(unittest.TestCase):
    """A fixture the other side offers is still checked against its kickoff."""

    NOW = datetime(2026, 9, 6, 17, 14, tzinfo=timezone.utc)

    def test_a_future_fixture_is_accepted(self):
        item = card("a", start_time=(self.NOW + timedelta(hours=3)).isoformat())
        self.assertTrue(merge.still_publishable(item, self.NOW, 10))

    def test_a_fixture_well_past_its_kickoff_is_not(self):
        item = card("a", start_time=(self.NOW - timedelta(hours=3)).isoformat())
        self.assertFalse(merge.still_publishable(item, self.NOW, 10))

    def test_the_grace_is_the_one_in_the_settings_file(self):
        from pathlib import Path
        settings = Path(ROOT) / "config" / "settings.json"
        declared = json.loads(settings.read_text(encoding="utf-8"))
        self.assertEqual(
            merge.past_grace_minutes(settings),
            declared["events"]["upcoming_past_grace_minutes"])

    def test_a_card_with_no_kickoff_is_not_judged_on_one(self):
        self.assertTrue(merge.still_publishable({"id": "x"}, self.NOW, 10))


@unittest.skipUnless(have(BASE) and have(THEIRS) and have(OURS),
                     "the 2026-09-06 17:14 commits are not in this clone")
class TheRealSeventeenFourteenLoss(unittest.TestCase):
    """The eight fixtures that went, replayed."""

    @classmethod
    def setUpClass(cls):
        path = "data/upcoming.json"
        cls.base = merge.items_of(merge.blob(BASE, path))
        cls.ours = merge.items_of(merge.blob(OURS, path))
        cls.theirs = merge.items_of(merge.blob(THEIRS, path))

    def test_the_commits_are_the_ones_that_shipped(self):
        self.assertEqual(len(self.base), 94)
        self.assertEqual(len(self.ours), 94)
        self.assertEqual(len(self.theirs), 102)

    def test_what_shipped_was_the_triggers_own_checkout(self):
        """Which is the whole fault: it published what it had read, not what
        was there."""
        self.assertEqual({merge.key_of(item) for item in self.ours},
                         {merge.key_of(item) for item in self.base})

    def test_the_merge_recovers_every_one_of_the_eight(self):
        merged, stats = merge.three_way(self.base, self.ours, self.theirs)
        base_keys = {merge.key_of(item) for item in self.base}
        merged_keys = {merge.key_of(item) for item in merged}
        found = [item for item in self.theirs
                 if merge.key_of(item) not in base_keys]
        self.assertEqual(len(found), 8)
        for item in found:
            self.assertIn(merge.key_of(item), merged_keys, item.get("name"))
        self.assertEqual(len(merged), 102)
        self.assertEqual(stats["they_added"], 8)

    def test_the_merge_loses_nothing_this_run_published(self):
        merged, _ = merge.three_way(self.base, self.ours, self.theirs)
        merged_keys = {merge.key_of(item) for item in merged}
        for item in self.ours:
            self.assertIn(merge.key_of(item), merged_keys)

    def test_the_merge_creates_no_duplicate(self):
        merged, _ = merge.three_way(self.base, self.ours, self.theirs)
        keys = [merge.key_of(item) for item in merged]
        ids = [str(item.get("id")) for item in merged if item.get("id")]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(len(ids), len(set(ids)))

    def test_no_source_id_and_no_stream_url_is_lost(self):
        merged, _ = merge.three_way(self.base, self.ours, self.theirs)

        def gather(items):
            sources, urls = set(), set()
            for item in items:
                for value in item.get("source_ids") or ():
                    sources.add(str(value))
                for channel in item.get("channels") or ():
                    if isinstance(channel, dict):
                        if channel.get("url"):
                            urls.add(str(channel["url"]))
                        for backup in channel.get("backups") or ():
                            if isinstance(backup, dict) and backup.get("url"):
                                urls.add(str(backup["url"]))
            return sources, urls

        for side in (self.ours, self.theirs):
            sources, urls = gather(side)
            merged_sources, merged_urls = gather(merged)
            self.assertEqual(sources - merged_sources, set())
            self.assertEqual(urls - merged_urls, set())


@unittest.skipUnless(GIT is not None, "git is required")
class TheHistoricalDuplicateMergeStaysFixed(unittest.TestCase):
    """The merge that put two cards on the site, through the new rule."""

    COMMITS = ("9bb67f71e", "48c9831c8", "e4c2ce3db")

    def setUp(self):
        if not all(have(commit) for commit in self.COMMITS):
            self.skipTest("the 2026-09-06 10:00 commits are not in this clone")

    def test_neither_tab_gains_a_duplicate(self):
        base_sha, theirs_sha, ours_sha = self.COMMITS
        for name in ("today-match", "upcoming"):
            path = "data/%s.json" % name
            merged, _ = merge.three_way(
                merge.items_of(merge.blob(base_sha, path)),
                merge.items_of(merge.blob(ours_sha, path)),
                merge.items_of(merge.blob(theirs_sha, path)))
            ids = [str(item.get("id")) for item in merged if item.get("id")]
            with self.subTest(surface=name):
                self.assertEqual(len(ids), len(set(ids)))

    def test_this_runs_own_scan_is_not_thrown_away(self):
        base_sha, theirs_sha, ours_sha = self.COMMITS
        path = "data/today-match.json"
        ours = merge.items_of(merge.blob(ours_sha, path))
        merged, _ = merge.three_way(
            merge.items_of(merge.blob(base_sha, path)), ours,
            merge.items_of(merge.blob(theirs_sha, path)))
        self.assertEqual(len(ours), 21)
        keys = {merge.key_of(item) for item in merged}
        for item in ours:
            self.assertIn(merge.key_of(item), keys)


class ACardAndTheRecordThatPlaysItMoveTogether(unittest.TestCase):
    """The fault the merge shipped with, and what closed it.

    Run 1411 took four Today cards from the other run. `AFC Toronto W vs
    Calgary Wild W` referred to a playback id that lived only in the other
    run's catalogue, the next scan republished the card, and
    scripts/validate-pages.py refused the whole publish:

        [ERROR] today_match event #19 playback_id catalogue-এ নেই:
                AFC Toronto W vs Calgary Wild W

    Run 1413 failed on it. The Today scan seven minutes later rebuilt the list
    from its own sources and the reference went with it - so it self-healed,
    which is exactly why it needed catching rather than waiting for.
    """

    ID_A = "ctv_" + "a" * 32
    ID_B = "ctv_" + "b" * 32

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = os.path.join(self.tmp.name, "data")
        os.makedirs(os.path.join(self.data, "playback"))
        self.write_shard("aa", {})
        self.write_shard("bb", {})
        from pathlib import Path
        self.data_path = Path(self.data)

    def write_shard(self, shard, records):
        path = os.path.join(self.data, "playback", "%s.json" % shard)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"shard": shard, "count": len(records),
                       "records": records}, handle)

    def their_catalogue(self, records):
        """Stand in for `git show theirs:data/playback/<shard>.json`."""
        original = merge.blob

        def fake(ref, path):
            if path.startswith("data/playback/"):
                shard = path.rsplit("/", 1)[-1].split(".")[0]
                held = {k: v for k, v in records.items()
                        if merge.catalog_shard_for(k) == shard}
                return {"records": held}
            return original(ref, path)

        merge.blob = fake
        self.addCleanup(setattr, merge, "blob", original)

    def test_every_playback_id_on_a_card_is_found(self):
        item = {"playback_id": self.ID_A,
                "backups": [{"playback_id": self.ID_B}],
                "channels": [{"backups": [{"playback_id": "ctv_" + "c" * 32}]}]}
        self.assertEqual(
            merge.playback_ids_in(item),
            [self.ID_A, self.ID_B, "ctv_" + "c" * 32])

    def test_the_record_is_carried_across_with_the_card(self):
        self.their_catalogue({self.ID_A: {"url": "https://theirs.test/a.m3u8"}})
        added = [card("a-vs-b", playback_id=self.ID_A)]
        kept, written = merge.keep_the_catalogue_honest(
            self.data_path, "theirs", added)
        self.assertEqual([item["id"] for item in kept], ["a-vs-b"])
        self.assertTrue(written)
        self.assertIn(self.ID_A, merge.catalogue_ids(self.data_path))

    def test_an_id_this_tree_already_serves_needs_nothing(self):
        self.write_shard("aa", {self.ID_A: {"url": "https://ours.test/a.m3u8"}})
        self.their_catalogue({})
        added = [card("a-vs-b", playback_id=self.ID_A)]
        kept, written = merge.keep_the_catalogue_honest(
            self.data_path, "theirs", added)
        self.assertEqual(len(kept), 1)
        self.assertEqual(written, [])

    def test_a_card_whose_only_route_cannot_be_served_is_dropped(self):
        """Better a fixture missing than a publish refused for every card."""
        self.their_catalogue({})
        added = [card("a-vs-b", playback_id=self.ID_A)]
        kept, _ = merge.keep_the_catalogue_honest(
            self.data_path, "theirs", added)
        self.assertEqual(kept, [])

    def test_a_card_with_a_real_url_keeps_its_place_without_the_id(self):
        self.their_catalogue({})
        added = [card("a-vs-b", playback_id=self.ID_A,
                      url="https://direct.test/a.m3u8")]
        kept, _ = merge.keep_the_catalogue_honest(
            self.data_path, "theirs", added)
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["playback_id"], "")
        self.assertEqual(kept[0]["url"], "https://direct.test/a.m3u8")

    def test_a_metadata_only_card_is_untouched(self):
        self.their_catalogue({})
        added = [card("a-vs-b")]
        kept, written = merge.keep_the_catalogue_honest(
            self.data_path, "theirs", added)
        self.assertEqual(len(kept), 1)
        self.assertEqual(written, [])

    def test_the_shard_key_is_the_scanners_own(self):
        from scanner.playback_profiles import catalog_shard_for
        self.assertEqual(merge.catalog_shard_for(self.ID_A),
                         catalog_shard_for(self.ID_A))


class ARunThatNeverScannedEventsIsNotASide(unittest.TestCase):
    """A catalogue run holds a stale copy of both lists. Merging that with the
    live one would offer back every fixture the live one has retired since."""

    def test_the_script_asks_the_same_receipt_the_selector_asks(self):
        with open(SCRIPT, encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn("scanned_events", source)
        self.assertIn("select-restorable-files.py", source)

    def test_the_two_scripts_share_one_definition_of_it(self):
        """Imported, not copied: two answers to one question is the fault this
        whole family keeps producing."""
        code = merge._selector().scanned_events.__code__
        self.assertEqual(
            os.path.abspath(code.co_filename),
            os.path.join(ROOT, "scripts", "select-restorable-files.py"))

    def test_a_catalogue_run_merges_nothing(self):
        """The receipt is unchanged since it branched, so the script stops
        before it can offer a stale list back."""
        selector = merge._selector()
        self.assertFalse(selector.scanned_events("HEAD", "HEAD"))


if __name__ == "__main__":
    unittest.main()
