"""Source health is shared state. A stale run must not rewind it.

`state/source-health.json` is written whole by every run: `collect_candidates`
loads the file its checkout carried, updates the rows for the sources it
actually fetched, and writes all of them back. The push step then restores this
run's generated files whole over the rebase - which is right for a file this run
owns, and wrong for a file every run writes. A run that branched at 19:46 and
pushed at 19:50 puts its 19:46 copy over a 19:48 publish, for every source in
it, including the ones it never fetched.

Measured over the last 500 publishes of that file:

    publishes that rewound at least one source     80
    rewind seconds        median 123   p90 5,758   max 329,102  (91 hours)
    rows lost                                       0
    streak counters rewound                       235
        consecutive_failures      115 -> 114, 69 -> 68
        consecutive_unproductive   32 ->  31,  2 ->   1,  1 -> 0
    last_scan rewound past the 45-minute outage window        318
    last_productive rewound past the 6-hour memory floor      138

Those last two numbers are the safety fault rather than the untidiness.
`scanner/source_outage.py` reads `last_scan` to decide whether a health record
belongs to this scan at all - older than `record_max_age_minutes` and the
source is UNKNOWN, which protects nothing - and reads `last_productive` against
`memory_hours` to decide whether silence is an outage or simply how that source
has always been. Both inputs were being moved backwards by runs that had not
observed the source at all: a `today` scan rewound `sportlive-jiotv-targeted`,
a channels source, by 91 hours, and `sm-movie-combined` by 72.

The settlement is three-way and per source, and it asks what each side
CHANGED rather than which file is newer:

    observed by neither   the row is its base; take main's copy
    observed by one       that side's row, whole
    observed by both      the newer observation owns the current state,
                          the last-known times move only forwards, and the
                          streaks and counters follow the SEQUENCE of the two
                          observations rather than either side's arithmetic

"Observed" is not a claim a run makes; it is `last_scan` differing from the
base. A run that did not fetch a source leaves its row byte for byte as it
checked it out, so it can never win a comparison, and `CANNOT CHECK` never has
to be told apart from `HEALTHY` by a rule of its own.

Nothing here names a source or a scan mode.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

#: The per-source observation timestamp already in the schema. Written by
#: `process_single_source` at the moment the fetch returned, so it orders
#: observations by when they happened - not by when their commit landed, which
#: is the whole of the fault this settles.
OBSERVATION = "last_scan"

#: Last-known facts. Each is the timestamp of an observation that already
#: happened, so a later reading can only add to what is known: they move
#: forwards or not at all.
MONOTONIC = ("last_success", "last_productive", "last_failure")

#: `last_productive_items` is not a time, but it is the count that belongs to
#: `last_productive`, so it travels with it rather than being maxed on its own.
PRODUCTIVE_ITEMS = "last_productive_items"

#: Streaks. These describe a SEQUENCE of observations, so neither side's value
#: is right on its own and max() is wrong in both directions: it hides a reset
#: and it loses an increment.
STREAKS = ("consecutive_failures", "consecutive_unproductive")

#: What the source IS rather than what it did. Carried from whichever
#: reading has it, because a registry entry does not change between two
#: observations a minute apart and losing it would empty a report column.
METADATA = ("source_id", "source_name", "url", "pipeline")

#: Counted once per observation.
TOTAL = "total_scans"
AVERAGE = "average_response_time_ms"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _moment(value: Any) -> Optional[datetime]:
    text = _text(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _later(left: Any, right: Any) -> Any:
    """Whichever of two timestamps is later. Unparseable loses to parseable."""
    first, second = _moment(left), _moment(right)
    if first is None:
        return right if second is not None else (left or right)
    if second is None:
        return left
    return left if first >= second else right


def sources_of(payload: Any) -> Dict[str, Dict[str, Any]]:
    """The `sources` map, or an empty one.

    A file that cannot be read is not evidence about any source. Every caller
    here treats an empty map as "this side changed nothing", which is what
    keeps a truncated or half-written local file from erasing good state.
    """
    if not isinstance(payload, dict):
        return {}
    sources = payload.get("sources")
    if not isinstance(sources, dict):
        return {}
    return {
        _text(key): dict(value)
        for key, value in sources.items()
        if _text(key) and isinstance(value, dict)
    }


def observed(row: Optional[Dict[str, Any]],
             base_row: Optional[Dict[str, Any]]) -> bool:
    """Whether this side actually fetched this source since the base.

    A run rewrites only the rows it fetched; every other row is copied out of
    the file it checked out. So a changed `last_scan` is the run saying "I
    looked", and an unchanged one is the run saying nothing at all - which is
    exactly the distinction requirement 6 asks for and the reason no rule here
    has to know which pipelines a mode collects.
    """
    if not isinstance(row, dict):
        return False
    if not isinstance(base_row, dict):
        return bool(_text(row.get(OBSERVATION)))
    return _text(row.get(OBSERVATION)) != _text(base_row.get(OBSERVATION))


def _newer(ours: Dict[str, Any], theirs: Dict[str, Any]) -> bool:
    """True when OUR observation is the later one.

    A tie is resolved to theirs: two observations stamped the same instant
    cannot be ordered, and the conservative answer is the one already
    published. Deterministic either way - the same two files always settle the
    same, whichever side runs the settlement.
    """
    mine, mains = _moment(ours.get(OBSERVATION)), _moment(theirs.get(OBSERVATION))
    if mine is None:
        return False
    if mains is None:
        return True
    return mine > mains


def _streak(field: str, base: Dict[str, Any], newer: Dict[str, Any],
            older: Dict[str, Any]) -> int:
    """One streak, following the order the two observations happened in.

    Three cases, and they are the sequence rather than the arithmetic:

      the newer observation reset it     the streak ended; it is 0
      the older one reset it             the newer one is the first since;
                                         it is 1
      neither reset it                   both continued it, so both count

    Each side computed `base + 1` from the same base, so taking either value
    loses the other observation and max() loses it too. The deltas are summed
    instead, which is why settling the same two files twice gives the same
    answer rather than a larger one.
    """
    base_value = _int(base.get(field))
    newer_value = _int(newer.get(field))
    older_value = _int(older.get(field))
    if newer_value <= 0:
        return 0
    if older_value <= 0:
        return 1
    return base_value + max(0, older_value - base_value) + max(
        0, newer_value - base_value)


def _accumulators(base: Dict[str, Any], ours: Dict[str, Any],
                  theirs: Dict[str, Any]) -> Tuple[int, int]:
    """`total_scans` and `average_response_time_ms` for two observations.

    Both sides counted one scan from the same base, so the base is subtracted
    once and each side's own step is added - the count two concurrent runs
    should have produced had they run in sequence. The average is recombined
    from the SUMS each side implies, so no observation is weighted twice and
    none is dropped:

        sum = average * total, for each side, minus the base's own sum

    A retry settles against a new base, so nothing is counted twice by running
    this again.
    """
    base_total = _int(base.get(TOTAL))
    our_total = _int(ours.get(TOTAL))
    their_total = _int(theirs.get(TOTAL))
    total = base_total + max(0, our_total - base_total) + max(
        0, their_total - base_total)

    base_sum = _int(base.get(AVERAGE)) * base_total
    our_sum = max(0, _int(ours.get(AVERAGE)) * our_total - base_sum)
    their_sum = max(0, _int(theirs.get(AVERAGE)) * their_total - base_sum)
    average = int((base_sum + our_sum + their_sum) / total) if total else 0
    return total, average


def _settle_row(base: Dict[str, Any], ours: Dict[str, Any],
                theirs: Dict[str, Any]) -> Dict[str, Any]:
    """Both sides observed this source. Neither row is right on its own."""
    ours_is_newer = _newer(ours, theirs)
    newer, older = (ours, theirs) if ours_is_newer else (theirs, ours)

    # The current observation comes from the newer reading WHOLE - status,
    # http_status, attempts, response_time_ms, detected_format, raw_items,
    # error, fetch_mode, cache_hit, cache_saved - including the fields it
    # does not carry. A conditional fetch writes `cache_hit` and a full one
    # does not, so filling the gap from the older reading would describe a
    # scan that never happened. Only the metadata is filled in.
    record = dict(newer)
    for field in METADATA:
        if not _text(record.get(field)):
            for side in (older, base):
                if _text(side.get(field)):
                    record[field] = side.get(field)
                    break

    for field in MONOTONIC:
        best = _later(_later(base.get(field), ours.get(field)),
                      theirs.get(field))
        if _text(best):
            record[field] = best
    # The count belongs to whichever reading holds the newest productive time.
    productive = record.get("last_productive")
    for side in (newer, older, base):
        if _text(side.get("last_productive")) == _text(productive):
            if side.get(PRODUCTIVE_ITEMS) is not None:
                record[PRODUCTIVE_ITEMS] = side.get(PRODUCTIVE_ITEMS)
            break

    for field in STREAKS:
        record[field] = _streak(field, base, newer, older)

    total, average = _accumulators(base, ours, theirs)
    record[TOTAL] = total
    record[AVERAGE] = average
    return record


def _carry_forward(*rows: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """One row from several that describe the SAME observation.

    Reached when neither side fetched this source, so every row here carries
    the same reading and they can differ only in what an earlier settlement
    already folded in. That work is kept rather than undone: a push retry
    settles against a new base - the publish it just rebased onto - so our own
    previous settlement arrives as the side with the larger counter, and
    dropping it would lose a scan every time a push had to be retried.
    """
    present = [dict(row) for row in rows if isinstance(row, dict) and row]
    if not present:
        return {}
    richest = max(present, key=lambda row: _int(row.get(TOTAL)))
    record = dict(present[0])
    record.update(richest)
    for field in MONOTONIC:
        best = ""
        for row in present:
            best = _later(best, row.get(field)) if best else row.get(field)
        if _text(best):
            record[field] = best
    for field in STREAKS:
        record[field] = max(_int(row.get(field)) for row in present)
    return record


def _single_observer(base: Dict[str, Any], winner: Dict[str, Any],
                     other: Dict[str, Any]) -> Dict[str, Any]:
    """One side fetched this source; the other carried its row unchanged.

    The observer owns the reading outright. What the other side may still hold
    is work an EARLIER settlement folded into it - a scan counted, a streak
    advanced, a last-known time moved - because a push retry settles against
    the publish it just rebased onto and brings our own previous result back as
    a side that observed nothing. Those are carried as deltas from the base, so
    the same file settled again adds nothing and drops nothing.
    """
    record = dict(winner)
    for field in MONOTONIC:
        best = _later(_later(base.get(field), winner.get(field)),
                      other.get(field))
        if _text(best):
            record[field] = best
    productive = record.get("last_productive")
    for side in (winner, other, base):
        if _text(side.get("last_productive")) == _text(productive):
            if side.get(PRODUCTIVE_ITEMS) is not None:
                record[PRODUCTIVE_ITEMS] = side.get(PRODUCTIVE_ITEMS)
            break
    for field in STREAKS:
        value = _int(winner.get(field))
        record[field] = 0 if value <= 0 else value + max(
            0, _int(other.get(field)) - _int(base.get(field)))
    carried = max(0, _int(other.get(TOTAL)) - _int(base.get(TOTAL)))
    if carried:
        total = _int(winner.get(TOTAL)) + carried
        winner_sum = _int(winner.get(AVERAGE)) * _int(winner.get(TOTAL))
        other_sum = max(0, _int(other.get(AVERAGE)) * _int(other.get(TOTAL))
                        - _int(base.get(AVERAGE)) * _int(base.get(TOTAL)))
        record[TOTAL] = total
        record[AVERAGE] = int((winner_sum + other_sum) / total) if total else 0
    return record


def settle(
    base_payload: Any,
    ours_payload: Any,
    theirs_payload: Any,
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    """Three-way settlement of one source-health file. Returns (sources, report).

    No row is ever dropped: a source is removed from this file only by whoever
    removes it from both sides, and a registry a run did not read is not a
    registry that stopped existing.
    """
    base = sources_of(base_payload)
    ours = sources_of(ours_payload)
    theirs = sources_of(theirs_payload)

    settled: Dict[str, Dict[str, Any]] = {}
    local_applied: List[str] = []
    remote_preserved: List[str] = []
    stale_rejected: List[str] = []
    combined: List[str] = []
    ties: List[str] = []

    for source_id in sorted(set(base) | set(ours) | set(theirs)):
        base_row = base.get(source_id, {})
        our_row = ours.get(source_id)
        their_row = theirs.get(source_id)

        we_looked = observed(our_row, base_row)
        they_looked = observed(their_row, base_row)

        if we_looked and they_looked:
            settled[source_id] = _settle_row(base_row, our_row, their_row)
            combined.append(source_id)
            if _newer(our_row, their_row):
                local_applied.append(source_id)
            else:
                stale_rejected.append(source_id)
                if _text(our_row.get(OBSERVATION)) == _text(
                        their_row.get(OBSERVATION)):
                    ties.append(source_id)
            continue

        if we_looked:
            settled[source_id] = _single_observer(
                base_row, our_row or {}, their_row or base_row or {})
            local_applied.append(source_id)
            continue

        if they_looked:
            settled[source_id] = _single_observer(
                base_row, their_row or {}, our_row or base_row or {})
            remote_preserved.append(source_id)
            continue

        # Neither run fetched it. Every row here is the same reading, so
        # nothing can be lost by the choice - but an earlier settlement of
        # ours may already have folded another run's scan into the
        # counters, and a push retry brings that back as `ours` against a
        # base that predates it.
        settled[source_id] = _carry_forward(their_row, our_row, base_row)

    report = {
        "sources": len(settled),
        "observed_locally": len(local_applied),
        "observed_remotely": len(remote_preserved),
        "observed_by_both": len(combined),
        "local_newer_applied": len(local_applied),
        "remote_newer_preserved": len(remote_preserved) + len(stale_rejected),
        "stale_local_updates_rejected": len(stale_rejected),
        "tied_observations": len(ties),
        "rows_in_base": len(base),
        "rows_lost": len(set(base) - set(settled)),
        # Bounded on purpose: this is a receipt, not a copy of the state.
        "source_ids": sorted(set(local_applied) | set(stale_rejected))[:40],
    }
    return settled, report


def settle_payload(
    base_payload: Any,
    ours_payload: Any,
    theirs_payload: Any,
    *,
    updated_at: str = "",
    last_mode: str = "",
) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """The settled file, ready to write, or None when there is nothing to do.

    None is returned when neither side observed anything, so a run that changed
    no health at all does not rewrite the file - and when our own side is
    unreadable, because an unreadable local file is not a reason to touch what
    is already published.
    """
    if not isinstance(ours_payload, dict) or not sources_of(ours_payload):
        return None, {"skipped": "no readable local source health"}

    settled, report = settle(base_payload, ours_payload, theirs_payload)
    if not settled:
        return None, {"skipped": "nothing to settle"}

    payload = dict(ours_payload)
    if isinstance(theirs_payload, dict):
        # Keep whatever the newer publish recorded about itself when it is the
        # newer publish; these two fields describe the file, not a source.
        newer_is_theirs = _later(
            theirs_payload.get("updated_at"),
            ours_payload.get("updated_at")) == theirs_payload.get("updated_at")
        if newer_is_theirs and _text(theirs_payload.get("updated_at")):
            payload["updated_at"] = theirs_payload.get("updated_at")
            payload["last_mode"] = theirs_payload.get("last_mode")
    if updated_at:
        payload["updated_at"] = updated_at
    if last_mode:
        payload["last_mode"] = last_mode
    payload["sources"] = settled
    return payload, report
