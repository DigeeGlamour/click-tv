"""Supplementary sports artwork (match poster, team/league badges), tried
only when the existing Streamed enrichment (scanner/streamed_provider.py)
did not already give a card a poster.

Live-tested findings this was built against:
  - TheSportsDB's own event search already returns the event poster/
    thumbnail/banner *and* both team badges *and* the league badge in one
    call - "Poster -> Thumbnail -> Fanart -> Team Logos" (the priority the
    key was handed over with) is read directly off that one response, not
    assembled from several lookups.
  - Highlightly has no name-search endpoint; its matches endpoint takes a
    date (or a league id), not team names, so a fixture is found by pulling
    that date's matches and matching team names against it - the same
    reason the Streamed integration matches by participants rather than by
    the provider's own id.
  - Cloudflare in front of Highlightly rejects a bare/non-browser
    User-Agent outright (its own error code 1010); the browser-like
    default here is required, not decorative.
  - Sportmonks authenticates correctly with the key handed over, but its
    free plan is restricted to two specific leagues (Danish Superliga,
    Scottish Premiership) - even a team search for a club that plays in one
    of those two returned no results against the live API, so in practice
    this provider contributes for almost nothing Click TV actually carries.
    Implemented anyway, degrading to "" like every other provider here,
    per direct request that nothing be left out.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

REQUEST_TIMEOUT_SECONDS = 10
_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

THESPORTSDB_EVENT_SEARCH_URL = "https://www.thesportsdb.com/api/v1/json/{key}/searchevents.php"
THESPORTSDB_TEAM_SEARCH_URL = "https://www.thesportsdb.com/api/v1/json/{key}/searchteams.php"
# TheSportsDB's own published open test key - not a private credential, and
# not something to store as a secret the way the other providers' keys are.
THESPORTSDB_DEFAULT_KEY = "123"

HIGHLIGHTLY_BASE_URLS = {
    "football": "https://soccer.highlightly.net",
    "soccer": "https://soccer.highlightly.net",
    "cricket": "https://cricket.highlightly.net",
}

SPORTMONKS_TEAM_SEARCH_URL = "https://api.sportmonks.com/v3/football/teams/search/{query}"


#: Per-host state for this process. A provider that starts rate-limiting is
#: asked once more and then left alone for the rest of the scan.
#:
#: Measured 2026-08-20: THESPORTSDB_API_KEY was not set, so every lookup used
#: THESPORTSDB_DEFAULT_KEY. The first calls returned full artwork (poster,
#: thumbnail and both team badges); after roughly forty the host answered
#: HTTP 429 to everything, including queries that had just succeeded. Because
#: every error was swallowed into {}, a rate-limited scan was indistinguishable
#: from "this fixture has no artwork", so 34 of 37 Upcoming cards published with
#: no logo and nothing anywhere said why.
_RATE_LIMIT_STATUSES = frozenset({429, 503})
_host_state: Dict[str, Dict[str, Any]] = {}

#: Filled in by the caller so a scan report can show what actually happened.
LOOKUP_STATS: Dict[str, int] = {
    "requests": 0,
    "hits": 0,
    "empty": 0,
    "rate_limited": 0,
    "errors": 0,
    "skipped_rate_limited": 0,
}


def reset_lookup_stats() -> None:
    for key in LOOKUP_STATS:
        LOOKUP_STATS[key] = 0
    _host_state.clear()


def thesportsdb_key_source() -> str:
    """Which key the next lookup will use: "env" or "public_test_key".

    THESPORTSDB_DEFAULT_KEY is TheSportsDB's own published open key. It works,
    and it is rate-limited hard: on 2026-08-20 the first calls of a scan
    returned full artwork and after roughly forty the host answered HTTP 429 to
    everything, including queries that had just succeeded. Nothing said so,
    because the key is substituted silently. A scan that is running on the
    public key is a scan whose artwork coverage is capped by someone else's
    quota, so it is reported rather than assumed.
    """
    return "env" if os.getenv("THESPORTSDB_API_KEY", "").strip() else "public_test_key"


def _host_of(url: str) -> str:
    try:
        return urllib.parse.urlsplit(url).netloc.casefold()
    except ValueError:
        return url[:64]


def provider_is_rate_limited(url: str) -> bool:
    """Whether this host has already answered "too many requests" this scan."""
    return bool(_host_state.get(_host_of(url), {}).get("rate_limited"))


def _get_json(url: str, *, headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    host = _host_of(url)
    state = _host_state.setdefault(host, {"rate_limited": False, "strikes": 0})
    if state["rate_limited"]:
        # Asking again costs a round trip and returns the same 429. Every later
        # lookup for this host is skipped for the rest of the scan.
        LOOKUP_STATS["skipped_rate_limited"] += 1
        return {}

    request_headers = {"Accept": "application/json", "User-Agent": _USER_AGENT}
    request_headers.update(headers or {})
    request = urllib.request.Request(url, headers=request_headers)
    LOOKUP_STATS["requests"] += 1
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            raw = response.read(2_000_000)
        payload = json.loads(raw.decode("utf-8", errors="replace"))
        if isinstance(payload, dict):
            LOOKUP_STATS["hits" if payload else "empty"] += 1
            return payload
        LOOKUP_STATS["empty"] += 1
        return {}
    except urllib.error.HTTPError as error:
        if error.code in _RATE_LIMIT_STATUSES:
            LOOKUP_STATS["rate_limited"] += 1
            state["strikes"] += 1
            # Two in a row is the host, not this one query.
            if state["strikes"] >= 2:
                state["rate_limited"] = True
        else:
            LOOKUP_STATS["errors"] += 1
        return {}
    except (
        urllib.error.URLError,
        TimeoutError,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        ValueError,
    ):
        LOOKUP_STATS["errors"] += 1
        return {}


#: Club affixes a source spells out and TheSportsDB usually does not, and the
#: reverse. Measured against the 2026-08-20 Upcoming list: "FC Sion vs Ajax",
#: "FC Lugano vs Maccabi Tel Aviv", "Rangers vs FK Jablonec", "FC Midtjylland
#: vs HNK Rijeka" and "Motherwell vs SC Freiburg" all failed on the verbatim
#: name and are the shape this retry exists for.
_CLUB_AFFIXES = (
    "fc", "fk", "sc", "cd", "ac", "as", "afc", "cf", "sk", "hnk", "nk",
    "us", "ss", "ssc", "sv", "vfl", "vfb", "bsc", "club", "cs", "ca",
)


def _strip_club_affixes(name: str) -> str:
    """"FC Sion" -> "Sion", "HNK Rijeka" -> "Rijeka", "Al Hilal Saudi FC" ->
    "Al Hilal Saudi". Never empties the name."""
    words = [w for w in str(name or "").split() if w]
    if len(words) > 1 and words[0].strip(".").casefold() in _CLUB_AFFIXES:
        words = words[1:]
    if len(words) > 1 and words[-1].strip(".").casefold() in _CLUB_AFFIXES:
        words = words[:-1]
    return " ".join(words).strip() or str(name or "").strip()


def thesportsdb_event_artwork_with_retry(
    home_team: str, away_team: str
) -> Dict[str, str]:
    """The verbatim pair first, then the same pair with club affixes removed.

    One extra request only when the first found nothing, and none at all once
    the host is rate-limited.
    """
    artwork = thesportsdb_event_artwork(home_team, away_team)
    if artwork:
        return artwork
    home = _strip_club_affixes(home_team)
    away = _strip_club_affixes(away_team)
    if (home, away) == (str(home_team or "").strip(), str(away_team or "").strip()):
        return {}
    return thesportsdb_event_artwork(home, away)


def thesportsdb_event_artwork(home_team: str, away_team: str) -> Dict[str, str]:
    """One TheSportsDB event search, read for poster, thumbnail, banner,
    both team badges and the league badge at once. Returns {} rather than
    partial keys when the event itself is not found."""
    home = str(home_team or "").strip()
    away = str(away_team or "").strip()
    if not home or not away:
        return {}
    key = os.getenv("THESPORTSDB_API_KEY", "").strip() or THESPORTSDB_DEFAULT_KEY
    query = f"{home}_vs_{away}".replace(" ", "_")
    url = THESPORTSDB_EVENT_SEARCH_URL.format(key=key) + "?" + urllib.parse.urlencode({"e": query})
    payload = _get_json(url)
    events = payload.get("event")
    if not isinstance(events, list) or not events or not isinstance(events[0], dict):
        return {}
    event = events[0]
    result = {
        "poster": str(event.get("strPoster") or "").strip(),
        "thumbnail": str(event.get("strThumb") or "").strip(),
        "banner": str(event.get("strBanner") or "").strip(),
        "home_badge": str(event.get("strHomeTeamBadge") or "").strip(),
        "away_badge": str(event.get("strAwayTeamBadge") or "").strip(),
        "league_badge": str(event.get("strLeagueBadge") or "").strip(),
    }
    return {k: v for k, v in result.items() if v}


def thesportsdb_best_poster(home_team: str, away_team: str) -> str:
    """Poster -> Thumbnail -> Fanart(banner) -> team logos, as handed over."""
    artwork = thesportsdb_event_artwork(home_team, away_team)
    for field in ("poster", "thumbnail", "banner", "home_badge", "away_badge"):
        if artwork.get(field):
            return artwork[field]
    return ""


def thesportsdb_team_badge(team_name: str) -> str:
    name = str(team_name or "").strip()
    if not name:
        return ""
    key = os.getenv("THESPORTSDB_API_KEY", "").strip() or THESPORTSDB_DEFAULT_KEY
    url = THESPORTSDB_TEAM_SEARCH_URL.format(key=key) + "?" + urllib.parse.urlencode({"t": name})
    payload = _get_json(url)
    teams = payload.get("teams")
    if isinstance(teams, list) and teams and isinstance(teams[0], dict):
        return str(teams[0].get("strBadge") or teams[0].get("strLogo") or "").strip()
    return ""


def _highlightly_base_url(sport: str) -> str:
    return HIGHLIGHTLY_BASE_URLS.get(str(sport or "").strip().casefold(), "")


def highlightly_match_artwork(
    home_team: str, away_team: str, sport: str = "football", date: str = ""
) -> Dict[str, str]:
    """Highlightly has no name-search endpoint - a fixture is found by
    pulling one date's matches and matching team names against it, the same
    reason the Streamed integration matches by participants rather than by
    the provider's own id. `date` is YYYY-MM-DD; the caller supplies it."""
    api_key = os.getenv("HIGHLIGHTLY_API_KEY", "").strip()
    base_url = _highlightly_base_url(sport)
    home = str(home_team or "").strip().casefold()
    away = str(away_team or "").strip().casefold()
    if not api_key or not base_url or not home or not away or not date:
        return {}
    url = base_url + "/matches?" + urllib.parse.urlencode({"date": date, "limit": 50})
    payload = _get_json(url, headers={"x-rapidapi-key": api_key})
    matches = payload.get("data")
    if not isinstance(matches, list):
        return {}
    for match in matches:
        if not isinstance(match, dict):
            continue
        match_home = str((match.get("homeTeam") or {}).get("name") or "").strip().casefold()
        match_away = str((match.get("awayTeam") or {}).get("name") or "").strip().casefold()
        if {match_home, match_away} != {home, away}:
            continue
        result = {
            "home_badge": str((match.get("homeTeam") or {}).get("logo") or "").strip(),
            "away_badge": str((match.get("awayTeam") or {}).get("logo") or "").strip(),
            "league_badge": str((match.get("league") or {}).get("logo") or "").strip(),
        }
        return {k: v for k, v in result.items() if v}
    return {}


def sportmonks_team_badge(team_name: str) -> str:
    """Authenticates correctly with the key handed over, but the free plan
    covers only two leagues (Danish Superliga, Scottish Premiership) - a
    team search outside those returns nothing, confirmed live, so this
    contributes for almost nothing Click TV actually carries in practice."""
    name = str(team_name or "").strip()
    api_token = os.getenv("SPORTMONKS_API_TOKEN", "").strip()
    if not name or not api_token:
        return ""
    url = SPORTMONKS_TEAM_SEARCH_URL.format(query=urllib.parse.quote(name)) + "?" + urllib.parse.urlencode(
        {"api_token": api_token}
    )
    payload = _get_json(url)
    data = payload.get("data")
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return str(data[0].get("image_path") or "").strip()
    return ""


def supplementary_sports_poster_lookup(
    home_team: str, away_team: str, *, sport: str = "football", date: str = ""
) -> str:
    """First non-empty poster/badge wins, tried in the order above."""
    for lookup in (
        lambda: thesportsdb_best_poster(home_team, away_team),
        lambda: (highlightly_match_artwork(home_team, away_team, sport, date) or {}).get("home_badge", ""),
        lambda: sportmonks_team_badge(home_team),
    ):
        try:
            poster = lookup()
        except Exception:  # pragma: no cover - a provider must never break a scan
            poster = ""
        if poster:
            return poster
    return ""
