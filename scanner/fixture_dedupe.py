"""One match, one card - even when two feeds spell the teams differently.

The merge folds two cards together when their participants match, and that
match is exact. So a fixture arriving from two sources under two spellings
publishes twice, side by side, and a viewer sees the same match offered as two
different things:

    Cagliari vs Inter                    Cagliari Vs Inter Milan
    Argentinos JRS vs Aldosivi           Argentinos Juniors Vs Aldosivi
    Deportivo vs Valencia                Deportivo de A Coruna Vs Valencia
    Independ Rivadavia vs Racing Club    Independiente Rivadavia Vs Racing Club

Every pair above shares a kickoff instant to the second and one side spelled
identically; only the other side differs, and it differs the way a feed
abbreviates rather than the way two clubs differ - a truncation ("Independ" for
"Independiente"), a dropped qualifier ("Inter" for "Inter Milan"), or an
initialism ("JRS" for "Juniors").

That is the whole rule, and it is deliberately narrow:

    the same kickoff, to the second
  + one side the same club beyond doubt - identical once the corporate suffix
    one feed keeps and another drops is set aside, so "Baniyas" and
    "Baniyas SC" anchor a fold and nothing looser does
  + the other side a truncation, an initialism, or a longer form of its pair

Two genuinely different fixtures do not clear that bar. "Manchester United vs
Arsenal" and "Manchester City vs Arsenal" share a kickoff and a side, and
"United" is not a truncation of "City", so they stay apart - which is the case
that makes a looser rule dangerous, because both sides would look equally
mergeable to anything that only counted matching words.

The richer card wins and absorbs the other's routes, so folding never costs a
stream: a card with five channels and a card with one become one card with the
five, plus whatever of the one it did not already have.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

_SPLIT = re.compile(r"\s+(?:vs?\.?|v|versus)\s+", re.IGNORECASE)
#: Decoration feeds add to a title that says nothing about which fixture it is.
_NOISE = re.compile(
    r"\b(?:live|hd|fhd|uhd|sd|4k|full\s*match|\d{1,2}\s+\w{3}\s+\d{4})\b",
    re.IGNORECASE)
#: Corporate suffixes that one feed keeps and another drops.
_SUFFIX = re.compile(
    r"\b(?:fc|afc|sc|cf|ac|as|ss|ssc|cd|ud|sd|club|futbol|football)\b",
    re.IGNORECASE)
_PUNCT = re.compile(r"[^\w\s]+", re.UNICODE)

#: Below this a "truncation" is just a short word that happens to start the
#: same way - "San" against "Santos" would fold two different clubs.
MIN_TRUNCATION = 5
#: An initialism has to be long enough to mean something: "j" inside "juniors"
#: is not evidence, "jrs" is.
MIN_INITIALISM = 3


def _clean(text: Any) -> str:
    plain = _PUNCT.sub(" ", str(text or ""))
    plain = _NOISE.sub(" ", plain)
    return " ".join(plain.split()).casefold()


def sides(item: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    """The two teams, cleaned, or None when the title is not a fixture."""
    name = str(item.get("name") or item.get("match_name") or "")
    parts = [_clean(part) for part in _SPLIT.split(name)]
    parts = [part for part in parts if len(part) > 2]
    if len(parts) != 2:
        return None
    return parts[0], parts[1]


def _bare(side: str) -> str:
    """The side without the suffixes feeds disagree about."""
    return " ".join(_SUFFIX.sub(" ", side).split())


def _is_initialism(short: str, long: str) -> bool:
    """Is `short` an abbreviation of `long`? "jrs" of "juniors".

    Letters in order is not enough on its own: "san" reads as the letters of
    "santos" in order, and folded San Lorenzo into Santos. A real abbreviation
    drops the vowels - "jrs", "utd", "bcn" - so a vowel anywhere but the first
    character means the short form is a word in its own right rather than a
    contraction of the long one.
    """
    if len(short) < MIN_INITIALISM or len(long) - len(short) < 2:
        return False
    if any(letter in "aeiou" for letter in short[1:]):
        return False
    position = 0
    for letter in short:
        position = long.find(letter, position)
        if position < 0:
            return False
        position += 1
    return True


def _same_token(left: str, right: str) -> bool:
    if left == right:
        return True
    short, long = sorted((left, right), key=len)
    if len(short) >= MIN_TRUNCATION and long.startswith(short):
        return True
    return _is_initialism(short, long)


def same_side(left: str, right: str) -> bool:
    """Whether two spellings name the same team.

    Compared token by token from the front, so a longer form only agrees when
    every token it shares with the shorter one agrees: "deportivo" matches
    "deportivo de a coruna", and "manchester united" does not match
    "manchester city".
    """
    if left == right:
        return True
    left_tokens = _bare(left).split()
    right_tokens = _bare(right).split()
    if not left_tokens or not right_tokens:
        return False
    if left_tokens == right_tokens:
        return True
    shared = min(len(left_tokens), len(right_tokens))
    return all(_same_token(left_tokens[index], right_tokens[index])
               for index in range(shared))


def _kickoff(item: Dict[str, Any]) -> str:
    for field in ("start_time", "start_at"):
        value = str(item.get(field) or "").strip()
        if value:
            return value
    return ""


def same_fixture(left: Dict[str, Any], right: Dict[str, Any]) -> bool:
    """The narrow rule, stated once."""
    kickoff = _kickoff(left)
    if not kickoff or kickoff != _kickoff(right):
        return False
    left_sides, right_sides = sides(left), sides(right)
    if not left_sides or not right_sides:
        return False
    # One side the same team beyond doubt, the other merely spelled
    # differently. The anchor compares without the corporate suffix one feed
    # keeps and another drops - "Baniyas" and "Baniyas SC" are one club - but
    # nothing looser than that, which is what keeps "Manchester United vs
    # Arsenal" and "Manchester City vs Arsenal" apart: their anchor is Arsenal,
    # and "united" is not a spelling of "city".
    for a, b in ((0, 1), (1, 0)):
        if (_bare(left_sides[a]) == _bare(right_sides[a])
                and same_side(left_sides[b], right_sides[b])):
            return True
    return False


def _weight(item: Dict[str, Any]) -> Tuple[int, int, int]:
    """How much a card is worth keeping: routes, then sources, then name length."""
    channels = item.get("channels")
    backups = item.get("backups")
    sources = item.get("source_ids")
    return (
        (len(channels) if isinstance(channels, list) else 0)
        + (len(backups) if isinstance(backups, list) else 0)
        + (1 if str(item.get("url") or "").strip() else 0),
        len(sources) if isinstance(sources, list) else 0,
        len(str(item.get("name") or "")),
    )


def _absorb(keeper: Dict[str, Any], other: Dict[str, Any]) -> None:
    """Move the folded card's routes and sources into the one being kept."""
    seen = {str(row.get("url") or "")
            for row in (keeper.get("backups") or []) if isinstance(row, dict)}
    seen.add(str(keeper.get("url") or ""))

    backups = list(keeper.get("backups") or [])
    for row in [other] + list(other.get("backups") or []):
        if not isinstance(row, dict):
            continue
        url = str(row.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        backups.append(row if row is not other else
                       {key: value for key, value in other.items()
                        if key not in ("backups", "channels", "source_ids")})
    if backups:
        keeper["backups"] = backups

    keeper_channels = list(keeper.get("channels") or [])
    known = {str(c.get("id") or c.get("name") or "")
             for c in keeper_channels if isinstance(c, dict)}
    for channel in other.get("channels") or []:
        if isinstance(channel, dict):
            marker = str(channel.get("id") or channel.get("name") or "")
            if marker and marker not in known:
                known.add(marker)
                keeper_channels.append(channel)
    if keeper_channels:
        keeper["channels"] = keeper_channels

    merged_sources = list(keeper.get("source_ids") or [])
    for source in other.get("source_ids") or []:
        if source not in merged_sources:
            merged_sources.append(source)
    if merged_sources:
        keeper["source_ids"] = merged_sources


def fold(items: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    """Fold duplicate spellings of one fixture together. Returns (kept, report)."""
    kept: List[Dict[str, Any]] = []
    report: List[Dict[str, str]] = []

    for item in items:
        if not isinstance(item, dict):
            continue
        match = next((existing for existing in kept if same_fixture(existing, item)),
                     None)
        if match is None:
            kept.append(item)
            continue
        # The richer card leads, so folding never costs a stream.
        if _weight(item) > _weight(match):
            keeper, folded = item, match
            kept[kept.index(match)] = item
        else:
            keeper, folded = match, item
        _absorb(keeper, folded)
        report.append({
            "kept": str(keeper.get("name") or "")[:70],
            "folded": str(folded.get("name") or "")[:70],
            "kickoff": _kickoff(keeper),
        })

    return kept, report
