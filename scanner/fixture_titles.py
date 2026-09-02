"""A card's title is the fixture. Clean it where the published list is final.

The adapters already tidy a name as they parse it, and that is the right place
for it - but it only reaches events the scan parsed this time round. A card
carried through from an earlier publish, or restored from the source cache,
never passes an adapter again, so the front page was still showing

    Indore Hawks vs Chennai Strikers 2 Sep 2026
    Mohali Kings vs Ludhiana Lions 2 Sep 2026

long after the parser stopped producing them. This is the same shape of hole
that let unclassified events through the sport filter: the fix is a sweep over
the final lists, not a second parser.

The other half is a name that never was a fixture. `TBC` was published as an
Upcoming card title with `Uttar Pradesh T20 League, 2026` in its competition
field - the league is announced, the teams are not. `TBC` tells a viewer
nothing, and the competition is the one thing actually known, so the card is
named that instead. Nothing is invented here: the replacement comes from the
card's own competition field, and a placeholder with no competition to fall
back on is left exactly as it arrived.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List

try:
    from scanner.parsers.event_adapters import _tidy_fixture_title
except ImportError:  # pragma: no cover - direct module execution
    from parsers.event_adapters import _tidy_fixture_title  # type: ignore

#: A whole title that names no fixture. Matched against the entire name, so a
#: real team whose name contains one of these words is untouched.
_PLACEHOLDER = re.compile(
    r"^(?:tbc|tba|tbd|n/?a|null|none|unknown|match|fixture|game|event"
    r"|vs\.?|v|versus|to be (?:confirmed|announced|decided)"
    r"|coming soon|no title)$",
    re.IGNORECASE,
)


def is_placeholder(name: Any) -> bool:
    """True when the title names no fixture at all."""
    return bool(_PLACEHOLDER.match(" ".join(str(name or "").split())))


def tidy(name: Any, competition: Any = "") -> str:
    """The title a card should carry, given the name and competition it has."""
    cleaned = _tidy_fixture_title(str(name or ""))
    if not is_placeholder(cleaned):
        return cleaned
    fallback = " ".join(str(competition or "").split())
    return fallback or cleaned


def apply(items: Iterable[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Tidy every card's title in place. Returns one row per title changed.

    `name_from_source` keeps what the feed sent, so a title that came out wrong
    can be traced back to the feed rather than to this sweep.
    """
    changed: List[Dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        before = str(item.get("name") or "")
        after = tidy(before, item.get("competition"))
        if not after or after == before:
            continue
        item["name_from_source"] = before
        item["name"] = after
        changed.append({"was": before, "now": after})
    return changed


__all__ = ["apply", "is_placeholder", "tidy"]
