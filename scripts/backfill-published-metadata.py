#!/usr/bin/env python3
"""Apply the metadata cache to the movie pages that are already published.

Why this exists
---------------
`scanner/movies.py` annotates every movie with the metadata cache during a
scan, and then writes the pages. When the private movie source returns far
fewer titles than are live, the output-safety guard keeps the published
catalogue and nothing is rewritten - which is correct, and which also means
metadata resolved since the last successful publish never reaches the site.
That is why the live catalogue carried no rating, no genre and no release
date at all while `state/movie-metadata-cache.json` held real TMDB records
for hundreds of the same titles.

This walks the published pages and applies the cache to them directly. It
is the same code path a scan uses - `movie_metadata_cache.enrich` with
`lookup=None`, so no provider is contacted and nothing is invented. Only
fields the cache really holds are written; a title the cache does not know
is left exactly as it is.

The poster-as-backdrop fallback that `enrich` applies in memory is NOT
written out. That stand-in is meant to live for the length of a render, and
`enrich` merges fill-only: a poster written into `backdrop` here would later
refuse the real backdrop when it arrives. The site already falls back to the
poster itself when a record has no backdrop.

Usage:  python scripts/backfill-published-metadata.py [--dry-run]
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from typing import Any, Dict, List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from scanner import movie_metadata_cache  # noqa: E402

MOVIES_ROOT = os.path.join(ROOT, "data", "movies")


def _page_files() -> List[str]:
    found: List[str] = []
    for category in sorted(os.listdir(MOVIES_ROOT)):
        folder = os.path.join(MOVIES_ROOT, category)
        if not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder)):
            if name.startswith("page-") and name.endswith(".json"):
                found.append(os.path.join(folder, name))
    return found


def _items(document: Dict[str, Any]) -> List[Dict[str, Any]]:
    for key in ("items", "movies"):
        value = document.get(key)
        if isinstance(value, list):
            return value
    return []


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    pages = _page_files()
    if not pages:
        print("no published movie pages found")
        return 1

    total = 0
    enriched = 0
    counts: Dict[str, int] = {}
    written = 0

    for path in pages:
        with open(path, "r", encoding="utf-8") as handle:
            document = json.load(handle)
        items = _items(document)
        if not items:
            continue
        before = copy.deepcopy(items)
        movie_metadata_cache.enrich(items, lookup=None, persist=False)

        changed_page = False
        for original, updated in zip(before, items):
            total += 1
            # The in-memory poster fallback is not part of the published
            # record; see the module docstring.
            if updated.get("backdrop_source") == "poster":
                for field in ("backdrop", "backdrop_source"):
                    if original.get(field) is None:
                        updated.pop(field, None)
                    else:
                        updated[field] = original[field]
            if original == updated:
                continue
            enriched += 1
            changed_page = True
            for field in updated:
                if original.get(field) != updated.get(field):
                    counts[field] = counts.get(field, 0) + 1

        if changed_page and not args.dry_run:
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(document, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            written += 1

    print(f"pages: {len(pages)}  items: {total}  enriched: {enriched}  written: {written}")
    for field, count in sorted(counts.items(), key=lambda pair: -pair[1]):
        print(f"   {field}: {count}")
    if args.dry_run:
        print("dry run - nothing was written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
