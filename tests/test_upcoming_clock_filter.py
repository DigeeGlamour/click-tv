"""The Upcoming tab never shows a match that has already kicked off.

The scanner drops these on its next pass, and that was fixed - but the scanner
cannot run when GitHub does not schedule it, and it frequently does not.
Measured on 2026-08-30, the gaps between runs of the scan workflow were 4, 11,
29, 33, 37, 56, 62, 89, 209 and 246 minutes, against crons asking for one every
five. In one of those gaps the owner was looking at a 15:30 match still sitting
on Upcoming at 16:14 with LINK UPDATING on it.

A published file is a snapshot of when it was written. The browser knows what
time it is now, so it can apply the same rule from the same clock without
waiting for anyone to rewrite the file. That makes the tab correct even when the
data behind it is hours old, which is the only way to make this hold in a
schedule nobody controls.
"""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

APP = (ROOT / "site" / "assets" / "js" / "app.js").read_text(encoding="utf-8")


class TheBrowserAppliesTheSameRuleTests(unittest.TestCase):
    def test_the_filter_exists(self):
        self.assertIn("function hasAlreadyKickedOff(item)", APP)

    def test_it_is_applied_to_the_upcoming_list(self):
        upcoming = APP[APP.index("if (sourceKind === VIEW.UPCOMING)"):]
        self.assertIn("hasAlreadyKickedOff(item)", upcoming[:300])

    def test_the_window_matches_the_scanner(self):
        """Two implementations of one rule; they have to agree or the tab and
        the file will disagree about the same fixture."""
        import json
        settings = json.loads(
            (ROOT / "config" / "settings.json").read_text(encoding="utf-8")
        )
        minutes = settings["events"]["upcoming_past_grace_minutes"]
        match = re.search(
            r"UPCOMING_PAST_GRACE_MS\s*=\s*(\d+)\s*\*\s*60\s*\*\s*1000", APP
        )
        self.assertIsNotNone(match, "the UI window must be written in minutes")
        self.assertEqual(minutes, int(match.group(1)))

    def test_it_needs_a_real_start_time(self):
        """A fixture with no clock is not evidence of anything, and a channel
        backed card carries none at all."""
        body = APP[APP.index("function hasAlreadyKickedOff(item)"):]
        body = body[:body.index("\n}\n")]
        self.assertIn("if (!raw) return false;", body)
        self.assertIn("Number.isFinite(start)", body)

    def test_it_only_touches_the_upcoming_list(self):
        """Today Match owns a live fixture; this must not reach it."""
        calls = APP.count("hasAlreadyKickedOff(item)") - APP.count(
            "function hasAlreadyKickedOff(item)"
        )
        self.assertEqual(1, calls,
                         "called in exactly one place, the Upcoming filter")


class TheScheduleItCompensatesForTests(unittest.TestCase):
    def test_the_frequent_crons_are_off_the_busiest_minutes(self):
        """GitHub runs scheduled workflows on a shared queue and drops what it
        cannot start; :00 and */5 are where every cron on the platform lands at
        once."""
        workflow = (ROOT / ".github" / "workflows" / "scan.yml").read_text(encoding="utf-8")
        crons = re.findall(r'- cron: "([^"]+)"', workflow)
        self.assertNotIn("*/5 * * * *", crons)
        self.assertNotIn("0,20,40 * * * *", crons)
        for cron in crons:
            first_minute = cron.split()[0].split(",")[0].split("-")[0]
            if first_minute != "*":
                self.assertNotEqual(
                    "0", first_minute,
                    f"{cron} starts on the busiest minute of the hour",
                )

    def test_the_two_queues_are_split_by_what_a_run_writes(self):
        workflow = (ROOT / ".github" / "workflows" / "scan.yml").read_text(encoding="utf-8")
        group = workflow[workflow.index("concurrency:"):workflow.index("cancel-in-progress")]
        self.assertIn("catalogue", group)
        self.assertIn("events", group)

    def test_the_group_still_names_the_crons_it_routes_on(self):
        """The group expression matches on cron strings. Editing a cron without
        editing the expression would silently move a mode to the wrong queue."""
        workflow = (ROOT / ".github" / "workflows" / "scan.yml").read_text(encoding="utf-8")
        group = workflow[workflow.index("group: >-"):workflow.index("cancel-in-progress")]
        crons = re.findall(r'- cron: "([^"]+)"', workflow)
        for named in re.findall(r"github\.event\.schedule == '([^']+)'", group):
            self.assertIn(named, crons,
                          f"the concurrency group routes on {named!r}, "
                          "which is no longer a cron in this workflow")


if __name__ == "__main__":
    unittest.main()
