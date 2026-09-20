"""ধাপ ৫ / A-02 - the controlled metadata and artwork backfill.

The catalogue has ~1,291 unresolved items and the ordinary run spends 150
lookups, so "try everyone once" takes about nine days. The plan's answer is to
raise the ceiling for a night or two and then put it back.

The number is not the point, and the plan says so twice
-------------------------------------------------------
"৬০০–৮০০ একটি **সর্বোচ্চ লক্ষ্য, স্থির সংখ্যা নয়**":

    safe_batch = min(backfill_target,
                     provider_remaining_budget,
                     time_budget)

A fixed 800 is a promise the run cannot keep. It ignores how much provider
quota is actually left, and it ignores how much of the scan's forty minutes
has already gone on verification - so on a slow day it either overruns the
budget or burns an allowance that a later run needed more.

Pause, not fail
---------------
"সব প্রোভাইডার শেষ → queue **pause**, পরের রানে resume." Running out is an
ordinary outcome, not an error: the queue records where it stopped and why,
the catalogue publishes exactly as it would have, and the next run carries on.

Nothing here is a second cache
------------------------------
`movie_metadata_cache.enrich` already does the work - cache first, never-
resolved before refresh, a failure cooldown, and an availability predicate
that abandons rather than records failures when every provider is down. This
module decides *how many* it may do and records *why it stopped*. Re-deriving
what is resolved would be a second answer to a question that already has one.

Preconditions, both met before this ships (ধারা ৭)
--------------------------------------------------
ধাপ ৩খ, or the budget is spent on dirty titles; and ধাপ ৪, or a provider that
answers badly has its answer cached for ninety days - which the plan calls the
harder damage to undo.

Stdlib only.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCHEMA_VERSION = 1

DEFAULT_STATE_PATH = Path("state") / "movie-backfill.json"

SETTINGS_KEY = "movie_metadata_backfill"

#: The ordinary daily ceiling. Unchanged from `movie_metadata_cache`, read
#: from there so there is one number rather than two that can drift.
def _normal_budget() -> int:
    try:
        from scanner.movie_metadata_cache import DEFAULT_LOOKUP_BUDGET

        return int(DEFAULT_LOOKUP_BUDGET)
    except Exception:  # noqa: BLE001
        return 150


#: Measured, not guessed. `movie_metadata_cache`'s own note: a matched title
#: costs about four requests and an unmatched one about six, and the request
#: policy paces at five per second - so a lookup is roughly a second, and 150
#: of them are the "two to three minutes" that note describes.
SECONDS_PER_LOOKUP = 1.0

#: Never spend the whole scan on metadata. Verification is what the catalogue
#: is actually for, and a backfill that pushes the run past its budget gets the
#: run killed rather than the backfill trimmed.
DEFAULT_TIME_SHARE = 0.35

REASON_TARGET = "target_reached"
REASON_PROVIDER_QUOTA = "provider_quota"
REASON_TIME_BUDGET = "time_budget"
REASON_NOTHING_TO_DO = "nothing_to_do"
REASON_DISABLED = "disabled"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=str(path.parent), delete=False,
        prefix=f".{path.name}.", suffix=".tmp",
    )
    try:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    finally:
        handle.close()
    os.replace(handle.name, path)


def load_state(path: Optional[str | Path] = None) -> Dict[str, Any]:
    target = Path(path or DEFAULT_STATE_PATH)
    try:
        with open(target, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return {
        "schema_version": SCHEMA_VERSION,
        "runs": payload.get("runs") or [],
        "paused": bool(payload.get("paused")),
        "pause_reason": payload.get("pause_reason") or "",
        "resolved_total": int(payload.get("resolved_total") or 0),
        "updated_at": payload.get("updated_at") or "",
    }


def save_state(state: Dict[str, Any], path: Optional[str | Path] = None) -> bool:
    target = Path(path or DEFAULT_STATE_PATH)
    payload = dict(state)
    payload["schema_version"] = SCHEMA_VERSION
    payload["updated_at"] = _utc_now()
    # A log nobody prunes becomes the biggest file in state/. Ten runs is
    # enough to see whether a backfill is making progress.
    payload["runs"] = list(payload.get("runs") or [])[-10:]
    try:
        _atomic_write(target, payload)
    except OSError:
        return False
    return True


def settings_block(settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    block = (settings or {}).get(SETTINGS_KEY)
    return dict(block) if isinstance(block, dict) else {}


def provider_remaining_lookups() -> int:
    """How many more lookups the providers between them will actually serve.

    The **maximum** across providers, not the sum: one lookup is answered by
    one provider walking the chain until something replies, so two providers
    with 100 left each do not make 200 lookups possible - they make the run
    survivable when the first one runs out.

    `-1` means at least one provider is unmetered, so quota is not the binding
    constraint.
    """
    try:
        from scanner import provider_health, provider_router
    except Exception:  # noqa: BLE001
        return -1

    best = 0
    for name in provider_router.provider_config():
        if not provider_router.can_answer(
            name, capability=provider_router.CAPABILITY_MOVIE_METADATA
        ):
            continue
        if not provider_health.is_available(name):
            continue
        record = provider_health._provider_record(name)  # noqa: SLF001
        remaining = provider_health.remaining_quota(record)
        if remaining < 0:
            return -1
        best = max(best, remaining)
    return best


def safe_batch(
    *,
    target: int,
    provider_remaining: int,
    time_budget_seconds: float,
    elapsed_seconds: float = 0.0,
    seconds_per_lookup: float = SECONDS_PER_LOOKUP,
    time_share: float = DEFAULT_TIME_SHARE,
) -> Tuple[int, str]:
    """`(lookups, limiting_reason)` - the plan's three-way minimum.

    The reason is returned with the number because "we did 40" and "we did 40
    because OMDb ran out" are different facts, and only the second one tells
    anybody what to change.
    """
    target = max(0, int(target))
    if target == 0:
        return 0, REASON_DISABLED

    allowed = target
    reason = REASON_TARGET

    if provider_remaining >= 0 and provider_remaining < allowed:
        allowed = provider_remaining
        reason = REASON_PROVIDER_QUOTA

    remaining_seconds = max(0.0, float(time_budget_seconds) - float(elapsed_seconds))
    per_lookup = max(0.01, float(seconds_per_lookup))
    affordable = int((remaining_seconds * max(0.0, min(1.0, time_share))) / per_lookup)
    if affordable < allowed:
        allowed = affordable
        reason = REASON_TIME_BUDGET

    allowed = max(0, allowed)
    if allowed == 0 and reason == REASON_TARGET:
        reason = REASON_NOTHING_TO_DO
    return allowed, reason


def plan_run(
    settings: Optional[Dict[str, Any]] = None,
    *,
    elapsed_seconds: float = 0.0,
    provider_remaining: Optional[int] = None,
) -> Dict[str, Any]:
    """How many lookups this run may make, and what limited it.

    With the backfill switched off this returns the ordinary daily ceiling, so
    the normal path is unchanged and the elevated target is opt-in - which is
    what "এক বা দুই রাত চালিয়ে আবার ১৫০-এ ফেরত" means in practice.
    """
    block = settings_block(settings)
    enabled = bool(block.get("enabled", False))
    normal = _normal_budget()

    try:
        target = int(block.get("target_lookups") or 0)
    except (TypeError, ValueError):
        target = 0
    if not enabled or target <= 0:
        target = normal

    try:
        time_budget = float(block.get("time_budget_seconds") or 0)
    except (TypeError, ValueError):
        time_budget = 0.0
    if time_budget <= 0:
        time_budget = _movie_time_budget(settings)

    remaining = (
        provider_remaining
        if provider_remaining is not None
        else provider_remaining_lookups()
    )

    lookups, reason = safe_batch(
        target=target,
        provider_remaining=remaining,
        time_budget_seconds=time_budget,
        elapsed_seconds=elapsed_seconds,
        seconds_per_lookup=float(block.get("seconds_per_lookup") or SECONDS_PER_LOOKUP),
        time_share=float(block.get("time_share") or DEFAULT_TIME_SHARE),
    )
    return {
        "enabled": enabled,
        "target": target,
        "normal_budget": normal,
        "provider_remaining": remaining,
        "time_budget_seconds": time_budget,
        "elapsed_seconds": elapsed_seconds,
        "lookups": lookups,
        "limited_by": reason,
        # Running out is an ordinary outcome, not a failure: the queue pauses
        # and the next run carries on from where the cache left it.
        "paused": lookups == 0 and reason in (
            REASON_PROVIDER_QUOTA, REASON_TIME_BUDGET),
    }


def _movie_time_budget(settings: Optional[Dict[str, Any]]) -> float:
    for section in ("pipeline", "verification"):
        block = (settings or {}).get(section)
        if not isinstance(block, dict):
            continue
        budgets = block.get("time_budget_seconds")
        if isinstance(budgets, dict) and budgets.get("movies"):
            try:
                return float(budgets["movies"])
            except (TypeError, ValueError):
                continue
    return 2400.0


def record_run(
    plan: Dict[str, Any],
    summary: Optional[Dict[str, Any]] = None,
    *,
    path: Optional[str | Path] = None,
) -> Dict[str, Any]:
    """Append this run's outcome, so progress across nights is readable."""
    state = load_state(path)
    entry = {
        "at": _utc_now(),
        "target": plan.get("target"),
        "lookups_allowed": plan.get("lookups"),
        "limited_by": plan.get("limited_by"),
        "provider_remaining": plan.get("provider_remaining"),
    }
    if isinstance(summary, dict):
        for field in ("fetched", "refreshed", "failed", "cached_hits",
                      "skipped_budget", "skipped_unavailable"):
            if field in summary:
                entry[field] = summary[field]
        state["resolved_total"] = int(state.get("resolved_total") or 0) + int(
            summary.get("fetched") or 0)
    state["runs"] = list(state.get("runs") or []) + [entry]
    state["paused"] = bool(plan.get("paused"))
    state["pause_reason"] = plan.get("limited_by") if plan.get("paused") else ""
    save_state(state, path)
    return state


def describe(plan: Dict[str, Any]) -> str:
    """One line for the scan log."""
    if not plan.get("enabled"):
        return (
            f"metadata budget {plan.get('lookups')} lookup(s) "
            f"(ordinary ceiling {plan.get('normal_budget')}, "
            f"limited by {plan.get('limited_by')})"
        )
    return (
        f"metadata BACKFILL: {plan.get('lookups')} lookup(s) of a "
        f"{plan.get('target')} target, limited by {plan.get('limited_by')}"
        + (" - queue paused, resumes next run" if plan.get("paused") else "")
    )
