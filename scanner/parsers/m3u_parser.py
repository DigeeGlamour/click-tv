"""
M3U / M3U8 Collection Parser

Parses IPTV-style M3U collections and preserves:
- #EXTINF metadata
- tvg-id, tvg-name, tvg-logo, group-title
- #EXTGRP
- #EXTVLCOPT Referer, User-Agent, Origin and Cookie
- #EXTHTTP headers and Toffee cookies
- #KODIPROP DRM license type and key
- Signed URL query parameters
- Inline URL headers: stream.m3u8|User-Agent=...&Referer=...
- Relative media URLs

Important:
HLS master/media manifests are not expanded here.
They must be handled by direct_stream.py or the verifier.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qsl, unquote, urljoin


ABSOLUTE_URL_RE = re.compile(
    r"^[a-zA-Z][a-zA-Z0-9+.-]*://"
)

RELATIVE_MEDIA_RE = re.compile(
    r"\.(?:m3u8?|mpd|ts|mp4|mkv|webm|avi)(?:[?#|].*)?$",
    re.IGNORECASE,
)

HLS_MANIFEST_MARKERS = (
    "#EXT-X-STREAM-INF",
    "#EXT-X-TARGETDURATION",
    "#EXT-X-MEDIA-SEQUENCE",
)


def _extract_attribute(line: str, key: str) -> str:
    """
    Extract an EXTINF attribute supporting:
    key="value"
    key='value'
    key=value
    """
    pattern = re.compile(
        rf"(?:^|\s){re.escape(key)}\s*=\s*"
        rf"(?:\"([^\"]*)\"|'([^']*)'|([^\s,]+))",
        re.IGNORECASE,
    )

    match = pattern.search(line)

    if not match:
        return ""

    return next(
        (
            value
            for value in match.groups()
            if value is not None
        ),
        "",
    ).strip()


def _extract_display_name(extinf_line: str) -> str:
    """
    Return the display name after the final comma outside quotes.

    Some public playlists contain unescaped commas inside poster URLs (for
    example IMDb image crop coordinates). Taking the first comma produced
    broken titles such as ``0,380,562 jpg...,Movie Name``. The EXTINF display
    name is the trailing field, so the last safe comma is the correct boundary.
    """
    active_quote: Optional[str] = None
    separator_index = -1

    for index, character in enumerate(extinf_line):
        escaped = (
            index > 0
            and extinf_line[index - 1] == "\\"
        )

        if character in ('"', "'") and not escaped:
            if active_quote is None:
                active_quote = character
            elif active_quote == character:
                active_quote = None

        elif character == "," and active_quote is None:
            separator_index = index

    if separator_index >= 0:
        return extinf_line[separator_index + 1:].strip()

    return ""


def _canonical_header_name(name: str) -> str:
    """
    Convert different header spellings into consistent names.
    """
    normalized = (
        name.strip()
        .lower()
        .replace("_", "-")
    )

    mapping = {
        "cookie": "Cookie",
        "authorization": "Authorization",

        "referer": "Referer",
        "referrer": "Referer",
        "http-referer": "Referer",
        "http-referrer": "Referer",

        "origin": "Origin",
        "http-origin": "Origin",

        "user-agent": "User-Agent",
        "http-user-agent": "User-Agent",

        "accept": "Accept",
        "accept-language": "Accept-Language",
    }

    return mapping.get(
        normalized,
        name.strip(),
    )


def _parse_inline_url_headers(
    raw_line: str,
) -> Tuple[str, Dict[str, str]]:
    """
    Parse IPTV pipe-style headers:

    https://example.com/live.m3u8|User-Agent=abc&Referer=https://site.com/
    """
    if "|" not in raw_line:
        return raw_line.strip(), {}

    stream_url, header_query = raw_line.split("|", 1)

    headers: Dict[str, str] = {}

    for key, value in parse_qsl(
        header_query,
        keep_blank_values=True,
    ):
        canonical_key = _canonical_header_name(key)
        headers[canonical_key] = value

    return stream_url.strip(), headers


def _parse_kodi_header_string(raw_value: str) -> Dict[str, str]:
    """Decode Kodi ``inputstream.adaptive.*_headers`` properties.

    Kodi stores headers as a query string and percent-encodes header values.
    These are request headers, not DRM metadata.  Keeping them in ``drm``
    meant the verifier and playback Worker silently fell back to a static
    profile instead of the source's exact Cookie/User-Agent.
    """
    value = str(raw_value or "").strip().lstrip("?")
    if not value:
        return {}

    headers: Dict[str, str] = {}
    for raw_name, raw_header_value in parse_qsl(
        value,
        keep_blank_values=True,
        strict_parsing=False,
    ):
        name = _canonical_header_name(unquote(str(raw_name))).strip()
        header_value = unquote(str(raw_header_value)).strip()
        if not name or not header_value:
            continue
        # The target URL already supplies Host. Hop-by-hop/entity headers
        # cannot safely be replayed by urllib or Cloudflare fetch().
        if name.casefold() in {
            "host", "connection", "content-length", "transfer-encoding",
        }:
            continue
        if "\r" in name or "\n" in name or "\r" in header_value or "\n" in header_value:
            continue
        headers[name] = header_value
    return headers


def _looks_like_stream_line(
    line: str,
    has_metadata: bool,
) -> bool:
    """
    Detect absolute stream URLs and relative media paths.
    """
    if not line or line.startswith("#"):
        return False

    raw_url = line.split("|", 1)[0].strip()

    if ABSOLUTE_URL_RE.match(raw_url):
        return True

    return (
        has_metadata
        and bool(RELATIVE_MEDIA_RE.search(raw_url))
    )


def _is_hls_manifest(content: str) -> bool:
    """
    Prevent an HLS master/media playlist from being treated
    as a collection of channels.
    """
    upper_content = content.upper()

    return any(
        marker in upper_content
        for marker in HLS_MANIFEST_MARKERS
    )


def parse_m3u_content(
    content: str,
    source_info: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    Parse an IPTV-style M3U collection.

    source_info should contain source configuration plus an injected
    pipeline value such as:

    {
        "id": "source-id",
        "pipeline": "tv"
    }

    Valid pipeline values:
    tv, movies, today_match, upcoming, manual
    """
    if not isinstance(content, str):
        return []

    if not content.strip():
        return []

    # Remove UTF-8 BOM without changing URLs or query parameters.
    content = content.lstrip("\ufeff")

    # A direct HLS manifest belongs to direct_stream.py/verifier.py.
    if _is_hls_manifest(content):
        return []

    source_info = dict(source_info or {})

    source_id = str(
        source_info.get("id")
        or "unknown-source"
    )

    source_name = str(
        source_info.get("name")
        or source_id
    )

    source_url = str(
        source_info.get("url")
        or ""
    )

    try:
        source_priority = int(
            source_info.get("priority", 100)
        )
    except (TypeError, ValueError):
        source_priority = 100

    source_pipeline = str(
        source_info.get("pipeline")
        or source_info.get("source_pipeline")
        or source_info.get("_pipeline")
        or "tv"
    )

    configured_headers = source_info.get("headers")

    if not isinstance(configured_headers, dict):
        configured_headers = {}

    items: List[Dict[str, Any]] = []

    def new_item_state() -> Dict[str, Any]:
        """
        Create a clean state for each EXTINF entry.

        Source-level headers are copied first. Headers declared
        inside the M3U entry will override them.
        """
        return {
            "name": "",
            "logo": "",
            "group_title": "",
            "resolution_hint": "",
            "tvg_id": "",
            "tvg_name": "",

            "headers": {
                _canonical_header_name(str(key)): str(value)
                for key, value in configured_headers.items()
            },

            "drm": {},
            "raw_extinf": "",
            "has_metadata": False,
        }

    state = new_item_state()

    for raw_line in content.splitlines():
        line = raw_line.strip()

        if not line:
            continue

        upper_line = line.upper()

        # --------------------------------------------------
        # 1. EXTINF metadata
        # --------------------------------------------------
        if upper_line.startswith("#EXTINF:"):
            # Prevent metadata from a broken previous entry
            # leaking into the next entry.
            state = new_item_state()

            state["has_metadata"] = True
            state["raw_extinf"] = line

            state["tvg_id"] = _extract_attribute(
                line,
                "tvg-id",
            )

            state["tvg_name"] = _extract_attribute(
                line,
                "tvg-name",
            )

            state["logo"] = _extract_attribute(
                line,
                "tvg-logo",
            )

            state["group_title"] = _extract_attribute(
                line,
                "group-title",
            )

            # A playlist may declare the resolution it is serving. Read because
            # the TV floor rejects an unknown resolution outright
            # (allow_unknown_tv_resolution is false), and a raw-TS route has no
            # manifest for the scanner to read one from - so a perfectly good
            # fallback was being dropped for having nothing to say about
            # itself, not for being too small. Only read here; nothing is
            # inferred or guessed.
            resolution_hint = (
                _extract_attribute(line, "tvg-resolution")
                or _extract_attribute(line, "resolution")
            )
            if resolution_hint:
                state["resolution_hint"] = resolution_hint

            display_name = _extract_display_name(line)

            state["name"] = (
                display_name
                or state["tvg_name"]
                or state["tvg_id"]
                or "Unknown Stream"
            )

            continue

        # --------------------------------------------------
        # 2. Alternative group declaration
        # --------------------------------------------------
        if upper_line.startswith("#EXTGRP:"):
            group_value = line.split(":", 1)[1].strip()

            if group_value and not state["group_title"]:
                state["group_title"] = group_value

            continue

        # --------------------------------------------------
        # 3. VLC request headers
        # --------------------------------------------------
        if upper_line.startswith("#EXTVLCOPT:"):
            option_text = line.split(":", 1)[1].strip()

            if "=" in option_text:
                key, value = option_text.split("=", 1)

                key_lower = key.strip().lower()
                value = value.strip()

                supported_options = {
                    "http-referrer": "Referer",
                    "http-referer": "Referer",
                    "http-user-agent": "User-Agent",
                    "http-origin": "Origin",
                    "http-cookie": "Cookie",
                }

                header_name = supported_options.get(key_lower)

                if header_name and value:
                    state["headers"][header_name] = value

            continue

        # --------------------------------------------------
        # 4. EXTHTTP JSON headers
        # --------------------------------------------------
        if upper_line.startswith("#EXTHTTP:"):
            payload = line.split(":", 1)[1].strip()

            try:
                parsed_data = json.loads(payload)

                if isinstance(parsed_data, dict):
                    nested_headers = parsed_data.get("headers")

                    if isinstance(nested_headers, dict):
                        header_values = nested_headers
                    else:
                        header_values = parsed_data

                    for key, value in header_values.items():
                        if value is None:
                            continue

                        if isinstance(value, (dict, list)):
                            continue

                        header_name = _canonical_header_name(
                            str(key)
                        )

                        state["headers"][header_name] = str(value)

            except (json.JSONDecodeError, TypeError):
                # Limited fallback for malformed Cookie entries.
                cookie_match = re.search(
                    r"(?i)[\"']?cookie[\"']?\s*[:=]\s*"
                    r"[\"']([^\"']+)",
                    payload,
                )

                if cookie_match:
                    state["headers"]["Cookie"] = (
                        cookie_match.group(1).strip()
                    )

            continue

        # --------------------------------------------------
        # 5. Kodi DRM properties
        # --------------------------------------------------
        if upper_line.startswith("#KODIPROP:"):
            property_text = line.split(":", 1)[1].strip()

            if "=" in property_text:
                key, value = property_text.split("=", 1)

                key_clean = key.strip()
                key_lower = key_clean.lower()
                value = value.strip()

                if key_lower.endswith("stream_headers") or key_lower.endswith("manifest_headers"):
                    # Source-declared headers override configured/static
                    # profiles. Inline URL headers still have final priority.
                    state["headers"].update(_parse_kodi_header_string(value))

                elif "license_type" in key_lower:
                    state["drm"]["license_type"] = value

                elif "license_key" in key_lower:
                    # Keep the complete original value.
                    # It may contain headers or ClearKey information.
                    state["drm"]["license_key"] = value

                else:
                    state["drm"].setdefault(
                        "properties",
                        {},
                    )[key_clean] = value

            continue

        # --------------------------------------------------
        # 6. Stream or nested playlist URL
        # --------------------------------------------------
        if not _looks_like_stream_line(
            line,
            bool(state["has_metadata"]),
        ):
            continue

        stream_url, inline_headers = (
            _parse_inline_url_headers(line)
        )

        # Resolve relative URLs using the source playlist URL.
        if not ABSOLUTE_URL_RE.match(stream_url):
            if not source_url:
                continue

            stream_url = urljoin(
                source_url,
                stream_url,
            )

        # Inline URL headers have the highest entry-level priority.
        state["headers"].update(inline_headers)

        item_name = str(
            state["name"]
            or "Unknown Stream"
        ).strip()

        item: Dict[str, Any] = {
            "name": item_name,
            "logo": str(
                state["logo"] or ""
            ).strip(),

            "group_title": str(
                state["group_title"] or ""
            ).strip(),
            # Only present when the playlist declared it. An empty string keeps
            # downstream "unknown resolution" handling exactly as before.
            "resolution_hint": str(state.get("resolution_hint") or ""),

            # Signed query parameters remain unchanged.
            "url": stream_url,

            "headers": dict(state["headers"]),
            "drm": dict(state["drm"]),

            "tvg_id": str(
                state["tvg_id"] or ""
            ).strip(),

            "tvg_name": str(
                state["tvg_name"] or ""
            ).strip(),

            "raw_extinf": str(
                state["raw_extinf"] or ""
            ),

            "parser": "m3u",

            "source_id": source_id,
            "source_name": source_name,
            "source_url": source_url,
            "source_priority": source_priority,
            "source_pipeline": source_pipeline,

            "category_mode": source_info.get(
                "category_mode",
                "detect",
            ),
            "manual_can_override_category": source_info.get(
                "manual_can_override_category",
                True,
            ),

            "force_category": source_info.get(
                "force_category",
                "",
            ),

            "force_output": source_info.get(
                "force_output",
                "",
            ),

            "default_category": source_info.get(
                "default_category",
                "",
            ),

            "content_filter": source_info.get(
                "content_filter",
                "",
            ),

            "status_filter": list(
                source_info.get("status_filter")
                or []
            ),

            "bd_candidate": bool(
                source_info.get(
                    "bd_candidate",
                    False,
                )
            ),

            "preserve_source_headers": bool(
                source_info.get(
                    "preserve_source_headers",
                    True,
                )
            ),

            "preserve_drm": bool(
                source_info.get(
                    "preserve_drm",
                    False,
                )
            ),

            "follow_nested_playlists": bool(
                source_info.get(
                    "follow_nested_playlists",
                    False,
                )
            ),

            "maximum_nested_depth": int(
                source_info.get(
                    "maximum_nested_depth",
                    0,
                )
                or 0
            ),

            "allow_without_stream": bool(
                source_info.get(
                    "allow_without_stream",
                    False,
                )
            ),
        }

        items.append(item)

        # Clear all entry-specific metadata.
        state = new_item_state()

    return items
