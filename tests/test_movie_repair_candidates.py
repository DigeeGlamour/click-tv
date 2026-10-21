"""ধারা ৪.৫ steps 3-5: a dead card's film, found where a source still serves it.

Step 3 searches the snapshot by canonical identity, step 4 probes only the new
URLs, step 5 records what answered for the scan to publish. Plus the P3 and
playback-failure wiring of the same section, which the repair queue was
missing.
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import movie_link_health as health  # noqa: E402
from scanner import movie_repair_candidates as rc  # noqa: E402
from scanner import movie_repair_queue as queue_module  # noqa: E402
from scanner import movies  # noqa: E402

DEAD = "https://lrtyuh.b-cdn.net/upload/videos/2026/10/Toxic.2026.1080p.mkv"
LIVE = "https://ftp.example/Indian/Toxic%20(2026)/Toxic.2026.1080p.mp4"
CARD = {"id": "toxic-2026", "name": "Toxic", "year": 2026, "url": DEAD, "backups": []}


def _row(name, url, source="sm-movie-bdix-combined"):
    return {"name": name, "url": url, "source_id": source}


class StepThreeFindsTheSameFilmOnly(unittest.TestCase):
    def test_the_same_film_elsewhere_is_a_candidate(self):
        offers = rc.index_snapshot([_row("Toxic (2026) 1080p", LIVE)])
        found = rc.candidates_for(CARD, offers)
        self.assertEqual([f["url"] for f in found], [LIVE])

    def test_a_different_year_is_a_different_film(self):
        offers = rc.index_snapshot([_row("Toxic (2019) 1080p", LIVE)])
        self.assertEqual(rc.candidates_for(CARD, offers), [])

    def test_a_link_the_card_already_has_is_not_offered_again(self):
        offers = rc.index_snapshot([_row("Toxic (2026) 1080p", DEAD)])
        self.assertEqual(rc.candidates_for(CARD, offers), [])

    def test_a_declared_low_copy_is_never_a_candidate(self):
        offers = rc.index_snapshot([_row("Toxic (2026) 480p", LIVE.replace("1080p", "480p"))])
        self.assertEqual(rc.candidates_for(CARD, offers), [])

    def test_probes_are_bounded(self):
        rows = [_row("Toxic (2026) 1080p", f"https://h{i}.example/Toxic.1080p.mkv")
                for i in range(30)]
        found = rc.candidates_for(CARD, rc.index_snapshot(rows))
        self.assertEqual(len(found), rc.MAX_PROBES_PER_ENTRY)


class StepFourProbesOnlyTheNewAndStopsAtFive(unittest.TestCase):
    def test_it_stops_once_five_answer(self):
        candidates = [{"url": f"https://h{i}.example/x.mkv"} for i in range(8)]
        probed = []
        live, _dead = rc.verify(candidates, lambda url: probed.append(url) or True)
        self.assertEqual(len(live), rc.MAX_BACKUPS)
        self.assertEqual(len(probed), rc.MAX_BACKUPS)

    def test_a_probe_that_raises_is_not_a_verdict(self):
        def boom(_url):
            raise OSError("network")
        live, dead = rc.verify([{"url": LIVE}], boom)
        self.assertEqual(live, [])
        self.assertEqual(len(dead), 1)


class StepFiveRecordsForTheScanToPublish(unittest.TestCase):
    def test_recorded_and_then_offered_back_to_the_card(self):
        store = {"links": {}}
        written = rc.record(store, "toxic-2026",
                            [{"url": LIVE, "source_id": "x", "height": "1080"}],
                            health_module=health)
        self.assertEqual(written, 1)
        found = rc.discovered_for(store, "toxic-2026", health_module=health)
        self.assertEqual([f["url"] for f in found], [LIVE])

    def test_a_tokenized_link_is_never_written_to_the_committed_ledger(self):
        store = {"links": {}}
        signed = "https://cdn.example/x.mkv?token=abc&expires=1999999999"
        with mock.patch.object(health, "host_class", return_value=health.HOST_TOKENIZED):
            self.assertEqual(rc.record(store, "c", [{"url": signed}], health_module=health), 0)
        self.assertEqual(store["links"], {})

    def test_a_discovery_that_went_unhealthy_is_not_offered(self):
        store = {"links": {}}
        rc.record(store, "toxic-2026", [{"url": LIVE}], health_module=health)
        for record in store["links"].values():
            record["status"] = health.STATUS_FAILING
        self.assertEqual(rc.discovered_for(store, "toxic-2026", health_module=health), [])


class TheScanPutsItOnTheCard(unittest.TestCase):
    def setUp(self):
        movies._REPAIR_LEDGER.clear()
        store = {"links": {}}
        rc.record(store, "toxic-2026", [{"url": LIVE, "height": "1080"}], health_module=health)
        movies._REPAIR_LEDGER.update(store)

    def tearDown(self):
        movies._REPAIR_LEDGER.clear()

    def test_the_discovery_becomes_a_backup_and_is_counted(self):
        card = dict(CARD, backups=[])
        self.assertEqual(movies._adopt_repair_discoveries([card], "hindi"), 1)
        self.assertEqual(card["backups"][0]["url"], LIVE)
        self.assertEqual(card["available_link_count"], 2)
        self.assertEqual(card["url"], DEAD, "the primary is never replaced here")

    def test_never_more_than_five_backups(self):
        card = dict(CARD, backups=[{"url": f"https://b{i}.example/x.mkv"} for i in range(5)])
        self.assertEqual(movies._adopt_repair_discoveries([card], "hindi"), 0)
        self.assertEqual(len(card["backups"]), 5)

    def test_an_undeclared_resolution_is_published_as_quality_unknown(self):
        movies._REPAIR_LEDGER.clear()
        store = {"links": {}}
        rc.record(store, "toxic-2026", [{"url": LIVE.replace("1080p", "")}],
                  health_module=health)
        movies._REPAIR_LEDGER.update(store)
        card = dict(CARD, backups=[])
        movies._adopt_repair_discoveries([card], "hindi")
        self.assertIs(card["backups"][0].get("quality_unknown"), True)


class TheRepairRunDoesStepsThreeToFive(unittest.TestCase):
    def test_discover_uses_the_snapshot_and_the_probe(self):
        spec = importlib.util.spec_from_file_location(
            "repair_run", ROOT / "scripts" / "movie-repair-run.py")
        run = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(run)
        store = {"links": {}}
        with mock.patch.object(run, "load_snapshot",
                               return_value=[_row("Toxic (2026) 1080p", LIVE)]), \
                mock.patch("scanner.movie_baseline.published_movies",
                           return_value=[("hindi", CARD)]):
            summary = run.discover(ROOT, store, [{"content_id": "toxic-2026"}],
                                   probe=lambda url: url == LIVE)
        self.assertEqual(summary["live"], 1)
        self.assertEqual(summary["recorded"], 1)

    def test_the_workflow_restores_the_scan_snapshot_read_only(self):
        workflow = (ROOT / ".github" / "workflows" / "movie-repair.yml").read_text(
            encoding="utf-8")
        self.assertIn("actions/cache/restore", workflow)
        self.assertNotIn("actions/cache/save", workflow)


class QueueP3AndPlaybackFailure(unittest.TestCase):
    def _queue_for(self, store, playback_reason=""):
        q = {"entries": {}}
        card = {"id": "c1", "name": "C", "url": DEAD, "backups": []}
        with mock.patch.object(queue_module, "_playback_failed", return_value=playback_reason):
            counts = queue_module.refresh_from_catalogue(q, [("hindi", card)], store)
        return q, counts

    def _store(self, status, stale_reason):
        identity = health.stream_id(DEAD)
        return {"links": {identity: {"status": status, "stale_reason": stale_reason}}}

    def test_a_geo_refusal_is_p3_not_p0(self):
        q, counts = self._queue_for(self._store("failing", "geo"))
        self.assertEqual(counts[queue_module.P3_RESTRICTED_OR_UNCERTAIN], 1)
        self.assertEqual(counts[queue_module.P0_NO_LIVE_LINK], 0)

    def test_a_host_down_primary_with_no_backup_is_still_p0(self):
        _q, counts = self._queue_for(self._store("failing", "host_down"))
        self.assertEqual(counts[queue_module.P0_NO_LIVE_LINK], 1)

    def test_a_measured_playback_failure_queues_the_card(self):
        q, counts = self._queue_for({"links": {}}, playback_reason="media progress 3s < 115s")
        self.assertEqual(counts[queue_module.P0_NO_LIVE_LINK], 1)
        entry = next(iter(q["entries"].values()))
        self.assertIn("playback_failed", entry["reason"])


if __name__ == "__main__":
    unittest.main()
