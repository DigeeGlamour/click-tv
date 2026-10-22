"""The owner's pause switch: config/settings.json -> paused.

Movies were paused on 2026-10-21 - no working source remained to replace the
suspended CDN, so the catalogue stays as published and its scans stop. A
paused mode must scan nothing, write nothing, never be caught up, never send
a "scan successful" message, and its switched-off workflows must not trip
the watchdog. Everything else must be untouched.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import scan  # noqa: E402

SETTINGS = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SELECT = _load("select_scan_mode_paused", "scripts/select-scan-mode.py")
WATCHDOG = _load("watchdog_paused", "scripts/scanner-watchdog.py")


class TheSwitch(unittest.TestCase):
    def test_movies_are_paused_and_nothing_else(self):
        self.assertEqual(scan.paused_modes(), {"movies"})
        for mode in ("channels", "today", "upcoming", "upcoming-targeted"):
            self.assertNotIn(mode, scan.paused_modes())

    def test_a_paused_mode_scans_nothing(self):
        with mock.patch.object(sys, "argv", ["scan.py", "movies"]), \
                mock.patch.object(scan, "run_pipeline") as pipeline:
            self.assertEqual(scan.main(), 0)
        pipeline.assert_not_called()

    def test_a_live_mode_still_scans(self):
        with mock.patch.object(sys, "argv", ["scan.py", "channels"]), \
                mock.patch.object(scan, "run_pipeline") as pipeline, \
                mock.patch.object(scan, "_flush_visibility_audit"):
            scan.main()
        pipeline.assert_called_once_with("channels")


class NeverCaughtUp(unittest.TestCase):
    def test_an_overdue_paused_catalogue_is_skipped(self):
        with mock.patch.object(SELECT, "hours_stale", return_value=999.0):
            _, catchup, _ = SELECT.choose("today", event_name="schedule")
        self.assertEqual(catchup, "channels")
        with mock.patch.object(SELECT, "hours_stale",
                               side_effect=lambda c, *a, **k: 1.0 if c == "channels" else 999.0):
            _, catchup, _ = SELECT.choose("today", event_name="schedule")
        self.assertEqual(catchup, "")


class TheWatchdogKnows(unittest.TestCase):
    def test_a_paused_workflow_is_not_reported(self):
        workflows = [{"name": "Movie repair", "path": ".github/workflows/movie-repair.yml",
                      "state": "disabled_manually"},
                     {"name": "Live Signal Scanner", "path": ".github/workflows/scan.yml",
                      "state": "disabled_manually"}]
        now = datetime(2026, 10, 22, tzinfo=timezone.utc)
        problems = WATCHDOG.findings(workflows, "2026-10-22T00:00:00Z", now)
        self.assertEqual(len(problems), 1)
        self.assertIn("Live Signal Scanner", problems[0])


if __name__ == "__main__":
    unittest.main()
