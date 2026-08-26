"""Every hide site respects the model, not just some of them.

Codex found that bd_verifier.py's five sites and verifier.py's three called
audit_hide_safe (record-only) and never model_permits_hide (enforce). So a
channel with real two-vantage evidence protecting it from
player_compatibility.py could still be hidden by bd_verifier reaching a
different verdict about the same channel with no gate at all - the model's
protection depended on which code path happened to hide the item first.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class CoverageTests(unittest.TestCase):
    #: Every file with at least one production hide path, and how many sites
    #: it has. Recorded explicitly so a new hide site added later without a
    #: model_permits_hide call fails this test rather than passing silently.
    EXPECTED_ENFORCED_SITES = {
        "scanner/player_compatibility.py": 2,
        "scanner/browser_reachability.py": 2,
        "scanner/fast_pipeline.py": 3,
        "scanner/bd_verifier.py": 5,
        "scanner/verifier.py": 3,
    }

    def test_every_listed_file_enforces_at_least_its_recorded_count(self):
        for relative, minimum in self.EXPECTED_ENFORCED_SITES.items():
            text = (ROOT / relative).read_text(encoding="utf-8")
            count = text.count("model_permits_hide(")
            self.assertGreaterEqual(
                count, minimum, f"{relative} has {count} enforced site(s), "
                f"expected at least {minimum}"
            )

    def test_no_production_hide_site_uses_audit_only_recording(self):
        """audit_hide_safe should now only remain for genuinely non-visibility
        call sites, if any - the visibility-changing ones must all enforce."""
        for relative in self.EXPECTED_ENFORCED_SITES:
            text = (ROOT / relative).read_text(encoding="utf-8")
            self.assertNotIn(
                "audit_hide_safe(", text,
                f"{relative} still has an audit-only (non-enforcing) hide call",
            )


if __name__ == "__main__":
    unittest.main()
