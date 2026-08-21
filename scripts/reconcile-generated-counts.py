"""Fix up every derived count field to match the file it actually describes.

Three independent scanners (GitHub Actions, a local PC clone, Google Colab)
each fetch, scan, commit and push around the same time. Every generated file
this script touches is written internally consistent by the scanner in a
single run — but a `git rebase` that reconciles two of those runs' commits
does NOT understand JSON. A large collection (channels list, playback-sources
records) with non-overlapping edits on both sides merges cleanly line-by-line,
while the single scalar "count" field describing that same collection sits on
one shared line and becomes a real conflict. `-X theirs` resolves that one
line in favour of whichever commit is being replayed, leaving a count that
described a snapshot which no longer exists once the collection itself
finished merging. That mismatch is exactly what scripts/validate-pages.py
calls a "count mismatch" and refuses to publish.

Run this once, right after a successful `git rebase`/merge and before the
final push, so any count a merge silently staled gets corrected before it
ever reaches the site. It is a pure reconciliation against files already on
disk: no network access, no scanner state, safe to run any number of times.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner.output import _reconcile_manifest_counts, refresh_allowed_hosts  # noqa: E402


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}


def _write_if_changed(path: Path, before: str, payload: dict) -> bool:
    after = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if after == before:
        return False
    path.write_text(after, encoding="utf-8", newline="\n")
    return True


def _catalog_playback_ids(data_root: Path) -> set:
    """Every playback id the catalogue can serve, sharded or not.

    Reading only the index would report zero ids under the sharded layout and
    make every protected item look orphaned, which would delete the entire
    published catalogue on the next reconciliation.
    """
    ids = set()
    shard_dir = data_root / "playback"
    if shard_dir.is_dir():
        for shard_file in sorted(shard_dir.glob("*.json")):
            records = _load(shard_file).get("records")
            if isinstance(records, dict):
                ids.update(records)

    legacy = _load(data_root / "playback-sources.json").get("records")
    if isinstance(legacy, dict):
        ids.update(legacy)
    return ids


def _item_is_playable(item: dict, playback_ids: set) -> bool:
    """An item must still have a way to actually play after a merge.

    A protected stream carries no url; its real address lives in
    data/playback-sources.json under `playback_id`. Those two files are written
    together by one scan, but a rebase resolves them independently: the channel
    list can merge in entries from one run while the catalogue keeps the other
    run's records. The leftover entry then points at a playback_id nobody holds
    - no url, no catalogue record, nothing to play - and the Pages validator
    correctly refuses it ("playback_id catalogue-এ নেই").

    Metadata-only cards are deliberately link-less (an announced fixture whose
    stream is published at kickoff) and are kept.
    """
    if item.get("metadata_only") is True:
        return True

    playback_id = str(item.get("playback_id") or "").strip()
    if playback_id and playback_id not in playback_ids:
        has_url = any(
            str(item.get(key) or "").strip()
            for key in ("url", "stream_url", "link")
        )
        if not has_url:
            return False
    return True


def _scrub_stale_playback_id(item: dict, playback_ids: set) -> bool:
    """A url fallback keeps an item playable per `_item_is_playable` above,
    but scripts/validate-pages.py rejects ANY playback_id absent from the
    catalogue outright, url or no url - it does not know one was offered as
    a fallback. Clearing the now-meaningless field (the url is the real
    route now) is what actually satisfies that check. Returns whether it
    changed anything.
    """
    if not isinstance(item, dict) or item.get("metadata_only") is True:
        return False
    playback_id = str(item.get("playback_id") or "").strip()
    if not playback_id or playback_id in playback_ids:
        return False
    has_url = any(
        str(item.get(key) or "").strip()
        for key in ("url", "stream_url", "link")
    )
    if not has_url:
        return False
    item["playback_id"] = ""
    return True


def _drop_orphaned_items(
    data_root: Path,
    playback_ids: set,
    changed: list,
) -> tuple:
    """Remove every published item whose only playback route went missing,
    collapse any identity a rebase's line-based merge duplicated, and sync
    each collection's own "count" field to its real, post-fix length.

    Returns (removed_count, movie_category_dirs_touched) so the index rebuild
    below only rewrites categories that actually lost something.
    """
    removed = 0
    touched_movie_dirs = set()

    targets = []
    channels_dir = data_root / "channels"
    if channels_dir.is_dir():
        targets.extend(sorted(channels_dir.glob("*.json")))
    movies_dir = data_root / "movies"
    if movies_dir.is_dir():
        targets.extend(sorted(movies_dir.glob("*/page-*.json")))
    for event_file in ("today-match.json", "upcoming.json"):
        path = data_root / event_file
        if path.is_file():
            targets.append(path)

    for path in targets:
        before = path.read_text(encoding="utf-8")
        if not before.strip():
            continue
        try:
            payload = json.loads(before)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue

        dropped_here = 0
        changed_here = False
        for list_key in ("channels", "movies", "items", "matches", "events"):
            values = payload.get(list_key)
            if not isinstance(values, list):
                continue
            kept = [
                item
                for item in values
                if not isinstance(item, dict)
                or _item_is_playable(item, playback_ids)
            ]
            for item in kept:
                if _scrub_stale_playback_id(item, playback_ids):
                    changed_here = True
            # A rebase merges non-overlapping edits to this same collection
            # line-by-line, with no idea it is JSON - it can just as easily
            # duplicate an entry (both sides kept it, at different positions)
            # as drop one. An identity seen twice is never legitimate here -
            # but `id` alone is not that identity: two genuinely different
            # channels ("Aaj Tak" and "Aaj Tak Bangla") were found sharing one
            # slugified id, and deleting the second would have been a second,
            # worse incident. Requiring `id` *and* name together fixes that
            # false match while still catching the real one: two published
            # "Star Sports 2" cards, discovered through two different
            # backup-worthy sources, share id and name but not url - the
            # validator rightly rejects two cards for one channel name
            # regardless, so identity is (id, name), not a url a genuine
            # duplicate stream is not even guaranteed to share. `url` alone
            # is the fallback only when there is no id to key on at all.
            deduped: list = []
            seen_identities: set = set()
            for item in kept:
                identity = None
                if isinstance(item, dict):
                    identity_id = str(item.get("id") or "").strip()
                    identity_name = str(item.get("name") or item.get("title") or "").strip()
                    if identity_id and identity_name:
                        identity = ("id+name", identity_id, identity_name)
                    else:
                        url = str(item.get("url") or "").strip()
                        if url:
                            identity = ("url", url)
                if identity is not None:
                    if identity in seen_identities:
                        continue
                    seen_identities.add(identity)
                deduped.append(item)
            if len(deduped) != len(values):
                dropped_here += max(len(values) - len(deduped), 0)
                payload[list_key] = deduped
                changed_here = True
            # Whether or not the collection itself changed, the scalar
            # "count" describing it is exactly the field a rebase's 3-way
            # text merge resolves independently of the list - sync it
            # unconditionally rather than only when something was dropped.
            if isinstance(payload.get("count"), int) and payload["count"] != len(payload[list_key]):
                payload["count"] = len(payload[list_key])
                changed_here = True

        if changed_here and _write_if_changed(path, before, payload):
            removed += dropped_here
            changed.append(str(path.relative_to(data_root.parent)).replace("\\", "/"))
            if path.parent.parent.name == "movies":
                touched_movie_dirs.add(path.parent)

    return removed, touched_movie_dirs


def _page_items(payload: dict):
    for list_key in ("movies", "items"):
        values = payload.get(list_key)
        if isinstance(values, list):
            return values
    return None


def _rebuild_movie_index(index_path: Path, changed: list, data_root: Path) -> None:
    """Re-derive one movie index from the page files that survived.

    Page filenames are zero-padded (page-001.json), so the page entry's own
    "file" field is the only reliable way back to the file it describes.
    """
    before = index_path.read_text(encoding="utf-8")
    if not before.strip():
        return
    try:
        index_payload = json.loads(before)
    except json.JSONDecodeError:
        return
    if not isinstance(index_payload, dict):
        return

    pages = index_payload.get("pages")
    if not isinstance(pages, list):
        return

    total = 0
    for page_entry in pages:
        if not isinstance(page_entry, dict):
            return
        filename = str(page_entry.get("file") or "").strip()
        if not filename:
            return
        page_file = index_path.parent / filename
        try:
            page_payload = json.loads(page_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        items = _page_items(page_payload)
        if items is None:
            return
        page_entry["count"] = len(items)
        total += len(items)

    index_payload["total_pages"] = len(pages)
    index_payload["count"] = total
    if _write_if_changed(index_path, before, index_payload):
        changed.append(
            str(index_path.relative_to(data_root.parent)).replace("\\", "/")
        )


def main() -> int:
    # An optional repository root argument keeps this testable against a
    # throwaway directory; real callers (the GitHub Actions workflow, the
    # local PC scan script) never pass one and it defaults to this repo.
    repo_root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT
    data_root = repo_root / "data"
    changed = []

    # Referential integrity first: dropping unplayable items changes the very
    # counts the manifest reconciliation below is about to recompute.
    playback_ids = _catalog_playback_ids(data_root)
    removed, touched_movie_dirs = _drop_orphaned_items(
        data_root, playback_ids, changed
    )
    for category_dir in sorted(touched_movie_dirs):
        index_path = category_dir / "index.json"
        if index_path.is_file():
            _rebuild_movie_index(index_path, changed, data_root)

    manifest_path = data_root / "manifest.json"
    if manifest_path.is_file():
        before = manifest_path.read_text(encoding="utf-8")
        manifest = json.loads(before) if before.strip() else {}
        _reconcile_manifest_counts(manifest, data_root)
        if _write_if_changed(manifest_path, before, manifest):
            changed.append("data/manifest.json")

        # The flat mirror (data/<name>.json) and the versioned snapshot slot
        # (data/snapshots/<slot>/<name>.json) are two on-disk copies of the
        # same event payload - snapshot_publish.py's commit() writes both
        # from the same in-memory value, so they normally agree. A rebase
        # has no idea they are supposed to match and can resolve them as two
        # independent files, picking the mirror from one commit while the
        # slot copy (and the manifest pointer naming it) comes from another.
        # The slot is what every reader's pointer actually names, so it is
        # the source of truth: resync the mirror to match it.
        snapshot = manifest.get("snapshot") if isinstance(manifest, dict) else None
        slot = str(snapshot.get("slot") or "").strip() if isinstance(snapshot, dict) else ""
        if slot:
            for name in ("today-match.json", "upcoming.json"):
                slot_path = data_root / "snapshots" / slot / name
                if not slot_path.is_file():
                    continue
                slot_text = slot_path.read_text(encoding="utf-8")
                mirror_path = data_root / name
                mirror_before = (
                    mirror_path.read_text(encoding="utf-8") if mirror_path.is_file() else ""
                )
                if slot_text != mirror_before:
                    mirror_path.write_text(slot_text, encoding="utf-8")
                    changed.append(f"data/{name}")

    # allowed-hosts.json is derived, not scanner state, from the exact same
    # channels/movies/today-match/playback files just corrected above - its
    # own declared "count" going stale by the same rebase mechanism is the
    # same failure mode, just one file later. Re-deriving it fresh is the
    # correct fix, not patching its scalar count in isolation.
    allowed_hosts_path = data_root / "allowed-hosts.json"
    if allowed_hosts_path.is_file():
        before = allowed_hosts_path.read_text(encoding="utf-8")
        refresh_allowed_hosts(data_root)
        after = allowed_hosts_path.read_text(encoding="utf-8")
        if after != before:
            changed.append("data/allowed-hosts.json")

    catalog_path = data_root / "playback-sources.json"
    if catalog_path.is_file():
        before = catalog_path.read_text(encoding="utf-8")
        catalog = json.loads(before) if before.strip() else {}
        records = catalog.get("records")
        if isinstance(records, dict):
            actual = len(records)
            if catalog.get("count") != actual:
                catalog["count"] = actual
        elif catalog.get("sharded") is True:
            # Sharded layout: the index declares a per-shard count plus a
            # total. A rebase can leave either disagreeing with the shard
            # files that actually shipped.
            shard_dir = data_root / "playback"
            actual_shards = {}
            for shard_file in sorted(shard_dir.glob("*.json")):
                shard_payload = _load(shard_file)
                shard_records = shard_payload.get("records")
                if isinstance(shard_records, dict):
                    actual_shards[shard_file.stem] = len(shard_records)
            if actual_shards:
                catalog["shards"] = actual_shards
                catalog["count"] = sum(actual_shards.values())
        if _write_if_changed(catalog_path, before, catalog):
            changed.append("data/playback-sources.json")

    for shard_file in sorted((data_root / "playback").glob("*.json")):
        before = shard_file.read_text(encoding="utf-8")
        shard_payload = json.loads(before) if before.strip() else {}
        shard_records = shard_payload.get("records")
        if isinstance(shard_records, dict) and shard_payload.get("count") != len(shard_records):
            shard_payload["count"] = len(shard_records)
            if _write_if_changed(shard_file, before, shard_payload):
                changed.append(f"data/playback/{shard_file.name}")

    if removed:
        print(f"Dropped {removed} item(s) with no surviving playback route.")
    if changed:
        print("Reconciled: " + ", ".join(sorted(set(changed))))
    else:
        print("No drift found; nothing to reconcile.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
