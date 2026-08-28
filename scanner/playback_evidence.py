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


def record(
    url: str,
    reason: str,
    *,
    sessions: int,
    media_progress_seconds: List[Any],
    window_seconds: float,
    evidence_report: str,
    path: Optional[str] = None,
) -> bool:
    """Record a measured failure. Returns whether it was written."""
    key = _route_key(url)
    if not key or not str(reason or "").strip():
        return False
    from scanner import route_evidence as rev

    store = load(path)
    store["routes"][key] = {
        "route_id": key,
        "url_public_template": rev.redact_public_template(str(url)),
        "reason": str(reason)[:400],
        "sessions": int(sessions),
        "media_progress_seconds": media_progress_seconds,
        "window_seconds": window_seconds,
        "evidence_report": evidence_report,
    }
    store["note"] = (
        "Routes a real browser measured and could not play. Marks the card; "
        "never hides it, never changes publish_allowed, never demotes another "
        "route. Keyed by route, so a channel recovers when its route changes."
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


def unproven_reason(url: Any, path: Optional[str] = None) -> str:
    """The measured reason this route cannot be called working, or ""."""
    key = _route_key(url)
    if not key:
        return ""
    record_for_route = (load(path).get("routes") or {}).get(key)
    if not isinstance(record_for_route, dict):
        return ""
    return str(record_for_route.get("reason") or "")
