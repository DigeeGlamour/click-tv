"""ধারা ৪.০ - an episode that was reaching viewers is not dropped in silence.

`scanner/movies.py` has carried a published movie card through a scan that
could not find it since the grace rule was written: three scans of patience,
then it goes. The series catalogue had nothing of the kind.
`publish_prepared_series` rebuilds `data/series` from the payload it is
handed, so an episode this run did not produce simply stops existing.

Measured on the 2026-10-17 movies runs, which is how this was found. The
no-loss gate blocked three publishes in a row and the third one's remaining
loss was entirely this:

    run 06:12   unexplained live loss  393   (all retired CDN hosts)
    run 07:42   unexplained live loss  119   (all `dertg.b-cdn.net`)

Every one of the 119 was an `episode|data/series/...` line on the CURRENT
host - streams the site was serving that morning, gone by the afternoon:

    Series published   181 Series / 772 Episodes   (06:12)
    Series published   175 Series / 738 Episodes   (07:42)

Nothing was wrong with those episodes. Their shows simply did not come back
through the staging catalogue on the second run, because what reaches
staging depends on which movie cards retention happened to carry, and that
moves between runs.

So the series side gets the same promise the movie side already makes, with
the same grace and the same vocabulary. The carried record is the published
episode itself, handed back in the staging shape: `_normalize_sources`
already accepts an episode that carries its own `url`, and a published
episode carries `resolution_height`, so it survives the 720p floor it
originally passed.

Carried BEFORE `_merge_duplicate_series`, so a show that did come back this
run absorbs its missing episodes through the merge that already exists -
deduplicated on `episode_key`, which is the same key that merge trusts.
"""
from __future__ import annotations

import datetime as _dt
import glob
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

SCHEMA_VERSION = 1

#: The same patience the movie catalogue gives a card, read from there so the
#: two cannot drift apart.
try:  # pragma: no cover - exercised by the import itself
    from scanner.movie_retention import GRACE_SCANS
except Exception:  # pragma: no cover - defensive
    GRACE_SCANS = 3

DEFAULT_STORE = os.path.join("state", "series-retention.json")
SERIES_ROOT = os.path.join("data", "series")

#: Written onto a carried episode so the catalogue can say why it is there.
CARRIED_FLAG = "retained_after_failed_scan"


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _iso(moment: _dt.datetime) -> str:
    return moment.astimezone(_dt.timezone.utc).isoformat()


def _family(url: Any) -> str:
    try:
        from scanner.movie_coverage import stream_family

        return stream_family(url)
    except Exception:  # noqa: BLE001 - one url, never the run
        return ""


def episode_families(episode: Mapping[str, Any]) -> List[str]:
    """Every stream this episode offers, as the gate spells them."""
    found: List[str] = []
    if not isinstance(episode, Mapping):
        return found
    candidates: List[Any] = [episode.get("url"), episode.get("link")]
    for link in list(episode.get("links") or ()) + list(episode.get("backups") or ()):
        if isinstance(link, Mapping):
            candidates.append(link.get("url"))
        else:
            candidates.append(link)
    for value in candidates:
        family = _family(value)
        if family and family not in found:
            found.append(family)
    return found


def payload_families(items: Iterable[Mapping[str, Any]]) -> set:
    """Every stream the payload about to be published offers."""
    present: set = set()
    for show in items or ():
        if not isinstance(show, Mapping):
            continue
        for season in show.get("seasons") or ():
            if not isinstance(season, Mapping):
                continue
            for episode in season.get("episodes") or ():
                present.update(episode_families(episode))
    return present


def published_shows(root: str | Path = ".") -> List[Dict[str, Any]]:
    """Every published show, as `{category, name, seasons: [...]}`.

    Read off disk in the published shape and handed back in the staging one,
    which is the only translation this module does.
    """
    base = Path(root)
    shows: Dict[str, Dict[str, Any]] = {}
    pattern = str(base / SERIES_ROOT / "*" / "*" / "season-*.json")
    for path in sorted(glob.glob(pattern)):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        episodes = payload.get("items")
        if not isinstance(episodes, list) or not episodes:
            continue
        series_id = str(payload.get("series_id") or "").strip()
        if not series_id:
            continue
        show = shows.setdefault(series_id, {
            "name": str(payload.get("series_name") or series_id),
            "category": str(payload.get("category") or ""),
            "series_id": series_id,
            "seasons": [],
        })
        season_number = payload.get("season_number")
        try:
            season_number = int(season_number)
        except (TypeError, ValueError):
            season_number = 1
        show["seasons"].append({
            "number": season_number,
            "episodes": [ep for ep in episodes if isinstance(ep, dict)],
            "_source_path": path,
        })
    return list(shows.values())


def _load(path: Path) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    absent = payload.get("absent")
    return {"schema_version": SCHEMA_VERSION,
            "absent": absent if isinstance(absent, dict) else {}}


def _write(store: Dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=str(path.parent), delete=False)
    try:
        json.dump({"schema_version": SCHEMA_VERSION,
                   "updated_at": _iso(_now()),
                   "absent": store.get("absent") or {}},
                  handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    finally:
        handle.close()
    os.replace(handle.name, path)


def _staging_episode(published: Mapping[str, Any], misses: int) -> Dict[str, Any]:
    """A published episode, in the shape the staging catalogue speaks.

    `_normalize_sources` prepends the episode itself when it carries a `url`,
    so the primary needs no translation; only the backups move into `links`.
    """
    carried = dict(published)
    links: List[Dict[str, Any]] = []
    for backup in published.get("backups") or ():
        if isinstance(backup, Mapping) and backup.get("url"):
            links.append(dict(backup))
    carried["links"] = links
    carried.pop("backups", None)
    carried[CARRIED_FLAG] = True
    carried["retained_scan_count"] = misses
    carried["retention_note"] = (
        "Published on a previous scan and not produced by this one. Carried "
        f"for {misses} of {GRACE_SCANS} allowed scans so a run that simply "
        "did not reach this show does not delete it."
    )
    return carried


def carry(
    items: List[Dict[str, Any]],
    *,
    root: str | Path = ".",
    store_path: Optional[str | Path] = None,
    grace_scans: Optional[int] = None,
    persist: bool = True,
) -> Dict[str, Any]:
    """Append the published episodes this payload is missing. Mutates `items`.

    Returns a summary. Wrapped by the caller: a failure here must publish the
    payload exactly as it would have been published without this step, which
    is the old behaviour - worse, but not broken.
    """
    grace = int(GRACE_SCANS if grace_scans is None else grace_scans)
    path = Path(store_path or (Path(root) / DEFAULT_STORE))
    store = _load(path)
    absent: Dict[str, Any] = store["absent"]
    stamp = _iso(_now())

    present = payload_families(items)
    summary = {"carried_episodes": 0, "carried_shows": 0,
               "dropped_after_grace": 0, "still_present": 0}

    by_show: Dict[str, Dict[str, Any]] = {}
    seen_keys: set = set()

    for show in published_shows(root):
        for season in show.get("seasons") or ():
            for episode in season.get("episodes") or ():
                families = episode_families(episode)
                if not families:
                    continue
                key = str(episode.get("id") or "").strip() or "|".join(families)
                seen_keys.add(key)
                if any(family in present for family in families):
                    summary["still_present"] += 1
                    absent.pop(key, None)
                    continue
                misses = int((absent.get(key) or {}).get("misses") or 0) + 1
                absent[key] = {"misses": misses, "last_missing_at": stamp}
                if misses > grace:
                    summary["dropped_after_grace"] += 1
                    continue
                record = by_show.setdefault(show["series_id"], {
                    "name": show["name"],
                    "category": show["category"],
                    "seasons": [],
                    "carried_from_published_series": True,
                })
                numbers = {s["number"]: s for s in record["seasons"]}
                target = numbers.get(season["number"])
                if target is None:
                    target = {"number": season["number"], "episodes": []}
                    record["seasons"].append(target)
                target["episodes"].append(_staging_episode(episode, misses))
                summary["carried_episodes"] += 1

    # An entry nobody asked about any more stops being tracked, so the file
    # does not grow a row for every episode the catalogue has ever had.
    for key in [key for key in absent if key not in seen_keys]:
        absent.pop(key, None)

    for record in by_show.values():
        items.append(record)
    summary["carried_shows"] = len(by_show)

    if persist:
        _write(store, path)
    return summary


def describe(summary: Mapping[str, Any]) -> str:
    if not summary or not summary.get("carried_episodes"):
        return ""
    tail = ""
    if summary.get("dropped_after_grace"):
        tail = (f"; {summary['dropped_after_grace']} dropped past the "
                f"{GRACE_SCANS}-scan grace")
    return (
        f"series retention: carried {summary['carried_episodes']} published "
        f"episode(s) across {summary['carried_shows']} show(s) this run did "
        f"not produce{tail}"
    )
