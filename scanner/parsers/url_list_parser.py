"""
Plain URL List Parser

Parses plain-text sources containing one stream URL per line or
simple Name + URL pairs.

Supported examples:
- https://example.com/live.m3u8
- Channel Name,https://example.com/live.m3u8
- Channel Name - https://example.com/live.m3u8
- Channel Name: https://example.com/live.m3u8
- Channel Name = https://example.com/live.m3u8
- https://example.com/live.m3u8|User-Agent=abc&Referer=https://site.com/

Features:
- IPTV pipe-style URL headers
- source-level header preservation
- automatic clean name extraction from URL
- generic index/master/playlist filename fallback
- relative media URL resolution
- HTML/error-page rejection
- signed query parameter preservation
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qsl, unquote, urljoin, urlparse


ALLOWED_STREAM_SCHEMES = {
    "http",
    "https",
    "rtmp",
    "rtmps",
    "rtsp",
    "rtsps",
    "udp",
}

ABSOLUTE_URL_RE = re.compile(
    r"(?i)(?:https?|rtmps?|rtsps?|udp)://"
)

RELATIVE_MEDIA_RE = re.compile(
    r"\.(?:m3u8?|mpd|ts|mp4|mkv|webm|avi|flv|mov)"
    r"(?:[?#|].*)?$",
    re.IGNORECASE,
)

GENERIC_FILENAMES = {
    "index",
    "master",
    "playlist",
    "stream",
    "live",
    "channel",
    "video",
    "manifest",
    "default",
    "output",
    "media",
}

HTML_MARKERS = (
    "<!doctype html",
    "<html",
    "<head",
    "<body",
    "<title>404",
    "<title>403",
    "access denied</title>",
    "page not found</title>",
)


def _canonical_header_name(name: str) -> str:
    """Convert header names into consistent capitalization."""
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
    raw_url: str,
) -> Tuple[str, Dict[str, str]]:
    """
    Parse IPTV pipe-style headers.

    Example:
    stream.m3u8|User-Agent=abc&Referer=https://site.com/
    """
    if "|" not in raw_url:
        return raw_url.strip(), {}

    stream_url, header_query = raw_url.split(
        "|",
        1,
    )

    headers: Dict[str, str] = {}

    for key, value in parse_qsl(
        header_query,
        keep_blank_values=True,
    ):
        canonical_name = _canonical_header_name(
            key
        )

        if canonical_name:
            headers[canonical_name] = value

    return stream_url.strip(), headers


def _clean_title(value: str) -> str:
    """Clean a filename or path component into a display title."""
    if not value:
        return ""

    name = unquote(value)

    name = re.sub(
        r"\.(?:m3u8?|mpd|ts|mp4|mkv|webm|avi|flv|mov)$",
        "",
        name,
        flags=re.IGNORECASE,
    )

    name = re.sub(
        r"(?i)\b(?:"
        r"2160p|1440p|1080p|1080|720p|720|576p|480p|360p|"
        r"4k|2k|uhd|fhd|full[\s._-]*hd|hd|sd|"
        r"dvdrip|hdrip|web[\s._-]*dl|webrip|camrip|"
        r"x264|x265|hevc|aac"
        r")\b",
        " ",
        name,
    )

    name = re.sub(
        r"[\[\]\(\)\{\}]",
        " ",
        name,
    )

    name = re.sub(
        r"[-_.]+",
        " ",
        name,
    )

    return " ".join(
        name.split()
    ).strip()


def _extract_title_from_url(url: str) -> str:
    """
    Extract a useful title from a stream URL.

    When the final filename is generic, such as index.m3u8 or
    master.m3u8, the parent directory is used instead.
    """
    if not url:
        return ""

    try:
        parsed = urlparse(url)
        path = unquote(parsed.path or "")

        parts = [
            part
            for part in path.split("/")
            if part
        ]

        if not parts:
            hostname = (
                parsed.hostname
                or ""
            )

            return _clean_title(
                hostname.split(".")[0]
            )

        final_component = parts[-1]

        title = _clean_title(
            final_component
        )

        if (
            not title
            or title.casefold() in GENERIC_FILENAMES
        ):
            for component in reversed(parts[:-1]):
                candidate = _clean_title(
                    component
                )

                if (
                    candidate
                    and candidate.casefold()
                    not in GENERIC_FILENAMES
                ):
                    title = candidate
                    break

        return title

    except (
        TypeError,
        ValueError,
        AttributeError,
    ):
        return ""


def _is_html_error_page(content: str) -> bool:
    """Detect common HTML error and repository web pages."""
    sample = content[:10000].casefold()

    return any(
        marker in sample
        for marker in HTML_MARKERS
    )


def _split_name_and_url(
    line: str,
) -> Tuple[str, str]:
    """
    Split a plain line into optional display name and URL.

    The first absolute URL location is preferred, which safely
    supports commas inside signed URL query parameters.
    """
    absolute_match = ABSOLUTE_URL_RE.search(
        line
    )

    if absolute_match:
        url_start = absolute_match.start()

        if url_start == 0:
            return "", line.strip()

        name_part = line[:url_start].strip()
        url_part = line[url_start:].strip()

        name_part = re.sub(
            r"[\s,\-–—:=|>]+$",
            "",
            name_part,
        ).strip()

        return name_part, url_part

    # Relative media line with an optional name.
    for separator in (
        "\t",
        ",",
        " - ",
        " : ",
        " = ",
    ):
        if separator not in line:
            continue

        name_part, possible_url = line.split(
            separator,
            1,
        )

        possible_url = possible_url.strip()

        if RELATIVE_MEDIA_RE.search(
            possible_url.split("|", 1)[0]
        ):
            return (
                name_part.strip(),
                possible_url,
            )

    return "", line.strip()


def _resolve_and_validate_url(
    raw_url: str,
    source_url: str,
) -> str:
    """
    Resolve relative media URLs and reject invalid text or schemes.

    Signed query parameters remain unchanged.
    """
    if not raw_url:
        return ""

    parsed = urlparse(raw_url)

    if parsed.scheme:
        if parsed.scheme.casefold() not in ALLOWED_STREAM_SCHEMES:
            return ""

        return raw_url

    # Relative input must resemble an actual media/playlist path.
    if not RELATIVE_MEDIA_RE.search(raw_url):
        return ""

    if not source_url:
        return ""

    resolved = urljoin(
        source_url,
        raw_url,
    )

    resolved_parsed = urlparse(
        resolved
    )

    if (
        resolved_parsed.scheme.casefold()
        not in ALLOWED_STREAM_SCHEMES
    ):
        return ""

    return resolved


def parse_url_list_content(
    content: str,
    source_info: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    Parse a plain-text URL list into normalized candidates.
    """
    if not isinstance(content, str):
        return []

    if not content.strip():
        return []

    content = content.lstrip("\ufeff")

    if _is_html_error_page(content):
        return []

    source_info = dict(
        source_info or {}
    )

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
            source_info.get(
                "priority",
                100,
            )
        )
    except (TypeError, ValueError):
        source_priority = 100

    source_pipeline = str(
        source_info.get("pipeline")
        or source_info.get("source_pipeline")
        or source_info.get("_pipeline")
        or "tv"
    )

    configured_headers = source_info.get(
        "headers"
    )

    base_headers: Dict[str, str] = {}

    if isinstance(configured_headers, dict):
        for key, value in configured_headers.items():
            if value is None:
                continue

            if isinstance(value, (dict, list)):
                continue

            base_headers[
                _canonical_header_name(
                    str(key)
                )
            ] = str(value)

    items: List[Dict[str, Any]] = []

    for line_number, raw_line in enumerate(
        content.splitlines(),
        start=1,
    ):
        line = raw_line.strip()

        if not line:
            continue

        if line.startswith(
            (
                "#",
                ";",
                "//",
            )
        ):
            continue

        extracted_name, raw_target = (
            _split_name_and_url(line)
        )

        stream_url, inline_headers = (
            _parse_inline_url_headers(
                raw_target
            )
        )

        stream_url = _resolve_and_validate_url(
            stream_url,
            source_url,
        )

        if not stream_url:
            continue

        headers = dict(
            base_headers
        )

        # URL pipe headers override source-level headers.
        headers.update(
            inline_headers
        )

        display_name = (
            extracted_name.strip()
            or _extract_title_from_url(
                stream_url
            )
            or "Unknown Stream"
        )

        item: Dict[str, Any] = {
            "name": display_name,
            "logo": "",
            "group_title": "",

            # Query parameters and signed tokens remain unchanged.
            "url": stream_url,

            "headers": headers,
            "drm": {},

            "tvg_id": "",
            "tvg_name": "",

            "parser": "url_list",
            "raw_line": raw_line,
            "line_number": line_number,

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
                source_info.get(
                    "status_filter",
                )
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

            "metadata_only": False,
        }

        items.append(item)

    return items
