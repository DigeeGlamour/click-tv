"""Every hide path reads the same proof, including the strict-publish one.

This is the fault that stopped the scanner. `Live Signal Scanner` last ran on
2026-10-04; every failed run that day and the day before died at the same
step, "Run the full test suite", and the workflow has not run since.
Reproduced by running the suite against the published tree of that run
(c68c7be3):

    FAIL  test_promote_proven_channels.RestoredStateTests
            .test_no_hide_path_can_undo_the_restoration
    FAIL  test_promote_proven_channels.RestoredStateTests
            .test_every_hide_path_keeps_the_restored_channels
          AssertionError: strict_player_visibility would hide the restored channels

Desh TV is one of the restored channels and has a sustained-playback proof on
file - two independent real browser sessions, each playing it for a full
120 s to the PASS floor. A scan brought it back as `retryable_pending`, and
on that tree:

    sustained_proof.has_proof        True
    is_player_proven                 True
    item_is_proven_live              True
    mark_confirmed_player_failures   hides 0
    mark_unproven_player_items       hides 0
    mark_unproven_items              hides 0
    _apply_strict_player_visibility  hides 1    <- only this one

`scanner/player_compatibility.is_player_proven` already carries the lesson in
a comment: the status "survives only until the next scan rebuilds the card...
The durable record lives outside the card." `_apply_strict_player_visibility`
had learned to honour the status but not where the status comes from, so one
scan rebuilding one card was enough to turn the suite red and, because the
suite gates every catalogue scan, to stop the whole scanner.

The ledger is keyed `kind|name` over the two words the other callers pass
literally, `channel` and `movie`. A card's own `content_kind` is a richer
vocabulary - Desh TV carries `live_tv` - so the kind is taken from the
pipeline, which was the second half of the fix.

Nothing here is loosened: a card with no proof anywhere is still hidden.
"""
from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import fast_pipeline as fp  # noqa: E402
from scanner import sustained_proof  # noqa: E402

STRICT = {"bd_verification": {"strict_player_publish": True}}


def _card(name="Desh TV", **extra):
    card = {
        "name": name,
        "content_kind": "live_tv",
        "source_pipeline": "tv",
        "verification_status": "retryable_pending",
        "verified": False,
        "publish_allowed": True,
        "url": "https://stream.example/live/index.m3u8",
    }
    card.update(extra)
    return card


class _Ledger:
    """Swap the durable ledger for one this test controls."""

    def __init__(self, *entries):
        self.registry = {"proofs": {
            sustained_proof._key(kind, name): {"passes": 2}
            for kind, name in entries
        }}
        self._real = None

    def __enter__(self):
        self._real = sustained_proof.load
        sustained_proof.load = lambda *a, **k: self.registry
        return self

    def __exit__(self, *exc):
        sustained_proof.load = self._real
        return False


class AProvenCardSurvivesTheStrictGate(unittest.TestCase):
    def test_a_card_with_a_proof_is_not_hidden(self):
        with _Ledger(("channel", "Desh TV")):
            cards = [_card()]
            self.assertEqual(0, fp._apply_strict_player_visibility(cards, STRICT))
            self.assertIs(cards[0]["publish_allowed"], True)

    def test_the_proof_is_looked_up_under_the_pipeline_kind(self):
        """Not `content_kind`: the ledger holds `channel|desh tv`, the card
        says `live_tv`, and asking for `live_tv|desh tv` finds nothing."""
        with _Ledger(("live_tv", "Desh TV")):
            self.assertEqual(1, fp._apply_strict_player_visibility([_card()], STRICT))

    def test_a_movie_is_looked_up_as_a_movie(self):
        with _Ledger(("movie", "Some Film")):
            card = _card("Some Film", source_pipeline="movies", content_kind="movie")
            self.assertEqual(0, fp._apply_strict_player_visibility([card], STRICT))

    def test_the_helper_answers_directly(self):
        with _Ledger(("channel", "Desh TV")):
            self.assertTrue(fp._has_durable_playback_proof(_card()))
            self.assertFalse(fp._has_durable_playback_proof(_card("Nobody TV")))


class NothingElseIsLoosened(unittest.TestCase):
    def test_a_card_with_no_proof_anywhere_is_still_hidden(self):
        with _Ledger():
            cards = [_card()]
            self.assertEqual(1, fp._apply_strict_player_visibility(cards, STRICT))
            self.assertIs(cards[0]["publish_allowed"], False)
            self.assertEqual(cards[0]["player_visibility"], "hidden_unverified")

    def test_the_gate_still_does_nothing_when_it_is_switched_off(self):
        with _Ledger():
            cards = [_card()]
            self.assertEqual(
                0,
                fp._apply_strict_player_visibility(
                    cards, {"bd_verification": {"strict_player_publish": False}}),
            )

    def test_a_verified_card_short_circuits_before_the_ledger(self):
        with _Ledger():
            self.assertEqual(
                0, fp._apply_strict_player_visibility([_card(verified=True)], STRICT))

    def test_the_status_path_still_works_on_its_own(self):
        from scanner.browser_reachability import PROVEN_LIVE_STATUSES

        with _Ledger():
            card = _card(verification_status=sorted(PROVEN_LIVE_STATUSES)[0])
            self.assertEqual(0, fp._apply_strict_player_visibility([card], STRICT))


class AnUnreadableLedgerMeansNoProofNotACrash(unittest.TestCase):
    def test_a_raising_ledger_is_survived(self):
        real = sustained_proof.load

        def boom(*_a, **_k):
            raise OSError("ledger unreadable")

        sustained_proof.load = boom
        try:
            self.assertFalse(fp._has_durable_playback_proof(_card()))
            self.assertEqual(1, fp._apply_strict_player_visibility([_card()], STRICT))
        finally:
            sustained_proof.load = real


class TheFourHidePathsAgree(unittest.TestCase):
    """What `test_promote_proven_channels` checks against the published
    catalogue, checked here against a card this test owns - so the invariant
    is pinned even on a tree whose catalogue has drifted.

    The card is the real Desh TV card of 2026-10-04, reduced to the fields
    the four paths read: a restored channel with a durable proof, which a
    scan brought back as `retryable_pending` through the transient rescue.
    The paths do not all answer for the same reason, and should not -
    `mark_unproven_items` asks about browser reachability and recognises the
    rescue, the other three ask about proof - but no path may hide it.
    """

    def test_no_path_hides_a_card_the_ledger_vouches_for(self):
        from scanner import browser_reachability as br
        from scanner import player_compatibility as pc

        def _card(**extra):  # noqa: D401 - the real card's shape
            base = {
                "name": "Desh TV",
                "content_kind": "live_tv",
                "source_pipeline": "tv",
                "verification_status": "retryable_pending",
                "verification_mode": "transient_rescue",
                "transient_rescue_count": 1,
                "verified": False,
                "publish_allowed": True,
                "url": "https://stream.example/live/index.m3u8",
            }
            base.update(extra)
            return base

        with _Ledger(("channel", "Desh TV")):
            paths = {
                "mark_confirmed_player_failures":
                    lambda items: pc.mark_confirmed_player_failures(items, "channel"),
                "mark_unproven_player_items":
                    lambda items: pc.mark_unproven_player_items(items, "channel"),
                "mark_unproven_items":
                    lambda items: br.mark_unproven_items(items, "channel", True)[0],
                "strict_player_visibility":
                    lambda items: fp._apply_strict_player_visibility(items, STRICT),
            }
            for name, run in paths.items():
                with self.subTest(path=name):
                    self.assertEqual(0, run([copy.deepcopy(_card())]))


if __name__ == "__main__":
    unittest.main()
