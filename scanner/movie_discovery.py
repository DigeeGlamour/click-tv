"""Discovery outputs: Just Added, Latest, Home (PARTs 07-09).

Three small static JSON files under data/movies/discovery/, built from the
catalogue this scan is about to publish. Together with trending.json
(scanner/movie_trending.py) they are everything the movie home needs, so
opening it costs a few kilobytes instead of the whole catalogue.

The two rows are answering genuinely different questions, and keeping them
apart is the point of having both:

  JUST ADDED - when did this arrive on Click TV?   -> first_seen_at
  LATEST     - when was this released?             -> release_date

A 2010 film added today is Just Added and is not Latest. A film released
last week that arrived here last week is both. Nothing about servers,
popularity or source order enters either list.

CARD SUMMARIES CARRY NO PLAYBACK DATA. No url, no backups, no headers, no
playback_id - a discovery row is a poster and a title, and duplicating
stream configuration into it would both bloat the payload and scatter the
one thing that must stay in a single place. A click resolves the real
record from the existing category pages and hands off to the existing
player entry point, untouched.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import time
from typing import Any, Dict, Iterable, List, Optional

DISCOVERY_ROOT = os.path.join("data", "movies", "discovery")
JUST_ADDED_PATH = os.path.join(DISCOVERY_ROOT, "just-added.json")

#: Rolling window for "Just Added", per the plan's suggested default.
JUST_ADDED_WINDOW_DAYS = 14

#: Rows are a shelf, not a catalogue. The category pages remain the place
#: to browse everything.
JUST_ADDED_LIMIT = 40

#: Everything a discovery card needs to render, and nothing that belongs to
#: playback. `poster` is sourced from the catalogue's existing `logo`.
CARD_FIELDS = (
    "id",
    "name",
    "category",
    "year",
    "release_date",
    "poster",
    "backdrop",
    "rating",
    "rating_source",
    "genres",
)

#: Fields that must never reach a discovery file. Asserted by tests.
FORBIDDEN_CARD_FIELDS = (
    "url",
    "stream_url",
    "link",
    "links",
    "backups",
    "standby",
    "headers",
    "request_headers",
    "cookie",
    "authorization",
    "user_agent",
    "drm",
    "playback_id",
    "proxy_mode",
    "header_profile",
)


def _now(now: Optional[_dt.datetime] = None) -> _dt.datetime:
    return now or _dt.datetime.now(_dt.timezone.utc)


def parse_stamp(value: Any) -> Optional[_dt.datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = _dt.datetime.fromisoformat(
            text[:-1] + "+00:00" if text.endswith("Z") else text
        )
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    return parsed


def atomic_write_json(file_path: str, payload: Dict[str, Any]) -> bool:
    try:
        target_dir = os.path.dirname(file_path) or "."
        os.makedirs(target_dir, exist_ok=True)
        temp_path = os.path.join(
            target_dir,
            f".{os.path.basename(file_path)}.{os.getpid()}.{time.time_ns()}.tmp",
        )
        try:
            with open(temp_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, file_path)
        finally:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
    except OSError:
        return False
    return True


def load_json(file_path: str) -> Dict[str, Any]:
    try:
        with open(file_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def iter_published_movies(paginated: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    """Every movie record this scan is publishing, in published order."""
    if not isinstance(paginated, dict):
        return
    for category_payload in paginated.values():
        if not isinstance(category_payload, dict):
            continue
        pages = category_payload.get("page_contents")
        if not isinstance(pages, dict):
            continue
        for page_name in sorted(pages):
            page = pages.get(page_name)
            if not isinstance(page, dict):
                continue
            items = page.get("items") or page.get("movies") or []
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict):
                        yield item


def card_summary(movie: Dict[str, Any], extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """A movie record reduced to what a discovery card renders.

    Empty fields are dropped rather than serialised as null: the row should
    say what is known, and an absent rating is not a rating of nothing.
    """
    card: Dict[str, Any] = {}
    for field in CARD_FIELDS:
        value = movie.get("logo") if field == "poster" else movie.get(field)
        if value in (None, "", [], {}):
            continue
        card[field] = value
    for key, value in (extra or {}).items():
        if value not in (None, "", [], {}):
            card[key] = value
    return card


# --------------------------------------------------------------------------
# PART 07 - Just Added
# --------------------------------------------------------------------------


def build_just_added(
    paginated: Dict[str, Any],
    *,
    now: Optional[_dt.datetime] = None,
    window_days: int = JUST_ADDED_WINDOW_DAYS,
    limit: int = JUST_ADDED_LIMIT,
) -> Dict[str, Any]:
    """Films that arrived on Click TV inside the rolling window.

    Purely internal: first_seen_at is our own observation, so this list
    needs no external API and keeps working with every provider down.
    """
    reference = _now(now)
    cutoff = reference - _dt.timedelta(days=window_days)

    dated: List[Any] = []
    seen_ids = set()
    for movie in iter_published_movies(paginated):
        movie_id = str(movie.get("id") or "").strip()
        if not movie_id or movie_id in seen_ids:
            continue
        first_seen = parse_stamp(movie.get("first_seen_at"))
        if first_seen is None or first_seen < cutoff:
            continue
        # A future stamp is a clock problem, not an arrival; it must not be
        # allowed to pin itself to the top of the row forever.
        if first_seen > reference:
            continue
        seen_ids.add(movie_id)
        dated.append((first_seen, movie))

    dated.sort(key=lambda pair: pair[0], reverse=True)
    items = [
        card_summary(movie, {"first_seen_at": movie.get("first_seen_at")})
        for _, movie in dated[:limit]
    ]

    return {
        "version": 1,
        "updated_at": reference.isoformat(),
        "window_days": window_days,
        "signal": "first_seen_at",
        "count": len(items),
        "items": items,
    }


def generate_just_added(
    paginated: Dict[str, Any],
    *,
    output_path: Optional[str] = None,
    now: Optional[_dt.datetime] = None,
) -> Dict[str, Any]:
    document = build_just_added(paginated, now=now)
    atomic_write_json(output_path or JUST_ADDED_PATH, document)
    return {"count": document["count"], "window_days": document["window_days"]}
