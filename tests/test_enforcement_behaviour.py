"""What the evidence model actually decides, measured rather than counted.

tests/test_enforcement_coverage.py counts call sites. A count cannot tell you
whether the gate can refuse - the previous round proved that by passing while
every gate ran before any evidence existed and so returned "caller decision
stands". This file drives the decision function through each case the rule
distinguishes, and then reads the committed production cache to show what the
model does with real records rather than constructed ones.

The window/vantage matrix is the part worth being explicit about. "Two
observations" is not the rule; the rule is two observations that are
independent in BOTH dimensions the rule names - measurably different vantages
AND separate time windows - because two readings from one egress in one moment
are one measurement written down twice.
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import route_evidence as rev  # noqa: E402
from scanner import visibility_audit as va  # noqa: E402

CACHE = ROOT / "state" / "route-evidence-cache.json"

SEPARATION = 300.0  # comfortably past the locked minimum separation


def _record(vantage="scanner_egress", asn="AS8075", provider="microsoft",
            when="2026-08-26T10:00:00+00:00", verdict=rev.PLAYBACK_FAIL,
            scope="global"):
    """A complete, escalatable, globally scoped observation."""
    return {
        "route_id": "host.example.net/live/index.m3u8",
        "url_public_template": "{id}.example.net/live/{file}",
        "url_registrable_domain": "example.net",
        "final_origin_public_template": "{id}.example.net/live/{file}",
        "final_origin_registrable_domain": "example.net",
        "failure_domain_provider": "example.net",
        "failure_domain_tenant": "0123456789abcdef0123456789abcdef",
        "delivery_path": "direct",
        "browser_profile": "desktop_chrome",
        "media_fingerprint": {"measured": True, "video_tracks": 1},
        "playback_metrics": {
            "startup_seconds": 2.0,
            "media_progress_seconds": 0.0,
            "cumulative_stall_seconds": 0.0,
            "fatal_errors": ["media element error code 3"],
        },
        "observed_at": when,
        "ttl": 1800.0,
        "verdict": verdict,
        "verdict_scope": scope,
        "test_vantage": {"id": vantage, "asn": asn, "provider": provider},
        "hmac_key_id": "test-key",
    }


class VantageAndWindowMatrixTests(unittest.TestCase):
    """Each cell of the rule, one test each."""

    def test_no_evidence_leaves_the_caller_in_charge(self):
        allowed, why = va.model_permits_hide("unit.test", {"url": "https://x/y.m3u8"})
        self.assertTrue(allowed)
        self.assertIn("caller decision stands", why)

    def test_one_observation_alone_cannot_hide(self):
        allowed, why = rev.may_hide(state="visible", evidence=[_record()])
        self.assertFalse(allowed)
        self.assertIn("fewer than two", why)

    def test_two_observations_from_one_vantage_cannot_hide(self):
        """Same egress, separate windows. Still one point of view."""
        evidence = [
            _record(when="2026-08-26T10:00:00+00:00"),
            _record(when="2026-08-26T10:10:00+00:00"),
        ]
        allowed, why = rev.may_hide(state="visible", evidence=evidence)
        self.assertFalse(allowed, why)

    def test_two_vantages_in_one_window_cannot_hide(self):
        """Two egresses, same instant. One moment in time."""
        when = "2026-08-26T10:00:00+00:00"
        evidence = [
            _record(vantage="scanner_egress", when=when),
            _record(vantage="proxy_egress", asn="AS13335",
                    provider="cloudflare", when=when),
        ]
        allowed, why = rev.may_hide(state="visible", evidence=evidence)
        self.assertFalse(allowed, why)

    def test_two_vantages_in_separate_windows_can_hide(self):
        """The one cell that permits it - and it has to, or nothing ever can."""
        evidence = [
            _record(vantage="scanner_egress", when="2026-08-26T10:00:00+00:00"),
            _record(vantage="proxy_egress", asn="AS13335",
                    provider="cloudflare", when="2026-08-26T10:10:00+00:00"),
        ]
        allowed, why = rev.may_hide(state="visible", evidence=evidence)
        self.assertTrue(allowed, why)

    def test_two_vantages_sharing_an_asn_are_not_independent(self):
        """Independence is measured, not declared by naming them differently."""
        evidence = [
            _record(vantage="a", asn="AS8075", when="2026-08-26T10:00:00+00:00"),
            _record(vantage="b", asn="AS8075", when="2026-08-26T10:10:00+00:00"),
        ]
        allowed, why = rev.may_hide(state="visible", evidence=evidence)
        self.assertFalse(allowed, why)

    def test_a_vantage_blocked_verdict_never_hides_however_it_is_repeated(self):
        """403 from everywhere we can see is still about where we are."""
        evidence = [
            _record(vantage="scanner_egress", when="2026-08-26T10:00:00+00:00",
                    verdict=rev.ADVISORY_VANTAGE_BLOCKED,
                    scope="vantage:scanner_egress"),
            _record(vantage="proxy_egress", asn="AS13335", provider="cloudflare",
                    when="2026-08-26T10:10:00+00:00",
                    verdict=rev.ADVISORY_VANTAGE_BLOCKED,
                    scope="vantage:proxy_egress"),
        ]
        allowed, why = rev.may_hide(state="visible", evidence=evidence)
        self.assertFalse(allowed, why)
        self.assertIn("escalatable", why)

    def test_a_globally_scoped_route_failure_from_both_vantages_hides(self):
        evidence = [
            _record(vantage="scanner_egress", when="2026-08-26T10:00:00+00:00"),
            _record(vantage="proxy_egress", asn="AS13335", provider="cloudflare",
                    when="2026-08-26T10:10:00+00:00"),
        ]
        self.assertTrue(rev.may_hide(state="visible", evidence=evidence)[0])

    def test_a_healthy_sibling_source_still_blocks_the_hide(self):
        evidence = [
            _record(vantage="scanner_egress", when="2026-08-26T10:00:00+00:00"),
            _record(vantage="proxy_egress", asn="AS13335", provider="cloudflare",
                    when="2026-08-26T10:10:00+00:00"),
        ]
        allowed, _why = rev.may_hide(
            state="visible", evidence=evidence, healthy_sibling_sources=1
        )
        self.assertFalse(allowed)


class ProductionCacheTests(unittest.TestCase):
    """Point 6 read off the committed artifact, not a fixture."""

    @classmethod
    def setUpClass(cls):
        if not CACHE.exists():
            raise unittest.SkipTest("no evidence cache committed")
        cls.routes = (json.loads(CACHE.read_text(encoding="utf-8")).get("routes") or {})
        if not cls.routes:
            raise unittest.SkipTest("evidence cache is empty")

    def _dual(self):
        for route_id, records in self.routes.items():
            vantages = {
                (r.get("test_vantage") or {}).get("id") for r in records
            }
            if len(vantages) > 1:
                yield route_id, records

    def test_the_cache_really_holds_both_vantages(self):
        vantages = {
            (r.get("test_vantage") or {}).get("id")
            for records in self.routes.values() for r in records
        }
        self.assertIn("scanner_egress", vantages)
        self.assertIn("proxy_egress", vantages, "proxy_http_status is not reaching the cache")

    def test_at_least_one_route_carries_two_vantages(self):
        self.assertTrue(next(self._dual(), None), "no route has both vantages")

    def test_a_real_dual_vantage_route_is_refused_and_says_why(self):
        """The finding worth pinning, because it is easy to overstate.

        Every dual-vantage route in the committed cache is REFUSED. The
        observations are transport-only, so the classifier caps them at vantage
        scope and marks them non-escalatable, and non-escalatable evidence
        cannot hide a visible channel however often it repeats. Escalatable
        globally scoped evidence needs real browser measurement, which an
        ordinary scan does not run per route.

        So the honest claim is: the gate is consulted on real routes and
        refuses; it is not the case that production has been hiding channels on
        two-vantage evidence.
        """
        route_id, records = next(self._dual())
        allowed, why = rev.may_hide(state="visible", evidence=records)
        self.assertFalse(allowed, f"{route_id}: {why}")
        self.assertTrue(why.strip(), "a refusal with no reason is not auditable")

    def test_no_route_in_the_committed_cache_supports_a_hide(self):
        permitted = [
            route_id for route_id, records in self._dual()
            if rev.may_hide(state="visible", evidence=records)[0]
        ]
        self.assertEqual(
            permitted, [],
            "a committed route now supports a hide - re-read the evidence and "
            "the report's claim before changing this test",
        )

    def test_the_cache_carries_a_key_id_on_every_record(self):
        """Without the keyed tenant field a record is incomplete and dropped."""
        missing = [
            r.get("route_id")
            for records in self.routes.values() for r in records
            if not str(r.get("hmac_key_id") or "").strip()
        ]
        self.assertEqual(missing[:5], [], f"{len(missing)} records carry no key id")

    def test_the_cache_holds_no_credential(self):
        """Checks for unredacted secrets, not for words that look alarming.

        A first version of this test searched for "password" and "token=" and
        flagged the cache. Both were false: "password" was a film title
        (Password (2019) Bengali) and every "token=" in the file reads
        "token={redacted}", which is the redaction doing its job. Matching on
        vocabulary would have made this test cry wolf on the catalogue's own
        contents, so it matches on shape instead.
        """
        import re

        blob = CACHE.read_text(encoding="utf-8")
        # An identity must not BE a URL - it is host-and-path. It may CONTAIN a
        # scheme, and legitimately does: some proxies carry the target URL
        # inside their own path, so a real route_id reads
        # "…herokuapp.com/https://…cloudfront.net/x.m3u8". A blanket search for
        # "https://" flagged that as a credential leak, which it is not.
        #
        # Assertion messages here are kept to a count. unittest prints the
        # values it compared, and comparing against this 33 MB file produced a
        # 33 MB failure message.
        schemed = [
            route_id for route_id in self.routes
            if str(route_id).startswith(("http://", "https://"))
        ]
        self.assertEqual(
            len(schemed), 0,
            f"{len(schemed)} route ids are full URLs rather than identities",
        )
        # Every query value that could carry a secret must be a placeholder.
        leaked = [
            m.group(0)[:60]
            for m in re.finditer(
                r"(?:token|auth|hdnea|key|sig|signature|password|pwd)=([^&\"\s]+)",
                blob,
                re.IGNORECASE,
            )
            if not m.group(1).startswith("{")
        ]
        self.assertEqual(
            len(leaked), 0,
            f"{len(leaked)} query values are not redacted; first: "
            f"{leaked[0][:40] if leaked else ''}",
        )


class FailureIsNeverFatalTests(unittest.TestCase):
    """A model or key problem must degrade, never stop a scan."""

    def test_a_model_exception_lets_the_caller_through(self):
        original = va.audit_hide

        def boom(*_a, **_k):
            raise RuntimeError("model exploded")

        va.audit_hide = boom
        try:
            allowed, why = va.model_permits_hide(
                "unit.test", {"url": "https://x/y.m3u8"}
            )
        finally:
            va.audit_hide = original
        self.assertTrue(allowed)
        self.assertIn("model unavailable", why)

    def test_evidence_with_no_key_id_is_not_accepted(self):
        record = _record()
        record.pop("hmac_key_id", None)
        record["failure_domain_tenant"] = None
        va.reset()
        accepted = va.supply_evidence([record])
        va.reset()
        self.assertEqual(
            accepted, 0, "an incomplete record was accepted as evidence"
        )

    def test_a_corrupt_cache_file_reads_as_empty(self):
        import tempfile

        from scanner import route_evidence_cache as cache

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.json"
            path.write_text("{not json at all", encoding="utf-8")
            original = cache.DEFAULT_PATH
            cache.DEFAULT_PATH = str(path)
            try:
                self.assertEqual(cache.all_records(), [])
            finally:
                cache.DEFAULT_PATH = original


if __name__ == "__main__":
    unittest.main()
