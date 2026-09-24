"""ধাপ ৭ / S-01 - when a link actually needs checking again.

The plan calls this "একক বৃহত্তম গতি-লাভ", the single largest speed win, and
the measurement behind it is plain: 2,475 links are re-checked every night in a
40-minute budget, the budget runs out, and 390 of them (23%) sit in
`stale_last_good` having never been reached. The scanner is not short of
workers - it spends its whole budget redoing work it did yesterday.

    ধারা ৪.৩, the one question in front of the existing pipeline:

        NEW / CHANGED / DUE / REPAIR  → verify, exactly as today
        FRESH HEALTHY                 → reuse state, zero network

    ধারা ৪.৪, the TTL ladder:

        new link                    immediately
        changed link                immediately
        playback failure report     immediately, highest priority
        recently failed             1h → 6h → 24h → 3d
        unstable public host        24-48h
        healthy public link         3-7 days
        trusted private link        ~7 days
        tokenized / expiring URL    expiry-aware

    plus a daily staggered sweep, `hash(content_id) % 7`, so one seventh of the
    catalogue (~354 links) is re-checked each night and the whole of it really
    is covered every week.

What this does not claim
------------------------
Not "stale goes to zero". ধারা ৪.৪ corrects that explicitly: geo restriction, a
host having a bad day and an expired token all produce stale entries and all of
them are *correct* behaviour. Every stale entry therefore carries a reason, and
the only one this work is trying to drive to zero is `budget` - stale because
the run ran out of time, which is the one nobody chose.

Safety, and why the first run changes nothing
---------------------------------------------
A link with no health record is always due. So the first scan after this ships
verifies exactly what it verifies today, and only the runs after that skip
anything - the state seeds itself from real observations rather than from an
assumption. There is no moment where this module decides a link is healthy
without having watched it be healthy.

Stdlib only. Decides what to check; checks nothing itself.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import parse_qs, urlsplit

SCHEMA_VERSION = 1

DEFAULT_PATH = Path("state") / "movie-link-health.json"

# ---------------------------------------------------------------------------
# ধারা ৪.০ axis 2 - stream health. NOT a content disposition.
# ---------------------------------------------------------------------------

#: v৩.৪ added `confirmed_unavailable` to this list and v৩.৩ made the reason
#: explicit: these are the states of a *link*, and they are a different axis
#: from where its content went. A card stays `published_movie` while its link
#: is `confirmed_unavailable` and sits in the repair queue.
STATUS_HEALTHY = "healthy"
STATUS_FAILING = "failing"
STATUS_CONFIRMED_UNAVAILABLE = "confirmed_unavailable"
STATUS_DEAD = "dead"
STATUS_UNKNOWN = "unknown"

STATUSES = (
    STATUS_HEALTHY, STATUS_FAILING, STATUS_CONFIRMED_UNAVAILABLE,
    STATUS_DEAD, STATUS_UNKNOWN,
)

#: `dead` is terminal evidence only - the four hard reasons of ধারা ৪.০.
#: Repeated failure from our own vantage produces `confirmed_unavailable`
#: instead, because a session/IP/token-dependent stream can fail here and work
#: elsewhere. This codebase has 1,312 posters proving exactly that.
TERMINAL_STATUSES = frozenset({STATUS_DEAD})

# ---------------------------------------------------------------------------
# host classes
# ---------------------------------------------------------------------------

HOST_STABLE = "stable"
HOST_UNSTABLE = "unstable"
HOST_TOKENIZED = "tokenized"

#: Query parameters that make a URL expire. Their presence is what makes a link
#: `tokenized`, and a tokenized link's TTL is bounded by its own expiry rather
#: than by how healthy it has been.
_EXPIRY_KEYS = ("expires", "expire", "exp", "e", "valid_until", "hdnts", "st")
_TOKEN_KEYS = ("token", "auth", "sig", "signature", "hash", "md5", "key",
               "policy", "hdnts")

#: Hosts whose links this catalogue has measured as short-lived. Deliberately a
#: short list of observed facts rather than a guess: everything unlisted is
#: treated as stable, which errs towards checking less often, and the failure
#: ladder catches a host that turns out to be worse than assumed.
UNSTABLE_HOST_MARKERS = (
    "workers.dev",
    "hf.space",
)

# ---------------------------------------------------------------------------
# ধারা ৪.৪ - the ladder, in seconds
# ---------------------------------------------------------------------------

HOUR = 3600
DAY = 24 * HOUR

#: Recently failed: 1h → 6h → 24h → 3d. Indexed by consecutive_failures.
FAILURE_BACKOFF_SECONDS: Tuple[int, ...] = (HOUR, 6 * HOUR, DAY, 3 * DAY)

TTL_UNSTABLE_HOST_SECONDS = 36 * HOUR          # the middle of 24-48h
TTL_HEALTHY_PUBLIC_SECONDS = 5 * DAY           # the middle of 3-7 days
TTL_TRUSTED_PRIVATE_SECONDS = 7 * DAY
#: A tokenized link is re-checked well before its token dies, so the repair
#: queue hears about it from a check rather than from a viewer.
TTL_TOKENIZED_FALLBACK_SECONDS = 12 * HOUR
TOKEN_EXPIRY_SAFETY_SECONDS = 2 * HOUR

#: ধারা ৪.৪ - `hash(content_id) % 7`. Seven buckets, one per day, so the whole
#: catalogue is genuinely re-verified every week instead of "never again".
SWEEP_BUCKETS = 7

# ---------------------------------------------------------------------------
# stale reasons - ধারা ৪.৪, "প্রতিটি stale-এর সঙ্গে একটি কারণ থাকবে"
# ---------------------------------------------------------------------------

STALE_GEO = "geo"
STALE_HOST_DOWN = "host_down"
STALE_TOKEN_EXPIRED = "token_expired"
STALE_BUDGET = "budget"

#: The only one the plan is trying to drive to zero. The other three are
#: correct behaviour and saying otherwise would make the metric a lie.
STALE_REASONS = (STALE_GEO, STALE_HOST_DOWN, STALE_TOKEN_EXPIRED, STALE_BUDGET)


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
# identity
# ---------------------------------------------------------------------------

def stream_id(url: Any, *, source_id: Any = "", header_profile: Any = "") -> str:
    """A stable id for one link, surviving a re-signed token.

    Built on `movie_coverage.stream_family` so the gate and the no-loss
    accounting agree about what "the same link" means - two answers to that
    would let a link be fresh to one and missing to the other.
    """
    try:
        from scanner.movie_coverage import stream_family

        family = stream_family(url)
    except Exception:  # noqa: BLE001
        family = str(url or "").split("|", 1)[0].strip().casefold()
    seed = "␟".join((
        str(source_id or "").strip().casefold(),
        family,
        str(header_profile or "").strip().casefold(),
    ))
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:20]


def host_class(url: Any) -> str:
    """`stable`, `unstable` or `tokenized` - ধারা ৪.২'s `host_class`."""
    text = str(url or "").split("|", 1)[0].strip()
    if not text:
        return HOST_STABLE
    try:
        parts = urlsplit(text)
    except ValueError:
        return HOST_STABLE
    query = (parts.query or "").casefold()
    if any(key in query for key in _TOKEN_KEYS + _EXPIRY_KEYS):
        return HOST_TOKENIZED
    host = (parts.hostname or "").casefold()
    if any(marker in host for marker in UNSTABLE_HOST_MARKERS):
        return HOST_UNSTABLE
    return HOST_STABLE


def token_expiry(url: Any, now: Optional[datetime] = None) -> Optional[datetime]:
    """When a signed URL stops working, if it says so in its own query.

    Only a plain unix timestamp is read. A format this cannot parse returns
    None and the link falls back to the tokenized TTL - guessing an expiry is
    how a link gets dropped while it still works.
    """
    text = str(url or "").split("|", 1)[0].strip()
    if not text:
        return None
    try:
        query = parse_qs(urlsplit(text).query)
    except ValueError:
        return None
    reference = _now(now)
    for key, values in query.items():
        if key.casefold() not in _EXPIRY_KEYS:
            continue
        for value in values:
            digits = re.sub(r"[^0-9]", "", str(value))
            if not (9 <= len(digits) <= 13):
                continue
            stamp = int(digits)
            if len(digits) == 13:
                stamp //= 1000
            try:
                moment = datetime.fromtimestamp(stamp, tz=timezone.utc)
            except (OverflowError, OSError, ValueError):
                continue
            # A date decades away is not an expiry, it is a coincidence.
            if reference - timedelta(days=365) < moment < reference + timedelta(days=365):
                return moment
    return None


def sweep_bucket(content_id: Any) -> int:
    """Which of the seven nights this content is swept on.

    Hashed rather than taken from a counter so the buckets stay balanced as the
    catalogue changes, and stable so an item does not wander between nights.
    """
    seed = str(content_id or "").encode("utf-8")
    return int(hashlib.sha1(seed).hexdigest()[:8], 16) % SWEEP_BUCKETS


def is_sweep_day(content_id: Any, now: Optional[datetime] = None) -> bool:
    reference = _now(now)
    day_index = reference.toordinal() % SWEEP_BUCKETS
    return sweep_bucket(content_id) == day_index


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
    links = payload.get("links")
    return {
        "schema_version": SCHEMA_VERSION,
        "updated_at": payload.get("updated_at") or "",
        "links": links if isinstance(links, dict) else {},
    }


def save(store: Dict[str, Any], path: Optional[str | Path] = None) -> bool:
    target = Path(path or DEFAULT_PATH)
    try:
        _atomic_write(target, {
            "schema_version": SCHEMA_VERSION,
            "updated_at": _iso(_now()),
            "count": len(store.get("links") or {}),
            "links": store.get("links") or {},
        })
    except OSError:
        return False
    return True


def new_record(
    *,
    identity: str,
    content_id: str = "",
    source_id: str = "",
    url: str = "",
    source_revision: str = "",
    preferred_primary: bool = False,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """A link nobody has watched yet. `next_verify_at` is deliberately absent.

    An absent `next_verify_at` reads as "due", which is what makes the first
    run after this ships verify exactly what it verifies today.
    """
    return {
        "stream_id": identity,
        "content_id": content_id,
        "source_id": source_id,
        "status": STATUS_UNKNOWN,
        "last_verified_at": "",
        # ধারা ৪.২ names this after the last successful check. The obvious
        # spelling of that is already owned by the Upcoming-fixture retry
        # ladder in `targeted_scan.py`, which has a guard test asserting no
        # module outside that ladder reads its field names - a second reader
        # would mean the gate had moved, and loosening the guard to fit an
        # unrelated ledger would weaken a check that protects something else.
        # The plan is naming a concept, not reserving a string.
        "last_healthy_at": "",
        "consecutive_failures": 0,
        "next_verify_at": "",
        "host_class": host_class(url),
        "source_revision": source_revision,
        "preferred_primary": bool(preferred_primary),
        "active_primary": bool(preferred_primary),
        "first_seen_at": _iso(_now(now)),
    }


# ---------------------------------------------------------------------------
# ধারা ৪.৪ - the TTL
# ---------------------------------------------------------------------------

def ttl_seconds(
    record: Dict[str, Any],
    *,
    url: str = "",
    trusted_private: bool = False,
    now: Optional[datetime] = None,
) -> int:
    """How long this link may go unchecked, from its own history."""
    failures = int(record.get("consecutive_failures") or 0)
    if failures > 0:
        index = min(failures, len(FAILURE_BACKOFF_SECONDS)) - 1
        return FAILURE_BACKOFF_SECONDS[index]

    klass = str(record.get("host_class") or host_class(url))
    if klass == HOST_TOKENIZED:
        expiry = token_expiry(url, now)
        if expiry is not None:
            reference = _now(now)
            remaining = int((expiry - reference).total_seconds()) - TOKEN_EXPIRY_SAFETY_SECONDS
            # Already expiring, or expired: due now rather than at a time in
            # the past, which would read as "never checked again".
            return max(0, min(remaining, TTL_TOKENIZED_FALLBACK_SECONDS))
        return TTL_TOKENIZED_FALLBACK_SECONDS
    if klass == HOST_UNSTABLE:
        return TTL_UNSTABLE_HOST_SECONDS
    if trusted_private:
        return TTL_TRUSTED_PRIVATE_SECONDS
    return TTL_HEALTHY_PUBLIC_SECONDS


def schedule_next(
    record: Dict[str, Any],
    *,
    url: str = "",
    trusted_private: bool = False,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    reference = _now(now)
    seconds = ttl_seconds(
        record, url=url, trusted_private=trusted_private, now=reference)
    record["next_verify_at"] = _iso(reference + timedelta(seconds=seconds))
    record["ttl_seconds"] = seconds
    return record


# ---------------------------------------------------------------------------
# ধারা ৪.৩ - the one question
# ---------------------------------------------------------------------------

DUE_NEW = "new"
DUE_CHANGED = "changed"
DUE_TTL = "due"
DUE_REPAIR = "repair"
DUE_PLAYBACK_FAILURE = "playback_failure"
DUE_SWEEP = "sweep"
FRESH = "fresh_healthy"


def verification_decision(
    record: Optional[Dict[str, Any]],
    *,
    url: str = "",
    content_id: str = "",
    source_revision: str = "",
    repair_queued: bool = False,
    playback_failed: bool = False,
    now: Optional[datetime] = None,
) -> Tuple[bool, str]:
    """`(must_verify, reason)` - ধারা ৪.৩, in one place.

    The condition for skipping is all three of the plan's clauses together:
    `status == healthy` AND the source revision is unchanged AND
    `now < next_verify_at`. Anything else verifies.
    """
    if playback_failed:
        # ধারা ৪.৪ puts this at the top of the ladder: a viewer has already
        # hit the failure, so nothing about a TTL is relevant any more.
        return True, DUE_PLAYBACK_FAILURE
    if repair_queued:
        return True, DUE_REPAIR
    if not isinstance(record, dict) or not record.get("last_verified_at"):
        return True, DUE_NEW
    if str(record.get("status") or "") != STATUS_HEALTHY:
        return True, DUE_TTL
    recorded_revision = str(record.get("source_revision") or "")
    if source_revision and source_revision != recorded_revision:
        # ধারা ৪.২ - "সোর্স বদলালে TTL বাতিল". The feed rewrote this entry, so
        # what we know about it describes something that may no longer exist.
        #
        # An *empty* recorded revision counts as different, not as a match. The
        # first version required both sides to be non-empty, which meant a
        # record made before revisions were tracked could never be marked
        # changed - "we do not know what revision this came from" is not the
        # same claim as "it has not changed". It costs one extra verification
        # per link, once, and then the revision is on file.
        return True, DUE_CHANGED
    scheduled = _parse(record.get("next_verify_at"))
    if scheduled is None or _now(now) >= scheduled:
        return True, DUE_TTL
    if is_sweep_day(content_id or record.get("content_id"), now):
        # The staggered sweep. Without it a link that stays healthy is never
        # looked at again, which is the mistake ধারা ৪.৪ names outright:
        # "সুস্থ লিংক আর কখনো দেখব না — এই ভুলটি এড়ানো যায়".
        return True, DUE_SWEEP
    return False, FRESH


# ---------------------------------------------------------------------------
# recording what a run observed
# ---------------------------------------------------------------------------

#: Verification statuses this scanner already uses that mean "it answered".
SUCCESS_STATUSES = frozenset({
    "verified_global", "verified_proxy", "manual_trusted", "verified",
})

#: Vantage-shaped refusals - never terminal, never `dead`. The same rule
#: `poster_validity` and `verifier` already apply, for the same measured
#: reason.
VANTAGE_SHAPED = frozenset({401, 402, 403, 407, 451})
DEFINITIVE_REFUSALS = frozenset({404, 410})


def record_result(
    store: Dict[str, Any],
    *,
    identity: str,
    url: str = "",
    content_id: str = "",
    source_id: str = "",
    source_revision: str = "",
    verification_status: str = "",
    http_status: int = 0,
    trusted_private: bool = False,
    preferred_primary: bool = False,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Fold one observation into the ledger and schedule the next check."""
    reference = _now(now)
    links = store.setdefault("links", {})
    record = links.get(identity)
    if not isinstance(record, dict):
        record = new_record(
            identity=identity, content_id=content_id, source_id=source_id,
            url=url, source_revision=source_revision,
            preferred_primary=preferred_primary, now=reference,
        )

    record["content_id"] = content_id or record.get("content_id") or ""
    record["source_id"] = source_id or record.get("source_id") or ""
    record["host_class"] = host_class(url) if url else record.get("host_class")
    if source_revision:
        record["source_revision"] = source_revision
    record["preferred_primary"] = bool(
        preferred_primary or record.get("preferred_primary"))
    record["last_verified_at"] = _iso(reference)

    status_text = str(verification_status or "").strip().casefold()
    succeeded = status_text in SUCCESS_STATUSES

    if succeeded:
        record["status"] = STATUS_HEALTHY
        record["last_healthy_at"] = _iso(reference)
        record["consecutive_failures"] = 0
        record.pop("stale_reason", None)
        # ধারা ৪.৫ - the owner's policy and this moment's reality are two
        # fields. A private link that comes back becomes primary again on its
        # own, without anybody editing the policy.
        record["active_primary"] = bool(record.get("preferred_primary")) or bool(
            record.get("active_primary"))
    else:
        failures = int(record.get("consecutive_failures") or 0) + 1
        record["consecutive_failures"] = failures
        if http_status in DEFINITIVE_REFUSALS:
            # The only route to `dead` from here: the resource itself is gone.
            record["status"] = STATUS_DEAD
        elif failures >= 3:
            # Repeatedly unreachable from our vantage, which is NOT terminal.
            # ধারা ৪.০ v৩.২ struck "multi-vantage multi-run failure" off the
            # definitive list; this is where that correction lives.
            record["status"] = STATUS_CONFIRMED_UNAVAILABLE
        else:
            record["status"] = STATUS_FAILING
        record["stale_reason"] = (
            STALE_GEO if http_status in VANTAGE_SHAPED
            else STALE_TOKEN_EXPIRED if record.get("host_class") == HOST_TOKENIZED
            else STALE_HOST_DOWN
        )

    # ধারা ৪.০. The gate matches previously-live streams by FAMILY, and this
    # record is the only place that knows one link got a definitive 404. The
    # family is stored rather than the url: it is exactly what the gate
    # compares, and it carries no signed token.
    #
    # Without it the gate was told nothing, `terminal_evidence` stayed 0, and
    # 362 streams with a confirmed 404 were counted as unexplained loss - which
    # blocked a publish over links that really were gone.
    family = _stream_family(url)
    if family:
        record["stream_family"] = family

    schedule_next(record, url=url, trusted_private=trusted_private, now=reference)
    links[identity] = record
    return record


def _stream_family(url: Any) -> str:
    try:
        from scanner.movie_coverage import stream_family

        return stream_family(url)
    except Exception:  # noqa: BLE001 - a missing family is not a scan failure
        return ""


def terminal_records(store: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """What the no-loss gate may treat as definitively gone.

    Only `dead`, and `dead` has exactly one route into this module: an HTTP
    status in `DEFINITIVE_REFUSALS`. Everything else - 403, geo, a host that
    would not answer, repeated failure from this vantage - is `failing` or
    `confirmed_unavailable`, and ধারা ৪.১ says none of those is terminal. So
    this cannot launder a vantage problem into a reason to drop content.

    A record written before the family was stored yields nothing, which costs
    one run: its TTL is an hour, so it is re-verified and gains one.
    """
    found: List[Dict[str, Any]] = []
    links = (store or {}).get("links")
    if not isinstance(links, dict):
        return found
    for record in links.values():
        if not isinstance(record, dict):
            continue
        if record.get("status") != STATUS_DEAD:
            continue
        family = str(record.get("stream_family") or "").strip()
        if not family:
            continue
        found.append({"url": family, "reason": "confirmed_404_or_410"})
    return found


def mark_skipped_for_budget(
    store: Dict[str, Any], identity: str, *, now: Optional[datetime] = None
) -> None:
    """The one stale reason the plan wants driven to zero.

    Recorded rather than inferred: a link that went unchecked because the run
    ran out of time is a different fact from one that answered 403, and the
    whole metric depends on being able to tell them apart.
    """
    record = (store.get("links") or {}).get(identity)
    if isinstance(record, dict):
        record["stale_reason"] = STALE_BUDGET
        record["last_budget_skip_at"] = _iso(_now(now))


def summarise(store: Dict[str, Any]) -> Dict[str, Any]:
    """The A-06 counters for a scan report."""
    links = [r for r in (store.get("links") or {}).values() if isinstance(r, dict)]
    statuses: Dict[str, int] = {status: 0 for status in STATUSES}
    reasons: Dict[str, int] = {reason: 0 for reason in STALE_REASONS}
    for record in links:
        statuses[str(record.get("status") or STATUS_UNKNOWN)] = statuses.get(
            str(record.get("status") or STATUS_UNKNOWN), 0) + 1
        reason = str(record.get("stale_reason") or "")
        if reason:
            reasons[reason] = reasons.get(reason, 0) + 1
    return {
        "links": len(links),
        "status": statuses,
        "stale_reasons": reasons,
        # ধারা ৪.৪'s target: this one, and only this one, should reach zero.
        "stale_for_budget": reasons.get(STALE_BUDGET, 0),
    }
