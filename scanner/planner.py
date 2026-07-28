"""
Pre-verification Candidate Planner

The public source lists can contain tens of thousands of repeated or irrelevant
entries.  Verifying every raw entry is too expensive for GitHub Actions.
This module runs after normalization and before network verification.

It performs only safe, deterministic reductions:
- keeps candidates belonging to the requested pipeline/mode;
- drops unknown TV categories before network verification;
- removes exact duplicate URL/header/DRM combinations;
- groups equivalent channel/movie/event entries;
- keeps a small, ranked and source/host-diverse candidate set per item;
- prioritizes URLs already published in data/ so regular scans recheck known
  working links first;
- writes a transparent planning report.

Signed URL query strings are never stripped for identity or playback.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Set, Tuple


VALID_TV_CATEGORIES = {
    "Bangla",
    "Sports",
    "Indian",
    "Cartoon",
    "Islamic",
    "Foreign News",
}

VALID_MOVIE_CATEGORIES = {
    "Dubbed",
    "Bangla",
    "Hindi",
    "South Indian",
    "English",
    "Mix",
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
    maximum: int | None = None,
) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = default

    result = max(minimum, result)
    if maximum is not None:
        result = min(result, maximum)
    return result


def _load_json(path: str | Path) -> Dict[str, Any]:
    file_path = Path(path)
    if not file_path.exists():
        return {}

    try:
        with file_path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return {}


def _atomic_write_json(path: str | Path, data: Any) -> None:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = file_path.with_name(
        f".{file_path.name}.{os.getpid()}.{time.time_ns()}.tmp"
    )

    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, file_path)
    finally:
        if temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass


def _clean_url(value: Any) -> str:
    return str(value or "").split("|", 1)[0].strip()


def _hostname(value: Any) -> str:
    try:
        return (urllib.parse.urlsplit(_clean_url(value)).hostname or "").lower()
    except Exception:
        return ""


def _slug(value: Any) -> str:
    text = str(value or "").casefold()
    text = re.sub(r"[^\w]+", "-", text)
    return text.strip("-")


def _event_key(value: Any) -> str:
    text = str(value or "").casefold()
    text = re.sub(
        r"\b(?:official|live|coverage|match|fancode|tapmad|willow|crichd|"
        r"server\s*\d*|alt|hindi|english|4k|2k|uhd|fhd|hd|sd|"
        r"1080p|720p|480p|360p)\b",
        " ",
        text,
    )
    text = re.sub(r"[^\w]+", " ", text)
    return "-".join(text.split())


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    except (TypeError, ValueError):
        return str(value)


def _exact_stream_key(item: Dict[str, Any]) -> str:
    """
    Exact playback identity.  Query strings are deliberately preserved.
    Different headers/DRM metadata are kept as separate candidates.
    """
    payload = {
        "url": str(item.get("url") or "").strip(),
        "headers": item.get("headers") if isinstance(item.get("headers"), dict) else {},
        "drm": item.get("drm") if isinstance(item.get("drm"), dict) else {},
        "metadata_only": item.get("metadata_only") is True,
        "start_time": item.get("start_time") if item.get("metadata_only") else "",
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _group_key(item: Dict[str, Any]) -> str:
    pipeline = str(item.get("source_pipeline") or "tv").strip().lower()

    if pipeline in {"today_match", "upcoming"}:
        identity = (
            _event_key(item.get("name"))
            or _slug(item.get("id"))
            or _slug(item.get("tvg_id"))
        )
    else:
        identity = (
            _slug(item.get("id"))
            or _slug(item.get("tvg_id"))
            or _slug(item.get("name"))
        )

    if not identity:
        identity = hashlib.sha1(
            _exact_stream_key(item).encode("ascii")
        ).hexdigest()[:16]

    return f"{pipeline}:{identity}"


def _pipeline_for_mode(mode: str) -> Set[str]:
    mode_clean = str(mode or "all").strip().lower()
    active: Set[str] = set()

    if mode_clean in {"all", "channels", "tv"}:
        active.add("tv")
    if mode_clean in {"all", "movies"}:
        active.add("movies")
    if mode_clean in {"all", "events", "today", "today_match"}:
        active.add("today_match")
    if mode_clean in {"all", "events", "upcoming"}:
        active.add("upcoming")

    return active


# ---------------------------------------------------------------------------
# Published URL history
# ---------------------------------------------------------------------------


def _iter_card_urls(card: Dict[str, Any]) -> Iterable[str]:
    primary = _clean_url(card.get("url"))
    if primary:
        yield primary

    backups = card.get("backups")
    if isinstance(backups, list):
        for backup in backups:
            if not isinstance(backup, dict):
                continue
            url = _clean_url(backup.get("url"))
            if url:
                yield url


def _published_urls(data_root: str | Path = "data") -> Set[str]:
    root = Path(data_root)
    urls: Set[str] = set()

    channels_dir = root / "channels"
    if channels_dir.exists():
        for file_path in channels_dir.glob("*.json"):
            payload = _load_json(file_path)
            cards = payload.get("channels")
            if isinstance(cards, list):
                for card in cards:
                    if isinstance(card, dict):
                        urls.update(_iter_card_urls(card))

    for file_name in ("today-match.json", "upcoming.json"):
        payload = _load_json(root / file_name)
        for key in ("items", "matches", "events"):
            cards = payload.get(key)
            if isinstance(cards, list):
                for card in cards:
                    if isinstance(card, dict):
                        urls.update(_iter_card_urls(card))
                break

    movies_dir = root / "movies"
    if movies_dir.exists():
        for file_path in movies_dir.glob("*/page-*.json"):
            payload = _load_json(file_path)
            cards = payload.get("items")
            if isinstance(cards, list):
                for card in cards:
                    if isinstance(card, dict):
                        urls.update(_iter_card_urls(card))

    return urls


# ---------------------------------------------------------------------------
# Ranking and diversity
# ---------------------------------------------------------------------------


def _resolution_height(item: Dict[str, Any]) -> int:
    value = (
        item.get("resolution_height")
        or item.get("height")
        or item.get("resolution")
        or 0
    )

    if isinstance(value, (int, float)):
        return max(0, int(value))

    text = str(value or "").upper()
    match = re.search(r"(?:X|×)\s*(\d{3,4})", text)
    if match:
        return _safe_int(match.group(1), 0)
    match = re.search(r"(\d{3,4})\s*P\b", text)
    if match:
        return _safe_int(match.group(1), 0)
    if "4K" in text:
        return 2160
    if "2K" in text:
        return 1440
    if "FHD" in text or "FULL HD" in text:
        return 1080
    if re.search(r"\bHD\b", text):
        return 720
    return 0


def _candidate_rank(
    item: Dict[str, Any],
    published: Set[str],
) -> Tuple[int, int, int, int, int, int, str]:
    url = _clean_url(item.get("url"))
    pipeline = str(item.get("source_pipeline") or "").lower()
    source_id = str(item.get("source_id") or "").lower()

    previously_published = int(bool(url and url in published))
    manual = int(
        item.get("manual_source") is True
        or pipeline == "manual"
        or source_id.startswith("manual-")
    )
    priority = _safe_int(item.get("source_priority"), 0, -1_000_000, 1_000_000)
    https = int(url.lower().startswith("https://"))
    resolution = _resolution_height(item)
    metadata_only = int(item.get("metadata_only") is True)

    # The final lexical component makes ordering stable across runs.
    stable = f"{source_id}:{url}:{item.get('stream_index', 0)}"
    return (
        previously_published,
        manual,
        priority,
        https,
        resolution,
        metadata_only,
        stable,
    )


def _diverse_take(
    candidates: Sequence[Dict[str, Any]],
    limit: int,
) -> List[Dict[str, Any]]:
    if limit <= 0:
        return []

    selected: List[Dict[str, Any]] = []
    selected_indexes: Set[int] = set()
    seen_sources: Set[str] = set()
    seen_hosts: Set[str] = set()

    # Pass 1: prefer both a new source and a new host.
    for index, item in enumerate(candidates):
        source = str(item.get("source_id") or "").lower()
        host = _hostname(item.get("url"))
        if source in seen_sources or (host and host in seen_hosts):
            continue
        selected.append(item)
        selected_indexes.add(index)
        if source:
            seen_sources.add(source)
        if host:
            seen_hosts.add(host)
        if len(selected) >= limit:
            return selected

    # Pass 2: prefer a new source even when the host repeats.
    for index, item in enumerate(candidates):
        if index in selected_indexes:
            continue
        source = str(item.get("source_id") or "").lower()
        if source and source in seen_sources:
            continue
        selected.append(item)
        selected_indexes.add(index)
        if source:
            seen_sources.add(source)
        host = _hostname(item.get("url"))
        if host:
            seen_hosts.add(host)
        if len(selected) >= limit:
            return selected

    # Pass 3: fill remaining slots by rank.
    for index, item in enumerate(candidates):
        if index in selected_indexes:
            continue
        selected.append(item)
        if len(selected) >= limit:
            break

    return selected


# ---------------------------------------------------------------------------
# Public planner
# ---------------------------------------------------------------------------


def plan_candidates(
    candidates: List[Dict[str, Any]],
    mode: str,
    settings_path: str = "config/settings.json",
    report_path: str = "reports/preverification-plan.json",
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    if not isinstance(candidates, list):
        raise ValueError("Planner input must be a list")

    settings = _load_json(settings_path)
    planning = settings.get("planning")
    if not isinstance(planning, dict):
        planning = {}

    active_pipelines = _pipeline_for_mode(mode)
    if not active_pipelines:
        raise ValueError(f"Unsupported planner mode: {mode}")

    limits = {
        "tv": _safe_int(
            planning.get("tv_candidates_per_channel", 2),
            2,
            1,
            12,
        ),
        "movies": _safe_int(
            planning.get("movie_candidates_per_title", 1),
            1,
            1,
            6,
        ),
        "today_match": _safe_int(
            planning.get("event_candidates_per_event", 3),
            3,
            1,
            10,
        ),
        "upcoming": _safe_int(
            planning.get("event_candidates_per_event", 3),
            3,
            1,
            10,
        ),
    }

    maximum_total_by_mode = planning.get("maximum_total_candidates")
    if not isinstance(maximum_total_by_mode, dict):
        maximum_total_by_mode = {}

    default_total_limits = {
        "channels": 1800,
        "tv": 1800,
        "events": 900,
        "today": 600,
        "today_match": 600,
        "upcoming": 600,
        "movies": 4000,
        "all": 5500,
    }
    mode_clean = str(mode or "all").strip().lower()
    maximum_total = _safe_int(
        maximum_total_by_mode.get(
            mode_clean,
            default_total_limits.get(mode_clean, 3000),
        ),
        default_total_limits.get(mode_clean, 3000),
        1,
        50_000,
    )

    drop_unknown_tv = bool(
        planning.get("drop_unknown_tv_before_verification", True)
    )
    published = _published_urls()

    input_count = len(candidates)
    mode_filtered: List[Dict[str, Any]] = []
    rejected_pipeline = 0
    rejected_unknown_tv = 0
    rejected_invalid_category = 0
    rejected_no_identity = 0
    unknown_samples: List[Dict[str, Any]] = []

    for raw_item in candidates:
        if not isinstance(raw_item, dict):
            continue

        item = dict(raw_item)
        pipeline = str(item.get("source_pipeline") or "tv").strip().lower()

        if pipeline not in active_pipelines:
            rejected_pipeline += 1
            continue

        if pipeline == "tv":
            category = str(item.get("category") or "").strip()
            if drop_unknown_tv and category not in VALID_TV_CATEGORIES:
                rejected_unknown_tv += 1
                if len(unknown_samples) < 100:
                    unknown_samples.append(
                        {
                            "id": item.get("id", ""),
                            "name": item.get("name", ""),
                            "source_id": item.get("source_id", ""),
                            "category": category or "missing",
                            "reason": "Unknown TV category skipped before verification",
                        }
                    )
                continue

        if pipeline == "movies":
            category = str(item.get("category") or "Mix").strip()
            if category not in VALID_MOVIE_CATEGORIES:
                item["category"] = "Mix"

        group = _group_key(item)
        if not group:
            rejected_no_identity += 1
            continue

        item["_verification_group"] = group
        url = _clean_url(item.get("url"))
        item["previously_published"] = bool(url and url in published)
        mode_filtered.append(item)

    # Exact playback dedupe across all normalized candidates.
    unique_map: Dict[str, Dict[str, Any]] = {}
    duplicate_exact = 0
    for item in mode_filtered:
        key = _exact_stream_key(item)
        current = unique_map.get(key)
        if current is None:
            unique_map[key] = item
            continue

        duplicate_exact += 1
        if _candidate_rank(item, published) > _candidate_rank(current, published):
            unique_map[key] = item

    unique_items = list(unique_map.values())

    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for item in unique_items:
        group = str(item.get("_verification_group") or _group_key(item))
        grouped.setdefault(group, []).append(item)

    planned: List[Dict[str, Any]] = []
    group_cap_dropped = 0

    # Rank groups with previously published candidates first.  This is useful
    # when a global cap must be applied.
    ordered_groups = sorted(
        grouped.items(),
        key=lambda pair: (
            -int(any(item.get("previously_published") for item in pair[1])),
            pair[0],
        ),
    )

    for group, group_items in ordered_groups:
        pipeline = str(group_items[0].get("source_pipeline") or "tv").lower()
        limit = limits.get(pipeline, 2)
        ranked = sorted(
            group_items,
            key=lambda item: _candidate_rank(item, published),
            reverse=True,
        )
        selected = _diverse_take(ranked, limit)
        group_cap_dropped += max(0, len(ranked) - len(selected))

        for rank_index, item in enumerate(selected):
            candidate = dict(item)
            candidate["_verification_group"] = group
            candidate["_verification_rank"] = rank_index
            planned.append(candidate)

    # Stable global order: recheck published URLs first, then primary candidate
    # of each group, then secondary candidates.
    planned.sort(
        key=lambda item: (
            -int(item.get("previously_published") is True),
            _safe_int(item.get("_verification_rank"), 0),
            str(item.get("_verification_group") or ""),
            str(item.get("source_id") or ""),
        )
    )

    global_cap_dropped = max(0, len(planned) - maximum_total)
    if len(planned) > maximum_total:
        planned = planned[:maximum_total]

    pipeline_counts: Dict[str, int] = {}
    for item in planned:
        pipeline = str(item.get("source_pipeline") or "unknown")
        pipeline_counts[pipeline] = pipeline_counts.get(pipeline, 0) + 1

    summary: Dict[str, Any] = {
        "timestamp": _utc_now(),
        "mode": mode_clean,
        "active_pipelines": sorted(active_pipelines),
        "input_candidates": input_count,
        "after_mode_and_category_filter": len(mode_filtered),
        "after_exact_deduplication": len(unique_items),
        "planned_candidates": len(planned),
        "unique_groups": len(grouped),
        "previously_published_urls_found": len(published),
        "pipeline_counts": pipeline_counts,
        "limits": {
            "per_pipeline": limits,
            "maximum_total": maximum_total,
        },
        "dropped": {
            "other_pipeline": rejected_pipeline,
            "unknown_tv_category": rejected_unknown_tv,
            "invalid_category": rejected_invalid_category,
            "missing_identity": rejected_no_identity,
            "exact_duplicates": duplicate_exact,
            "per_item_cap": group_cap_dropped,
            "global_cap": global_cap_dropped,
        },
        "unknown_tv_samples": unknown_samples,
    }

    _atomic_write_json(report_path, summary)
    return planned, summary
