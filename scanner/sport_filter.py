"""Is this event cricket or football? Decided on evidence, never on one keyword.

Today Match and Upcoming show cricket and football, and nothing else. Two
failures are possible and both are real:

  * a valid cricket or football fixture is dropped because nobody labelled it
  * another sport reaches the tab because nobody labelled it either

The published data on 2026-08-30 shows why neither is solved by reading one
field. Of 23 events whose `sport_type` said "other", seven were football and
one was cricket:

    Machico vs Camacha                    Taça de Portugal      football
    Aberdeen vs Rangers                   (no competition)      football
    Arzignano Valchiampo vs PRO Vercelli  Serie C - Girone A    football
    Dolomiti Bellunesi vs Alcione         Serie C - Girone A    football
    Lumezzane vs Giana Erminio            Serie C - Girone A    football
    Pergolettese vs Union Brescia         Serie C - Girone A    football
    União PR vs Campo Mourão              Paranaense - 3        football
    Top End Series, Final Teams           (no competition)      cricket

while in the same list "BC Lions vs Ottawa Redblacks / CFL" is gridiron and
"Boland Cavaliers v Suzuki Griquas" is Currie Cup rugby - both of which read as
football if you go by the shape of the name.

So the sport is worked out in stages, in order of how much each can be trusted,
and an event that survives every stage without producing evidence is not
guessed at in either direction. It is held back:

  1. the source's own structured sport field
  2. the competition or league
  3. the title and team names
  4. a gazetteer of clubs and national sides, per sport
  5. a second pass across the whole batch - the same fixture arriving from
     another source, already classified, settles it

  confirmed_cricket / confirmed_football   published
  likely_cricket   / likely_football       published
  confirmed_other                          refused
  unknown                                  quarantined, and reported by name

`unknown` is the absence of a verdict, not a verdict, so it never reaches a
tab. It is also never silently binned: every quarantined event is listed in the
run report with its source and the reason nothing could be established, so a
fixture that should have been there shows up as a name to fix rather than as a
gap nobody sees.

`audit_visible` then re-reads what survived and looks for another sport in it,
independently of the decision that let it through, so a leak is a number in the
report rather than a tennis match on the front page.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

CONFIRMED_CRICKET = "confirmed_cricket"
CONFIRMED_FOOTBALL = "confirmed_football"
LIKELY_CRICKET = "likely_cricket"
LIKELY_FOOTBALL = "likely_football"
CONFIRMED_OTHER = "confirmed_other"
UNKNOWN = "unknown"

#: Only a positive cricket or football verdict reaches a tab.
PUBLISHABLE = frozenset({
    CONFIRMED_CRICKET, CONFIRMED_FOOTBALL, LIKELY_CRICKET, LIKELY_FOOTBALL,
})
CRICKET_STATES = frozenset({CONFIRMED_CRICKET, LIKELY_CRICKET})
FOOTBALL_STATES = frozenset({CONFIRMED_FOOTBALL, LIKELY_FOOTBALL})

SPORT_FIELDS = (
    "sport_type", "sport", "event_category", "category_name", "type",
    "discipline", "game_type",
)
COMPETITION_FIELDS = (
    "competition", "league", "tournament", "series", "event_name", "title",
    "group_title", "tour",
)
NAME_FIELDS = ("name", "match_name", "title", "short_name", "event_name")


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).casefold()


def _gather(item: Dict[str, Any], fields: Iterable[str]) -> str:
    parts = []
    for field in fields:
        value = item.get(field)
        if isinstance(value, str) and value.strip():
            parts.append(value)
    return _text(" | ".join(parts))


def _word(pattern: str) -> re.Pattern:
    """Match as a whole word, so `test` does not fire inside `contest`."""
    return re.compile(rf"(?<![a-z0-9]){pattern}(?![a-z0-9])", re.IGNORECASE)


def _any(patterns: Iterable[re.Pattern], text: str) -> str:
    for pattern in patterns:
        found = pattern.search(text)
        if found:
            return found.group(0)
    return ""


# ── Cricket ───────────────────────────────────────────────────────────────────
CRICKET_STRONG = [_word(p) for p in (
    "cricket", "t20", "t20i", "t10", "odi", "one ?day international",
    "test match", "first class", "the hundred",
    "ipl", "bbl", "psl", "cpl", "wpl", "lpl", "bpl", "mlc", "ilt20", "sa20",
    "icc", "ashes", "ranji", "vijay hazare", "syed mushtaq",
    "duleep trophy", "irani cup", "county championship", "royal london",
    "vitality blast", "super smash", "plunket shield", "sheffield shield",
    "marsh cup", "ford trophy", "quaid-e-azam",
    "asia cup", "champions trophy", "world test championship", "wtc",
    "bangladesh premier league", "indian premier league",
    "pakistan super league", "caribbean premier league",
    "lanka premier league", "big bash", "major league cricket",
    "top end", "willow", "d50", "50 ?over", "hundred ball",
)]
CRICKET_WEAK = [_word(p) for p in (
    "wickets?", "overs?", "innings", "stumps", "batting", "bowling",
    "kings ?xi", "super ?kings", "knight ?riders", "zalmi", "qalandars",
    "sixers", "scorchers", "renegades", "hurricanes", "sunrisers",
    "royal challengers", "rajvansh", "soormas", "strikers", "blasters",
)]

# ── Cricket formats ────────────────────────────────────────────
#
# How long a cricket match lasts depends entirely on which kind it is: a
# T10 runs about two and a half hours and a Test runs five days. The
# duration table treats them all as one sport at eight hours, written with
# a Test in mind, and on 2026-09-05 all 24 cricket fixtures on the site
# were T20 or shorter.
#
# This says WHICH. It does not say how long - that is PROMPT 18 - and
# nothing here removes, reorders or hides a card.
#
# The rules are the ones already in this file: whole-word tokens through
# `_word`, gathered from the same name/competition/sport fields the sport
# verdict uses. Deterministic - the same card always answers the same.
CRICKET_T10 = "T10"
CRICKET_T20 = "T20"
CRICKET_ODI = "ODI"
CRICKET_TEST = "Test"
CRICKET_HUNDRED = "Hundred"
CRICKET_FORMAT_UNKNOWN = "unknown"
CRICKET_FORMATS = (
    CRICKET_T10, CRICKET_T20, CRICKET_ODI, CRICKET_TEST, CRICKET_HUNDRED,
    CRICKET_FORMAT_UNKNOWN,
)

#: Every format is tried, not just the first - a card matching two of them
#: is ambiguous, and ambiguous is not the same as whichever came first.
CRICKET_FORMAT_PATTERNS = (
    (CRICKET_HUNDRED, [_word(p) for p in (
        "the ?hundred", "hundred ball", "100 ?ball",
    )]),
    (CRICKET_TEST, [_word(p) for p in (
        "tests?", "test match(?:es)?", "first class",
        "world test championship", "wtc", "ashes",
        "ranji", "duleep trophy", "sheffield shield", "plunket shield",
        "county championship", "quaid-e-azam",
    )]),
    (CRICKET_ODI, [_word(p) for p in (
        "odis?", "one ?day international(?:s)?", "list a",
        "50 ?overs?", "d50", "vijay hazare", "royal london", "marsh cup",
        "ford trophy",
    )]),
    (CRICKET_T20, [_word(p) for p in (
        "t20i?s?", "twenty ?20", "20 ?overs?",
        "ipl", "bbl", "psl", "cpl", "wpl", "lpl", "bpl", "mlc",
        "ilt20", "sa20", "big bash", "vitality blast", "super smash",
        "syed mushtaq", "indian premier league", "big bash league",
        "pakistan super league", "caribbean premier league",
        "lanka premier league", "bangladesh premier league",
        "major league cricket", "blast",
    )]),
    (CRICKET_T10, [_word(p) for p in (
        "t10s?", "10 ?overs?", "abu dhabi t10",
    )]),
)

#: Deliberately absent from every list above. "World Cup", "Asia Cup" and
#: "Champions Trophy" are each played in more than one format, so a card
#: carrying only one of them is ambiguous and stays `unknown` rather than
#: being guessed at.
CRICKET_FORMAT_NOT_EVIDENCE = (
    "world cup", "asia cup", "champions trophy", "tri series", "tour",
)


def _is_cricket(item: Dict[str, Any]) -> bool:
    """Whether the sport rules already in this file call this cricket.

    The gate matters because format tokens are ambiguous outside cricket:
    `blast` is Vitality Blast and a dozen other things, `mlc` is Major
    League Cricket and Major League Cricket only here. Asking the sport
    question first stops a stray token inventing a cricket answer.

    It delegates rather than deciding, so it inherits that verdict exactly -
    including where the verdict is already wrong. `BBL` is the Big Bash
    League to CRICKET_STRONG and the German basketball league to everyone
    else, and a basketball card carrying it is called cricket today, before
    this function is reached. Correcting that is a sport-classification
    change with tab-routing consequences, not a format one; it is recorded
    rather than quietly patched here.
    """
    stamped = _text(item.get("sport_class"))
    if stamped:
        return stamped in CRICKET_STATES
    return classify(item).get("state") in CRICKET_STATES


def cricket_format(item: Dict[str, Any]) -> Dict[str, str]:
    """Which format of cricket this fixture is, with the evidence for it.

    Returns `{"format": ..., "evidence": ..., "reason": ...}` where format is
    one of CRICKET_FORMATS. `unknown` is a real answer and the common one -
    returned whenever the card does not say, and whenever it says two
    different things.

    Nothing is inferred from a card merely being cricket. A fixture labelled
    only "cricket" is `unknown`, because that is exactly what is known.
    """
    if not _is_cricket(item):
        return {"format": CRICKET_FORMAT_UNKNOWN,
                "evidence": "",
                "reason": "not cricket"}
    text = " | ".join((
        _gather(item, NAME_FIELDS),
        _gather(item, COMPETITION_FIELDS),
        _gather(item, SPORT_FIELDS),
    ))
    hits = []
    for name, patterns in CRICKET_FORMAT_PATTERNS:
        found = _any(patterns, text)
        if found:
            hits.append((name, found))
    if not hits:
        return {"format": CRICKET_FORMAT_UNKNOWN,
                "evidence": "",
                "reason": "no format token"}
    if len(hits) > 1:
        return {
            "format": CRICKET_FORMAT_UNKNOWN,
            "evidence": ", ".join("%s=%s" % pair for pair in hits),
            "reason": "ambiguous: %s" % " and ".join(h[0] for h in hits),
        }
    name, found = hits[0]
    return {"format": name, "evidence": found, "reason": "token"}


# ── Football ──────────────────────────────────────────────────────────────────
FOOTBALL_STRONG = [_word(p) for p in (
    "football", "soccer", "futbol", "fútbol", "futebol", "socca",
    "uefa", "fifa", "conmebol", "concacaf", "afc cup", "afc champions",
    "premier ?league", "la ?liga", "laliga", "serie ?[abcd]", "bundesliga",
    "ligue ?[12]", "eredivisie", "primeira ?liga", "liga ?portugal",
    "champions ?league", "europa ?league", "conference ?league",
    "mls", "isl", "i-?league", "super ?lig", "superliga", "allsvenskan",
    "eliteserien", "ekstraklasa", "j1 ?league", "j2 ?league", "k ?league",
    "a-?league", "brasileir[ãa]o", "libertadores", "sudamericana",
    "copa ?del ?rey", "coppa ?italia", "dfb ?pokal", "fa ?cup", "efl",
    "carabao", "efl ?championship", "sky ?bet championship",
    "league ?one", "league ?two", "national ?league",
    "ta[çc]a de portugal", "paranaense", "paulista", "carioca", "gaúcho",
    "girone", "primera ?división", "liga ?mx", "scottish", "eerste ?divisie",
    "world ?cup qualif", "euro qualif", "nations ?league", "primavera",
)]
FOOTBALL_WEAK = [_word(p) for p in (
    r"f\.?c\.?", r"a\.?f\.?c\.?", r"s\.?c\.?", r"c\.?f\.?", r"u\.?d\.?",
    "united", "city", "athletic", "atl[ée]tico", "real", "sporting",
    "dynamo", "dinamo", "spartak", "lokomotiv", "olympiacos", "galatasaray",
    "fenerbah[çc]e", "besiktas", "ajax", "psv", "feyenoord", "benfica",
    "porto", "celtic", "rangers", "rovers", "albion",
    # Not bare "championship": the golf TOUR Championship and the County
    # Championship both carry it, and the football competitions that mean it -
    # EFL, Sky Bet - are named in full above. Keeping it here made the audit
    # read a refused golf broadcast back as football.
    "u1[5-9]", "u2[0-3]", "reserves",
)]

# ── Everything else, named so it can be refused with confidence ───────────────
# The two-word forms matter: "Bulls", "Sharks", "Lions", "Chiefs" and "Tigers"
# each belong to three different sports, so only unambiguous names are listed.
#: Words that identify a sport outright and cannot be a club name or a
#: football/cricket competition. Read BEFORE the source's sport field, unlike
#: OTHER_SPORTS below - see step 1b in classify() for why the two are separate
#: and what happened when they were not.
#:
#: Nothing goes in here that a team could be called. "Rally", "Masters" and
#: "Открытие" style sponsor words are deliberately absent; so is every word
#: already proven dangerous - guardians, mavericks, wimbledon, dockers.
UNAMBIGUOUS_OTHER_SPORT = [_word(p) for p in (
    "fim", "motogp", "moto ?2", "moto ?3", "moto ?junior", "motocross",
    "supercross", "superbike", "nascar", "indycar", "indy ?nxt",
    "formula ?[123e]", "grand ?prix", "wrc",
    "ppa tour", "pickleball",
    "bmx", "velodrome",
)]


OTHER_SPORTS = {
    "tennis": [_word(p) for p in (
        "tennis", "atp", "wta", "wimbledon", "roland ?garros",
        "australian ?open", "davis ?cup", "billie ?jean", "itf",
    )],
    "table tennis / pickleball": [_word(p) for p in (
        "table ?tennis", "ping ?pong", "pickleball", "mlp finals", "ptt",
        "padel", "squash",
        # Professional Pickleball Association. "PPA Tour: Cary-Championship
        # Sunday" was published as football for the same reason FIM was.
        "ppa tour", "ppa",
    )],
    "basketball": [_word(p) for p in (
        "basketball", "nba", "wnba", "euroleague", "fiba",
        "cleveland cavaliers", "mavericks", "knicks", "lakers", "celtics",
        "atlanta dream", "dallas wings", "minnesota lynx", "golden state",
    )],
    "hockey": [_word(p) for p in (
        "ice ?hockey", "field ?hockey", "nhl", "khl", "hockey league",
        "fih", "pro ?league hockey",
    )],
    "badminton": [_word(p) for p in (
        "badminton", "bwf", "thomas ?cup", "uber ?cup", "sudirman",
    )],
    "motorsport": [_word(p) for p in (
        "formula ?[123e]", "f1", "motogp", "moto2", "moto3", "nascar",
        "indycar", "indy ?nxt", "wrc", "rally", "le ?mans", "gt world",
        "superbike", "dtm", "grand ?prix", "milwaukee mile",
        # FIM is the governing body; its championships are motorcycle races
        # and none of them is football, whatever the word "championship" in
        # the title makes event_sport think.
        "fim", "moto ?junior", "motocross", "supercross", "speedway gp",
    )],
    "kabaddi": [_word(p) for p in ("kabaddi", "pkl", "pro kabaddi")],
    "wrestling / mma / boxing": [_word(p) for p in (
        "wrestling", "wwe", "aew", "ufc", "mma", "bkfc", "boxing", "bellator",
    )],
    "baseball": [_word(p) for p in (
        "baseball", "mlb", "npb", "kbo", "world series",
        "yankees", "red sox", "dodgers", "mets", "cubs", "braves", "orioles",
        "marlins", "nationals", "padres", "rays", "twins", "brewers",
        "cardinals", "pirates", "astros", "rockies", "mariners", "blue jays",
        "guardians", "white sox", "phillies", "angels", "athletics",
    )],
    "rugby": [_word(p) for p in (
        "rugby", "six ?nations", "super ?rugby", "nrl", "currie ?cup",
        "united rugby", "griquas", "boland cavaliers", "blue bulls",
        "golden lions", "western province", "free state cheetahs",
        "sale sharks", "exeter chiefs", "leicester tigers",
        "glasgow warriors", "northampton saints", "saracens", "harlequins",
        "leinster", "munster", "ulster", "connacht", "ospreys", "scarlets",
        "top ?14", "pro ?d2", "state of origin",
    )],
    "gridiron": [_word(p) for p in (
        "nfl", "cfl", "american ?football", "gridiron", "super ?bowl",
        "redblacks", "roughriders", "tiger-?cats", "blue bombers",
        "argonauts", "stampeders", "bc lions",
    )],
    "golf": [_word(p) for p in (
        "golf", "pga", "liv golf", "ryder ?cup", "masters tournament",
        "open championship", "dp world tour", "k club",
    )],
    "cycling": [_word(p) for p in (
        "cycling", "vuelta", "tour de france", "giro d'?italia", "uci",
        "criterium", "cross country olympic", "xco", "peloton",
    )],
    "esports": [_word(p) for p in (
        "esports", "dota", "counter-?strike", "cs2", "valorant",
        "league of legends",
    )],
    "poker / cards": [_word(p) for p in (
        "poker", "buy ?in", "all ?in", "wsop", "blackjack",
    )],
    "horse racing": [_word(p) for p in (
        "horse ?racing", "saratoga", "steeplechase", "belmont", "preakness",
        "racecourse",
    )],
    "other": [_word(p) for p in (
        "darts", "snooker", "bowling", "stepladder", "volleyball", "handball",
        "athletics", "swimming", "weightlifting", "archery", "shooting",
        "pentathlon", "chess", "netball", "lacrosse", "curling", "sumo",
    )],
}

STRUCTURED_CRICKET = frozenset({"cricket"})
STRUCTURED_FOOTBALL = frozenset({"football", "soccer"})
#: Structured values that name another sport. "other", "sports", "live" and ""
#: are NOT here: they say nothing, and reading them as a refusal is what would
#: have dropped seven Serie C fixtures.
STRUCTURED_OTHER = frozenset({
    "tennis", "basketball", "hockey", "badminton", "motorsport", "racing",
    "baseball", "golf", "kabaddi", "wrestling", "rugby", "cycling", "esports",
    "volleyball", "handball", "boxing", "mma", "darts", "snooker", "athletics",
    "table_tennis", "table tennis", "american_football", "nfl", "poker",
})

#: Countries that field a side in both sports, so a bare "India vs Thailand"
#: cannot be settled by the country names alone.
AMBIGUOUS_NATIONS = frozenset({
    "india", "pakistan", "bangladesh", "sri lanka", "afghanistan", "nepal",
    "england", "australia", "south africa", "new zealand", "ireland",
    "scotland", "netherlands", "thailand", "malaysia", "hong kong", "usa",
    "united states", "canada", "kenya", "namibia", "zimbabwe", "uganda",
    "japan", "china", "singapore", "indonesia", "germany", "spain", "wales",
})


#: Competitions whose name identifies the sport outright, including the
#: abbreviations feeds actually ship. Consulted before anything else, because a
#: league name is a fact about the sport while a team name is a coincidence:
#: `Guardians vs Dockers` in the ETPL was refused as baseball for the word
#: "guardians", and `Seoul W vs Incheon Red Angels W` in the WK-League was
#: refused for "angels". Both are real fixtures - European T20 cricket and
#: Korean women's football - and both were lost to a single word in a team's
#: name.
#:
#: It also outranks the source's own sport field, which is free text and
#: demonstrably wrong: `Wolves vs Castle Rockers` arrived tagged `football` and
#: is Belfast Wolves vs Edinburgh Castle Rockers, an ETPL cricket match.
LEAGUE_SPORT = {}


def _register_league(sport: str, *names: str) -> None:
    for name in names:
        LEAGUE_SPORT[" ".join(name.split()).casefold()] = sport


_register_league("cricket",
    "etpl", "european t20 premier league", "european t20", "euro t20",
    "jito premier league", "jito", "sher-e-punjab", "sher e punjab",
    "oman d50", "d50", "acc premier cup", "acc mens premier cup",
    "dehradun premier league", "dpl dehradun", "punjab t20 league",
    "top end t20 series", "willow cricket", "cricket review",
    "caribbean premier league", "cpl", "the hundred", "vitality blast",
    "county championship", "sheffield shield", "ford trophy",
    "world test championship", "icc world test championship",
)
_register_league("football",
    "wk league", "wk-league", "wk league women", "k league", "k-league",
    "j league cup", "j.league cup", "jleague cup", "j league", "j.league",
    "ykk anzen levain cup", "levain cup",
    "austrian bundesliga", "belarusian premier league", "polish cup",
    "brasileirao serie a", "brazilian serie a", "primera division",
    "laliga", "la liga", "premier league", "serie a", "serie b", "serie c",
    "bundesliga", "ligue 1", "eredivisie", "scottish premiership",
    "liga profesional argentina", "liga profesional de futbol",
    "copa sudamericana", "copa libertadores", "nwsl", "mls",
    "uefa womens champions league", "uefa women champions league",
    "socca world cup", "campionato primavera", "taca de portugal",
)
_register_league("rugby", "currie cup", "sa cup", "united rugby championship")
_register_league("gridiron", "cfl", "nfl")
_register_league("baseball", "mlb", "npb", "kbo league")
_register_league("basketball", "nba", "wnba", "eurobasket")
_register_league("hockey", "fih hockey world cup", "fih pro league", "nhl")
_register_league("golf", "pga tour", "dp world tour", "liv golf")
_register_league("tennis", "atp tour", "wta tour")
_register_league("motorsport", "formula 1", "motogp", "wrc", "indycar")

#: Trailing season and round decoration a feed appends to a league name.
_LEAGUE_TAIL = re.compile(
    r"\s*(?:[-\u2013,]\s*)?(?:girone\s*\w+|group\s*\w+|round\s*\w+|matchday\s*\w+"
    r"|jornada\s*\w+|week\s*\w+|day\s*\w+|\d{4}(?:/\d{2,4})?|\d+|[ivx]+)\s*$",
    re.IGNORECASE)


def league_sport(competition: Any) -> Tuple[str, str]:
    """The sport this competition names, and the form that matched."""
    raw = " ".join(str(competition or "").split())
    if not raw:
        return "", ""
    seen = set()
    candidate = raw
    for _ in range(4):
        key = " ".join(candidate.split()).casefold()
        if not key or key in seen:
            break
        seen.add(key)
        if key in LEAGUE_SPORT:
            return LEAGUE_SPORT[key], candidate
        trimmed = _LEAGUE_TAIL.sub("", candidate).strip(" -–,|")
        if trimmed == candidate:
            break
        candidate = trimmed
    return "", ""


def _other_sport_hit(text: str) -> Tuple[str, str]:
    for sport, patterns in OTHER_SPORTS.items():
        found = _any(patterns, text)
        if found:
            return sport, found
    return "", ""


def _verdict(state: str, reason: str, evidence: str = "") -> Dict[str, Any]:
    return {"state": state, "reason": reason, "evidence": evidence}


def looks_like_a_league_not_a_team(text: Any) -> str:
    """The league this side actually names, if it is a league and not a team.

    Asked of the league map first, then of the strong competition patterns -
    those are all competition names ("premier league", "la liga", "serie a",
    "uefa"), never club names, which live in the weak lists instead. Without
    the second question `Premier League vs Championship` read as a football
    fixture on the strength of the words in it.
    """
    sport, matched = league_sport(text)
    if sport:
        return matched
    plain = _text(text)
    if not plain:
        return ""
    for patterns in (CRICKET_STRONG, FOOTBALL_STRONG):
        found = _any(patterns, plain)
        # Only when the competition name is essentially the whole side, so a
        # club whose name happens to contain one is not mistaken for a league.
        if found and len(found) >= len(plain) - 2:
            return found
    return ""


def is_generic_fixture(item: Dict[str, Any]) -> str:
    """Why this card is not a real fixture, or "".

    `EUROPEAN T20 Vs Premier League` was published as a Today Match card with
    three channels hanging under it - FOX CRICKET, WILLOW SPORTS and Willow
    Cricbuzz - while the actual ETPL match at that hour, `Dublin Guardians vs
    Rotterdam Dockers`, sat beside it with its own two. Nobody plays "European
    T20": the feed's generic header had been read as a team-vs-team fixture,
    and a viewer looking for the match had two cards to choose between, one of
    them fictional.

    A fixture has two teams. Two competition names, or the competition facing
    itself, is a header.
    """
    sides = _participants(item)
    if len(sides) != 2:
        return ""
    left, right = (looks_like_a_league_not_a_team(side) for side in sides)
    if left and right:
        return f"both sides name a competition ({left} / {right})"

    competition = _text(_gather(item, COMPETITION_FIELDS))
    if not competition:
        return ""
    # One side repeating the competition it belongs to - "EUROPEAN T20 Vs
    # Premier League" under "EUROPEAN T20 Premier League".
    for side in sides:
        if len(side) > 4 and side in competition and (left or right):
            return f"a side repeats its own competition ({side})"
    return ""


def classify(item: Dict[str, Any]) -> Dict[str, Any]:
    """Stages 1-4 for a single event. Stage 5 needs the whole batch."""
    if not isinstance(item, dict):
        return _verdict(UNKNOWN, "not an event record")
    structured = _text(_gather(item, SPORT_FIELDS))
    competition = _text(_gather(item, COMPETITION_FIELDS))
    name = _text(_gather(item, NAME_FIELDS))
    everything = " | ".join(part for part in (structured, competition, name) if part)

    # 0. Not a fixture at all. A feed's generic header read as two teams
    # publishes a card nobody can play - see is_generic_fixture.
    generic = is_generic_fixture(item)
    if generic:
        return _verdict(CONFIRMED_OTHER, "not a fixture", generic)

    # 1. the competition, matched exactly against the league map. First,
    # because a league name identifies the sport outright - and ahead of the
    # source's own sport field, which is free text a feed gets wrong: `Wolves
    # vs Castle Rockers` arrived tagged `football` and is an ETPL cricket
    # match.
    for field in COMPETITION_FIELDS:
        sport, matched = league_sport(item.get(field))
        if not sport:
            continue
        if sport == "cricket":
            return _verdict(CONFIRMED_CRICKET, "league map", matched)
        if sport == "football":
            return _verdict(CONFIRMED_FOOTBALL, "league map", matched)
        return _verdict(CONFIRMED_OTHER, f"league map: {sport}", matched)

    # 1b. a governing body or race series in the record's own words.
    #
    # Ahead of the sport field, and the ONLY change to how that field is read.
    # scanner/merger.py builds every event card with
    # `sport_type = event_sport(base_item)`, and event_sport matches a loose
    # word list - the bare word "championship" sits in its football pattern
    # for the EFL. So "FIM MotoJunior World Championship-Valencia" was derived
    # as football, written into sport_type, and read back below as "source
    # sport field": the merge layer's guess returning as this module's
    # evidence. It published a motorcycle race on Today Match as football on
    # 2026-09-06, with "PPA Tour: Cary-Championship Sunday" beside it.
    #
    # Two things this deliberately is NOT.
    #
    # It is not "ignore sport_type". Measured on the 193 cards published that
    # afternoon, ignoring it would have unpublished 66 of them: "Jupiler Pro
    # League", "Úrvalsdeild", "NB I" and the rest reach no other rule here, so
    # for most cards that field is the only evidence this module has.
    #
    # It is not the full OTHER_SPORTS gazetteer either. Running that here cost
    # five false positives on the same 193 cards - "Meerut Mavericks" read as
    # basketball, "Dublin Guardians" as baseball, "AFC Wimbledon" as tennis -
    # which is exactly what the notes at step 5 warn about: a club is allowed
    # to be named after another sport. That is why the gazetteer stays where
    # it is, behind the competition and the title.
    #
    # UNAMBIGUOUS_OTHER_SPORT is the narrow subset that cannot be a club or a
    # football/cricket competition: governing bodies and race series.
    hit = _any(UNAMBIGUOUS_OTHER_SPORT, " | ".join(
        part for part in (competition, name) if part))
    if hit:
        return _verdict(CONFIRMED_OTHER, "governing body or race series", hit)

    # 2. the source said so.
    for token in structured.replace("|", " ").split():
        if token in STRUCTURED_CRICKET:
            return _verdict(CONFIRMED_CRICKET, "source sport field", token)
        if token in STRUCTURED_FOOTBALL:
            return _verdict(CONFIRMED_FOOTBALL, "source sport field", token)

    # 3. the competition, by pattern. Ahead of the structured "other sport" values, because
    # a feed that files a Serie C fixture under "other" is wrong about the
    # sport and right about the competition.
    hit = _any(CRICKET_STRONG, competition)
    if hit:
        return _verdict(CONFIRMED_CRICKET, "competition", hit)
    hit = _any(FOOTBALL_STRONG, competition)
    if hit:
        return _verdict(CONFIRMED_FOOTBALL, "competition", hit)

    # 4. the title.
    hit = _any(CRICKET_STRONG, name)
    if hit:
        return _verdict(CONFIRMED_CRICKET, "title", hit)
    hit = _any(FOOTBALL_STRONG, name)
    if hit:
        return _verdict(CONFIRMED_FOOTBALL, "title", hit)

    # 5. a named other sport. Where the evidence sits decides how far it
    # counts, because a team's name is a coincidence and a competition is a
    # fact. "Guardians vs Dockers" is an ETPL cricket fixture that was refused
    # as baseball for the word "guardians"; "Seoul W vs Incheon Red Angels W"
    # is Korean women's football refused for "angels". Both were real matches,
    # and both were lost to one word in a team's name.
    sport, found = _other_sport_hit(" | ".join(
        part for part in (structured, competition) if part))
    if sport:
        return _verdict(CONFIRMED_OTHER, f"{sport} gazetteer", found)
    for token in structured.replace("|", " ").split():
        if token in STRUCTURED_OTHER:
            return _verdict(CONFIRMED_OTHER, "source sport field", token)

    sport, found = _other_sport_hit(name)
    if sport:
        if not competition:
            # Nothing but the names to go on, so they decide: `Boland
            # Cavaliers v Suzuki Griquas` carries no competition and is rugby.
            return _verdict(CONFIRMED_OTHER, f"{sport} gazetteer", found)
        # A competition exists and named no sport this module recognises. A
        # word in a team's name is not enough to overrule it, so the fixture
        # is looked up rather than refused on the guess.
        return _verdict(UNKNOWN,
                        f"team name suggests {sport}, but the competition "
                        "does not - needs the fixture", found)

    # 6. club-name shapes, which only ever produce `likely`.
    hit = _any(FOOTBALL_WEAK, everything)
    if hit:
        return _verdict(LIKELY_FOOTBALL, "club-name shape", hit)
    hit = _any(CRICKET_WEAK, everything)
    if hit:
        return _verdict(LIKELY_CRICKET, "cricket-team shape", hit)

    return _verdict(UNKNOWN, "no sport evidence in the record")


# ── Stage 5: the second pass, which needs every event at once ────────────────

_SPLIT = re.compile(r"\s+(?:vs?\.?|v|versus)\s+", re.IGNORECASE)
_TIDY = re.compile(r"\b(?:live|hd|fhd|sd|women|men|w|u\d{2}|\d{1,2} \w{3} \d{4})\b",
                   re.IGNORECASE)


def _participants(item: Dict[str, Any]) -> List[str]:
    """The two sides of a fixture, as comparable text."""
    name = str(item.get("name") or item.get("match_name") or "")
    sides = [_TIDY.sub("", part).strip(" .-|,") for part in _SPLIT.split(name)]
    return [_text(side) for side in sides if len(_text(side)) > 2]


def _second_pass(item: Dict[str, Any],
                 settled: List[Tuple[Dict[str, Any], Dict[str, Any]]]
                 ) -> Optional[Dict[str, Any]]:
    """Can another event in this batch settle what this one is?

    Both sides appearing together in a fixture somebody else has already
    classified is real evidence - it is the same match, arriving twice. One
    side alone is not: a club can share a city, a sponsor or a name with a team
    in another sport. And two national sides that both play cricket and
    football settle nothing at all, which is why India vs Thailand stays
    unresolved rather than borrowing a verdict from a different India fixture.
    """
    sides = _participants(item)
    if len(sides) < 2:
        return None
    if all(side in AMBIGUOUS_NATIONS for side in sides):
        return None
    for other, verdict in settled:
        other_sides = _participants(other)
        if len(other_sides) < 2:
            continue
        matched = sum(
            1 for side in sides
            if any(side in candidate or candidate in side for candidate in other_sides)
        )
        if matched < 2:
            continue
        state = (LIKELY_CRICKET if verdict["state"] in CRICKET_STATES
                 else LIKELY_FOOTBALL)
        return _verdict(state, "same fixture from another source",
                        str(other.get("name") or "")[:60])
    return None


# ── Stage 6: ask the world, when the record will not say ────────────────────

#: Structured sport values that carry no claim, so a card holding one is not
#: classified - it is unlabelled, and has to be looked up rather than guessed.
NO_CLAIM = frozenset({"", "other", "sports", "sport", "live", "event", "misc",
                      "unknown", "none", "general"})


def _fixture_date(item: Dict[str, Any]) -> str:
    for field in ("start_time", "start_at", "date", "event_date"):
        value = str(item.get(field) or "")
        if len(value) >= 10 and value[4] == "-" and value[7] == "-":
            return value[:10]
    return ""


def needs_fixture_check(item: Dict[str, Any], verdict: Dict[str, Any]) -> bool:
    """Two identifiable sides, and nothing trustworthy saying which sport.

    These are the events a keyword cannot settle, because the keyword is the
    same for all of them: `Boland Cavaliers v Suzuki Griquas` is rugby,
    `Lumezzane vs Giana Erminio` is Serie C football, and `India vs Thailand`
    is either. A decision here has to come from a fixture lookup, not a
    pattern.

    A card whose source names cricket or football outright is left alone -
    that is a real claim from the feed, and re-litigating every one of them
    would mean thousands of lookups to change nothing.
    """
    if len(_participants(item)) < 2:
        return False
    structured = _text(_gather(item, SPORT_FIELDS)).replace("|", " ").split()
    claimed = [token for token in structured if token not in NO_CLAIM]
    if not claimed:
        # Nothing claimed at all: unlabelled, whatever the title happens to
        # look like.
        return True
    if any(token in STRUCTURED_CRICKET or token in STRUCTURED_FOOTBALL
           for token in claimed):
        # The source says cricket or football. Taken at face value.
        return False
    # The source names some other sport. If the rest of the record agrees,
    # there is nothing to resolve; if it disagrees, that is a conflict and the
    # fixture decides.
    return verdict["state"] in (UNKNOWN, CONFIRMED_CRICKET, CONFIRMED_FOOTBALL,
                                LIKELY_CRICKET, LIKELY_FOOTBALL)


def _apply_fixture_verdict(answer: Dict[str, Any]) -> Dict[str, Any]:
    sport = str(answer.get("sport") or "")
    if not answer.get("confirmed"):
        return _verdict(UNKNOWN, "fixture lookup: " + str(answer.get("reason") or
                                                          "not confirmed"))
    if sport == "cricket":
        return _verdict(CONFIRMED_CRICKET, "fixture lookup",
                        str(answer.get("reason") or ""))
    if sport == "football":
        return _verdict(CONFIRMED_FOOTBALL, "fixture lookup",
                        str(answer.get("reason") or ""))
    return _verdict(CONFIRMED_OTHER, f"fixture lookup: {sport}",
                    str(answer.get("reason") or ""))


def resolve(items: Iterable[Dict[str, Any]],
            verify_fixtures: Optional[Any] = None
            ) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """Classify every event, then resolve what the record could not settle.

    Three passes. The keyword stages first, because most events say what they
    are. Then the batch itself, because the same fixture often arrives from a
    second source that did label it. Then, for anything still resting on
    nothing, the fixture is looked up - and that answer overrules the
    patterns, because a pattern was never evidence about these.
    """
    first = [(item, classify(item)) for item in items if isinstance(item, dict)]
    settled = [pair for pair in first if pair[1]["state"] in PUBLISHABLE]

    second = []
    for item, verdict in first:
        if verdict["state"] == UNKNOWN:
            verdict = _second_pass(item, settled) or verdict
        second.append((item, verdict))

    wanted = [(item, verdict) for item, verdict in second
              if needs_fixture_check(item, verdict)]
    if not wanted:
        return second

    if verify_fixtures is None:
        from scanner import fixture_lookup  # noqa: PLC0415 - optional at import
        verify_fixtures = fixture_lookup.verify_many
        key_of = fixture_lookup.cache_key
    else:
        from scanner import fixture_lookup  # noqa: PLC0415
        key_of = fixture_lookup.cache_key

    queries = []
    for item, _ in wanted:
        sides = _participants(item)
        queries.append((sides[0], sides[1], _fixture_date(item),
                        _gather(item, COMPETITION_FIELDS)[:80]))
    answers = verify_fixtures(queries) or {}

    resolved_by_fixture = {}
    for (item, _), query in zip(wanted, queries):
        answer = answers.get(key_of(query[0], query[1], query[2]))
        if isinstance(answer, dict):
            resolved_by_fixture[id(item)] = _apply_fixture_verdict(answer)

    out = []
    for item, verdict in second:
        looked_up = resolved_by_fixture.get(id(item))
        if looked_up is not None:
            verdict = looked_up
        elif needs_fixture_check(item, verdict):
            # The lookup did not get to this one - past the budget, or every
            # provider was quiet. It waits rather than publishing on a guess.
            verdict = _verdict(UNKNOWN, "fixture lookup has not run yet")
        out.append((item, verdict))
    return out


# ── Applying it, and proving afterwards that nothing leaked ──────────────────

def apply(items: List[Dict[str, Any]], verify_fixtures: Optional[Any] = None
          ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Keep cricket and football; hold everything else back, and say what.

    `verify_fixtures` exists so the tests can answer the lookups themselves.
    Left alone, it reaches the real providers.
    """
    kept: List[Dict[str, Any]] = []
    report: Dict[str, Any] = {
        "total": 0, "published": 0, "rejected": 0, "quarantined": 0,
        "states": {}, "by_source": {},
        "rejected_examples": [], "quarantined_events": [], "leaks": [],
    }
    for item, verdict in resolve(items, verify_fixtures=verify_fixtures):
        state = verdict["state"]
        report["total"] += 1
        report["states"][state] = report["states"].get(state, 0) + 1

        sources = item.get("source_ids")
        if not isinstance(sources, list) or not sources:
            sources = [str(item.get("source_id") or "unknown")]
        for source in sources:
            bucket = report["by_source"].setdefault(
                str(source), {"total": 0, "cricket": 0, "football": 0,
                              "quarantined": 0, "rejected": 0, "published": 0})
            bucket["total"] += 1
            if state in CRICKET_STATES:
                bucket["cricket"] += 1
            elif state in FOOTBALL_STATES:
                bucket["football"] += 1

        item["sport_class"] = state
        item["sport_class_reason"] = verdict["reason"]
        if verdict.get("evidence"):
            item["sport_class_evidence"] = verdict["evidence"]

        # The card's badge reads `sport_type`, so a fixture established here
        # has to be written back or the page contradicts the decision that put
        # it there. Seen on Upcoming: `Lumezzane vs Giana Erminio` and
        # `Pergolettese vs Union Brescia` are Serie C football, were published
        # as football, and showed an OTHER badge - because the feed had filed
        # them under "other" and nothing had corrected it.
        #
        # Only ever overwritten in favour of what was actually established:
        # a competition, or a fixture lookup, both of which know more than a
        # feed that shrugged.
        established = ("cricket" if state in CRICKET_STATES
                       else "football" if state in FOOTBALL_STATES else "")
        if established and _text(item.get("sport_type")) != established:
            item["sport_type_from_source"] = item.get("sport_type")
            item["sport_type"] = established

        if state in PUBLISHABLE:
            kept.append(item)
            report["published"] += 1
            for source in sources:
                report["by_source"][str(source)]["published"] += 1
        elif state == UNKNOWN:
            report["quarantined"] += 1
            for source in sources:
                report["by_source"][str(source)]["quarantined"] += 1
            report["quarantined_events"].append({
                "name": str(item.get("name") or "")[:80],
                "source": ", ".join(str(s) for s in sources)[:60],
                "competition": str(item.get("competition") or "")[:60],
                "sport_type": str(item.get("sport_type") or "")[:30],
                "reason": verdict["reason"],
            })
        else:
            report["rejected"] += 1
            for source in sources:
                report["by_source"][str(source)]["rejected"] += 1
            if len(report["rejected_examples"]) < 40:
                report["rejected_examples"].append({
                    "name": str(item.get("name") or "")[:80],
                    "source": ", ".join(str(s) for s in sources)[:60],
                    "competition": str(item.get("competition") or "")[:60],
                    "sport_type": str(item.get("sport_type") or "")[:30],
                    "reason": verdict["reason"],
                    "evidence": verdict["evidence"],
                })

    report["leaks"] = audit_visible(kept)
    return kept, report


def audit_visible(items: Iterable[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Re-read what is about to be published, looking for another sport.

    The filter decides; this checks the decision independently by scanning the
    text of every surviving card against the other-sport gazetteer. A
    `confirmed_*` verdict came from the source's own field or a named
    competition and outranks a stray word, so only the `likely_*` cards are
    re-examined - which is exactly where a wrong guess would hide.
    """
    leaks = []
    for item in items:
        if not isinstance(item, dict):
            continue
        state = str(item.get("sport_class") or "")
        if state in (CONFIRMED_CRICKET, CONFIRMED_FOOTBALL):
            continue
        text = " | ".join(filter(None, (
            _gather(item, SPORT_FIELDS),
            _gather(item, COMPETITION_FIELDS),
            _gather(item, NAME_FIELDS),
        )))
        sport, found = _other_sport_hit(text)
        if sport:
            leaks.append({"name": str(item.get("name") or "")[:70],
                          "sport": sport, "evidence": found,
                          "classified_as": state})
    return leaks


def discard_confirmed_other(items: List[Dict[str, Any]]
                            ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Drop the events that are provably another sport, and only those.

    An early pass, run before the merge has assembled each fixture's metadata.
    At that point a card may have a name and nothing else, so "no evidence" is
    a statement about the pipeline rather than about the match - which is why
    nothing is quarantined here and no fixture is looked up. Only a named other
    sport is removed, because that much is already certain: a title carrying
    `WNBA` or `Currie Cup` is not going to become cricket after enrichment.

    Measured on 2026-08-31: applying the full rules at this stage left 517 of
    1062 events unresolved and lost 201 real cricket and football fixtures.
    Applying only this one loses none of them.
    """
    kept, report = [], {"total": 0, "discarded": 0, "examples": []}
    for item in items:
        if not isinstance(item, dict):
            continue
        report["total"] += 1
        verdict = classify(item)
        if verdict["state"] != CONFIRMED_OTHER:
            kept.append(item)
            continue
        report["discarded"] += 1
        if len(report["examples"]) < 60:
            report["examples"].append({
                "name": str(item.get("name") or "")[:80],
                "source": ", ".join(str(s) for s in (item.get("source_ids") or
                                                     [item.get("source_id") or "?"]))[:60],
                "competition": str(item.get("competition") or "")[:60],
                "sport_type": str(item.get("sport_type") or "")[:30],
                "reason": verdict["reason"],
                "evidence": verdict["evidence"],
            })
    return kept, report


def never_dropped_audit(discarded: Iterable[Dict[str, Any]],
                        quarantined: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Re-read what this filter refused, and check no ball sport is in there.

    The first version of this compared the raw feeds against the final tabs,
    which measured the wrong thing: 105 fixtures came back "missing" and they
    were not missing at all. `ADT vs Sport Huancayo` and `Asociacion Deportiva
    Tarma Vs Sport Huancayo` are one match from two feeds, and the merge had
    folded them into a single card - so the audit was reporting the merge
    working, and the clock retiring finished matches, as losses.

    What this filter can actually be blamed for is what it refused. So that is
    what gets re-read: every discarded and quarantined row, classified again
    from scratch. Anything that comes back cricket or football is a real
    mistake and is named.
    """
    wrongly_refused = []
    for row in list(discarded) + list(quarantined):
        if not isinstance(row, dict):
            continue
        # The rows are decision records - name, source, reason - so the sport
        # is read back from the name and whatever else was kept with it.
        verdict = classify({"name": row.get("name"),
                            "competition": row.get("competition"),
                            "sport_type": row.get("sport_type")})
        if verdict["state"] in CRICKET_STATES or verdict["state"] in FOOTBALL_STATES:
            wrongly_refused.append({
                "name": str(row.get("name") or "")[:80],
                "source": str(row.get("source") or "")[:60],
                "refused_because": str(row.get("reason") or "")[:80],
                "reads_as": verdict["state"],
            })
    return {"refused": len(list(discarded)) + len(list(quarantined)),
            "wrongly_refused": wrongly_refused}


def is_publishable(item: Dict[str, Any]) -> bool:
    return classify(item)["state"] in PUBLISHABLE
