"""
Bangladesh / BDIX Stream Protection Engine (GitHub-Only Policy)

This module prevents trusted Bangladesh-only streams from being deleted when
GitHub receives geo/network failures.

Status policy:
- verified_global: direct GitHub verification succeeded.
- verified_proxy: a configured proxy adapter returned a genuinely playable stream.
- stale_last_good: a recent real success is preserved temporarily.
- bd_protected_pending: an eligible trusted BD candidate is kept publishable
  because GitHub-only verification is inconclusive.
- failed / failed_bd: not publishable.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import ipaddress
import json
import os
import re
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

try:
    from scanner.verifier import verify_single_stream as _global_verify_single
except ImportError:
    module_dir = str(Path(__file__).resolve().parent)
    if module_dir not in sys.path:
        sys.path.insert(0, module_dir)
    from verifier import verify_single_stream as _global_verify_single


DEFAULT_PROXY_BASE = "https://channelverification.juelgrsan3679.workers.dev"
DEFAULT_PROTECT_HTTP_CODES = {401, 403, 451}
DEFAULT_TRANSIENT_HTTP_CODES = {429, 500, 502, 503, 504}
DEFAULT_PERMANENT_HTTP_CODES = {404, 410}
DEFAULT_TRANSIENT_ERROR_KINDS = {
    "connection",
    "dns",
    "network",
    "ssl",
    "timeout",
}
SENSITIVE_HEADER_NAMES = {
    "authorization",
    "cookie",
    "proxy-authorization",
}


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


def _safe_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)

    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "off", "disabled"}:
        return False
    return default


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


def _parse_iso_datetime(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None

    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _hours_since(value: Any) -> Optional[float]:
    parsed = _parse_iso_datetime(value)
    if parsed is None:
        return None
    return max(
        0.0,
        (datetime.now(timezone.utc) - parsed).total_seconds() / 3600.0,
    )


def _slug(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def _clean_url(raw_url: Any) -> str:
    return str(raw_url or "").split("|", 1)[0].strip()


def _safe_url_for_report(raw_url: Any) -> str:
    """Remove signed query/fragment data from reports."""
    clean_url = _clean_url(raw_url)
    try:
        parts = urllib.parse.urlsplit(clean_url)
        return urllib.parse.urlunsplit(
            (parts.scheme, parts.netloc, parts.path, "", "")
        )
    except Exception:
        return ""


def _stream_identity_keys(
    item: Dict[str, Any],
    include_legacy_raw_url: bool = True,
) -> List[str]:
    keys: List[str] = []
    url = _clean_url(item.get("url"))

    if url:
        if include_legacy_raw_url:
            keys.append(url)
        keys.append(
            "url_sha256:" + hashlib.sha256(url.encode("utf-8")).hexdigest()
        )

    pipeline = _slug(item.get("source_pipeline")) or "unknown"
    item_id = _slug(item.get("id") or item.get("tvg_id"))
    source_id = _slug(item.get("source_id"))
    name = _slug(item.get("name"))

    if item_id:
        keys.append(f"card:{pipeline}:{item_id}")
    if source_id and (item_id or name):
        keys.append(f"source:{source_id}:{item_id or name}")

    return list(dict.fromkeys(key for key in keys if key))


def _clean_domain_rule(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text.startswith("*."):
        text = text[2:]
    return text.lstrip(".")


def _is_safe_domain_rule(value: Any) -> bool:
    domain = _clean_domain_rule(value)
    if not domain or "." not in domain:
        return False

    try:
        ipaddress.ip_address(domain)
        return False
    except ValueError:
        pass

    labels = domain.split(".")
    return all(
        label
        and re.fullmatch(r"[a-z0-9-]+", label)
        and not label.startswith("-")
        and not label.endswith("-")
        for label in labels
    )


def _extract_bd_rules(settings: Dict[str, Any]) -> Dict[str, Any]:
    config = settings.get("bd_verification", {})
    if not isinstance(config, dict):
        config = {}

    domain_values: List[Any] = []
    for key in (
        "known_bd_domains",
        "known_bd_domain_suffixes",
        "exact_domains",
        "domain_suffixes",
    ):
        values = config.get(key, [])
        if isinstance(values, list):
            domain_values.extend(values)

    domains = {
        _clean_domain_rule(value)
        for value in domain_values
        if _is_safe_domain_rule(value)
    }

    exact_ips: Set[str] = set()
    raw_ips = config.get("known_bd_ips", config.get("exact_ips", []))
    if isinstance(raw_ips, list):
        for value in raw_ips:
            text = str(value or "").strip()
            try:
                exact_ips.add(str(ipaddress.ip_address(text)))
            except ValueError:
                continue

    networks: List[ipaddress._BaseNetwork] = []
    raw_cidrs = config.get("known_bd_cidrs", config.get("cidrs", []))
    if isinstance(raw_cidrs, list):
        for value in raw_cidrs:
            try:
                networks.append(
                    ipaddress.ip_network(str(value).strip(), strict=False)
                )
            except ValueError:
                continue

    trusted_source_ids = {
        str(value or "").strip().lower()
        for value in config.get("trusted_source_ids", [])
        if str(value or "").strip()
    } if isinstance(config.get("trusted_source_ids", []), list) else set()

    protect_codes_raw = config.get(
        "protect_http_status_codes",
        sorted(DEFAULT_PROTECT_HTTP_CODES),
    )
    protect_codes = {
        _safe_int(value, 0)
        for value in protect_codes_raw
        if _safe_int(value, 0) > 0
    } if isinstance(protect_codes_raw, list) else set(DEFAULT_PROTECT_HTTP_CODES)

    transient_codes_raw = config.get(
        "transient_http_status_codes",
        sorted(DEFAULT_TRANSIENT_HTTP_CODES),
    )
    transient_codes = {
        _safe_int(value, 0)
        for value in transient_codes_raw
        if _safe_int(value, 0) > 0
    } if isinstance(transient_codes_raw, list) else set(DEFAULT_TRANSIENT_HTTP_CODES)

    permanent_codes_raw = config.get(
        "permanent_http_status_codes",
        sorted(DEFAULT_PERMANENT_HTTP_CODES),
    )
    permanent_codes = {
        _safe_int(value, 0)
        for value in permanent_codes_raw
        if _safe_int(value, 0) > 0
    } if isinstance(permanent_codes_raw, list) else set(DEFAULT_PERMANENT_HTTP_CODES)

    return {
        "domains": domains,
        "exact_ips": exact_ips,
        "networks": networks,
        "trusted_source_ids": trusted_source_ids,
        "protect_http_status_codes": protect_codes,
        "transient_http_status_codes": transient_codes,
        "permanent_http_status_codes": permanent_codes,
    }


def _is_trusted_bd_candidate(
    item: Dict[str, Any],
    rules: Dict[str, Any],
) -> bool:
    if _safe_bool(item.get("bd_candidate"), False):
        return True

    source_id = str(item.get("source_id") or "").strip().lower()
    if source_id and source_id in rules.get("trusted_source_ids", set()):
        return True

    url = _clean_url(item.get("url"))
    try:
        host = (urllib.parse.urlsplit(url).hostname or "").strip().lower()
    except Exception:
        return False

    if not host:
        return False

    for domain in rules.get("domains", set()):
        if host == domain or host.endswith("." + domain):
            return True

    try:
        host_ip = ipaddress.ip_address(host)
    except ValueError:
        return False

    if str(host_ip) in rules.get("exact_ips", set()):
        return True

    return any(
        host_ip in network
        for network in rules.get("networks", [])
    )


def _error_kind(item: Dict[str, Any]) -> str:
    explicit = str(
        item.get("verification_error_kind")
        or item.get("error_kind")
        or ""
    ).strip().lower()
    if explicit:
        return explicit

    message = str(
        item.get("verification_error")
        or item.get("error_reason")
        or ""
    ).lower()

    if "timeout" in message or "timed out" in message:
        return "timeout"
    if "dns" in message or "name or service not known" in message:
        return "dns"
    if "ssl" in message or "certificate" in message:
        return "ssl"
    if "connection" in message or "reset" in message:
        return "connection"
    if "network" in message:
        return "network"
    if "invalid" in message or "html" in message or "manifest" in message:
        return "invalid_content"
    return ""


def _is_movie_candidate(item: Dict[str, Any]) -> bool:
    return (
        str(item.get("source_pipeline") or "").strip().casefold()
        == "movies"
    )


def _looks_like_direct_stream_candidate(item: Dict[str, Any]) -> bool:
    """Return True for stream-like URLs and False for ordinary HTML/PHP wrappers."""
    url = _clean_url(item.get("url"))
    if not url:
        return False

    try:
        parsed = urllib.parse.urlsplit(url)
        path = (parsed.path or "").casefold()
    except Exception:
        return False

    if path.endswith((".php", ".html", ".htm", ".asp", ".aspx", ".jsp")):
        return False

    if path.endswith((
        ".m3u8", ".mpd", ".mp4", ".mkv", ".webm", ".ts",
        ".m2ts", ".aac", ".mp3", ".flv",
    )):
        return True

    manifest_type = str(item.get("manifest_type") or "").strip().casefold()
    if manifest_type in {"hls", "dash", "direct_media", "media"}:
        return True

    content_type = str(item.get("content_type") or "").casefold()
    if any(token in content_type for token in (
        "mpegurl", "dash+xml", "video/", "audio/", "mp2t",
    )):
        return True

    return any(token in path for token in (
        "/stream/channelid/", "/live/", "/hls/", "/dash/",
        "/playlist", "/manifest", "/chunklist",
    ))


def _is_uncertain_tv_result(
    item: Dict[str, Any],
    rules: Dict[str, Any],
) -> bool:
    """A cloud failure on a direct TV stream is not always proof of death."""
    if str(item.get("source_pipeline") or "").strip().casefold() != "tv":
        return False
    if not _looks_like_direct_stream_candidate(item):
        return False

    error_kind = _error_kind(item)
    if error_kind in {
        "invalid_content", "invalid_manifest", "html", "unsupported",
        "media_signature",
    }:
        return False

    http_status = _safe_int(item.get("http_status"), 0)
    if http_status in {401, 403, 451}:
        return True

    trusted = _is_trusted_bd_candidate(item, rules)
    if not trusted:
        return False

    if http_status in {429, 500, 502, 503, 504}:
        return True

    return http_status == 0 and error_kind in {
        "connection", "dns", "network", "ssl", "timeout",
        "host_circuit_open",
    }


def _is_uncertain_movie_result(item: Dict[str, Any]) -> bool:
    if not _is_movie_candidate(item):
        return False

    http_status = _safe_int(item.get("http_status"), 0)
    error_kind = _error_kind(item)
    status = str(item.get("verification_status") or "").strip().casefold()

    if http_status in {401, 403, 429, 451, 500, 502, 503, 504}:
        return True

    if error_kind in {
        "timeout",
        "dns",
        "ssl",
        "connection",
        "network",
        "host_circuit_open",
    }:
        return True

    return status in {
        "needs_bd_check",
        "bd_protected_pending",
        "geo_pending",
        "retryable_pending",
        "host_deferred",
        "stale_last_good",
    }


def _movie_pending_status(item: Dict[str, Any]) -> str:
    http_status = _safe_int(item.get("http_status"), 0)
    error_kind = _error_kind(item)

    if http_status in {429, 500, 502, 503, 504}:
        return "retryable_pending"

    if error_kind == "host_circuit_open":
        return "host_deferred"

    return "geo_pending"


def _eligible_for_github_protection(
    item: Dict[str, Any],
    rules: Dict[str, Any],
) -> bool:
    trusted = _is_trusted_bd_candidate(item, rules)
    movie_uncertain = _is_uncertain_movie_result(item)
    tv_uncertain = _is_uncertain_tv_result(item, rules)

    if not trusted and not movie_uncertain and not tv_uncertain:
        return False

    status = str(item.get("verification_status") or "").strip().lower()
    if status in {
        "needs_bd_check",
        "bd_protected_pending",
        "geo_pending",
        "retryable_pending",
        "host_deferred",
        "stale_last_good",
    }:
        return True

    http_status = _safe_int(item.get("http_status"), 0)

    if http_status in rules.get("permanent_http_status_codes", set()):
        return False

    if http_status in rules.get("protect_http_status_codes", set()):
        return True

    if http_status in rules.get("transient_http_status_codes", set()):
        return True

    return http_status == 0 and _error_kind(item) in {
        "connection",
        "dns",
        "network",
        "ssl",
        "timeout",
        "host_circuit_open",
    }


def _history_records(history: Dict[str, Any]) -> Dict[str, Any]:
    records = history.get("streams", {})
    return records if isinstance(records, dict) else {}


def _migrate_sensitive_history_keys(history: Dict[str, Any]) -> None:
    records = history.get("streams", {})
    if not isinstance(records, dict):
        history["streams"] = {}
        return

    for key in list(records.keys()):
        if not isinstance(key, str) or not key.startswith(("http://", "https://")):
            continue

        record = records.get(key)
        if not isinstance(record, dict):
            records.pop(key, None)
            continue

        hash_key = "url_sha256:" + hashlib.sha256(
            key.encode("utf-8")
        ).hexdigest()

        existing = records.get(hash_key)
        if not isinstance(existing, dict):
            existing = {}

        merged = dict(record)
        merged.update(existing)
        if merged.get("last_url"):
            merged["last_url"] = _safe_url_for_report(merged.get("last_url"))
        records[hash_key] = merged
        records.pop(key, None)

    history["updated_at"] = _utc_now()


def _last_success_record(
    item: Dict[str, Any],
    history: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    records = _history_records(history)
    best_record: Optional[Dict[str, Any]] = None
    best_time: Optional[datetime] = None

    for key in _stream_identity_keys(item):
        record = records.get(key)
        if not isinstance(record, dict):
            continue

        parsed = _parse_iso_datetime(record.get("last_success"))
        if parsed is None:
            continue

        if best_time is None or parsed > best_time:
            best_time = parsed
            best_record = record

    return best_record


def _is_recently_verified(
    item: Dict[str, Any],
    history: Dict[str, Any],
    max_hours: int,
) -> bool:
    record = _last_success_record(item, history)
    if not record:
        return False

    age = _hours_since(record.get("last_success"))
    return age is not None and age <= max_hours


def _record_success(
    item: Dict[str, Any],
    history: Dict[str, Any],
    verification_status: str,
) -> None:
    records = history.setdefault("streams", {})
    if not isinstance(records, dict):
        records = {}
        history["streams"] = records

    now = _utc_now()
    for key in _stream_identity_keys(
        item,
        include_legacy_raw_url=False,
    ):
        record = records.get(key, {})
        if not isinstance(record, dict):
            record = {}

        record.update(
            last_success=now,
            last_status=verification_status,
            last_url=_safe_url_for_report(item.get("url")),
            source_id=str(item.get("source_id") or ""),
            item_id=str(item.get("id") or ""),
            name=str(item.get("name") or ""),
        )
        records[key] = record

    history["updated_at"] = now


def _state_records(state: Dict[str, Any]) -> Dict[str, Any]:
    records = state.get("streams")
    if not isinstance(records, dict):
        records = {}
        state["streams"] = records
    return records


def _primary_state_key(item: Dict[str, Any]) -> str:
    keys = _stream_identity_keys(
        item,
        include_legacy_raw_url=False,
    )
    for key in keys:
        if key.startswith("url_sha256:"):
            return key
    for key in keys:
        if key.startswith("source:") or key.startswith("card:"):
            return key
    return keys[0] if keys else "unknown:" + hashlib.sha256(
        repr(sorted(item.items())).encode("utf-8", errors="ignore")
    ).hexdigest()


def _increment_permanent_failure(
    item: Dict[str, Any],
    state: Dict[str, Any],
    http_status: int,
) -> int:
    records = _state_records(state)
    key = _primary_state_key(item)
    record = records.get(key, {})
    if not isinstance(record, dict):
        record = {}

    previous_status = _safe_int(record.get("last_http_status"), 0)
    previous_count = _safe_int(record.get("permanent_fail_count"), 0)
    count = previous_count + 1 if previous_status == http_status else 1

    record.update(
        permanent_fail_count=count,
        last_http_status=http_status,
        last_checked=_utc_now(),
        last_url=_safe_url_for_report(item.get("url")),
    )
    records[key] = record
    state["updated_at"] = _utc_now()
    return count


def _reset_permanent_failure(item: Dict[str, Any], state: Dict[str, Any]) -> None:
    records = _state_records(state)
    key = _primary_state_key(item)
    record = records.get(key)
    if not isinstance(record, dict):
        return

    record.update(
        permanent_fail_count=0,
        last_http_status=0,
        last_checked=_utc_now(),
    )
    records[key] = record
    state["updated_at"] = _utc_now()


def _proxy_workers(settings: Dict[str, Any]) -> List[Dict[str, Any]]:
    config = settings.get("bd_verification", {})
    if not isinstance(config, dict):
        config = {}

    if not _safe_bool(config.get("proxy_enabled", True), True):
        return []

    configured = config.get("proxy_workers", [])
    workers: List[Dict[str, Any]] = []

    if isinstance(configured, list):
        for index, raw_worker in enumerate(configured):
            if isinstance(raw_worker, str):
                workers.append(
                    {
                        "name": f"proxy-{index + 1}",
                        "base_url": raw_worker,
                    }
                )
            elif isinstance(raw_worker, dict):
                workers.append(dict(raw_worker))

    if not workers:
        base_url = str(
            config.get("proxy_base")
            or config.get("proxy_worker_url")
            or DEFAULT_PROXY_BASE
        ).strip()
        if base_url:
            workers.append(
                {
                    "name": "default-cloudflare-worker",
                    "base_url": base_url,
                    "path": "/hls",
                    "url_param": "url",
                    "headers_param": "headers",
                }
            )

    return workers


def _safe_proxy_headers(
    raw_headers: Any,
    allow_sensitive: bool,
) -> Dict[str, str]:
    if not isinstance(raw_headers, dict):
        return {}

    result: Dict[str, str] = {}
    for key, value in raw_headers.items():
        name = str(key or "").strip()
        clean_value = str(value or "").strip()
        if not name or not clean_value:
            continue
        if not allow_sensitive and name.lower() in SENSITIVE_HEADER_NAMES:
            continue
        if "\r" in name or "\n" in name or "\r" in clean_value or "\n" in clean_value:
            continue
        result[name] = clean_value
    return result


def _build_proxy_url(
    worker: Dict[str, Any],
    original_url: str,
    headers: Dict[str, str],
    allow_sensitive_headers: bool,
) -> Tuple[str, str]:
    base_url = str(worker.get("base_url") or "").strip().rstrip("/")
    if not base_url:
        return "", "Proxy base URL is empty"

    path = str(worker.get("path") or "/hls").strip()
    if path and not path.startswith("/"):
        path = "/" + path

    endpoint = base_url + path
    url_param = str(worker.get("url_param") or "url").strip()
    headers_param = str(worker.get("headers_param") or "headers").strip()

    query: Dict[str, str] = {url_param: _clean_url(original_url)}
    proxy_headers = _safe_proxy_headers(headers, allow_sensitive_headers)
    if proxy_headers and headers_param:
        query[headers_param] = json.dumps(
            proxy_headers,
            separators=(",", ":"),
            ensure_ascii=False,
        )

    separator = "&" if "?" in endpoint else "?"
    return endpoint + separator + urllib.parse.urlencode(query), ""


def _proxy_verifier_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
    copied = dict(settings)
    copied["bd_verification"] = {}

    verification = settings.get("verification", {})
    copied_verification = dict(verification) if isinstance(verification, dict) else {}
    copied_verification["retry_attempts"] = _safe_int(
        copied_verification.get("retry_attempts", 1),
        1,
        1,
        2,
    )
    copied["verification"] = copied_verification

    resolution = settings.get("resolution", {})
    copied_resolution = dict(resolution) if isinstance(resolution, dict) else {}
    copied_resolution["allow_unknown_tv_resolution"] = True
    copied_resolution["allow_unknown_event_resolution"] = True
    copied["resolution"] = copied_resolution
    return copied


def _verify_via_proxy_workers(
    item: Dict[str, Any],
    settings: Dict[str, Any],
) -> Tuple[bool, Dict[str, Any]]:
    config = settings.get("bd_verification", {})
    if not isinstance(config, dict):
        config = {}

    allow_sensitive_headers = _safe_bool(
        config.get("proxy_send_sensitive_headers", False),
        False,
    )
    proxy_timeout = _safe_int(
        config.get(
            "proxy_timeout_seconds",
            settings.get("stream_timeout_seconds", 8),
        ),
        8,
        1,
        30,
    )

    proxy_settings = _proxy_verifier_settings(settings)
    proxy_settings["stream_timeout_seconds"] = proxy_timeout

    last_detail: Dict[str, Any] = {
        "proxy_error": "No proxy worker configured",
        "proxy_http_status": 0,
        "proxy_name": "",
    }

    direct_browser_retry = _safe_bool(
        config.get("direct_browser_second_pass", True),
        True,
    )
    if direct_browser_retry and _looks_like_direct_stream_candidate(item):
        browser_candidate = dict(item)
        browser_headers = dict(
            item.get("headers") if isinstance(item.get("headers"), dict) else {}
        )
        lower_names = {str(key).casefold() for key in browser_headers}
        if "user-agent" not in lower_names:
            browser_headers["User-Agent"] = (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            )
        if "accept" not in lower_names:
            browser_headers["Accept"] = (
                "application/vnd.apple.mpegurl,application/x-mpegURL,"
                "application/dash+xml,video/*,audio/*,*/*;q=0.8"
            )
        browser_candidate.update(
            headers=browser_headers,
            verified=False,
            publish_allowed=False,
            verification_status="",
            verification_mode="direct_browser_second_pass",
        )
        try:
            checked = _global_verify_single(browser_candidate, proxy_settings, {})
        except Exception as error:
            checked = {
                "verified": False,
                "http_status": 0,
                "verification_error": f"Direct browser retry exception: {error}",
            }

        last_detail = {
            "proxy_error": str(checked.get("verification_error") or ""),
            "proxy_http_status": _safe_int(checked.get("http_status"), 0),
            "proxy_name": "direct-browser-retry",
            "proxy_manifest_type": str(checked.get("manifest_type") or ""),
            "proxy_segment_verified": _safe_bool(
                checked.get("segment_verified"), False
            ),
        }
        if checked.get("verified") is True:
            last_detail["proxy_checked_at"] = _utc_now()
            return True, last_detail

    for worker in _proxy_workers(settings):
        worker_name = str(worker.get("name") or "cloudflare-worker")
        proxy_url, build_error = _build_proxy_url(
            worker=worker,
            original_url=str(item.get("url") or ""),
            headers=item.get("headers") if isinstance(item.get("headers"), dict) else {},
            allow_sensitive_headers=allow_sensitive_headers,
        )

        if build_error or not proxy_url:
            last_detail = {
                "proxy_error": build_error or "Invalid proxy URL",
                "proxy_http_status": 0,
                "proxy_name": worker_name,
            }
            continue

        proxy_candidate = dict(item)
        proxy_candidate.update(
            url=proxy_url,
            headers={},
            bd_candidate=False,
            metadata_only=False,
            verified=False,
            verification_status="",
            source_id=f"proxy-check:{worker_name}",
        )

        try:
            checked = _global_verify_single(
                proxy_candidate,
                proxy_settings,
                {},
            )
        except Exception as error:
            last_detail = {
                "proxy_error": f"Proxy verifier exception: {error}",
                "proxy_http_status": 0,
                "proxy_name": worker_name,
            }
            continue

        last_detail = {
            "proxy_error": str(checked.get("verification_error") or ""),
            "proxy_http_status": _safe_int(checked.get("http_status"), 0),
            "proxy_name": worker_name,
            "proxy_manifest_type": str(checked.get("manifest_type") or ""),
            "proxy_segment_verified": _safe_bool(
                checked.get("segment_verified"),
                False,
            ),
        }

        if checked.get("verified") is True:
            last_detail["proxy_checked_at"] = _utc_now()
            return True, last_detail

    return False, last_detail


def verify_bd_stream(
    candidate: Dict[str, Any],
    settings: Dict[str, Any],
    stream_history: Dict[str, Any],
    protection_state: Dict[str, Any],
    bd_rules: Dict[str, Any],
    proxy_result: Optional[Tuple[bool, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Finish one candidate after the immediate same-run second pass.

    GitHub/Colab cannot prove Bangladesh-IP playback. Therefore an uncertain
    movie is kept publishable after the second pass without being labelled as
    verified. Only confirmed permanent/invalid failures are removed.
    """
    item = dict(candidate or {})
    status = str(item.get("verification_status") or "").strip().lower()

    if status == "metadata_only":
        item["publish_allowed"] = True
        return item

    if item.get("verified") is True:
        item["publish_allowed"] = True
        if status in {"verified_global", "verified_proxy", "verified_bd"}:
            _record_success(item, stream_history, status)
            _reset_permanent_failure(item, protection_state)
        return item

    url = _clean_url(item.get("url"))
    if not url:
        item.update(
            verified=False,
            publish_allowed=False,
            verification_status="failed",
            verification_mode="same_run_cloud_second_pass",
            verification_error="Stream URL is empty",
        )
        return item

    trusted = _is_trusted_bd_candidate(item, bd_rules)
    movie_uncertain = _is_uncertain_movie_result(item)
    tv_uncertain = _is_uncertain_tv_result(item, bd_rules)
    eligible = _eligible_for_github_protection(item, bd_rules)

    if not eligible and not movie_uncertain and not tv_uncertain:
        item["publish_allowed"] = False
        return item

    bd_config = settings.get("bd_verification", {})
    if not isinstance(bd_config, dict):
        bd_config = {}

    keep_pending_hours = _safe_int(
        bd_config.get("keep_pending_hours", 48),
        48,
        1,
        24 * 30,
    )
    permanent_confirmations = _safe_int(
        bd_config.get("permanent_fail_confirmations", 2),
        2,
        1,
        10,
    )
    publish_uncertain_movies = _safe_bool(
        bd_config.get("publish_uncertain_movies", True),
        True,
    )

    http_status = _safe_int(item.get("http_status"), 0)
    permanent_codes = bd_rules.get(
        "permanent_http_status_codes",
        DEFAULT_PERMANENT_HTTP_CODES,
    )

    if proxy_result is None:
        proxy_ok, proxy_detail = _verify_via_proxy_workers(item, settings)
    else:
        proxy_ok, proxy_detail = proxy_result

    item.update(proxy_detail)

    if proxy_ok:
        item.update(
            verified=True,
            publish_allowed=True,
            verification_status="verified_proxy",
            verification_mode="same_run_cloud_proxy",
            verification_error="",
            verification_note="",
            last_check_success=True,
            recent_success=True,
        )
        _record_success(item, stream_history, "verified_proxy")
        _reset_permanent_failure(item, protection_state)
        return item

    if http_status in permanent_codes:
        fail_count = _increment_permanent_failure(
            item,
            protection_state,
            http_status,
        )
        item["permanent_fail_count"] = fail_count
        item["permanent_fail_required"] = permanent_confirmations

        if _is_recently_verified(item, stream_history, keep_pending_hours):
            item.update(
                verified=False,
                publish_allowed=True,
                verification_status="stale_last_good",
                verification_mode="preserved_last_good",
                verification_note=(
                    f"Recent success preserved while HTTP {http_status} "
                    f"is confirmed ({fail_count}/{permanent_confirmations})"
                ),
            )
            return item

        if trusted and fail_count < permanent_confirmations:
            item.update(
                verified=False,
                publish_allowed=True,
                verification_status="bd_protected_pending",
                verification_mode="same_run_cloud_second_pass",
                verification_note=(
                    "Temporary protection during permanent-failure "
                    f"confirmation ({fail_count}/{permanent_confirmations})"
                ),
            )
            return item

        item.update(
            verified=False,
            publish_allowed=False,
            verification_status="failed_bd",
            verification_mode="same_run_cloud_second_pass",
            verification_error=(
                f"Confirmed permanent HTTP {http_status} after "
                f"{fail_count} consecutive checks"
            ),
        )
        return item

    _reset_permanent_failure(item, protection_state)

    if _is_recently_verified(item, stream_history, keep_pending_hours):
        item.update(
            verified=False,
            publish_allowed=True,
            verification_status="stale_last_good",
            verification_mode="preserved_last_good",
            verification_note=(
                f"Preserved under {keep_pending_hours}h last-good policy"
            ),
        )
        return item

    # A movie that still returns 403/451/timeout/transport/429/5xx after the
    # immediate second pass is not proven dead from GitHub/Colab. Keep it in the
    # output so a Bangladesh user's browser can try the original URL directly.
    if movie_uncertain and publish_uncertain_movies:
        pending_status = _movie_pending_status(item)
        item.update(
            verified=False,
            publish_allowed=True,
            verification_status=pending_status,
            verification_mode="same_run_cloud_inconclusive",
            verification_note=(
                "The same-run cloud second pass was inconclusive; the movie "
                "was kept without claiming Bangladesh-IP verification"
            ),
        )
        return item

    if tv_uncertain:
        item.update(
            verified=False,
            publish_allowed=True,
            verification_status="bd_protected_pending",
            verification_mode="same_run_cloud_inconclusive",
            verification_note=(
                "The direct TV stream remained inconclusive after the same-run "
                "browser/proxy pass; it was kept so Bangladesh users can try "
                "the original URL without claiming confirmed verification"
            ),
        )
        return item

    if trusted:
        item.update(
            verified=False,
            publish_allowed=True,
            verification_status="bd_protected_pending",
            verification_mode="github_only_protection",
            verification_note=(
                "GitHub/Colab verification was inconclusive; trusted BD "
                "candidate was kept without claiming Bangladesh-IP verification"
            ),
        )
        return item

    item.update(
        verified=False,
        publish_allowed=False,
        verification_status="failed_bd",
        verification_mode="same_run_cloud_second_pass",
        verification_error=(
            str(item.get("proxy_error") or "")
            or "Candidate is not covered by protected movie rules"
        ),
    )
    return item


def _report_item(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": item.get("id", ""),
        "name": item.get("name", ""),
        "source_id": item.get("source_id", ""),
        "source_pipeline": item.get("source_pipeline", ""),
        "url": _safe_url_for_report(item.get("url")),
        "verification_status": item.get("verification_status", ""),
        "verification_mode": item.get("verification_mode", ""),
        "publish_allowed": _safe_bool(item.get("publish_allowed"), False),
        "http_status": _safe_int(item.get("http_status"), 0),
        "proxy_name": item.get("proxy_name", ""),
        "proxy_http_status": _safe_int(item.get("proxy_http_status"), 0),
        "verification_error": item.get("verification_error", ""),
        "verification_note": item.get("verification_note", ""),
        "permanent_fail_count": _safe_int(
            item.get("permanent_fail_count"),
            0,
        ),
    }


def verify_bd_candidates(
    global_results_path: str = "working/global-results.json",
    settings_path: str = "config/settings.json",
    history_path: str = "state/stream-history.json",
    state_path: str = "state/bd-protection-state.json",
    output_path: str = "working/bd-results.json",
    report_path: str = "reports/bd-verification.json",
) -> Dict[str, Any]:
    global_file = Path(global_results_path)
    if not global_file.exists():
        raise FileNotFoundError(
            f"Global results file not found: {global_results_path}"
        )

    global_data = _load_json_file(global_file)
    if "results" not in global_data:
        raise ValueError(
            f"Global results are invalid or missing 'results': "
            f"{global_results_path}"
        )

    results = global_data.get("results", [])
    if not isinstance(results, list):
        raise ValueError("working/global-results.json field 'results' must be a list")

    settings = _load_json_file(settings_path)
    stream_history = _load_json_file(history_path)
    _migrate_sensitive_history_keys(stream_history)
    protection_state = _load_json_file(state_path)
    bd_rules = _extract_bd_rules(settings)

    bd_config = settings.get("bd_verification", {})
    if not isinstance(bd_config, dict):
        bd_config = {}

    verification = settings.get("verification", {})
    if not isinstance(verification, dict):
        verification = {}

    workers = _safe_int(
        bd_config.get(
            "workers",
            verification.get(
                "workers",
                settings.get("verification_workers", 12),
            ),
        ),
        12,
        1,
        32,
    )

    proxy_results: Dict[int, Tuple[bool, Dict[str, Any]]] = {}

    eligible_indexes = [
        index
        for index, item in enumerate(results)
        if isinstance(item, dict)
        and item.get("verified") is not True
        and _eligible_for_github_protection(item, bd_rules)
    ]

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        future_map = {
            executor.submit(
                _verify_via_proxy_workers,
                dict(results[index]),
                settings,
            ): index
            for index in eligible_indexes
        }

        for future in concurrent.futures.as_completed(future_map):
            index = future_map[future]
            try:
                proxy_results[index] = future.result()
            except Exception as error:
                proxy_results[index] = (
                    False,
                    {
                        "proxy_error": f"Proxy worker exception: {error}",
                        "proxy_http_status": 0,
                        "proxy_name": "",
                    },
                )

    ordered_results: List[Dict[str, Any]] = []

    for index, raw_item in enumerate(results):
        if not isinstance(raw_item, dict):
            continue

        cached_proxy = proxy_results.get(
            index,
            (
                False,
                {
                    "proxy_error": "Proxy check not required",
                    "proxy_http_status": 0,
                    "proxy_name": "",
                },
            ),
        )

        try:
            processed = verify_bd_stream(
                candidate=raw_item,
                settings=settings,
                stream_history=stream_history,
                protection_state=protection_state,
                bd_rules=bd_rules,
                proxy_result=cached_proxy,
            )
        except Exception as error:
            processed = dict(raw_item)
            if _eligible_for_github_protection(processed, bd_rules):
                processed.update(
                    verified=False,
                    publish_allowed=True,
                    verification_status="bd_protected_pending",
                    verification_mode="github_only_protection",
                    verification_error=(
                        f"BD protection worker exception: {error}"
                    ),
                )
            else:
                processed.update(
                    publish_allowed=bool(processed.get("verified")),
                    verification_error=(
                        f"BD protection worker exception: {error}"
                    ),
                )

        ordered_results.append(processed)

    _atomic_write_json(history_path, stream_history)
    _atomic_write_json(state_path, protection_state)

    status_counts: Dict[str, int] = {}
    for item in ordered_results:
        status = str(item.get("verification_status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1

    publishable = [
        item
        for item in ordered_results
        if item.get("verified") is True
        or item.get("publish_allowed") is True
    ]

    report_groups: Dict[str, List[Dict[str, Any]]] = {}
    for item in ordered_results:
        status = str(item.get("verification_status") or "unknown")
        report_groups.setdefault(status, []).append(_report_item(item))

    report_data: Dict[str, Any] = {
        "timestamp": _utc_now(),
        "total_input": len(results),
        "total_processed": len(ordered_results),
        "total_proxy_checked": len(eligible_indexes),
        "total_publishable": len(publishable),
        "status_counts": status_counts,
        "groups": report_groups,
    }
    _atomic_write_json(report_path, report_data)

    output_data: Dict[str, Any] = {
        "timestamp": _utc_now(),
        "mode": global_data.get("mode", "all"),
        "total_candidates": len(results),
        "total_processed": len(ordered_results),
        "total_verified": sum(
            1 for item in ordered_results if item.get("verified") is True
        ),
        "total_publishable": len(publishable),
        "status_counts": status_counts,
        "results": ordered_results,
    }
    _atomic_write_json(output_path, output_data)
    return output_data


if __name__ == "__main__":
    summary = verify_bd_candidates()
    print(
        "BD protection processed "
        f"{summary['total_processed']} candidates; "
        f"{summary['total_publishable']} are publishable"
    )
