"""PROMPT 28 - a feed is fresh if its fixtures are ahead of the clock.

ChatGPT's reading was that bingstream's playlist carried an internal
`last_update_time` of 20 August against an audit date of 4 September, so the
feed was fifteen days stale and its 85 Upcoming cards should not be built. The
kickoffs of those 85 cards were then measured: earliest 4 September 08:30 UTC,
latest 5 September 02:06 UTC, none of them in the past. A file written on 20
August cannot contain 5 September fixtures, so the field is simply not
maintained - and deleting on its say-so would have removed 68.5% of a correct
Upcoming tab.

FINAL_3 keeps the idea and changes the measure. A feed that has really stopped
moving cannot hide it: its own fixtures fall into the past one by one, and
`future_fixture_count` reaches zero on its own. That depends on the clock, not
on the source's honesty about itself.
"""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scanner.source_coverage import build_source_coverage  # noqa: E402

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)


def _fixture(source_id, identity, start, **extra):
    item = {"source_id": source_id, "id": identity, "name": identity,
            "start_time": start.isoformat()}
    item.update(extra)
    return item


class TheFourStates(unittest.TestCase):
    def _row(self, health, candidates=(), **kwargs):
        report = build_source_coverage(
            configured_sources=[{"id": "src"}],
            raw_candidates=list(candidates),
            parsed_candidates=None,
            matched_candidates=[],
            source_health={"src": health},
            now=NOW,
            **kwargs,
        )
        return report["sources"][0]

    def test_a_future_kickoff_makes_it_fresh(self):
        row = self._row(
            {"status": "success", "http_status": 200, "raw_items": 39},
            [_fixture("src", "a-vs-b", NOW + timedelta(hours=6))],
        )
        self.assertEqual("FRESH", row["content_state"])
        self.assertEqual(1, row["future_fixture_count"])
        self.assertEqual((NOW + timedelta(hours=6)).isoformat(),
                         row["newest_fixture_start"])

    def test_records_but_no_future_kickoff_is_stale(self):
        row = self._row(
            {"status": "success", "http_status": 200, "raw_items": 12},
            [_fixture("src", "a-vs-b", NOW - timedelta(days=2)),
             _fixture("src", "c-vs-d", NOW - timedelta(hours=3))],
        )
        self.assertEqual("STALE", row["content_state"])
        self.assertEqual(0, row["future_fixture_count"])
        self.assertIn("already kicked off", " ".join(row["drop_reasons"]))

    def test_a_successful_but_empty_answer_is_empty_not_stale(self):
        row = self._row({"status": "success_empty", "http_status": 200,
                         "raw_items": 0})
        self.assertEqual("EMPTY", row["content_state"])

    def test_a_failed_fetch_is_unreachable_not_empty(self):
        row = self._row({"status": "failed", "http_status": 404, "raw_items": 0,
                         "error": "HTTP Error 404: Not Found"})
        self.assertEqual("UNREACHABLE", row["content_state"])

    def test_the_two_zeroes_are_never_confused(self):
        """HTTP success with nothing in it and a dead host are different
        problems with different fixes; one number cannot mean both."""
        empty = self._row({"status": "success", "http_status": 200, "raw_items": 0})
        dead = self._row({"status": "failed", "http_status": 0, "raw_items": 0})
        self.assertEqual("EMPTY", empty["content_state"])
        self.assertEqual("UNREACHABLE", dead["content_state"])

    def test_the_report_counts_the_states(self):
        report = build_source_coverage(
            configured_sources=[{"id": "a"}, {"id": "b"}, {"id": "c"}],
            raw_candidates=[_fixture("a", "x-vs-y", NOW + timedelta(hours=2)),
                            _fixture("b", "p-vs-q", NOW - timedelta(days=1))],
            parsed_candidates=None,
            matched_candidates=[],
            source_health={
                "a": {"status": "success", "raw_items": 5},
                "b": {"status": "success", "raw_items": 5},
                "c": {"status": "failed", "raw_items": 0},
            },
            now=NOW,
        )
        self.assertEqual({"FRESH": 1, "STALE": 1, "EMPTY": 0, "UNREACHABLE": 1},
                         report["content_states"])


class ThePayloadsOwnTimestampIsNotConsulted(unittest.TestCase):
    def test_a_feed_that_calls_itself_old_is_fresh_if_its_fixtures_are_ahead(self):
        """The bingstream case, at the numbers it was measured with."""
        kickoffs = [NOW + timedelta(hours=n) for n in range(1, 86)]
        report = build_source_coverage(
            configured_sources=[{"id": "srhady-bingstream"}],
            raw_candidates=[
                _fixture("srhady-bingstream", "fixture-%d" % index, start,
                         last_update_time="2026-08-20T00:00:00+00:00")
                for index, start in enumerate(kickoffs)
            ],
            parsed_candidates=None,
            matched_candidates=[],
            source_health={"srhady-bingstream": {"status": "success",
                                                 "raw_items": 874}},
            now=NOW,
        )
        row = report["sources"][0]
        self.assertEqual("FRESH", row["content_state"])
        self.assertEqual(85, row["future_fixture_count"])

    def test_no_payload_timestamp_field_is_read_anywhere(self):
        source = (Path(__file__).resolve().parents[1] / "scanner"
                  / "source_coverage.py").read_text(encoding="utf-8")
        body = source.split('"""', 2)[2]          # past the module docstring
        for field in ("last_update_time", "updated_at", "generated_at"):
            self.assertNotIn('get("%s")' % field, body)

    def test_freshness_does_not_decide_whether_a_fixture_publishes(self):
        """Reporting a state and refusing a card are different jobs. A STALE
        source's fixtures are still counted where they were published."""
        card = _fixture("src", "a-vs-b", NOW - timedelta(hours=4))
        report = build_source_coverage(
            configured_sources=[{"id": "src"}],
            raw_candidates=[card],
            parsed_candidates=None,
            matched_candidates=[card],
            deduped_candidates=[card],
            published_today_items=[card],
            published_upcoming_items=[],
            source_health={"src": {"status": "success", "raw_items": 9}},
            now=NOW,
        )
        row = report["sources"][0]
        self.assertEqual("STALE", row["content_state"])
        self.assertEqual(1, row["published_today"])


class TheHorizonFollowsTheFixtureNotTheRecord(unittest.TestCase):
    def test_a_source_credited_only_through_a_merged_card_is_not_called_stale(self):
        """srhady-axsports-live loses every record to an exact-duplicate check
        and reaches the tabs only through the cards its identity survives on.
        Counting its own surviving records would report a source carrying 95
        future kickoffs as having none."""
        merged = [
            {"id": "a-vs-b", "name": "A vs B", "source_id": "srhady-bingstream",
             "source_ids": ["srhady-axsports-live"],
             "start_time": (NOW + timedelta(hours=3)).isoformat()},
        ]
        report = build_source_coverage(
            configured_sources=[{"id": "srhady-axsports-live"}],
            raw_candidates=[],
            parsed_candidates=None,
            matched_candidates=[],
            deduped_candidates=merged,
            published_today_items=[],
            published_upcoming_items=merged,
            source_health={"srhady-axsports-live": {"status": "success",
                                                    "raw_items": 632}},
            now=NOW,
        )
        row = report["sources"][0]
        self.assertEqual(1, row["future_fixture_count"])
        self.assertEqual("FRESH", row["content_state"])

    def test_one_fixture_seen_at_three_stages_is_counted_once(self):
        card = _fixture("src", "a-vs-b", NOW + timedelta(hours=2))
        report = build_source_coverage(
            configured_sources=[{"id": "src"}],
            raw_candidates=[card],
            parsed_candidates=None,
            matched_candidates=[card],
            deduped_candidates=[card],
            published_today_items=[card],
            published_upcoming_items=[],
            source_health={"src": {"status": "success", "raw_items": 1}},
            now=NOW,
        )
        self.assertEqual(1, report["sources"][0]["future_fixture_count"])

    def test_a_record_with_no_kickoff_never_invents_one(self):
        report = build_source_coverage(
            configured_sources=[{"id": "src"}],
            raw_candidates=[{"source_id": "src", "id": "no-time", "name": "No Time"}],
            parsed_candidates=None,
            matched_candidates=[],
            source_health={"src": {"status": "success", "raw_items": 4}},
            now=NOW,
        )
        row = report["sources"][0]
        self.assertEqual("", row["newest_fixture_start"])
        self.assertEqual(0, row["future_fixture_count"])
        self.assertEqual("STALE", row["content_state"])


if __name__ == "__main__":
    unittest.main()
