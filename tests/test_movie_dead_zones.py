"""A CDN zone its provider switched off is terminal evidence; nothing else is.

On 2026-10-21 four suspended BunnyCDN zones held 938 previously live streams,
and the no-loss gate counted every one as unexplained loss.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import movie_coverage  # noqa: E402
from scanner import movie_dead_zones as zones  # noqa: E402

GONE_PAGE = ('<html><head><link rel="stylesheet" href="https://bunnycdn.b-cdn.net/'
             'assets/landingpage/css/unconfigured.css"><title>BunnyCDN</title>')
DEAD = "https://jrtyh.b-cdn.net/s3/upload/videos/2026/08/Sultan.S01E21.720P.mkv"
LINE = "movie|mix|sultan-s01e21|" + DEAD


def _fetch(answers):
    calls = []

    def fetch(host):
        calls.append(host)
        return answers[host]
    fetch.calls = calls
    return fetch


class OnlyTheProviderSZoneGonePageCounts(unittest.TestCase):
    def test_the_zone_gone_page_is_terminal(self):
        self.assertTrue(zones.zone_gone("jrtyh.b-cdn.net",
                                        _fetch({"jrtyh.b-cdn.net": (403, GONE_PAGE)})))

    def test_a_plain_403_is_not(self):
        """ধারা ৪.১: a 403 is usually our vantage, never terminal by itself."""
        self.assertFalse(zones.zone_gone("jrtyh.b-cdn.net",
                                         _fetch({"jrtyh.b-cdn.net": (403, "Forbidden")})))

    def test_a_working_zone_is_not(self):
        self.assertFalse(zones.zone_gone("ok.b-cdn.net",
                                         _fetch({"ok.b-cdn.net": (200, GONE_PAGE)})))

    def test_another_domain_is_never_asked(self):
        fetch = _fetch({})
        self.assertFalse(zones.zone_gone("pub-1.r2.dev", fetch))
        self.assertEqual(fetch.calls, [])

    def test_no_answer_is_no_opinion(self):
        def boom(_host):
            raise OSError("timeout")
        self.assertFalse(zones.zone_gone("jrtyh.b-cdn.net", boom))


class TheGateCountsThemAsExplained(unittest.TestCase):
    def test_one_request_per_zone_and_a_hard_list_reason(self):
        fetch = _fetch({"jrtyh.b-cdn.net": (403, GONE_PAGE)})
        records, summary = zones.terminal_records(
            [LINE, LINE.replace("E21", "E22")], fetch)
        self.assertEqual(fetch.calls, ["jrtyh.b-cdn.net"])
        self.assertEqual(len(records), 2)
        self.assertEqual(summary["zones_gone"], ["jrtyh.b-cdn.net"])
        for record in records:
            self.assertIn(record["reason"], movie_coverage.DEFINITIVE_REJECT_REASONS)

    def test_a_live_zone_yields_nothing(self):
        records, _ = zones.terminal_records(
            [LINE], _fetch({"jrtyh.b-cdn.net": (200, "")}))
        self.assertEqual(records, [])

    def test_the_scan_hands_them_to_the_gate(self):
        source = (ROOT / "scan.py").read_text(encoding="utf-8")
        self.assertIn("movie_dead_zones.terminal_records(previous_inventory)", source)
        self.assertIn("previous_inventory=previous_inventory,\n            ),", source.replace("\r\n", "\n"))


if __name__ == "__main__":
    unittest.main()
