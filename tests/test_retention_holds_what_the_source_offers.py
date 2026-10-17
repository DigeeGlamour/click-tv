"""The grace countdown may not delete a card the source is still offering.

`sm-movie-combined` renames its CDN host roughly weekly. A published card
therefore sits on a hostname the feed no longer uses while the feed serves
the same file from the new one, so no scan ever "finds" that card, the grace
runs out, and retention drops it.

That alone was survivable. What made it a deadlock is that
`movie_source_removal` - correctly - refuses to call a file the feed is
still listing `source_removed_with_no_alternative`, which is one of the four
DEFINITIVE reject reasons. Measured on the 2026-10-17 11:00 movies run:

    source removals: 49 of 799 past the retention grace with no alternative
      kept: 456 a_source_offers_the_same_file_on_another_host
            151 card_still_published
              1 stream_still_published
            142 within_retention_grace

Those 456 cards left the catalogue with no terminal explanation, and the
no-loss gate blocked the publish over the streams that went with them:

    06:12  unexplained live loss  635
    07:06  unexplained live loss  393
    09:30  unexplained live loss  119
    11:00  unexplained live loss  102

So the two halves now answer the same way: if a source is still listing the
file, the card is held rather than deleted, and the countdown is not what
decides. A file that is listed but genuinely gone is still removed, by the
other road - a confirmed 404 or 410 in the link-health ledger is terminal
evidence in its own right and does not wait for this countdown.

The comparison is the weaker one the gate knows, by file path rather than
host+path, and it is used for one direction only: it can keep a card on the
site, never remove one.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import movie_retention as mr  # noqa: E402

PATH = "/s3/upload/videos/2026/03/[Fibwatch.Com]Ali.2025.1080p.mkv"
RETIRED = f"https://jrtyh.b-cdn.net{PATH}"
GONE = {"id": "ali-2025", "name": "Ali", "url": RETIRED}


def _fixture(misses):
    """A category whose other nine cards came back and whose tenth did not."""
    root = tempfile.mkdtemp()
    os.makedirs(os.path.join(root, "bangla"))
    others = [{"id": f"k{i}", "name": f"K{i}", "url": f"https://h/{i}.mkv"}
              for i in range(9)]
    with open(os.path.join(root, "bangla", "page-001.json"), "w",
              encoding="utf-8") as handle:
        json.dump({"items": [GONE] + others}, handle)
    store = os.path.join(root, "retention.json")
    with open(store, "w", encoding="utf-8") as handle:
        json.dump({"absent": {"ali-2025": {"misses": misses,
                                           "complete_misses": misses}}}, handle)
    return root, store, others


def _retain(misses, source_paths):
    root, store, others = _fixture(misses)
    kept, summary = mr.retain(others, "bangla", root=root, path=store,
                              persist=False, source_paths=source_paths)
    return summary, {card["id"] for card in kept}


class ACardTheSourceStillListsIsHeld(unittest.TestCase):
    def test_it_is_not_dropped_when_the_grace_runs_out(self):
        summary, ids = _retain(mr.GRACE_SCANS, {PATH})
        self.assertEqual(summary.get("dropped_after_grace"), 0)
        self.assertIn("ali-2025", ids)

    def test_the_hold_is_counted_so_the_scan_can_say_so(self):
        summary, _ids = _retain(mr.GRACE_SCANS, {PATH})
        self.assertEqual(summary.get("held_source_still_offers"), 1)

    def test_the_card_says_why_it_is_still_here(self):
        root, store, others = _fixture(mr.GRACE_SCANS)
        kept, _summary = mr.retain(others, "bangla", root=root, path=store,
                                   persist=False, source_paths={PATH})
        card = next(c for c in kept if c["id"] == "ali-2025")
        self.assertIs(card["retained_while_source_offers_it"], True)
        self.assertIn("different hostname", card["retention_note"])

    def test_it_is_still_marked_as_carried_not_as_fresh(self):
        root, store, others = _fixture(mr.GRACE_SCANS)
        kept, _summary = mr.retain(others, "bangla", root=root, path=store,
                                   persist=False, source_paths={PATH})
        card = next(c for c in kept if c["id"] == "ali-2025")
        self.assertIs(card["retained_after_failed_scan"], True)
        self.assertEqual(card["verification_status"], mr.RETAINED_STATUS)


class ACardNobodyOffersStillGoes(unittest.TestCase):
    def test_no_source_paths_at_all_keeps_the_old_behaviour(self):
        summary, ids = _retain(mr.GRACE_SCANS, None)
        self.assertEqual(summary.get("dropped_after_grace"), 1)
        self.assertNotIn("ali-2025", ids)

    def test_a_different_file_being_offered_does_not_save_it(self):
        summary, ids = _retain(mr.GRACE_SCANS, {"/s3/upload/videos/Other.mkv"})
        self.assertEqual(summary.get("dropped_after_grace"), 1)
        self.assertNotIn("ali-2025", ids)

    def test_inside_the_grace_it_is_carried_as_before(self):
        summary, ids = _retain(0, None)
        self.assertEqual(summary.get("dropped_after_grace"), 0)
        self.assertIn("ali-2025", ids)
        self.assertEqual(summary.get("held_source_still_offers", 0), 0)


class TheComparisonIsTheWeakOneAndOnlyProtects(unittest.TestCase):
    def test_it_matches_the_same_file_on_another_host(self):
        self.assertTrue(mr._source_still_offers(GONE, {PATH}))

    def test_a_generic_path_can_never_protect_a_card(self):
        card = {"id": "x", "url": "https://a.example/live"}
        self.assertFalse(mr._source_still_offers(card, {"/live"}))

    def test_an_empty_offer_set_protects_nothing(self):
        self.assertFalse(mr._source_still_offers(GONE, set()))
        self.assertFalse(mr._source_still_offers(GONE, None))

    def test_a_backup_counts_too(self):
        card = {"id": "x", "url": "https://dead.example/a.mkv",
                "backups": [{"url": RETIRED}]}
        self.assertTrue(mr._source_still_offers(card, {PATH}))


class TheScanHandsItTheFeedsPaths(unittest.TestCase):
    SOURCE = (ROOT / "scanner" / "movies.py").read_text(encoding="utf-8")

    def test_retention_is_given_the_offers_this_run_collected(self):
        self.assertIn("source_paths=set(_SOURCE_OFFERS),", self.SOURCE)

    def test_the_hold_is_reported_in_the_scan_log(self):
        self.assertIn("held past the grace", self.SOURCE)

    def test_the_offers_are_the_same_index_the_rotation_uses(self):
        """One index, so the two cannot disagree about the same file."""
        self.assertIn("_SOURCE_OFFERS.update(offers)", self.SOURCE)


if __name__ == "__main__":
    unittest.main()
