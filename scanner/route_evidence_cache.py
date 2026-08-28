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

#: How long an observation is kept.
#:
#: Was 14 days, chosen to span the ~6 h channel-scan cadence comfortably. The
#: file reached 60.4 MB across 26,396 routes and is committed by every scan -
#: GitHub warns above 50 MB per file and refuses above 100 MB, so a few more
#: scans would have broken pushing entirely.
#:
#: 7 days still spans about 28 channel scans and 3 movie scans, which is far
#: more than the rule needs: two observations from different vantages in
#: separate time windows. Nothing about the two-vantage decision reaches back a
#: fortnight.
RETENTION_SECONDS = 7 * 24 * 3600

#: A single route accumulating unboundedly is a slow leak, not a feature - once
#: two windows exist the verdict is already decided.
MAX_RECORDS_PER_ROUTE = 20

#: A hard ceiling on how many routes the cache holds, newest observation first.
#:
#: Retention alone does not bound this file. Every route the scanner has ever
#: probed within the window is kept, and one scan probes 3,200 channels and
#: 21,400 movies - so the file reached 60.4 MB with every route inside 7 days
#: and nothing to trim. It is committed by every scan, and GitHub refuses a
#: file above 100 MB, so the growth had a deadline.
#:
#: 20,000 routes at ~1 KB each keeps it near 20 MB. The routes dropped are the
#: least recently observed, which are also the least likely to be asked about:
#: the two-vantage decision is made about a route the CURRENT scan is looking
#: at, and that route is by definition freshly observed.
MAX_ROUTES = 20_000


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

    if len(routes) > MAX_ROUTES:
        # Least recently observed routes go first. Retention could not bound
        # this on its own: one scan probes 24,600 routes, so every route was
        # inside the window and nothing was ever trimmed.
        def _latest(item) -> float:
            return max(
                (rev._parse_observed_at(r.get("observed_at")) or 0.0)
                for r in item[1]
            )

        ordered = sorted(routes.items(), key=_latest, reverse=True)
        routes = dict(ordered[:MAX_ROUTES])

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
            # Compact separators, not indent=2. This file is machine-read only
            # and 24% of its 60 MB was whitespace; a human reading it uses
            # jq or the audit report, both of which are unaffected.
            json.dump(
                cache, handle, ensure_ascii=False, separators=(",", ":")
            )
            handle.write("\n")
        os.replace(temporary, target)
    except OSError:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        return 0
    return written
