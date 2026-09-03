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

What this adds is per-item, and deliberately short: one scan of grace. A movie
published last time and missing this time is re-published once, marked
`stale_last_good` - a status this project already defines and already ranks
below a fresh verification. If it is missing again on the next scan it goes.
With the movie scan now running daily, that is a single day of grace, which is
long enough to survive a CDN hiccup or a timeout and too short to leave a dead
link in the catalogue.

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

#: Consecutive scans a movie may be missing and still be re-published. One:
#: enough for a transient failure, not enough to keep a withdrawn film.
GRACE_SCANS = 1

#: The status a re-published movie carries. Already defined in
#: scanner/movies.py's MOVIE_STATUS_PRIORITY, ranked below every fresh
#: verification, so a retained item sorts after a verified one by construction.
RETAINED_STATUS = "stale_last_good"


def _load(path: Optional[str] = None) -> Dict[str, Any]:
    target = path or DEFAULT_PATH
    try:
        with open(target, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return {"version": 1, "absent": {}}
    if not isinstance(payload, dict):
        return {"version": 1, "absent": {}}
    if not isinstance(payload.get("absent"), dict):
        payload["absent"] = {}
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

    retained: List[Dict[str, Any]] = []
    for previous in previously_published(category_slug, root):
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
