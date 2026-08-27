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
from scanner import persistence_store as ps  # noqa: E402
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
            # Either form observes the site: model_permits_hide calls
            # audit_hide internally, so a site that uses it is still audited.
            observed = text.count("audit_hide_safe(") + text.count(
                "model_permits_hide("
            )
            self.assertGreaterEqual(
                observed,
                expected,
                f"{path} lost an audit call",
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
        # "audit_only" was true when this file was written and became false
        # when the hide paths were put behind model_permits_hide. The report has
        # to say which it is, so this pins mode to the flag rather than to a
        # literal that can quietly go stale again.
        self.assertEqual(payload["mode"], "conditional_enforcement")
        self.assertTrue(va.ENFORCE_MODEL_DECISION)
        self.assertTrue(payload["enforcement"]["enforced"])
        self.assertIn("REFUSES", payload["enforcement"]["when_enforced"])
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


class HmacReportingTests(unittest.TestCase):
    """The report must say whether a key was present, not leave it ambiguous.

    A bare `hmac_key_id: null` was read as "the secret is not working". It
    actually means the secret was absent from the environment that produced the
    report, which is normal for a local run - the secret lives in CI. The report
    now says which, so the distinction is not left to guesswork.
    """

    def setUp(self):
        va.reset()
        import os  # noqa: PLC0415

        self._os = os
        self._saved = {
            n: os.environ.get(n)
            for n in (rev.HMAC_KEY_ENV, rev.HMAC_KEY_ID_ENV)
        }

    def tearDown(self):
        for name, value in self._saved.items():
            if value is None:
                self._os.environ.pop(name, None)
            else:
                self._os.environ[name] = value

    def test_the_summary_says_whether_a_key_was_configured(self):
        self._os.environ.pop(rev.HMAC_KEY_ENV, None)
        va.audit_hide("unit.test", _item())
        summary = va.summary()
        self.assertIn("hmac_key", summary)
        self.assertFalse(summary["hmac_key"]["configured"])
        self.assertIn("never hide", summary["hmac_key"]["note"])

    def test_a_configured_key_reaches_the_report(self):
        self._os.environ[rev.HMAC_KEY_ENV] = "k" * 32
        self._os.environ[rev.HMAC_KEY_ID_ENV] = "key-test"
        va.audit_hide("unit.test", _item())
        summary = va.summary()
        self.assertTrue(summary["hmac_key"]["configured"])
        self.assertEqual(summary["hmac_key"]["key_id"], "key-test")
        self.assertEqual(summary["decisions"][0]["hmac_key_id"], "key-test")

    def test_a_configured_key_produces_keyed_tenants(self):
        # The whole point of the secret: the tenant field stops being unknown.
        self._os.environ[rev.HMAC_KEY_ENV] = "k" * 32
        domain = rev.failure_domain(
            "https://tenant.example.net/x.m3u8", rev.configured_hmac_key()
        )
        self.assertIsNotNone(domain["failure_domain_tenant"])
        self.assertNotEqual(domain["failure_domain_tenant"], rev.UNKNOWN)


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


class PersistenceIntegrationTests(unittest.TestCase):
    """The store has to be fed by something, or the counter never moves.

    Before this integration nothing wrote observations anywhere, so
    persistence_state could only ever see a single run: the counter was fully
    implemented and permanently stuck at one, and the escalation path it guards
    was unreachable in practice.
    """

    def setUp(self):
        va.reset()
        self._tmp = tempfile.TemporaryDirectory()
        self._original = ps.DEFAULT_STORE_PATH
        ps.DEFAULT_STORE_PATH = str(Path(self._tmp.name) / "store.json")

    def tearDown(self):
        ps.DEFAULT_STORE_PATH = self._original
        self._tmp.cleanup()

    def test_an_audited_decision_reaches_the_store(self):
        va.audit_hide("unit.test", _item(), status=502)
        stored = ps.load(ps.DEFAULT_STORE_PATH)["routes"]
        self.assertEqual(len(stored), 1)

    def test_the_route_id_survives_a_rotating_token(self):
        # A cache-buster must not read as a different route, or every run starts
        # the history over and the counter can never accumulate.
        va.audit_hide("unit.test", _item(url="https://h.example.net/a.m3u8?_t=1"))
        va.audit_hide("unit.test", _item(url="https://h.example.net/a.m3u8?_t=2"))
        self.assertEqual(len(ps.load(ps.DEFAULT_STORE_PATH)["routes"]), 1)

    def test_a_distinct_route_gets_its_own_history(self):
        va.audit_hide("unit.test", _item(url="https://h.example.net/a.m3u8?id=1"))
        va.audit_hide("unit.test", _item(url="https://h.example.net/a.m3u8?id=2"))
        # 22 published channels differ only by "?id=NNN"; fusing them would
        # pool unrelated evidence into one counter.
        self.assertEqual(len(ps.load(ps.DEFAULT_STORE_PATH)["routes"]), 2)

    def test_no_credential_reaches_the_store(self):
        va.audit_hide(
            "unit.test",
            _item(url="https://h.example.net/a.m3u8?token=SECRETVALUE"),
        )
        blob = Path(ps.DEFAULT_STORE_PATH).read_text(encoding="utf-8")
        self.assertNotIn("SECRETVALUE", blob)
        self.assertNotIn("token", blob)

    def test_the_decision_reports_the_cross_run_state(self):
        decision = va.audit_hide("unit.test", _item(), status=502)
        self.assertIn("persistence_state", decision)
        self.assertIn("persistence_counter", decision)

    def test_a_broken_store_cannot_break_the_audit(self):
        Path(ps.DEFAULT_STORE_PATH).write_text("not json", encoding="utf-8")
        decision = va.audit_hide("unit.test", _item(), status=502)
        self.assertFalse(decision["model_would_hide"])

    def test_recording_does_not_change_the_item(self):
        item = _item()
        before = copy.deepcopy(item)
        va.audit_hide("unit.test", item, status=502)
        self.assertEqual(item, before)


if __name__ == "__main__":
    unittest.main()
