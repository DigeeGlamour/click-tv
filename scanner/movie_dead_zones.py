"""A CDN zone its provider has switched off is gone, and says so itself.

ধারা ৪.১ keeps 403 off the terminal list, and rightly: a 403 from a GitHub
runner is usually our vantage, not the file. But one 403 is not ours to
doubt. A BunnyCDN pull zone that is suspended or deleted answers every path,
from every vantage, with BunnyCDN's own "unconfigured" landing page - an HTML
page about the zone, not the file. On 2026-10-21 four such zones held 938
previously live streams; the gate counted them as unexplained loss and kept
the whole movie catalogue at its 2026-09-24 publish.

So this asks the zone, not the file: one request to the zone's root. Only
the provider's own zone-gone page counts - the exact markers below, on the
provider's own domain. Any other answer, a timeout, or a host on any other
domain is no opinion, and the stream stays exactly as unexplained as before.
"""
from __future__ import annotations

import urllib.error
import urllib.request
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlsplit

#: Hosts whose provider serves a recognisable zone-gone page.
PROVIDER_SUFFIXES: Tuple[str, ...] = (".b-cdn.net",)
#: BunnyCDN's landing page for a zone that is suspended or not configured.
ZONE_GONE_MARKERS: Tuple[str, ...] = (
    "assets/landingpage/css/unconfigured.css",
    "Domain suspended or not configured",
)
#: Reason on the gate's hard list: what the URL serves is confirmed not media.
REASON = "confirmed_not_media"
MAX_ZONES_PER_RUN = 40


def _fetch_root(host: str) -> Tuple[int, str]:
    request = urllib.request.Request(
        f"https://{host}/", headers={"User-Agent": "Mozilla/5.0 click-tv-zone-check"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, response.read(4096).decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return error.code, error.read(4096).decode("utf-8", "replace")


def zone_gone(host: str, fetch: Callable[[str], Tuple[int, str]] = _fetch_root) -> bool:
    if not host or not host.endswith(PROVIDER_SUFFIXES):
        return False
    try:
        status, body = fetch(host)
    except Exception:  # noqa: BLE001 - no answer is no opinion
        return False
    return status == 403 and any(marker in body for marker in ZONE_GONE_MARKERS)


def _inventory_url(line: Any) -> str:
    parts = str(line or "").split("|")
    return parts[-1] if len(parts) >= 4 else ""


def terminal_records(
    previous_inventory: Iterable[Any],
    fetch: Callable[[str], Tuple[int, str]] = _fetch_root,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Terminal evidence for every previously live stream on a dead zone."""
    by_host: Dict[str, List[Tuple[str, str]]] = {}
    for line in previous_inventory or ():
        url = _inventory_url(line)
        host = (urlsplit(url).hostname or "").lower() if url else ""
        if host.endswith(PROVIDER_SUFFIXES):
            key = str(line).split("|")[2]
            by_host.setdefault(host, []).append((url, key))

    gone: List[str] = []
    for host in sorted(by_host, key=lambda h: -len(by_host[h]))[:MAX_ZONES_PER_RUN]:
        if zone_gone(host, fetch):
            gone.append(host)

    records = [{"url": url, "reason": REASON, "zone": host}
               for host in gone for url, key in by_host[host]]
    return records, {"zones_checked": min(len(by_host), MAX_ZONES_PER_RUN),
                     "zones_gone": gone, "streams_on_gone_zones": len(records)}
