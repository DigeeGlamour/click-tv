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
        "clear_keys", "headers", "authorization", "cookie", "token",
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
    if isinstance(value, str) and ("?" in value or "&" in value):
        return _redacted_url(value)
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
            if key_lower in {"backups", "links", "sources"} and isinstance(value, list):
                maximum = 5 if key_lower == "backups" else 6
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
            "context": context,
            "item_id": str(item.get("id") or item.get("channel_id") or ""),
            "name": str(item.get("name") or item.get("title") or "").casefold().strip(),
            "source_id": str(item.get("source_id") or ""),
            "url": _redacted_url(source_url),
            "header_names": sorted(name.casefold() for name in headers),
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


def merge_public_catalog(
    data_root: str | Path,
    collector: PlaybackProfileCollector,
) -> Dict[str, Any]:
    """Merge one partial scan into the public Pages playback catalogue."""
    root = Path(data_root)
    root.mkdir(parents=True, exist_ok=True)
    target = root / "playback-sources.json"
    existing: Dict[str, Any] = {}
    if target.exists():
        try:
            loaded = json.loads(target.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                existing = loaded
        except (OSError, UnicodeError, json.JSONDecodeError):
            existing = {}

    records = existing.get("records")
    if not isinstance(records, dict):
        records = {}
    records.update(collector.records)
    payload = {
        "schema_version": 1,
        "generated_at": collector.timestamp,
        "scan_mode": collector.scan_mode,
        "storage": "public_git_pages_json",
        "count": len(records),
        "records": dict(sorted(records.items())),
    }

    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, target)
    return payload
