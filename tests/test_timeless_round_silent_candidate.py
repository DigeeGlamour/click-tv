"""A stamped card that states a round folds into the one candidate that is silent.

2026-10-21: "ODI Tri-Nation Series in UAE 2026 - 3rd Match - Afghanistan vs
Bangladesh" (time stamped by the scan, round "3rd match", played) and
"Bangladesh Vs Afghanistan" (11:30, no round, no key, did not play) were the
only two cards for the pair and stayed two.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import fixture_dedupe  # noqa: E402


def _card(name, start, stated, url, competition=""):
    return {"name": name, "sport_type": "cricket", "start_time": start, "start_at": start,
            "source_start_time": start if stated else "", "url": url, "competition": competition,
            "status": "LIVE_NOW", "source_ids": [url.split("/")[2]]}


STAMPED = _card("Afghanistan vs Bangladesh", "2026-10-21T16:47:06+00:00", False,
                "https://pv.example/a.mpd", "ODI Tri-Nation Series in UAE 2026 - 3rd Match")
SILENT = _card("Bangladesh Vs Afghanistan", "2026-10-21T11:30:00+00:00", True,
               "https://sm.example/b.mpd", "ODI Tri-Series in United Arab Emirates")


class TheSilentCandidate(unittest.TestCase):
    def test_one_silent_candidate_takes_the_stamped_card(self):
        kept, report = fixture_dedupe.fold([dict(STAMPED), dict(SILENT)])
        self.assertEqual(len(kept), 1)
        self.assertIn("https://pv.example/a.mpd",
                      [b.get("url") for b in kept[0].get("backups") or []])

    def test_a_different_day_is_never_folded(self):
        tomorrow = dict(SILENT, start_time="2026-10-22T11:30:00+00:00",
                        start_at="2026-10-22T11:30:00+00:00",
                        source_start_time="2026-10-22T11:30:00+00:00")
        kept, _ = fixture_dedupe.fold([dict(STAMPED), tomorrow])
        self.assertEqual(len(kept), 2)

    def test_two_silent_candidates_are_a_question(self):
        other = _card("Bangladesh vs Afghanistan Women", "2026-10-21T08:00:00+00:00", True,
                      "https://w.example/d.mpd")
        other["name"] = "Bangladesh vs Afghanistan"
        kept, _ = fixture_dedupe.fold([dict(STAMPED), dict(SILENT), other])
        self.assertGreaterEqual(len(kept), 2)


if __name__ == "__main__":
    unittest.main()
