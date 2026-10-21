"""A handful of lost streams no longer holds back the whole movie catalogue.

On 2026-10-19 twenty-three episodes on a suspended CDN zone - 0.66% of 3,491
previously live streams - kept all 1,970 movies at their 2026-09-24 publish.
Within MAX_HELD_LOSS_SHARE the loss is still a failed check, listed and
reported; it just does not block. Past it, the gate blocks exactly as before.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import movie_coverage  # noqa: E402


def _report(previously_live, lost):
    visible = previously_live - lost
    return {
        "invariant_1": {"total_in_scope_entries": 10, "published_movie": 10,
                        "bucket_sum": 10, "identity_balanced": True},
        "invariant_2": {
            "previously_live": previously_live,
            "currently_visible": visible,
            "unexplained_live_loss": lost,
            "block_reasons": ["%d previously live stream(s) are lost" % lost] if lost else [],
        },
    }


class TheMeasuredCase(unittest.TestCase):
    def test_23_of_3491_publishes_and_is_still_reported(self):
        verdict = movie_coverage.check_invariants(_report(3491, 23))
        self.assertFalse(verdict["block"])
        self.assertIn("INVARIANT_2 unexplained_live_loss == 0", verdict["failures"])
        detail = next(c["detail"] for c in verdict["checks"]
                      if c["name"] == "INVARIANT_2 unexplained_live_loss == 0")
        self.assertIn("lost_streams", detail)


class AMassLossStillBlocks(unittest.TestCase):
    def test_past_the_allowance_the_previous_catalogue_is_kept(self):
        verdict = movie_coverage.check_invariants(_report(3491, 200))
        self.assertTrue(verdict["block"])
        self.assertTrue(any("unexplained live loss: 200" in r
                            for r in verdict["block_reasons"]))

    def test_a_small_catalogue_has_no_free_losses(self):
        """int(10 * 0.02) == 0: one lost stream of ten still blocks."""
        self.assertTrue(movie_coverage.check_invariants(_report(10, 1))["block"])

    def test_no_loss_passes(self):
        verdict = movie_coverage.check_invariants(_report(3491, 0))
        self.assertNotIn("INVARIANT_2 unexplained_live_loss == 0", verdict["failures"])


class TheLostStreamsAreListed(unittest.TestCase):
    def test_the_report_keeps_the_full_list(self):
        source = (ROOT / "scanner" / "movie_coverage.py").read_text(encoding="utf-8")
        self.assertIn('"lost_streams": lost[:500]', source)


if __name__ == "__main__":
    unittest.main()
