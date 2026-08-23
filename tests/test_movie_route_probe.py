"""The movie reachability probe, and the claim it must never make.

This probe answered in three minutes what the browser sweep would have taken
13.6 hours to say: 211 of 215 movie routes return HTTP 403 from this vantage, all
of them on a single host. That is one host blocking this egress, not 211 dead
movies, and the model's own rules make advisory:vantage_blocked non-escalatable -
so none of those records can be hidden on this evidence.

The danger with a cheap probe is that its output starts getting read as proof of
playback. That is the exact mistake this whole project exists to correct, so the
tests below pin the probe's limits rather than its speed.
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import route_evidence as rev  # noqa: E402

SCRIPT = ROOT / "scripts" / "movie-route-probe.py"
REPORT = ROOT / "reports" / "movie-route-probe.json"


class ProbeContractTests(unittest.TestCase):
    def setUp(self):
        self.source = SCRIPT.read_text(encoding="utf-8")

    def test_the_probe_classifies_through_the_shared_model(self):
        # Its own opinion about a status would drift from the rest of the system.
        self.assertIn("rev.classify_transport", self.source)

    def test_the_probe_cannot_hide_or_promote(self):
        for forbidden in ("publish_allowed", "may_hide", "mark_unproven", "mark_confirmed"):
            self.assertNotIn(forbidden, self.source, f"probe references {forbidden}")

    def test_the_probe_records_only_a_redacted_url(self):
        self.assertIn("redact_public_template", self.source)

    def test_the_probe_states_that_200_is_not_playback(self):
        # The note is load-bearing: a reachability number read as proof of
        # playback is how HTTP 200 came to stand in for a working stream.
        self.assertIn("NOT a working movie", self.source)


class ProbeResultTests(unittest.TestCase):
    """What the committed probe report is allowed to conclude."""

    @classmethod
    def setUpClass(cls):
        if not REPORT.exists():
            raise unittest.SkipTest("probe report not present")
        cls.payload = json.loads(REPORT.read_text(encoding="utf-8"))

    def test_a_403_is_never_escalatable(self):
        for row in self.payload["results"]:
            if row["status"] == 403:
                self.assertEqual(
                    row["transport_class"], rev.ADVISORY_VANTAGE_BLOCKED, row["name"]
                )
                self.assertFalse(row["escalatable"], row["name"])

    def test_the_bulk_of_the_set_is_not_escalatable(self):
        # 211 of 215 blocked from one egress must not become 211 route faults.
        escalatable = sum(1 for r in self.payload["results"] if r["escalatable"])
        self.assertLess(escalatable, len(self.payload["results"]) // 10)

    def test_no_row_carries_a_credential(self):
        self.assertFalse(
            rev.evidence_contains_forbidden_material(self.payload)
        )

    def test_the_report_says_what_it_cannot_prove(self):
        self.assertIn("NOT a working movie", self.payload["note"])

    def test_cors_absence_is_recorded_as_a_structural_fact(self):
        # 0/215 permit a direct browser fetch, which is IP-independent and so
        # usable for ranking and player config - but never for hiding.
        self.assertIn("cors_permits_browser_direct", self.payload)
        self.assertIsInstance(self.payload["cors_permits_browser_direct"], int)


if __name__ == "__main__":
    unittest.main()
