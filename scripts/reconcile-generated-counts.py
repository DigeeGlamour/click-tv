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

from scanner.output import _reconcile_manifest_counts  # noqa: E402


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


def main() -> int:
    # An optional repository root argument keeps this testable against a
    # throwaway directory; real callers (the GitHub Actions workflow, the
    # local PC scan script) never pass one and it defaults to this repo.
    repo_root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT
    data_root = repo_root / "data"
    changed = []

    manifest_path = data_root / "manifest.json"
    if manifest_path.is_file():
        before = manifest_path.read_text(encoding="utf-8")
        manifest = json.loads(before) if before.strip() else {}
        _reconcile_manifest_counts(manifest, data_root)
        if _write_if_changed(manifest_path, before, manifest):
            changed.append("data/manifest.json")

    catalog_path = data_root / "playback-sources.json"
    if catalog_path.is_file():
        before = catalog_path.read_text(encoding="utf-8")
        catalog = json.loads(before) if before.strip() else {}
        records = catalog.get("records")
        if isinstance(records, dict):
            actual = len(records)
            if catalog.get("count") != actual:
                catalog["count"] = actual
        if _write_if_changed(catalog_path, before, catalog):
            changed.append("data/playback-sources.json")

    if changed:
        print("Reconciled stale counts in: " + ", ".join(changed))
    else:
        print("No count drift found; nothing to reconcile.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
