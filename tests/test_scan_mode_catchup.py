"""A surviving run picks up whichever catalogue the schedule skipped.

The numbers behind this, all read from the GitHub Actions API for this
repository on 2026-08-29:

    2026-08-22   95 scheduled runs delivered
    2026-08-27   10
    2026-08-28    9
    2026-08-29    9

against five crons asking for roughly 370 a day. GitHub delays a `schedule`
event under load and skips it once the delay reaches the next occurrence, and
`*/5` puts the next occurrence five minutes away, so the dense crons take the
few surviving slots and the sparse ones lose theirs. The visible result was a
channels catalogue sixteen hours stale and a movie catalogue forty-seven hours
stale, both still being served.

These tests pin the narrowness of the fix as much as the fix. A catch-up must
never displace the scheduled mode, never fire on a manual dispatch, never queue
two catalogues into one job, and never fire at all while the schedule is
actually working.
"""
import datetime
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "select_scan_mode", ROOT / "scripts" / "select-scan-mode.py"
)
select_scan_mode = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(select_scan_mode)

NOW = datetime.datetime(2026, 8, 29, 14, 0, tzinfo=datetime.timezone.utc)


def _written(tmp, **ages_in_hours):
    """A reports directory whose summaries are this many hours old."""
    for mode, hours in ages_in_hours.items():
        if hours is None:
            continue
        when = NOW - datetime.timedelta(hours=hours)
        (Path(tmp) / ("scan-summary-%s.json" % mode)).write_text(
            json.dumps({"mode": mode, "last_scan": when.isoformat()}),
            encoding="utf-8",
        )
    return str(tmp)


class CatchUpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def choose(self, scheduled, *, event_name="schedule", **ages):
        return select_scan_mode.choose(
            scheduled,
            event_name=event_name,
            reports_dir=_written(self.tmp.name, **ages),
            now=NOW,
        )

    def test_a_fresh_schedule_adds_nothing(self):
        mode, catchup, _ = self.choose("today", channels=2.0, movies=6.0)
        self.assertEqual("today", mode)
        self.assertEqual("", catchup)

    def test_an_overdue_channels_catalogue_is_picked_up(self):
        """The measured case: 16.2 h stale against a six-hour cron."""
        mode, catchup, why = self.choose("today", channels=16.2, movies=6.0)
        self.assertEqual("today", mode)
        self.assertEqual("channels", catchup)
        self.assertIn("16.2", why)

    def test_the_scheduled_mode_is_never_replaced(self):
        mode, _, _ = self.choose("upcoming-targeted", channels=99.0, movies=99.0)
        self.assertEqual("upcoming-targeted", mode)

    def test_only_one_catalogue_per_run(self):
        _, catchup, _ = self.choose("today", channels=99.0, movies=99.0)
        self.assertEqual("channels", catchup)

    def test_movies_is_picked_up_when_only_movies_is_overdue(self):
        _, catchup, _ = self.choose("today", channels=1.0, movies=47.7)
        self.assertEqual("movies", catchup)

    def test_a_manual_dispatch_is_left_alone(self):
        """Somebody asked for a specific mode; that is the whole request."""
        mode, catchup, _ = self.choose(
            "today", event_name="workflow_dispatch", channels=99.0, movies=99.0
        )
        self.assertEqual("today", mode)
        self.assertEqual("", catchup)

    def test_a_channels_run_does_not_add_channels_to_itself(self):
        _, catchup, _ = self.choose("channels", channels=99.0, movies=1.0)
        self.assertEqual("", catchup)

    def test_a_movies_run_adds_nothing(self):
        """`movies` is the long one. Stacking channels on it risks the ceiling."""
        _, catchup, _ = self.choose("movies", channels=99.0, movies=99.0)
        self.assertEqual("", catchup)

    def test_a_mode_that_has_never_run_here_counts_as_overdue(self):
        _, catchup, why = self.choose("today", movies=1.0)
        self.assertEqual("channels", catchup)
        self.assertIn("no recorded scan", why)

    def test_the_limits_leave_room_for_an_on_time_schedule(self):
        """A cron that is working must never trigger a catch-up."""
        self.assertGreater(select_scan_mode.STALE_AFTER_HOURS["channels"], 6.0)
        self.assertGreater(select_scan_mode.STALE_AFTER_HOURS["movies"], 24.0)

    def test_an_unreadable_summary_is_treated_as_never_scanned(self):
        (Path(self.tmp.name) / "scan-summary-channels.json").write_text(
            "{ not json", encoding="utf-8"
        )
        self.assertIsNone(
            select_scan_mode.hours_stale("channels", self.tmp.name, now=NOW)
        )


class WorkflowWiringTests(unittest.TestCase):
    """The decision is worthless if the workflow does not act on it."""

    def setUp(self):
        try:
            import yaml
        except ImportError:  # pragma: no cover - yaml ships with the runner
            self.skipTest("pyyaml unavailable")
        self.doc = yaml.safe_load(
            (ROOT / ".github" / "workflows" / "scan.yml").read_text(encoding="utf-8")
        )
        self.steps = self.doc["jobs"]["scan"]["steps"]
        self.names = [str(s.get("name") or "") for s in self.steps]

    def _step(self, name):
        return self.steps[self.names.index(name)]

    def test_the_planning_step_exists_and_follows_mode_selection(self):
        self.assertIn("Plan a catch-up for a catalogue the schedule skipped", self.names)
        self.assertLess(
            self.names.index("Select scan mode"),
            self.names.index("Plan a catch-up for a catalogue the schedule skipped"),
        )

    def test_it_runs_before_the_scanner(self):
        self.assertLess(
            self.names.index("Plan a catch-up for a catalogue the schedule skipped"),
            self.names.index("Run scanner"),
        )

    def test_the_scanner_runs_the_catch_up_first(self):
        body = str(self._step("Run scanner").get("run") or "")
        self.assertIn("steps.catchup.outputs.catchup", body)
        self.assertLess(
            body.index('scan.py "$CATCHUP"'),
            body.index('scan.py "$MODE"'),
            "the overdue catalogue has to go first, or a slow job loses it",
        )

    def test_a_superseded_frequent_run_still_performs_its_catch_up(self):
        for name in (
            "Run scanner",
            "Validate generated Cloudflare Pages output",
            "Commit and push updated data",
        ):
            condition = str(self._step(name).get("if") or "")
            self.assertIn("steps.staleness.outputs.stale != 'yes'", condition)
            self.assertIn("steps.catchup.outputs.catchup != ''", condition)

    def test_a_superseded_run_still_skips_its_own_scheduled_pass(self):
        body = str(self._step("Run scanner").get("run") or "")
        self.assertIn('if [[ "$STALE" == "yes" ]]', body)


if __name__ == "__main__":
    unittest.main()
