"""Cross-scan storage for the per-route evidence records `may_hide` reads.

`route_evidence_pipeline` assembles complete evidence records; `visibility_audit`
holds them in a process-local dict that starts empty every run. A scan is one
process, so nothing built there could ever accumulate a second, separately-timed
observation - the "two independent vantages, two separate time windows"
requirement was structurally unreachable in production, not merely unmet.

This closes that by writing records to disk and reading them back at the start
of the next scan. Pruning uses a retention window, not the Phase 0b
persistence.ttl_seconds lock (1800 s): that lock governs a different mechanism
(the escalation counter in persistence_store.py) and is far shorter than the
measured gap between real channel scans (~6 h) or movie scans (~48 h) - at
1800 s, evidence from one scan would always have expired before the next one
ran, and cross-scan accumulation could never happen at all. See
route_preference.PREFERENCE_TTL_SECONDS for the same reasoning applied to a
different registry.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from scanner import route_evidence as rev

DEFAULT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "state",
    "route-evidence-cache.json",
)

#: New policy, reasoned from measured scan cadence rather than the Phase 0b
#: lock: several multiples of the slowest real cadence (movies, ~48 h), long
#: enough that at least two real scans' evidence can co-exist, short enough
#: that a route nobody re-observes for two weeks stops counting.
RETENTION_SECONDS = 14 * 24 * 3600

#: A single route accumulating unboundedly is a slow leak, not a feature - once
#: two windows exist the verdict is already decided.
MAX_RECORDS_PER_ROUTE = 20


def load(path: Optional[str] = None) -> Dict[str, Any]:
    """Read the cache. Unreadable means empty, never an error."""
    target = path or DEFAULT_PATH
    try:
        with open(target, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return {"version": 1, "routes": {}}
    if not isinstance(payload, dict) or not isinstance(payload.get("routes"), dict):
        return {"version": 1, "routes": {}}
    return payload


def _prune(
    cache: Dict[str, Any], *, now: Optional[float] = None
) -> Dict[str, Any]:
    import datetime as _dt

    reference = (
        _dt.datetime.now(_dt.timezone.utc).timestamp() if now is None else now
    )
    routes: Dict[str, List[Dict[str, Any]]] = {}
    for route_id, records in (cache.get("routes") or {}).items():
        kept = []
        for record in records or ():
            if not isinstance(record, dict):
                continue
            if rev.evidence_contains_forbidden_material(record):
                continue
            moment = rev._parse_observed_at(record.get("observed_at"))
            if moment is None or (reference - moment) > RETENTION_SECONDS:
                continue
            kept.append(record)
        if kept:
            kept.sort(key=lambda r: rev._parse_observed_at(r.get("observed_at")) or 0.0)
            routes[str(route_id)] = kept[-MAX_RECORDS_PER_ROUTE:]
    return {"version": 1, "routes": routes}


def all_records(path: Optional[str] = None, *, now: Optional[float] = None) -> List[Dict[str, Any]]:
    """Every non-expired record currently cached, flattened."""
    cache = _prune(load(path), now=now)
    return [record for records in cache["routes"].values() for record in records]


def append(
    records: List[Dict[str, Any]],
    *,
    path: Optional[str] = None,
    now: Optional[float] = None,
) -> int:
    """Merge new records into the cache and persist. Returns records written.

    Best-effort: a write failure loses this run's contribution rather than
    breaking the scan, the same trade every other state file in this project
    makes.
    """
    target = path or DEFAULT_PATH
    cache = _prune(load(target), now=now)
    written = 0
    for record in records or ():
        if not isinstance(record, dict):
            continue
        if rev.evidence_contains_forbidden_material(record):
            continue
        route_id = str(record.get("route_id") or "")
        if not route_id:
            continue
        bucket = cache["routes"].setdefault(route_id, [])
        bucket.append(record)
        cache["routes"][route_id] = bucket[-MAX_RECORDS_PER_ROUTE:]
        written += 1
    # Written to a temporary file and renamed into place. A 13 MB cache takes
    # long enough to serialise that a reader can catch it half-written: a test
    # reading it while a movie scan was writing hit
    # "JSONDecodeError: Expecting value: line 316268". The loader tolerates a
    # broken file by returning empty, so the visible cost was a lost cache
    # rather than a crash - but losing 22,000 records to a badly-timed read is
    # not a cost worth keeping when a rename makes the swap atomic.
    temporary = f"{target}.tmp"
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(cache, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        os.replace(temporary, target)
    except OSError:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        return 0
    return written
