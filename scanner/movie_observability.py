"""ধাপ ১৩ / A-06 - the nine numbers that say whether the plan is working.

    প্রতি রানে গণনা: skipped_fresh · due_verified · repair_attempted ·
    series_signal_candidates · classification_cache_hits · confirmed_series ·
    classification_conflicts · season_only_unknown_episode ·
    provider quota remaining
    ► ছাড়া বোঝা যাবে না পরিকল্পনা কাজ করছে কিনা।

Most of these already existed after ধাপ ৩–৭, under their own names, printed
in their own lines. What did not exist is **one place that answers the
question**, and A-06 is that question: a reader should be able to tell from a
single block whether ধাপ ৭'s TTL is actually skipping work, whether the
classification cache is actually saving lookups, and how close the providers
are to their quota.

So this module counts almost nothing itself. It **collects**, from the state
the existing phases already write, and it reports a counter as *unmeasured*
rather than as zero when a run did not produce it. A zero and a blank mean
opposite things here: "the TTL skipped nothing" is a problem, "the TTL did not
run" is not.

The recorder is process-local and deliberately so. Threading nine counters
through the pipeline's signatures would touch every function between here and
there, on a live production path, to carry numbers that are diagnostics.
"""
from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional

#: The nine A-06 asks for, in the order the plan lists them.
COUNTERS = (
    "skipped_fresh",
    "due_verified",
    "repair_attempted",
    "series_signal_candidates",
    "classification_cache_hits",
    "confirmed_series",
    "classification_conflicts",
    "season_only_unknown_episode",
)

#: Not a counter - a per-provider mapping, collected separately.
QUOTA_KEY = "provider_quota_remaining"

_LOCK = threading.Lock()
_COUNTS: Dict[str, int] = {}


def reset() -> None:
    """Start a run. Also what tests call, so one test cannot see another's."""
    with _LOCK:
        _COUNTS.clear()


def note(key: str, value: int = 1) -> None:
    """Add to a counter. Creating it in the process marks it as measured."""
    if not key:
        return
    try:
        amount = int(value)
    except (TypeError, ValueError):
        return
    with _LOCK:
        _COUNTS[key] = _COUNTS.get(key, 0) + amount


def set_value(key: str, value: int) -> None:
    """Record a count that is known outright rather than accumulated."""
    if not key:
        return
    try:
        amount = int(value)
    except (TypeError, ValueError):
        return
    with _LOCK:
        _COUNTS[key] = amount


def measured() -> Dict[str, int]:
    with _LOCK:
        return dict(_COUNTS)


def provider_quota(settings_path: Optional[str] = None) -> Dict[str, Any]:
    """What each provider has left today, from ধারা ৪.৭'s own accounting."""
    try:
        from scanner import provider_router

        snapshot = provider_router.snapshot(settings_path=settings_path)
    except Exception:  # noqa: BLE001 - a diagnostic never fails a scan
        return {}
    remaining: Dict[str, Any] = {}
    for entry in (snapshot or {}).get("providers") or ():
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("provider") or "").strip()
        if not name:
            continue
        value = entry.get("remaining_quota")
        # -1 is provider_health's word for "no daily ceiling". Reporting it as
        # a number would read as a provider one call from exhaustion, which is
        # the opposite of what it means.
        remaining[name] = "unmetered" if value is None or int(value) < 0 else int(value)
    return remaining


def collect(settings_path: Optional[str] = None) -> Dict[str, Any]:
    """The A-06 block. Every counter present; unmeasured ones are None."""
    counts = measured()
    block: Dict[str, Any] = {
        key: counts.get(key) if key in counts else None for key in COUNTERS
    }
    block[QUOTA_KEY] = provider_quota(settings_path)
    return block


def unmeasured(block: Dict[str, Any]) -> List[str]:
    """Which of the nine this run did not produce - itself a finding."""
    return [key for key in COUNTERS if (block or {}).get(key) is None]


def describe(block: Dict[str, Any]) -> List[str]:
    """The block as lines for the scan log, one subject per line."""
    if not block:
        return []

    def shown(key: str) -> str:
        value = block.get(key)
        return "not measured" if value is None else str(value)

    lines = [
        "   A-06 link health:  {} skipped fresh, {} verified because due".format(
            shown("skipped_fresh"), shown("due_verified")),
        "   A-06 repair:       {} attempted".format(shown("repair_attempted")),
        "   A-06 series:       {} signal(s), {} with an explicit episode, "
        "{} season-only".format(
            shown("series_signal_candidates"), shown("confirmed_series"),
            shown("season_only_unknown_episode")),
        "   A-06 classification cache: {} hit(s), {} conflict(s)".format(
            shown("classification_cache_hits"),
            shown("classification_conflicts")),
    ]
    quota = block.get(QUOTA_KEY) or {}
    if quota:
        lines.append(
            "   A-06 provider quota: "
            + ", ".join(f"{name} {value}" for name, value in sorted(quota.items()))
        )
    missing = unmeasured(block)
    if missing:
        # Said out loud rather than left to be inferred from a blank: a
        # counter that stopped being produced is how a phase silently stops
        # working, which is the failure A-06 exists to catch.
        lines.append(
            "   A-06 not measured this run: " + ", ".join(missing)
            + " (a counter that disappears is itself a finding)"
        )
    return lines
