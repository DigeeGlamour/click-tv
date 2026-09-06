"""PROMPT 31 - the coverage report is checked against itself.

FINAL_3, অংশ ৬ lists the checks a coverage report has to survive. Every one of
them exists because a number in that file had already been believed once: the
report said twelve sources when twenty-one were configured, said a source
published nothing when it had put its name on 48 cards, and said
`streams_attached: 0` on a scan holding 33 playable routes.

A failing invariant is stated. It is written into the report, printed in the
scan log, and raises the scan summary to `completed_with_warnings` - because a
check that fails quietly is the same as no check.

Nothing here accepts, refuses, routes or filters anything. It reads.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scanner.source_coverage import (  # noqa: E402
    build_source_coverage,
    check_invariants,
    load_configured_sources,
)

ROOT = Path(__file__).resolve().parents[1]


def _card(source_id, name, **extra):
    card = {"id": name.lower().replace(" ", "-"), "name": name,
            "fixture_id": "%s|2026-09-05" % name.lower().replace(" ", "-"),
            "source_id": source_id}
    card.update(extra)
    return card


def _healthy():
    """A report and its tabs, all invariants satisfied."""
    configured = [{"id": "src-a"}, {"id": "src-b"}]
    today = [_card("src-a", "Genoa vs Como")]
    upcoming = [_card("src-b", "Ipswich vs Liverpool")]
    report = build_source_coverage(
        configured_sources=configured,
        raw_candidates=[{"source_id": "src-a"}, {"source_id": "src-b"}],
        parsed_candidates=[{"source_id": "src-a"}, {"source_id": "src-b"}],
        matched_candidates=[{"source_id": "src-a"}, {"source_id": "src-b"}],
        deduped_candidates=today + upcoming,
        published_today_items=today,
        published_upcoming_items=upcoming,
        source_health={"src-a": {"status": "success", "raw_items": 5},
                       "src-b": {"status": "success", "raw_items": 5}},
    )
    stream_health = {
        "published_fixtures": 2, "fixtures_with_stream": 2,
        "fixtures_with_fresh_stream": 2, "fixtures_with_carried_stream": 0,
        "today_with_stream": 1, "upcoming_with_stream": 1,
        "streams_attached": 0, "state": "ok", "warnings": [],
    }
    return configured, report, today, upcoming, stream_health


def _check(configured, report, today, upcoming, stream_health):
    return check_invariants(
        report, configured_sources=configured, today_items=today,
        upcoming_items=upcoming, stream_health=stream_health)


class AGoodReportPassesEveryCheck(unittest.TestCase):
    def test_nothing_fails_on_a_consistent_report(self):
        result = _check(*_healthy())
        self.assertEqual([], result["failures"])
        self.assertEqual(0, result["failed"])
        self.assertGreaterEqual(result["passed"], 10)

    def test_the_real_configuration_produces_a_full_row_set(self):
        configured = load_configured_sources(ROOT / "config")
        report = build_source_coverage(
            configured_sources=configured,
            raw_candidates=[], parsed_candidates=[], matched_candidates=[],
            published_today_items=[], published_upcoming_items=[],
        )
        result = check_invariants(report, configured_sources=configured)
        self.assertEqual(len(configured), report["configured_source_count"])
        self.assertNotIn("configured_source_count == coverage_row_count",
                         result["failures"])

    def test_every_check_carries_a_detail_line(self):
        for entry in _check(*_healthy())["checks"]:
            self.assertIn("name", entry)
            self.assertIn("passed", entry)
            self.assertIn("detail", entry)


class EachInvariantActuallyCatchesItsFault(unittest.TestCase):
    """A check that cannot fail proves nothing. Each one is broken on purpose."""

    def test_a_missing_row_is_caught(self):
        configured, report, today, upcoming, health = _healthy()
        report["sources"] = report["sources"][:1]
        result = _check(configured, report, today, upcoming, health)
        self.assertIn("configured_source_count == coverage_row_count",
                      result["failures"])
        self.assertIn("every enabled configured source has exactly one row",
                      result["failures"])

    def test_a_duplicated_row_is_caught(self):
        configured, report, today, upcoming, health = _healthy()
        report["sources"].append(dict(report["sources"][0]))
        result = _check(configured, report, today, upcoming, health)
        self.assertIn("every enabled configured source has exactly one row",
                      result["failures"])

    def test_a_published_source_with_no_row_anywhere_is_caught(self):
        """The srhady-axsports-live case: 48 published cards, no row."""
        configured, report, today, upcoming, health = _healthy()
        today.append(_card("ghost-source", "Fiorentina vs Torino"))
        health["published_fixtures"] = 3
        result = _check(configured, report, today, upcoming, health)
        self.assertIn("every published source_id appears in coverage",
                      result["failures"])
        self.assertIn("every published card is credited to a source in the report",
                      result["failures"])

    def test_a_silent_zero_row_is_caught(self):
        configured, report, today, upcoming, health = _healthy()
        report["sources"][0]["raw_items"] = 0
        report["sources"][0]["drop_reasons"] = []
        result = _check(configured, report, today, upcoming, health)
        self.assertIn("a fetched configured source has a row even at raw_items 0",
                      result["failures"])

    def test_an_unexplained_drop_is_caught(self):
        configured, report, today, upcoming, health = _healthy()
        report["sources"][0]["sport_allowed_events"] = 9
        report["sources"][0]["drop_reasons"] = []
        result = _check(configured, report, today, upcoming, health)
        self.assertIn("a candidate that is not published has an explicit drop reason",
                      result["failures"])

    def test_the_same_fixture_on_both_tabs_is_caught(self):
        configured, report, today, upcoming, health = _healthy()
        upcoming.append(dict(today[0]))
        result = _check(configured, report, today, upcoming, health)
        self.assertIn("no fixture is on Today and Upcoming at once",
                      result["failures"])
        self.assertIn("no fixture routed to Today survives on Upcoming",
                      result["failures"])

    def test_a_non_configured_source_among_the_rows_is_caught(self):
        """`streamed-fixtures` in the configured list is how twelve rows once
        looked like a plausible twenty-one."""
        configured, report, today, upcoming, health = _healthy()
        report["sources"].append(dict(report["sources"][0],
                                      source_id="streamed-fixtures"))
        report["configured_source_count"] = len(report["sources"])
        result = _check(configured, report, today, upcoming, health)
        self.assertIn("no non-configured source sits among the configured rows",
                      result["failures"])

    def test_tab_counts_that_do_not_add_up_are_caught(self):
        configured, report, today, upcoming, health = _healthy()
        report["sources"][0]["published_today"] = 7
        result = _check(configured, report, today, upcoming, health)
        self.assertIn(
            "published_today + published_upcoming == published_unique_fixtures",
            result["failures"])

    def test_inconsistent_stream_counters_are_caught(self):
        configured, report, today, upcoming, health = _healthy()
        health["fixtures_with_fresh_stream"] = 5
        result = _check(configured, report, today, upcoming, health)
        self.assertIn("stream counters are internally consistent",
                      result["failures"])

    def test_a_degraded_state_with_no_warning_is_caught(self):
        configured, report, today, upcoming, health = _healthy()
        health["state"] = "degraded"
        result = _check(configured, report, today, upcoming, health)
        self.assertIn("a degraded stream state carries a warning, and vice versa",
                      result["failures"])

    def test_no_playable_route_without_a_warning_is_caught(self):
        """FINAL_3 অংশ ৪ক, as an invariant: 124 cards, no stream, silence."""
        configured, report, today, upcoming, health = _healthy()
        health.update({"published_fixtures": 124, "fixtures_with_stream": 0,
                       "fixtures_with_fresh_stream": 0,
                       "fixtures_with_carried_stream": 0,
                       "today_with_stream": 0, "upcoming_with_stream": 0,
                       "state": "ok", "warnings": []})
        result = _check(configured, report, today, upcoming, health)
        self.assertIn("no playable route anywhere is never a silent success",
                      result["failures"])


class AFailureIsNeverSwallowed(unittest.TestCase):
    def test_the_publish_path_writes_and_prints_the_failures(self):
        source = (ROOT / "scanner" / "events.py").read_text(
            encoding="utf-8").replace("\r\n", "\n")
        self.assertIn('coverage["invariants"] = check_invariants(', source)
        self.assertIn('COVERAGE INVARIANT FAILED', source)

    def test_the_scan_summary_raises_its_status_on_a_failure(self):
        source = (ROOT / "scanner" / "output.py").read_text(
            encoding="utf-8").replace("\r\n", "\n")
        self.assertIn('"today-source-coverage.json").get("invariants")', source)
        self.assertIn("or coverage_failures", source)
        self.assertIn('"coverage_invariant_failures": coverage_failures,', source)

    def test_the_checker_never_touches_what_it_reads(self):
        configured, report, today, upcoming, health = _healthy()
        before = (repr(report), repr(today), repr(upcoming), repr(health))
        _check(configured, report, today, upcoming, health)
        self.assertEqual(
            before, (repr(report), repr(today), repr(upcoming), repr(health)))

    def test_missing_optional_evidence_does_not_invent_a_failure(self):
        """Called with the report alone - no tabs, no stream health - the
        checks that need them are simply not run."""
        _, report, _, _, _ = _healthy()
        result = check_invariants(report)
        self.assertEqual([], result["failures"])


if __name__ == "__main__":
    unittest.main()
