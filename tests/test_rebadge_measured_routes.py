"""Published badges can be brought into line with the ledger without a scan.

The merger already computes a card's badge from the measured-playback ledger, so
a fresh scan is correct on its own. What no scan can do is reach a catalogue
published BEFORE the measurement existed, and the gap between a browser session
and the next channels scan is measured in hours - during which the site keeps
offering a route it has been measured unable to play, under a green "Verified".

On 2026-08-29 the published Star Jalsha card offered three backups badged
"Verified": cache.devm3u.top (15.56 s of media in a 120 s window, twice),
premiumtvs.space and rgkkw.live (29.86 s and 24.66 s, with 90 s and 95 s
stalled). The channels scan that would have re-badged them was sixteen hours
away.

This marks and never hides. The tests below are mostly about that boundary.
"""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import playback_evidence  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "rebadge_measured_routes", ROOT / "scripts" / "rebadge-measured-routes.py"
)
rebadge = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rebadge)

DEAD = "http://rgkkw.live:80/live/1Aoen7elp5/IgMJ60tmAa/198.ts"
ALIVE = "https://s3.itcnbd.live/server-4/stream/abc.m3u8"


class RebadgeTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.ledger = str(Path(self.dir.name) / "ledger.json")
        real = playback_evidence.DEFAULT_PATH
        playback_evidence.DEFAULT_PATH = self.ledger
        self.addCleanup(setattr, playback_evidence, "DEFAULT_PATH", real)
        playback_evidence.record(
            DEAD, "media progress 29.86s < 115s", sessions=2,
            media_progress_seconds=[29.86, 24.66], window_seconds=120.0,
            evidence_report="reports/x.json", vantage="bangladesh-residential",
            path=self.ledger,
        )
        self.data = Path(self.dir.name) / "channels"
        self.data.mkdir()

    def _write(self, cards):
        (self.data / "indian.json").write_text(
            json.dumps({"category": "Indian", "channels": cards}),
            encoding="utf-8",
        )

    def _read(self):
        return json.loads(
            (self.data / "indian.json").read_text(encoding="utf-8")
        )["channels"]

    def _run(self, *extra):
        rebadge.main(["--data-dir", str(self.data), *extra])

    def test_a_measured_dead_backup_stops_saying_verified(self):
        self._write([{
            "name": "Star Jalsha", "url": ALIVE, "verification_badge": "Verified",
            "backups": [{"name": "Backup-3", "url": DEAD,
                         "verification_badge": "Verified"}],
        }])
        self._run()
        backup = self._read()[0]["backups"][0]
        self.assertEqual(playback_evidence.BADGE, backup["verification_badge"])
        self.assertIn("29.86", backup["playback_unproven_reason"])

    def test_a_route_the_ledger_says_nothing_about_is_untouched(self):
        self._write([{
            "name": "Star Jalsha", "url": ALIVE, "verification_badge": "Verified",
            "backups": [],
        }])
        self._run()
        self.assertEqual("Verified", self._read()[0]["verification_badge"])

    def test_a_primary_is_marked_the_same_way_as_a_backup(self):
        self._write([{
            "name": "Zee Bangla", "url": DEAD, "verification_badge": "Verified",
            "backups": [],
        }])
        self._run()
        self.assertEqual(playback_evidence.BADGE, self._read()[0]["verification_badge"])

    def test_a_superseded_failure_gets_its_badge_back(self):
        playback_evidence.record_proof(
            DEAD, vantage="bangladesh-residential", sessions=2,
            media_progress_seconds=[119.9, 119.8], window_seconds=120.0,
            evidence_report="reports/y.json", path=self.ledger,
        )
        self._write([{
            "name": "Zee Bangla", "url": DEAD,
            "verification_status": "verified_global",
            "verification_badge": playback_evidence.BADGE,
            "playback_unproven": True,
            "playback_unproven_reason": "media progress 29.86s < 115s",
            "backups": [],
        }])
        self._run()
        card = self._read()[0]
        self.assertEqual("Verified", card["verification_badge"])
        self.assertNotIn("playback_unproven", card)

    def test_nothing_is_hidden_or_removed(self):
        """It marks. A card and every route it carries survive."""
        self._write([{
            "name": "Star Jalsha", "url": DEAD, "verification_badge": "Verified",
            "publish_allowed": True, "verified": True,
            "backups": [{"name": "Backup-1", "url": ALIVE,
                         "verification_badge": "Verified"}],
        }])
        self._run()
        card = self._read()[0]
        self.assertEqual(1, len(card["backups"]))
        self.assertEqual(DEAD, card["url"])
        self.assertTrue(card["publish_allowed"])

    def test_a_dry_run_writes_nothing(self):
        self._write([{
            "name": "Star Jalsha", "url": DEAD, "verification_badge": "Verified",
            "backups": [],
        }])
        self._run("--dry-run")
        self.assertEqual("Verified", self._read()[0]["verification_badge"])


if __name__ == "__main__":
    unittest.main()
