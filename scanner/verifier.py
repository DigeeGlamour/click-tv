"""
Global Stream Verifier Engine

Concurrently verifies HLS, DASH, MP4, TS, WebM, and related HTTP/HTTPS
stream candidates. It preserves request headers, probes HLS media segments,
extracts manifest resolution, applies configurable live-TV quality rules,
and safely marks eligible Bangladesh-only candidates as `needs_bd_check`.

This module does not invent proxy URL formats. Proxy verification should be
implemented only after a concrete proxy-adapter contract is configured.
"""

from __future__ import annotations

import concurrent.futures
import ipaddress
import json
import os
import re
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from scanner.visibility_audit import audit_hide_safe, model_permits_hide


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

DEFAULT_RETRY_STATUS_CODES = {429, 500, 502, 503, 504}
DEFAULT_BD_DEFER_STATUS_CODES = {403, 451}

HLS_CONTENT_TYPES = {
    "application/vnd.apple.mpegurl",
    "application/x-mpegurl",
    "audio/mpegurl",
    "audio/x-mpegurl",
}

DASH_CONTENT_TYPES = {
    "application/dash+xml",
}

DIRECT_MEDIA_EXTENSIONS = {
    ".aac",
    ".flac",
    ".flv",
    ".m4a",
    ".m4s",
    ".mkv",
    ".mov",
    ".mp3",
    ".mp4",
    ".mpeg",
    ".mpg",
    ".ogg",
    ".opus",
    ".ts",
    ".webm",
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


def _resolution_label(height: int) -> str:
    if height >= 2160:
        return "4K"
    if height >= 1440:
        return "2K"
    if height >= 1080:
        return "FHD"
    if height >= 720:
        return "HD"
    if height > 0:
        return "SD"
    return ""


def _parse_resolution_height(value: Any) -> int:
    if value in (None, ""):
        return 0
    if isinstance(value, (int, float)):
        return max(0, int(value))

    text = str(value).strip().upper()

    dimension_match = re.search(r"\d+\s*[X×]\s*(\d+)", text)
    if dimension_match:
        return _safe_int(dimension_match.group(1), 0)

    progressive_match = re.search(r"(\d+)\s*P\b", text)
    if progressive_match:
        return _safe_int(progressive_match.group(1), 0)

    if "4K" in text:
        return 2160
    if "2K" in text:
        return 1440
    if "FHD" in text or "FULL HD" in text:
        return 1080
    if re.search(r"\bHD\b", text):
        return 720
    if re.search(r"\bSD\b", text):
        return 480

    return _safe_int(text, 0)


# ---------------------------------------------------------------------------
# URL and request-header helpers
# ---------------------------------------------------------------------------

def _canonical_header_name(name: str) -> str:
    normalized = str(name or "").strip().lower().replace("_", "-")
    aliases = {
        "user-agent": "User-Agent",
        "http-user-agent": "User-Agent",
        "referer": "Referer",
        "referrer": "Referer",
        "http-referer": "Referer",
        "http-referrer": "Referer",
        "origin": "Origin",
        "http-origin": "Origin",
        "cookie": "Cookie",
        "authorization": "Authorization",
        "accept": "Accept",
        "accept-language": "Accept-Language",
        "accept-encoding": "Accept-Encoding",
        "range": "Range",
    }
    return aliases.get(normalized, str(name or "").strip())


def _split_pipe_headers(raw_url: str) -> Tuple[str, Dict[str, str]]:
    if "|" not in raw_url:
        return raw_url.strip(), {}

    request_url, raw_query = raw_url.split("|", 1)
    result: Dict[str, str] = {}

    for key, value in urllib.parse.parse_qsl(
        raw_query,
        keep_blank_values=True,
    ):
        canonical = _canonical_header_name(key)
        if canonical:
            result[canonical] = value

    return request_url.strip(), result


def _sanitize_headers(
    existing_headers: Any,
    pipe_headers: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    result: Dict[str, str] = {}

    # Existing/profile headers are applied first. URL pipe headers are
    # stream-specific and therefore must have the final override priority.
    for container in (existing_headers, pipe_headers):
        if not isinstance(container, dict):
            continue

        for key, value in container.items():
            if value is None or isinstance(value, (dict, list, tuple, set)):
                continue

            canonical = _canonical_header_name(str(key))
            clean_value = str(value).strip()

            if (
                not canonical
                or not clean_value
                or "\r" in canonical
                or "\n" in canonical
                or "\r" in clean_value
                or "\n" in clean_value
            ):
                continue

            result[canonical] = clean_value

    return result


def _build_request_headers(
    existing_headers: Any,
    pipe_headers: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    result: Dict[str, str] = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": "*/*",
        "Accept-Encoding": "identity",
        "Connection": "close",
    }
    result.update(
        _sanitize_headers(existing_headers, pipe_headers)
    )
    return result


def _join_stream_url(base_url: str, reference: str) -> str:
    """
    Resolve a manifest-relative URI and preserve the base query string when
    the child URI does not provide its own query.
    """
    clean_reference = str(reference or "").strip()
    if not clean_reference:
        return ""

    joined = urllib.parse.urljoin(base_url, clean_reference)

    try:
        base_parts = urllib.parse.urlparse(base_url)
        joined_parts = urllib.parse.urlparse(joined)
    except Exception:
        return joined

    reference_has_query = "?" in clean_reference
    is_absolute_reference = bool(
        urllib.parse.urlparse(clean_reference).scheme
        or clean_reference.startswith("//")
    )

    if (
        not is_absolute_reference
        and not reference_has_query
        and not joined_parts.query
        and base_parts.query
    ):
        joined_parts = joined_parts._replace(query=base_parts.query)
        joined = urllib.parse.urlunparse(joined_parts)

    return joined


def _url_scheme(url: str) -> str:
    try:
        return urllib.parse.urlparse(url).scheme.lower()
    except Exception:
        return ""


def _url_extension(url: str) -> str:
    try:
        return Path(urllib.parse.urlparse(url).path).suffix.lower()
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Bangladesh/BDIX candidate matching
# ---------------------------------------------------------------------------

def _clean_domain_rule(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text.startswith("*."):
        text = text[2:]
    return text.lstrip(".")


def _is_safe_domain_rule(value: Any) -> bool:
    """
    Accept concrete domains such as `toffee.live` or `cdn.example.com`.
    Reject TLD-wide rules such as `bd`/`.bd` and bare labels.
    """
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

    exact_ips = config.get("known_bd_ips", config.get("exact_ips", []))
    cidrs = config.get("known_bd_cidrs", config.get("cidrs", []))

    domains = {
        _clean_domain_rule(value)
        for value in domain_values
        if _is_safe_domain_rule(value)
    }

    ip_values = {
        str(value).strip()
        for value in exact_ips
        if str(value).strip()
    } if isinstance(exact_ips, list) else set()

    networks: List[ipaddress._BaseNetwork] = []
    if isinstance(cidrs, list):
        for value in cidrs:
            try:
                networks.append(
                    ipaddress.ip_network(
                        str(value).strip(),
                        strict=False,
                    )
                )
            except ValueError:
                continue

    defer_statuses_raw = config.get(
        "defer_http_status_codes",
        sorted(DEFAULT_BD_DEFER_STATUS_CODES),
    )
    if isinstance(defer_statuses_raw, list):
        defer_statuses = {
            _safe_int(value, 0)
            for value in defer_statuses_raw
            if _safe_int(value, 0) > 0
        }
    else:
        defer_statuses = set(DEFAULT_BD_DEFER_STATUS_CODES)

    return {
        "domains": domains,
        "exact_ips": ip_values,
        "networks": networks,
        "defer_http_status_codes": defer_statuses,
    }


def is_bd_candidate_url(
    url: str,
    bd_rules: Dict[str, Any] | Sequence[str],
) -> bool:
    request_url, _ = _split_pipe_headers(str(url or ""))

    try:
        host = (
            urllib.parse.urlparse(request_url).hostname
            or ""
        ).strip().lower()
    except Exception:
        return False

    if not host:
        return False

    if not isinstance(bd_rules, dict):
        domains = {
            _clean_domain_rule(value)
            for value in bd_rules
            if _is_safe_domain_rule(value)
        }
        return any(
            host == domain or host.endswith("." + domain)
            for domain in domains
        )

    domains = bd_rules.get("domains", set())
    if isinstance(domains, (set, list, tuple)):
        for domain in domains:
            domain_clean = _clean_domain_rule(domain)
            if host == domain_clean or host.endswith("." + domain_clean):
                return True

    exact_ips = bd_rules.get("exact_ips", set())
    if host in exact_ips:
        return True

    try:
        host_ip = ipaddress.ip_address(host)
    except ValueError:
        return False

    networks = bd_rules.get("networks", [])
    return any(host_ip in network for network in networks)


def _should_defer_to_bd(
    status_code: int,
    error_kind: str,
    bd_rules: Dict[str, Any],
) -> bool:
    defer_statuses = bd_rules.get(
        "defer_http_status_codes",
        DEFAULT_BD_DEFER_STATUS_CODES,
    )

    if status_code in defer_statuses:
        return True

    return error_kind in {
        "connection",
        "dns",
        "network",
        "ssl",
        "timeout",
    }


# ---------------------------------------------------------------------------
# HTTP fetching
# ---------------------------------------------------------------------------

def _looks_like_html(body: bytes, content_type: str = "") -> bool:
    if "text/html" in content_type.lower():
        return True

    prefix = body[:2048].lstrip().lower()
    return (
        prefix.startswith(b"<!doctype html")
        or prefix.startswith(b"<html")
        or b"<html" in prefix[:512]
    )


def _decode_text(body: bytes, charset: str = "utf-8") -> str:
    try:
        return body.decode(charset or "utf-8", errors="ignore")
    except (LookupError, UnicodeError):
        return body.decode("utf-8", errors="ignore")


def _fetch_once(
    url: str,
    headers: Dict[str, str],
    timeout: int,
    verify_ssl: bool,
    max_bytes: int,
    range_request: bool = False,
) -> Dict[str, Any]:
    request_headers = dict(headers)
    if range_request and "Range" not in request_headers:
        request_headers["Range"] = f"bytes=0-{max(0, max_bytes - 1)}"

    ssl_context = None if verify_ssl else ssl._create_unverified_context()
    request = urllib.request.Request(url, headers=request_headers)

    try:
        with urllib.request.urlopen(
            request,
            timeout=timeout,
            context=ssl_context,
        ) as response:
            status_code = _safe_int(
                getattr(response, "status", 200),
                200,
            )
            # urllib's timeout is an inactivity timeout, not a total request
            # deadline. A server can otherwise drip a byte periodically and
            # hold a worker forever. Read in bounded chunks and enforce a hard
            # wall-clock deadline as well.
            try:
                response.fp.raw._sock.settimeout(max(0.5, min(2.0, float(timeout))))
            except (AttributeError, OSError, ValueError):
                pass
            deadline = time.monotonic() + max(1, timeout)
            chunks: List[bytes] = []
            remaining = max_bytes + 1
            while remaining > 0:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Response body exceeded hard request deadline")
                chunk = response.read(min(16 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            raw_body = b"".join(chunks)
            truncated = len(raw_body) > max_bytes
            body = raw_body[:max_bytes]

            content_type = str(
                response.headers.get("Content-Type", "")
            ).strip().lower()
            charset = (
                response.headers.get_content_charset()
                if hasattr(response.headers, "get_content_charset")
                else None
            ) or "utf-8"

            return {
                "ok": status_code in {200, 206},
                "status_code": status_code,
                "body": body,
                "truncated": truncated,
                "content_type": content_type,
                "charset": charset,
                "final_url": str(
                    getattr(response, "geturl", lambda: url)()
                    or url
                ),
                "error_kind": "",
                "error": "",
            }

    except urllib.error.HTTPError as error:
        status_code = _safe_int(
            getattr(error, "code", 0),
            0,
        )

        return {
            "ok": False,
            "status_code": status_code,
            "body": b"",
            "truncated": False,
            "content_type": "",
            "charset": "utf-8",
            "final_url": url,
            "error_kind": "http",
            "error": (
                f"HTTP {status_code}: "
                f"{getattr(error, 'reason', '')}"
            ).strip(),
        }
    except urllib.error.URLError as error:
        reason = getattr(error, "reason", error)

        if isinstance(reason, socket.timeout):
            error_kind = "timeout"
        elif isinstance(reason, ssl.SSLError):
            error_kind = "ssl"
        elif isinstance(reason, socket.gaierror):
            error_kind = "dns"
        elif isinstance(reason, ConnectionError):
            error_kind = "connection"
        else:
            error_kind = "network"

        return {
            "ok": False,
            "status_code": 0,
            "body": b"",
            "truncated": False,
            "content_type": "",
            "charset": "utf-8",
            "final_url": url,
            "error_kind": error_kind,
            "error": f"{error_kind}: {reason}",
        }
    except (socket.timeout, TimeoutError):
        return {
            "ok": False,
            "status_code": 0,
            "body": b"",
            "truncated": False,
            "content_type": "",
            "charset": "utf-8",
            "final_url": url,
            "error_kind": "timeout",
            "error": "Request timed out",
        }
    except ssl.SSLError as error:
        return {
            "ok": False,
            "status_code": 0,
            "body": b"",
            "truncated": False,
            "content_type": "",
            "charset": "utf-8",
            "final_url": url,
            "error_kind": "ssl",
            "error": f"SSL error: {error}",
        }
    except (OSError, ValueError) as error:
        return {
            "ok": False,
            "status_code": 0,
            "body": b"",
            "truncated": False,
            "content_type": "",
            "charset": "utf-8",
            "final_url": url,
            "error_kind": "connection",
            "error": f"Connection error: {error}",
        }


def _fetch_with_retry(
    url: str,
    headers: Dict[str, str],
    timeout: int,
    verify_ssl: bool,
    max_bytes: int,
    attempts: int,
    retry_delays: Sequence[int],
    retry_status_codes: Iterable[int],
    range_request: bool = False,
) -> Dict[str, Any]:
    attempts = _safe_int(attempts, 2, 1, 5)
    retry_codes = {
        _safe_int(value, 0)
        for value in retry_status_codes
        if _safe_int(value, 0) > 0
    }

    last_result: Dict[str, Any] = {
        "ok": False,
        "status_code": 0,
        "body": b"",
        "truncated": False,
        "content_type": "",
        "charset": "utf-8",
        "final_url": url,
        "error_kind": "network",
        "error": "No request attempted",
    }

    for attempt_index in range(attempts):
        delay = (
            _safe_int(retry_delays[attempt_index], 0, 0, 30)
            if attempt_index < len(retry_delays)
            else 0
        )
        if delay:
            time.sleep(delay)

        last_result = _fetch_once(
            url=url,
            headers=headers,
            timeout=timeout,
            verify_ssl=verify_ssl,
            max_bytes=max_bytes,
            range_request=range_request,
        )
        last_result["attempts"] = attempt_index + 1

        if (
            range_request
            and not last_result.get("ok")
            and _safe_int(last_result.get("status_code"), 0)
            in {400, 405, 416}
        ):
            last_result = _fetch_once(
                url=url,
                headers=headers,
                timeout=timeout,
                verify_ssl=verify_ssl,
                max_bytes=max_bytes,
                range_request=False,
            )
            last_result["attempts"] = attempt_index + 1
            last_result["range_fallback_used"] = True

        if last_result.get("ok"):
            return last_result

        status_code = _safe_int(
            last_result.get("status_code"),
            0,
        )
        error_kind = str(
            last_result.get("error_kind") or ""
        )

        retryable = (
            status_code in retry_codes
            or error_kind in {
                "connection",
                "dns",
                "network",
                "ssl",
                "timeout",
            }
        )

        if not retryable:
            break

    return last_result


# ---------------------------------------------------------------------------
# Manifest parsing and media probing
# ---------------------------------------------------------------------------

def parse_manifest_resolution(content: str) -> Tuple[int, str]:
    if not content:
        return 0, ""

    heights: List[int] = []

    for match in re.findall(
        r"RESOLUTION\s*=\s*\d+\s*[xX×]\s*(\d+)",
        content,
        flags=re.IGNORECASE,
    ):
        height = _safe_int(match, 0)
        if height > 0:
            heights.append(height)

    for match in re.findall(
        r"\bheight\s*=\s*[\"'](\d+)[\"']",
        content,
        flags=re.IGNORECASE,
    ):
        height = _safe_int(match, 0)
        if height > 0:
            heights.append(height)

    if not heights:
        return 0, ""

    maximum_height = max(heights)
    return maximum_height, _resolution_label(maximum_height)


def _hls_attribute(line: str, attribute: str) -> str:
    pattern = (
        rf"(?:^|,)\s*{re.escape(attribute)}\s*=\s*"
        rf"(?:\"([^\"]*)\"|([^,]*))"
    )
    match = re.search(pattern, line, flags=re.IGNORECASE)
    if not match:
        return ""
    return str(match.group(1) or match.group(2) or "").strip()


def _parse_hls_variants(
    content: str,
    manifest_url: str,
) -> List[Tuple[int, str]]:
    lines = content.splitlines()
    variants: List[Tuple[int, str]] = []

    for index, line in enumerate(lines):
        stripped = line.strip()

        if stripped.upper().startswith("#EXT-X-STREAM-INF"):
            height = 0
            resolution = _hls_attribute(
                stripped.split(":", 1)[-1],
                "RESOLUTION",
            )
            if resolution:
                height = _parse_resolution_height(resolution)

            for next_line in lines[index + 1:]:
                uri = next_line.strip()
                if not uri:
                    continue
                if uri.startswith("#"):
                    continue
                variants.append(
                    (
                        height,
                        _join_stream_url(
                            manifest_url,
                            uri,
                        ),
                    )
                )
                break

    return variants


def _extract_hls_media_urls(
    content: str,
    manifest_url: str,
) -> List[str]:
    lines = content.splitlines()
    media_urls: List[str] = []

    for line in lines:
        stripped = line.strip()
        upper = stripped.upper()

        if not stripped:
            continue

        if upper.startswith("#EXT-X-MAP"):
            uri = _hls_attribute(
                stripped.split(":", 1)[-1],
                "URI",
            )
            if uri:
                media_urls.append(
                    _join_stream_url(
                        manifest_url,
                        uri,
                    )
                )
            continue

        if upper.startswith("#EXT-X-PART"):
            uri = _hls_attribute(
                stripped.split(":", 1)[-1],
                "URI",
            )
            if uri:
                media_urls.append(
                    _join_stream_url(
                        manifest_url,
                        uri,
                    )
                )
            continue

        if upper.startswith("#EXT-X-PRELOAD-HINT"):
            attributes = stripped.split(":", 1)[-1]
            hint_type = _hls_attribute(
                attributes,
                "TYPE",
            ).upper()
            uri = _hls_attribute(attributes, "URI")
            if hint_type == "PART" and uri:
                media_urls.append(
                    _join_stream_url(
                        manifest_url,
                        uri,
                    )
                )
            continue

        if not stripped.startswith("#"):
            media_urls.append(
                _join_stream_url(
                    manifest_url,
                    stripped,
                )
            )

    unique_reversed: List[str] = []
    seen: set[str] = set()
    for value in reversed(media_urls):
        if value and value not in seen:
            seen.add(value)
            unique_reversed.append(value)

    return unique_reversed


def _extract_dash_probe_urls(
    content: str,
    manifest_url: str,
) -> List[str]:
    base_references = re.findall(
        r"<BaseURL[^>]*>\s*([^<]+?)\s*</BaseURL>",
        content,
        flags=re.IGNORECASE,
    )
    media_references: List[str] = []
    for pattern in (
        r"\bsourceURL\s*=\s*[\"']([^\"']+)[\"']",
        r"\bmedia\s*=\s*[\"']([^\"']+)[\"']",
        r"\binitialization\s*=\s*[\"']([^\"']+)[\"']",
    ):
        media_references.extend(
            re.findall(pattern, content, flags=re.IGNORECASE)
        )

    directory_bases: List[str] = [manifest_url]
    direct_references: List[str] = []

    for reference in base_references:
        clean_reference = str(reference or "").strip()
        if not clean_reference or "$" in clean_reference:
            continue

        absolute = _join_stream_url(manifest_url, clean_reference)
        parsed_path = urllib.parse.urlparse(absolute).path

        if clean_reference.endswith("/") or parsed_path.endswith("/"):
            directory_bases.append(absolute)
        else:
            direct_references.append(absolute)

    result: List[str] = []
    seen: set[str] = set()

    def _add(value: str) -> None:
        if value and value not in seen:
            seen.add(value)
            result.append(value)

    for value in direct_references:
        _add(value)

    for reference in media_references:
        clean_reference = str(reference or "").strip()
        if not clean_reference or "$" in clean_reference:
            continue

        parsed_reference = urllib.parse.urlparse(clean_reference)
        if parsed_reference.scheme or clean_reference.startswith("//"):
            _add(_join_stream_url(manifest_url, clean_reference))
            continue

        for base in directory_bases:
            absolute = _join_stream_url(base, clean_reference)
            if not urllib.parse.urlparse(absolute).path.endswith("/"):
                _add(absolute)

    return result

def _is_probably_binary(body: bytes) -> bool:
    if not body:
        return False

    sample = body[:4096]
    printable = sum(
        1
        for byte in sample
        if byte in {9, 10, 13} or 32 <= byte <= 126
    )
    return printable / max(1, len(sample)) < 0.85


def _has_media_signature(body: bytes) -> bool:
    if not body:
        return False

    prefix = body[:64]

    if len(prefix) >= 12 and b"ftyp" in prefix[:32]:
        return True
    if prefix.startswith(b"\x1a\x45\xdf\xa3"):
        return True
    if prefix.startswith(b"FLV"):
        return True
    if prefix.startswith(b"OggS"):
        return True
    if prefix.startswith(b"ID3"):
        return True
    if prefix.startswith(b"fLaC"):
        return True
    if prefix.startswith(b"RIFF"):
        return True

    if prefix[:1] == b"\x47":
        if len(body) < 189 or body[188:189] == b"\x47":
            return True

    return _is_probably_binary(body)


def _probe_media_url(
    media_url: str,
    headers: Dict[str, str],
    timeout: int,
    verify_ssl: bool,
    sample_bytes: int,
) -> Dict[str, Any]:
    request_url, pipe_headers = _split_pipe_headers(media_url)
    request_headers = _build_request_headers(
        headers,
        pipe_headers,
    )

    result = _fetch_with_retry(
        url=request_url,
        headers=request_headers,
        timeout=timeout,
        verify_ssl=verify_ssl,
        max_bytes=sample_bytes,
        attempts=1,
        retry_delays=[0],
        retry_status_codes=DEFAULT_RETRY_STATUS_CODES,
        range_request=True,
    )

    body = result.get("body", b"")
    content_type = str(
        result.get("content_type") or ""
    ).lower()

    valid_body = (
        result.get("ok") is True
        and bool(body)
        and not _looks_like_html(body, content_type)
    )

    if not valid_body:
        return result

    if (
        content_type.startswith(("video/", "audio/"))
        or "octet-stream" in content_type
        or "mp2t" in content_type
        or "iso.segment" in content_type
        or _has_media_signature(body)
    ):
        result["media_sample_valid"] = True
        return result

    result["ok"] = False
    result["error_kind"] = "invalid_content"
    result["error"] = (
        f"Unexpected media content type: "
        f"{content_type or 'unknown'}"
    )
    return result


def _verify_hls(
    manifest_url: str,
    manifest_result: Dict[str, Any],
    headers: Dict[str, str],
    timeout: int,
    verify_ssl: bool,
    manifest_bytes: int,
    sample_bytes: int,
    probe_segments: bool,
    max_variant_probes: int = 4,
    depth: int = 0,
) -> Dict[str, Any]:
    body = manifest_result.get("body", b"")
    content = _decode_text(
        body,
        str(manifest_result.get("charset") or "utf-8"),
    )

    if "#EXTM3U" not in content.upper():
        result = dict(manifest_result)
        result["ok"] = False
        result["error_kind"] = "invalid_manifest"
        result["error"] = "HLS response does not contain #EXTM3U"
        return result

    detected_height, resolution_label = parse_manifest_resolution(
        content
    )
    variants = _parse_hls_variants(
        content,
        str(manifest_result.get("final_url") or manifest_url),
    )

    if variants:
        ordered_variants: List[Tuple[int, str]] = []
        seen_variant_urls: set[str] = set()
        for height, variant_url in sorted(
            variants,
            key=lambda entry: entry[0],
            reverse=True,
        ):
            if variant_url and variant_url not in seen_variant_urls:
                seen_variant_urls.add(variant_url)
                ordered_variants.append((height, variant_url))

        if depth >= 3:
            result = dict(manifest_result)
            result.update(
                ok=False,
                error_kind="nested_manifest_depth",
                error="HLS nested-manifest depth exceeded",
                detected_height=0,
                resolution_label="",
                manifest_type="hls_master",
                segment_verified=False,
            )
            return result

        last_failure: Dict[str, Any] = {}
        probe_limit = max(1, min(int(max_variant_probes), 10))

        for advertised_height, variant_url in ordered_variants[:probe_limit]:
            variant_request_url, variant_pipe_headers = _split_pipe_headers(
                variant_url
            )
            variant_headers = _build_request_headers(
                headers,
                variant_pipe_headers,
            )
            variant_result = _fetch_with_retry(
                url=variant_request_url,
                headers=variant_headers,
                timeout=timeout,
                verify_ssl=verify_ssl,
                max_bytes=manifest_bytes,
                attempts=2,
                retry_delays=[0, 1],
                retry_status_codes=DEFAULT_RETRY_STATUS_CODES,
                range_request=False,
            )

            if not variant_result.get("ok"):
                last_failure = variant_result
                continue

            nested_result = _verify_hls(
                manifest_url=variant_request_url,
                manifest_result=variant_result,
                headers=variant_headers,
                timeout=timeout,
                verify_ssl=verify_ssl,
                manifest_bytes=manifest_bytes,
                sample_bytes=sample_bytes,
                probe_segments=probe_segments,
                max_variant_probes=max_variant_probes,
                depth=depth + 1,
            )

            if not nested_result.get("ok"):
                last_failure = nested_result
                continue

            nested_height = _safe_int(
                nested_result.get("detected_height"),
                0,
            )
            working_height = max(advertised_height, nested_height)
            nested_result["detected_height"] = working_height
            nested_result["resolution_label"] = _resolution_label(
                working_height
            )
            nested_result["manifest_type"] = "hls_master"
            nested_result["selected_variant_url"] = variant_request_url
            nested_result["advertised_max_height"] = detected_height
            return nested_result

        failed = dict(last_failure or manifest_result)
        failed.update(
            ok=False,
            error_kind=str(
                failed.get("error_kind") or "dead_variants"
            ),
            error=str(
                failed.get("error")
                or "No HLS variant produced a playable media sample"
            ),
            detected_height=0,
            resolution_label="",
            manifest_type="hls_master",
            segment_verified=False,
            advertised_max_height=detected_height,
        )
        return failed

    result = dict(manifest_result)
    result.update(
        detected_height=detected_height,
        resolution_label=resolution_label,
        manifest_type="hls_media",
        segment_verified=False,
    )

    if not probe_segments:
        return result

    media_urls = _extract_hls_media_urls(
        content,
        str(manifest_result.get("final_url") or manifest_url),
    )

    if not media_urls:
        result["ok"] = False
        result["error_kind"] = "empty_playlist"
        result["error"] = "HLS media playlist has no playable segment"
        return result

    last_probe: Dict[str, Any] = {}
    for media_url in media_urls[:2]:
        last_probe = _probe_media_url(
            media_url=media_url,
            headers=headers,
            timeout=min(timeout, 6),
            verify_ssl=verify_ssl,
            sample_bytes=sample_bytes,
        )
        if last_probe.get("ok"):
            result["segment_verified"] = True
            result["segment_url"] = media_url
            return result

    failed = dict(last_probe or result)
    failed["detected_height"] = detected_height
    failed["resolution_label"] = resolution_label
    failed["manifest_type"] = "hls_media"
    failed["segment_verified"] = False
    return failed


def _verify_dash(
    manifest_url: str,
    manifest_result: Dict[str, Any],
    headers: Dict[str, str],
    timeout: int,
    verify_ssl: bool,
    sample_bytes: int,
    probe_segments: bool,
) -> Dict[str, Any]:
    body = manifest_result.get("body", b"")
    content = _decode_text(
        body,
        str(manifest_result.get("charset") or "utf-8"),
    )

    if "<mpd" not in content.lower():
        result = dict(manifest_result)
        result["ok"] = False
        result["error_kind"] = "invalid_manifest"
        result["error"] = "DASH response does not contain an MPD document"
        return result

    detected_height, resolution_label = parse_manifest_resolution(
        content
    )
    result = dict(manifest_result)
    result.update(
        detected_height=detected_height,
        resolution_label=resolution_label,
        manifest_type="dash",
        segment_verified=False,
    )

    if not probe_segments:
        return result

    probe_urls = _extract_dash_probe_urls(
        content,
        str(manifest_result.get("final_url") or manifest_url),
    )

    if not probe_urls:
        result["segment_probe_skipped"] = True
        return result

    last_probe: Dict[str, Any] = {}
    for media_url in probe_urls[:2]:
        last_probe = _probe_media_url(
            media_url=media_url,
            headers=headers,
            timeout=min(timeout, 6),
            verify_ssl=verify_ssl,
            sample_bytes=sample_bytes,
        )
        if last_probe.get("ok"):
            result["segment_verified"] = True
            result["segment_url"] = media_url
            return result

    failed = dict(last_probe or result)
    failed["detected_height"] = detected_height
    failed["resolution_label"] = resolution_label
    failed["manifest_type"] = "dash"
    failed["segment_verified"] = False
    return failed


def _verify_direct_media(
    media_url: str,
    initial_result: Dict[str, Any],
) -> Dict[str, Any]:
    body = initial_result.get("body", b"")
    content_type = str(
        initial_result.get("content_type") or ""
    ).lower()

    result = dict(initial_result)

    if not result.get("ok"):
        return result

    if not body:
        result["ok"] = False
        result["error_kind"] = "empty_response"
        result["error"] = "Direct media response is empty"
        return result

    if _looks_like_html(body, content_type):
        result["ok"] = False
        result["error_kind"] = "html_response"
        result["error"] = "Direct media URL returned HTML"
        return result

    if _has_media_signature(body):
        result["media_sample_valid"] = True
        result["manifest_type"] = "direct_media"
        return result

    result["ok"] = False
    result["error_kind"] = "invalid_content"
    result["error"] = (
        f"Unexpected direct-media response: "
        f"{content_type or 'unknown content type'}"
    )
    return result


# ---------------------------------------------------------------------------
# Verification policy
# ---------------------------------------------------------------------------

def _is_manual_candidate(item: Dict[str, Any]) -> bool:
    source_pipeline = str(
        item.get("source_pipeline") or ""
    ).strip().lower()
    original_pipeline = str(
        item.get("original_source_pipeline") or ""
    ).strip().lower()
    source_id = str(
        item.get("source_id") or ""
    ).strip().lower()

    return (
        source_pipeline == "manual"
        or original_pipeline == "manual"
        or source_id.startswith("manual-")
        or item.get("manual_source") is True
    )


def _protected_bd_tv_candidate(
    item: Dict[str, Any],
    settings: Dict[str, Any],
) -> bool:
    """Return True for live-TV candidates that deserve Bangladesh-safe quality protection."""
    pipeline = str(item.get("source_pipeline") or "tv").strip().casefold()
    if pipeline != "tv":
        return False

    if _is_manual_candidate(item):
        return True

    if _safe_bool(item.get("bd_candidate"), False):
        return True

    protection = settings.get("channel_protection", {})
    if not isinstance(protection, dict):
        protection = {}

    category = str(item.get("category") or "").strip().casefold()
    protected_categories = protection.get("protected_categories", ["Bangla"])
    if isinstance(protected_categories, list):
        protected_category_set = {
            str(value or "").strip().casefold()
            for value in protected_categories
            if str(value or "").strip()
        }
        if category and category in protected_category_set:
            return True

    source_id = str(item.get("source_id") or "").strip().casefold()
    trusted_ids = protection.get("trusted_source_ids", [])
    if isinstance(trusted_ids, list):
        trusted_id_set = {
            str(value or "").strip().casefold()
            for value in trusted_ids
            if str(value or "").strip()
        }
        if source_id and source_id in trusted_id_set:
            return True

    keywords = protection.get(
        "trusted_source_id_keywords",
        ["bdix", "jagobd", "toffee", "ayna", "bangla"],
    )
    if isinstance(keywords, list) and source_id:
        for raw_keyword in keywords:
            keyword = str(raw_keyword or "").strip().casefold()
            if keyword and keyword in source_id:
                return True

    return False


def _resolution_policy(settings: Dict[str, Any]) -> Dict[str, Any]:
    nested = settings.get("resolution", {})
    if not isinstance(nested, dict):
        nested = {}

    tv_minimum_height = _safe_int(
        nested.get(
            "tv_minimum_height",
            settings.get("minimum_live_height", 720),
        ),
        720,
        0,
        4320,
    )
    allow_unknown_tv = _safe_bool(
        nested.get(
            "allow_unknown_tv_resolution",
            settings.get(
                "allow_unknown_live_resolution",
                False,
            ),
        ),
        False,
    )
    manual_override = _safe_bool(
        nested.get(
            "manual_can_override_resolution",
            settings.get(
                "manual_can_override_resolution",
                True,
            ),
        ),
        True,
    )

    event_minimum_height = _safe_int(
        nested.get("event_minimum_height", 0),
        0,
        0,
        4320,
    )
    allow_unknown_event = _safe_bool(
        nested.get(
            "allow_unknown_event_resolution",
            True,
        ),
        True,
    )
    movie_minimum_height = _safe_int(
        nested.get("movie_minimum_height", 720),
        720,
        0,
        4320,
    )
    allow_unknown_movie = _safe_bool(
        nested.get("allow_unknown_movie_resolution", False),
        False,
    )

    preserve_working_bd_below_minimum = _safe_bool(
        nested.get("preserve_working_bd_below_minimum", True),
        True,
    )
    preserve_unknown_working_tv = _safe_bool(
        nested.get("preserve_unknown_working_tv", True),
        True,
    )
    # Live event playlists are the worst offenders for undeclared resolution:
    # a match feed is usually an ABR master or a bare chunk list with no
    # RESOLUTION attribute at all. Quarantining those threw away matches whose
    # stream had already verified as live, which is why Today Match kept
    # publishing an empty tab while the source playlists plainly held fixtures.
    # Mirrors preserve_unknown_working_tv: a dead link still fails earlier in
    # verification, so this only ever rescues a stream that already answered.
    preserve_unknown_working_event = _safe_bool(
        nested.get("preserve_unknown_working_event", True),
        True,
    )

    return {
        "tv_minimum_height": tv_minimum_height,
        "allow_unknown_tv_resolution": allow_unknown_tv,
        "manual_can_override_resolution": manual_override,
        "preserve_working_bd_below_minimum": preserve_working_bd_below_minimum,
        "preserve_unknown_working_tv": preserve_unknown_working_tv,
        "preserve_unknown_working_event": preserve_unknown_working_event,
        "event_minimum_height": event_minimum_height,
        "allow_unknown_event_resolution": allow_unknown_event,
        "movie_minimum_height": movie_minimum_height,
        "allow_unknown_movie_resolution": allow_unknown_movie,
    }


def _below_floor_exception(
    item: Dict[str, Any],
    settings: Dict[str, Any],
) -> Tuple[bool, str]:
    """(allowed, reason) for publishing one channel below the resolution floor.

    Deliberately not a loosening of the floor. The floor stays 720p for every
    channel on the site - all 530 published cards are at or above it - and this
    opens a named, evidence-bound hole for a single case where holding the line
    costs the viewer the channel entirely.

    The case that forced it: Zee Bangla's published 720p route decodes nowhere.
    Six 120 s browser sessions across three profiles produced MEDIA_ERR_DECODE
    every time, because the stream is interlaced H.264 with zero IDR frames.
    The one route that does play is the mobile profile of the same upstream at
    1024x576. A floor that prefers a nominally-HD stream nobody can watch over
    an SD stream that plays is not protecting quality.

    Two conditions, both required, so neither config nor proof alone is enough:

      1. the channel is listed in resolution.below_floor_exceptions, and the
         measured height is at or above the height that entry allows;
      2. route_preference holds a sustained-playback proof for THIS route -
         two independent 120 s browser sessions. Config alone cannot publish an
         unproven low-resolution route, which is what stops this becoming a
         general escape hatch.

    The item keeps its detected SD resolution and badge. Nothing here dresses
    576p up as HD.
    """
    resolution = settings.get("resolution")
    if not isinstance(resolution, dict):
        resolution = {}
    exceptions = resolution.get("below_floor_exceptions")
    if not isinstance(exceptions, list) or not exceptions:
        return False, ""

    name = str(item.get("name") or item.get("title") or "").strip().casefold()
    if not name:
        return False, ""

    detected = _safe_int(
        item.get("resolution_height") or item.get("height") or 0, 0
    )

    for entry in exceptions:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("channel") or "").strip().casefold() != name:
            continue
        floor = _safe_int(entry.get("minimum_height"), 0)
        if floor <= 0 or detected < floor:
            return False, (
                f"{name}: exception allows {floor}p and this route measured "
                f"{detected}p"
            )
        if not _has_sustained_proof(item):
            return False, (
                f"{name}: listed as an exception but this route carries no "
                "sustained-playback proof"
            )
        return True, str(entry.get("reason") or "listed exception with proof")
    return False, ""


def _has_sustained_proof(item: Dict[str, Any]) -> bool:
    """Whether route_preference records a browser proof for this exact route."""
    url = str(item.get("url") or "").strip()
    if not url:
        return False
    try:
        from scanner import route_evidence as _rev
        from scanner import route_preference as _rp

        kind = "channel"
        name = str(item.get("name") or item.get("title") or "")
        wanted = _rp.preferred_route_id(kind, name)
        if not wanted:
            return False
        return _rev.normalize_source_identity(url) == wanted
    except Exception:  # noqa: BLE001 - a lookup failure must not publish
        return False


#: HTTP codes that say something about the asker rather than the resource.
#: 404 and 410 are deliberately absent: those are about the file.
VANTAGE_SHAPED_CODES = {401, 403, 407, 451}
RETRYABLE_CODES = {429, 500, 502, 503, 504}


def _vantage_shaped_movie_status(status_code: int, error_kind: str) -> str:
    """The pending status a movie deserves, or "" to fall through to failed."""
    code = _safe_int(status_code, 0)
    if code in VANTAGE_SHAPED_CODES:
        return "geo_pending"
    if code in RETRYABLE_CODES:
        return "retryable_pending"
    if str(error_kind or "").strip().casefold() == "host_circuit_open":
        return "host_deferred"
    return ""


#: Network-level failures that say the same thing 429 says: the asker did not
#: get through this time. None of them is evidence about the stream.
TRANSIENT_ERROR_KINDS = frozenset({"timeout", "connection", "network", "dns"})

#: How many consecutive scans may hold one route pending on transient evidence
#: before it is allowed to fail. Two, matching
#: bd_verification.permanent_fail_confirmations: long enough to ride out a rate
#: limit or a bad minute, short enough that a genuinely dead route goes within a
#: day at the six-hourly channels cadence.
MAXIMUM_TRANSIENT_RESCUES = 2


def _transient_reason(status_code: Any, error_kind: str) -> str:
    code = _safe_int(status_code, 0)
    if code:
        return "HTTP %d" % code
    kind = str(error_kind or "").strip().casefold()
    return "a %s error" % kind if kind else "a network error"


def _transient_rescue_next(item: Dict[str, Any]) -> int:
    try:
        from scanner import last_published

        return last_published.rescue_count(item.get("url")) + 1
    except Exception:  # noqa: BLE001 - a counter must never fail a scan
        return 1


def _transient_rescue_status(
    item: Dict[str, Any],
    status_code: Any,
    error_kind: str,
) -> str:
    """`retryable_pending` when this run's only evidence is transient, else "".

    Narrow on purpose, and every clause earns its place:

      * Transient answers only - 429 and 5xx, timeouts, connection and DNS
        failures. 403 and 451 are excluded even though they are equally
        vantage-shaped, because holding those would put geo-blocked routes on
        cards; 404 and 410 are about the file and always fail.
      * Only a route the previous published catalogue was already carrying.
        This can never publish a route that has not been verified before, which
        is the objection that kept the movie rule movies-only.
      * At most two consecutive rescues per route, counted on the card itself,
        so a host that answers 429 forever cannot keep a dead channel alive.

    Measured cause, 2026-08-29: BTV News and My TV answered HTTP 429 to the CI
    runner and were deleted from the catalogue; both answered HTTP 200 at 1080p
    from a Bangladeshi residential connection minutes later.
    """
    pipeline = str(item.get("source_pipeline") or "tv").strip().casefold()
    if pipeline == "movies":
        return ""
    code = _safe_int(status_code, 0)
    kind = str(error_kind or "").strip().casefold()
    if code not in RETRYABLE_CODES and kind not in TRANSIENT_ERROR_KINDS:
        return ""
    if code in {404, 410}:
        return ""
    try:
        from scanner import last_published

        if not last_published.was_published(item.get("url")):
            return ""
        if last_published.rescue_count(item.get("url")) >= MAXIMUM_TRANSIENT_RESCUES:
            return ""
    except Exception:  # noqa: BLE001 - a lookup failure must not publish
        return ""
    return "retryable_pending"


def _measure_resolution_from_media(item: Dict[str, Any]) -> int:
    """Height read out of the stream itself, or 0.

    Deliberately bounded and best-effort: a small byte sample, a short timeout,
    and any failure returns 0 so the caller falls back to the existing unknown
    handling. It runs only for streams that would otherwise have no resolution
    at all, so it costs nothing on the ordinary path.
    """
    url = str(item.get("url") or "").strip()
    if not url:
        return 0
    try:
        import urllib.parse
        import urllib.request

        from scanner import media_probe

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
            ),
            "Referer": "https://clicktv.pages.dev/",
        }

        def _get(target: str, limit: int) -> bytes:
            request = urllib.request.Request(target, headers=headers)
            with urllib.request.urlopen(request, timeout=8) as response:
                return response.read(limit)

        body = _get(url, 262144)
        if not body:
            return 0
        head = body[:8192].decode("utf-8", "replace")
        if head.lstrip().startswith("#EXTM3U"):
            declared = media_probe.master_playlist_height(head)
            if declared:
                return declared
            segments = [
                line.strip()
                for line in head.splitlines()
                if line.strip() and not line.startswith("#")
            ]
            if not segments:
                return 0
            body = _get(urllib.parse.urljoin(url, segments[0]), 262144)
            if not body:
                return 0
        decoded = media_probe.sps_from_transport_stream(body)
        if not decoded:
            return 0
        item["resolution_scan_type"] = decoded.get("scan_type")
        item["resolution_evidence"] = "h264 SPS decoded from the stream"
        return media_probe.plausible(decoded.get("height"))
    except Exception:  # noqa: BLE001 - a probe must never fail a scan
        return 0


def _apply_resolution_policy(
    item: Dict[str, Any],
    settings: Dict[str, Any],
    detected_height: int,
) -> Tuple[bool, str, str]:
    pipeline = str(
        item.get("source_pipeline") or "tv"
    ).strip().lower()
    policy = _resolution_policy(settings)
    manual_override_active = (
        _is_manual_candidate(item)
        and policy["manual_can_override_resolution"]
    )

    if pipeline == "tv":
        minimum = policy["tv_minimum_height"]
        allow_unknown = policy[
            "allow_unknown_tv_resolution"
        ]
        protected_bd_tv = _protected_bd_tv_candidate(item, settings)

        if detected_height > 0 and detected_height < minimum:
            exception_allowed, exception_reason = _below_floor_exception(
                item, settings
            )
            if exception_allowed:
                item["quality_below_preferred"] = True
                item["quality_policy_note"] = (
                    f"Below the {minimum}p floor at {detected_height}p by a "
                    f"named per-channel exception with browser proof: "
                    f"{exception_reason}"
                )
                item["resolution_exception"] = True
                return True, "verified_global", ""
            if manual_override_active or (
                protected_bd_tv
                and policy["preserve_working_bd_below_minimum"]
            ):
                item["quality_below_preferred"] = True
                item["quality_policy_note"] = (
                    f"Playable protected TV stream kept at {detected_height}p "
                    f"below preferred {minimum}p"
                )
                return True, "verified_global", ""
            return (
                False,
                "rejected_low_quality",
                (
                    f"Detected TV resolution {detected_height}p "
                    f"is below required {minimum}p"
                ),
            )

        if detected_height == 0:
            if (
                manual_override_active
                or allow_unknown
                or policy["preserve_unknown_working_tv"]
                or protected_bd_tv
            ):
                item["quality_unknown"] = True
                item["quality_policy_note"] = (
                    "Manifest/media verification succeeded; resolution metadata "
                    "was unavailable"
                )
                return True, "verified_global", ""
            return (
                False,
                "quarantine",
                "TV resolution could not be determined",
            )

    if pipeline in {"today_match", "upcoming"}:
        minimum = policy["event_minimum_height"]
        allow_unknown = policy[
            "allow_unknown_event_resolution"
        ]

        if minimum > 0 and detected_height > 0 and detected_height < minimum:
            if manual_override_active:
                return True, "verified_global", ""
            return (
                False,
                "rejected_low_quality",
                (
                    f"Detected event resolution {detected_height}p "
                    f"is below required {minimum}p"
                ),
            )

        if minimum > 0 and detected_height == 0 and not allow_unknown:
            if manual_override_active:
                return True, "verified_global", ""
            if policy["preserve_unknown_working_event"]:
                item["quality_unknown"] = True
                item["quality_policy_note"] = (
                    "Manifest/media verification succeeded; event resolution "
                    "metadata was unavailable"
                )
                return True, "verified_global", ""
            return (
                False,
                "quarantine",
                "Event resolution could not be determined",
            )

    if pipeline == "movies":
        minimum = policy["movie_minimum_height"]
        allow_unknown = policy["allow_unknown_movie_resolution"]

        if minimum > 0 and detected_height > 0 and detected_height < minimum:
            return (
                False,
                "rejected_low_quality",
                (
                    f"Detected movie resolution {detected_height}p "
                    f"is below required {minimum}p"
                ),
            )

        if minimum > 0 and detected_height == 0 and not allow_unknown:
            return (
                False,
                "quarantine",
                "Movie resolution could not be determined",
            )

    return True, "verified_global", ""


# ---------------------------------------------------------------------------
# Public verification functions
# ---------------------------------------------------------------------------

def verify_single_stream(
    candidate: Dict[str, Any],
    settings: Dict[str, Any],
    bd_rules: Dict[str, Any] | Sequence[str],
) -> Dict[str, Any]:
    item = dict(candidate or {})
    raw_url = str(item.get("url") or "").strip()
    pipeline = str(
        item.get("source_pipeline") or "tv"
    ).strip().lower()

    item["verified"] = False
    item["verification_mode"] = "global"
    item["verification_checked_at"] = _utc_now()

    if not raw_url:
        if (
            pipeline == "upcoming"
            and item.get("metadata_only") is True
            and _safe_bool(
                item.get("allow_without_stream"),
                False,
            )
        ):
            item["verification_status"] = "metadata_only"
            item["verification_mode"] = "none"
            item["verification_error"] = ""
            item["response_time_ms"] = 0
            return item

        item["verification_status"] = "failed"
        item["verification_error"] = "Stream URL is empty"
        item["response_time_ms"] = 0
        return item

    if item.get("metadata_only") is True:
        item["verification_status"] = "failed"
        item["verification_error"] = (
            "metadata_only item must not contain a stream URL"
        )
        item["response_time_ms"] = 0
        return item

    request_url, pipe_headers = _split_pipe_headers(raw_url)
    item["url"] = request_url

    if _url_scheme(request_url) not in {"http", "https"}:
        item["verification_status"] = "failed"
        item["verification_error"] = (
            f"Unsupported stream scheme: "
            f"{_url_scheme(request_url) or 'missing'}"
        )
        item["response_time_ms"] = 0
        return item

    playback_headers = _sanitize_headers(
        item.get("headers"),
        pipe_headers,
    )
    item["headers"] = playback_headers
    headers = _build_request_headers(playback_headers)

    network = settings.get("network", {})
    if not isinstance(network, dict):
        network = {}

    verification = settings.get("verification", {})
    if not isinstance(verification, dict):
        verification = {}

    timeout = _safe_int(
        verification.get(
            "timeout_seconds",
            settings.get("stream_timeout_seconds", 8),
        ),
        8,
        1,
        60,
    )
    verify_ssl = _safe_bool(
        network.get("verify_ssl", True),
        True,
    )
    manifest_bytes = _safe_int(
        verification.get(
            "maximum_manifest_bytes",
            512 * 1024,
        ),
        512 * 1024,
        16 * 1024,
        2 * 1024 * 1024,
    )
    sample_bytes = _safe_int(
        verification.get(
            "media_sample_bytes",
            64 * 1024,
        ),
        64 * 1024,
        4096,
        512 * 1024,
    )
    attempts = _safe_int(
        verification.get("retry_attempts", 2),
        2,
        1,
        5,
    )
    retry_delays = verification.get(
        "retry_delays_seconds",
        [0, 1],
    )
    if not isinstance(retry_delays, list):
        retry_delays = [0, 1]

    retry_statuses = verification.get(
        "retry_status_codes",
        sorted(DEFAULT_RETRY_STATUS_CODES),
    )
    if not isinstance(retry_statuses, list):
        retry_statuses = sorted(
            DEFAULT_RETRY_STATUS_CODES
        )

    probe_segments = _safe_bool(
        verification.get("probe_media_segments", True),
        True,
    )
    max_variant_probes = _safe_int(
        verification.get("maximum_hls_variant_probes", 4),
        4,
        1,
        10,
    )

    is_bd_candidate = (
        _safe_bool(item.get("bd_candidate"), False)
        or is_bd_candidate_url(request_url, bd_rules)
    )

    started = time.monotonic()

    extension = _url_extension(request_url)
    initial_range_request = extension in DIRECT_MEDIA_EXTENSIONS
    initial_max_bytes = (
        sample_bytes
        if initial_range_request
        else manifest_bytes
    )

    result = _fetch_with_retry(
        url=request_url,
        headers=headers,
        timeout=timeout,
        verify_ssl=verify_ssl,
        max_bytes=initial_max_bytes,
        attempts=attempts,
        retry_delays=retry_delays,
        retry_status_codes=retry_statuses,
        range_request=initial_range_request,
    )

    if result.get("ok"):
        body = result.get("body", b"")
        content_type = str(
            result.get("content_type") or ""
        ).split(";", 1)[0].strip().lower()
        text_prefix = _decode_text(body[:4096]).lstrip()
        final_url = str(
            result.get("final_url") or request_url
        )

        is_hls = (
            extension == ".m3u8"
            or content_type in HLS_CONTENT_TYPES
            or text_prefix.upper().startswith("#EXTM3U")
        )
        is_dash = (
            extension == ".mpd"
            or content_type in DASH_CONTENT_TYPES
            or "<mpd" in text_prefix.lower()
        )

        if is_hls:
            result = _verify_hls(
                manifest_url=final_url,
                manifest_result=result,
                headers=headers,
                timeout=timeout,
                verify_ssl=verify_ssl,
                manifest_bytes=manifest_bytes,
                sample_bytes=sample_bytes,
                probe_segments=probe_segments,
                max_variant_probes=max_variant_probes,
            )
        elif is_dash:
            result = _verify_dash(
                manifest_url=final_url,
                manifest_result=result,
                headers=headers,
                timeout=timeout,
                verify_ssl=verify_ssl,
                sample_bytes=sample_bytes,
                probe_segments=probe_segments,
            )
        else:
            result = _verify_direct_media(
                media_url=final_url,
                initial_result=result,
            )

    elapsed_ms = max(
        1,
        int((time.monotonic() - started) * 1000),
    )

    item["response_time_ms"] = elapsed_ms
    item["http_status"] = _safe_int(
        result.get("status_code"),
        0,
    )
    item["verification_attempts"] = _safe_int(
        result.get("attempts"),
        attempts,
        0,
    )
    item["final_url"] = str(
        result.get("final_url") or request_url
    )
    item["content_type"] = str(
        result.get("content_type") or ""
    )
    item["manifest_type"] = str(
        result.get("manifest_type") or ""
    )
    item["segment_verified"] = _safe_bool(
        result.get("segment_verified"),
        False,
    )

    detected_height = _safe_int(
        result.get("detected_height"),
        0,
    )

    if detected_height <= 0:
        detected_height = _parse_resolution_height(
            item.get("resolution_height")
            or item.get("height")
            or item.get("resolution")
            # Last, and only when the playlist declared it. A raw-TS route has
            # no manifest to read a resolution from, and the TV floor rejects
            # an unknown resolution outright, so a working fallback was being
            # dropped for saying nothing about itself rather than for being too
            # small. Read only - never inferred from the URL or the name.
            or item.get("resolution_hint")
        )

    if detected_height <= 0:
        # Ask the media. A manifest that declares no RESOLUTION and a raw
        # transport stream both leave the scanner with nothing to read, and the
        # TV floor rejects an unknown resolution outright - so these streams
        # were being published under a blanket "verified but unknown"
        # exemption instead of being measured.
        #
        # Measured across the 120 cards carrying that exemption: 110 could be
        # read. 75 were at or above 720p and had simply never declared it, and
        # 35 were genuinely below the floor. Guessing in either direction was
        # wrong; this reads the H.264 SPS out of the bytes.
        measured = _measure_resolution_from_media(item)
        if measured:
            detected_height = measured

    if detected_height > 0:
        item["resolution_height"] = detected_height
        item["resolution"] = _resolution_label(
            detected_height
        )

    if result.get("ok"):
        policy_ok, status, policy_error = (
            _apply_resolution_policy(
                item,
                settings,
                detected_height,
            )
        )

        if not policy_ok:
            allowed, why = model_permits_hide("verifier.resolution_policy", item)
            if not allowed:
                item["model_blocked_hide"] = why
                policy_ok = True
        item["verified"] = policy_ok
        item["publish_allowed"] = policy_ok
        item["verification_status"] = status
        item["verification_error"] = policy_error

        if policy_ok:
            item["last_check_success"] = True
            item["recent_success"] = True
            if item.get("quality_policy_note"):
                item["verification_note"] = str(item.get("quality_policy_note"))
        return item

    status_code = _safe_int(
        result.get("status_code"),
        0,
    )
    error_kind = str(
        result.get("error_kind") or "network"
    )
    error_message = str(
        result.get("error")
        or "Stream verification failed"
    )

    item["verification_error"] = error_message
    item["last_check_success"] = False
    item["recent_success"] = False

    if (
        is_bd_candidate
        and _should_defer_to_bd(
            status_code,
            error_kind,
            bd_rules if isinstance(bd_rules, dict) else {},
        )
    ):
        item["verification_status"] = "needs_bd_check"
        item["verification_mode"] = "pending_bd"
        item["bd_candidate"] = True
        return item

    # A movie refused from this egress is not a movie that is gone.
    #
    # Measured on the last movie scan: 2,053 candidates answered 403 and 20,081
    # answered 404, and every one of them was recorded as "failed". 404 says the
    # file is not there; 403 from a datacentre says this egress may not have it,
    # which is exactly what a Bangladesh-only or residential-only library
    # answers. Both egresses available here are datacentre (AS8075 Microsoft,
    # AS13335 Cloudflare), so the question cannot be settled from here at all -
    # and calling it "failed" states an answer the scan does not have.
    #
    # Movies only, deliberately. geo_pending is a publishable status for TV, so
    # widening this would put unverified TV routes on cards; the movie publish
    # gate requires a confirmed verification, so a movie in this state is
    # recorded and held back rather than shown.
    if str(item.get("source_pipeline") or "").strip().casefold() == "movies":
        pending = _vantage_shaped_movie_status(status_code, error_kind)
        if pending:
            item["verification_status"] = pending
            item["verification_mode"] = "vantage_inconclusive"
            item["vantage_note"] = (
                f"HTTP {status_code} from a datacentre egress. Not treated as a "
                "dead route: this vantage cannot distinguish a geo-restricted "
                "library from a missing file."
            )
            return item

    rescued = _transient_rescue_status(item, status_code, error_kind)
    if rescued:
        item["verification_status"] = rescued
        item["verification_mode"] = "transient_rescue"
        item["transient_rescue_count"] = _transient_rescue_next(item)
        # Publishable, and explicitly NOT verified. The first version set only
        # the status and the 2026-08-29 channels scan produced thirteen rescued
        # routes and published none of them: `publish_allowed` was left unset,
        # so the merger fell back to `verified`, which is False here by
        # definition. `verified` stays False on purpose - the card keeps the
        # "Temporary" badge and claims nothing this run did not measure.
        item["publish_allowed"] = True
        item["verified"] = False
        item["vantage_note"] = (
            f"{_transient_reason(status_code, error_kind)} from this run's "
            "egress on a route the published catalogue was already carrying. "
            "Held for one more scan instead of being deleted; a second "
            "consecutive transient answer, or any permanent one, fails it."
        )
        return item

    item["verification_status"] = "failed"
    return item


def _verification_time_budget_seconds(
    settings: Dict[str, Any],
    mode: str,
) -> int:
    verification = settings.get("verification", {})
    if not isinstance(verification, dict):
        verification = {}

    raw_budget = verification.get("time_budget_seconds", {})
    mode_clean = str(mode or "all").strip().lower()

    defaults = {
        "channels": 2100,
        "tv": 2100,
        "events": 600,
        "today": 600,
        "today_match": 600,
        "upcoming": 600,
        "movies": 2400,
        "all": 3000,
    }

    if isinstance(raw_budget, dict):
        value = raw_budget.get(mode_clean, defaults.get(mode_clean, 2100))
    else:
        value = raw_budget or defaults.get(mode_clean, 2100)

    return _safe_int(
        value,
        defaults.get(mode_clean, 2100),
        60,
        6 * 60 * 60,
    )


def _budget_exhausted_result(
    candidate: Dict[str, Any],
) -> Dict[str, Any]:
    item = dict(candidate)
    item["verified"] = False
    item["verification_checked_at"] = _utc_now()
    item["response_time_ms"] = 0

    pipeline = str(item.get("source_pipeline") or "").strip().lower()
    if (
        pipeline == "upcoming"
        and item.get("metadata_only") is True
        and item.get("allow_without_stream") is True
        and not str(item.get("url") or "").strip()
    ):
        item.update(
            publish_allowed=True,
            verification_status="metadata_only",
            verification_mode="none",
            verification_error="",
        )
        return item

    if item.get("previously_published") is True:
        item.update(
            publish_allowed=True,
            verification_status="stale_last_good",
            verification_mode="time_budget_fallback",
            verification_error=(
                "Global verification time budget ended before this "
                "previously published stream was rechecked"
            ),
        )
    else:
        allowed, why = model_permits_hide("verifier.time_budget", item)
        if not allowed:
            item["model_blocked_hide"] = why
            return item
        item.update(
            publish_allowed=False,
            verification_status="failed",
            verification_mode="time_budget",
            verification_error=(
                "Global verification time budget ended before this "
                "candidate was checked"
            ),
        )

    return item


def verify_all_candidates(
    candidates_path: str = "working/candidates.json",
    settings_path: str = "config/settings.json",
    output_path: str = "working/global-results.json",
) -> Dict[str, Any]:
    """
    Verify planned candidates in bounded batches.

    The previous implementation queued every raw source item at once.  Large
    public lists could therefore run until the GitHub job timeout.  The planner
    now reduces the set first, and this verifier enforces an internal deadline
    while preserving previously published streams as stale-last-good instead of
    deleting them.
    """
    candidates_file = Path(candidates_path)
    if not candidates_file.exists():
        raise FileNotFoundError(
            f"Candidates file not found: {candidates_path}"
        )

    candidates_data = _load_json_file(candidates_file)
    if "items" not in candidates_data:
        raise ValueError(
            f"Candidates file is invalid or missing 'items': "
            f"{candidates_path}"
        )

    raw_items = candidates_data.get("items", [])
    if not isinstance(raw_items, list):
        raise ValueError(
            "working/candidates.json field 'items' must be a list"
        )

    items = [item for item in raw_items if isinstance(item, dict)]
    settings = _load_json_file(settings_path)
    bd_rules = _extract_bd_rules(settings)

    verification = settings.get("verification", {})
    if not isinstance(verification, dict):
        verification = {}

    max_workers = _safe_int(
        verification.get(
            "workers",
            settings.get("verification_workers", 12),
        ),
        12,
        1,
        32,
    )

    mode = str(candidates_data.get("mode") or "all").strip().lower()
    time_budget_seconds = _verification_time_budget_seconds(settings, mode)
    progress_interval = _safe_int(
        verification.get("progress_interval", 100),
        100,
        1,
        10_000,
    )
    batch_multiplier = _safe_int(
        verification.get("batch_size_multiplier", 3),
        3,
        1,
        10,
    )
    batch_size = max_workers * batch_multiplier

    started = time.monotonic()
    results_by_index: Dict[int, Dict[str, Any]] = {}
    processed = 0
    next_progress = progress_interval
    budget_exhausted = False

    print(
        "   Verification plan: "
        f"{len(items)} candidates, {max_workers} workers, "
        f"budget={time_budget_seconds}s",
        flush=True,
    )

    for batch_start in range(0, len(items), batch_size):
        elapsed = time.monotonic() - started
        if elapsed >= time_budget_seconds:
            budget_exhausted = True
            break

        batch_end = min(len(items), batch_start + batch_size)
        batch = list(enumerate(items[batch_start:batch_end], start=batch_start))

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=max_workers
        ) as executor:
            future_map = {
                executor.submit(
                    verify_single_stream,
                    item,
                    settings,
                    bd_rules,
                ): (index, item)
                for index, item in batch
            }

            for future in concurrent.futures.as_completed(future_map):
                index, original_item = future_map[future]
                try:
                    verified_item = future.result()
                except Exception as error:
                    allowed, why = model_permits_hide(
                        "verifier.worker_exception", original_item
                    )
                    verified_item = dict(original_item)
                    if not allowed:
                        verified_item["model_blocked_hide"] = why
                    else:
                        verified_item.update(
                            verified=False,
                            publish_allowed=False,
                            verification_status="failed",
                            verification_mode="global",
                            verification_checked_at=_utc_now(),
                            verification_error=(
                                f"Unhandled verifier error: {error}"
                            ),
                            response_time_ms=0,
                        )

                results_by_index[index] = verified_item
                processed += 1

                if processed >= next_progress or processed == len(items):
                    elapsed_now = int(time.monotonic() - started)
                    print(
                        "   Verification progress: "
                        f"{processed}/{len(items)} completed "
                        f"({elapsed_now}s elapsed)",
                        flush=True,
                    )
                    while next_progress <= processed:
                        next_progress += progress_interval

        if time.monotonic() - started >= time_budget_seconds:
            budget_exhausted = batch_end < len(items)
            if budget_exhausted:
                break

    skipped = 0
    for index, item in enumerate(items):
        if index in results_by_index:
            continue
        results_by_index[index] = _budget_exhausted_result(item)
        skipped += 1

    results = [results_by_index[index] for index in range(len(items))]

    status_counts: Dict[str, int] = {}
    for item in results:
        status = str(item.get("verification_status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1

    elapsed_seconds = max(0, int(time.monotonic() - started))
    if budget_exhausted or skipped:
        print(
            "   Verification time budget reached: "
            f"processed={processed}, preserved/skipped={skipped}",
            flush=True,
        )

    output_data: Dict[str, Any] = {
        "timestamp": _utc_now(),
        "mode": mode,
        "total_candidates": len(items),
        "total_processed": len(results),
        "total_network_checked": processed,
        "total_budget_skipped": skipped,
        "time_budget_seconds": time_budget_seconds,
        "elapsed_seconds": elapsed_seconds,
        "budget_exhausted": bool(budget_exhausted or skipped),
        "total_verified": sum(
            1 for item in results if item.get("verified") is True
        ),
        "total_metadata_only": status_counts.get("metadata_only", 0),
        "total_needs_bd_check": status_counts.get("needs_bd_check", 0),
        "status_counts": status_counts,
        "results": results,
    }

    _atomic_write_json(output_path, output_data)
    return output_data


if __name__ == "__main__":
    summary = verify_all_candidates()
    print(
        "Verified "
        f"{summary['total_verified']} of "
        f"{summary['total_candidates']} candidates"
    )
