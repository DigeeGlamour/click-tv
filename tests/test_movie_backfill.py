"""ধাপ ৫ / A-02 - the controlled backfill, and why the number is not fixed.

The plan says it twice, in bold: "৬০০–৮০০ একটি সর্বোচ্চ লক্ষ্য, স্থির সংখ্যা নয়"
and

    safe_batch = min(backfill_target, provider_remaining_budget, time_budget)

A fixed 800 is a promise the run cannot keep - it ignores how much provider
quota is left and how much of the scan's forty minutes verification already
took. So most of these tests are about the three-way minimum and about which
of the three is binding, because "we did 40" and "we did 40 because OMDb ran
out" are different facts and only the second tells anybody what to change.

The other rule tested here: running out is an ordinary outcome. "সব প্রোভাইডার
শেষ → queue pause, পরের রানে resume" - the catalogue publishes exactly as it
would have, and the next run carries on.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import movie_backfill as mb  # noqa: E402


class SafeBatchTests(unittest.TestCase):
    """The three-way minimum, and the reason travelling with the number."""

    def test_the_target_binds_when_nothing_else_is_short(self) -> None:
        lookups, reason = mb.safe_batch(
            target=700, provider_remaining=-1,
            time_budget_seconds=2400, elapsed_seconds=0)
        self.assertEqual(lookups, 700)
        self.assertEqual(reason, mb.REASON_TARGET)

    def test_provider_quota_binds_and_says_so(self) -> None:
        lookups, reason = mb.safe_batch(
            target=700, provider_remaining=45,
            time_budget_seconds=2400, elapsed_seconds=0)
        self.assertEqual(lookups, 45)
        self.assertEqual(reason, mb.REASON_PROVIDER_QUOTA)

    def test_the_time_budget_binds_and_says_so(self) -> None:
        """Thirty of the forty minutes already gone on verification."""
        lookups, reason = mb.safe_batch(
            target=700, provider_remaining=-1,
            time_budget_seconds=2400, elapsed_seconds=1800,
            seconds_per_lookup=1.0, time_share=0.35)
        self.assertEqual(lookups, 210)
        self.assertEqual(reason, mb.REASON_TIME_BUDGET)

    def test_the_smallest_of_the_three_always_wins(self) -> None:
        lookups, reason = mb.safe_batch(
            target=700, provider_remaining=30,
            time_budget_seconds=2400, elapsed_seconds=1800)
        self.assertEqual(lookups, 30)
        self.assertEqual(reason, mb.REASON_PROVIDER_QUOTA)

    def test_an_unmetered_provider_means_quota_is_not_the_constraint(self) -> None:
        lookups, _ = mb.safe_batch(
            target=100, provider_remaining=-1,
            time_budget_seconds=2400, elapsed_seconds=0)
        self.assertEqual(lookups, 100)

    def test_a_backfill_never_takes_the_whole_scan(self) -> None:
        """Verification is what the catalogue is for. A backfill that pushes
        the run past its budget gets the run killed, not the backfill trimmed.
        """
        lookups, _ = mb.safe_batch(
            target=100000, provider_remaining=-1,
            time_budget_seconds=2400, elapsed_seconds=0,
            seconds_per_lookup=1.0, time_share=0.35)
        self.assertEqual(lookups, 840)

    def test_a_zero_target_is_disabled_not_an_error(self) -> None:
        self.assertEqual(
            mb.safe_batch(target=0, provider_remaining=-1,
                          time_budget_seconds=2400),
            (0, mb.REASON_DISABLED),
        )

    def test_nothing_affordable_is_reported_as_the_reason_not_as_a_target(self) -> None:
        lookups, reason = mb.safe_batch(
            target=700, provider_remaining=0,
            time_budget_seconds=2400, elapsed_seconds=0)
        self.assertEqual(lookups, 0)
        self.assertEqual(reason, mb.REASON_PROVIDER_QUOTA)


class PlanRunTests(unittest.TestCase):
    """The elevated target is opt-in; the ordinary path is untouched."""

    def _settings(self, **backfill):
        return {
            "pipeline": {"time_budget_seconds": {"movies": 2400}},
            "movie_metadata_backfill": {
                "enabled": False, "target_lookups": 700, **backfill},
        }

    def test_with_the_backfill_off_the_ordinary_ceiling_applies(self) -> None:
        plan = mb.plan_run(self._settings(), provider_remaining=-1)
        self.assertFalse(plan["enabled"])
        self.assertEqual(plan["lookups"], plan["normal_budget"])

    def test_the_shipped_config_never_has_it_on_by_accident(self) -> None:
        """"এক বা দুই রাত চালিয়ে আবার ১৫০-এ ফেরত" - a backfill that ships on
        by default is one nobody chose to run.

        Off is still the resting state. When it IS on, the choice has to be
        written down - why, and when to look at it again - which is what tells
        a deliberate night's backfill apart from one left running because
        nobody noticed. On 2026-09-26 it was switched on with 1,024 of 1,970
        cards holding no metadata at all; that sentence is the difference.
        """
        settings = json.loads(
            (ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
        self.assertIn("movie_metadata_backfill", settings)
        block = settings["movie_metadata_backfill"]
        if not block["enabled"]:
            return
        reason = str(block.get("enabled_reason") or "")
        self.assertGreater(
            len(reason), 40,
            msg="a backfill that is on has to say why, in the config",
        )
        review = str(block.get("review_after") or "")
        self.assertRegex(
            review, r"^\d{4}-\d{2}-\d{2}$",
            msg="a backfill that is on has to say when to look at it again",
        )

    def test_a_backfill_left_on_is_still_bounded_by_the_run(self) -> None:
        """The reason it is safe to leave on at all: the ceiling is the
        smallest of the target, what the providers have left and what the run
        can afford - never the target on its own."""
        plan = mb.plan_run(self._settings(enabled=True), provider_remaining=12)
        self.assertEqual(plan["lookups"], 12)

    def test_switching_it_on_raises_the_ceiling(self) -> None:
        plan = mb.plan_run(self._settings(enabled=True), provider_remaining=-1)
        self.assertTrue(plan["enabled"])
        self.assertEqual(plan["lookups"], 700)

    def test_exhausted_providers_pause_the_queue_rather_than_fail(self) -> None:
        plan = mb.plan_run(self._settings(enabled=True), provider_remaining=0)
        self.assertEqual(plan["lookups"], 0)
        self.assertTrue(plan["paused"])
        self.assertEqual(plan["limited_by"], mb.REASON_PROVIDER_QUOTA)
        self.assertIn("resumes next run", mb.describe(plan))

    def test_an_exhausted_time_budget_also_pauses(self) -> None:
        plan = mb.plan_run(self._settings(enabled=True),
                           elapsed_seconds=2400, provider_remaining=-1)
        self.assertEqual(plan["lookups"], 0)
        self.assertTrue(plan["paused"])

    def test_an_ordinary_run_with_no_lookups_left_is_not_a_pause(self) -> None:
        """A normal day that simply spent its 150 is not a paused backfill."""
        plan = mb.plan_run(self._settings(), provider_remaining=-1)
        self.assertFalse(plan["paused"])

    def test_the_movie_time_budget_is_read_from_settings(self) -> None:
        plan = mb.plan_run(
            {"pipeline": {"time_budget_seconds": {"movies": 600}},
             "movie_metadata_backfill": {"enabled": True, "target_lookups": 700}},
            provider_remaining=-1)
        self.assertEqual(plan["time_budget_seconds"], 600)
        self.assertEqual(plan["lookups"], 210)

    def test_missing_settings_fall_back_to_the_documented_budget(self) -> None:
        plan = mb.plan_run({}, provider_remaining=-1)
        self.assertEqual(plan["time_budget_seconds"], 2400.0)


class ProviderRemainingTests(unittest.TestCase):
    """The maximum across providers, not the sum."""

    def setUp(self) -> None:
        from scanner import provider_health

        self.ph = provider_health
        self.path = str(Path(tempfile.mkdtemp()) / "provider-health.json")
        provider_health.reset(self.path)
        self.addCleanup(provider_health.reset, None)

    def _meter(self, provider, budget, spent=0, reserve=0):
        record = self.ph._provider_record(provider)
        record["daily_soft_budget"] = budget
        record["reserved_for_fallback"] = reserve
        for _ in range(spent):
            self.ph.note_spend(provider)
        return record

    def test_an_unmetered_provider_makes_quota_not_binding(self) -> None:
        self._meter("omdb", 100, spent=90)
        # tmdb has no budget configured, so it is unmetered.
        self.assertEqual(mb.provider_remaining_lookups(), -1)

    def test_two_metered_providers_do_not_add_up(self) -> None:
        """One lookup is answered by one provider walking the chain. Two
        providers with 100 left each do not make 200 lookups possible - they
        make the run survivable when the first one runs out.
        """
        for provider in ("tmdb", "omdb", "cinemeta", "moviesdatabase"):
            self._meter(provider, 100, spent=40)
        self.assertEqual(mb.provider_remaining_lookups(), 60)

    def test_an_unavailable_provider_does_not_count(self) -> None:
        self._meter("tmdb", 100)
        self._meter("omdb", 100)
        self._meter("cinemeta", 100)
        self._meter("moviesdatabase", 100)
        for _ in range(100):
            self.ph.note_spend("tmdb")
        self.ph._mark_unavailable(
            self.ph._provider_record("omdb"), self.ph.STATUS_COOLING_DOWN, 3600)
        self.assertEqual(mb.provider_remaining_lookups(), 100)

    def test_artwork_only_providers_are_not_counted_for_metadata(self) -> None:
        """Fanart cannot answer a metadata lookup, so its allowance is not
        headroom for one."""
        for provider in ("tmdb", "omdb", "cinemeta", "moviesdatabase"):
            self._meter(provider, 10, spent=10)
        self._meter("fanart", 1000)
        self.assertEqual(mb.provider_remaining_lookups(), 0)


class StateTests(unittest.TestCase):
    """Progress across nights has to be readable."""

    def setUp(self) -> None:
        self.path = Path(tempfile.mkdtemp()) / "movie-backfill.json"

    def test_a_missing_state_file_is_empty_not_an_error(self) -> None:
        state = mb.load_state(self.path)
        self.assertEqual(state["runs"], [])
        self.assertFalse(state["paused"])

    def test_a_damaged_state_file_is_empty_not_a_scan_failure(self) -> None:
        self.path.write_text("{not json", encoding="utf-8")
        self.assertEqual(mb.load_state(self.path)["runs"], [])

    def test_a_run_is_recorded_with_what_limited_it(self) -> None:
        plan = {"target": 700, "lookups": 45, "limited_by": mb.REASON_PROVIDER_QUOTA,
                "provider_remaining": 45, "paused": False}
        state = mb.record_run(plan, {"fetched": 45, "failed": 2}, path=self.path)
        entry = state["runs"][-1]
        self.assertEqual(entry["lookups_allowed"], 45)
        self.assertEqual(entry["limited_by"], mb.REASON_PROVIDER_QUOTA)
        self.assertEqual(entry["fetched"], 45)
        self.assertEqual(state["resolved_total"], 45)

    def test_resolved_totals_accumulate_across_runs(self) -> None:
        plan = {"target": 700, "lookups": 100, "limited_by": mb.REASON_TARGET,
                "paused": False}
        mb.record_run(plan, {"fetched": 100}, path=self.path)
        state = mb.record_run(plan, {"fetched": 80}, path=self.path)
        self.assertEqual(state["resolved_total"], 180)

    def test_a_pause_is_recorded_with_its_reason(self) -> None:
        plan = {"target": 700, "lookups": 0,
                "limited_by": mb.REASON_PROVIDER_QUOTA, "paused": True}
        state = mb.record_run(plan, {}, path=self.path)
        self.assertTrue(state["paused"])
        self.assertEqual(state["pause_reason"], mb.REASON_PROVIDER_QUOTA)

    def test_the_run_log_is_pruned(self) -> None:
        """A log nobody prunes becomes the biggest file in state/."""
        plan = {"target": 700, "lookups": 1, "limited_by": mb.REASON_TARGET,
                "paused": False}
        for _ in range(25):
            mb.record_run(plan, {"fetched": 1}, path=self.path)
        self.assertEqual(len(mb.load_state(self.path)["runs"]), 10)


class WiringTests(unittest.TestCase):
    """The budget reaches `enrich`, and only on the real publish path."""

    SOURCE = (ROOT / "scanner" / "movies.py").read_text(encoding="utf-8")

    def test_the_budget_is_computed_and_passed_through(self) -> None:
        self.assertIn("movie_backfill.plan_run(", self.SOURCE)
        self.assertIn("budget=budget", self.SOURCE)

    def test_the_plan_is_only_built_when_lookups_are_allowed(self) -> None:
        """`allow_lookup` is false for tests and ad-hoc calls, which must never
        reach the network or move the backfill state."""
        marker = self.SOURCE.index("backfill_plan = None")
        window = self.SOURCE[marker:marker + 400]
        self.assertIn("if allow_lookup:", window)

    def test_each_run_is_recorded(self) -> None:
        self.assertIn("movie_backfill.record_run(backfill_plan, summary)",
                      self.SOURCE)

    def test_elapsed_time_is_measured_from_process_start(self) -> None:
        self.assertIn("_PROCESS_STARTED_AT", self.SOURCE)
        self.assertIn("elapsed_seconds=_elapsed_scan_seconds()", self.SOURCE)


if __name__ == "__main__":
    unittest.main()
