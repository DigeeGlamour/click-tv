"""Click TV manual Series publisher.

The owner's remote category repository stores Movies and Series together in TXT
files. ``scanner.movies`` downloads the latest repository snapshot once during a
``movies``/``all`` scan, keeps only TXT files, sends Movie blocks through the
existing Movie pipeline and writes Series blocks to
``working/manual-series-catalog.json``.  This module validates that staged
catalogue and atomically publishes lazy Series/Season/Episode JSON under
``data/series``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple
from urllib.parse import urlparse

from scanner.playback_profiles import PlaybackProfileCollector, merge_public_catalog

VALID_SERIES_CATEGORIES: Tuple[str, ...] = (
    "Bangla",
    "Hindi",
    "English",
    "Dubbed",
    "South Indian",
    "Premium",
    "Mix",
)

CATEGORY_SLUGS: Dict[str, str] = {
    "Bangla": "bangla",
    "Hindi": "hindi",
    "English": "english",
    "Dubbed": "dubbed",
    "South Indian": "south-indian",
    "Premium": "premium",
    "Mix": "mix",
}

_CATEGORY_ALIASES = {
    re.sub(r"[^a-z0-9]+", "", category.casefold()): category
    for category in VALID_SERIES_CATEGORIES
}
_CATEGORY_ALIASES.update(
    {
        "bengali": "Bangla",
        "banglamovies": "Bangla",
        "hindimovies": "Hindi",
        "englishmovies": "English",
        "hollywood": "English",
        "hindidubbed": "Dubbed",
        "bangladubbed": "Dubbed",
        "tamil": "South Indian",
        "telugu": "South Indian",
        "malayalam": "South Indian",
        "kannada": "South Indian",
        "disneyhotstar": "Premium",
        "disneyplushotstar": "Premium",
        "hotstar": "Premium",
        "ott": "Premium",
    }
)

SCHEMA_VERSION = 1
MAX_BACKUPS = 5
DEFAULT_CATALOG_PATH = "working/manual-series-catalog.json"
DEFAULT_OUTPUT_ROOT = "data/series"
DEFAULT_REPORT_PATH = "reports/manual-series.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any, fallback: str = "") -> str:
    result = str(value if value is not None else "").strip()
    return result or fallback


def _int(value: Any, fallback: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _slug(value: Any, fallback: str = "item") -> str:
    result = re.sub(r"[^a-z0-9]+", "-", _text(value).casefold()).strip("-")
    return result or fallback


def _canonical_category(value: Any) -> str:
    key = re.sub(r"[^a-z0-9]+", "", _text(value).casefold())
    return _CATEGORY_ALIASES.get(key, "Mix")


def _valid_url(value: Any) -> bool:
    text = _text(value)
    if not text:
        return False
    try:
        parsed = urlparse(text)
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _stream_type(url: str) -> str:
    path = (urlparse(url).path or "").casefold()
    if path.endswith(".m3u8"):
        return "hls"
    if path.endswith(".mpd"):
        return "dash"
    if path.endswith((".ts", ".m2ts")):
        return "mpegts"
    return "media"


def _quality_height(value: Any) -> int:
    text = _text(value).casefold()
    match = re.search(r"(?:^|\D)(2160|1440|1080|720|576|540|480|360|240)(?:p|\D|$)", text)
    if match:
        return int(match.group(1))
    if "4k" in text or "uhd" in text:
        return 2160
    return 0


def _source(raw: Any, position: int) -> Dict[str, Any] | None:
    if isinstance(raw, str):
        raw = {"url": raw}
    if not isinstance(raw, dict):
        return None
    url = _text(raw.get("url") or raw.get("link") or raw.get("stream_url"))
    if not _valid_url(url):
        return None
    label = _text(raw.get("label") or raw.get("resolution") or raw.get("quality"), f"Source {position}")
    height = _int(raw.get("resolution_height") or raw.get("height"), _quality_height(label + " " + url))
    source: Dict[str, Any] = {
        "url": url,
        "label": label,
        "resolution": _text(raw.get("resolution"), label),
        "resolution_height": height,
        "header_profile": _text(raw.get("header_profile"), "android_tv"),
        "proxy_mode": "direct_first",
        "stream_type": _text(raw.get("stream_type"), _stream_type(url)),
        "requires_headers": bool(raw.get("requires_headers", False)),
        "inherit_manifest_query": bool(raw.get("inherit_manifest_query", False)),
        "verification_status": "manual_trusted",
        "manual_source": True,
        "publish_allowed": True,
    }
    for key in ("codec", "edition", "language", "provider", "audio_url", "audio_codec"):
        if raw.get(key) not in (None, ""):
            source[key] = raw[key]
    headers = raw.get("headers")
    if isinstance(headers, dict) and headers:
        source["headers"] = {str(k): str(v) for k, v in headers.items() if v not in (None, "")}
    return source


def _quality_sort_key(source: Mapping[str, Any]) -> Tuple[int, int, int]:
    """Start with compatible FHD, retain 4K and codecs as selectable backups."""
    height = _int(source.get("resolution_height"), 0)
    text = f"{source.get('label', '')} {source.get('codec', '')} {source.get('url', '')}".casefold()
    codec_penalty = 2 if "av1" in text else 1 if any(token in text for token in ("hevc", "h265", "x265")) else 0
    height_penalty = 0 if height == 1080 else 1 if height == 720 else 2 if height >= 1440 else 1
    https_penalty = 0 if _text(source.get("url")).casefold().startswith("https://") else 1
    return codec_penalty, height_penalty, https_penalty


def _normalize_sources(raw_episode: Mapping[str, Any]) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    raw_links = raw_episode.get("links")
    if not isinstance(raw_links, list):
        raw_links = []
    primary_url = raw_episode.get("url") or raw_episode.get("link")
    if primary_url:
        raw_links = [raw_episode, *raw_links]

    seen: set[str] = set()
    sources: List[Dict[str, Any]] = []
    for position, raw in enumerate(raw_links, start=1):
        normalized = _source(raw, position)
        if not normalized:
            continue
        if _int(normalized.get("resolution_height"), 0) < 720:
            continue
        identity = json.dumps(
            {
                "url": normalized.get("url"),
                "headers": normalized.get("headers", {}),
                "drm": normalized.get("drm", {}),
                "header_profile": normalized.get("header_profile", ""),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if identity in seen:
            continue
        seen.add(identity)
        sources.append(normalized)

    if not sources:
        raise ValueError("Episode has no valid http/https source")
    sources.sort(key=_quality_sort_key)
    sources = sources[: MAX_BACKUPS + 1]
    return sources[0], sources[1:]


def _normalize_episode(
    raw: Mapping[str, Any],
    *,
    series_id: str,
    series_name: str,
    category: str,
    season_number: int,
    ordinal: int,
) -> Dict[str, Any]:
    label = _text(raw.get("episode_label") or raw.get("episode_title") or raw.get("title"), f"Episode {ordinal:02d}")
    episode_key = _slug(raw.get("episode_key") or label, f"episode-{ordinal:03d}")
    primary, backups = _normalize_sources(raw)
    episode_id = _slug(raw.get("id"), f"{series_id}-s{season_number:02d}-{episode_key}")

    episode: Dict[str, Any] = {
        "id": episode_id,
        "name": f"{series_name} — {label}",
        "title": label,
        "episode_title": label,
        "episode_label": label,
        "episode_key": episode_key,
        "episode_number": ordinal,
        "number": ordinal,
        "series_id": series_id,
        "series_name": series_name,
        "season_number": season_number,
        "category": category,
        "content_kind": "episode",
        "url": primary["url"],
        "backups": backups,
        "available_link_count": 1 + len(backups),
        "manual_source": True,
        "manual_position": ordinal,
        "verification_status": "manual_trusted",
        "verification_mode": "manual",
        "skip_verification": True,
        "verified": True,
        "is_valid": True,
        "publish_allowed": True,
        "enabled": raw.get("enabled") is not False,
        "proxy_mode": "direct_first",
        "header_profile": primary.get("header_profile", "android_tv"),
        "stream_type": primary.get("stream_type", "media"),
        "requires_headers": bool(primary.get("requires_headers", False)),
        "inherit_manifest_query": bool(primary.get("inherit_manifest_query", False)),
        "resolution": primary.get("resolution", ""),
        "resolution_height": primary.get("resolution_height", 0),
    }
    for key in ("codec", "edition", "language", "provider", "headers", "audio_url", "audio_codec"):
        if primary.get(key) not in (None, "", {}):
            episode[key] = primary[key]
    for key in ("thumbnail", "logo", "duration_seconds", "duration", "release_date", "description"):
        if raw.get(key) not in (None, ""):
            episode[key] = raw[key]
    return episode


def _normalize_series(raw: Mapping[str, Any], position: int) -> Dict[str, Any]:
    if raw.get("enabled") is False:
        raise ValueError("Series is disabled")
    name = _text(raw.get("name") or raw.get("show_name") or raw.get("title"))
    if not name:
        raise ValueError("Series name missing")
    category = _canonical_category(raw.get("category"))
    year = _int(raw.get("year"), 0)
    series_id = _slug(raw.get("id"), f"{_slug(name, 'series')}-{year or 'unknown'}")
    raw_seasons = raw.get("seasons")
    if not isinstance(raw_seasons, list) or not raw_seasons:
        raise ValueError(f"Series has no seasons: {name}")

    seasons: List[Dict[str, Any]] = []
    episode_payloads: Dict[int, List[Dict[str, Any]]] = {}
    seen_seasons: set[int] = set()
    total_episodes = 0

    for season_position, raw_season in enumerate(raw_seasons, start=1):
        if not isinstance(raw_season, dict):
            continue
        number = _int(raw_season.get("number"), season_position)
        if number in seen_seasons:
            raise ValueError(f"Duplicate Season {number}: {name}")
        seen_seasons.add(number)
        raw_episodes = raw_season.get("episodes")
        if not isinstance(raw_episodes, list) or not raw_episodes:
            continue
        episodes: List[Dict[str, Any]] = []
        seen_episode_keys: set[str] = set()
        for episode_position, raw_episode in enumerate(raw_episodes, start=1):
            if not isinstance(raw_episode, dict) or raw_episode.get("enabled") is False:
                continue
            episode = _normalize_episode(
                raw_episode,
                series_id=series_id,
                series_name=name,
                category=category,
                season_number=number,
                ordinal=episode_position,
            )
            key = _text(episode.get("episode_key"))
            if key in seen_episode_keys:
                key = f"{key}-{episode_position:03d}"
                episode["episode_key"] = key
                episode["id"] = f"{episode['id']}-{episode_position:03d}"
            seen_episode_keys.add(key)
            episodes.append(episode)
        if not episodes:
            continue
        title = _text(raw_season.get("title"), "Specials" if number == 0 else f"Season {number}")
        seasons.append({"number": number, "title": title, "count": len(episodes)})
        episode_payloads[number] = episodes
        total_episodes += len(episodes)

    if not seasons:
        raise ValueError(f"Series has no playable episodes: {name}")
    seasons.sort(key=lambda item: item["number"])
    default_season = _int(raw.get("default_season"), seasons[0]["number"])
    if default_season not in episode_payloads:
        default_season = seasons[0]["number"]
    latest_season = seasons[-1]["number"]
    latest_episode = episode_payloads[latest_season][-1]

    return {
        "id": series_id,
        "name": name,
        "category": category,
        "slug": CATEGORY_SLUGS[category],
        "year": year,
        "logo": _text(raw.get("logo") or raw.get("poster") or raw.get("image")),
        "backdrop": _text(raw.get("backdrop") or raw.get("banner")),
        "description": _text(raw.get("description") or raw.get("overview")),
        "status": _text(raw.get("status"), "ongoing").casefold(),
        "default_season": default_season,
        "total_seasons": len([season for season in seasons if season["number"] > 0]),
        "total_episodes": total_episodes,
        "latest_episode": _text(latest_episode.get("episode_label")),
        "updated_at": _text(raw.get("updated_at"), _utc_now()),
        "manual_source": True,
        "manual_position": position,
        "verification_status": "manual_trusted",
        "publish_allowed": True,
        "source_path": _text(raw.get("source_path")),
        "source_revision": _text(raw.get("source_revision")),
        "seasons": seasons,
        "episode_payloads": episode_payloads,
    }


def _merge_duplicate_series(items: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    merged: Dict[Tuple[str, int, str], Dict[str, Any]] = {}
    for position, raw in enumerate(items, start=1):
        normalized = _normalize_series(raw, position)
        identity = (normalized["name"].casefold(), normalized["year"], normalized["category"])
        existing = merged.get(identity)
        if existing is None:
            merged[identity] = normalized
            continue
        by_number = {season["number"]: dict(season) for season in existing["seasons"]}
        for season in normalized["seasons"]:
            number = season["number"]
            current_episodes = existing["episode_payloads"].setdefault(number, [])
            seen = {_text(ep.get("episode_key")) for ep in current_episodes}
            for episode in normalized["episode_payloads"].get(number, []):
                key = _text(episode.get("episode_key"))
                if key in seen:
                    current = next(ep for ep in current_episodes if _text(ep.get("episode_key")) == key)
                    urls = {_text(current.get("url")), *[_text(b.get("url")) for b in current.get("backups", []) if isinstance(b, dict)]}
                    for candidate in [episode, *episode.get("backups", [])]:
                        if not isinstance(candidate, dict):
                            continue
                        url = _text(candidate.get("url"))
                        if url and url not in urls and len(current.get("backups", [])) < MAX_BACKUPS:
                            current.setdefault("backups", []).append(dict(candidate))
                            urls.add(url)
                    current["available_link_count"] = 1 + len(current.get("backups", []))
                else:
                    current_episodes.append(episode)
                    seen.add(key)
            by_number[number] = {"number": number, "title": season["title"], "count": len(current_episodes)}
        existing["seasons"] = sorted(by_number.values(), key=lambda season: season["number"])
        existing["total_seasons"] = len([season for season in existing["seasons"] if season["number"] > 0])
        existing["total_episodes"] = sum(len(value) for value in existing["episode_payloads"].values())
        if not existing.get("logo") and normalized.get("logo"):
            existing["logo"] = normalized["logo"]
    return list(merged.values())


def prepare_manual_series(
    project_root: str | Path | None = None,
    catalog_path: str | Path = DEFAULT_CATALOG_PATH,
) -> Dict[str, Any]:
    root = Path(project_root or Path.cwd()).resolve()
    source = Path(catalog_path)
    if not source.is_absolute():
        source = root / source
    if not source.exists():
        raise FileNotFoundError(f"Manual Series staging catalogue missing: {source}")
    with source.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise ValueError("Manual Series staging catalogue must contain an items list")
    normalized = _merge_duplicate_series(item for item in payload["items"] if isinstance(item, dict))
    normalized.sort(
        key=lambda item: (
            1 if _int(item.get("year"), 0) == 0 else 0,
            -_int(item.get("year"), 0),
            0 if item.get("manual_source") else 1,
            _int(item.get("manual_position"), 999999),
            _text(item.get("name")).casefold(),
        )
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "prepared_at": _utc_now(),
        "source": str(source.relative_to(root) if source.is_relative_to(root) else source),
        "repository_snapshots": payload.get("repository_snapshots", []),
        "items": normalized,
        "series": len(normalized),
        "episodes": sum(_int(item.get("total_episodes"), 0) for item in normalized),
    }


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        with temp.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink(missing_ok=True)


def _summary(series: Mapping[str, Any]) -> Dict[str, Any]:
    result = {
        key: series.get(key)
        for key in (
            "id", "name", "category", "year", "logo", "backdrop", "description",
            "status", "default_season", "total_seasons", "total_episodes",
            "latest_episode", "updated_at", "manual_source", "manual_position",
            "verification_status", "publish_allowed", "source_revision",
        )
    }
    result.update(
        {
            "content_kind": "series",
            "series_manifest": f"data/series/{series['slug']}/{series['id']}/index.json",
        }
    )
    return {key: value for key, value in result.items() if value not in (None, "")}


def _build_tree(
    root: Path,
    prepared: Mapping[str, Any],
    generated_at: str,
    playback_collector: PlaybackProfileCollector,
) -> Dict[str, Any]:
    category_items: Dict[str, List[Dict[str, Any]]] = {category: [] for category in VALID_SERIES_CATEGORIES}
    for series in prepared.get("items", []):
        category = _text(series["category"])
        slug = _text(series["slug"])
        series_id = _text(series["id"])
        series_root = root / slug / series_id
        series_root.mkdir(parents=True, exist_ok=True)
        seasons_index: List[Dict[str, Any]] = []
        for season in series["seasons"]:
            number = _int(season["number"])
            path = f"data/series/{slug}/{series_id}/season-{number:02d}.json"
            seasons_index.append({**season, "path": path})
            _atomic_write_json(
                series_root / f"season-{number:02d}.json",
                {
                    "schema_version": SCHEMA_VERSION,
                    "generated_at": generated_at,
                    "series_id": series_id,
                    "series_name": series["name"],
                    "category": category,
                    "season_number": number,
                    "season_title": season["title"],
                    "count": len(series["episode_payloads"].get(number, [])),
                    "items": [
                        playback_collector.sanitize_item(
                            episode,
                            f"series:{series_id}:season:{number}:episode:{index}",
                        )
                        for index, episode in enumerate(
                            series["episode_payloads"].get(number, [])
                        )
                    ],
                },
            )
        _atomic_write_json(
            series_root / "index.json",
            {
                "schema_version": SCHEMA_VERSION,
                "generated_at": generated_at,
                **_summary(series),
                "default_season": series["default_season"],
                "seasons": seasons_index,
            },
        )
        category_items[category].append(_summary(series))

    categories_manifest: Dict[str, Dict[str, Any]] = {}
    for category in VALID_SERIES_CATEGORIES:
        slug = CATEGORY_SLUGS[category]
        items = category_items[category]
        items.sort(
            key=lambda item: (
                1 if _int(item.get("year"), 0) == 0 else 0,
                -_int(item.get("year"), 0),
                0 if item.get("manual_source") else 1,
                _int(item.get("manual_position"), 999999),
                _text(item.get("name")).casefold(),
            )
        )
        index_path = f"data/series/{slug}/index.json"
        _atomic_write_json(
            root / slug / "index.json",
            {
                "schema_version": SCHEMA_VERSION,
                "generated_at": generated_at,
                "category": category,
                "slug": slug,
                "count": len(items),
                "items": items,
            },
        )
        categories_manifest[category] = {
            "slug": slug,
            "count": len(items),
            "visible": len(items) > 0,
            "index": index_path,
        }

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "total_series": sum(len(items) for items in category_items.values()),
        "total_episodes": _int(prepared.get("episodes"), 0),
        "repository_snapshots": prepared.get("repository_snapshots", []),
        "categories": categories_manifest,
    }
    _atomic_write_json(root / "manifest.json", manifest)
    return manifest


def publish_prepared_series(
    prepared: Mapping[str, Any],
    project_root: str | Path | None = None,
    output_root: str | Path = DEFAULT_OUTPUT_ROOT,
    report_path: str | Path = DEFAULT_REPORT_PATH,
) -> Dict[str, Any]:
    root = Path(project_root or Path.cwd()).resolve()
    destination = Path(output_root)
    if not destination.is_absolute():
        destination = root / destination
    temp = destination.with_name(f".{destination.name}.build.{os.getpid()}.{time.time_ns()}")
    backup = destination.with_name(f".{destination.name}.previous.{os.getpid()}.{time.time_ns()}")
    shutil.rmtree(temp, ignore_errors=True)
    temp.mkdir(parents=True, exist_ok=False)
    generated_at = _utc_now()
    playback_collector = PlaybackProfileCollector("series", generated_at)
    try:
        manifest = _build_tree(temp, prepared, generated_at, playback_collector)
        if destination.exists():
            os.replace(destination, backup)
        os.replace(temp, destination)
        shutil.rmtree(backup, ignore_errors=True)
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        if backup.exists() and not destination.exists():
            os.replace(backup, destination)
        raise

    if playback_collector.records:
        catalog = merge_public_catalog(root / "data", playback_collector)

        profile_report_path = root / "reports" / "playback-profiles.json"
        _atomic_write_json(profile_report_path, {
            "schema_version": 1,
            "generated_at": generated_at,
            "scan_mode": "series",
            "catalogued_sources": len(playback_collector.records),
            "total_catalogued_sources": int(catalog.get("count") or 0),
            "catalog": "data/playback-sources.json",
            "storage": "public_git_pages_json",
            "contains_playback_credentials": True,
        })

    result = {
        "status": "success",
        "generated_at": generated_at,
        "output": str(destination.relative_to(root) if destination.is_relative_to(root) else destination),
        "series": manifest["total_series"],
        "episodes": manifest["total_episodes"],
        "categories": {key: value["count"] for key, value in manifest["categories"].items()},
        "repository_snapshots": prepared.get("repository_snapshots", []),
    }
    report = Path(report_path)
    if not report.is_absolute():
        report = root / report
    _atomic_write_json(report, result)
    return result


def validate_manual_series(
    project_root: str | Path | None = None,
    input_path: str | Path = DEFAULT_CATALOG_PATH,
) -> Dict[str, Any]:
    prepared = prepare_manual_series(project_root=project_root, catalog_path=input_path)
    return {"status": "valid", **{key: prepared[key] for key in ("source", "series", "episodes", "repository_snapshots")}}


def publish_manual_series(
    project_root: str | Path | None = None,
    input_path: str | Path = DEFAULT_CATALOG_PATH,
    output_root: str | Path = DEFAULT_OUTPUT_ROOT,
) -> Dict[str, Any]:
    prepared = prepare_manual_series(project_root=project_root, catalog_path=input_path)
    return publish_prepared_series(prepared, project_root=project_root, output_root=output_root)


__all__ = [
    "CATEGORY_SLUGS",
    "VALID_SERIES_CATEGORIES",
    "prepare_manual_series",
    "publish_prepared_series",
    "publish_manual_series",
    "validate_manual_series",
]
