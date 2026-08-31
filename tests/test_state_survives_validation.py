"""The validation suite must not leave the scanner an emptied evidence ledger.

Around thirty tests call the persistence helpers on their default paths, and
those paths point at the working tree. Running the suite therefore rewrites the
repository's real state/ and reports/ - and the workflow runs the suite in the
"Validate scanner files" step, before the scanner and before the commit.

Measured on 2026-08-29, one local `python -m unittest discover -s tests` run:

    state/route-persistence.json    13,736 lines removed
    state/movie-retention.json         968 lines rewritten
    state/live-event-protection.json   963 lines rewritten
    state/movie-first-seen.json         28 lines rewritten

route-persistence.json is the ledger the whole "never give up on a channel
after one bad observation" model reads. Its committed size across consecutive
runs shows the damage plainly: 497 KB at 150d3487c, 22 KB three commits later,
216 KB at 3796cd144, 23 KB at 71fda2082. Evidence that is supposed to accumulate
across scans has instead been starting from nothing on every run.

Isolating the tests properly means changing thirty call sites and belongs in its
own commit. Until then the workflow discards the churn between the suite and the
scanner, and this test is what stops that line being dropped.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

WORKFLOW = ROOT / ".github" / "workflows" / "scan.yml"


class ValidationStepTests(unittest.TestCase):
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
        self.validate = self.steps[self.names.index("Validate scanner files")]
        self.body = str(self.validate.get("run") or "")

    def test_the_suite_still_runs(self):
        self.assertIn("unittest discover", self.body)

    def test_the_suite_runs_in_a_throwaway_worktree(self):
        """The real fix: the tests cannot reach the scanner's tree at all.

        A worktree shares the object database, so it costs a checkout and no
        clone, and everything the suite writes is thrown away with it.
        """
        self.assertIn("git worktree add --detach", self.body)
        self.assertIn('cd "$VALIDATE_TREE"', self.body)
        self.assertIn("git worktree remove --force", self.body)

    def test_a_failing_suite_still_fails_the_step(self):
        """Running in a subshell swallows the exit code unless it is carried
        back out, and a validator that cannot fail is worse than none."""
        self.assertIn("VALIDATE_STATUS=$?", self.body)
        self.assertIn('exit "$VALIDATE_STATUS"', self.body)

    def test_the_worktree_is_removed_even_when_the_suite_fails(self):
        self.assertLess(
            self.body.index("git worktree remove --force"),
            self.body.index('exit "$VALIDATE_STATUS"'),
            "the tree would be left behind on a failing run",
        )

    def test_the_churn_is_discarded_after_it(self):
        self.assertIn("git checkout -- state reports", self.body)
        self.assertLess(
            self.body.index("unittest discover"),
            self.body.index("git checkout -- state reports"),
            "restoring before the suite would restore nothing",
        )

    def test_it_happens_before_the_scanner_reads_the_ledger(self):
        self.assertLess(
            self.names.index("Validate scanner files"),
            self.names.index("Run scanner"),
        )

    def test_the_restore_covers_both_directories(self):
        """reports/ carries the audit output the same scan then republishes."""
        line = next(
            line for line in self.body.splitlines()
            if "git checkout --" in line
        )
        self.assertIn("state", line)
        self.assertIn("reports", line)

    def test_nothing_else_is_discarded(self):
        """data/ must never be restored here - the scanner has not run yet, and
        a blanket `git checkout -- .` would also revert config a run depends on."""
        for line in self.body.splitlines():
            if "git checkout" in line:
                self.assertNotIn(" data", line)
                self.assertNotEqual("git checkout -- .", line.strip())


if __name__ == "__main__":
    unittest.main()
