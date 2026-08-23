"""Audit-only wiring of the route-evidence model into every hide path.

Phase 2 of THE_EXCLUSIVE_UPDATE is visibility-invariant: the model is connected
to each site that can remove an item from the public catalogue, it computes what
it WOULD decide, and it changes nothing. That ordering is deliberate. The hide
paths in this scanner predate the model by a long way and hide on name-keyed
ledgers, single-vantage HTTP status and single-attempt browser runs; switching
them over blind would move hundreds of items in one commit, in both directions,
with no measurement of what moved or why.

So this module answers one question per hide, in writing, before anything is
rewired: does the evidence this site is acting on actually support removing this
item? The answer is written to reports/visibility-model-audit.json and nowhere
else. No caller's behaviour depends on the return value.

Reading the audit: `model_would_hide: false` does NOT mean the item is fine. It
means the evidence recorded at that call site is not sufficient, by itself, to
prove the item is broken - which is usually because the site never collected
per-route, multi-vantage, multi-window evidence in the first place. The count of
those is the size of the gap between what this scanner currently acts on and
what the model requires.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

from scanner import persistence_store
from scanner import route_evidence as rev

#: Where the audit lands. Reports only; never read back by the scanner.
DEFAULT_AUDIT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "reports",
    "visibility-model-audit.json",
)

#: Every recorded decision this process has seen.
_LEDGER: List[Dict[str, Any]] = []

#: Hard cap so a full scan cannot grow the ledger without bound. The counters in
#: the summary keep counting after the cap; only the per-item detail stops.
MAX_DETAIL_ROWS = 400


def _routes(item: Dict[str, Any]) -> List[Dict[str, Any]]:
    routes = [{"url": item.get("url")}]
    for backup in item.get("backups") or []:
        url = backup.get("url") if isinstance(backup, dict) else backup
        if url:
            routes.append({"url": url})
    return [r for r in routes if r.get("url")]


def _state_of(item: Dict[str, Any]) -> str:
    """Three-state classification as it stands BEFORE this hide site acts."""
    already_hidden = item.get("publish_allowed") is False
    return rev.three_state(
        is_published=not already_hidden,
        is_legacy_hidden=already_hidden,
    )


def audit_hide(
    site: str,
    item: Dict[str, Any],
    *,
    kind: str = "",
    reason: str = "",
    status: Any = None,
    error_kind: str = "",
    content_type: str = "",
    evidence: Sequence[Dict[str, Any]] = (),
    browser_profile: str = "",
    failed_profiles: Sequence[str] = (),
    healthy_sibling_sources: int = 0,
) -> Dict[str, Any]:
    """Record what the model would decide about one hide. Changes nothing.

    Deliberately total: any exception is swallowed by the caller-side wrapper
    below, because an auditing mistake must never be able to fail a scan or,
    worse, alter what gets published.
    """
    routes = _routes(item)
    state = _state_of(item)
    transport = rev.classify_transport(
        status, error_kind=error_kind, content_type=content_type
    )
    scope = rev.resolve_verdict_scope(
        transport,
        browser_profile=browser_profile,
        failed_profiles=failed_profiles,
    )
    allowed, why = rev.may_hide(
        state=state,
        evidence=evidence,
        healthy_sibling_sources=healthy_sibling_sources,
    )
    # Use the configured key when one exists, so adding the repository secret
    # actually changes the output instead of being inert.
    hmac_key = rev.configured_hmac_key()
    correlation = rev.correlated_event(routes, hmac_key)

    # Feed the cross-run store, then read back what it now says. Until this
    # existed nothing wrote observations anywhere, so persistence_state could
    # only ever see one run and the escalation path was unreachable in practice -
    # the counter was implemented and permanently stuck at one.
    #
    # The route id is the normalized source identity, so a rotating token or
    # cache-buster does not look like a different route and reset the history.
    # Recording happens even when the model would keep the item, because the
    # absence of a failure is exactly what a later window needs to know about.
    persistence: Dict[str, Any] = {"state": rev.UNKNOWN, "counter": 0}
    route_id = ""
    try:
        primary = (routes[0] or {}).get("url") if routes else ""
        if primary:
            route_id = rev.normalize_source_identity(str(primary))
        if route_id:
            persistence_store.record(
                route_id,
                {
                    "observed_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
                    "verdict": transport,
                    "kind": "http_status",
                },
            )
            persistence = persistence_store.state_for(route_id)
    except Exception:  # noqa: BLE001 - the store must never break an audit
        pass

    decision = {
        "site": site,
        "kind": kind or str(item.get("_sourceKind") or ""),
        "name": str(item.get("name") or item.get("title") or ""),
        "three_state": state,
        "distinct_sources": rev.distinct_sources(routes),
        "independent_redundancy": rev.independent_redundancy(routes, hmac_key),
        "hmac_key_id": rev.configured_hmac_key_id(),
        "correlation": correlation["correlation"],
        "tenant_undetermined_routes": correlation["undetermined_count"],
        "transport_class": transport,
        "escalatable": rev.is_escalatable(transport),
        "verdict_scope": scope,
        "site_reason": str(reason or "")[:200],
        "persistence_state": persistence.get("state"),
        "persistence_counter": persistence.get("counter"),
        "model_would_hide": bool(allowed),
        "model_reason": why,
        "evidence_records_supplied": len(list(evidence or ())),
    }
    if len(_LEDGER) < MAX_DETAIL_ROWS:
        _LEDGER.append(decision)
    else:
        _LEDGER.append({"site": site, "model_would_hide": bool(allowed), "_truncated": True})
    return decision


#: When True the model's decision is ENFORCED, not merely recorded: a hide that
#: `may_hide` rejects does not happen.
#:
#: Off, and this is now a measured decision rather than caution. Turning it on
#: broke seven existing contract tests, and reading them showed they were right
#: to break: they require hides that ARE justified. "An item with no reachable
#: route at all is hidden" is the clearest - that is a structural finding across
#: every route a channel has, not one vantage disagreeing about one route.
#: `may_hide` refuses it anyway, because it demands two independent vantages for
#: anything, and blanket enforcement therefore stops legitimate hides along with
#: the illegitimate ones.
#:
#: The protection that actually mattered is in place by a narrower route: an item
#: with sustained-playback proof is exempt at each hide site (see
#: scanner/sustained_proof.py), which is what keeps the seven restored channels.
#: That is targeted at the failure that was measured, instead of switching off
#: hiding in general.
#:
#: Turning this on becomes correct once real two-vantage evidence records are
#: being collected per route - vantage independence itself is now measured
#: (reports/vantage-independence.json), so the remaining piece is the evidence
#: pipeline, not the network.
ENFORCE_MODEL_DECISION = False


def model_permits_hide(
    site: str,
    item: Dict[str, Any],
    *,
    healthy_sibling_sources: int = 0,
    evidence: Sequence[Dict[str, Any]] = (),
) -> Tuple[bool, str]:
    """Whether the model allows this item to be hidden.

    Returns (allowed, reason). With enforcement off it always allows, so the
    caller's behaviour is unchanged and only the audit records the difference.
    """
    try:
        decision = audit_hide(
            site,
            item,
            evidence=evidence,
            healthy_sibling_sources=healthy_sibling_sources,
        )
    except Exception:  # noqa: BLE001 - a model failure must not block a scan
        return True, "model unavailable; caller behaviour unchanged"
    if not ENFORCE_MODEL_DECISION:
        return True, "audit-only mode; decision recorded but not enforced"
    if decision.get("model_would_hide"):
        return True, str(decision.get("model_reason") or "model permits")
    return False, str(decision.get("model_reason") or "model refuses")


def audit_hide_safe(site: str, item: Dict[str, Any], **kwargs: Any) -> None:
    """`audit_hide` that can never raise. This is what hide paths call."""
    try:
        if isinstance(item, dict):
            audit_hide(site, item, **kwargs)
    except Exception:  # noqa: BLE001 - auditing must never break a scan
        pass


def summary() -> Dict[str, Any]:
    rows = [r for r in _LEDGER if not r.get("_truncated")]
    per_site: Dict[str, Dict[str, int]] = {}
    for row in _LEDGER:
        bucket = per_site.setdefault(
            str(row.get("site")), {"total": 0, "model_would_hide": 0, "model_would_keep": 0}
        )
        bucket["total"] += 1
        if row.get("model_would_hide"):
            bucket["model_would_hide"] += 1
        else:
            bucket["model_would_keep"] += 1
    return {
        "mode": "audit_only",
        "note": (
            "Advisory. No value here changed any item's visibility. "
            "model_would_hide=false means the evidence this site acted on does "
            "not by itself support removing the item."
        ),
        "hmac_key": {
            "configured": rev.configured_hmac_key() is not None,
            "key_id": rev.configured_hmac_key_id(),
            "env_var": rev.HMAC_KEY_ENV,
            "note": (
                "A null key_id here means the secret was not in the environment "
                "that produced this report - which is the normal state for a "
                "local run, since the secret lives in the CI environment. It is "
                "not a failure: without a key every keyed field reports "
                "'unknown', and 'unknown' can never hide anything."
            ),
        },
        "locks": {
            "declared": rev.LOCKS_DECLARED,
            "target_matrix": list(rev.DECLARED_TARGET_MATRIX),
            "persistence_ttl_seconds": rev.PERSISTENCE_TTL_SECONDS,
            "keyframe_min_media_clock_seconds": rev.KEYFRAME_MIN_MEDIA_CLOCK_SECONDS,
        },
        "decisions_seen": len(_LEDGER),
        "model_would_hide": sum(1 for r in _LEDGER if r.get("model_would_hide")),
        "model_would_keep": sum(1 for r in _LEDGER if not r.get("model_would_hide")),
        "per_site": per_site,
        "detail_rows_recorded": len(rows),
        "detail_truncated_at": MAX_DETAIL_ROWS if len(_LEDGER) > MAX_DETAIL_ROWS else None,
        "decisions": rows,
    }


def flush(path: Optional[str] = None, provenance: str = "") -> Optional[str]:
    """Write the audit out. Returns the path, or None when nothing was seen.

    `provenance` records HOW the hide paths were reached, because the counts are
    meaningless without it: calling a hide function directly over the whole
    catalogue is not the same as a scan reaching it through its normal gating,
    and reporting the first as if it were the second would overstate how many
    items are at risk.
    """
    if not _LEDGER:
        return None
    target = path or DEFAULT_AUDIT_PATH
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        payload = summary()
        payload["provenance"] = provenance or "not recorded"
        if rev.evidence_contains_forbidden_material(payload):
            # Drop the offending rows rather than the whole report. Blanking
            # everything was the earlier behaviour and it destroyed the audit on
            # a single bad row - and worse, a false positive in the credential
            # check would have destroyed a perfectly clean one.
            kept, dropped = [], []
            for row in payload.get("decisions") or ():
                if rev.evidence_contains_forbidden_material(row):
                    # Record only the SITE, never the row's own text. Echoing
                    # the offending name back into the payload puts the
                    # credential straight back in - measured, on the first
                    # version of this branch.
                    dropped.append(str(row.get("site") or "?"))
                else:
                    kept.append(row)
            payload["decisions"] = kept
            payload["rows_withheld_for_forbidden_material"] = len(dropped)
            payload["rows_withheld_sites"] = sorted(set(dropped))[:20]
            if rev.evidence_contains_forbidden_material(payload):
                # Still tripping after row removal: the problem is in the
                # summary itself, so withhold the detail and say exactly that.
                payload = {
                    "mode": "audit_only",
                    "error": (
                        "audit detail withheld: the summary itself contained "
                        "forbidden material"
                    ),
                    "provenance": provenance or "not recorded",
                    "decisions_seen": len(_LEDGER),
                }
        with open(target, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        return target
    except OSError:
        return None


def reset() -> None:
    """Clear the ledger. For tests."""
    _LEDGER.clear()
