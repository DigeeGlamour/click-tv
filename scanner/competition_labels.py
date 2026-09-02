"""`Bundesliga` is not one competition, and the card has to say which.

Several feeds print the bare domestic name, which reads as the famous league
of that name. Observed on the site 2026-09-02:

    Bundesliga         Austria Vienna vs WSG Wattens        Austrian, not German
    Premier League     Dinamo Minsk vs Naftan               Belarusian, not English
    Cup                Znicz Pruszkow vs Cracovia Krakow    Polish
    Serie A            Flamengo vs Mirassol                 Brazilian
    Primera Division   Coquimbo Unido vs Nublense           Chilean
    Championship       Millwall vs Wrexham                  English second tier

None of those cards is wrong about the fixture; each is wrong about which
country's competition it belongs to, which is exactly the thing a viewer uses
the label for.

The teams settle it. A club plays in one country's league, so a side that is
recognisably Austrian makes `Bundesliga` the Austrian one - and when no side is
recognised the label is left exactly as the feed sent it, because a wrong guess
here is worse than a vague truth.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, Iterable, List, Tuple

#: ambiguous label -> [(specific label, team markers that identify it)]
#:
#: The markers are club and city fragments, matched as whole words against
#: either side of the fixture. They are deliberately few and distinctive: this
#: only has to recognise the countries whose feeds print a bare label.
DISAMBIGUATION: Dict[str, List[Tuple[str, Tuple[str, ...]]]] = {
    "bundesliga": [
        ("Austrian Bundesliga", (
            "austria wien", "austria vienna", "rapid wien", "rapid vienna",
            "salzburg", "sturm graz", "wsg", "wattens", "altach", "lask",
            "wolfsberger", "hartberg", "ried", "klagenfurt", "blau weiss linz",
        )),
        ("German Bundesliga", (
            "bayern", "dortmund", "leverkusen", "leipzig", "frankfurt",
            "stuttgart", "wolfsburg", "bremen", "freiburg", "mainz",
            "hoffenheim", "augsburg", "union berlin", "gladbach", "heidenheim",
            "st pauli", "bochum", "kiel",
        )),
    ],
    "premier league": [
        ("Belarusian Premier League", (
            "minsk", "vitebsk", "belshina", "naftan", "bate", "borisov",
            "gomel", "slutsk", "neman", "grodno", "shakhtyor", "soligorsk",
            "torpedo zhodino", "isloch", "slavia mozyr",
        )),
        ("English Premier League", (
            "arsenal", "chelsea", "liverpool", "manchester", "tottenham",
            "everton", "newcastle", "aston villa", "brighton", "brentford",
            "fulham", "crystal palace", "west ham", "wolves", "nottingham",
            "bournemouth", "ipswich", "leicester", "southampton",
        )),
    ],
    "cup": [
        ("Polish Cup", (
            "wisla", "cracovia", "legia", "lech poznan", "pruszkow",
            "gornik", "zaglebie", "pogon", "rakow", "widzew", "luzino",
            "znicz", "plock", "krakow", "slask",
        )),
    ],
    "serie a": [
        ("Brazilian Serie A", (
            "flamengo", "palmeiras", "corinthians", "santos", "sao paulo",
            "gremio", "internacional", "cruzeiro", "atletico mineiro",
            "botafogo", "vasco", "fluminense", "bahia", "fortaleza",
            "mirassol", "juventude", "bragantino", "vitoria",
        )),
        ("Italian Serie A", (
            "inter", "milan", "juventus", "napoli", "roma", "lazio",
            "atalanta", "fiorentina", "bologna", "torino", "udinese",
            "sassuolo", "cagliari", "genoa", "verona", "empoli", "lecce",
            "monza", "parma", "como", "venezia",
        )),
    ],
    "primera division": [
        ("Chilean Primera Division", (
            "coquimbo", "nublense", "colo colo", "universidad de chile",
            "universidad catolica", "huachipato", "cobresal", "palestino",
            "everton vina", "audax", "iquique", "limache", "la serena",
            "union espanola", "o higgins",
        )),
        ("Argentine Primera Division", (
            "boca", "river plate", "racing club", "independiente",
            "san lorenzo", "velez", "estudiantes", "newells", "rosario",
            "talleres", "godoy cruz", "argentinos", "banfield", "lanus",
            "huracan", "tigre", "platense", "defensa",
        )),
    ],
    "championship": [
        ("EFL Championship", (
            "millwall", "wrexham", "qpr", "queens park rangers", "cardiff",
            "west brom", "charlton", "norwich", "watford", "coventry",
            "hull", "middlesbrough", "preston", "swansea", "bristol city",
            "blackburn", "stoke", "sheffield", "derby", "portsmouth",
            "oxford united", "luton", "plymouth", "burnley", "leeds",
        )),
    ],
}

_SPLIT = re.compile(r"\s+(?:vs?\.?|v|versus)\s+", re.IGNORECASE)
_WORD = re.compile(r"[^\w\s]+", re.UNICODE)
#: Season and round decoration on the label, which must survive the rename.
_TAIL = re.compile(r"\s*[-,|]?\s*(?:\d{4}(?:[/-]\d{2,4})?|matchday\s*\w+"
                   r"|jornada\s*\w+|round\s*\w+|week\s*\w+)\s*$", re.IGNORECASE)


def _fold(text: Any) -> str:
    plain = unicodedata.normalize("NFKD", str(text or ""))
    plain = "".join(ch for ch in plain if not unicodedata.combining(ch))
    return " ".join(_WORD.sub(" ", plain).split()).casefold()


def _sides(name: Any) -> str:
    """Both team names as one comparable string."""
    return _fold(" ".join(_SPLIT.split(str(name or ""))))


def clarify(competition: Any, fixture_name: Any) -> str:
    """The specific competition name, or "" when the teams do not say.

    The label keeps whatever season decoration it arrived with, so
    "Bundesliga 2026/27" becomes "Austrian Bundesliga 2026/27" rather than
    losing the season.
    """
    raw = " ".join(str(competition or "").split())
    if not raw:
        return ""
    stem = _TAIL.sub("", raw).strip(" -,|")
    candidates = DISAMBIGUATION.get(_fold(stem))
    if not candidates:
        return ""

    teams = _sides(fixture_name)
    if not teams:
        return ""
    for specific, markers in candidates:
        for marker in markers:
            if re.search(rf"(?<![a-z0-9]){re.escape(marker)}(?![a-z0-9])", teams):
                tail = raw[len(stem):]
                return f"{specific}{tail}"
    return ""


def apply(items: Iterable[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Name the country on every ambiguous label a card carries.

    Returns one row per card renamed, so a wrong rename is a line to read
    rather than a label nobody can trace back.
    """
    changed: List[Dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        before = str(item.get("competition") or "")
        after = clarify(before, item.get("name"))
        if not after or after == before:
            continue
        item["competition_from_source"] = before
        item["competition"] = after
        changed.append({
            "name": str(item.get("name") or "")[:70],
            "was": before,
            "now": after,
        })
    return changed
