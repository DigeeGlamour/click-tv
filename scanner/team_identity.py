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

#: An apostrophe joins a word to its possessive; it does not separate two
#: words. Replacing it with a space made `Newell's Old Boys` into
#: "newell s old boys" while the feed that spells it `Newells Old Boys` gave
#: "newells old boys", and one Argentinian fixture published twice for the
#: length of a scan. Removed rather than spaced, and only the apostrophe:
#: every other mark still separates.
_APOSTROPHE = re.compile("['’ʼ`´]+")
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
    plain = _PUNCT.sub(" ", _fold_accents(_APOSTROPHE.sub("", str(value or ""))))
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
    # Nothing in the words _gender knows. A competition can still state the
    # category outright - Liga F, NWSL, Frauen-Bundesliga - and a fixture in a
    # women's league is a women's fixture however neutrally it is titled.
    for field in GENDER_FIELDS:
        value = normalize_team(item.get(field))
        if value and _WOMEN_COMPETITIONS.search(value):
            return "women"
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


#: Club-form words that are part of a club's legal name and never its
#: distinctive part. Removing one can only ever leave the name that identifies
#: the club, which is why this is a closed list and not a heuristic:
#:
#:      "1 FSV Mainz 05"  and  "FSV Mainz 05"   ->  "mainz 05"
#:      "CA Lanus"        and  "Lanus"          ->  "lanus"
#:      "Seattle Reign FC" and "Seattle Reign"  ->  "seattle reign"
#:
#: Nothing goes in here that is a club on its own. "Real", "Sporting",
#: "Athletic", "Racing", "Dynamo" and "Union" are deliberately absent - each
#: names a different club depending on the city beside it, and dropping the
#: word would relate clubs nobody has verified are one. "Deportivo" is out
#: for the same reason and was briefly in by mistake: on its own it names
#: Deportivo La Coruña, and tests/test_fixture_dedupe.py guards it as an
#: identity word. "Deportivo Alavés" reaches "Alavés" through the alias
#: table instead, with the evidence written beside it.
CLUB_FORM_WORDS = frozenset((
    "fc", "cf", "sc", "ac", "as", "afc", "sv", "fsv", "vfl", "vfb", "tsv",
    "bsc", "sk", "fk", "cd", "ca", "ud", "ss", "rc", "rcd", "sd", "cs", "ce",
    "cp", "gd", "sl", "ks", "estac", "club", "societa", "sociedade",
))

#: A leading ordinal is only ever dropped in front of one of those words -
#: the German "1. FC" / "1. FSV" convention. A bare leading number that is not
#: followed by a club-form word is left alone, because "1860 Munich" and
#: "09 Wolfsburg" carry it as part of the name.
_LEADING_ORDINAL = re.compile(r"^\s*(\d{1,2})\s+(?=([a-z]+))")

#: A round or format a broadcaster prefixes to the first participant:
#: "3rd ODI England vs Ireland" splits into "3rd ODI England" and "Ireland",
#: so the round travels inside the club name and no key can match. Removed
#: only from the FRONT, and only when a recognised round noun follows the
#: number, so "1860 Munich" and "09 Wolfsburg" keep theirs.
_ROUND_PREFIX = re.compile(
    r"^(?:live\s*[:-]?\s*)?\d{1,2}\s*(?:st|nd|rd|th)?\s+"
    r"(?:odis?|tests?|t20is?|t20s?|matches?|match|games?|legs?|rounds?)\b\s*[:-]?\s*",
    re.IGNORECASE)

#: Gender words a participant's own name may carry. They are REMOVED from the
#: participant and carried separately, never dropped outright: the merge key
#: ends with the fixture's category, so a women's fixture and a men's fixture
#: between the same clubs stay two fixtures. Stripping the word without
#: keeping the category is what the working agreement forbids.
_GENDER_WORDS = re.compile(
    r"\b(?:w|women|womens|ladies|femenino|femenina|feminina|frauen|m|men|mens)\b")

#: Competitions whose name states the category even when the fixture's title
#: does not. "Real Madrid Vs Eibar" in Liga F is a women's fixture; read from
#: the title alone it looked like it might be the men's one, and so it stayed
#: a second card beside "Real Madrid W vs Eibar W".
_WOMEN_COMPETITIONS = re.compile(
    r"\b(?:liga\s?f|nwsl|wsl|frauen[\s-]?bundesliga|femenina|femenino|feminina|"
    r"women'?s?|ladies|damallsvenskan|kvindeliga|serie\s?a\s?femminile)\b")


def structural_form(value: Any) -> str:
    """The club name with legal-form words and gender markers removed.

    Deterministic and closed: it deletes only words from CLUB_FORM_WORDS, a
    leading ordinal in front of one of them, and gender markers. It never
    compares two names, never scores similarity and never shortens a name to
    nothing - a name that would be emptied comes back normalized instead.
    """
    name = normalize_team(value)
    if not name:
        return ""
    name = _ROUND_PREFIX.sub("", name)
    name = _LEADING_ORDINAL.sub(
        lambda m: "" if m.group(2) in CLUB_FORM_WORDS else m.group(0), name)
    tokens = name.split()
    stripped = " ".join(t for t in tokens if t not in CLUB_FORM_WORDS)
    stripped = " ".join(_GENDER_WORDS.sub(" ", stripped).split())
    return stripped or name


def identity_form(value: Any, gender: str = "",
                  path: Path | str = ALIAS_FILE) -> str:
    """What the merge layer compares: a verified alias, else the structure.

    The alias table still comes first and still wins - it is the only place
    two genuinely different spellings of one club may be related, and every
    entry in it was checked against a real source. structural_form only
    removes words that cannot carry identity, so it relates
    "1 FSV Mainz 05" to "FSV Mainz 05" without relating anything else.
    """
    canonical = canonical_team(value, gender, path)
    aliased = canonical != normalize_team(value)
    if aliased:
        # A verified alias is the answer; structure is not applied on top of
        # it, so the table stays the single place a club is renamed.
        return canonical
    return structural_form(canonical)


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
