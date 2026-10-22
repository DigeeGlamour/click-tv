"""scripts/renew-proven-routes.py: renewal needs a fresh pass, never a claim."""
from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location("renew", ROOT / "scripts" / "renew-proven-routes.py")
RENEW = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(RENEW)
SOURCE = (ROOT / "scripts" / "renew-proven-routes.py").read_text(encoding="utf-8")


class Renewal(unittest.TestCase):
    def test_it_measures_with_the_same_two_session_acceptance(self):
        self.assertIn('"--sessions", "2"', SOURCE)
        self.assertIn("sustained-playback-check.py", SOURCE)

    def test_only_a_route_that_passed_again_is_recorded(self):
        self.assertIn("evidence = proven.get(label)", SOURCE)
        self.assertIn("did not pass twice again", SOURCE)

    def test_the_committed_evidence_is_redacted(self):
        report = {"results": [{"name": "X", "url": "https://s3.itcnbd.live/server-4/stream/"
                               "aHR0cDovLzE3Mi4xOS4xNy4yMzA6ODA5MC9obHMvU3RhckphbHNoYUhELm0zdTg.m3u8",
                               "url_public_template": "{id}.itcnbd.live/server-4/stream/"
                               "aHR0cDovLzE3Mi4xOS4xNy4yMzA6ODA5MC9obHMvU3RhckphbHNoYUhELm0zdTg.m3u8"}]}
        clean = RENEW._redacted(report)
        self.assertFalse(RENEW.rev.evidence_contains_forbidden_material(clean))
        self.assertTrue(clean["results"][0]["url_public_template"].endswith("{seg}.m3u8"))

    def test_a_route_no_longer_on_the_card_is_not_renewed(self):
        self.assertIsNone(RENEW._card_route("No Such Channel", "x"))


if __name__ == "__main__":
    unittest.main()


class RenewalRunsOnItsOwnTests(unittest.TestCase):
    """Nobody has to remember: the workflow renews on a schedule."""

    def test_scheduled_workflow_runs_the_renewal(self):
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            ".github", "workflows", "route-renewal.yml")
        text = open(path, encoding="utf-8").read()
        self.assertIn("schedule:", text)
        self.assertIn("scripts/renew-proven-routes.py", text)
        self.assertIn("--vantage github-actions", text)
        # Renewal comes only from a fresh pass, never from age alone.
        self.assertNotIn("--dry-run", text)
