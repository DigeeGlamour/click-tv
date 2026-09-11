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
LATEST_PATH = os.path.join(DISCOVERY_ROOT, "latest.json")
TRENDING_PATH = os.path.join(DISCOVERY_ROOT, "trending.json")
HOME_PATH = os.path.join(DISCOVERY_ROOT, "home.json")
SEARCH_INDEX_PATH = os.path.join("data", "movies", "search-index.json")

#: Items per row on the home page. The plan asks for a sensible 10-15 cap:
#: enough to fill a shelf, nowhere near enough to be a catalogue.
HOME_ROW_LIMIT = 15

#: Rolling window for "Just Added", per the plan's suggested default.
JUST_ADDED_WINDOW_DAYS = 14

#: Rows are a shelf, not a catalogue. The category pages remain the place
#: to browse everything.
JUST_ADDED_LIMIT = 40
LATEST_LIMIT = 40

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


# --------------------------------------------------------------------------
# PART 08 - Latest
# --------------------------------------------------------------------------

#: What happens to a film whose exact release date is unknown. Stated in
#: the output itself, not just here, because "why is this film missing from
#: Latest" is a question the data should be able to answer for itself.
MISSING_DATE_POLICY = "excluded"


def normalize_release_date(value: Any) -> str:
    """A real calendar date as YYYY-MM-DD, or "" - never a guess.

    A bare year is deliberately NOT accepted. "2010" is not a release date,
    and turning it into 2010-01-01 would invent a day and a month the
    provider never gave, which is precisely the fabrication the plan
    forbids. Such films are handled by the missing-date policy instead.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    candidate = text[:10]
    if len(candidate) != 10 or candidate[4] != "-" or candidate[7] != "-":
        return ""
    try:
        parsed = _dt.date.fromisoformat(candidate)
    except ValueError:
        return ""
    return parsed.isoformat()


def build_latest(
    paginated: Dict[str, Any],
    *,
    now: Optional[_dt.datetime] = None,
    limit: int = LATEST_LIMIT,
) -> Dict[str, Any]:
    """The catalogue by actual release date, newest first.

    The ONLY ranking signal is release_date. first_seen_at,
    available_link_count, trend rank and source order are all deliberately
    absent: a film released in 2015 and added here yesterday is Just Added
    and is not Latest, and conflating the two is the mistake this row
    exists to avoid.
    """
    reference = _now(now)
    today = reference.date()

    dated: List[Any] = []
    seen_ids = set()
    no_exact_date = 0
    future_release = 0

    for movie in iter_published_movies(paginated):
        movie_id = str(movie.get("id") or "").strip()
        if not movie_id or movie_id in seen_ids:
            continue
        seen_ids.add(movie_id)

        released = normalize_release_date(movie.get("release_date"))
        if not released:
            no_exact_date += 1
            continue
        if _dt.date.fromisoformat(released) > today:
            # Announced but not out yet. A release date in the future is
            # real data, it just does not belong in "Latest releases".
            future_release += 1
            continue
        dated.append((released, movie_id, movie))

    # Sorted on the release date alone. The id is only a deterministic
    # tie-break between two films released the same day - neutral by
    # design, so nothing about popularity or servers can creep in.
    dated.sort(key=lambda row: (row[0], row[1]), reverse=True)

    items = [
        card_summary(movie, {"release_date": released})
        for released, _, movie in dated[:limit]
    ]

    return {
        "version": 1,
        "updated_at": reference.isoformat(),
        "signal": "release_date",
        "missing_date_policy": MISSING_DATE_POLICY,
        "count": len(items),
        "eligible": len(dated),
        "excluded": {
            "no_exact_release_date": no_exact_date,
            "future_release": future_release,
        },
        "items": items,
    }


def generate_latest(
    paginated: Dict[str, Any],
    *,
    output_path: Optional[str] = None,
    now: Optional[_dt.datetime] = None,
) -> Dict[str, Any]:
    document = build_latest(paginated, now=now)
    atomic_write_json(output_path or LATEST_PATH, document)
    return {
        "count": document["count"],
        "eligible": document["eligible"],
        "excluded": document["excluded"],
    }


# --------------------------------------------------------------------------
# PART 09 - Discovery home
# --------------------------------------------------------------------------


def _catalog_by_id(paginated: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    index: Dict[str, Dict[str, Any]] = {}
    for movie in iter_published_movies(paginated):
        movie_id = str(movie.get("id") or "").strip()
        if movie_id:
            index.setdefault(movie_id, movie)
    return index


def _trending_row(
    trending_document: Dict[str, Any],
    catalog: Dict[str, Dict[str, Any]],
    limit: int,
) -> List[Dict[str, Any]]:
    """Trending ids, resolved to cards from the catalogue we just published.

    trending.json itself stays ids-and-ranks; the poster and genres it does
    not carry are looked up here rather than duplicated there.
    """
    row: List[Dict[str, Any]] = []
    for entry in (trending_document.get("movies") or [])[:limit]:
        if not isinstance(entry, dict):
            continue
        movie = catalog.get(str(entry.get("id") or "").strip())
        if movie is None:
            continue
        row.append(
            card_summary(
                movie,
                {
                    "trending_rank": entry.get("trending_rank"),
                    "trend_sources": entry.get("trend_sources"),
                },
            )
        )
    return row


def _carried_featured(
    existing_home: Dict[str, Any], catalog: Dict[str, Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Whatever a real Featured source last put here, if anything.

    The Featured/Hero system is its own plan and is NOT implemented yet, so
    nothing is invented to fill this row. Not the top trending title, not
    the film with the most servers, not a random pick - each of those would
    be a fabricated editorial choice presented as a real one.

    What this does instead is keep the row last-good compatible: entries a
    future Featured builder writes into home.json survive the next scan,
    minus any whose film is no longer in the catalogue (a Featured slot
    pointing at something unplayable is worse than an empty shelf).
    """
    featured = existing_home.get("featured")
    if not isinstance(featured, list):
        return []
    carried: List[Dict[str, Any]] = []
    for entry in featured:
        if not isinstance(entry, dict):
            continue
        movie = catalog.get(str(entry.get("id") or "").strip())
        if movie is None:
            continue
        carried.append(entry)
    return carried


def build_home(
    paginated: Dict[str, Any],
    *,
    trending_document: Optional[Dict[str, Any]] = None,
    existing_home: Optional[Dict[str, Any]] = None,
    now: Optional[_dt.datetime] = None,
    limit: int = HOME_ROW_LIMIT,
) -> Dict[str, Any]:
    """The whole movie home in one small file.

    Card summaries only. Opening the home costs this file, not the
    catalogue; a click resolves the real record from the existing category
    pages and hands off to the existing player entry point unchanged.
    """
    reference = _now(now)
    catalog = _catalog_by_id(paginated)

    just_added = build_just_added(paginated, now=reference, limit=limit)
    latest = build_latest(paginated, now=reference, limit=limit)
    trending = _trending_row(trending_document or {}, catalog, limit)
    featured = _carried_featured(existing_home or {}, catalog)

    return {
        "version": 1,
        "updated_at": reference.isoformat(),
        "row_limit": limit,
        "featured": featured,
        # Says plainly that an empty Featured row is a system that has not
        # been built yet, not a system that failed.
        "featured_status": "carried_last_good" if featured else "awaiting_featured_system",
        "trending": trending,
        "just_added": just_added["items"],
        "latest": latest["items"],
        "counts": {
            "featured": len(featured),
            "trending": len(trending),
            "just_added": len(just_added["items"]),
            "latest": len(latest["items"]),
        },
    }


# --------------------------------------------------------------------------
# PART 12/13 - browse + search index
# --------------------------------------------------------------------------

#: One small index covering the whole catalogue, so "every Action film" and
#: "every film matching this text" are both answerable without loading a
#: single category page. Card-shaped, id-keyed, and carrying no playback
#: data whatsoever.
SEARCH_INDEX_FIELDS = (
    "id",
    "name",
    "category",
    "year",
    "poster",
    "genres",
    "rating",
    "rating_source",
    "first_seen_at",
)


SERIES_ROOT = os.path.join("data", "series")


def _index_entry(record: Dict[str, Any], kind: str) -> Dict[str, Any]:
    entry: Dict[str, Any] = {"type": kind}
    for field in SEARCH_INDEX_FIELDS:
        value = record.get("logo") if field == "poster" else record.get(field)
        if value in (None, "", [], {}):
            continue
        entry[field] = value
    # Only real alternate titles are indexed. Nothing is transliterated or
    # invented here: an alias the source never gave is an alias that will
    # match the wrong film.
    for optional in ("original_title", "search_aliases"):
        value = record.get(optional)
        if value not in (None, "", [], {}):
            entry["aliases" if optional == "search_aliases" else optional] = value
    return entry


def iter_published_series(root: str = SERIES_ROOT) -> Iterable[Dict[str, Any]]:
    """Series cards from the published series indexes. Read-only."""
    if not os.path.isdir(root):
        return
    for name in sorted(os.listdir(root)):
        index_path = os.path.join(root, name, "index.json")
        if not os.path.isfile(index_path):
            continue
        payload = load_json(index_path)
        items = payload.get("items")
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict):
                yield item


def build_search_index(
    paginated: Dict[str, Any],
    *,
    now: Optional[_dt.datetime] = None,
    series_root: str = SERIES_ROOT,
) -> Dict[str, Any]:
    """Every published title reduced to what browsing and search need.

    Deliberately NOT the catalogue: no url, no backups, no headers, no
    playback_id. Clicking a result resolves the real record from the
    category pages, which is where playback configuration stays.

    Series are indexed alongside films and carry type "series", so a series
    result can never be rendered as a film - the label is in the data, not
    inferred by the UI.
    """
    reference = _now(now)
    items: List[Dict[str, Any]] = []
    seen_ids = set()

    for movie in iter_published_movies(paginated):
        movie_id = str(movie.get("id") or "").strip()
        if not movie_id or movie_id in seen_ids:
            continue
        seen_ids.add(movie_id)
        items.append(_index_entry(movie, "movie"))

    series_count = 0
    for series in iter_published_series(series_root):
        series_id = str(series.get("id") or "").strip()
        if not series_id or series_id in seen_ids:
            continue
        seen_ids.add(series_id)
        items.append(_index_entry(series, "series"))
        series_count += 1

    return {
        "version": 1,
        "updated_at": reference.isoformat(),
        "count": len(items),
        "movies": len(items) - series_count,
        "series": series_count,
        "items": items,
    }


def generate_search_index(
    paginated: Dict[str, Any],
    *,
    output_path: Optional[str] = None,
    now: Optional[_dt.datetime] = None,
    series_root: str = SERIES_ROOT,
) -> Dict[str, Any]:
    target = output_path or SEARCH_INDEX_PATH
    document = build_search_index(paginated, now=now, series_root=series_root)

    if not document["items"]:
        # An empty index would make every genre and every search look empty.
        # If the catalogue really is empty that is the scan's problem to
        # report, not this file's to record.
        existing = load_json(target)
        if existing.get("items"):
            return {"count": 0, "preserved": True}

    atomic_write_json(target, document)
    return {"count": document["count"], "preserved": False}


def generate_home(
    paginated: Dict[str, Any],
    *,
    output_path: Optional[str] = None,
    trending_path: Optional[str] = None,
    now: Optional[_dt.datetime] = None,
) -> Dict[str, Any]:
    target = output_path or HOME_PATH
    document = build_home(
        paginated,
        trending_document=load_json(trending_path or TRENDING_PATH),
        existing_home=load_json(target),
        now=now,
    )
    atomic_write_json(target, document)
    return {"counts": document["counts"], "featured_status": document["featured_status"]}
