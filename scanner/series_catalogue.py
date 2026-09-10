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

#: Every published surface that carries a `playback_id`, as a glob relative to
#: `data/`, with the kind of thing it publishes.
#:
#: Measured on the repository tree: 75 season files holding 328 episode ids,
#: 21 movie pages holding 1,667. Both are written by the movies/series scan and
#: both are exposed to the same loss, because the playback catalogue is shared
#: by every scan mode while the push step treats each generated file as having
#: one owner.
#:
#: Events are deliberately absent. They carry playback ids too, and they are
#: settled in scripts/merge-published-events.py, which knows the things this
#: module has no business knowing - the archive, the two tabs, the identity
#: fold. Two settlements, each where its rules live.
PUBLISHED_SURFACES = (
    ("episode", "series/*/*/season-*.json"),
    ("movie", "movies/*/page-*.json"),
)


def _seasons(series_root: Path) -> List[Path]:
    if not series_root.is_dir():
        return []
    return sorted(series_root.glob("*/*/season-*.json"))


def _surface_files(data_root: Path, kinds: Tuple[str, ...]) -> List[Tuple[str, Path]]:
    found: List[Tuple[str, Path]] = []
    for kind, pattern in PUBLISHED_SURFACES:
        if kinds and kind not in kinds:
            continue
        found.extend((kind, path) for path in sorted(data_root.glob(pattern)))
    return found


def published_items(
    data_root: str | Path,
    kinds: Tuple[str, ...] = (),
) -> Iterator[Tuple[str, Path, Mapping[str, Any], Dict[str, Any]]]:
    """Every published item that could carry a playback id, with its container.

    The container is the season or page payload, which is where the naming for
    a report comes from - a season file states its series and season, a movie
    page states nothing and the item names itself.
    """
    root = Path(data_root)
    for kind, path in _surface_files(root, kinds):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            # Unreadable is not empty. A file this pass cannot parse is left
            # alone and its items are not reported as missing anything -
            # deciding they are dangling on a read failure is the same mistake
            # as reading an inconclusive probe as "confirmed dead".
            continue
        if not isinstance(payload, dict):
            continue
        for item in payload.get("items") or []:
            if isinstance(item, dict):
                yield kind, path, payload, item


def _describe(kind: str, container: Mapping[str, Any],
              item: Mapping[str, Any]) -> Dict[str, Any]:
    """Names for the report, taken from wherever this kind states them."""
    if kind == "episode":
        return {
            "series": str(container.get("series_name") or ""),
            "category": str(container.get("category") or ""),
            "season": container.get("season_number"),
            "episode": str(item.get("episode_label") or ""),
        }
    return {
        "series": str(item.get("name") or item.get("title") or ""),
        "category": str(item.get("category") or container.get("category") or ""),
        "season": None,
        "episode": "",
    }


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


def missing_profiles(
    data_root: str | Path,
    kinds: Tuple[str, ...] = (),
) -> List[Dict[str, Any]]:
    """Published items whose playback_id is not in the catalogue.

    `recomputes` says whether the id the card carries is the id its own
    configuration produces. Only those can be registered from the published
    file alone; the rest need the record carried across from another tree, and
    what neither can explain is reported for a human to read.
    """
    records = load_public_catalog_records(data_root)
    missing: List[Dict[str, Any]] = []
    for kind, path, container, item in published_items(data_root, kinds):
        playback_id = str(item.get("playback_id") or "").strip()
        if not playback_id or playback_id in records:
            continue
        row = {
            "playback_id": playback_id,
            "kind": kind,
            "path": str(path),
            "recomputes": stable_playback_id(item) == playback_id,
            "episode_item": item,
        }
        row.update(_describe(kind, container, item))
        missing.append(row)
    return missing


def missing_episode_profiles(data_root: str | Path) -> List[Dict[str, Any]]:
    """The episode half, which is what `scanner/series.py` insists on."""
    return missing_profiles(data_root, ("episode",))


def reconcile(
    data_root: str | Path,
    timestamp: str = "",
    kinds: Tuple[str, ...] = (),
) -> Dict[str, Any]:
    """Register every missing profile that its own card can prove.

    Idempotent, and additive only: existing records are read back and merged
    unchanged, so a route already in the catalogue is never rewritten by this.

    `kinds` empty means every surface in `PUBLISHED_SURFACES`. The default
    widened from episodes alone when the loss turned out not to be an episode
    problem: movie pages carry 1,667 playback ids on the same catalogue and are
    damaged by the same push-step restore. Nothing about the repair is
    kind-specific - `stable_playback_id` is a pure function of the playable
    configuration, and a movie card carries one exactly as an episode does.
    """
    root = Path(data_root)
    missing = missing_profiles(root, kinds)
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
        # Context is a label on the record, not an identity: the identity is
        # the id itself, and only the ids the published cards ask for survive
        # the filter below. So a movie and an episode can share one collector
        # without either being able to take the other's record.
        if row.get("kind") == "movie":
            context = f"movie:{row['series']}"
        else:
            context = (f"series:{row['series']}:season:{row['season']}"
                       f":episode:{row['episode']}")
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
    "PUBLISHED_SURFACES",
    "missing_episode_profiles",
    "missing_profiles",
    "published_episodes",
    "published_items",
    "reconcile",
]
