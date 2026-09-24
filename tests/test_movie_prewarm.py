"""ধাপ ১০খ - the metadata stage that runs beside stream verification.

What it must never do is more interesting than what it does. It spends real
provider quota from a background thread while the scan's own thread is busy
verifying links, so the tests here are mostly about restraint:

  - it warms the site's existing films, not this scan's unproven ones
  - it never asks about a film that is on a failure cooldown
  - it writes nothing itself; `commit` writes, afterwards, once
  - stopping early loses nothing - a film it did not reach keeps its metadata

and the one that pays for the feature: what it warms, the ordinary pass no
longer has to pay for.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import movie_metadata_cache as cache  # noqa: E402
from scanner import movie_prewarm as prewarm  # noqa: E402
from scanner import movie_stages  # noqa: E402

NOW = dt.datetime(2026, 9, 24, 4, 37, tzinfo=dt.timezone.utc)


def card(name, year=2020, **extra):
    item = {"name": name, "year": year, "category": "Bangla"}
    item.update(extra)
    return item


def store_with(records):
    path = Path(tempfile.mkdtemp()) / "movie-metadata-cache.json"
    path.write_text(json.dumps({"version": 1, "movies": records}),
                    encoding="utf-8")
    return str(path)


def identity(item):
    return cache.canonical_identity(item)


class TheCacheSaysWhatIsWorthLookingUp(unittest.TestCase):
    """`lookup_state` is `enrich`'s own pass-one decision, exposed - so the
    stage and the ordinary pass cannot drift into disagreeing."""

    def test_nothing_cached_is_missing(self):
        self.assertEqual(cache.lookup_state(None, NOW), cache.LOOKUP_MISSING)

    def test_an_empty_record_is_missing(self):
        self.assertEqual(cache.lookup_state({}, NOW), cache.LOOKUP_MISSING)

    def test_a_recent_failure_is_a_cooldown_not_a_gap(self):
        """The failure this prevents: asking again about a film nobody could
        resolve, and earning it a fresh seven-day cooldown for the trouble."""
        record = {"metadata_last_attempt_at": (NOW - dt.timedelta(days=1)).isoformat()}
        self.assertEqual(cache.lookup_state(record, NOW), cache.LOOKUP_COOLDOWN)

    def test_an_old_failure_may_be_tried_again(self):
        record = {"metadata_last_attempt_at": (NOW - dt.timedelta(days=90)).isoformat()}
        self.assertEqual(cache.lookup_state(record, NOW), cache.LOOKUP_MISSING)

    def test_fresh_metadata_is_fresh(self):
        record = {"tmdb_id": 11, "metadata_updated_at": NOW.isoformat()}
        cache._stamp_versions(record)
        self.assertEqual(cache.lookup_state(record, NOW), cache.LOOKUP_FRESH)

    def test_metadata_past_its_ttl_is_due(self):
        stale = (NOW - dt.timedelta(days=4000)).isoformat()
        record = {"tmdb_id": 11, "metadata_updated_at": stale}
        self.assertEqual(cache.lookup_state(record, NOW), cache.LOOKUP_DUE)


class ThePlanChoosesWhatToWarm(unittest.TestCase):
    def test_a_film_with_no_metadata_is_chosen(self):
        report = prewarm.plan([card("Rongin Shurma")], cache_path=store_with({}),
                              now=NOW, remaining_quota=1000)
        self.assertEqual(report["missing"], 1)
        self.assertEqual(len(report["identities"]), 1)

    def test_a_film_with_fresh_metadata_is_left_alone(self):
        item = card("Rongin Shurma")
        record = {"tmdb_id": 7, "metadata_updated_at": NOW.isoformat()}
        cache._stamp_versions(record)
        report = prewarm.plan(
            [item], cache_path=store_with({identity(item): record}),
            now=NOW, remaining_quota=1000)
        self.assertEqual(report["fresh"], 1)
        self.assertEqual(report["identities"], [])
        self.assertEqual(report["reason"], "nothing due")

    def test_a_film_on_cooldown_is_never_asked_about(self):
        item = card("Unresolvable")
        record = {"metadata_last_attempt_at": (NOW - dt.timedelta(days=1)).isoformat()}
        report = prewarm.plan(
            [item], cache_path=store_with({identity(item): record}),
            now=NOW, remaining_quota=1000)
        self.assertEqual(report["cooldown"], 1)
        self.assertEqual(report["identities"], [])

    def test_films_with_nothing_come_before_refreshes(self):
        """The same priority `enrich` uses: an entry with nothing at all is
        worth more than a refresh of one that already reads correctly."""
        empty = card("Has Nothing")
        stale = card("Is Stale")
        records = {identity(stale): {
            "tmdb_id": 3,
            "metadata_updated_at": (NOW - dt.timedelta(days=4000)).isoformat(),
        }}
        report = prewarm.plan(
            [stale, empty], cache_path=store_with(records), now=NOW,
            remaining_quota=1000,
            settings={"movie_parallel_stages": {"max_lookups": 1}})
        self.assertEqual(
            [entry["identity"] for entry in report["identities"]],
            [identity(empty)],
        )
        self.assertEqual(report["deferred"], 1)

    def test_the_budget_is_a_slice_of_the_quota_not_all_of_it(self):
        """A newly discovered film with no metadata is found later and is
        worth more than any refresh, so the warm-up may not take everything."""
        items = [card(f"Film {index}") for index in range(100)]
        report = prewarm.plan(items, cache_path=store_with({}), now=NOW,
                              remaining_quota=50)
        self.assertEqual(report["budget"], 20)  # 50 * 0.4
        self.assertEqual(len(report["identities"]), 20)

    def test_no_quota_means_no_lookups(self):
        report = prewarm.plan([card("A")], cache_path=store_with({}), now=NOW,
                              remaining_quota=0)
        self.assertEqual(report["identities"], [])
        self.assertEqual(report["reason"], "no lookup budget")

    def test_it_can_be_switched_off(self):
        report = prewarm.plan(
            [card("A")], {"movie_parallel_stages": {"enabled": False}},
            cache_path=store_with({}), now=NOW, remaining_quota=1000)
        self.assertFalse(report["enabled"])
        self.assertEqual(report["identities"], [])
        self.assertIn("disabled", report["reason"])

    def test_one_film_listed_twice_is_one_lookup(self):
        item = card("Twice")
        report = prewarm.plan([item, dict(item)], cache_path=store_with({}),
                              now=NOW, remaining_quota=1000)
        self.assertEqual(len(report["identities"]), 1)

    def test_a_card_with_no_identity_is_skipped_not_fatal(self):
        report = prewarm.plan([{}, "not a dict", card("Real")],
                              cache_path=store_with({}), now=NOW,
                              remaining_quota=1000)
        self.assertEqual(len(report["identities"]), 1)
        self.assertNotIn(
            "fallback::0",
            [entry["identity"] for entry in report["identities"]],
        )


class TheStageReturnsFragmentsAndWritesNothing(unittest.TestCase):
    def _run(self, items, lookup, *, cache_path=None, **plan_kwargs):
        plan_kwargs.setdefault("remaining_quota", 1000)
        plan_report = prewarm.plan(
            items, cache_path=cache_path or store_with({}), now=NOW,
            **plan_kwargs)
        results = movie_stages.run_stages(
            [prewarm.stage(plan_report, lookup=lookup)])
        return plan_report, results, movie_stages.aggregate(
            results, order=[prewarm.STAGE_NAME])

    def test_what_a_provider_answers_becomes_a_fragment(self):
        item = card("Rongin Shurma")
        _plan, _results, report = self._run(
            [item], lambda movie: {"tmdb_id": 42, "genres": ["Drama"]})
        self.assertEqual(report["merged"][identity(item)]["tmdb_id"], 42)

    def test_the_movie_card_itself_is_never_modified(self):
        """Rule 1. The stage is handed a copy; the real card is Stage C's."""
        item = card("Rongin Shurma")
        before = dict(item)
        self._run([item], lambda movie: {"tmdb_id": 42})
        self.assertEqual(item, before)

    def test_the_cache_is_not_written_by_the_stage(self):
        """Rule 2. Only `commit` writes."""
        path = store_with({})
        self._run([card("A")], lambda movie: {"tmdb_id": 1}, cache_path=path)
        self.assertEqual(json.loads(Path(path).read_text(encoding="utf-8"))
                         ["movies"], {})

    def test_a_film_nobody_could_resolve_is_recorded_as_attempted(self):
        """Empty fragment, not absence: the retry cooldown is counted from
        the attempt, and an attempt that is not recorded is one that repeats
        every run."""
        item = card("Unresolvable")
        _plan, results, report = self._run([item], lambda movie: None)
        self.assertIn(identity(item), report["merged"])
        self.assertEqual(report["merged"][identity(item)], {})
        self.assertEqual(report["not_reached"], [])
        self.assertIn("1 unresolved", results[prewarm.STAGE_NAME].note)

    def test_a_provider_that_raises_costs_one_film_not_the_stage(self):
        def explode(movie):
            if "Bad" in movie["name"]:
                raise RuntimeError("provider down")
            return {"tmdb_id": 9}

        good = card("Good")
        bad = card("Bad")
        _plan, results, report = self._run([bad, good], explode)
        self.assertEqual(results[prewarm.STAGE_NAME].status,
                         movie_stages.COMPLETE)
        self.assertEqual(report["merged"][identity(good)]["tmdb_id"], 9)
        # Reached and answered nothing - NOT "never tried". Confusing the two
        # would either repeat the doomed lookup every run or hide an outage.
        self.assertEqual(report["merged"][identity(bad)], {})
        self.assertEqual(report["not_reached"], [])


class StoppingEarlyLosesNothing(unittest.TestCase):
    """Rule 4, where it actually bites: the stage is stopped the moment
    verification finishes, so it is *designed* to be interrupted."""

    def test_the_halt_leaves_the_rest_unattempted(self):
        started = threading.Event()

        def slow(movie):
            started.set()
            time.sleep(0.2)
            return {"tmdb_id": 1}

        items = [card(f"Film {index}") for index in range(20)]
        handle = prewarm.start(items, lookup=slow, cache_path=store_with({}),
                               remaining_quota=1000, now=NOW)
        started.wait(timeout=5)
        summary = prewarm.finish(handle, now=NOW)

        self.assertTrue(summary["ran"])
        self.assertEqual(summary["status"], movie_stages.PARTIAL)
        self.assertGreater(summary["not_reached"], 0)
        self.assertEqual(
            summary["not_reached"] + summary["written"], summary["planned"])

    def test_what_it_did_finish_is_still_written(self):
        path = store_with({})
        item = card("Finished")
        # A realistic provider answer: PART 19 will not write an external id
        # on anything less than a high-confidence match, and a candidate that
        # names no title cannot earn one. This is the contract the real
        # `resolve_metadata` already meets.
        resolved = {"tmdb_id": 77, "title": "Finished", "year": 2020,
                    "genres": ["Drama"]}
        handle = prewarm.start([item], lookup=lambda movie: dict(resolved),
                               cache_path=path, remaining_quota=1000, now=NOW)
        # Let it run to the end first: this is about what a COMPLETED stage
        # writes. The halt is what the test below is about, and `finish` sets
        # it the instant it is called - in a real scan that instant is minutes
        # of verification later.
        handle.thread.join(timeout=10)
        summary = prewarm.finish(handle, cache_path=path, now=NOW)
        stored = json.loads(Path(path).read_text(encoding="utf-8"))["movies"]
        self.assertEqual(stored[identity(item)]["tmdb_id"], 77)
        self.assertEqual(summary["resolved"], 1)

    def test_an_unreached_film_keeps_the_metadata_it_had(self):
        item = card("Untouched")
        existing = {"tmdb_id": 5, "genres": ["Drama"],
                    "metadata_updated_at": (NOW - dt.timedelta(days=4000)).isoformat()}
        path = store_with({identity(item): existing})
        report = movie_stages.aggregate({prewarm.STAGE_NAME: movie_stages.StageResult(
            prewarm.STAGE_NAME, unattempted=[identity(item)])})
        prewarm.commit(report, {"identities": []}, cache_path=path, now=NOW)
        stored = json.loads(Path(path).read_text(encoding="utf-8"))["movies"]
        self.assertEqual(stored[identity(item)]["tmdb_id"], 5)
        self.assertEqual(stored[identity(item)]["genres"], ["Drama"])

    def test_nothing_to_warm_starts_no_thread_at_all(self):
        handle = prewarm.start([], cache_path=store_with({}))
        self.assertFalse(handle.started)
        summary = prewarm.finish(handle)
        self.assertFalse(summary["ran"])

    def test_a_broken_plan_is_a_skipped_warm_up_not_a_failed_scan(self):
        handle = prewarm.start([card("A")], cache_path="/nope/\0/bad.json")
        prewarm.finish(handle)  # must not raise

    def test_a_provider_that_never_answers_does_not_hold_the_publish(self):
        release = threading.Event()

        in_flight = threading.Event()

        def hangs(movie):
            in_flight.set()
            release.wait(timeout=30)
            return None

        handle = prewarm.start([card("A")], lookup=hangs,
                               cache_path=store_with({}), remaining_quota=1000,
                               now=NOW)
        self.assertTrue(in_flight.wait(timeout=5), "the lookup never started")
        summary = prewarm.finish(handle, timeout=0.3)
        release.set()
        self.assertTrue(summary["abandoned"])


class TheProviderOutageCase(unittest.TestCase):
    def test_films_skipped_for_an_outage_are_not_recorded_as_failures(self):
        """An outage must not leave a seven-day cooldown on films that were
        never actually tried - the same rule `enrich` already follows."""
        path = store_with({})
        items = [card("A"), card("B")]
        plan_report = prewarm.plan(items, cache_path=path, now=NOW,
                                   remaining_quota=1000)

        import scanner.provider_health as health
        original = health.any_metadata_provider_available
        health.any_metadata_provider_available = lambda *a, **k: False
        try:
            results = movie_stages.run_stages([prewarm.stage(plan_report)])
        finally:
            health.any_metadata_provider_available = original

        report = movie_stages.aggregate(results, order=[prewarm.STAGE_NAME])
        prewarm.commit(report, plan_report, cache_path=path, now=NOW)
        stored = json.loads(Path(path).read_text(encoding="utf-8"))["movies"]
        self.assertEqual(stored, {})
        self.assertEqual(len(report["not_reached"]), 2)


class TheLogLineIsHonest(unittest.TestCase):
    def test_it_says_when_it_did_not_run(self):
        line = prewarm.describe({"ran": False, "reason": "nothing due"})
        self.assertIn("skipped", line)
        self.assertIn("nothing due", line)

    def test_it_says_what_was_not_reached(self):
        line = prewarm.describe({
            "ran": True, "resolved": 5, "planned": 20, "seconds": 12.0,
            "not_reached": 15})
        self.assertIn("previous metadata kept", line)

    def test_it_says_what_was_left_for_the_ordinary_pass(self):
        line = prewarm.describe({
            "ran": True, "resolved": 5, "planned": 5, "seconds": 3.0,
            "deferred": 40})
        self.assertIn("40 left for the ordinary pass", line)

    def test_nothing_to_say_says_nothing(self):
        self.assertEqual(prewarm.describe({}), "")


class TheWiringPutsItBesideVerification(unittest.TestCase):
    """If it is started after the blocking call, or joined before it, the
    feature is a slower scan with extra machinery."""

    SCAN = (ROOT / "scan.py").read_text(encoding="utf-8")

    def test_it_starts_before_the_verification_call(self):
        start = self.SCAN.index("_start_movie_metadata_stage(mode_clean)")
        verify = self.SCAN.index("bd_summary = run_fast_verification_pipeline()")
        self.assertLess(start, verify)

    def test_it_is_joined_after_the_verification_call(self):
        verify = self.SCAN.index("bd_summary = run_fast_verification_pipeline()")
        finish = self.SCAN.index("_finish_movie_metadata_stage(movie_metadata_stage)")
        self.assertLess(verify, finish)

    def test_it_is_joined_before_the_movie_catalogue_is_built(self):
        """The warm cache is only worth having if it is warm before the pass
        that reads it."""
        finish = self.SCAN.index("_finish_movie_metadata_stage(movie_metadata_stage)")
        process = self.SCAN.index("movies_data = process_movies()")
        self.assertLess(finish, process)

    def test_neither_half_can_fail_a_scan(self):
        for marker in ("movie metadata stage skipped",
                       "movie metadata stage did not complete"):
            self.assertIn(marker, self.SCAN)

    def test_it_does_not_run_in_the_live_sport_modes(self):
        """Live TV and Live Sports runs must not pay for movie metadata."""
        self.assertIn('if mode not in {"movies", "all"}:', self.SCAN)

    def test_the_stage_module_writes_nothing_but_the_metadata_cache(self):
        """Rule 2, as a source guard: the only write in this module goes
        through `movie_metadata_cache.save`, from `commit`, on the scan's own
        thread. A worker that published anything would be the bug the rule
        names, so this reads the calls rather than the prose."""
        import ast

        source = (ROOT / "scanner" / "movie_prewarm.py").read_text(
            encoding="utf-8")
        tree = ast.parse(source)

        def dotted(node):
            if isinstance(node, ast.Name):
                return node.id
            if isinstance(node, ast.Attribute):
                return f"{dotted(node.func if isinstance(node, ast.Call) else node.value)}.{node.attr}"
            if isinstance(node, ast.Call):
                return dotted(node.func)
            return ""

        called = set()
        opens = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = dotted(node)
            called.add(name)
            if name == "open":
                mode = node.args[1].value if len(node.args) > 1 else "r"
                opens.append(mode)

        for forbidden in ("write_text", "write_bytes", "os.replace",
                          "shutil.move", "subprocess.run", "json.dump"):
            self.assertNotIn(forbidden, called,
                             msg=f"unexpected writing path: {forbidden}")
        for mode in opens:
            self.assertNotIn("w", mode, msg="a file is opened for writing")
            self.assertNotIn("a", mode, msg="a file is opened for appending")
        self.assertIn("movie_metadata_cache.save", called,
                      msg="the one legitimate write is missing")

    def test_the_settings_block_exists_and_is_on(self):
        import json

        settings = json.loads(
            (ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
        block = settings["movie_parallel_stages"]
        self.assertTrue(block["enabled"])
        self.assertEqual(block["workers"], 1,
                         "provider_health pacing is unlocked shared state")

    def test_the_defaults_in_code_match_the_shipped_settings(self):
        import json

        settings = json.loads(
            (ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
        shipped = prewarm.settings_for(settings)
        defaults = prewarm.settings_for({})
        self.assertEqual(shipped, defaults)

if __name__ == "__main__":
    unittest.main()
