#!/usr/bin/env python3
"""Catch rebase merge damage in generated collections, before it is tidied away.

WHY THIS RUNS BEFORE reconcile-generated-counts.py
The push step ends with `git rebase -X theirs origin/main`. That is a
three-way merge per file, and `-X theirs` settles only the hunks that
CONFLICT - two non-conflicting regions of a pretty-printed JSON array are both
kept. A collection can therefore come out of the rebase holding one record
twice, or holding more records than the scalar `count` written beside it by
the run that produced it.

reconcile-generated-counts.py then recomputes every count from the collection
it describes. That is the right repair for a count, but applied to a merged
collection it does something worse than nothing: it makes the damaged file
self-consistent, so scripts/validate-pages.py accepts it and the duplicate
publishes. That is exactly how 2026-09-06 went - commit 90edb2faf shipped
data/today-match.json with 25 items under `"count": 24` and manifest count
reconciled up to 25, and the site served one match on two cards.

So the order is: rebase, restore this run's own files whole, LOOK, then
reconcile. This script is the LOOK.

WHAT IT DOES ABOUT WHAT IT FINDS
  duplicate id   -> exit 1. The push fails. Two records with one id cannot be
                    told apart by anything downstream, so nothing downstream
                    can repair it, and the locked grouping rule - one match,
                    one card - is already broken at that point.
  count mismatch -> reported loudly, exit 0. reconcile-generated-counts.py is
                    the designed repair and still runs. What must not happen
                    is the repair happening SILENTLY: a count that disagrees
                    with its own collection is the fingerprint of a merge, and
                    if it is only ever fixed and never mentioned, the merge is
                    invisible. It is printed here, and annotated so it reaches
                    the run summary.

It reads only. It repairs nothing.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Only machine-generated published collections. state/ and reports/ are not
# scanned: they are not arrays of identified records, and a false failure in
# the push step is a scan that never publishes.
SCAN_ROOTS = ("data",)

# A collection is a JSON object with an "items" list. Every one of the 101
# published collections has that shape, and 99 of them carry an "id" on each
# item; the two that do not (series index files) are simply skipped by the id
# check rather than special-cased.
ITEMS_KEY = "items"
COUNT_KEY = "count"


def annotate(level: str, message: str) -> None:
    """Print, and ask GitHub to surface it in the run summary if we are in CI."""
    print(message)
    single_line = message.replace("\n", " ")
    print(f"::{level}::{single_line}")


def collections_under(root: Path):
    for path in sorted(root.rglob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # Unreadable JSON is validate-pages.py's job to reject, with a far
            # better message than this script could give. Not this one's.
            continue
        if isinstance(payload, dict) and isinstance(payload.get(ITEMS_KEY), list):
            yield path, payload


def main() -> int:
    duplicates: list[str] = []
    mismatches: list[str] = []
    checked = 0

    for scan_root in SCAN_ROOTS:
        root = PROJECT_ROOT / scan_root
        if not root.is_dir():
            continue
        for path, payload in collections_under(root):
            checked += 1
            items = payload[ITEMS_KEY]
            relative = path.relative_to(PROJECT_ROOT).as_posix()

            declared = payload.get(COUNT_KEY)
            if isinstance(declared, int) and declared != len(items):
                mismatches.append(
                    f"{relative}: {len(items)} items under \"count\": {declared}"
                )

            ids = [
                str(item.get("id") or "").strip()
                for item in items
                if isinstance(item, dict)
            ]
            repeated = [
                identity
                for identity, times in Counter(i for i in ids if i).items()
                if times > 1
            ]
            for identity in sorted(repeated):
                positions = [
                    number
                    for number, value in enumerate(ids, start=1)
                    if value == identity
                ]
                duplicates.append(f"{relative}: id {identity!r} at {positions}")

    print(f"merge corruption check: {checked} generated collection(s)")

    if mismatches:
        annotate(
            "warning",
            "A generated collection disagrees with its own count. That is the "
            "fingerprint of a rebase merge, not of a scan. reconcile-generated"
            "-counts.py will make the numbers agree, which is why it is being "
            "said out loud first:\n  " + "\n  ".join(mismatches),
        )
    else:
        print("  every collection agrees with its own count")

    if duplicates:
        annotate(
            "error",
            "A generated collection holds the same id twice. One match, one "
            "card is a locked rule, and nothing downstream can tell the two "
            "copies apart - so this push is refused rather than reconciled "
            "into looking correct:\n  " + "\n  ".join(duplicates),
        )
        return 1

    print("  no duplicate ids")
    return 0


if __name__ == "__main__":
    sys.exit(main())
