"""PROMPT 46 - the five-minute run stops paying for the full suite.

FINAL_2 ধাপ ৯, third bullet: "targeted run-এ পুরো test suite (~২২৪২টা test)
চালাবেন না — শুধু scan। নাহলে প্রতি ৫ মিনিটে GitHub minutes পুড়বে." The suite is
around 2800 tests now and the trigger fires every five minutes, so it was
charging the one mode that has to be quick for the slowest thing in the job -
and delaying the link hunt the mode exists to perform.

Nothing is deleted and no other mode loses a check. The suite moved into a step
of its own with one condition on it. What stays for targeted:

    every required file present            (Validate scanner files)
    shell scripts LF-only                  (Validate scanner files)
    py_compile over every module           (Validate scanner files)
    node --check on all three site scripts (Validate scanner files)
    the playback worker runtime test       (Validate scanner files)
    the merge-damage repair                (its own step, before validation)
    the scan itself                        (Run scanner)
    the delivery-path check                (its own step)
    Cloudflare Pages output validation     (its own step)
    generated-count reconciliation + push  (Commit and push updated data)

What targeted skips: `python -m unittest discover -s tests`, and nothing else.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

WORKFLOW = ROOT / ".github" / "workflows" / "scan.yml"

TARGETED = "upcoming-targeted"

#: Steps a targeted run must still execute. Each is either unconditional or
#: conditioned on something other than the mode.
REQUIRED_FOR_TARGETED = (
    "Repair merge damage in the committed data before validating it",
    "Validate scanner files",
    "Select scan mode",
    "Run scanner",
    "Check the delivery path a viewer actually uses",
    "Validate generated Cloudflare Pages output",
    "Commit and push updated data",
)


def _steps():
    try:
        import yaml
    except ImportError:  # pragma: no cover - yaml ships with the runner
        raise unittest.SkipTest("pyyaml unavailable")
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["scan"]["steps"]


def _runs_for(step, mode):
    """Whether this step runs for `mode`, judged on the mode condition only.

    Conditions that do not mention the mode (staleness, failure(), the movie
    checkout) are left alone: they are not what this prompt changed.
    """
    condition = str(step.get("if") or "")
    if "steps.scan_mode.outputs.mode" not in condition:
        return True
    if "!=" in condition:
        return "'%s'" % mode not in condition
    return "'%s'" % mode in condition


class TheTargetedRunSkipsOnlyTheSuite(unittest.TestCase):
    def setUp(self):
        if not WORKFLOW.is_file():
            self.skipTest("no workflow")
        self.steps = _steps()
        self.names = [str(step.get("name") or "") for step in self.steps]

    def test_the_suite_does_not_run_for_targeted(self):
        suite = next(step for step in self.steps
                     if "unittest discover" in str(step.get("run") or ""))
        self.assertFalse(_runs_for(suite, TARGETED))

    def test_it_still_runs_for_every_other_mode(self):
        suite = next(step for step in self.steps
                     if "unittest discover" in str(step.get("run") or ""))
        for mode in ("today", "upcoming", "channels", "movies", "all"):
            self.assertTrue(_runs_for(suite, mode), mode)

    def test_every_safety_step_a_targeted_run_needs_still_runs(self):
        for name in REQUIRED_FOR_TARGETED:
            self.assertIn(name, self.names, name)
            step = self.steps[self.names.index(name)]
            with self.subTest(step=name):
                self.assertTrue(_runs_for(step, TARGETED),
                                "%s no longer runs for a targeted scan" % name)

    def test_the_cheap_checks_are_all_still_in_the_validation_step(self):
        body = str(self.steps[self.names.index("Validate scanner files")]["run"])
        for check in ("REQUIRED_FILES=(",
                      "must use LF line endings",
                      "python -m py_compile",
                      "node --check site/assets/js/app.js",
                      "node tests/playback-worker-runtime.mjs"):
            self.assertIn(check, body, check)

    def test_the_suite_is_the_only_thing_conditioned_on_the_mode_by_exclusion(self):
        """A `!=` on the mode is how a step opts a mode out. Only one exists,
        so nothing else was quietly dropped from the targeted path."""
        excluded = [
            str(step.get("name") or "") for step in self.steps
            if "steps.scan_mode.outputs.mode" in str(step.get("if") or "")
            and "!=" in str(step.get("if") or "")
        ]
        self.assertEqual(["Run the full test suite"], excluded)

    def test_no_step_lost_its_condition_to_the_move(self):
        """The movie steps and the staleness guard are untouched."""
        for name in ("Checkout private movie source repository",
                     "Validate private movie source repository"):
            condition = str(self.steps[self.names.index(name)].get("if"))
            self.assertIn("'movies'", condition)
            self.assertFalse(_runs_for(self.steps[self.names.index(name)], TARGETED))
        guard = self.steps[self.names.index(
            "Skip a superseded run of a frequent mode")]
        self.assertIn("schedule", str(guard.get("if")))


class ThePushSafetyIsUnchanged(unittest.TestCase):
    def setUp(self):
        if not WORKFLOW.is_file():
            self.skipTest("no workflow")
        self.steps = _steps()
        self.names = [str(step.get("name") or "") for step in self.steps]

    def test_the_reconciliation_still_guards_every_push(self):
        body = str(self.steps[self.names.index("Commit and push updated data")]["run"])
        for marker in ("rebase", "retry", "count"):
            self.assertIn(marker, body.lower(), marker)

    def test_the_suite_still_cannot_damage_the_scanners_ledger(self):
        suite = next(step for step in self.steps
                     if "unittest discover" in str(step.get("run") or ""))
        body = str(suite["run"])
        self.assertIn("git worktree add --detach", body)
        self.assertIn("git checkout -- state reports", body)
        self.assertLess(body.index("unittest discover"),
                        body.index("git checkout -- state reports"))

    def test_the_suite_still_runs_before_the_scanner(self):
        suite_index = next(
            index for index, step in enumerate(self.steps)
            if "unittest discover" in str(step.get("run") or ""))
        self.assertLess(suite_index, self.names.index("Run scanner"))


if __name__ == "__main__":
    unittest.main()
