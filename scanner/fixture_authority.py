"""What a fixture IS, read from a feed whose subject is fixtures.

Every event source we publish from is a stream playlist that happens to name
a match. `events.fixture_authority_sources` lists all twenty-one of them, so
the word "authority" there means "configured" and separates nothing: on
2026-09-07 the catalogue was dead (`schedulable_now: 0`), `matched` was 0,
`provider_fixture` was 284, and 80 of 82 published cards rested on a single
upstream family. Every published card carried `schedule_verified: true` and
`time_verification: provider_feed`, which means a feed said so - not that
anything was checked.

This module reads two feeds whose subject is the fixture itself:

  LiveScore   prod-public-api.livescore.com/v1/api/app/date/{sport}/{date}/0
  ESPN        site.web.api.espn.com/apis/v2/scoreboard/header

Both were probed from a GitHub Actions runner before a line of this was
written - CI_REACHABLE and ESPN_HEADER_CI_REACHABLE, unauthenticated, no
secret - and the status vocabularies below contain only states that came back
in a real response.

**Shadow only.** Nothing here may add a card, remove a card, change a status,
a kickoff, a lifecycle state, a badge, or `fixture_id`. It observes and it
writes a report. Two rules make that safe rather than merely intended:

  * a fetch that fails is INCONCLUSIVE - *authority unavailable* - and is
    never evidence that a fixture ended or never existed. `live_protection`
    already applies that rule to an inconclusive probe, and the catalogue
    replay proved the cost of the other reading: treating "cannot check" as
    "confirmed dead" dropped 16 of 21 cards.
  * a status that has not been observed in a real response maps to UNKNOWN.
    Football half-time is the standing example: 6,240 LiveScore soccer events
    across 17 dates and not one in play at the sampling hour, so HT is absent
    here rather than guessed. ESPN has shown STATUS_SECOND_HALF exactly once
    and STATUS_HALFTIME never.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
from concurrent import futures
import ssl
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    from scanner import upstream_family
except ImportError:                                              # pragma: no cover
    import upstream_family                                       # type: ignore

DEFAULT_REGISTRY_PATH = "config/sources/fixture-authority.json"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)
DEFAULT_TIMEOUT_SECONDS = 12
#: Nothing bigger than this is read. ESPN's soccer day is about 1 MB at
#: limit=300; the cap is generous and exists so a redirected host cannot
#: stream forever into a scan that has a time budget.
MAX_RESPONSE_BYTES = 8 * 1024 * 1024

# --- the normalized vocabulary -------------------------------------------
#: What this module is willing to say about a fixture's state. Deliberately
#: smaller than either feed's vocabulary, and UNKNOWN is a real answer.
UPCOMING = "UPCOMING"
LIVE = "LIVE"
FINISHED = "FINISHED"
POSTPONED = "POSTPONED"
CANCELLED = "CANCELLED"
ABANDONED = "ABANDONED"
SUSPENDED = "SUSPENDED"
NO_RESULT = "NO_RESULT"
UNKNOWN = "UNKNOWN"

NORMALIZED_STATUSES = (
    UPCOMING, LIVE, FINISHED, POSTPONED, CANCELLED, ABANDONED, SUSPENDED,
    NO_RESULT, UNKNOWN,
)

#: An authority that could not be read. Never a statement about a fixture.
INCONCLUSIVE = "INCONCLUSIVE"

# --- verdicts -------------------------------------------------------------
VERIFIED = "VERIFIED"
PARTIAL_AUTHORITY = "PARTIAL_AUTHORITY"
UNVERIFIED = "UNVERIFIED"
CONFLICT = "CONFLICT"

#: How far a kickoff may differ and still be the same fixture. A provider
#: that rounds to the quarter hour and an authority that does not will
#: disagree by minutes; two hours apart is a different match.
KICKOFF_TOLERANCE_MINUTES = 90
#: Beyond this the kickoffs are reported as a conflict rather than a rounding
#: difference. Both sides still refer to one fixture - the participants and
#: the competition said so - but the clocks do not agree.
KICKOFF_CONFLICT_MINUTES = 20


# =========================================================================
# LiveScore
# =========================================================================

#: Esid -> normalized status, from real responses only.
#:
#: Observed on a GitHub Actions runner across 17 dates (6,240 soccer events,
#: 254 cricket events) and locally across 21 dates:
#:
#:   1   NS                5254 soccer, 130 cricket
#:   6   FT                3249 soccer, 122 cricket
#:   33  Play in progress     2 cricket
#:   157 Between innings      1 cricket   (found by this module's own report:
#:                                         it came back UNKNOWN, was named in
#:                                         `unknown_statuses`, and is mapped
#:                                         here now that it has been seen)
#:   5  Postp.               37 soccer
#:   13 AP                   20 soccer   (after penalties)
#:   56 Canc.                 5 soccer
#:   11 AET                   2 soccer   (after extra time)
#:   91 AW                    1 soccer   (awarded)
#:   17 Aband.                1 soccer
#:   44 ToFi                  1 soccer   (to finish) -> UNKNOWN, see below
#:   0  (blank)               1 soccer                -> UNKNOWN
#:
#: `ToFi` is deliberately UNKNOWN. "To finish" is neither in play nor
#: finished, and picking one of those would be a guess dressed as a reading.
#: Football half-time has never been observed and so has no entry at all.
LIVESCORE_STATUS_BY_ESID: Dict[int, str] = {
    1: UPCOMING,
    6: FINISHED,
    33: LIVE,
    # Between innings of a limited-overs match: play has started and has not
    # finished. LiveScore's own listing keeps it on the day's in-play block.
    157: LIVE,
    5: POSTPONED,
    13: FINISHED,
    56: CANCELLED,
    11: FINISHED,
    91: FINISHED,
    17: ABANDONED,
}

#: The same answers keyed by the text, for a response that carries a status
#: string this module has seen alongside an Esid it has not.
LIVESCORE_STATUS_BY_TEXT: Dict[str, str] = {
    "ns": UPCOMING,
    "ft": FINISHED,
    "play in progress": LIVE,
    "between innings": LIVE,
    "innings break": LIVE,
    "postp.": POSTPONED,
    "postponed": POSTPONED,
    "canc.": CANCELLED,
    "cancelled": CANCELLED,
    "canceled": CANCELLED,
    "aband.": ABANDONED,
    "abandoned": ABANDONED,
    "aet": FINISHED,
    "ap": FINISHED,
    "aw": FINISHED,
}

#: `EtTx` states the cricket format, and states gender inside it. No feed we
#: publish from carries either as a field.
_WOMENS = re.compile(r"\bwomen'?s?\b", re.IGNORECASE)


def _text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def _iso_utc(stamp: _dt.datetime) -> str:
    return stamp.astimezone(_dt.timezone.utc).isoformat()


def parse_livescore_kickoff(value: Any) -> Optional[_dt.datetime]:
    """`Esd` is YYYYMMDDHHMMSS in UTC.

    Checked, not assumed: `Kuwait vs Qatar` carried Esd 20260907013000 and
    kicked off at 01:30Z, which every other feed in the scan agreed on.
    """
    digits = _text(value)
    if len(digits) != 14 or not digits.isdigit():
        return None
    try:
        return _dt.datetime.strptime(digits, "%Y%m%d%H%M%S").replace(
            tzinfo=_dt.timezone.utc)
    except ValueError:
        return None


def normalize_livescore_status(esid: Any, text: Any = "") -> str:
    """Normalized status for one LiveScore event.

    Esid first, because it is the machine-readable one; then the text, for a
    code this module has not catalogued yet; then UNKNOWN. Esid 0 and a blank
    string both arrive here and both leave as UNKNOWN.
    """
    try:
        code = int(str(esid).strip())
    except (TypeError, ValueError):
        code = -1
    if code in LIVESCORE_STATUS_BY_ESID:
        return LIVESCORE_STATUS_BY_ESID[code]
    return LIVESCORE_STATUS_BY_TEXT.get(_text(text).casefold(), UNKNOWN)


def parse_livescore(payload: Any, sport: str) -> List[Dict[str, Any]]:
    """Authority fixtures from one LiveScore date response.

    An event with fewer than two named sides is skipped rather than repaired:
    a fixture with one participant is not a fixture.
    """
    out: List[Dict[str, Any]] = []
    if not isinstance(payload, dict):
        return out
    for stage in payload.get("Stages") or []:
        if not isinstance(stage, dict):
            continue
        competition = _text(stage.get("Snm"))
        for event in stage.get("Events") or []:
            if not isinstance(event, dict):
                continue
            home_side = (event.get("T1") or [{}])
            away_side = (event.get("T2") or [{}])
            home = _text(home_side[0].get("Nm") if home_side else "")
            away = _text(away_side[0].get("Nm") if away_side else "")
            if not home or not away:
                continue
            kickoff = parse_livescore_kickoff(event.get("Esd"))
            match_type = _text(event.get("EtTx"))
            status_raw = _text(event.get("Eps") or event.get("EpsL"))
            out.append({
                "authority": "livescore",
                "upstream_family": "LiveScore",
                "authority_event_id": _text(event.get("Eid")),
                "name": "%s vs %s" % (home, away),
                "home": home,
                "away": away,
                "home_team_id": _text(home_side[0].get("ID") if home_side else ""),
                "away_team_id": _text(away_side[0].get("ID") if away_side else ""),
                "competition": competition,
                "competition_id": _text(stage.get("CompId") or stage.get("Sid")),
                "country": _text(stage.get("Cnm")),
                "kickoff": _iso_utc(kickoff) if kickoff else "",
                "status_raw": status_raw,
                "status_code": _text(event.get("Esid")),
                "status": normalize_livescore_status(event.get("Esid"), status_raw),
                "round": _text(event.get("ErnInf")),
                "format": match_type,
                "gender": "women" if _WOMENS.search(
                    match_type + " " + competition + " " + home + " " + away
                ) else "",
                "sport": sport,
                # No LiveScore field states when a match finishes. Saying so
                # explicitly keeps a reader from reaching for one.
                "end_time": "",
                "end_time_stated": False,
            })
    return out


# =========================================================================
# ESPN scoreboard header
# =========================================================================

#: `fullStatus.type.name` -> normalized status. Soccer only; ESPN's cricket
#: events carry no STATUS_ name at all (measured: `name` was None on all 344
#: cricket events across 15 dates), which is why the description map below
#: exists as well.
#:
#: Observed across 15 dates on the runner, 2,339 soccer events:
#:
#:   STATUS_SCHEDULED    1141      STATUS_CANCELED       7
#:   STATUS_FULL_TIME    1134      STATUS_FINAL_AET      2
#:   STATUS_POSTPONED      15      STATUS_SECOND_HALF    1
#:   STATUS_FINAL_PEN      10      STATUS_SUSPENDED      1
#:
#: STATUS_HALFTIME and STATUS_FIRST_HALF have never come back. They are not
#: here. An unmapped name reaches UNKNOWN and the report names it.
ESPN_STATUS_BY_NAME: Dict[str, str] = {
    "STATUS_SCHEDULED": UPCOMING,
    "STATUS_FULL_TIME": FINISHED,
    "STATUS_FINAL_PEN": FINISHED,
    "STATUS_FINAL_AET": FINISHED,
    "STATUS_POSTPONED": POSTPONED,
    "STATUS_CANCELED": CANCELLED,
    "STATUS_SUSPENDED": SUSPENDED,
    "STATUS_SECOND_HALF": LIVE,
}

#: `fullStatus.type.description` -> normalized status, for cricket.
#:
#: Observed, 344 cricket events across 15 dates on the runner plus a local
#: sweep: Scheduled 153, Result 140, Final 36, Live 9, Stumps 4,
#: Abandoned 1, No result 1, Drinks 1 (local).
#:
#: `Stumps` and `Drinks` are LIVE on purpose: ESPN's own `state` for both is
#: "in". Stumps is the end of a day's play in a multi-day match, not the end
#: of the match. `No result` is its own answer - the match happened and
#: produced none - and flattening it into FINISHED would lose that.
ESPN_STATUS_BY_DESCRIPTION: Dict[str, str] = {
    "scheduled": UPCOMING,
    "live": LIVE,
    "stumps": LIVE,
    "drinks": LIVE,
    "innings break": LIVE,
    "result": FINISHED,
    "final": FINISHED,
    "full time": FINISHED,
    "abandoned": ABANDONED,
    "postponed": POSTPONED,
    "canceled": CANCELLED,
    "cancelled": CANCELLED,
    "suspended": SUSPENDED,
    "no result": NO_RESULT,
}

#: Last resort, and only a coarse one. `state` alone cannot tell FINISHED
#: from ABANDONED or CANCELLED, so it is consulted only when the name and the
#: description are both unrecognised - and `post` then reaches UNKNOWN rather
#: than claiming a clean finish.
ESPN_STATUS_BY_STATE: Dict[str, str] = {
    "pre": UPCOMING,
    "in": LIVE,
}


def normalize_espn_status(
    status: Any = "",
    state: Any = "",
    name: Any = "",
    description: Any = "",
) -> str:
    """Normalized status for one ESPN header event.

    Four fields in order of how much they actually say: the STATUS_ name, the
    description, then `state` - and `state == "post"` is not enough on its
    own, because post covers Result, Final, Abandoned, Canceled, Postponed,
    Suspended and No result. An unrecognised post is UNKNOWN.
    """
    by_name = ESPN_STATUS_BY_NAME.get(_text(name).upper())
    if by_name:
        return by_name
    by_description = ESPN_STATUS_BY_DESCRIPTION.get(_text(description).casefold())
    if by_description:
        return by_description
    resolved = _text(state).casefold() or _text(status).casefold()
    return ESPN_STATUS_BY_STATE.get(resolved, UNKNOWN)


def parse_espn_kickoff(value: Any) -> Optional[_dt.datetime]:
    """ESPN's `date` is ISO 8601 with a Z. Nothing is inferred from a bare one."""
    text = _text(value)
    if not text:
        return None
    try:
        stamp = _dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=_dt.timezone.utc)


def _espn_sides(competitors: Any) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Home and away from `competitors[]`, by their own `homeAway`.

    Order in the array is not the answer - the Premier League event measured
    on 2026-09-07 listed the AWAY side first, with `order: 1`. When neither
    side declares itself, the array order is used and the report can say the
    sides were positional.
    """
    rows = [row for row in (competitors or []) if isinstance(row, dict)]
    if len(rows) < 2:
        return {}, {}
    home = next((r for r in rows if _text(r.get("homeAway")).casefold() == "home"), None)
    away = next((r for r in rows if _text(r.get("homeAway")).casefold() == "away"), None)
    if home is None or away is None:
        home, away = rows[0], rows[1]
    return home, away


def _side(row: Dict[str, Any]) -> Dict[str, str]:
    return {
        "name": _text(row.get("displayName") or row.get("name")),
        "team_id": _text(row.get("id")),
        "abbreviation": _text(row.get("abbreviation")),
        "logo": _text(row.get("logo")),
    }


def parse_espn_header(payload: Any, sport: str) -> List[Dict[str, Any]]:
    """Authority fixtures from one ESPN header response.

    The fixture name is built from `competitors[]` for BOTH sports, never
    from `name`. Measured: ESPN's soccer `name` is the season - '2026-27
    English Premier League' - while its cricket `name` is the fixture,
    'Kuwait v Qatar'. Building from the sides is the only rule that is
    right in both cases.
    """
    out: List[Dict[str, Any]] = []
    if not isinstance(payload, dict):
        return out
    for sport_block in payload.get("sports") or []:
        if not isinstance(sport_block, dict):
            continue
        for league in sport_block.get("leagues") or []:
            if not isinstance(league, dict):
                continue
            competition = _text(league.get("name"))
            for event in league.get("events") or []:
                if not isinstance(event, dict):
                    continue
                home_row, away_row = _espn_sides(event.get("competitors"))
                if not home_row or not away_row:
                    continue
                home, away = _side(home_row), _side(away_row)
                if not home["name"] or not away["name"]:
                    continue
                full_type = (event.get("fullStatus") or {})
                type_block = full_type.get("type") if isinstance(full_type, dict) else {}
                type_block = type_block if isinstance(type_block, dict) else {}
                kickoff = parse_espn_kickoff(event.get("date"))
                event_class = event.get("class") or {}
                event_class = event_class if isinstance(event_class, dict) else {}
                match_format = _text(event.get("eventType")
                                     or event_class.get("eventType"))
                blob = " ".join((competition, home["name"], away["name"],
                                 _text(event.get("name"))))
                out.append({
                    "authority": "espn-header",
                    "upstream_family": "ESPN",
                    "authority_event_id": _text(event.get("id")),
                    "authority_uid": _text(event.get("uid")),
                    "name": "%s vs %s" % (home["name"], away["name"]),
                    "home": home["name"],
                    "away": away["name"],
                    "home_team_id": home["team_id"],
                    "away_team_id": away["team_id"],
                    "home_logo": home["logo"],
                    "away_logo": away["logo"],
                    "competition": competition,
                    "competition_id": _text(league.get("id")),
                    "country": "",
                    "kickoff": _iso_utc(kickoff) if kickoff else "",
                    "status_raw": _text(event.get("summary")),
                    "status_code": _text(type_block.get("name")
                                         or type_block.get("description")),
                    "espn_state": _text(type_block.get("state")
                                        or event.get("status")),
                    "espn_status": _text(event.get("status")),
                    "espn_status_detail": _text(type_block.get("detail")),
                    "espn_status_description": _text(type_block.get("description")),
                    "espn_completed": bool(type_block.get("completed"))
                    if "completed" in type_block else None,
                    "status": normalize_espn_status(
                        event.get("status"),
                        type_block.get("state"),
                        type_block.get("name"),
                        type_block.get("description"),
                    ),
                    "round": _text(event.get("title")),
                    "format": match_format,
                    "gender": "women" if _WOMENS.search(blob) else "",
                    "sport": sport,
                    # `endDate` is a season/window end, not a match end: a T20
                    # that finished on 2026-09-06 carried endDate
                    # 2026-09-08T23:59Z. It is preserved raw and never read as
                    # an end-of-match time.
                    "espn_end_date_raw": _text(event.get("endDate")),
                    "end_time": "",
                    "end_time_stated": False,
                })
    return out


# =========================================================================
# registry and fetching
# =========================================================================

PARSERS = {
    "livescore_date": parse_livescore,
    "espn_header": parse_espn_header,
}


def load_registry(path: str | Path = DEFAULT_REGISTRY_PATH) -> List[Dict[str, Any]]:
    """The configured authorities. A missing or unreadable file is no authorities.

    Not an exception: a scan whose only new capability is a shadow report must
    not fail because that report cannot be built.
    """
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    authorities = payload.get("authorities") if isinstance(payload, dict) else None
    if not isinstance(authorities, list):
        return []
    return [entry for entry in authorities
            if isinstance(entry, dict) and entry.get("enabled", True)]


def _build_url(authority: Dict[str, Any], sport: str, day: _dt.date) -> str:
    template = _text(authority.get("url_template"))
    sport_paths = authority.get("sport_paths") or {}
    sport_path = _text(sport_paths.get(sport)) or sport
    date_format = _text(authority.get("date_format")) or "%Y%m%d"
    return (template
            .replace("{sport}", sport_path)
            .replace("{date}", day.strftime(date_format))
            .replace("{limit}", str(authority.get("default_limit") or 300)))


def fetch_json(
    url: str,
    *,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> Tuple[Optional[Any], str]:
    """`(payload, "")` on success, `(None, reason)` on anything else.

    Every failure is a reason string, and every reason means INCONCLUSIVE. A
    403, a 502, a timeout and a body that is not JSON are all "this authority
    could not be read", never "this fixture is not real".
    """
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        context = ssl.create_default_context()
        with urllib.request.urlopen(request, timeout=timeout,
                                    context=context) as response:
            status = int(getattr(response, "status", 200) or 200)
            if status != 200:
                return None, "http %d" % status
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as error:
        return None, "http %s" % error.code
    except Exception as error:                                   # noqa: BLE001
        return None, "%s: %s" % (type(error).__name__, str(error)[:120])
    if len(raw) > MAX_RESPONSE_BYTES:
        return None, "response larger than %d bytes" % MAX_RESPONSE_BYTES
    try:
        return json.loads(raw.decode("utf-8", "replace")), ""
    except ValueError as error:
        return None, "body is not JSON: %s" % str(error)[:80]


def collect(
    *,
    sports: Iterable[str] = ("cricket", "football"),
    # Yesterday, today, tomorrow. Yesterday is not optional: a fixture that
    # kicked off at 22:30Z is still on Today Match after midnight UTC, and
    # `Barbados Tridents vs Saint Lucia Kings` went unmatched by both
    # authorities on the first run for exactly that reason.
    days: Iterable[int] = (-1, 0, 1),
    now: Optional[_dt.datetime] = None,
    registry_path: str | Path = DEFAULT_REGISTRY_PATH,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    fetcher=None,
) -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[str, Any]]:
    """Read every enabled authority and return its fixtures, plus a health block.

    `fetcher` is the seam the tests use; production passes nothing and gets
    `fetch_json`. Returns `({authority_id: [fixture, ...]}, health)`, and an
    authority that could not be read appears in `health` as INCONCLUSIVE with
    an empty fixture list - which is a different thing from an authority that
    answered and listed nothing.
    """
    fetch = fetcher or fetch_json
    reference = (now or _dt.datetime.now(_dt.timezone.utc)).astimezone(
        _dt.timezone.utc)
    fixtures: Dict[str, List[Dict[str, Any]]] = {}
    health: Dict[str, Any] = {}

    for authority in load_registry(registry_path):
        authority_id = _text(authority.get("id"))
        parser = PARSERS.get(_text(authority.get("adapter")))
        if not authority_id or parser is None:
            continue
        rows: List[Dict[str, Any]] = []
        attempts: List[Dict[str, Any]] = []
        reachable = 0
        # Two sports across three dates is six requests per authority, and
        # ESPN's soccer day is about a megabyte, so sequentially this cost 21
        # seconds of a scan that already has a time budget. The requests are
        # independent reads of somebody else's API, so they go in parallel -
        # and the results are consumed in the order the tasks were built, so
        # a report cannot come out differently because a response was quicker.
        tasks = [(sport, (reference + _dt.timedelta(days=int(offset))).date())
                 for sport in sports
                 if sport in (authority.get("sport_paths") or {})
                 for offset in days]
        if not tasks:
            continue
        with futures.ThreadPoolExecutor(max_workers=min(6, len(tasks))) as pool:
            answers = list(pool.map(
                lambda task: fetch(_build_url(authority, task[0], task[1]),
                                   timeout=timeout),
                tasks))
        for (sport, day), (payload, reason) in zip(tasks, answers):
            if payload is None:
                attempts.append({"sport": sport, "date": day.isoformat(),
                                 "result": INCONCLUSIVE, "reason": reason})
                continue
            parsed = parser(payload, sport)
            reachable += 1
            rows.extend(parsed)
            attempts.append({"sport": sport, "date": day.isoformat(),
                             "result": "ok", "fixtures": len(parsed)})
        fixtures[authority_id] = rows
        health[authority_id] = {
            "upstream_family": _text(authority.get("upstream_family"))
            or upstream_family.family_for(authority_id),
            "role": _text(authority.get("role")),
            "shadow_only": bool(authority.get("shadow_only", True)),
            "state": "ok" if reachable else INCONCLUSIVE,
            "requests": len(attempts),
            "requests_ok": reachable,
            "fixtures": len(rows),
            "unknown_statuses": sorted({
                "%s|%s" % (row.get("status_code"), row.get("status_raw"))
                for row in rows if row.get("status") == UNKNOWN
            })[:20],
            "attempts": attempts,
        }
    return fixtures, health
