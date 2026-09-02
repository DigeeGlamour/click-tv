"""A published episode must be in the playback catalogue, or it will not play.

Every episode card carries a `playback_id` and nothing else: the Worker looks
that id up in `data/playback/` to learn the real URL and headers. So an episode
whose id is not in the catalogue is a card that opens and then plays nothing,
and the Pages validator refuses the build over it - 137 of them on run
33630856186, reading

    Bangla episode #2 playback_id catalogue-এ নেই: Chokro 2 — Episode 01

The two files are written by the same call. `sanitize_item` puts the id into
the season file and the record into the collector at the same moment, so they
cannot disagree within one run. They disagree across runs: the season tree was
published at 11:42:46 while every one of the 256 committed shards still says
11:39:47, so the tree in the repository is newer than the catalogue beside it.

Nothing here invents a route. `stable_playback_id` is a pure function of the
playable configuration - url, headers, drm, header profile, stream type,
manifest-query inheritance - and all of those survive into the season file, so
the record is rebuilt from the episode's own published configuration through
the same collector the scan uses. Any episode whose id does not recompute from
what it carries is left alone and reported, because that one really is
unexplained.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Tuple

try:
    from scanner.playback_profiles import (
        PlaybackProfileCollector,
        load_public_catalog_records,
        merge_public_catalog,
        stable_playback_id,
    )
except ImportError:  # pragma: no cover - direct module execution
    from playback_profiles import (  # type: ignore
        PlaybackProfileCollector,
        load_public_catalog_records,
        merge_public_catalog,
        stable_playback_id,
    )

SERIES_DIRECTORY = "series"


def _seasons(series_root: Path) -> List[Path]:
    if not series_root.is_dir():
        return []
    return sorted(series_root.glob("*/*/season-*.json"))


def published_episodes(data_root: str | Path) -> Iterator[Tuple[Path, Mapping[str, Any], Dict[str, Any]]]:
    """Every episode in every published season file, with its season payload."""
    for path in _seasons(Path(data_root) / SERIES_DIRECTORY):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        for episode in payload.get("items") or []:
            if isinstance(episode, dict):
                yield path, payload, episode


def missing_episode_profiles(data_root: str | Path) -> List[Dict[str, Any]]:
    """Published episodes whose playback_id is not in the catalogue.

    `recomputes` says whether the id the card carries is the id its own
    configuration produces. Only those can be registered from the season file;
    the rest are reported for a human to read.
    """
    records = load_public_catalog_records(data_root)
    missing: List[Dict[str, Any]] = []
    for path, season, episode in published_episodes(data_root):
        playback_id = str(episode.get("playback_id") or "").strip()
        if not playback_id or playback_id in records:
            continue
        missing.append({
            "playback_id": playback_id,
            "series": str(season.get("series_name") or ""),
            "category": str(season.get("category") or ""),
            "season": season.get("season_number"),
            "episode": str(episode.get("episode_label") or ""),
            "path": str(path),
            "recomputes": stable_playback_id(episode) == playback_id,
            "episode_item": episode,
        })
    return missing


def reconcile(data_root: str | Path, timestamp: str = "") -> Dict[str, Any]:
    """Register every missing episode profile that its own card can prove.

    Idempotent, and additive only: existing records are read back and merged
    unchanged, so a route already in the catalogue is never rewritten by this.
    """
    root = Path(data_root)
    missing = missing_episode_profiles(root)
    registerable = [row for row in missing if row["recomputes"]]
    unexplained = [
        {key: value for key, value in row.items() if key != "episode_item"}
        for row in missing if not row["recomputes"]
    ]

    report: Dict[str, Any] = {
        "missing": len(missing),
        "registered": 0,
        "unexplained": unexplained,
        "examples": [
            f"{row['series']} — {row['episode']}" for row in registerable[:8]
        ],
    }
    if not registerable:
        return report

    collector = PlaybackProfileCollector("series", timestamp or _now())
    for row in registerable:
        season = row["season"]
        context = f"series:{row['series']}:season:{season}:episode:{row['episode']}"
        collector.sanitize_item(row["episode_item"], context)

    # sanitize_item registers whatever the item resolves to; keep only the ids
    # the published cards actually ask for, so a nested backup route inside an
    # episode cannot smuggle an unreferenced record into the catalogue.
    wanted = {row["playback_id"] for row in registerable}
    collector.records = {
        key: value for key, value in collector.records.items() if key in wanted
    }
    if not collector.records:
        return report

    merge_public_catalog(root, collector)
    report["registered"] = len(collector.records)
    return report


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "missing_episode_profiles",
    "published_episodes",
    "reconcile",
]
