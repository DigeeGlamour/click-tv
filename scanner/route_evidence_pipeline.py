"""Build per-route evidence records from two measured vantages.

`may_hide` has always required escalatable evidence for the same route from two
measurably independent vantages in separate time windows. That condition was
unreachable, so blanket enforcement of the model refused every hide - including
the legitimate ones, which broke seven contract tests when it was tried.

Vantage independence is now measured (reports/vantage-independence.json shows
hosts the scanner cannot reach at all while the proxy returns 200), so the
missing piece is this: something that turns two observations into the complete,
scoped evidence records `may_hide` reads. That is what this module does, and it
does only that - it never decides visibility.

Two rules shape it, both learned the hard way:

  * An observation is recorded per ROUTE, not per item. A channel with three
    sources is not disqualified because one of them is unreachable, and evidence
    keyed to the item cannot express that.
  * A record is only as strong as the field it is missing. Anything incomplete
    is dropped rather than filled in, because `evidence_is_complete` treats a
    missing field as `unknown`, and `unknown` must never be able to hide
    anything.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any, Dict, List, Optional, Sequence

from scanner import route_evidence as rev

#: What each vantage is called in the records it produces. These names end up in
#: `test_vantage`, which `vantages_are_independent` compares - so they carry the
#: asn/provider that makes two observations count as two.
SCANNER_VANTAGE = {
    "id": "scanner_egress",
    "asn": "AS8075",
    "provider": "microsoft",
}
PROXY_VANTAGE = {
    "id": "proxy_egress",
    "asn": "AS13335",
    "provider": "cloudflare",
}


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def build_record(
    *,
    route_url: str,
    vantage: Dict[str, Any],
    status: Any = None,
    error_kind: str = "",
    content_type: str = "",
    playback_metrics: Optional[Dict[str, Any]] = None,
    delivery_path: str = "",
    browser_profile: str = "",
    failed_profiles: Sequence[str] = (),
    observed_at: str = "",
    hmac_key: Optional[bytes] = None,
) -> Optional[Dict[str, Any]]:
    """One evidence record for one route from one vantage, or None.

    None when the inputs cannot produce a complete record. Returning a partial
    record would be worse than returning nothing: `may_hide` would read the
    missing fields as `unknown` and the caller would believe evidence exists.
    """
    if not str(route_url or "").strip():
        return None

    domain = rev.failure_domain(route_url, hmac_key)
    if playback_metrics:
        verdict, _reasons = rev.classify_playback(
            playback_metrics, delivery_path=delivery_path
        )
    else:
        verdict = rev.classify_transport(
            status, error_kind=error_kind, content_type=content_type
        )

    scope = rev.resolve_verdict_scope(
        verdict,
        browser_profile=browser_profile,
        vantage_id=str(vantage.get("id") or ""),
        failed_profiles=failed_profiles,
    )

    record = {
        "route_id": rev.normalize_source_identity(route_url),
        "url_public_template": rev.redact_public_template(route_url),
        "url_registrable_domain": domain["failure_domain_provider"],
        # The final origin is not followed here; recording the request target as
        # the origin would be a claim this pipeline has not measured.
        "final_origin_public_template": rev.redact_public_template(route_url),
        "final_origin_registrable_domain": domain["failure_domain_provider"],
        "failure_domain_provider": domain["failure_domain_provider"],
        "failure_domain_tenant": domain["failure_domain_tenant"],
        "delivery_path": delivery_path or "direct",
        "browser_profile": browser_profile or "none",
        "test_vantage": {
            "id": vantage.get("id"),
            "asn": vantage.get("asn"),
            "provider": vantage.get("provider"),
        },
        "media_fingerprint": (playback_metrics or {}).get("media_fingerprint")
        or {"measured": False},
        "playback_metrics": playback_metrics or {"transport_only": True},
        "observed_at": observed_at or _now_iso(),
        "ttl": rev.PERSISTENCE_TTL_SECONDS,
        "verdict": verdict,
        "verdict_scope": scope,
        "hmac_key_id": rev.configured_hmac_key_id(),
    }

    complete, missing = rev.evidence_is_complete(record)
    if not complete:
        return None
    if rev.evidence_contains_forbidden_material(record):
        return None
    return record


def build_route_evidence(
    route_url: str,
    *,
    scanner: Dict[str, Any],
    proxy: Optional[Dict[str, Any]] = None,
    hmac_key: Optional[bytes] = None,
) -> List[Dict[str, Any]]:
    """Records for one route, one per vantage that reported.

    `scanner` and `proxy` are each a dict of the observation from that egress:
    status / error_kind / content_type, or playback_metrics for a browser run.
    """
    records = []
    first = build_record(
        route_url=route_url, vantage=SCANNER_VANTAGE, hmac_key=hmac_key,
        **{k: v for k, v in (scanner or {}).items()}
    )
    if first:
        records.append(first)
    if proxy:
        second = build_record(
            route_url=route_url, vantage=PROXY_VANTAGE, hmac_key=hmac_key,
            delivery_path="proxy",
            **{k: v for k, v in proxy.items() if k != "delivery_path"}
        )
        if second:
            records.append(second)
    return records


def evidence_supports_hide(records: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """What `may_hide` would make of these records, and why - without deciding.

    Reports the gap explicitly. "Two vantages present but the verdict is
    non-escalatable" and "escalatable but only one vantage" are different
    situations, and a caller that cannot tell them apart cannot act sensibly.
    """
    usable = [
        r for r in (records or ())
        if rev.evidence_is_complete(r)[0]
        and not rev.evidence_contains_forbidden_material(r)
    ]
    escalatable = [r for r in usable if rev.is_escalatable(str(r.get("verdict") or ""))]
    global_scoped = [
        r for r in escalatable if str(r.get("verdict_scope") or "").startswith("global")
    ]
    # Distinct timestamps are not distinct WINDOWS. Two records built in the
    # same call are microseconds apart and would otherwise satisfy "separate
    # time windows" instantly - which is precisely the cache-window mistake the
    # locked 120 s separation exists to prevent, since two reads inside one CDN
    # cache TTL can be one response counted twice.
    moments = sorted(
        m for m in (rev._parse_observed_at(r.get("observed_at")) for r in global_scoped)
        if m is not None
    )
    windows = []
    for moment in moments:
        if not windows or (moment - windows[-1]) >= rev.PERSISTENCE_MIN_WINDOW_SEPARATION_SECONDS:
            windows.append(moment)

    independent = False
    for i in range(len(global_scoped)):
        for j in range(i + 1, len(global_scoped)):
            if rev.vantages_are_independent(
                global_scoped[i].get("test_vantage") or {},
                global_scoped[j].get("test_vantage") or {},
            ):
                independent = True
                break
        if independent:
            break

    missing = []
    if len(escalatable) < 2:
        missing.append("fewer than two escalatable observations")
    if len(global_scoped) < 2:
        missing.append("fewer than two globally scoped observations")
    if len(windows) < 2:
        missing.append(
            "observations do not span two time windows at least "
            f"{rev.PERSISTENCE_MIN_WINDOW_SEPARATION_SECONDS:.0f}s apart"
        )
    if not independent:
        missing.append("no two observations from measurably independent vantages")

    return {
        "records": len(records or ()),
        "complete": len(usable),
        "escalatable": len(escalatable),
        "globally_scoped": len(global_scoped),
        "distinct_windows": len(windows),
        "independent_vantages": independent,
        "supports_hide": not missing,
        "missing": missing,
    }
