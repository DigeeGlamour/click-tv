"""Cross-run storage for the persistence counter.

`route_evidence.persistence_state` decides whether repeated escalatable evidence
has matured into a persistent-unavailable candidate. It needs observations from
SEPARATE runs to do that - the locked window is 1800 s with observations at least
120 s apart, which no single scan spans. Without somewhere to keep them the
counter could never advance past one, so the whole escalation path was inert.

This is that store, and it is deliberately conservative in one direction only:
every failure mode here loses evidence rather than inventing it. An unreadable
file, a corrupt record, a missing timestamp - each results in fewer observations,
which can only make a channel harder to hide, never easier.

What is stored per route is a verdict, a timestamp and a TTL. No URL, no
credential, no host: the route is identified by the caller's route id, and
`prune` drops anything past the locked TTL so the file cannot grow without bound
or resurrect evidence that has expired.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
from typing import Any, Dict, List, Optional

from scanner import route_evidence as rev

DEFAULT_STORE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "state",
    "route-persistence.json",
)

#: Fields kept per observation. Anything else a caller passes is dropped, so a
#: careless caller cannot leak a stream URL into a committed state file.
ALLOWED_OBSERVATION_FIELDS = ("observed_at", "verdict", "kind", "window_seconds")

#: Metric names kept inside `playback_metrics`. These have to survive, because a
#: reset must be RE-VERIFIED from the numbers rather than taken on the stored
#: verdict's word - trusting a caller's claim of "PASS" is the same loose reading
#: of success that let HTTP 200 stand in for working playback. Measured: without
#: them, a genuine full PASS failed to reset the counter at all.
ALLOWED_METRIC_FIELDS = (
    "announced_render_tracks",
    "progressing_tracks",
    "first_frame_seconds",
    "startup_seconds",
    "media_progress_seconds",
    "cumulative_stall_seconds",
    "fatal_errors",
    "recovered_to_pass_floor",
    "max_delivery_gap_seconds",
)

#: A single route cannot accumulate more than this. A runaway loop would
#: otherwise write unboundedly, and beyond a handful the verdict never changes.
MAX_OBSERVATIONS_PER_ROUTE = 40


def _now() -> float:
    return _dt.datetime.now(_dt.timezone.utc).timestamp()


def load(path: Optional[str] = None) -> Dict[str, Any]:
    """Read the store. An unreadable store is an empty store, never an error."""
    target = path or DEFAULT_STORE_PATH
    try:
        with open(target, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return {"version": 1, "routes": {}}
    if not isinstance(payload, dict) or not isinstance(payload.get("routes"), dict):
        return {"version": 1, "routes": {}}
    return payload


def _clean(observation: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(observation, dict):
        return None
    verdict = str(observation.get("verdict") or "")
    if not verdict:
        return None
    kept = {
        field: observation.get(field)
        for field in ALLOWED_OBSERVATION_FIELDS
        if observation.get(field) is not None
    }
    metrics = observation.get("playback_metrics")
    if isinstance(metrics, dict):
        filtered = {
            field: metrics.get(field)
            for field in ALLOWED_METRIC_FIELDS
            if metrics.get(field) is not None
        }
        if filtered:
            kept["playback_metrics"] = filtered
    if "observed_at" not in kept:
        # An observation with no time cannot be placed in a window, and
        # persistence_state ignores it anyway. Dropping it here keeps the file
        # honest about what it holds.
        return None
    kept["verdict"] = verdict
    return kept


def prune(
    store: Dict[str, Any], *, now: Optional[float] = None, ttl_seconds: Optional[float] = None
) -> Dict[str, Any]:
    """Drop expired observations and empty routes."""
    reference = _now() if now is None else now
    ttl = rev.PERSISTENCE_TTL_SECONDS if ttl_seconds is None else float(ttl_seconds)
    routes: Dict[str, Any] = {}
    for route_id, observations in (store.get("routes") or {}).items():
        kept: List[Dict[str, Any]] = []
        for observation in observations or ():
            cleaned = _clean(observation)
            if cleaned is None:
                continue
            moment = rev._parse_observed_at(cleaned.get("observed_at"))
            if moment is None or (reference - moment) > ttl:
                continue
            kept.append(cleaned)
        if kept:
            kept.sort(key=lambda o: rev._parse_observed_at(o.get("observed_at")) or 0.0)
            routes[str(route_id)] = kept[-MAX_OBSERVATIONS_PER_ROUTE:]
    return {"version": 1, "routes": routes}


def record(
    route_id: str,
    observation: Dict[str, Any],
    *,
    path: Optional[str] = None,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Append one observation for a route and return the pruned store.

    Writing is best-effort: if the state file cannot be written the observation
    is lost, which weakens the counter rather than strengthening it.
    """
    cleaned = _clean(observation)
    store = prune(load(path), now=now)
    if cleaned is not None and str(route_id or "").strip():
        store["routes"].setdefault(str(route_id), []).append(cleaned)
        store["routes"][str(route_id)] = store["routes"][str(route_id)][
            -MAX_OBSERVATIONS_PER_ROUTE:
        ]
    target = path or DEFAULT_STORE_PATH
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as handle:
            json.dump(store, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
    except OSError:
        pass
    return store


def state_for(
    route_id: str,
    *,
    path: Optional[str] = None,
    now: Optional[float] = None,
    extra: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """The persistence verdict for one route across every stored run."""
    store = prune(load(path), now=now)
    observations = list(store["routes"].get(str(route_id)) or [])
    if extra:
        observations.extend(o for o in (_clean(e) for e in extra) if o)
    return rev.persistence_state(observations, now=now)
