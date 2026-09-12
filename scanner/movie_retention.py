"""Keep a working movie through one bad scan instead of dropping it.

Measured problem. Between the 2026-08-22 and 2026-08-27 scans, 510 ids
appeared and **383 disappeared** - out of 817. The catalogue was not growing and
shrinking because films were being added and withdrawn upstream at that rate;
it was churning, because a movie that failed verification once was simply gone
from the next publish.

The existing protection does not cover this. `movie_failure_protection` guards
the CATEGORY total: it refuses a publish when the count drops by more than 40%.
Across those two scans the total went UP, 817 to 944, so the guard was silent
while 383 individual films were lost.

What this adds is per-item: a movie published last time and missing this time
is re-published, marked `stale_last_good` - a status this project already
defines and already ranks below a fresh verification - until it has been
missing for GRACE_SCANS consecutive scans. With the movie scan running daily
that is three days, long enough to survive a source outage across a weekend
and short enough that a withdrawn film stops being offered within the week.

PART 18 adds the lifecycle beside it: last_seen_at, is_active, inactive_since
and consecutive_missing_scans, plus the rule that a scan which did not cover
enough of a category is not evidence that anything is missing. first_seen_at
is never touched here, so a film that comes back comes back as itself and is
not announced as a new arrival.

Nothing here hides, removes or reorders anything. It only re-adds.
"""
from __future__ import annotations

import datetime as _dt
import glob
import json
import os
from typing import Any, Dict, List, Optional, Tuple

try:
    from scanner import paths
except ImportError:  # pragma: no cover - direct-module import path
    import paths  # type: ignore

DEFAULT_PATH = paths.state_path("movie-retention.json")

MOVIES_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "movies",
)

#: Consecutive scans a movie may be missing and still be re-published.
#:
#: This was 1. PART 18 of the movie plan requires a conservative grace of
#: about three consecutive successful scans before a title stops being
#: offered, so the published grace and the lifecycle threshold below are now
#: the same number rather than disagreeing by two days. The measured incident
#: this module was written for - 383 of 817 films lost between two scans -
#: argues for the longer grace, not the shorter one.
GRACE_SCANS = 3

#: The status a re-published movie carries. Already defined in
#: scanner/movies.py's MOVIE_STATUS_PRIORITY, ranked below every fresh
#: verification, so a retained item sorts after a verified one by construction.
RETAINED_STATUS = "stale_last_good"

# --- lifecycle (PART 18) ---------------------------------------------------
#
# The grace above decides whether to keep PUBLISHING a card. The lifecycle
# below is a separate question: is this title still part of the catalogue at
# all? They are deliberately different numbers. One missing scan must never
# mean either, and a scan that half-failed must not mean anything at all.

#: Consecutive *successful* scans a movie may be missing before it is marked
#: inactive. The movie scan runs daily (scan.yml, `37 4 * * *`), so three is
#: three days - long enough to ride out a source outage over a weekend, short
#: enough that a withdrawn film stops being offered within the week. This is
#: the plan's "3 consecutive successful movie scans" option, chosen over the
#: 7-day window because the cadence is already daily and counting scans is
#: exact where counting days has to guess at missed runs.
INACTIVE_AFTER_MISSING_SCANS = 3

#: A scan that found less than this fraction of what the category published
#: last time is treated as a failed or partial run, not as a discovery that
#: the catalogue shrank. Nothing is counted absent on such a scan.
#:
#: This is the gap the existing empty-list check leaves open: a run that dies
#: a third of the way through returns a short list, not an empty one, and
#: every film it never reached would otherwise be recorded as missing.
MINIMUM_SCAN_COVERAGE = 0.5

#: How long an inactive record is kept before it may be garbage-collected.
#: Long on purpose: the history, the metadata and any manual override are
#: worth more than the bytes, and a film that returns after a season should
#: come back as itself rather than as a new arrival.
GC_AFTER_INACTIVE_DAYS = 90


def _load(path: Optional[str] = None) -> Dict[str, Any]:
    target = path or DEFAULT_PATH
    try:
        with open(target, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return {"version": 1, "absent": {}, "lifecycle": {}}
    if not isinstance(payload, dict):
        return {"version": 1, "absent": {}, "lifecycle": {}}
    if not isinstance(payload.get("absent"), dict):
        payload["absent"] = {}
    if not isinstance(payload.get("lifecycle"), dict):
        payload["lifecycle"] = {}
    payload.setdefault("version", 1)
    return payload


def _write(store: Dict[str, Any], path: Optional[str] = None) -> bool:
    target = path or DEFAULT_PATH
    store["note"] = (
        "How many consecutive scans each movie has been missing. A movie is "
        "re-published for GRACE_SCANS scans before it is dropped, because 383 "
        "of 817 films vanished between two scans while the category-total "
        "guard stayed silent."
    )
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as handle:
            json.dump(store, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
    except OSError:
        return False
    return True


def _item_key(movie: Dict[str, Any]) -> str:
    from scanner import movie_recency

    return movie_recency.movie_key(movie)


def previously_published(
    category_slug: str, root: Optional[str] = None
) -> List[Dict[str, Any]]:
    """The items this category published last time, read off disk."""
    base = root or MOVIES_ROOT
    found: List[Dict[str, Any]] = []
    pattern = os.path.join(base, category_slug, "page-*.json")
    for path in sorted(glob.glob(pattern)):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError):
            continue
        items = (
            payload
            if isinstance(payload, list)
            else (payload.get("items") or payload.get("movies") or [])
        )
        for item in items if isinstance(items, list) else ():
            if isinstance(item, dict) and item.get("url"):
                found.append(item)
    return found


def _lifecycle_record(store: Dict[str, Any], key: str) -> Dict[str, Any]:
    lifecycle = store.setdefault("lifecycle", {})
    if not isinstance(lifecycle, dict):
        lifecycle = {}
        store["lifecycle"] = lifecycle
    record = lifecycle.get(key)
    if not isinstance(record, dict):
        record = {}
        lifecycle[key] = record
    return record


def scan_looks_complete(found: int, previously: int) -> bool:
    """Did this scan see enough of the category to be believed?

    A category that published 800 films last time and returns 12 this time has
    not lost 788 films; the run broke. Absence is only meaningful when the scan
    that reported it actually finished.
    """
    if found <= 0:
        return False
    if previously <= 0:
        return True
    return (found / previously) >= MINIMUM_SCAN_COVERAGE


def update_lifecycle(
    present_keys,
    previous_keys,
    *,
    store: Dict[str, Any],
    stamp: str,
    scan_complete: bool,
    now: Optional[_dt.datetime] = None,
) -> Dict[str, Any]:
    """Move the lifecycle on by one scan. Returns a summary.

    Seen this scan: active, `last_seen_at` refreshed, missing count back to
    zero. `first_seen_at` is never touched here - it belongs to
    scanner/movie_recency and a reappearance must not reset it, or a film that
    came back after an outage would be announced as a new arrival.

    Missing on a complete scan: the count goes up, and only once it passes
    INACTIVE_AFTER_MISSING_SCANS does the record go inactive.

    Missing on an incomplete scan: nothing happens at all. Not a smaller
    increment, not a shorter grace - nothing. The scan simply did not produce
    evidence of absence.
    """
    reference = now or _dt.datetime.now(_dt.timezone.utc)
    summary = {
        "seen": 0,
        "reactivated": 0,
        "missing": 0,
        "newly_inactive": 0,
        "absence_ignored_incomplete_scan": 0,
    }

    for key in present_keys:
        if not key:
            continue
        record = _lifecycle_record(store, key)
        was_inactive = record.get("is_active") is False
        record["last_seen_at"] = stamp
        record["is_active"] = True
        record["inactive_since"] = None
        record["consecutive_missing_scans"] = 0
        summary["seen"] += 1
        if was_inactive:
            # Same stable identity, so it is the same film: it comes back as
            # itself, keeping its history and anything manual set on it.
            record["reactivated_at"] = stamp
            summary["reactivated"] += 1

    for key in previous_keys:
        if not key or key in present_keys:
            continue
        if not scan_complete:
            summary["absence_ignored_incomplete_scan"] += 1
            continue
        record = _lifecycle_record(store, key)
        misses = int(record.get("consecutive_missing_scans") or 0) + 1
        record["consecutive_missing_scans"] = misses
        record["last_missing_at"] = stamp
        summary["missing"] += 1
        if misses >= INACTIVE_AFTER_MISSING_SCANS and record.get("is_active") is not False:
            record["is_active"] = False
            record["inactive_since"] = stamp
            summary["newly_inactive"] += 1

    summary["collected"] = _collect_expired(store, reference)
    return summary


def _collect_expired(store: Dict[str, Any], reference: _dt.datetime) -> int:
    """Drop lifecycle records that have been inactive longer than the window.

    Deliberately the last thing that happens and deliberately slow: history is
    cheap and a film that returns should return as itself.
    """
    lifecycle = store.get("lifecycle")
    if not isinstance(lifecycle, dict):
        return 0
    cutoff = reference - _dt.timedelta(days=GC_AFTER_INACTIVE_DAYS)
    removed = 0
    for key, record in list(lifecycle.items()):
        if not isinstance(record, dict) or record.get("is_active") is not False:
            continue
        stamp = record.get("inactive_since")
        if not stamp:
            continue
        try:
            when = _dt.datetime.fromisoformat(str(stamp))
        except ValueError:
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=_dt.timezone.utc)
        if when < cutoff:
            lifecycle.pop(key, None)
            removed += 1
    return removed


def lifecycle_state(path: Optional[str] = None) -> Dict[str, Any]:
    """The lifecycle records, read-only, for callers that need to filter."""
    return dict(_load(path).get("lifecycle") or {})


def is_active(key: str, path: Optional[str] = None) -> bool:
    """Unknown means active: a title nobody has recorded is not withdrawn."""
    record = lifecycle_state(path).get(key)
    if not isinstance(record, dict):
        return True
    return record.get("is_active") is not False


def retain(
    movies: List[Dict[str, Any]],
    category_slug: str,
    *,
    root: Optional[str] = None,
    path: Optional[str] = None,
    now: Optional[_dt.datetime] = None,
    persist: bool = True,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """(list to publish, summary). Adds back recent drop-outs, adds only.

    An empty incoming list is left completely alone. A scan that found nothing
    for a category has almost certainly failed rather than discovered that the
    category is empty, and re-publishing everything under `stale_last_good`
    would dress that failure up as a result. The category-total guard in
    scanner/output.py is the right mechanism for that case.
    """
    incoming = [movie for movie in (movies or []) if isinstance(movie, dict)]
    summary: Dict[str, Any] = {
        "incoming": len(incoming),
        "retained": 0,
        "dropped_after_grace": 0,
        "category": category_slug,
    }
    if not incoming:
        summary["skipped"] = "incoming list is empty; category guard owns this case"
        return incoming, summary

    reference = now or _dt.datetime.now(_dt.timezone.utc)
    stamp = reference.isoformat()
    store = _load(path)
    absent = store["absent"]

    present_keys = {_item_key(movie) for movie in incoming}
    present_keys.discard("")

    for key in list(absent):
        if key in present_keys:
            absent.pop(key, None)

    previous_items = previously_published(category_slug, root)
    previous_keys = {_item_key(item) for item in previous_items}
    previous_keys.discard("")

    # Was this scan complete enough to be evidence of anything? A run that
    # broke half way through returns a short list, and every title it never
    # reached would otherwise be recorded as missing.
    complete = scan_looks_complete(len(present_keys), len(previous_keys))
    summary["scan_complete"] = complete
    summary["lifecycle"] = update_lifecycle(
        present_keys,
        previous_keys,
        store=store,
        stamp=stamp,
        scan_complete=complete,
        now=reference,
    )
    if not complete:
        summary["lifecycle_note"] = (
            f"{len(present_keys)} of {len(previous_keys)} previously published "
            "found; treated as an incomplete scan, so nothing was counted "
            "absent in the lifecycle. The publish grace above is unchanged - "
            "it owns whether a card is still shown, and its behaviour is "
            "pinned by its own tests."
        )

    retained: List[Dict[str, Any]] = []
    for previous in previous_items:
        key = _item_key(previous)
        if not key or key in present_keys:
            continue
        record = absent.get(key)
        misses = int((record or {}).get("misses") or 0) + 1
        if misses > GRACE_SCANS:
            absent[key] = {"misses": misses, "last_missing_at": stamp}
            summary["dropped_after_grace"] += 1
            continue
        absent[key] = {"misses": misses, "last_missing_at": stamp}
        carried = dict(previous)
        carried["verification_status"] = RETAINED_STATUS
        carried["retained_after_failed_scan"] = True
        carried["retained_scan_count"] = misses
        carried["retention_note"] = (
            "Published on a previous scan and not found on this one. Carried "
            f"for {misses} of {GRACE_SCANS} allowed scans so a single "
            "transient failure does not remove a working film; dropped if it "
            "is missing again."
        )
        retained.append(carried)

    if persist:
        _write(store, path)

    summary["retained"] = len(retained)
    return incoming + retained, summary
