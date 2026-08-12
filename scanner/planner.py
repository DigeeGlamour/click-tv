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
    "Infotainments",
    "Foreign News",
    "Other",
}

VALID_MOVIE_CATEGORIES = {
    "Dubbed",
    "Bangla",
    "Hindi",
    "South Indian",
    "English",
    "Premium",
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
    text = text.replace("pheonix", "phoenix").replace("spirits", "spirit")
    if "|" in text:
        text = text.split("|", 1)[0].strip()
    match = re.search(r"(?:^|[-|])\s*([^-|]+?)\s+v(?:s|\.)\s+([^|]+)", text)
    if match:
        left = match.group(1)
        right = match.group(2)
        right = re.split(r"\s+-\s+(?!(?:women|men)\b)", right, maxsplit=1)[0]
        gender = "women" if re.search(r"\bwom(?:e|a)n(?:'s|s)?\b", f"{left} {right}") else ""
        left = re.sub(r"\bwom(?:e|a)n(?:'s|s)?\b", " ", left)
        right = re.sub(r"\bwom(?:e|a)n(?:'s|s)?\b", " ", right)
        text = f"{left} vs {right} {gender}"
    text = re.sub(
        r"\b\d{1,2}\s+(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|"
        r"may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|"
        r"nov(?:ember)?|dec(?:ember)?)\s+(?:19|20)\d{2}\b",
        " ",
        text,
    )
    text = re.sub(
        r"\b(?:official|live|coverage|match|fancode|tapmad|willow(?:\s+cricket)?|crichd|"
        r"sony\s*liv|star\s*sports?\s*\d*|server\s*\d*|alt|hindi|english|4k|2k|uhd|fhd|hd|sd|"
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
        # The same playback configuration may intentionally appear in Today
        # and Upcoming. Output pipeline is therefore part of exact identity.
        "pipeline": str(item.get("source_pipeline") or "tv").strip().lower(),
        "url": str(item.get("url") or "").strip(),
        "headers": item.get("headers") if isinstance(item.get("headers"), dict) else {},
        "drm": item.get("drm") if isinstance(item.get("drm"), dict) else {},
        "metadata_only": item.get("metadata_only") is True,
        "start_time": item.get("start_time") if item.get("metadata_only") else "",
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _source_provenance(item: Dict[str, Any]) -> List[Dict[str, str]]:
    """Return every configured source that supplied this playback setup."""
    records: List[Dict[str, str]] = []
    existing = item.get("source_provenance")
    if isinstance(existing, list):
        for raw in existing:
            if not isinstance(raw, dict):
                continue
            source_id = str(raw.get("source_id") or "").strip()
            if source_id:
                records.append(
                    {
                        "source_id": source_id,
                        "source_name": str(raw.get("source_name") or source_id).strip(),
                        "source_url": str(raw.get("source_url") or "").strip(),
                    }
                )

    source_id = str(item.get("source_id") or "").strip()
    if source_id:
        records.append(
            {
                "source_id": source_id,
                "source_name": str(item.get("source_name") or source_id).strip(),
                "source_url": str(item.get("source_url") or "").strip(),
            }
        )

    unique: Dict[str, Dict[str, str]] = {}
    for record in records:
        unique.setdefault(record["source_id"], record)
    return list(unique.values())


def _merge_source_provenance(
    preferred: Dict[str, Any],
    duplicate: Dict[str, Any],
) -> Dict[str, Any]:
    merged = dict(preferred)
    combined = _source_provenance(preferred) + _source_provenance(duplicate)
    unique: Dict[str, Dict[str, str]] = {}
    for record in combined:
        unique.setdefault(record["source_id"], record)
    merged["source_provenance"] = list(unique.values())
    merged["source_ids"] = list(unique)
    return merged


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

    if mode_clean in {"all", "full-audit", "channels", "tv", "channels-discovery"}:
        active.add("tv")
    if mode_clean in {"all", "full-audit", "movies", "movies-discovery"}:
        active.add("movies")
    if mode_clean in {"all", "full-audit", "events", "today", "today_match"}:
        active.add("today_match")
    if mode_clean in {"all", "full-audit", "events", "today", "today_match", "upcoming"}:
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
# Telemetry-guided recheck priority
# ---------------------------------------------------------------------------


def _feedback_priority_keys(
    path: str | Path = "reports/playback-feedback.json",
) -> Set[str]:
    payload = _load_json(path)
    items = payload.get("items")
    if not isinstance(items, list):
        return set()

    keys: Set[str] = set()
    for item in items:
        if not isinstance(item, dict) or item.get("suspected_dead") is not True:
            continue
        item_id = str(item.get("item_id") or "").strip()
        if not item_id:
            continue
        keys.add(_slug(item_id))
        # Frontend UIDs may carry pipeline/index prefixes. Preserve useful parts.
        for part in re.split(r"[:|/]", item_id):
            normalized = _slug(part)
            if normalized and normalized not in {"channel", "movie", "event", "upcoming"}:
                keys.add(normalized)
    return keys


def _is_feedback_priority(
    item: Dict[str, Any],
    priority_keys: Set[str],
) -> bool:
    if not priority_keys:
        return False
    identities = {
        _slug(item.get("id")),
        _slug(item.get("tvg_id")),
        _slug(item.get("name")),
    }
    identities.discard("")
    return bool(identities & priority_keys)


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
    feedback_priority_keys: Set[str],
) -> Tuple[int, int, int, int, int, int, int, str]:
    url = _clean_url(item.get("url"))
    pipeline = str(item.get("source_pipeline") or "").lower()
    source_id = str(item.get("source_id") or "").lower()

    previously_published = int(bool(url and url in published))
    feedback_priority = int(_is_feedback_priority(item, feedback_priority_keys))
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
        feedback_priority,
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
    """
    Build ranked candidate pools for adaptive verification.

    Unlike a fixed per-channel cut, the returned list retains a bounded pool.
    ``scanner.fast_pipeline`` verifies the first wave and expands a group only
    when it still lacks enough publishable links. This keeps correctness while
    avoiding unnecessary backup checks.
    """
    if not isinstance(candidates, list):
        raise ValueError("Planner input must be a list")

    settings = _load_json(settings_path)
    planning = settings.get("planning")
    if not isinstance(planning, dict):
        planning = {}
    exhaustive_verification = bool(
        planning.get("exhaustive_verification", False)
    )

    active_pipelines = _pipeline_for_mode(mode)
    if not active_pipelines:
        raise ValueError(f"Unsupported planner mode: {mode}")

    mode_clean = str(mode or "all").strip().lower()
    discovery_mode = mode_clean in {
        "channels-discovery",
        "movies-discovery",
        "full-audit",
        "all",
    }

    initial_cfg = planning.get("initial_candidates_per_group")
    if not isinstance(initial_cfg, dict):
        initial_cfg = {}
    pool_cfg = planning.get("maximum_candidates_per_group")
    if not isinstance(pool_cfg, dict):
        pool_cfg = {}
    target_cfg = planning.get("target_publishable_per_group")
    if not isinstance(target_cfg, dict):
        target_cfg = {}

    defaults_initial = {
        "tv": 2 if discovery_mode else 1,
        "movies": 1,
        "today_match": 1,
        "upcoming": 1,
    }
    defaults_pool = {
        "tv": 8 if discovery_mode else 6,
        "movies": 3 if discovery_mode else 2,
        "today_match": 4,
        "upcoming": 3,
    }
    defaults_target = {
        "tv": 3 if discovery_mode else 2,
        "movies": 1,
        "today_match": 1,
        "upcoming": 1,
    }

    initial_limits = {
        pipeline: _safe_int(initial_cfg.get(pipeline), default, 1, 8)
        for pipeline, default in defaults_initial.items()
    }
    pool_limits = {
        pipeline: _safe_int(pool_cfg.get(pipeline), default, 1, 50_000)
        for pipeline, default in defaults_pool.items()
    }
    target_limits = {
        pipeline: _safe_int(target_cfg.get(pipeline), default, 1, 6)
        for pipeline, default in defaults_target.items()
    }

    # Pool must never be smaller than the initial wave or success target.
    for pipeline in pool_limits:
        pool_limits[pipeline] = max(
            pool_limits[pipeline],
            initial_limits[pipeline],
            target_limits[pipeline],
        )

    maximum_total_by_mode = planning.get("maximum_total_candidate_pool")
    if not isinstance(maximum_total_by_mode, dict):
        maximum_total_by_mode = planning.get("maximum_total_candidates")
    if not isinstance(maximum_total_by_mode, dict):
        maximum_total_by_mode = {}

    default_total_limits = {
        "channels": 3200,
        "tv": 3200,
        "channels-discovery": 5000,
        "events": 1200,
        "today": 800,
        "today_match": 800,
        "upcoming": 800,
        "movies": 5000,
        "movies-discovery": 7000,
        "all": 9000,
        "full-audit": 12000,
    }
    configured_total = maximum_total_by_mode.get(
        mode_clean,
        default_total_limits.get(mode_clean, 5000),
    )
    maximum_total = (
        0
        if exhaustive_verification or _safe_int(configured_total, 0, 0) == 0
        else _safe_int(configured_total, default_total_limits.get(mode_clean, 5000), 1, 500_000)
    )

    drop_unknown_tv = bool(
        planning.get("drop_unknown_tv_before_verification", True)
    )
    published = _published_urls()
    feedback_priority_keys = _feedback_priority_keys()

    input_count = len(candidates)
    mode_filtered: List[Dict[str, Any]] = []
    rejected_pipeline = 0
    rejected_unknown_tv = 0
    rejected_no_identity = 0
    unknown_samples: List[Dict[str, Any]] = []
    rerouted_counts: Dict[str, int] = {}

    for raw_item in candidates:
        if not isinstance(raw_item, dict):
            continue

        item = dict(raw_item)
        pipeline = str(item.get("source_pipeline") or "tv").strip().lower()

        if item.get("pipeline_rerouted") is True:
            key = (
                f"{item.get('original_source_pipeline', 'unknown')}"
                f"->{pipeline}"
            )
            rerouted_counts[key] = rerouted_counts.get(key, 0) + 1

        if pipeline not in active_pipelines:
            rejected_pipeline += 1
            continue

        if pipeline == "tv":
            category = str(item.get("category") or "").strip()
            if category not in VALID_TV_CATEGORIES:
                item["original_category"] = category or "missing"
                item["category"] = "Other"
                item["category_detection"] = "fallback_other"

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
        item["telemetry_priority"] = _is_feedback_priority(item, feedback_priority_keys)
        mode_filtered.append(item)

    # Exact playback dedupe across all normalized candidates. Signed query
    # strings and stream-specific headers remain part of the identity.
    unique_map: Dict[str, Dict[str, Any]] = {}
    duplicate_exact = 0
    for item in mode_filtered:
        key = _exact_stream_key(item)
        current = unique_map.get(key)
        if current is None:
            unique_map[key] = item
            continue

        duplicate_exact += 1
        if _candidate_rank(item, published, feedback_priority_keys) > _candidate_rank(current, published, feedback_priority_keys):
            unique_map[key] = _merge_source_provenance(item, current)
        else:
            unique_map[key] = _merge_source_provenance(current, item)

    unique_items = list(unique_map.values())

    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for item in unique_items:
        group = str(item.get("_verification_group") or _group_key(item))
        grouped.setdefault(group, []).append(item)

    planned: List[Dict[str, Any]] = []
    group_cap_dropped = 0
    initial_candidate_count = 0

    ordered_groups = sorted(
        grouped.items(),
        key=lambda pair: (
            -int(any(item.get("telemetry_priority") for item in pair[1])),
            -int(any(item.get("previously_published") for item in pair[1])),
            pair[0],
        ),
    )

    for group, group_items in ordered_groups:
        pipeline = str(group_items[0].get("source_pipeline") or "tv").lower()
        pool_limit = pool_limits.get(pipeline, 2)
        initial_limit = initial_limits.get(pipeline, 1)
        target = target_limits.get(pipeline, 1)

        ranked = sorted(
            group_items,
            key=lambda item: _candidate_rank(item, published, feedback_priority_keys),
            reverse=True,
        )
        selected = ranked if exhaustive_verification else _diverse_take(ranked, pool_limit)
        group_cap_dropped += max(0, len(ranked) - len(selected))

        for rank_index, item in enumerate(selected):
            candidate = dict(item)
            candidate["_verification_group"] = group
            candidate["_verification_rank"] = rank_index
            candidate["_verification_wave"] = (
                0
                if exhaustive_verification or rank_index < initial_limit
                else rank_index - initial_limit + 1
            )
            candidate["_verification_initial_limit"] = initial_limit
            candidate["_verification_target"] = target
            candidate["_verification_pool_size"] = len(selected)
            planned.append(candidate)
            if rank_index < initial_limit:
                initial_candidate_count += 1

    # Published links and first-wave items are placed first. The adaptive
    # scheduler still groups them and will not verify later waves unnecessarily.
    planned.sort(
        key=lambda item: (
            _safe_int(item.get("_verification_wave"), 0),
            -int(item.get("previously_published") is True),
            _safe_int(item.get("_verification_rank"), 0),
            str(item.get("_verification_group") or ""),
            str(item.get("source_id") or ""),
        )
    )

    global_cap_dropped = (
        max(0, len(planned) - maximum_total) if maximum_total > 0 else 0
    )
    if maximum_total > 0 and len(planned) > maximum_total:
        planned = planned[:maximum_total]

    # Recalculate pool metadata after a global cap because some groups may
    # have lost their later candidates.
    final_group_sizes: Dict[str, int] = {}
    for item in planned:
        group = str(item.get("_verification_group") or "")
        final_group_sizes[group] = final_group_sizes.get(group, 0) + 1
    for item in planned:
        item["_verification_pool_size"] = final_group_sizes.get(
            str(item.get("_verification_group") or ""), 1
        )

    pipeline_counts: Dict[str, int] = {}
    initial_pipeline_counts: Dict[str, int] = {}
    for item in planned:
        pipeline = str(item.get("source_pipeline") or "unknown")
        pipeline_counts[pipeline] = pipeline_counts.get(pipeline, 0) + 1
        if _safe_int(item.get("_verification_wave"), 0) == 0:
            initial_pipeline_counts[pipeline] = initial_pipeline_counts.get(pipeline, 0) + 1

    actual_initial_count = sum(
        1 for item in planned if _safe_int(item.get("_verification_wave"), 0) == 0
    )

    summary: Dict[str, Any] = {
        "timestamp": _utc_now(),
        "mode": mode_clean,
        "adaptive_verification": True,
        "exhaustive_verification": exhaustive_verification,
        "active_pipelines": sorted(active_pipelines),
        "input_candidates": input_count,
        "after_mode_and_category_filter": len(mode_filtered),
        "after_exact_deduplication": len(unique_items),
        "candidate_pool_count": len(planned),
        "planned_candidates": len(planned),
        "initial_wave_candidates": actual_initial_count,
        "unique_groups": len(final_group_sizes),
        "previously_published_urls_found": len(published),
        "telemetry_priority_key_count": len(feedback_priority_keys),
        "pipeline_counts": pipeline_counts,
        "initial_pipeline_counts": initial_pipeline_counts,
        "rerouted_counts": rerouted_counts,
        "limits": {
            "initial_per_group": initial_limits,
            "maximum_pool_per_group": pool_limits,
            "target_publishable_per_group": target_limits,
            "maximum_total_pool": maximum_total,
        },
        "dropped": {
            "other_pipeline": rejected_pipeline,
            "unknown_tv_category": rejected_unknown_tv,
            "missing_identity": rejected_no_identity,
            "exact_duplicates": duplicate_exact,
            "per_item_cap": group_cap_dropped,
            "global_cap": global_cap_dropped,
        },
        "unknown_tv_samples": unknown_samples,
    }

    _atomic_write_json(report_path, summary)
    return planned, summary
