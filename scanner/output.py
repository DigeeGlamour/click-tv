"""
Output Publisher & Protection Engine

Responsibilities:
- Atomically writes channel JSON files, movie category folders/pages, event JSON,
  manifest.json, and scan reports.
- Enforces configurable sudden-drop protection for live-TV categories by
  preserving the last known good payload.
- Removes stale movie page files through a staged category-folder swap.
- Builds dynamic manifest visibility/count metadata.
- Sends an optional Telegram HTML completion alert without weakening TLS.
"""

from __future__ import annotations

import html
import json
import os
import re
import shutil
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


DEFAULT_DROP_PERCENTAGE = 70
DEFAULT_DROP_MINIMUM_BASELINE = 10

CHANNEL_SLUGS = {
    "Bangla": "bangla",
    "Sports": "sports",
    "Indian": "indian",
    "Cartoon": "cartoon",
    "Islamic": "islamic",
    "Foreign News": "foreign-news",
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


def _safe_slug(value: Any, fallback: str = "unknown") -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text or fallback


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


def _fsync_directory(directory: Path) -> None:
    """Best-effort directory fsync after atomic rename operations."""
    try:
        fd = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return

    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _atomic_write_json(file_path: str | Path, data: Any) -> None:
    """Write one JSON file through a unique same-directory temporary file."""
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    temp_path = path.with_name(
        f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp"
    )

    try:
        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

        os.replace(temp_path, path)
        _fsync_directory(path.parent)
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass


def _atomic_replace_directory(
    target_directory: str | Path,
    files: Dict[str, Any],
) -> None:
    """
    Stage a complete directory, then swap it into place with rollback support.

    This removes stale files such as old movie page-003.json files that no
    longer belong to the current scan.
    """
    target = Path(target_directory)
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)

    token = f"{os.getpid()}.{time.time_ns()}"
    staging = parent / f".{target.name}.{token}.stage"
    backup = parent / f".{target.name}.{token}.backup"

    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=False)

    moved_old_target = False

    try:
        for relative_name, payload in files.items():
            relative_path = Path(relative_name)

            if (
                relative_path.is_absolute()
                or ".." in relative_path.parts
                or not relative_path.name
            ):
                raise ValueError(
                    f"Unsafe staged output path: {relative_name}"
                )

            _atomic_write_json(staging / relative_path, payload)

        if target.exists():
            os.replace(target, backup)
            moved_old_target = True

        os.replace(staging, target)
        _fsync_directory(parent)

        if backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
    except Exception:
        if target.exists() and moved_old_target:
            shutil.rmtree(target, ignore_errors=True)

        if moved_old_target and backup.exists() and not target.exists():
            os.replace(backup, target)
            _fsync_directory(parent)

        raise
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        if backup.exists() and target.exists():
            shutil.rmtree(backup, ignore_errors=True)


def _ensure_manifest(existing: Dict[str, Any]) -> Dict[str, Any]:
    manifest = dict(existing) if isinstance(existing, dict) else {}

    manifest.setdefault("schema_version", 1)
    manifest.setdefault(
        "today_match",
        {
            "count": 0,
            "visible": False,
            "url": "data/today-match.json",
        },
    )
    manifest.setdefault(
        "upcoming",
        {
            "count": 0,
            "visible": False,
            "url": "data/upcoming.json",
        },
    )

    if not isinstance(manifest.get("channels"), dict):
        manifest["channels"] = {}
    if not isinstance(manifest.get("movies"), dict):
        manifest["movies"] = {}

    return manifest


def _channel_count(payload: Dict[str, Any]) -> int:
    channels = payload.get("channels")
    if isinstance(channels, list):
        return len(channels)

    return _safe_int(payload.get("count"), 0, 0)


def _drop_percentage(previous_count: int, current_count: int) -> float:
    if previous_count <= 0 or current_count >= previous_count:
        return 0.0

    return (
        (previous_count - current_count)
        / float(previous_count)
        * 100.0
    )


def _best_previous_channel_payload(
    target_file: Path,
    last_good_file: Path,
) -> Tuple[Dict[str, Any], str]:
    last_good = _load_json_file(last_good_file)
    if _channel_count(last_good) > 0:
        return last_good, "last_good"

    current_target = _load_json_file(target_file)
    if _channel_count(current_target) > 0:
        return current_target, "current_target"

    return {}, ""


def _channel_payload_has_vod(payload: Dict[str, Any]) -> bool:
    cards = payload.get("channels") if isinstance(payload, dict) else []
    if not isinstance(cards, list):
        return False

    vod_extensions = (".mp4", ".mkv", ".webm", ".avi", ".mov", ".m4v", ".flv")
    path_markers = (
        "/movies/", "/movie/", "/bollywood/", "/hollywood/",
        "/hindidub/", "/indianbangla/", "/series/", "/natok/",
        "/telefilm/", "/vod/",
    )

    for card in cards:
        if not isinstance(card, dict):
            continue
        url = str(card.get("url") or "").split("|", 1)[0].casefold()
        path = url.split("?", 1)[0]
        if path.endswith(vod_extensions) or any(marker in path for marker in path_markers):
            return True
    return False


def _publish_channel_category(
    category_name: str,
    card_list: List[Dict[str, Any]],
    channels_dir: Path,
    last_good_dir: Path,
    maximum_drop_percentage: int,
    minimum_baseline_count: int,
    timestamp: str,
    force_replace: bool = False,
) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    slug = CHANNEL_SLUGS.get(
        category_name,
        _safe_slug(category_name),
    )
    target_file = channels_dir / f"{slug}.json"
    last_good_file = last_good_dir / f"{slug}.json"

    previous_payload, previous_source = _best_previous_channel_payload(
        target_file,
        last_good_file,
    )
    previous_count = _channel_count(previous_payload)
    current_count = len(card_list)
    drop_pct = _drop_percentage(previous_count, current_count)

    # A migration bypass is allowed only when the previous category actually
    # contains direct VOD/movie links. Normal sudden-drop protection remains
    # active for clean TV categories.
    force_replace = bool(force_replace and _channel_payload_has_vod(previous_payload))

    should_preserve = (
        not force_replace
        and previous_count >= minimum_baseline_count
        and drop_pct >= maximum_drop_percentage
    )

    if should_preserve and previous_payload:
        if _channel_count(_load_json_file(target_file)) <= 0:
            _atomic_write_json(target_file, previous_payload)

        published_count = previous_count
        manifest_entry = {
            "count": published_count,
            "incoming_count": current_count,
            "visible": published_count > 0,
            "protected": True,
            "url": f"data/channels/{slug}.json",
        }
        error = {
            "type": "sudden_drop_protection",
            "category": category_name,
            "previous_count": previous_count,
            "incoming_count": current_count,
            "drop_percentage": round(drop_pct, 2),
            "threshold_percentage": maximum_drop_percentage,
            "preserved_from": previous_source,
            "error": (
                f"Sudden drop protection triggered for {category_name}: "
                f"{previous_count} -> {current_count} "
                f"({drop_pct:.2f}% drop). Previous good data was preserved."
            ),
            "timestamp": timestamp,
        }
        return manifest_entry, error

    payload = {
        "category": category_name,
        "updated_at": timestamp,
        "count": current_count,
        "channels": card_list,
    }

    _atomic_write_json(target_file, payload)
    _atomic_write_json(last_good_file, payload)

    return (
        {
            "count": current_count,
            "incoming_count": current_count,
            "visible": current_count > 0,
            "protected": False,
            "url": f"data/channels/{slug}.json",
        },
        None,
    )


def _event_count(payload: Dict[str, Any]) -> int:
    for key in ("total", "count"):
        if payload.get(key) is not None:
            return _safe_int(payload.get(key), 0, 0)

    for key in ("items", "matches", "events"):
        values = payload.get(key)
        if isinstance(values, list):
            return len(values)

    return 0


def _as_dict_list(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _publish_movie_category(
    category_name: str,
    category_payload: Dict[str, Any],
    movies_dir: Path,
    timestamp: str,
) -> Dict[str, Any]:
    if not isinstance(category_payload, dict):
        raise ValueError(
            f"Movie category payload must be an object: {category_name}"
        )

    index_payload = category_payload.get("index")
    page_contents = category_payload.get("page_contents")

    if not isinstance(index_payload, dict):
        raise ValueError(
            f"Movie category is missing index payload: {category_name}"
        )
    if not isinstance(page_contents, dict):
        raise ValueError(
            f"Movie category is missing page_contents: {category_name}"
        )

    slug = _safe_slug(
        index_payload.get("slug"),
        _safe_slug(category_name),
    )

    total_pages = _safe_int(index_payload.get("total_pages"), 0, 0)
    if total_pages != len(page_contents):
        raise ValueError(
            f"Movie page count mismatch for {category_name}: "
            f"index={total_pages}, payloads={len(page_contents)}"
        )

    staged_files: Dict[str, Any] = {}
    final_index = dict(index_payload)
    final_index["updated_at"] = timestamp
    staged_files["index.json"] = final_index

    for page_filename, page_payload in sorted(page_contents.items()):
        filename = str(page_filename or "")
        if not re.fullmatch(r"page-\d{3,}\.json", filename):
            raise ValueError(
                f"Invalid movie page filename for {category_name}: "
                f"{filename}"
            )
        if not isinstance(page_payload, dict):
            raise ValueError(
                f"Movie page payload must be an object: "
                f"{category_name}/{filename}"
            )

        staged_files[filename] = page_payload

    _atomic_replace_directory(
        movies_dir / slug,
        staged_files,
    )

    count = _safe_int(final_index.get("count"), 0, 0)
    return {
        "count": count,
        "visible": count > 0,
        "total_pages": total_pages,
        "index": f"data/movies/{slug}/index.json",
    }


def send_telegram_alert(
    summary_text: str,
    timeout_seconds: int = 10,
) -> bool:
    """Send an HTML Telegram message using normal certificate verification."""
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

    if not bot_token or not chat_id:
        return False

    api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": summary_text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        request = urllib.request.Request(
            api_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "User-Agent": "LiveSignal-Scanner/2.0",
            },
            method="POST",
        )

        with urllib.request.urlopen(
            request,
            timeout=_safe_int(timeout_seconds, 10, 1, 30),
        ) as response:
            status = int(getattr(response, "status", 200) or 200)
            return 200 <= status < 300
    except Exception as error:
        print(
            "[Telegram Notification Warning] "
            f"Could not send message: {error}"
        )
        return False


def publish_scan_outputs(
    channels_data: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    movies_data: Optional[Dict[str, Dict[str, Any]]] = None,
    events_data: Optional[Dict[str, Dict[str, Any]]] = None,
    settings_path: str = "config/settings.json",
    source_error_items: Optional[List[Dict[str, Any]]] = None,
    rejected_low_quality_items: Optional[List[Dict[str, Any]]] = None,
    extra_quarantine_items: Optional[List[Dict[str, Any]]] = None,
    data_dir: str = "data",
    state_dir: str = "state",
    reports_dir: str = "reports",
    scan_mode: str = "all",
) -> Dict[str, Any]:
    """
    Publish scanner outputs and return the written scan-summary payload.

    channels_data, movies_data, and events_data are distinguished from None so
    an explicitly empty scan result can still update/hide its output.
    """
    mode_clean = str(scan_mode or "all").strip().lower()
    mode_aliases = {
        "tv": "channels",
        "channels-discovery": "channels",
        "movies-discovery": "movies",
        "full-audit": "all",
        "today_match": "today",
    }
    mode_clean = mode_aliases.get(mode_clean, mode_clean)
    supported_modes = {
        "all",
        "channels",
        "movies",
        "events",
        "today",
        "upcoming",
    }
    if mode_clean not in supported_modes:
        mode_clean = "all"

    settings = _load_json_file(settings_path)
    failure_config = settings.get("failure_protection", {})
    if not isinstance(failure_config, dict):
        failure_config = {}

    maximum_drop_percentage = _safe_int(
        failure_config.get(
            "maximum_drop_percentage",
            DEFAULT_DROP_PERCENTAGE,
        ),
        DEFAULT_DROP_PERCENTAGE,
        1,
        100,
    )
    minimum_baseline_count = _safe_int(
        failure_config.get(
            "minimum_previous_count",
            DEFAULT_DROP_MINIMUM_BASELINE,
        ),
        DEFAULT_DROP_MINIMUM_BASELINE,
        1,
        1_000_000,
    )

    data_root = Path(data_dir)
    state_root = Path(state_dir)
    reports_root = Path(reports_dir)
    channels_dir = data_root / "channels"
    movies_dir = data_root / "movies"
    last_good_dir = state_root / "last-good"

    routing_config = settings.get("content_routing", {})
    if not isinstance(routing_config, dict):
        routing_config = {}
    migration_id = str(
        routing_config.get("cleanup_migration_id", "vod-routing-v1")
    ).strip() or "vod-routing-v1"
    migration_marker = state_root / "migrations" / f"{migration_id}.json"
    cleanup_categories_raw = routing_config.get(
        "cleanup_polluted_tv_categories", ["Bangla", "Indian"]
    )
    cleanup_categories = {
        str(value).strip()
        for value in cleanup_categories_raw
        if str(value).strip()
    } if isinstance(cleanup_categories_raw, list) else {"Bangla", "Indian"}
    cleanup_minimum_incoming = _safe_int(
        routing_config.get("cleanup_minimum_incoming_tv", 20),
        20,
        1,
        1_000_000,
    )
    incoming_channel_total = 0
    if isinstance(channels_data, dict):
        incoming_channel_total = sum(
            len(value)
            for key, value in channels_data.items()
            if key != "quarantine" and isinstance(value, list)
        )

    cleanup_migration_active = bool(
        channels_data is not None
        and routing_config.get("cleanup_polluted_tv_once", True)
        and not migration_marker.exists()
        and incoming_channel_total >= cleanup_minimum_incoming
    )

    data_root.mkdir(parents=True, exist_ok=True)
    reports_root.mkdir(parents=True, exist_ok=True)
    last_good_dir.mkdir(parents=True, exist_ok=True)

    timestamp = _utc_now()
    manifest_file = data_root / "manifest.json"
    manifest = _ensure_manifest(_load_json_file(manifest_file))
    manifest["updated_at"] = timestamp

    source_errors = _as_dict_list(source_error_items)
    rejected_items = _as_dict_list(rejected_low_quality_items)
    quarantine_items = _as_dict_list(extra_quarantine_items)

    # 1. Live TV
    if channels_data is not None:
        if not isinstance(channels_data, dict):
            raise ValueError("channels_data must be a dictionary")

        quarantine_items.extend(
            _as_dict_list(channels_data.get("quarantine"))
        )

        for category_name, raw_cards in channels_data.items():
            if category_name == "quarantine":
                continue

            if not isinstance(raw_cards, list):
                source_errors.append(
                    {
                        "type": "invalid_channel_payload",
                        "category": category_name,
                        "error": (
                            "Channel category payload was not a list; "
                            "previous output was preserved."
                        ),
                        "timestamp": timestamp,
                    }
                )
                continue

            cards = [
                item for item in raw_cards
                if isinstance(item, dict)
            ]

            manifest_entry, drop_error = _publish_channel_category(
                category_name=str(category_name),
                card_list=cards,
                channels_dir=channels_dir,
                last_good_dir=last_good_dir,
                maximum_drop_percentage=maximum_drop_percentage,
                minimum_baseline_count=minimum_baseline_count,
                timestamp=timestamp,
                force_replace=(
                    cleanup_migration_active
                    and str(category_name) in cleanup_categories
                ),
            )
            manifest["channels"][str(category_name)] = manifest_entry

            if drop_error is not None:
                source_errors.append(drop_error)

        if cleanup_migration_active:
            migration_marker.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write_json(
                migration_marker,
                {
                    "migration": migration_id,
                    "completed_at": timestamp,
                    "categories_rebuilt": sorted(cleanup_categories),
                    "reason": (
                        "Removed VOD/movie items that were previously "
                        "misrouted into TV category files"
                    ),
                },
            )
            source_errors.append(
                {
                    "type": "content_routing_migration",
                    "status": "completed",
                    "categories": sorted(cleanup_categories),
                    "timestamp": timestamp,
                    "error": (
                        "One-time TV/VOD cleanup migration completed; sudden "
                        "drop protection was bypassed only for affected categories."
                    ),
                }
            )

    # 2. Movies
    if movies_data is not None:
        if not isinstance(movies_data, dict):
            raise ValueError("movies_data must be a dictionary")

        for category_name, category_payload in movies_data.items():
            try:
                manifest["movies"][str(category_name)] = (
                    _publish_movie_category(
                        category_name=str(category_name),
                        category_payload=category_payload,
                        movies_dir=movies_dir,
                        timestamp=timestamp,
                    )
                )
            except Exception as error:
                source_errors.append(
                    {
                        "type": "movie_publish_error",
                        "category": str(category_name),
                        "error": str(error),
                        "timestamp": timestamp,
                    }
                )

    # 3. Events
    if events_data is not None:
        if not isinstance(events_data, dict):
            raise ValueError("events_data must be a dictionary")

        event_specs = {
            "today_match": (
                data_root / "today-match.json",
                "data/today-match.json",
            ),
            "upcoming": (
                data_root / "upcoming.json",
                "data/upcoming.json",
            ),
        }

        for event_key, (target_file, public_url) in event_specs.items():
            if event_key not in events_data:
                continue

            payload = events_data.get(event_key)
            if not isinstance(payload, dict):
                source_errors.append(
                    {
                        "type": "invalid_event_payload",
                        "event": event_key,
                        "error": (
                            "Event payload was not an object; "
                            "previous output was preserved."
                        ),
                        "timestamp": timestamp,
                    }
                )
                continue

            final_payload = dict(payload)
            final_payload.setdefault("updated_at", timestamp)
            _atomic_write_json(target_file, final_payload)

            count = _event_count(final_payload)
            manifest[event_key] = {
                "count": count,
                "visible": count > 0,
                "url": public_url,
            }

    # 4. Manifest and reports
    _atomic_write_json(manifest_file, manifest)

    _atomic_write_json(
        reports_root / "source-errors.json",
        {
            "timestamp": timestamp,
            "count": len(source_errors),
            "errors": source_errors,
        },
    )
    _atomic_write_json(
        reports_root / "quarantine.json",
        {
            "timestamp": timestamp,
            "count": len(quarantine_items),
            "items": quarantine_items,
        },
    )
    _atomic_write_json(
        reports_root / "rejected-low-quality.json",
        {
            "timestamp": timestamp,
            "count": len(rejected_items),
            "items": rejected_items,
        },
    )

    total_channels = sum(
        _safe_int(entry.get("count"), 0, 0)
        for entry in manifest.get("channels", {}).values()
        if isinstance(entry, dict)
    )
    total_movies = sum(
        _safe_int(entry.get("count"), 0, 0)
        for entry in manifest.get("movies", {}).values()
        if isinstance(entry, dict)
    )

    scan_status = (
        "completed_with_warnings"
        if source_errors
        else "completed"
    )

    scan_summary: Dict[str, Any] = {
        "last_scan": timestamp,
        "status": scan_status,
        "mode": mode_clean,
        "source_errors": len(source_errors),
        "quarantined_channels": len(quarantine_items),
        "rejected_low_quality": len(rejected_items),
        "totals": {
            "channels": total_channels,
            "movies": total_movies,
            "today_match": _safe_int(
                manifest.get("today_match", {}).get("count"),
                0,
                0,
            ),
            "upcoming": _safe_int(
                manifest.get("upcoming", {}).get("count"),
                0,
                0,
            ),
        },
        "manifest_summary": manifest,
    }

    _atomic_write_json(
        reports_root / "scan-summary.json",
        scan_summary,
    )

    # Telegram notification is intentionally NOT sent here.
    # This function runs before Git commit/push. A separate post-push
    # notifier sends success only after GitHub has accepted the commit.
    scan_summary["telegram_sent"] = False
    scan_summary["telegram_after_push"] = True
    _atomic_write_json(
        reports_root / "scan-summary.json",
        scan_summary,
    )

    return scan_summary


if __name__ == "__main__":
    summary = publish_scan_outputs()
    print(
        "Output publish completed: "
        f"status={summary['status']}, "
        f"errors={summary['source_errors']}"
    )
