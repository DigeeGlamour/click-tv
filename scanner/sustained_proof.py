"""A durable record of items that passed the 120 s browser acceptance.

The fingerprint proof ledger cannot hold this evidence. Its key includes the
stream URL, so it stops matching the moment a source rotates - measured on
2026-08-20, when 29 Bangla channels reached verified_global and only 9 were
published, six of them in the ledger by name and lost only to a changed URL.

Putting the proof on the card does not work either. Measured directly: the scan
that ran after the seven channels were restored rebuilt each card from its
sources, and every field the restoration had written - the status, the mode, the
note - was gone. One backup URL had changed too. Anything stored on a card is
temporary by construction.

So the proof lives here, outside the card, keyed by (kind, name). Name keying is
a deliberate trade with a known weakness: a different stream could later reuse a
channel's name and inherit its proof. Two things make that acceptable. This
registry only ever GRANTS proof, so the failure mode is keeping something that
should have been re-tested, never hiding something that works - and hiding
something that works is the failure this whole system exists to prevent. And the
fingerprint at proof time is recorded alongside, so a name match on a changed
route is visible in the file rather than silent.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional, Tuple

DEFAULT_PROOF_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "state",
    "sustained-playback-proof.json",
)


def _key(kind: str, name: str) -> str:
    return "{0}|{1}".format(
        str(kind or "").strip().casefold(), str(name or "").strip().casefold()
    )


def load(path: Optional[str] = None) -> Dict[str, Any]:
    """Read the registry. An unreadable registry grants nothing."""
    target = path or DEFAULT_PROOF_PATH
    try:
        with open(target, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return {"version": 1, "proofs": {}}
    if not isinstance(payload, dict) or not isinstance(payload.get("proofs"), dict):
        return {"version": 1, "proofs": {}}
    return payload


def has_proof(
    item: Dict[str, Any], kind: str, registry: Optional[Dict[str, Any]] = None
) -> bool:
    """Whether this item passed the sustained-playback acceptance."""
    if not isinstance(item, dict):
        return False
    name = str(item.get("name") or item.get("title") or "")
    if not name:
        return False
    proofs = (registry if registry is not None else load()).get("proofs") or {}
    return _key(kind, name) in proofs


def proof_for(
    item: Dict[str, Any], kind: str, registry: Optional[Dict[str, Any]] = None
) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict):
        return None
    name = str(item.get("name") or item.get("title") or "")
    proofs = (registry if registry is not None else load()).get("proofs") or {}
    return proofs.get(_key(kind, name))


def record(
    kind: str,
    name: str,
    evidence: Dict[str, Any],
    *,
    path: Optional[str] = None,
) -> Tuple[bool, str]:
    """Add one proof. Returns (written, reason).

    Refuses anything that is not a genuine acceptance result, so the registry
    cannot be seeded with a claim: the evidence has to carry at least two full
    PASSes and the window each was measured over.
    """
    if not str(name or "").strip():
        return False, "no name"
    try:
        passes = int(evidence.get("pass_count"))
    except (TypeError, ValueError):
        return False, "evidence records no pass count"
    if passes < 2:
        return False, f"only {passes} full PASS; two independent sessions required"
    if not evidence.get("window_seconds"):
        return False, "evidence records no measurement window"

    target = path or DEFAULT_PROOF_PATH
    registry = load(target)
    registry.setdefault("proofs", {})[_key(kind, name)] = {
        "kind": kind,
        "name": name,
        "pass_count": passes,
        "window_seconds": evidence.get("window_seconds"),
        "session_separation_seconds": evidence.get("session_separation_seconds"),
        "browser_profile": evidence.get("browser_profile"),
        "media_progress_seconds": evidence.get("media_progress_seconds"),
        "cumulative_stall_seconds": evidence.get("cumulative_stall_seconds"),
        "observed_at": evidence.get("observed_at"),
        # Recorded so a later name match on a changed route is visible in the
        # file rather than silent. It is NOT part of the key.
        "fingerprint_at_proof_time": evidence.get("fingerprint_at_proof_time"),
        "evidence_report": evidence.get("evidence_report"),
    }
    registry["note"] = (
        "Keyed by (kind, name), not by fingerprint: a fingerprint includes the "
        "stream URL and stops matching when a source rotates, and anything "
        "written onto a card is erased by the next scan. This registry only "
        "grants proof, never withholds it."
    )
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as handle:
            json.dump(registry, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
    except OSError as error:
        return False, f"could not write registry: {error}"
    return True, "recorded"
