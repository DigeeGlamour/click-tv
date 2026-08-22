"""Route-scoped playback evidence, and the guard that decides what may be hidden.

The scanner runs from a cloud egress while viewers are in Bangladesh, so a
negative observation here is often a statement about the observer rather than
about the stream. A published, owner-working channel was measured returning HTTP
403 three times from this vantage during the investigation that produced this
module. A model that hides on a single negative signal therefore deletes working
channels, which is the failure this module exists to prevent.

Two kinds of fact are kept strictly apart:

  * STRUCTURAL / media-intrinsic facts (codec, interlace, keyframe presence) are
    properties of the bytes. Identical bytes give the same answer from any IP, so
    they are safe to act on - but only to rank and to configure the player.
  * REACHABILITY facts (HTTP status, connection lifetime) depend on who is
    looking. They accumulate as evidence with thresholds and can never hide a
    channel on their own.

Everything not measured is `unknown`, and `unknown` is never a reason to remove
anything. The visibility rules are expressed once, in `may_hide`, so a caller
cannot accidentally hide an item by writing `publish_allowed = False` on a whim.

Escalation is deliberately asymmetric by CLASS, not by count. Repetition only
carries information when the evidence is about the route itself: a thousand
vantage blocks are a thousand statements about the observer, and a device codec
limitation repeated on one browser says nothing about another. Only transient /
persistent-unavailable evidence and genuine playback FAILs can escalate.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import urllib.parse
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


# ---------------------------------------------------------------------------
# Source identity. The allowlist is CLOSED: a query parameter is removed only
# if it is named here. 22 published channels share one host+path and differ
# only by "?id=NNN", so dropping unknown parameters would fuse 22 distinct
# channels into one identity.
# ---------------------------------------------------------------------------
REMOVABLE_QUERY_PARAMS = frozenset(
    {
        "token",
        "signature",
        "sig",
        "expires",
        "hdnts",
        "session",
        "sid",
        "nonce",
        "_t",
        "cb",
    }
)

#: Host labels that identify infrastructure rather than a tenant. A "cache" or
#: "s2" label cannot be told apart from shared infrastructure, so tenancy is
#: reported as undetermined instead of guessed.
GENERIC_HOST_LABELS = frozenset(
    {
        "www",
        "cdn",
        "stream",
        "streams",
        "live",
        "media",
        "video",
        "vod",
        "edge",
        "tv",
        "api",
        "app",
        "play",
        "proxy",
        "cache",
        "origin",
        "static",
        "content",
        "ott",
    }
)

# ---------------------------------------------------------------------------
# Verdict vocabulary
# ---------------------------------------------------------------------------
UNKNOWN = "unknown"
PROVEN = "proven"
HARD_DISQUALIFIED = "hard_disqualified"
PERSISTENT_UNAVAILABLE_CANDIDATE = "persistent_unavailable_candidate"

ADVISORY_VANTAGE_BLOCKED = "advisory:vantage_blocked"
ADVISORY_TRANSIENT_NETWORK = "advisory:transient_network"
ADVISORY_DOMAIN_EVENT = "advisory:domain_event"
ADVISORY_DEVICE_UNSUPPORTED = "advisory:device_or_browser_unsupported"
ADVISORY_STRUCTURALLY_RISKY = "advisory:structurally_risky"

#: A playback observation that met the FAIL floor and was neither
#: vantage-explainable nor transient.
PLAYBACK_FAIL = "playback_fail"

#: Statuses that describe the observer's access, not the route.
VANTAGE_BLOCKED_STATUSES = frozenset({401, 403, 429, 451})

#: Server-side and transport-transient outcomes. A single one of these must
#: never count toward failure; a genuinely dead origin also produces them, so
#: they remain escalatable over a locked persistence window.
TRANSIENT_STATUSES = frozenset({408, 500, 502, 503, 504}) | frozenset(range(520, 528))

#: Only these classes may ever lead toward a hard disqualification.
ESCALATABLE_CLASSES = frozenset(
    {ADVISORY_TRANSIENT_NETWORK, PERSISTENT_UNAVAILABLE_CANDIDATE, PLAYBACK_FAIL}
)

#: Repetition of these adds no information about the route.
NON_ESCALATABLE_CLASSES = frozenset(
    {
        ADVISORY_VANTAGE_BLOCKED,
        ADVISORY_DEVICE_UNSUPPORTED,
        ADVISORY_STRUCTURALLY_RISKY,
        ADVISORY_DOMAIN_EVENT,
    }
)

# ---------------------------------------------------------------------------
# Three-state visibility model
# ---------------------------------------------------------------------------
EXISTING_VISIBLE = "existing_visible"
NEW_UNPROVEN = "new_unproven"
LEGACY_HIDDEN = "legacy_hidden"

# ---------------------------------------------------------------------------
# Playback acceptance floors. Fixed numbers, so two testers cannot reach
# different verdicts on identical playback.
# ---------------------------------------------------------------------------
WINDOW_SECONDS = 120.0
PASS_MAX_STARTUP_SECONDS = 10.0
PASS_MIN_MEDIA_PROGRESS_SECONDS = 115.0
PASS_MAX_CUMULATIVE_STALL_SECONDS = 5.0
STALL_MIN_STAGNANT_SECONDS = 2.0
FAIL_MAX_FIRST_FRAME_SECONDS = 30.0
FAIL_MAX_MEDIA_PROGRESS_SECONDS = 10.0
REQUIRED_FRESH_SESSIONS = 2

#: Every field an evidence record must carry. A record missing any of them is
#: `unknown`, which can never hide anything.
MANDATORY_EVIDENCE_FIELDS = (
    "route_id",
    "url_public_template",
    "url_registrable_domain",
    "final_origin_public_template",
    "final_origin_registrable_domain",
    "failure_domain_provider",
    "failure_domain_tenant",
    "delivery_path",
    "browser_profile",
    "test_vantage",
    "media_fingerprint",
    "playback_metrics",
    "observed_at",
    "ttl",
    "verdict",
    "verdict_scope",
)

#: Material that must never reach a committed report.
FORBIDDEN_EVIDENCE_PATTERN = re.compile(
    r"(?:authorization|set-cookie|bearer\s|[?&](?:token|signature|sig|hdnts)=)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Host / identity helpers
# ---------------------------------------------------------------------------
def registrable_domain(host: str) -> str:
    """The provider-level domain used for failure-domain grouping."""
    labels = str(host or "").strip().lower().strip(".").split(".")
    return ".".join(labels[-2:]) if len(labels) >= 2 else ".".join(labels)


def derive_tenant(host: str) -> Tuple[Optional[str], str]:
    """Tenant labels plus the method used, or (None, reason) when undetermined.

    Tenancy is never guessed. `cache.devm3u.top` is the backup host of several
    hidden channels and its single generic label cannot be distinguished from
    shared infrastructure, so it resolves to undetermined - which forces both
    correlation and redundancy to `unknown` rather than to a convenient number.
    """
    clean = str(host or "").strip().lower().strip(".")
    labels = clean.split(".")
    if len(labels) <= 2:
        return None, "no_sub_label"
    sub = ".".join(labels[:-2])
    parts = sub.split(".")
    if all(p in GENERIC_HOST_LABELS or re.fullmatch(r"s?\d+", p) for p in parts):
        return None, "generic_or_infra_sub_label"
    return sub, "host_sub_label"


def normalize_source_identity(url: str) -> str:
    """Canonical source identity under the CLOSED allowlist.

    Only allowlisted volatile parameters are dropped. Identity parameters, the
    full path and the host are preserved verbatim.
    """
    split = urllib.parse.urlsplit(str(url or ""))
    host = (split.hostname or "").lower()
    port = f":{split.port}" if split.port and split.port not in (80, 443) else ""
    kept = [
        (key, value)
        for key, value in urllib.parse.parse_qsl(split.query, keep_blank_values=True)
        if key.lower() not in REMOVABLE_QUERY_PARAMS
    ]
    query = urllib.parse.urlencode(sorted(kept))
    return f"{host}{port}{split.path}" + (f"?{query}" if query else "")


def _redact_path_segment(segment: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_\-]{1,14}\.(?:m3u8|ts|mpd|mp4)", segment):
        return "{file}"
    if ":" in segment or re.fullmatch(r"[A-Za-z0-9]{8,}", segment):
        return "{seg}"
    return segment


def redact_public_template(url: str) -> str:
    """A shape that can be compared and published without revealing identifiers.

    Host labels beyond the provider domain are placeholders because a stable
    account id was measured embedded in a redirect hostname, and a durable
    identifier is more sensitive than a rotating one, not less.
    """
    split = urllib.parse.urlsplit(str(url or ""))
    host = (split.hostname or "").lower()
    provider = registrable_domain(host)
    labels = host.split(".")
    if len(labels) > 2:
        host_template = ".".join(["{id}"] * (len(labels) - 2)) + "." + provider
    else:
        host_template = provider
    segments = [_redact_path_segment(s) for s in split.path.split("/") if s]
    template = host_template + "/" + "/".join(segments)
    if split.query:
        names = sorted(
            {k for k, _ in urllib.parse.parse_qsl(split.query, keep_blank_values=True)}
        )
        rendered = "&".join(
            f"{name}=" + ("{redacted}" if name.lower() in REMOVABLE_QUERY_PARAMS else "{v}")
            for name in names
        )
        template += f"?{rendered}"
    return template


def hmac_id(value: str, key: Optional[bytes], *, length: int = 32) -> Optional[str]:
    """Keyed identity, or None when no key is available.

    There is deliberately no unkeyed fallback: an unkeyed digest would look
    comparable to a keyed one while never matching it, which is worse than
    having no value at all. A record without its HMAC fields is `unknown`.
    """
    if not key:
        return None
    digest = hmac.new(key, str(value or "").encode("utf-8"), hashlib.sha256)
    return digest.hexdigest()[:length]


def failure_domain(url: str, key: Optional[bytes] = None) -> Dict[str, Any]:
    """Provider + tenant hierarchy.

    Registrable-domain-only grouping was measured fusing 38 akamaized.net
    tenants (and 34 on cloudfront.net) into a single failure domain, which would
    both invent provider-wide incidents and score unrelated tenants as zero
    redundancy.
    """
    host = (urllib.parse.urlsplit(str(url or "")).hostname or "").lower()
    tenant, method = derive_tenant(host)
    return {
        "failure_domain_provider": registrable_domain(host),
        "failure_domain_tenant": hmac_id(f"tenant|{tenant}", key) if tenant else UNKNOWN,
        "tenant_derivation_method": method,
        "tenant_determined": tenant is not None,
    }


# ---------------------------------------------------------------------------
# Transport classification
# ---------------------------------------------------------------------------
def classify_transport(
    status: Any = None,
    *,
    error_kind: str = "",
    content_type: str = "",
) -> str:
    """Map a transport outcome onto the verdict vocabulary.

    HTTP 200 is never success here: a 200 serving HTML was measured on four
    routes that are stored as browser failures.
    """
    kind = str(error_kind or "").strip().lower()
    if kind in {"dns", "dns_failure", "name_resolution"}:
        return ADVISORY_VANTAGE_BLOCKED
    if kind in {"connect_timeout", "tcp_timeout"}:
        return ADVISORY_VANTAGE_BLOCKED
    if kind in {
        "tls",
        "tls_failure",
        "handshake",
        "connection_reset",
        "reset",
        "read_timeout",
        "proxy_error",
        "network",
    }:
        return ADVISORY_TRANSIENT_NETWORK

    try:
        code = int(status)
    except (TypeError, ValueError):
        return UNKNOWN

    if code in VANTAGE_BLOCKED_STATUSES:
        return ADVISORY_VANTAGE_BLOCKED
    if code in TRANSIENT_STATUSES or code == 0:
        return ADVISORY_TRANSIENT_NETWORK
    if code == 200:
        if "html" in str(content_type or "").lower():
            # Reachable, but not media. Not a failure of the route's
            # availability, and certainly not proof that it plays.
            return UNKNOWN
        return UNKNOWN
    return UNKNOWN


def is_escalatable(verdict_class: str) -> bool:
    """Whether repetition of this class can ever lead toward a hard failure."""
    return str(verdict_class or "") in ESCALATABLE_CLASSES


# ---------------------------------------------------------------------------
# Playback acceptance
# ---------------------------------------------------------------------------
def classify_playback(metrics: Dict[str, Any]) -> Tuple[str, List[str]]:
    """PASS / FAIL / AMBIGUOUS over one fixed 120 s observation.

    PASS requires every announced RENDER track to progress - not "audio and
    video", because an audio-only channel exists in the published set and would
    otherwise be impossible to pass. Announced data/subtitle streams are
    recorded but never required to progress.
    """
    reasons: List[str] = []
    if not isinstance(metrics, dict) or not metrics:
        return UNKNOWN, ["no playback metrics"]

    announced = [str(t) for t in (metrics.get("announced_render_tracks") or [])]
    progressing = {str(t) for t in (metrics.get("progressing_tracks") or [])}
    fatal = list(metrics.get("fatal_errors") or [])
    recovered = bool(metrics.get("recovered_to_pass_floor"))
    first_frame = metrics.get("first_frame_seconds")
    startup = metrics.get("startup_seconds")
    progress = metrics.get("media_progress_seconds")
    stall = metrics.get("cumulative_stall_seconds")

    if progress is None or startup is None:
        return UNKNOWN, ["incomplete playback metrics"]

    # ---- FAIL floor ----
    if first_frame is None or float(first_frame) > FAIL_MAX_FIRST_FRAME_SECONDS:
        reasons.append(f"no first frame within {FAIL_MAX_FIRST_FRAME_SECONDS:.0f}s")
    if float(progress) < FAIL_MAX_MEDIA_PROGRESS_SECONDS:
        reasons.append(f"media progress {progress}s < {FAIL_MAX_MEDIA_PROGRESS_SECONDS:.0f}s")
    if fatal and not recovered:
        reasons.append(f"unrecovered fatal error: {fatal[0]}")
    dead_tracks = [t for t in announced if t not in progressing]
    if announced and dead_tracks and len(dead_tracks) < len(announced):
        # One announced render track never progressed while another did: this is
        # the environment-scoped case, reported through verdict_scope.
        reasons.append(f"announced render track never progressed: {dead_tracks[0]}")
    if announced and not progressing:
        reasons.append("no announced render track progressed")
    if reasons:
        return PLAYBACK_FAIL, reasons

    # ---- PASS floor ----
    ok = True
    if float(startup) > PASS_MAX_STARTUP_SECONDS:
        reasons.append(f"startup {startup}s > {PASS_MAX_STARTUP_SECONDS:.0f}s")
        ok = False
    if float(progress) < PASS_MIN_MEDIA_PROGRESS_SECONDS:
        reasons.append(
            f"media progress {progress}s < {PASS_MIN_MEDIA_PROGRESS_SECONDS:.0f}s"
        )
        ok = False
    if stall is None or float(stall) > PASS_MAX_CUMULATIVE_STALL_SECONDS:
        reasons.append(f"cumulative stall {stall}s > {PASS_MAX_CUMULATIVE_STALL_SECONDS:.0f}s")
        ok = False
    if fatal:
        reasons.append("fatal error occurred (recovered) - not a pass")
        ok = False
    if not announced:
        reasons.append("no announced render tracks recorded")
        ok = False
    if ok:
        return PROVEN, []
    return UNKNOWN, reasons


def resolve_verdict_scope(
    verdict: str,
    *,
    browser_profile: str = "",
    vantage_id: str = "",
    environment_independent: bool = False,
    declared_matrix: Sequence[str] = (),
    failed_profiles: Sequence[str] = (),
) -> str:
    """How widely a verdict may be applied.

    Two arbitrary browser profiles are not the world: this project targets
    TV-class devices explicitly, so global scope needs either an
    environment-independent failure or the complete declared target matrix.
    """
    if verdict in {ADVISORY_DEVICE_UNSUPPORTED}:
        return f"environment:{browser_profile or UNKNOWN}"
    if verdict in {ADVISORY_VANTAGE_BLOCKED}:
        return f"vantage:{vantage_id or UNKNOWN}"
    if environment_independent:
        return "global"
    if declared_matrix and set(map(str, declared_matrix)) <= set(map(str, failed_profiles)):
        return "global"
    if browser_profile:
        return f"environment:{browser_profile}"
    return UNKNOWN


# ---------------------------------------------------------------------------
# Redundancy
# ---------------------------------------------------------------------------
def independent_redundancy(
    routes: Iterable[Dict[str, Any]], key: Optional[bytes] = None
) -> Any:
    """Distinct (provider, tenant) pairs, or `unknown` if any tenant is unclear.

    Two delivery attempts of one source are one source, and an undetermined
    tenant may not be substituted with 0 or 1.
    """
    pairs = set()
    for route in routes or ():
        url = (route or {}).get("url")
        if not url:
            continue
        domain = failure_domain(url, key)
        if not domain["tenant_determined"]:
            return UNKNOWN
        pairs.add((domain["failure_domain_provider"], domain["failure_domain_tenant"]))
    return len(pairs)


def distinct_sources(routes: Iterable[Dict[str, Any]]) -> int:
    """Sources, after collapsing delivery attempts of the same upstream."""
    return len(
        {
            normalize_source_identity(route["url"])
            for route in (routes or ())
            if isinstance(route, dict) and route.get("url")
        }
    )


# ---------------------------------------------------------------------------
# Evidence completeness
# ---------------------------------------------------------------------------
def evidence_is_complete(record: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """A record missing any mandatory field cannot support a hide."""
    if not isinstance(record, dict):
        return False, list(MANDATORY_EVIDENCE_FIELDS)
    missing = [f for f in MANDATORY_EVIDENCE_FIELDS if record.get(f) in (None, "", [])]
    return (not missing), missing


def evidence_contains_forbidden_material(record: Dict[str, Any]) -> bool:
    """Reject a record that would commit a credential."""
    try:
        import json as _json

        blob = _json.dumps(record, ensure_ascii=False)
    except (TypeError, ValueError):
        blob = str(record)
    if FORBIDDEN_EVIDENCE_PATTERN.search(blob):
        return True
    return bool(re.search(r"[A-Za-z0-9+/=]{40,}", blob))


def vantages_are_independent(first: Dict[str, Any], second: Dict[str, Any]) -> bool:
    """Independent means a different network path, not a different hostname.

    All four configured play proxies sit on one provider account, so no pair of
    them can serve as two vantages.
    """
    if not isinstance(first, dict) or not isinstance(second, dict):
        return False
    asn_a = str(first.get("asn") or "").strip().lower()
    asn_b = str(second.get("asn") or "").strip().lower()
    prov_a = str(first.get("provider") or "").strip().lower()
    prov_b = str(second.get("provider") or "").strip().lower()
    if not asn_a or not asn_b or not prov_a or not prov_b:
        return False
    return asn_a != asn_b and prov_a != prov_b


# ---------------------------------------------------------------------------
# THE GUARD
# ---------------------------------------------------------------------------
def three_state(
    *, is_published: bool, is_legacy_hidden: bool, has_pass: bool = False
) -> str:
    if is_legacy_hidden:
        return LEGACY_HIDDEN
    if is_published:
        return EXISTING_VISIBLE
    return NEW_UNPROVEN


def may_hide(
    *,
    state: str,
    evidence: Sequence[Dict[str, Any]] = (),
    grandfathered: bool = False,
    healthy_sibling_sources: int = 0,
) -> Tuple[bool, str]:
    """Single decision point for removing an item from the public catalogue.

    Returns (allowed, reason). Callers must not set `publish_allowed = False`
    for playback reasons without consulting this function, so the rules live in
    exactly one place.
    """
    if grandfathered:
        # A pre-existing hidden record keeps its current visibility. The model
        # migration must not itself un-hide untested legacy items, and must not
        # newly hide them either.
        return True, "legacy record grandfathered; visibility unchanged by the model"

    if state == LEGACY_HIDDEN:
        return True, "already hidden; only a full acceptance pass may un-hide"

    if state == NEW_UNPROVEN:
        return True, "never published and never proven; stays out until a pass exists"

    # From here the item is EXISTING_VISIBLE: the no-working-channel-lost case.
    if healthy_sibling_sources > 0:
        return False, (
            "a distinct source of this channel is still healthy; "
            "hiding the channel would discard a working route"
        )

    usable: List[Dict[str, Any]] = []
    for record in evidence or ():
        complete, missing = evidence_is_complete(record)
        if not complete:
            continue
        if evidence_contains_forbidden_material(record):
            continue
        verdict = str(record.get("verdict") or "")
        if not is_escalatable(verdict):
            continue
        scope = str(record.get("verdict_scope") or "")
        if not scope.startswith("global"):
            continue
        usable.append(record)

    if len(usable) < 2:
        return False, (
            "fewer than two complete, escalatable, globally scoped observations; "
            "unknown and non-escalatable evidence can never hide a visible channel"
        )

    windows = {str(r.get("observed_at")) for r in usable}
    if len(windows) < 2:
        return False, "all escalatable evidence comes from one time window"

    for i in range(len(usable)):
        for j in range(i + 1, len(usable)):
            if vantages_are_independent(
                usable[i].get("test_vantage") or {}, usable[j].get("test_vantage") or {}
            ):
                return True, (
                    "escalatable failure reproduced from two independent vantages "
                    "in separate time windows"
                )
    return False, (
        "no two observations come from measurably independent vantages; "
        "a scanner-side or provider-side view cannot stand in for a second vantage"
    )


# ---------------------------------------------------------------------------
# Browser-confirmation guard
# ---------------------------------------------------------------------------
#: Substrings in a browser failure reason that describe the observer's access
#: rather than the route.
VANTAGE_REASON_MARKERS = (
    "403",
    "401",
    "429",
    "451",
    "forbidden",
    "geo",
    "blocked",
    "dns",
    "err_name_not_resolved",
    "connect_timeout",
)

#: Substrings that describe a transient server or transport condition.
TRANSIENT_REASON_MARKERS = (
    "500",
    "502",
    "503",
    "504",
    "520",
    "521",
    "522",
    "523",
    "524",
    "tls",
    "ssl",
    "reset",
    "econnreset",
    "network_error",
    "proxy_error",
    "timeout_mid_stream",
)


def may_hide_from_browser_confirmation(
    *,
    confirmation: Dict[str, Any],
    distinct_source_count: int,
) -> Tuple[bool, str]:
    """Whether one browser-confirmation failure may remove a whole channel.

    A confirmation report records how many routes the player actually walked.
    Measured on the live report: entries carry ``planLength: 1`` and
    ``attemptsRun: 1`` for channels that publish three links, so the alternative
    sources were never attempted at all. Hiding the channel on that basis throws
    away routes nobody tested - which is how a channel holding a healthy backup
    ends up fully hidden.

    This is deliberately narrower than `may_hide`: it does not demand two
    independent vantages, because that would stop every hide until new
    infrastructure exists. It blocks only the cases where the evidence provably
    does not support removing the channel.
    """
    if not isinstance(confirmation, dict):
        return False, "no confirmation record"

    session = confirmation.get("session") or {}
    attempts = session.get("attemptsRun")
    plan_length = session.get("planLength")
    try:
        attempted = int(attempts)
    except (TypeError, ValueError):
        attempted = None
    try:
        planned = int(plan_length)
    except (TypeError, ValueError):
        planned = None

    sources = max(int(distinct_source_count or 0), 0)

    if sources > 1:
        if attempted is None or planned is None:
            return False, (
                "channel has multiple distinct sources and the confirmation does "
                "not record how many routes were attempted"
            )
        # The confirmation records COUNTS, never which routes ran, so the only
        # safe reading of "every source was tried" is an exhausted plan. A run
        # that stopped at 4 of 8 attempts may have exercised one source twice
        # and never reached the third.
        if attempted < planned:
            return False, (
                f"channel has {sources} distinct sources and the attempt plan was "
                f"abandoned after {attempted} of {planned} attempts; the report "
                "does not say which routes ran, so untested sources must not be "
                "discarded"
            )
        if attempted < sources:
            return False, (
                f"channel has {sources} distinct sources but only {attempted} "
                f"attempt(s) ran; untested sources must not be discarded"
            )

    blob = " ".join(
        str(confirmation.get(field) or "")
        for field in ("reason", "mediaError", "error", "status")
    ).lower()
    for marker in VANTAGE_REASON_MARKERS:
        if marker in blob:
            return False, f"failure is vantage-explainable ({marker}); not a route fault"
    for marker in TRANSIENT_REASON_MARKERS:
        if marker in blob:
            return False, f"failure is transient-explainable ({marker}); not yet a route fault"

    return True, "single-source channel, all routes attempted, non-vantage failure"
