"""The push step must not let a rebase merge two regenerated collections.

WHAT WENT WRONG, AND WHY A TEST EXISTS FOR IT

`git rebase -X theirs origin/main` is a three-way merge per file. `-X theirs`
settles only the hunks that CONFLICT, so where two runs edited different
regions of the same pretty-printed JSON array, git keeps both - and a fixture
ends up on two cards. On 2026-09-06 that shipped four times in half an hour:

    90edb2faf  today   25 items under "count": 24   myanmar-u20-vs-maldives-u20
    69e1afcdd  movies  17 items under "count": 8    two more, rebased onto it
    f90b0e9ee  today   17 items, count 17           fim-motojunior-...-valencia
    ba0608bd3  today   23 items under "count": 22   fc-groningen-vs-fc-twente

f90b0e9ee is the one that matters most for what is asserted below: its count
was RIGHT. A count check alone would have passed it. Only an id check catches
that one, which is why the detector fails on duplicate ids and merely reports
count drift.

These tests run the workflow's own bash, extracted from .github/workflows/
scan.yml, against real published files taken from git history. Nothing here is
a paraphrase of the shipped code, and nothing here is synthetic data: if the
function in scan.yml changes, these tests change with it or they fail.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "scan.yml"
FIXTURES = PROJECT_ROOT / "tests" / "fixtures" / "rebase-merge"
DETECTOR = PROJECT_ROOT / "scripts" / "detect-merge-corruption.py"

BASH = shutil.which("bash")
GIT = shutil.which("git")

# The historical merge, by commit. base is what the run checked out, origin is
# what another run pushed while it worked, mine is what its own scanner wrote.
HISTORICAL = {
    "base": ("9bb67f71e", 8),
    "origin": ("48c9831c8", 18),
    "mine": ("e4c2ce3db", 21),
}

# What the unguarded rebase produces from those three. Measured, not chosen.
OLD_BROKEN_ITEMS = 23
OLD_BROKEN_COUNT = 21
OLD_BROKEN_DUPLICATES = {
    "england-w-vs-ireland-w",
    "england-women-vs-ireland-women",
}


def _push_step_run() -> str:
    """The `run:` body of the push step, read from the workflow itself."""
    text = WORKFLOW.read_text(encoding="utf-8")
    marker = "      - name: Commit and push updated data\n"
    marker_crlf = marker.replace("\n", "\r\n")
    if marker_crlf in text:
        text = text.replace("\r\n", "\n")
    start = text.index(marker)
    body = text[start:]
    # up to the next step at the same indentation
    end = body.index("\n      - name:", 1)
    step = body[:end]
    run_at = step.index("        run: |\n") + len("        run: |\n")
    lines = step[run_at:].splitlines()
    return "\n".join(line[10:] if line.startswith(" " * 10) else line for line in lines)


def _extract_function(name: str) -> str:
    """One shell function, verbatim, from the push step."""
    run = _push_step_run()
    start = run.index(f"{name}() {{")
    depth = 0
    for index in range(start, len(run)):
        if run[index] == "{":
            depth += 1
        elif run[index] == "}":
            depth -= 1
            if depth == 0:
                return run[start : index + 1]
    raise AssertionError(f"{name} is not closed in scan.yml")


def _items(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["items"]


def _declared_count(path: Path) -> int:
    return json.loads(path.read_text(encoding="utf-8"))["count"]


def _duplicate_ids(path: Path) -> set[str]:
    ids = [str(item.get("id") or "") for item in _items(path)]
    return {value for value, times in Counter(ids).items() if value and times > 1}


class _Repo:
    """A throwaway repository shaped like the runner's checkout.

    main is the run's own branch. refs/remotes/origin/main is what somebody
    else pushed while it was scanning. Both descend from one base commit, which
    is what `git merge-base` in the shipped function reads.
    """

    def __init__(self, root: Path):
        self.root = root
        self._git("init", "-q", ".")
        self._git("config", "user.email", "test@example.invalid")
        self._git("config", "user.name", "rebase integrity test")
        self._git("config", "core.autocrlf", "false")
        self._git("config", "commit.gpgsign", "false")
        self._git("checkout", "-q", "-b", "main")

    def _git(self, *args: str) -> str:
        result = subprocess.run(
            [GIT, *args],
            cwd=self.root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            raise AssertionError(
                "git %s failed:\n%s\n%s" % (" ".join(args), result.stdout, result.stderr)
            )
        return result.stdout

    def write(self, relative: str, payload: bytes | str) -> None:
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(payload, str):
            target.write_text(payload, encoding="utf-8", newline="\n")
        else:
            target.write_bytes(payload)

    def commit(self, message: str) -> str:
        self._git("add", "-A")
        self._git("commit", "-q", "-m", message)
        return self._git("rev-parse", "HEAD").strip()

    def build(self, base: dict, origin: dict, mine: dict) -> None:
        """base -> (origin side, published) and (our side, in flight)."""
        for name, payload in base.items():
            self.write(name, payload)
        base_sha = self.commit("base")

        self._git("branch", "originside")
        self._git("checkout", "-q", "originside")
        for name, payload in origin.items():
            self.write(name, payload)
        if origin:
            origin_sha = self.commit("what another run pushed")
        else:
            origin_sha = base_sha
        self._git("update-ref", "refs/remotes/origin/main", origin_sha)

        self._git("checkout", "-q", "main")
        self._git("reset", "-q", "--hard", base_sha)
        for name, payload in mine.items():
            self.write(name, payload)
        self.commit("what this run scanned")

    def run_shipped_function(self) -> subprocess.CompletedProcess:
        script = (
            "set -euo pipefail\n"
            + _extract_function("rebase_keeping_our_generated_files")
            + "\nrebase_keeping_our_generated_files\n"
        )
        # Outside the repository under test: a stray untracked file would show
        # up in `git status` and make the "nothing left behind" assertion lie.
        script_path = self.root.parent / f"{self.root.name}-shipped.sh"
        script_path.write_text(script, encoding="utf-8", newline="\n")
        return subprocess.run(
            [BASH, str(script_path)],
            cwd=self.root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def rebase_the_old_way(self) -> None:
        self._git("rebase", "-X", "theirs", "origin/main")


@unittest.skipIf(BASH is None or GIT is None, "bash and git are required")
class TheHistoricalMergeThatShipped(unittest.TestCase):
    """The real 2026-09-06 files, the real commands, both behaviours."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.payloads = {}
        for role, (_commit, expected_items) in HISTORICAL.items():
            path = FIXTURES / f"{role}-today-match.json"
            cls.payloads[role] = path.read_bytes()
            got = len(json.loads(cls.payloads[role].decode("utf-8"))["items"])
            assert got == expected_items, f"{role} fixture drifted: {got}"

    def _tree(self, name: str) -> Path:
        import tempfile

        root = Path(tempfile.mkdtemp(prefix=f"rebase-{name}-"))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        return root

    def _repo(self, name: str) -> _Repo:
        repo = _Repo(self._tree(name))
        repo.build(
            base={"data/today-match.json": self.payloads["base"]},
            origin={"data/today-match.json": self.payloads["origin"]},
            mine={"data/today-match.json": self.payloads["mine"]},
        )
        return repo

    def test_the_old_rebase_still_produces_the_duplicates_that_shipped(self):
        """Pin the defect. If this ever stops failing, the fix is untestable."""
        repo = self._repo("old")
        repo.rebase_the_old_way()
        merged = repo.root / "data" / "today-match.json"

        self.assertEqual(len(_items(merged)), OLD_BROKEN_ITEMS)
        self.assertEqual(_declared_count(merged), OLD_BROKEN_COUNT)
        self.assertEqual(_duplicate_ids(merged), OLD_BROKEN_DUPLICATES)

    def test_the_shipped_function_keeps_this_runs_collection_whole(self):
        repo = self._repo("new")
        result = repo.run_shipped_function()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        restored = repo.root / "data" / "today-match.json"
        # Byte for byte what this run's scanner wrote. Not merged, not repaired
        # into looking right - the same file.
        self.assertEqual(restored.read_bytes(), self.payloads["mine"])
        self.assertEqual(len(_items(restored)), HISTORICAL["mine"][1])
        self.assertEqual(_declared_count(restored), len(_items(restored)))
        self.assertEqual(_duplicate_ids(restored), set())

    def test_the_restore_is_committed_not_just_left_in_the_worktree(self):
        """A push sends commits. A repaired worktree that was never committed
        would push the merged version and look fine locally."""
        repo = self._repo("committed")
        repo.run_shipped_function()
        committed = repo._git("show", "HEAD:data/today-match.json")
        self.assertEqual(
            committed.encode("utf-8").replace(b"\r\n", b"\n"),
            self.payloads["mine"].replace(b"\r\n", b"\n"),
        )
        self.assertEqual(repo._git("status", "--porcelain").strip(), "")

    def test_the_other_runs_commit_is_still_the_parent(self):
        """Keeping our file whole must not throw away their commit."""
        repo = self._repo("parent")
        origin_sha = repo._git("rev-parse", "refs/remotes/origin/main").strip()
        repo.run_shipped_function()
        parents = repo._git("rev-list", "--parents", "-n", "1", "HEAD").split()
        self.assertIn(origin_sha, parents[1:])


@unittest.skipIf(BASH is None or GIT is None, "bash and git are required")
class TwoRunsWritingAtOnce(unittest.TestCase):
    """A, B, C: which side of a concurrent write wins, file by file."""

    def _repo(self, base, origin, mine) -> _Repo:
        import tempfile

        root = Path(tempfile.mkdtemp(prefix="rebase-concurrent-"))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        repo = _Repo(root)
        repo.build(base=base, origin=origin, mine=mine)
        return repo

    @staticmethod
    def _collection(*ids: str) -> str:
        items = ",\n".join(
            '    {\n      "id": "%s",\n      "name": "%s"\n    }' % (i, i) for i in ids
        )
        return '{\n  "count": %d,\n  "items": [\n%s\n  ]\n}\n' % (len(ids), items)

    def test_A_both_runs_wrote_the_same_file_so_ours_wins_whole(self):
        repo = self._repo(
            base={"data/today-match.json": self._collection("a", "b")},
            origin={"data/today-match.json": self._collection("a", "b", "remote")},
            mine={"data/today-match.json": self._collection("a", "b", "ours", "extra")},
        )
        result = repo.run_shipped_function()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        merged = repo.root / "data" / "today-match.json"
        ids = [item["id"] for item in _items(merged)]
        self.assertEqual(ids, ["a", "b", "ours", "extra"])
        # The union - what the bug produced - would have kept "remote" too.
        self.assertNotIn("remote", ids)
        self.assertEqual(_declared_count(merged), len(ids))
        self.assertEqual(_duplicate_ids(merged), set())

    def test_B_each_run_wrote_a_different_file_so_both_survive(self):
        repo = self._repo(
            base={
                "data/today-match.json": self._collection("a"),
                "data/upcoming.json": self._collection("u"),
            },
            origin={"data/upcoming.json": self._collection("u", "remote-only")},
            mine={"data/today-match.json": self._collection("a", "ours-only")},
        )
        result = repo.run_shipped_function()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        ours = [i["id"] for i in _items(repo.root / "data" / "today-match.json")]
        theirs = [i["id"] for i in _items(repo.root / "data" / "upcoming.json")]
        self.assertEqual(ours, ["a", "ours-only"])
        self.assertEqual(theirs, ["u", "remote-only"])

    def test_C_a_file_this_run_never_touched_keeps_the_remote_version(self):
        repo = self._repo(
            base={
                "data/today-match.json": self._collection("a"),
                "data/channels/sports.json": self._collection("chan"),
            },
            origin={"data/channels/sports.json": self._collection("chan", "chan-new")},
            mine={"data/today-match.json": self._collection("a", "ours")},
        )
        result = repo.run_shipped_function()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        channels = [i["id"] for i in _items(repo.root / "data" / "channels" / "sports.json")]
        self.assertEqual(channels, ["chan", "chan-new"])

    def test_a_run_that_generated_nothing_leaves_the_remote_alone(self):
        repo = self._repo(
            base={"data/today-match.json": self._collection("a")},
            origin={"data/today-match.json": self._collection("a", "remote")},
            mine={"notes.txt": "this run wrote no generated file\n"},
        )
        result = repo.run_shipped_function()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        ids = [i["id"] for i in _items(repo.root / "data" / "today-match.json")]
        self.assertEqual(ids, ["a", "remote"])
        self.assertIn("regenerated 0 file(s)", result.stdout)

    def test_a_file_only_this_run_created_survives_the_rebase(self):
        repo = self._repo(
            base={"data/today-match.json": self._collection("a")},
            origin={"data/today-match.json": self._collection("a", "remote")},
            mine={
                "data/today-match.json": self._collection("a", "ours"),
                "reports/brand-new.json": '{"count": 0, "items": []}\n',
            },
        )
        result = repo.run_shipped_function()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((repo.root / "reports" / "brand-new.json").is_file())


class TheDetectorRunsBeforeAnythingIsTidiedAway(unittest.TestCase):
    """D, E, F: what the pre-reconcile look actually reports."""

    def _tree(self) -> Path:
        import tempfile

        root = Path(tempfile.mkdtemp(prefix="detector-"))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        (root / "data").mkdir()
        (root / "scripts").mkdir()
        shutil.copy2(DETECTOR, root / "scripts" / DETECTOR.name)
        return root

    def _run(self, collections: dict[str, str]) -> subprocess.CompletedProcess:
        import sys

        root = self._tree()
        for name, payload in collections.items():
            target = root / "data" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(payload, encoding="utf-8", newline="\n")
        return subprocess.run(
            [sys.executable, str(root / "scripts" / DETECTOR.name)],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    @staticmethod
    def _collection(ids, declared=None) -> str:
        items = ",\n".join('    {"id": "%s"}' % i for i in ids)
        count = len(ids) if declared is None else declared
        return '{\n  "count": %d,\n  "items": [\n%s\n  ]\n}\n' % (count, items)

    def test_D_a_count_mismatch_is_reported_and_does_not_stop_the_push(self):
        result = self._run({"today-match.json": self._collection(["a", "b", "c"], declared=2)})
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("3 items under \"count\": 2", result.stdout)
        self.assertIn("::warning::", result.stdout)

    def test_D_the_mismatch_is_never_repaired_silently(self):
        """reconcile-generated-counts.py fixes it afterwards. The point of the
        detector is that the fix is never the first anyone hears of it."""
        payload = self._collection(["a", "b", "c"], declared=2)
        result = self._run({"today-match.json": payload})
        self.assertIn("fingerprint of a rebase merge", result.stdout)

    def test_E_a_duplicate_id_fails_even_when_the_count_already_agrees(self):
        """Commit f90b0e9ee: 17 items, count 17, and a duplicate anyway."""
        payload = self._collection(["a", "b", "a"])
        self.assertEqual(json.loads(payload)["count"], 3)
        result = self._run({"today-match.json": payload})
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("::error::", result.stdout)
        self.assertIn("'a'", result.stdout)
        self.assertIn("[1, 3]", result.stdout)

    def test_F_a_clean_tree_says_so_and_changes_nothing(self):
        result = self._run(
            {
                "today-match.json": self._collection(["a", "b"]),
                "upcoming.json": self._collection(["u"]),
            }
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("every collection agrees with its own count", result.stdout)
        self.assertIn("no duplicate ids", result.stdout)
        self.assertNotIn("snapshot slot that is not the live one", result.stdout)
        self.assertNotIn("::error::", result.stdout)
        self.assertNotIn("::warning::", result.stdout)

    def test_the_historical_damage_would_have_been_refused(self):
        """Every one of the four commits that shipped, replayed through it."""
        for name, ids, declared in (
            ("90edb2faf", ["x", "myanmar", "myanmar", "y"], 3),
            ("69e1afcdd", ["uipm", "uipm", "madrid", "madrid"], 2),
            ("f90b0e9ee", ["fim", "fim"], 2),
            ("ba0608bd3", ["groningen", "groningen", "z"], 2),
        ):
            with self.subTest(commit=name):
                result = self._run(
                    {"today-match.json": self._collection(ids, declared=declared)}
                )
                self.assertEqual(result.returncode, 1, result.stdout)


class ARotatingSnapshotSlotMustNotDeadlockThePush(unittest.TestCase):
    """snapshot_publish.py cycles s0/s1/s2 and rewrites a slot in full before
    it is served, so a duplicate left behind in a slot that is not live cannot
    reach anyone. Failing the push on one would be unrecoverable: the only
    thing that rewrites that slot is a run that pushes."""

    CLEAN = '{"count": 2, "items": [{"id": "a"}, {"id": "b"}]}\n'
    DAMAGED = '{"count": 3, "items": [{"id": "a"}, {"id": "b"}, {"id": "a"}]}\n'

    def _tree(self, prefix: str) -> Path:
        import tempfile

        root = Path(tempfile.mkdtemp(prefix=prefix))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        (root / "scripts").mkdir()
        shutil.copy2(DETECTOR, root / "scripts" / DETECTOR.name)
        return root

    @staticmethod
    def _write(path: Path, payload: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload, encoding="utf-8", newline="\n")

    @staticmethod
    def _detect(root: Path) -> subprocess.CompletedProcess:
        import sys

        return subprocess.run(
            [sys.executable, str(root / "scripts" / DETECTOR.name)],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def _run(self, live_slot: str, damaged_slot: str) -> subprocess.CompletedProcess:
        root = self._tree("slots-")
        data = root / "data"
        self._write(data / "today-match.json", self.CLEAN)
        self._write(data / "snapshots" / live_slot / "today-match.json", self.CLEAN)
        self._write(data / "snapshots" / damaged_slot / "today-match.json", self.DAMAGED)
        self._write(
            data / "manifest.json",
            json.dumps({"snapshot": {"directory": f"data/snapshots/{live_slot}"}}),
        )
        return self._detect(root)

    def test_a_duplicate_in_the_previous_slot_is_reported_but_not_fatal(self):
        result = self._run(live_slot="s1", damaged_slot="s0")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("snapshot slot that is not the live one", result.stdout)
        self.assertIn("::warning::", result.stdout)
        self.assertNotIn("::error::", result.stdout)

    def test_a_duplicate_in_the_live_slot_still_fails(self):
        result = self._run(live_slot="s0", damaged_slot="s0")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("::error::", result.stdout)

    def test_a_duplicate_in_the_flat_mirror_still_fails(self):
        root = self._tree("slots-flat-")
        data = root / "data"
        self._write(data / "today-match.json", self.DAMAGED)
        self._write(data / "snapshots" / "s1" / "today-match.json", self.CLEAN)
        self._write(
            data / "manifest.json",
            json.dumps({"snapshot": {"directory": "data/snapshots/s1"}}),
        )
        result = self._detect(root)
        self.assertEqual(result.returncode, 1, result.stdout)

    def test_an_unreadable_manifest_treats_every_slot_as_live(self):
        root = self._tree("slots-nomanifest-")
        self._write(root / "data" / "snapshots" / "s0" / "today-match.json", self.DAMAGED)
        result = self._detect(root)
        # Refusing too much is something a person can undo in a minute.
        # Publishing a duplicate is not.
        self.assertEqual(result.returncode, 1, result.stdout)


class TheWorkflowStillWiresItUp(unittest.TestCase):
    """The function and the detector are useless if the step stops calling them."""

    def setUp(self) -> None:
        self.run_body = _push_step_run()

    def test_the_rebase_is_only_reached_through_the_guarding_function(self):
        bare = re.findall(r"^\s*git rebase -X theirs origin/main\s*$", self.run_body, re.M)
        self.assertEqual(len(bare), 1, "the rebase should live in exactly one place")
        function = _extract_function("rebase_keeping_our_generated_files")
        self.assertIn("git rebase -X theirs origin/main", function)

    def test_both_the_first_attempt_and_the_retry_use_it(self):
        self.assertEqual(
            len(re.findall(r"^\s*rebase_keeping_our_generated_files\s*$", self.run_body, re.M)),
            2,
        )

    def test_the_detector_runs_after_the_restore_and_before_reconcile(self):
        # Order between COMMANDS, not between mentions: the comment above the
        # detector call names reconcile_generated_counts to explain why it has
        # to come first, and matching prose would read the order backwards.
        commands = "\n".join(
            line
            for line in self.run_body.splitlines()
            if not line.lstrip().startswith("#")
        )
        for block in commands.split("rebase_keeping_our_generated_files\n")[1:]:
            detector = block.find("scripts/detect-merge-corruption.py")
            reconcile = block.find("reconcile_generated_counts")
            self.assertNotEqual(detector, -1, "detector missing after a rebase")
            self.assertNotEqual(reconcile, -1, "reconcile missing after a rebase")
            self.assertLess(detector, reconcile, "the look must precede the repair")

    def test_the_restore_never_takes_a_whole_directory(self):
        function = _extract_function("rebase_keeping_our_generated_files")
        self.assertIn('git checkout "$PRE" -- "${OURS[@]}"', function)
        for directory in ("data/", "reports/", "state/"):
            self.assertNotIn(f'git checkout "$PRE" -- {directory}', function)

    def test_deletions_are_excluded_from_the_restore_list(self):
        self.assertIn("--diff-filter=ACMR", _extract_function("rebase_keeping_our_generated_files"))

    def test_the_detector_script_exists_and_compiles(self):
        import py_compile

        self.assertTrue(DETECTOR.is_file())
        py_compile.compile(str(DETECTOR), doraise=True)

    def test_the_fixtures_are_real_published_files(self):
        provenance = json.loads((FIXTURES / "PROVENANCE.json").read_text(encoding="utf-8"))
        for role, (commit, items) in HISTORICAL.items():
            self.assertEqual(provenance[role]["commit"], commit)
            self.assertEqual(provenance[role]["items"], items)
            self.assertTrue(provenance[role]["byte_identical_to_git_blob"])


if __name__ == "__main__":
    unittest.main()
