#!/usr/bin/env python3
"""One broken card is taken out; the catalogue it sits in still publishes.

    python scripts/quarantine-bad-items.py                  # repository data/
    python scripts/quarantine-bad-items.py --data path/to/data --dry-run

The fault this exists for
-------------------------
`scripts/validate-pages.py` checks every published item before a build is
allowed out, and it is right to: a card with no name, an insecure URL, a
second card claiming the same film. But it reported every one of those as a
build error, and one build error failed the whole run. So one malformed
channel among hundreds held back the Today Match scan, every channel and
every film with it - and the next scan, producing the same card, failed the
same way.

What it does
------------
Runs the validator's own per-item checks - imported, not copied, so the two
cannot disagree - over each item before the build, and removes only the items
that would fail: the card itself, and a later duplicate of a card already
kept. Every count the validator cross-checks is brought back into line (page,
category index, manifest; both copies of an event file), and every removal is
written to `reports/item-quarantine.json` with the validator's own words for
why. Nothing is removed silently.

What it will not do
-------------------
Quarantine a catalogue. If more items fail than a broken card could explain -
`MAX_SHARE` of a surface, with a floor of `MIN_ALLOWANCE` - something is wrong
with the scan, not with one card, and removing that much would be publishing a
hollowed-out site. It then changes nothing and exits non-zero, so the build
fails exactly as before and the previous catalogue stays live.

Structural faults - a missing file, a broken manifest, a count that no single
item explains - are not item faults and are left to the validator.
"""
from __future__ import annotations

import argparse
import glob
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]

#: More than this share of a surface failing is a scan fault, not a bad card.
MAX_SHARE = 0.05
#: ...but a small surface may always lose this many before that is assumed.
MIN_ALLOWANCE = 10


def _load_validator():
    spec = importlib.util.spec_from_file_location(
        "clicktv_validate_pages", ROOT / "scripts" / "validate-pages.py")
    module = importlib.util.module_from_spec(spec)
    saved = sys.argv
    try:
        sys.argv = [saved[0]]  # the validator reads its build root from argv
        spec.loader.exec_module(module)
    finally:
        sys.argv = saved
    return module


VALIDATOR = _load_validator()


def item_problems(item: Any, label: str, *, media_kind: str,
                  allow_metadata_only: bool = False) -> List[str]:
    """The validator's verdict on one item, without touching its error list."""
    errors = VALIDATOR.ERRORS
    mark = len(errors)
    try:
        VALIDATOR.validate_stream_item(
            item, label, allow_metadata_only=allow_metadata_only,
            media_kind=media_kind)
    except Exception as error:  # noqa: BLE001 - a crash on one item is that item's fault
        errors.append(f"{label} could not be validated: {error}")
    found = list(errors[mark:])
    del errors[mark:]
    return found


def _read(path: Path) -> Any:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _write(path: Path, payload: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".quarantine-tmp")
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(temporary, path)


def _items_key(data: Any, *keys: str) -> Optional[str]:
    if not isinstance(data, dict):
        return None
    for key in keys:
        if isinstance(data.get(key), list):
            return key
    return None


def screen(items: List[Any], label: str, *, media_kind: str,
           allow_metadata_only: bool = False,
           identity: Optional[Callable[[Dict[str, Any]], str]] = None,
           seen_names: Optional[Dict[str, str]] = None,
           seen_urls: Optional[Dict[str, str]] = None,
           ) -> Tuple[List[Any], List[Dict[str, Any]]]:
    """(kept, removed) for one list, using the validator's own rules.

    Duplicates follow the validator: the first card keeps the name and the
    URL, a later card claiming either is the one removed. `seen_*` may be
    shared across the pages of one category, because that is the scope the
    validator checks duplicates in.
    """
    seen_names = {} if seen_names is None else seen_names
    seen_urls = {} if seen_urls is None else seen_urls
    kept: List[Any] = []
    removed: List[Dict[str, Any]] = []
    for number, item in enumerate(items, start=1):
        problems = item_problems(item, f"{label} #{number}", media_kind=media_kind,
                                 allow_metadata_only=allow_metadata_only)
        if not problems and isinstance(item, dict) and identity is not None:
            name_key = identity(item)
            url_key = VALIDATOR.normalized_primary_url(item)
            if name_key and name_key in seen_names:
                problems.append(f"duplicate name of {seen_names[name_key]!r}")
            if url_key and url_key in seen_urls:
                problems.append(f"duplicate URL of {seen_urls[url_key]!r}")
            if not problems:
                name = str(item.get("name") or item.get("title") or "").strip()
                if name_key:
                    seen_names[name_key] = name
                if url_key:
                    seen_urls[url_key] = name
        if problems:
            removed.append({
                "id": str(item.get("id") or "") if isinstance(item, dict) else "",
                "name": str(item.get("name") or "") if isinstance(item, dict) else "",
                "reasons": problems,
            })
        else:
            kept.append(item)
    return kept, removed


class Plan:
    """Every change, held until the whole run has decided it is safe."""

    def __init__(self) -> None:
        self.writes: Dict[Path, Any] = {}
        self.removed: List[Dict[str, Any]] = []
        self.surface_totals: Dict[str, int] = {}
        self.surface_removed: Dict[str, int] = {}

    def note(self, surface: str, total: int, removed: List[Dict[str, Any]],
             where: str) -> None:
        self.surface_totals[surface] = self.surface_totals.get(surface, 0) + total
        self.surface_removed[surface] = self.surface_removed.get(surface, 0) + len(removed)
        for entry in removed:
            self.removed.append(dict(entry, surface=surface, file=where))

    def over_limit(self) -> List[str]:
        refusals = []
        for surface, total in self.surface_totals.items():
            count = self.surface_removed.get(surface, 0)
            allowance = max(MIN_ALLOWANCE, int(total * MAX_SHARE))
            if count > allowance:
                refusals.append(
                    f"{surface}: {count} of {total} items fail (allowance "
                    f"{allowance}) - a scan fault, not a bad card")
        return refusals


def _rel(path: Path, data_root: Path) -> str:
    try:
        return "data/" + path.relative_to(data_root).as_posix()
    except ValueError:
        return str(path)


def plan_channels(data_root: Path, manifest: Dict[str, Any], plan: Plan) -> None:
    section = manifest.get("channels") if isinstance(manifest, dict) else None
    if not isinstance(section, dict):
        return
    for category, slug in VALIDATOR.CHANNELS.items():
        entry = section.get(category)
        path = data_root / "channels" / f"{slug}.json"
        if not isinstance(entry, dict) or not path.is_file():
            continue
        data = _read(path)
        key = _items_key(data, "channels", "items")
        if key is None:
            continue
        items = data[key]
        kept, removed = screen(
            items, f"{category} channel", media_kind="channel",
            identity=lambda item: VALIDATOR.normalize_catalog_identity(
                item.get("name") or item.get("title")))
        plan.note("channels", len(items), removed, _rel(path, data_root))
        if removed:
            data[key] = kept
            if "count" in data:
                data["count"] = len(kept)
            entry["count"] = len(kept)
            plan.writes[path] = data


def plan_movies(data_root: Path, manifest: Dict[str, Any], plan: Plan) -> None:
    section = manifest.get("movies") if isinstance(manifest, dict) else None
    if not isinstance(section, dict):
        return
    for category, slug in VALIDATOR.MOVIES.items():
        entry = section.get(category)
        index_path = data_root / "movies" / slug / "index.json"
        if not isinstance(entry, dict) or not index_path.is_file():
            continue
        index = _read(index_path)
        pages = index.get("pages") if isinstance(index, dict) else None
        if not isinstance(pages, list):
            continue
        seen_names: Dict[str, str] = {}
        seen_urls: Dict[str, str] = {}
        total = 0
        category_changed = False
        for position, page_entry in enumerate(pages, start=1):
            if not isinstance(page_entry, dict):
                continue
            number = page_entry.get("page") or position
            page_path = data_root / "movies" / slug / f"page-{int(number):03d}.json"
            if not page_path.is_file():
                continue
            page = _read(page_path)
            key = _items_key(page, "items", "movies")
            if key is None:
                continue
            items = page[key]
            kept, removed = screen(
                items, f"{category} movie page {number}", media_kind="movie",
                identity=VALIDATOR.normalize_movie_identity,
                seen_names=seen_names, seen_urls=seen_urls)
            plan.note("movies", len(items), removed, _rel(page_path, data_root))
            total += len(kept)
            if removed:
                category_changed = True
                page[key] = kept
                if "count" in page:
                    page["count"] = len(kept)
                page_entry["count"] = len(kept)
                plan.writes[page_path] = page
        if category_changed:
            if "count" in index:
                index["count"] = total
            entry["count"] = total
            for page_path, page in list(plan.writes.items()):
                if page_path.parent == index_path.parent and "total_count" in page:
                    page["total_count"] = total
            plan.writes[index_path] = index


def plan_events(data_root: Path, plan: Plan, manifests: List[Tuple[Path, Any]]) -> None:
    for flat_name, manifest_key in (("today-match.json", "today_match"),
                                    ("upcoming.json", "upcoming")):
        copies = [data_root / flat_name] + [
            Path(p) for p in sorted(glob.glob(str(data_root / "snapshots" / "*" / flat_name)))]
        for path in copies:
            if not path.is_file():
                continue
            data = _read(path)
            key = _items_key(data, "items", "events")
            if key is None:
                continue
            items = data[key]
            kept, removed = screen(items, f"{manifest_key} event", media_kind="event",
                                   allow_metadata_only=True)
            # Count each surface once: the snapshot copies are the same items.
            if path == copies[0]:
                plan.note("events", len(items), removed, _rel(path, data_root))
            if removed:
                data[key] = kept
                if "count" in data:
                    data["count"] = len(kept)
                plan.writes[path] = data
                for manifest_path, manifest in manifests:
                    entry = manifest.get(manifest_key) if isinstance(manifest, dict) else None
                    if isinstance(entry, dict) and "count" in entry:
                        entry["count"] = len(kept)
                        entry["visible"] = len(kept) > 0 if "visible" in entry else entry.get("visible")
                        plan.writes[manifest_path] = manifest


def run(data_root: Path, *, dry_run: bool = False,
        report_path: Optional[Path] = None) -> int:
    manifest_path = data_root / "manifest.json"
    if not manifest_path.is_file():
        print("quarantine: no data/manifest.json - nothing to screen")
        return 0
    manifest = _read(manifest_path)
    manifests: List[Tuple[Path, Any]] = [(manifest_path, manifest)]

    # The item checks consult the playback catalogue ("is this playback_id
    # something the proxy can resolve?"), which the validator fills from its
    # build root. Point it at the tree being screened and fill it, keeping
    # none of the catalogue's own findings - those are structural, and the
    # validator proper reports them.
    VALIDATOR.ROOT = data_root.resolve().parent
    VALIDATOR.PLAYBACK_IDS.clear()
    mark_errors, mark_warnings = len(VALIDATOR.ERRORS), len(VALIDATOR.WARNINGS)
    try:
        VALIDATOR.validate_playback_catalog()
    except Exception as error:  # noqa: BLE001
        print(f"quarantine: playback catalogue unreadable ({error}); "
              "screening without it would misjudge every id-only card, so not screening")
        return 0
    del VALIDATOR.ERRORS[mark_errors:]
    del VALIDATOR.WARNINGS[mark_warnings:]
    for extra in sorted(glob.glob(str(data_root / "snapshots" / "*" / "manifest.json"))):
        manifests.append((Path(extra), _read(Path(extra))))

    plan = Plan()
    plan_channels(data_root, manifest, plan)
    plan_movies(data_root, manifest, plan)
    plan_events(data_root, plan, manifests)
    if plan.writes and manifest_path not in plan.writes and plan.removed:
        if any(e["surface"] in ("channels", "movies") for e in plan.removed):
            plan.writes[manifest_path] = manifest

    refusals = plan.over_limit()
    report = {
        "kind": "item_quarantine",
        "removed": plan.removed,
        "surface_totals": plan.surface_totals,
        "surface_removed": plan.surface_removed,
        "refused": refusals,
        "applied": bool(plan.removed) and not refusals and not dry_run,
    }
    for entry in plan.removed:
        print("::warning title=Quarantined (only this item is held back)::"
              f"{entry['surface']} {entry.get('id') or entry.get('name')} "
              f"in {entry['file']}: {'; '.join(entry['reasons'])[:300]}")
    if refusals:
        for refusal in refusals:
            print("::error title=Quarantine refused::" + refusal)
    elif plan.removed and not dry_run:
        for path, payload in plan.writes.items():
            _write(path, payload)
    print(f"quarantine: {len(plan.removed)} item(s) held back, "
          f"{'refused' if refusals else ('dry run' if dry_run else ('applied' if plan.removed else 'nothing to hold back'))}; "
          f"totals {plan.surface_totals}")

    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        _write(report_path, report)
    return 2 if refusals else 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default=str(ROOT / "data"))
    parser.add_argument("--report", default=str(ROOT / "reports" / "item-quarantine.json"))
    parser.add_argument("--dry-run", action="store_true")
    arguments = parser.parse_args(argv)
    return run(Path(arguments.data), dry_run=arguments.dry_run,
               report_path=Path(arguments.report) if arguments.report else None)


if __name__ == "__main__":
    raise SystemExit(main())
