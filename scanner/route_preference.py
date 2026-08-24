"""Routes that passed the 120 s acceptance, and which one a channel should lead with.

The Zee Bangla fix was written straight into data/channels/indian.json, and the
script that wrote it says in its own comment that the next scan rebuilds cards
from their sources and erases whatever was written on them. So that fix had a
shelf life of one scan - the same mistake that undid the seven restored channels,
made again one step further along.

`sustained_proof` already stops a proven channel being hidden. It says nothing
about which of a channel's routes should be the primary, which is the part that
decides whether a viewer sees a working stream or a stuttering one.

This closes that. A route with two independent 120 s passes outranks every
verification tier the scanner can assign, because those tiers are network
observations and this is decoded frames. The registry lives outside the cards, so
a rebuild reads it rather than erasing it.

Deliberately narrow: it can only PROMOTE a route within a channel's existing
candidates. It cannot introduce a route, remove one, or hide anything.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from scanner import route_evidence as rev

DEFAULT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "state",
    "route-preference.json",
)


def load(path: Optional[str] = None) -> Dict[str, Any]:
    """Read the registry. Unreadable means no preferences, never an error."""
    target = path or DEFAULT_PATH
    try:
        with open(target, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return {"version": 1, "preferred": {}}
    if not isinstance(payload, dict) or not isinstance(payload.get("preferred"), dict):
        return {"version": 1, "preferred": {}}
    return payload


def _key(kind: str, name: str) -> str:
    return "{0}|{1}".format(
        str(kind or "").strip().casefold(), str(name or "").strip().casefold()
    )


def record(
    kind: str,
    channel: str,
    route_url: str,
    evidence: Dict[str, Any],
    *,
    path: Optional[str] = None,
) -> tuple:
    """Register a proven route as this channel's preferred primary.

    Refuses anything short of two full passes, so the registry cannot be seeded
    with a claim - the same floor every other promotion here answers to.
    """
    if not str(channel or "").strip() or not str(route_url or "").strip():
        return False, "channel or route missing"
    try:
        passes = int(evidence.get("pass_count"))
    except (TypeError, ValueError):
        return False, "evidence records no pass count"
    if passes < rev.REQUIRED_FRESH_SESSIONS:
        return False, (
            f"only {passes} full PASS; {rev.REQUIRED_FRESH_SESSIONS} independent "
            "sessions required"
        )
    if not evidence.get("window_seconds"):
        return False, "evidence records no measurement window"

    target = path or DEFAULT_PATH
    registry = load(target)
    registry.setdefault("preferred", {})[_key(kind, channel)] = {
        "kind": kind,
        "channel": channel,
        # Stored normalised so a rotating cache-buster does not look like a
        # different route on the next scan.
        "route_id": rev.normalize_source_identity(route_url),
        "url_public_template": rev.redact_public_template(route_url),
        "pass_count": passes,
        "window_seconds": evidence.get("window_seconds"),
        "media_progress_seconds": evidence.get("media_progress_seconds"),
        "cumulative_stall_seconds": evidence.get("cumulative_stall_seconds"),
        "browser_profile": evidence.get("browser_profile"),
        "evidence_report": evidence.get("evidence_report"),
    }
    registry["note"] = (
        "A route here passed two independent 120 s browser sessions. It outranks "
        "the scanner's verification tiers, which are network observations, "
        "because this is decoded frames. Promotion only - this registry cannot "
        "introduce, remove or hide a route."
    )
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as handle:
            json.dump(registry, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
    except OSError as error:
        return False, f"could not write registry: {error}"
    return True, "recorded"


def preferred_route_id(
    kind: str, channel: str, registry: Optional[Dict[str, Any]] = None
) -> Optional[str]:
    entry = ((registry if registry is not None else load()).get("preferred") or {}).get(
        _key(kind, channel)
    )
    if not isinstance(entry, dict):
        return None
    route_id = str(entry.get("route_id") or "")
    return route_id or None


def promote_preferred(
    streams: List[Dict[str, Any]],
    kind: str,
    channel: str,
    registry: Optional[Dict[str, Any]] = None,
) -> tuple:
    """Move a proven route to the front of `streams`, if it is present.

    Returns (streams, promoted). The list is returned unchanged when the proven
    route is not among the candidates - this never adds one, because a route the
    scanner did not find is a route this scan cannot vouch for.
    """
    wanted = preferred_route_id(kind, channel, registry)
    if not wanted or not streams:
        return streams, False
    for index, stream in enumerate(streams):
        url = str((stream or {}).get("url") or "")
        if not url:
            continue
        if rev.normalize_source_identity(url) == wanted:
            if index == 0:
                return streams, False
            reordered = list(streams)
            reordered.insert(0, reordered.pop(index))
            return reordered, True
    return streams, False
