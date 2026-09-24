"""ধাপ ০ - the record of "what is live" has to keep being true.

The baseline exists so there is a way back, and `RealRepositoryBaselineTests`
verifies on every run that it still describes the published catalogue - because
"a claim that drifts is worse than no claim". Both halves were built. Nothing
ever re-recorded it.

So every scan that published anything poisoned the next one:

    08:41  the movies run publishes 166 series / 700 episodes
           the baseline still records 156 / 653
    09:49  the next run fails in "Run the full test suite", five failures,
           all of them that drift - and the scanner never starts

Which is the same shape as the no-loss deadlock it was found beside: a correct
check, a correct publish, and no one whose job it was to reconcile them. The
tests were right both times; what was missing was the step that keeps the thing
they check up to date.

These tests hold that step in place, and hold it in the right ORDER - before
the suite that reads it, in the same run - because a re-record that happens
after the tests would fix every run except the one it is in.
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

WORKFLOW = (ROOT / ".github" / "workflows" / "scan.yml").read_text(
    encoding="utf-8")

BACKUP = "scripts/movie-baseline-backup.py"


def _step_index(name: str) -> int:
    match = re.search(r"^      - name: " + re.escape(name) + r"$",
                      WORKFLOW, re.MULTILINE)
    if match is None:
        raise AssertionError(f"no step named {name!r} in scan.yml")
    return match.start()


class TheBaselineIsReRecordedByTheRunItself(unittest.TestCase):
    def test_the_workflow_runs_the_backup_script(self):
        self.assertIn(BACKUP, WORKFLOW)

    def test_it_asks_before_it_writes(self):
        """`--check` writes nothing and exits non-zero on drift, so an
        ordinary run whose catalogue has not moved gains no commit."""
        self.assertIn(BACKUP + " --check", WORKFLOW)

    def test_the_new_record_is_committed_not_merely_written(self):
        """The worktree the tests read is built from HEAD."""
        self.assertIn("git add state/movie-baseline.json", WORKFLOW)

    def test_it_happens_before_the_suite_that_verifies_it(self):
        """A re-record after the tests would fix every run except this one -
        which is exactly the failure being fixed."""
        self.assertLess(
            WORKFLOW.index(BACKUP + " --check"),
            _step_index("Run the full test suite"),
        )

    def test_it_happens_after_the_data_it_describes_is_repaired(self):
        """Recording a baseline over merge damage would make the damage the
        thing we could get back to."""
        self.assertLess(
            WORKFLOW.index("scripts/reconcile-generated-counts.py --fold-only"),
            WORKFLOW.index(BACKUP + " --check"),
        )

    def test_it_is_not_limited_to_one_scan_mode(self):
        """Any mode can publish a catalogue change; the step therefore sits in
        the unconditional part of the job, before the mode is even chosen."""
        self.assertLess(
            WORKFLOW.index(BACKUP + " --check"),
            _step_index("Select scan mode"),
        )


class TheScriptStillSupportsWhatTheWorkflowAsksOfIt(unittest.TestCase):
    """The workflow calls this script in a specific shape. If the script's
    arguments move, the step fails at 02:37 in the morning."""

    SCRIPT = (ROOT / "scripts" / "movie-baseline-backup.py").read_text(
        encoding="utf-8")

    def test_check_is_a_flag(self):
        self.assertIn('"--check"', self.SCRIPT)

    def test_check_exits_non_zero_on_drift(self):
        self.assertIn("return 1", self.SCRIPT)

    def test_label_is_a_flag(self):
        self.assertIn('"--label"', self.SCRIPT)

    def test_the_script_exists_where_the_workflow_looks_for_it(self):
        self.assertTrue((ROOT / BACKUP).is_file())


if __name__ == "__main__":
    unittest.main()
