"""A stale run must not rewind shared source health.

PROMPT 16, and a production fault rather than a hypothesis. `collect_candidates`
loads `state/source-health.json` as its checkout carried it, updates the rows
for the sources it fetched, and writes every row back; the push step then
restores this run's generated files whole over the rebase. For a file this run
owns that is right. For a file every run writes it puts an old copy of every
OTHER run's observations back.

Measured over the last 500 publishes of that file:

    publishes that rewound at least one source      80
    rewind seconds      median 123   p90 5,758   max 329,102  (91 hours)
    rows lost                                        0
    streak counters rewound                        235
    last_scan rewound past the 45-minute outage window       318
    last_productive rewound past the 6-hour memory floor     138

The two window numbers are the fault rather than the untidiness:
`scanner/source_outage.py` refuses to read a health record older than
`record_max_age_minutes`, and refuses to call silence an outage when
`last_productive` is older than `memory_hours`. Runs were moving both inputs
backwards for sources they had never fetched - a `today` scan rewound the
channels source `sportlive-jiotv-targeted` by 91 hours.

The rule asks what each side CHANGED since the base, never which file is newer,
so "this run did not fetch that source" needs no rule of its own: the row is
byte for byte the base's, and a side that changed nothing can never win.
"""
import copy
import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scanner import source_health_settlement as settlement   # noqa: E402
from scanner import source_outage                            # noqa: E402


def _merge_module():
    path = ROOT / "scripts" / "merge-published-events.py"
    spec = importlib.util.spec_from_file_location("merge_published_events", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BASE_TIME = "2026-09-06T19:40:00.000000+00:00"
OURS_TIME = "2026-09-06T19:46:44.442105+00:00"
THEIRS_TIME = "2026-09-06T19:48:10.957313+00:00"


def row(source_id="feed-a", *, when=BASE_TIME, status="success", raw_items=5,
        failures=0, unproductive=0, total=100, average=200,
        last_success=None, last_productive=None, productive_items=None,
        pipeline="today_match", **extra):
    record = {
        "source_id": source_id,
        "source_name": source_id,
        "url": "https://feed.test/%s.json" % source_id,
        "pipeline": pipeline,
        "status": status,
        "last_scan": when,
        "http_status": 200 if status in ("success", "success_empty") else 0,
        "attempts": 1,
        "response_time_ms": average,
        "detected_format": "json",
        "raw_items": raw_items,
        "error": "",
        "total_scans": total,
        "average_response_time_ms": average,
        "consecutive_failures": failures,
        "consecutive_unproductive": unproductive,
    }
    record["last_success"] = (
        when if last_success is None and status in ("success", "success_empty")
        else last_success)
    if record["last_success"] is None:
        record.pop("last_success")
    productive = (when if last_productive is None and raw_items > 0
                  else last_productive)
    if productive:
        record["last_productive"] = productive
        record["last_productive_items"] = (
            raw_items if productive_items is None else productive_items)
    record.update(extra)
    return record


def payload(*rows, updated_at="", last_mode="today"):
    return {
        "updated_at": updated_at or THEIRS_TIME,
        "last_mode": last_mode,
        "sources": {r["source_id"]: copy.deepcopy(r) for r in rows},
    }


class TheObservationIsWhatChangedTests(unittest.TestCase):
    """Requirement 4 and 6. `observed` is the whole of the ownership rule."""

    def test_a_changed_last_scan_is_an_observation(self):
        self.assertTrue(settlement.observed(row(when=OURS_TIME), row()))

    def test_an_unchanged_row_is_not_an_observation(self):
        self.assertFalse(settlement.observed(row(), row()))

    def test_a_row_the_base_never_had_is_an_observation(self):
        self.assertTrue(settlement.observed(row(when=OURS_TIME), None))

    def test_a_missing_row_observes_nothing(self):
        self.assertFalse(settlement.observed(None, row()))

    def test_cannot_check_is_not_healthy_and_not_dead(self):
        # A run that did not fetch a source says nothing about it, so the row
        # it carries is neither evidence of health nor of failure.
        base = payload(row("feed-a"), row("feed-b"))
        ours = payload(row("feed-a", when=OURS_TIME), row("feed-b"))
        theirs = payload(row("feed-a"),
                         row("feed-b", when=THEIRS_TIME, status="failed",
                             raw_items=0, failures=3))
        settled, _ = settlement.settle(base, ours, theirs)
        self.assertEqual(settled["feed-a"]["last_scan"], OURS_TIME)
        self.assertEqual(settled["feed-b"]["status"], "failed")
        self.assertEqual(settled["feed-b"]["consecutive_failures"], 3)


class AStaleObservationNeverWinsTests(unittest.TestCase):
    """Requirement 4, 5 and 7. The four orderings that matter."""

    def _settle(self, ours_row, theirs_row, base_row=None):
        base = payload(base_row or row())
        settled, report = settlement.settle(
            base, payload(ours_row), payload(theirs_row))
        return settled["feed-a"], report

    def test_stale_failure_cannot_overwrite_a_newer_failure(self):
        settled, report = self._settle(
            row(when=OURS_TIME, status="failed", raw_items=0, failures=1),
            row(when=THEIRS_TIME, status="failed", raw_items=0, failures=1))
        self.assertEqual(settled["last_scan"], THEIRS_TIME)
        self.assertEqual(report["stale_local_updates_rejected"], 1)

    def test_stale_failure_cannot_overwrite_a_newer_recovery(self):
        settled, _ = self._settle(
            row(when=OURS_TIME, status="failed", raw_items=0, failures=1),
            row(when=THEIRS_TIME, status="success", raw_items=7))
        self.assertEqual(settled["status"], "success")
        self.assertEqual(settled["last_scan"], THEIRS_TIME)
        self.assertEqual(settled["consecutive_failures"], 0)

    def test_stale_success_cannot_overwrite_a_newer_failure(self):
        settled, _ = self._settle(
            row(when=OURS_TIME, status="success", raw_items=7),
            row(when=THEIRS_TIME, status="failed", raw_items=0, failures=1))
        self.assertEqual(settled["status"], "failed")
        self.assertEqual(settled["consecutive_failures"], 1)

    def test_stale_unproductive_cannot_overwrite_a_newer_productive(self):
        settled, _ = self._settle(
            row(when=OURS_TIME, status="success_empty", raw_items=0,
                unproductive=1),
            row(when=THEIRS_TIME, status="success", raw_items=9))
        self.assertEqual(settled["raw_items"], 9)
        self.assertEqual(settled["consecutive_unproductive"], 0)
        self.assertEqual(settled["last_productive"], THEIRS_TIME)

    def test_a_newer_local_observation_is_applied(self):
        settled, report = self._settle(
            row(when=THEIRS_TIME, status="success", raw_items=9),
            row(when=OURS_TIME, status="failed", raw_items=0, failures=1))
        self.assertEqual(settled["status"], "success")
        self.assertEqual(report["local_newer_applied"], 1)
        self.assertEqual(report["stale_local_updates_rejected"], 0)

    def test_a_tie_is_deterministic_and_conservative(self):
        settled, report = self._settle(
            row(when=THEIRS_TIME, status="failed", raw_items=0, failures=1),
            row(when=THEIRS_TIME, status="success", raw_items=9))
        self.assertEqual(settled["status"], "success")
        self.assertEqual(report["tied_observations"], 1)
        again, _ = self._settle(
            row(when=THEIRS_TIME, status="failed", raw_items=0, failures=1),
            row(when=THEIRS_TIME, status="success", raw_items=9))
        self.assertEqual(again, settled)


class TheTimesOnlyMoveForwardsTests(unittest.TestCase):
    """Requirement 3B. Last-known facts are last-known, whoever read them."""

    def test_last_success_never_goes_backwards(self):
        base = payload(row())
        ours = payload(row(when=OURS_TIME, status="success"))
        theirs = payload(row(when=THEIRS_TIME, status="failed", raw_items=0,
                             failures=1, last_success=OURS_TIME))
        settled, _ = settlement.settle(base, ours, theirs)
        self.assertEqual(settled["feed-a"]["last_success"], OURS_TIME)

    def test_last_productive_never_goes_backwards(self):
        base = payload(row())
        ours = payload(row(when=OURS_TIME, status="success_empty", raw_items=0,
                           last_productive=THEIRS_TIME, productive_items=12))
        theirs = payload(row(when=THEIRS_TIME, status="success_empty",
                             raw_items=0, last_productive=BASE_TIME,
                             productive_items=3))
        settled, _ = settlement.settle(base, ours, theirs)
        self.assertEqual(settled["feed-a"]["last_productive"], THEIRS_TIME)
        self.assertEqual(settled["feed-a"]["last_productive_items"], 12)

    def test_the_item_count_travels_with_the_time_it_belongs_to(self):
        base = payload(row())
        ours = payload(row(when=OURS_TIME, raw_items=41))
        theirs = payload(row(when=THEIRS_TIME, raw_items=2))
        settled, _ = settlement.settle(base, ours, theirs)
        self.assertEqual(settled["feed-a"]["last_productive"], THEIRS_TIME)
        self.assertEqual(settled["feed-a"]["last_productive_items"], 2)

    def test_last_failure_never_goes_backwards(self):
        base = payload(row())
        ours = payload(row(when=OURS_TIME, status="failed", raw_items=0,
                           failures=1, last_failure=OURS_TIME))
        theirs = payload(row(when=THEIRS_TIME, status="success", raw_items=4))
        settled, _ = settlement.settle(base, ours, theirs)
        self.assertEqual(settled["feed-a"]["last_failure"], OURS_TIME)

    def test_the_newest_observation_never_moves_backwards(self):
        base = payload(row())
        for first, second in ((OURS_TIME, THEIRS_TIME), (THEIRS_TIME, OURS_TIME)):
            settled, _ = settlement.settle(
                base, payload(row(when=first)), payload(row(when=second)))
            self.assertEqual(settled["feed-a"]["last_scan"], max(first, second))


class TheStreaksFollowTheSequenceTests(unittest.TestCase):
    """Requirement 7. Two observations, in the order they happened."""

    def _streak(self, field, base_value, ours_value, theirs_value,
                ours_when=OURS_TIME, theirs_when=THEIRS_TIME):
        kwargs = {"failures": 0, "unproductive": 0}
        key = "failures" if field == "consecutive_failures" else "unproductive"
        base = payload(row(**{**kwargs, key: base_value}))
        ours = payload(row(when=ours_when, status="failed", raw_items=0,
                           **{**kwargs, key: ours_value}))
        theirs = payload(row(when=theirs_when, status="failed", raw_items=0,
                             **{**kwargs, key: theirs_value}))
        settled, _ = settlement.settle(base, ours, theirs)
        return settled["feed-a"][field]

    def test_two_failures_count_as_two(self):
        # Both runs read `base + 1` from the same base. Taking either loses one
        # real failure; max() loses it too.
        self.assertEqual(
            self._streak("consecutive_failures", 114, 115, 115), 116)

    def test_two_unproductive_scans_count_as_two(self):
        self.assertEqual(
            self._streak("consecutive_unproductive", 31, 32, 32), 33)

    def test_the_newest_reset_ends_the_streak(self):
        # theirs is the newer observation and it saw a success.
        self.assertEqual(self._streak("consecutive_failures", 114, 115, 0), 0)

    def test_a_reset_then_one_failure_is_one(self):
        # ours is older and reset it; theirs incremented from the stale base.
        self.assertEqual(self._streak("consecutive_failures", 114, 0, 115), 1)

    def test_a_recovery_resets_the_failure_streak(self):
        base = payload(row(failures=9, status="failed", raw_items=0))
        ours = payload(row(when=OURS_TIME, status="failed", raw_items=0,
                           failures=10))
        theirs = payload(row(when=THEIRS_TIME, status="success", raw_items=5))
        settled, _ = settlement.settle(base, ours, theirs)
        self.assertEqual(settled["feed-a"]["consecutive_failures"], 0)
        self.assertEqual(settled["feed-a"]["status"], "success")

    def test_a_new_failure_starts_the_streak(self):
        base = payload(row(failures=0))
        ours = payload(row(when=OURS_TIME, status="success", raw_items=5))
        theirs = payload(row(when=THEIRS_TIME, status="failed", raw_items=0,
                             failures=1))
        settled, _ = settlement.settle(base, ours, theirs)
        self.assertEqual(settled["feed-a"]["consecutive_failures"], 1)

    def test_empty_then_productive_clears_the_unproductive_streak(self):
        base = payload(row(unproductive=2, status="success_empty", raw_items=0))
        ours = payload(row(when=OURS_TIME, status="success_empty", raw_items=0,
                           unproductive=3))
        theirs = payload(row(when=THEIRS_TIME, status="success", raw_items=6))
        settled, _ = settlement.settle(base, ours, theirs)
        self.assertEqual(settled["feed-a"]["consecutive_unproductive"], 0)


class TheCountersAreCountedOnceTests(unittest.TestCase):
    """Requirement 8. Two observations, two scans, and no more than two."""

    def test_two_observations_count_as_two_scans(self):
        base = payload(row(total=100))
        ours = payload(row(when=OURS_TIME, total=101))
        theirs = payload(row(when=THEIRS_TIME, total=101))
        settled, _ = settlement.settle(base, ours, theirs)
        self.assertEqual(settled["feed-a"]["total_scans"], 102)

    def test_an_unobserved_source_adds_no_scan(self):
        base = payload(row(total=100))
        ours = payload(row(total=100))
        theirs = payload(row(when=THEIRS_TIME, total=101))
        settled, _ = settlement.settle(base, ours, theirs)
        self.assertEqual(settled["feed-a"]["total_scans"], 101)

    def test_the_average_keeps_both_readings(self):
        base = payload(row(total=1, average=100))
        ours = payload(row(when=OURS_TIME, total=2, average=150))     # 200ms
        theirs = payload(row(when=THEIRS_TIME, total=2, average=200))  # 300ms
        settled, _ = settlement.settle(base, ours, theirs)
        # (100 + 200 + 300) / 3
        self.assertEqual(settled["feed-a"]["total_scans"], 3)
        self.assertEqual(settled["feed-a"]["average_response_time_ms"], 200)

    def test_a_push_retry_does_not_count_the_same_scans_again(self):
        """The retry settles against a NEW base - the publish it just rebased
        onto - with our previous settlement as `ours`. Nothing new is observed,
        so nothing new may be counted, and the scan the settlement already
        folded in may not be dropped either."""
        base = payload(row(total=100, failures=2, status="failed", raw_items=0))
        ours = payload(row(when=OURS_TIME, total=101, failures=3,
                           status="failed", raw_items=0))
        theirs = payload(row(when=THEIRS_TIME, total=101, failures=3,
                             status="failed", raw_items=0))
        once, _ = settlement.settle(base, ours, theirs)

        retry_base = theirs
        twice, _ = settlement.settle(retry_base, payload(once["feed-a"]), theirs)
        self.assertEqual(twice["feed-a"]["total_scans"],
                         once["feed-a"]["total_scans"])
        self.assertEqual(twice["feed-a"]["consecutive_failures"],
                         once["feed-a"]["consecutive_failures"])
        thrice, _ = settlement.settle(retry_base, payload(twice["feed-a"]), theirs)
        self.assertEqual(thrice["feed-a"], twice["feed-a"])

    def test_a_retry_that_meets_a_newer_publish_still_settles(self):
        base = payload(row(total=100))
        ours = payload(row(when=OURS_TIME, total=101))
        theirs = payload(row(when=THEIRS_TIME, total=101))
        once, _ = settlement.settle(base, ours, theirs)
        newer = payload(row(when="2026-09-06T19:52:00.000000+00:00", total=102))
        retried, _ = settlement.settle(theirs, payload(once["feed-a"]), newer)
        self.assertEqual(retried["feed-a"]["last_scan"],
                         "2026-09-06T19:52:00.000000+00:00")
        self.assertEqual(retried["feed-a"]["total_scans"], 103)


class EverySourceSettlesOnItsOwnTests(unittest.TestCase):
    """Requirement 5 and 6. One stale row must not cost another its update."""

    def test_two_sources_choose_different_sides(self):
        base = payload(row("feed-a"), row("feed-b"))
        ours = payload(row("feed-a", when=THEIRS_TIME, raw_items=11),
                       row("feed-b", when=OURS_TIME, raw_items=2))
        theirs = payload(row("feed-a", when=OURS_TIME, raw_items=3),
                         row("feed-b", when=THEIRS_TIME, raw_items=8))
        settled, report = settlement.settle(base, ours, theirs)
        self.assertEqual(settled["feed-a"]["raw_items"], 11)
        self.assertEqual(settled["feed-b"]["raw_items"], 8)
        self.assertEqual(report["local_newer_applied"], 1)
        self.assertEqual(report["stale_local_updates_rejected"], 1)

    def test_each_run_observed_a_different_source(self):
        base = payload(row("feed-a"), row("feed-b"))
        ours = payload(row("feed-a", when=OURS_TIME, raw_items=4),
                       row("feed-b"))
        theirs = payload(row("feed-a"),
                         row("feed-b", when=THEIRS_TIME, raw_items=6))
        settled, _ = settlement.settle(base, ours, theirs)
        self.assertEqual(settled["feed-a"]["last_scan"], OURS_TIME)
        self.assertEqual(settled["feed-b"]["last_scan"], THEIRS_TIME)

    def test_a_targeted_run_that_observed_nothing_rewinds_nothing(self):
        base = payload(row("feed-a"), row("feed-b"))
        ours = copy.deepcopy(base)
        theirs = payload(row("feed-a", when=THEIRS_TIME, raw_items=9),
                         row("feed-b", when=THEIRS_TIME, status="failed",
                             raw_items=0, failures=4))
        settled, report = settlement.settle(base, ours, theirs)
        self.assertEqual(settled["feed-a"]["last_scan"], THEIRS_TIME)
        self.assertEqual(settled["feed-b"]["consecutive_failures"], 4)
        self.assertEqual(report["local_newer_applied"], 0)

    def test_a_movie_run_cannot_rewind_an_event_source(self):
        # The measured shape: a `today` publish rewound `sportlive-jiotv-
        # targeted` by 91 hours and two movie sources by 72, none of which it
        # had fetched. Nothing here knows what a mode is - the movie run simply
        # carries the event row unchanged, and an unchanged row cannot win.
        base = payload(row("event-feed", pipeline="today_match"),
                       row("movie-feed", pipeline="movies"))
        movies = payload(row("event-feed", pipeline="today_match"),
                         row("movie-feed", pipeline="movies",
                             when=OURS_TIME, raw_items=300))
        events = payload(row("event-feed", pipeline="today_match",
                             when=THEIRS_TIME, raw_items=42),
                         row("movie-feed", pipeline="movies"))
        settled, _ = settlement.settle(base, movies, events)
        self.assertEqual(settled["event-feed"]["last_scan"], THEIRS_TIME)
        self.assertEqual(settled["event-feed"]["raw_items"], 42)
        self.assertEqual(settled["movie-feed"]["last_scan"], OURS_TIME)

    def test_a_direct_channel_source_settles_like_any_other(self):
        base = payload(row("direct-feed", pipeline="direct_channel"))
        ours = payload(row("direct-feed", pipeline="direct_channel",
                           when=OURS_TIME, status="failed", raw_items=0,
                           failures=1))
        theirs = payload(row("direct-feed", pipeline="direct_channel",
                             when=THEIRS_TIME, raw_items=2))
        settled, _ = settlement.settle(base, ours, theirs)
        self.assertEqual(settled["direct-feed"]["status"], "success")
        self.assertEqual(settled["direct-feed"]["pipeline"], "direct_channel")

    def test_a_direct_channel_failure_leaves_the_fixture_sources_alone(self):
        base = payload(row("direct-feed", pipeline="direct_channel"),
                       row("fixture-feed", pipeline="today_match"))
        ours = payload(row("direct-feed", pipeline="direct_channel",
                           when=OURS_TIME, status="failed", raw_items=0,
                           failures=6),
                       row("fixture-feed", pipeline="today_match",
                           when=OURS_TIME, raw_items=40))
        theirs = copy.deepcopy(base)
        settled, _ = settlement.settle(base, ours, theirs)
        self.assertEqual(settled["fixture-feed"]["raw_items"], 40)
        self.assertEqual(settled["direct-feed"]["consecutive_failures"], 6)


class NoRowIsEverLostTests(unittest.TestCase):
    """Requirement 5 and 11. A registry a run did not read still exists."""

    def test_a_source_only_main_knows_is_kept(self):
        base = payload(row("feed-a"))
        ours = payload(row("feed-a", when=OURS_TIME))
        theirs = payload(row("feed-a"), row("feed-new", when=THEIRS_TIME))
        settled, report = settlement.settle(base, ours, theirs)
        self.assertIn("feed-new", settled)
        self.assertEqual(report["rows_lost"], 0)

    def test_a_source_only_this_run_knows_is_kept(self):
        base = payload(row("feed-a"))
        ours = payload(row("feed-a"), row("feed-new", when=OURS_TIME))
        theirs = payload(row("feed-a", when=THEIRS_TIME))
        settled, _ = settlement.settle(base, ours, theirs)
        self.assertIn("feed-new", settled)

    def test_a_source_missing_from_one_registry_is_not_deleted(self):
        base = payload(row("feed-a"), row("retired-feed"))
        ours = payload(row("feed-a", when=OURS_TIME))
        theirs = payload(row("feed-a"), row("retired-feed"))
        settled, report = settlement.settle(base, ours, theirs)
        self.assertIn("retired-feed", settled)
        self.assertEqual(report["rows_lost"], 0)

    def test_no_source_is_written_twice(self):
        base = payload(row("feed-a"), row("feed-b"))
        ours = payload(row("feed-a", when=OURS_TIME), row("feed-b"))
        theirs = payload(row("feed-a"), row("feed-b", when=THEIRS_TIME))
        settled, report = settlement.settle(base, ours, theirs)
        self.assertEqual(len(settled), 2)
        self.assertEqual(report["sources"], 2)
        self.assertEqual(sorted(settled), ["feed-a", "feed-b"])

    def test_the_row_count_survives_every_ordering(self):
        base = payload(row("feed-a"), row("feed-b"), row("feed-c"))
        ours = payload(row("feed-a", when=OURS_TIME), row("feed-b"),
                       row("feed-c", when=THEIRS_TIME))
        theirs = payload(row("feed-a", when=THEIRS_TIME),
                         row("feed-b", when=THEIRS_TIME), row("feed-c"))
        settled, report = settlement.settle(base, ours, theirs)
        self.assertEqual(report["rows_in_base"], 3)
        self.assertEqual(len(settled), 3)


class AnUnreadableFileIsNotEvidenceTests(unittest.TestCase):
    """Requirement 11. Broken JSON must fail towards what is published."""

    def test_a_malformed_local_file_cannot_erase_good_remote_state(self):
        theirs = payload(row("feed-a", when=THEIRS_TIME, raw_items=9))
        result, report = settlement.settle_payload(
            payload(row("feed-a")), "not json at all", theirs)
        self.assertIsNone(result)
        self.assertIn("skipped", report)

    def test_an_empty_local_file_cannot_erase_good_remote_state(self):
        theirs = payload(row("feed-a", when=THEIRS_TIME, raw_items=9))
        result, _ = settlement.settle_payload(
            payload(row("feed-a")), {"sources": {}}, theirs)
        self.assertIsNone(result)

    def test_a_malformed_remote_file_leaves_our_observations_standing(self):
        base = payload(row("feed-a"))
        ours = payload(row("feed-a", when=OURS_TIME, raw_items=7))
        result, report = settlement.settle_payload(base, ours, None)
        self.assertIsNotNone(result)
        self.assertEqual(result["sources"]["feed-a"]["raw_items"], 7)
        self.assertEqual(report["rows_lost"], 0)

    def test_a_malformed_base_settles_on_the_two_readings(self):
        ours = payload(row("feed-a", when=OURS_TIME, raw_items=7))
        theirs = payload(row("feed-a", when=THEIRS_TIME, raw_items=9))
        settled, _ = settlement.settle("", ours, theirs)
        self.assertEqual(settled["feed-a"]["raw_items"], 9)

    def test_a_row_that_is_not_a_dict_is_ignored_rather_than_fatal(self):
        broken = {"updated_at": THEIRS_TIME, "sources": {"feed-a": "nonsense"}}
        ours = payload(row("feed-a", when=OURS_TIME, raw_items=7))
        settled, _ = settlement.settle(payload(row("feed-a")), ours, broken)
        self.assertEqual(settled["feed-a"]["raw_items"], 7)


class TheOutageEvidenceSurvivesTests(unittest.TestCase):
    """Requirement 9. The two inputs the outage gate actually reads."""

    NOW = source_outage.parse_time("2026-09-06T19:50:00+00:00")

    def test_a_record_rewound_past_the_window_stops_protecting(self):
        # The fault, stated as the consumer sees it. 318 real rewinds pushed a
        # last_scan past this window.
        stale = {"feed-a": row("feed-a", when="2026-09-06T18:30:00+00:00",
                               status="success_empty", raw_items=0,
                               last_productive="2026-09-06T18:00:00+00:00",
                               productive_items=40)}
        states = source_outage.read_source_states(stale, now=self.NOW)
        self.assertEqual(states["feed-a"]["state"], source_outage.UNKNOWN)

    def test_the_settlement_keeps_the_record_inside_the_window(self):
        base = payload(row("feed-a", status="success", raw_items=40))
        ours = payload(row("feed-a", when="2026-09-06T18:30:00+00:00",
                           status="success_empty", raw_items=0,
                           unproductive=1,
                           last_productive="2026-09-06T18:00:00+00:00",
                           productive_items=40))
        theirs = payload(row("feed-a", when="2026-09-06T19:48:10+00:00",
                             status="success_empty", raw_items=0,
                             unproductive=2,
                             last_productive="2026-09-06T19:40:00+00:00",
                             productive_items=40))
        settled, _ = settlement.settle(base, ours, theirs)
        states = source_outage.read_source_states(settled, now=self.NOW)
        self.assertEqual(states["feed-a"]["state"], source_outage.OUTAGE)
        self.assertEqual(states["feed-a"]["content_state"], source_outage.EMPTY)

    def test_recovery_becomes_visible_immediately(self):
        base = payload(row("feed-a", status="success_empty", raw_items=0,
                           unproductive=3))
        ours = payload(row("feed-a", when="2026-09-06T19:46:44+00:00",
                           status="success_empty", raw_items=0,
                           unproductive=4))
        theirs = payload(row("feed-a", when="2026-09-06T19:48:10+00:00",
                             status="success", raw_items=44))
        settled, _ = settlement.settle(base, ours, theirs)
        states = source_outage.read_source_states(settled, now=self.NOW)
        self.assertEqual(states["feed-a"]["state"], source_outage.PRODUCTIVE)

    def test_a_source_that_never_produced_still_protects_nothing(self):
        base = payload(row("feed-a", status="success_empty", raw_items=0))
        base["sources"]["feed-a"].pop("last_productive", None)
        base["sources"]["feed-a"].pop("last_productive_items", None)
        ours = copy.deepcopy(base)
        ours["sources"]["feed-a"]["last_scan"] = "2026-09-06T19:46:44+00:00"
        theirs = copy.deepcopy(base)
        theirs["sources"]["feed-a"]["last_scan"] = "2026-09-06T19:48:10+00:00"
        settled, _ = settlement.settle(base, ours, theirs)
        states = source_outage.read_source_states(settled, now=self.NOW)
        self.assertEqual(states["feed-a"]["state"], source_outage.UNKNOWN)

    def test_a_healthy_withdrawal_is_still_a_withdrawal(self):
        # The source answered and produced. Nothing here holds anything: a feed
        # that is talking has withdrawn what it no longer lists.
        base = payload(row("feed-a", raw_items=40))
        ours = payload(row("feed-a", when="2026-09-06T19:46:44+00:00",
                           raw_items=40))
        theirs = payload(row("feed-a", when="2026-09-06T19:48:10+00:00",
                             raw_items=24))
        settled, _ = settlement.settle(base, ours, theirs)
        states = source_outage.read_source_states(settled, now=self.NOW)
        self.assertEqual(states["feed-a"]["state"], source_outage.PRODUCTIVE)
        self.assertEqual(settled["feed-a"]["raw_items"], 24)

    def test_an_unreachable_source_is_not_given_a_success(self):
        base = payload(row("feed-a", raw_items=40))
        ours = payload(row("feed-a", when="2026-09-06T19:46:44+00:00",
                           raw_items=40))
        theirs = payload(row("feed-a", when="2026-09-06T19:48:10+00:00",
                             status="failed", raw_items=0, failures=1))
        settled, _ = settlement.settle(base, ours, theirs)
        states = source_outage.read_source_states(settled, now=self.NOW)
        self.assertEqual(states["feed-a"]["content_state"],
                         source_outage.UNREACHABLE)
        self.assertEqual(settled["feed-a"]["status"], "failed")


class TheSettlementIsWiredIntoThePushTests(unittest.TestCase):
    """Requirement 10 and 13. Structure, not discipline."""

    def setUp(self):
        self.merge = _merge_module()

    def test_it_runs_from_main_whatever_the_merge_decided(self):
        import inspect
        body = inspect.getsource(self.merge.main)
        self.assertIn("settle_source_health_in_tree", body)
        merged = inspect.getsource(self.merge.merge_lists)
        self.assertNotIn("settle_source_health_in_tree", merged,
                         "the merge must not be the thing that remembers")

    def test_the_push_step_commits_what_the_settlement_wrote(self):
        workflow = (ROOT / ".github" / "workflows" / "scan.yml").read_text(
            encoding="utf-8")
        self.assertIn("state/source-health.json reports/source-health-settlement.json",
                      workflow,
                      "`git add -A data` would leave the settlement on disk")

    def test_the_push_step_does_not_die_on_a_file_that_is_not_there(self):
        """The step runs under `set -euo pipefail`. `git add` on a missing path
        exits 1, and the receipt is missing on every run the settlement skips -
        which would have taken the whole push down rather than one file."""
        workflow = (ROOT / ".github" / "workflows" / "scan.yml").read_text(
            encoding="utf-8")
        guarded = workflow.split("for SETTLED in", 1)
        self.assertEqual(len(guarded), 2, "the add must be guarded")
        self.assertIn('if [[ -f "$SETTLED" ]]; then git add -- "$SETTLED"; fi',
                      guarded[1])

    def test_it_writes_the_settled_file_and_a_bounded_receipt(self):
        import tempfile
        trees = {
            "base": payload(row("feed-a"), row("feed-b")),
            "ours": payload(row("feed-a", when=OURS_TIME, raw_items=4),
                            row("feed-b")),
            "theirs": payload(row("feed-a"),
                              row("feed-b", when=THEIRS_TIME, raw_items=6)),
        }
        with tempfile.TemporaryDirectory() as tmp:
            written = self.merge.settle_source_health_in_tree(
                "base", "ours", "theirs", root=Path(tmp),
                read=lambda ref, path: trees[ref])
            self.assertTrue(written)
            settled = json.loads(
                (Path(tmp) / "state" / "source-health.json").read_text(
                    encoding="utf-8"))
            self.assertEqual(settled["sources"]["feed-a"]["last_scan"], OURS_TIME)
            self.assertEqual(settled["sources"]["feed-b"]["last_scan"],
                             THEIRS_TIME)
            receipt = json.loads(
                (Path(tmp) / "reports" /
                 "source-health-settlement.json").read_text(encoding="utf-8"))
            for key in ("sources", "local_newer_applied",
                        "remote_newer_preserved",
                        "stale_local_updates_rejected", "tied_observations",
                        "rows_lost"):
                self.assertIn(key, receipt)
            self.assertLessEqual(len(receipt["source_ids"]), 40)

    def test_the_receipt_never_grows_with_the_state(self):
        base = payload(*[row("feed-%03d" % index) for index in range(90)])
        ours = payload(*[row("feed-%03d" % index, when=OURS_TIME)
                         for index in range(90)])
        theirs = payload(*[row("feed-%03d" % index, when=THEIRS_TIME)
                           for index in range(90)])
        _, report = settlement.settle(base, ours, theirs)
        self.assertEqual(report["sources"], 90)
        self.assertLessEqual(len(report["source_ids"]), 40)
        self.assertLess(len(json.dumps(report)), 2000)

    def test_an_unchanged_run_does_not_rewrite_the_file(self):
        base = payload(row("feed-a"))
        result, _ = settlement.settle_payload(base, base, base)
        self.assertIsNotNone(result)
        self.assertEqual(result["sources"]["feed-a"]["last_scan"], BASE_TIME)


class TheRealRollbackIsSettledTests(unittest.TestCase):
    """Requirement 12, with the numbers off the published file.

    19:46:44 was a `today` run's reading; 19:48:10 was an `upcoming-targeted`
    publish that landed first. `26a607daf0` then put the 19:46 copy back for
    every source in the file - `manual-playlist-1` among them, whose
    total_scans went 791 -> 790 and whose consecutive_unproductive went
    32 -> 31 on a source it had not fetched.
    """

    def test_the_1946_reading_does_not_replace_the_1948_publish(self):
        base = payload(row("manual-playlist-1", when=BASE_TIME, total=790,
                           unproductive=31, pipeline="manual"))
        ours = payload(row("manual-playlist-1", when=OURS_TIME, total=791,
                           unproductive=0, pipeline="manual"))
        theirs = payload(row("manual-playlist-1", when=THEIRS_TIME, total=791,
                             unproductive=0, pipeline="manual"))
        settled, report = settlement.settle(base, ours, theirs)
        record = settled["manual-playlist-1"]
        self.assertEqual(record["last_scan"], THEIRS_TIME)
        self.assertEqual(record["last_success"], THEIRS_TIME)
        self.assertEqual(record["last_productive"], THEIRS_TIME)
        self.assertEqual(record["total_scans"], 792)
        self.assertEqual(report["stale_local_updates_rejected"], 1)
        self.assertEqual(report["rows_lost"], 0)

    def test_the_published_state_settles_against_itself_unchanged(self):
        # The real file, all 87 rows: settling a publish against itself must
        # neither move a timestamp nor invent a scan.
        import subprocess
        blob = subprocess.run(
            ("git", "show", "origin/main:state/source-health.json"),
            capture_output=True, cwd=str(ROOT))
        if blob.returncode:
            self.skipTest("no origin/main in this checkout")
        live = json.loads(blob.stdout.decode("utf-8", "replace"))
        settled, report = settlement.settle(live, live, live)
        self.assertEqual(report["rows_lost"], 0)
        self.assertEqual(len(settled), len(settlement.sources_of(live)))
        for key, record in settlement.sources_of(live).items():
            self.assertEqual(settled[key]["last_scan"], record["last_scan"])
            self.assertEqual(settled[key]["total_scans"],
                             record.get("total_scans"))


class TheReadingIsNotAMixtureTests(unittest.TestCase):
    """Requirement 3A and 3E. One observation's fields, not two half-rows."""

    def test_the_newer_reading_owns_its_absences_too(self):
        # A conditional fetch writes `cache_hit`; a full one does not. Filling
        # the gap from the older reading describes a scan that never happened.
        base = payload(row("feed-a"))
        ours = payload(row("feed-a", when=OURS_TIME, fetch_mode="conditional",
                           cache_hit=True, cache_saved=True))
        theirs = payload(row("feed-a", when=THEIRS_TIME, fetch_mode="full"))
        settled, _ = settlement.settle(base, ours, theirs)
        self.assertEqual(settled["feed-a"]["fetch_mode"], "full")
        self.assertNotIn("cache_hit", settled["feed-a"])

    def test_every_observation_field_comes_from_one_side(self):
        base = payload(row("feed-a"))
        ours = payload(row("feed-a", when=OURS_TIME, status="failed",
                           raw_items=0, failures=1, error="boom",
                           detected_format="", attempts=3))
        theirs = payload(row("feed-a", when=THEIRS_TIME, status="success",
                             raw_items=12, attempts=1))
        settled, _ = settlement.settle(base, ours, theirs)
        record = settled["feed-a"]
        for field in ("status", "http_status", "attempts", "raw_items",
                      "error", "detected_format", "response_time_ms"):
            self.assertEqual(record.get(field),
                             theirs["sources"]["feed-a"].get(field), field)

    def test_metadata_is_filled_in_rather_than_lost(self):
        base = payload(row("feed-a"))
        ours = payload(row("feed-a", when=OURS_TIME))
        theirs = payload(row("feed-a", when=THEIRS_TIME))
        theirs["sources"]["feed-a"]["url"] = ""
        theirs["sources"]["feed-a"]["pipeline"] = ""
        settled, _ = settlement.settle(base, ours, theirs)
        self.assertTrue(settled["feed-a"]["url"])
        self.assertEqual(settled["feed-a"]["pipeline"], "today_match")

    def test_the_settled_row_keeps_the_whole_schema(self):
        base = payload(row("feed-a"))
        ours = payload(row("feed-a", when=OURS_TIME))
        theirs = payload(row("feed-a", when=THEIRS_TIME))
        settled, _ = settlement.settle(base, ours, theirs)
        for field in ("source_id", "source_name", "url", "pipeline", "status",
                      "last_scan", "http_status", "attempts",
                      "response_time_ms", "detected_format", "raw_items",
                      "error", "total_scans", "average_response_time_ms",
                      "last_success", "consecutive_failures",
                      "consecutive_unproductive", "last_productive",
                      "last_productive_items"):
            self.assertIn(field, settled["feed-a"], field)


if __name__ == "__main__":
    unittest.main()
