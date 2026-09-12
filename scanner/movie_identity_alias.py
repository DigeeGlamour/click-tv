"""Reconciling the two movie identities, without touching history (PART 07).

The movie system has carried two identities since long before this work,
for good reasons, and PART 01's audit recorded both:

  - `movie_recency.movie_key` - the scanner's own generated `id`, falling
    back to a title slug. This is what `state/movie-first-seen.json` has
    been keyed by for its entire life, and it now holds 15,000+ real
    historical timestamps. It is the source of truth and is NOT rebuilt,
    re-keyed or migrated by anything here.
  - `merger.movie_identity_key` - tmdb_id -> imdb_id -> normalized
    title+year, the content identity the metadata cache uses.

They disagree in exactly one situation that matters: when a film's
generated `id` changes while the film does not. A manual entry replaced by
a discovered one, a title cleaned up at the source, a year finally filled
in - each mints a new `movie_key`, and the first-seen ledger, quite
correctly by its own rules, stamps it as seen for the first time today.
The film is then announced as "Just Added" months after it actually
arrived.

This module keeps a SEPARATE, additive ledger - canonical identity to the
earliest first_seen_at ever observed for it - so that case can be spotted
and corrected. `state/movie-first-seen.json` is never written to from here;
the correction is applied to the movie record being published, and this
ledger is what makes it stable across runs.

The correction only ever moves a date BACKWARDS, to a date the system has
genuinely seen before. Nothing here can make a film look newer than it is,
which is the direction that would actually mislead anyone.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    from scanner import paths
    from scanner.movie_metadata_cache import canonical_identity
except ImportError:  # pragma: no cover - direct-module import path
    import paths  # type: ignore
    from movie_metadata_cache import canonical_identity  # type: ignore

DEFAULT_PATH = paths.state_path("movie-identity-aliases.json")

#: Matches the first-seen ledger's own retention, so the two stay in step:
#: a film absent for longer than this is genuinely new when it returns.
RETENTION_DAYS = 180


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
    """Missing/corrupt -> empty ledger, never raises."""
    target = path or DEFAULT_PATH
    try:
        with open(target, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return {"version": 1, "identities": {}}
    if not isinstance(payload, dict):
        return {"version": 1, "identities": {}}
    payload.setdefault("version", 1)
    if not isinstance(payload.get("identities"), dict):
        payload["identities"] = {}
    return payload


def save(store: Dict[str, Any], path: Optional[str] = None) -> bool:
    target = path or DEFAULT_PATH
    store["updated_at"] = _now().isoformat()
    store["note"] = (
        "Canonical movie identity to the earliest first_seen_at ever seen "
        "for it. Exists so a film whose generated id changes is not "
        "announced as newly added months after it arrived. "
        "state/movie-first-seen.json remains the source of truth and is "
        "never written to from here."
    )
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


def alias_identity(movie: Dict[str, Any]) -> str:
    """The canonical identity, but only when it is strong enough to trust.

    An id-based key (tmdb_id/imdb_id) always is. A title key is accepted
    only with a year: two unrelated films share a title far more often than
    one film changes its id, and inheriting a date across a coincidence
    would be worse than the problem being solved.
    """
    if not isinstance(movie, dict):
        return ""
    identity = canonical_identity(movie)
    if identity.startswith(("tmdb_id:", "imdb_id:")):
        return identity
    if identity.startswith("title:") and not identity.endswith(":"):
        return identity
    return ""


def _earliest(left: Optional[str], right: Optional[str]) -> Optional[str]:
    left_at = _parse_stamp(left)
    right_at = _parse_stamp(right)
    if left_at is None:
        return right if right_at is not None else None
    if right_at is None:
        return left
    return left if left_at <= right_at else right


def _prune(identities: Dict[str, Any], now: _dt.datetime) -> int:
    cutoff = now - _dt.timedelta(days=RETENTION_DAYS)
    stale = [
        key
        for key, record in list(identities.items())
        if not isinstance(record, dict)
        or (_parse_stamp(record.get("last_seen_at") or record.get("first_seen_at")) or now) < cutoff
    ]
    for key in stale:
        identities.pop(key, None)
    return len(stale)


def reconcile(
    movies: Iterable[Dict[str, Any]],
    *,
    path: Optional[str] = None,
    now: Optional[_dt.datetime] = None,
    persist: bool = True,
) -> Dict[str, int]:
    """Give a film back its real arrival date when its id changed.

    Adds only: a movie whose date is already the earliest known keeps it.
    Never writes to state/movie-first-seen.json.
    """
    reference = _now(now)
    stamp = reference.isoformat()
    store = load(path)
    identities = store.setdefault("identities", {})

    corrected = 0
    recorded = 0

    for movie in movies or ():
        if not isinstance(movie, dict):
            continue
        identity = alias_identity(movie)
        if not identity:
            continue
        current = str(movie.get("first_seen_at") or "").strip()
        record = identities.get(identity)
        known = record.get("first_seen_at") if isinstance(record, dict) else None

        earliest = _earliest(known, current or None)
        if earliest is None:
            continue

        if current and earliest != current:
            # The film has been here since `earliest`; its id merely changed.
            movie["first_seen_at"] = earliest
            corrected += 1

        if not isinstance(record, dict):
            record = {}
            identities[identity] = record
            recorded += 1
        record["first_seen_at"] = earliest
        record["last_seen_at"] = stamp

    pruned = _prune(identities, reference)
    if persist:
        save(store, path)
    return {"corrected": corrected, "recorded": recorded, "pruned": pruned}
