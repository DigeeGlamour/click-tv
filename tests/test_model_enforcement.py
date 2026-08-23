"""Model enforcement: available, measured, and deliberately off.

I turned blanket enforcement on and it broke seven existing contract tests.
Reading them showed they were right to break. "An item with no reachable route at
all is hidden" is a structural finding across every route a channel has, not one
vantage disagreeing about one route - and `may_hide` refuses it anyway, because it
demands two independent vantages for anything. So blanket enforcement stops
legitimate hides along with illegitimate ones, and these tests pin that finding
rather than the assumption I started with.

The protection that mattered is in place by a narrower route: an item carrying
sustained-playback proof is exempt at each hide site, which is what keeps the
seven restored channels. That targets the failure that was actually measured
instead of switching off hiding in general.
"""
import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import browser_reachability as br  # noqa: E402
from scanner import fast_pipeline as fp  # noqa: E402
from scanner import player_compatibility as pc  # noqa: E402
from scanner import route_evidence as rev  # noqa: E402
from scanner import visibility_audit as va  # noqa: E402

CATALOGUE = ROOT / "data" / "channels" / "bangla.json"
SETTINGS = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))


def _cards():
    payload = json.loads(CATALOGUE.read_text(encoding="utf-8"))
    return payload if isinstance(payload, list) else (payload.get("channels") or [])


class EnforcementDefaultTests(unittest.TestCase):
    def test_enforcement_is_off_by_default(self):
        # Measured, not assumed: turning it on broke seven contracts that
        # require hides which are justified.
        self.assertFalse(va.ENFORCE_MODEL_DECISION)

    def test_audit_only_mode_leaves_the_caller_alone(self):
        allowed, why = va.model_permits_hide(
            "unit.test", {"name": "Live", "url": "https://a.example.net/x.m3u8"}
        )
        self.assertTrue(allowed, why)
        self.assertIn("audit-only", why)

    def test_the_model_would_still_refuse_a_visible_channel(self):
        # The refusal itself is correct; what was wrong was enforcing it
        # everywhere. Recorded so the distinction stays visible.
        allowed, why = rev.may_hide(state=rev.EXISTING_VISIBLE, evidence=[])
        self.assertFalse(allowed, why)

    def test_enforcement_when_switched_on_refuses_a_visible_channel(self):
        original = va.ENFORCE_MODEL_DECISION
        try:
            va.ENFORCE_MODEL_DECISION = True
            allowed, _ = va.model_permits_hide(
                "unit.test", {"name": "Live", "url": "https://a.example.net/x.m3u8"}
            )
            self.assertFalse(allowed)
        finally:
            va.ENFORCE_MODEL_DECISION = original

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

    def test_enforcement_would_stop_every_hide_which_is_why_it_is_off(self):
        # The number that decided it: with enforcement on, nothing is hidden at
        # all - including the hides seven contract tests require. That is too
        # blunt an instrument, and this records the measurement.
        va.ENFORCE_MODEL_DECISION = True
        for path, count in self._run_all_paths().items():
            self.assertEqual(count, 0, f"{path} hid {count} item(s)")

    def test_a_blocked_hide_is_recorded_on_the_item(self):
        # When enforcement IS on, the reason has to be visible where the item is,
        # not only in a report.
        va.ENFORCE_MODEL_DECISION = True
        cards = [copy.deepcopy(c) for c in _cards()]
        pc.mark_unproven_player_items(cards, "channel")
        blocked = [c for c in cards if c.get("model_blocked_hide")]
        self.assertTrue(blocked, "no item recorded why its hide was blocked")
        for card in blocked:
            self.assertIsNot(card.get("publish_allowed"), False, card.get("name"))


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


if __name__ == "__main__":
    unittest.main()
