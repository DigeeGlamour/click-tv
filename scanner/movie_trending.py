"""Real international trending, matched to what Click TV can actually play (PART 06).

The thing this replaces is the assumption that `available_link_count` means
popularity. It does not: it counts how many verified sources were found. A
2018 film with five working servers is not more trending than this week's
international hit with one, and saying so on the site would be a claim the
data never supported.

So trending is built the other way round:

  1. Fetch a real external trend signal - TMDB /trending/movie/week and
     /trending/tv/week, fetched SEPARATELY because a film and a series are
     not ranked against each other.
  2. Keep only the titles Click TV can actually play. An external hit with
     no stream here is not shown at all - a trending row that cannot be
     clicked is worse than a shorter row.
  3. Preserve the external rank. Our order is TMDB's order; that is the
     whole point of calling it international trending.

`available_link_count` survives only as a reliability tie-breaker worth
hundredths of a point - it can never reorder two titles that TMDB ranked
differently, and there is a test that holds that line.

WHAT IS DELIBERATELY NOT A SOURCE HERE:
  - MoviesDatabase popularity. The plan allows it as a secondary signal
    only once its endpoint and schema are verified. Verified on 2026-09-11:
    `GET /titles?list=most_pop_movies` answers HTTP 200 with
    `{"entries": 0, "results": []}` - an empty list, not a popularity
    ranking. It therefore stays disabled rather than being wired up to
    something that returns nothing. MOVIESDATABASE_POPULARITY_VERIFIED
    below is the single switch to flip if that ever changes.
  - AniList, except for anime, which has no catalogue here yet.
  - Click TV's own view counts. There are none collected yet, and inventing
    them to pad a row would be exactly the fake signal this PART exists to
    remove. The output schema leaves room for them (PART 22's job).

FAILURE BEHAVIOUR: TMDB being unreachable never empties trending.json. The
last good snapshot is kept, its age is recorded, and once it is older than
a week the international claim is dropped rather than dressed up as fresh.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import time
import urllib.parse
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

try:
    from scanner import paths
    from scanner import provider_health
    from scanner.merger import movie_identity_key
except ImportError:  # pragma: no cover - direct-module import path
    import paths  # type: ignore
    import provider_health  # type: ignore
    from merger import movie_identity_key  # type: ignore

DEFAULT_STATE_PATH = paths.state_path("movie-trending-cache.json")
DEFAULT_OUTPUT_PATH = os.path.join("data", "movies", "discovery", "trending.json")

TMDB_TRENDING_MOVIE_URL = "https://api.themoviedb.org/3/trending/movie/week"
TMDB_TRENDING_TV_URL = "https://api.themoviedb.org/3/trending/tv/week"

#: 0-72h: a normal trending claim. 3-7 days: still usable, flagged stale.
#: Past a week: keep a short last-known list, drop the "international" claim.
FRESH_HOURS = 72
USABLE_DAYS = 7
EXPIRED_LIST_LIMIT = 10

#: See the module docstring - verified empty on 2026-09-11, so disabled.
MOVIESDATABASE_POPULARITY_VERIFIED = False


def _now(now: Optional[_dt.datetime] = None) -> _dt.datetime:
    return now or _dt.datetime.now(_dt.timezone.utc)


def _parse_stamp(value: Any) -> Optional[_dt.datetime]:
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


def _atomic_write_json(file_path: str, payload: Dict[str, Any]) -> bool:
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


def _load_json(file_path: str) -> Dict[str, Any]:
    try:
        with open(file_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


# --------------------------------------------------------------------------
# Last-good snapshot state
# --------------------------------------------------------------------------


def load_state(path: Optional[str] = None) -> Dict[str, Any]:
    """Missing/corrupt state -> empty, never raises."""
    payload = _load_json(path or DEFAULT_STATE_PATH)
    if not payload:
        return {"version": 1, "snapshot": {}}
    payload.setdefault("version", 1)
    if not isinstance(payload.get("snapshot"), dict):
        payload["snapshot"] = {}
    return payload


def save_state(state: Dict[str, Any], path: Optional[str] = None) -> bool:
    return _atomic_write_json(path or DEFAULT_STATE_PATH, state)


# --------------------------------------------------------------------------
# External signal
# --------------------------------------------------------------------------


def _tmdb_auth(params: Dict[str, str]) -> Optional[Dict[str, str]]:
    token = os.getenv("TMDB_API_TOKEN", "").strip()
    api_key = os.getenv("TMDB_API_KEY", "").strip()
    if not token and not api_key:
        return None
    headers: Dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    elif api_key:
        params["api_key"] = api_key
    return headers


def _fetch_trending_page(url: str, kind: str) -> List[Dict[str, Any]]:
    """One TMDB trending window -> ranked entries. [] on any failure."""
    params: Dict[str, str] = {"language": "en-US", "page": "1"}
    headers = _tmdb_auth(params)
    if headers is None:
        return []
    payload = provider_health.request_json(
        "tmdb", url + "?" + urllib.parse.urlencode(params), headers=headers
    )
    results = payload.get("results") if isinstance(payload.get("results"), list) else []
    entries: List[Dict[str, Any]] = []
    for position, item in enumerate(results, start=1):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("name") or "").strip()
        if not title:
            continue
        released = str(item.get("release_date") or item.get("first_air_date") or "").strip()
        entries.append(
            {
                "tmdb_id": item.get("id"),
                "title": title,
                "year": released[:4],
                "kind": kind,
                # The rank IS the signal. It is carried through untouched.
                "external_rank": position,
            }
        )
    return entries


def fetch_external(now: Optional[_dt.datetime] = None) -> Optional[Dict[str, Any]]:
    """Fetch both trending windows separately. None if neither answered."""
    movies = _fetch_trending_page(TMDB_TRENDING_MOVIE_URL, "movie")
    tv = _fetch_trending_page(TMDB_TRENDING_TV_URL, "tv")
    if not movies and not tv:
        return None
    return {
        "fetched_at": _now(now).isoformat(),
        "sources": ["tmdb"],
        "movies": movies,
        "tv": tv,
    }


# --------------------------------------------------------------------------
# Matching against our own playable catalogue
# --------------------------------------------------------------------------


def identity_keys(item: Dict[str, Any]) -> Set[str]:
    """Every identity an item could be matched on.

    Built from the same merger identity the rest of the movie system uses,
    by asking it one field at a time, so trending can never drift into its
    own private notion of "the same film".
    """
    keys: Set[str] = set()
    if not isinstance(item, dict):
        return keys
    tmdb_id = item.get("tmdb_id")
    if tmdb_id not in (None, ""):
        keys.add(movie_identity_key({"tmdb_id": tmdb_id}))
    imdb_id = item.get("imdb_id")
    if imdb_id not in (None, ""):
        keys.add(movie_identity_key({"imdb_id": imdb_id}))
    name = item.get("name") or item.get("title")
    year = item.get("year")
    if name:
        title_key = movie_identity_key({"name": name, "year": year})
        if title_key.startswith("title:") and not title_key.endswith(":"):
            # Title with no year is not matched on: two films share a title
            # far more often than a remake trends in the same week, and a
            # wrong match is worse than a missing row.
            keys.add(title_key)
    return keys


def _is_withdrawn(movie: Dict[str, Any]) -> bool:
    """PART 18. A title the lifecycle has marked inactive is not offered.

    Shares scanner.movie_discovery's predicate so the rule lives in one place;
    falls back to reading the flag directly if that import is unavailable.
    """
    try:
        from scanner.movie_discovery import is_withdrawn

        return is_withdrawn(movie)
    except ImportError:  # pragma: no cover - direct-module import path
        return movie.get("is_active") is False


def iter_published_movies(paginated: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    """Published movies, minus anything the lifecycle has withdrawn."""
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
                    if isinstance(item, dict) and not _is_withdrawn(item):
                        yield item


def build_catalog_index(paginated: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """identity key -> the published movie carrying it."""
    index: Dict[str, Dict[str, Any]] = {}
    for movie in iter_published_movies(paginated):
        for key in identity_keys(movie):
            index.setdefault(key, movie)
    return index


def _reliability_bonus(movie: Dict[str, Any]) -> float:
    """available_link_count, as a tie-breaker and nothing more.

    Capped at 0.05 while one rank step is worth at least 1.0, so more
    servers can never move a title past one TMDB ranked above it. That
    ceiling is the entire reason this function exists rather than the count
    being added to the score directly.
    """
    try:
        count = int(movie.get("available_link_count") or 0)
    except (TypeError, ValueError):
        count = 0
    return min(max(count, 0), 5) * 0.01


def _rank_score(external_rank: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round(100.0 * (total - external_rank + 1) / total, 2)


def match_entries(
    entries: List[Dict[str, Any]], catalog_index: Dict[str, Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """External entries that Click TV can actually play, in external order."""
    total = len(entries)
    matched: List[Dict[str, Any]] = []
    seen_ids: Set[str] = set()

    for entry in entries:
        movie = None
        for key in identity_keys(entry):
            movie = catalog_index.get(key)
            if movie is not None:
                break
        if movie is None:
            continue
        movie_id = str(movie.get("id") or "").strip()
        if not movie_id or movie_id in seen_ids:
            continue
        seen_ids.add(movie_id)
        matched.append(
            {
                "id": movie_id,
                "name": movie.get("name") or entry.get("title"),
                "tmdb_id": entry.get("tmdb_id"),
                "is_trending": True,
                "external_rank": entry["external_rank"],
                "trend_score": round(
                    _rank_score(entry["external_rank"], total) + _reliability_bonus(movie), 2
                ),
                "trend_sources": ["tmdb"],
            }
        )

    # Our order is TMDB's order. Ranks are renumbered 1..n only because the
    # unplayable titles between them are gone, never re-sorted.
    for position, item in enumerate(matched, start=1):
        item["trending_rank"] = position
    return matched


# --------------------------------------------------------------------------
# Freshness
# --------------------------------------------------------------------------


def freshness_for(snapshot: Dict[str, Any], now: Optional[_dt.datetime] = None) -> Tuple[str, float]:
    """('fresh'|'stale'|'expired'|'unknown', age_hours)."""
    fetched = _parse_stamp(snapshot.get("fetched_at"))
    if fetched is None:
        return "unknown", 0.0
    age_hours = max((_now(now) - fetched).total_seconds() / 3600.0, 0.0)
    if age_hours <= FRESH_HOURS:
        return "fresh", age_hours
    if age_hours <= USABLE_DAYS * 24:
        return "stale", age_hours
    return "expired", age_hours


# --------------------------------------------------------------------------
# Document
# --------------------------------------------------------------------------


def build_document(
    snapshot: Dict[str, Any],
    catalog_index: Dict[str, Dict[str, Any]],
    *,
    now: Optional[_dt.datetime] = None,
) -> Dict[str, Any]:
    state, age_hours = freshness_for(snapshot, now)
    movies = match_entries(snapshot.get("movies") or [], catalog_index)
    tv = match_entries(snapshot.get("tv") or [], catalog_index)

    if state == "expired":
        # A week-old snapshot is not "what is trending internationally".
        # Keep a short last-known list and say so, rather than dressing
        # stale data up as current.
        movies = movies[:EXPIRED_LIST_LIMIT]
        tv = tv[:EXPIRED_LIST_LIMIT]

    return {
        "version": 1,
        "updated_at": _now(now).isoformat(),
        "trend_updated_at": snapshot.get("fetched_at"),
        "trend_sources": snapshot.get("sources") or ["tmdb"],
        "trend_external_fresh": state == "fresh",
        "freshness": state,
        "snapshot_age_hours": round(age_hours, 2),
        "international_claim": state in ("fresh", "stale"),
        "movies": movies,
        "tv": tv,
    }


def generate(
    paginated: Dict[str, Any],
    *,
    state_path: Optional[str] = None,
    output_path: Optional[str] = None,
    now: Optional[_dt.datetime] = None,
    fetch: Any = None,
) -> Dict[str, Any]:
    """Refresh the external snapshot, match it, write trending.json."""
    state_file = state_path or DEFAULT_STATE_PATH
    output_file = output_path or DEFAULT_OUTPUT_PATH
    fetcher = fetch or fetch_external

    state = load_state(state_file)
    previous = state.get("snapshot") or {}

    try:
        fresh_snapshot = fetcher(now)
    except Exception:  # noqa: BLE001 - a provider failure is not a scan failure
        fresh_snapshot = None

    if fresh_snapshot:
        state["snapshot"] = fresh_snapshot
        state["last_good_tmdb_at"] = fresh_snapshot.get("fetched_at")
        snapshot = fresh_snapshot
        save_state(state, state_file)
    else:
        # NEVER overwrite good data with an outage. The previous snapshot
        # stays exactly as it is, and its age does the talking.
        snapshot = previous
        state["last_failed_fetch_at"] = _now(now).isoformat()
        save_state(state, state_file)

    if not snapshot:
        return {
            "skipped": "no external trending snapshot available yet",
            "external_items": 0,
            "matched": 0,
        }

    catalog_index = build_catalog_index(paginated)
    document = build_document(snapshot, catalog_index, now=now)
    matched = len(document["movies"]) + len(document["tv"])

    if matched == 0 and not fresh_snapshot:
        existing = _load_json(output_file)
        if existing.get("movies") or existing.get("tv"):
            return {
                "skipped": "external fetch failed and nothing matched; kept last-good trending.json",
                "external_items": len(snapshot.get("movies") or []) + len(snapshot.get("tv") or []),
                "matched": 0,
                "freshness": document["freshness"],
            }

    _atomic_write_json(output_file, document)
    return {
        "external_items": len(snapshot.get("movies") or []) + len(snapshot.get("tv") or []),
        "matched": matched,
        "movies": len(document["movies"]),
        "tv": len(document["tv"]),
        "freshness": document["freshness"],
        "fresh_fetch": bool(fresh_snapshot),
    }
