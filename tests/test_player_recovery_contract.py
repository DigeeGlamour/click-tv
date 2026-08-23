"""The raw-TS recovery path, pinned as a source contract.

There is no JS test runner in this repository, so these assertions read app.js
directly. That is a weak form of test and worth saying so - it proves the code
is shaped correctly, not that it runs correctly. It exists because the thing it
guards was invisible: the old recovery called load() on an already-loaded
mpegts.js player, which that library ignores, inside a catch that discarded the
error. It looked like a recovery in every log and was a no-op in every run.
"""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

APP = (ROOT / "site" / "assets" / "js" / "app.js").read_text(encoding="utf-8")


class RecoveryContractTests(unittest.TestCase):
    def test_the_recreate_function_exists(self):
        self.assertIn("async function recreateMpegTsPlayer(", APP)

    def test_the_full_teardown_sequence_is_used(self):
        # mpegts.js will not reload an instance in place, so a partial teardown
        # leaves the old player attached and the "recovery" does nothing.
        for step in ("pause", "unload", "detachMediaElement", "destroy"):
            self.assertIn(f"'{step}'", APP, f"teardown step {step} missing")

    def test_the_in_place_reload_no_op_is_gone(self):
        # The exact shape of the old bug: load() then play() on state.mpegts,
        # with no unload first.
        self.assertNotRegex(
            APP,
            r"state\.mpegts\.load\(\);\s*\r?\n\s*state\.mpegts\.play\(\);",
            "the no-op in-place reload is back",
        )

    def test_loading_complete_is_handled(self):
        # A raw-TS live route can return a finite body and end cleanly - measured
        # at 10.6 MB then a clean early EOF. Without this the stream just stopped.
        self.assertIn("LOADING_COMPLETE", APP)

    def test_a_movie_ending_is_not_treated_as_a_fault(self):
        window = APP[APP.index("LOADING_COMPLETE"):]
        window = window[: window.index("recreateMpegTsPlayer") + 200]
        self.assertIn("VIEW.MOVIE", window)

    def test_recovery_is_bounded(self):
        self.assertIn("MPEGTS_RECOVERY_MAX_ATTEMPTS", APP)
        match = re.search(r"MPEGTS_RECOVERY_MAX_ATTEMPTS\s*=\s*(\d+)", APP)
        self.assertIsNotNone(match, "the retry cap is not a literal")
        self.assertGreater(int(match.group(1)), 0)
        self.assertLessEqual(
            int(match.group(1)), 10, "an unbounded-in-practice retry cap"
        )

    def test_recovery_uses_backoff(self):
        self.assertIn("MPEGTS_RECOVERY_BASE_DELAY_MS", APP)
        self.assertRegex(APP, r"Math\.pow\(2,\s*attemptNumber")

    def test_duplicate_bursts_are_suppressed(self):
        # Stall watchdog, error handler and LOADING_COMPLETE can all fire for one
        # underlying event.
        self.assertIn("MPEGTS_RECOVERY_MIN_GAP_MS", APP)
        self.assertIn("inFlight", APP)

    def test_exhaustion_reaches_the_attempt_ladder(self):
        window = APP[APP.index("async function recreateMpegTsPlayer("):]
        window = window[: window.index("async function initMpegTs(")]
        self.assertIn("failCurrentAttempt", window)
        self.assertIn("recovery exhausted", window)

    def test_a_failed_rebuild_is_not_swallowed(self):
        window = APP[APP.index("async function recreateMpegTsPlayer("):]
        window = window[: window.index("async function initMpegTs(")]
        self.assertIn("MPEGTS recovery failed", window)

    def test_the_retry_budget_is_restored_on_real_success(self):
        # Otherwise the budget is spent once and never returns: a channel that
        # recovered four times over an evening would be dropped on its fifth
        # hiccup even though every earlier recovery worked.
        window = APP[APP.index("function handlePlaybackSuccess()"):]
        window = window[: window.index("\n}")]
        self.assertIn("resetMpegTsRecovery()", window)

    def test_the_recreate_has_a_url_to_rebuild_from(self):
        self.assertIn("state.mpegtsContext", APP)
        window = APP[APP.index("async function recreateMpegTsPlayer("):]
        window = window[: window.index("async function initMpegTs(")]
        self.assertIn("context.url", window)

    def test_recovery_stops_when_the_attempt_is_no_longer_active(self):
        window = APP[APP.index("async function recreateMpegTsPlayer("):]
        window = window[: window.index("async function initMpegTs(")]
        self.assertGreaterEqual(
            window.count("isActiveAttempt"), 2,
            "the attempt must be re-checked after the backoff wait",
        )


if __name__ == "__main__":
    unittest.main()
