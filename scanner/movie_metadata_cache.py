"""Canonical movie metadata schema + persistent cache (PART 02).

Real metadata (tmdb_id, genres, rating, release_date, backdrop, ...) is
expensive and rate-limited to fetch, so it is resolved once per movie
identity and kept in state/movie-metadata-cache.json across scans. This
module owns the schema, the cache file, and the enrich() step that applies
cached (and, once a `lookup` callable is supplied, newly resolved) metadata
onto movie records.

Nothing here talks to a network - PART 03's scanner/metadata_providers.py
supplies the `lookup` callable; this module stays a pure cache/schema layer
so it works, and is testable, with no provider wired in yet.

Identity: this cache uses `scanner.merger.movie_identity_key` (imdb_id ->
tmdb_id -> normalized title:year) as its key - deliberately NOT the same key
as `scanner.movie_recency.movie_key` (which uses the scanner's own generated
`id`/title-slug). The two ledgers answer different questions: recency asks
"have I already published this exact card before", metadata asks "what
external metadata belongs to this film regardless of which card carries
it". Merging the two namespaces would risk re-keying 15k+ already-correct
first_seen_at entries for no benefit. See docs/movie-discovery-audit.md
sections 2 and 7 for the full rationale.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    from scanner import paths
    from scanner.merger import movie_identity_key
except ImportError:  # pragma: no cover - direct-module import path
    import paths  # type: ignore
    from merger import movie_identity_key  # type: ignore

DEFAULT_PATH = paths.state_path("movie-metadata-cache.json")

#: Schema fields this cache owns. Applied onto a movie dict fill-only -
#: never overwrites a value the scanner/manual data already set.
METADATA_FIELDS: Tuple[str, ...] = (
    "tmdb_id",
    "imdb_id",
    "release_date",
    "genres",
    "rating",
    "rating_source",
    "rating_votes",
    "backdrop",
    "metadata_source",
    "metadata_updated_at",
    "metadata_confidence",
)

#: How many fresh provider lookups one scan run may perform. The movie scan
#: has a fixed time budget (config/settings.json time_budget_seconds.movies)
#: and a cold cache spans 15k+ movies, so a full backfill happens
#: incrementally across many scans rather than in one run. Tunable without a
#: code change; the real rate-limit/retry policy is PART 04's scope.
DEFAULT_LOOKUP_BUDGET = int(os.environ.get("MOVIE_METADATA_LOOKUP_BUDGET", "40") or 40)

#: Skip re-attempting a movie that matched nothing last time for this long,
#: so a handful of unmatchable titles cannot eat the whole budget every run.
RETRY_AFTER_FAILURE_DAYS = 7


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


def load(path: Optional[str] = None) -> Dict[str, Any]:
    """Load the cache. Missing/corrupt file -> empty cache, never raises."""
    target = path or DEFAULT_PATH
    try:
        with open(target, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return {"version": 1, "movies": {}}
    if not isinstance(payload, dict):
        return {"version": 1, "movies": {}}
    payload.setdefault("version", 1)
    if not isinstance(payload.get("movies"), dict):
        payload["movies"] = {}
    return payload


def save(store: Dict[str, Any], path: Optional[str] = None) -> bool:
    """Atomic write: temp file + fsync + os.replace. Never leaves a partial file."""
    target = path or DEFAULT_PATH
    try:
        target_dir = os.path.dirname(target) or "."
        os.makedirs(target_dir, exist_ok=True)
        temp_path = os.path.join(
            target_dir,
            f".{os.path.basename(target)}.{os.getpid()}.{time.time_ns()}.tmp",
        )
        try:
            with open(temp_path, "w", encoding="utf-8") as handle:
                json.dump(store, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, target)
        finally:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
    except OSError:
        return False
    return True


def canonical_identity(movie: Dict[str, Any]) -> str:
    """Stable content identity: imdb_id -> tmdb_id -> title+year (see module docstring)."""
    if not isinstance(movie, dict):
        return ""
    return movie_identity_key(movie)


def _apply_fill_only(movie: Dict[str, Any], record: Dict[str, Any]) -> None:
    """Copy schema fields from a cache record onto a movie, never overwriting
    a value the movie already has - manual/scanner-provided data always wins."""
    for field in METADATA_FIELDS:
        value = record.get(field)
        if value in (None, "", [], {}):
            continue
        existing = movie.get(field)
        if existing in (None, "", [], {}):
            movie[field] = value


def _should_skip_after_failure(record: Dict[str, Any], now: _dt.datetime) -> bool:
    attempted = _parse_stamp(record.get("metadata_last_attempt_at"))
    if attempted is None:
        return False
    return (now - attempted) <= _dt.timedelta(days=RETRY_AFTER_FAILURE_DAYS)


def upsert(
    store: Dict[str, Any],
    identity: str,
    fields: Optional[Dict[str, Any]],
    *,
    now: Optional[_dt.datetime] = None,
) -> Dict[str, Any]:
    """Merge a (possibly partial) resolved-metadata dict into the cache.

    Fill-only across calls too: a later, lower-confidence provider cannot
    blank out a field an earlier one already resolved, and a failed
    lookup (`fields=None`) never erases a previously cached value - it only
    records the attempt so the retry-after-failure cooldown applies.
    """
    movies = store.setdefault("movies", {})
    record = movies.get(identity)
    if not isinstance(record, dict):
        record = {}
        movies[identity] = record

    stamp = (now or _now()).isoformat()
    record["metadata_last_attempt_at"] = stamp

    if fields:
        for field in METADATA_FIELDS:
            value = fields.get(field)
            if value in (None, "", [], {}):
                continue
            if record.get(field) in (None, "", [], {}):
                record[field] = value
        if fields.get("metadata_source"):
            record["metadata_source"] = fields["metadata_source"]
        if fields.get("metadata_sources"):
            record["metadata_sources"] = fields["metadata_sources"]
        if fields.get("metadata_confidence"):
            record["metadata_confidence"] = fields["metadata_confidence"]
        record["metadata_updated_at"] = stamp
    return record


def enrich(
    movies: List[Dict[str, Any]],
    *,
    path: Optional[str] = None,
    now: Optional[_dt.datetime] = None,
    persist: bool = True,
    lookup: Optional[Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]] = None,
    budget: Optional[int] = None,
) -> Dict[str, int]:
    """Apply cached metadata onto every movie; optionally resolve new ones.

    Adds only - never removes/overrides an existing field (mirrors
    movie_recency.enrich's contract). With `lookup=None` (PART 02 default)
    this only ever reads the cache; no network call is made. PART 03 passes
    a real lookup, and callers gate that to the true publish path only so
    unit tests / ad-hoc calls never make a network call.
    """
    reference = _now(now)
    remaining_budget = DEFAULT_LOOKUP_BUDGET if budget is None else budget
    store = load(path)
    movies_store = store.setdefault("movies", {})

    cached_hits = 0
    fetched = 0
    failed = 0
    skipped_budget = 0
    skipped_cooldown = 0

    for movie in movies or ():
        if not isinstance(movie, dict):
            continue
        identity = canonical_identity(movie)
        if not identity:
            continue

        record = movies_store.get(identity)
        has_data = isinstance(record, dict) and any(
            record.get(field) for field in METADATA_FIELDS
        )
        if has_data:
            _apply_fill_only(movie, record)
            cached_hits += 1
            continue

        if lookup is None:
            continue
        if isinstance(record, dict) and _should_skip_after_failure(record, reference):
            skipped_cooldown += 1
            continue
        if remaining_budget <= 0:
            skipped_budget += 1
            continue

        remaining_budget -= 1
        try:
            resolved = lookup(movie)
        except Exception:  # noqa: BLE001 - a provider bug must not fail a scan
            resolved = None

        record = upsert(store, identity, resolved, now=reference)
        if resolved:
            _apply_fill_only(movie, record)
            fetched += 1
        else:
            failed += 1

    if persist:
        save(store, path)

    return {
        "cached_hits": cached_hits,
        "fetched": fetched,
        "failed": failed,
        "skipped_budget": skipped_budget,
        "skipped_cooldown": skipped_cooldown,
    }
