"""Build the simple Git/Pages playback catalogue used by Click TV Workers.

Every playable source receives a stable ``playback_id``. The complete source
configuration (URL, headers, cookies and DRM) is written to the public
``data/playback-sources.json`` file, as explicitly selected for this project.
Sensitive values are still removed from the smaller channel/movie/event files
so the player has one consistent ID-based route for credentialed playback.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from scanner.security import redact_sensitive_text


PRIVATE_FIELDS = {
    "headers",
    "request_headers",
    "raw_headers",
    "source_headers",
    "cookie",
    "authorization",
    "user_agent",
    "verify_token",
    "api_token",
    "password",
    "secret",
}

SENSITIVE_HEADER_NAMES = {
    "authorization",
    "cookie",
    "proxy-authorization",
    "x-api-key",
    "api-key",
    "edge-cache-cookie",
    "x-auth-token",
}

SENSITIVE_QUERY_RE = re.compile(
    r"(?:^|[_-])(?:access[_-]?token|api[_-]?key|auth|authorization|"
    r"cookie|credential|expires?|hdnea|jwt|key|policy|session|sig|"
    r"signature|token)(?:$|[_-])",
    re.IGNORECASE,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _headers_from_item(item: Mapping[str, Any]) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for field in ("headers", "request_headers", "raw_headers", "source_headers"):
        value = item.get(field)
        if not isinstance(value, Mapping):
            continue
        for name, header_value in value.items():
            clean_name = str(name or "").strip()
            clean_value = str(header_value or "").strip()
            if clean_name and clean_value:
                result[clean_name] = clean_value

    aliases = {
        "cookie": "Cookie",
        "authorization": "Authorization",
        "user_agent": "User-Agent",
    }
    for field, header_name in aliases.items():
        value = str(item.get(field) or "").strip()
        if value:
            result[header_name] = value
    return result


def _url_from_item(item: Mapping[str, Any]) -> Tuple[str, str]:
    for field in ("url", "stream_url", "link"):
        value = str(item.get(field) or "").strip()
        if value:
            return field, value
    return "", ""


def _sensitive_query_names(url: str) -> List[str]:
    try:
        query = urlsplit(url).query
    except ValueError:
        return []
    return sorted({name for name, _ in parse_qsl(query, keep_blank_values=True) if SENSITIVE_QUERY_RE.search(name)})


def _redacted_url(url: str) -> str:
    try:
        parts = urlsplit(url)
        query = [
            (name, "<protected>" if SENSITIVE_QUERY_RE.search(name) else value)
            for name, value in parse_qsl(parts.query, keep_blank_values=True)
        ]
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, urlencode(query), ""))
    except ValueError:
        return re.sub(r"([?&][^=]+)=([^&]*)", r"\1=<protected>", url)


def _drm_is_private(drm: Any) -> bool:
    if not isinstance(drm, Mapping):
        return False
    secret_keys = {
        "key", "keys", "key_id", "kid", "license_key", "clearkey",
        "clear_keys", "headers", "license_headers", "license_url",
        "certificate_url", "certificate_headers", "authorization", "cookie", "token",
    }
    return any(str(key).strip().lower() in secret_keys for key in drm)


def redact_public_report(value: Any) -> Any:
    """Recursively remove credentials and redact secret-looking URL values."""
    if isinstance(value, list):
        return [redact_public_report(item) for item in value]
    if isinstance(value, Mapping):
        clean: Dict[str, Any] = {}
        for key, child in value.items():
            key_lower = str(key).strip().lower()
            if key_lower in PRIVATE_FIELDS or key_lower in {
                "license_key", "clear_keys", "clearkey", "key_id", "kid",
            }:
                continue
            clean[str(key)] = redact_public_report(child)
        return clean
    if isinstance(value, str):
        clean_value = redact_sensitive_text(value)
        if "?" in clean_value or "&" in clean_value:
            return _redacted_url(clean_value)
        return clean_value
    return value


class PlaybackProfileCollector:
    """Create playback references and public Git/Pages catalogue records."""

    def __init__(self, scan_mode: str, timestamp: str | None = None) -> None:
        self.scan_mode = str(scan_mode or "all")
        self.timestamp = timestamp or _utc_now()
        self.records: Dict[str, Dict[str, Any]] = {}

    def sanitize_item(self, item: Mapping[str, Any], context: str = "item") -> Dict[str, Any]:
        source_field, source_url = _url_from_item(item)
        headers = _headers_from_item(item)
        sensitive_headers = sorted(
            name for name in headers if name.strip().lower() in SENSITIVE_HEADER_NAMES
        )
        sensitive_query = _sensitive_query_names(source_url) if source_url else []
        drm = item.get("drm")
        protected = bool(source_url and (sensitive_headers or sensitive_query or _drm_is_private(drm)))

        clean: Dict[str, Any] = {}
        for key, value in item.items():
            key_lower = str(key).strip().lower()
            if key_lower in PRIVATE_FIELDS:
                continue
            if protected and key_lower in {"url", "stream_url", "link"}:
                continue
            if protected and key_lower == "drm":
                drm_type = str(drm.get("type") or drm.get("scheme") or "protected").strip().lower() if isinstance(drm, Mapping) else "protected"
                clean[key] = {"type": drm_type, "protected": True}
                continue
            if key_lower in {"backups", "links", "sources", "standby"} and isinstance(value, list):
                maximum = 5 if key_lower == "backups" else len(value) if key_lower == "standby" else 6
                children: List[Any] = []
                for index, child in enumerate(value[:maximum]):
                    child_context = f"{context}:{key_lower}:{index}"
                    if isinstance(child, Mapping):
                        children.append(self.sanitize_item(child, child_context))
                    elif isinstance(child, str) and child.strip():
                        children.append(self.sanitize_item({"url": child.strip()}, child_context))
                clean[key] = children
                continue
            clean[key] = value

        if source_url:
            playback_id = self._stable_id(item, source_url, context, headers)
            self.records[playback_id] = {
                "schema_version": 1,
                "status": "active",
                "url": source_url,
                "headers": headers,
                "drm": drm if isinstance(drm, Mapping) else {},
                "stream_type": str(item.get("stream_type") or item.get("type") or "").strip().lower(),
                "header_profile": str(item.get("header_profile") or item.get("profile") or "").strip(),
                "inherit_manifest_query": bool(item.get("inherit_manifest_query")),
                "updated_at": self.timestamp,
                "scan_mode": self.scan_mode,
            }
            clean["playback_id"] = playback_id
        if protected:
            clean["protected_source"] = True
            clean["requires_credentials"] = True
            clean["proxy_mode"] = "proxy_only"
            clean["credential_hints"] = {
                "headers": sensitive_headers,
                "query_parameters": sensitive_query,
                "drm": _drm_is_private(drm),
            }
        return clean

    def _stable_id(
        self,
        item: Mapping[str, Any],
        source_url: str,
        context: str,
        headers: Mapping[str, str],
    ) -> str:
        identity = {
            # The ID represents the complete playable configuration. Header,
            # Cookie/token and DRM values are intentionally included because
            # this project stores them in its public Git/Pages catalogue and
            # equal URLs with different credentials must never collide.
            "url": source_url,
            "headers": dict(sorted((str(name), str(value)) for name, value in headers.items())),
            "drm": item.get("drm") if isinstance(item.get("drm"), Mapping) else {},
            "header_profile": str(item.get("header_profile") or ""),
            "stream_type": str(item.get("stream_type") or item.get("type") or ""),
            "inherit_manifest_query": bool(item.get("inherit_manifest_query")),
        }
        digest = hashlib.sha256(
            json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return f"ctv_{digest[:32]}"

    def catalog_bundle(self) -> Dict[str, Any]:
        return {
            "schema_version": 1,
            "generated_at": self.timestamp,
            "scan_mode": self.scan_mode,
            "count": len(self.records),
            "records": dict(sorted(self.records.items())),
        }

    def public_report(self) -> Dict[str, Any]:
        return {
            "schema_version": 1,
            "generated_at": self.timestamp,
            "scan_mode": self.scan_mode,
            "catalogued_sources": len(self.records),
            "catalog": "data/playback-sources.json",
            "storage": "public_git_pages_json",
            "contains_playback_credentials": True,
        }


try:
    from scanner.snapshot_publish import ORDER_PLAYBACK_INDEX, ORDER_PLAYBACK_SHARD
except ImportError:  # pragma: no cover - direct module execution
    from snapshot_publish import ORDER_PLAYBACK_INDEX, ORDER_PLAYBACK_SHARD

CATALOG_SHARD_DIRECTORY = "playback"
CATALOG_SHARD_KEY = "playback_id_prefix_2"
CATALOG_SHARD_PATH_TEMPLATE = "data/playback/{shard}.json"


def catalog_shard_for(playback_id: str) -> str:
    """Return the shard a playback_id belongs to.

    A playback_id is ``ctv_`` followed by 32 hex characters, so the first two
    of those characters spread the catalogue evenly over 256 shards. The proxy
    Worker computes the same prefix, which is why this must stay a pure
    function of the id and never depend on scan order or record contents.
    """
    text = str(playback_id or "").strip().lower()
    if text.startswith("ctv_"):
        text = text[4:]
    prefix = text[:2]
    return prefix if len(prefix) == 2 and all(c in "0123456789abcdef" for c in prefix) else "00"


def _atomic_write(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def load_public_catalog_records(data_root: str | Path) -> Dict[str, Any]:
    """Read every playback record, from shards or the pre-shard single file.

    The single-file layout is still understood so a repository mid-migration,
    and any tooling that has not been redeployed yet, keeps working.
    """
    root = Path(data_root)
    records: Dict[str, Any] = {}

    shard_dir = root / CATALOG_SHARD_DIRECTORY
    if shard_dir.is_dir():
        for shard_file in sorted(shard_dir.glob("*.json")):
            try:
                payload = json.loads(shard_file.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            shard_records = payload.get("records") if isinstance(payload, dict) else None
            if isinstance(shard_records, dict):
                records.update(shard_records)

    index_file = root / "playback-sources.json"
    if index_file.is_file():
        try:
            payload = json.loads(index_file.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            payload = {}
        legacy = payload.get("records") if isinstance(payload, dict) else None
        if isinstance(legacy, dict):
            # Pre-shard records are older than anything already read from a
            # shard, so they must not overwrite it.
            for key, value in legacy.items():
                records.setdefault(key, value)

    return records


def merge_public_catalog(
    data_root: str | Path,
    collector: PlaybackProfileCollector,
    snapshot: Any = None,
) -> Dict[str, Any]:
    """Merge one partial scan into the public Pages playback catalogue.

    The catalogue is written as one file per shard plus a small index, because
    the playback proxy Worker has to look a single id up on every manifest and
    segment request. Reading and parsing one ~70 KB shard fits comfortably in a
    Worker's CPU budget; re-parsing the previous single 17 MB file on every
    request did not, and that is what stalled live playback. Splitting it also
    keeps each scan's git diff to the few shards that actually changed.

    Requirement 15: given a SnapshotPublisher, every shard and the index are
    staged into it instead of being written here, so they are published in the
    same single consistent swap as the event files that reference them.
    """
    root = Path(data_root)
    root.mkdir(parents=True, exist_ok=True)

    records = load_public_catalog_records(root)
    records.update(collector.records)

    grouped: Dict[str, Dict[str, Any]] = {}
    for playback_id, profile in sorted(records.items()):
        grouped.setdefault(catalog_shard_for(playback_id), {})[playback_id] = profile

    shard_dir = root / CATALOG_SHARD_DIRECTORY
    shard_dir.mkdir(parents=True, exist_ok=True)
    for shard, shard_records in sorted(grouped.items()):
        shard_payload = {
            "schema_version": 1,
            "shard": shard,
            "generated_at": collector.timestamp,
            "count": len(shard_records),
            "records": shard_records,
        }
        if snapshot is not None:
            snapshot.stage(
                shard_dir / f"{shard}.json",
                shard_payload,
                order=ORDER_PLAYBACK_SHARD,
                kind="playback_shard",
            )
        else:
            _atomic_write(shard_dir / f"{shard}.json", shard_payload)

    # A shard that lost its last record must not linger with stale credentials.
    for stale in shard_dir.glob("*.json"):
        if stale.stem in grouped:
            continue
        if snapshot is not None:
            snapshot.stage_deletion(stale)
            continue
        try:
            stale.unlink()
        except OSError:
            pass

    payload = {
        "schema_version": 2,
        "generated_at": collector.timestamp,
        "scan_mode": collector.scan_mode,
        "storage": "public_git_pages_json",
        "sharded": True,
        "shard_key": CATALOG_SHARD_KEY,
        "shard_path": CATALOG_SHARD_PATH_TEMPLATE,
        "count": len(records),
        "shards": {shard: len(items) for shard, items in sorted(grouped.items())},
    }
    if snapshot is not None:
        snapshot.stage(
            root / "playback-sources.json",
            payload,
            order=ORDER_PLAYBACK_INDEX,
            kind="playback_index",
        )
    else:
        _atomic_write(root / "playback-sources.json", payload)
    return payload
