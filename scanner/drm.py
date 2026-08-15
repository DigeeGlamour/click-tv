"""Normalize IPTV DRM metadata without guessing incompatible key systems."""

from __future__ import annotations

import re
from typing import Any, Dict, Mapping
from urllib.parse import parse_qsl


_CLEARKEY_PAIR_RE = re.compile(
    r"^[0-9a-fA-F]{16,64}\s*:\s*[0-9a-fA-F]{16,64}(?:\s*,\s*[0-9a-fA-F]{16,64}\s*:\s*[0-9a-fA-F]{16,64})*$"
)


def normalize_drm_type(value: Any) -> str:
    text = str(value or "").strip().casefold()
    if not text:
        return ""
    if "widevine" in text or text == "com.widevine.alpha":
        return "widevine"
    if "playready" in text or "microsoft" in text:
        return "playready"
    if "fairplay" in text or "apple.fps" in text or "com.apple" in text:
        return "fairplay"
    if "clearkey" in text or "clear_key" in text or "org.w3.clearkey" in text:
        return "clearkey"
    return "unknown"


def _headers_from_query(value: Any) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for name, header_value in parse_qsl(
        str(value or "").strip().lstrip("?"),
        keep_blank_values=True,
    ):
        clean_name = str(name or "").strip()
        clean_value = str(header_value or "").strip()
        if not clean_name or not clean_value:
            continue
        if clean_name.casefold() in {
            "host", "connection", "content-length", "transfer-encoding",
        }:
            continue
        if any(ch in clean_name or ch in clean_value for ch in ("\r", "\n")):
            continue
        result[clean_name] = clean_value
    return result


def normalize_drm(raw_value: Any) -> Dict[str, Any]:
    """Return one explicit DRM schema.

    ``license_key`` is interpreted as a ClearKey only when the declared type
    is ClearKey or the value is an unambiguous hexadecimal KID:key pair. Kodi
    Widevine/PlayReady ``url|headers|template|`` strings become a license URL
    plus exact license headers instead of being fed to ClearKey parsing.
    """
    if not isinstance(raw_value, Mapping):
        return {}

    raw = {str(key): value for key, value in raw_value.items() if value not in (None, "", {}, [])}
    declared = (
        raw.get("type") or raw.get("scheme") or raw.get("license_type")
        or raw.get("drm_type") or raw.get("key_system")
    )
    drm_type = normalize_drm_type(declared)
    license_value = str(raw.get("license_key") or "").strip()

    if not drm_type and license_value and _CLEARKEY_PAIR_RE.fullmatch(license_value):
        drm_type = "clearkey"

    result: Dict[str, Any] = {}
    if drm_type:
        result["type"] = drm_type

    if drm_type == "clearkey":
        clear_value = raw.get("clear_keys") or raw.get("clearkey") or license_value
        if clear_value:
            result["clear_keys"] = clear_value
    else:
        license_url = str(
            raw.get("license_url") or raw.get("license_server")
            or raw.get("server_url") or ""
        ).strip()
        license_headers: Dict[str, str] = {}
        raw_headers = raw.get("license_headers") or raw.get("headers")
        if isinstance(raw_headers, Mapping):
            license_headers.update({
                str(name): str(value)
                for name, value in raw_headers.items()
                if str(name).strip() and str(value).strip()
            })

        request_template = str(raw.get("license_request_template") or "").strip()
        if license_value:
            parts = license_value.split("|")
            if parts and parts[0].strip().casefold().startswith(("http://", "https://")):
                license_url = parts[0].strip()
                if len(parts) > 1:
                    license_headers.update(_headers_from_query(parts[1]))
                if len(parts) > 2 and parts[2].strip():
                    request_template = parts[2].strip()
            elif not drm_type and _CLEARKEY_PAIR_RE.fullmatch(license_value):
                result.update(type="clearkey", clear_keys=license_value)

        if license_url:
            result["license_url"] = license_url
        if license_headers:
            result["license_headers"] = license_headers
        if request_template:
            result["license_request_template"] = request_template

    certificate_url = str(
        raw.get("certificate_url") or raw.get("server_certificate_url")
        or raw.get("fairplay_certificate_url") or ""
    ).strip()
    if certificate_url:
        result["certificate_url"] = certificate_url
    certificate_headers = raw.get("certificate_headers")
    if isinstance(certificate_headers, Mapping) and certificate_headers:
        result["certificate_headers"] = {
            str(name): str(value)
            for name, value in certificate_headers.items()
            if str(name).strip() and str(value).strip()
        }

    # Properties alone are player/plugin hints, not proof of DRM. This removes
    # the old false DRM badge on Toffee stream-header records.
    if not result.get("type") and not result.get("license_url") and not result.get("certificate_url"):
        return {}
    return result
