"""Routes that passed the 120 s acceptance, and which one a channel should lead with.

The Zee Bangla fix was written straight into data/channels/indian.json, and the
script that wrote it says in its own comment that the next scan rebuilds cards
from their sources and erases whatever was written on them. So that fix had a
shelf life of one scan - the same mistake that undid the seven restored channels,
made again one step further along.

`sustained_proof` already stops a proven channel being hidden. It says nothing
about which of a channel's routes should be the primary, which is the part that
decides whether a viewer sees a working stream or a stuttering one.

This closes that. A route with two independent 120 s passes outranks every
verification tier the scanner can assign, because those tiers are network
observations and this is decoded frames. The registry lives outside the cards, so
a rebuild reads it rather than erasing it.

Deliberately narrow: it can only PROMOTE a route within a channel's existing
candidates. It cannot introduce a route, remove one, or hide anything.

Two guards keep a stale proof from outliving its usefulness: entries expire
after PREFERENCE_TTL_SECONDS, and a route this scan found unusable is not
promoted even while its proof is still inside that window.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
from typing import Any, Dict, List, Optional

from scanner import route_evidence as rev

#: How long a recorded preference stays in force without being re-verified.
#:
#: Codex asked what stops a route that later breaks from being forced into
#: primary forever - correctly, since nothing did. This is a new policy
#: decision, not a previously-locked one: Phase 0b's persistence.ttl_seconds
#: (1800 s) governs a different mechanism (the escalation counter) and is far
#: too short here - channel scans run roughly every 6 hours, so a 30-minute
#: expiry would make every preference stale before the next scan ever reads it.
#: 14 days is several times the movie-scan cadence (48 h) and dozens of times
#: the channel-scan cadence (6 h), long enough that a route proven once keeps
#: leading across many real scans, short enough that a route nobody re-verifies
#: for two weeks stops being forced and falls back to ordinary ranking.
PREFERENCE_TTL_SECONDS = 14 * 24 * 3600

DEFAULT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "state",
    "route-preference.json",
)

#: How many superseded proofs one channel keeps. Enough to read the history of
#: a channel that has moved between CDNs a few times, bounded so a flapping
#: channel cannot grow the registry without limit.
MAXIMUM_SUPERSEDED = 6


def load(path: Optional[str] = None) -> Dict[str, Any]:
    """Read the registry. Unreadable means no preferences, never an error."""
    target = path or DEFAULT_PATH
    try:
        with open(target, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return {"version": 1, "preferred": {}}
    if not isinstance(payload, dict) or not isinstance(payload.get("preferred"), dict):
        return {"version": 1, "preferred": {}}
    return payload


def _key(kind: str, name: str) -> str:
    return "{0}|{1}".format(
        str(kind or "").strip().casefold(), str(name or "").strip().casefold()
    )


def record(
    kind: str,
    channel: str,
    route_url: str,
    evidence: Dict[str, Any],
    *,
    path: Optional[str] = None,
) -> tuple:
    """Register a proven route as this channel's preferred primary.

    Refuses anything short of two full passes, so the registry cannot be seeded
    with a claim - the same floor every other promotion here answers to.
    """
    if not str(channel or "").strip() or not str(route_url or "").strip():
        return False, "channel or route missing"
    try:
        passes = int(evidence.get("pass_count"))
    except (TypeError, ValueError):
        return False, "evidence records no pass count"
    if passes < rev.REQUIRED_FRESH_SESSIONS:
        return False, (
            f"only {passes} full PASS; {rev.REQUIRED_FRESH_SESSIONS} independent "
            "sessions required"
        )
    if not evidence.get("window_seconds"):
        return False, "evidence records no measurement window"

    target = path or DEFAULT_PATH
    registry = load(target)
    key = _key(kind, channel)
    previous = (registry.get("preferred") or {}).get(key)

    entry = {
        "kind": kind,
        "channel": channel,
        "recorded_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        # Stored normalised so a rotating cache-buster does not look like a
        # different route on the next scan.
        "route_id": rev.normalize_source_identity(route_url),
        "url_public_template": rev.redact_public_template(route_url),
        "pass_count": passes,
        "window_seconds": evidence.get("window_seconds"),
        "media_progress_seconds": evidence.get("media_progress_seconds"),
        "cumulative_stall_seconds": evidence.get("cumulative_stall_seconds"),
        "browser_profile": evidence.get("browser_profile"),
        "evidence_report": evidence.get("evidence_report"),
    }

    # The route being replaced is kept, not overwritten.
    #
    # It only got into this registry by passing two full 120 s sessions, so it
    # is a real measurement whatever happens to it later - and what usually
    # happens is a vantage-shaped negative, not a stream that stopped existing.
    # The committed registry had a hand-written superseded chain and a contract
    # test guarding it; `record` did not know about either, so the first call
    # through this function silently deleted two earlier Zee Bangla proofs.
    if isinstance(previous, dict) and previous.get("route_id"):
        if previous["route_id"] != entry["route_id"]:
            history = [
                item for item in (previous.get("superseded") or ())
                if isinstance(item, dict)
            ]
            retained = {
                field: previous.get(field)
                for field in (
                    "route_id", "url_public_template", "recorded_at",
                    "pass_count", "window_seconds", "media_progress_seconds",
                    "cumulative_stall_seconds", "browser_profile",
                    "evidence_report",
                )
                if previous.get(field) is not None
            }
            retained["why_superseded"] = str(
                evidence.get("why_superseded")
                or "A later route passed two independent sessions. This proof "
                   "is retained rather than deleted: it was a real measurement, "
                   "and a route can come back."
            )[:400]
            entry["superseded"] = ([retained] + history)[:MAXIMUM_SUPERSEDED]
        elif previous.get("superseded"):
            entry["superseded"] = previous["superseded"]

    registry.setdefault("preferred", {})[key] = entry
    registry["note"] = (
        "A route here passed two independent 120 s browser sessions. It outranks "
        "the scanner's verification tiers, which are network observations, "
        "because this is decoded frames. Promotion only - this registry cannot "
        "introduce, remove or hide a route."
    )
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as handle:
            json.dump(registry, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
    except OSError as error:
        return False, f"could not write registry: {error}"
    return True, "recorded"


def _is_stale(entry: Dict[str, Any], *, now: Optional[float] = None) -> bool:
    stamp = str(entry.get("recorded_at") or "")
    if not stamp:
        # An entry from before this field existed. Treated as stale rather than
        # eternal, so an old record cannot outlive verification forever simply
        # by predating the check that would have caught it.
        return True
    try:
        text = stamp[:-1] + "+00:00" if stamp.endswith("Z") else stamp
        recorded = _dt.datetime.fromisoformat(text)
    except ValueError:
        return True
    if recorded.tzinfo is None:
        recorded = recorded.replace(tzinfo=_dt.timezone.utc)
    reference = (
        _dt.datetime.now(_dt.timezone.utc)
        if now is None
        else _dt.datetime.fromtimestamp(now, tz=_dt.timezone.utc)
    )
    return (reference - recorded).total_seconds() > PREFERENCE_TTL_SECONDS


def preferred_route_id(
    kind: str,
    channel: str,
    registry: Optional[Dict[str, Any]] = None,
    *,
    now: Optional[float] = None,
) -> Optional[str]:
    entry = ((registry if registry is not None else load()).get("preferred") or {}).get(
        _key(kind, channel)
    )
    if not isinstance(entry, dict):
        return None
    if _is_stale(entry, now=now):
        # Stale preferences fall back to ordinary ranking rather than forcing a
        # route nobody has re-verified in PREFERENCE_TTL_SECONDS.
        return None
    route_id = str(entry.get("route_id") or "")
    return route_id or None


#: The three answers this scan can give about a preferred route.
#:
#: Two states were not enough, and the gap was pointed out rather than
#: discovered here: the first version of this check treated
#: `publish_allowed is False` as the single disqualifier, which reads every
#: negative as final. Most negatives available from this vantage are not. A
#: datacentre egress sees a Bangladesh-only route as 403 and a Cloudflare
#: worker outage as 530, and neither says the route is dead - concluding that
#: it is would throw away a working channel on the strength of where the
#: scanner happens to run.
HEALTH_HEALTHY = "healthy"
HEALTH_HARD_FAILED = "hard_failed"
HEALTH_INCONCLUSIVE = "inconclusive"

#: This scan positively observed the route working.
POSITIVE_STATUSES = frozenset({
    "verified",
    "verified_global",
    "verified_bd",
    "verified_proxy",
    "verified_sustained_playback",
})

#: The scan could not settle the question, and said so. These are the statuses
#: the publish gate already treats as publishable rather than failed, which is
#: the same judgement in a different place.
INCONCLUSIVE_STATUSES = frozenset({
    "geo_pending",
    "bd_protected_pending",
    "needs_bd_check",
    "retryable_pending",
    "host_deferred",
    "host_circuit_open",
    "stale_last_good",
})

#: "This resource is gone" - about the route itself, and true from anywhere.
HARD_HTTP_STATUSES = frozenset({404, 410})

#: "You may not have this from here" - about the asker, the credential or the
#: origin's own availability, none of which is a property of the route. 530 is
#: in this set for a measured reason: the proven Zee Bangla route returns it
#: today because the Cloudflare worker in front of a working upstream is down,
#: and the same upstream answers 200 through a different front.
VANTAGE_HTTP_STATUSES = frozenset({401, 403, 407, 429, 451, 502, 503, 530})


def route_health(stream: Dict[str, Any]) -> tuple:
    """(state, reason) for what THIS scan found about a route.

    Expiry alone was not enough - a preference could sit well inside its 14
    days while the route it named had already stopped working. Nor is a plain
    boolean enough, because it forces a vantage-shaped negative to be read as
    either "fine" or "dead" when the honest answer is "not from here".

    The three states are acted on differently by `promote_preferred`:
    hard_failed refuses the promotion, healthy and inconclusive allow it. That
    asymmetry is deliberate. Decoded frames from two independent browser
    sessions are the strongest evidence this project can hold, and an
    inconclusive network reading from one datacentre is not grounds to discard
    it. A route that is genuinely gone does not stay inconclusive: it returns
    404, or fails playback, or the proof expires.
    """
    if not isinstance(stream, dict):
        return HEALTH_HARD_FAILED, "not a stream record"
    if str(stream.get("metadata_only") or "").lower() == "true" or stream.get(
        "metadata_only"
    ) is True:
        return HEALTH_HARD_FAILED, "metadata-only record, no route to promote"
    if not str(stream.get("url") or "").strip():
        return HEALTH_HARD_FAILED, "no URL on this record"

    verdict = str(stream.get("verdict") or "").strip().lower()
    if verdict == rev.PLAYBACK_FAIL:
        return HEALTH_HARD_FAILED, "playback_fail: the route was measured unplayable"
    if rev.is_escalatable(verdict) and verdict:
        return HEALTH_HARD_FAILED, f"escalatable route failure: {verdict}"

    status = str(stream.get("verification_status") or "").strip().lower()
    http_status = _safe_int(stream.get("http_status"))

    if http_status in HARD_HTTP_STATUSES:
        return HEALTH_HARD_FAILED, f"HTTP {http_status}: the route is gone"

    if status in POSITIVE_STATUSES and stream.get("publish_allowed") is not False:
        return HEALTH_HEALTHY, f"this scan verified the route ({status})"

    if http_status in VANTAGE_HTTP_STATUSES:
        return (
            HEALTH_INCONCLUSIVE,
            f"HTTP {http_status} is about this vantage, not the route",
        )
    if status in INCONCLUSIVE_STATUSES:
        return HEALTH_INCONCLUSIVE, f"the scan did not settle it ({status})"
    if status == "failed":
        # Failed with nothing to say why. Treated as unsettled rather than
        # final: this vantage produces geo-shaped failures routinely, and the
        # TTL already bounds how long an unconfirmed proof can lead.
        return HEALTH_INCONCLUSIVE, "failed without an attributable cause"

    if verdict:
        # A verdict that reached here is one the taxonomy already declared
        # non-escalatable - a device or vantage limit rather than a property of
        # the route. Named rather than folded into "no observation", because
        # the two are different findings and a reader of this reason should be
        # able to tell them apart.
        return HEALTH_INCONCLUSIVE, f"non-escalatable verdict: {verdict}"

    return HEALTH_INCONCLUSIVE, "this scan recorded no observation for the route"


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def is_promotable(stream: Dict[str, Any]) -> tuple:
    """(allowed, reason) - whether this record may be made primary.

    Health is what we believe about the route; promotability is whether making
    it primary is even coherent. They are separate on purpose: a geo-blocked
    route is inconclusive AND publishable (the publish gate allows geo_pending
    by design, because those routes work for the audience this site is built
    for), so it can still lead. A route the pipeline has marked unpublishable
    cannot lead whatever we believe about it - the card would point at
    something the site will not serve.
    """
    state, reason = route_health(stream)
    if state == HEALTH_HARD_FAILED:
        return False, reason
    if stream.get("publish_allowed") is False:
        return False, f"the pipeline marked this route unpublishable ({state})"
    return True, reason


def promote_preferred(
    streams: List[Dict[str, Any]],
    kind: str,
    channel: str,
    registry: Optional[Dict[str, Any]] = None,
    *,
    full_pool: Optional[List[Dict[str, Any]]] = None,
    now: Optional[float] = None,
) -> tuple:
    """Move a proven route to the front of `streams`, if it is present.

    Returns (streams, promoted). Without `full_pool`, the list is returned
    unchanged when the proven route is not among `streams` - measured to be a
    real gap: `streams` is usually a slot-limited selection (six by default),
    and a channel with seven or more sources could rank its proven route
    seventh, below that cutoff, where this function never saw it at all.

    `full_pool` is the pre-truncation candidate list the caller ranked `streams`
    from. When the preferred route is present there but not in `streams`, it is
    pulled in and the lowest-ranked current entry is evicted so the result never
    exceeds the caller's own size limit - the route was always a genuine
    candidate the scan found; only the slot count hid it.
    """
    wanted = preferred_route_id(kind, channel, registry, now=now)
    if not wanted:
        return streams, False

    def _matches(stream: Dict[str, Any]) -> bool:
        url = str((stream or {}).get("url") or "")
        if not url or rev.normalize_source_identity(url) != wanted:
            return False
        # A proof from two weeks ago does not outrank this scan measuring the
        # route unplayable today - but an inconclusive reading from one
        # datacentre egress is not that measurement.
        allowed, _reason = is_promotable(stream)
        return allowed

    for index, stream in enumerate(streams or ()):
        if _matches(stream):
            if index == 0:
                return streams, False
            reordered = list(streams)
            reordered.insert(0, reordered.pop(index))
            return reordered, True

    if full_pool:
        for stream in full_pool:
            if not _matches(stream):
                continue
            if not streams:
                return [stream], True
            promoted_list = [stream] + list(streams)
            # Keep the caller's own slot limit: evict the weakest entry rather
            # than growing the selection past what it asked for.
            if len(promoted_list) > len(streams):
                promoted_list = promoted_list[: len(streams)]
            return promoted_list, True

    return streams, False
