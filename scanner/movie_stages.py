"""ধাপ ১০খ / ধারা ৪.৯ - dependency-aware parallel stages.

    "Parallel where independent, wait only where there is a real dependency."

v2.0 dropped the nine-worker DAG. That was right about **nine GitHub Actions
jobs** and wrong about the idea underneath it: inside one run, work that does
not depend on other work has no reason to queue. Metadata lookups need a title
and a year, both of which Stage A already produced - so metadata has nothing to
wait for while stream verification runs.

    STAGE A (sequential - everything else is born here)
        source fetch -> delta / identity

    STAGE B (parallel, each with its own bounded pool)
        stream verify | series classification | metadata | artwork | repair

    STAGE C (single, sequential)
        aggregator -> no-loss validation -> atomic publish

This module is the coordinator, not the stages. It exists because the four
rules below are the whole difficulty of the feature, and rules enforced in one
place are rules that hold:

1.  **No stage touches shared data.** Today's code annotates in place
    (`_annotate_recency(prepared)`), and two of those running at once on one
    dict is a race. A stage here returns a *fragment* - content_id to changed
    fields - and `aggregate` merges, alone, in a fixed order.

2.  **One writer.** No stage writes or pushes production JSON. Stage C does.

3.  **Each stage has its own bounded pool, and shared hosts share a limit.**
    Stream verify's per-host 4 and metadata's 5/s must not become 9/s because
    they were asked at the same time. `HostBudget` is held by the coordinator
    and passed to every stage, so the limit is joint rather than per-stage.

4.  **A partial stage never means "missing".** ★ A stage that runs out of time
    or budget says so, and the aggregator leaves the items it never reached
    exactly as they were. Without this a slow run would tell the No-Loss gate
    that live content had vanished - blocking the publish at best, publishing
    a half-built catalogue at worst.

And the deadlock rule that makes rule 1 possible: with `concurrent.futures`,
a worker that waits on another worker's future can deadlock. **No stage here
can wait on another stage**, because a stage is never handed a future - the
coordinator resolves dependencies and passes finished results in.
"""
from __future__ import annotations

import concurrent.futures
import threading
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

#: A stage ran everything it was given.
COMPLETE = "complete"
#: A stage stopped early - deadline, budget, or an unavailable provider. What
#: it did not reach is named, and rule 4 applies to it.
PARTIAL = "partial"
#: A stage raised. Treated exactly like a partial that reached nothing: a
#: crashed enricher is a reason to publish without enrichment, never a reason
#: to publish a catalogue with things removed from it.
FAILED = "failed"

#: Per-stage worker ceiling. Deliberately small: the work is I/O-bound, and the
#: real limits are the per-host and per-provider ones below, not this.
DEFAULT_WORKERS = 4

#: Joint per-host ceiling, shared by every stage at once. The number matches
#: the verifier's existing per_host_limit, because it is the same politeness
#: rule about the same hosts.
DEFAULT_HOST_LIMIT = 4


def host_of(url: Any) -> str:
    """The host a piece of work will hit, or "" when it is local work."""
    text = str(url or "").strip()
    if not text:
        return ""
    try:
        return (urlparse(text).hostname or "").lower()
    except ValueError:
        return ""


class HostBudget:
    """One ceiling per host, shared across every stage running at once.

    Rule 3. Two stages that both reach `api.themoviedb.org` hold one limit
    between them; without this the ceilings add up, which is the failure the
    rule is written about. Unknown/local work is never throttled.
    """

    def __init__(
        self,
        *,
        default: int = DEFAULT_HOST_LIMIT,
        limits: Optional[Dict[str, int]] = None,
    ) -> None:
        self._default = max(1, int(default))
        self._limits = {
            str(host).lower(): max(1, int(limit))
            for host, limit in (limits or {}).items()
        }
        self._lock = threading.Lock()
        self._gates: Dict[str, threading.BoundedSemaphore] = {}
        #: Diagnostics only - what the run actually did, for the report.
        self.waits: Dict[str, int] = {}

    def limit_for(self, host: str) -> int:
        return self._limits.get(str(host).lower(), self._default)

    def _gate(self, host: str) -> threading.BoundedSemaphore:
        key = str(host).lower()
        with self._lock:
            gate = self._gates.get(key)
            if gate is None:
                gate = threading.BoundedSemaphore(self.limit_for(key))
                self._gates[key] = gate
            return gate

    class _Hold:
        def __init__(self, gate, budget, host) -> None:
            self._gate = gate
            self._budget = budget
            self._host = host

        def __enter__(self):
            if self._gate is not None:
                if not self._gate.acquire(blocking=False):
                    with self._budget._lock:
                        self._budget.waits[self._host] = (
                            self._budget.waits.get(self._host, 0) + 1
                        )
                    self._gate.acquire()
            return self

        def __exit__(self, *_exc) -> bool:
            if self._gate is not None:
                self._gate.release()
            return False

    def hold(self, host: Any):
        """Hold a slot for `host` for the duration of a `with` block."""
        key = str(host or "").strip().lower()
        if not key:
            return HostBudget._Hold(None, self, "")
        return HostBudget._Hold(self._gate(key), self, key)


class StageResult:
    """What a stage hands back. Never the data itself - only what changed.

    `unattempted` is the load-bearing field: rule 4 turns on the difference
    between "this item was looked at and has nothing" and "this item was never
    reached", and only the stage knows which.
    """

    def __init__(
        self,
        name: str,
        *,
        status: str = COMPLETE,
        fragments: Optional[Dict[str, Dict[str, Any]]] = None,
        unattempted: Sequence[str] = (),
        attempted: int = 0,
        note: str = "",
        seconds: float = 0.0,
    ) -> None:
        self.name = str(name)
        self.fragments = {
            str(key): dict(value)
            for key, value in (fragments or {}).items()
            if isinstance(value, dict)
        }
        self.unattempted = tuple(str(item) for item in unattempted)
        self.attempted = int(attempted)
        self.note = str(note or "")
        self.seconds = float(seconds)
        # A stage that names unreached items is partial whatever it claims, so
        # a stage cannot accidentally report success over work it skipped.
        if status == COMPLETE and self.unattempted:
            status = PARTIAL
        self.status = status

    @property
    def is_partial(self) -> bool:
        return self.status in (PARTIAL, FAILED)

    def describe(self) -> str:
        parts = [f"{self.name}: {self.status}"]
        if self.fragments:
            parts.append(f"{len(self.fragments)} enriched")
        if self.unattempted:
            parts.append(f"{len(self.unattempted)} not reached")
        if self.note:
            parts.append(self.note)
        return ", ".join(parts)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stage": self.name,
            "status": self.status,
            "enriched": len(self.fragments),
            "attempted": self.attempted,
            "not_reached": len(self.unattempted),
            "seconds": round(self.seconds, 2),
            "note": self.note,
        }


class StageContext:
    """What a stage is given: its pool, the joint host budget, the deadline,
    and the *finished* results of what it depends on.

    Finished results, never futures. That is the deadlock rule (ধারা ৩৪.৩): a
    stage cannot wait on another stage because it is never given anything to
    wait on.
    """

    def __init__(
        self,
        name: str,
        *,
        workers: int,
        budget: HostBudget,
        deadline: Optional[float] = None,
        depends_on: Optional[Dict[str, StageResult]] = None,
        payload: Any = None,
        halt: Optional[threading.Event] = None,
    ) -> None:
        self.name = str(name)
        self.workers = max(1, int(workers))
        self.budget = budget
        self.deadline = deadline
        self.depends_on = dict(depends_on or {})
        self.payload = payload
        #: Cooperative stop. A stage running beside something else finishes
        #: when that something else does, rather than holding the run open -
        #: and stopping early is a PARTIAL, which rule 4 already makes safe.
        self.halt = halt
        self._started = time.monotonic()

    def out_of_time(self) -> bool:
        if self.halt is not None and self.halt.is_set():
            return True
        return self.deadline is not None and time.monotonic() >= self.deadline

    def remaining_seconds(self) -> Optional[float]:
        if self.halt is not None and self.halt.is_set():
            return 0.0
        if self.deadline is None:
            # Still bounded: a stage asked to stop must notice reasonably
            # soon, so waits are never allowed to be indefinite.
            return None if self.halt is None else 0.25
        return max(0.0, self.deadline - time.monotonic())

    def map(
        self,
        work: Callable[[Any], Any],
        items: Iterable[Any],
        *,
        key: Optional[Callable[[Any], str]] = None,
        host: Optional[Callable[[Any], str]] = None,
    ) -> Tuple[List[Tuple[str, Any]], List[str]]:
        """Run `work` over `items` in this stage's bounded pool.

        Returns `(outcomes, unattempted)`. Anything the deadline cut off is in
        `unattempted` and NOT in outcomes, because rule 4 needs those two to be
        distinguishable. An item whose work raises is an outcome of `None` - it
        was reached and produced nothing, which is a different fact.
        """
        listed = list(items or ())
        identify = key or (lambda item: str(item))
        outcomes: List[Tuple[str, Any]] = []
        unattempted: List[str] = []
        if not listed:
            return outcomes, unattempted

        def run_one(item):
            target = host(item) if host is not None else ""
            with self.budget.hold(target):
                try:
                    return work(item)
                except Exception:  # noqa: BLE001 - one bad item, not the stage
                    return None

        workers = min(self.workers, len(listed))
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix=f"stage-{self.name}"
        ) as pool:
            pending: Dict[Any, str] = {}
            index = 0
            # Submitted in waves rather than all at once, so a deadline reached
            # halfway leaves the rest genuinely unsubmitted - work that was
            # never attempted, which is what rule 4 is about.
            while index < len(listed) or pending:
                while (
                    index < len(listed)
                    and len(pending) < workers
                    and not self.out_of_time()
                ):
                    item = listed[index]
                    pending[pool.submit(run_one, item)] = identify(item)
                    index += 1
                if not pending:
                    break
                done, _ = concurrent.futures.wait(
                    list(pending),
                    timeout=self.remaining_seconds(),
                    return_when=concurrent.futures.FIRST_COMPLETED,
                )
                if not done:
                    # The deadline passed with work still running. It is left
                    # to finish - cancelling mid-request would be the only way
                    # to make it worse - but nothing more is submitted.
                    done = set(concurrent.futures.wait(list(pending))[0])
                for future in done:
                    outcomes.append((pending.pop(future), future.result()))
            unattempted = [identify(item) for item in listed[index:]]
        return outcomes, unattempted

    def result(self, **kwargs) -> StageResult:
        kwargs.setdefault("seconds", time.monotonic() - self._started)
        return StageResult(self.name, **kwargs)


class Stage:
    """A unit of the graph: what it is called, what it needs, how wide it runs."""

    def __init__(
        self,
        name: str,
        run: Callable[[StageContext], Optional[StageResult]],
        *,
        needs: Sequence[str] = (),
        workers: int = DEFAULT_WORKERS,
        payload: Any = None,
    ) -> None:
        self.name = str(name)
        self.run = run
        self.needs = tuple(str(item) for item in needs)
        self.workers = max(1, int(workers))
        self.payload = payload


def run_stages(
    stages: Sequence[Stage],
    *,
    budget: Optional[HostBudget] = None,
    deadline: Optional[float] = None,
    max_concurrent: Optional[int] = None,
    halt: Optional[threading.Event] = None,
) -> Dict[str, StageResult]:
    """Run the graph: everything whose dependencies are met starts at once.

    The coordinator owns every dependency. A stage is started only once its
    needs have *finished*, and is handed their results - so no worker ever
    waits on a worker, at any pool size. That is deliberately load-bearing:
    with `max_concurrent=1` this still completes, where a design that waited
    inside a worker would hang.

    A stage whose dependency failed still runs. Enrichment is additive here;
    a stage with less to work from produces a smaller fragment, and a smaller
    fragment is not a loss.
    """
    listed = [stage for stage in stages or () if isinstance(stage, Stage)]
    if not listed:
        return {}

    order = {stage.name: index for index, stage in enumerate(listed)}
    shared = budget if budget is not None else HostBudget()
    results: Dict[str, StageResult] = {}
    remaining = list(listed)
    width = max(1, int(max_concurrent or len(listed)))

    def invoke(stage: Stage) -> StageResult:
        context = StageContext(
            stage.name,
            workers=stage.workers,
            budget=shared,
            deadline=deadline,
            depends_on={
                name: results[name] for name in stage.needs if name in results
            },
            payload=stage.payload,
            halt=halt,
        )
        started = time.monotonic()
        try:
            produced = stage.run(context)
        except Exception as error:  # noqa: BLE001 - a stage is never fatal
            return StageResult(
                stage.name,
                status=FAILED,
                note=f"{type(error).__name__}: {error}",
                seconds=time.monotonic() - started,
            )
        if isinstance(produced, StageResult):
            return produced
        return context.result()

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=width, thread_name_prefix="stage-coordinator"
    ) as pool:
        running: Dict[Any, str] = {}
        while remaining or running:
            ready = [
                stage for stage in remaining
                if all(need in results for need in stage.needs)
            ]
            if not ready and not running:
                # Nothing can proceed: a missing or circular dependency. The
                # rest are reported rather than dropped, so a graph mistake is
                # visible instead of silently halving the run.
                for stage in remaining:
                    results[stage.name] = StageResult(
                        stage.name,
                        status=FAILED,
                        note="dependency never satisfied: "
                             + ", ".join(
                                 need for need in stage.needs
                                 if need not in results
                             ),
                    )
                break
            for stage in ready:
                if len(running) >= width:
                    break
                remaining.remove(stage)
                running[pool.submit(invoke, stage)] = stage.name
            if not running:
                continue
            done, _ = concurrent.futures.wait(
                list(running), return_when=concurrent.futures.FIRST_COMPLETED
            )
            for future in done:
                name = running.pop(future)
                results[name] = future.result()

    return dict(sorted(results.items(), key=lambda pair: order.get(pair[0], 0)))


def aggregate(
    results: Dict[str, StageResult],
    *,
    order: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Merge every fragment, alone, in one fixed order. Rules 1 and 4.

    Fill-only, like every other enricher in this codebase: a stage adds what
    is missing and never overwrites what is already there. Two stages offering
    different values for one field is recorded rather than resolved - silently
    picking one is how a catalogue ends up with a value nobody can account for.

    `not_reached` is what rule 4 exists for. Those ids keep whatever they had;
    the caller passes them on so the No-Loss gate reads them as "not looked at"
    instead of "gone".
    """
    names = list(order) if order else list(results.keys())
    merged: Dict[str, Dict[str, Any]] = {}
    sources: Dict[str, Dict[str, str]] = {}
    conflicts: List[Dict[str, Any]] = []
    not_reached: Dict[str, List[str]] = {}
    partial_stages: List[str] = []

    for name in names:
        result = results.get(name)
        if not isinstance(result, StageResult):
            continue
        if result.is_partial:
            partial_stages.append(name)
        for content_id in sorted(result.fragments):
            fragment = result.fragments[content_id]
            target = merged.setdefault(content_id, {})
            credit = sources.setdefault(content_id, {})
            for field in sorted(fragment):
                value = fragment[field]
                if value is None or value == "" or value == [] or value == {}:
                    continue
                if field in target:
                    if target[field] != value:
                        conflicts.append({
                            "content_id": content_id,
                            "field": field,
                            "kept": credit.get(field, ""),
                            "kept_value": target[field],
                            "refused": name,
                        })
                    continue
                target[field] = value
                credit[field] = name
        for content_id in result.unattempted:
            not_reached.setdefault(content_id, []).append(name)

    return {
        "merged": merged,
        "sources": sources,
        "conflicts": conflicts,
        "not_reached": sorted(not_reached),
        "not_reached_by_stage": {
            key: sorted(value) for key, value in sorted(not_reached.items())
        },
        "partial_stages": sorted(partial_stages),
        "stages": [
            results[name].to_dict() for name in names
            if isinstance(results.get(name), StageResult)
        ],
    }


def apply_fragments(
    items: Iterable[Dict[str, Any]],
    report: Dict[str, Any],
    *,
    identity: Callable[[Dict[str, Any]], str],
) -> int:
    """Stage C, the only writer: put the merged fragments onto the real items.

    Fill-only and single-threaded by construction - this is called after every
    stage has finished, on the thread that will do the publishing.
    """
    merged = (report or {}).get("merged") or {}
    if not merged:
        return 0
    touched = 0
    for item in items or ():
        if not isinstance(item, dict):
            continue
        fragment = merged.get(identity(item))
        if not fragment:
            continue
        changed = False
        for field in sorted(fragment):
            if not item.get(field):
                item[field] = fragment[field]
                changed = True
        if changed:
            touched += 1
    return touched


def describe(report: Dict[str, Any]) -> str:
    """One line for the scan log."""
    stages = (report or {}).get("stages") or []
    if not stages:
        return ""
    parts = [
        "{stage} {status} ({enriched} enriched, {seconds}s)".format(**entry)
        for entry in stages
    ]
    line = "parallel stages: " + " | ".join(parts)
    not_reached = (report or {}).get("not_reached") or []
    if not_reached:
        line += (
            f" | {len(not_reached)} item(s) not reached - previous state kept, "
            "not counted missing"
        )
    return line
