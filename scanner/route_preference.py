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

Two guards keep a stale proof from outliving its usefulness: entries expire
after PREFERENCE_TTL_SECONDS, and a route this scan found unusable is not
promoted even while its proof is still inside that window.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
from typing import Any, Dict, List, Optional

from scanner import route_evidence as rev

#: How long a recorded preference stays in force without being re-verified.
#:
#: Codex asked what stops a route that later breaks from being forced into
#: primary forever - correctly, since nothing did. This is a new policy
#: decision, not a previously-locked one: Phase 0b's persistence.ttl_seconds
#: (1800 s) governs a different mechanism (the escalation counter) and is far
#: too short here - channel scans run roughly every 6 hours, so a 30-minute
#: expiry would make every preference stale before the next scan ever reads it.
#: 14 days is several times the movie-scan cadence (48 h) and dozens of times
#: the channel-scan cadence (6 h), long enough that a route proven once keeps
#: leading across many real scans, short enough that a route nobody re-verifies
#: for two weeks stops being forced and falls back to ordinary ranking.
PREFERENCE_TTL_SECONDS = 14 * 24 * 3600

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
        "recorded_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
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


def _is_stale(entry: Dict[str, Any], *, now: Optional[float] = None) -> bool:
    stamp = str(entry.get("recorded_at") or "")
    if not stamp:
        # An entry from before this field existed. Treated as stale rather than
        # eternal, so an old record cannot outlive verification forever simply
        # by predating the check that would have caught it.
        return True
    try:
        text = stamp[:-1] + "+00:00" if stamp.endswith("Z") else stamp
        recorded = _dt.datetime.fromisoformat(text)
    except ValueError:
        return True
    if recorded.tzinfo is None:
        recorded = recorded.replace(tzinfo=_dt.timezone.utc)
    reference = (
        _dt.datetime.now(_dt.timezone.utc)
        if now is None
        else _dt.datetime.fromtimestamp(now, tz=_dt.timezone.utc)
    )
    return (reference - recorded).total_seconds() > PREFERENCE_TTL_SECONDS


def preferred_route_id(
    kind: str,
    channel: str,
    registry: Optional[Dict[str, Any]] = None,
    *,
    now: Optional[float] = None,
) -> Optional[str]:
    entry = ((registry if registry is not None else load()).get("preferred") or {}).get(
        _key(kind, channel)
    )
    if not isinstance(entry, dict):
        return None
    if _is_stale(entry, now=now):
        # Stale preferences fall back to ordinary ranking rather than forcing a
        # route nobody has re-verified in PREFERENCE_TTL_SECONDS.
        return None
    route_id = str(entry.get("route_id") or "")
    return route_id or None


def _is_healthy_this_scan(stream: Dict[str, Any]) -> bool:
    """Whether THIS scan found the route usable.

    The expiry window alone was not enough. A preference could be well inside
    its 14 days while the route it names had already stopped working, and this
    function is what stops that being promoted over a route that answers today.

    Deliberately permissive about what counts as healthy, and strict about only
    one thing: an explicit denial. `publish_allowed is False` is how every hide
    path in this project records "do not serve this", so a route carrying it is
    the one case where a stale proof must not override a fresh negative.
    Anything else - verified, pending, geo-protected, simply unremarked - is
    left alone, because reading a missing field as unhealthy would refuse
    promotion on most routes and quietly restore the exact behaviour this
    registry exists to fix.
    """
    if not isinstance(stream, dict):
        return False
    if stream.get("publish_allowed") is False:
        return False
    if stream.get("metadata_only") is True:
        # No playable URL to promote.
        return False
    return bool(str(stream.get("url") or "").strip())


def promote_preferred(
    streams: List[Dict[str, Any]],
    kind: str,
    channel: str,
    registry: Optional[Dict[str, Any]] = None,
    *,
    full_pool: Optional[List[Dict[str, Any]]] = None,
    now: Optional[float] = None,
) -> tuple:
    """Move a proven route to the front of `streams`, if it is present.

    Returns (streams, promoted). Without `full_pool`, the list is returned
    unchanged when the proven route is not among `streams` - measured to be a
    real gap: `streams` is usually a slot-limited selection (six by default),
    and a channel with seven or more sources could rank its proven route
    seventh, below that cutoff, where this function never saw it at all.

    `full_pool` is the pre-truncation candidate list the caller ranked `streams`
    from. When the preferred route is present there but not in `streams`, it is
    pulled in and the lowest-ranked current entry is evicted so the result never
    exceeds the caller's own size limit - the route was always a genuine
    candidate the scan found; only the slot count hid it.
    """
    wanted = preferred_route_id(kind, channel, registry, now=now)
    if not wanted:
        return streams, False

    def _matches(stream: Dict[str, Any]) -> bool:
        url = str((stream or {}).get("url") or "")
        if not url or rev.normalize_source_identity(url) != wanted:
            return False
        # A proof from two weeks ago does not outrank this scan finding the
        # route unusable today.
        return _is_healthy_this_scan(stream)

    for index, stream in enumerate(streams or ()):
        if _matches(stream):
            if index == 0:
                return streams, False
            reordered = list(streams)
            reordered.insert(0, reordered.pop(index))
            return reordered, True

    if full_pool:
        for stream in full_pool:
            if not _matches(stream):
                continue
            if not streams:
                return [stream], True
            promoted_list = [stream] + list(streams)
            # Keep the caller's own slot limit: evict the weakest entry rather
            # than growing the selection past what it asked for.
            if len(promoted_list) > len(streams):
                promoted_list = promoted_list[: len(streams)]
            return promoted_list, True

    return streams, False
