"""A route a browser could not play must never be the one a card leads with.

`state/measured-playback-failures.json` holds routes a real Chrome session was
pointed at and could not decode. The merge already ranks those last, above
every other signal - but ranking only helps when there is something else to
rank. When a card's only surviving route is one of them, it publishes anyway,
with a green badge, because HTTP verification asked the runner for the URL and
got a 200. `rgkkw.live` answers 200 and produces 0.12 seconds of video.

Measured on 2026-09-02, across the published channel catalogue:

    22 cards led with a route the ledger records as unplayable
    10 of them had a backup the ledger does NOT record as unplayable

So there are two different faults wearing one symptom, and they need different
answers:

  * a playable backup exists - promote it. The card keeps working and nothing
    is lost; it should have led with that route in the first place.
  * no route is playable - hide the card. That is what the CI check has been
    asserting all along ("the route that decodes nowhere must never lead"),
    and it is honest: a card that cannot play is not content, it is a dead
    button that costs the viewer a tap to find out.

Hiding here is the same reversible thing `mark_unproven_items` does - the card
returns by itself as soon as any route is measured playable again, because a
passing measurement supersedes the failure in the ledger.

Nothing is deleted. The card keeps its channels, headers and backups; it only
loses `publish_allowed`, and every decision is reported by name.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Tuple

try:
    from scanner import playback_evidence
    from scanner.visibility_audit import model_permits_hide
except ImportError:  # pragma: no cover - direct-module import path
    import playback_evidence  # type: ignore
    from visibility_audit import model_permits_hide  # type: ignore

#: What the card says about itself once it is held back.
HIDDEN_REASON = "every measured route for this card failed in a real browser"


def _url(row: Any) -> str:
    if isinstance(row, dict):
        return str(row.get("url") or "").strip()
    return str(row or "").strip()


def _unplayable(url: str) -> str:
    if not url:
        return ""
    try:
        return playback_evidence.unproven_reason(url)
    except Exception:  # noqa: BLE001 - a ledger read must never break a scan
        return ""


def _swap_in(card: Dict[str, Any], index: int) -> Dict[str, Any]:
    """Make backup `index` the primary, and demote the old primary behind it.

    The whole route is exchanged, not just the URL: header profile, proxy mode,
    stream type and playback id all belong to the route rather than to the
    card, and moving the URL alone would point the player at one host with
    another host's headers.
    """
    backups = list(card.get("backups") or [])
    promoted = dict(backups.pop(index))

    carried = ("url", "header_profile", "proxy_mode", "stream_type",
               "requires_headers", "headers", "playback_id", "resolution",
               "resolution_height", "verification_status", "verification_badge",
               "verified", "quality", "inherit_manifest_query", "referer",
               "user_agent", "origin", "drm")
    demoted = {key: card.get(key) for key in carried if key in card}
    for key in carried:
        if key in promoted:
            card[key] = promoted[key]
        else:
            card.pop(key, None)

    card["backups"] = [demoted] + backups
    card["primary_promoted_from_backup"] = True
    return card


def enforce(items: Iterable[Dict[str, Any]]) -> Tuple[int, int, List[Dict[str, str]]]:
    """Promote past an unplayable primary, or hide the card. Returns the counts.

    (promoted, hidden, report). The report names every card and says which of
    the two happened and why, so a hidden channel is a line to read rather than
    a channel that quietly stopped existing.
    """
    promoted = 0
    hidden = 0
    report: List[Dict[str, str]] = []

    for card in items:
        if not isinstance(card, dict):
            continue
        if card.get("publish_allowed") is False:
            continue

        reason = _unplayable(_url(card))
        if not reason:
            continue

        backups = list(card.get("backups") or [])
        playable = next(
            (index for index, row in enumerate(backups)
             if _url(row) and not _unplayable(_url(row))),
            None,
        )

        if playable is not None:
            was = _url(card)
            _swap_in(card, playable)
            promoted += 1
            report.append({
                "action": "promoted a playable backup",
                "name": str(card.get("name") or ""),
                "category": str(card.get("category") or ""),
                "was": was,
                "now": _url(card),
                "reason": reason,
            })
            continue

        # There is deliberately no exemption for a channel with a
        # sustained-playback proof, though one was tried. That proof records
        # that a browser played the CHANNEL for two full windows; it carries no
        # fingerprint, so it cannot say which route it played. The ledger row
        # is about the route this card is using now, and the two do not
        # contradict each other - a channel proven good on one route can be
        # left holding a dead one.
        #
        # The exemption was added because an earlier draft hid three of the
        # seven hand-restored channels. That was the wrong fix for the wrong
        # cause: the draft ran on merge candidates, which still carry routes
        # the scan is about to replace. Run on the cards actually being
        # written, this rule does not touch any of the seven - and with the
        # exemption in place it left Zee Bangla published with a route that
        # decodes nowhere, which is the exact thing the CI check forbids.
        # Every other hide path in the scanner asks the visibility model first,
        # and this one is no different: the model exists to stop a hide that
        # would remove a card the evidence does not actually condemn, and
        # skipping it here would be quietly claiming an exemption.
        allowed, why = model_permits_hide("unplayable_primary.enforce", card)
        if not allowed:
            card["model_blocked_hide"] = why
            report.append({
                "action": "left visible - the visibility model refused the hide",
                "name": str(card.get("name") or ""),
                "category": str(card.get("category") or ""),
                "was": _url(card),
                "now": _url(card),
                "reason": f"{reason} | {why}",
            })
            continue

        card["publish_allowed"] = False
        card["player_visibility"] = "hidden_measured_unplayable"
        card["verification_note"] = HIDDEN_REASON
        card["measured_unplayable_reason"] = reason
        hidden += 1
        report.append({
            "action": "hidden - no playable route",
            "name": str(card.get("name") or ""),
            "category": str(card.get("category") or ""),
            "was": _url(card),
            "now": "",
            "reason": reason,
        })

    return promoted, hidden, report
