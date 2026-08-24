"""Model enforcement, conditional on evidence actually existing.

Blanket enforcement was tried first and broke seven contract tests. Reading them
showed they were right: "an item with no reachable route at all is hidden" is a
structural finding across every route a channel has, and `may_hide` refuses it
anyway because it demands two independent vantages for anything. Refusing every
hide is not safety, it is a different failure.

So enforcement now depends on evidence. With no per-route records for an item the
caller's decision stands and only the audit records the model's view; with records
present the model's refusal is honoured. That is the case the guard was written
for - a channel with a 403 from two datacentre egresses must not be removed, and
a route that produced no data from two independent vantages in separate windows
may be.
"""
import copy
import datetime as dt
import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import browser_reachability as br  # noqa: E402
from scanner import fast_pipeline as fp  # noqa: E402
from scanner import player_compatibility as pc  # noqa: E402
from scanner import route_evidence as rev  # noqa: E402
from scanner import route_evidence_pipeline as rp  # noqa: E402
from scanner import visibility_audit as va  # noqa: E402

CATALOGUE = ROOT / "data" / "channels" / "bangla.json"
SETTINGS = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))


def _cards():
    payload = json.loads(CATALOGUE.read_text(encoding="utf-8"))
    return payload if isinstance(payload, list) else (payload.get("channels") or [])


class EnforcementDefaultTests(unittest.TestCase):
    def setUp(self):
        va.clear_evidence()

    def tearDown(self):
        va.clear_evidence()

    def test_enforcement_is_on(self):
        self.assertTrue(va.ENFORCE_MODEL_DECISION)

    def test_without_evidence_the_caller_decision_stands(self):
        # This is what stops enforcement from breaking legitimate structural
        # hides. Measured: making it unconditional broke seven contracts.
        allowed, why = va.model_permits_hide(
            "unit.test", {"name": "Live", "url": "https://a.example.net/x.m3u8"}
        )
        self.assertTrue(allowed, why)
        self.assertIn("no per-route evidence", why)

    def test_the_model_would_still_refuse_a_visible_channel(self):
        # The refusal itself is correct; what was wrong was enforcing it
        # everywhere. Recorded so the distinction stays visible.
        allowed, why = rev.may_hide(state=rev.EXISTING_VISIBLE, evidence=[])
        self.assertFalse(allowed, why)

    def test_a_partial_record_is_not_accepted_as_evidence(self):
        # A partial record reads as `unknown`, which would make the model look
        # better informed than it is.
        accepted = va.supply_evidence([{"route_id": "r1", "verdict": "playback_fail"}])
        self.assertEqual(accepted, 0)

    def test_a_record_without_a_route_id_is_not_accepted(self):
        self.assertEqual(va.supply_evidence([{"verdict": "playback_fail"}]), 0)

    def test_an_already_hidden_item_stays_hidden(self):
        # Enforcement must not resurrect anything either; it is one-directional.
        allowed, _ = va.model_permits_hide(
            "unit.test",
            {
                "name": "Hidden",
                "url": "https://a.example.net/x.m3u8",
                "publish_allowed": False,
            },
        )
        self.assertTrue(allowed)

    def test_a_model_failure_does_not_block_a_scan(self):
        # An exception inside the model must leave the caller's behaviour alone
        # rather than halting a scan or silently hiding everything.
        allowed, why = va.model_permits_hide("unit.test", None)
        self.assertTrue(allowed, why)


class MeasuredEffectTests(unittest.TestCase):
    """The numbers the decision to enforce was based on."""

    def setUp(self):
        self._original = va.ENFORCE_MODEL_DECISION

    def tearDown(self):
        va.ENFORCE_MODEL_DECISION = self._original

    def _run_all_paths(self):
        cards = _cards()
        va.reset()
        return {
            "unproven_player": pc.mark_unproven_player_items(
                [copy.deepcopy(c) for c in cards], "channel"
            ),
            "confirmed_failures": pc.mark_confirmed_player_failures(
                [copy.deepcopy(c) for c in cards], "channel"
            ),
            "unproven_run": br.mark_unproven_items(
                [copy.deepcopy(c) for c in cards], "channel", True
            )[0],
            "strict_visibility": fp._apply_strict_player_visibility(
                [copy.deepcopy(c) for c in cards], SETTINGS
            ),
        }

    def test_enforcement_never_hides_more_than_audit_only(self):
        va.ENFORCE_MODEL_DECISION = False
        without = self._run_all_paths()
        va.ENFORCE_MODEL_DECISION = True
        with_enforcement = self._run_all_paths()
        for path, count in with_enforcement.items():
            self.assertLessEqual(
                count,
                without[path],
                f"{path} hid MORE with enforcement on: "
                f"{count} vs {without[path]}",
            )

    def test_without_evidence_enforcement_changes_nothing(self):
        # The measurement that shaped the design. Unconditional enforcement hid
        # nothing at all, including the hides seven contracts require; made
        # conditional on evidence, an evidence-free scan behaves exactly as
        # before and the audit still records the model's view.
        va.clear_evidence()
        va.ENFORCE_MODEL_DECISION = False
        without = self._run_all_paths()
        va.ENFORCE_MODEL_DECISION = True
        with_enforcement = self._run_all_paths()
        self.assertEqual(with_enforcement, without)

    def test_a_blocked_hide_is_recorded_on_the_item(self):
        # When the model does block a hide, the reason has to be visible where
        # the item is, not only in a report. Driven by real evidence, since
        # enforcement without evidence deliberately does nothing.
        import os as _os  # noqa: PLC0415

        saved = _os.environ.get(rev.HMAC_KEY_ENV)
        _os.environ[rev.HMAC_KEY_ENV] = "k" * 32
        try:
            va.clear_evidence()
            va.ENFORCE_MODEL_DECISION = True
            key = rev.configured_hmac_key()
            metrics = {
                "announced_render_tracks": ["video", "audio"],
                "progressing_tracks": [],
                "first_frame_seconds": None,
                "startup_seconds": None,
                "media_progress_seconds": 0.0,
                "cumulative_stall_seconds": 120,
                "fatal_errors": ['HttpStatusCodeInvalid {"code":403}'],
                "recovered_to_pass_floor": False,
            }
            cards = [copy.deepcopy(c) for c in _cards()][:4]
            records = []
            for card in cards:
                for offset in (0, 200):
                    stamp = (
                        dt.datetime(2026, 8, 23, 10, 0, 0, tzinfo=dt.timezone.utc)
                        + dt.timedelta(seconds=offset)
                    ).isoformat()
                    observation = {
                        "playback_metrics": metrics,
                        "browser_profile": "desktop_chrome",
                        "failed_profiles": list(rev.DECLARED_TARGET_MATRIX),
                        "observed_at": stamp,
                    }
                    records.extend(
                        rp.build_route_evidence(
                            str(card.get("url") or ""),
                            scanner=dict(observation),
                            proxy=dict(observation),
                            hmac_key=key,
                        )
                    )
            va.supply_evidence(records)
            pc.mark_unproven_player_items(cards, "channel")
            blocked = [c for c in cards if c.get("model_blocked_hide")]
            self.assertTrue(blocked, "no item recorded why its hide was blocked")
            for card in blocked:
                self.assertIsNot(card.get("publish_allowed"), False, card.get("name"))
        finally:
            va.clear_evidence()
            if saved is None:
                _os.environ.pop(rev.HMAC_KEY_ENV, None)
            else:
                _os.environ[rev.HMAC_KEY_ENV] = saved


class VantageIndependenceEvidenceTests(unittest.TestCase):
    """The condition that made the strict guard unreachable is now measured."""

    REPORT = ROOT / "reports" / "vantage-independence.json"

    def test_the_probe_report_exists(self):
        self.assertTrue(self.REPORT.exists())

    def test_independence_is_established_by_a_reachability_difference(self):
        payload = json.loads(self.REPORT.read_text(encoding="utf-8"))
        self.assertTrue(payload["independent"])
        self.assertGreaterEqual(payload["decisive_hosts"], 1)
        for item in payload["evidence"]:
            direct_ok = {s for s in item["direct"] if str(s).startswith("2")}
            proxy_ok = {
                s
                for states in item["via_proxy"].values()
                for s in states
                if str(s).startswith("2")
            }
            # One side reaches it, the other never does. A status-code
            # disagreement alone would prove nothing.
            self.assertNotEqual(bool(direct_ok), bool(proxy_ok), item["host"])

    def test_the_report_states_the_proxy_caveat(self):
        # Four proxies on one account are one vantage between them, not four.
        payload = json.loads(self.REPORT.read_text(encoding="utf-8"))
        self.assertIn("ONE", payload["proxy_caveat"])

    def test_the_report_carries_no_credential(self):
        payload = json.loads(self.REPORT.read_text(encoding="utf-8"))
        self.assertFalse(rev.evidence_contains_forbidden_material(payload))


class EvidenceDrivenEnforcementTests(unittest.TestCase):
    """The cases the guard exists for, driven by assembled records."""

    ROUTE = "https://tenant-a.akamaized.net/live/index.m3u8"
    BASE = dt.datetime(2026, 8, 23, 10, 0, 0, tzinfo=dt.timezone.utc)

    BASE_METRICS = {
        "announced_render_tracks": ["video", "audio"],
        "progressing_tracks": [],
        "first_frame_seconds": None,
        "startup_seconds": None,
        "media_progress_seconds": 0.0,
        "cumulative_stall_seconds": 120,
        "recovered_to_pass_floor": False,
    }

    def setUp(self):
        self._saved = os.environ.get(rev.HMAC_KEY_ENV)
        os.environ[rev.HMAC_KEY_ENV] = "k" * 32
        va.clear_evidence()
        va.reset()

    def tearDown(self):
        va.clear_evidence()
        if self._saved is None:
            os.environ.pop(rev.HMAC_KEY_ENV, None)
        else:
            os.environ[rev.HMAC_KEY_ENV] = self._saved

    def _load(self, fatal):
        key = rev.configured_hmac_key()
        metrics = dict(self.BASE_METRICS, fatal_errors=list(fatal))
        records = []
        for offset in (0, 200):
            stamp = (self.BASE + dt.timedelta(seconds=offset)).isoformat()
            observation = {
                "playback_metrics": metrics,
                "browser_profile": "desktop_chrome",
                "failed_profiles": list(rev.DECLARED_TARGET_MATRIX),
                "observed_at": stamp,
            }
            records.extend(
                rp.build_route_evidence(
                    self.ROUTE,
                    scanner=dict(observation),
                    proxy=dict(observation),
                    hmac_key=key,
                )
            )
        self.assertGreater(va.supply_evidence(records), 0)
        return {"name": "X", "url": self.ROUTE, "backups": []}

    def test_a_403_from_both_vantages_blocks_the_hide(self):
        # The founding measurement of this whole model: a published channel
        # returning 403 from a datacentre egress is working for its audience.
        item = self._load(['HttpStatusCodeInvalid {"code":403}'])
        allowed, why = va.model_permits_hide("unit.test", item)
        self.assertFalse(allowed, why)

    def test_a_decoder_limit_from_both_vantages_blocks_the_hide(self):
        item = self._load(["media element error code 3"])
        allowed, why = va.model_permits_hide("unit.test", item)
        self.assertFalse(allowed, why)

    def test_a_real_route_failure_permits_the_hide(self):
        # Two independent vantages, two separated windows, no vantage or decoder
        # explanation. This is the one case the model is willing to act on.
        item = self._load(["source produced no data"])
        allowed, why = va.model_permits_hide("unit.test", item)
        self.assertTrue(allowed, why)

    def test_a_healthy_sibling_source_still_blocks_the_hide(self):
        item = self._load(["source produced no data"])
        allowed, why = va.model_permits_hide(
            "unit.test", item, healthy_sibling_sources=1
        )
        self.assertFalse(allowed, why)


if __name__ == "__main__":
    unittest.main()
