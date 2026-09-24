"""ধাপ ১৩ / A-06 - the nine numbers, in one place, honest about what is missing.

    ► ছাড়া বোঝা যাবে না পরিকল্পনা কাজ করছে কিনা।

The distinction this file spends most of its tests on is **zero versus
unmeasured**, because they mean opposite things:

    skipped_fresh = 0        ধাপ ৭'s TTL skipped nothing. Something is wrong.
    skipped_fresh = None     the gate did not run this time. Nothing is wrong.

A report that prints 0 for both cannot tell anyone whether the plan is
working, which is the one thing A-06 exists to do.
"""
from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import movie_observability as obs  # noqa: E402


class TheNineCounters(unittest.TestCase):
    def setUp(self) -> None:
        obs.reset()
        self.addCleanup(obs.reset)

    def test_every_counter_the_plan_names_is_present(self):
        """A-06 lists them; leaving one out is how it stops being measured."""
        for name in ("skipped_fresh", "due_verified", "repair_attempted",
                     "series_signal_candidates", "classification_cache_hits",
                     "confirmed_series", "classification_conflicts",
                     "season_only_unknown_episode"):
            self.assertIn(name, obs.COUNTERS)
        self.assertEqual(obs.QUOTA_KEY, "provider_quota_remaining")

    def test_the_block_always_carries_all_of_them(self):
        block = obs.collect()
        for name in obs.COUNTERS:
            self.assertIn(name, block)
        self.assertIn(obs.QUOTA_KEY, block)


class ZeroAndUnmeasuredAreDifferentAnswers(unittest.TestCase):
    def setUp(self) -> None:
        obs.reset()
        self.addCleanup(obs.reset)

    def test_a_counter_nobody_recorded_is_none(self):
        self.assertIsNone(obs.collect()["skipped_fresh"])

    def test_a_counter_recorded_as_zero_is_zero(self):
        """"The TTL skipped nothing" is a finding. It must not be erased into
        "the TTL did not run"."""
        obs.set_value("skipped_fresh", 0)
        self.assertEqual(obs.collect()["skipped_fresh"], 0)

    def test_the_unmeasured_ones_are_listed(self):
        obs.set_value("skipped_fresh", 3)
        block = obs.collect()
        self.assertNotIn("skipped_fresh", obs.unmeasured(block))
        self.assertIn("repair_attempted", obs.unmeasured(block))

    def test_the_log_says_which_were_not_measured(self):
        """A counter that quietly disappears is how a phase stops working
        without anyone noticing."""
        lines = obs.describe(obs.collect())
        joined = "\n".join(lines)
        self.assertIn("not measured this run", joined)
        self.assertIn("itself a finding", joined)

    def test_a_fully_measured_run_says_nothing_about_missing_counters(self):
        for name in obs.COUNTERS:
            obs.set_value(name, 1)
        joined = "\n".join(obs.describe(obs.collect()))
        self.assertNotIn("not measured", joined)


class Recording(unittest.TestCase):
    def setUp(self) -> None:
        obs.reset()
        self.addCleanup(obs.reset)

    def test_note_accumulates(self):
        obs.note("repair_attempted")
        obs.note("repair_attempted", 3)
        self.assertEqual(obs.collect()["repair_attempted"], 4)

    def test_set_value_replaces(self):
        obs.set_value("due_verified", 10)
        obs.set_value("due_verified", 7)
        self.assertEqual(obs.collect()["due_verified"], 7)

    def test_reset_clears_everything(self):
        obs.note("repair_attempted")
        obs.reset()
        self.assertIsNone(obs.collect()["repair_attempted"])

    def test_rubbish_is_ignored_rather_than_raising(self):
        obs.note("", 1)
        obs.note("repair_attempted", "many")
        obs.set_value("due_verified", None)
        self.assertIsNone(obs.collect()["repair_attempted"])
        self.assertIsNone(obs.collect()["due_verified"])

    def test_it_is_safe_to_count_from_several_threads(self):
        """ধাপ ১০খ put stages on their own threads; a counter that loses
        increments would under-report exactly when the run is busiest."""
        def work():
            for _ in range(200):
                obs.note("repair_attempted")

        threads = [threading.Thread(target=work) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(obs.collect()["repair_attempted"], 800)


class TheProviderQuota(unittest.TestCase):
    def test_an_unmetered_provider_is_not_reported_as_exhausted(self):
        """provider_health says -1 for "no daily ceiling". Printing -1, or 0,
        would read as a provider one call from being spent."""
        quota = obs.provider_quota()
        self.assertTrue(quota, "no providers were reported at all")
        for name, value in quota.items():
            with self.subTest(provider=name):
                self.assertTrue(value == "unmetered" or int(value) >= 0)

    def test_every_configured_provider_appears(self):
        from scanner import provider_router

        quota = obs.provider_quota()
        for name in provider_router.DEFAULT_PROVIDERS:
            self.assertIn(name, quota)

    def test_a_broken_router_costs_the_quota_line_and_nothing_else(self):
        from scanner import provider_router

        original = provider_router.snapshot
        provider_router.snapshot = lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("router down"))
        self.addCleanup(setattr, provider_router, "snapshot", original)
        block = obs.collect()
        self.assertEqual(block[obs.QUOTA_KEY], {})
        self.assertIn("skipped_fresh", block)


class TheCountersAreRecordedWhereTheyHappen(unittest.TestCase):
    """Recomputing a number somewhere else is how it drifts from the thing it
    is supposed to describe."""

    def test_link_health_records_both_sides_of_the_ttl(self):
        source = (ROOT / "scanner" / "fast_pipeline.py").read_text(
            encoding="utf-8")
        self.assertIn('movie_observability.set_value("skipped_fresh"', source)
        self.assertIn('movie_observability.set_value("due_verified"', source)

    def test_the_repair_queue_counts_every_attempt(self):
        source = (ROOT / "scanner" / "movie_repair_queue.py").read_text(
            encoding="utf-8")
        body = source[source.index("def record_attempt("):]
        body = body[:body.index("\ndef ", 10)]
        self.assertIn('movie_observability.note("repair_attempted")', body)
        # Counted before the repaired/not-repaired branch: the question is how
        # much repair work happened, not how much of it failed.
        self.assertLess(body.index("repair_attempted"), body.index("if repaired:"))

    def test_classification_records_its_five(self):
        source = (ROOT / "scanner" / "movies.py").read_text(encoding="utf-8")
        for name in ("series_signal_candidates", "confirmed_series",
                     "season_only_unknown_episode", "classification_cache_hits",
                     "classification_conflicts"):
            self.assertIn(name, source)

    def test_none_of_the_hooks_can_fail_a_scan(self):
        for module in ("fast_pipeline.py", "movie_repair_queue.py"):
            source = (ROOT / "scanner" / module).read_text(encoding="utf-8")
            hook = source[source.index("from scanner import movie_observability"):]
            hook = hook[:hook.index("except") + 40]
            self.assertIn("except Exception", hook)

    def test_the_block_reaches_the_scan_summary(self):
        source = (ROOT / "scanner" / "output.py").read_text(encoding="utf-8")
        self.assertIn('"movie_observability": movie_observability_block', source)
        self.assertIn("A-06 observability skipped", source)

    def test_it_is_collected_before_the_summary_that_carries_it(self):
        source = (ROOT / "scanner" / "output.py").read_text(encoding="utf-8")
        collect_at = source.index("movie_observability.collect()")
        summary_at = source.index("scan_summary: Dict[str, Any] = {")
        self.assertLess(collect_at, summary_at)


class TheClassificationCacheCountersAreReal(unittest.TestCase):
    """`classification_cache_hits` is the number that says whether ধারা ৪.৬'s
    "zero API cost" claim is true, so it has to count something real."""

    def _signal(self, key="bachelor-point"):
        return {"is_series_signal": True, "evidence_tier": "explicit_episode",
                "show_key": key, "base_show": "Bachelor Point"}

    def test_a_show_seen_for_the_first_time_is_not_a_cache_hit(self):
        from scanner import movie_classification

        store = {"shows": {}}
        summary = movie_classification.remember_from_signals(
            store, [self._signal()])
        self.assertEqual(summary["cache_hits"], 0)
        self.assertEqual(summary["recorded"], 1)

    def test_a_show_already_known_is_a_cache_hit(self):
        from scanner import movie_classification

        store = {"shows": {}}
        movie_classification.remember_from_signals(store, [self._signal()])
        summary = movie_classification.remember_from_signals(
            store, [self._signal()])
        self.assertEqual(summary["cache_hits"], 1)

    def test_a_stored_answer_that_disagrees_is_a_conflict(self):
        """ধারা ৪.৬ never resolves one silently, so it has to be countable."""
        from scanner import movie_classification

        store = {"shows": {"bachelor-point": {"content_type": "movie"}}}
        summary = movie_classification.remember_from_signals(
            store, [self._signal()])
        self.assertEqual(summary["conflicts"], 1)

    def test_agreement_is_not_a_conflict(self):
        from scanner import movie_classification

        store = {"shows": {"bachelor-point": {
            "content_type": movie_classification.TYPE_SERIES}}}
        summary = movie_classification.remember_from_signals(
            store, [self._signal()])
        self.assertEqual(summary["conflicts"], 0)
        self.assertEqual(summary["cache_hits"], 1)


if __name__ == "__main__":
    unittest.main()
