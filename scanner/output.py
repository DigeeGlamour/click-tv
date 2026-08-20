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
import ipaddress
import json
import os
import re
import shutil
import time
import urllib.request
from urllib.parse import urlparse
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from scanner.snapshot_publish import (
    ORDER_ALLOWED_HOSTS,
    ORDER_EVENT_FILE,
    ORDER_MANIFEST,
    SnapshotPublisher,
)
from scanner.playback_profiles import (
    PlaybackProfileCollector,
    merge_public_catalog,
    redact_public_report,
)
from scanner.browser_reachability import (
    item_is_browser_reachable,
    item_is_proven_live,
    requires_same_run_proof,
)
from scanner.security import redact_sensitive_text


DEFAULT_DROP_PERCENTAGE = 70
DEFAULT_DROP_MINIMUM_BASELINE = 10

CHANNEL_SLUGS = {
    "Bangla": "bangla",
    "Sports": "sports",
    "Indian": "indian",
    "Cartoon": "cartoon",
    "Islamic": "islamic",
    "Infotainments": "infotainments",
    "Foreign News": "foreign-news",
    "Other": "other",
}

# A fixed serial order for these named Sports channels, by direct request:
# never re-sorted by health, verification status or which scan happened to
# discover them first - the position is the identity, always in this order
# whenever the channel is present. A channel found this scan that is not on
# this list keeps its existing relative order, appended after every pinned
# name that was actually found.
PINNED_SPORTS_CHANNEL_ORDER = (
    "T Sports", "PTV Sports", "Willow", "Willow 2", "Star Sports 1 Hindi",
    "Star Sports 2", "A Sports", "Star Sports Select 2", "UNITE8 SPORTS 2",
    "Pk Sports", "BEIN SPORTS", "BEIN Sports 2", "BEIN Sports 3",
    "BEIN Sports 4", "BEIN Sports 5", "Bein Sports Extra", "BeIN Sports Xtra",
    "Bein Sports Xtra - *2", "Live Sports", "GOAL TV", "M Sports",
)


def _pinned_channel_name_key(name: Any) -> str:
    text = str(name or "").strip().casefold()
    text = text.replace("–", "-").replace("—", "-")
    return " ".join(text.split())


_PINNED_SPORTS_ORDER_INDEX = {
    _pinned_channel_name_key(name): index
    for index, name in enumerate(PINNED_SPORTS_CHANNEL_ORDER)
}


def _apply_pinned_sports_order(cards: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    pinned: List[Tuple[int, Dict[str, Any]]] = []
    rest: List[Dict[str, Any]] = []
    for card in cards:
        index = _PINNED_SPORTS_ORDER_INDEX.get(_pinned_channel_name_key(card.get("name")))
        if index is None:
            rest.append(card)
        else:
            pinned.append((index, card))
    pinned.sort(key=lambda pair: pair[0])
    return [card for _, card in pinned] + rest


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


PUBLIC_PRIVATE_FIELDS = {
    "headers",
    "request_headers",
    "raw_headers",
    "source_headers",
    "cookie",
    "authorization",
    "user_agent",
    "verify_token",
    "api_token",
    "password",
    "secret",
}

_ACTIVE_PLAYBACK_COLLECTOR: Optional[PlaybackProfileCollector] = None


def _sanitize_public_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """Remove raw credentials/headers while preserving safe playback metadata."""
    if _ACTIVE_PLAYBACK_COLLECTOR is not None:
        return _ACTIVE_PLAYBACK_COLLECTOR.sanitize_item(item)
    clean: Dict[str, Any] = {}
    for key, value in item.items():
        key_lower = str(key).strip().lower()
        if key_lower in PUBLIC_PRIVATE_FIELDS:
            continue
        if key_lower == "backups" and isinstance(value, list):
            clean_backups: List[Any] = []
            for backup in value[:5]:
                if isinstance(backup, dict):
                    clean_backups.append(_sanitize_public_item(backup))
                elif isinstance(backup, str):
                    clean_backups.append(backup)
            clean[key] = clean_backups
            continue
        if key_lower == "links" and isinstance(value, list):
            clean_links: List[Any] = []
            for source in value[:6]:
                if isinstance(source, dict):
                    clean_links.append(_sanitize_public_item(source))
                elif isinstance(source, str):
                    clean_links.append(source)
            clean[key] = clean_links
            continue
        clean[key] = value
    return clean


def _dedupe_public_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Last-gate identity dedup. Whatever produced two cards for the same
    channel/movie upstream, the public JSON's declared count (len of this
    same list) must always match what actually ships - a duplicate id here
    is exactly what turns into a Pages-validator "count mismatch" plus a
    "duplicate channel name" failure at once.

    `id` alone is not a safe identity: two genuinely different channels can
    share one slugified id ("Aaj Tak" and "Aaj Tak Bangla" both slugify to
    "aaj-tak"), and dropping the second would trade one incident for a worse
    one. Requiring `id` *and* name together fixes that false match while
    still catching the real one: two published cards for the same channel
    name discovered through two different backup-worthy sources share id and
    name but not necessarily url - the validator rightly rejects two cards
    for one channel name regardless, so identity is (id, name), not a url a
    genuine duplicate is not even guaranteed to share. `url` alone is the
    fallback only when there is no id to key on at all.
    """
    seen: Set[Tuple[str, ...]] = set()
    deduped: List[Dict[str, Any]] = []
    for item in items:
        identity: Optional[Tuple[str, ...]] = None
        identity_id = str(item.get("id") or "").strip()
        identity_name = str(item.get("name") or item.get("title") or "").strip()
        if identity_id and identity_name:
            identity = ("id+name", identity_id, identity_name)
        else:
            url = str(item.get("url") or "").strip()
            if url:
                identity = ("url", url)
        if identity is not None:
            if identity in seen:
                continue
            seen.add(identity)
        deduped.append(item)
    return deduped


def _sanitize_public_items(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    sanitized = [
        _sanitize_public_item(item)
        for item in value
        if isinstance(item, dict)
    ]
    # Last gate before the public JSON. Two ways a dead link used to ship with a
    # green "Verified" badge: no viewer route at all (http:// on a bare IP), or a
    # status like "stale_last_good" that means "this run's check failed, reusing
    # the old link". Neither is allowed out.
    reachable = [
        item for item in sanitized
        if item_is_browser_reachable(item)
        and (item_is_proven_live(item) if requires_same_run_proof(item) else True)
    ]
    return _dedupe_public_items(reachable)




def _iter_public_source_urls(value: Any) -> Iterable[str]:
    """Yield primary and backup URLs from one public item recursively."""
    if not isinstance(value, dict):
        return

    for key in ("url", "stream_url", "link"):
        url = str(value.get(key) or "").strip()
        if url:
            yield url

    drm = value.get("drm")
    if isinstance(drm, dict):
        for key in (
            "license_url", "license_server", "server_url",
            "certificate_url", "server_certificate_url", "fairplay_certificate_url",
        ):
            drm_url = str(drm.get(key) or "").strip()
            if drm_url:
                yield drm_url

    for key in ("backups", "links", "sources", "standby"):
        children = value.get(key)
        if not isinstance(children, list):
            continue
        for child in children:
            if isinstance(child, str):
                child_url = child.strip()
                if child_url:
                    yield child_url
            elif isinstance(child, dict):
                yield from _iter_public_source_urls(child)


def _collect_allowed_hosts_from_data(
    data_root: Path,
    overrides: Optional[Dict[Path, Any]] = None,
) -> List[str]:
    """Build the playback proxy initial-host allowlist from published JSON.

    Requirement 15: while a snapshot is staged, the files it is about to publish
    are the ones that matter. Reading their published predecessors instead would
    produce an allowlist describing the previous snapshot - missing exactly the
    hosts the new cards need.
    """
    hosts: set[str] = set()
    staged = {
        (path.resolve() if path.is_absolute() else path): payload
        for path, payload in (overrides or {}).items()
    }

    def read(file_path: Path) -> Dict[str, Any]:
        key = file_path.resolve() if file_path.is_absolute() else file_path
        if key in staged:
            payload = staged[key]
            return payload if isinstance(payload, dict) else {}
        return _load_json_file(file_path)

    json_files: List[Path] = []
    channels_dir = data_root / "channels"
    movies_dir = data_root / "movies"
    series_dir = data_root / "series"
    if channels_dir.exists():
        json_files.extend(channels_dir.glob("*.json"))
    if movies_dir.exists():
        json_files.extend(movies_dir.glob("*/page-*.json"))
    if series_dir.exists():
        json_files.extend(series_dir.glob("*/*/season-*.json"))
    json_files.extend([
        data_root / "today-match.json",
        data_root / "upcoming.json",
        # Still read for a repository that predates the sharded catalogue.
        data_root / "playback-sources.json",
    ])
    # The catalogue holds the only copy of a protected source's real URL, plus
    # its DRM licence and certificate hosts. Missing these would leave the
    # proxy's allowlist without the very hosts protected playback needs, so
    # every shard has to be walked here, not just the index.
    playback_dir = data_root / "playback"
    if playback_dir.exists():
        json_files.extend(sorted(playback_dir.glob("*.json")))

    # A staged file that does not exist on disk yet - the very first scan writes
    # every shard for the first time - would otherwise be invisible here, and
    # the allowlist would ship without the hosts those new profiles need.
    if staged:
        already = {
            (path.resolve() if path.is_absolute() else path)
            for path in json_files
        }
        json_files.extend(sorted(path for path in staged if path not in already))

    for file_path in json_files:
        payload = read(file_path)
        candidate_lists: List[Any] = []
        for key in ("channels", "items", "movies", "events", "matches"):
            if isinstance(payload.get(key), list):
                candidate_lists.append(payload.get(key))
        if isinstance(payload, list):
            candidate_lists.append(payload)
        records = payload.get("records")
        if isinstance(records, dict):
            candidate_lists.append(list(records.values()))

        for candidate_list in candidate_lists:
            for item in candidate_list:
                if not isinstance(item, dict):
                    continue
                for source_url in _iter_public_source_urls(item):
                    try:
                        parsed = urlparse(source_url)
                    except ValueError:
                        continue
                    hostname = str(parsed.hostname or "").strip().lower().rstrip(".")
                    if parsed.scheme not in {"http", "https"} or not hostname:
                        continue
                    try:
                        ip_value = ipaddress.ip_address(hostname)
                    except ValueError:
                        ip_value = None
                    if ip_value is not None and (
                        ip_value.is_private
                        or ip_value.is_loopback
                        or ip_value.is_link_local
                        or ip_value.is_multicast
                        or ip_value.is_reserved
                        or ip_value.is_unspecified
                    ):
                        continue
                    hosts.add(hostname)

    return sorted(hosts)


def _write_allowed_hosts_file(
    data_root: Path,
    timestamp: str,
    snapshot: Any = None,
) -> Dict[str, Any]:
    hosts = _collect_allowed_hosts_from_data(
        data_root,
        overrides=(snapshot.overrides() if snapshot is not None else None),
    )
    payload = {
        "updated_at": timestamp,
        "count": len(hosts),
        "hosts": hosts,
    }
    if snapshot is not None:
        snapshot.stage(
            data_root / "allowed-hosts.json",
            payload,
            order=ORDER_ALLOWED_HOSTS,
            kind="allowed_hosts",
        )
    else:
        _atomic_write_json(data_root / "allowed-hosts.json", payload)
    return payload


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


def _payload_age_hours(payload: Dict[str, Any], now: Optional[datetime] = None) -> float:
    """Hours since the preserved payload was written; -1.0 when unknown.

    Sudden-drop protection is meant to survive one bad scan, not to freeze a
    category forever. It compares the incoming count against the *preserved*
    count, so once preservation happens the preserved count becomes the new
    baseline and every later scan is measured against it again - the category
    can never recover on its own. Measured on 2026-08-20: data/channels/other
    .json was still the 2026-08-17T09:24 payload, 433 cards, while three days
    of scans kept arriving with 85-89 and being discarded. Age gives the rule
    an exit.
    """
    stamp = str(payload.get("updated_at") or "").strip() if isinstance(payload, dict) else ""
    if not stamp:
        return -1.0
    try:
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return -1.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    reference = now or datetime.now(timezone.utc)
    return max(0.0, (reference - parsed).total_seconds() / 3600.0)


def _publish_channel_category(
    category_name: str,
    card_list: List[Dict[str, Any]],
    channels_dir: Path,
    last_good_dir: Path,
    maximum_drop_percentage: int,
    minimum_baseline_count: int,
    timestamp: str,
    force_replace: bool = False,
    maximum_preserved_age_hours: int = 0,
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
    public_cards = _sanitize_public_items(card_list)
    previous_count = _channel_count(previous_payload)
    current_count = len(public_cards)
    drop_pct = _drop_percentage(previous_count, current_count)

    preserved_age_hours = _payload_age_hours(previous_payload)
    preservation_expired = bool(
        maximum_preserved_age_hours > 0
        and preserved_age_hours >= 0
        and preserved_age_hours >= maximum_preserved_age_hours
        and current_count > 0
    )

    should_preserve = (
        not force_replace
        and not preservation_expired
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
        "channels": public_cards,
    }
    if preservation_expired:
        payload["drop_protection_expired"] = {
            "preserved_count": previous_count,
            "preserved_age_hours": round(preserved_age_hours, 2),
            "maximum_preserved_age_hours": maximum_preserved_age_hours,
            "reason": (
                "Sudden-drop protection released: the preserved payload was "
                "older than the configured limit, so a stale catalogue is no "
                "longer served in place of a fresh scan."
            ),
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


def _movie_payload_count(movies_data: Dict[str, Any]) -> int:
    total = 0
    for payload in movies_data.values():
        if not isinstance(payload, dict):
            continue
        index_payload = payload.get("index")
        if isinstance(index_payload, dict):
            total += _safe_int(index_payload.get("count"), 0, 0)
    return total


def _manifest_movie_count(manifest: Dict[str, Any]) -> int:
    movies = manifest.get("movies")
    if not isinstance(movies, dict):
        return 0
    return sum(
        _safe_int(entry.get("count"), 0, 0)
        for entry in movies.values()
        if isinstance(entry, dict)
    )


def _movie_drop_warning(
    previous_count: int,
    incoming_count: int,
    maximum_drop_percentage: int,
    timestamp: str,
) -> Dict[str, Any]:
    actual_drop = _drop_percentage(previous_count, incoming_count)
    return {
        "type": "movie_output_safety_warning",
        "status": "previous_output_preserved",
        "previous_count": previous_count,
        "incoming_count": incoming_count,
        "drop_percentage": round(actual_drop, 2),
        "maximum_allowed_drop_percentage": maximum_drop_percentage,
        "timestamp": timestamp,
        "error": (
            "Movie output was not replaced because the new publishable total "
            f"fell by {actual_drop:.1f}% (allowed: {maximum_drop_percentage}%)."
        ),
    }


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

        final_page_payload = dict(page_payload)
        if isinstance(final_page_payload.get("items"), list):
            final_page_payload["items"] = _sanitize_public_items(
                final_page_payload.get("items")
            )
            final_page_payload["count"] = len(final_page_payload["items"])
        if isinstance(final_page_payload.get("movies"), list):
            final_page_payload["movies"] = _sanitize_public_items(
                final_page_payload.get("movies")
            )
            final_page_payload["count"] = len(final_page_payload["movies"])
        staged_files[filename] = final_page_payload

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
            f"Could not send message: {redact_sensitive_text(error)}"
        )
        return False


def _reconcile_manifest_counts(
    manifest: Dict[str, Any],
    data_root: Path,
    overrides: Optional[Dict[Path, Any]] = None,
) -> None:
    """Force every manifest count to match the file that is actually on disk.

    Three independent scanners (GitHub Actions, a local PC clone, Google Colab)
    can each fetch, scan and push around the same time. A scan mode that never
    touches channels (e.g. "today") still loads and rewrites the WHOLE
    manifest, and the sudden-drop-protection path in
    _publish_channel_category() sources its "previous count" from
    state/last-good/<slug>.json while leaving data/channels/<slug>.json
    completely untouched. If a git rebase resolves those two files (or a
    manifest entry versus the channel file it describes) from different sides
    of a conflict, the count baked into manifest.json can end up describing a
    file that no longer looks like that anymore — exactly the
    "manifest <Category> count mismatch" failures seen in production.

    Whatever the cause, the fix is the same: manifest.json is a derived index,
    never the source of truth, so its counts are recomputed here from the
    actual files this run is about to publish, right before the final write.
    """

    staged = {
        (path.resolve() if path.is_absolute() else path): payload
        for path, payload in (overrides or {}).items()
    }

    def read(file_path: Path) -> Dict[str, Any]:
        key = file_path.resolve() if file_path.is_absolute() else file_path
        if key in staged:
            payload = staged[key]
            return payload if isinstance(payload, dict) else {}
        return _load_json_file(file_path)

    def resolve(public_path: str) -> Path:
        # Manifest "url"/"index" fields are always the fixed public path the
        # site fetches ("data/channels/x.json"), independent of the data_dir
        # this run was actually given (tests use a temporary directory). The
        # real on-disk file is the same path rooted at data_root instead.
        relative = PurePosixPath(public_path)
        if relative.parts and relative.parts[0] == "data":
            relative = PurePosixPath(*relative.parts[1:])
        return data_root / Path(*relative.parts)

    channels = manifest.get("channels")
    if isinstance(channels, dict):
        for entry in channels.values():
            if not isinstance(entry, dict):
                continue
            url = str(entry.get("url") or "")
            if not url:
                continue
            payload = read(resolve(url))
            count = _channel_count(payload) if payload else 0
            entry["count"] = count
            entry["visible"] = count > 0

    movies = manifest.get("movies")
    if isinstance(movies, dict):
        for entry in movies.values():
            if not isinstance(entry, dict):
                continue
            index_path = str(entry.get("index") or "")
            if not index_path:
                continue
            index_payload = read(resolve(index_path))
            count = _safe_int(index_payload.get("count"), 0, 0) if index_payload else 0
            entry["count"] = count
            entry["visible"] = count > 0

            # total_pages drifts the same way "count" does, and the Pages
            # validator checks it separately: it compares the manifest entry
            # against the number of page entries the index actually lists. A
            # merge that kept one side's manifest line while the index and its
            # page files came from the other side leaves the two disagreeing.
            if index_payload is not None and "pages" in index_payload:
                pages = index_payload.get("pages")
                if isinstance(pages, list):
                    entry["total_pages"] = len(pages)

    for event_key in ("today_match", "upcoming"):
        entry = manifest.get(event_key)
        if not isinstance(entry, dict):
            continue
        url = str(entry.get("url") or "")
        if not url:
            continue
        payload = read(resolve(url))
        # _event_count() trusts the file's own declared "count"/"total" field
        # first, which is exactly the stale value this reconciliation exists
        # to correct. Count the actual list instead.
        count = 0
        if payload:
            for list_key in ("items", "matches", "events"):
                values = payload.get(list_key)
                if isinstance(values, list):
                    count = len(values)
                    break
        entry["count"] = count
        entry["visible"] = count > 0


def refresh_allowed_hosts(
    data_dir: str | Path = "data",
) -> Dict[str, Any]:
    """Rebuild the public playback host allowlist after Series publication."""
    data_root = Path(data_dir)
    data_root.mkdir(parents=True, exist_ok=True)
    return _write_allowed_hosts_file(data_root, _utc_now())



def validate_event_snapshot(
    events_data: Optional[Dict[str, Dict[str, Any]]],
    previous_today_count: int,
    previous_upcoming_count: int,
) -> Tuple[bool, str]:
    """Requirement 15 - the validation stage between a scan and a publish.

    A scan that crashed halfway, or whose sources all timed out, produces a
    structurally valid but empty payload. Writing that over a good production
    snapshot is the one failure mode that takes the whole Live Sports section
    down at once, so the publish is refused instead.
    """
    if events_data is None:
        return True, "no event payload in this scan"
    if not isinstance(events_data, dict):
        return False, "event payload is not a mapping"

    for key in ("today_match", "upcoming"):
        payload = events_data.get(key)
        if payload is None:
            continue
        if not isinstance(payload, dict):
            return False, f"{key} payload is not a mapping"
        items = payload.get("items")
        if not isinstance(items, list):
            return False, f"{key} payload has no item list"
        if int(payload.get("count") or 0) != len(items):
            return False, (
                f"{key} count {payload.get('count')} does not match "
                f"{len(items)} items"
            )
        for item in items:
            if not isinstance(item, dict) or not str(item.get("id") or "").strip():
                return False, f"{key} contains an item with no id"

    today_items = (events_data.get("today_match") or {}).get("items")
    if isinstance(today_items, list) and previous_today_count > 0 and not today_items:
        return False, (
            f"today_match came back empty while {previous_today_count} events "
            "are published - refusing to replace a good snapshot"
        )
    upcoming_items = (events_data.get("upcoming") or {}).get("items")
    if isinstance(upcoming_items, list) and previous_upcoming_count > 0 and not upcoming_items:
        return False, (
            f"upcoming came back empty while {previous_upcoming_count} events "
            "are published - refusing to replace a good snapshot"
        )
    return True, "event snapshot is complete"


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
    global _ACTIVE_PLAYBACK_COLLECTOR

    # Requirement 15. Validate the complete event snapshot first; only a scan
    # that produced a consistent one is allowed to touch production data.
    data_root_probe = Path(data_dir)
    previous_today = _event_count(_load_json_file(data_root_probe / "today-match.json"))
    previous_upcoming = _event_count(_load_json_file(data_root_probe / "upcoming.json"))
    snapshot_ok, snapshot_reason = validate_event_snapshot(
        events_data, previous_today, previous_upcoming
    )
    if not snapshot_ok:
        print(f"   Publish refused (requirement 15): {snapshot_reason}")
        events_data = None

    mode_clean = str(scan_mode or "all").strip().lower()
    mode_aliases = {
        "tv": "channels",
        "channels-discovery": "channels",
        "movies-discovery": "movies",
        "full-audit": "all",
        "today_match": "today",
        "upcoming-targeted": "upcoming",
        "upcoming_targeted": "upcoming",
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
    # 0 keeps the old behaviour (preserve forever). Anything above it releases
    # a category whose preserved payload has gone stale, which is the only way
    # a protected category can ever shrink back to reality.
    maximum_preserved_age_hours = _safe_int(
        failure_config.get("maximum_preserved_age_hours", 0),
        0,
        0,
        24 * 365,
    )

    movie_failure_config = settings.get("movie_failure_protection", {})
    if not isinstance(movie_failure_config, dict):
        movie_failure_config = {}
    movie_drop_protection_enabled = bool(
        movie_failure_config.get("enabled", True)
    )
    movie_maximum_drop_percentage = _safe_int(
        movie_failure_config.get("maximum_drop_percentage", 40),
        40,
        1,
        100,
    )
    movie_minimum_previous_count = _safe_int(
        movie_failure_config.get("minimum_previous_count", 100),
        100,
        1,
        1_000_000,
    )
    movie_migration_id = str(
        movie_failure_config.get("quality_migration_id", "")
    ).strip()

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
    movie_migration_marker = (
        state_root / "migrations" / f"{movie_migration_id}.json"
        if movie_migration_id else None
    )
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
    movie_quality_migration_active = bool(
        movies_data is not None
        and movie_migration_marker is not None
        and not movie_migration_marker.exists()
    )

    data_root.mkdir(parents=True, exist_ok=True)
    reports_root.mkdir(parents=True, exist_ok=True)
    last_good_dir.mkdir(parents=True, exist_ok=True)

    timestamp = _utc_now()
    playback_collector = PlaybackProfileCollector(mode_clean, timestamp)
    _ACTIVE_PLAYBACK_COLLECTOR = playback_collector
    # Requirement 15, corrected. Today/Upcoming, the playback catalogue and its
    # shards, the host allowlist and the manifest are one snapshot: staged here,
    # validated as a set below, and swapped in together or not at all.
    snapshot = SnapshotPublisher(timestamp=timestamp, data_root=data_root)
    manifest_file = data_root / "manifest.json"
    manifest = _ensure_manifest(_load_json_file(manifest_file))
    manifest["updated_at"] = timestamp

    source_errors = _as_dict_list(source_error_items)
    rejected_items = _as_dict_list(rejected_low_quality_items)
    quarantine_items = _as_dict_list(extra_quarantine_items)
    output_safety_items: List[Dict[str, Any]] = []
    movie_output_preserved = False

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
            if str(category_name) == "Sports":
                cards = _apply_pinned_sports_order(cards)

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
                maximum_preserved_age_hours=maximum_preserved_age_hours,
            )
            manifest["channels"][str(category_name)] = manifest_entry

            if drop_error is not None:
                source_errors.append(drop_error)

        cleanup_failed_categories = {
            str(item.get("category") or "")
            for item in source_errors
            if item.get("type") == "sudden_drop_protection"
            and str(item.get("category") or "") in cleanup_categories
        }
        if cleanup_migration_active and not cleanup_failed_categories:
            migration_marker.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write_json(
                migration_marker,
                {
                    "migration": migration_id,
                    "completed_at": timestamp,
                    "categories_rebuilt": sorted(cleanup_categories),
                    "reason": (
                        "Rebuilt TV categories from the final configured "
                        "sources and enforced the minimum resolution policy"
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
                        "One-time TV source/quality migration completed; sudden "
                        "drop protection was bypassed only for configured categories."
                    ),
                }
            )

    # 2. Movies
    if movies_data is not None:
        if not isinstance(movies_data, dict):
            raise ValueError("movies_data must be a dictionary")

        previous_movie_total = _manifest_movie_count(manifest)
        incoming_movie_total = _movie_payload_count(movies_data)
        movie_drop = _drop_percentage(
            previous_movie_total,
            incoming_movie_total,
        )
        protect_previous_movies = bool(
            movie_drop_protection_enabled
            and not movie_quality_migration_active
            and previous_movie_total >= movie_minimum_previous_count
            and movie_drop > movie_maximum_drop_percentage
        )

        if protect_previous_movies:
            movie_output_preserved = True
            warning = _movie_drop_warning(
                previous_count=previous_movie_total,
                incoming_count=incoming_movie_total,
                maximum_drop_percentage=movie_maximum_drop_percentage,
                timestamp=timestamp,
            )
            output_safety_items.append(warning)
            source_errors.append(warning)
        else:
            movie_publish_failed = False
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
                    movie_publish_failed = True
                    source_errors.append(
                        {
                            "type": "movie_publish_error",
                            "category": str(category_name),
                            "error": str(error),
                            "timestamp": timestamp,
                        }
                    )
            if (
                movie_quality_migration_active
                and movie_migration_marker is not None
                and not movie_publish_failed
            ):
                movie_migration_marker.parent.mkdir(parents=True, exist_ok=True)
                _atomic_write_json(
                    movie_migration_marker,
                    {
                        "migration": movie_migration_id,
                        "completed_at": timestamp,
                        "reason": (
                            "Rebuilt movie categories with the minimum "
                            "resolution and final source policy"
                        ),
                    },
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
            for item_key in ("items", "events", "matches"):
                if isinstance(final_payload.get(item_key), list):
                    final_payload[item_key] = _sanitize_public_items(
                        final_payload.get(item_key)
                    )
                    final_payload["count"] = len(final_payload[item_key])
                    break
            final_payload.setdefault("updated_at", timestamp)
            snapshot.stage(
                target_file,
                final_payload,
                order=ORDER_EVENT_FILE,
                kind="event",
            )

            count = _event_count(final_payload)
            manifest[event_key] = {
                "count": count,
                "visible": count > 0,
                "url": public_url,
            }

    # 4. Public Git/Pages playback catalogue, manifest, host allowlist, reports.
    # This deliberately keeps URL/header/DRM configuration in one public data
    # file so all four Workers can resolve playback_id without KV or secrets.
    playback_catalog = merge_public_catalog(
        data_root, playback_collector, snapshot=snapshot
    )
    playback_report = playback_collector.public_report()
    playback_report["total_catalogued_sources"] = _safe_int(
        playback_catalog.get("count"), 0, 0
    )
    _atomic_write_json(reports_root / "playback-profiles.json", playback_report)

    _reconcile_manifest_counts(manifest, data_root, overrides=snapshot.overrides())
    snapshot.stage(manifest_file, manifest, order=ORDER_MANIFEST, kind="manifest")
    allowed_hosts_payload = _write_allowed_hosts_file(
        data_root, timestamp, snapshot=snapshot
    )

    # Validate the staged snapshot as a whole before any of it is published.
    snapshot_ok, snapshot_detail = snapshot.validate()
    if not snapshot_ok:
        # The inconsistency always comes from the event side - the catalogue and
        # the manifest are derived from it - so the event files are dropped and
        # the rest of the snapshot still publishes. Production keeps the last
        # good Today/Upcoming pair rather than gaining a card that cannot play.
        print(f"   Snapshot validation failed (requirement 15): {snapshot_detail}")
        for event_file, _ in snapshot.of_kind("event"):
            snapshot.drop(event_file)
        previous_manifest = _ensure_manifest(_load_json_file(manifest_file))
        for event_key in ("today_match", "upcoming"):
            if isinstance(previous_manifest.get(event_key), dict):
                manifest[event_key] = previous_manifest[event_key]
        _reconcile_manifest_counts(manifest, data_root, overrides=snapshot.overrides())
        snapshot.stage(manifest_file, manifest, order=ORDER_MANIFEST, kind="manifest")
        allowed_hosts_payload = _write_allowed_hosts_file(
            data_root, timestamp, snapshot=snapshot
        )
        source_errors.append(
            {
                "type": "snapshot_validation_failed",
                "error": (
                    "Event snapshot was inconsistent and was not published: "
                    f"{snapshot_detail}"
                ),
                "timestamp": timestamp,
            }
        )
        retry_ok, retry_detail = snapshot.validate()
        if not retry_ok:
            snapshot.abandon()
            print(
                "   Snapshot abandoned entirely; published data is unchanged: "
                f"{retry_detail}"
            )

    commit_summary = snapshot.commit() if snapshot.files else {"files": 0}
    if commit_summary.get("files"):
        print(
            f"   Snapshot {commit_summary.get('id') or commit_summary.get('generation')} "
            f"published to data/snapshots/{commit_summary.get('slot')} "
            f"({commit_summary.get('slot_files')} files), then "
            f"{commit_summary.get('pointer')} switched in one rename"
        )
        if commit_summary.get("carried_forward"):
            print(
                "   Carried forward unchanged: "
                + ", ".join(commit_summary["carried_forward"])
            )

    _atomic_write_json(
        reports_root / "source-errors.json",
        {
            "timestamp": timestamp,
            "count": len(source_errors),
            "errors": redact_public_report(source_errors),
        },
    )
    _atomic_write_json(
        reports_root / "quarantine.json",
        {
            "timestamp": timestamp,
            "count": len(quarantine_items),
            "items": redact_public_report(quarantine_items),
        },
    )
    _atomic_write_json(
        reports_root / "rejected-low-quality.json",
        {
            "timestamp": timestamp,
            "count": len(rejected_items),
            "items": redact_public_report(rejected_items),
        },
    )

    _atomic_write_json(
        reports_root / "output-safety.json",
        {
            "timestamp": timestamp,
            "count": len(output_safety_items),
            "warnings": redact_public_report(output_safety_items),
            "movie_output_preserved": movie_output_preserved,
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

    quarantined_movie_count = sum(
        1
        for item in quarantine_items
        if str(item.get("source_pipeline") or "").strip().casefold() == "movies"
    )
    quarantined_channel_count = max(
        0,
        len(quarantine_items) - quarantined_movie_count,
    )

    bd_report = _load_json_file(reports_root / "bd-verification.json")
    movie_status_counts = bd_report.get("status_counts")
    if not isinstance(movie_status_counts, dict):
        movie_status_counts = {}

    pipeline_performance = _load_json_file(
        reports_root / "pipeline-performance.json"
    )

    scan_status = (
        "completed_with_warnings"
        if source_errors or output_safety_items
        else "completed"
    )

    scan_summary: Dict[str, Any] = {
        "last_scan": timestamp,
        "status": scan_status,
        "mode": mode_clean,
        "source_errors": len(source_errors),
        "quarantined_channels": quarantined_channel_count,
        "quarantined_movies": quarantined_movie_count,
        "rejected_low_quality": len(rejected_items),
        "output_safety_warnings": len(output_safety_items),
        "movie_output_preserved": movie_output_preserved,
        "allowed_playback_hosts": _safe_int(allowed_hosts_payload.get("count"), 0, 0),
        "catalogued_playback_sources": len(playback_collector.records),
        "total_playback_catalogue_sources": _safe_int(
            playback_catalog.get("count"), 0, 0
        ),
        "movie_verification_status_counts": movie_status_counts,
        "pipeline_performance": pipeline_performance,
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
    _atomic_write_json(
        reports_root / f"scan-summary-{mode_clean}.json",
        scan_summary,
    )
    _atomic_write_json(
        reports_root / f"source-errors-{mode_clean}.json",
        {
            "timestamp": timestamp,
            "mode": mode_clean,
            "count": len(source_errors),
            "errors": redact_public_report(source_errors),
        },
    )
    _atomic_write_json(
        reports_root / f"output-safety-{mode_clean}.json",
        {
            "timestamp": timestamp,
            "mode": mode_clean,
            "count": len(output_safety_items),
            "warnings": redact_public_report(output_safety_items),
            "movie_output_preserved": movie_output_preserved,
        },
    )

    notifications = settings.get("notifications", {})
    if not isinstance(notifications, dict):
        notifications = {}

    telegram_enabled = bool(
        notifications.get("telegram_enabled", True)
    )

    telegram_sent = False
    if telegram_enabled:
        safe_status = html.escape(scan_status)
        safe_mode = html.escape(mode_clean)

        title_by_mode = {
            "channels": "📺 <b>TV CHANNEL SCAN COMPLETED</b>",
            "movies": "🎬 <b>MOVIE SCAN COMPLETED</b>",
            "events": "⚽ <b>EVENT SCAN COMPLETED</b>",
            "today": "⚽ <b>TODAY MATCH SCAN COMPLETED</b>",
            "upcoming": "🗓 <b>UPCOMING MATCH SCAN COMPLETED</b>",
            "all": "📡 <b>FULL SCAN COMPLETED</b>",
        }

        message_lines = [
            title_by_mode.get(
                mode_clean,
                "📡 <b>LIVE SIGNAL SCAN COMPLETED</b>",
            ),
            "",
            f"<b>Mode:</b> {safe_mode}",
            f"<b>Status:</b> {safe_status}",
            f"<b>Updated At:</b> {html.escape(timestamp)}",
        ]

        if mode_clean == "channels":
            message_lines.extend(
                [
                    f"<b>TV Channels:</b> {total_channels}",
                    (
                        "<b>Quarantined Channels:</b> "
                        f"{quarantined_channel_count}"
                    ),
                    (
                        "<b>Rejected Low Quality:</b> "
                        f"{len(rejected_items)}"
                    ),
                ]
            )
        elif mode_clean == "movies":
            visible_movie_categories = sum(
                1
                for entry in manifest.get("movies", {}).values()
                if isinstance(entry, dict)
                and _safe_int(entry.get("count"), 0, 0) > 0
            )
            message_lines.extend(
                [
                    f"<b>Movies:</b> {total_movies}",
                    (
                        "<b>Visible Movie Categories:</b> "
                        f"{visible_movie_categories}"
                    ),
                    (
                        "<b>Rejected Low Quality:</b> "
                        f"{len(rejected_items)}"
                    ),
                    (
                        "<b>Verified Global:</b> "
                        f"{_safe_int(movie_status_counts.get('verified_global'), 0, 0)}"
                    ),
                    (
                        "<b>Verified Proxy:</b> "
                        f"{_safe_int(movie_status_counts.get('verified_proxy'), 0, 0)}"
                    ),
                    (
                        "<b>Geo Pending:</b> "
                        f"{_safe_int(movie_status_counts.get('geo_pending'), 0, 0)}"
                    ),
                    (
                        "<b>Retryable Pending:</b> "
                        f"{_safe_int(movie_status_counts.get('retryable_pending'), 0, 0)}"
                    ),
                    (
                        "<b>Host Deferred:</b> "
                        f"{_safe_int(movie_status_counts.get('host_deferred'), 0, 0)}"
                    ),
                    (
                        "<b>404 Quarantined:</b> "
                        f"{quarantined_movie_count}"
                    ),
                    (
                        "<b>Output Safety Warnings:</b> "
                        f"{len(output_safety_items)}"
                    ),
                ]
            )
        elif mode_clean == "events":
            message_lines.extend(
                [
                    (
                        "<b>Today Matches:</b> "
                        f"{scan_summary['totals']['today_match']}"
                    ),
                    (
                        "<b>Upcoming Matches:</b> "
                        f"{scan_summary['totals']['upcoming']}"
                    ),
                ]
            )
        elif mode_clean == "today":
            message_lines.append(
                "<b>Today Matches:</b> "
                f"{scan_summary['totals']['today_match']}"
            )
        elif mode_clean == "upcoming":
            message_lines.append(
                "<b>Upcoming Matches:</b> "
                f"{scan_summary['totals']['upcoming']}"
            )
        else:
            message_lines.extend(
                [
                    f"<b>TV Channels:</b> {total_channels}",
                    f"<b>Movies:</b> {total_movies}",
                    (
                        "<b>Today Matches:</b> "
                        f"{scan_summary['totals']['today_match']}"
                    ),
                    (
                        "<b>Upcoming Matches:</b> "
                        f"{scan_summary['totals']['upcoming']}"
                    ),
                    (
                        "<b>Quarantined Channels:</b> "
                        f"{quarantined_channel_count}"
                    ),
                    (
                        "<b>Rejected Low Quality:</b> "
                        f"{len(rejected_items)}"
                    ),
                ]
            )

        message_lines.append(
            f"<b>Source/Stream Warnings:</b> {len(source_errors)}"
        )
        message = "\n".join(message_lines)
        telegram_sent = send_telegram_alert(message)

    scan_summary["telegram_sent"] = telegram_sent
    _atomic_write_json(
        reports_root / "scan-summary.json",
        scan_summary,
    )

    _ACTIVE_PLAYBACK_COLLECTOR = None

    return scan_summary


if __name__ == "__main__":
    summary = publish_scan_outputs()
    print(
        "Output publish completed: "
        f"status={summary['status']}, "
        f"errors={summary['source_errors']}"
    )
