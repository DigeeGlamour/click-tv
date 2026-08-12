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
    competition: Dict[str, Any], name: str, start_text: str, venue: str = ""
) -> Dict[str, Any]:
    source_zone = _zone(
        competition.get("timezone"), "UTC", competition.get("utc_offset", "+00:00")
    )
    start = datetime.fromisoformat(start_text)
    if start.tzinfo is None:
        start = start.replace(tzinfo=source_zone)
    duration = int(competition.get("duration_minutes") or 240)
    return {
        "fixture_id": f"{competition.get('id')}:{_norm(name).replace(' ', '-')}",
        "name": name,
        "competition": str(competition.get("name") or "Live Sports"),
        "competition_id": str(competition.get("id") or ""),
        "competition_aliases": [_norm(v) for v in competition.get("aliases", [])],
        "start": start.astimezone(timezone.utc),
        "end": (start + timedelta(minutes=duration)).astimezone(timezone.utc),
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
                    competition, str(entry["name"]), str(entry["start"]), str(entry.get("venue") or "")
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
                    name = f"The Hundred {gender} {round_name}".strip()
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
    }
    return {part for part in _norm(value).split() if part not in ignored and not part.isdigit()}


def _gender(value: str) -> str:
    normalized = _norm(value)
    if re.search(r"\bw(?:omen)?\b", normalized):
        return "women"
    if re.search(r"\bmen\b", normalized):
        return "men"
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


def _fixture_score(candidate_name: str, fixture: Dict[str, Any]) -> int:
    candidate = _norm(candidate_name)
    fixture_name = _norm(fixture.get("name"))
    candidate_tokens = _team_tokens(candidate)
    fixture_tokens = _team_tokens(fixture_name)
    overlap = len(candidate_tokens & fixture_tokens)
    if not overlap:
        return 0
    score = overlap * 10
    if fixture_tokens and fixture_tokens.issubset(candidate_tokens):
        score += 50
    candidate_gender, fixture_gender = _gender(candidate), _gender(fixture_name)
    if candidate_gender and fixture_gender:
        score += 20 if candidate_gender == fixture_gender else -100
    ordinal = re.search(r"\b(\d+)(?:st|nd|rd|th)?\b", candidate)
    fixture_ordinal = re.search(r"\b(\d+)(?:st|nd|rd|th)?\b", fixture_name)
    if ordinal and fixture_ordinal:
        score += 25 if ordinal.group(1) == fixture_ordinal.group(1) else -50
    return score


def _competition_matches(name: str, fixture: Dict[str, Any]) -> bool:
    normalized = _norm(name)
    return any(alias and alias in normalized for alias in fixture.get("competition_aliases", []))


def _best_fixture(name: str, fixtures: Iterable[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    scored = sorted(
        ((_fixture_score(name, fixture), fixture) for fixture in fixtures),
        key=lambda pair: pair[0], reverse=True,
    )
    return scored[0][1] if scored and scored[0][0] >= 40 else None


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
    local_now = now.astimezone(source_timezone)
    day = local_now.date()
    if re.match(r"(?i)^tomorrow\b", cleaned):
        day += timedelta(days=1)
        cleaned = re.sub(r"(?i)^tomorrow\s+", "", cleaned)
    for pattern in ("%Y-%m-%d %I:%M %p", "%Y-%m-%d %H:%M", "%d-%m-%Y %I:%M %p"):
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
        item["source_pipeline"] = "today_match" if playable else "upcoming"
    elif now < start:
        minutes = (start - now).total_seconds() / 60
        item["schedule_status"] = "STARTING_SOON" if minutes <= 60 else "UPCOMING"
        item["status"] = item["schedule_status"]
        item["source_pipeline"] = "upcoming"
    else:
        item["schedule_status"] = "ENDED"
        item["status"] = "ENDED"
    return item


def enrich_event_candidates(
    candidates: List[Dict[str, Any]],
    fixture_path: str | Path = "config/event-fixtures.json",
    timezone_name: str = "Asia/Dhaka",
    now: Optional[datetime] = None,
    future_days: int = 120,
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    source_zone = _zone(timezone_name, "Asia/Dhaka", "+06:00")
    fixtures = load_fixtures(fixture_path)
    relevant = [
        fixture for fixture in fixtures
        if fixture["end"] >= now_utc - timedelta(hours=6)
        and fixture["start"] <= now_utc + timedelta(days=future_days)
    ]
    output: List[Dict[str, Any]] = []
    stats = {
        "matched": 0,
        "corrected": 0,
        "catalogue": 0,
        "ambiguous_suppressed": 0,
        "unverified_suppressed": 0,
    }
    matched_fixture_ids: set[str] = set()

    for original in candidates:
        item = copy.deepcopy(original)
        name = str(item.get("name") or "")
        source_time = _parse_source_time(item.get("start_time"), source_zone, now_utc)
        best = _best_fixture(name, relevant) if _is_exact_event(name) else None
        if best:
            item = _apply_fixture(item, best, source_time)
            item = _classify(item, now_utc)
            matched_fixture_ids.add(best["fixture_id"])
            stats["matched"] += 1
            if item.get("time_verification") == "corrected":
                stats["corrected"] += 1
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
            continue

        # A source-provided date is useful evidence, but it is not authoritative
        # enough to publish an event card by itself. The raw candidate remains in
        # scan reports; it becomes public only after an exact catalogue match.
        stats["unverified_suppressed"] += 1

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
