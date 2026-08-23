"""The audit must observe hide paths without ever changing one.

Phase 2 is visibility-invariant by design: the model is connected to every hide
site, computes what it would decide, and changes nothing. If that invariant ever
breaks, a library that was written to stop channels disappearing would start
making them disappear, so it is pinned here rather than trusted.
"""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import route_evidence as rev  # noqa: E402
from scanner import visibility_audit as va  # noqa: E402


def _item(**over):
    base = {
        "name": "Sample Channel",
        "url": "https://tenant-a.example.net/live/index.m3u8",
        "backups": [{"url": "https://tenant-b.example.org/live/index.m3u8"}],
        "verification_status": "failed_player_twice",
    }
    base.update(over)
    return base


class AuditIsSideEffectFreeTests(unittest.TestCase):
    def setUp(self):
        va.reset()

    def test_the_audited_item_is_not_modified(self):
        item = _item()
        before = copy.deepcopy(item)
        va.audit_hide("unit.test", item, kind="channel", reason="failed_player_twice")
        self.assertEqual(item, before)

    def test_the_audit_never_sets_publish_allowed(self):
        item = _item()
        va.audit_hide("unit.test", item, kind="channel")
        self.assertNotIn("publish_allowed", item)

    def test_a_broken_item_cannot_raise_through_the_safe_wrapper(self):
        # Anything that reaches a hide path must be survivable: an auditing
        # error that propagated would fail a scan mid-run.
        for bad in (None, {}, {"url": None}, {"backups": "not-a-list"}, 42):
            va.audit_hide_safe("unit.test", bad)  # must not raise

    def test_every_wired_hide_site_still_calls_the_audit(self):
        # A future edit that removes a call site would silently blind the audit.
        sites = {
            "scanner/player_compatibility.py": 2,
            "scanner/browser_reachability.py": 2,
            "scanner/bd_verifier.py": 5,
            "scanner/verifier.py": 3,
            "scanner/fast_pipeline.py": 3,
        }
        for path, expected in sites.items():
            text = (ROOT / path).read_text(encoding="utf-8")
            # The import line has no parenthesis, so the call count is exact.
            self.assertEqual(
                text.count("audit_hide_safe("),
                expected,
                f"{path} lost or gained an audit call",
            )


class AuditContentTests(unittest.TestCase):
    def setUp(self):
        va.reset()

    def test_the_model_keeps_a_visible_channel_on_bare_ledger_evidence(self):
        decision = va.audit_hide(
            "unit.test", _item(), kind="channel", reason="failed_player_twice"
        )
        self.assertEqual(decision["three_state"], rev.EXISTING_VISIBLE)
        self.assertFalse(decision["model_would_hide"])

    def test_an_already_hidden_item_is_reported_as_legacy(self):
        decision = va.audit_hide(
            "unit.test", _item(publish_allowed=False), kind="channel"
        )
        self.assertEqual(decision["three_state"], rev.LEGACY_HIDDEN)

    def test_a_vantage_status_is_reported_as_non_escalatable(self):
        decision = va.audit_hide("unit.test", _item(), status=403)
        self.assertEqual(decision["transport_class"], rev.ADVISORY_VANTAGE_BLOCKED)
        self.assertFalse(decision["escalatable"])

    def test_an_undeterminable_tenant_leaves_correlation_unknown(self):
        decision = va.audit_hide(
            "unit.test",
            _item(backups=[{"url": "https://cache.devm3u.top/live/x.m3u8"}]),
        )
        self.assertEqual(decision["correlation"], rev.UNKNOWN)
        self.assertEqual(decision["independent_redundancy"], rev.UNKNOWN)

    def test_a_written_audit_carries_the_locks_and_its_provenance(self):
        va.audit_hide("unit.test", _item())
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "audit.json")
            written = va.flush(target, provenance="unit test")
            self.assertEqual(written, target)
            payload = json.loads(Path(target).read_text(encoding="utf-8"))
        self.assertEqual(payload["mode"], "audit_only")
        self.assertEqual(payload["provenance"], "unit test")
        self.assertTrue(payload["locks"]["declared"])
        self.assertEqual(
            payload["locks"]["target_matrix"], list(rev.DECLARED_TARGET_MATRIX)
        )

    def test_an_empty_ledger_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "audit.json")
            self.assertIsNone(va.flush(target))
            self.assertFalse(Path(target).exists())

    def test_a_committed_audit_carries_no_credential(self):
        va.audit_hide(
            "unit.test",
            _item(url="https://host.example.net/live/index.m3u8?token=SECRETVALUE"),
        )
        payload = va.summary()
        self.assertFalse(rev.evidence_contains_forbidden_material(payload))


class AuditDriverTests(unittest.TestCase):
    """The committed audit must reflect the scanner's real gating."""

    def test_the_driver_exists_and_states_its_gating(self):
        driver = ROOT / "scripts" / "visibility-model-audit.py"
        self.assertTrue(driver.exists())
        text = driver.read_text(encoding="utf-8")
        # The Bangla-only gate is the difference between "748 at risk" and
        # "21 at risk"; the driver must not quietly drop it.
        self.assertIn("Bangla", text)
        self.assertIn("deep copies", text)

    def test_the_driver_never_writes_outside_reports(self):
        text = (ROOT / "scripts" / "visibility-model-audit.py").read_text(
            encoding="utf-8"
        )
        for line in text.splitlines():
            if 'open(' in line and '"w"' in line:
                self.fail(f"driver opens a file for writing directly: {line.strip()}")


class AuditWithholdingTests(unittest.TestCase):
    """A bad row must cost that row, not the whole report."""

    def setUp(self):
        va.reset()

    def _write(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "audit.json")
            va.flush(target, provenance="unit test")
            return json.loads(Path(target).read_text(encoding="utf-8"))

    def test_a_clean_row_survives_a_dirty_one(self):
        va.audit_hide("unit.test", _item(name="Clean Channel"))
        va.audit_hide(
            "unit.test",
            _item(
                name="https://h.example.net/live/"
                "abc123DEF456ghi789JKL012mno345PQR678stu901vwx/i.m3u8"
            ),
        )
        payload = self._write()
        self.assertIsNone(payload.get("error"))
        self.assertEqual(
            [row["name"] for row in payload["decisions"]], ["Clean Channel"]
        )
        self.assertEqual(payload["rows_withheld_for_forbidden_material"], 1)

    def test_the_withheld_row_is_not_echoed_back(self):
        va.audit_hide(
            "unit.test",
            _item(
                name="https://h.example.net/live/"
                "abc123DEF456ghi789JKL012mno345PQR678stu901vwx/i.m3u8"
            ),
        )
        payload = self._write()
        # Naming the offending row would put the credential straight back into
        # the file the check exists to protect.
        self.assertNotIn("rows_withheld_names", payload)
        self.assertFalse(rev.evidence_contains_forbidden_material(payload))

    def test_a_fully_clean_run_is_written_untouched(self):
        va.audit_hide("unit.test", _item(name="Clean Channel"))
        payload = self._write()
        self.assertIsNone(payload.get("error"))
        self.assertNotIn("rows_withheld_for_forbidden_material", payload)
        self.assertEqual(len(payload["decisions"]), 1)


if __name__ == "__main__":
    unittest.main()
