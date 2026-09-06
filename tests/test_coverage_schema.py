"""PROMPT 27 - the coverage row carries the whole funnel, and it adds up.

FINAL_3, অংশ ৬ lists what a row must say. The stages are not decorative: each
one is a different explanation of the same zero. A source can fetch nothing,
fetch and parse into no event, parse events that are not cricket or football,
carry a fixture that folded into another source's card, or lead a card whose
every route failed verification - and until the row named the stage, all five
looked identical from the outside.

Two of the numbers are not counted here at all. `raw_items`, `fetch_status` and
`http_status` come from state/source-health.json, which is the only thing that
saw the response: `srhady-axsports-live` fetched 743 records in the scan this
was written against and lost every one of them to an exact-duplicate check, so
counting its surviving candidates would report 0 fetched for a source that
fetched more than any other but one.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scanner.source_coverage import (  # noqa: E402
    build_source_coverage,
    load_configured_sources,
    load_source_health,
)

ROOT = Path(__file__).resolve().parents[1]

#: FINAL_3, অংশ ৬. The freshness three - `newest_fixture_start`,
#: `future_fixture_count`, `content_state` - are PROMPT 28's, and tested there.
ROW_FIELDS = {
    "source_id",
    "configured",
    "fetch_status",
    "http_status",
    "raw_items",
    "parsed_events",
    "sport_allowed_events",
    "deduped_events",
    "published_unique_fixtures",
    "published_today",
    "published_upcoming",
    "dropped_count",
    "drop_reasons",
    "newest_fixture_start",
    "future_fixture_count",
    "content_state",
    "verified_streams",
    "failed_streams",
}


def _card(source_id, name, **extra):
    card = {"id": name.lower().replace(" ", "-"), "name": name, "source_id": source_id}
    card.update(extra)
    return card


class TheRowSaysEveryStage(unittest.TestCase):
    def _report(self, **kwargs):
        base = dict(
            configured_sources=[{"id": "src-a"}],
            raw_candidates=[],
            parsed_candidates=[],
            matched_candidates=[],
            published_today_items=[],
            published_upcoming_items=[],
        )
        base.update(kwargs)
        return build_source_coverage(**base)

    def test_the_row_carries_exactly_the_named_fields(self):
        report = self._report()
        self.assertEqual(ROW_FIELDS, set(report["sources"][0]))

    def test_every_stage_of_the_funnel_is_its_own_number(self):
        report = self._report(
            raw_candidates=[{"source_id": "src-a"}] * 6,
            parsed_candidates=[{"source_id": "src-a"}] * 6,
            matched_candidates=[{"source_id": "src-a"}] * 4,
            deduped_candidates=[_card("src-a", "A vs B"), _card("src-a", "C vs D")],
            published_today_items=[_card("src-a", "A vs B")],
            published_upcoming_items=[],
            source_health={"src-a": {"status": "success", "http_status": 200,
                                     "raw_items": 40}},
        )
        row = report["sources"][0]
        self.assertEqual(40, row["raw_items"])
        self.assertEqual(6, row["parsed_events"])
        self.assertEqual(4, row["sport_allowed_events"])
        self.assertEqual(2, row["deduped_events"])
        self.assertEqual(1, row["published_unique_fixtures"])
        self.assertEqual(1, row["dropped_count"])
        self.assertTrue(row["drop_reasons"])

    def test_the_two_tabs_are_counted_separately(self):
        report = self._report(
            matched_candidates=[{"source_id": "src-a"}] * 3,
            deduped_candidates=[_card("src-a", "A vs B"), _card("src-a", "C vs D"),
                                _card("src-a", "E vs F")],
            published_today_items=[_card("src-a", "A vs B")],
            published_upcoming_items=[_card("src-a", "C vs D"),
                                      _card("src-a", "E vs F")],
        )
        row = report["sources"][0]
        self.assertEqual(1, row["published_today"])
        self.assertEqual(2, row["published_upcoming"])
        self.assertEqual(3, row["published_unique_fixtures"])
        self.assertEqual(0, row["dropped_count"])

    def test_a_backup_contributor_is_credited_on_both_counts(self):
        report = build_source_coverage(
            configured_sources=[{"id": "lead"}, {"id": "helper"}],
            raw_candidates=[],
            parsed_candidates=[],
            matched_candidates=[],
            deduped_candidates=[_card("lead", "A vs B",
                                      backups=[{"source_id": "helper"}])],
            published_today_items=[_card("lead", "A vs B",
                                         backups=[{"source_id": "helper"}])],
            published_upcoming_items=[],
        )
        rows = {row["source_id"]: row for row in report["sources"]}
        self.assertEqual(1, rows["helper"]["published_today"])
        self.assertEqual(0, rows["helper"]["dropped_count"])


class TheFetchFactsComeFromTheLoader(unittest.TestCase):
    def _row(self, health, **kwargs):
        base = dict(
            configured_sources=[{"id": "src-a"}],
            raw_candidates=[], parsed_candidates=[], matched_candidates=[],
            published_today_items=[], published_upcoming_items=[],
            source_health={"src-a": health},
        )
        base.update(kwargs)
        return build_source_coverage(**base)["sources"][0]

    def test_what_the_source_returned_is_not_recounted_from_survivors(self):
        """The axsports case: 743 fetched, 0 surviving, and both are true."""
        row = self._row({"status": "success", "http_status": 200, "raw_items": 743},
                        published_today_items=[_card("lead", "A vs B",
                                                     source_ids=["src-a"])])
        self.assertEqual(743, row["raw_items"])
        self.assertEqual(0, row["parsed_events"])
        self.assertEqual(1, row["published_unique_fixtures"])
        self.assertIn("exact duplicate", " ".join(row["drop_reasons"]))

    def test_the_loaders_failed_is_reported_as_error(self):
        row = self._row({"status": "failed", "http_status": 404, "raw_items": 0,
                         "error": "HTTP Error 404: Not Found"})
        self.assertEqual("error", row["fetch_status"])
        self.assertEqual(404, row["http_status"])
        self.assertIn("404", row["drop_reasons"][0])

    def test_success_with_nothing_in_it_keeps_its_own_name(self):
        row = self._row({"status": "success_empty", "http_status": 200,
                         "raw_items": 0})
        self.assertEqual("success_empty", row["fetch_status"])
        self.assertEqual("nothing fetched from this source", row["drop_reasons"][0])

    def test_verified_and_failed_routes_are_counted_from_the_candidates(self):
        report = build_source_coverage(
            configured_sources=[{"id": "src-a"}],
            raw_candidates=[
                {"source_id": "src-a", "verification_status": "verified_global"},
                {"source_id": "src-a", "verification_status": "verified_proxy"},
                {"source_id": "src-a", "verification_status": "failed"},
                {"source_id": "src-a", "verification_status": "unreachable_from_browser"},
                {"source_id": "src-a", "verification_status": "metadata_only"},
            ],
            parsed_candidates=[], matched_candidates=[],
            published_today_items=[], published_upcoming_items=[],
        )
        row = report["sources"][0]
        self.assertEqual(2, row["verified_streams"])
        self.assertEqual(2, row["failed_streams"])

    def test_a_source_whose_every_route_failed_is_not_called_merged(self):
        """srhady-sonyliv-live: two candidates, two failed routes, nothing
        published. Calling that 'folded into a card another source leads'
        points the reader at the wrong stage entirely."""
        report = build_source_coverage(
            configured_sources=[{"id": "src-a"}],
            raw_candidates=[
                {"source_id": "src-a", "verification_status": "failed"},
                {"source_id": "src-a", "verification_status": "failed"},
            ],
            parsed_candidates=[{"source_id": "src-a"}] * 2,
            matched_candidates=[{"source_id": "src-a"}] * 2,
            deduped_candidates=[],
            published_today_items=[], published_upcoming_items=[],
        )
        row = report["sources"][0]
        self.assertEqual(2, row["failed_streams"])
        self.assertIn("no link passed", " ".join(row["drop_reasons"]))
        self.assertNotIn("folded into", " ".join(row["drop_reasons"]))

    def test_a_source_that_did_fold_still_says_so(self):
        report = build_source_coverage(
            configured_sources=[{"id": "src-a"}],
            raw_candidates=[{"source_id": "src-a"}],
            parsed_candidates=[{"source_id": "src-a"}],
            matched_candidates=[{"source_id": "src-a"}],
            deduped_candidates=[],
            published_today_items=[_card("lead", "A vs B", source_ids=["src-a"])],
            published_upcoming_items=[],
        )
        row = report["sources"][0]
        self.assertIn("folded into", " ".join(row["drop_reasons"]))

    def test_the_health_file_is_read_where_the_loader_writes_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "source-health.json"
            path.write_text(json.dumps({"sources": {
                "src-a": {"status": "success", "raw_items": 12},
                "broken": "not a dict",
            }}), encoding="utf-8")
            health = load_source_health(path)
            self.assertEqual(12, health["src-a"]["raw_items"])
            self.assertNotIn("broken", health)

    def test_a_missing_health_file_never_raises(self):
        self.assertEqual({}, load_source_health(Path(tempfile.mkdtemp()) / "gone.json"))


class TheInvariantsHold(unittest.TestCase):
    """FINAL_3, অংশ ৬ - the checks a coverage report has to survive."""

    def _real_shaped_report(self):
        configured = load_configured_sources(ROOT / "config")
        today = [_card(configured[6]["id"], "Genoa vs Como")]
        upcoming = [_card(configured[7]["id"], "Ipswich vs Liverpool"),
                    _card(configured[6]["id"], "Fiorentina vs Torino")]
        return configured, build_source_coverage(
            configured_sources=configured,
            raw_candidates=[{"source_id": configured[6]["id"]}] * 9,
            parsed_candidates=[{"source_id": configured[6]["id"]}] * 9,
            matched_candidates=[{"source_id": configured[6]["id"]}] * 5,
            deduped_candidates=today + upcoming,
            published_today_items=today,
            published_upcoming_items=upcoming,
            source_health={"streamed-fixtures": {"status": "success",
                                                 "raw_items": 551}},
        )

    def test_configured_count_equals_row_count(self):
        configured, report = self._real_shaped_report()
        self.assertEqual(len(configured), report["configured_source_count"])
        self.assertEqual(report["configured_source_count"], len(report["sources"]))

    def test_every_configured_source_has_exactly_one_row(self):
        configured, report = self._real_shaped_report()
        ids = [row["source_id"] for row in report["sources"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual({entry["id"] for entry in configured}, set(ids))

    def test_every_published_source_appears_somewhere_in_the_report(self):
        _, report = self._real_shaped_report()
        published = [row for row in report["sources"]
                     if row["published_unique_fixtures"]]
        self.assertTrue(published)
        for row in published:
            self.assertTrue(row["configured"])

    def test_no_unconfigured_source_sits_among_the_configured_rows(self):
        configured, report = self._real_shaped_report()
        known = {entry["id"] for entry in configured}
        for row in report["sources"]:
            self.assertIn(row["source_id"], known)

    def test_the_tab_counts_reconcile_with_the_published_count(self):
        _, report = self._real_shaped_report()
        for row in report["sources"]:
            self.assertEqual(
                row["published_today"] + row["published_upcoming"],
                row["published_unique_fixtures"],
                row["source_id"],
            )
        totals = report["totals"]
        self.assertEqual(
            totals["published_today"] + totals["published_upcoming"],
            totals["published_unique_fixtures"],
        )

    def test_nothing_is_dropped_without_a_stated_reason(self):
        _, report = self._real_shaped_report()
        for row in report["sources"] + report["unconfigured_sources"]:
            if row["dropped_count"] or not row["published_unique_fixtures"]:
                self.assertTrue(row["drop_reasons"], row["source_id"])

    def test_a_card_cannot_be_counted_on_both_tabs(self):
        """The same fixture on Today and Upcoming would double its source's
        published count - and would be the tab-hygiene bug itself."""
        card = _card("src-a", "A vs B")
        report = build_source_coverage(
            configured_sources=[{"id": "src-a"}],
            raw_candidates=[], parsed_candidates=[], matched_candidates=[],
            deduped_candidates=[card],
            published_today_items=[card],
            published_upcoming_items=[],
        )
        row = report["sources"][0]
        self.assertEqual(1, row["published_unique_fixtures"])
        self.assertEqual(0, row["published_upcoming"])


if __name__ == "__main__":
    unittest.main()
