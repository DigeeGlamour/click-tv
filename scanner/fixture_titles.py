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


#: Both sides of a fixture, however the feed spells the separator.
_SPLIT_SIDES = re.compile(r"\s+(?:vs?\.?|v|versus)\s+", re.IGNORECASE)

#: (competition marker, short side) -> the side's full name.
#:
#: Two feeds abbreviate a side to a word that means a different club
#: elsewhere, so every entry is scoped to the competition it belongs to.
#: `Wolves` is Belfast Wolves in the European T20 Premier League and
#: Wolverhampton in the Premier League, and nothing here may confuse them:
#: an expansion applies only when the card's own competition matches.
#:
#: Observed on the front page 2026-09-02, with the full names taken from
#: the competitions' own schedules:
#:
#:     Wolves vs Castle Rockers      ETPL   Belfast / Edinburgh Castle
#:     Indore Hawks vs Chennai Strikers
#:                                   JITO   Chennai Strikers Kings
TEAM_FULL_NAMES: Dict[str, Dict[str, str]] = {
    "european t20 premier league": {
        "wolves": "Belfast Wolves",
        "castle rockers": "Edinburgh Castle Rockers",
        "guardians": "Dublin Guardians",
        "dockers": "Rotterdam Dockers",
    },
    "jito premier league": {
        "chennai strikers": "Chennai Strikers Kings",
    },
}

#: Competition spellings that mean the same competition as a key above.
COMPETITION_ALIASES: Dict[str, str] = {
    "etpl": "european t20 premier league",
    "european t20": "european t20 premier league",
    "jito": "jito premier league",
    "jito premier league 2026": "jito premier league",
}


def _competition_key(competition: Any) -> str:
    """The competition table key, or "" when this is not one of them."""
    text = " ".join(str(competition or "").split()).casefold()
    if not text:
        return ""
    if text in TEAM_FULL_NAMES:
        return text
    if text in COMPETITION_ALIASES:
        return COMPETITION_ALIASES[text]
    # A season or round tail is decoration, not a different competition.
    for key in TEAM_FULL_NAMES:
        if text.startswith(key):
            return key
    for alias, key in COMPETITION_ALIASES.items():
        if re.match(rf"{re.escape(alias)}(?![a-z0-9])", text):
            return key
    return ""


def expand_sides(name: Any, competition: Any) -> str:
    """Both sides written out, when this competition abbreviates them.

    A side already carrying its full name is left alone, and a side the
    table does not know is left exactly as the feed spelled it - a half
    expanded fixture is still the right fixture.
    """
    text = " ".join(str(name or "").split())
    table = TEAM_FULL_NAMES.get(_competition_key(competition))
    if not text or not table:
        return text
    parts = _SPLIT_SIDES.split(text)
    if len(parts) != 2:
        return text
    separator = _SPLIT_SIDES.search(text).group(0)
    sides = []
    for side in parts:
        full = table.get(side.strip().casefold())
        sides.append(full if full else side.strip())
    return separator.join(sides)


def is_placeholder(name: Any) -> bool:
    """True when the title names no fixture at all."""
    return bool(_PLACEHOLDER.match(" ".join(str(name or "").split())))


def tidy(name: Any, competition: Any = "") -> str:
    """The title a card should carry, given the name and competition it has."""
    cleaned = expand_sides(_tidy_fixture_title(str(name or "")), competition)
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


__all__ = ["apply", "expand_sides", "is_placeholder", "tidy"]
