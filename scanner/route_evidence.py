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

import datetime as _dt
import hashlib
import hmac
import json as _json
import os
import re
import urllib.parse
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


# ---------------------------------------------------------------------------
# Phase 0b locks. THE_EXCLUSIVE_UPDATE L7 names four values as deliberately
# unspecified until locked in writing, and forbids classifier code before that.
# They are loaded from config/phase0b-locks.json so the policy lives in one
# auditable place rather than being retyped into each classifier.
# ---------------------------------------------------------------------------
_LOCKS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config",
    "phase0b-locks.json",
)


def _load_locks() -> Dict[str, Any]:
    try:
        with open(_LOCKS_PATH, "r", encoding="utf-8") as handle:
            return _json.load(handle)
    except (OSError, ValueError):
        # No locks on disk means nothing is locked. Every consumer below treats
        # an absent lock as "undeclared", which makes verdicts weaker, never
        # stronger - an unreadable file must not be able to hide a channel.
        return {}


LOCKS: Dict[str, Any] = _load_locks()

#: True only when Phase 0b actually locked the four values.
LOCKS_DECLARED = bool(LOCKS.get("lock_version"))


def _lock(path: str, default: Any = None) -> Any:
    node: Any = LOCKS
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


#: The declared target-environment matrix. Empty when Phase 0b has not run,
#: which makes global scope unreachable via route (b) by construction.
DECLARED_TARGET_MATRIX: Tuple[str, ...] = tuple(
    _lock("target_environment_matrix.profiles", []) or ()
) if _lock("target_environment_matrix.declared") else ()

#: Locked persistence window. Absent locks give 0, and a 0 TTL can never mature
#: a candidate.
PERSISTENCE_TTL_SECONDS: float = float(_lock("persistence.ttl_seconds", 0) or 0)
PERSISTENCE_MIN_WINDOWS: int = int(_lock("persistence.min_separate_windows", 2) or 2)
PERSISTENCE_MIN_WINDOW_SEPARATION_SECONDS: float = float(
    _lock("persistence.min_window_separation_seconds", 0) or 0
)

#: Locked keyframe / media-window threshold. Absent locks give infinity, so no
#: keyframe verdict can be reached - the "no classifier before 0b" rule.
KEYFRAME_MIN_MEDIA_CLOCK_SECONDS: float = float(
    _lock("keyframe_media_window.min_media_clock_seconds", float("inf"))
)

# ---------------------------------------------------------------------------
# Source identity. The allowlist is CLOSED: a query parameter is removed only
# if it is named here. 22 published channels share one host+path and differ
# only by "?id=NNN", so dropping unknown parameters would fuse 22 distinct
# channels into one identity.
# ---------------------------------------------------------------------------
REMOVABLE_QUERY_PARAMS = frozenset(
    _lock(
        "normalization_allowlist.removable_query_params",
        [
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
        ],
    )
)

#: The allowlist is only authoritative once Phase 0b locked it. Before that the
#: same names are used, but the policy is recorded as unlocked.
NORMALIZATION_ALLOWLIST_LOCKED = bool(
    _lock("normalization_allowlist.policy") == "closed" and LOCKS_DECLARED
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

#: Fatal-error signatures that describe the DECODER rather than the route.
#: Measured on Zee Bangla, 2026-08-23, real Chromium, full 120 s window: audio
#: decoded (384 bytes) while the video decoder produced zero frames and the
#: media element reported error code 3 (MEDIA_ERR_DECODE), followed by repeated
#: MediaMSEError code 11 appendBuffer failures because the element had already
#: errored. That matches the Phase 0 structural finding for this route exactly:
#: 1920x1080 INTERLACED H.264 (frame_mbs_only = 0) with ZERO IDR frames, only
#: open-GOP I-slices. The bytes arrive perfectly; this browser cannot decode
#: them. Classifying that as a route failure would hide a channel the owner
#: watches, which is the exact mistake this module exists to prevent.
DECODE_CAPABILITY_MARKERS = (
    "media element error code 3",
    "media element error code 4",
    "media_err_decode",
    "media_err_src_not_supported",
    "mediamseerror",
    "appendbuffer",
    # hls.js spells the same condition the other way round, so the plain
    # "appendbuffer" marker missed it - measured on Asian TV, Phase 1, which
    # classified as an escalatable route failure on a SourceBuffer rejection.
    # These three are all "the decoder would not take these bytes", which is a
    # statement about this browser's MSE implementation, not about the origin.
    "bufferappenderror",
    "bufferaddcodecerror",
    "bufferincompatiblecodecserror",
    "not supported",
    "unsupported",
    "decode error",
    "decodererror",
    "codec",
)


#: Fatal-error signatures that describe the NETWORK, not the route's health.
#: Measured on the Phase 5 movie run: a single mpegts NetworkError "Failed to
#: fetch" was classifying as PLAYBACK_FAIL, the strongest escalatable class,
#: which reaches hard_disqualified from two observations. One failed fetch is
#: not a dead route - the whole reason advisory:transient_network exists is that
#: a genuinely dead origin and a momentary network fault look identical once,
#: and only persistence over the locked window tells them apart.
TRANSIENT_ERROR_MARKERS = (
    "failed to fetch",
    "networkerror",
    "network_error",
    "err_network",
    "err_connection",
    "err_timed_out",
    "fragloaderror",
    "levelloaderror",
    "manifestloaderror",
    "keyloaderror",
    "timeout",
    "econnreset",
    "connection reset",
    "tls",
    "ssl",
    " 500",
    " 502",
    " 503",
    " 504",
)


#: Fatal-error signatures that describe the OBSERVER's access, not the route.
#: Measured on the Phase 5 movie run: an mpegts
#: HttpStatusCodeInvalid {"code":403} was classifying as
#: advisory:transient_network, which IS escalatable - so a geo-block from this
#: egress could have accumulated toward disqualifying a route that works
#: perfectly for the audience. A published, owner-working channel was measured
#: returning 403 three times from this vantage; that is the founding measurement
#: of this whole module, and the transient marker list had quietly undone it.
VANTAGE_ERROR_STATUS_PATTERN = re.compile(
    r'"?code"?\s*[:=]\s*"?(401|403|429|451)\b|\b(?:http\s*)?(401|403|429|451)\b',
    re.IGNORECASE,
)

VANTAGE_ERROR_MARKERS = (
    "forbidden",
    "unauthorized",
    "geo",
    "geoblock",
    "not available in your",
    "too many requests",
)


def describes_vantage_block(errors: Iterable[Any]) -> bool:
    """Whether a fatal-error list is about the observer's access."""
    blob = " ".join(str(e or "") for e in (errors or ())).lower()
    if not blob:
        return False
    if VANTAGE_ERROR_STATUS_PATTERN.search(blob):
        return True
    return any(marker in blob for marker in VANTAGE_ERROR_MARKERS)


def describes_transient_network(errors: Iterable[Any]) -> bool:
    """Whether a fatal-error list is about the network rather than the route."""
    blob = " ".join(str(e or "") for e in (errors or ())).lower()
    if not blob:
        return False
    return any(marker in blob for marker in TRANSIENT_ERROR_MARKERS)


def describes_decoder_capability(errors: Iterable[Any]) -> bool:
    """Whether a fatal-error list is about this decoder, not about the route."""
    blob = " ".join(str(e or "") for e in (errors or ())).lower()
    if not blob:
        return False
    return any(marker in blob for marker in DECODE_CAPABILITY_MARKERS)


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


#: Environment variable carrying the keyed-identity secret. Nothing read a key
#: from anywhere before this, so `failure_domain_tenant` was unconditionally
#: None and adding the repository secret would have changed nothing. The name is
#: paired with ROUTE_IDENTITY_HMAC_KEY_ID so a rotation can be identified in the
#: records it produced.
HMAC_KEY_ENV = "ROUTE_IDENTITY_HMAC_KEY"
HMAC_KEY_ID_ENV = "ROUTE_IDENTITY_HMAC_KEY_ID"

#: The shortest key worth accepting. A key below this is treated as absent
#: rather than used, because a weak keyed id is worse than an honest `unknown`:
#: it looks like an identity while being trivially reversible.
MIN_HMAC_KEY_BYTES = 16


def configured_hmac_key() -> Optional[bytes]:
    """The keyed-identity secret from the environment, or None.

    None is a supported state, not a failure. Every consumer reports `unknown`
    without a key, and `unknown` can never hide a channel - so a missing secret
    makes the model weaker, never wrong.
    """
    raw = os.environ.get(HMAC_KEY_ENV) or ""
    encoded = raw.strip().encode("utf-8")
    if len(encoded) < MIN_HMAC_KEY_BYTES:
        return None
    return encoded


def configured_hmac_key_id() -> Optional[str]:
    """Which key produced a record, so a rotation stays traceable."""
    value = str(os.environ.get(HMAC_KEY_ID_ENV) or "").strip()
    return value or None


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


#: Grouping salt, generated per process. `failure_domain_tenant` is an HMAC and
#: is therefore None whenever no key is configured - which would collapse every
#: determined tenant on one provider into the single pair (provider, None) and
#: report two independent tenants as redundancy 1. Undercounting redundancy
#: makes hiding EASIER, so correlation and redundancy must keep using a value
#: that distinguishes tenants even with no key present. This salt makes that
#: value distinguishing in memory and meaningless if it is ever persisted, so it
#: can never become a stable identifier by accident.
_GROUPING_SALT = os.urandom(16)


def tenant_grouping_key(tenant: Optional[str]) -> Optional[str]:
    """In-memory only key that distinguishes tenants without identifying them.

    Never write this into an evidence record: it is deliberately unstable across
    processes so it cannot be used as an identity.
    """
    if not tenant:
        return None
    return hmac.new(_GROUPING_SALT, tenant.encode("utf-8"), hashlib.sha256).hexdigest()[:16]


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
        # Grouping only, never committed. See tenant_grouping_key.
        "tenant_grouping_key": tenant_grouping_key(tenant),
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

    if progress is None:
        return UNKNOWN, ["incomplete playback metrics"]
    if startup is None:
        # Startup was watched for the whole window and never happened. That is a
        # measurement, not a missing field, and calling it `unknown` hid a
        # decoder failure we had actually observed: the Zee Bangla Phase 1 run
        # reported MEDIA_ERR_DECODE with the media clock frozen at zero, and the
        # earlier version returned `unknown` for it. Treated as "startup never
        # reached" so the FAIL floor below can see it - which then routes a
        # decoder signature to the non-escalatable device verdict.
        startup = float("inf")
        reasons.append("playback never started within the window")

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
        # A decoder that cannot handle the bytes is a statement about this
        # browser, not about the stream's availability. The escalation matrix
        # makes advisory:device_or_browser_unsupported non-escalatable and caps
        # it at environment scope, so a channel can never be removed on it.
        if describes_decoder_capability(fatal):
            reasons.append(
                "fatal error describes the decoder, not the route; "
                "capped at environment scope and never escalatable"
            )
            return ADVISORY_DEVICE_UNSUPPORTED, reasons
        if describes_vantage_block(fatal):
            # Never escalatable and capped at vantage scope: a thousand blocks
            # from one egress are a thousand statements about the egress.
            reasons.append(
                "fatal error describes the observer's access, not the route; "
                "capped at vantage scope and never escalatable"
            )
            return ADVISORY_VANTAGE_BLOCKED, reasons
        if describes_transient_network(fatal):
            # Still escalatable, but only through the persistence window: one
            # failed fetch cannot reach hard_disqualified on its own.
            reasons.append(
                "fatal error describes the network; escalatable only through "
                "the locked persistence window, never on a single observation"
            )
            return ADVISORY_TRANSIENT_NETWORK, reasons
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
    declared_matrix: Optional[Sequence[str]] = None,
    failed_profiles: Sequence[str] = (),
) -> str:
    """How widely a verdict may be applied.

    Two arbitrary browser profiles are not the world: this project targets
    TV-class devices explicitly, so global scope needs either an
    environment-independent failure or the complete declared target matrix.

    `declared_matrix=None` uses the matrix locked in Phase 0b; an explicit empty
    sequence means the matrix is undeclared, which makes route (b) to global
    scope unreachable by construction.
    """
    if declared_matrix is None:
        declared_matrix = DECLARED_TARGET_MATRIX
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
        # Grouped on tenant_grouping_key rather than failure_domain_tenant: the
        # latter is None without an HMAC key, which would fuse every tenant on a
        # shared CDN and report real redundancy as 1.
        pairs.add((domain["failure_domain_provider"], domain["tenant_grouping_key"]))
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
# Tenant correlation (Correction 4)
# ---------------------------------------------------------------------------
#: Observation kinds that are explicitly not a sustained success. Named rather
#: than inferred, because the whole investigation began with HTTP 200 being read
#: as working playback.
NON_RESETTING_OBSERVATION_KINDS = frozenset(
    {
        "http_status",
        "head_request",
        "manifest_fetch",
        "playlist_fetch",
        "byte_sample",
        "short_probe",
        "partial_startup",
        "startup_then_failure",
    }
)

#: The only two kinds that can reset a persistence counter.
RESETTING_OBSERVATION_KINDS = frozenset(
    {"full_playback_session", "sustained_media_delivery"}
)


def correlation_group(url: str, key: Optional[bytes] = None) -> Any:
    """The correlated-event key for a route, or `unknown`.

    An undetermined tenant means the route may neither be grouped into a
    correlated event nor excluded from one, so this returns `unknown` instead of
    inventing a group. `cache.devm3u.top` is the backup host for four of the
    channels under investigation and lands here by measurement, not by choice.
    """
    domain = failure_domain(url, key)
    if not domain["tenant_determined"]:
        return UNKNOWN
    return "{0}|{1}".format(
        domain["failure_domain_provider"], domain["tenant_grouping_key"]
    )


def correlated_event(
    routes: Iterable[Dict[str, Any]], key: Optional[bytes] = None
) -> Dict[str, Any]:
    """Group routes into correlated events, keeping undetermined ones apart.

    Returns the determined groups plus the routes whose tenancy could not be
    derived. Those are reported as `unknown`: never merged into a group, never
    counted as their own group, and never reported as 0 or 1.
    """
    groups: Dict[str, List[str]] = {}
    undetermined: List[str] = []
    for route in routes or ():
        url = (route or {}).get("url")
        if not url:
            continue
        group = correlation_group(url, key)
        if group == UNKNOWN:
            undetermined.append(redact_public_template(url))
            continue
        groups.setdefault(group, []).append(redact_public_template(url))
    return {
        "groups": groups,
        "determined_group_count": len(groups),
        "undetermined_routes": undetermined,
        "undetermined_count": len(undetermined),
        # A caller that wants one number must be told it cannot have one.
        "correlation": UNKNOWN if undetermined else len(groups),
    }


# ---------------------------------------------------------------------------
# Persistence counter (Correction 3)
# ---------------------------------------------------------------------------
def _parse_observed_at(value: Any) -> Optional[float]:
    """Epoch seconds from an ISO-8601 timestamp or a number."""
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = _dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    return parsed.timestamp()


def resets_persistence(observation: Dict[str, Any]) -> Tuple[bool, str]:
    """Whether one observation is a FULL SUSTAINED SUCCESS.

    Only two things qualify: a complete 120 s PASS, or an equally long
    continuous media-delivery measurement where a browser session is not
    available. Everything the loose reading would have accepted - a 200, a
    manifest, the 16 KiB sample, a truncated window, a startup that then failed
    - is rejected by name.
    """
    if not isinstance(observation, dict):
        return False, "not an observation record"

    kind = str(observation.get("kind") or "").strip().lower()
    if kind in NON_RESETTING_OBSERVATION_KINDS:
        return False, f"observation kind '{kind}' is not a sustained success"

    window = observation.get("window_seconds")
    try:
        window_seconds = float(window)
    except (TypeError, ValueError):
        window_seconds = 0.0
    if window_seconds < WINDOW_SECONDS:
        return False, (
            f"observed window {window_seconds:.0f}s is shorter than the "
            f"required {WINDOW_SECONDS:.0f}s"
        )

    if kind == "sustained_media_delivery":
        # The browserless equivalent: continuous delivery for the same window.
        gap = observation.get("max_delivery_gap_seconds")
        try:
            max_gap = float(gap)
        except (TypeError, ValueError):
            return False, "sustained delivery claimed without a measured gap"
        if max_gap > STALL_MIN_STAGNANT_SECONDS:
            return False, (
                f"delivery gap {max_gap:.1f}s exceeds the "
                f"{STALL_MIN_STAGNANT_SECONDS:.0f}s continuity limit"
            )
        if observation.get("fatal_errors"):
            return False, "delivery interrupted by a fatal error"
        return True, "continuous media delivery across the full window"

    if kind and kind not in RESETTING_OBSERVATION_KINDS:
        return False, f"observation kind '{kind}' is not a sustained success"

    verdict, reasons = classify_playback(observation.get("playback_metrics") or {})
    if verdict == PROVEN:
        return True, "complete 120 s PASS"
    return False, "; ".join(reasons) or "did not meet the PASS floor"


def persistence_state(
    observations: Sequence[Dict[str, Any]],
    *,
    now: Optional[float] = None,
    ttl_seconds: Optional[float] = None,
) -> Dict[str, Any]:
    """Whether repeated escalatable evidence has matured into a candidate.

    Maturity needs all of: escalatable evidence only, at least the locked number
    of separate time windows, windows separated by more than the measured cache
    TTL, everything inside the locked persistence TTL, and no full sustained
    success anywhere in that span. Any full sustained success resets the counter
    to zero and discards everything before it.
    """
    ttl = PERSISTENCE_TTL_SECONDS if ttl_seconds is None else float(ttl_seconds)
    records = [o for o in (observations or ()) if isinstance(o, dict)]
    stamped: List[Tuple[float, Dict[str, Any]]] = []
    undated = 0
    for record in records:
        moment = _parse_observed_at(record.get("observed_at"))
        if moment is None:
            undated += 1
            continue
        stamped.append((moment, record))
    stamped.sort(key=lambda pair: pair[0])

    reference = now if now is not None else (stamped[-1][0] if stamped else 0.0)

    # A reset discards everything at or before it.
    reset_at: Optional[float] = None
    reset_reason = ""
    for moment, record in stamped:
        resets, why = resets_persistence(record)
        if resets:
            reset_at = moment
            reset_reason = why

    considered = [
        (moment, record)
        for moment, record in stamped
        if (reset_at is None or moment > reset_at) and (reference - moment) <= ttl
    ]

    escalatable = [
        (moment, record)
        for moment, record in considered
        if is_escalatable(str(record.get("verdict") or ""))
    ]
    non_escalatable = len(considered) - len(escalatable)

    # Distinct windows: greedily keep observations that are far enough apart to
    # not be one cached response counted twice.
    windows: List[float] = []
    for moment, _record in escalatable:
        if not windows or (moment - windows[-1]) >= PERSISTENCE_MIN_WINDOW_SEPARATION_SECONDS:
            windows.append(moment)

    span = (windows[-1] - windows[0]) if len(windows) >= 2 else 0.0
    reasons: List[str] = []
    state = UNKNOWN

    if not LOCKS_DECLARED or ttl <= 0:
        reasons.append(
            "persistence TTL is not locked; no observation can mature into a candidate"
        )
    elif not escalatable:
        reasons.append(
            "no escalatable evidence in the span"
            + (f" ({non_escalatable} non-escalatable observation(s) ignored)" if non_escalatable else "")
        )
    elif len(windows) < PERSISTENCE_MIN_WINDOWS:
        reasons.append(
            f"{len(windows)} separate window(s), {PERSISTENCE_MIN_WINDOWS} required "
            f"at >= {PERSISTENCE_MIN_WINDOW_SEPARATION_SECONDS:.0f}s apart"
        )
    else:
        state = PERSISTENT_UNAVAILABLE_CANDIDATE
        reasons.append(
            f"escalatable evidence in {len(windows)} separate windows spanning "
            f"{span:.0f}s with no full sustained success"
        )

    if reset_at is not None:
        reasons.append(f"counter reset by a full sustained success ({reset_reason})")
    if undated:
        reasons.append(f"{undated} observation(s) ignored for having no timestamp")

    return {
        "state": state,
        "counter": len(windows),
        "escalatable_observations": len(escalatable),
        "non_escalatable_ignored": non_escalatable,
        "window_span_seconds": span,
        "ttl_seconds": ttl,
        "reset_at": reset_at,
        "reset_reason": reset_reason,
        "locked": LOCKS_DECLARED,
        "reasons": reasons,
    }


# ---------------------------------------------------------------------------
# Keyframe / media-window classifier. Permitted only because Phase 0b locked
# the threshold; without a lock the floor is infinite and every answer is
# `unknown`, which is the "no classifier code before 0b" rule expressed in code.
# ---------------------------------------------------------------------------
def keyframe_verdict(
    *,
    media_clock_seconds: Any,
    keyframes_observed: Any = None,
    open_gop_intra_observed: Any = None,
) -> Tuple[str, str]:
    """Structural keyframe finding, or `unknown` below the locked floor."""
    try:
        clock = float(media_clock_seconds)
    except (TypeError, ValueError):
        return UNKNOWN, "no measured media clock"
    if clock < KEYFRAME_MIN_MEDIA_CLOCK_SECONDS:
        return UNKNOWN, (
            f"observed {clock:.1f}s of media clock, below the locked "
            f"{KEYFRAME_MIN_MEDIA_CLOCK_SECONDS:.0f}s floor"
        )
    try:
        keyframes = int(keyframes_observed)
    except (TypeError, ValueError):
        return UNKNOWN, "keyframe count not measured"
    if keyframes > 0:
        return PROVEN, f"{keyframes} keyframe(s) in {clock:.1f}s of media clock"
    try:
        intra = int(open_gop_intra_observed)
    except (TypeError, ValueError):
        intra = 0
    if intra > 0:
        # A structural risk, not a failure: it is why this stream needs a
        # different player configuration, not why it should be hidden.
        return ADVISORY_STRUCTURALLY_RISKY, (
            f"no IDR keyframe in {clock:.1f}s, but {intra} open-GOP intra "
            "slice(s) present; recoverable with an adjusted player, not a fault"
        )
    return ADVISORY_STRUCTURALLY_RISKY, (
        f"no keyframe and no intra slice in {clock:.1f}s of media clock"
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
    """Reject a record that would commit a credential or an unstable key."""
    if isinstance(record, dict) and "tenant_grouping_key" in record:
        # Process-local and unstable: committing it would create an identifier
        # that silently changes meaning between runs.
        return True
    try:
        import json as _json

        blob = _json.dumps(record, ensure_ascii=False)
    except (TypeError, ValueError):
        blob = str(record)
    if FORBIDDEN_EVIDENCE_PATTERN.search(blob):
        return True
    # A long opaque run is the signature of an embedded credential, but the
    # plain length rule fired on ordinary prose: the Phase 0 sentence
    # "TV/AndroidTV/AFT/SmartTV/BRAVIA/MiBOX/TV" is a 40-character run of this
    # very character class. Since flush() withholds a report that trips this
    # check, a false positive silently destroys a legitimate report - so the
    # run must also look opaque rather than like words, which in practice means
    # carrying at least one digit. Every opaque run measured in the real stream
    # URLs of this repository (132-161 characters) satisfies that.
    for match in re.finditer(r"[A-Za-z0-9+/=_-]{40,}", blob):
        token = match.group()
        if any(character.isdigit() for character in token):
            return True
    return False


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
