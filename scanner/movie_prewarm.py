"""ধাপ ১০খ / ধারা ৪.৯ - the metadata stage, run beside stream verification.

The plan's own example of what the structure is for:

> Metadata worker stream verification শেষ হওয়ার জন্য বসে থাকবে না, কারণ
> মেটাডাটা লুকআপের জন্য দরকার টাইটেল ও বছর, যা Stage A-তেই তৈরি।

and its honest accounting of the size of the prize: ~3 minutes in a 40-minute
run today, but a much larger share once ধাপ ৭'s TTL has cut verification from
2,475 links to ~354 - which is exactly why this is ধাপ ১০খ and not ধাপ ১.

**What it warms.** The films already on the site, whose cache record is
missing or past its TTL. Not this scan's newly discovered films: their
identity is only settled after verification, and looking up a title that turns
out not to match would spend quota on nothing. Those keep the ordinary path in
`movie_metadata_cache.enrich`, which now finds most of its work already done.

**How it stays inside the rules** (ধারা ৪.৯):

1.  It never touches the movie list. It returns fragments - identity to
    resolved fields - and `commit` applies them, afterwards, on the scan's
    own thread.
2.  It writes nothing. `commit` writes the cache, and only `commit`.
3.  One worker by default, so the provider pacing in `provider_health` is
    reached by one thread at a time exactly as it is today, and the joint
    `HostBudget` caps it again from outside.
4.  It stops when verification does. Everything it did not reach is reported
    unattempted and keeps whatever it had - a film whose metadata was not
    refreshed is a film with slightly older metadata, never a missing film.

There is no double spend: every provider request goes through
`provider_health`, which decrements the same daily quota the later backfill
plan reads, so warming 60 records leaves 60 fewer in the budget that follows.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional, Sequence

from scanner import movie_stages

#: Off unless settings say otherwise. The gain is real but it is a schedule
#: optimisation, and a scan that runs the old way is never wrong.
DEFAULT_ENABLED = True

#: Records warmed per run. Deliberately a slice rather than the whole budget:
#: a newly discovered film with no metadata at all is worth more than a
#: refresh of one that already reads correctly, and those are found later.
DEFAULT_MAX_LOOKUPS = 60

#: Share of the remaining provider quota this may take, whatever the number
#: above says. Protects the same priority when the quota is nearly spent.
DEFAULT_QUOTA_SHARE = 0.4

#: One thread: `provider_health`'s pacing is per-provider state without a lock,
#: and the point here is to overlap metadata with *verification*, not to make
#: metadata itself concurrent. Raising this would need that lock first.
DEFAULT_WORKERS = 1

#: A ceiling in case verification runs very long. The stage is stopped when
#: verification finishes anyway; this is what stops it if that never happens.
DEFAULT_MAX_SECONDS = 900

STAGE_NAME = "metadata"


def _load_settings(path: str = "config/settings.json") -> Dict[str, Any]:
    import json

    try:
        with open(path, "r", encoding="utf-8") as handle:
            loaded = json.load(handle)
        return loaded if isinstance(loaded, dict) else {}
    except (OSError, ValueError):
        return {}


def settings_for(settings: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if settings is None:
        settings = _load_settings()
    block = (settings or {}).get("movie_parallel_stages")
    if not isinstance(block, dict):
        block = {}
    return {
        "enabled": bool(block.get("enabled", DEFAULT_ENABLED)),
        "max_lookups": max(0, int(block.get("max_lookups", DEFAULT_MAX_LOOKUPS) or 0)),
        "quota_share": min(1.0, max(0.0, float(
            block.get("quota_share", DEFAULT_QUOTA_SHARE) or 0.0))),
        "workers": max(1, int(block.get("workers", DEFAULT_WORKERS) or 1)),
        "max_seconds": max(1, int(block.get("max_seconds", DEFAULT_MAX_SECONDS) or 1)),
        "host_limit": max(1, int(
            block.get("host_limit", movie_stages.DEFAULT_HOST_LIMIT) or 1)),
    }


def published_cards(root: Any) -> List[Dict[str, Any]]:
    """The films the site is serving right now, read off disk.

    The same reader the no-loss gate's "before" side uses, so the two cannot
    disagree about what is published. Deliberately not this scan's candidates:
    their identity is only settled after verification, and a lookup on a title
    that turns out not to match spends quota on nothing.
    """
    from scanner import movie_baseline

    try:
        return [card for _slug, card in movie_baseline.published_movies(root)]
    except Exception:  # noqa: BLE001 - a warm-up never fails a scan
        return []


def plan(
    cards: Sequence[Dict[str, Any]],
    settings: Optional[Dict[str, Any]] = None,
    *,
    now=None,
    cache_path: Optional[str] = None,
    remaining_quota: Optional[int] = None,
) -> Dict[str, Any]:
    """Decide what to warm, and how much of it.

    Returns the plan whether or not it is worth running, so the reason is
    always printable: "disabled", "nothing due", "no quota" are all answers.
    """
    from scanner import movie_metadata_cache

    policy = settings_for(settings)
    report: Dict[str, Any] = {
        "enabled": policy["enabled"],
        "workers": policy["workers"],
        "max_seconds": policy["max_seconds"],
        "host_limit": policy["host_limit"],
        "identities": [],
        "considered": 0,
        "missing": 0,
        "due": 0,
        "fresh": 0,
        "cooldown": 0,
        "budget": 0,
        "reason": "",
    }
    if not policy["enabled"]:
        report["reason"] = "disabled in settings"
        return report

    store = movie_metadata_cache.load(cache_path)
    records = store.get("movies") if isinstance(store, dict) else None
    records = records if isinstance(records, dict) else {}

    seen: Dict[str, Dict[str, Any]] = {}
    for card in cards or ():
        if not isinstance(card, dict):
            continue
        if not _titled(card):
            # The premise of this stage is that a lookup needs a title and a
            # year, both settled in Stage A. A card with no title has neither,
            # and `canonical_identity` still answers - "fallback::0" - so
            # without this the warm-up spends quota asking about nothing.
            continue
        identity = movie_metadata_cache.canonical_identity(card)
        if not identity or identity in seen:
            continue
        report["considered"] += 1
        state = movie_metadata_cache.lookup_state(records.get(identity), now)
        report[state] = report.get(state, 0) + 1
        if state in (movie_metadata_cache.LOOKUP_MISSING,
                     movie_metadata_cache.LOOKUP_DUE):
            seen[identity] = card

    if remaining_quota is None:
        remaining_quota = _remaining_quota()
    allowed = policy["max_lookups"]
    if remaining_quota is not None:
        allowed = min(allowed, int(remaining_quota * policy["quota_share"]))
    report["budget"] = max(0, allowed)

    # Missing first, then due-for-refresh - the same priority `enrich` uses,
    # for the same reason: an entry with nothing is worth more than a refresh
    # of one that already reads correctly.
    ordered = sorted(
        seen.items(),
        key=lambda pair: (
            movie_metadata_cache.lookup_state(records.get(pair[0]), now)
            != movie_metadata_cache.LOOKUP_MISSING,
            pair[0],
        ),
    )
    report["identities"] = [
        {"identity": identity, "card": card}
        for identity, card in ordered[: report["budget"]]
    ]
    report["deferred"] = max(0, len(ordered) - len(report["identities"]))
    if not report["identities"]:
        report["reason"] = (
            "nothing due" if report["budget"] else "no lookup budget")
    return report


def _titled(card: Dict[str, Any]) -> bool:
    for field in ("name", "title", "clean_title", "imdb_id", "tmdb_id"):
        if str(card.get(field) or "").strip():
            return True
    return False


def _remaining_quota() -> Optional[int]:
    try:
        from scanner import movie_backfill

        return movie_backfill.provider_remaining_lookups()
    except Exception:  # noqa: BLE001
        return None


def stage(plan_report: Dict[str, Any], *, lookup=None) -> movie_stages.Stage:
    """The Stage B unit. Returns fragments; writes nothing."""
    entries = list((plan_report or {}).get("identities") or ())

    def run(context: movie_stages.StageContext) -> movie_stages.StageResult:
        resolve = lookup
        available = None
        if resolve is None:
            from scanner import metadata_providers
            from scanner import provider_health

            resolve = metadata_providers.resolve_metadata
            available = provider_health.any_metadata_provider_available

        def work(entry):
            if available is not None and not available():
                # Every provider is cooling down. Returning nothing here would
                # be recorded as a failed attempt and earn this film a
                # seven-day cooldown it never deserved, so say so instead.
                return {"unavailable": True}
            try:
                return {"resolved": resolve(dict(entry["card"]))}
            except Exception:  # noqa: BLE001 - one film, not the stage
                # Reached and answered nothing, which is the same fact as a
                # provider replying "no match": the attempt happened. Letting
                # this escape would make it indistinguishable from an outage,
                # and an outage means "never tried".
                return {"resolved": None}

        outcomes, unattempted = context.map(
            work, entries, key=lambda entry: entry["identity"])

        fragments: Dict[str, Dict[str, Any]] = {}
        unavailable: List[str] = []
        resolved = 0
        unresolved = 0
        for identity, outcome in outcomes:
            if not isinstance(outcome, dict) or outcome.get("unavailable"):
                unavailable.append(identity)
                continue
            fields = outcome.get("resolved")
            if fields:
                fragments[identity] = dict(fields)
                resolved += 1
            else:
                # Reached, answered nothing. Recorded so `commit` can note the
                # attempt, which is what the retry cooldown is counted from.
                fragments[identity] = {}
                unresolved += 1

        # A provider outage is not work this stage skipped by choice, so those
        # identities join the unattempted list: rule 4 keeps their state.
        return context.result(
            fragments=fragments,
            unattempted=list(unattempted) + unavailable,
            attempted=len(outcomes) - len(unavailable),
            note=(f"{resolved} resolved, {unresolved} unresolved"
                  + (f", {len(unavailable)} no provider" if unavailable else "")),
        )

    policy_workers = int((plan_report or {}).get("workers") or DEFAULT_WORKERS)
    return movie_stages.Stage(
        STAGE_NAME, run, workers=policy_workers, payload=entries)


def commit(
    report: Dict[str, Any],
    plan_report: Dict[str, Any],
    *,
    cache_path: Optional[str] = None,
    now=None,
) -> Dict[str, Any]:
    """Stage C, the only writer: fragments into the metadata cache.

    Runs on the scan's own thread once every stage has finished, so the cache
    is written by exactly one thread - and a fragment that arrived is written
    whether or not the stage that produced it finished everything it was given.
    """
    from scanner import movie_metadata_cache

    merged = (report or {}).get("merged") or {}
    cards = {
        entry["identity"]: entry["card"]
        for entry in (plan_report or {}).get("identities") or ()
        if isinstance(entry, dict)
    }
    written = 0
    resolved = 0
    if merged:
        store = movie_metadata_cache.load(cache_path)
        for identity in sorted(merged):
            fields = merged.get(identity) or None
            movie_metadata_cache.upsert(
                store, identity, fields or None, now=now,
                local=cards.get(identity),
            )
            written += 1
            if fields:
                resolved += 1
        movie_metadata_cache.save(store, cache_path)
    return {
        "written": written,
        "resolved": resolved,
        "not_reached": len((report or {}).get("not_reached") or ()),
    }


class Handle:
    """A running prewarm. Started before verification, joined after it."""

    def __init__(self, plan_report: Dict[str, Any], thread, halt) -> None:
        self.plan = plan_report
        self.thread = thread
        self.halt = halt
        self.results: Dict[str, movie_stages.StageResult] = {}
        self.report: Dict[str, Any] = {}

    @property
    def started(self) -> bool:
        return self.thread is not None


def start(
    cards: Sequence[Dict[str, Any]],
    settings: Optional[Dict[str, Any]] = None,
    *,
    lookup=None,
    cache_path: Optional[str] = None,
    **plan_kwargs,
) -> Handle:
    """Begin the metadata stage. Never raises; a failure means no warm-up."""
    try:
        plan_report = plan(cards, settings, cache_path=cache_path, **plan_kwargs)
    except Exception as error:  # noqa: BLE001 - never fail a scan
        return Handle({"enabled": False, "reason": f"plan failed: {error}"},
                      None, None)
    if not plan_report.get("identities"):
        return Handle(plan_report, None, None)

    halt = threading.Event()
    budget = movie_stages.HostBudget(default=plan_report["host_limit"])
    deadline = time.monotonic() + plan_report["max_seconds"]
    handle = Handle(plan_report, None, halt)

    def coordinate():
        # The coordinator, on its own thread. Stages never wait on stages;
        # this thread is what the scan waits on, once, after verification.
        handle.results = movie_stages.run_stages(
            [stage(plan_report, lookup=lookup)],
            budget=budget, deadline=deadline, halt=halt,
        )

    thread = threading.Thread(
        target=coordinate, name="movie-prewarm", daemon=True)
    handle.thread = thread
    thread.start()
    return handle


def finish(
    handle: Optional[Handle],
    *,
    cache_path: Optional[str] = None,
    now=None,
    timeout: float = 60.0,
) -> Dict[str, Any]:
    """Stop the stage, wait for what is in flight, write what came back."""
    if handle is None or not handle.started:
        return {"ran": False,
                "reason": (handle.plan.get("reason") if handle else "not started")}
    handle.halt.set()
    handle.thread.join(timeout=timeout)
    if handle.thread.is_alive():
        # A provider that never answers must not hold the publish. What it
        # would have written is simply not written - rule 4 again.
        return {"ran": True, "abandoned": True,
                "reason": "stage still running at the join deadline"}
    handle.report = movie_stages.aggregate(handle.results, order=[STAGE_NAME])
    written = commit(handle.report, handle.plan, cache_path=cache_path, now=now)
    result = (handle.results or {}).get(STAGE_NAME)
    return {
        "ran": True,
        "status": result.status if result is not None else movie_stages.FAILED,
        "note": result.note if result is not None else "",
        "seconds": round(result.seconds, 1) if result is not None else 0.0,
        "considered": handle.plan.get("considered", 0),
        "planned": len(handle.plan.get("identities") or ()),
        "deferred": handle.plan.get("deferred", 0),
        **written,
    }


def describe(summary: Dict[str, Any]) -> str:
    """One line for the scan log."""
    if not summary:
        return ""
    if not summary.get("ran"):
        reason = summary.get("reason") or "not run"
        return f"movie metadata prewarm: skipped ({reason})"
    if summary.get("abandoned"):
        return f"movie metadata prewarm: {summary.get('reason')}"
    line = (
        f"movie metadata prewarm: {summary.get('resolved', 0)} resolved of "
        f"{summary.get('planned', 0)} planned in {summary.get('seconds', 0)}s "
        f"alongside verification"
    )
    if summary.get("not_reached"):
        line += (
            f" | {summary['not_reached']} not reached - previous metadata kept"
        )
    if summary.get("deferred"):
        line += f" | {summary['deferred']} left for the ordinary pass"
    return line
