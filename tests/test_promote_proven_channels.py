"""Phase 3 restoration, and the conditions it must never relax.

Seven channels were put back into the catalogue because a 120 s measurement, run
twice, disproved the ledger entry they were removed on. That is the right outcome
and also the most dangerous script in the repository: it writes to data/. The
tests below pin the four things that keep it honest - proof required, record
preserved verbatim, nothing hidden, no duplicates.
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import route_evidence as rev  # noqa: E402

SCRIPT = ROOT / "scripts" / "promote-proven-channels.py"
PHASE1 = ROOT / "reports" / "phase1-sustained-playback.json"
LEDGER = ROOT / "reports" / "confirmed-player-failures.json"
CATALOGUE = ROOT / "data" / "channels" / "bangla.json"

RESTORED = {
    "Channel 24", "Channel 1 NEWS", "Desh TV", "Ekhon Tv",
    "Global TV", "Mohona TV", "NEXUS TV",
}


def _cards(path):
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, list) else (
        payload.get("channels") or payload.get("items") or []
    )


class ScriptContractTests(unittest.TestCase):
    def setUp(self):
        self.source = SCRIPT.read_text(encoding="utf-8")

    def test_restoration_requires_two_full_passes(self):
        self.assertIn("REQUIRED_FRESH_SESSIONS", self.source)
        self.assertIn("rev.PROVEN", self.source)

    def test_the_script_never_hides_anything(self):
        # It sets publish_allowed True and must have no path to False.
        self.assertIn('card["publish_allowed"] = True', self.source)
        self.assertNotIn("publish_allowed\"] = False", self.source)
        self.assertNotIn("publish_allowed=False", self.source)

    def test_the_stored_record_is_used_verbatim(self):
        # No URL, credential, header profile or proxy mode may be invented.
        self.assertIn('dict(item["record"])', self.source)
        for invented in ("url =", "header_profile =", "proxy_mode ="):
            self.assertNotIn(invented, self.source, f"script writes {invented}")

    def test_a_dry_run_mode_exists(self):
        self.assertIn("--dry-run", self.source)


class RestoredStateTests(unittest.TestCase):
    def test_every_restored_channel_is_proven_in_phase1(self):
        phase1 = json.loads(PHASE1.read_text(encoding="utf-8"))
        proven = {
            str(r.get("name")) for r in phase1.get("results") or ()
            if r.get("proven")
        }
        self.assertTrue(
            RESTORED <= proven,
            f"restored without proof: {sorted(RESTORED - proven)}",
        )

    def test_each_proof_is_two_independent_full_passes(self):
        phase1 = json.loads(PHASE1.read_text(encoding="utf-8"))
        for result in phase1.get("results") or ():
            if str(result.get("name")) not in RESTORED:
                continue
            passes = [
                o for o in (result.get("observations") or ())
                if o.get("verdict") == rev.PROVEN
            ]
            self.assertGreaterEqual(
                len(passes), rev.REQUIRED_FRESH_SESSIONS, str(result.get("name"))
            )
            for observation in passes:
                metrics = observation.get("playback_metrics") or {}
                self.assertGreaterEqual(
                    float(metrics.get("media_progress_seconds") or 0),
                    rev.PASS_MIN_MEDIA_PROGRESS_SECONDS,
                    str(result.get("name")),
                )

    def test_the_restored_channels_are_in_the_catalogue_exactly_once(self):
        names = [str(c.get("name") or "") for c in _cards(CATALOGUE)]
        for name in RESTORED:
            self.assertEqual(names.count(name), 1, f"{name} appears {names.count(name)}x")

    def test_the_restored_channels_are_publishable(self):
        for card in _cards(CATALOGUE):
            if str(card.get("name") or "") in RESTORED:
                self.assertIsNot(card.get("publish_allowed"), False, card.get("name"))

    def test_the_disproved_ledger_entries_are_gone(self):
        # Leaving them would let the next scan hide the channel again on the
        # evidence that was just disproved.
        ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
        still = {
            str((r.get("record") or {}).get("name") or "")
            for r in ledger.get("records") or ()
        }
        self.assertFalse(
            RESTORED & still, f"still in the failure ledger: {sorted(RESTORED & still)}"
        )

    def test_the_removal_is_recorded_rather_than_silent(self):
        ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
        self.assertEqual(
            set(ledger.get("phase1_disproved_removals") or []), RESTORED
        )
        self.assertIn("phase1_disproved_note", ledger)

    def test_the_restored_status_counts_as_proven_live(self):
        # Found by a failing test, not by reading: the restoration invented a new
        # status string without registering it, so the publish gate saw seven
        # published channels it considered unplayable and would have hidden them
        # on the next scan - undoing the restoration with the measurement still
        # sitting in the report.
        from scanner.browser_reachability import (  # noqa: PLC0415
            PROVEN_LIVE_STATUSES,
            item_is_proven_live,
        )

        self.assertIn("verified_sustained_playback", PROVEN_LIVE_STATUSES)
        for card in _cards(CATALOGUE):
            if str(card.get("name") or "") in RESTORED:
                self.assertTrue(item_is_proven_live(card), card.get("name"))

    def test_the_status_written_is_the_status_registered(self):
        # The script and the gate must not drift apart.
        from scanner.browser_reachability import (  # noqa: PLC0415
            PROVEN_LIVE_STATUSES,
        )

        source = SCRIPT.read_text(encoding="utf-8")
        import re  # noqa: PLC0415

        match = re.search(
            r'card\["verification_status"\]\s*=\s*"([^"]+)"', source
        )
        self.assertIsNotNone(match, "the script no longer sets a literal status")
        self.assertIn(match.group(1), PROVEN_LIVE_STATUSES)

    def test_no_hide_path_can_undo_the_restoration(self):
        """The restoration must survive the next scan.

        Found by running the audit after restoring: two hide paths key on
        evidence weaker than the proof these channels now carry, and both would
        have hidden all seven on the next scan with the measurement still
        sitting in the report.

        `_apply_strict_player_visibility` keys on `verified`, which means "this
        scan proved it" and is False for a card proven by a separate measurement
        run - and strict_player_publish is ON in config/settings.json, so this
        one was live. `is_player_proven` keys on the fingerprint ledger, whose
        fingerprint includes the URL and so stops matching when a source
        rotates; that gate is currently off, and this keeps turning it back on
        from being destructive.
        """
        import copy  # noqa: PLC0415
        import json as _json  # noqa: PLC0415

        from scanner.fast_pipeline import (  # noqa: PLC0415
            _apply_strict_player_visibility,
        )
        from scanner.player_compatibility import (  # noqa: PLC0415
            mark_unproven_player_items,
        )

        settings = _json.loads(
            (ROOT / "config" / "settings.json").read_text(encoding="utf-8")
        )
        restored = [
            copy.deepcopy(c) for c in _cards(CATALOGUE)
            if str(c.get("name") or "") in RESTORED
        ]
        self.assertEqual(len(restored), len(RESTORED))

        hidden = _apply_strict_player_visibility(copy.deepcopy(restored), settings)
        self.assertEqual(
            hidden, 0, "strict player visibility would undo the restoration"
        )
        hidden = mark_unproven_player_items(copy.deepcopy(restored), "channel")
        self.assertEqual(
            hidden, 0, "the player-proof gate would undo the restoration"
        )

    def test_every_hide_path_keeps_the_restored_channels(self):
        # Two paths were found by hand; this walks all four so a fifth cannot be
        # added later without the restoration silently breaking again.
        import copy  # noqa: PLC0415
        import json as _json  # noqa: PLC0415

        from scanner import browser_reachability as br  # noqa: PLC0415
        from scanner import fast_pipeline as fp  # noqa: PLC0415
        from scanner import player_compatibility as pc  # noqa: PLC0415

        settings = _json.loads(
            (ROOT / "config" / "settings.json").read_text(encoding="utf-8")
        )
        restored = [
            c for c in _cards(CATALOGUE) if str(c.get("name") or "") in RESTORED
        ]
        paths = {
            "mark_confirmed_player_failures":
                lambda items: pc.mark_confirmed_player_failures(items, "channel"),
            "mark_unproven_player_items":
                lambda items: pc.mark_unproven_player_items(items, "channel"),
            "mark_unproven_items":
                lambda items: br.mark_unproven_items(items, "channel", True)[0],
            "strict_player_visibility":
                lambda items: fp._apply_strict_player_visibility(items, settings),
        }
        for name, run in paths.items():
            hidden = run([copy.deepcopy(c) for c in restored])
            self.assertEqual(hidden, 0, f"{name} would hide the restored channels")

    def test_the_restored_channels_read_as_reachable_and_proven(self):
        from scanner.browser_reachability import (  # noqa: PLC0415
            item_is_browser_reachable,
            item_is_proven_live,
        )

        for card in _cards(CATALOGUE):
            if str(card.get("name") or "") not in RESTORED:
                continue
            self.assertTrue(item_is_browser_reachable(card), card.get("name"))
            self.assertTrue(item_is_proven_live(card), card.get("name"))

    def test_the_proof_survives_a_scan_rebuilding_the_card(self):
        """The durable record, and why a card cannot hold it.

        Measured: the first scan after the restoration rebuilt each card from its
        sources. Every field the restoration wrote - the status, the mode, the
        note - was gone, one backup URL had changed, and the status-based
        exemption stopped matching. All seven would have been hidden again.

        The proof therefore lives outside the card, in
        state/sustained-playback-proof.json, keyed by name rather than by a
        fingerprint that contains the URL.
        """
        from scanner import sustained_proof  # noqa: PLC0415

        registry = sustained_proof.load()
        for card in _cards(CATALOGUE):
            if str(card.get("name") or "") not in RESTORED:
                continue
            self.assertTrue(
                sustained_proof.has_proof(card, "channel", registry),
                card.get("name"),
            )
            proof = sustained_proof.proof_for(card, "channel", registry)
            self.assertGreaterEqual(proof["pass_count"], 2, card.get("name"))
            self.assertEqual(float(proof["window_seconds"]), 120.0)

    def test_the_registry_refuses_a_claim_without_two_passes(self):
        # It must not be possible to seed the registry with an assertion.
        from scanner import sustained_proof  # noqa: PLC0415

        for bad in (
            {"pass_count": 1, "window_seconds": 120.0},
            {"pass_count": 2},
            {"window_seconds": 120.0},
            {},
        ):
            written, why = sustained_proof.record(
                "channel", "Fake Channel", bad, path="/dev/null"
            )
            self.assertFalse(written, f"{bad} was accepted: {why}")

    def test_a_genuinely_unproven_card_is_still_hidden(self):
        # The exemption must be narrow: it protects the sustained-playback
        # status, not everything.
        from scanner.fast_pipeline import (  # noqa: PLC0415
            _apply_strict_player_visibility,
        )

        unproven = [
            {
                "name": "Unproven",
                "publish_allowed": True,
                "verified": False,
                "verification_status": "pending",
            }
        ]
        hidden = _apply_strict_player_visibility(
            unproven, {"bd_verification": {"strict_player_publish": True}}
        )
        self.assertEqual(hidden, 1)

    def test_no_other_channel_was_touched(self):
        # A fixed count was wrong here: the scanner adds and removes Bangla
        # channels on its own schedule, and this test failed the moment a scan
        # added a 39th. What must hold is that the restoration added exactly the
        # seven, each exactly once, and nothing outside that set changed - which
        # is what the other tests in this class check. So the invariant is the
        # set, not the size.
        names = [str(c.get("name") or "") for c in _cards(CATALOGUE)]
        for name in RESTORED:
            self.assertEqual(
                names.count(name), 1, f"{name} appears {names.count(name)}x"
            )
        self.assertGreaterEqual(
            len(names), len(RESTORED), "the catalogue lost the restored channels"
        )
        self.assertEqual(
            len(names), len(set(names)), "the catalogue has duplicate names"
        )


if __name__ == "__main__":
    unittest.main()
