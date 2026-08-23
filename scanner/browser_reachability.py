"""Decide whether a viewer can actually play an item, and block the rest.

Two independent gates live here, both answering the same question the network
verifier cannot answer on its own — "will this play in a real viewer's browser?"

1.  ``item_is_browser_reachable`` — structural. Some URLs have no viewer route
    at all, whatever the upstream server says (see the note below).
2.  ``item_is_proven_live`` — evidential. Only a status that was proven in the
    current scan run may be published. ``stale_last_good`` in particular means
    "verification failed, republishing yesterday's link anyway", which is
    exactly how dead links reach the site.

The network verifier runs from Python on a GitHub runner, where a plain
``http://<ip-address>/stream.m3u8`` answers perfectly.  The published site does
not have that freedom, and two hard limits apply to every viewer:

1.  The site is served over HTTPS, so the browser refuses to load an ``http://``
    media URL directly (mixed content).  The only remaining route is the
    playback proxy.
2.  The playback proxy is a Cloudflare Worker, and ``fetch()`` inside a Worker
    refuses to connect to a bare IPv4/IPv6 literal.  Cloudflare answers with
    HTTP 403 and ``error code: 1003``.

An ``http://`` URL whose host is a bare IP therefore has no working route at
all: direct is blocked by the browser and proxied is blocked by Cloudflare.  It
was still being published with a green "Verified" badge because the scanner
only ever measured the Python route.

Non-standard ports are fine — this was measured against the live proxy, where
``http://host.example:7000/...`` and ``:8081`` and ``:3500`` all streamed
normally.  Only the bare-IP restriction is real, so only that is enforced.
"""

from __future__ import annotations

import ipaddress
from typing import Any, Dict, Iterable, List, Tuple
from urllib.parse import urlsplit
from scanner.visibility_audit import audit_hide_safe


UNREACHABLE_STATUS = "unreachable_from_browser"
UNREACHABLE_NOTE = (
    "Hidden from Click TV: an http:// link on a bare IP address has no viewer "
    "route. The browser blocks it as mixed content and the Cloudflare playback "
    "proxy cannot fetch a raw IP host (error code 1003)."
)

UNPROVEN_NOTE = (
    "Hidden from Click TV: this link was not proven playable in the current "
    "scan run. It stays in scanner state and returns automatically as soon as "
    "one verification succeeds again."
)

# Proven in this run, or curated by hand — safe to publish.
PROVEN_LIVE_STATUSES = frozenset({
    "verified",
    "verified_global",
    "verified_bd",
    "verified_proxy",
    "manual_trusted",
    # The strongest evidence this project can produce: two independent real
    # browser sessions, 120 s apart, each playing the card to the full PASS
    # floor (startup <= 10 s, media progress >= 115 s, cumulative stall <= 5 s,
    # every announced render track progressing). Every other status here is a
    # network or manifest observation; this one is decoded frames. See
    # reports/phase1-sustained-playback.json and
    # scripts/promote-proven-channels.py.
    "verified_sustained_playback",
})

# Not proven from a GitHub runner, but genuinely reachable for the Bangladeshi
# audience the site is built for: these hosts geo-block everything outside BD.
# Dropping them would remove working channels, so they are allowed by default
# and can be turned off with publish_gate.allow_geo_pending.
GEO_PENDING_STATUSES = frozenset({
    "geo_pending",
    "bd_protected_pending",
})


def requires_same_run_proof(item: Dict[str, Any], apply_to_movies: bool = False) -> bool:
    """Which items the same-run-proof rule applies to.

    Live TV only, by default, and that split is measured rather than assumed.

    For channels the status is a clean signal: sampling the live catalogue
    through the real player path gave 26/29 playable for ``verified_global``
    against 1/11 for ``stale_last_good``.

    For movies it is not. The same sampling gave 0/12 for ``verified_global`` —
    identical to every pending status — because a movie plays as a direct
    browser download, not through the proxy the sample could measure. Applying
    the rule there would have deleted 774 of 2030 titles on no evidence.
    """
    if apply_to_movies:
        return True
    kind = str(item.get("content_kind") or "").strip().casefold()
    pipeline = str(item.get("source_pipeline") or "").strip().casefold()
    return kind == "live_tv" or pipeline == "tv"


def item_is_proven_live(item: Dict[str, Any], allow_geo_pending: bool = True) -> bool:
    """True when the item carries real evidence that a viewer can play it.

    ``verified`` is the authoritative flag: every success path sets it, and the
    preserved-last-good path deliberately leaves it False while still setting
    ``publish_allowed``. So ``publish_allowed`` alone is never taken as proof.
    """
    if item.get("verified") is True:
        return True

    status = str(item.get("verification_status") or "").strip().casefold()
    if status in PROVEN_LIVE_STATUSES:
        return True
    if allow_geo_pending and status in GEO_PENDING_STATUSES:
        return True
    # Manual catalogue entries carry their own trust and are never network
    # verified, so an empty status on a manual card is not a failure signal.
    if not status and item.get("manual_source") is True:
        return True
    return False


def mark_unproven_items(
    items: Iterable[Dict[str, Any]],
    kind: str = "channel",
    allow_geo_pending: bool = True,
) -> Tuple[int, List[Dict[str, str]]]:
    """Hide anything not proven live this run; return (hidden_count, rows)."""
    hidden = 0
    report: List[Dict[str, str]] = []

    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("publish_allowed") is False:
            continue
        if item_is_proven_live(item, allow_geo_pending):
            continue

        status = str(item.get("verification_status") or "").strip()
        audit_hide_safe(
            "browser_reachability.mark_unproven_items",
            item,
            kind=kind,
            reason=status or "unknown_status",
        )
        item["network_verification_status"] = status
        item["publish_allowed"] = False
        item["player_visibility"] = "hidden_unproven_this_run"
        item["verification_note"] = UNPROVEN_NOTE
        hidden += 1
        report.append({
            "kind": kind,
            "name": str(item.get("name") or item.get("title") or ""),
            "category": str(item.get("category") or ""),
            "url": str(item.get("url") or ""),
            "reason": status or "unknown_status",
        })

    return hidden, report


def _is_bare_ip_host(host: str) -> bool:
    candidate = str(host or "").strip().strip("[]")
    if not candidate:
        return False
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        return False
    return True


def route_is_browser_reachable(url: Any) -> bool:
    """True when at least one viewer route exists for this URL."""
    text = str(url or "").split("|", 1)[0].strip()
    if not text:
        return False
    try:
        parts = urlsplit(text)
    except ValueError:
        return False

    scheme = (parts.scheme or "").lower()
    if scheme not in {"http", "https"}:
        return False

    host = parts.hostname or ""
    if not host:
        return False

    # https:// always has the direct route, whatever the host looks like.
    if scheme == "https":
        return True

    # http:// survives only through the proxy, which cannot reach a raw IP.
    return not _is_bare_ip_host(host)


def _item_routes(item: Dict[str, Any]) -> List[str]:
    routes = [item.get("url")]
    for field in ("backups", "standby", "links"):
        for entry in item.get(field) or []:
            if isinstance(entry, dict):
                routes.append(entry.get("url"))
            elif isinstance(entry, str):
                routes.append(entry)
    return [str(route) for route in routes if route]


def item_is_browser_reachable(item: Dict[str, Any]) -> bool:
    routes = _item_routes(item)
    if not routes:
        # No link at all is not this gate's decision. Upcoming matches are
        # published on purpose without one ("stream link will be added before
        # the match starts") and carry verification_status "metadata_only";
        # judging them here silently emptied the Upcoming tab.
        return True
    return any(route_is_browser_reachable(route) for route in routes)


def prune_unreachable_routes(item: Dict[str, Any]) -> int:
    """Drop dead backups so a partially reachable item keeps only live routes."""
    removed = 0
    for field in ("backups", "standby"):
        entries = item.get(field)
        if not isinstance(entries, list):
            continue
        kept = []
        for entry in entries:
            url = entry.get("url") if isinstance(entry, dict) else entry
            if url and not route_is_browser_reachable(url):
                removed += 1
                continue
            kept.append(entry)
        if removed:
            item[field] = kept
    return removed


def mark_browser_unreachable(
    items: Iterable[Dict[str, Any]],
    kind: str = "channel",
) -> Tuple[int, List[Dict[str, str]]]:
    """Hide items with no viewer route; return (hidden_count, report_rows)."""
    hidden = 0
    report: List[Dict[str, str]] = []

    for item in items:
        if not isinstance(item, dict):
            continue

        primary_reachable = route_is_browser_reachable(item.get("url"))
        if primary_reachable:
            prune_unreachable_routes(item)
            continue

        if item_is_browser_reachable(item):
            # A backup can still carry this card. Promote the first live route.
            for entry in item.get("backups") or []:
                url = entry.get("url") if isinstance(entry, dict) else entry
                if url and route_is_browser_reachable(url):
                    item["url"] = url
                    if isinstance(entry, dict):
                        for field in ("header_profile", "stream_type", "proxy_mode"):
                            if entry.get(field):
                                item[field] = entry[field]
                    break
            prune_unreachable_routes(item)
            continue

        prior_status = str(item.get("verification_status") or "").strip()
        if prior_status and prior_status != UNREACHABLE_STATUS:
            item["network_verification_status"] = prior_status
        audit_hide_safe(
            "browser_reachability.hide_browser_unreachable",
            item,
            kind=kind,
            reason=prior_status or "browser_unreachable",
        )
        item["publish_allowed"] = False
        item["player_verified"] = False
        item["player_visibility"] = "hidden_browser_unreachable"
        item["verification_status"] = UNREACHABLE_STATUS
        item["verification_note"] = UNREACHABLE_NOTE
        hidden += 1
        report.append({
            "kind": kind,
            "name": str(item.get("name") or item.get("title") or ""),
            "category": str(item.get("category") or ""),
            "url": str(item.get("url") or ""),
            "reason": "http_bare_ip_host",
        })

    return hidden, report
