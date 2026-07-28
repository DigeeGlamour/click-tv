"""
Stream Merger & Deduplication Engine

Merges verified and protected stream candidates into unified cards.

Live TV keeps 1 Primary + up to 5 Backups (6 links total). Movies use a safer,
smaller 1 Primary + up to 3 Backups (4 links total) so the player can fail over
without carrying excessive duplicate URLs. Ranking is status-first:
verified_global/verified_bd -> verified_proxy -> stale_last_good -> geo_pending
-> retryable_pending -> host_deferred. HTTPS is preferred within the same tier.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse


def _load_json_file(file_path: str | Path) -> Dict[str, Any]:
    path = Path(file_path)
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _safe_int(value: Any, default: int, minimum: int = 0) -> int:
    try:
        result = int(value)
        return max(minimum, result)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _response_time_ms(stream: Dict[str, Any]) -> int:
    raw_value = stream.get("response_time_ms")
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        return 999999
    return value if value > 0 else 999999


def _verification_label(stream: Dict[str, Any]) -> str:
    """Preserve the real status instead of inventing verified_global."""
    if stream.get("metadata_only") is True:
        return "metadata_only"

    explicit = str(stream.get("verification_status") or "").strip().lower()
    if explicit:
        return explicit

    if stream.get("verified") is True or stream.get("is_valid") is True:
        return "verified"

    return ""


def _extract_hostname(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def _normalize_movie_title(value: Any) -> str:
    """Build a source-independent movie title key without removing episode data."""
    text = str(value or "").casefold()
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(
        r"\b(?:official|full\s*movie|movie|film|uncut|web[-\s]?dl|webrip|"
        r"hdrip|bluray|brrip|dvdrip|hdtc|camrip|amzn|amazon|netflix|"
        r"dsnp|hotstar|hoichoi|chorki|aha|esub|org|dual\s*audio|dual|"
        r"multi\s*audio|hindi\s*dubbed|bengali\s*dubbed|bangla\s*dubbed|"
        r"4k|2k|uhd|fhd|full\s*hd|hd|sd|2160p|1440p|1080p|720p|"
        r"576p|480p|360p|x264|x265|h\.?264|h\.?265|hevc|aac|"
        r"fibwatch\.?com)\b",
        " ",
        text,
    )
    text = re.sub(r"\.(?:mkv|mp4|m3u8|mov|avi|webm)\b", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _movie_identity_key(item: Dict[str, Any]) -> str:
    """Group the same title/year across different sources into one movie card."""
    for field_name in ("imdb_id", "tmdb_id"):
        value = str(item.get(field_name) or "").strip().casefold()
        if value:
            return f"{field_name}:{value}"

    raw_name = str(item.get("name") or item.get("title") or "").strip()
    explicit_year = str(item.get("year") or "").strip()
    year_match = re.search(r"\b(?:19|20)\d{2}\b", raw_name)
    year = explicit_year or (year_match.group(0) if year_match else "")
    title_without_year = re.sub(r"\b(?:19|20)\d{2}\b", " ", raw_name)
    normalized_title = _normalize_movie_title(title_without_year)

    if normalized_title:
        return f"title:{normalized_title}:{year}"

    fallback_id = str(item.get("id") or "").strip().casefold()
    if fallback_id:
        return f"id:{fallback_id}"

    return (
        f"fallback:{str(item.get('source_id') or '').casefold()}:"
        f"{item.get('stream_index', item.get('source_index', 0))}"
    )


def _verification_badge(stream: Dict[str, Any]) -> str:
    status = str(stream.get("verification_status") or "").strip().casefold()
    if status in {"verified_global", "verified_proxy", "verified_bd", "verified"}:
        return "Verified"
    if status == "stale_last_good":
        return "Last Good"
    if status in {"geo_pending", "bd_protected_pending"}:
        return "Geo/BD"
    if status == "retryable_pending":
        return "Temporary"
    if status == "host_deferred":
        return "Unconfirmed"
    return ""


def _parse_resolution_height(res_val: Any) -> int:
    if not res_val:
        return 0
    if isinstance(res_val, (int, float)):
        return max(0, int(res_val))

    text = str(res_val).strip().upper()

    m_dim = re.search(r"\d+\s*[X×]\s*(\d+)", text)
    if m_dim:
        return int(m_dim.group(1))

    m_p = re.search(r"(\d+)P", text)
    if m_p:
        return int(m_p.group(1))

    if "4K" in text or "UHD" in text:
        return 2160
    if "2K" in text:
        return 1440
    if "FHD" in text or "FULL HD" in text:
        return 1080
    if "HD" in text:
        return 720
    if "SD" in text:
        return 480

    try:
        return int(text)
    except ValueError:
        return 0


def _is_publishable_stream(stream: Dict[str, Any]) -> bool:
    """
    Publish only genuinely verified streams or explicitly protected BD streams.
    A status label by itself is never enough to publish a confirmed tier.
    """
    pipeline = str(stream.get("source_pipeline") or "").lower()

    if stream.get("metadata_only", False):
        return (
            pipeline == "upcoming"
            and bool(stream.get("allow_without_stream", False))
            and not str(stream.get("url") or "").strip()
        )

    status = str(stream.get("verification_status") or "").strip().lower()
    confirmed = (
        stream.get("verified") is True
        or stream.get("is_valid") is True
    )
    publish_allowed = stream.get("publish_allowed") is True

    if status in {
        "failed",
        "failed_bd",
        "rejected_low_quality",
        "quarantine",
    }:
        return False

    if confirmed:
        return True

    return (
        publish_allowed
        and status in {
            "stale_last_good",
            "bd_protected_pending",
            "geo_pending",
            "retryable_pending",
            "host_deferred",
        }
    )


def _is_strongly_verified_today_match(stream: Dict[str, Any]) -> bool:
    """Require a real verified flag before Today Match suppresses Upcoming."""
    if str(stream.get("source_pipeline") or "").lower() != "today_match":
        return False
    if not stream.get("url") or stream.get("metadata_only"):
        return False

    confirmed = (
        stream.get("verified") is True
        or stream.get("is_valid") is True
    )
    if not confirmed:
        return False

    status = str(stream.get("verification_status") or "").strip().lower()
    return status in {
        "",
        "verified_global",
        "verified_proxy",
        "verified_bd",
        "verified",
    }


def normalize_event_key(name: str) -> str:
    text = name.lower()

    text = re.sub(
        r"(?i)\b(?:official\s+live|live\s+coverage|live\s+match|live\s+now|"
        r"today\s+match|upcoming|scheduled|fixture|not\s+started|live)\b",
        " ",
        text,
    )

    text = re.sub(
        r"(?i)\b(?:fancode|tapmad|willow|crichd|server\s*\d*|alt|hindi|english|bd|pk)\b",
        " ",
        text,
    )

    text = re.sub(
        r"(?i)\b(?:4k|2k|uhd|fhd|full\s*hd|hd|sd|1080p|720p|480p|360p)\b",
        " ",
        text,
    )

    text = re.sub(r"[^\w\s]", " ", text)
    return " ".join(text.split()).strip().replace(" ", "-")


def _is_t_sports(channel: Dict[str, Any]) -> bool:
    name = str(channel.get("name", "")).lower()
    name_clean = re.sub(
        r"\b(?:live|official|4k|2k|uhd|fhd|full\s*hd|hd|sd|1080p|720p)\b",
        " ",
        name,
    )
    name_clean = re.sub(r"[^\w\s]", " ", name_clean)
    normalized = " ".join(name_clean.split()).strip()
    return normalized in {"t sports", "tsports"}


def pin_t_sports_first(channels: List[Dict[str, Any]], category: str = "Sports") -> List[Dict[str, Any]]:
    if category != "Sports" or not channels:
        return channels

    tsports_idx = -1
    for idx, item in enumerate(channels):
        if _is_t_sports(item):
            tsports_idx = idx
            break

    if tsports_idx > 0:
        tsports_card = channels.pop(tsports_idx)
        channels.insert(0, tsports_card)

    return channels


def _normalize_priority_name(value: Any) -> str:
    text = str(value or "").casefold()
    text = re.sub(
        r"\b(?:official|live|channel|4k|2k|uhd|fhd|full\s*hd|fullhd|"
        r"hd|sd|2160p|1440p|1080p|720p|576p|480p|360p)\b",
        " ",
        text,
    )
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _configured_priority_index(
    card: Dict[str, Any],
    priority_entries: List[Dict[str, Any]],
) -> int:
    normalized_name = _normalize_priority_name(card.get("name"))

    for index, entry in enumerate(priority_entries):
        if not isinstance(entry, dict):
            continue
        aliases: List[str] = []
        canonical = str(entry.get("canonical_name") or "").strip()
        if canonical:
            aliases.append(canonical)
        raw_aliases = entry.get("aliases")
        if isinstance(raw_aliases, list):
            aliases.extend(str(alias) for alias in raw_aliases)

        normalized_aliases = {
            _normalize_priority_name(alias)
            for alias in aliases
            if _normalize_priority_name(alias)
        }
        if normalized_name in normalized_aliases:
            return index

    return len(priority_entries)


def pin_configured_channels_first(
    cards: List[Dict[str, Any]],
    category: str,
    pinned_config: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Pin configured channels inside one category without fake cards."""
    raw_entries = pinned_config.get(category)
    if not isinstance(raw_entries, list) or not raw_entries or not cards:
        return cards

    indexed_cards = list(enumerate(cards))
    indexed_cards.sort(
        key=lambda pair: (
            _configured_priority_index(pair[1], raw_entries),
            pair[0],
        )
    )
    return [card for _, card in indexed_cards]


def _verification_tier_score(stream: Dict[str, Any]) -> int:
    """Return a strict confidence tier; higher is better."""
    status = str(stream.get("verification_status") or "").strip().lower()
    confirmed = (
        stream.get("verified") is True
        or stream.get("is_valid") is True
    )
    publish_allowed = stream.get("publish_allowed") is True

    if status in {
        "failed",
        "failed_bd",
        "rejected_low_quality",
        "quarantine",
    }:
        return 0

    if status in {"verified_global", "verified_bd", "verified"}:
        return 6 if confirmed else 0

    if status == "verified_proxy":
        return 5 if confirmed else 0

    if confirmed and not status:
        return 6

    if status == "stale_last_good" and publish_allowed:
        return 4

    if status in {"geo_pending", "bd_protected_pending"} and publish_allowed:
        return 3

    if status == "retryable_pending" and publish_allowed:
        return 2

    if status == "host_deferred" and publish_allowed:
        return 1

    return 0


def _stream_quality_score(
    stream: Dict[str, Any],
) -> Tuple[int, int, int, int, int, int, int, int]:
    """
    Ranking score, higher is better:
    1. Verification Tier Score (Global > Proxy > Last-Good > Protected Pending)
    2. Manual-source flag
    3. Source priority
    4. Resolution height
    5. Lower response time
    6. Recent success
    7. Stability score
    8. Preserved metadata
    """
    tier_score = _verification_tier_score(stream)

    source_id = str(stream.get("source_id") or "").lower()
    source_pipeline = str(stream.get("source_pipeline") or "").lower()

    is_manual = 1 if (
        source_pipeline == "manual"
        or source_id.startswith("manual-")
        or stream.get("manual_source") is True
    ) else 0

    priority = _safe_int(stream.get("source_priority", 0), 0)

    res_val = (
        stream.get("resolution_height")
        or stream.get("height")
        or stream.get("resolution")
        or 0
    )
    resolution_height = _parse_resolution_height(res_val)

    response_time = _response_time_ms(stream)

    recent_success = 1 if (
        stream.get("recent_success") is True
        or stream.get("last_check_success") is True
    ) else 0

    stability_raw = (
        stream.get("stability_score")
        if stream.get("stability_score") is not None
        else stream.get("success_rate", 0)
    )
    stability_score = int(max(0.0, _safe_float(stability_raw, 0.0)) * 1000)

    has_request_metadata = 1 if (stream.get("drm") or stream.get("headers")) else 0

    return (
        tier_score,
        is_manual,
        priority,
        resolution_height,
        -response_time,
        recent_success,
        stability_score,
        has_request_metadata,
    )


def _effective_publish_allowed(stream: Dict[str, Any]) -> bool:
    if stream.get("publish_allowed") is not None:
        return stream.get("publish_allowed") is True
    if stream.get("metadata_only") is True:
        return True
    return (
        stream.get("verified") is True
        or stream.get("is_valid") is True
    )


def _apply_host_diversity(streams: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    if not streams or limit <= 0:
        return []

    selected: List[Dict[str, Any]] = []
    seen_hosts: set[str] = set()
    remaining: List[Dict[str, Any]] = []

    for s in streams:
        host = _extract_hostname(s.get("url", ""))
        if host and host not in seen_hosts:
            seen_hosts.add(host)
            selected.append(s)
        else:
            remaining.append(s)

    combined = selected + remaining
    return combined[:limit]


def rank_and_select_streams(
    streams: List[Dict[str, Any]],
    max_total: int = 6,
    max_backups: int = 5,
    prefer_https: bool = True,
    allow_http_fallback: bool = True,
    prefer_different_hosts: bool = True,
) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    if not streams:
        return None, []

    max_total = min(_safe_int(max_total, 6, 1), 6)
    max_backups = min(_safe_int(max_backups, 5, 0), 5)

    publishable_candidates = [s for s in streams if _is_publishable_stream(s)]
    if not publishable_candidates:
        return None, []

    playable_candidates = [
        s for s in publishable_candidates if s.get("url") and not s.get("metadata_only")
    ]
    metadata_candidates = [
        s for s in publishable_candidates if s.get("metadata_only") or not s.get("url")
    ]

    if not playable_candidates:
        if metadata_candidates:
            return metadata_candidates[0], []
        return None, []

    # Deduplicate exact URLs keeping highest-scoring candidate
    url_map: Dict[str, Dict[str, Any]] = {}
    for s in playable_candidates:
        url = str(s.get("url", "")).strip()
        if not url:
            continue
        if url not in url_map:
            url_map[url] = s
        else:
            if _stream_quality_score(s) > _stream_quality_score(url_map[url]):
                url_map[url] = s

    unique_streams = list(url_map.values())
    if not unique_streams:
        return None, []

    # Enforce confidence tier first, then HTTPS preference within each tier.
    protocol_candidates: List[Dict[str, Any]] = []
    for stream in unique_streams:
        url_lower = str(stream.get("url", "")).lower()
        if url_lower.startswith("https://"):
            protocol_candidates.append(stream)
        elif url_lower.startswith("http://") and allow_http_fallback:
            protocol_candidates.append(stream)

    selected_streams: List[Dict[str, Any]] = []

    for tier in (6, 5, 4, 3, 2, 1):
        tier_streams = [
            stream
            for stream in protocol_candidates
            if _verification_tier_score(stream) == tier
        ]

        def _within_tier_score(stream: Dict[str, Any]) -> Tuple[int, ...]:
            quality = _stream_quality_score(stream)
            is_https = int(
                str(stream.get("url") or "").lower().startswith("https://")
            )
            protocol_score = is_https if prefer_https else 0
            return (protocol_score, *quality[1:])

        tier_streams.sort(key=_within_tier_score, reverse=True)

        remaining_slots = max_total - len(selected_streams)
        if remaining_slots <= 0:
            break

        if prefer_different_hosts:
            tier_selected = _apply_host_diversity(
                tier_streams,
                remaining_slots,
            )
        else:
            tier_selected = tier_streams[:remaining_slots]

        selected_streams.extend(tier_selected)

    if not selected_streams:
        return None, []

    primary = selected_streams[0]
    backup_candidates = selected_streams[1 : max_backups + 1]

    backups: List[Dict[str, Any]] = []
    for index, b_stream in enumerate(backup_candidates, start=1):
        backup_item = {
            "name": f"Backup-{index}",
            "url": b_stream.get("url", ""),
            "headers": b_stream.get("headers", {}),
            "verification_mode": b_stream.get("verification_mode", "local"),
            "verification_status": _verification_label(b_stream),
            "verification_badge": _verification_badge(b_stream),
            "verified": bool(b_stream.get("verified", False)),
            "publish_allowed": _effective_publish_allowed(b_stream),
            "source_id": str(b_stream.get("source_id") or ""),
            "host": _extract_hostname(str(b_stream.get("url") or "")),
        }
        if b_stream.get("drm"):
            backup_item["drm"] = b_stream["drm"]
        if b_stream.get("resolution"):
            backup_item["resolution"] = b_stream["resolution"]

        backups.append(backup_item)

    return primary, backups


def merge_candidates(
    candidates: List[Dict[str, Any]],
    settings_path: str = "config/settings.json",
) -> List[Dict[str, Any]]:
    settings = _load_json_file(settings_path)
    link_policy = settings.get("link_policy", {})
    if not isinstance(link_policy, dict):
        link_policy = {}

    if not isinstance(candidates, list):
        return []

    max_total = _safe_int(link_policy.get("maximum_total_links", 6), 6, 1)
    max_backups = _safe_int(link_policy.get("maximum_backups", 5), 5, 0)
    movie_max_total = _safe_int(
        link_policy.get("movie_maximum_total_links", 4), 4, 1
    )
    movie_max_backups = _safe_int(
        link_policy.get("movie_maximum_backups", 3), 3, 0
    )
    movie_max_total = min(movie_max_total, 4)
    movie_max_backups = min(movie_max_backups, 3, movie_max_total - 1)
    prefer_https = bool(link_policy.get("prefer_https", True))
    allow_http_fallback = bool(link_policy.get("allow_http_fallback", True))
    prefer_different_hosts = bool(link_policy.get("prefer_different_hosts", True))

    # 1. Check Today Match events with STRONGLY VERIFIED playable streams
    strongly_verified_today_event_keys: set[str] = set()
    for c in candidates:
        if not isinstance(c, dict):
            continue
        if _is_strongly_verified_today_match(c):
            key = normalize_event_key(c.get("name", ""))
            if key:
                strongly_verified_today_event_keys.add(key)

    # 2. Filter out Upcoming duplicates ONLY IF Today Match has a STRONGLY VERIFIED stream
    filtered_candidates: List[Dict[str, Any]] = []
    for c in candidates:
        if not isinstance(c, dict):
            continue

        if c.get("source_pipeline") == "upcoming":
            key = normalize_event_key(c.get("name", ""))
            if key in strongly_verified_today_event_keys:
                continue
        filtered_candidates.append(c)

    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for c in filtered_candidates:
        pipeline = c.get("source_pipeline", "tv")
        raw_id = str(c.get("id") or "").strip()

        if pipeline in ("today_match", "upcoming"):
            evt_key = normalize_event_key(c.get("name", ""))
            fallback_key = (
                raw_id
                or str(c.get("tvg_id") or "").strip()
                or f"{c.get('source_id', 'unknown')}:{c.get('stream_index', 0)}"
            )
            group_key = f"{pipeline}:{evt_key or fallback_key}"
        elif str(pipeline).strip().lower() == "movies":
            group_key = f"movies:{_movie_identity_key(c)}"
        else:
            fallback_name = re.sub(
                r"[^\w\s-]",
                "",
                str(c.get("name", "")).lower(),
            )
            fallback_name = re.sub(r"[-\s]+", "-", fallback_name).strip("-")
            if (
                str(c.get("category") or "").strip().lower() == "sports"
                and _is_t_sports(c)
            ):
                card_id = "t-sports"
            else:
                card_id = (
                    raw_id
                    or fallback_name
                    or f"{c.get('source_id', 'unknown')}:{c.get('stream_index', 0)}"
                )
            group_key = f"{pipeline}:{card_id}"

        if group_key not in grouped:
            grouped[group_key] = []
        grouped[group_key].append(c)

    merged_results: List[Dict[str, Any]] = []

    for group_key, stream_candidates in grouped.items():
        if not stream_candidates:
            continue

        publishable_candidates = [
            item
            for item in stream_candidates
            if _is_publishable_stream(item)
        ]

        if not publishable_candidates:
            continue

        base_item = max(
            publishable_candidates,
            key=_stream_quality_score,
        )

        group_pipeline = str(base_item.get("source_pipeline") or "").strip().lower()
        selected_max_total = movie_max_total if group_pipeline == "movies" else max_total
        selected_max_backups = (
            movie_max_backups if group_pipeline == "movies" else max_backups
        )

        primary, backups = rank_and_select_streams(
            stream_candidates,
            max_total=selected_max_total,
            max_backups=selected_max_backups,
            prefer_https=prefer_https,
            allow_http_fallback=allow_http_fallback,
            prefer_different_hosts=prefer_different_hosts,
        )

        if not primary:
            continue

        card_url = str(primary.get("url") or "")
        card_headers = primary.get("headers", {})
        if not isinstance(card_headers, dict):
            card_headers = {}

        is_metadata_only = primary.get("metadata_only") is True
        v_mode = str(
            primary.get("verification_mode")
            or ("none" if is_metadata_only else "local")
        )
        v_status = _verification_label(primary)

        merged_card: Dict[str, Any] = {
            "id": base_item.get("id", ""),
            "name": base_item.get("name", ""),
            "logo": base_item.get("logo", ""),
            "category": base_item.get("category", ""),
            "url": card_url,
            "headers": card_headers,
            "verification_mode": v_mode,
            "verification_status": v_status,
            "verification_badge": _verification_badge(primary),
            "verified": bool(primary.get("verified", False)),
            "publish_allowed": _effective_publish_allowed(primary),
            "source_pipeline": str(base_item.get("source_pipeline") or ""),
            "source_id": str(primary.get("source_id") or base_item.get("source_id") or ""),
            "metadata_only": is_metadata_only,
            "available_link_count": 1 + len(backups),
            "backups": backups,
        }

        if primary and primary.get("drm"):
            merged_card["drm"] = primary["drm"]
        if primary and primary.get("resolution"):
            merged_card["resolution"] = primary["resolution"]
        for field_name in (
            "start_time",
            "end_time",
            "competition",
            "event_url",
            "status",
            "original_status",
        ):
            if base_item.get(field_name) not in (None, ""):
                merged_card[field_name] = base_item[field_name]

        merged_results.append(merged_card)

    pinned_config = settings.get("pinned_channels")
    if not isinstance(pinned_config, dict):
        pinned_config = {}

    # Reorder only inside each category. Other category/card positions stay
    # unchanged, and missing channels do not create empty/fake cards.
    for category_name in ("Sports", "Indian", "Cartoon"):
        category_indices = [
            index
            for index, card in enumerate(merged_results)
            if str(card.get("category") or "").strip() == category_name
        ]
        if not category_indices:
            continue

        category_cards = [merged_results[index] for index in category_indices]
        ordered_cards = pin_configured_channels_first(
            category_cards,
            category_name,
            pinned_config,
        )
        if category_name == "Sports" and not pinned_config.get("Sports"):
            ordered_cards = pin_t_sports_first(ordered_cards, "Sports")

        for card_index, original_position in enumerate(category_indices):
            merged_results[original_position] = ordered_cards[card_index]

    return merged_results
