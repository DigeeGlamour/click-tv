"""
Direct Stream Source Parser

Converts one direct media source into a normalized candidate item.

Supported inputs:
- Direct HLS/DASH/media URLs
- A text body containing exactly one direct URL
- HLS master/media manifest content whose request URL is the stream URL
- DASH MPD content whose request URL is the stream URL
- IPTV pipe-style headers
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

MEDIA_PATH_RE = re.compile(
    r"\.(?:m3u8?|mpd|ts|mp4|mkv|webm|avi|flv|mov)"
    r"(?:[?#].*)?$",
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

HLS_MARKERS = (
    "#EXTM3U",
    "#EXT-X-STREAM-INF",
    "#EXT-X-TARGETDURATION",
    "#EXT-X-MEDIA-SEQUENCE",
)

DASH_MARKERS = (
    "<mpd",
    "urn:mpeg:dash:schema:mpd",
)


def _canonical_header_name(name: str) -> str:
    """Convert common header spellings into consistent names."""
    normalized = name.strip().lower().replace("_", "-")

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
    """Separate IPTV pipe headers from the actual stream URL."""
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
    """Clean a URL path component into a readable display title."""
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
    """Extract a useful title from the URL filename or parent folder."""
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
            hostname = parsed.hostname or ""

            return _clean_title(
                hostname.split(".")[0]
            )

        title = _clean_title(
            parts[-1]
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
                    return candidate

        return title

    except (
        TypeError,
        ValueError,
        AttributeError,
    ):
        return ""


def _is_html_page(content: str) -> bool:
    """Reject obvious HTML and HTTP error pages."""
    sample = content[:10000].casefold()

    return any(
        marker in sample
        for marker in HTML_MARKERS
    )


def _is_hls_manifest(content: str) -> bool:
    """Return True when the downloaded body looks like HLS."""
    sample = content[:20000].upper()

    return any(
        marker in sample
        for marker in HLS_MARKERS
    )


def _is_dash_manifest(content: str) -> bool:
    """Return True when the downloaded body looks like a DASH MPD."""
    sample = content[:20000].casefold()

    return any(
        marker in sample
        for marker in DASH_MARKERS
    )


def _extract_single_content_url(
    content: str,
) -> str:
    """
    Return a URL only when the body contains one usable
    non-comment line.
    """
    lines = [
        line.strip()
        for line in content.lstrip("\ufeff").splitlines()
        if (
            line.strip()
            and not line.lstrip().startswith(
                (
                    "#",
                    ";",
                    "//",
                )
            )
        )
    ]

    if len(lines) != 1:
        return ""

    return lines[0]


def _resolve_and_validate_url(
    raw_url: str,
    base_url: str = "",
) -> str:
    """
    Resolve relative media paths and reject unsupported schemes.
    """
    if not raw_url:
        return ""

    parsed = urlparse(
        raw_url
    )

    if parsed.scheme:
        if (
            parsed.scheme.casefold()
            not in ALLOWED_STREAM_SCHEMES
        ):
            return ""

        return raw_url

    if not MEDIA_PATH_RE.search(
        raw_url
    ):
        return ""

    if not base_url:
        return ""

    resolved = urljoin(
        base_url,
        raw_url,
    )

    resolved_scheme = urlparse(
        resolved
    ).scheme.casefold()

    if (
        resolved_scheme
        not in ALLOWED_STREAM_SCHEMES
    ):
        return ""

    return resolved


def _extract_source_headers(
    source_info: Dict[str, Any],
) -> Dict[str, str]:
    """Read source-level headers while ignoring nested values."""
    headers: Dict[str, str] = {}

    for container_key in (
        "headers",
        "http_headers",
    ):
        raw_headers = source_info.get(
            container_key
        )

        if not isinstance(
            raw_headers,
            dict,
        ):
            continue

        for key, value in raw_headers.items():
            if (
                value is None
                or isinstance(
                    value,
                    (
                        dict,
                        list,
                    ),
                )
            ):
                continue

            headers[
                _canonical_header_name(
                    str(key)
                )
            ] = str(value)

    return headers


def _extract_source_drm(
    source_info: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Read source-level DRM fields without modifying values.
    """
    drm: Dict[str, Any] = {}

    raw_drm = source_info.get(
        "drm"
    )

    if isinstance(
        raw_drm,
        dict,
    ):
        drm.update(
            raw_drm
        )

    license_type = (
        source_info.get("license_type")
        or source_info.get("drm_type")
    )

    license_key = (
        source_info.get("license_key")
        or source_info.get("drm_key")
        or source_info.get("clearkey")
        or source_info.get("clear_key")
    )

    if license_type:
        drm["license_type"] = str(
            license_type
        ).strip()

    if license_key:
        drm["license_key"] = str(
            license_key
        ).strip()

        drm.setdefault(
            "license_type",
            "clearkey",
        )

    return drm


def _detect_stream_format(
    stream_url: str,
    content: str,
) -> str:
    """Identify the broad stream format for later verification."""
    if _is_hls_manifest(
        content
    ):
        return "hls"

    if _is_dash_manifest(
        content
    ):
        return "dash"

    path = urlparse(
        stream_url
    ).path.casefold()

    if path.endswith(
        (
            ".m3u8",
            ".m3u",
        )
    ):
        return "hls"

    if path.endswith(".mpd"):
        return "dash"

    if path.endswith(".ts"):
        return "mpegts"

    if path.endswith(".mp4"):
        return "mp4"

    if path.endswith(".mkv"):
        return "mkv"

    if path.endswith(".webm"):
        return "webm"

    return "direct"


def parse_direct_stream_content(
    content: str,
    source_info: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    Parse one direct source into a normalized candidate.
    """
    source_info = dict(
        source_info or {}
    )

    content = (
        content
        if isinstance(content, str)
        else ""
    )

    content = content.lstrip(
        "\ufeff"
    )

    if content and _is_html_page(
        content
    ):
        return []

    upper_content = content.upper()

    if (
        "#EXTINF:" in upper_content
        and "#EXT-X-" not in upper_content
    ):
        return []

    stripped_content = content.lstrip()

    if stripped_content.startswith(
        (
            "{",
            "[",
        )
    ):
        return []

    source_id = str(
        source_info.get("id")
        or "unknown-source"
    )

    source_name = str(
        source_info.get("name")
        or source_id
    )

    original_source_url = str(
        source_info.get("url")
        or source_info.get("location")
        or ""
    ).strip()

    (
        source_url_without_headers,
        source_inline_headers,
    ) = _parse_inline_url_headers(
        original_source_url
    )

    content_url = ""

    if (
        content
        and not _is_hls_manifest(content)
        and not _is_dash_manifest(content)
    ):
        content_url = _extract_single_content_url(
            content
        )

    raw_stream_url = (
        content_url
        or original_source_url
    )

    if not raw_stream_url:
        return []

    (
        stream_url,
        content_inline_headers,
    ) = _parse_inline_url_headers(
        raw_stream_url
    )

    stream_url = _resolve_and_validate_url(
        stream_url,
        source_url_without_headers,
    )

    if not stream_url:
        return []

    try:
        source_priority = int(
            source_info.get(
                "priority",
                100,
            )
        )
    except (
        TypeError,
        ValueError,
    ):
        source_priority = 100

    source_pipeline = str(
        source_info.get("pipeline")
        or source_info.get("source_pipeline")
        or source_info.get("_pipeline")
        or "tv"
    )

    headers = _extract_source_headers(
        source_info
    )

    headers.update(
        source_inline_headers
    )

    headers.update(
        content_inline_headers
    )

    display_name = (
        str(
            source_info.get("display_name")
            or source_info.get("channel_name")
            or source_info.get("title")
            or source_info.get("name")
            or ""
        ).strip()
        or _extract_title_from_url(
            stream_url
        )
        or "Direct Stream"
    )

    item: Dict[str, Any] = {
        "name": display_name,

        "logo": str(
            source_info.get("logo")
            or ""
        ).strip(),

        "group_title": str(
            source_info.get("group_title")
            or source_info.get("category")
            or ""
        ).strip(),

        "url": stream_url,

        "headers": headers,

        "drm": _extract_source_drm(
            source_info
        ),

        "stream_format": _detect_stream_format(
            stream_url,
            content,
        ),

        "tvg_id": str(
            source_info.get("tvg_id")
            or ""
        ).strip(),

        "tvg_name": str(
            source_info.get("tvg_name")
            or ""
        ).strip(),

        "parser": "direct_stream",

        "source_id": source_id,
        "source_name": source_name,
        "source_url": original_source_url,
        "source_priority": source_priority,
        "source_pipeline": source_pipeline,

        "category_mode": source_info.get(
            "category_mode",
            "detect",
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

    return [item]
