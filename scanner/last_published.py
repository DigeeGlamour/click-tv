"""What the previous published catalogue actually carried, indexed by route.

The scanner verifies from a GitHub Actions runner in a US datacentre while the
audience is in Bangladesh, and a single unlucky answer from that one egress used
to delete a working channel outright. Measured on 2026-08-29 against the cards
that were published in 150d3487c and are not published now, re-probed from a
Bangladeshi residential connection:

    BTV News        CI: HTTP 429  ->  here: HTTP 200, 1080p
    My TV           CI: HTTP 429  ->  here: HTTP 200
    Anand TV        CI: timeout   ->  here: HTTP 200, 1080p
    Praise TV       CI: timeout   ->  here: HTTP 200,  720p

429 means "too many requests". It is a statement about the asker, not about the
stream, and neither is a socket timeout. The verifier already knows this for
movies; this module supplies the one fact the TV path was missing - whether the
route had ever been published - so a transient answer can be held pending
instead of being treated as proof the channel is gone.

Read-only, loaded once, and it never invents a route: a URL that is not in the
snapshot gets no protection at all.
"""
from __future__ import annotations

import glob
import json
import os
import threading
from typing import Any, Dict, Optional

DEFAULT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "state",
    "last-good",
)

_LOCK = threading.Lock()
_CACHE: Dict[str, Dict[str, Any]] = {}
_CACHE_DIR: Optional[str] = None


def _route_key(url: Any) -> str:
    from scanner import route_evidence as rev

    text = str(url or "").split("|", 1)[0].strip()
    return rev.normalize_source_identity(text) if text else ""


def _index(directory: str) -> Dict[str, Dict[str, Any]]:
    """Every route in the snapshot - primaries and backups alike."""
    found: Dict[str, Dict[str, Any]] = {}
    for path in sorted(glob.glob(os.path.join(directory, "*.json"))):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError):
            continue
        for card in payload.get("channels") or ():
            if not isinstance(card, dict):
                continue
            routes = [card] + [
                backup for backup in (card.get("backups") or ())
                if isinstance(backup, dict)
            ]
            for route in routes:
                key = _route_key(route.get("url"))
                if not key or key in found:
                    continue
                found[key] = {
                    "name": str(card.get("name") or ""),
                    "category": str(card.get("category") or payload.get("category") or ""),
                    "transient_rescue_count": int(
                        route.get("transient_rescue_count")
                        or card.get("transient_rescue_count")
                        or 0
                    ),
                }
    return found


def load(directory: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    global _CACHE, _CACHE_DIR
    target = directory or DEFAULT_DIR
    with _LOCK:
        if _CACHE_DIR == target:
            return _CACHE
        _CACHE = _index(target)
        _CACHE_DIR = target
        return _CACHE


def reset_cache() -> None:
    """Forget the snapshot. Tests and a second scan in one process need this."""
    global _CACHE, _CACHE_DIR
    with _LOCK:
        _CACHE = {}
        _CACHE_DIR = None


def entry_for(url: Any, directory: Optional[str] = None) -> Dict[str, Any]:
    """The snapshot's record for this exact route, or {}."""
    key = _route_key(url)
    if not key:
        return {}
    return dict(load(directory).get(key) or {})


def was_published(url: Any, directory: Optional[str] = None) -> bool:
    return bool(entry_for(url, directory))


def rescue_count(url: Any, directory: Optional[str] = None) -> int:
    """How many consecutive scans have already held this route pending."""
    return int((entry_for(url, directory) or {}).get("transient_rescue_count") or 0)
