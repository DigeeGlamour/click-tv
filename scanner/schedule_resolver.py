"""Resolve sports feed labels to authoritative, absolute fixture times."""

from __future__ import annotations

import copy
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _norm(value: Any) -> str:
    text = str(value or "").casefold()
    text = text.replace("sessione mattutina", "morning session")
    text = text.replace("sessione serale", "evening session")
    text = text.replace("women's", "women").replace("womens", "women")
    text = text.replace("men's", "men").replace("mens", "men")
    text = re.sub(r"\bw\b", " women ", text)
    text = re.sub(r"\b(?:official|live|upcoming|match|coverage|stream)\b", " ", text)
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def _offset_zone(value: Any) -> timezone:
    match = re.fullmatch(r"([+-])(\d{2}):(\d{2})", str(value or "+00:00").strip())
    if not match:
        return timezone.utc
    minutes = int(match.group(2)) * 60 + int(match.group(3))
    if match.group(1) == "-":
        minutes *= -1
    return timezone(timedelta(minutes=minutes))


def _zone(name: Any, fallback: str = "UTC", offset: Any = "+00:00") -> timezone | ZoneInfo:
    try:
        return ZoneInfo(str(name or fallback))
    except ZoneInfoNotFoundError:
        return _offset_zone(offset)


def _iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _fixture_record(
    competition: Dict[str, Any],
    name: str,
    start_text: str,
    venue: str = "",
    end_text: str = "",
    aliases: Optional[List[str]] = None,
) -> Dict[str, Any]:
    source_zone = _zone(
        competition.get("timezone"), "UTC", competition.get("utc_offset", "+00:00")
    )
    start = datetime.fromisoformat(start_text)
    if start.tzinfo is None:
        start = start.replace(tzinfo=source_zone)
    if end_text:
        end = datetime.fromisoformat(end_text)
        if end.tzinfo is None:
            end = end.replace(tzinfo=source_zone)
    else:
        duration = int(competition.get("duration_minutes") or 240)
        end = start + timedelta(minutes=duration)
    return {
        "fixture_id": f"{competition.get('id')}:{_norm(name).replace(' ', '-')}",
        "name": name,
        "competition": str(competition.get("name") or "Live Sports"),
        "competition_id": str(competition.get("id") or ""),
        # The competition's own name counts as one of its aliases. It was left
        # out, so a title that spelled the series exactly as the catalogue spells
        # it - "... | India Tour of Sri Lanka 2026" - only matched when someone had
        # also happened to repeat that string in `aliases`. When they had not, the
        # broadcast published as its own card beside the fixture it was carrying.
        "competition_aliases": sorted({
            alias
            for alias in (
                [_norm(competition.get("name"))]
                + [_norm(v) for v in competition.get("aliases", []) or []]
            )
            if alias
        }),
        "aliases": sorted({
            _norm(a) for a in (aliases or []) if a
        }),
        "start": start.astimezone(timezone.utc),
        "end": end.astimezone(timezone.utc),
        "venue": venue,
        "schedule_source_url": str(competition.get("source_url") or ""),
    }


def load_fixtures(path: str | Path) -> List[Dict[str, Any]]:
    config_path = Path(path)
    if not config_path.exists():
        return []
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    fixtures: List[Dict[str, Any]] = []
    for competition in raw.get("competitions", []):
        if not isinstance(competition, dict):
            continue
        for entry in competition.get("fixtures", []):
            if isinstance(entry, dict) and entry.get("name") and entry.get("start"):
                fixtures.append(_fixture_record(
                    competition,
                    str(entry["name"]),
                    str(entry["start"]),
                    str(entry.get("venue") or ""),
                    str(entry.get("end") or ""),
                    entry.get("aliases"),
                ))
        for entry in competition.get("double_headers", []):
            if not isinstance(entry, dict):
                continue
            date_text = str(entry.get("date") or "")
            home = str(entry.get("home") or "")
            away = str(entry.get("away") or "")
            venue = str(entry.get("venue") or "")
            round_name = str(entry.get("round") or "").strip()
            for gender, clock in (("Women", entry.get("women")), ("Men", entry.get("men"))):
                if not date_text or not clock:
                    continue
                if home == "TBD" and away == "TBD":
                    # Do not make a generic round label look like confirmed
                    # teams. Update the official fixture config after the
                    # qualified teams are officially published.
                    possessive_gender = "Women's" if gender == "Women" else "Men's"
                    name = f"The Hundred {possessive_gender} {round_name} - Teams TBA".strip()
                else:
                    name = f"{home} {gender} vs {away} {gender}"
                fixtures.append(_fixture_record(
                    competition, name, f"{date_text}T{clock}:00", venue
                ))
    return fixtures


def _team_tokens(value: str) -> set[str]:
    ignored = {
        "the", "2026", "competition", "series", "tour", "odi", "t20", "cup", "league",
        "men", "women", "willow", "cricket", "sky", "sport", "sports", "star", "fox",
        "server", "link", "alt", "low", "fhd", "hd", "uhd", "english", "hindi",
        "vs", "v", "jan", "january", "feb", "february", "mar", "march", "apr",
        "april", "may", "jun", "june", "jul", "july", "aug", "august", "sep",
        "september", "oct", "october", "nov", "november", "dec", "december",
    }
    return {part for part in _norm(value).split() if part not in ignored and not part.isdigit()}


def _gender(value: str) -> str:
    normalized = _norm(value)
    if re.search(r"\b(?:w|woman|women|womens)\b", normalized):
        return "women"
    if re.search(r"\b(?:man|men|mens)\b", normalized):
        return "men"
    return ""


def _candidate_gender(item: Dict[str, Any]) -> str:
    """Use provider metadata as evidence when the display title is neutral."""
    evidence = " ".join(
        str(item.get(field) or "")
        for field in (
            "name", "title", "competition", "group_title", "category",
            "logo", "url", "event_url", "tvg_id", "source_name",
        )
    )
    explicit = _gender(evidence)
    if explicit:
        return explicit

    # Sony event path codes such as DAI18-HMEN identify a men's feed even
    # when the published M3U title omits the word Men.
    lowered = evidence.casefold()
    if re.search(r"(?:^|[-_/])h?men(?:$|[-_/])", lowered):
        return "men"
    if re.search(r"(?:^|[-_/])h?women(?:$|[-_/])", lowered):
        return "women"
    return ""


def _is_exact_event(value: str) -> bool:
    # Provider/quality suffixes belong to the stream candidate, not the teams.
    # Example: "The Hundred W Vs The Hundred W - SKY SPORT NZ" is a generic
    # tournament placeholder even though the suffix makes the right side differ.
    raw = str(value or "")
    match = re.search(r"(?i)\b(?:vs|v\.)\b", raw)
    if not match:
        return False
    left = _norm(raw[:match.start()])
    right = _norm(re.split(r"\s+-\s+", raw[match.end():], maxsplit=1)[0])
    if not left or not right:
        return False
    left_tokens, right_tokens = _team_tokens(left), _team_tokens(right)
    return bool(left_tokens and right_tokens and left_tokens != right_tokens)


def _fixture_score(
    candidate_name: str,
    fixture: Dict[str, Any],
    candidate_gender: str = "",
) -> int:
    candidate = _norm(candidate_name)
    fixture_name = _norm(fixture.get("name"))
    candidate_tokens = _team_tokens(candidate)
    fixture_tokens = _team_tokens(fixture_name)
    overlap = len(candidate_tokens & fixture_tokens)
    # An ordinal such as "4th" is schedule metadata, not team identity.  Two
    # actual identity tokens must agree before ordinal/gender bonuses apply.
    identity_overlap = {
        token for token in candidate_tokens & fixture_tokens
        if not re.fullmatch(r"\d+(?:st|nd|rd|th)", token)
    }
    if len(identity_overlap) < 2:
        return 0
    score = len(identity_overlap) * 10
    if fixture_tokens and fixture_tokens.issubset(candidate_tokens):
        score += 50
    elif (
        len(candidate_tokens) >= 2
        and candidate_tokens.issubset(fixture_tokens)
    ):
        # Generic team labels can still match fixtures without an ordinal.
        # Numbered fixtures are rejected below unless the candidate carries
        # the same ordinal, because reusable channel labels are not proof of
        # the programme currently being broadcast.
        score += 40
    candidate_gender = candidate_gender or _gender(candidate)
    fixture_gender = _gender(fixture_name)
    if candidate_gender and fixture_gender:
        if candidate_gender != fixture_gender:
            return 0
        score += 20
    # Only a suffixed match ordinal is comparable. A year/date such as 2026
    # must never be treated as the event number.
    ordinal = re.search(r"\b(\d+)(?:st|nd|rd|th)\b", candidate)
    fixture_ordinal = re.search(r"\b(\d+)(?:st|nd|rd|th)\b", fixture_name)
    # A generic channel label such as "Australia vs Bangladesh Willow" is
    # not proof that the channel is carrying the current numbered fixture.
    # Providers frequently reuse those labels while airing another match.
    # Numbered fixtures therefore require the same ordinal in the candidate.
    if fixture_ordinal and not ordinal:
        return 0
    if ordinal and fixture_ordinal:
        score += 25 if ordinal.group(1) == fixture_ordinal.group(1) else -50
    return score


def _competition_matches(name: str, fixture: Dict[str, Any]) -> bool:
    normalized = _norm(name)
    if any(alias and alias in normalized for alias in fixture.get("competition_aliases", [])):
        return True
    fixture_aliases = [_norm(a) for a in fixture.get("aliases", [])]
    return any(a and (a in normalized or normalized in a) for a in fixture_aliases)


#: "1st Test", "2nd ODI", "6th Match" - which round of a series, spelled the way
#: both a broadcaster label and a catalogue fixture name spell it.
_ROUND_LABEL = re.compile(
    r"(?i)\b(\d{1,3})(?:st|nd|rd|th)?\s+"
    r"(test|odi|t20i?|match|leg|round|day|session)\b"
)


def _round_labels(value: str) -> set[Tuple[str, str]]:
    """The (number, kind) rounds named in a title, e.g. {("1","test")}."""
    return {
        (match.group(1), match.group(2).casefold().rstrip("s"))
        for match in _ROUND_LABEL.finditer(str(value or ""))
    }


def _competition_round_fixture(
    item: Dict[str, Any],
    fixtures: Iterable[Dict[str, Any]],
    now: datetime,
) -> Optional[Dict[str, Any]]:
    """Bind a participant-less broadcast label to the fixture it is carrying.

    A broadcaster that relays one day of a Test titles the feed after the day, not
    after the teams:

        "Day 3 1st Test 17 Aug 2026 | India Tour of Sri Lanka 2026"

    There are no participants in that string, so team-token scoring cannot reach
    it and normalisation reduces it to "1-test" - a key so generic that two
    different series would collide on it. It published as its own card beside the
    real fixture, which is the duplicate section 1 forbids.

    The catalogue can identify it, and only the catalogue: the title names a
    competition alias and a round, and the series says which fixture that is. All
    four conditions must hold, so this never guesses:

      * the title carries no usable participants - anything that does is left to
        team scoring, which is stronger;
      * the title contains one of the competition's aliases;
      * the title's round matches the fixture's own round; and
      * that fixture is running *now*, because a channel label is reused between
        matches and only the live window proves which one is on air.

    If two fixtures answer, none is chosen.
    """
    name = str(item.get("name") or "")
    if not name.strip():
        return None
    # Anything with real participants is identified better by team scoring.
    if _is_exact_event(name):
        return None
    rounds = _round_labels(name)
    if not rounds:
        return None
    normalized = _norm(name)
    now_utc = now.astimezone(timezone.utc)

    matches: List[Dict[str, Any]] = []
    for fixture in fixtures:
        if not _competition_matches(name, fixture):
            continue
        if not (_round_labels(str(fixture.get("name") or "")) & rounds):
            continue
        if not fixture["start"] - timedelta(minutes=20) <= now_utc < fixture["end"]:
            continue
        matches.append(fixture)

    unique = {str(fixture.get("fixture_id") or "") for fixture in matches}
    if len(unique) != 1:
        return None
    # A gendered label must not be attached to the other gender's fixture.
    candidate_gender = _candidate_gender(item) or _gender(normalized)
    fixture_gender = _gender(str(matches[0].get("name") or ""))
    if candidate_gender and fixture_gender and candidate_gender != fixture_gender:
        return None
    return matches[0]


def _best_fixture(
    item: Dict[str, Any],
    fixtures: Iterable[Dict[str, Any]],
    now: Optional[datetime] = None,
) -> Optional[Dict[str, Any]]:
    name = str(item.get("name") or "")
    candidate_gender = _candidate_gender(item)
    scored = sorted(
        (
            (_fixture_score(name, fixture, candidate_gender), fixture)
            for fixture in fixtures
        ),
        key=lambda pair: pair[0], reverse=True,
    )
    if not scored or scored[0][0] < 40:
        return None

    # A neutral provider title can match concurrent men's and women's cards,
    # or two numbered fixtures, equally well. Never guess in that situation.
    top_score = scored[0][0]
    tied = [fixture for score, fixture in scored if score == top_score]
    if len(tied) > 1:
        tied_ids = {str(fixture.get("fixture_id") or "") for fixture in tied}
        if len(tied_ids) > 1:
            tied_genders = {_gender(str(fixture.get("name") or "")) for fixture in tied}
            # A gender-neutral title must never choose between men's and
            # women's fixtures.  For a numbered series with the same teams,
            # however, one currently active fixture is authoritative.
            if not candidate_gender and len(tied_genders) > 1:
                return None
            now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
            active = [
                fixture for fixture in tied
                if fixture["start"] - timedelta(minutes=20) <= now_utc < fixture["end"]
            ]
            if len(active) == 1:
                return active[0]
            return None
    return scored[0][1]


def _resolve_fixture(
    item: Dict[str, Any],
    fixtures: Iterable[Dict[str, Any]],
    now: Optional[datetime] = None,
) -> Optional[Dict[str, Any]]:
    """The fixture this candidate is carrying, by team names or by series+round.

    Team scoring is tried first and is unchanged. The competition+round path only
    ever sees titles team scoring found nothing in, so it can add matches but
    never change one.
    """
    fixture_list = list(fixtures)
    resolved = _best_fixture(item, fixture_list, now)
    if resolved is not None:
        return resolved
    return _competition_round_fixture(
        item, fixture_list, now or datetime.now(timezone.utc)
    )


#: A feed publishes its own local clock, and not every feed is in Dhaka.
#: FanCode is an Indian service on Indian Standard Time, so reading its
#: fixtures as Bangladesh time put every one of them exactly thirty minutes
#: early - the half hour between UTC+5:30 and UTC+6:00. Measured 2026-09-03:
#: `Namibia vs Zimbabwe` published 11:30 where Cricket Namibia says 12:00, and
#: `Real Sociedad vs RC Celta` 18:30 where LaLiga and thesportsdb say 19:00.
#:
#: Keyed on a fragment of the source id so every FanCode mirror is covered by
#: one entry rather than eight - the first fix reached two adapters and left
#: `sayanpal-fancode-mirror` still half an hour out.
SOURCE_CLOCK_ZONES = (
    ("fancode", "Asia/Kolkata"),
    ("sonyliv", "Asia/Kolkata"),
    ("jiotv", "Asia/Kolkata"),
    ("willow", "Asia/Kolkata"),
)


def source_clock_zone(source_id: Any, default_zone: ZoneInfo) -> ZoneInfo:
    """The timezone this feed's naive clocks are written in."""
    marker = str(source_id or "").casefold()
    for fragment, zone_name in SOURCE_CLOCK_ZONES:
        if fragment in marker:
            resolved = _zone(zone_name, zone_name, "+05:30")
            if resolved is not None:
                return resolved
    return default_zone


def _parse_source_time(value: Any, source_timezone: ZoneInfo, now: datetime) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=source_timezone)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        pass
    cleaned = re.sub(r"(?i)^\s*live\s+at\s+", "", text)
    cleaned = re.sub(r"(?i)\s*(?:BDT|BST|UTC|GMT)\s*$", "", cleaned).strip()
    # TrySports writes "20 Aug 2026, 05:00 PM (BD Time)". The trailing bracket
    # is a timezone label, not part of the clock, and every pattern below failed
    # on it - so all 163 of its fixtures were discarded for "no kickoff time".
    cleaned = re.sub(
        r"(?i)\s*\(\s*(?:BD|BST|BDT|UTC|GMT|LOCAL|IST)?\s*TIME\s*\)\s*$",
        "",
        cleaned,
    ).strip()
    cleaned = re.sub(r"(?i)\s*\((?:BD|BST|BDT|UTC|GMT|IST)\)\s*$", "", cleaned).strip()
    local_now = now.astimezone(source_timezone)
    day = local_now.date()
    if re.match(r"(?i)^tomorrow\b", cleaned):
        day += timedelta(days=1)
        cleaned = re.sub(r"(?i)^tomorrow\s+", "", cleaned)
    for pattern in (
        "%Y-%m-%d %I:%M %p", "%Y-%m-%d %H:%M", "%d-%m-%Y %I:%M %p",
        # AX Sports writes the clock before the date ("12:30 AM 17-08-2026").
        # Without these the kickoff time was unreadable, so every one of its
        # not-started fixtures was discarded for having no schedule.
        "%I:%M %p %d-%m-%Y", "%I %p %d-%m-%Y", "%H:%M %d-%m-%Y",
        "%I:%M %p %Y-%m-%d", "%H:%M %Y-%m-%d",
        # TrySports (0matbank/trysports) writes the month by name:
        # "20 Aug 2026, 05:00 PM" and the comma-less variant.
        "%d %b %Y, %I:%M %p", "%d %b %Y %I:%M %p",
        "%d %B %Y, %I:%M %p", "%d %B %Y %I:%M %p",
        "%d %b %Y, %H:%M", "%d %b %Y %H:%M",
        "%b %d %Y, %I:%M %p", "%b %d, %Y %I:%M %p",
    ):
        try:
            parsed = datetime.strptime(cleaned, pattern).replace(tzinfo=source_timezone)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            pass
    for pattern in ("%I:%M %p", "%I %p", "%H:%M"):
        try:
            clock = datetime.strptime(cleaned, pattern).time()
            parsed = datetime.combine(day, clock, tzinfo=source_timezone)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            pass
    return None


def _apply_fixture(item: Dict[str, Any], fixture: Dict[str, Any], source_time: Optional[datetime]) -> Dict[str, Any]:
    resolved = copy.deepcopy(item)
    resolved["name"] = fixture["name"]
    resolved["competition"] = fixture["competition"]
    resolved["fixture_id"] = fixture["fixture_id"]
    resolved["venue"] = fixture["venue"]
    resolved["source_start_time"] = str(item.get("start_time") or "")
    resolved["start_time"] = _iso_utc(fixture["start"])
    resolved["start_at"] = resolved["start_time"]
    resolved["end_time"] = _iso_utc(fixture["end"])
    resolved["schedule_verified"] = True
    resolved["schedule_source_url"] = fixture["schedule_source_url"]
    if source_time is None:
        resolved["time_verification"] = "official_catalogue"
    else:
        delta = abs((source_time - fixture["start"]).total_seconds())
        resolved["time_verification"] = "verified" if delta <= 300 else "corrected"
        resolved["source_time_delta_minutes"] = round(delta / 60)
    return resolved


def _classify(item: Dict[str, Any], now: datetime) -> Dict[str, Any]:
    start = datetime.fromisoformat(str(item["start_time"]))
    end = datetime.fromisoformat(str(item["end_time"]))
    playable = bool(str(item.get("url") or "").strip()) and item.get("metadata_only") is not True
    if start - timedelta(minutes=20) <= now <= end:
        item["schedule_status"] = "LIVE_NOW" if playable else "LINK_UPDATING"
        item["status"] = item["schedule_status"]
    elif now < start:
        minutes = (start - now).total_seconds() / 60
        item["schedule_status"] = "STARTING_SOON" if minutes <= 60 else "UPCOMING"
        item["status"] = item["schedule_status"]
    else:
        item["schedule_status"] = "ENDED"
        item["status"] = "ENDED"
    return item


def _today_source_channel_fallback(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Keep a verified Today-source channel without inventing a live fixture.

    Today source playlists also expose reusable sports channels.  An exact
    official fixture match upgrades one of those entries to LIVE_NOW.  When no
    fixture can be proven, a playable Today candidate remains useful as a
    CHANNEL_LIVE card; it must not be labelled as an actual live match.
    """
    if str(item.get("source_pipeline") or "").strip().casefold() != "today_match":
        return None
    if not str(item.get("url") or "").strip() or item.get("metadata_only") is True:
        return None
    configured_status = str(
        item.get("schedule_status") or item.get("status") or item.get("original_status") or ""
    ).strip().upper()
    if configured_status in {"ENDED", "COMPLETED", "FINISHED", "OFFLINE", "DEAD"}:
        return None

    fallback = copy.deepcopy(item)
    # A provider clock or title is not authoritative fixture evidence.  Clear
    # it so the frontend cannot turn a reusable channel into UPCOMING/LIVE_NOW.
    for field in (
        "start_time", "start_at", "end_time", "end_at", "fixture_id",
        "schedule_source_url", "time_verification", "source_time_delta_minutes",
    ):
        fallback.pop(field, None)
    fallback["schedule_verified"] = False
    fallback["today_source_channel"] = True
    fallback["schedule_status"] = "CHANNEL_LIVE"
    fallback["status"] = "CHANNEL_LIVE"
    return fallback


# Feeds that publish a real fixture list - participants, competition, kickoff
# time and a live/not-started status - rather than only a playable link. They
# are the authority on what a match is and when it starts; stream playlists are
# the authority on how to play it. Only a source named here may bring a fixture
# into existence, so a stream-only playlist can still never invent one.
DEFAULT_FIXTURE_AUTHORITY_SOURCES = frozenset({
    "srhady-axsports-upcoming",
    "srhady-willow-event-upcoming",
})

# A provider feed gives a kickoff time but almost never an end time, so a
# window has to be assumed to decide when a card stops being current.
DEFAULT_PROVIDER_EVENT_HOURS = 4

PROVIDER_LIVE_STATUSES = frozenset({"LIVE", "LIVE_NOW", "IN_PROGRESS", "STARTED"})
PROVIDER_UPCOMING_STATUSES = frozenset({"UPCOMING", "NOT_STARTED", "SCHEDULED", "NS"})
PROVIDER_DEAD_STATUSES = frozenset({
    "COMPLETED", "ENDED", "FINISHED", "CLOSED", "UNSCHEDULED",
    "CANCELLED", "POSTPONED", "ABANDONED",
})


UNUSABLE_STREAM_STATUSES = frozenset({
    "failed", "failed_bd", "404_quarantined", "rejected_low_quality", "quarantine",
})


def _stream_is_usable(item: Dict[str, Any]) -> bool:
    """Whether this candidate still carries a link worth publishing."""
    if not str(item.get("url") or "").strip():
        return False
    if item.get("metadata_only") is True:
        return False
    if item.get("publish_allowed") is False:
        return False
    status = str(item.get("verification_status") or "").strip().lower()
    return status not in UNUSABLE_STREAM_STATUSES


def _provider_status(item: Dict[str, Any]) -> str:
    return str(
        item.get("status")
        or item.get("event_status")
        or item.get("original_status")
        or ""
    ).strip().upper()


def _provider_fixture_item(
    item: Dict[str, Any],
    source_time: Optional[datetime],
    now_utc: datetime,
    event_hours: int,
) -> Optional[Dict[str, Any]]:
    """Turn one fixture-authority feed entry into a scheduled event.

    The local config/event-fixtures.json catalogue holds a handful of
    hand-written competitions, and requiring an exact match against it
    suppressed 414 of 444 candidates - every AX Sports football fixture and
    every SM Sports Data entry - because their competitions were simply not
    listed. A feed that states the participants, the kickoff time and whether
    the match has started is itself sufficient evidence to publish a card.
    """
    status = _provider_status(item)
    if status in PROVIDER_DEAD_STATUSES:
        return None

    is_live = status in PROVIDER_LIVE_STATUSES
    is_upcoming = status in PROVIDER_UPCOMING_STATUSES
    if not is_live and not is_upcoming:
        return None

    start = source_time
    if start is None:
        if not is_live:
            # No kickoff time and not currently playing: nothing to schedule.
            return None
        start = now_utc

    resolved = copy.deepcopy(item)
    end = start + timedelta(hours=max(1, event_hours))
    if is_live:
        # The feed says this is playing now, which outranks a guessed window;
        # a long format like a Test day would otherwise read as already ended.
        end = max(end, now_utc + timedelta(hours=1))

    resolved["start_time"] = _iso_utc(start)
    resolved["start_at"] = resolved["start_time"]
    resolved["end_time"] = _iso_utc(end)
    resolved["source_start_time"] = str(item.get("start_time") or "")
    resolved["schedule_verified"] = True
    resolved["schedule_authority"] = str(item.get("source_id") or "provider_feed")
    resolved["time_verification"] = "provider_feed"
    resolved.setdefault(
        "schedule_source_url", str(item.get("source_url") or item.get("event_url") or "")
    )
    resolved["fixture_id"] = resolved.get("fixture_id") or _provider_fixture_id(resolved, start)

    if is_upcoming and not _stream_is_usable(resolved):
        # A fixture that has not started usually carries a placeholder link, so
        # verification rightly marks it failed. The fixture itself is still
        # real and belongs on the Upcoming tab; only its link is not ready. Drop
        # the dead link and keep the card, exactly as `allow_without_stream`
        # describes - a later scan attaches a working stream near kickoff. This
        # keeps the promise that no dead link is ever published.
        for field in ("url", "stream_url", "link", "final_url", "backups", "standby"):
            resolved.pop(field, None)
        resolved["metadata_only"] = True
        resolved["verification_status"] = "metadata_only"
        resolved["publish_allowed"] = True
        resolved["allow_without_stream"] = True
        resolved["stream_pending"] = True

    resolved = _classify(resolved, now_utc)

    if is_live and resolved.get("schedule_status") == "ENDED":
        playable = (
            bool(str(resolved.get("url") or "").strip())
            and resolved.get("metadata_only") is not True
        )
        resolved["schedule_status"] = "LIVE_NOW" if playable else "LINK_UPDATING"
        resolved["status"] = resolved["schedule_status"]
    elif is_upcoming and resolved.get("schedule_status") == "ENDED":
        # A not-started fixture whose kickoff has already passed means the feed
        # is stale. Publishing it would show a match that is over.
        return None

    return resolved


def _event_identity_name(value: Any) -> str:
    """Name portion of a fixture id, stable across day labels and channels."""
    try:
        from scanner.merger import normalize_event_key
    except ImportError:  # scanner/ on sys.path directly
        from merger import normalize_event_key
    return normalize_event_key(value) or _norm(value)


def _provider_fixture_id(item: Dict[str, Any], start: datetime) -> str:
    """Identity from participants + competition + date, never the title alone.

    Same teams meeting again on another date stay distinguishable (guide 22),
    while a multi-day Test keeps one identity across its days because the name
    portion is the normalised event key, which drops "Day 3"/"Session 2" but
    keeps "1st Test" (guide 19).

    The date is dropped for a multi-day fixture; including it would mint a new
    id every morning and break the "one match, one card" rule the moment play
    resumed.
    """
    parts = [
        _event_identity_name(item.get("name")),
        _norm(item.get("competition")),
    ]
    if not _MULTI_DAY_FIXTURE.search(str(item.get("name") or "")):
        parts.append(start.strftime("%Y-%m-%d"))
    return "provider:" + "|".join(part for part in parts if part)


#: A format that legitimately spans more than one calendar day.
_MULTI_DAY_FIXTURE = re.compile(
    r"(?i)\b(?:test|day\s*\d|session\s*\d|stage\s*\d|tour|championship)\b"
)


def reuse_published_event_ids(
    items: List[Dict[str, Any]],
    data_root: str | Path = "data",
) -> int:
    """Guide 30.8: a promoted event reuses its card, it does not get a new one.

    When a fixture goes from not-started to in-play it moves from the Upcoming
    tab to Today Match. Minting a fresh id at that moment would read to the
    site as a brand new card, losing whatever the viewer already had open.
    Matching this scan's events against the ids already published keeps one
    identity for the whole life of the match. Two small file reads, so it adds
    nothing measurable to a scan.
    """
    published: Dict[str, str] = {}
    for filename in ("today-match.json", "upcoming.json"):
        path = Path(data_root) / filename
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        for entry in payload.get("items") or []:
            if not isinstance(entry, dict):
                continue
            key = _event_identity_name(entry.get("name"))
            existing = str(entry.get("id") or "").strip()
            if key and existing:
                published.setdefault(key, existing)

    if not published:
        return 0

    # Reuse must never hand a card an id that another card in this same scan is
    # already using. It did: one fixture was published as both
    # `sri-lanka-vs-india-1st-test` and `sri-lanka-vs-india`, because the second
    # card's name matched a previously published entry whose id the first card had
    # legitimately minted. Two cards then answered to overlapping identities and
    # the frontend had no way to tell which one it had open.
    claimed: Dict[str, str] = {}
    for item in items:
        event_id = str(item.get("id") or "").strip()
        if event_id:
            claimed.setdefault(event_id, _event_identity_name(item.get("name")))

    reused = 0
    for item in items:
        key = _event_identity_name(item.get("name"))
        previous = published.get(key)
        if not previous:
            continue
        current = str(item.get("id") or "").strip()
        if current == previous:
            continue
        owner = claimed.get(previous)
        if owner is not None and owner != key:
            # Another fixture in this scan owns that id. Keeping the freshly
            # minted id is the safe answer: a card with its own identity is
            # recoverable, two cards sharing one identity is not.
            continue
        item["previous_event_id"] = current
        item["id"] = previous
        item["promoted_card"] = True
        claimed.pop(current, None)
        claimed[previous] = key
        reused += 1
    return reused


TEAM_SEPARATOR = re.compile(r"(?i)\s+(?:versus|vs\.?|v\.?)\s+")

# Words that describe the broadcast or the competition rather than the teams.
# Everything from the first of these onwards is competition/channel noise.
_TEAM_TAIL_NOISE = re.compile(
    r"(?i)\b(?:"
    r"premier|league|liga|bundesliga|eredivisie|serie|ligue|division|"
    r"championship|cup|trophy|series|test|odi|t20|tnpl|cpl|ipl|bpl|"
    r"friendl(?:y|ies)|qualif(?:ier|ying)|round|matchday|group|women|men|"
    r"willow|crichd|criclife|tapmad|fancode|sony|star|fox|ptv|supersport|"
    r"server|hd|fhd|uhd|sd|live|stream|"
    # Labels a playlist puts on a link rather than on a team: "Braves vs
    # Diamondbacks Quality", "R Racing Club vs Villarreal Link 1".
    r"quality|link|alt|mirror|option|backup|feed"
    r")\b"
)


# A round descriptor can sit in FRONT of the participants as easily as behind
# them: cricket playlists write "1st Test Australia vs Bangladesh" while the
# fixture feed writes "Australia vs Bangladesh 2nd Test". Cutting at the first
# noise word handles the second shape and destroys the first - "1st Test
# Australia" collapses to "1st", so the two never meet and the stream can never
# be hung on its fixture. This pattern is removed from the front instead.
_TEAM_LEADING_ROUND = re.compile(
    r"(?i)^\s*(?:\d{1,3}(?:st|nd|rd|th)\s+)?"
    r"(?:tests?|odis?|t20i?s?|t10|matches?|match|legs?|rounds?|days?|"
    r"semi[-\s]?finals?|quarter[-\s]?finals?|finals?|qf|sf)\s+(?=\S)"
)

# "Bangladesh 2nd" after the tail cut is still the fixture's round, not a team.
# Only an explicit ordinal is removed, so "Felgueiras 1932" keeps its year.
_TEAM_TRAILING_ORDINAL = re.compile(r"(?i)\s+\d{1,3}(?:st|nd|rd|th)$")


def team_pair_key(name: str) -> str:
    """Identity built from the participants alone.

    A fixture feed says `Amarante vs Lusitania Lourosa`; the playlist relaying
    it says `Amarante vs Lusitania Lourosa Segunda Liga`. Keyed on the whole
    title those never meet, which is why every Upcoming card was published with
    no stream at all. Cutting the title at the competition leaves the one part
    both sides always agree on.
    """
    text = str(name or "").casefold()
    if "|" in text:
        pipe_parts = [p.strip() for p in text.split("|") if p.strip()]
        for p in pipe_parts:
            if re.search(r"\b(?:vs|v|versus)\b", p):
                text = p
                break
        else:
            text = pipe_parts[0] if pipe_parts else text

    parts = TEAM_SEPARATOR.split(text, maxsplit=1)
    if len(parts) != 2:
        return ""

    def clean(side: str) -> str:
        side = re.split(r"\s+-\s+", side, maxsplit=1)[0]
        side = re.sub(r"(?i)^.*?\b(?:tour\s+of\s+[a-z\s]+?(?:\s+\d{4})?|series|trophy|cup|championship)\b\s*", "", side)
        side = _TEAM_LEADING_ROUND.sub("", side, count=1)
        noise = _TEAM_TAIL_NOISE.search(side)
        if noise:
            side = side[: noise.start()]
        side = re.sub(r"[^\w\s]", " ", side)
        side = " ".join(side.split())
        return _TEAM_TRAILING_ORDINAL.sub("", side).strip()

    left, right = clean(parts[0]), clean(parts[1])
    if not left or not right:
        return ""
    return f"{left}|{right}"


def _key_sides(key: str) -> Optional[Tuple[frozenset, frozenset, str, str]]:
    """Split a participants key into the word sets and the distinctive word."""
    if "|" not in key:
        return None
    left, right = (part.split() for part in key.split("|", 1))
    if not left or not right:
        return None
    return frozenset(left), frozenset(right), left[-1], right[-1]


def _side_matches(
    stream: Tuple[frozenset, str], fixture: Tuple[frozenset, str]
) -> bool:
    """Whether two spellings name the same club.

    One side may name the club more fully than the other - "Orioles" against
    "Baltimore Orioles" - so containment either way is allowed. Two guards keep
    that from becoming a wildcard: the last word has to be the same, so "Sox"
    cannot answer to both "Boston Red Sox" and "Chicago White Sox"; and the words
    they share must include a real one, so a pair of initials cannot match.
    """
    stream_tokens, stream_last = stream
    fixture_tokens, fixture_last = fixture
    if not stream_tokens or not fixture_tokens:
        return False
    if stream_tokens == fixture_tokens:
        return True
    if not (stream_tokens <= fixture_tokens or fixture_tokens <= stream_tokens):
        return False
    if stream_last != fixture_last:
        return False
    return any(len(token) >= 4 for token in stream_tokens & fixture_tokens)


def _pairs_match(
    stream: Tuple[frozenset, frozenset, str, str],
    fixture: Tuple[frozenset, frozenset, str, str],
) -> bool:
    """The same two clubs, in either order - the fixture supplies the order."""
    s_left, s_right, s_left_last, s_right_last = stream
    f_left, f_right, f_left_last, f_right_last = fixture
    same_order = (
        _side_matches((s_left, s_left_last), (f_left, f_left_last))
        and _side_matches((s_right, s_right_last), (f_right, f_right_last))
    )
    if same_order:
        return True
    return (
        _side_matches((s_left, s_left_last), (f_right, f_right_last))
        and _side_matches((s_right, s_right_last), (f_left, f_left_last))
    )


def _display_name_without_broadcaster(item: Dict[str, Any]) -> str:
    """The title with the trailing broadcaster removed, if that layer is loaded.

    A playlist writes the broadcaster into the title - "Sevilla Vs Rayo
    Vallecano beiN ENGLISH" - so the participants-only key still ends up with
    "bein english" glued to the second team. Section 5's helper already knows
    how to cut a resolved broadcaster off a title, so it is reused here rather
    than reimplemented. Imported lazily and defensively: attachment must keep
    working on the raw title if the channel layer is absent.
    """
    try:
        from scanner.merger import fixture_display_name
    except Exception:  # pragma: no cover - optional layer
        return ""
    try:
        return str(fixture_display_name(item) or "")
    except Exception:  # pragma: no cover - never break attachment over a name
        return ""


def attach_streams_to_fixtures(
    items: List[Dict[str, Any]],
    authority_source_ids: set,
    attachment_pool: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Guide 30.7: build the fixture first, then hang matching streams on it.

    A fixture feed carries no playable link and a playlist carries no reliable
    schedule, so on their own each is half a card: 88 Upcoming fixtures with
    nothing to play, beside verified streams nobody could place on a timeline.
    Re-labelling a matching stream with its fixture's identity lets the ordinary
    merge fold them into one card with primary and backups, which is exactly the
    structure the guide asks for.

    attachment_pool carries the stream-only candidates that the enrichment gate
    refused to publish - see the note on enrich_event_candidates. They are
    offered a fixture here and nothing else: a pool item joins the output only
    when an authority or catalogue fixture claims it, and it arrives wearing that
    fixture's identity, clock and status. So the pool can add broadcasters to an
    event authority already established, and can never bring an event of its own.
    """
    fixtures_by_team = {}
    for item in items:
        if str(item.get("source_id") or "") not in authority_source_ids:
            continue
        key = team_pair_key(item.get("name"))
        if key:
            fixtures_by_team.setdefault(key, item)

    stats = {
        "streams_attached": 0,
        "fixtures_with_stream": 0,
        "pool_offered": len(attachment_pool or []),
        "pool_attached": 0,
        "pool_unclaimed": 0,
    }
    if not fixtures_by_team:
        stats["pool_unclaimed"] = stats["pool_offered"]
        return items, stats

    # Longest first so "Arsenal|Manchester City" is preferred over a shorter
    # fixture that happens to share a prefix.
    fixture_keys = sorted(fixtures_by_team, key=len, reverse=True)
    fixture_sides = {key: _key_sides(key) for key in fixture_keys}

    def find_fixture(stream_key: str):
        exact = fixtures_by_team.get(stream_key)
        if exact is not None:
            return exact
        # A playlist titles the same match as the fixture plus its competition
        # ("... Segunda Liga", "... Currie Cup"), so the fixture key is a
        # prefix of the stream key. The space guard stops "Arsenal|Man" from
        # capturing "Arsenal|Manchester City".
        for key in fixture_keys:
            if stream_key.startswith(key) and stream_key[len(key):len(key) + 1] == " ":
                return fixtures_by_team[key]

        # The two feeds routinely name the same club at different lengths, and in
        # either order. The fixture feed says "Baltimore Orioles vs Tampa Bay
        # Rays"; the playlist says "Rays vs Orioles". Neither equality nor a
        # prefix can see that those are one game, which is why an entire slate of
        # fixtures published with nothing to play beside streams nobody could
        # place. Matched on the club words instead, with the distinctive word
        # required to agree and an all-or-nothing uniqueness rule.
        stream_sides = _key_sides(stream_key)
        if stream_sides is None:
            return None
        found = None
        for key in fixture_keys:
            sides = fixture_sides.get(key)
            if sides is None or not _pairs_match(stream_sides, sides):
                continue
            if found is not None and found is not fixtures_by_team[key]:
                # More than one fixture answers to these club words, so there is
                # no honest way to pick. Attach to none of them.
                return None
            found = fixtures_by_team[key]
        return found

    def match_fixture(item: Dict[str, Any]):
        """Try the title as given, then the title with its broadcaster removed."""
        for candidate_name in (
            item.get("name"),
            _display_name_without_broadcaster(item),
        ):
            key = team_pair_key(candidate_name)
            if not key:
                continue
            fixture = find_fixture(key)
            if fixture is not None:
                return fixture, key
        return None, ""

    enriched_fixture_keys = set()
    output: List[Dict[str, Any]] = []
    for item in items:
        if str(item.get("source_id") or "") in authority_source_ids:
            output.append(item)
            continue
        if not _stream_is_usable(item):
            output.append(item)
            continue

        fixture, key = match_fixture(item)
        if fixture is None:
            output.append(item)
            continue

        attached = copy.deepcopy(item)
        # The fixture owns the identity and the clock; the stream keeps only
        # what makes it playable.
        attached["name"] = fixture["name"]
        for field in (
            "competition", "fixture_id", "start_time", "start_at", "end_time",
            "schedule_status", "status", "schedule_verified",
            "schedule_source_url", "time_verification", "schedule_authority",
        ):
            if field in fixture:
                attached[field] = fixture[field]
        # Guide 30.8 step 6: the fixture was in play but had no link, so it was
        # parked as LINK_UPDATING on the Upcoming tab. A working stream has now
        # arrived, so the event is genuinely playable and belongs on Today
        # Match. Without this the card stays in Upcoming holding a live stream
        # nobody can reach from there.
        if str(fixture.get("schedule_status") or "").upper() == "LINK_UPDATING":
            attached["schedule_status"] = "LIVE_NOW"
            attached["status"] = "LIVE_NOW"
            attached["promoted_from_upcoming"] = True

        attached["stream_attached_to_fixture"] = True
        output.append(attached)
        stats["streams_attached"] += 1
        enriched_fixture_keys.add(key)

    # The suppressed pool gets exactly the same treatment, and only that.
    for item in attachment_pool or []:
        if not isinstance(item, dict) or not _stream_is_usable(item):
            stats["pool_unclaimed"] += 1
            continue
        fixture, key = match_fixture(item)
        if fixture is None:
            # No fixture claims it, so it stays suppressed - which is the
            # behaviour it had before the pool existed.
            stats["pool_unclaimed"] += 1
            continue

        attached = copy.deepcopy(item)
        attached["name"] = fixture["name"]
        for field in (
            "competition", "fixture_id", "start_time", "start_at", "end_time",
            "schedule_status", "status", "schedule_verified",
            "schedule_source_url", "time_verification", "schedule_authority",
        ):
            if field in fixture:
                attached[field] = fixture[field]
        if str(fixture.get("schedule_status") or "").upper() == "LINK_UPDATING":
            attached["schedule_status"] = "LIVE_NOW"
            attached["status"] = "LIVE_NOW"
            attached["promoted_from_upcoming"] = True
        attached["stream_attached_to_fixture"] = True
        # Auditability: this stream reached the public output only because a
        # fixture claimed it, and a report can say which ones those were.
        attached["attached_from_suppressed_pool"] = True
        output.append(attached)
        stats["streams_attached"] += 1
        stats["pool_attached"] += 1
        enriched_fixture_keys.add(key)

    stats["fixtures_with_stream"] = len(enriched_fixture_keys)
    return output, stats


def enrich_event_candidates(
    candidates: List[Dict[str, Any]],
    fixture_path: str | Path = "config/event-fixtures.json",
    timezone_name: str = "Asia/Dhaka",
    now: Optional[datetime] = None,
    future_days: int = 120,
    authority_source_ids: Optional[set] = None,
    provider_event_hours: int = DEFAULT_PROVIDER_EVENT_HOURS,
    attachment_pool: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Decide which candidates may become public event cards.

    This is a publish gate, and it stays one: a stream-only playlist entry with
    no catalogue fixture and no fixture-authority feed behind it does not become
    a card. What it must not also be is a shredder. A candidate refused here used
    to be deleted, and deleting it is what silently cost the published cards
    their broadcasters: the very entries that carry a broadcaster in the title
    are the ones a playlist supplies, so they were gone before
    attach_streams_to_fixtures - the stage whose whole job is to hang a stream on
    a fixture - ever saw them. Both halves of the card were present in the same
    scan and never introduced.

    So when attachment_pool is given, a refused stream-only candidate is placed
    in it rather than dropped. The gate is unchanged: nothing in the pool is in
    the returned output, and a pool item can only ever re-enter through a fixture
    that authority or the catalogue already established. Pass no pool and the
    old behaviour is exactly what happens.
    """
    now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    source_zone = _zone(timezone_name, "Asia/Dhaka", "+06:00")
    fixtures = load_fixtures(fixture_path)
    relevant = [
        fixture for fixture in fixtures
        if fixture["end"] > now_utc
        and fixture["start"] <= now_utc + timedelta(days=future_days)
    ]
    authority = (
        DEFAULT_FIXTURE_AUTHORITY_SOURCES
        if authority_source_ids is None
        else {str(value).strip() for value in authority_source_ids if str(value).strip()}
    )
    output: List[Dict[str, Any]] = []
    stats = {
        "matched": 0,
        "corrected": 0,
        "catalogue": 0,
        "provider_fixture": 0,
        "provider_rejected": 0,
        "ambiguous_suppressed": 0,
        "unverified_suppressed": 0,
    }
    matched_fixture_ids: set[str] = set()

    for original in candidates:
        item = copy.deepcopy(original)
        name = str(item.get("name") or "")
        # The feed's own zone, not the site's. See SOURCE_CLOCK_ZONES.
        item_zone = source_clock_zone(item.get("source_id"), source_zone)
        source_time = _parse_source_time(item.get("start_time"), item_zone, now_utc)
        # `relevant` intentionally excludes completed fixtures.  Before using
        # the reusable-channel fallback, nevertheless check the full official
        # catalogue so an ended match link can never return as CHANNEL_LIVE.
        historical_match = (
            _best_fixture(item, fixtures, now_utc) if _is_exact_event(name) else None
        )
        if historical_match and historical_match["end"] <= now_utc:
            stats["unverified_suppressed"] += 1
            continue
        best = _best_fixture(item, relevant, now_utc) if _is_exact_event(name) else None
        if best:
            item = _apply_fixture(item, best, source_time)
            item = _classify(item, now_utc)
            matched_fixture_ids.add(best["fixture_id"])
            stats["matched"] += 1
            if item.get("time_verification") == "corrected":
                stats["corrected"] += 1
            output.append(item)
            continue

        # A label that names its own round identifies one fixture even when the
        # series has several running at once, so it is asked before the broad
        # "one live fixture in this competition" rule below.
        round_match = _competition_round_fixture(item, relevant, now_utc)
        if round_match is not None and str(item.get("url") or "").strip():
            item = _classify(_apply_fixture(item, round_match, source_time), now_utc)
            matched_fixture_ids.add(round_match["fixture_id"])
            stats["matched"] += 1
            stats["round_label_matched"] = int(stats.get("round_label_matched", 0)) + 1
            output.append(item)
            continue

        competition_fixtures = [fixture for fixture in relevant if _competition_matches(name, fixture)]
        if competition_fixtures and not _is_exact_event(name):
            current = [
                fixture for fixture in competition_fixtures
                if fixture["start"] - timedelta(minutes=20) <= now_utc <= fixture["end"]
                and (not _gender(name) or _gender(name) == _gender(fixture["name"]))
            ]
            if len(current) == 1 and str(item.get("url") or "").strip():
                item = _classify(_apply_fixture(item, current[0], source_time), now_utc)
                matched_fixture_ids.add(current[0]["fixture_id"])
                stats["matched"] += 1
                output.append(item)
            else:
                stats["ambiguous_suppressed"] += 1
                fallback = _today_source_channel_fallback(item)
                if fallback is not None:
                    output.append(fallback)
            continue

        # No entry in the local catalogue. A fixture-authority feed states the
        # participants, the kickoff time and whether play has started, which is
        # evidence enough on its own; a stream-only playlist is not and still
        # falls through to the channel handling below.
        if str(item.get("source_id") or "").strip() in authority:
            provider_item = _provider_fixture_item(
                item, source_time, now_utc, provider_event_hours
            )
            if provider_item is not None:
                stats["provider_fixture"] += 1
                output.append(provider_item)
                continue
            stats["provider_rejected"] += 1
            continue

        # A stream source's own date is useful evidence, but it is not
        # authoritative enough to publish an event card by itself. The raw
        # candidate remains in scan reports; it becomes public only after a
        # catalogue or fixture-authority match.
        stats["unverified_suppressed"] += 1
        fallback = _today_source_channel_fallback(item)
        if fallback is not None:
            output.append(fallback)
        elif attachment_pool is not None and _stream_is_usable(item):
            # Refused as a card, offered as a broadcaster. It only becomes
            # public if a fixture claims it in attach_streams_to_fixtures, and
            # then it is that fixture's event, not this candidate's. Items that
            # already produced a channel-card fallback are deliberately excluded
            # so the same link cannot arrive twice by two different routes.
            stats["pooled_for_attachment"] = int(stats.get("pooled_for_attachment", 0)) + 1
            attachment_pool.append(item)

    for fixture in relevant:
        if fixture["fixture_id"] in matched_fixture_ids:
            continue
        metadata = {
            "id": fixture["fixture_id"],
            "name": fixture["name"],
            "logo": "",
            "url": "",
            "headers": {},
            "drm": {},
            "source_id": "official-fixture-catalogue",
            "source_name": fixture["competition"],
            "source_url": fixture["schedule_source_url"],
            "source_priority": 2000,
            "source_pipeline": "upcoming",
            "metadata_only": True,
            "allow_without_stream": True,
            "verification_status": "metadata_only",
            "publish_allowed": True,
        }
        metadata = _classify(_apply_fixture(metadata, fixture, None), now_utc)
        output.append(metadata)
        stats["catalogue"] += 1
    return output, stats
