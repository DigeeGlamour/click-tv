"""PROMPT 26 - the coverage report is answerable to the configuration.

The report exists to answer one question: did every source a scan is supposed
to read actually contribute, and if not, where was it lost? The old builder
took its list of sources from `raw_event_candidates` - from the sources that
had already contributed - so the only sources it could report on were the ones
the question was not about. On the day this was measured, ten of twenty-one
configured sources had no row, including `srhady-axsports-live`, which had
fetched 283 items and put its name on 48 published cards.

The list now comes from config/sources/today-match.json. Twenty-one configured
sources, twenty-one rows, whatever each of them did.
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
)

CONFIG = Path(__file__).resolve().parents[1] / "config" / "sources" / "today-match.json"


def _config_dir(sources):
    """A throwaway config tree holding just a Today Match source list."""
    root = Path(tempfile.mkdtemp())
    (root / "sources").mkdir(parents=True)
    (root / "sources" / "today-match.json").write_text(
        json.dumps({"pipeline": "today_match", "sources": sources}),
        encoding="utf-8",
    )
    return root


def _rows(report):
    return {row["source_id"]: row for row in report["sources"]}


class TheListComesFromTheConfiguration(unittest.TestCase):
    def test_the_real_config_is_read_and_every_enabled_source_is_returned(self):
        configured = load_configured_sources(CONFIG.parents[1])
        declared = [
            entry for entry in json.loads(CONFIG.read_text(encoding="utf-8"))["sources"]
            if entry.get("enabled") is not False
        ]
        self.assertEqual(
            [entry["id"] for entry in declared],
            [entry["id"] for entry in configured],
        )

    def test_a_disabled_source_is_not_a_configured_row(self):
        root = _config_dir([
            {"id": "on-a", "enabled": True},
            {"id": "off", "enabled": False},
            {"id": "on-b"},                       # absent means enabled
        ])
        self.assertEqual(
            ["on-a", "on-b"],
            [entry["id"] for entry in load_configured_sources(root)],
        )

    def test_an_unreadable_configuration_returns_nothing_rather_than_raising(self):
        self.assertEqual([], load_configured_sources(Path(tempfile.mkdtemp())))


class EverySourceGetsARow(unittest.TestCase):
    def _report(self, configured, **stages):
        return build_source_coverage(
            configured_sources=configured,
            raw_candidates=stages.get("raw", []),
            parsed_candidates=stages.get("parsed", []),
            matched_candidates=stages.get("matched", []),
            published_items=stages.get("published", []),
            fetch_errors=stages.get("errors"),
        )

    def test_the_row_count_equals_the_configured_count(self):
        configured = load_configured_sources(CONFIG.parents[1])
        report = self._report(configured, raw=[{"source_id": "srhady-bingstream"}])
        self.assertEqual(len(configured), report["configured_source_count"])
        self.assertEqual(report["configured_source_count"], report["source_count"])
        self.assertEqual(report["source_count"], len(report["sources"]))

    def test_a_source_that_fetched_nothing_still_has_a_row(self):
        """The branch that says 'nothing fetched from this source' used to be
        unreachable: a source with no candidates was not in the list."""
        report = self._report([{"id": "quiet"}, {"id": "busy"}],
                              raw=[{"source_id": "busy"}])
        rows = _rows(report)
        self.assertEqual({"quiet", "busy"}, set(rows))
        self.assertEqual(0, rows["quiet"]["raw_items"])
        self.assertIn("nothing fetched", rows["quiet"]["drop_reasons"][0])

    def test_a_source_that_failed_to_fetch_still_has_a_row(self):
        report = self._report([{"id": "broken"}], errors={"broken": "HTTP 503"})
        rows = _rows(report)
        self.assertIn("broken", rows)
        self.assertIn("HTTP 503", rows["broken"]["drop_reasons"][0])

    def test_a_source_that_published_nothing_still_has_a_row(self):
        report = self._report([{"id": "primevideo"}],
                              raw=[{"source_id": "primevideo"}] * 4,
                              parsed=[{"source_id": "primevideo"}] * 4)
        rows = _rows(report)
        self.assertEqual(4, rows["primevideo"]["parsed_events"])
        self.assertEqual(0, rows["primevideo"]["published_unique_fixtures"])
        self.assertTrue(rows["primevideo"]["drop_reasons"])

    def test_every_configured_row_says_so(self):
        report = self._report([{"id": "a"}, {"id": "b"}])
        self.assertTrue(all(row["configured"] for row in report["sources"]))


class NothingUnconfiguredSitsAmongThem(unittest.TestCase):
    """`streamed-fixtures` fetches 407 records a scan and is in no config file.

    It made twelve rows look like a plausible twenty-one. It does not belong
    among the configured rows - but its accounting is real, so it is reported
    beside them rather than thrown away.
    """

    def _report(self):
        return build_source_coverage(
            configured_sources=[{"id": "configured-one"}],
            raw_candidates=[{"source_id": "configured-one"}]
                           + [{"source_id": "streamed-fixtures"}] * 407,
            parsed_candidates=[{"source_id": "streamed-fixtures"}] * 407,
            matched_candidates=[],
            published_items=[],
        )

    def test_it_is_not_a_configured_row(self):
        report = self._report()
        self.assertEqual(["configured-one"],
                         [row["source_id"] for row in report["sources"]])
        self.assertEqual(1, report["configured_source_count"])

    def test_its_accounting_is_not_lost(self):
        report = self._report()
        extra = {row["source_id"]: row for row in report["unconfigured_sources"]}
        self.assertIn("streamed-fixtures", extra)
        self.assertEqual(407, extra["streamed-fixtures"]["raw_items"])
        self.assertEqual(407, extra["streamed-fixtures"]["parsed_events"])
        self.assertFalse(extra["streamed-fixtures"]["configured"])
        self.assertEqual(407, report["unconfigured_totals"]["raw_items"])

    def test_a_configured_source_is_never_duplicated_into_the_extra_list(self):
        report = self._report()
        self.assertNotIn(
            "configured-one",
            [row["source_id"] for row in report["unconfigured_sources"]],
        )

    def test_with_no_configuration_at_all_the_report_is_not_silently_empty(self):
        report = build_source_coverage(
            configured_sources=[],
            raw_candidates=[{"source_id": "observed"}],
            parsed_candidates=[],
            matched_candidates=[],
            published_items=[],
        )
        self.assertEqual(0, report["configured_source_count"])
        self.assertEqual(["observed"], [r["source_id"] for r in report["sources"]])
        self.assertFalse(report["sources"][0]["configured"])


class TheCallSiteUsesIt(unittest.TestCase):
    def test_the_publish_path_no_longer_derives_the_list_from_candidates(self):
        source = (
            Path(__file__).resolve().parents[1] / "scanner" / "events.py"
        ).read_text(encoding="utf-8").replace("\r\n", "\n")
        call = source.split("build_source_coverage(", 1)[1].split("\n        )", 1)[0]
        # PROMPT 31 gave the list a name, because the invariant check needs
        # the same list the report was built from. It still comes from the
        # configuration and from nothing else.
        self.assertIn("configured = load_configured_sources()", source)
        self.assertIn("configured_sources=configured,", call)
        self.assertNotIn("raw_event_candidates", call.split("raw_candidates=", 1)[0])


if __name__ == "__main__":
    unittest.main()
