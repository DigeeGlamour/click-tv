"""
JSON Source Parser

Supports flexible JSON sources such as Sports_data.json and live_sports.json.

Features:
- case-insensitive root and field lookup
- LIVE / UPCOMING status filtering
- multiple stream candidates per event
- streamless Upcoming metadata with allow_without_stream
- source, item and stream-level headers
- Referer, User-Agent, Cookie, Origin and Authorization
- ClearKey / DRM metadata
- competition, event URL, and team flag logos
- relative URLs and IPTV pipe-style URL headers
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import parse_qsl, urljoin, urlparse


ROOT_KEYS = (
    "response",
    "matches",
    "channels",
    "events",
    "data",
    "items",
    "results",
)

NAME_KEYS = (
    "event_name",
    "match_name",
    "channel_name",
    "title",
    "name",
)

LOGO_KEYS = (
    "logo",
    "poster",
    "image",
    "icon",
    "thumbnail",
    "cover_image",
    "coverImage",
    "src",
    "teamAFlag",
    "team_a_flag",
    "teamBFlag",
    "team_b_flag",
)

GROUP_KEYS = (
    "group",
    "group_title",
    "category",
    "category_name",
    "event_category",
    "sport",
    "type",
)

START_KEYS = (
    "bd_time",
    "start_time_bd",
    "start_time",
    "startTime",
    "start",
    "scheduled_at",
    "scheduledAt",
    "datetime",
    "date_time",
    "time",
)

END_KEYS = (
    "end_time",
    "endTime",
    "end",
)

COMPETITION_KEYS = (
    "league_name",
    "eventName",
    "competition",
    "tournament",
    "series",
    "league",
)

EVENT_URL_KEYS = (
    "match_url",
    "event_url",
    "page_url",
    "web_url",
)

ID_KEYS = (
    "tvg_id",
    "event_id",
    "match_id",
    "channel_id",
    "id",
)

DIRECT_STREAM_KEYS = (
    "videoURL",
    "stream_link",
    "url",
    "link",
    "stream_url",
    "direct_stream_url",
    "stream",
    "playback_url",
    "playbackUrl",
    "hls",
    "hls_url",
    "m3u8",
    "mpd",
    "dash",
    "fancode_bd",
)

STREAM_CONTAINER_KEYS = (
    "link_live",
    "streams",
    "sources",
    "links",
    "playback",
)


def _canonical_header_name(name: str) -> str:
    """Convert different header spellings into consistent names."""
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

    return mapping.get(normalized, name.strip())


def _get_ci(
    data: Any,
    *keys: str,
    default: Any = None,
) -> Any:
    """
    Case-insensitive dictionary lookup.
    """
    if not isinstance(data, dict):
        return default

    key_map = {
        str(key).casefold(): key
        for key in data.keys()
    }

    for key in keys:
        real_key = key_map.get(
            str(key).casefold()
        )

        if real_key is not None:
            value = data.get(real_key)

            if value is not None:
                return value

    return default


def _first_text(
    data: Any,
    keys: Iterable[str],
) -> str:
    """Return the first non-empty scalar field as text."""
    for key in keys:
        value = _get_ci(data, key)

        if value is None:
            continue

        if isinstance(value, (dict, list)):
            continue

        text = str(value).strip()

        if text:
            return text

    return ""


def _normalize_status(value: Any) -> str:
    """
    Normalize provider-specific event statuses.
    """
    text = str(value or "").strip().upper()

    mapping = {
        "IN PROGRESS": "LIVE",
        "IN_PROGRESS": "LIVE",
        "STARTED": "LIVE",
        "ONGOING": "LIVE",
        "PLAYING": "LIVE",
        "ACTIVE": "LIVE",
        # AX Sports reports the period a match is currently in rather than a
        # plain "LIVE". Without these an in-play match reads as an unknown
        # status and never reaches Today Match.
        "1H": "LIVE",
        "2H": "LIVE",
        "HT": "LIVE",          # half time - still an in-play broadcast
        "ET": "LIVE",          # extra time
        "BT": "LIVE",          # break before extra time
        "P": "LIVE",           # penalty shootout
        "PEN_LIVE": "LIVE",
        "INT": "LIVE",         # interrupted, expected to resume
        "SUSP": "LIVE",        # suspended, expected to resume
        "LIVE": "LIVE",
        "LIVE_NOW": "LIVE",

        "NOT STARTED": "UPCOMING",
        "NOT_STARTED": "UPCOMING",
        "SCHEDULED": "UPCOMING",
        "FIXTURE": "UPCOMING",
        "NS": "UPCOMING",
        "UPCOMING": "UPCOMING",

        "FINISHED": "COMPLETED",
        "ENDED": "COMPLETED",
        "CLOSED": "COMPLETED",
        "FT": "COMPLETED",     # full time
        "AET": "COMPLETED",    # after extra time
        "PEN": "COMPLETED",    # decided on penalties
        "AWD": "COMPLETED",    # awarded
        "WO": "COMPLETED",     # walkover

        # Neither live nor reliably scheduled. Kept distinct from UPCOMING so a
        # postponed or abandoned fixture never becomes a published card.
        "TBD": "UNSCHEDULED",
        "PST": "UNSCHEDULED",  # postponed
        "CANC": "UNSCHEDULED",
        "CANCELLED": "UNSCHEDULED",
        "ABD": "UNSCHEDULED",  # abandoned
        "DELAYED": "UNSCHEDULED",
    }

    return mapping.get(text, text)


def _extract_root_items(raw_data: Any) -> List[Any]:
    """
    Locate the main item list.
    """
    if isinstance(raw_data, list):
        return raw_data

    if not isinstance(raw_data, dict):
        return []

    for key in ROOT_KEYS:
        value = _get_ci(raw_data, key)

        if isinstance(value, list):
            return value

    for key in ROOT_KEYS:
        wrapper = _get_ci(raw_data, key)

        if not isinstance(wrapper, dict):
            continue

        for nested_key in ROOT_KEYS:
            value = _get_ci(
                wrapper,
                nested_key,
            )

            if isinstance(value, list):
                return value

    return [raw_data]


def _extract_headers(
    *objects: Any,
) -> Dict[str, str]:
    """
    Merge source, item and stream-level headers.
    """
    headers: Dict[str, str] = {}

    for obj in objects:
        if not isinstance(obj, dict):
            continue

        raw_headers = _get_ci(
            obj,
            "headers",
            "http_headers",
        )

        if isinstance(raw_headers, dict):
            for key, value in raw_headers.items():
                if value is None:
                    continue

                if isinstance(value, (dict, list)):
                    continue

                headers[
                    _canonical_header_name(str(key))
                ] = str(value)

        direct_fields = {
            "referer": "Referer",
            "referrer": "Referer",
            "user-agent": "User-Agent",
            "user_agent": "User-Agent",
            "cookie": "Cookie",
            "origin": "Origin",
            "authorization": "Authorization",
        }

        for key, canonical_name in direct_fields.items():
            value = _get_ci(obj, key)

            if value is None:
                continue

            if isinstance(value, (dict, list)):
                continue

            text = str(value).strip()

            if text:
                headers[canonical_name] = text

    return headers


def _extract_drm(
    *objects: Any,
) -> Dict[str, Any]:
    """
    Merge DRM and ClearKey information.
    """
    drm: Dict[str, Any] = {}

    for obj in objects:
        if not isinstance(obj, dict):
            continue

        raw_drm = _get_ci(obj, "drm")

        if isinstance(raw_drm, dict):
            for key, value in raw_drm.items():
                if value is not None:
                    drm[str(key)] = value

        license_type = _first_text(
            obj,
            (
                "license_type",
                "drm_type",
            ),
        )

        license_key = _first_text(
            obj,
            (
                "license_key",
                "drm_key",
                "clearkey",
                "clear_key",
            ),
        )

        if license_type:
            drm["license_type"] = license_type

        if license_key:
            drm["license_key"] = license_key
            drm.setdefault(
                "license_type",
                "clearkey",
            )

    return drm


def _parse_inline_url_headers(
    raw_url: str,
) -> Tuple[str, Dict[str, str]]:
    """
    Parse URL pipe headers.
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
        headers[
            _canonical_header_name(key)
        ] = value

    return stream_url.strip(), headers


def _resolve_url(
    stream_url: str,
    source_url: str,
) -> str:
    """
    Resolve relative URLs without changing signed query parameters.
    """
    parsed = urlparse(stream_url)

    if parsed.scheme:
        return stream_url

    if not source_url:
        return stream_url

    return urljoin(
        source_url,
        stream_url,
    )


def _collect_streams(
    raw_item: Dict[str, Any],
) -> List[Tuple[str, Dict[str, Any]]]:
    """
    Return all stream candidates.
    """
    collected: List[
        Tuple[str, Dict[str, Any]]
    ] = []

    seen: set[str] = set()

    def add(
        value: Any,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not isinstance(value, str):
            return

        raw_url = value.strip()

        if not raw_url:
            return

        if raw_url in seen:
            return

        seen.add(raw_url)

        collected.append(
            (
                raw_url,
                dict(metadata or {}),
            )
        )

    for key in DIRECT_STREAM_KEYS:
        value = _get_ci(raw_item, key)

        if isinstance(value, str):
            add(value)

    for container_key in STREAM_CONTAINER_KEYS:
        container = _get_ci(
            raw_item,
            container_key,
        )

        if isinstance(container, list):
            for entry in container:
                if isinstance(entry, str):
                    add(entry)

                elif isinstance(entry, dict):
                    stream_url = _first_text(
                        entry,
                        DIRECT_STREAM_KEYS,
                    )

                    if stream_url:
                        add(
                            stream_url,
                            entry,
                        )

        elif isinstance(container, dict):
            direct_url = _first_text(
                container,
                DIRECT_STREAM_KEYS,
            )

            if direct_url:
                add(
                    direct_url,
                    container,
                )
                continue

            for provider_name, entry in container.items():
                if isinstance(entry, str):
                    add(
                        entry,
                        {
                            "provider": provider_name
                        },
                    )

                elif isinstance(entry, dict):
                    stream_url = _first_text(
                        entry,
                        DIRECT_STREAM_KEYS,
                    )

                    if stream_url:
                        metadata = dict(entry)

                        metadata.setdefault(
                            "provider",
                            provider_name,
                        )

                        add(
                            stream_url,
                            metadata,
                        )

    # Some event feeds expose provider maps as stream_url_alpha,
    # stream_url_bravo, etc. Treat every such field as a stream container
    # instead of silently producing metadata-only events.
    for raw_key, container in raw_item.items():
        key = str(raw_key).strip().casefold()
        if not key.startswith(("stream_url_", "playback_url_")):
            continue
        if isinstance(container, str):
            add(container, {"provider": str(raw_key)})
        elif isinstance(container, dict):
            for provider_name, entry in container.items():
                if isinstance(entry, str):
                    add(entry, {"provider": str(provider_name), "server_group": str(raw_key)})
                elif isinstance(entry, dict):
                    stream_url = _first_text(entry, DIRECT_STREAM_KEYS)
                    if stream_url:
                        metadata = dict(entry)
                        metadata.setdefault("provider", str(provider_name))
                        metadata.setdefault("server_group", str(raw_key))
                        add(stream_url, metadata)

    return collected


def parse_json_content(
    content: str,
    source_info: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    Parse JSON source content into normalized candidates.
    """
    if not isinstance(content, str):
        return []

    if not content.strip():
        return []

    try:
        raw_data = json.loads(
            content.lstrip("\ufeff")
        )
    except (
        json.JSONDecodeError,
        TypeError,
        ValueError,
    ):
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

    status_filter = {
        _normalize_status(value)
        for value in (
            source_info.get("status_filter")
            or []
        )
        if str(value).strip()
    }

    allow_without_stream = bool(
        source_info.get(
            "allow_without_stream",
            False,
        )
    )

    configured_headers = source_info.get(
        "headers"
    )

    if not isinstance(
        configured_headers,
        dict,
    ):
        configured_headers = {}

    items: List[Dict[str, Any]] = []

    raw_items = _extract_root_items(
        raw_data
    )

    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            continue

        raw_status = _first_text(
            raw_item,
            (
                "status",
                "event_status",
                "match_status",
                "state",
            ),
        )

        status = _normalize_status(
            raw_status
        )

        if status_filter:
            if status not in status_filter:
                continue

        event_info = _get_ci(
            raw_item,
            "eventInfo",
            "event_info",
            default={},
        )

        if not isinstance(event_info, dict):
            event_info = {}

        name = (
            _first_text(
                raw_item,
                NAME_KEYS,
            )
            or _first_text(
                event_info,
                NAME_KEYS,
            )
        )

        logo = (
            _first_text(
                raw_item,
                LOGO_KEYS,
            )
            or _first_text(
                event_info,
                LOGO_KEYS,
            )
        )

        group_title = (
            _first_text(
                raw_item,
                GROUP_KEYS,
            )
            or _first_text(
                event_info,
                GROUP_KEYS,
            )
        )

        start_time = (
            _first_text(
                raw_item,
                START_KEYS,
            )
            or _first_text(
                event_info,
                START_KEYS,
            )
        )

        end_time = (
            _first_text(
                raw_item,
                END_KEYS,
            )
            or _first_text(
                event_info,
                END_KEYS,
            )
        )

        competition = (
            _first_text(
                raw_item,
                COMPETITION_KEYS,
            )
            or _first_text(
                event_info,
                COMPETITION_KEYS,
            )
        )

        event_url = _first_text(
            raw_item,
            EVENT_URL_KEYS,
        )

        tvg_id = _first_text(
            raw_item,
            ID_KEYS,
        )

        tvg_name = _first_text(
            raw_item,
            (
                "tvg_name",
            ),
        )

        base_headers = _extract_headers(
            {"headers": configured_headers},
            raw_item,
        )

        base_drm = _extract_drm(
            raw_item
        )

        streams = _collect_streams(
            raw_item
        )

        if not streams:
            if not allow_without_stream:
                continue

            streams = [
                (
                    "",
                    {},
                )
            ]

        for stream_index, (
            raw_stream_url,
            stream_metadata,
        ) in enumerate(streams):
            stream_url = ""
            inline_headers: Dict[str, str] = {}

            if raw_stream_url:
                stream_url, inline_headers = (
                    _parse_inline_url_headers(
                        raw_stream_url
                    )
                )

                stream_url = _resolve_url(
                    stream_url,
                    source_url,
                )

            headers = dict(
                base_headers
            )

            headers.update(
                _extract_headers(
                    stream_metadata
                )
            )

            headers.update(
                inline_headers
            )

            drm = dict(
                base_drm
            )

            drm.update(
                _extract_drm(
                    stream_metadata
                )
            )

            provider = _first_text(
                stream_metadata,
                (
                    "provider",
                    "channel_name",
                    "server",
                    "language",
                    "quality",
                    "name",
                    "title",
                ),
            )

            item: Dict[str, Any] = {
                "name": (
                    name
                    or "Unknown Event"
                ),

                "logo": logo,
                "group_title": group_title,

                "url": stream_url,
                "headers": headers,
                "drm": drm,

                "status": status,
                "original_status": raw_status,

                "start_time": start_time,
                "end_time": end_time,
                "competition": competition,
                "event_url": event_url,

                "provider": provider,
                "stream_index": stream_index,

                "tvg_id": tvg_id,
                "tvg_name": tvg_name,

                "parser": "json",

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

                "allow_without_stream": (
                    allow_without_stream
                ),

                "metadata_only": (
                    not bool(stream_url)
                ),
            }

            items.append(item)

    return items
