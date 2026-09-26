"""A card that holds a working link must not be published as unplayable.

`_absorb_carried_card` exists so that reconciling two cards for one fixture
loses nothing: the carried card's proven-playable primary is put at the front of
the surviving card's backups. It then wrote `available_link_count = 1 + len(
backups)` unconditionally - and when the surviving card was metadata-only, that
1 was a primary that does not exist.

Measured on 2026-09-26, and it stopped every scan for hours:

    Girona FC vs Albacete
      metadata_only        true
      url                  ""
      backups              1, verified_global, playback record active
      available_link_count 1          <- the contradiction

Two things were wrong and only one of them was arithmetic. A fixture with a
working route was being published as unwatchable. The guarantee this routine
states - "the canonical primary is not touched" - is about not demoting a
WORKING primary; there was none, so the arrival becomes it.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import live_protection as lp  # noqa: E402


def _stream(playback_id="ctv_a", **extra):
    stream = {
        "playback_id": playback_id,
        "stream_type": "hls",
        "verification_status": "verified_global",
        "verified": True,
        "url": "https://cdn.test/live/a.m3u8",
    }
    stream.update(extra)
    return stream


class APromotedBackupBecomesTheCardsRoute(unittest.TestCase):
    def _card(self, **extra):
        card = {"id": "girona-vs-albacete", "name": "Girona FC vs Albacete",
                "metadata_only": True, "url": "", "backups": []}
        card.update(extra)
        return card

    def test_a_metadata_only_card_stops_being_metadata_only(self):
        card = self._card()
        lp._promote_backup_to_primary(card, [_stream()])
        self.assertFalse(card["metadata_only"])

    def test_it_takes_the_route(self):
        card = self._card()
        lp._promote_backup_to_primary(card, [_stream()])
        self.assertEqual(card["playback_id"], "ctv_a")
        self.assertEqual(card["url"], "https://cdn.test/live/a.m3u8")

    def test_it_takes_how_the_route_is_fetched(self):
        card = self._card()
        lp._promote_backup_to_primary(card, [_stream(
            header_profile="android_tv", proxy_mode="direct_first",
            requires_headers=True)])
        self.assertEqual(card["header_profile"], "android_tv")
        self.assertEqual(card["proxy_mode"], "direct_first")
        self.assertTrue(card["requires_headers"])

    def test_it_takes_how_the_route_was_verified(self):
        card = self._card()
        lp._promote_backup_to_primary(card, [_stream()])
        self.assertEqual(card["verification_status"], "verified_global")
        self.assertTrue(card["verified"])

    def test_the_promotion_is_recorded_on_the_card(self):
        card = self._card()
        lp._promote_backup_to_primary(card, [_stream()])
        self.assertTrue(card["promoted_from_absorbed_backup"])

    def test_the_remaining_backups_keep_their_order_and_are_renumbered(self):
        card = self._card()
        lp._promote_backup_to_primary(card, [
            _stream("ctv_a", name="Backup-1"),
            _stream("ctv_b", name="Backup-2"),
            _stream("ctv_c", name="Backup-3"),
        ])
        self.assertEqual([b["playback_id"] for b in card["backups"]],
                         ["ctv_b", "ctv_c"])
        self.assertEqual([b["name"] for b in card["backups"]],
                         ["Backup-1", "Backup-2"])

    def test_a_field_the_backup_lacks_is_left_alone(self):
        card = self._card(header_profile="kept")
        lp._promote_backup_to_primary(card, [_stream()])
        self.assertEqual(card["header_profile"], "kept")

    def test_an_empty_value_does_not_overwrite_a_real_one(self):
        card = self._card(stream_type="hls")
        lp._promote_backup_to_primary(card, [_stream(stream_type="")])
        self.assertEqual(card["stream_type"], "hls")


class TheCountCountsWhatIsThere(unittest.TestCase):
    SOURCE = (ROOT / "scanner" / "live_protection.py").read_text(encoding="utf-8")

    def test_the_absorb_no_longer_assumes_a_primary(self):
        body = self.SOURCE[self.SOURCE.index("def _absorb_carried_card("):]
        body = body[:body.index("\ndef ", 10)]
        self.assertNotIn('canonical["available_link_count"] = 1 + len(merged)', body)
        self.assertIn('0 if canonical.get("metadata_only") is True else 1', body)

    def test_a_working_primary_is_still_not_demoted(self):
        """The promotion is only ever reached when there is nothing to demote."""
        body = self.SOURCE[self.SOURCE.index("def _absorb_carried_card("):]
        body = body[:body.index("\ndef ", 10)]
        guard = body.index('if canonical.get("metadata_only") is True and merged:')
        call = body.index("_promote_backup_to_primary(")
        self.assertLess(guard, call)


if __name__ == "__main__":
    unittest.main()
