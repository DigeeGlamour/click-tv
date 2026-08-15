#!/usr/bin/env python3
"""Collapse canonical-name/route channel duplicates while preserving every route."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scanner.merger import _movie_identity_key
from scanner.movies import CATEGORY_SLUGS, VALID_MOVIE_CATEGORIES, _merge_preferred_movie, paginate_movie_list
from scanner.normalizer import Normalizer


CHANNEL_DIRS = (ROOT / "data" / "channels", ROOT / "state" / "last-good")
BACKUP_FIELDS = (
    "url", "playback_id", "header_profile", "proxy_mode", "stream_type",
    "requires_headers", "inherit_manifest_query", "verification_mode",
    "verification_status", "verification_badge", "verified", "publish_allowed",
    "source_id", "resolution", "resolution_height",
)
NORMALIZER = Normalizer()


def _atomic_write(path: Path, payload: Any) -> None:
    temp = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(temp, path)


def _url_key(item: dict[str, Any]) -> str:
    return str(item.get("url") or "").split("|", 1)[0].strip().casefold()


def _backup_from(item: dict[str, Any]) -> dict[str, Any]:
    backup = {"name": str(item.get("name") or "Duplicate route")}
    for field in BACKUP_FIELDS:
        if item.get(field) not in (None, ""):
            backup[field] = item[field]
    return backup


def _backup_identity(item: dict[str, Any]) -> tuple[str, str]:
    return (_url_key(item), str(item.get("playback_id") or ""))


def _canonical_name(item: dict[str, Any]) -> str:
    clean = NORMALIZER.clean_title(str(item.get("name") or item.get("title") or ""))
    return " ".join(clean.casefold().split())


def _route_keys(item: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    for route in [item, *[entry for entry in item.get("backups", []) if isinstance(entry, dict)]]:
        url = _url_key(route)
        playback_id = str(route.get("playback_id") or "").strip()
        if url:
            keys.add(f"url:{url}")
        if playback_id:
            keys.add(f"pid:{playback_id}")
    return keys


def _owner_score(item: dict[str, Any]) -> tuple[int, int, int, int]:
    status = str(item.get("verification_status") or "").casefold()
    return (
        1 if item.get("verified") is True else 0,
        1 if status in {"verified_global", "verified_proxy", "verified_bd"} else 0,
        int(item.get("resolution_height") or 0),
        1 if _url_key(item).startswith("https://") else 0,
    )


def _merge_group(group: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    owner = dict(max(group, key=_owner_score))
    removed = [item for item in group if item is not max(group, key=_owner_score)]
    routes = [dict(entry) for entry in owner.get("backups", []) if isinstance(entry, dict)]
    identities = {_backup_identity(owner), *(_backup_identity(entry) for entry in routes)}
    for item in group:
        if item == owner:
            continue
        duplicate_route = _backup_from(item)
        if _backup_identity(duplicate_route) not in identities:
            routes.append(duplicate_route)
            identities.add(_backup_identity(duplicate_route))
        for entry in item.get("backups", []):
            if isinstance(entry, dict) and _backup_identity(entry) not in identities:
                routes.append(dict(entry))
                identities.add(_backup_identity(entry))
    owner["backups"] = routes
    owner["available_link_count"] = 1 + len(routes)
    owner["source_ids"] = list(dict.fromkeys(
        value
        for item in group
        for value in [*(item.get("source_ids") or []), str(item.get("source_id") or "")]
        if value
    ))
    provenance: list[dict[str, Any]] = []
    known_sources: set[str] = set()
    for item in group:
        for entry in item.get("source_provenance", []):
            if not isinstance(entry, dict):
                continue
            source_id = str(entry.get("source_id") or "")
            if source_id not in known_sources:
                provenance.append(entry)
                known_sources.add(source_id)
    owner["source_provenance"] = provenance
    return owner, removed


def dedupe_payload(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = payload.get("channels")
    if not isinstance(items, list):
        return []

    cards = [dict(item) for item in items if isinstance(item, dict)]
    parent = list(range(len(cards)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    key_owner: dict[str, int] = {}
    for index, item in enumerate(cards):
        keys = _route_keys(item)
        canonical = _canonical_name(item)
        if canonical:
            keys.add(f"name:{canonical}")
        for key in keys:
            if key in key_owner:
                union(index, key_owner[key])
            else:
                key_owner[key] = index

    groups: dict[int, list[tuple[int, dict[str, Any]]]] = {}
    for index, item in enumerate(cards):
        groups.setdefault(find(index), []).append((index, item))

    visible: list[tuple[int, dict[str, Any]]] = []
    removed: list[dict[str, Any]] = []
    for entries in groups.values():
        group_items = [item for _, item in entries]
        owner, group_removed = _merge_group(group_items)
        visible.append((min(index for index, _ in entries), owner))
        removed.extend(group_removed)

    if removed:
        payload["channels"] = [item for _, item in sorted(visible)]
        payload["count"] = len(payload["channels"])
        _atomic_write(path, payload)
    return removed


def _movie_owner_score(item: dict[str, Any]) -> tuple[int, int, int, int, int]:
    return (
        1 if item.get("manual_source") is True else 0,
        -int(item.get("manual_source_tier") or 99),
        1 if item.get("verified") is True else 0,
        int(item.get("resolution_height") or 0),
        len(str(item.get("name") or "")),
    )


def dedupe_movies() -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    source_files: list[str] = []
    for category in VALID_MOVIE_CATEGORIES:
        category_dir = ROOT / "data" / "movies" / CATEGORY_SLUGS[category]
        index_path = category_dir / "index.json"
        if not index_path.is_file():
            continue
        index = json.loads(index_path.read_text(encoding="utf-8"))
        for entry in index.get("pages") or []:
            page_path = ROOT / str(entry.get("path") or "")
            if not page_path.is_file():
                continue
            payload = json.loads(page_path.read_text(encoding="utf-8"))
            for item in payload.get("items") or []:
                if isinstance(item, dict):
                    cards.append(dict(item))
                    source_files.append(str(page_path.relative_to(ROOT)))

    parent = list(range(len(cards)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    key_owner: dict[str, int] = {}
    for index, item in enumerate(cards):
        keys = {f"route:{key}" for key in _route_keys(item)}
        identity = _movie_identity_key(item)
        if identity:
            keys.add(f"movie:{identity}")
        for key in keys:
            if key in key_owner:
                union(index, key_owner[key])
            else:
                key_owner[key] = index

    groups: dict[int, list[int]] = {}
    for index in range(len(cards)):
        groups.setdefault(find(index), []).append(index)

    visible: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    for indexes in groups.values():
        owner_index = max(indexes, key=lambda index: _movie_owner_score(cards[index]))
        merged = dict(cards[owner_index])
        for index in indexes:
            if index == owner_index:
                continue
            merged = _merge_preferred_movie(merged, cards[index])
            removed.append({"kind": "movie", "file": source_files[index], "record": cards[index]})
        visible.append(merged)

    grouped: dict[str, list[dict[str, Any]]] = {category: [] for category in VALID_MOVIE_CATEGORIES}
    for movie in visible:
        category = str(movie.get("category") or "Mix")
        if category not in grouped:
            category = "Mix"
            movie["category"] = category
        grouped[category].append(movie)

    manifest_path = ROOT / "data" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for category, movies in grouped.items():
        result = paginate_movie_list(movies, category, page_size=100)
        category_dir = ROOT / "data" / "movies" / CATEGORY_SLUGS[category]
        expected_pages = set(result["page_contents"])
        for old_page in category_dir.glob("page-*.json"):
            if old_page.name not in expected_pages:
                old_page.unlink()
        _atomic_write(category_dir / "index.json", result["index"])
        for filename, payload in result["page_contents"].items():
            _atomic_write(category_dir / filename, payload)
        for entry in (manifest.get("movies") or {}).values():
            if isinstance(entry, dict) and Path(str(entry.get("index") or "")).parent.name == CATEGORY_SLUGS[category]:
                entry["count"] = result["index"]["count"]
                entry["total_pages"] = result["index"]["total_pages"]
    _atomic_write(manifest_path, manifest)
    return removed


def main() -> int:
    removed_records: list[dict[str, Any]] = []
    data_counts: dict[str, int] = {}
    for directory in CHANNEL_DIRS:
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.json")):
            removed = dedupe_payload(path)
            if directory.name == "channels" and removed:
                removed_records.extend({"file": path.name, "record": item} for item in removed)
            if directory.name == "channels":
                payload = json.loads(path.read_text(encoding="utf-8"))
                data_counts[path.stem] = len(payload.get("channels") or [])

    movie_removed_records = dedupe_movies()

    manifest_path = ROOT / "data" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for name, entry in (manifest.get("channels") or {}).items():
        if isinstance(entry, dict):
            slug = Path(str(entry.get("url") or "")).stem
            if slug in data_counts:
                entry["count"] = data_counts[slug]
    _atomic_write(manifest_path, manifest)

    report_path = ROOT / "reports" / "public-dedupe.json"
    previous_records: list[dict[str, Any]] = []
    if report_path.is_file():
        previous = json.loads(report_path.read_text(encoding="utf-8"))
        if isinstance(previous.get("records"), list):
            previous_records = [
                ({"kind": "channel", **entry} if isinstance(entry, dict) and not entry.get("kind") else entry)
                for entry in previous["records"]
            ]
    combined_records = [
        *previous_records,
        *({"kind": "channel", **entry} for entry in removed_records),
        *movie_removed_records,
    ]
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "hidden_duplicate_cards": len(combined_records),
        "records": combined_records,
    }
    _atomic_write(report_path, report)
    print(f"Public channel dedupe complete: hidden={len(removed_records)}; records preserved in reports/public-dedupe.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
