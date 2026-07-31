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

try:
    from scanner.merger import merge_candidates
except ImportError:
    module_dir = str(Path(__file__).resolve().parent)
    if module_dir not in sys.path:
        sys.path.insert(0, module_dir)
    from merger import merge_candidates


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


def _parse_datetime(value: Any) -> Optional[datetime]:
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
            "%d-%m-%Y %H:%M:%S",
            "%d-%m-%Y %H:%M",
        ):
            try:
                parsed = datetime.strptime(text, pattern)
                break
            except ValueError:
                continue

    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _sort_time(value: Any) -> str:
    parsed = _parse_datetime(value)
    if parsed is None:
        return "9999-12-31T23:59:59+00:00"
    return parsed.isoformat()


def _event_sort_key(item: Dict[str, Any]) -> Tuple[str, str, str]:
    start_time = _sort_time(item.get("start_time"))
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
    return start_time, competition, name


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
    return bool(_primary_url(item) or _backup_urls(item))


def _is_today_fresh(
    item: Dict[str, Any],
    now: datetime,
    max_age_hours: int,
) -> bool:
    end_time = _parse_datetime(item.get("end_time"))
    if end_time is not None and end_time < now - timedelta(hours=1):
        return False

    start_time = _parse_datetime(item.get("start_time"))
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
    start_time = _parse_datetime(item.get("start_time"))
    if start_time is None:
        return True
    if start_time < now - timedelta(hours=past_grace_hours):
        return False
    if start_time > now + timedelta(days=future_days):
        return False
    return True


def _payload(
    items: List[Dict[str, Any]],
    event_type: str,
    filtered_stale: int,
    filtered_unplayable: int,
) -> Dict[str, Any]:
    ordered = sorted(
        [item for item in items if isinstance(item, dict)],
        key=_event_sort_key,
    )
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
) -> Dict[str, Dict[str, Any]]:
    """Return freshness-checked today_match and upcoming payloads."""
    results = _load_required_results(bd_results_path)
    settings = _load_optional_json(settings_path)
    event_settings = settings.get("events") if isinstance(settings.get("events"), dict) else {}

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

    event_candidates = [
        dict(item)
        for item in results
        if str(item.get("source_pipeline") or "").strip().lower()
        in {"today_match", "upcoming"}
    ]

    merged = merge_candidates(
        event_candidates,
        settings_path=settings_path,
    )

    now = _utc_now_dt()
    today_items: List[Dict[str, Any]] = []
    upcoming_items: List[Dict[str, Any]] = []
    today_stale = 0
    today_unplayable = 0
    upcoming_stale = 0

    for card in merged:
        if not isinstance(card, dict):
            continue

        pipeline = str(card.get("source_pipeline") or "").strip().lower()
        card_copy = dict(card)

        if pipeline == "today_match":
            if not _is_playable(card_copy):
                today_unplayable += 1
                continue
            if not _is_today_fresh(card_copy, now, today_max_age_hours):
                today_stale += 1
                continue
            card_copy["event_type"] = "today_match"
            card_copy["status"] = "LIVE"
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
            card_copy["status"] = "UPCOMING"
            upcoming_items.append(card_copy)

    return {
        "today_match": _payload(
            today_items,
            "today_match",
            filtered_stale=today_stale,
            filtered_unplayable=today_unplayable,
        ),
        "upcoming": _payload(
            upcoming_items,
            "upcoming",
            filtered_stale=upcoming_stale,
            filtered_unplayable=0,
        ),
    }


if __name__ == "__main__":
    result = process_events()
    print(
        "Events processed: "
        f"today={result['today_match']['count']}, "
        f"upcoming={result['upcoming']['count']}, "
        f"today_stale={result['today_match']['filtered_stale']}, "
        f"today_unplayable={result['today_match']['filtered_unplayable']}"
    )
