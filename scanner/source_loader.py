"""
Source Loader Module

Downloads public sources concurrently, detects their formats, routes them to
format-specific parsers, safely follows nested catalog playlists, loads manual
sources, and updates candidate/source-health JSON files atomically.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import ssl
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import parse_qsl, urlparse

from scanner.parsers.direct_stream import parse_direct_stream_content
from scanner.parsers.json_parser import parse_json_content
from scanner.parsers.m3u_parser import parse_m3u_content
from scanner.parsers.url_list_parser import parse_url_list_content


DEFAULT_RETRY_DELAYS = [0, 2, 5]
DEFAULT_RETRY_STATUS_CODES = [429, 500, 502, 503, 504]
DEFAULT_MAX_SOURCE_BYTES = 25 * 1024 * 1024

DIRECT_MEDIA_EXTENSIONS = {
    ".ts", ".mp4", ".mkv", ".webm", ".avi", ".flv", ".mov"
}
NESTED_CATALOG_EXTENSIONS = {".m3u", ".m3u8", ".txt", ".json"}
FORMAT_ALIASES = {
    "auto": "auto",
    "m3u": "m3u",
    "playlist": "m3u",
    "iptv": "m3u",
    "json": "json",
    "txt": "url_list",
    "text": "url_list",
    "url-list": "url_list",
    "url_list": "url_list",
    "direct": "direct_stream",
    "direct-stream": "direct_stream",
    "direct_stream": "direct_stream",
    "hls": "direct_stream",
    "dash": "direct_stream",
    "mpd": "direct_stream",
}


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------

def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_int(
    value: Any,
    default: int,
    minimum: int = 0,
    maximum: Optional[int] = None,
) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = default

    result = max(minimum, result)
    if maximum is not None:
        result = min(result, maximum)
    return result


def _load_json_file(file_path: str | Path) -> Dict[str, Any]:
    path = Path(file_path)
    if not path.exists():
        return {}

    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return {}


def _atomic_write_json(file_path: str | Path, data: Dict[str, Any]) -> None:
    """Write JSON to a temporary file, then replace the target atomically."""
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")

    try:
        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass


def _normalize_format(value: Any) -> str:
    raw = str(value or "auto").strip().lower()
    if not raw:
        return "auto"
    return FORMAT_ALIASES.get(raw, raw)


def _split_request_url_and_headers(raw_url: str) -> Tuple[str, Dict[str, str]]:
    """Separate IPTV pipe headers before making the HTTP request."""
    if "|" not in raw_url:
        return raw_url.strip(), {}

    request_url, header_query = raw_url.split("|", 1)
    headers: Dict[str, str] = {}
    aliases = {
        "user-agent": "User-Agent",
        "http-user-agent": "User-Agent",
        "referer": "Referer",
        "referrer": "Referer",
        "http-referer": "Referer",
        "http-referrer": "Referer",
        "origin": "Origin",
        "cookie": "Cookie",
        "authorization": "Authorization",
    }

    for key, value in parse_qsl(header_query, keep_blank_values=True):
        normalized = key.strip().lower().replace("_", "-")
        headers[aliases.get(normalized, key.strip())] = value

    return request_url.strip(), headers


def _usable_non_comment_lines(content: str) -> List[str]:
    return [
        line.strip()
        for line in content.splitlines()
        if line.strip() and not line.lstrip().startswith(("#", ";", "//"))
    ]


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------

def _detect_format(
    content: str,
    forced_format: str = "auto",
    source_url: str = "",
) -> str:
    """Detect JSON, IPTV M3U, direct HLS/DASH, direct URL, or URL list."""
    forced = _normalize_format(forced_format)
    if forced != "auto":
        return forced

    stripped = (content or "").lstrip("\ufeff").strip()
    upper = stripped.upper()
    lower = stripped.lower()

    if stripped.startswith(("{", "[")):
        return "json"

    if "#EXTM3U" in upper and "#EXT-X-" in upper:
        return "direct_stream"

    if "<mpd" in lower or "urn:mpeg:dash:schema:mpd" in lower:
        return "direct_stream"

    if "#EXTINF:" in upper or upper.startswith("#EXTM3U"):
        return "m3u"

    usable_lines = _usable_non_comment_lines(stripped)
    if len(usable_lines) == 1 and usable_lines[0].startswith(
        ("http://", "https://", "rtmp://", "rtmps://", "rtsp://", "rtsps://", "udp://")
    ):
        return "direct_stream"

    source_path = urlparse(str(source_url).split("|", 1)[0]).path.casefold()
    if source_path.endswith((".mpd", ".ts", ".mp4", ".mkv", ".webm", ".avi", ".flv", ".mov")):
        return "direct_stream"

    return "url_list"


def parse_source_content(
    content: str,
    source_info: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], str]:
    """Route content to the appropriate parser and return its detected format."""
    detected = _detect_format(
        content,
        source_info.get("format", "auto"),
        str(source_info.get("url") or source_info.get("location") or ""),
    )

    if detected == "json":
        return parse_json_content(content, source_info), detected
    if detected == "m3u":
        return parse_m3u_content(content, source_info), detected
    if detected == "direct_stream":
        return parse_direct_stream_content(content, source_info), detected

    return parse_url_list_content(content, source_info), "url_list"


# ---------------------------------------------------------------------------
# Conditional source cache
# ---------------------------------------------------------------------------

SOURCE_CACHE_DIR = Path("working/source-cache")


def _source_cache_paths(source_id: str, request_url: str) -> Tuple[Path, Path]:
    identity = f"{source_id}\n{request_url}".encode("utf-8", errors="ignore")
    key = hashlib.sha256(identity).hexdigest()
    return (
        SOURCE_CACHE_DIR / f"{key}.meta.json",
        SOURCE_CACHE_DIR / f"{key}.items.json",
    )


def _load_source_cache(source_id: str, request_url: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    meta_path, items_path = _source_cache_paths(source_id, request_url)
    meta = _load_json_file(meta_path)
    payload = _load_json_file(items_path)
    items = payload.get("items") if isinstance(payload, dict) else []
    if not isinstance(items, list):
        items = []
    return meta, [item for item in items if isinstance(item, dict)]


def _save_source_cache(
    source_id: str,
    request_url: str,
    response_meta: Dict[str, Any],
    detected_format: str,
    items: List[Dict[str, Any]],
) -> None:
    meta_path, items_path = _source_cache_paths(source_id, request_url)
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(
        meta_path,
        {
            "updated_at": _utc_now(),
            "source_id": source_id,
            "url": request_url,
            "etag": str(response_meta.get("etag") or ""),
            "last_modified": str(response_meta.get("last_modified") or ""),
            "detected_format": detected_format,
            "item_count": len(items),
        },
    )
    _atomic_write_json(
        items_path,
        {
            "updated_at": _utc_now(),
            "source_id": source_id,
            "items": items,
        },
    )


# ---------------------------------------------------------------------------
# Network download
# ---------------------------------------------------------------------------

def _fetch_url_with_retry(
    url: str,
    headers: Dict[str, str],
    timeout: int = 15,
    retries: int = 3,
    delays: Optional[List[int]] = None,
    retry_status_codes: Optional[List[int]] = None,
    verify_ssl: bool = True,
    max_bytes: int = DEFAULT_MAX_SOURCE_BYTES,
) -> Tuple[Optional[str], Optional[str], int, int, int, Dict[str, str]]:
    """Fetch text content with retries."""
    delays = list(delays or DEFAULT_RETRY_DELAYS)
    retry_codes = {
        int(code) for code in (retry_status_codes or DEFAULT_RETRY_STATUS_CODES)
    }
    retries = _safe_int(retries, 3, 1, 10)
    timeout = _safe_int(timeout, 15, 1, 120)
    max_bytes = _safe_int(max_bytes, DEFAULT_MAX_SOURCE_BYTES, 1024)

    ssl_context = None if verify_ssl else ssl._create_unverified_context()

    request_headers: Dict[str, str] = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/131 Safari/537.36"
        ),
        "Accept": "*/*",
    }
    if isinstance(headers, dict):
        for key, value in headers.items():
            if value is None or isinstance(value, (dict, list)):
                continue
            request_headers[str(key)] = str(value)

    started = time.monotonic()
    last_error = "Unknown fetch error"
    status_code = 0
    attempts_used = 0

    for attempt_index in range(retries):
        attempts_used = attempt_index + 1
        delay = delays[attempt_index] if attempt_index < len(delays) else 0
        if delay > 0:
            time.sleep(delay)

        try:
            request = urllib.request.Request(url, headers=request_headers)
            with urllib.request.urlopen(
                request,
                timeout=timeout,
                context=ssl_context,
            ) as response:
                status_code = int(getattr(response, "status", 200) or 200)
                raw_bytes = response.read(max_bytes + 1)

                if len(raw_bytes) > max_bytes:
                    last_error = f"Source exceeded maximum size of {max_bytes} bytes"
                    break

                charset = response.headers.get_content_charset() or "utf-8"
                content = raw_bytes.decode(charset, errors="ignore")
                elapsed_ms = int((time.monotonic() - started) * 1000)
                response_meta = {
                    "etag": str(response.headers.get("ETag") or ""),
                    "last_modified": str(response.headers.get("Last-Modified") or ""),
                }
                return content, None, status_code, elapsed_ms, attempts_used, response_meta

        except urllib.error.HTTPError as error:
            status_code = int(getattr(error, "code", 0) or 0)
            if status_code == 304:
                elapsed_ms = int((time.monotonic() - started) * 1000)
                return None, None, 304, elapsed_ms, attempts_used, {
                    "etag": str(error.headers.get("ETag") or "") if error.headers else "",
                    "last_modified": str(error.headers.get("Last-Modified") or "") if error.headers else "",
                }
            last_error = f"HTTP Error {status_code}: {getattr(error, 'reason', '')}"
            if status_code not in retry_codes:
                break
        except urllib.error.URLError as error:
            last_error = f"URL Error: {getattr(error, 'reason', error)}"
        except (TimeoutError, OSError, ValueError) as error:
            last_error = f"Fetch Error: {error}"

    elapsed_ms = int((time.monotonic() - started) * 1000)
    return None, last_error, status_code, elapsed_ms, attempts_used, {}


def _is_direct_media_url(url: str) -> bool:
    clean_url = str(url or "").split("|", 1)[0].strip()
    parsed = urlparse(clean_url)

    if parsed.scheme.casefold() in {"rtmp", "rtmps", "rtsp", "rtsps", "udp"}:
        return True

    return Path(parsed.path).suffix.casefold() in DIRECT_MEDIA_EXTENSIONS


def _looks_like_nested_catalog_url(url: str) -> bool:
    clean_url = str(url or "").split("|", 1)[0].strip()
    parsed = urlparse(clean_url)
    if parsed.scheme.casefold() not in {"http", "https"}:
        return False
    return Path(parsed.path).suffix.casefold() in NESTED_CATALOG_EXTENSIONS


def _merge_nested_headers(
    source_info: Dict[str, Any],
    item: Dict[str, Any],
) -> Dict[str, str]:
    merged: Dict[str, str] = {}

    for container in (source_info.get("headers"), item.get("headers")):
        if not isinstance(container, dict):
            continue
        for key, value in container.items():
            if value is None or isinstance(value, (dict, list)):
                continue
            merged[str(key)] = str(value)

    return merged


# ---------------------------------------------------------------------------
# Remote/local source processing
# ---------------------------------------------------------------------------

def process_single_source(
    source_info: Dict[str, Any],
    settings: Dict[str, Any],
    visited_urls: Optional[Set[str]] = None,
    current_depth: int = 0,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Download, parse, and optionally expand one configured source."""
    source_info = dict(source_info or {})
    visited = visited_urls if visited_urls is not None else set()

    source_id = str(source_info.get("id") or "unknown-source")
    source_name = str(source_info.get("name") or source_id)
    source_url = str(
        source_info.get("url") or source_info.get("location") or ""
    ).strip()
    request_url, pipe_request_headers = _split_request_url_and_headers(source_url)
    pipeline = str(source_info.get("pipeline") or "tv")

    network = settings.get("network", {})
    if not isinstance(network, dict):
        network = {}

    timeout = _safe_int(settings.get("source_timeout_seconds", 15), 15, 1, 120)
    retries = _safe_int(network.get("retry_attempts", 3), 3, 1, 10)
    delays = network.get("retry_delays_seconds", DEFAULT_RETRY_DELAYS)
    retry_codes = network.get("retry_status_codes", DEFAULT_RETRY_STATUS_CODES)
    verify_ssl = bool(network.get("verify_ssl", True))
    max_source_bytes = _safe_int(
        network.get(
            "maximum_source_bytes",
            settings.get("maximum_source_bytes", DEFAULT_MAX_SOURCE_BYTES),
        ),
        DEFAULT_MAX_SOURCE_BYTES,
        1024,
    )

    if not isinstance(delays, list):
        delays = DEFAULT_RETRY_DELAYS
    if not isinstance(retry_codes, list):
        retry_codes = DEFAULT_RETRY_STATUS_CODES

    source_headers = source_info.get("headers")
    if not isinstance(source_headers, dict):
        source_headers = {}
    else:
        source_headers = dict(source_headers)
    source_headers.update(pipe_request_headers)

    health: Dict[str, Any] = {
        "source_id": source_id,
        "source_name": source_name,
        "url": source_url,
        "pipeline": pipeline,
        "status": "failed",
        "last_scan": _utc_now(),
        "http_status": 0,
        "attempts": 0,
        "response_time_ms": 0,
        "detected_format": "",
        "raw_items": 0,
        "error": None,
    }

    if not source_info.get("enabled", True):
        health["status"] = "disabled"
        return [], health

    if not source_url:
        health["error"] = "Source URL/path is empty"
        return [], health

    remote_prefixes = (
        "http://", "https://", "rtmp://", "rtmps://",
        "rtsp://", "rtsps://", "udp://",
    )

    # Local file
    if not request_url.startswith(remote_prefixes):
        path = Path(request_url)
        if not path.exists():
            health["error"] = "Local file not found"
            return [], health

        try:
            if path.stat().st_size > max_source_bytes:
                health["error"] = "Local source exceeded maximum size"
                return [], health

            content = path.read_text(encoding="utf-8", errors="ignore")
            items, detected = parse_source_content(content, source_info)
            health.update(
                status="success" if items else "success_empty",
                detected_format=detected,
                raw_items=len(items),
            )
            return items, health
        except (OSError, UnicodeError, ValueError) as error:
            health["error"] = f"Local file read error: {error}"
            return [], health

    if request_url.startswith(("rtmp://", "rtmps://", "rtsp://", "rtsps://", "udp://")):
        items, detected = parse_direct_stream_content("", source_info), "direct_stream"
        health.update(
            status="success" if items else "success_empty",
            detected_format=detected,
            raw_items=len(items),
        )
        return items, health

    if request_url in visited:
        health["error"] = "Recursive playlist loop detected"
        return [], health
    visited.add(request_url)

    if _is_direct_media_url(request_url):
        items, detected = parse_direct_stream_content("", source_info), "direct_stream"
        health.update(
            status="success" if items else "success_empty",
            detected_format=detected,
            raw_items=len(items),
            fetch_mode="direct_url_without_body",
        )
        return items, health

    source_cache_cfg = settings.get("source_cache", {})
    if not isinstance(source_cache_cfg, dict):
        source_cache_cfg = {}
    source_cache_enabled = bool(source_cache_cfg.get("enabled", True))
    cached_meta: Dict[str, Any] = {}
    cached_items: List[Dict[str, Any]] = []

    if source_cache_enabled:
        cached_meta, cached_items = _load_source_cache(source_id, request_url)
        etag = str(cached_meta.get("etag") or "")
        last_modified = str(cached_meta.get("last_modified") or "")
        if etag:
            source_headers.setdefault("If-None-Match", etag)
        if last_modified:
            source_headers.setdefault("If-Modified-Since", last_modified)

    content, error, status_code, elapsed_ms, attempts, response_meta = _fetch_url_with_retry(
        request_url,
        headers=source_headers,
        timeout=timeout,
        retries=retries,
        delays=delays,
        retry_status_codes=retry_codes,
        verify_ssl=verify_ssl,
        max_bytes=max_source_bytes,
    )

    health.update(
        http_status=status_code,
        attempts=attempts,
        response_time_ms=elapsed_ms,
    )

    if status_code == 304 and cached_items:
        detected = str(cached_meta.get("detected_format") or "cached")
        health.update(
            status="success" if cached_items else "success_empty",
            detected_format=detected,
            raw_items=len(cached_items),
            fetch_mode="conditional_cache_304",
            cache_hit=True,
        )
        return cached_items, health

    if error or content is None:
        # A temporary source outage may use a recent parsed cache instead of
        # wiping candidates. The health record still reports the fetch warning.
        allow_stale = bool(source_cache_cfg.get("allow_stale_on_error", True))
        if allow_stale and cached_items:
            health.update(
                status="stale_cache",
                detected_format=str(cached_meta.get("detected_format") or "cached"),
                raw_items=len(cached_items),
                error=error or f"HTTP {status_code}",
                fetch_mode="stale_source_cache",
                cache_hit=True,
            )
            return cached_items, health
        health["error"] = error or f"HTTP {status_code}"
        return [], health

    items, detected = parse_source_content(content, source_info)
    health["detected_format"] = detected

    follow_nested = bool(source_info.get("follow_nested_playlists", False))
    max_depth = _safe_int(source_info.get("maximum_nested_depth", 0), 0, 0, 10)

    if (
        follow_nested
        and current_depth < max_depth
        and detected in {"m3u", "json", "url_list"}
    ):
        expanded: List[Dict[str, Any]] = []

        for item in items:
            item_url = str(item.get("url") or "").strip()
            if not _looks_like_nested_catalog_url(item_url):
                expanded.append(item)
                continue

            nested_info = dict(source_info)
            nested_info.update(
                url=item_url,
                format="auto",
                headers=_merge_nested_headers(source_info, item),
            )

            nested_items, nested_health = process_single_source(
                nested_info,
                settings,
                visited,
                current_depth + 1,
            )

            if nested_items and nested_health.get("detected_format") in {
                "m3u", "json", "url_list"
            }:
                expanded.extend(nested_items)
            else:
                expanded.append(item)

        items = expanded

    if source_cache_enabled:
        try:
            _save_source_cache(
                source_id,
                request_url,
                response_meta,
                detected,
                items,
            )
            health["cache_saved"] = True
        except Exception as cache_error:
            health["cache_error"] = str(cache_error)

    health.update(
        status="success" if items else "success_empty",
        raw_items=len(items),
    )
    return items, health


# ---------------------------------------------------------------------------
# Manual source loading
# ---------------------------------------------------------------------------

def _manual_health(
    source_id: str,
    source_name: str,
    source_url: str,
    status: str,
    item_count: int = 0,
    error: Optional[str] = None,
    detected_format: str = "",
) -> Dict[str, Any]:
    return {
        "source_id": source_id,
        "source_name": source_name,
        "url": source_url,
        "pipeline": "manual",
        "status": status,
        "last_scan": _utc_now(),
        "http_status": 0,
        "attempts": 0,
        "response_time_ms": 0,
        "detected_format": detected_format,
        "raw_items": item_count,
        "error": error,
    }


def load_manual_sources(
    sources_config: Dict[str, Any],
    settings: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """Load manual/manual.m3u, manual/manual.json, and manual/uploads/."""
    manual_cfg = sources_config.get("manual", {})
    if not isinstance(manual_cfg, dict):
        return [], {}

    max_bytes = _safe_int(
        settings.get("maximum_source_bytes", DEFAULT_MAX_SOURCE_BYTES),
        DEFAULT_MAX_SOURCE_BYTES,
        1024,
    )

    base_info: Dict[str, Any] = {
        "priority": manual_cfg.get("priority", 1000),
        "pipeline": "manual",
        "enabled": True,
        "preserve_source_headers": manual_cfg.get("preserve_source_headers", True),
        "preserve_drm": manual_cfg.get("preserve_drm", True),
        "manual_can_override_category": manual_cfg.get(
            "manual_can_override_category", True
        ),
        "manual_can_override_resolution": manual_cfg.get(
            "manual_can_override_resolution", True
        ),
    }

    configured_files: List[Tuple[str, str]] = []

    playlist_files = manual_cfg.get("playlist_files", ["manual/manual.m3u"])
    if isinstance(playlist_files, list):
        for index, file_path in enumerate(playlist_files, start=1):
            configured_files.append((f"manual-playlist-{index}", str(file_path)))

    items_file = manual_cfg.get("items_file", "manual/manual.json")
    if items_file:
        configured_files.append(("manual-items-json", str(items_file)))

    upload_dir = Path(str(manual_cfg.get("upload_directory", "manual/uploads")))
    if upload_dir.exists() and upload_dir.is_dir():
        for upload_file in sorted(upload_dir.iterdir()):
            if (
                upload_file.is_file()
                and not upload_file.name.startswith(".")
                and upload_file.suffix.casefold() in {".m3u", ".m3u8", ".json", ".txt"}
            ):
                configured_files.append(
                    (f"manual-upload-{upload_file.stem}", str(upload_file))
                )

    all_items: List[Dict[str, Any]] = []
    health_map: Dict[str, Dict[str, Any]] = {}

    for source_id, file_path in configured_files:
        path = Path(file_path)
        source_name = path.name or source_id

        if not path.exists():
            health_map[source_id] = _manual_health(
                source_id, source_name, file_path, "failed", error="Manual file not found"
            )
            continue

        try:
            if path.stat().st_size > max_bytes:
                health_map[source_id] = _manual_health(
                    source_id,
                    source_name,
                    file_path,
                    "failed",
                    error="Manual file exceeded maximum size",
                )
                continue

            content = path.read_text(encoding="utf-8", errors="ignore")
            info = dict(base_info)
            info.update(id=source_id, name=source_name, url=file_path, format="auto")

            items, detected = parse_source_content(content, info)
            all_items.extend(items)
            health_map[source_id] = _manual_health(
                source_id,
                source_name,
                file_path,
                "success" if items else "success_empty",
                item_count=len(items),
                detected_format=detected,
            )
        except (OSError, UnicodeError, ValueError) as error:
            health_map[source_id] = _manual_health(
                source_id,
                source_name,
                file_path,
                "failed",
                error=f"Manual file read error: {error}",
            )

    return all_items, health_map


# ---------------------------------------------------------------------------
# Source health history
# ---------------------------------------------------------------------------

def _merge_health_history(
    previous_state: Dict[str, Any],
    current_records: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    previous_sources = previous_state.get("sources", {})
    if not isinstance(previous_sources, dict):
        previous_sources = {}

    merged: Dict[str, Dict[str, Any]] = {
        str(key): dict(value)
        for key, value in previous_sources.items()
        if isinstance(value, dict)
    }

    for source_id, current in current_records.items():
        previous = merged.get(source_id, {})
        previous_total = _safe_int(previous.get("total_scans", 0), 0, 0)
        total_scans = previous_total + 1
        previous_average = _safe_int(
            previous.get("average_response_time_ms", 0), 0, 0
        )
        current_response = _safe_int(current.get("response_time_ms", 0), 0, 0)
        average = int(
            ((previous_average * previous_total) + current_response) / total_scans
        )

        status = str(current.get("status") or "failed")
        record = dict(previous)
        record.update(current)
        record["total_scans"] = total_scans
        record["average_response_time_ms"] = average

        if status in {"success", "success_empty"}:
            record["last_success"] = current.get("last_scan")
            record["consecutive_failures"] = 0
        elif status != "disabled":
            record["last_failure"] = current.get("last_scan")
            record["consecutive_failures"] = (
                _safe_int(previous.get("consecutive_failures", 0), 0, 0) + 1
            )

        merged[source_id] = record

    return merged


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def collect_candidates(mode: str = "all") -> Dict[str, Any]:
    """Fetch active pipelines and write working/candidates.json."""
    sources_config = _load_json_file("config/sources.json")
    settings = _load_json_file("config/settings.json")
    mode_clean = str(mode or "all").strip().lower()

    active_pipelines: List[str] = []
    if mode_clean in {"all", "full-audit", "tv", "channels", "channels-discovery"}:
        active_pipelines.append("tv")
    if mode_clean in {"all", "full-audit", "movies", "movies-discovery"}:
        active_pipelines.append("movies")
    if mode_clean in {"all", "full-audit", "events", "today", "today_match"}:
        active_pipelines.append("today_match")
    # Exact rotating event links can remain in provider Upcoming catalogues
    # after kickoff. A Today scan therefore collects both event source groups;
    # schedule resolution still publishes only the currently active fixture.
    if mode_clean in {"all", "full-audit", "events", "today", "today_match", "upcoming"}:
        active_pipelines.append("upcoming")

    # Movie discovery also reads mixed TV sources, but the content router keeps
    # only VOD items for the movie planner. This repairs movie files that public
    # playlist maintainers incorrectly placed inside TV lists.
    routing_cfg = settings.get("content_routing", {})
    if not isinstance(routing_cfg, dict):
        routing_cfg = {}
    discover_movies_in_tv = bool(
        routing_cfg.get("discover_movies_in_tv_sources", True)
    )
    if mode_clean in {"movies", "movies-discovery"} and discover_movies_in_tv:
        if "tv" not in active_pipelines:
            active_pipelines.append("tv")

    if not active_pipelines:
        raise ValueError(f"Unsupported scan mode: {mode_clean}")

    sources_to_process: List[Dict[str, Any]] = []
    for pipeline in active_pipelines:
        pipeline_sources = sources_config.get(pipeline, [])
        if not isinstance(pipeline_sources, list):
            continue

        for source in pipeline_sources:
            if not isinstance(source, dict):
                continue
            source_entry = dict(source)
            source_entry["pipeline"] = pipeline
            sources_to_process.append(source_entry)

    all_candidates: List[Dict[str, Any]] = []
    current_health: Dict[str, Dict[str, Any]] = {}
    max_workers = _safe_int(settings.get("source_workers", 6), 6, 1, 32)

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_source = {
            executor.submit(process_single_source, source, settings): source
            for source in sources_to_process
        }

        for future in concurrent.futures.as_completed(future_to_source):
            source = future_to_source[future]
            try:
                items, health = future.result()
                all_candidates.extend(items)
                current_health[health["source_id"]] = health
            except Exception as error:
                source_id = str(source.get("id") or "unknown-source")
                current_health[source_id] = {
                    "source_id": source_id,
                    "source_name": str(source.get("name") or source_id),
                    "url": str(source.get("url") or source.get("location") or ""),
                    "pipeline": str(source.get("pipeline") or "tv"),
                    "status": "failed",
                    "last_scan": _utc_now(),
                    "http_status": 0,
                    "attempts": 0,
                    "response_time_ms": 0,
                    "detected_format": "",
                    "raw_items": 0,
                    "error": f"Unhandled source error: {error}",
                }

    manual_items, manual_health = load_manual_sources(sources_config, settings)
    all_candidates.extend(manual_items)
    current_health.update(manual_health)

    output_data: Dict[str, Any] = {
        "timestamp": _utc_now(),
        "mode": mode_clean,
        "active_pipelines": active_pipelines,
        "source_count": len(sources_to_process),
        "manual_candidate_count": len(manual_items),
        "total_candidates": len(all_candidates),
        "items": all_candidates,
    }

    _atomic_write_json("working/candidates.json", output_data)

    previous_health = _load_json_file("state/source-health.json")
    merged_health = _merge_health_history(previous_health, current_health)
    _atomic_write_json(
        "state/source-health.json",
        {
            "updated_at": _utc_now(),
            "last_mode": mode_clean,
            "sources": merged_health,
        },
    )

    return output_data


if __name__ == "__main__":
    result = collect_candidates("all")
    print(f"Collected total candidates: {result['total_candidates']}")
