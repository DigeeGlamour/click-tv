"""A category may be restricted to a named list of channels.

Most categories take whatever the sources and the router hand them. One does
not: the owner curates Indian by hand and asked for exactly forty-nine names,
with nothing else published in that category - not as a card, not in the JSON.

That is a publishing decision, so it lives in configuration rather than in a
one-off edit to data/channels/indian.json. A hand-edited catalogue file survives
until the next scan rebuilds it from source, which is a few hours; a list in
config/channel-categories.json survives every scan.

Deliberately narrow:

  * A category with no `publish_allowlist` entry behaves exactly as before -
    this cannot quietly start filtering Bangla or Sports.
  * A name that is not on the list is dropped from that category, not moved to
    Other. Moving it would leave the card the owner asked to remove.
  * Matching is on the published card name, case-folded with runs of whitespace
    collapsed, so "COLORS BANGLA" and "Colors Bangla" are one name and
    "B4U MOVIES" matches "B4U Movies". Nothing cleverer: an alias-based match
    would quietly re-admit "Enter 10 Bangla", "Enter10 Bangla" and
    "Enterr 10 Bangla" alongside the "Enterr10 Bangla" that was asked for, and
    those extra spellings are exactly what the list exists to remove.
"""
from __future__ import annotations

import json
import os
import re
import threading
from typing import Any, Dict, List, Optional, Set

DEFAULT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config",
    "channel-categories.json",
)

#: Key in config/channel-categories.json holding {category: [name, ...]}.
CONFIG_KEY = "publish_allowlist"

_LOCK = threading.Lock()
_CACHE: Optional[Dict[str, Set[str]]] = None
_CACHE_ORDER: Optional[Dict[str, Dict[str, int]]] = None
_CACHE_PATH: Optional[str] = None


def normalize(name: Any) -> str:
    """The form two spellings of one name have to agree on."""
    return re.sub(r"\s+", " ", str(name or "").strip()).casefold()


def _read(path: str) -> Any:
    """Returns ({category: {name}}, {category: {name: position}})."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return {}, {}
    raw = payload.get(CONFIG_KEY) if isinstance(payload, dict) else None
    if not isinstance(raw, dict):
        return {}, {}
    found: Dict[str, Set[str]] = {}
    order: Dict[str, Dict[str, int]] = {}
    for category, names in raw.items():
        if not isinstance(names, list):
            continue
        positions: Dict[str, int] = {}
        for name in names:
            key = normalize(name)
            if key and key not in positions:
                positions[key] = len(positions)
        if positions:
            found[normalize(category)] = set(positions)
            order[normalize(category)] = positions
    return found, order


def load(path: Optional[str] = None) -> Dict[str, Set[str]]:
    global _CACHE, _CACHE_ORDER, _CACHE_PATH
    target = path or DEFAULT_PATH
    with _LOCK:
        if _CACHE_PATH == target and _CACHE is not None:
            return _CACHE
        _CACHE, _CACHE_ORDER = _read(target)
        _CACHE_PATH = target
        return _CACHE


def order_of(category: Any, path: Optional[str] = None) -> Dict[str, int]:
    """{normalised name: position in the list} for a curated category.

    The list is not just a filter, it is the running order the owner wrote it
    in. Published alphabetically instead, Star Jalsha - the first name they
    asked for - came out thirty-first, behind &TV and four 9X music channels.
    """
    load(path)
    return dict((_CACHE_ORDER or {}).get(normalize(category)) or {})


def reset_cache() -> None:
    """Forget the config. Tests and a second scan in one process need this."""
    global _CACHE, _CACHE_ORDER, _CACHE_PATH
    with _LOCK:
        _CACHE = None
        _CACHE_ORDER = None
        _CACHE_PATH = None


def is_restricted(category: Any, path: Optional[str] = None) -> bool:
    return normalize(category) in load(path)


def allowed_names(category: Any, path: Optional[str] = None) -> Set[str]:
    return set(load(path).get(normalize(category)) or ())


def is_allowed(category: Any, name: Any, path: Optional[str] = None) -> bool:
    """Whether this card may be published in this category.

    True for every category that declares no list, which is every category but
    the curated one.
    """
    allowed = load(path).get(normalize(category))
    if allowed is None:
        return True
    return normalize(name) in allowed


def apply(
    cards: List[Dict[str, Any]],
    category: Any,
    path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """The cards this category may publish, in the order the list gives them."""
    if not is_restricted(category, path):
        return list(cards)
    kept = [
        card for card in cards
        if isinstance(card, dict) and is_allowed(category, card.get("name"), path)
    ]
    return in_list_order(kept, category, path)


def in_list_order(
    cards: List[Dict[str, Any]],
    category: Any,
    path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Sort a curated category into the order its list was written in.

    A name that somehow reached the category without being on the list keeps
    its relative place at the end rather than being dropped here - dropping is
    `apply`'s job, and a sort that silently deleted a card would be a far worse
    surprise than one out of order.
    """
    positions = order_of(category, path)
    if not positions:
        return list(cards)
    tail = len(positions)
    return sorted(
        cards,
        key=lambda card: positions.get(
            normalize((card or {}).get("name")), tail
        ),
    )


def rejected(
    cards: List[Dict[str, Any]],
    category: Any,
    path: Optional[str] = None,
) -> List[str]:
    """Names this category refused, so a scan can report them rather than
    dropping them silently."""
    if not is_restricted(category, path):
        return []
    return [
        str(card.get("name") or "")
        for card in cards
        if isinstance(card, dict) and not is_allowed(category, card.get("name"), path)
    ]


def missing_from(
    cards: List[Dict[str, Any]],
    category: Any,
    path: Optional[str] = None,
) -> List[str]:
    """Allowed names that produced no card this run.

    The list is what the owner asked for; this says which of it the sources did
    not deliver, which is the only honest way to report a curated category.
    """
    if not is_restricted(category, path):
        return []
    present = {
        normalize(card.get("name")) for card in cards if isinstance(card, dict)
    }
    return sorted(allowed_names(category, path) - present)
