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

Two mechanisms keep it out now, and both are asserted below.

The worktree covers every module whose state path is relative, because those
resolve against the working directory and the suite is run from somewhere else.
Nine modules were not covered by it: their default path is anchored to
`__file__`, so it names the imported checkout however the suite is started, and
route-persistence.json is one of the nine. Those answer to
CLICKTV_STATE_ROOT/CLICKTV_REPORTS_ROOT instead - see scanner/paths.py - which
the validation step points at the worktree's own copies.

The `git checkout -- state reports` after the suite stays as well. It is belt
and braces now rather than the fix, and it costs nothing.
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


class TheAbsoluteStatePathsCanBeSentElsewhere(unittest.TestCase):
    """The nine modules a worktree alone could not protect.

    Their default path is `os.path.dirname(__file__)/../state/...`, which is
    the checkout the module was imported from - not the directory the suite is
    running in. Measured: one suite run took 13,736 lines out of the real
    state/route-persistence.json even when started from elsewhere.
    """

    MODULES = (
        ("persistence_store", "DEFAULT_STORE_PATH", "state"),
        ("playback_evidence", "DEFAULT_PATH", "state"),
        ("route_evidence_cache", "DEFAULT_PATH", "state"),
        ("route_preference", "DEFAULT_PATH", "state"),
        ("sustained_proof", "DEFAULT_PROOF_PATH", "state"),
        ("movie_recency", "DEFAULT_PATH", "state"),
        ("movie_retention", "DEFAULT_PATH", "state"),
        ("last_published", "DEFAULT_DIR", "state"),
        ("visibility_audit", "DEFAULT_AUDIT_PATH", "reports"),
    )

    def _reload_all(self):
        import importlib

        loaded = []
        for name, attribute, directory in self.MODULES:
            module = importlib.reload(importlib.import_module(f"scanner.{name}"))
            loaded.append((name, str(getattr(module, attribute)), directory))
        return loaded

    def test_every_one_of_them_defaults_inside_this_repository(self):
        """With no override the answer must be exactly what it always was, so
        a scan, a Colab run and a local PC run are all unaffected.

        The environment is cleared rather than trusted: the validation step
        itself sets these two, so the suite may well be running with them set
        - which is the point of them existing.
        """
        import os

        from scanner import paths

        previous = {
            key: os.environ.pop(key, None)
            for key in (paths.STATE_ROOT_ENV, paths.REPORTS_ROOT_ENV)
        }
        try:
            for name, value, directory in self._reload_all():
                with self.subTest(module=name):
                    self.assertTrue(
                        value.startswith(str(ROOT)),
                        f"{name} left the repository with no override: {value}",
                    )
                    self.assertIn(directory, value)
        finally:
            for key, was in previous.items():
                if was is not None:
                    os.environ[key] = was
            self._reload_all()

    def test_an_override_moves_them(self):
        import importlib
        import os
        import tempfile

        from scanner import paths

        with tempfile.TemporaryDirectory() as tmp:
            previous = {
                key: os.environ.get(key)
                for key in (paths.STATE_ROOT_ENV, paths.REPORTS_ROOT_ENV)
            }
            os.environ[paths.STATE_ROOT_ENV] = os.path.join(tmp, "state")
            os.environ[paths.REPORTS_ROOT_ENV] = os.path.join(tmp, "reports")
            try:
                for name, attribute, directory in self.MODULES:
                    module = importlib.reload(
                        importlib.import_module(f"scanner.{name}")
                    )
                    value = str(getattr(module, attribute))
                    with self.subTest(module=name):
                        self.assertTrue(
                            value.startswith(tmp),
                            f"{name}.{attribute} ignored the override: {value}",
                        )
            finally:
                for key, was in previous.items():
                    if was is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = was
                self._reload_all()

    def test_the_validation_step_points_them_at_the_worktree(self):
        if not WORKFLOW.is_file():
            self.skipTest("no workflow")
        try:
            import yaml
        except ImportError:  # pragma: no cover - yaml ships with the runner
            self.skipTest("pyyaml unavailable")
        steps = yaml.safe_load(
            WORKFLOW.read_text(encoding="utf-8")
        )["jobs"]["scan"]["steps"]
        body = next(
            str(step.get("run") or "") for step in steps
            if str(step.get("name") or "") == "Validate scanner files"
        )
        self.assertIn('CLICKTV_STATE_ROOT="$VALIDATE_TREE/state"', body)
        self.assertIn('CLICKTV_REPORTS_ROOT="$VALIDATE_TREE/reports"', body)


if __name__ == "__main__":
    unittest.main()
