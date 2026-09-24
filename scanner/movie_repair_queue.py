"""ধাপ ৮ / S-02 - the separate path for repairing a broken link.

S-02 in one line: there is no way to fix one dead link without running the
whole catalogue, and that run's budget is then divided across 2,475 links - so
repair is effectively a matter of luck.

This is the queue that makes it deliberate. ধারা ৪.৫:

    P0  no live link at all          the viewer sees nothing
    P1  primary dead, backup live
    P2  backup dead, primary live
    P3  restricted / uncertain

and the flow it feeds is explicitly *not* the whole catalogue:

    1. check the backups that already exist
    2. promote a live backup to active_primary for now
    3. look for candidates by canonical identity in the source snapshot
       already fetched - no new source round trip
    4. verify only those new URLs
    5. keep as many live ones as turn up, to a maximum of five backups
    6. success leaves the queue; failure backs off

Severity is about the viewer, not about us
------------------------------------------
P0 is "nobody can watch this", which is why it outranks a dead primary with a
working backup: the second is a quality problem and the first is an outage. A
queue ordered by when something broke would spend its budget on P3s while a P0
waited, so the order is severity first and age only within it.

Being in this queue is not a disposition
----------------------------------------
ধারা ৪.০ is explicit that stream health and content disposition are two axes. A
card stays `published_movie` while its link sits here as
`confirmed_unavailable`. Nothing in this module removes, hides or reorders a
card - it records what needs looking at.

Stdlib only.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

SCHEMA_VERSION = 1

DEFAULT_PATH = Path("state") / "movie-repair-queue.json"

# ---------------------------------------------------------------------------
# ধারা ৪.৫ - severity
# ---------------------------------------------------------------------------

P0_NO_LIVE_LINK = "P0"
P1_PRIMARY_DEAD_BACKUP_LIVE = "P1"
P2_BACKUP_DEAD_PRIMARY_LIVE = "P2"
P3_RESTRICTED_OR_UNCERTAIN = "P3"

SEVERITIES = (
    P0_NO_LIVE_LINK,
    P1_PRIMARY_DEAD_BACKUP_LIVE,
    P2_BACKUP_DEAD_PRIMARY_LIVE,
    P3_RESTRICTED_OR_UNCERTAIN,
)

#: Lower sorts first. A P0 that arrived a minute ago still outranks a P3 that
#: has waited a week, because one is an outage and the other is a quality
#: problem.
_SEVERITY_ORDER = {severity: index for index, severity in enumerate(SEVERITIES)}

# ---------------------------------------------------------------------------
# backoff
# ---------------------------------------------------------------------------

HOUR = 3600

#: Attempt 1 waits an hour, then 4, 12, 24, and 48 from there on. A repair that
#: keeps failing is usually waiting on something outside this system - a host
#: that is down, a token issuer that is unhappy - and asking it every four
#: hours forever is how a queue becomes noise.
ATTEMPT_BACKOFF_SECONDS: Tuple[int, ...] = (
    1 * HOUR, 4 * HOUR, 12 * HOUR, 24 * HOUR, 48 * HOUR)

#: A P0 is an outage, so its first retry is not made to wait the full hour.
P0_FIRST_ATTEMPT_SECONDS = 15 * 60

#: Past this an entry is still kept - it is evidence, and dropping it would
#: lose the only record that this content ever had a working link - but it
#: stops being offered to the repair run.
MAX_ATTEMPTS = 12


def _now(now: Optional[datetime] = None) -> datetime:
    return now or datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.isoformat()


def _parse(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(
            text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# the store
# ---------------------------------------------------------------------------

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


def load(path: Optional[str | Path] = None) -> Dict[str, Any]:
    target = Path(path or DEFAULT_PATH)
    try:
        with open(target, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    entries = payload.get("entries")
    return {
        "schema_version": SCHEMA_VERSION,
        "updated_at": payload.get("updated_at") or "",
        "entries": entries if isinstance(entries, dict) else {},
    }


def save(queue: Dict[str, Any], path: Optional[str | Path] = None) -> bool:
    target = Path(path or DEFAULT_PATH)
    entries = queue.get("entries") or {}
    try:
        _atomic_write(target, {
            "schema_version": SCHEMA_VERSION,
            "updated_at": _iso(_now()),
            "count": len(entries),
            "by_severity": {
                severity: sum(
                    1 for entry in entries.values()
                    if isinstance(entry, dict)
                    and entry.get("severity") == severity
                )
                for severity in SEVERITIES
            },
            "entries": entries,
        })
    except OSError:
        return False
    return True


def entry_key(content_id: Any, broken_link_id: Any) -> str:
    """One entry per broken link per content, not one per attempt."""
    return f"{str(content_id or '').strip()}␟{str(broken_link_id or '').strip()}"


# ---------------------------------------------------------------------------
# ধারা ৪.৫ - deciding severity
# ---------------------------------------------------------------------------

def classify(
    *,
    primary_healthy: bool,
    live_backup_count: int,
    dead_backup_count: int = 0,
    restricted: bool = False,
) -> Optional[str]:
    """The severity for one card, or None when there is nothing to repair.

    Asked about the card rather than the link, because "is anybody able to
    watch this" is a question about the card and it is the one that decides
    P0.
    """
    if not primary_healthy and live_backup_count <= 0:
        return P0_NO_LIVE_LINK
    if not primary_healthy and live_backup_count > 0:
        return P1_PRIMARY_DEAD_BACKUP_LIVE
    if primary_healthy and dead_backup_count > 0:
        return P2_BACKUP_DEAD_PRIMARY_LIVE
    if restricted:
        return P3_RESTRICTED_OR_UNCERTAIN
    return None


def _next_attempt_seconds(severity: str, attempts: int) -> int:
    if severity == P0_NO_LIVE_LINK and attempts <= 1:
        return P0_FIRST_ATTEMPT_SECONDS
    index = min(max(attempts, 1), len(ATTEMPT_BACKOFF_SECONDS)) - 1
    return ATTEMPT_BACKOFF_SECONDS[index]


def enqueue(
    queue: Dict[str, Any],
    *,
    content_id: str,
    broken_link_id: str,
    severity: str,
    reason: str = "",
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Add or update one entry. Re-queuing does not restart its backoff.

    An entry that is already here keeps its attempt count: a link that has
    failed five repairs and is seen again has failed five repairs, and resetting
    the counter on every scan would turn the backoff into a fixed interval.
    """
    reference = _now(now)
    entries = queue.setdefault("entries", {})
    key = entry_key(content_id, broken_link_id)
    entry = entries.get(key)
    if not isinstance(entry, dict):
        entry = {
            "content_id": str(content_id or ""),
            "broken_link_id": str(broken_link_id or ""),
            "queued_at": _iso(reference),
            "attempts": 0,
            "next_attempt_at": _iso(reference),
        }
    # Severity can rise or fall as the card changes - a backup dying while the
    # primary is fine is a P2, and the same card losing its primary later is a
    # P0. The latest assessment wins.
    entry["severity"] = severity
    entry["reason"] = reason or entry.get("reason") or ""
    entry["last_seen_at"] = _iso(reference)
    entries[key] = entry
    return entry


def record_attempt(
    queue: Dict[str, Any],
    key: str,
    *,
    repaired: bool,
    detail: str = "",
    now: Optional[datetime] = None,
) -> Optional[Dict[str, Any]]:
    """Note one repair attempt. A repaired link leaves the queue.

    Returns the entry, or None once it has been removed - "it is gone because
    it worked" is the outcome the caller most wants to be able to see.
    """
    reference = _now(now)
    entries = queue.setdefault("entries", {})
    entry = entries.get(key)
    if not isinstance(entry, dict):
        return None
    # ধাপ ১৩ / A-06 `repair_attempted`. Counted here, at the one place an
    # attempt is recorded, so a repaired link and a failed one both count -
    # "how much repair work happened" is the question, not "how much failed".
    try:
        from scanner import movie_observability

        movie_observability.note("repair_attempted")
    except Exception:  # noqa: BLE001 - a counter never costs a repair
        pass
    if repaired:
        entries.pop(key, None)
        return None
    attempts = int(entry.get("attempts") or 0) + 1
    entry["attempts"] = attempts
    entry["last_attempt_at"] = _iso(reference)
    entry["last_attempt_detail"] = detail
    entry["next_attempt_at"] = _iso(
        reference + timedelta(
            seconds=_next_attempt_seconds(
                str(entry.get("severity") or P3_RESTRICTED_OR_UNCERTAIN),
                attempts,
            )
        )
    )
    entries[key] = entry
    return entry


def resolve(queue: Dict[str, Any], content_id: str, broken_link_id: str) -> bool:
    """Drop an entry because the link works again. True when one was removed."""
    return queue.setdefault("entries", {}).pop(
        entry_key(content_id, broken_link_id), None) is not None


def due(
    queue: Dict[str, Any],
    *,
    limit: int = 0,
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """What a repair run should work on, worst first.

    Severity first and age only within it, so a P0 that arrived a minute ago
    outranks a P3 that has waited a week. `limit` is what keeps a repair run
    small - the whole point of S-02 is that it is not a catalogue scan.
    """
    reference = _now(now)
    ready: List[Tuple[Any, Dict[str, Any]]] = []
    for key, entry in (queue.get("entries") or {}).items():
        if not isinstance(entry, dict):
            continue
        if int(entry.get("attempts") or 0) >= MAX_ATTEMPTS:
            continue
        scheduled = _parse(entry.get("next_attempt_at"))
        if scheduled is not None and reference < scheduled:
            continue
        severity = str(entry.get("severity") or P3_RESTRICTED_OR_UNCERTAIN)
        queued = _parse(entry.get("queued_at")) or reference
        ready.append(((_SEVERITY_ORDER.get(severity, 99), queued, key),
                      {**entry, "key": key}))
    ready.sort(key=lambda pair: pair[0])
    ordered = [entry for _, entry in ready]
    return ordered[:limit] if limit and limit > 0 else ordered


def summarise(queue: Dict[str, Any], *, now: Optional[datetime] = None) -> Dict[str, Any]:
    """The A-06 counters, and the one number worth alerting on."""
    entries = [
        entry for entry in (queue.get("entries") or {}).values()
        if isinstance(entry, dict)
    ]
    by_severity = {
        severity: sum(1 for entry in entries if entry.get("severity") == severity)
        for severity in SEVERITIES
    }
    return {
        "queued": len(entries),
        "by_severity": by_severity,
        "due_now": len(due(queue, now=now)),
        "exhausted": sum(
            1 for entry in entries
            if int(entry.get("attempts") or 0) >= MAX_ATTEMPTS
        ),
        # A viewer can watch nothing at all for these. It is the number that
        # should wake somebody up, so it is reported on its own.
        "no_live_link": by_severity[P0_NO_LIVE_LINK],
    }


# ---------------------------------------------------------------------------
# building the queue from what a scan already knows
# ---------------------------------------------------------------------------

def _link_status(health: Dict[str, Any], identity: str) -> str:
    record = (health.get("links") or {}).get(identity)
    if not isinstance(record, dict):
        return "unknown"
    return str(record.get("status") or "unknown")


def refresh_from_catalogue(
    queue: Dict[str, Any],
    published: Iterable[Tuple[str, Dict[str, Any]]],
    health_store: Dict[str, Any],
    *,
    now: Optional[datetime] = None,
) -> Dict[str, int]:
    """Re-derive the queue from the published cards and the link ledger.

    Derived rather than accumulated, so a card that has quietly healed leaves
    the queue without anybody having to remember to remove it - a repair queue
    that only grows is one nobody trusts.

    Reads only what two other steps already wrote. It makes no request, and it
    changes no card.
    """
    try:
        from scanner import movie_link_health as health_module
    except Exception:  # noqa: BLE001
        return {}

    counts = {severity: 0 for severity in SEVERITIES}
    counts["resolved"] = 0
    seen_keys = set()

    for _scope, card in published:
        if not isinstance(card, dict):
            continue
        content_id = str(card.get("id") or card.get("name") or "")
        if not content_id:
            continue

        primary_url = str(card.get("url") or "").strip()
        primary_id = health_module.stream_id(
            primary_url,
            source_id=card.get("source_id"),
            header_profile=card.get("header_profile"),
        ) if primary_url else ""
        primary_status = _link_status(health_store, primary_id) if primary_id else "unknown"
        # `unknown` is not a fault. A link nobody has checked yet is the state
        # every link starts in, and queuing those would put the whole catalogue
        # in the repair queue on day one.
        primary_healthy = primary_status in ("healthy", "unknown")

        live_backups = 0
        dead_backups = 0
        for backup in card.get("backups") or ():
            if not isinstance(backup, dict):
                continue
            url = str(backup.get("url") or "").strip()
            if not url:
                continue
            status = _link_status(health_store, health_module.stream_id(
                url,
                source_id=backup.get("source_id") or card.get("source_id"),
                header_profile=backup.get("header_profile"),
            ))
            if status in ("healthy", "unknown"):
                live_backups += 1
            elif status in ("dead", "confirmed_unavailable"):
                dead_backups += 1

        severity = classify(
            primary_healthy=primary_healthy,
            live_backup_count=live_backups,
            dead_backup_count=dead_backups,
        )
        key = entry_key(content_id, primary_id)
        if severity is None:
            if resolve(queue, content_id, primary_id):
                counts["resolved"] += 1
            continue
        enqueue(
            queue,
            content_id=content_id,
            broken_link_id=primary_id,
            severity=severity,
            reason=f"primary={primary_status}, live_backups={live_backups}, "
                   f"dead_backups={dead_backups}",
            now=now,
        )
        seen_keys.add(key)
        counts[severity] += 1

    return counts
