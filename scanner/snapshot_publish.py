"""Requirement 15 - versioned snapshot publish behind one atomic pointer.

Writing each output file atomically is not the same thing as publishing a
snapshot atomically, and a run of sequential renames is not atomic either. Nine
individually-safe renames still leave eight instants at which the site is live
and self-contradictory: a card whose playback profile is not published yet, a
manifest counting events the event file does not contain, or an allowlist that
predates the hosts the new cards need.

So this module does not swap the published files at all. It publishes a complete
new snapshot **beside** the live one, under its own version, and then moves a
single pointer:

    data/snapshots/s0/            <- one complete, self-consistent snapshot
    data/snapshots/s1/               today-match.json, upcoming.json,
    data/snapshots/s2/               allowed-hosts.json, playback-sources.json
    data/manifest.json            <- THE pointer: names the live slot

`data/manifest.json` is what every reader dereferences first - the site loads it
before anything else and takes each event URL from it - so replacing that one
regular file with one os.replace() is the entire switch. Before the rename a
reader follows the previous manifest to the previous slot, which is untouched and
complete. After it, a reader follows the new manifest to the new slot, which was
written and validated in full beforehand. There is no third possibility, so a
mixed old/new snapshot is never visible.

Three slots are reused round-robin rather than a new directory per scan, so the
repository does not grow without bound and a scan's git diff stays the size of
one snapshot. Round-robin also means the slot being written is two generations
old: no reader still holding a usable pointer can be looking at it.

Two published surfaces deliberately stay outside the switch, because both are
strictly append-only and therefore satisfy the old and the new snapshot at once:

  * data/playback/<shard>.json - a playback_id is a SHA-256 of the stream URL,
    headers, DRM block and profile, so a record is content-addressed. A rotated
    URL produces a different id rather than mutating one, and each scan merges
    its records into the previous set. The shard files are consequently a
    superset of every retained snapshot's needs.
  * data/allowed-hosts.json - rebuilt as the union over every data file
    including all shards, so it is likewise a superset.

The flat data/today-match.json, data/upcoming.json and data/playback-sources.json
are kept as a compatibility mirror for tooling that predates this layout. They
are written after the pointer has already moved, so nothing that follows the
pointer ever depends on them.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

SNAPSHOT_DIRECTORY = "snapshots"
SNAPSHOT_SLOTS: Tuple[str, ...] = ("s0", "s1", "s2")
POINTER_FILE = "manifest.json"

# Ordering metadata kept for the callers that classify what they stage. Every
# file of a snapshot now lands before the pointer moves, so these no longer
# describe a sequence of visible states - only the order files are written to
# the slot in.
ORDER_PLAYBACK_SHARD = 10
ORDER_PLAYBACK_INDEX = 20
ORDER_ALLOWED_HOSTS = 30
ORDER_EVENT_FILE = 40
ORDER_MANIFEST = 50
ORDER_REPORT = 60

# Files that live inside the versioned slot and are named by the pointer.
SLOT_FILES = {
    "today-match.json": "event",
    "upcoming.json": "event",
    "allowed-hosts.json": "allowed_hosts",
    "playback-sources.json": "playback_index",
}


@dataclass
class _StagedFile:
    target: Path
    payload: Any
    order: int
    kind: str


@dataclass
class SnapshotPublisher:
    """One consistent snapshot, published beside the live one and then pointed at."""

    timestamp: str = ""
    data_root: Optional[Path] = None
    files: Dict[Path, _StagedFile] = field(default_factory=dict)
    deletions: List[Path] = field(default_factory=list)
    committed: bool = False
    notes: List[str] = field(default_factory=list)
    slot: str = ""
    generation: int = 0

    # -- staging ----------------------------------------------------------
    def stage(
        self,
        target: Path | str,
        payload: Any,
        *,
        order: int = ORDER_EVENT_FILE,
        kind: str = "data",
    ) -> None:
        path = Path(target)
        self.files[path] = _StagedFile(
            target=path, payload=payload, order=order, kind=kind
        )

    def stage_deletion(self, target: Path | str) -> None:
        """Queue a published file for removal after the pointer has moved.

        Removal never happens before the switch: while the previous pointer is
        still live, everything it names has to remain readable.
        """
        path = Path(target)
        self.files.pop(path, None)
        if path not in self.deletions:
            self.deletions.append(path)

    def drop(self, target: Path | str) -> bool:
        """Take one file back out of the snapshot, leaving the published one."""
        return self.files.pop(Path(target), None) is not None

    def staged_payload(self, target: Path | str) -> Optional[Any]:
        entry = self.files.get(Path(target))
        return None if entry is None else entry.payload

    def overrides(self) -> Dict[Path, Any]:
        """Staged payloads keyed by resolved path.

        Any helper that normally reads a published file - the manifest count
        reconciler, the allowlist builder - has to see what this snapshot is
        about to publish instead, or it would describe the previous one.
        """
        return {
            path.resolve() if path.is_absolute() else path: entry.payload
            for path, entry in self.files.items()
        }

    def of_kind(self, kind: str) -> List[Tuple[Path, Any]]:
        return [
            (entry.target, entry.payload)
            for entry in self.files.values()
            if entry.kind == kind
        ]

    # -- validation -------------------------------------------------------
    def validate(self) -> Tuple[bool, str]:
        """Check the staged set as a set, before anything is published."""
        for path, entry in self.files.items():
            try:
                json.dumps(entry.payload, ensure_ascii=False)
            except (TypeError, ValueError) as error:
                return False, f"{path.name} is not serializable: {error}"

        ok, reason = self._validate_playback_references()
        if not ok:
            return False, reason
        ok, reason = self._validate_index_matches_shards()
        if not ok:
            return False, reason
        return self._validate_manifest_matches_events()

    def _event_items(self) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        for _, payload in self.of_kind("event"):
            if not isinstance(payload, dict):
                continue
            for key in ("items", "events", "matches"):
                values = payload.get(key)
                if isinstance(values, list):
                    items.extend(v for v in values if isinstance(v, dict))
                    break
        return items

    def _staged_playback_ids(self) -> Set[str]:
        ids: Set[str] = set()
        for _, payload in self.of_kind("playback_shard"):
            records = payload.get("records") if isinstance(payload, dict) else None
            if isinstance(records, dict):
                ids.update(str(key) for key in records)
        return ids

    def _published_playback_ids(self) -> Set[str]:
        """Ids already on disk, minus any shard this snapshot replaces or drops.

        A shard being replaced must be judged on its staged contents alone -
        reading the published copy would let a profile this snapshot has just
        dropped vouch for a card that would then have nothing to play.
        """
        if self.data_root is None:
            return set()
        shard_dir = Path(self.data_root) / "playback"
        if not shard_dir.is_dir():
            return set()
        replaced = {
            entry.target.resolve()
            for entry in self.files.values()
            if entry.kind == "playback_shard"
        } | {path.resolve() for path in self.deletions}

        ids: Set[str] = set()
        for shard_file in shard_dir.glob("*.json"):
            if shard_file.resolve() in replaced:
                continue
            try:
                payload = json.loads(shard_file.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            records = payload.get("records") if isinstance(payload, dict) else None
            if isinstance(records, dict):
                ids.update(str(key) for key in records)
        return ids

    def _validate_playback_references(self) -> Tuple[bool, str]:
        items = self._event_items()
        if not items:
            return True, "no staged event items"

        wanted = {
            str(item.get("playback_id") or "").strip()
            for item in items
            if str(item.get("playback_id") or "").strip()
        }
        for item in items:
            for backup in item.get("backups") or []:
                if isinstance(backup, dict):
                    value = str(backup.get("playback_id") or "").strip()
                    if value:
                        wanted.add(value)
        if not wanted:
            return True, "no staged card references a playback profile"

        available = self._staged_playback_ids() | self._published_playback_ids()
        missing = sorted(wanted - available)
        if missing:
            return False, (
                f"{len(missing)} staged card playback_id(s) resolve to no "
                f"playback profile (first: {missing[0]})"
            )
        return True, "every staged playback_id resolves"

    def _validate_index_matches_shards(self) -> Tuple[bool, str]:
        index_entries = self.of_kind("playback_index")
        if not index_entries:
            return True, "no staged playback index"
        _, index = index_entries[0]
        if not isinstance(index, dict):
            return False, "staged playback index is not a mapping"

        declared = index.get("shards")
        if not isinstance(declared, dict):
            return False, "staged playback index declares no shard map"

        staged_counts: Dict[str, int] = {}
        for path, payload in self.of_kind("playback_shard"):
            records = payload.get("records") if isinstance(payload, dict) else None
            staged_counts[path.stem] = len(records) if isinstance(records, dict) else 0

        for shard, count in staged_counts.items():
            if int(declared.get(shard) or 0) != count:
                return False, (
                    f"playback index says shard {shard} holds "
                    f"{declared.get(shard)} records, staged shard holds {count}"
                )
        total = int(index.get("count") or 0)
        if total < sum(staged_counts.values()):
            return False, (
                f"playback index total {total} is smaller than the "
                f"{sum(staged_counts.values())} records staged"
            )
        return True, "playback index agrees with its shards"

    def _validate_manifest_matches_events(self) -> Tuple[bool, str]:
        manifest_entries = self.of_kind("manifest")
        if not manifest_entries:
            return True, "no staged manifest"
        _, manifest = manifest_entries[0]
        if not isinstance(manifest, dict):
            return False, "staged manifest is not a mapping"

        staged_events: Dict[str, int] = {}
        for path, payload in self.of_kind("event"):
            key = "today_match" if "today" in path.name else "upcoming"
            count = 0
            if isinstance(payload, dict):
                for list_key in ("items", "events", "matches"):
                    values = payload.get(list_key)
                    if isinstance(values, list):
                        count = len(values)
                        break
            staged_events[key] = count

        for key, count in staged_events.items():
            entry = manifest.get(key)
            if not isinstance(entry, dict):
                return False, f"staged manifest has no {key} entry"
            if int(entry.get("count") or 0) != count:
                return False, (
                    f"staged manifest counts {entry.get('count')} {key} events "
                    f"while the staged file holds {count}"
                )
        return True, "staged manifest agrees with the staged event files"

    # -- slot selection ---------------------------------------------------
    def live_pointer(self) -> Dict[str, Any]:
        if self.data_root is None:
            return {}
        try:
            payload = json.loads(
                (Path(self.data_root) / POINTER_FILE).read_text(encoding="utf-8")
            )
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _published_slot_file(self, name: str) -> Optional[Any]:
        """The live snapshot's copy of one slot file, for carrying forward.

        The live slot is preferred over the flat mirror: it is what the current
        pointer actually names, so it is the copy readers are on.
        """
        if self.data_root is None:
            return None
        root = Path(self.data_root)
        pointer = self.live_pointer().get("snapshot")
        candidates: List[Path] = []
        if isinstance(pointer, dict):
            slot = str(pointer.get("slot") or "").strip()
            if slot:
                candidates.append(root / SNAPSHOT_DIRECTORY / slot / name)
        candidates.append(root / name)
        for path in candidates:
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
        return None

    def choose_slot(self) -> Tuple[str, int]:
        """The next slot in the round-robin, and its generation number.

        The live slot is read from the pointer that is currently published, so
        the slot chosen here is never the one a reader is following.
        """
        pointer = self.live_pointer().get("snapshot")
        current_slot = ""
        generation = 0
        if isinstance(pointer, dict):
            current_slot = str(pointer.get("slot") or "")
            try:
                generation = int(pointer.get("generation") or 0)
            except (TypeError, ValueError):
                generation = 0
        if current_slot in SNAPSHOT_SLOTS:
            index = (SNAPSHOT_SLOTS.index(current_slot) + 1) % len(SNAPSHOT_SLOTS)
        else:
            index = 0
        return SNAPSHOT_SLOTS[index], generation + 1

    # -- commit -----------------------------------------------------------
    def commit(self) -> Dict[str, Any]:
        """Publish the snapshot into a fresh slot, then move the pointer once."""
        if self.data_root is None:
            raise ValueError("SnapshotPublisher needs data_root to publish a snapshot")

        root = Path(self.data_root)
        token = f"{os.getpid()}.{time.time_ns()}"
        self.slot, self.generation = self.choose_slot()
        slot_dir = root / SNAPSHOT_DIRECTORY / self.slot

        slot_payloads: Dict[str, Any] = {}
        outside: List[_StagedFile] = []
        manifest_entry: Optional[_StagedFile] = None
        for entry in sorted(self.files.values(), key=lambda e: (e.order, str(e.target))):
            if entry.kind == "manifest":
                manifest_entry = entry
            elif entry.target.name in SLOT_FILES and entry.kind == SLOT_FILES[entry.target.name]:
                slot_payloads[entry.target.name] = entry.payload
            else:
                outside.append(entry)

        # 1. A slot is only useful if it is whole. A scan that publishes no
        #    events - requirement 15 refused them, or the mode never touched
        #    them - carries the live snapshot's copies forward, so the pointer
        #    always names a complete set rather than a slot with holes in it.
        carried_forward: List[str] = []
        for name in sorted(SLOT_FILES):
            if name in slot_payloads:
                continue
            payload = self._published_slot_file(name)
            if payload is not None:
                slot_payloads[name] = payload
                carried_forward.append(name)

        # 2. The pointer this switch will publish. Built first so the identical
        #    document can also be stored inside the slot, which makes rollback a
        #    single copy of one file back over data/manifest.json.
        pointer_payload: Optional[Dict[str, Any]] = None
        if manifest_entry is not None and isinstance(manifest_entry.payload, dict):
            pointer_payload = dict(manifest_entry.payload)
            pointer_payload["snapshot"] = {
                "slot": self.slot,
                "generation": self.generation,
                "id": f"{self.slot}-{self.generation}",
                "directory": f"data/{SNAPSHOT_DIRECTORY}/{self.slot}",
                "published_at": self.timestamp,
                "switch": "one os.replace() of data/manifest.json",
                "mirrored_flat_paths": True,
            }
            for name in sorted(slot_payloads):
                url = f"data/{SNAPSHOT_DIRECTORY}/{self.slot}/{name}"
                key = {"today-match.json": "today_match", "upcoming.json": "upcoming"}.get(name)
                if key and isinstance(pointer_payload.get(key), dict):
                    pointer_payload[key] = {**pointer_payload[key], "url": url}
                elif name == "allowed-hosts.json":
                    pointer_payload["allowed_hosts_url"] = url
                elif name == "playback-sources.json":
                    pointer_payload["playback_catalog_url"] = url

        # 3. Everything that is not part of the switch: the playback shards and
        #    anything else a caller staged. Append-only by construction (see the
        #    module docstring), so writing it while the previous pointer is still
        #    live cannot contradict the snapshot a reader is on.
        for entry in outside:
            _write_json(entry.target, entry.payload)

        # 4. The complete new snapshot, into a slot nobody is reading. Staged in
        #    a scratch directory and renamed in, so the slot is never half built.
        staging = slot_dir.with_name(f".{self.slot}.{token}.stage")
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True, exist_ok=True)
        try:
            for name, payload in slot_payloads.items():
                _write_json(staging / name, payload)
            if pointer_payload is not None:
                _write_json(staging / POINTER_FILE, pointer_payload)
            if slot_dir.exists():
                shutil.rmtree(slot_dir, ignore_errors=True)
            slot_dir.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staging, slot_dir)
            _fsync_directory(slot_dir.parent)
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)

        # 5. THE SWITCH. One os.replace() of one regular file. Until this line
        #    every reader follows the previous manifest to the previous slot;
        #    after it every reader follows the new manifest to a slot that is
        #    already complete and already validated.
        if pointer_payload is not None:
            _atomic_replace_file(root / POINTER_FILE, pointer_payload)

        # 6. After the switch: the compatibility mirror at the flat paths, and
        #    only now the deletions the previous pointer no longer protects.
        mirrored = 0
        for name, payload in slot_payloads.items():
            _write_json(root / name, payload)
            mirrored += 1

        removed = 0
        for path in self.deletions:
            try:
                path.unlink()
                removed += 1
            except OSError:
                continue

        self.committed = True
        return {
            "files": len(self.files),
            "slot": self.slot,
            "generation": self.generation,
            "id": f"{self.slot}-{self.generation}",
            "slot_files": len(slot_payloads) + (1 if manifest_entry else 0),
            "outside_switch": len(outside),
            "mirrored": mirrored,
            "carried_forward": carried_forward,
            "deleted": removed,
            "pointer": f"data/{POINTER_FILE}",
            "kinds": sorted({entry.kind for entry in self.files.values()}),
        }

    def abandon(self) -> None:
        """Throw the staged snapshot away; production keeps what it has."""
        self.files.clear()
        self.deletions.clear()


def _write_json(path: Path, payload: Any) -> None:
    """One file, written through a same-directory temporary and fsynced."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        with temp.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            try:
                temp.unlink()
            except OSError:
                pass


def _atomic_replace_file(path: Path, payload: Any) -> None:
    """The pointer switch: write beside the target, fsync, one rename."""
    _write_json(path, payload)
    _fsync_directory(path.parent)


def read_snapshot_pointer(data_root: Path | str = "data") -> Dict[str, Any]:
    """The live snapshot descriptor, for tooling and tests."""
    try:
        payload = json.loads(
            (Path(data_root) / POINTER_FILE).read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    snapshot = payload.get("snapshot")
    return snapshot if isinstance(snapshot, dict) else {}


def _fsync_directory(directory: Path) -> None:
    if not hasattr(os, "O_DIRECTORY"):  # Windows has no directory fsync
        return
    try:
        fd = os.open(str(directory), os.O_DIRECTORY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)
