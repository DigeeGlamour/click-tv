"""Popular on Click TV - what people here actually watched.

This is NOT Trending. Trending (PART 06) is an external signal about what the
world is watching, and calling an internal list by that name would be a lie
told to every viewer who reads it. This row is only ever labelled with where
its numbers came from.

What counts is deliberately narrow. A click is not a view: `play_start` fires
the moment someone presses play, which makes it a measure of curiosity and of
mis-taps, and a row built from it rewards misleading posters. A QUALIFIED play
is thirty seconds of actual playback, counted once per session per title, and
that is the only thing that moves a title up this list.

The aggregate carries counts, never people: an item id, a day bucket and a
number. No session id, no address, no profile survives aggregation.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

try:
    from scanner import paths
except ImportError:  # pragma: no cover - direct-module import path
    import paths  # type: ignore

DEFAULT_STATE_PATH = paths.state_path("movie-analytics-aggregate.json")
DEFAULT_OUTPUT_PATH = os.path.join("data", "movies", "discovery", "popular-clicktv.json")

#: The events worth keeping. Everything else is ignored rather than stored,
#: so an unknown or spoofed event type cannot inflate anything.
EVENT_TYPES: Tuple[str, ...] = (
    "detail_open",
    "play_start",
    "play_30s",
    "play_complete",
    "watchlist_add",
    "search_result_open",
)

#: The one event that makes a play "qualified". Thirty seconds is long enough
#: that a mis-tap and a change of mind do not count, and short enough that a
#: viewer who genuinely started watching does.
QUALIFIED_EVENT = "play_30s"

#: How far back the published row looks.
POPULAR_WINDOW_DAYS = 7

#: How long a day bucket is kept in the aggregate before it is dropped.
AGGREGATE_RETENTION_DAYS = 60

#: Most rows the published file will carry.
POPULAR_LIMIT = 15

#: A title needs at least this many qualified plays to appear at all. One
#: person watching one film is not a popularity signal, and publishing it as
#: one would be both wrong and a privacy leak.
MINIMUM_QUALIFIED_PLAYS = 3

#: Fields that must never appear in a stored event, whatever a client sends.
FORBIDDEN_EVENT_FIELDS: Tuple[str, ...] = (
    "url",
    "stream_url",
    "backups",
    "headers",
    "authorization",
    "cookie",
    "token",
    "api_key",
    "ip",
    "user_agent",
    "referer",
)


def _now(now: Optional[_dt.datetime] = None) -> _dt.datetime:
    return now or _dt.datetime.now(_dt.timezone.utc)


def day_bucket(timestamp_ms: Any) -> str:
    """The UTC day an event belongs to. Day, not second: a timestamp to the
    millisecond is a fingerprint, and a day is all a popularity count needs."""
    try:
        value = float(timestamp_ms)
    except (TypeError, ValueError):
        return ""
    if value <= 0:
        return ""
    try:
        moment = _dt.datetime.fromtimestamp(value / 1000.0, tz=_dt.timezone.utc)
    except (OverflowError, OSError, ValueError):
        return ""
    return moment.date().isoformat()


def sanitize_event(raw: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """Reduce a reported event to the few fields worth keeping, or drop it.

    Anything not in this shape does not survive - including every field in
    FORBIDDEN_EVENT_FIELDS, which are dropped rather than trusted to be absent.
    """
    if not isinstance(raw, Mapping):
        return None
    event_type = str(raw.get("event_type") or "").strip().lower()
    if event_type not in EVENT_TYPES:
        return None
    item_id = str(raw.get("item_id") or "").strip()[:160]
    if not item_id:
        return None
    bucket = day_bucket(raw.get("ts"))
    if not bucket:
        return None

    content_type = str(raw.get("content_type") or "movie").strip().lower()
    if content_type not in ("movie", "series", "episode"):
        content_type = "movie"

    event: Dict[str, Any] = {
        "event_type": event_type,
        "item_id": item_id,
        "content_type": content_type,
        "day": bucket,
        # Kept only long enough to deduplicate within this aggregation run,
        # and never written to the aggregate.
        "session_id": str(raw.get("session_id") or "").strip()[:64],
    }
    if content_type in ("series", "episode"):
        series_id = str(raw.get("series_id") or "").strip()[:160]
        if series_id:
            event["series_id"] = series_id
        for field in ("season_number", "episode_number"):
            try:
                number = int(raw.get(field) or 0)
            except (TypeError, ValueError):
                number = 0
            if number > 0:
                event[field] = number
    return event


def aggregate_events(
    events: Iterable[Mapping[str, Any]],
    *,
    existing: Optional[Mapping[str, Any]] = None,
    now: Optional[_dt.datetime] = None,
) -> Dict[str, Any]:
    """Fold raw events into day-bucketed counts.

    One qualified play per session per title per day. A client that reports
    the same thirty-second mark forty times - a retry loop, a reload, a bot -
    moves the number by one.
    """
    reference = _now(now)
    store: Dict[str, Any] = {
        "version": 1,
        "updated_at": reference.isoformat(),
        "window_days": POPULAR_WINDOW_DAYS,
        "qualified_event": QUALIFIED_EVENT,
        "note": (
            "Counts only. No session id, address or profile is stored here. "
            "A qualified play is one session reaching "
            f"{QUALIFIED_EVENT} on a title on a given day."
        ),
        "days": {},
    }
    if isinstance(existing, Mapping) and isinstance(existing.get("days"), Mapping):
        for day, rows in existing["days"].items():
            if isinstance(rows, Mapping):
                store["days"][str(day)] = {
                    str(item): dict(counts)
                    for item, counts in rows.items()
                    if isinstance(counts, Mapping)
                }

    seen: set = set()
    for raw in events or ():
        event = sanitize_event(raw)
        if event is None:
            continue
        # Dedupe: the same session reporting the same event for the same item
        # on the same day counts once.
        fingerprint = (
            event["day"], event["item_id"], event["event_type"], event["session_id"]
        )
        if event["session_id"] and fingerprint in seen:
            continue
        seen.add(fingerprint)

        day_rows = store["days"].setdefault(event["day"], {})
        counts = day_rows.setdefault(
            event["item_id"], {"content_type": event["content_type"]}
        )
        counts[event["event_type"]] = int(counts.get(event["event_type"], 0)) + 1
        if event.get("series_id"):
            counts["series_id"] = event["series_id"]

    cutoff = (reference - _dt.timedelta(days=AGGREGATE_RETENTION_DAYS)).date().isoformat()
    for day in list(store["days"]):
        if day < cutoff:
            store["days"].pop(day, None)
    return store


def build_popular(
    store: Mapping[str, Any],
    *,
    catalogue: Optional[Mapping[str, Mapping[str, Any]]] = None,
    now: Optional[_dt.datetime] = None,
    limit: int = POPULAR_LIMIT,
    minimum: int = MINIMUM_QUALIFIED_PLAYS,
) -> Dict[str, Any]:
    """The published row. Real counts, active titles, or an empty list.

    `catalogue` maps item id -> published record. A title that is not in it -
    withdrawn, or never published - is not offered: a popularity row that
    sends people to something they cannot watch is worse than a shorter row.
    """
    reference = _now(now)
    first_day = (reference - _dt.timedelta(days=POPULAR_WINDOW_DAYS)).date().isoformat()

    totals: Dict[str, Dict[str, Any]] = {}
    days = store.get("days") if isinstance(store, Mapping) else None
    for day, rows in (days or {}).items():
        if str(day) < first_day or not isinstance(rows, Mapping):
            continue
        for item_id, counts in rows.items():
            if not isinstance(counts, Mapping):
                continue
            entry = totals.setdefault(
                str(item_id),
                {"qualified_plays": 0, "content_type": counts.get("content_type", "movie")},
            )
            entry["qualified_plays"] += int(counts.get(QUALIFIED_EVENT, 0) or 0)

    items: List[Dict[str, Any]] = []
    excluded_inactive = 0
    for item_id, entry in totals.items():
        plays = int(entry["qualified_plays"])
        if plays < minimum:
            continue
        record = (catalogue or {}).get(item_id)
        if catalogue is not None and not record:
            excluded_inactive += 1
            continue
        row: Dict[str, Any] = {
            "id": item_id,
            "qualified_plays": plays,
            "content_type": entry.get("content_type", "movie"),
        }
        if record:
            for field in ("name", "category", "year", "logo"):
                value = record.get(field)
                if value not in (None, "", [], {}):
                    row[field] = value
        items.append(row)

    items.sort(key=lambda row: (-row["qualified_plays"], str(row["id"])))
    return {
        "version": 1,
        "updated_at": reference.isoformat(),
        # The label travels with the data so no UI can rename it by accident.
        "label": "Popular on Click TV",
        "signal": "internal_qualified_plays",
        "signal_note": (
            "Counted from playback on Click TV itself. This is NOT an "
            "international trending list and must never be presented as one."
        ),
        "window_days": POPULAR_WINDOW_DAYS,
        "minimum_qualified_plays": minimum,
        "count": len(items[:limit]),
        "excluded_inactive": excluded_inactive,
        "items": items[:limit],
    }


def load_aggregate(path: Optional[str] = None) -> Dict[str, Any]:
    target = path or DEFAULT_STATE_PATH
    try:
        with open(target, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return {"version": 1, "days": {}}
    if not isinstance(payload, dict) or not isinstance(payload.get("days"), dict):
        return {"version": 1, "days": {}}
    return payload


def save_aggregate(store: Mapping[str, Any], path: Optional[str] = None) -> bool:
    target = path or DEFAULT_STATE_PATH
    return _atomic_write(target, store)


def write_popular(document: Mapping[str, Any], path: Optional[str] = None) -> bool:
    """Write the published row - but never replace a good file with an empty one.

    An analytics outage must not blank a row that was correct yesterday.
    """
    target = path or DEFAULT_OUTPUT_PATH
    if not document.get("items"):
        try:
            with open(target, "r", encoding="utf-8") as handle:
                previous = json.load(handle)
            if isinstance(previous, dict) and previous.get("items"):
                return False
        except (OSError, ValueError):
            pass
    return _atomic_write(target, document)


def _atomic_write(target: str, payload: Mapping[str, Any]) -> bool:
    directory = os.path.dirname(target)
    temporary = f"{target}.{os.getpid()}.tmp"
    try:
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        return True
    except OSError:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        return False


__all__ = [
    "EVENT_TYPES",
    "QUALIFIED_EVENT",
    "POPULAR_WINDOW_DAYS",
    "POPULAR_LIMIT",
    "MINIMUM_QUALIFIED_PLAYS",
    "FORBIDDEN_EVENT_FIELDS",
    "DEFAULT_STATE_PATH",
    "DEFAULT_OUTPUT_PATH",
    "day_bucket",
    "sanitize_event",
    "aggregate_events",
    "build_popular",
    "load_aggregate",
    "save_aggregate",
    "write_popular",
]
