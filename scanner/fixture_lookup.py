"""Ask the world what sport a fixture is, when the feed will not say.

Some events arrive as two team names and nothing else. `Boland Cavaliers v
Suzuki Griquas` is rugby, `Lumezzane vs Giana Erminio` is Serie C football and
`India vs Thailand` could be either - and no pattern in the title separates
them, because the pattern is identical in all three. Deciding those on keywords
is guessing, and a guess in either direction is a bug: a rugby match on the
front page, or a cricket match nobody can find.

So they are looked up. The query is the fixture itself - both sides plus the
date, and the competition when there is one - across three independent
providers:

    thesportsdb   searchevents.php    the fixture, by both names and the date
    thesportsdb   searchteams.php     each side on its own
    wikidata      property P641       each side's sport, as structured data
    wikipedia     page summary        each side's sport, as prose

An answer counts as confirmed in one of two ways, and never as a tally of
lookups:

  * the fixture itself was found and states its sport - that is the whole
    question answered at once, so it stands alone, or
  * two DIFFERENT providers agree about the sides.

Counting lookups instead of providers is what let `India vs Thailand` confirm
as football on the first attempt: one provider holds a football side called
India and a football side called Thailand, which says what that provider
indexes, not what this match is. Both nations field a cricket team too, and no
fixture was found, so the honest answer is that nobody knows.

    cricket or football, confirmed   ->  publish
    another sport, confirmed         ->  reject
    nothing confirmed                ->  quarantine, and say so

A provider that is rate-limited or unreachable is recorded as unavailable, not
as silence. The difference matters: silence from three working providers is an
answer about the fixture, while three refused requests are an answer about the
network, and caching the second as though it were the first would bury a real
match for as long as the cache holds.

Confirmed answers are cached permanently, because a fixture's sport does not
change. That is what keeps this affordable inside a scan that runs every twenty
minutes: a fixture is looked up once, ever.
"""
from __future__ import annotations

import concurrent.futures
import json
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

CACHE_PATH = Path("state") / "fixture-sport-lookups.json"
#: A scan should never spend its time here. Past this the remaining fixtures
#: stay unanswered - which holds them back rather than publishing them on a
#: guess - and the next scan picks them up with the earlier answers cached.
MAX_LOOKUPS_PER_SCAN = 40
LOOKUP_WORKERS = 2
TIMEOUT_SECONDS = 15
#: Politeness between calls. These are free endpoints and a scan that hammers
#: them gets 429s, which is how the first version of this managed to quarantine
#: every fixture it looked at.
PAUSE_BETWEEN_CALLS = 0.35
#: A confirmed answer is permanent. A "nobody knows" is retried in case the
#: fixture simply had not been published yet; a "nobody answered" is retried
#: much sooner, because that was the network, not the fixture.
UNRESOLVED_RETRY_SECONDS = 6 * 60 * 60
UNAVAILABLE_RETRY_SECONDS = 20 * 60

USER_AGENT = "ClickTV/1.0 (fixture sport verification)"
SPORTSDB = "https://www.thesportsdb.com/api/v1/json/3"
WIKIPEDIA = "https://en.wikipedia.org/api/rest_v1/page/summary"
WIKIDATA = "https://www.wikidata.org/w/api.php"

CRICKET = "cricket"
FOOTBALL = "football"

#: What the providers call each sport, mapped onto what this project calls it.
SPORT_ALIASES = {
    "soccer": FOOTBALL,
    "football": FOOTBALL,
    "association football": FOOTBALL,
    "women's association football": FOOTBALL,
    "cricket": CRICKET,
    "women's cricket": CRICKET,
    "rugby": "rugby",
    "rugby union": "rugby",
    "rugby league": "rugby",
    "american football": "gridiron",
    "canadian football": "gridiron",
    "basketball": "basketball",
    "baseball": "baseball",
    "ice hockey": "hockey",
    "field hockey": "hockey",
    "hockey": "hockey",
    "tennis": "tennis",
    "golf": "golf",
    "motorsport": "motorsport",
    "auto racing": "motorsport",
    "esports": "esports",
    "volleyball": "volleyball",
    "handball": "handball",
    "netball": "netball",
    "kabaddi": "kabaddi",
    "australian rules football": "australian rules",
    "horse racing": "horse racing",
}

#: Wikipedia writes prose, so the sport is read from whole words in the
#: summary. Gridiron, Australian rules and rugby all describe themselves as
#: football, so those are read before the bare word - "BC Lions" reads as
#: football on Wikipedia and is gridiron.
WIKI_PATTERNS = [
    (re.compile(r"\b(?:canadian|american|gridiron)\s+football\b", re.I), "gridiron"),
    (re.compile(r"\baustralian\s+(?:rules\s+)?football\b", re.I), "australian rules"),
    (re.compile(r"\brugby\b", re.I), "rugby"),
    (re.compile(r"\bcricket\b", re.I), CRICKET),
    (re.compile(r"\b(?:association )?football\b|\bfootball club\b|\bsoccer\b", re.I),
     FOOTBALL),
    (re.compile(r"\bbasketball\b", re.I), "basketball"),
    (re.compile(r"\bbaseball\b", re.I), "baseball"),
    (re.compile(r"\bice hockey\b|\bfield hockey\b", re.I), "hockey"),
    (re.compile(r"\bvolleyball\b", re.I), "volleyball"),
    (re.compile(r"\bhandball\b", re.I), "handball"),
    (re.compile(r"\btennis\b", re.I), "tennis"),
    (re.compile(r"\bkabaddi\b", re.I), "kabaddi"),
]

#: What a fetch returns: the body, or "" with a flag saying nobody answered.
UNAVAILABLE = "__unavailable__"


def _context():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _fetch(url: str) -> str:
    """The body, or UNAVAILABLE when the provider refused to answer at all.

    A 429 is not a fact about the fixture. Returning "" for it would let a
    rate-limited minute look exactly like a fixture no provider has heard of.
    """
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS,
                                    context=_context()) as response:
            if response.status == 200:
                return response.read(400_000).decode("utf-8", "replace")
            return UNAVAILABLE
    except urllib.error.HTTPError as failure:
        # 404 is a real answer - there is no such page. Everything else,
        # including 429 and 5xx, is the provider declining to answer.
        return "" if failure.code == 404 else UNAVAILABLE
    except Exception:  # noqa: BLE001 - a lookup must never break a scan
        return UNAVAILABLE


def _normalise(sport: str) -> str:
    return SPORT_ALIASES.get(" ".join(str(sport or "").split()).casefold(), "")


def _json(text: str) -> Dict[str, Any]:
    if not text or text == UNAVAILABLE:
        return {}
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


class _Session:
    """One fixture's worth of lookups, remembering which providers went quiet."""

    def __init__(self, fetch: Callable[[str], str]):
        self._fetch = fetch
        self.unavailable: set = set()

    def get(self, provider: str, url: str) -> Dict[str, Any]:
        body = self._fetch(url)
        if body == UNAVAILABLE:
            self.unavailable.add(provider)
            return {}
        if PAUSE_BETWEEN_CALLS:
            time.sleep(PAUSE_BETWEEN_CALLS)
        return _json(body)


# ── The providers ────────────────────────────────────────────────────────────

def _sportsdb_fixture(session: _Session, team_a: str, team_b: str,
                      date: str) -> Tuple[str, str]:
    """The match itself, found by both names and - when known - the date."""
    url = f"{SPORTSDB}/searchevents.php?e={urllib.parse.quote(f'{team_a}_vs_{team_b}')}"
    if date:
        url += f"&d={urllib.parse.quote(date)}"
    for event in session.get("thesportsdb", url).get("event") or []:
        if not isinstance(event, dict):
            continue
        # With a date in the query the provider still returns near misses, so
        # the date is checked rather than trusted.
        if date and str(event.get("dateEvent") or "") != date:
            continue
        sport = _normalise(event.get("strSport"))
        if sport:
            return sport, (f"thesportsdb fixture: {event.get('strEvent')}"
                           f" / {event.get('strLeague')}")
    return "", ""


def _sportsdb_team(session: _Session, name: str) -> Tuple[str, str]:
    url = f"{SPORTSDB}/searchteams.php?t={urllib.parse.quote(name)}"
    for team in session.get("thesportsdb", url).get("teams") or []:
        if not isinstance(team, dict):
            continue
        sport = _normalise(team.get("strSport"))
        if sport:
            return sport, f"thesportsdb team: {team.get('strTeam')} / {team.get('strLeague')}"
    return "", ""


def _wikipedia_topic(session: _Session, name: str) -> Tuple[str, str]:
    slug = urllib.parse.quote(name.replace(" ", "_"))
    summary = session.get("wikipedia", f"{WIKIPEDIA}/{slug}")
    extract = str(summary.get("extract") or "")
    if not extract:
        return "", ""
    for pattern, sport in WIKI_PATTERNS:
        if pattern.search(extract):
            return sport, f"wikipedia: {summary.get('title')}"
    return "", ""


def _wikidata_topic(session: _Session, name: str) -> Tuple[str, str]:
    """Wikidata's `sport` (P641) claim - structured, so no prose to misread."""
    search = session.get("wikidata", (
        f"{WIKIDATA}?action=wbsearchentities&format=json&language=en&limit=3"
        f"&search={urllib.parse.quote(name)}"))
    for hit in (search.get("search") or [])[:3]:
        entity = hit.get("id")
        if not entity:
            continue
        claims = session.get("wikidata", (
            f"{WIKIDATA}?action=wbgetclaims&format=json&entity={entity}"
            "&property=P641")).get("claims") or {}
        for claim in claims.get("P641") or []:
            try:
                sport_id = claim["mainsnak"]["datavalue"]["value"]["id"]
            except (KeyError, TypeError):
                continue
            labels = session.get("wikidata", (
                f"{WIKIDATA}?action=wbgetentities&format=json&ids={sport_id}"
                "&props=labels&languages=en")).get("entities") or {}
            try:
                label = labels[sport_id]["labels"]["en"]["value"]
            except (KeyError, TypeError):
                continue
            sport = _normalise(label)
            if sport:
                return sport, f"wikidata: {hit.get('label')} ({label})"
    return "", ""


#: Feeds decorate a competition with a round or a group - "Paranaense - 3",
#: "Serie C - Girone A" - and the encyclopaedias hold the plain name.
_COMPETITION_TAIL = re.compile(
    r"\s*[-–]\s*(?:girone\s*\w+|group\s*\w+|round\s*\w+|\d+|[ivx]+)\s*$",
    re.IGNORECASE)


_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")


def _competition_topic(competition: str) -> str:
    """The plain league name an encyclopaedia files an article under."""
    name = " ".join(str(competition or "").split())
    if not name:
        return ""
    name = _COMPETITION_TAIL.sub("", name).strip(" -–,|")
    # A season year is not part of the name, and what is left has to be
    # more than a fragment: "A" or "2026" would match anything or nothing.
    name = _YEAR.sub("", name).strip(" -,|")
    return " ".join(name.split()) if len(name.strip()) > 3 else ""


# ── Weighing what came back ──────────────────────────────────────────────────

def verify(team_a: str, team_b: str, date: str = "", competition: str = "",
           fetch: Callable[[str], str] = _fetch) -> Dict[str, Any]:
    """What sport is this fixture? Confirmed by two independents, or nothing."""
    team_a = " ".join(str(team_a or "").split())
    team_b = " ".join(str(team_b or "").split())
    if not team_a or not team_b:
        return {"sport": "", "confirmed": False, "signals": [], "unavailable": [],
                "reason": "fewer than two identifiable sides"}

    session = _Session(fetch)
    signals: List[str] = []
    backers: Dict[str, set] = {}

    def record(sport: str, provider: str, note: str):
        if not sport:
            return
        backers.setdefault(sport, set()).add(provider)
        signals.append(f"{note} -> {sport}")

    # The fixture itself: these two sides, on this date, with the sport stated.
    # That is the whole question answered at once, so it stands alone.
    sport, note = _sportsdb_fixture(session, team_a, team_b, date)
    reason = "the fixture itself names this sport"
    if not sport and date:
        # No fixture on that date. These two sides meeting on any date is
        # still evidence about the sport - clubs do not change codes between
        # seasons - so it is taken, and labelled as the weaker thing it is.
        sport, note = _sportsdb_fixture(session, team_a, team_b, "")
        reason = "these two sides meet in this sport, though not on that date"
    if sport:
        signals.append(f"{note} -> {sport}")
        return {"sport": sport, "confirmed": True, "signals": signals,
                "unavailable": sorted(session.unavailable), "reason": reason}

    # The competition is evidence about the fixture too, and often the only
    # evidence there is: a third-tier Brazilian state league fields teams no
    # provider indexes, while the league itself has an article saying what
    # sport it is. Looked up rather than pattern-matched, so it is a provider
    # answering and not a keyword firing.
    topics = [team_a, team_b]
    league = _competition_topic(competition)
    if league:
        topics.append(league)

    for side in (team_a, team_b):
        found, note = _sportsdb_team(session, side)
        record(found, "thesportsdb", note)
    for topic in topics:
        found, note = _wikidata_topic(session, topic)
        record(found, "wikidata", note)
    for topic in topics:
        found, note = _wikipedia_topic(session, topic)
        record(found, "wikipedia", note)

    unavailable = sorted(session.unavailable)
    if not backers:
        reason = ("no provider answered" if unavailable
                  else "no provider recognised this fixture")
        return {"sport": "", "confirmed": False, "signals": signals,
                "unavailable": unavailable, "reason": reason}

    agreed = sorted(((sport, providers) for sport, providers in backers.items()
                     if len(providers) >= 2), key=lambda pair: -len(pair[1]))
    if not agreed:
        seen = ", ".join(sorted(backers))
        reason = (f"providers disagree: {seen}" if len(backers) > 1
                  else f"only one provider knows this ({seen})")
        return {"sport": "", "confirmed": False, "signals": signals,
                "unavailable": unavailable, "reason": reason}
    if len(agreed) > 1 and len(agreed[0][1]) == len(agreed[1][1]):
        return {"sport": "", "confirmed": False, "signals": signals,
                "unavailable": unavailable,
                "reason": "providers disagree: " + ", ".join(s for s, _ in agreed)}
    best, providers = agreed[0]
    return {"sport": best, "confirmed": True, "signals": signals,
            "unavailable": unavailable,
            "reason": f"{len(providers)} providers agree on {best}"}


def home_side_with_status(team_a: str, team_b: str, date: str = "",
                          fetch: Callable[[str], str] = _fetch
                          ) -> Tuple[str, bool]:
    """Which of the two is the home team, as the fixture itself records it.

    Two feeds can order one fixture two ways - `Real Sociedad vs RC Celta` and
    `Celta Vigo vs Real Sociedad` were both published for one LaLiga match -
    and neither is authoritative on its own. The fixture is, so it is asked.

    The provider's own search is the answer: `searchevents.php?e=A_vs_B` finds
    the match only when A is the home side, so asking both ways round says
    which is which. Reading `strHomeTeam` out of an undated search does not
    work - it happily returns the reverse leg, and answered "Real Madrid" for a
    match played at Betis.

    Returns the home side spelled as the caller spelled it, or "" when the
    fixture is not found or the two answers disagree.
    """
    team_a = " ".join(str(team_a or "").split())
    team_b = " ".join(str(team_b or "").split())
    if not team_a or not team_b or not date:
        return "", False
    session = _Session(fetch)

    def found(first: str, second: str) -> bool:
        query = urllib.parse.quote(f"{first}_vs_{second}")
        events = session.get(
            "thesportsdb",
            f"{SPORTSDB}/searchevents.php?e={query}&d={urllib.parse.quote(date)}",
        ).get("event") or []
        return any(isinstance(event, dict)
                   and str(event.get("dateEvent") or "") == date
                   for event in events)

    a_at_home = found(team_a, team_b)
    b_at_home = found(team_b, team_a)
    if a_at_home and not b_at_home:
        return team_a, False
    if b_at_home and not a_at_home:
        return team_b, False
    # No answer. Whether the provider had nothing to say or never spoke at
    # all is the difference between a fixture nobody records and a minute
    # of rate limiting, and the caller has to be able to tell them apart:
    # 52 of 52 lookups came back empty from the CI runner while the same
    # fixture resolved locally, and the cache recorded that as 52 unknown
    # fixtures to retry in six hours.
    return "", bool(session.unavailable)


def home_side(team_a: str, team_b: str, date: str = "",
              fetch: Callable[[str], str] = _fetch) -> str:
    """The home side, or "". Says nothing about why it is "".""" 
    return home_side_with_status(team_a, team_b, date, fetch)[0]

# ── The cache, because a fixture's sport does not change ─────────────────────

def cache_key(team_a: str, team_b: str, date: str = "") -> str:
    sides = sorted(" ".join(str(s or "").split()).casefold() for s in (team_a, team_b))
    return f"{sides[0]}|{sides[1]}|{date}"


def load_cache(path: Path = CACHE_PATH) -> Dict[str, Any]:
    try:
        parsed = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def save_cache(cache: Dict[str, Any], path: Path = CACHE_PATH) -> None:
    try:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(cache, indent=1, sort_keys=True),
                          encoding="utf-8")
    except OSError:
        pass


def _still_good(entry: Any, now: float) -> bool:
    if not isinstance(entry, dict):
        return False
    if entry.get("confirmed"):
        return True
    written = entry.get("checked_at")
    if not isinstance(written, (int, float)):
        return False
    # A verdict reached while a provider was refusing to answer is about the
    # network, so it is retried on the next scan rather than held for hours.
    window = (UNAVAILABLE_RETRY_SECONDS if entry.get("unavailable")
              else UNRESOLVED_RETRY_SECONDS)
    return (now - written) < window


def verify_many(fixtures: List[Tuple[str, str, str, str]],
                fetch: Callable[[str], str] = _fetch,
                cache_path: Path = CACHE_PATH,
                budget: int = MAX_LOOKUPS_PER_SCAN) -> Dict[str, Dict[str, Any]]:
    """Look up every fixture that is not already answered, and remember it.

    Returns {cache_key: verdict}.
    """
    now = time.time()
    cache = load_cache(cache_path)
    answers: Dict[str, Dict[str, Any]] = {}
    pending: List[Tuple[str, Tuple[str, str, str, str]]] = []
    queued: set = set()

    for fixture in fixtures:
        team_a, team_b, date, _competition = fixture
        key = cache_key(team_a, team_b, date)
        entry = cache.get(key)
        if _still_good(entry, now):
            answers[key] = entry
            continue
        if key not in queued:
            queued.add(key)
            pending.append((key, fixture))

    if not pending:
        return answers

    def run(job):
        key, (team_a, team_b, date, competition) = job
        verdict = verify(team_a, team_b, date, competition, fetch=fetch)
        verdict["checked_at"] = now
        verdict["fixture"] = f"{team_a} vs {team_b}"
        return key, verdict

    with concurrent.futures.ThreadPoolExecutor(max_workers=LOOKUP_WORKERS) as pool:
        for key, verdict in pool.map(run, pending[:budget]):
            answers[key] = verdict
            cache[key] = verdict

    save_cache(cache, cache_path)
    return answers

#: Home/away answers live beside the sport answers, under their own key, so
#: one confirmed reading is never re-bought and never confused with a sport
#: verdict for the same fixture.
HOME_AWAY_PREFIX = "home_away|"
#: `home_side` costs two requests. A confirmed answer is permanent, so the
#: cost is paid once per fixture rather than once per scan: with around a
#: hundred cards on the page, twelve pairs a scan needed nine scans to reach
#: the end of the list, and `Real Madrid vs Real Betis` sat near the end of
#: it, still reversed after a full run. Forty covers the list in three scans
#: and then only asks about fixtures it has never seen before.
MAX_HOME_AWAY_LOOKUPS_PER_SCAN = 40


def resolve_home_sides(
    fixtures: List[Tuple[str, str, str]],
    fetch: Callable[[str], str] = _fetch,
    cache_path: Path = CACHE_PATH,
    budget: int = MAX_HOME_AWAY_LOOKUPS_PER_SCAN,
) -> Dict[str, str]:
    """Which side is at home, for each (team_a, team_b, date). Cached.

    Returns {cache_key: home side as the caller spelled it}. A fixture the
    provider could not settle is absent rather than guessed at, because
    turning a card round on no evidence is worse than leaving the order the
    feed sent - `Real Madrid vs Real Betis` is wrong, and so is reversing a
    fixture that was right.
    """
    now = time.time()
    cache = load_cache(cache_path)
    answers: Dict[str, str] = {}
    #: (cache key, fixture, whether this is a retry rather than a first ask)
    pending: List[Tuple[str, Tuple[str, str, str], bool]] = []
    queued: set = set()

    for team_a, team_b, date in fixtures:
        if not team_a or not team_b or not date:
            continue
        key = HOME_AWAY_PREFIX + cache_key(team_a, team_b, date)
        entry = cache.get(key)
        if _still_good(entry, now):
            home = str(entry.get("home") or "")
            if home:
                answers[key] = home
            continue
        if key not in queued:
            queued.add(key)
            pending.append((key, (team_a, team_b, date), key in cache))

    if not pending:
        return answers

    # A fixture nobody has asked about yet goes first. Truncating the queue
    # in list order starved the tail of the page: the cards near the top
    # fall due again every twenty minutes and ate the whole budget, so
    # `Real Madrid vs Real Betis` sat at position 100-odd and had still
    # never been asked after several scans, while nine other fixtures were
    # resolved and two of them corrected. Retries keep their turn - they
    # just take it after everything that has never had one.
    pending.sort(key=lambda job: job[2])

    def run(job):
        key, (team_a, team_b, date), _retry = job
        try:
            home, unavailable = home_side_with_status(
                team_a, team_b, date, fetch=fetch)
        except Exception:  # noqa: BLE001 - a lookup must never break a scan
            home, unavailable = "", True
        return key, {
            "home": home,
            "confirmed": bool(home),
            # A provider that never answered is not a fixture nobody
            # records: _still_good retries this in twenty minutes rather
            # than holding a non-answer for six hours.
            "unavailable": unavailable,
            "checked_at": now,
            "fixture": f"{team_a} vs {team_b}",
        }

    asked = silent = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=LOOKUP_WORKERS) as pool:
        for key, entry in pool.map(run, pending[:budget]):
            cache[key] = entry
            asked += 1
            if entry["home"]:
                answers[key] = entry["home"]
            elif entry["unavailable"]:
                silent += 1

    save_cache(cache, cache_path)
    if asked and not answers:
        # Said out loud, because this is what a feature that never fires
        # in production looks like from the inside: it resolved locally
        # and returned nothing at all from the CI runner.
        print(f"   home/away lookups: {asked} asked, none resolved"
              f"{f', {silent} unanswered by the provider' if silent else ''}")
    return answers


def home_side_from(answers: Mapping[str, str], team_a: str, team_b: str,
                   date: str) -> str:
    """The answer `resolve_home_sides` gave for one fixture, or ""."""
    return str(answers.get(HOME_AWAY_PREFIX + cache_key(team_a, team_b, date))
               or "")
