"""ধাপ ১০খ / ধারা ৪.৯ - the coordinator, and the four rules that make it safe.

The plan is blunt about why the rules are listed at all:

> ৪টি নিরাপত্তা শর্ত (ধারা ৪.৯) বাধ্যতামূলক — বিশেষত "আংশিক ফলাফল হারানো নয়",
> নইলে No-Loss গেট ভুল BLOCK করবে

So each rule gets tested as a rule, not as an implementation detail:

1. no stage mutates shared data - stages return fragments, one merger applies
2. one writer
3. two stages on one host share one limit, they do not each get one
4. ★ a partial stage's unreached items are not losses

and the deadlock rule underneath them: a stage is never handed a future, so
the graph completes at any pool width - including one.
"""
from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import movie_stages as ms  # noqa: E402


def stage(name, fn, **kwargs):
    return ms.Stage(name, fn, **kwargs)


class TheGraphRunsWhatIsIndependentAtOnce(unittest.TestCase):
    def test_two_independent_stages_overlap(self):
        """The whole point. If they serialise, the feature does nothing."""
        both_in = threading.Barrier(2, timeout=5)

        def run(context):
            both_in.wait()  # only returns if the other stage is also inside
            return context.result()

        results = ms.run_stages([stage("a", run), stage("b", run)])
        self.assertEqual(results["a"].status, ms.COMPLETE)
        self.assertEqual(results["b"].status, ms.COMPLETE)

    def test_a_dependent_stage_waits_for_what_it_needs(self):
        finished = []

        def first(context):
            time.sleep(0.05)
            finished.append("a")
            return context.result(fragments={"x": {"title": "A"}})

        def second(context):
            finished.append("b")
            return context.result()

        ms.run_stages([stage("a", first), stage("b", second, needs=["a"])])
        self.assertEqual(finished, ["a", "b"])

    def test_a_dependent_stage_is_handed_the_finished_result(self):
        seen = {}

        def first(context):
            return context.result(fragments={"x": {"year": 1999}})

        def second(context):
            seen.update(context.depends_on["a"].fragments)
            return context.result()

        ms.run_stages([stage("a", first), stage("b", second, needs=["a"])])
        self.assertEqual(seen, {"x": {"year": 1999}})

    def test_the_graph_completes_with_a_single_coordinator_thread(self):
        """The deadlock rule, as a test (ধারা ৩৪.৩).

        A design where a worker waits on another worker's future hangs here
        for ever. This one cannot, because a stage is never given a future -
        the coordinator resolves dependencies and passes results in.
        """
        chain = [
            stage("a", lambda context: context.result()),
            stage("b", lambda context: context.result(), needs=["a"]),
            stage("c", lambda context: context.result(), needs=["b"]),
        ]
        results = ms.run_stages(chain, max_concurrent=1)
        self.assertEqual(
            [result.status for result in results.values()], [ms.COMPLETE] * 3)

    def test_a_stage_that_raises_does_not_take_the_run_down(self):
        def boom(context):
            raise RuntimeError("provider exploded")

        results = ms.run_stages([
            stage("bad", boom),
            stage("good", lambda context: context.result(
                fragments={"x": {"title": "kept"}})),
        ])
        self.assertEqual(results["bad"].status, ms.FAILED)
        self.assertIn("provider exploded", results["bad"].note)
        self.assertEqual(results["good"].status, ms.COMPLETE)

    def test_a_stage_still_runs_when_its_dependency_failed(self):
        """Enrichment is additive. A stage with less to work from produces a
        smaller fragment, and a smaller fragment is not a loss."""
        def boom(context):
            raise RuntimeError("no")

        ran = []
        results = ms.run_stages([
            stage("a", boom),
            stage("b", lambda context: ran.append(1) or context.result(),
                  needs=["a"]),
        ])
        self.assertEqual(ran, [1])
        self.assertEqual(results["b"].status, ms.COMPLETE)

    def test_an_impossible_dependency_is_reported_not_hung(self):
        results = ms.run_stages([stage("b", lambda c: c.result(), needs=["a"])])
        self.assertEqual(results["b"].status, ms.FAILED)
        self.assertIn("dependency never satisfied", results["b"].note)

    def test_a_cycle_is_reported_not_hung(self):
        results = ms.run_stages([
            stage("a", lambda c: c.result(), needs=["b"]),
            stage("b", lambda c: c.result(), needs=["a"]),
        ])
        self.assertEqual(
            {result.status for result in results.values()}, {ms.FAILED})

    def test_no_stages_is_not_an_error(self):
        self.assertEqual(ms.run_stages([]), {})


class RuleThreeSharedHostsShareOneLimit(unittest.TestCase):
    """"একই হোস্টে দুটি stage গেলে limit যোগ হবে না - যৌথ সীমা মানা হবে।\""""

    def _peak(self, budget, stage_count, per_stage_items):
        live = {"now": 0, "peak": 0}
        guard = threading.Lock()

        def work(_item):
            with guard:
                live["now"] += 1
                live["peak"] = max(live["peak"], live["now"])
            time.sleep(0.02)
            with guard:
                live["now"] -= 1
            return True

        def run(context):
            context.map(
                work, list(range(per_stage_items)),
                key=str, host=lambda _item: "api.example.com",
            )
            return context.result()

        ms.run_stages(
            [stage(f"s{index}", run, workers=8) for index in range(stage_count)],
            budget=budget,
        )
        return live["peak"]

    def test_one_stage_never_exceeds_the_host_limit(self):
        budget = ms.HostBudget(limits={"api.example.com": 2})
        self.assertLessEqual(self._peak(budget, 1, 8), 2)

    def test_three_stages_on_one_host_still_share_that_limit(self):
        """Three stages, eight workers each: without a joint budget this would
        reach twenty-four requests at once against a limit of two."""
        budget = ms.HostBudget(limits={"api.example.com": 2})
        self.assertLessEqual(self._peak(budget, 3, 6), 2)

    def test_a_wide_limit_proves_the_narrow_one_is_what_is_holding(self):
        """Without this the test above passes just as well on a coordinator
        that never runs anything at once. Measured: the same three stages
        reach 18 concurrent with a wide limit and 2 with a limit of 2."""
        budget = ms.HostBudget(limits={"api.example.com": 99})
        self.assertGreater(self._peak(budget, 3, 6), 2)

    def test_different_hosts_do_not_block_each_other(self):
        budget = ms.HostBudget(limits={"a.example.com": 1, "b.example.com": 1})
        both_in = threading.Barrier(2, timeout=5)

        def run_on(host):
            def run(context):
                context.map(
                    lambda _item: both_in.wait(), [1],
                    key=str, host=lambda _item: host,
                )
                return context.result()
            return run

        results = ms.run_stages([
            stage("a", run_on("a.example.com")),
            stage("b", run_on("b.example.com")),
        ], budget=budget)
        self.assertEqual(
            {result.status for result in results.values()}, {ms.COMPLETE})

    def test_local_work_is_never_throttled(self):
        budget = ms.HostBudget(default=1)
        both_in = threading.Barrier(2, timeout=5)

        def run(context):
            context.map(lambda _item: both_in.wait(), [1], key=str)
            return context.result()

        results = ms.run_stages([stage("a", run), stage("b", run)],
                                budget=budget)
        self.assertEqual(
            {result.status for result in results.values()}, {ms.COMPLETE})

    def test_the_host_of_a_url_is_what_is_limited(self):
        self.assertEqual(ms.host_of("https://API.Example.com/3/movie"),
                         "api.example.com")
        self.assertEqual(ms.host_of(""), "")
        self.assertEqual(ms.host_of(None), "")


class RuleFourAPartialStageIsNotALoss(unittest.TestCase):
    """★ "আংশিক ফলাফল কখনো 'হারানো' হিসেবে গোনা হবে না\""""

    def test_naming_unreached_items_makes_a_stage_partial(self):
        """A stage cannot report success over work it skipped, even by
        mistake - the two facts are not allowed to disagree."""
        result = ms.StageResult("m", status=ms.COMPLETE, unattempted=["x"])
        self.assertEqual(result.status, ms.PARTIAL)
        self.assertTrue(result.is_partial)

    def test_unreached_items_are_listed_not_merged(self):
        result = ms.StageResult(
            "m", status=ms.PARTIAL,
            fragments={"a": {"year": 2001}}, unattempted=["b", "c"])
        report = ms.aggregate({"m": result})
        self.assertEqual(sorted(report["merged"]), ["a"])
        self.assertEqual(report["not_reached"], ["b", "c"])
        self.assertEqual(report["partial_stages"], ["m"])

    def test_an_unreached_item_keeps_every_field_it_had(self):
        """The failure this prevents: a slow run telling the No-Loss gate that
        live content disappeared."""
        items = [
            {"id": "a", "title": "Reached", "year": None},
            {"id": "b", "title": "Not reached", "year": 1988},
        ]
        report = ms.aggregate({"m": ms.StageResult(
            "m", status=ms.PARTIAL,
            fragments={"a": {"year": 2001}}, unattempted=["b"])})
        ms.apply_fragments(items, report, identity=lambda item: item["id"])
        self.assertEqual(items[0]["year"], 2001)
        self.assertEqual(items[1]["year"], 1988)
        self.assertEqual(items[1]["title"], "Not reached")

    def test_a_failed_stage_reaches_nothing_and_removes_nothing(self):
        items = [{"id": "a", "title": "Still here"}]
        results = ms.run_stages([
            stage("a", lambda context: (_ for _ in ()).throw(ValueError("x")))])
        report = ms.aggregate(results)
        ms.apply_fragments(items, report, identity=lambda item: item["id"])
        self.assertEqual(items, [{"id": "a", "title": "Still here"}])

    def test_the_line_for_the_log_says_it_out_loud(self):
        report = ms.aggregate({"m": ms.StageResult(
            "m", status=ms.PARTIAL, unattempted=["b"])})
        self.assertIn("not counted missing", ms.describe(report))

    def test_a_deadline_leaves_the_rest_unattempted_not_failed(self):
        def run(context):
            outcomes, unattempted = context.map(
                lambda item: time.sleep(0.02) or item, list(range(60)), key=str)
            return context.result(
                fragments={key: {"seen": True} for key, _ in outcomes},
                unattempted=unattempted,
                attempted=len(outcomes),
            )

        results = ms.run_stages(
            [stage("slow", run, workers=2)], deadline=time.monotonic() + 0.3)
        result = results["slow"]
        self.assertEqual(result.status, ms.PARTIAL)
        self.assertTrue(result.unattempted, "nothing was left unattempted")
        self.assertTrue(result.fragments, "nothing was attempted either")
        # Every item is accounted for as one or the other, never both.
        self.assertEqual(
            len(result.fragments) + len(result.unattempted), 60)
        self.assertEqual(
            set(result.fragments) & set(result.unattempted), set())

    def test_an_item_whose_work_raises_was_still_reached(self):
        """"Looked at and produced nothing" is a different fact from "never
        looked at" - only the second is protected by rule 4."""
        def run(context):
            outcomes, unattempted = context.map(
                lambda item: (_ for _ in ()).throw(RuntimeError("no")),
                [1, 2], key=str)
            return context.result(unattempted=unattempted,
                                  attempted=len(outcomes),
                                  note=f"{len(outcomes)} reached")

        results = ms.run_stages([stage("s", run)])
        self.assertEqual(results["s"].status, ms.COMPLETE)
        self.assertEqual(results["s"].attempted, 2)
        self.assertEqual(results["s"].unattempted, ())

    def test_an_empty_stage_is_complete_not_partial(self):
        def run(context):
            outcomes, unattempted = context.map(lambda item: item, [])
            return context.result(unattempted=unattempted)

        self.assertEqual(ms.run_stages([stage("s", run)])["s"].status,
                         ms.COMPLETE)


class RuleOneOnlyTheAggregatorMerges(unittest.TestCase):
    """"প্রতিটি stage result fragment ফেরত দেবে, আর Aggregator একা সেগুলো
    deterministic ক্রমে merge করবে।\""""

    def test_the_merge_order_is_the_declared_order_not_the_finish_order(self):
        """Two stages racing must not decide the catalogue between them."""
        def slow(context):
            time.sleep(0.05)
            return context.result(fragments={"x": {"title": "from first"}})

        def quick(context):
            return context.result(fragments={"x": {"title": "from second"}})

        stages = [stage("first", slow), stage("second", quick)]
        results = ms.run_stages(stages)
        report = ms.aggregate(results, order=[s.name for s in stages])
        self.assertEqual(report["merged"]["x"]["title"], "from first")

    def test_the_same_fragments_always_merge_the_same_way(self):
        results = {
            "b": ms.StageResult("b", fragments={"x": {"year": 2002}}),
            "a": ms.StageResult("a", fragments={"x": {"year": 1999}}),
        }
        first = ms.aggregate(results, order=["a", "b"])
        second = ms.aggregate(dict(reversed(list(results.items()))),
                              order=["a", "b"])
        self.assertEqual(first["merged"], second["merged"])
        self.assertEqual(first["merged"]["x"]["year"], 1999)

    def test_a_second_opinion_is_recorded_rather_than_resolved(self):
        report = ms.aggregate({
            "a": ms.StageResult("a", fragments={"x": {"year": 1999}}),
            "b": ms.StageResult("b", fragments={"x": {"year": 2002}}),
        }, order=["a", "b"])
        self.assertEqual(report["merged"]["x"]["year"], 1999)
        self.assertEqual(len(report["conflicts"]), 1)
        self.assertEqual(report["conflicts"][0]["refused"], "b")
        self.assertEqual(report["conflicts"][0]["kept"], "a")

    def test_agreeing_stages_are_not_a_conflict(self):
        report = ms.aggregate({
            "a": ms.StageResult("a", fragments={"x": {"year": 1999}}),
            "b": ms.StageResult("b", fragments={"x": {"year": 1999}}),
        }, order=["a", "b"])
        self.assertEqual(report["conflicts"], [])

    def test_every_field_is_credited_to_the_stage_that_supplied_it(self):
        report = ms.aggregate({
            "meta": ms.StageResult("meta", fragments={"x": {"year": 1999}}),
            "art": ms.StageResult("art", fragments={"x": {"poster": "p.jpg"}}),
        }, order=["meta", "art"])
        self.assertEqual(report["sources"]["x"],
                         {"year": "meta", "poster": "art"})

    def test_empty_values_are_never_merged_over_nothing(self):
        """A provider answering "" must not occupy the field a later one could
        have filled."""
        report = ms.aggregate({
            "a": ms.StageResult("a", fragments={"x": {"year": None,
                                                     "genres": [],
                                                     "title": ""}}),
            "b": ms.StageResult("b", fragments={"x": {"year": 1999}}),
        }, order=["a", "b"])
        self.assertEqual(report["merged"]["x"], {"year": 1999})


class RuleTwoOneWriter(unittest.TestCase):
    """"কোনো worker নিজে production JSON লিখবে না, push করবে না। শুধু Stage C
    লেখে।" - so the only path from a fragment to an item is this one."""

    def test_applying_fragments_is_fill_only(self):
        items = [{"id": "a", "title": "Local title", "year": None}]
        report = ms.aggregate({"m": ms.StageResult(
            "m", fragments={"a": {"title": "Provider title", "year": 1999}})})
        ms.apply_fragments(items, report, identity=lambda item: item["id"])
        self.assertEqual(items[0]["title"], "Local title")
        self.assertEqual(items[0]["year"], 1999)

    def test_an_item_no_stage_mentioned_is_untouched(self):
        items = [{"id": "a", "title": "t"}, {"id": "b", "title": "u"}]
        report = ms.aggregate({"m": ms.StageResult(
            "m", fragments={"a": {"year": 1999}})})
        touched = ms.apply_fragments(
            items, report, identity=lambda item: item["id"])
        self.assertEqual(touched, 1)
        self.assertEqual(items[1], {"id": "b", "title": "u"})

    def test_nothing_is_applied_when_no_stage_produced_anything(self):
        items = [{"id": "a"}]
        self.assertEqual(
            ms.apply_fragments(items, ms.aggregate({}),
                               identity=lambda item: item["id"]),
            0,
        )
        self.assertEqual(items, [{"id": "a"}])

    def test_a_stage_holding_the_items_cannot_change_them_through_the_result(self):
        """A fragment is copied out of the stage, so a stage that keeps a
        reference and edits it afterwards changes nothing."""
        mutable = {"year": 1999}
        result = ms.StageResult("m", fragments={"a": mutable})
        mutable["year"] = 2002
        self.assertEqual(result.fragments["a"]["year"], 1999)


class TheReportIsReadable(unittest.TestCase):
    def test_each_stage_reports_what_it_did(self):
        report = ms.aggregate({
            "meta": ms.StageResult("meta", fragments={"x": {"year": 1}},
                                   attempted=1, seconds=1.234),
        })
        entry = report["stages"][0]
        self.assertEqual(entry["stage"], "meta")
        self.assertEqual(entry["enriched"], 1)
        self.assertEqual(entry["seconds"], 1.23)

    def test_the_log_line_names_every_stage(self):
        report = ms.aggregate({
            "meta": ms.StageResult("meta", fragments={"x": {"year": 1}}),
            "art": ms.StageResult("art"),
        }, order=["meta", "art"])
        line = ms.describe(report)
        self.assertIn("meta", line)
        self.assertIn("art", line)

    def test_nothing_to_say_says_nothing(self):
        self.assertEqual(ms.describe(ms.aggregate({})), "")

    def test_a_result_describes_itself_for_a_failure_message(self):
        result = ms.StageResult("meta", status=ms.PARTIAL,
                                fragments={"x": {}}, unattempted=["y"],
                                note="budget spent")
        text = result.describe()
        self.assertIn("meta", text)
        self.assertIn("not reached", text)
        self.assertIn("budget spent", text)


if __name__ == "__main__":
    unittest.main()
