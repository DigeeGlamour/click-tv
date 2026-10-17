"""The link-health ledger is a committed file, so it has to have a size.

`state/movie-link-health.json` is written into the repository on every scan,
and GitHub refuses any file over 100 MB at the pre-receive hook. Nothing ever
removed a record from it, so it only grew.

Measured on 2026-10-17. The first movies scan in thirteen days passed the
test suite, ran the scanner, produced a catalogue, passed the output
validator - and was rejected at the last step:

    remote: error: File state/movie-link-health.json is 109.27 MB;
    this exceeds GitHub's file size limit of 100.00 MB
    remote: error: GH001: Large files detected.
    ! [remote rejected]  HEAD -> main (pre-receive hook declined)

    records                        110,879
    catalogue cards                 ~1,900
    bytes per record                   890
    stale_reason = budget          110,878   (every record but one)
    newest timestamp in September  110,878

The driver is the CDN rotation fixed in the same round: `sm-movie-combined`
offers ~38,000 URLs and renames its host roughly weekly, and every rename
mints a fresh `stream_id` for every one of them. Six host generations is a
quarter of a million records for one source, none of which the site can still
reach.

Pruning the real ledger with the live links protected: 110,879 -> 40,000
records, 33.9 MB.

What must never be dropped is a link this run verified or carried. Those are
the records the TTL ladder, the repair queue and ধারা ৪.০'s terminal evidence
read; forgetting one would re-check a link the run already answered, or lose
the 404 that justified retiring a card.
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import movie_link_health as h  # noqa: E402

NOW = datetime(2026, 10, 17, 12, 0, 0, tzinfo=timezone.utc)


def _record(days_ago, **extra):
    stamp = h._iso(NOW - timedelta(days=days_ago))
    record = {"last_verified_at": stamp, "first_seen_at": stamp,
              "last_healthy_at": "", "status": h.STATUS_FAILING}
    record.update(extra)
    return record


def _store(**records):
    return {"schema_version": h.SCHEMA_VERSION, "updated_at": "", "links": dict(records)}


class AStaleRecordIsForgotten(unittest.TestCase):
    def test_older_than_the_window_goes(self):
        store = _store(old=_record(h.FORGET_AFTER_DAYS + 1))
        out = h.prune(store, now=NOW)
        self.assertEqual(out["expired"], 1)
        self.assertEqual(store["links"], {})

    def test_inside_the_window_stays(self):
        store = _store(recent=_record(h.FORGET_AFTER_DAYS - 1))
        h.prune(store, now=NOW)
        self.assertIn("recent", store["links"])

    def test_the_window_is_longer_than_the_longest_ttl(self):
        """A link merely waiting its turn in the weekly sweep is never
        forgotten for waiting."""
        longest = max(h.TTL_HEALTHY_PUBLIC_SECONDS, h.TTL_TRUSTED_PRIVATE_SECONDS)
        self.assertGreater(h.FORGET_AFTER_DAYS * h.DAY, longest * 3)

    def test_a_record_with_no_timestamps_is_droppable(self):
        store = _store(nameless={"status": h.STATUS_FAILING})
        h.prune(store, now=NOW)
        self.assertEqual(store["links"], {})

    def test_the_newest_of_the_three_timestamps_decides(self):
        """Verified long ago but healthy recently is a live record."""
        store = _store(mixed={
            "first_seen_at": h._iso(NOW - timedelta(days=400)),
            "last_verified_at": h._iso(NOW - timedelta(days=400)),
            "last_healthy_at": h._iso(NOW - timedelta(days=1)),
        })
        h.prune(store, now=NOW)
        self.assertIn("mixed", store["links"])


class TheRunsOwnLinksAreNeverDropped(unittest.TestCase):
    def test_age_does_not_remove_a_protected_record(self):
        store = _store(ancient=_record(900))
        out = h.prune(store, keep={"ancient"}, now=NOW)
        self.assertEqual(out["expired"], 0)
        self.assertIn("ancient", store["links"])

    def test_the_cap_does_not_remove_a_protected_record(self):
        records = {f"old{i}": _record(1) for i in range(50)}
        records["live"] = _record(900)
        store = _store(**records)
        h.prune(store, keep={"live"}, now=NOW, limit=10)
        self.assertIn("live", store["links"])
        self.assertEqual(len(store["links"]), 10)

    def test_protecting_more_than_the_limit_keeps_them_all(self):
        """Correctness outranks the cap: the ledger may exceed it rather than
        forget a link the site is serving."""
        records = {f"live{i}": _record(1) for i in range(30)}
        store = _store(**records)
        h.prune(store, keep=set(records), now=NOW, limit=10)
        self.assertEqual(len(store["links"]), 30)

    def test_the_tally_counts_what_survived_under_protection(self):
        store = _store(a=_record(1), b=_record(1))
        out = h.prune(store, keep={"a"}, now=NOW, limit=1)
        self.assertEqual(out["protected"], 1)
        self.assertIn("a", store["links"])


class TheCapTakesTheLeastRecentlyTouchedFirst(unittest.TestCase):
    def test_the_oldest_go_and_the_newest_stay(self):
        store = _store(**{f"r{i}": _record(i + 1) for i in range(10)})
        h.prune(store, now=NOW, limit=3)
        self.assertEqual(sorted(store["links"]), ["r0", "r1", "r2"])

    def test_nothing_happens_when_it_is_already_small(self):
        store = _store(a=_record(1), b=_record(2))
        out = h.prune(store, now=NOW, limit=10)
        self.assertEqual(out["trimmed"], 0)
        self.assertEqual(len(store["links"]), 2)

    def test_the_cap_keeps_the_file_well_under_the_github_limit(self):
        """890 bytes a record was measured on the real ledger."""
        self.assertLess(h.MAX_RECORDS * 890, 60 * 1024 * 1024)


class ItSurvivesRubbish(unittest.TestCase):
    def test_a_store_with_no_links_is_fine(self):
        self.assertEqual(h.prune({}, now=NOW)["after"], 0)

    def test_a_links_value_that_is_not_a_dict_is_fine(self):
        self.assertEqual(h.prune({"links": []}, now=NOW)["after"], 0)

    def test_an_empty_keep_is_fine(self):
        store = _store(a=_record(1))
        h.prune(store, keep=(), now=NOW)
        self.assertIn("a", store["links"])


class TheScanPrunesBeforeItWrites(unittest.TestCase):
    SOURCE = (ROOT / "scanner" / "fast_pipeline.py").read_text(encoding="utf-8")

    def test_prune_is_called(self):
        self.assertIn("health.prune(store, keep=verified_ids | carried_ids)", self.SOURCE)

    def test_it_runs_before_the_save(self):
        prune_at = self.SOURCE.index("health.prune(store")
        save_at = self.SOURCE.index("health.save(store)")
        self.assertLess(prune_at, save_at)

    def test_what_it_protects_is_what_the_run_reached(self):
        """`verified_ids` and `carried_ids` are built a few lines above from
        the run's own results."""
        block = self.SOURCE[:self.SOURCE.index("health.prune(store")]
        self.assertIn("verified_ids = {", block)
        self.assertIn("carried_ids = {", block)

    def test_the_scan_says_what_it_forgot(self):
        self.assertIn("link health ledger pruned:", self.SOURCE)


if __name__ == "__main__":
    unittest.main()
