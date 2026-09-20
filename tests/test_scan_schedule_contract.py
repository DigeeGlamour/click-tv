"""ধাপ ১০ক / A-01 - the movie scan runs twice a day, and the file agrees with itself.

`scan.yml` says it out loud:

> Do not edit the two cron STRINGS "17 0,6,12,18 * * *" or "37 4,16 * * *"
> without updating `concurrency.group` below - it matches on them by text to
> decide which queue a run belongs to.

Three places match that string by text - `on.schedule`, the mode selector, and
the concurrency group - and a fourth lists it in the "unrecognised schedule"
error. A comment is the only thing that has ever kept them in step, and the
file already records what happens when a schedule and its selector drift:

> it ran "channels" 11 times in 24 hours against a four-a-day cron while
> "today" and "upcoming-targeted" never ran at all, and the only symptom was a
> targeted report 115 hours stale.

So this is the comment, as a test.
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import movie_retention as mrt  # noqa: E402

WORKFLOW = ROOT / ".github" / "workflows" / "scan.yml"


def _declared_crons(text: str) -> list:
    return re.findall(r'^\s*-\s*cron:\s*"([^"]+)"', text, re.MULTILINE)


def _selector_crons(text: str) -> list:
    block = text[text.index("Select scan mode"):]
    block = block[:block.index("mode=$MODE")]
    return re.findall(r'github\.event\.schedule\s*\}\}"\s*==\s*"([^"]+)"', block)


class TheScheduleAndItsSelectorAgree(unittest.TestCase):
    def setUp(self) -> None:
        self.text = WORKFLOW.read_text(encoding="utf-8")

    def test_every_declared_cron_has_a_selector_branch(self) -> None:
        """A schedule the selector does not recognise fails the run by design -
        which is right, and better still is not shipping one."""
        declared = set(_declared_crons(self.text))
        selected = set(_selector_crons(self.text))
        self.assertEqual(
            declared - selected, set(),
            msg="declared crons with no branch in the mode selector",
        )

    def test_every_selector_branch_matches_a_declared_cron(self) -> None:
        declared = set(_declared_crons(self.text))
        selected = set(_selector_crons(self.text))
        self.assertEqual(
            selected - declared, set(),
            msg="selector branches for crons that are not declared",
        )

    def test_the_error_message_lists_the_crons_that_are_declared(self) -> None:
        """It is what somebody reads at 3am when a run fails."""
        listed = self.text[self.text.index("Declared crons:"):]
        listed = listed[:listed.index('"')]
        for cron in _declared_crons(self.text):
            self.assertIn(cron, listed)

    def test_the_catalogue_crons_are_in_the_catalogue_concurrency_group(self) -> None:
        """channels and movies write whole trees under data/; they must not
        share a queue with the five-minute event hunt."""
        group = self.text[self.text.index("group: >-"):]
        group = group[:group.index("cancel-in-progress")]
        for cron in ("17 0,6,12,18 * * *", "37 4,16 * * *"):
            self.assertIn(cron, group, msg=f"{cron} is not routed to a queue")


class TheMovieScanRunsTwiceADay(unittest.TestCase):
    """A-01 - "দিনে ২–৩ বার", after ধাপ ৭ made each run small."""

    def setUp(self) -> None:
        self.text = WORKFLOW.read_text(encoding="utf-8")
        self.movie_cron = next(
            cron for cron in _declared_crons(self.text)
            if cron.startswith("37 ")
        )

    def test_it_fires_between_two_and_three_times_a_day(self) -> None:
        hours = self.movie_cron.split()[1].split(",")
        self.assertGreaterEqual(len(hours), 2)
        self.assertLessEqual(
            len(hours), 3,
            msg="A-01 offers two to three a day, not more",
        )

    def test_the_runs_are_spread_across_the_day(self) -> None:
        """Two runs an hour apart would cost the same and gain nothing."""
        hours = sorted(int(hour) for hour in self.movie_cron.split()[1].split(","))
        gaps = [b - a for a, b in zip(hours, hours[1:])]
        gaps.append(24 - hours[-1] + hours[0])
        self.assertGreaterEqual(min(gaps), 6, msg=f"runs at {hours} are bunched")

    def test_it_is_one_cron_string_not_several_entries(self) -> None:
        """Every extra entry is another place for the three matchers to drift."""
        movie_crons = [
            cron for cron in _declared_crons(self.text) if cron.startswith("37 ")
        ]
        self.assertEqual(len(movie_crons), 1)

    def test_it_still_runs_off_the_round_minute(self) -> None:
        """GitHub drops what it cannot start, and the round minutes are where
        every cron on the platform lands at once - measured in this file."""
        self.assertNotEqual(self.movie_cron.split()[0], "0")


class TheGraceWindowFollowsTheCadence(unittest.TestCase):
    """The retention grace is counted in SCANS; what it protects is measured in
    DAYS, and the two are only equal at one cadence.

    `movie_retention` forgives a film that goes missing for three days, after
    383 of 817 films disappeared between two scans. Counted in scans that was
    three - because the scan ran once a day. A-01 made it twice a day, and the
    same 3 would have quietly become a day and a half: a source outage over a
    weekend, the exact case the grace was written for, would have started
    retiring films instead of riding it out.

    Nothing ties the cron string to that constant except this test.
    """

    #: What the window is FOR, in the unit it was chosen in.
    GRACE_DAYS = 3

    def setUp(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        movie_cron = next(
            cron for cron in _declared_crons(text) if cron.startswith("37 ")
        )
        self.runs_per_day = len(movie_cron.split()[1].split(","))

    def test_the_grace_window_is_still_three_days_long(self) -> None:
        self.assertEqual(
            mrt.INACTIVE_AFTER_MISSING_SCANS,
            self.GRACE_DAYS * self.runs_per_day,
            msg=(
                "the movie cron fires %d times a day, so %d days of grace is "
                "%d missing scans - but movie_retention."
                "INACTIVE_AFTER_MISSING_SCANS is %d. Changing the cadence "
                "changes what that number means."
                % (self.runs_per_day, self.GRACE_DAYS,
                   self.GRACE_DAYS * self.runs_per_day,
                   mrt.INACTIVE_AFTER_MISSING_SCANS)
            ),
        )

    def test_a_film_is_never_retired_on_a_single_bad_day(self) -> None:
        """The floor, independent of the arithmetic above: however the cadence
        is tuned, one day of silence may not retire anything."""
        self.assertGreater(
            mrt.INACTIVE_AFTER_MISSING_SCANS, self.runs_per_day)

if __name__ == "__main__":
    unittest.main()
