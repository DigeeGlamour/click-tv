"""The workflow's mode selector answers to the crons it actually declares.

THE FAULT, measured on 2026-09-04 against origin/main.

`on.schedule` declared five crons. The "Select scan mode" step compared
`github.event.schedule` against a different pair of strings for two of them:

    declared in on.schedule        the selector looked for
    "3,23,43 * * * *"    today     "0,20,40 * * * *"      no match
    "1-59/5 * * * *"     targeted  "*/5 * * * *"          no match

Both fell through to `else MODE="channels"`, and nothing said so. What that
looked like from outside, all read from the real repository:

    Auto update commits, last 40      channels 20 · today 7 · upcoming 2
                                      movies 1 · upcoming-targeted 0
    channels commits in 24 hours      11, against a cron budget of 4 a day
    scan-summary-upcoming-targeted    last_scan 2026-08-30, 115 hours stale
    Upcoming cards carrying a link    0 of 124
    streams_attached                  0

So the mode that hunts for a fixture's stream link near kickoff had not run
once in five days, while the catalogue mode ran three times more often than it
was ever asked to.

TWO THINGS ARE PINNED HERE.

First, that every declared cron reaches its intended mode - checked by running
the step's own script, rendered the way GitHub renders it, rather than by
matching strings in the file. A test that only greps cannot tell a working
selector from a broken one; that is precisely the fault it missed.

Second, that a scheduled trigger whose cron matches nothing now FAILS instead
of quietly scanning channels. Guessing a mode is what made the drift invisible
for five days, and the step carries no `continue-on-error`, so the run fails
and `if: failure()` carries it to Telegram and uploads the log.

A manual `workflow_dispatch` is untouched: it never consults a cron.
"""
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

WORKFLOW = ROOT / ".github" / "workflows" / "scan.yml"

#: What each declared cron is for. The comments in scan.yml state these, and a
#: cron whose purpose changes has to be changed here too - deliberately, so the
#: two cannot drift apart again in silence.
INTENDED = {
    "3,23,43 * * * *": "today",
    "1-59/5 * * * *": "upcoming-targeted",
    "9 5,17 * * *": "upcoming",
    "17 0,6,12,18 * * *": "channels",
    "37 4 * * *": "movies",
}


def _document():
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    # PyYAML reads the bare key `on` as the boolean True.
    triggers = doc.get("on")
    if triggers is None:
        triggers = doc[True]
    return doc, triggers


def _selector_script():
    doc, _ = _document()
    for step in doc["jobs"]["scan"]["steps"]:
        if str(step.get("name") or "") == "Select scan mode":
            return step
    raise AssertionError("the 'Select scan mode' step is gone")


def _render(script, event_name, schedule="", inputs_mode=""):
    """Substitute the three expressions GitHub substitutes, and nothing else."""
    return (
        script
        .replace("${{ github.event_name }}", event_name)
        .replace("${{ github.event.schedule }}", schedule)
        .replace("${{ inputs.mode }}", inputs_mode)
    )


class TheSelectorIsRunNotRead(unittest.TestCase):
    """Every case below executes the step's real script under bash."""

    @classmethod
    def setUpClass(cls):
        if shutil.which("bash") is None:
            raise unittest.SkipTest("no bash to run the step's own script under")
        cls.step = _selector_script()
        cls.script = cls.step["run"]
        _, cls.triggers = _document()
        cls.declared = [entry["cron"] for entry in cls.triggers["schedule"]]

    def _run(self, event_name, schedule="", inputs_mode=""):
        handle, output = tempfile.mkstemp(suffix=".txt")
        os.close(handle)
        environment = dict(os.environ, GITHUB_OUTPUT=output)
        try:
            finished = subprocess.run(
                ["bash", "-s"],
                input=_render(self.script, event_name, schedule, inputs_mode),
                text=True, capture_output=True, env=environment,
            )
            written = pathlib.Path(output).read_text(encoding="utf-8").strip()
        finally:
            os.unlink(output)
        found = re.search(r"Selected scan mode: (\S+)", finished.stdout)
        return {
            "returncode": finished.returncode,
            "mode": found.group(1) if found else None,
            "output_file": written,
            "stdout": finished.stdout,
        }

    def test_the_script_is_valid_bash(self):
        finished = subprocess.run(
            ["bash", "-n"],
            input=_render(self.script, "schedule", "3,23,43 * * * *"),
            text=True, capture_output=True,
        )
        self.assertEqual(0, finished.returncode, finished.stderr)

    def test_every_declared_cron_reaches_its_intended_mode(self):
        """The whole fault. Two of these five used to answer 'channels'."""
        for cron in self.declared:
            with self.subTest(cron=cron):
                self.assertIn(cron, INTENDED, f"{cron} has no intended mode recorded")
                result = self._run("schedule", cron)
                self.assertEqual(0, result["returncode"])
                self.assertEqual(INTENDED[cron], result["mode"])
                self.assertEqual(f"mode={INTENDED[cron]}", result["output_file"])

    def test_the_selector_and_the_schedule_declare_the_same_crons(self):
        """A cron in one place and not the other is the drift itself."""
        matched = set(re.findall(
            r'github\.event\.schedule \}\}" == "([^"]+)"', self.script
        ))
        self.assertEqual(set(self.declared), matched)

    def test_an_unrecognised_schedule_fails_rather_than_guessing(self):
        for cron in ("0,20,40 * * * *", "*/5 * * * *", "13 * * * *", "0 0 * * 0", ""):
            with self.subTest(cron=cron):
                result = self._run("schedule", cron)
                self.assertNotEqual(0, result["returncode"],
                                    "an unknown cron still selected a mode")
                self.assertIsNone(result["mode"])
                self.assertEqual("", result["output_file"],
                                 "a failed selection must not publish a mode")

    def test_the_two_strings_that_caused_the_fault_are_among_them(self):
        """They were the selector's own values, so this is not hypothetical:
        had the fix been applied to `on.schedule` instead, these would arrive."""
        for cron in ("0,20,40 * * * *", "*/5 * * * *"):
            with self.subTest(cron=cron):
                self.assertNotEqual(0, self._run("schedule", cron)["returncode"])

    def test_the_failure_says_what_to_do_about_it(self):
        stdout = self._run("schedule", "13 * * * *")["stdout"]
        self.assertIn("::error", stdout, "no annotation reaches the Actions summary")
        self.assertIn("13 * * * *", stdout, "the offending cron is not named")
        self.assertIn("on.schedule", stdout, "the fix is not pointed at")

    def test_a_manual_dispatch_is_untouched(self):
        options = self.triggers["workflow_dispatch"]["inputs"]["mode"]["options"]
        self.assertTrue(options)
        for mode in options:
            with self.subTest(mode=mode):
                result = self._run("workflow_dispatch", "", mode)
                self.assertEqual(0, result["returncode"])
                self.assertEqual(mode, result["mode"])

    def test_a_dispatch_never_consults_a_cron(self):
        """Its branch is first, so a cron value cannot reach it."""
        result = self._run("workflow_dispatch", "13 * * * *", "movies")
        self.assertEqual(0, result["returncode"])
        self.assertEqual("movies", result["mode"])

    def test_only_the_scheduled_path_was_given_the_fail_safe(self):
        """Scope. Any other event name keeps the default it always had, so this
        change cannot fail a trigger it was not written for."""
        result = self._run("repository_dispatch", "")
        self.assertEqual(0, result["returncode"])
        self.assertEqual("channels", result["mode"])


class TheFailureCannotBeSwallowed(unittest.TestCase):
    def setUp(self):
        self.doc, _ = _document()
        self.steps = self.doc["jobs"]["scan"]["steps"]
        self.names = [str(step.get("name") or "") for step in self.steps]

    def _step(self, name):
        return self.steps[self.names.index(name)]

    def test_the_selector_step_is_not_allowed_to_continue_on_error(self):
        step = self._step("Select scan mode")
        self.assertNotEqual(True, step.get("continue-on-error"))
        self.assertIsNone(step.get("if"),
                          "a condition here could skip the selection entirely")

    def test_nothing_between_the_selector_and_the_scanner_ignores_a_failure(self):
        for name in ("Plan a catch-up for a catalogue the schedule skipped",
                     "Run scanner",
                     "Commit and push updated data"):
            with self.subTest(step=name):
                condition = str(self._step(name).get("if") or "")
                self.assertNotIn("always()", condition)
                self.assertNotIn("success() || failure()", condition)

    def test_a_failed_selection_reaches_the_owner(self):
        for name in ("Send Telegram after failed scan or push",
                     "Upload scanner log on failure"):
            with self.subTest(step=name):
                self.assertEqual("failure()", str(self._step(name).get("if") or ""))


if __name__ == "__main__":
    unittest.main()
