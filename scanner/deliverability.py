"""Routes the delivery path provably cannot fetch, whatever the scanner measures.

The scanner verifies a route with Python's own socket, straight out of a GitHub
runner. The audience reaches it through a Cloudflare Worker, from a browser, on
an HTTPS page. Those are different fetchers with different rules, and a route
the scanner can read is not automatically a route a viewer can play - the whole
class of bug this project keeps meeting.

This module holds the rules where the delivery path refuses BY CONSTRUCTION, so
no amount of retrying, rescuing or re-verifying will ever change the answer.
Only rules of that kind belong here. A route that merely failed today is a
measurement, and measurements live in state/measured-playback-failures.json.

The rule, measured on 2026-08-30:

    Cloudflare's `fetch()` refuses a URL whose host is a bare IP literal. The
    edge answers HTTP 403 with body `error code: 1003` - its "Direct IP access
    not allowed" - before the request leaves Cloudflare.

Measured against the live workers, with the site Origin, on three unrelated
addresses, all of which were already on the host allowlist so the Worker's own
gate had passed them:

    http://181.119.215.61:8000/...   Disney Channel  -> 403 error code: 1003
    http://23.237.104.106:8080/...   Dazn 2, 4, 5    -> 403 error code: 1003
    http://66.102.126.10:8000/...    Star Gold       -> 403 error code: 1003

A name-based host through the same worker in the same run returned 200 and a
parseable manifest, so the refusal is the IP literal and nothing else.

There is no route around it for these cards. The site is served over HTTPS, so
an `http://` stream cannot be handed to the video element directly - the browser
blocks the mixed content - which leaves the proxy as the only path, and the
proxy is what refuses. An `https://` bare-IP route fails too, one step later:
the certificate is issued for a name, so the TLS handshake fails in the browser
even when the bytes are there.

So a bare-IP route is undeliverable. It should never carry a Verified badge and
should never be published as a playable route.
"""
from __future__ import annotations

import ipaddress
import re
from typing import Any, Dict, Tuple
from urllib.parse import urlsplit

#: Cloudflare's error code for a direct-IP fetch, quoted so a future reader can
#: match what this module claims against what the edge actually returns.
CLOUDFLARE_DIRECT_IP_ERROR = "error code: 1003"

UNDELIVERABLE_BARE_IP = "undeliverable_bare_ip"

_BRACKETED = re.compile(r"^\[(?P<host>.+)\]$")


def host_of(url: Any) -> str:
    """The host of a stream URL, with the project's `url|header=value` tail cut."""
    text = str(url or "").split("|", 1)[0].strip()
    if not text:
        return ""
    try:
        return (urlsplit(text).hostname or "").strip()
    except ValueError:
        return ""


def is_bare_ip_host(host: Any) -> bool:
    """True when the host is an IP literal rather than a name.

    Both families count. IPv6 arrives bracketed in a URL and `urlsplit` already
    strips the brackets, but the raw form is accepted too so a caller can pass
    a host it read from somewhere else.
    """
    text = str(host or "").strip()
    if not text:
        return False
    match = _BRACKETED.match(text)
    if match:
        text = match.group("host")
    # A trailing zone id ("fe80::1%eth0") is not part of the address.
    text = text.split("%", 1)[0]
    try:
        ipaddress.ip_address(text)
    except ValueError:
        return False
    return True


def undeliverable_reason(url: Any) -> str:
    """Empty when the delivery path can fetch this URL; a reason when it cannot."""
    host = host_of(url)
    if not host:
        return ""
    if is_bare_ip_host(host):
        return (
            f"the playback proxy cannot fetch a bare IP host ({host}): "
            f"Cloudflare refuses a direct-IP fetch with {CLOUDFLARE_DIRECT_IP_ERROR}, "
            "and an HTTPS page cannot load an http:// stream directly, so no "
            "route to the viewer exists"
        )
    return ""


def is_deliverable(url: Any) -> bool:
    return not undeliverable_reason(url)


def check(url: Any) -> Tuple[bool, str]:
    """(deliverable, reason). Convenience for callers that want both."""
    reason = undeliverable_reason(url)
    return (not reason), reason


def mark_undeliverable(item: Dict[str, Any], reason: str) -> Dict[str, Any]:
    """Stamp an item as something no viewer can play, and say why.

    Deliberately mirrors the shape the verifier's other failure paths write, so
    every consumer that already reads `verification_status` sees this one too
    without being taught about it.
    """
    item["verification_status"] = "failed"
    item["verification_mode"] = UNDELIVERABLE_BARE_IP
    item["verification_error"] = reason
    item["verification_badge"] = "Playback Unproven"
    item["verified"] = False
    item["publish_allowed"] = False
    item["playback_unproven"] = True
    item["playback_unproven_reason"] = reason
    item["undeliverable"] = True
    item["response_time_ms"] = 0
    return item
