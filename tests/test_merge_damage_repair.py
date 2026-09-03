"""A text merge of two generated catalogues must not survive into a scan.

Run #1211 failed on `Ananda TV repeats a backup route`. The card that failed
was not written by a scan: it is byte-identical to its predecessor except that
two backup entries appear twice, two of them share the name `Backup-2`, and
`available_link_count` still reads 4 over five entries. The merge numbers
backups 1..N in a single pass and folds repeats as it goes, so it cannot emit
that.

`git rebase -X theirs origin/main` can. `-X theirs` settles only the hunks that
genuinely conflict; entries differing in non-overlapping hunks inside one
`backups` array are kept from BOTH sides. Two runs each scanning channels from
the same base is all it takes, and channels commits landed at 04:51 and 04:56.

These cover the three things that had to be true and were not:

  * the rule notices a repeat it could not see before - one with no URL to
    compare, or with only a single backup to compare against;
  * the rule is applied where the damage lands: after the rebase, and to
    state/last-good as well as data/;
  * the published data carries no such repeat now.

And one thing that must NOT become true: 19 published cards pair a direct
primary with a proxied backup on the same URL, and those are two delivery
attempts at one address, not one route listed twice.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import unplayable_primary  # noqa: E402

WORKFLOW = ROOT / ".github" / "workflows" / "scan.yml"
RECONCILE = ROOT / "scripts" / "reconcile-generated-counts.py"


class TheRuleSeesWhatItUsedToMiss(unittest.TestCase):
    def test_a_protected_route_repeated_is_still_a_repeat(self):
        """Sanitisation strips the URL from anything needing credentials, so
        two such entries have only their content-addressed id to compare."""
        card = {
            "name": "A",
            "playback_id": "ctv_primary",
            "backups": [
                {"name": "Backup-1", "playback_id": "ctv_same"},
                {"name": "Backup-2", "playback_id": "ctv_other"},
                {"name": "Backup-3", "playback_id": "ctv_same"},
            ],
        }
        dropped = unplayable_primary.dedupe_backup_urls([card])
        self.assertEqual([row["dropped"] for row in dropped], ["Backup-3"])
        self.assertEqual(
            [row["playback_id"] for row in card["backups"]],
            ["ctv_same", "ctv_other"],
        )

    def test_a_single_backup_identical_to_the_primary_goes(self):
        """The old guard skipped any card with fewer than two backups, so the
        one case scripts/validate-pages.py refuses outright went unseen."""
        card = {
            "name": "A",
            "url": "http://one/x.ts",
            "playback_id": "ctv_x",
            "available_link_count": 2,
            "backups": [
                {"name": "Backup-1", "url": "http://one/x.ts",
                 "playback_id": "ctv_x"},
            ],
        }
        dropped = unplayable_primary.dedupe_backup_urls([card])
        self.assertEqual(len(dropped), 1)
        self.assertEqual(card["backups"], [])
        self.assertEqual(card["available_link_count"], 1)

    def test_the_same_url_through_the_proxy_is_kept(self):
        """The regression this must never become.

        Jago News 24, NRB TV, Mr Bean and sixteen more publish a
        `direct_first` primary needing no headers beside one `proxy_first`
        backup on the SAME URL that does. Different headers, so different
        playback ids, so a different route configuration - and the proxied
        attempt is the whole reason workers/playback-proxy exists.
        """
        shared = "https://app.ncare.live/live-orgin/jagonews24.stream/playlist.m3u8"
        card = {
            "name": "Jago News 24",
            "url": shared,
            "proxy_mode": "direct_first",
            "requires_headers": False,
            "playback_id": "ctv_direct",
            "available_link_count": 2,
            "backups": [
                {"name": "Backup-1", "url": shared,
                 "proxy_mode": "proxy_first", "requires_headers": True,
                 "playback_id": "ctv_proxied"},
            ],
        }
        self.assertEqual(unplayable_primary.dedupe_backup_urls([card]), [])
        self.assertEqual(len(card["backups"]), 1)

    def test_the_link_count_is_recomputed_not_adjusted(self):
        """Duronto TV published 2 over a primary and two backups with no
        duplicate involved at all: its primary is protected, and the old
        formula counted a primary with no URL as no link."""
        card = {
            "name": "Duronto TV",
            "playback_id": "ctv_protected",
            "available_link_count": 2,
            "backups": [{"playback_id": "a"}, {"playback_id": "b"}],
        }
        self.assertEqual(unplayable_primary.link_count(card), 3)

    def test_a_metadata_only_card_counts_no_primary(self):
        card = {"metadata_only": True, "backups": [{"url": "u"}]}
        self.assertEqual(unplayable_primary.link_count(card), 1)


class TheRepairRunsWhereTheDamageLands(unittest.TestCase):
    def setUp(self):
        if not WORKFLOW.is_file():
            self.skipTest("no workflow")
        try:
            import yaml
        except ImportError:  # pragma: no cover - yaml ships with the runner
            self.skipTest("pyyaml unavailable")
        self.steps = yaml.safe_load(
            WORKFLOW.read_text(encoding="utf-8")
        )["jobs"]["scan"]["steps"]
        self.names = [str(step.get("name") or "") for step in self.steps]
        self.bodies = {
            str(step.get("name") or ""): str(step.get("run") or "")
            for step in self.steps
        }

    def _repair_name(self):
        match = [n for n in self.names if n.startswith("Repair merge damage")]
        self.assertTrue(match, "the pre-validation repair step is gone")
        return match[0]

    def test_the_tree_is_repaired_before_it_is_judged(self):
        """The published-data checks read a worktree built from HEAD, so a
        duplicate left by an earlier run fails this run before its scanner
        has started - which is how #1211 died with a healthy scanner."""
        self.assertLess(
            self.names.index(self._repair_name()),
            self.names.index("Validate scanner files"),
        )

    def test_the_repair_is_committed_so_the_worktree_can_see_it(self):
        body = self.bodies[self._repair_name()]
        self.assertIn("--fold-only", body)
        self.assertIn("git add data/ state/", body)
        self.assertIn("git commit", body)

    def test_the_repair_also_runs_after_the_rebase(self):
        push = self.bodies["Commit and push updated data"]
        self.assertIn("git rebase -X theirs origin/main", push)
        self.assertIn("scripts/reconcile-generated-counts.py", push)

    def test_the_amend_after_the_rebase_carries_state(self):
        """state/last-good/<slug>.json is merged by the same rebase, and it is
        what sudden-drop protection restores a category FROM."""
        push = self.bodies["Commit and push updated data"]
        self.assertIn("git add data/ state/", push)

    def test_the_catchup_asks_the_remote_how_fresh_a_catalogue_is(self):
        """Without it, every run created before the first catch-up manages to
        push plans the same catch-up, and they all scan channels at once."""
        body = self.bodies["Plan a catch-up for a catalogue the schedule skipped"]
        self.assertIn("--remote-reports-dir", body)
        self.assertIn("origin/main:reports/scan-summary-", body)


class TheReconcilerSharesTheScannersRule(unittest.TestCase):
    def setUp(self):
        self.source = RECONCILE.read_text(encoding="utf-8")

    def test_it_calls_the_rule_rather_than_copying_it(self):
        self.assertIn("unplayable_primary.dedupe_backup_urls", self.source)
        self.assertIn("unplayable_primary.link_count", self.source)

    def test_it_covers_last_good_as_well_as_the_published_copy(self):
        self.assertIn("last-good", self.source)

    def test_fold_only_repairs_a_merged_card_in_both_copies(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data" / "channels").mkdir(parents=True)
            (root / "state" / "last-good").mkdir(parents=True)
            damaged = {
                "category": "Bangla",
                "count": 1,
                "channels": [{
                    "id": "ananda-tv",
                    "name": "Ananda TV",
                    "url": "https://primary/x.m3u8",
                    "playback_id": "ctv_primary",
                    "available_link_count": 4,
                    "backups": [
                        {"name": "Backup-1", "url": "https://a/1.m3u8",
                         "playback_id": "ctv_a"},
                        {"name": "Backup-2", "url": "https://b/2.m3u8",
                         "playback_id": "ctv_b"},
                        {"name": "Backup-2", "url": "https://b/2.m3u8",
                         "playback_id": "ctv_b"},
                        {"name": "Backup-3", "url": "https://a/1.m3u8",
                         "playback_id": "ctv_a"},
                    ],
                }],
            }
            targets = (
                root / "data" / "channels" / "bangla.json",
                root / "state" / "last-good" / "bangla.json",
            )
            for target in targets:
                target.write_text(
                    json.dumps(damaged, indent=2) + "\n", encoding="utf-8"
                )

            result = subprocess.run(
                [sys.executable, str(RECONCILE), "--fold-only", str(root)],
                capture_output=True, text=True, cwd=str(ROOT),
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            for target in targets:
                card = json.loads(
                    target.read_text(encoding="utf-8")
                )["channels"][0]
                self.assertEqual(
                    [row["name"] for row in card["backups"]],
                    ["Backup-1", "Backup-2"],
                    target.name,
                )
                self.assertEqual(card["available_link_count"], 3, target.name)

    def test_fold_only_leaves_a_clean_tree_byte_identical(self):
        """It runs on every scan and its output is committed, so a no-op has
        to write nothing whatsoever - otherwise every run gains a commit
        carrying a new timestamp and nothing else."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data" / "channels").mkdir(parents=True)
            clean = {
                "count": 1,
                "channels": [{
                    "id": "a", "name": "A", "url": "https://p/x",
                    "available_link_count": 2,
                    "backups": [{"name": "Backup-1", "url": "https://q/y"}],
                }],
            }
            target = root / "data" / "channels" / "bangla.json"
            target.write_text(
                json.dumps(clean, indent=2) + "\n", encoding="utf-8"
            )
            before = target.read_bytes()

            subprocess.run(
                [sys.executable, str(RECONCILE), "--fold-only", str(root)],
                capture_output=True, text=True, cwd=str(ROOT), check=True,
            )
            self.assertEqual(target.read_bytes(), before)


class ThePublishedDataAgreesWithBothGates(unittest.TestCase):
    """One data byte used to be a [WARN] in the Pages validator and a dead run
    in the suite. Both now judge the same identity, so both are asserted."""

    def _card_files(self):
        for directory in (ROOT / "data" / "channels",
                          ROOT / "state" / "last-good"):
            if directory.is_dir():
                for path in sorted(directory.glob("*.json")):
                    yield path

    def test_no_card_offers_one_route_configuration_twice(self):
        for path in self._card_files():
            payload = json.loads(path.read_text(encoding="utf-8"))
            for card in payload.get("channels") or []:
                if not isinstance(card, dict):
                    continue
                rows = [card] + [
                    row for row in (card.get("backups") or [])
                    if isinstance(row, dict)
                ]
                identities = [
                    unplayable_primary.exact_route_identity(row)
                    for row in rows
                ]
                with self.subTest(file=path.name, card=card.get("name")):
                    self.assertEqual(
                        len(identities), len(set(identities)),
                        f"{card.get('name')} lists one configuration twice",
                    )

    def test_every_link_count_matches_its_own_list(self):
        for path in self._card_files():
            payload = json.loads(path.read_text(encoding="utf-8"))
            for card in payload.get("channels") or []:
                if not isinstance(card, dict):
                    continue
                if not isinstance(card.get("available_link_count"), int):
                    continue
                with self.subTest(file=path.name, card=card.get("name")):
                    self.assertEqual(
                        card["available_link_count"],
                        unplayable_primary.link_count(card),
                        f"{card.get('name')} miscounts its own links",
                    )


if __name__ == "__main__":
    unittest.main()
