"""The scan feeds the model, so conditional enforcement is not inert.

The evidence pipeline existed with tests and nothing called it. So enforcement,
which only engages when evidence is present, never engaged during a real scan -
and the report claimed "no code change will be needed", which was wrong. This is
that integration, and these tests exist so it cannot quietly disappear again.

The honest limit is pinned here too: a scan observes from ONE vantage, so a record
built from it can never on its own satisfy the two-independent-vantage
requirement. It makes the model better informed without making it more willing to
hide anything.
"""
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import fast_pipeline as fp  # noqa: E402
from scanner import route_evidence as rev  # noqa: E402
from scanner import visibility_audit as va  # noqa: E402


class WiringTests(unittest.TestCase):
    def test_the_pipeline_is_called_from_the_scan(self):
        source = (ROOT / "scanner" / "fast_pipeline.py").read_text(encoding="utf-8")
        self.assertIn("_supply_scan_evidence", source)
        self.assertIn("route_evidence_pipeline", source)

    def test_evidence_is_supplied_before_any_hide_path_runs(self):
        # Supplying it afterwards would leave the hide paths deciding without it.
        source = (ROOT / "scanner" / "fast_pipeline.py").read_text(encoding="utf-8")
        supply = source.index("supplied = _supply_scan_evidence(")
        first_hide = source.index("player_failure_hidden += mark_confirmed_player_failures(")
        self.assertLess(supply, first_hide)


class SupplyTests(unittest.TestCase):
    def setUp(self):
        va.clear_evidence()
        self._saved = os.environ.get(rev.HMAC_KEY_ENV)

    def tearDown(self):
        va.clear_evidence()
        if self._saved is None:
            os.environ.pop(rev.HMAC_KEY_ENV, None)
        else:
            os.environ[rev.HMAC_KEY_ENV] = self._saved

    def test_no_key_supplies_nothing_rather_than_junk(self):
        # Without a key every record is incomplete and would be discarded, so
        # building 700 of them to throw away is worse than saying no.
        os.environ.pop(rev.HMAC_KEY_ENV, None)
        supplied = fp._supply_scan_evidence(
            [{"name": "A", "url": "https://a.example.net/x.m3u8", "http_status": 503}]
        )
        self.assertEqual(supplied, 0)

    def test_a_scan_observation_becomes_a_record(self):
        os.environ[rev.HMAC_KEY_ENV] = "k" * 32
        supplied = fp._supply_scan_evidence(
            [{"name": "A", "url": "https://tenant.example.net/x.m3u8",
              "http_status": 503}]
        )
        self.assertGreater(supplied, 0)

    def test_items_with_nothing_observed_are_skipped(self):
        os.environ[rev.HMAC_KEY_ENV] = "k" * 32
        supplied = fp._supply_scan_evidence(
            [{"name": "A", "url": "https://tenant.example.net/x.m3u8"}]
        )
        self.assertEqual(supplied, 0)

    def test_items_without_a_url_are_skipped(self):
        os.environ[rev.HMAC_KEY_ENV] = "k" * 32
        self.assertEqual(
            fp._supply_scan_evidence([{"name": "A", "http_status": 503}]), 0
        )

    def test_broken_items_cannot_break_the_scan(self):
        os.environ[rev.HMAC_KEY_ENV] = "k" * 32
        for bad in (None, {}, {"url": None}, 42, {"url": "https://a/x", "http_status": object()}):
            fp._supply_scan_evidence([bad])  # must not raise

    def test_one_vantage_alone_still_cannot_hide_anything(self):
        """The honest limit, asserted rather than described.

        A scan sees from one egress. Even with its evidence loaded, the model must
        refuse to hide, because `may_hide` wants two measurably independent
        vantages and a scan supplies one.
        """
        os.environ[rev.HMAC_KEY_ENV] = "k" * 32
        item = {"name": "A", "url": "https://tenant.example.net/x.m3u8",
                "http_status": 503}
        self.assertGreater(fp._supply_scan_evidence([item]), 0)
        allowed, why = va.model_permits_hide("unit.test", item)
        self.assertFalse(allowed, why)


if __name__ == "__main__":
    unittest.main()
