"""Persistent visibility gate for items that failed the real Click TV player.

Network/media probes and a decoded browser frame are different facts.  This
module keeps the former in scan reports while preventing a twice-confirmed
browser failure from being republished by a later HTTP-only scan.
"""

from __future__ import annotations

import json
import hashlib
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, Set, Tuple


DEFAULT_FAILURE_REPORT = Path("reports/confirmed-player-failures.json")
DEFAULT_PROOF_LEDGER = Path("state/player-playback-proof.json")


def _text_key(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return " ".join(re.sub(r"[^a-z0-9\u0980-\u09ff]+", " ", text).split())


def _year(value: Any) -> str:
    match = re.search(r"(?:19|20)\d{2}", str(value or ""))
    return match.group(0) if match else ""


def _item_key(kind: str, item: Dict[str, Any]) -> Tuple[str, str, str]:
    name = item.get("name") or item.get("title")
    year = _year(item.get("year")) if kind == "movie" else ""
    return kind, _text_key(name), year


def playback_fingerprint(item: Dict[str, Any]) -> str:
    routes = []
    candidates = [item]
    for field in ("backups", "standby"):
        candidates.extend(entry for entry in item.get(field) or [] if isinstance(entry, dict))
    for route in candidates:
        url = str(route.get("url") or "").split("|", 1)[0].strip()
        if not url:
            continue
        routes.append({
            "url": url,
            "header_profile": str(route.get("header_profile") or ""),
            "proxy_mode": str(route.get("proxy_mode") or "auto"),
            "stream_type": str(route.get("stream_type") or ""),
            "requires_headers": bool(route.get("requires_headers", False)),
        })
    encoded = json.dumps(sorted(routes, key=lambda route: json.dumps(route, sort_keys=True)), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest() if routes else ""


@lru_cache(maxsize=8)
def _load_failure_keys_cached(path_text: str, modified_ns: int) -> Set[Tuple[str, str, str]]:
    del modified_ns
    path = Path(path_text)
    if not path.is_file():
        return set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    keys: Set[Tuple[str, str, str]] = set()
    for entry in payload.get("records") or []:
        if not isinstance(entry, dict):
            continue
        kind = str(entry.get("kind") or "").strip().casefold()
        record = entry.get("record")
        if kind not in {"channel", "movie"} or not isinstance(record, dict):
            continue
        key = _item_key(kind, record)
        if key[1]:
            keys.add(key)
    return keys


def load_failure_keys(report_path: str | Path = DEFAULT_FAILURE_REPORT) -> Set[Tuple[str, str, str]]:
    path = Path(report_path).resolve()
    modified_ns = path.stat().st_mtime_ns if path.is_file() else 0
    return _load_failure_keys_cached(str(path), modified_ns)


@lru_cache(maxsize=8)
def _load_proof_keys_cached(path_text: str, modified_ns: int) -> Set[Tuple[str, str, str, str]]:
    del modified_ns
    path = Path(path_text)
    if not path.is_file():
        return set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    keys: Set[Tuple[str, str, str, str]] = set()
    for entry in payload.get("proofs") or []:
        if not isinstance(entry, dict):
            continue
        kind = str(entry.get("kind") or "").strip().casefold()
        name = _text_key(entry.get("name"))
        year = _year(entry.get("year")) if kind == "movie" else ""
        fingerprint = str(entry.get("fingerprint") or "").strip()
        if kind in {"channel", "movie"} and name and fingerprint:
            keys.add((kind, name, year, fingerprint))
    return keys


def load_proof_keys(ledger_path: str | Path = DEFAULT_PROOF_LEDGER) -> Set[Tuple[str, str, str, str]]:
    path = Path(ledger_path).resolve()
    modified_ns = path.stat().st_mtime_ns if path.is_file() else 0
    return _load_proof_keys_cached(str(path), modified_ns)


def is_player_proven(
    item: Dict[str, Any],
    kind: str,
    proof_keys: Iterable[Tuple[str, str, str, str]] | None = None,
) -> bool:
    normalized_kind = str(kind or "").strip().casefold()
    base = _item_key(normalized_kind, item)
    fingerprint = playback_fingerprint(item)
    keys = set(proof_keys) if proof_keys is not None else load_proof_keys()
    return bool(fingerprint and (*base, fingerprint) in keys)


def mark_unproven_player_items(
    items: Iterable[Dict[str, Any]],
    kind: str,
    ledger_path: str | Path = DEFAULT_PROOF_LEDGER,
) -> int:
    proof_keys = load_proof_keys(ledger_path)
    if not proof_keys:
        return 0
    hidden = 0
    for item in items:
        if not isinstance(item, dict) or is_player_proven(item, kind, proof_keys):
            continue
        item["publish_allowed"] = False
        item["player_verified"] = False
        item["player_visibility"] = "hidden_pending_player_proof"
        item["network_verification_status"] = str(item.get("verification_status") or "")
        item["verification_status"] = "pending_player_proof"
        item["verification_note"] = (
            "Retained outside the public catalogue until this exact Click TV "
            "card and route set produces a decoded browser frame."
        )
        hidden += 1
    return hidden


def is_confirmed_player_failure(
    item: Dict[str, Any],
    kind: str,
    failure_keys: Iterable[Tuple[str, str, str]] | None = None,
) -> bool:
    normalized_kind = str(kind or "").strip().casefold()
    keys = set(failure_keys) if failure_keys is not None else load_failure_keys()
    key = _item_key(normalized_kind, item)
    if key in keys:
        return True
    # A missing movie year in either source is matched only by its exact title.
    if normalized_kind == "movie" and not key[2]:
        return any(candidate[:2] == key[:2] for candidate in keys)
    return False


def mark_confirmed_player_failures(
    items: Iterable[Dict[str, Any]],
    kind: str,
    report_path: str | Path = DEFAULT_FAILURE_REPORT,
) -> int:
    keys = load_failure_keys(report_path)
    hidden = 0
    for item in items:
        if not isinstance(item, dict) or not is_confirmed_player_failure(item, kind, keys):
            continue
        prior_status = str(item.get("verification_status") or "").strip()
        if prior_status and prior_status != "failed_player_twice":
            item["network_verification_status"] = prior_status
        item["publish_allowed"] = False
        item["player_verified"] = False
        item["player_visibility"] = "hidden_failed_player"
        item["verification_status"] = "failed_player_twice"
        item["verification_mode"] = "real_clicktv_browser_twice"
        item["verification_note"] = (
            "Retained in scanner/report state but hidden because two independent "
            "Click TV browser attempts produced no decoded video frame."
        )
        hidden += 1
    return hidden
