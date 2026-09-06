"""One canonical name per club, decided by an exact table.

FINAL_2's identity rule is "normalized teams + competition + kickoff time
bucket". The competition and the kickoff were already handled; the teams were
normalized only for punctuation, accents and corporate abbreviations, which is
why one Premier League fixture published as two cards on 2026-09-05:

    provider:brighton-vs-leeds|premier league|2026-09-05                  (bingstream)
    provider:brighton-hove-albion-vs-leeds-united|premier league|...      (sm-sports-data)

Both sides were spelled differently at once, and `fixture_dedupe.same_fixture`
deliberately requires one side to match exactly - that anchor is what keeps
`Manchester United vs Arsenal` and `Manchester City vs Arsenal` apart. So the
fixture ended in the archive as ended AND live on Today, under two identities.

This module is the missing step, and it is deliberately the dullest possible
one: an exact lookup in config/team-aliases.json. No similarity score, no
substring test, no token overlap, no "drop the last word" rule. A spelling
either has an entry or it does not, and a club with no entry is left exactly as
it was.

`fixture_dedupe` and `merger` both ask this module, so the tabs' fold, the
merge layer's verdict and the archive's recognition cannot disagree about who
is playing.
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

ALIAS_FILE = Path("config") / "team-aliases.json"

_PUNCT = re.compile(r"[^\w\s]+", re.UNICODE)

#: Loaded once per process, keyed by the path it came from. A scan reads this
#: for every comparison it makes, and the file does not change under it.
#: Two tables: the plain one, and the one whose entries only apply to a
#: fixture whose own gender evidence matches.
_CACHE: Dict[str, Tuple[Dict[str, str], Dict[Tuple[str, str], str]]] = {}

#: The fields a fixture's own gender evidence may come from. The title is
#: read first because a feed that writes it there means it; the competition
#: is read second, which is how a neutrally-titled fixture in a women's
#: league is still known to be a women's fixture.
GENDER_FIELDS = ("name", "match_name", "competition")


def _fold_accents(text: str) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFKD", str(text or ""))
        if not unicodedata.combining(char)
    )


def normalize_team(value: Any) -> str:
    """The plain form a lookup is done on: no accents, no punctuation, folded.

    `Brighton & Hove Albion` and `Brighton and Hove Albion` reach the table as
    the same string, because the ampersand is punctuation and the word is not.
    """
    plain = _PUNCT.sub(" ", _fold_accents(value))
    return " ".join(plain.split()).casefold()


def _load(path: Path | str = ALIAS_FILE
          ) -> Tuple[Dict[str, str], Dict[Tuple[str, str], str]]:
    key = str(path)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached

    table: Dict[str, str] = {}
    scoped: Dict[Tuple[str, str], str] = {}
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        _CACHE[key] = (table, scoped)
        return table, scoped

    aliases = payload.get("aliases") if isinstance(payload, dict) else None
    if isinstance(aliases, dict):
        for spelling, entry in aliases.items():
            canonical = (entry.get("canonical") if isinstance(entry, dict)
                         else entry)
            left, right = normalize_team(spelling), normalize_team(canonical)
            # A blank on either side, or an entry that points at itself, is
            # noise rather than an alias.
            if not left or not right or left == right:
                continue
            gender = ""
            if isinstance(entry, dict):
                gender = str(entry.get("gender") or "").strip().casefold()
            if gender:
                # Applies to a women's fixture and to nothing else, so the
                # same spelling can never quietly relate two categories.
                scoped[(gender, left)] = right
            else:
                table[left] = right
    _CACHE[key] = (table, scoped)
    return table, scoped


def load_aliases(path: Path | str = ALIAS_FILE) -> Dict[str, str]:
    """The unscoped alias table, normalized string -> canonical string."""
    return dict(_load(path)[0])


def load_scoped_aliases(
    path: Path | str = ALIAS_FILE
) -> Dict[Tuple[str, str], str]:
    """The gender-scoped table, (gender, spelling) -> canonical string."""
    return dict(_load(path)[1])


def fixture_gender(item: Any) -> str:
    """"women", "men", or "" - a fixture's own explicit gender evidence.

    The vocabulary is not invented here: it is `schedule_resolver._gender`,
    which the merge layer has always used to keep a women's fixture apart
    from the men's fixture of the same name. The only thing added is WHERE
    it is allowed to look - the competition as well as the title, because a
    feed that writes a neutral title under a women's competition has stated
    the category just as plainly as one that writes it in the title.

    Only those two fields. A logo path or a URL can contain a stray "w" that
    means nothing, and a guess is worse here than a blank.
    """
    if not isinstance(item, dict):
        return ""
    try:
        from scanner.schedule_resolver import _gender
    except ImportError:  # pragma: no cover - flat layout
        try:
            from schedule_resolver import _gender  # type: ignore
        except ImportError:
            return ""
    for field in GENDER_FIELDS:
        value = str(item.get(field) or "").strip()
        if not value:
            continue
        try:
            verdict = _gender(value)
        except Exception:  # noqa: BLE001 - a name must never break grouping
            verdict = ""
        if verdict:
            return verdict
    return ""


def genders_compatible(left: Any, right: Any) -> bool:
    """Whether two fixtures can be the same match as far as category goes.

    Equal or nothing - the merge layer's existing rule, which already refuses
    to fold a neutral title into a gendered one, said once so the tabs' own
    narrower rule cannot answer differently. A men's and a women's fixture
    between the same two clubs at the same hour are two fixtures.
    """
    return fixture_gender(left) == fixture_gender(right)


def canonical_team(value: Any, gender: str = "",
                   path: Path | str = ALIAS_FILE) -> str:
    """The canonical spelling of one club, or the name itself.

    One lookup, no fallback cleverness. A name that is not in the table comes
    back normalized and otherwise untouched, which is what keeps this from
    quietly relating two clubs nobody has verified are one.

    `gender` is the fixture's own evidence (see fixture_gender). A scoped
    entry is consulted only when it matches; passing nothing reaches the
    plain table alone, which is the behaviour every existing caller had.
    """
    name = normalize_team(value)
    if not name:
        return ""
    table, scoped = _load(path)
    category = str(gender or "").strip().casefold()
    if category:
        found = scoped.get((category, name))
        if found:
            return found
    return table.get(name, name)


def clear_cache() -> None:
    """Forget the loaded table. For tests that write their own."""
    _CACHE.clear()


def alias_evidence(path: Path | str = ALIAS_FILE) -> Dict[str, Optional[str]]:
    """Why each alias exists, for an audit. Never read by the scan itself."""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    aliases = payload.get("aliases") if isinstance(payload, dict) else None
    if not isinstance(aliases, dict):
        return {}
    return {
        str(spelling): (entry.get("evidence") if isinstance(entry, dict) else None)
        for spelling, entry in aliases.items()
    }
