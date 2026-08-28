"""A late run of a frequent mode must not hold the queue against the next one.

Measured on 2026-08-28: a today-match run created at 06:53 started at 07:43 -
fifty minutes late against a twenty-minute cadence. The concurrency group is
deliberately one-writer with cancel-in-progress false, so slow runs stack
instead of replacing each other, and six workflow_dispatch runs queued on
2026-08-05 and 08-11 have never cleared.

The consequence was not just lateness: while the backlog drained, no run picked
up a pushed fix for over ninety minutes, so a validator failure kept repeating
on a commit that had already been superseded.

The guard drops only the two frequent modes, and only past their own interval.
channels, movies and the twice-daily upcoming refresh always run - they publish
the catalogue, and a late one is still worth having.
"""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

WORKFLOW = ROOT / ".github" / "workflows" / "scan.yml"


def _load():
    try:
        import yaml
    except ImportError:  # pragma: no cover - yaml ships with the runner
        raise unittest.SkipTest("pyyaml unavailable")
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


class StalenessGuardTests(unittest.TestCase):
    def setUp(self):
        if not WORKFLOW.is_file():
            self.skipTest("no workflow")
        self.steps = _load()["jobs"]["scan"]["steps"]
        self.names = [str(s.get("name") or "") for s in self.steps]

    def _step(self, name):
        return self.steps[self.names.index(name)]

    def test_the_guard_exists_and_runs_only_for_schedules(self):
        self.assertIn("Skip a superseded run of a frequent mode", self.names)
        step = self._step("Skip a superseded run of a frequent mode")
        self.assertIn("schedule", str(step.get("if")))

    def test_it_runs_after_the_mode_is_known(self):
        """It has to read the mode to decide, so order matters."""
        self.assertLess(
            self.names.index("Select scan mode"),
            self.names.index("Skip a superseded run of a frequent mode"),
        )

    def test_the_working_steps_honour_it(self):
        """Setting an output nobody reads would be worse than no guard."""
        for name in (
            "Run scanner",
            "Validate generated Cloudflare Pages output",
            "Commit and push updated data",
        ):
            self.assertIn(
                "steps.staleness.outputs.stale != 'yes'",
                str(self._step(name).get("if")),
                f"{name} ignores the staleness guard",
            )

    def test_only_the_frequent_modes_can_be_skipped(self):
        """channels and movies publish the catalogue; a late one still counts."""
        body = str(self._step("Skip a superseded run of a frequent mode").get("run"))
        self.assertIn("today)", body)
        self.assertIn("upcoming-targeted)", body)
        for protected in ("channels", "movies"):
            self.assertNotRegex(
                body, rf"^\s*{protected}\)", f"{protected} must never be skipped"
            )

    def test_a_skip_is_never_reported_as_a_successful_scan(self):
        step = self._step("Send Telegram after successful GitHub push")
        self.assertIn("stale != 'yes'", str(step.get("if")))

    def test_an_unavailable_creation_time_runs_the_scan(self):
        """Failing closed here would silently stop scanning altogether."""
        body = str(self._step("Skip a superseded run of a frequent mode").get("run"))
        self.assertIn("running anyway", body)
        self.assertIn('stale=no', body)

    def test_the_thresholds_are_longer_than_the_cadences(self):
        body = str(self._step("Skip a superseded run of a frequent mode").get("run"))
        today = re.search(r"today\)\s*LIMIT=(\d+)", body)
        targeted = re.search(r"upcoming-targeted\)\s*LIMIT=(\d+)", body)
        self.assertTrue(today and targeted)
        # today runs every 20 minutes, upcoming-targeted every 5.
        self.assertGreater(int(today.group(1)), 20 * 60)
        self.assertGreater(int(targeted.group(1)), 5 * 60)


class ConcurrencyTests(unittest.TestCase):
    def test_the_one_writer_guarantee_is_still_in_place(self):
        """The guard reduces the backlog; it must not weaken write safety."""
        if not WORKFLOW.is_file():
            self.skipTest("no workflow")
        payload = _load()
        concurrency = payload.get("concurrency") or {}
        self.assertTrue(str(concurrency.get("group") or "").strip())
        self.assertFalse(
            concurrency.get("cancel-in-progress"),
            "cancelling a data writer mid-run is what the group exists to stop",
        )


if __name__ == "__main__":
    unittest.main()
