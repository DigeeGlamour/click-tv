"""The measured-playback ledger, its vantage, and the badge a backup gets.

Two defects this file pins, both found on 2026-08-29 against the published
Star Jalsha card:

  1. The card's Backup-2 shipped ``"verification_badge": "Verified"`` on
     ``rgkkw.live`` while that exact route sat in the ledger as measured
     unplayable over two sessions, and its Backup-1 shipped "Verified" on a DRM
     route no browser had ever been asked to play. Only the primary was passed
     through the playback-aware badge; the backups went straight to the
     verification badge, so the evidence never reached them.

  2. The ledger recorded a verdict without saying where it was measured. All
     four rows came from a US datacentre runner. Re-measured from a residential
     connection in Bangladesh - where this site's viewers are -
     ``cache.devm3u.top`` returned 112.2 s and then a full pass over two 120 s
     sessions. A failure with no vantage reads as a fact about every viewer,
     and it is not one.

Nothing here hides a card or changes ``publish_allowed``. The subject is only
what a card is allowed to *claim*.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import merger  # noqa: E402
from scanner import playback_evidence  # noqa: E402

URL = "http://rgkkw.live:80/live/1Aoen7elp5/IgMJ60tmAa/198.ts"
OTHER = "https://cache.devm3u.top/hls/starjalsha.m3u8"


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = str(Path(self.dir.name) / "measured-playback-failures.json")

    def _fail(self, url=URL, vantage="github-actions-us", reason="ac-3 unsupported"):
        return playback_evidence.record(
            url,
            reason,
            sessions=2,
            media_progress_seconds=[0, 0],
            window_seconds=120.0,
            evidence_report="reports/x.json",
            vantage=vantage,
            path=self.path,
        )

    def _pass(self, url=URL, vantage="bangladesh-residential"):
        return playback_evidence.record_proof(
            url,
            vantage=vantage,
            sessions=2,
            media_progress_seconds=[119.0, 120.0],
            window_seconds=120.0,
            evidence_report="reports/y.json",
            path=self.path,
        )

    def test_a_recorded_failure_gives_a_reason(self):
        self.assertTrue(self._fail())
        self.assertIn("ac-3", playback_evidence.unproven_reason(URL, self.path))

    def test_the_vantage_is_stored_with_the_measurement(self):
        self._fail(vantage="github-actions-us")
        self.assertEqual(
            "github-actions-us", playback_evidence.vantage_of(URL, self.path)
        )

    def test_a_row_written_without_one_reads_as_unknown_not_as_global(self):
        """Legacy rows predate the field. Guessing for them invents evidence."""
        self._fail(vantage="")
        self.assertEqual(
            playback_evidence.UNKNOWN_VANTAGE,
            playback_evidence.vantage_of(URL, self.path),
        )

    def test_a_later_pass_supersedes_the_failure(self):
        self._fail()
        self.assertTrue(self._pass())
        self.assertEqual("", playback_evidence.unproven_reason(URL, self.path))

    def test_but_it_does_not_delete_the_history(self):
        self._fail()
        self._pass()
        stored = json.loads(Path(self.path).read_text(encoding="utf-8"))["routes"]
        row = stored[list(stored)[0]]
        self.assertIn("ac-3", row["reason"])
        self.assertEqual("github-actions-us", row["vantage"])
        self.assertEqual("bangladesh-residential", row["superseded_by"]["vantage"])

    def test_a_stale_failure_cannot_overwrite_a_newer_pass(self):
        """The US runner re-measures nightly; it must not erase the proof."""
        self._fail()
        self._pass()
        self.assertFalse(self._fail(reason="ac-3 unsupported again"))
        self.assertEqual("", playback_evidence.unproven_reason(URL, self.path))

    def test_a_proof_for_a_route_with_no_recorded_failure_writes_nothing(self):
        """This file lists failures. A working route does not belong in it."""
        self.assertFalse(self._pass(url=OTHER))
        self.assertEqual("", playback_evidence.unproven_reason(OTHER, self.path))

    def test_an_unrelated_route_is_untouched(self):
        self._fail()
        self.assertEqual("", playback_evidence.unproven_reason(OTHER, self.path))


class BackupBadgeTests(unittest.TestCase):
    """The badge a backup carries has to answer to the same evidence."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = str(Path(self.dir.name) / "ledger.json")
        playback_evidence.record(
            URL,
            "mpegts MediaError: addSourceBuffer audio/mp4;codecs=ac-3",
            sessions=2,
            media_progress_seconds=[0, 0],
            window_seconds=120.0,
            evidence_report="reports/starjalsha-cand-rgkkw.json",
            vantage="github-actions-us",
            path=self.path,
        )
        real = playback_evidence.DEFAULT_PATH
        playback_evidence.DEFAULT_PATH = self.path
        self.addCleanup(setattr, playback_evidence, "DEFAULT_PATH", real)

    def test_the_primary_and_a_backup_read_the_same_ledger(self):
        as_primary = {"url": URL, "verification_status": "verified_global"}
        as_backup = {"url": URL, "verification_status": "verified_global"}
        self.assertEqual(
            merger._playback_aware_badge(as_primary),
            merger._playback_aware_badge(as_backup),
        )
        self.assertEqual(playback_evidence.BADGE, merger._playback_aware_badge(as_backup))

    def test_a_backup_on_a_clean_route_keeps_its_verified_badge(self):
        """The fix must not paint every backup unproven."""
        clean = {"url": OTHER, "verification_status": "verified_global"}
        self.assertEqual("Verified", merger._playback_aware_badge(clean))

    def test_the_reason_travels_with_the_badge(self):
        stream = {"url": URL, "verification_status": "verified_global"}
        merger._playback_aware_badge(stream)
        self.assertTrue(stream["playback_unproven"])
        self.assertIn("ac-3", stream["playback_unproven_reason"])

    def test_the_merger_asks_for_the_backup_badge_through_the_ledger(self):
        """A direct read of the call site, so a revert cannot pass silently."""
        source = Path(ROOT / "scanner" / "merger.py").read_text(encoding="utf-8")
        head, _, tail = source.partition("for index, b_stream in enumerate(")
        self.assertTrue(tail, "the backup construction loop moved")
        block = tail[: tail.index("backups.append(")]
        self.assertIn('"verification_badge": _playback_aware_badge(b_stream)', block)
        self.assertNotIn('"verification_badge": _verification_badge(b_stream)', block)

    def test_the_backup_carries_the_unproven_fields(self):
        source = Path(ROOT / "scanner" / "merger.py").read_text(encoding="utf-8")
        _, _, tail = source.partition("for index, b_stream in enumerate(")
        block = tail[: tail.index("backups.append(")]
        self.assertIn("playback_unproven_reason", block)


if __name__ == "__main__":
    unittest.main()
