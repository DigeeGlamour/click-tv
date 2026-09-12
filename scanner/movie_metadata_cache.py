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
    "backdrop_source",
    "metadata_source",
    "metadata_updated_at",
    "metadata_confidence",
)

#: How many fresh provider lookups one scan run may perform, so a cold
#: cache backfills across several runs instead of blowing the movie scan's
#: time budget (config/settings.json time_budget_seconds.movies = 2400) in
#: one go.
#:
#: 150 is measured rather than picked: the published catalogue is ~1,670
#: movies, a matched title costs about four requests and an unmatched one
#: about six, and the request policy paces at 5/s - so 150 lookups is
#: roughly two to three minutes of a forty-minute budget, and the whole
#: catalogue is enriched in under a fortnight of daily scans. Tunable
#: without a code change.
DEFAULT_LOOKUP_BUDGET = int(os.environ.get("MOVIE_METADATA_LOOKUP_BUDGET", "150") or 150)

#: Skip re-attempting a movie that matched nothing last time for this long,
#: so a handful of unmatchable titles cannot eat the whole budget every run.
RETRY_AFTER_FAILURE_DAYS = 7

#: Cache-first refresh policy (PART 04). An already-resolved movie is only
#: looked up again once its record is older than the TTL for its kind, and
#: only ever with budget left over after every not-yet-resolved movie has
#: had its turn. A film from 2011 is not going to change its genres; a film
#: released this year is still accumulating votes and can correct its
#: release date, so it is re-read sooner.
REFRESH_TTL_DAYS_STABLE = 90
REFRESH_TTL_DAYS_RECENT = 14

#: How recent a release_date has to be to count as "still moving".
RECENT_RELEASE_DAYS = 365


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


#: Poster fields the existing scanner already resolves, in the order
#: scanner/movies.py itself prefers them. Only ever read here - the poster
#: chain in movies.py is untouched.
_POSTER_FIELDS = ("logo", "poster", "image")


def _apply_poster_backdrop_fallback(movie: Dict[str, Any]) -> bool:
    """Last-resort backdrop: the poster the movie already has (PART 05 K).

    Deliberately applied to the movie only and never written to the cache.
    Caching it would make a stretched poster permanent - fill-only merging
    would then refuse the real TMDB/Fanart backdrop when it finally
    arrives. `backdrop_source` says plainly where the image came from, so a
    consumer that needs a true 16:9 backdrop can decline this one.
    """
    if movie.get("backdrop"):
        return False
    for field in _POSTER_FIELDS:
        poster = str(movie.get(field) or "").strip()
        if poster:
            movie["backdrop"] = poster
            movie["backdrop_source"] = "poster"
            return True
    return False


def _should_skip_after_failure(record: Dict[str, Any], now: _dt.datetime) -> bool:
    attempted = _parse_stamp(record.get("metadata_last_attempt_at"))
    if attempted is None:
        return False
    return (now - attempted) <= _dt.timedelta(days=RETRY_AFTER_FAILURE_DAYS)


def _refresh_ttl_days(record: Dict[str, Any], now: _dt.datetime) -> int:
    """Long TTL for settled metadata, shorter for a recent release."""
    released = _parse_stamp(record.get("release_date"))
    if released is not None and (now - released) <= _dt.timedelta(days=RECENT_RELEASE_DAYS):
        return REFRESH_TTL_DAYS_RECENT
    return REFRESH_TTL_DAYS_STABLE


def _is_due_for_refresh(record: Dict[str, Any], now: _dt.datetime) -> bool:
    updated = _parse_stamp(record.get("metadata_updated_at"))
    if updated is None:
        return False
    return (now - updated) > _dt.timedelta(days=_refresh_ttl_days(record, now))


def _load_manual(manual_path: Optional[str] = None) -> Dict[str, Any]:
    """Admin-asserted metadata. A missing file is simply no overrides."""
    try:
        from scanner import movie_metadata_confidence as confidence_policy

        return confidence_policy.load_manual(manual_path)
    except Exception:  # noqa: BLE001 - overrides must never fail a scan
        return {"version": 1, "movies": {}}


def _manual_entry(manual_store: Dict[str, Any], identity: str) -> Dict[str, Any]:
    entry = (manual_store.get("movies") or {}).get(identity)
    return entry if isinstance(entry, dict) else {}


def upsert(
    store: Dict[str, Any],
    identity: str,
    fields: Optional[Dict[str, Any]],
    *,
    now: Optional[_dt.datetime] = None,
    local: Optional[Dict[str, Any]] = None,
    manual: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Merge a (possibly partial) resolved-metadata dict into the cache.

    Fill-only across calls too: a later, lower-confidence provider cannot
    blank out a field an earlier one already resolved, and a failed
    lookup (`fields=None`) never erases a previously cached value - it only
    records the attempt so the retry-after-failure cooldown applies.

    PART 19. When `local` is given, how much of the candidate is believed is
    worked out from THIS match rather than taken from the provider's own
    label: a trusted external id or an exact title-and-year is high, a year
    off by one is medium, a conflicting year or a different title is low, and
    a series or person result for a film is rejected outright. A low match
    writes nothing and is recorded as unresolved. `manual` carries the
    admin-asserted values and locks, which no level may overwrite.
    """
    movies = store.setdefault("movies", {})
    record = movies.get(identity)
    if not isinstance(record, dict):
        record = {}
        movies[identity] = record

    stamp = (now or _now()).isoformat()
    record["metadata_last_attempt_at"] = stamp

    if not fields:
        return record

    if local is None and not manual:
        # Unchanged legacy path: fill-only, provider-labelled confidence.
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

    from scanner import movie_metadata_confidence as confidence_policy

    level, reason = (
        confidence_policy.match_confidence(local, fields)
        if local is not None
        else (fields.get("metadata_confidence") or confidence_policy.CONFIDENCE_MEDIUM, "provider label")
    )
    plan = confidence_policy.plan_application(
        record, fields, confidence=level, reason=reason, manual=manual or {}
    )

    for field, value in plan["apply"].items():
        if field in METADATA_FIELDS or field in ("metadata_source", "metadata_sources"):
            record[field] = value

    # The admin's assertions are applied last and unconditionally: they are
    # the one source no provider outranks.
    for field, value in confidence_policy.manual_values(manual or {}).items():
        if field in METADATA_FIELDS:
            record[field] = value

    record["metadata_confidence"] = level or confidence_policy.CONFIDENCE_LOW
    record["metadata_match_state"] = plan["state"]
    record["metadata_match_reason"] = reason
    if plan["refused"]:
        record["metadata_refused"] = plan["refused"]
    if plan["state"] == "applied":
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
    availability: Optional[Callable[[], bool]] = None,
    manual_path: Optional[str] = None,
) -> Dict[str, int]:
    """Apply cached metadata onto every movie; optionally resolve new ones.

    Adds only - never removes/overrides an existing field (mirrors
    movie_recency.enrich's contract). With `lookup=None` (PART 02 default)
    this only ever reads the cache; no network call is made. PART 03 passes
    a real lookup, and callers gate that to the true publish path only so
    unit tests / ad-hoc calls never make a network call.

    `availability` (PART 04) is an optional predicate answering "is any
    provider still worth asking right now". When it says no, the remaining
    lookups are abandoned for this run rather than recorded as failures -
    an outage must not leave a seven-day cooldown on movies that were never
    actually tried.
    """
    reference = _now(now)
    remaining_budget = DEFAULT_LOOKUP_BUDGET if budget is None else budget
    store = load(path)
    movies_store = store.setdefault("movies", {})
    manual_store = _load_manual(manual_path)

    cached_hits = 0
    fetched = 0
    refreshed = 0
    failed = 0
    skipped_budget = 0
    skipped_cooldown = 0
    skipped_unavailable = 0

    # Pass one: apply what the cache already knows, and sort the rest into
    # "never resolved" and "resolved but past its TTL". Cache-first, so a
    # movie with usable metadata costs no request at all.
    never_resolved: List[Dict[str, Any]] = []
    due_for_refresh: List[Dict[str, Any]] = []

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
            if lookup is not None and _is_due_for_refresh(record, reference):
                due_for_refresh.append(movie)
            continue

        if lookup is None:
            continue
        if isinstance(record, dict) and _should_skip_after_failure(record, reference):
            skipped_cooldown += 1
            continue
        never_resolved.append(movie)

    # Pass two: spend the run's budget. Movies with no metadata at all come
    # first - a catalogue entry with nothing is worth more than a refresh of
    # one that already reads correctly.
    for is_refresh, queue in ((False, never_resolved), (True, due_for_refresh)):
        for movie in queue:
            if remaining_budget <= 0:
                skipped_budget += 1
                continue
            if availability is not None and not availability():
                # Every provider is cooling down or key-broken. Stop asking:
                # recording these as failed attempts would put a
                # seven-day cooldown on movies that were never actually
                # tried, and the cached data must be left exactly as it is.
                skipped_unavailable += 1
                continue

            identity = canonical_identity(movie)
            remaining_budget -= 1
            try:
                resolved = lookup(movie)
            except Exception:  # noqa: BLE001 - a provider bug must not fail a scan
                resolved = None

            record = upsert(
                store, identity, resolved, now=reference,
                local=movie, manual=_manual_entry(manual_store, identity),
            )
            if resolved:
                _apply_fill_only(movie, record)
                if is_refresh:
                    refreshed += 1
                else:
                    fetched += 1
            else:
                failed += 1

    # Last: the poster-derived backdrop, for every movie still without one.
    # Never cached, never overwrites a real backdrop, and never a reason to
    # drop a movie - a title with no artwork at all simply keeps none.
    poster_backdrops = 0
    for movie in movies or ():
        if isinstance(movie, dict) and _apply_poster_backdrop_fallback(movie):
            poster_backdrops += 1

    # PART 19. Two local items holding one external id means at least one of
    # them is matched to the wrong film. It is reported and left alone: merging
    # them would destroy a real stream to tidy up a metadata mistake.
    conflicts: List[Dict[str, Any]] = []
    try:
        from scanner import movie_metadata_confidence as confidence_policy

        conflicts = confidence_policy.external_id_conflicts(movies_store)
    except Exception:  # noqa: BLE001 - reporting must never fail a scan
        conflicts = []

    if persist:
        save(store, path)

    return {
        "cached_hits": cached_hits,
        "fetched": fetched,
        "refreshed": refreshed,
        "failed": failed,
        "skipped_budget": skipped_budget,
        "skipped_cooldown": skipped_cooldown,
        "skipped_unavailable": skipped_unavailable,
        "poster_backdrops": poster_backdrops,
        "external_id_conflicts": conflicts,
    }
