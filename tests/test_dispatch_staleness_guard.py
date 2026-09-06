"""The superseded-run guard covers an externally dispatched run too.

The guard drops a `today` or `upcoming-targeted` run that has already waited
out its own cadence: finishing it writes a view of the world that is older than
the next trigger, and it holds a one-writer queue against that trigger.

It was written when only the cron created those runs, so its condition read
`github.event_name == 'schedule'`. The step's own note already recorded what
that costs - "six workflow_dispatch runs have been queued since 2026-08-05 and
08-11 and cannot be cancelled from here" - and an external scheduler firing on
the Today cadence adds to exactly that queue. Measured on the live repository
before this change: GitHub delivered 6.7% of the runs its crons asked for, and
45 of 45 expected slots in one three-hour window produced no run object at all,
which is why an external trigger is wanted in the first place.

So the condition now also names `today` and `upcoming-targeted` dispatches.
Nothing else changes: the `case` inside the step is untouched, and channels,
movies, upcoming and all still always run, from the cron or by hand.

These tests do not read the step's text and hope. Where the question is "what
does it decide", the step's own bash body is executed with `gh` stubbed, so the
answer comes from the script that will run on the runner.
"""
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

WORKFLOW = ROOT / ".github" / "workflows" / "scan.yml"
GUARD = "Skip a superseded run of a frequent mode"

#: today asks for a run every 20 minutes and the guard allows 25; targeted asks
#: for one every 5 and the guard allows 12. Both come from the step itself.
TODAY_LIMIT = 1500
TARGETED_LIMIT = 720


def _workflow():
    if not WORKFLOW.is_file():
        raise unittest.SkipTest("no workflow")
    try:
        import yaml
    except ImportError:  # pragma: no cover - depends on the runner
        raise unittest.SkipTest("pyyaml unavailable")
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _steps():
    return _workflow()["jobs"]["scan"]["steps"]


def _step(name):
    for step in _steps():
        if str(step.get("name") or "") == name:
            return step
    raise AssertionError(f"no step named {name!r}")


def _condition():
    return " ".join(str(_step(GUARD).get("if") or "").split())


# --------------------------------------------------------------------------
# The condition: which triggers reach the guard at all
# --------------------------------------------------------------------------
class WhichTriggersReachTheGuard(unittest.TestCase):
    """Cases 1-6: the `if:` on the step."""

    def test_1_a_native_scheduled_run_still_reaches_it(self):
        self.assertIn("github.event_name == 'schedule'", _condition())

    def test_2_and_3_a_dispatched_today_reaches_it(self):
        self.assertIn("inputs.mode == 'today'", _condition())

    def test_4_a_dispatched_targeted_reaches_it(self):
        self.assertIn("inputs.mode == 'upcoming-targeted'", _condition())

    def test_5_and_6_no_other_mode_is_named_in_the_condition(self):
        condition = _condition()
        for mode in ("channels", "movies", "upcoming'", "all"):
            self.assertNotIn(f"inputs.mode == '{mode}", condition,
                             f"{mode} must not be pulled into the guard")

    def test_the_condition_is_a_union_not_a_replacement(self):
        # Dropping the schedule arm would leave the cron unguarded, which is
        # the fault this guard was built for in the first place.
        condition = _condition()
        self.assertTrue(condition.startswith("github.event_name == 'schedule'"))
        self.assertEqual(condition.count("||"), 2, condition)

    def test_the_guard_still_runs_after_the_mode_is_known(self):
        names = [str(s.get("name") or "") for s in _steps()]
        self.assertLess(names.index("Select scan mode"), names.index(GUARD))

    def test_the_working_steps_still_honour_it(self):
        for name in ("Run scanner",
                     "Validate generated Cloudflare Pages output",
                     "Commit and push updated data"):
            self.assertIn("steps.staleness.outputs.stale != 'yes'",
                          str(_step(name).get("if")), name)


# --------------------------------------------------------------------------
# The body: run it, do not read it
# --------------------------------------------------------------------------
class TheGuardBodyDecides(unittest.TestCase):
    """Cases 5-8: execute the step's own bash with `gh` stubbed."""

    @classmethod
    def setUpClass(cls):
        if shutil.which("bash") is None:  # pragma: no cover - Windows without git bash
            raise unittest.SkipTest("bash unavailable")
        cls.body = str(_step(GUARD).get("run") or "")

    def _run(self, mode, queued_seconds):
        """Return the `stale=` the step writes, for a run of `mode` that was
        created `queued_seconds` ago."""
        script = self.body.replace("${{ steps.scan_mode.outputs.mode }}", mode)
        with tempfile.TemporaryDirectory() as folder:
            folder = pathlib.Path(folder)
            # `gh api ... --jq .created_at` is the only external call the step
            # makes, so that is the only thing stubbed. The stub answers with a
            # real creation timestamp `queued_seconds` in the past.
            created = datetime.now(timezone.utc) - timedelta(
                seconds=int(queued_seconds))
            gh = folder / "gh"
            gh.write_text(
                "#!/usr/bin/env bash\necho "
                + created.strftime("%Y-%m-%dT%H:%M:%SZ") + "\n",
                encoding="utf-8")
            gh.chmod(0o755)
            step = folder / "step.sh"
            step.write_text(script, encoding="utf-8")
            output = folder / "github_output"
            output.write_text("", encoding="utf-8")
            env = dict(os.environ)
            env["PATH"] = f"{folder}{os.pathsep}" + env.get("PATH", "")
            env["GITHUB_OUTPUT"] = str(output)
            env["GITHUB_REPOSITORY"] = "DigeeGlamour/click-tv"
            env["GITHUB_RUN_ID"] = "1"
            done = subprocess.run(["bash", str(step)], env=env,
                                  capture_output=True, text=True, timeout=60)
            self.assertEqual(done.returncode, 0, done.stderr)
            written = output.read_text(encoding="utf-8")
        for line in written.splitlines():
            if line.startswith("stale="):
                return line.split("=", 1)[1].strip()
        raise AssertionError(f"the step wrote no stale= line: {written!r}")

    # ---- case 7: a fresh run is allowed, whoever created it ----------------
    def test_7_a_fresh_today_run_is_allowed(self):
        self.assertEqual(self._run("today", 30), "no")

    def test_7_a_fresh_targeted_run_is_allowed(self):
        self.assertEqual(self._run("upcoming-targeted", 30), "no")

    def test_7_a_run_inside_its_own_cadence_is_allowed(self):
        self.assertEqual(self._run("today", TODAY_LIMIT - 60), "no")
        self.assertEqual(self._run("upcoming-targeted", TARGETED_LIMIT - 60), "no")

    # ---- case 8: a superseded run is skipped ------------------------------
    def test_8_a_today_run_past_its_interval_is_skipped(self):
        self.assertEqual(self._run("today", TODAY_LIMIT + 60), "yes")

    def test_8_a_targeted_run_past_its_interval_is_skipped(self):
        self.assertEqual(self._run("upcoming-targeted", TARGETED_LIMIT + 60), "yes")

    def test_8_the_measured_fifty_minute_case_is_skipped(self):
        # 2026-08-28: a today run created at 06:53 started at 07:43.
        self.assertEqual(self._run("today", 50 * 60), "yes")

    # ---- cases 5 and 6: the other modes are never skipped ------------------
    def test_5_channels_always_runs_however_long_it_queued(self):
        for age in (30, TODAY_LIMIT + 60, 6 * 3600):
            self.assertEqual(self._run("channels", age), "no", age)

    def test_6_movies_always_runs_however_long_it_queued(self):
        for age in (30, TODAY_LIMIT + 60, 24 * 3600):
            self.assertEqual(self._run("movies", age), "no", age)

    def test_upcoming_and_all_always_run(self):
        for mode in ("upcoming", "all"):
            self.assertEqual(self._run(mode, 6 * 3600), "no", mode)

    def test_the_boundary_is_the_stated_limit(self):
        self.assertEqual(self._run("today", TODAY_LIMIT - 5), "no")
        self.assertEqual(self._run("today", TODAY_LIMIT + 5), "yes")
        self.assertEqual(self._run("upcoming-targeted", TARGETED_LIMIT - 5), "no")
        self.assertEqual(self._run("upcoming-targeted", TARGETED_LIMIT + 5), "yes")

    def test_an_unreadable_creation_time_still_runs_the_scan(self):
        """Failing closed here would silently stop scanning altogether."""
        script = self.body.replace("${{ steps.scan_mode.outputs.mode }}", "today")
        with tempfile.TemporaryDirectory() as folder:
            folder = pathlib.Path(folder)
            gh = folder / "gh"
            gh.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
            gh.chmod(0o755)
            step = folder / "step.sh"
            step.write_text(script, encoding="utf-8")
            output = folder / "out"
            output.write_text("", encoding="utf-8")
            env = dict(os.environ)
            env["PATH"] = f"{folder}{os.pathsep}" + env.get("PATH", "")
            env["GITHUB_OUTPUT"] = str(output)
            env["GITHUB_REPOSITORY"] = "x/y"
            env["GITHUB_RUN_ID"] = "1"
            done = subprocess.run(["bash", str(step)], env=env,
                                  capture_output=True, text=True, timeout=60)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertIn("stale=no", output.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# Cases 9 and 10: the queues did not move
# --------------------------------------------------------------------------
class TheQueuesAreUnchanged(unittest.TestCase):
    """A guard change must not move a run into a different queue."""

    def _group_for(self, schedule="", mode=""):
        # The same evaluator PROMPT 44 wrote and tested against GitHub's
        # own operand-returning `&&`/`||`, reused rather than copied.
        try:
            from tests.test_targeted_concurrency import _load, _render
        except ImportError:  # pragma: no cover - direct-module discovery
            from test_targeted_concurrency import _load, _render
        group = _load()["concurrency"]["group"]
        return _render(group, schedule=schedule, mode=mode)

    def test_9_a_dispatched_targeted_run_keeps_the_targeted_queue(self):
        self.assertEqual(self._group_for(mode="upcoming-targeted"),
                         "live-signal-targeted-v1")

    def test_9_the_targeted_cron_keeps_the_targeted_queue(self):
        self.assertEqual(self._group_for(schedule="1-59/5 * * * *"),
                         "live-signal-targeted-v1")

    def test_10_a_dispatched_today_run_keeps_the_events_queue(self):
        self.assertEqual(self._group_for(mode="today"), "live-signal-events-v4")

    def test_10_the_today_cron_keeps_the_events_queue(self):
        self.assertEqual(self._group_for(schedule="3,23,43 * * * *"),
                         "live-signal-events-v4")

    def test_an_external_trigger_shares_the_queue_of_its_cron(self):
        """The whole safety argument for an external scheduler."""
        for schedule, mode in (("3,23,43 * * * *", "today"),
                               ("1-59/5 * * * *", "upcoming-targeted"),
                               ("9 5,17 * * *", "upcoming"),
                               ("17 0,6,12,18 * * *", "channels"),
                               ("37 4 * * *", "movies")):
            self.assertEqual(self._group_for(schedule=schedule),
                             self._group_for(mode=mode),
                             f"{schedule} and dispatch {mode} must share a queue")

    def test_the_selector_is_untouched(self):
        body = str(_step("Select scan mode").get("run") or "")
        for cron in ("3,23,43 * * * *", "1-59/5 * * * *", "9 5,17 * * *",
                     "17 0,6,12,18 * * *", "37 4 * * *"):
            self.assertIn(cron, body, cron)
        self.assertIn("Unrecognised schedule", body)


if __name__ == "__main__":
    unittest.main()
