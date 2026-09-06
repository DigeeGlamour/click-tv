"""PROMPT 38/39 - a stale catalogue must not pose as active truth.

`config/event-fixtures.json` is a hand-written list of competitions. Measured
while auditing it: 8 competitions, 31 listed fixtures plus 32 double-header
rows expanding to 95 fixture records, **all of them in the past**, newest
kickoff 8.8 days ago. The system stands on the provider path now.

Nothing is deleted, because nothing needed to be: both readers are already
guarded by the clock.

  * `enrich_event_candidates` keeps only fixtures whose end is still ahead, so
    a finished fixture can never match a candidate.
  * `_competition_round_fixture` - the one thing only this file can do, binding
    "Day 3 1st Test 17 Aug 2026 | India Tour of Sri Lanka 2026" to its series -
    requires the fixture to be running *now*.

And the metadata it carries is real and stays: `duration_minutes` per
competition (Tests 480, The Hundred 210, CPL 270) is what gives a catalogue
fixture an honest end. PROMPT 18's cricket durations were *derived* from those
numbers but are hard-coded in `event_lifecycle.CRICKET_FORMAT_MINUTES` - the
lifecycle never reads this file, so deleting it would not have broken them, and
keeping it does not make them depend on it.

What was missing is that a dead catalogue and a quiet one looked identical from
outside: `catalogue: 0` says both. It now says which.
"""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scanner import event_lifecycle  # noqa: E402
from scanner.schedule_resolver import (  # noqa: E402
    CATALOGUE_ACTIVE,
    CATALOGUE_EMPTY,
    CATALOGUE_MISSING,
    CATALOGUE_NO_FUTURE,
    catalogue_state,
    enrich_event_candidates,
    load_fixtures,
)

ROOT = Path(__file__).resolve().parents[1]
CATALOGUE = ROOT / "config" / "event-fixtures.json"
NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)


def _catalogue(*fixtures, duration=240):
    root = Path(tempfile.mkdtemp())
    path = root / "event-fixtures.json"
    path.write_text(json.dumps({
        "version": 1,
        "competitions": [{
            "id": "test-series-2026",
            "name": "Test Series 2026",
            "timezone": "UTC",
            "duration_minutes": duration,
            "fixtures": list(fixtures),
        }],
    }), encoding="utf-8")
    return path


class TheStateIsReported(unittest.TestCase):
    def test_the_real_catalogue_is_read_and_measured(self):
        state = catalogue_state(CATALOGUE, now=NOW)
        self.assertEqual(len(load_fixtures(CATALOGUE)), state["fixtures"])
        self.assertIn(state["state"], (CATALOGUE_ACTIVE, CATALOGUE_NO_FUTURE))
        self.assertEqual(state["past"] + state["future"], state["fixtures"])

    def test_a_catalogue_with_nothing_ahead_says_so(self):
        path = _catalogue({"name": "A vs B", "start": "2026-08-27T19:00:00"})
        state = catalogue_state(path, now=NOW)
        self.assertEqual(CATALOGUE_NO_FUTURE, state["state"])
        self.assertEqual(0, state["schedulable_now"])
        self.assertEqual(0, state["future"])
        self.assertGreater(state["days_since_newest"], 8)

    def test_a_catalogue_with_a_future_fixture_is_active(self):
        path = _catalogue({"name": "A vs B", "start": "2026-09-20T19:00:00"})
        state = catalogue_state(path, now=NOW)
        self.assertEqual(CATALOGUE_ACTIVE, state["state"])
        self.assertEqual(1, state["schedulable_now"])

    def test_a_fixture_still_running_counts_as_schedulable(self):
        """Kickoff is behind us and the Test has four days left."""
        path = _catalogue({"name": "A vs B", "start": "2026-09-05T09:00:00"},
                          duration=480)
        state = catalogue_state(path, now=NOW)
        self.assertEqual(CATALOGUE_ACTIVE, state["state"])
        self.assertEqual(1, state["past"])

    def test_an_empty_or_missing_file_is_named_rather_than_guessed(self):
        empty = Path(tempfile.mkdtemp()) / "event-fixtures.json"
        empty.write_text(json.dumps({"version": 1, "competitions": []}),
                         encoding="utf-8")
        self.assertEqual(CATALOGUE_EMPTY, catalogue_state(empty, now=NOW)["state"])
        self.assertEqual(CATALOGUE_MISSING, catalogue_state(
            Path(tempfile.mkdtemp()) / "gone.json", now=NOW)["state"])

    def test_the_scan_reports_it(self):
        source = (ROOT / "scanner" / "events.py").read_text(
            encoding="utf-8").replace("\r\n", "\n")
        self.assertIn('schedule_stats["fixture_catalogue"] = catalogue_state(', source)
        self.assertIn("contributing nothing to this scan", source)


class AStaleCatalogueCannotPublishAnything(unittest.TestCase):
    def _resolve(self, path, candidate):
        return enrich_event_candidates(
            [candidate], fixture_path=path, now=NOW,
            authority_source_ids=set())

    def _candidate(self, name):
        return {"name": name, "source_id": "some-playlist",
                "url": "https://a.example/1.m3u8",
                "verification_status": "verified_global", "verified": True}

    def test_a_finished_fixture_never_matches_a_candidate(self):
        path = _catalogue({"name": "A vs B", "start": "2026-08-27T19:00:00"})
        _, stats = self._resolve(path, self._candidate("A vs B"))
        self.assertEqual(0, stats["catalogue"])
        self.assertEqual(0, stats["matched"])

    def test_a_future_fixture_is_still_matched_normally(self):
        """The guard is the clock, not the file: a legitimate catalogue use is
        not blocked by anything added here."""
        path = _catalogue({"name": "A vs B", "start": "2026-09-05T14:00:00"})
        _, stats = self._resolve(path, self._candidate("A vs B"))
        self.assertEqual(1, stats["matched"])

    def test_the_provider_path_does_not_depend_on_the_catalogue(self):
        path = _catalogue({"name": "Long Gone", "start": "2026-08-01T19:00:00"})
        resolved, stats = enrich_event_candidates(
            [{"name": "Genoa Vs Como", "source_id": "srhady-bingstream",
              "status": "UPCOMING",
              "start_time": (NOW + timedelta(hours=3)).isoformat()}],
            fixture_path=path, now=NOW,
            authority_source_ids={"srhady-bingstream"})
        self.assertEqual(1, stats["provider_fixture"])
        self.assertEqual(1, len(resolved))


class TheMetadataStays(unittest.TestCase):
    def test_duration_minutes_still_decides_a_catalogue_fixtures_end(self):
        path = _catalogue({"name": "A vs B", "start": "2026-09-20T10:00:00"},
                          duration=210)
        fixture = load_fixtures(path)[0]
        self.assertEqual(210, int(
            (fixture["end"] - fixture["start"]).total_seconds() // 60))
        self.assertEqual("sport", fixture["end_source"])

    def test_a_stated_end_still_beats_the_duration(self):
        path = _catalogue({"name": "A vs B", "start": "2026-09-20T10:00:00",
                           "end": "2026-09-24T18:00:00"}, duration=210)
        fixture = load_fixtures(path)[0]
        self.assertEqual("provider", fixture["end_source"])

    def test_the_lifecycle_durations_do_not_read_this_file(self):
        """PROMPT 18's Hundred = 210 was derived from the catalogue and then
        written down. The lifecycle never opens the file, so the catalogue is
        not load-bearing for it either way."""
        source = (ROOT / "scanner" / "event_lifecycle.py").read_text(
            encoding="utf-8")
        self.assertNotIn("load_fixtures", source)
        self.assertEqual(210, event_lifecycle.CRICKET_FORMAT_MINUTES["Hundred"])
        self.assertEqual(480, event_lifecycle.CRICKET_FORMAT_MINUTES["Test"])

    def test_nothing_here_deletes_or_rewrites_the_catalogue(self):
        before = CATALOGUE.read_bytes()
        catalogue_state(CATALOGUE, now=NOW)
        load_fixtures(CATALOGUE)
        self.assertEqual(before, CATALOGUE.read_bytes())


if __name__ == "__main__":
    unittest.main()
