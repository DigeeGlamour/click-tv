"""Reject sources that a real browser + Cloudflare Worker can never reach.

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


UNREACHABLE_STATUS = "unreachable_from_browser"
UNREACHABLE_NOTE = (
    "Hidden from Click TV: an http:// link on a bare IP address has no viewer "
    "route. The browser blocks it as mixed content and the Cloudflare playback "
    "proxy cannot fetch a raw IP host (error code 1003)."
)


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
        return False
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
