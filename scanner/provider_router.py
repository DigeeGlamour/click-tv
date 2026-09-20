"""ধারা ৪.৭ - which provider to ask, in what order, and when to stop.

The chain in `metadata_providers.resolve_metadata` was written as a fixed
sequence: TMDB, then OMDb, then Cinemeta, and so on. The plan is explicit that
this sequence is a *description of what the code does today, not an
instruction* - "কোনো প্রোভাইডারের অবস্থান স্থির নয়" - and that the real order has
to come from capability, quota, health and confidence.

So the order lives here, and it is computed per question:

    order(capability="artwork", kind="movie", have={"tmdb_id": 42})

Four things decide it, in this precedence:

  1. **Capability.** A provider that cannot answer this question is not in the
     list at all. Fanart returns artwork and nothing else; TVMaze knows
     television; AniList knows anime; Cinemeta needs an imdb_id to say
     anything. Those are not preferences, they are facts about the provider,
     and asking anyway spends quota to be told nothing.
  2. **Availability.** Cooling down, key-broken, breaker open, or out of daily
     budget - excluded, not demoted. `provider_health` owns that judgement.
  3. **Weight.** A number per provider in config/settings.json, so the owner
     can change the order without changing code. The defaults reproduce
     today's sequence exactly, which is what makes this change safe to land:
     nothing moves until somebody edits the config.
  4. **Measured behaviour.** Among equal weights, more remaining quota first,
     then lower average latency. This is the part that makes the order
     genuinely dynamic - a provider that has been slow all run drifts down on
     its own.

What this module does not do
----------------------------
It does not make requests. `provider_health.request_json` still owns every
outbound call, its pacing, its 429 handling and its breaker. This decides who
to ask.

It does not ask everyone at once. ধারা ৪.৭: "একই আইটেমের জন্য সব প্রোভাইডার
সমান্তরালে কল করা যাবে না — কোটা নষ্ট হবে." The order is a sequence to walk
until something answers well enough, not a fan-out.

Stdlib only.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

CAPABILITY_MOVIE_METADATA = "movie_metadata"
CAPABILITY_SERIES_METADATA = "series_metadata"
CAPABILITY_ARTWORK = "artwork"

CAPABILITIES = (
    CAPABILITY_MOVIE_METADATA,
    CAPABILITY_SERIES_METADATA,
    CAPABILITY_ARTWORK,
)

KIND_MOVIE = "movie"
KIND_SERIES = "series"
KIND_ANIME = "anime"

#: What each provider can actually answer, and what it needs to be asked.
#:
#: `weight` reproduces the existing hard-coded order exactly - TMDB 10, OMDb
#: 20, Cinemeta 30, MoviesDatabase 40, Fanart 50, TVMaze 60, AniList 70 - so
#: landing the router changes no behaviour at all until the config says
#: otherwise. A migration that quietly reorders seven providers on the day it
#: ships is not one anybody can review.
#:
#: `requires` are the inputs without which the provider has nothing to work
#: from: Cinemeta is an imdb_id lookup, Fanart is a tmdb_id lookup. Calling
#: them without those is a request that can only fail.
DEFAULT_PROVIDERS: Dict[str, Dict[str, Any]] = {
    "tmdb": {
        "weight": 10,
        "capabilities": [CAPABILITY_MOVIE_METADATA, CAPABILITY_SERIES_METADATA,
                         CAPABILITY_ARTWORK],
        "kinds": [KIND_MOVIE, KIND_SERIES, KIND_ANIME],
        "requires": [],
    },
    "omdb": {
        "weight": 20,
        "capabilities": [CAPABILITY_MOVIE_METADATA, CAPABILITY_SERIES_METADATA],
        "kinds": [KIND_MOVIE, KIND_SERIES, KIND_ANIME],
        "requires": [],
        # ধারা ৪.৭ - "OMDb: ফ্রি কোটার একটি অংশ জরুরি fallback-এর জন্য সংরক্ষিত".
        "daily_soft_budget": 900,
        "reserved_for_fallback": 100,
    },
    "cinemeta": {
        "weight": 30,
        "capabilities": [CAPABILITY_MOVIE_METADATA, CAPABILITY_SERIES_METADATA,
                         CAPABILITY_ARTWORK],
        "kinds": [KIND_MOVIE, KIND_SERIES, KIND_ANIME],
        "requires": ["imdb_id"],
    },
    "moviesdatabase": {
        "weight": 40,
        "capabilities": [CAPABILITY_MOVIE_METADATA, CAPABILITY_ARTWORK],
        "kinds": [KIND_MOVIE, KIND_SERIES, KIND_ANIME],
        "requires": [],
    },
    "fanart": {
        "weight": 50,
        "capabilities": [CAPABILITY_ARTWORK],
        "kinds": [KIND_MOVIE, KIND_SERIES, KIND_ANIME],
        "requires": ["tmdb_id"],
    },
    "tvmaze": {
        "weight": 60,
        "capabilities": [CAPABILITY_SERIES_METADATA, CAPABILITY_ARTWORK],
        "kinds": [KIND_SERIES],
        "requires": [],
    },
    "anilist": {
        "weight": 70,
        "capabilities": [CAPABILITY_SERIES_METADATA, CAPABILITY_ARTWORK],
        "kinds": [KIND_ANIME],
        "requires": [],
    },
}

DEFAULT_SETTINGS_PATH = "config/settings.json"
SETTINGS_KEY = "metadata_providers"


def _load_settings(settings_path: Optional[str | Path] = None) -> Dict[str, Any]:
    path = Path(settings_path or DEFAULT_SETTINGS_PATH)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    block = payload.get(SETTINGS_KEY)
    return block if isinstance(block, dict) else {}


def provider_config(
    settings_path: Optional[str | Path] = None,
) -> Dict[str, Dict[str, Any]]:
    """The provider registry, defaults merged with config/settings.json.

    Merged field by field rather than replaced wholesale: an owner changing one
    provider's weight must not have to restate that provider's capabilities,
    and a capability list silently emptied by a partial config edit is how a
    provider stops being asked without anybody noticing.
    """
    merged: Dict[str, Dict[str, Any]] = {
        name: dict(spec) for name, spec in DEFAULT_PROVIDERS.items()
    }
    configured = _load_settings(settings_path).get("providers")
    if not isinstance(configured, dict):
        return merged
    for name, override in configured.items():
        if not isinstance(override, dict):
            continue
        spec = merged.setdefault(str(name), {
            "weight": 100, "capabilities": [], "kinds": [], "requires": [],
        })
        for field, value in override.items():
            spec[str(field)] = value
    return merged


def _health_record(provider: str) -> Dict[str, Any]:
    try:
        from scanner import provider_health

        state = provider_health._state()  # noqa: SLF001 - same package
        record = (state.get("providers") or {}).get(provider)
        return record if isinstance(record, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def apply_budgets(settings_path: Optional[str | Path] = None) -> Dict[str, int]:
    """Push the configured daily budgets into the health file.

    The budget belongs in configuration and the spend belongs in state, so this
    copies one into the other at the start of a run. Without it a budget change
    would take effect only for providers that happened to have no record yet.
    """
    applied: Dict[str, int] = {}
    try:
        from scanner import provider_health
    except Exception:  # noqa: BLE001
        return applied
    for name, spec in provider_config(settings_path).items():
        budget = spec.get("daily_soft_budget")
        if budget in (None, ""):
            continue
        record = provider_health._provider_record(name)  # noqa: SLF001
        try:
            record["daily_soft_budget"] = int(budget)
        except (TypeError, ValueError):
            continue
        reserve = spec.get("reserved_for_fallback")
        if reserve not in (None, ""):
            try:
                record["reserved_for_fallback"] = int(reserve)
            except (TypeError, ValueError):
                pass
        applied[name] = int(budget)
    return applied


def can_answer(
    provider: str,
    *,
    capability: str,
    kind: str = KIND_MOVIE,
    have: Optional[Dict[str, Any]] = None,
    config: Optional[Dict[str, Dict[str, Any]]] = None,
) -> bool:
    """Whether this provider could answer this question at all.

    A fact about the provider, not a preference: asking Fanart for a synopsis
    or Cinemeta for anything without an imdb_id spends a request to be told
    nothing.
    """
    spec = (config or provider_config()).get(provider)
    if not isinstance(spec, dict):
        return False
    capabilities = spec.get("capabilities") or []
    if capability not in capabilities:
        return False
    kinds = spec.get("kinds") or []
    if kinds and kind not in kinds:
        return False
    present = have or {}
    for field in spec.get("requires") or ():
        value = present.get(field)
        if value in (None, "", 0, [], {}):
            return False
    return True


def available(provider: str, *, reserved: bool = False) -> bool:
    """Whether this provider may be called right now."""
    try:
        from scanner import provider_health

        if not provider_health.is_available(provider):
            return False
        record = _health_record(provider)
        return provider_health.remaining_quota(record, reserved=reserved) != 0
    except Exception:  # noqa: BLE001 - a health-check failure must not
        # silently remove every provider; that would turn an unreadable state
        # file into "all APIs down" and strand the catalogue on pending flags.
        return True


def _sort_key(provider: str, spec: Dict[str, Any]) -> Any:
    record = _health_record(provider)
    try:
        weight = int(spec.get("weight", 100))
    except (TypeError, ValueError):
        weight = 100
    try:
        from scanner import provider_health

        remaining = provider_health.remaining_quota(record)
        budget = provider_health.daily_soft_budget(record)
    except Exception:  # noqa: BLE001
        remaining, budget = -1, 0

    # A bucket, not the raw count. Comparing counts made "unmetered" beat every
    # metered provider outright - an infinite headroom always sorts first - so
    # latency could never decide anything, which is half the reason the plan
    # asks for a dynamic order at all. Three buckets is all the resolution this
    # question has: plenty, running low, none.
    if remaining < 0 or budget <= 0:
        headroom = 0          # unmetered
    elif remaining == 0:
        headroom = 2
    elif remaining <= max(1, budget // 4):
        headroom = 1          # under a quarter left
    else:
        headroom = 0

    try:
        latency = float(record.get("average_latency_ms") or 0.0)
    except (TypeError, ValueError):
        latency = 0.0
    return (weight, headroom, latency, provider)


def order(
    *,
    capability: str,
    kind: str = KIND_MOVIE,
    have: Optional[Dict[str, Any]] = None,
    reserved: bool = False,
    include_unavailable: bool = False,
    config: Optional[Dict[str, Dict[str, Any]]] = None,
) -> List[str]:
    """The providers to ask, best first. May be empty - that is an answer too.

    An empty list means every provider that could have answered is unavailable,
    which is ধারা ৪.৭'s "all APIs down" case: the caller publishes the movie
    with `metadata_pending` rather than dropping it.
    """
    registry = config or provider_config()
    candidates = [
        name for name in registry
        if can_answer(name, capability=capability, kind=kind, have=have,
                      config=registry)
    ]
    if not include_unavailable:
        candidates = [
            name for name in candidates if available(name, reserved=reserved)
        ]
    return sorted(candidates, key=lambda name: _sort_key(name, registry[name]))


def any_available(
    *,
    capability: str = CAPABILITY_MOVIE_METADATA,
    kind: str = KIND_MOVIE,
    have: Optional[Dict[str, Any]] = None,
) -> bool:
    """Is there anyone left to ask? The gate ধারা ৪.৭'s pending flags read."""
    return bool(order(capability=capability, kind=kind, have=have))


# ---------------------------------------------------------------------------
# ধারা ৪.৭ - the pending marks
# ---------------------------------------------------------------------------

PENDING_METADATA = "metadata_pending"
PENDING_ARTWORK = "artwork_pending"


def mark_pending(movie: Dict[str, Any]) -> Dict[str, bool]:
    """Flag what a movie is still waiting for. Never removes anything.

    ধারা ৪.৭'s second unbreakable condition: metadata enrichment is not a
    condition of publishing. A new film whose every provider is down is
    published with the source-cleaned title, its category, its stream and a
    placeholder - carrying these two marks - and is filled in when the
    providers come back.

    The marks are cleared the moment the field arrives, so they describe the
    present rather than accumulating a history of what was once missing.
    """
    if not isinstance(movie, dict):
        return {}
    has_metadata = any(
        movie.get(field) not in (None, "", [], {})
        for field in ("tmdb_id", "imdb_id", "genres", "rating", "overview")
    )
    has_artwork = any(
        str(movie.get(field) or "").strip()
        for field in ("logo", "poster", "backdrop")
    )
    marks = {
        PENDING_METADATA: not has_metadata,
        PENDING_ARTWORK: not has_artwork,
    }
    for field, pending in marks.items():
        if pending:
            movie[field] = True
        else:
            movie.pop(field, None)
    return marks


def mark_all_pending(movies: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    counts = {PENDING_METADATA: 0, PENDING_ARTWORK: 0}
    for movie in movies or ():
        marks = mark_pending(movie)
        for field, pending in marks.items():
            if pending:
                counts[field] += 1
    return counts


def snapshot(settings_path: Optional[str | Path] = None) -> Dict[str, Any]:
    """Router state for a scan report - who is up, who is spent, how fast.

    A-06 asks for provider quota in the run report, and a number nobody can
    see is a number nobody acts on.
    """
    registry = provider_config(settings_path)
    rows = []
    for name in sorted(registry):
        record = _health_record(name)
        try:
            from scanner import provider_health

            remaining = provider_health.remaining_quota(record)
        except Exception:  # noqa: BLE001
            remaining = -1
        rows.append({
            "provider": name,
            "status": record.get("status") or "healthy",
            "available": available(name),
            "weight": registry[name].get("weight"),
            "capabilities": list(registry[name].get("capabilities") or ()),
            "daily_soft_budget": record.get("daily_soft_budget", 0),
            "reserved_for_fallback": record.get("reserved_for_fallback", 0),
            "spent_today": record.get("spent_today", 0),
            "remaining_quota": remaining,
            "reset_at": record.get("reset_at", ""),
            "cooldown_until": record.get("next_retry_at", ""),
            "breaker_open_until": record.get("breaker_open_until", ""),
            "consecutive_failures": record.get("consecutive_failures", 0),
            "average_latency_ms": record.get("average_latency_ms", 0),
            "last_http_status": record.get("last_http_status", 0),
        })
    return {
        "providers": rows,
        "any_movie_metadata_available": any_available(),
        "any_artwork_available": any_available(
            capability=CAPABILITY_ARTWORK, have={"tmdb_id": 1, "imdb_id": "tt1"}),
    }
