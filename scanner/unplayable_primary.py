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

import json
from typing import Any, Dict, Iterable, List, Optional, Tuple

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


def route_identity(row: Any) -> str:
    """What makes two entries the same physical route.

    The URL, when the entry has one - that is the rule the published-data
    check asserts and the one the merge already applies.

    A protected route has no URL at all: sanitisation strips the address of
    anything that needs credentials, so the public card carries only the
    content-addressed `playback_id`. Two such entries with one id are one
    route, and comparing their (absent) URLs would call them distinct
    forever. Only used as a fallback, so an entry that HAS a URL is judged
    exactly as before.
    """
    url = _url(row)
    if url:
        return url
    if isinstance(row, dict):
        playback_id = str(row.get("playback_id") or "").strip()
        if playback_id:
            return "playback_id:" + playback_id
    return ""


def exact_route_identity(row: Any) -> Optional[Tuple[str, str, str, str]]:
    """A byte-identical route configuration: same URL, same playback id, same
    headers, same DRM.

    Two entries matching on all four are the same route by every definition
    there is, so folding one away can never cost a card a distinct way to
    play. That is why this one is applied to the primary as well, and to a
    card carrying only a single backup - neither of which the physical-route
    rule below reaches.

    This is deliberately the identity scripts/validate-pages.py refuses to
    publish (`duplicate primary/backup configuration`). Sharing it is what
    makes the two gates agree: after this has run, the condition that gate
    rejects cannot be present.
    """
    if not isinstance(row, dict):
        return None
    return (
        _url(row),
        str(row.get("playback_id") or "").strip(),
        json.dumps(row.get("headers") or {}, sort_keys=True),
        json.dumps(row.get("drm") or {}, sort_keys=True),
    )


def link_count(card: Dict[str, Any]) -> int:
    """What `available_link_count` must say, given what the card now holds.

    One rule, and it is the merger's own (scanner/merger.py): the primary
    counts as one link unless the card is metadata-only, plus one per backup.

    Recomputed from the card rather than adjusted by a delta, because a count
    that has drifted has no history worth preserving - Duronto TV published
    `available_link_count: 2` over a primary and two backups, and Ananda TV
    published 4 over five, because whatever grew the backup list never
    revisited the number describing it.
    """
    backups = card.get("backups")
    total = len(backups) if isinstance(backups, list) else 0
    if card.get("metadata_only") is True:
        return total
    if route_identity(card):
        total += 1
    return total


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

    # The demoted route often already sits in the backup list - a card
    # commonly lists its primary there too - so putting it back unconditionally
    # printed it twice. Measured: the published Zee Bangla card listed the same
    # rgkkw.live URL as two separate backups, which the catalogue's own test
    # caught on the next scan.
    demoted_url = _url(demoted)
    kept = [row for row in backups if _url(row) != demoted_url] if demoted_url else backups
    card["backups"] = ([demoted] + kept) if demoted_url else kept
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

def dedupe_backup_urls(items: Iterable[Dict[str, Any]]) -> List[Dict[str, str]]:
    """One physical route, one entry in a card's backup list.

    The same URL can arrive from two sources with different headers, so it
    is two playback ids and passes any id-keyed check - and a viewer sees
    one dead route offered twice. The published Zee Bangla card listed
    `rgkkw.live/.../98881.ts` as Backup-1 and again as Backup-3, one from
    manual-playlist-1 needing no headers and one from
    smartplaytv-worker-stream needing them.

    This was repaired once in the committed data by a script and came back,
    so the rule was moved here, into the scan. It came back again anyway -
    run #1211 failed on Ananda TV - and the second time the scan was not the
    one putting it back.

    The published card at 24ff54a is byte-identical to its predecessor except
    that two backup entries appear twice, `available_link_count` still says 4
    over five entries, and two entries share the name `Backup-2`. The merge
    numbers backups 1..N in one pass and cannot emit that; a line-based text
    merge of two independently generated files can, and does. The workflow
    ends every run with `git rebase -X theirs origin/main`, `-X theirs`
    settles only the hunks that actually conflict, and two runs that each
    scanned channels from the same base leave non-conflicting hunks inside one
    `backups` array - so git keeps both sides' entries.

    So this rule runs in the scan AND in scripts/reconcile-generated-counts.py,
    which is the step that runs after that rebase and before the push. This is
    the only implementation of it; both callers reach it here.

    The first spelling of a route wins, because that is the order the merge
    already decided.
    """
    removed: List[Dict[str, str]] = []
    for card in items:
        if not isinstance(card, dict):
            continue
        backups = card.get("backups")
        if not isinstance(backups, list) or not backups:
            continue
        # Two rules, and they reach different distances on purpose.
        #
        # The exact rule sees every card, including one with a single backup,
        # and it is seeded from the primary - an entry repeating a route's
        # whole configuration is noise wherever it sits.
        #
        # The physical-route rule (URL alone) is left reaching exactly as far
        # as it always has: across the backups of a card that has more than
        # one, seeded from the primary. Widening it to the single-backup case
        # was tried and rejected. 19 published cards pair a `direct_first`
        # primary needing no headers with one `proxy_first` backup on the SAME
        # URL that does - Jago News 24, NRB TV, Mr Bean and sixteen more.
        # Those are two delivery attempts at one address, direct and through
        # the worker, and the proxied one is the whole reason
        # workers/playback-proxy exists. Folding them would have quietly taken
        # a working fallback off nineteen cards.
        exact_seen = {exact_route_identity(card)} - {None}
        route_seen = (
            ({route_identity(card)} - {""}) if len(backups) > 1 else set()
        )
        kept: List[Any] = []
        for row in backups:
            exact = exact_route_identity(row)
            identity = route_identity(row) if isinstance(row, dict) else ""
            repeated = (exact is not None and exact in exact_seen) or bool(
                identity and identity in route_seen
            )
            if repeated:
                removed.append({
                    "name": str(card.get("name") or ""),
                    "category": str(card.get("category") or ""),
                    "url": identity or _url(card),
                    "dropped": str(row.get("name") or "")
                    if isinstance(row, dict) else "",
                })
                continue
            if exact is not None:
                exact_seen.add(exact)
            if identity:
                route_seen.add(identity)
            kept.append(row)
        if len(kept) != len(backups):
            card["backups"] = kept
            if isinstance(card.get("available_link_count"), int):
                card["available_link_count"] = link_count(card)
    return removed
