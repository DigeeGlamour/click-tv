"""The scanner says when it is stuck: a failure streak, and a watchdog outside it.

October's longest outage was thirteen days of disabled workflows that nothing
reported, and its noisiest was a failure alert every twenty minutes. These
pin the middle: one failure pages nobody, the third in a row always does, and
a switched-off workflow is reported by something that is still running.
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


STREAK = _load("failure_streak", "failure-streak.py")
WATCHDOG = _load("scanner_watchdog", "scanner-watchdog.py")


def _run(conclusion, status="completed"):
    return {"status": status, "conclusion": conclusion}


class TheStreakCountsVerdictsOnly(unittest.TestCase):
    def test_the_asking_run_counts(self):
        self.assertEqual(STREAK.streak([]), 1)

    def test_it_stops_at_the_first_success(self):
        runs = [_run("failure"), _run("failure"), _run("success"), _run("failure")]
        self.assertEqual(STREAK.streak(runs), 3)

    def test_cancelled_and_in_flight_runs_are_stepped_over(self):
        runs = [_run(None, "in_progress"), _run("cancelled"), _run("failure"),
                _run("cancelled"), _run("failure"), _run("success")]
        self.assertEqual(STREAK.streak(runs), 3)


class TheWorkflowPagesOnAStreak(unittest.TestCase):
    WORKFLOW = (ROOT / ".github" / "workflows" / "scan.yml").read_text(encoding="utf-8")

    def test_it_counts_the_streak(self):
        self.assertIn("scripts/failure-streak.py", self.WORKFLOW)
        self.assertIn("--consecutive", self.WORKFLOW)

    def test_the_alert_names_what_stopped_it(self):
        self.assertIn("--details-file", self.WORKFLOW)

    def test_it_may_read_its_own_run_history(self):
        self.assertIn("actions: read", self.WORKFLOW)


class TheWatchdogSeesWhatTheScannerCannot(unittest.TestCase):
    NOW = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)

    def _recent(self, hours):
        return (self.NOW - timedelta(hours=hours)).isoformat().replace("+00:00", "Z")

    def test_a_disabled_workflow_is_reported(self):
        problems = WATCHDOG.findings(
            [{"name": "Live Signal Scanner", "state": "disabled_manually"}],
            self._recent(1), self.NOW)
        self.assertEqual(len(problems), 1)
        self.assertIn("disabled_manually", problems[0])

    def test_a_scanner_with_no_recent_success_is_reported(self):
        problems = WATCHDOG.findings(
            [{"name": "Live Signal Scanner", "state": "active"}],
            self._recent(WATCHDOG.STALE_AFTER_HOURS + 1), self.NOW)
        self.assertEqual(len(problems), 1)

    def test_a_healthy_repository_is_quiet(self):
        problems = WATCHDOG.findings(
            [{"name": "Live Signal Scanner", "state": "active"}],
            self._recent(1), self.NOW)
        self.assertEqual(problems, [])

    def test_it_never_re_enables_anything(self):
        source = (ROOT / "scripts" / "scanner-watchdog.py").read_text(encoding="utf-8")
        self.assertNotIn("/enable", source)

    def test_it_runs_on_its_own_schedule(self):
        workflow = (ROOT / ".github" / "workflows" / "watchdog.yml").read_text(
            encoding="utf-8")
        self.assertIn("scripts/scanner-watchdog.py", workflow)
        self.assertIn("cron:", workflow)



class EveryTelegramMessageIsInBangla(unittest.TestCase):
    """The owner reads these: Bangla words, Bangla digits, no Banglish."""

    def setUp(self):
        from scanner import telegram_notify
        self.notify = telegram_notify

    @staticmethod
    def _prose(message):
        import re
        text = re.sub(r"<[^>]+>", "", message)
        text = re.sub(r"https?://\S+", "", text)
        return text

    def test_the_failure_message_has_no_english_words(self):
        message = self.notify.build_failure_message(
            "movies", "abc123", "main", "https://github.com/x/actions/runs/1",
            consecutive=3)
        prose = self._prose(message).replace("abc123", "").replace("main", "")
        self.assertNotRegex(prose, r"[A-Za-z0-9]")
        self.assertIn("৩", message)

    def test_the_success_message_has_no_english_labels(self):
        import re
        message = self.notify.build_message("movies", "abc123", "main")
        for line in message.splitlines():
            label = re.match(r"<b>([^<]*)</b>", line)
            if label:
                self.assertNotRegex(label.group(1), r"[A-Za-z]", line)

    def test_bangla_digits_and_bangladesh_time(self):
        self.assertEqual(self.notify.bn(1970), "১৯৭০")
        self.assertEqual(self.notify.time_bn("2026-10-21T08:17:00+00:00"),
                         "২০২৬-১০-২১ ১৪:১৭ (বাংলাদেশ সময়)")

    def test_a_held_movie_loss_is_announced(self):
        import json
        import tempfile
        report = {"invariant_2": {"unexplained_live_loss": 23, "lost_streams": [
            {"inventory_line": "movie|mix|sultan-s01e21|https://x/y.mkv"}]},
            "invariants": {"block": False}}
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "c.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            lines = self.notify.held_movie_loss_lines(path)
        self.assertIn("২৩", lines[1])
        self.assertIn("sultan-s01e21", lines[2])

    def test_a_blocked_publish_is_not_announced_as_a_held_loss(self):
        import json
        import tempfile
        report = {"invariant_2": {"unexplained_live_loss": 200},
                  "invariants": {"block": True}}
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "c.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            self.assertEqual(self.notify.held_movie_loss_lines(path), [])

    def test_the_watchdog_speaks_bangla(self):
        from datetime import datetime, timezone
        problems = WATCHDOG.findings([], "2026-10-20T00:00:00Z",
                                     datetime(2026, 10, 21, tzinfo=timezone.utc))
        self.assertNotRegex(problems[0].split("(")[0], r"[A-Za-z0-9]")

if __name__ == "__main__":
    unittest.main()
