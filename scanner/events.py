"""
Today Match and Upcoming Events Processor

Reads event candidates from working/bd-results.json, merges duplicate sources,
removes stale/expired cards, keeps only playable Today Match cards, and returns
stable payloads for scanner/output.py.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    from scanner.live_protection import probe_card_is_playable, protect_live_events
    from scanner.targeted_scan import fixture_key, has_valid_link
    from scanner.source_coverage import build_source_coverage, write_source_coverage
    from scanner.merger import event_sport, sport_sort_index, load_previous_primary_keys, merge_candidates
    from scanner.schedule_resolver import (
        DEFAULT_FIXTURE_AUTHORITY_SOURCES,
        DEFAULT_PROVIDER_EVENT_HOURS,
        attach_streams_to_fixtures,
        enrich_event_candidates,
        reuse_published_event_ids,
    )
except ImportError:
    module_dir = str(Path(__file__).resolve().parent)
    if module_dir not in sys.path:
        sys.path.insert(0, module_dir)
    from live_protection import probe_card_is_playable, protect_live_events
    from targeted_scan import fixture_key, has_valid_link
    from source_coverage import build_source_coverage, write_source_coverage
    from merger import event_sport, sport_sort_index, load_previous_primary_keys, merge_candidates
    from schedule_resolver import (
        DEFAULT_FIXTURE_AUTHORITY_SOURCES,
        DEFAULT_PROVIDER_EVENT_HOURS,
        attach_streams_to_fixtures,
        enrich_event_candidates,
        reuse_published_event_ids,
    )


DEFAULT_TODAY_MAX_AGE_HOURS = 12
DEFAULT_UPCOMING_PAST_GRACE_HOURS = 3
DEFAULT_UPCOMING_FUTURE_DAYS = 120

FAILED_STATUSES = {
    "failed",
    "failed_bd",
    "404_quarantined",
    "rejected_low_quality",
    "quarantine",
}

CONFIRMED_PLAYABLE_STATUSES = {
    "verified",
    "verified_global",
    "verified_proxy",
    "verified_bd",
}


def _utc_now_dt() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now() -> str:
    return _utc_now_dt().isoformat()


def _load_required_results(file_path: str | Path) -> List[Dict[str, Any]]:
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"BD results file not found: {file_path}")

    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(
            f"BD results file could not be read: {file_path}: {error}"
        ) from error

    if not isinstance(data, dict) or "results" not in data:
        raise ValueError(
            f"BD results file is invalid or missing 'results': {file_path}"
        )

    results = data.get("results")
    if not isinstance(results, list):
        raise ValueError(
            f"BD results field 'results' must be a list: {file_path}"
        )

    return [item for item in results if isinstance(item, dict)]


def _load_optional_json(file_path: str | Path) -> Dict[str, Any]:
    path = Path(file_path)
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}


def _safe_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def _parse_datetime(
    value: Any,
    default_timezone: timezone | ZoneInfo = timezone.utc,
) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None

    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        parsed = None

    if parsed is None:
        for pattern in (
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d %I:%M:%S %p",
            "%Y-%m-%d %I:%M %p",
            "%d-%m-%Y %H:%M:%S",
            "%d-%m-%Y %H:%M",
            "%d-%m-%Y %I:%M:%S %p",
            "%d-%m-%Y %I:%M %p",
        ):
            try:
                parsed = datetime.strptime(text, pattern)
                break
            except ValueError:
                continue

    if parsed is None:
        relative_text = re.sub(r"(?i)\s*(?:BDT|BST|UTC|GMT)\s*$", "", text).strip()
        local_now = datetime.now(default_timezone)
        tomorrow_match = re.fullmatch(
            r"(?i)tomorrow\s+(\d{1,2}(?::\d{2})?(?:\s*[AP]M)?)",
            relative_text,
        )
        if tomorrow_match:
            clock_text = tomorrow_match.group(1).strip()
            for pattern in ("%I:%M %p", "%I %p", "%H:%M"):
                try:
                    clock = datetime.strptime(clock_text, pattern).time()
                    parsed = datetime.combine(
                        (local_now + timedelta(days=1)).date(),
                        clock,
                        tzinfo=default_timezone,
                    )
                    break
                except ValueError:
                    continue

        if parsed is None:
            for pattern in ("%a, %b %d %I:%M %p", "%a, %b %d %I %p", "%b %d %I:%M %p"):
                try:
                    partial = datetime.strptime(relative_text, pattern)
                    candidate = partial.replace(year=local_now.year, tzinfo=default_timezone)
                    if candidate < local_now - timedelta(days=30):
                        candidate = candidate.replace(year=local_now.year + 1)
                    parsed = candidate
                    break
                except ValueError:
                    continue

    if parsed is None:
        # Daily sports feeds commonly provide only "7 PM BDT" or
        # "Live at 11:30 PM BDT". Anchor those values to today's date in the
        # configured source timezone so freshness and ordering remain useful.
        time_only = re.sub(r"(?i)^\s*live\s+at\s+", "", text)
        time_only = re.sub(r"(?i)\s*(?:BDT|BST|UTC|GMT)\s*$", "", time_only).strip()
        for pattern in ("%I:%M:%S %p", "%I:%M %p", "%I %p", "%H:%M:%S", "%H:%M"):
            try:
                clock = datetime.strptime(time_only, pattern).time()
                local_now = datetime.now(default_timezone)
                parsed = datetime.combine(local_now.date(), clock, tzinfo=default_timezone)
                break
            except ValueError:
                continue

    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=default_timezone)
    return parsed.astimezone(timezone.utc)


def _sort_time(
    value: Any,
    default_timezone: timezone | ZoneInfo = timezone.utc,
) -> str:
    parsed = _parse_datetime(value, default_timezone)
    if parsed is None:
        return "9999-12-31T23:59:59+00:00"
    return parsed.isoformat()


def _event_sort_key(
    item: Dict[str, Any],
    default_timezone: timezone | ZoneInfo = timezone.utc,
) -> Tuple[int, str, str, str]:
    # Requirement 11. Cricket first, football second, every other sport after -
    # then the existing kickoff/competition/name order inside each group.
    sport_rank = sport_sort_index(item.get("sport_type") or event_sport(item))
    start_time = _sort_time(item.get("start_time"), default_timezone)
    competition = re.sub(
        r"\s+",
        " ",
        str(item.get("competition") or "").strip(),
    ).casefold()
    name = re.sub(
        r"\s+",
        " ",
        str(item.get("name") or "").strip(),
    ).casefold()
    return sport_rank, start_time, competition, name


def _primary_url(item: Dict[str, Any]) -> str:
    for key in ("url", "stream_url", "link"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return ""


def _backup_urls(item: Dict[str, Any]) -> List[str]:
    backups = item.get("backups")
    if not isinstance(backups, list):
        return []

    urls: List[str] = []
    for backup in backups[:5]:
        if isinstance(backup, str):
            value = backup.strip()
        elif isinstance(backup, dict):
            value = str(
                backup.get("url")
                or backup.get("stream_url")
                or backup.get("link")
                or ""
            ).strip()
        else:
            value = ""
        if value:
            urls.append(value)
    return urls


def _is_playable(item: Dict[str, Any]) -> bool:
    if item.get("metadata_only") is True:
        return False
    if item.get("publish_allowed") is False:
        return False
    status = str(item.get("verification_status") or "").strip().lower()
    if status in FAILED_STATUSES:
        return False
    # An URL, or even an HTTP 200 response, is not enough for Today Match.
    # These verified states are assigned only after protocol-aware validation;
    # for HLS that includes a readable media playlist and media segment.
    confirmed = item.get("verified") is True or item.get("is_valid") is True
    if not confirmed or status not in CONFIRMED_PLAYABLE_STATUSES:
        return False
    return bool(_primary_url(item) or _backup_urls(item))


def _is_today_fresh(
    item: Dict[str, Any],
    now: datetime,
    max_age_hours: int,
) -> bool:
    schedule_status = str(
        item.get("schedule_status") or item.get("status") or ""
    ).strip().upper()
    if schedule_status == "ENDED":
        return False

    end_time = _parse_datetime(
        item.get("end_time"),
        item.get("_source_timezone", timezone.utc),
    )
    # Today Match is a live surface, not a recent-results archive.  Remove a
    # card as soon as its authoritative fixture end_time is reached.
    if end_time is not None and end_time <= now:
        return False

    # An official multi-day fixture remains current until its authoritative
    # end time.  The generic age guard below is only a fallback for feeds that
    # do not carry a verified schedule.
    if item.get("schedule_verified") is True and end_time is not None:
        return True

    start_time = _parse_datetime(
        item.get("start_time"),
        item.get("_source_timezone", timezone.utc),
    )
    if start_time is None:
        return True

    if start_time > now + timedelta(hours=6):
        return False
    if start_time < now - timedelta(hours=max_age_hours):
        return False
    return True


def _is_upcoming_fresh(
    item: Dict[str, Any],
    now: datetime,
    past_grace_hours: int,
    future_days: int,
) -> bool:
    start_time = _parse_datetime(
        item.get("start_time"),
        item.get("_source_timezone", timezone.utc),
    )
    if start_time is None:
        return True
    if start_time < now - timedelta(hours=past_grace_hours):
        return False
    if start_time > now + timedelta(days=future_days):
        return False
    return True


LIVE_SCHEDULE_STATUSES = frozenset({"LIVE_NOW", "LIVE", "CHANNEL_LIVE", "IN_PROGRESS"})
UPCOMING_SCHEDULE_STATUSES = frozenset({
    "UPCOMING", "STARTING_SOON", "LINK_UPDATING", "NOT_STARTED", "SCHEDULED",
})


def _destination_for(card: Dict[str, Any]) -> str:
    """Decide Today Match vs Upcoming from the event, not from its source file.

    Routing used to read `source_pipeline`, so a match stayed wherever its
    playlist happened to be configured. A live fixture that arrived from an
    "upcoming" feed was therefore filed as Upcoming and then dropped for having
    started in the past - which is how `Sri Lanka vs India 1st Test`, carrying
    five working streams, vanished from both tabs. The schedule status is what
    actually decides where an event belongs; the source group is only a hint
    for anything with no resolved status at all.
    """
    status = str(card.get("schedule_status") or card.get("status") or "").strip().upper()
    if status in LIVE_SCHEDULE_STATUSES:
        return "today_match"
    if status in UPCOMING_SCHEDULE_STATUSES:
        return "upcoming"
    if status == "ENDED":
        return "ended"
    return str(card.get("source_pipeline") or "").strip().lower()


def _payload(
    items: List[Dict[str, Any]],
    event_type: str,
    filtered_stale: int,
    filtered_unplayable: int,
    source_timezone: timezone | ZoneInfo = timezone.utc,
) -> Dict[str, Any]:
    # Requirement 11. A card carried forward from a previous publish (see
    # requirement 6) predates the sport field, so fill it in before sorting -
    # otherwise those cards would all sort as "other" and land behind the rest.
    candidates = [item for item in items if isinstance(item, dict)]
    for item in candidates:
        if not str(item.get("sport_type") or "").strip():
            item["sport_type"] = event_sport(item)
    ordered = sorted(
        candidates,
        key=lambda item: _event_sort_key(item, source_timezone),
    )
    for item in ordered:
        item.pop("_source_timezone", None)
    return {
        "type": event_type,
        "updated_at": _utc_now(),
        "count": len(ordered),
        "filtered_stale": filtered_stale,
        "filtered_unplayable": filtered_unplayable,
        "items": ordered,
    }


def process_events(
    bd_results_path: str = "working/bd-results.json",
    settings_path: str = "config/settings.json",
    fixture_path: str = "config/event-fixtures.json",
    *,
    now: Optional[datetime] = None,
    targeted_window_minutes: int = 0,
    targeted_keys: Optional[set] = None,
) -> Dict[str, Dict[str, Any]]:
    """Return freshness-checked today_match and upcoming payloads.

    targeted_window_minutes implements requirement 4: with a window set, only
    fixtures kicking off inside it are treated as scan targets. Every other
    future fixture keeps the card it already has, so a five-minute trigger can
    chase the links of the matches about to start without re-verifying a
    hundred fixtures that are still hours away.

    targeted_keys narrows that further, and is the corrected behaviour: the
    caller has already decided which individual fixtures this trigger may work
    on, having excluded the ones that produced a valid link on an earlier tick.
    A fixture inside the window but absent from targeted_keys is therefore left
    exactly as published - it is not re-scanned every five minutes.
    """
    results = _load_required_results(bd_results_path)
    settings = _load_optional_json(settings_path)
    event_settings = settings.get("events") if isinstance(settings.get("events"), dict) else {}
    fixture_path = str(event_settings.get("fixture_catalogue") or fixture_path)
    timezone_name = str(
        event_settings.get("timezone")
        or settings.get("timezone")
        or "UTC"
    ).strip()
    try:
        source_timezone: timezone | ZoneInfo = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        source_timezone = (
            timezone(timedelta(hours=6), name="BDT")
            if timezone_name.casefold() in {"asia/dhaka", "bdt"}
            else timezone.utc
        )


    today_max_age_hours = _safe_int(
        event_settings.get("today_max_age_hours"),
        DEFAULT_TODAY_MAX_AGE_HOURS,
        2,
        48,
    )
    upcoming_past_grace_hours = _safe_int(
        event_settings.get("upcoming_past_grace_hours"),
        DEFAULT_UPCOMING_PAST_GRACE_HOURS,
        0,
        24,
    )
    upcoming_future_days = _safe_int(
        event_settings.get("upcoming_future_days"),
        DEFAULT_UPCOMING_FUTURE_DAYS,
        1,
        365,
    )

    raw_event_candidates = [
        dict(item)
        for item in results
        if str(item.get("source_pipeline") or "").strip().lower()
        in {"today_match", "upcoming"}
    ]

    configured_authority = event_settings.get("fixture_authority_sources")
    authority_source_ids = (
        {str(value).strip() for value in configured_authority if str(value).strip()}
        if isinstance(configured_authority, list)
        else None
    )
    provider_event_hours = _safe_int(
        event_settings.get("provider_event_hours"),
        DEFAULT_PROVIDER_EVENT_HOURS,
        1,
        24,
    )

    reference_now = now or _utc_now_dt()
    event_candidates, schedule_stats = enrich_event_candidates(
        raw_event_candidates,
        fixture_path=fixture_path,
        timezone_name=timezone_name,
        now=reference_now,
        future_days=upcoming_future_days,
        authority_source_ids=authority_source_ids,
        provider_event_hours=provider_event_hours,
    )

    # Guide 30.7: the fixture exists first, then a matching stream is attached
    # to it. Without this an Upcoming card and the stream that could play it
    # stay in separate groups and the card publishes with nothing to play.
    event_candidates, attach_stats = attach_streams_to_fixtures(
        event_candidates,
        authority_source_ids or set(DEFAULT_FIXTURE_AUTHORITY_SOURCES),
    )
    schedule_stats.update(attach_stats)

    merged = merge_candidates(
        event_candidates,
        settings_path=settings_path,
        # Requirement 16: a healthy primary keeps its place across scans.
        previous_primary_keys=load_previous_primary_keys(),
    )

    # Guide 30.8: an event that moved from Upcoming to Today Match keeps the
    # card it already had rather than appearing as a new one.
    schedule_stats["reused_event_ids"] = reuse_published_event_ids(merged)

    now = reference_now
    skip_live_protection = False
    today_items: List[Dict[str, Any]] = []
    upcoming_items: List[Dict[str, Any]] = []
    today_stale = 0
    today_unplayable = 0
    upcoming_stale = 0

    for card in merged:
        if not isinstance(card, dict):
            continue

        card_copy = dict(card)
        card_copy["_source_timezone"] = source_timezone
        pipeline = _destination_for(card_copy)

        if pipeline == "today_match":
            if not _is_playable(card_copy):
                today_unplayable += 1
                continue
            if not _is_today_fresh(card_copy, now, today_max_age_hours):
                today_stale += 1
                continue
            card_copy["event_type"] = "today_match"
            card_copy["status"] = str(
                card_copy.get("schedule_status")
                or card_copy.get("status")
                or ("CHANNEL_LIVE" if card_copy.get("today_source_channel") else "LIVE_NOW")
            )
            today_items.append(card_copy)

        elif pipeline == "upcoming":
            if not _is_upcoming_fresh(
                card_copy,
                now,
                upcoming_past_grace_hours,
                upcoming_future_days,
            ):
                upcoming_stale += 1
                continue
            card_copy["event_type"] = "upcoming"
            card_copy["status"] = str(
                card_copy.get("schedule_status") or card_copy.get("status") or "UPCOMING"
            )
            upcoming_items.append(card_copy)

    # Requirement 4, corrected. A targeted trigger publishes the snapshot it
    # already had, with only its targeted fixtures refreshed.
    #
    # This has to start from what is published rather than from what this scan
    # produced. A targeted scan deliberately verifies a handful of candidates,
    # so its merged output only ever contains the targeted fixtures - filtering
    # that output would silently drop every other Upcoming card. Untargeted
    # fixtures are therefore copied through verbatim, still freshness-checked so
    # a finished match cannot linger, and a target that yielded nothing this time
    # keeps the card it already had.
    if targeted_window_minutes > 0:
        horizon = reference_now + timedelta(minutes=targeted_window_minutes)
        previous_upcoming_payload = _load_optional_json(Path("data") / "upcoming.json")
        previous_upcoming_items = [
            item
            for item in (previous_upcoming_payload.get("items") or [])
            if isinstance(item, dict)
        ]

        refreshed: Dict[str, Dict[str, Any]] = {}
        targeted = 0
        skipped_already_scanned = 0
        skipped_outside_window = 0
        for card in upcoming_items:
            start = _parse_datetime(card.get("start_time"), source_timezone)
            in_window = bool(start and reference_now <= start <= horizon)
            if not in_window:
                skipped_outside_window += 1
                continue
            if targeted_keys is not None and fixture_key(card) not in targeted_keys:
                skipped_already_scanned += 1
                continue
            targeted += 1
            refreshed[str(card.get("id") or "")] = card

        kept: List[Dict[str, Any]] = []
        seen_ids: set = set()
        for previous in previous_upcoming_items:
            event_id = str(previous.get("id") or "")
            seen_ids.add(event_id)
            fresh = refreshed.get(event_id)
            if fresh is not None:
                kept.append(fresh)
                continue
            carried = dict(previous)
            carried["_source_timezone"] = source_timezone
            if not _is_upcoming_fresh(
                carried, now, upcoming_past_grace_hours, upcoming_future_days
            ):
                upcoming_stale += 1
                continue
            kept.append(carried)
        for event_id, fresh in refreshed.items():
            if event_id not in seen_ids:
                kept.append(fresh)

        upcoming_items = kept
        schedule_stats["targeted_window_minutes"] = targeted_window_minutes
        schedule_stats["targeted_fixtures"] = targeted
        schedule_stats["targeted_skipped_already_scanned"] = skipped_already_scanned
        schedule_stats["targeted_skipped_outside_window"] = skipped_outside_window
        schedule_stats["targeted_carried_published_cards"] = len(
            [card for card in kept if str(card.get("id") or "") not in refreshed]
        )
        schedule_stats["targeted_keys_supplied"] = (
            -1 if targeted_keys is None else len(targeted_keys)
        )
        schedule_stats["targeted_resolved_now"] = sum(
            1 for card in refreshed.values() if has_valid_link(card)
        )

        # Today Match is not this trigger's business either. Its published cards
        # are copied through untouched - no re-verification and no liveness
        # probing, both of which belong to the Today Match scan - except that a
        # targeted fixture whose link arrived right at kickoff is allowed to
        # promote into it, because that promotion IS the work being done.
        previous_today_payload = _load_optional_json(Path("data") / "today-match.json")
        previous_today_published = [
            item
            for item in (previous_today_payload.get("items") or [])
            if isinstance(item, dict)
        ]
        promoted = {
            str(card.get("id") or ""): card
            for card in today_items
            if targeted_keys is None or fixture_key(card) in targeted_keys
        }
        kept_today: List[Dict[str, Any]] = []
        seen_today: set = set()
        for previous in previous_today_published:
            event_id = str(previous.get("id") or "")
            seen_today.add(event_id)
            kept_today.append(promoted.get(event_id) or dict(previous))
        for event_id, card in promoted.items():
            if event_id not in seen_today:
                kept_today.append(card)
        schedule_stats["targeted_promoted_to_today"] = len(
            [key for key in promoted if key not in seen_today]
        )
        today_items = kept_today
        skip_live_protection = True

    # Requirement 6, corrected. A live event that this scan simply failed to
    # fetch is carried forward with its previous card rather than deleted, for
    # as many consecutive scans as it takes. Only an authoritative ENDED/FT, or
    # a probe proving every link on the card is dead, actually retires it.
    if skip_live_protection:
        # A targeted trigger already published Today Match verbatim. Running
        # protection here would probe cards this trigger never scanned and
        # retire them on behalf of a scan that did not look for them.
        schedule_stats["live_protection"] = {"skipped": "targeted scan"}
    else:
        previous_today = _load_optional_json(Path("data") / "today-match.json")
        previous_today_items = [
            item for item in (previous_today.get("items") or []) if isinstance(item, dict)
        ]
        today_items, protection_stats = protect_live_events(
            today_items,
            previous_today_items,
            probe=probe_card_is_playable,
        )
        schedule_stats["live_protection"] = protection_stats

    result = {
        "today_match": _payload(
            today_items,
            "today_match",
            filtered_stale=today_stale,
            filtered_unplayable=today_unplayable,
            source_timezone=source_timezone,
        ),
        "upcoming": _payload(
            upcoming_items,
            "upcoming",
            filtered_stale=upcoming_stale,
            filtered_unplayable=0,
            source_timezone=source_timezone,
        ),
    }
    # Requirement 3. One row per configured source, with the exact stage a
    # contribution was lost at and why.
    try:
        coverage = build_source_coverage(
            configured_sources=[
                {"id": source_id}
                for source_id in sorted({
                    str(c.get("source_id") or "")
                    for c in raw_event_candidates
                    if str(c.get("source_id") or "")
                })
            ],
            raw_candidates=raw_event_candidates,
            parsed_candidates=raw_event_candidates,
            matched_candidates=event_candidates,
            published_items=today_items + upcoming_items,
        )
        write_source_coverage(coverage)
        result_coverage = coverage
    except Exception as error:  # pragma: no cover - reporting must never break a scan
        result_coverage = {"error": str(error)}

    result["source_coverage"] = result_coverage
    result["schedule"] = {
        "timezone": timezone_name,
        **schedule_stats,
    }
    return result


if __name__ == "__main__":
    result = process_events()
    print(
        "Events processed: "
        f"today={result['today_match']['count']}, "
        f"upcoming={result['upcoming']['count']}, "
        f"today_stale={result['today_match']['filtered_stale']}, "
        f"today_unplayable={result['today_match']['filtered_unplayable']}, "
        f"time_corrected={result['schedule']['corrected']}"
    )
