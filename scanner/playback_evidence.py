"""Routes a real browser measured and could not play.

A card whose route answers HTTP 200 and whose manifest parses looks identical
to a card that plays. Star Jalsha is the case that forced this: every one of
its four reachable routes was measured over two 120-second sessions and none
reached the floor -

    cache.devm3u.top      15.56 s of media in 120        (under the 115 s floor)
    rgkkw.live            fatal: audio/mp4;codecs=ac-3 unsupported
    premiumtvs.space      the same AC-3 failure, same provider
    catchup.yuppcdn.net   manifest load error

- yet the card was published looking exactly like a working channel.

The honest answer is neither to hide the channel nor to present it as working.
This marks the card: the route stays, the badge says the playback is unproven,
and the reason travels with it. Nothing here hides a card, changes
publish_allowed, or demotes another route - a viewer who can play it still can,
and an audit can see what was measured.

Keyed by route rather than by channel, for the reason the confirmed-failure
ledger had to learn: a measurement belongs to the URL it was taken on, so a
channel recovers the moment its route changes.

Every measurement also carries the vantage it was taken from, because a
measurement without one turned out to be a claim the project could not support.
The four rows above were all taken from a GitHub Actions runner in a US
datacentre. Re-measured from a residential connection in Bangladesh - which is
where this site's viewers are - `cache.devm3u.top` reached 112.2 s and then a
full pass over two 120 s sessions on 2026-08-29. The same route, the same
harness, the opposite verdict.

So a failure never outranks a later pass. `record_proof` marks the failing row
superseded instead of deleting it: the history stays readable, and
`unproven_reason` stops returning a reason the newer measurement contradicts. A
row with no vantage is a legacy row and is treated as `unknown`, not as global.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

DEFAULT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "state",
    "measured-playback-failures.json",
)

BADGE = "Playback Unproven"


def load(path: Optional[str] = None) -> Dict[str, Any]:
    target = path or DEFAULT_PATH
    try:
        with open(target, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return {"version": 1, "routes": {}}
    if not isinstance(payload, dict) or not isinstance(payload.get("routes"), dict):
        return {"version": 1, "routes": {}}
    return payload


def _route_key(url: Any) -> str:
    from scanner import route_evidence as rev

    text = str(url or "").split("|", 1)[0].strip()
    return rev.normalize_source_identity(text) if text else ""


#: What a row means when it does not say where it was measured. Every row
#: written before 2026-08-29 was taken from a US datacentre runner, but the file
#: did not say so, and guessing on its behalf would be inventing evidence.
UNKNOWN_VANTAGE = "unknown"


def _write(store: Dict[str, Any], path: Optional[str]) -> bool:
    store["note"] = (
        "Routes a real browser measured and could not play, each with the "
        "vantage it was measured from. Marks the card; never hides it, never "
        "changes publish_allowed, never demotes another route. Keyed by route, "
        "so a channel recovers when its route changes - or when a later "
        "measurement from any vantage passes, which supersedes the failure "
        "rather than deleting it."
    )
    target = path or DEFAULT_PATH
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as handle:
            json.dump(store, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
    except OSError:
        return False
    return True


def record(
    url: str,
    reason: str,
    *,
    sessions: int,
    media_progress_seconds: List[Any],
    window_seconds: float,
    evidence_report: str,
    vantage: str = "",
    path: Optional[str] = None,
) -> bool:
    """Record a measured failure. Returns whether it was written.

    A failure never overwrites a pass that was recorded for the same route
    later: `superseded_by` on the existing row is the newer measurement, and
    re-recording the old failure on top of it would erase the only thing that
    says the route works somewhere.
    """
    key = _route_key(url)
    if not key or not str(reason or "").strip():
        return False
    from scanner import route_evidence as rev

    store = load(path)
    existing = (store.get("routes") or {}).get(key)
    if isinstance(existing, dict) and existing.get("superseded_by"):
        return False
    store["routes"][key] = {
        "route_id": key,
        "url_public_template": rev.redact_public_template(str(url)),
        "reason": str(reason)[:400],
        "sessions": int(sessions),
        "media_progress_seconds": media_progress_seconds,
        "window_seconds": window_seconds,
        "evidence_report": evidence_report,
        "vantage": str(vantage or "").strip() or UNKNOWN_VANTAGE,
    }
    return _write(store, path)


def record_proof(
    url: str,
    *,
    vantage: str,
    sessions: int,
    media_progress_seconds: List[Any],
    window_seconds: float,
    evidence_report: str,
    path: Optional[str] = None,
) -> bool:
    """Mark a recorded failure superseded by a later pass on the same route.

    Does nothing when the route has no recorded failure - this file is a list
    of failures, and a route that plays does not belong in it. What it will not
    do is delete the failing row: an audit has to be able to see that the route
    once measured unplayable, from where, and what contradicted it.
    """
    key = _route_key(url)
    if not key:
        return False
    store = load(path)
    existing = (store.get("routes") or {}).get(key)
    if not isinstance(existing, dict):
        return False
    existing["superseded_by"] = {
        "verdict": "proven",
        "vantage": str(vantage or "").strip() or UNKNOWN_VANTAGE,
        "sessions": int(sessions),
        "media_progress_seconds": media_progress_seconds,
        "window_seconds": window_seconds,
        "evidence_report": evidence_report,
    }
    store["routes"][key] = existing
    return _write(store, path)


def unproven_reason(url: Any, path: Optional[str] = None) -> str:
    """The measured reason this route cannot be called working, or "".

    Empty once a later measurement passed on the same route, wherever it was
    taken: a route that plays for somebody is not unproven, and a stale verdict
    from one datacentre is not a fact about every viewer.
    """
    key = _route_key(url)
    if not key:
        return ""
    record_for_route = (load(path).get("routes") or {}).get(key)
    if not isinstance(record_for_route, dict):
        return ""
    if record_for_route.get("superseded_by"):
        return ""
    return str(record_for_route.get("reason") or "")


def vantage_of(url: Any, path: Optional[str] = None) -> str:
    """Where the recorded failure for this route was measured, or ""."""
    key = _route_key(url)
    if not key:
        return ""
    record_for_route = (load(path).get("routes") or {}).get(key)
    if not isinstance(record_for_route, dict):
        return ""
    return str(record_for_route.get("vantage") or UNKNOWN_VANTAGE)
