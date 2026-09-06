"""PROMPT 34 - what the ladder tried, joined onto the fixture it tried it for.

The targeted trigger already keeps a ledger: how many attempts a fixture has
had, which five-minute slot the last one was in, and when a link first existed
for it. Until now that evidence lived only in state/upcoming-targeting.json,
where nothing read it back, so "is the ladder working?" could not be answered
from a report.

Naming, since the two plans disagree: FINAL_2 ধাপ ৬ writes `first_seen_link_at`
and FINAL_3 অংশ ৫ writes `first_link_at`. The ledger written by PROMPT 04-08
calls it `first_link_at`, and so do its accessor and its tests - so that is the
name here. Renaming a key in a live state file to match a note in a plan would
orphan every timestamp already written under it.

This is reporting. It reads the ledger and writes nothing to it.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scanner import targeted_scan  # noqa: E402
from scanner.fixture_stream_health import (  # noqa: E402
    LEDGER_FIELDS,
    build_fixture_stream_health,
    refresh_ladder_fields,
    write_fixture_stream_health,
)


def _route_free_card():
    return _card("Hull City Vs Aston Villa", url="https://a.example/1.m3u8")


def _card(name, **extra):
    card = {"id": name.lower().replace(" ", "-"), "name": name,
            "source_id": "sm-fancode"}
    card.update(extra)
    return card


def _ledger(**entries):
    return {"fixtures": entries}


def _rows(report):
    return {row["fixture_key"]: row for row in report["fixtures"]}


class TheLedgerEvidenceReachesTheRow(unittest.TestCase):
    def _report(self, ledger):
        cards = [
            _card("Ajax vs PSV Eindhoven"),
            _card("Genoa Vs Como", url="https://a.example/1.m3u8"),
            _card("Never Targeted"),
        ]
        return build_fixture_stream_health([], cards, [], ledger=ledger)

    def test_attempts_and_bucket_and_first_link_are_carried(self):
        report = self._report(_ledger(**{
            "ajax-vs-psv-eindhoven": {
                "attempts": 4,
                "last_attempt_bucket": "2026-09-05T14:35Z",
                "resolved": False,
            },
            "genoa-vs-como": {
                "attempts": 2,
                "last_attempt_bucket": "2026-09-05T14:05Z",
                "first_link_at": "2026-09-05T14:07:11+00:00",
                "resolved": True,
            },
        }))
        rows = _rows(report)
        self.assertEqual(4, rows["ajax-vs-psv-eindhoven"]["target_attempt_count"])
        self.assertEqual("2026-09-05T14:35Z",
                         rows["ajax-vs-psv-eindhoven"]["last_attempt_bucket"])
        self.assertEqual("", rows["ajax-vs-psv-eindhoven"]["first_link_at"])
        self.assertEqual("2026-09-05T14:07:11+00:00",
                         rows["genoa-vs-como"]["first_link_at"])
        self.assertEqual(2, rows["genoa-vs-como"]["target_attempt_count"])

    def test_a_fixture_the_ladder_never_touched_reports_an_honest_zero(self):
        rows = _rows(self._report(_ledger()))
        row = rows["never-targeted"]
        self.assertEqual(0, row["target_attempt_count"])
        self.assertEqual("", row["last_attempt_bucket"])
        self.assertEqual("", row["first_link_at"])
        self.assertFalse(row["targeted"])

    def test_the_totals_say_how_much_laddering_happened(self):
        report = self._report(_ledger(**{
            "ajax-vs-psv-eindhoven": {"attempts": 4},
            "genoa-vs-como": {"attempts": 2,
                              "first_link_at": "2026-09-05T14:07:11+00:00"},
        }))
        totals = report["totals"]
        self.assertEqual(2, totals["targeted_fixtures"])
        self.assertEqual(6, totals["targeted_attempts"])
        self.assertEqual(1, totals["fixtures_with_a_first_link_time"])

    def test_a_missing_or_broken_ledger_never_invents_attempts(self):
        for ledger in ({}, {"fixtures": None}, {"fixtures": {"x": "not a dict"}}):
            rows = _rows(build_fixture_stream_health(
                [], [_card("Genoa Vs Como")], [], ledger=ledger))
            self.assertEqual(0, rows["genoa-vs-como"]["target_attempt_count"])

    def test_a_nonsense_attempt_count_is_read_as_zero(self):
        rows = _rows(build_fixture_stream_health(
            [], [_card("Genoa Vs Como")], [],
            ledger=_ledger(**{"genoa-vs-como": {"attempts": "many"}})))
        self.assertEqual(0, rows["genoa-vs-como"]["target_attempt_count"])


class TheJoinSurvivesThePromotion(unittest.TestCase):
    def test_a_fixture_targeted_before_it_had_a_card_is_still_found(self):
        """The ledger keys by card id where there is one and by
        `name@2026090512` where there is not. A fixture targeted on Upcoming
        and promoted to Today must not lose its attempts on the way."""
        rows = _rows(build_fixture_stream_health(
            [], [_card("Aberdeen vs Rangers")], [],
            ledger=_ledger(**{
                "aberdeen-vs-rangers@2026090511": {
                    "attempts": 3, "name": "Aberdeen vs Rangers"},
            })))
        self.assertEqual(3, rows["aberdeen-vs-rangers"]["target_attempt_count"])

    def test_the_ledgers_own_name_is_used_when_the_key_does_not_match(self):
        rows = _rows(build_fixture_stream_health(
            [], [_card("Aberdeen vs Rangers")], [],
            ledger=_ledger(**{
                "some-other-key": {"attempts": 5, "name": "Aberdeen vs Rangers"},
            })))
        self.assertEqual(5, rows["aberdeen-vs-rangers"]["target_attempt_count"])


class TheSpellingOfANameNeverSplitsAFixture(unittest.TestCase):
    """Found on a real targeted run: `fc-schalke-04-vs-bayern-munchen`.

    The ledger keys by the card id, which keeps the letters the feed wrote;
    the verifier's group key kept them too. Both were compared against slugged
    keys elsewhere, so the same fixture existed under two spellings and its
    attempts landed on the row that had none.
    """

    def test_a_non_ascii_name_joins_the_ledger_entry(self):
        card = {"id": "fc-schalke-04-vs-bayern-münchen",
                "name": "FC Schalke 04 vs Bayern München",
                "source_id": "srhady-bingstream"}
        route = {"name": card["name"], "source_id": "srhady-bingstream",
                 "url": "https://a.example/1.m3u8",
                 "verification_status": "failed",
                 "_verification_group":
                     "upcoming:fc-schalke-04-vs-bayern-münchen"}
        report = build_fixture_stream_health(
            [route], [], [card],
            ledger=_ledger(**{card["id"]: {"attempts": 2,
                                           "last_attempt_bucket": "2026-09-05T16:35Z"}}))
        self.assertEqual(1, len(report["fixtures"]))
        row = report["fixtures"][0]
        self.assertEqual(2, row["target_attempt_count"])
        self.assertEqual(1, row["candidate_stream_count"])


class TheNamingIsSettledAgainstTheCode(unittest.TestCase):
    def test_the_field_the_ledger_actually_writes_is_the_field_reported(self):
        self.assertIn("first_link_at", LEDGER_FIELDS)
        self.assertTrue(hasattr(targeted_scan, "first_link_at"))
        self.assertTrue(hasattr(targeted_scan, "attempt_count"))
        self.assertTrue(hasattr(targeted_scan, "last_attempt_bucket"))

    def test_the_accessors_and_the_report_read_the_same_key(self):
        ledger = _ledger(**{"genoa-vs-como": {
            "attempts": 2, "first_link_at": "2026-09-05T14:07:11+00:00",
            "last_attempt_bucket": "2026-09-05T14:05Z"}})
        row = _rows(build_fixture_stream_health(
            [], [_card("Genoa Vs Como")], [], ledger=ledger))["genoa-vs-como"]
        self.assertEqual(targeted_scan.attempt_count(ledger, "genoa-vs-como"),
                         row["target_attempt_count"])
        self.assertEqual(targeted_scan.first_link_at(ledger, "genoa-vs-como"),
                         row["first_link_at"])
        self.assertEqual(
            targeted_scan.last_attempt_bucket(ledger, "genoa-vs-como"),
            row["last_attempt_bucket"])


class TheReportDescribesTheScanThatWroteIt(unittest.TestCase):
    """Found on a real targeted run: 8 fixtures attempted, 5 resolved, and the
    report written moments earlier said `targeted_fixtures: 0`.

    Not a join fault - an ordering one. A targeted scan saves its ledger after
    the outputs are published, so the report was built from the ledger as it
    stood before the run. The three ladder columns are refreshed once the
    attempts are final; nothing else in the report moves.
    """

    def _written(self, tmp, ledger):
        report = build_fixture_stream_health(
            [], [_route_free_card()], [], ledger={})
        path = Path(tmp) / "fixture-stream-health.json"
        write_fixture_stream_health(report, path)
        return path

    def test_the_ladder_columns_are_refreshed_in_place(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._written(tmp, {})
            before = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(0, before["fixtures"][0]["target_attempt_count"])

            result = refresh_ladder_fields(path, ledger=_ledger(**{
                "hull-city-vs-aston-villa": {
                    "attempts": 1,
                    "last_attempt_bucket": "2026-09-05T16:30Z",
                    "first_link_at": "2026-09-05T16:34:12+00:00",
                }}))
            after = json.loads(path.read_text(encoding="utf-8"))
            row = after["fixtures"][0]
            self.assertEqual(1, result["refreshed"])
            self.assertEqual(1, row["target_attempt_count"])
            self.assertEqual("2026-09-05T16:30Z", row["last_attempt_bucket"])
            self.assertEqual("2026-09-05T16:34:12+00:00", row["first_link_at"])
            self.assertEqual(1, after["totals"]["targeted_fixtures"])
            self.assertEqual(1, after["totals"]["targeted_attempts"])

    def test_nothing_but_the_ladder_columns_moves(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._written(tmp, {})
            before = json.loads(path.read_text(encoding="utf-8"))["fixtures"][0]
            refresh_ladder_fields(path, ledger=_ledger(**{
                "hull-city-vs-aston-villa": {"attempts": 3}}))
            after = json.loads(path.read_text(encoding="utf-8"))["fixtures"][0]
            for field in ("candidate_stream_count", "verified_stream_count",
                          "failed_stream_count", "failure_codes",
                          "published", "published_without_stream",
                          "fallback_available", "source_id"):
                self.assertEqual(before[field], after[field], field)

    def test_a_missing_report_file_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = refresh_ladder_fields(Path(tmp) / "gone.json", ledger={})
            self.assertEqual({"refreshed": 0}, result)

    def test_the_scan_refreshes_the_report_after_saving_the_ledger(self):
        source = (Path(__file__).resolve().parents[1] / "scan.py").read_text(
            encoding="utf-8")
        after_save = source.split("save_ledger(ledger, ledger_path)", 1)[1]
        self.assertIn("refresh_ladder_fields(", after_save)


class ReportingChangesNothing(unittest.TestCase):
    def test_the_ledger_is_not_written_to(self):
        ledger = _ledger(**{"genoa-vs-como": {"attempts": 2}})
        before = repr(ledger)
        build_fixture_stream_health([], [_card("Genoa Vs Como")], [], ledger=ledger)
        self.assertEqual(before, repr(ledger))

    def test_a_repeated_report_does_not_move_the_first_link_time(self):
        """`first_link_at` is how long the ladder took. A report that rewrote
        it would make every fixture look instantaneous."""
        ledger = _ledger(**{"genoa-vs-como": {
            "attempts": 2, "first_link_at": "2026-09-05T14:07:11+00:00"}})
        for _ in range(3):
            row = _rows(build_fixture_stream_health(
                [], [_card("Genoa Vs Como")], [], ledger=ledger))["genoa-vs-como"]
            self.assertEqual("2026-09-05T14:07:11+00:00", row["first_link_at"])
        self.assertEqual("2026-09-05T14:07:11+00:00",
                         ledger["fixtures"]["genoa-vs-como"]["first_link_at"])

    def test_reading_the_real_ledger_file_never_raises(self):
        report = build_fixture_stream_health(
            [], [_card("Genoa Vs Como")], [],
            ledger_path=Path("state") / "does-not-exist.json")
        self.assertEqual(1, len(report["fixtures"]))


if __name__ == "__main__":
    unittest.main()
