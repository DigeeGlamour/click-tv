#!/usr/bin/env python3
"""Refresh movie discovery without rescanning a single stream (PART 10).

Refreshing "what is trending internationally" needs two HTTP requests. The
full movie scan needs forty minutes, thousands of link probes and the
private source repository, and it is scheduled once a day for good reasons.
Tying the first to the second means trending is a day stale by design.

So this is a separate, deliberately small job:

  read the ALREADY-PUBLISHED catalogue off disk
    -> refresh the TMDB weekly trending snapshot   (2 requests)
    -> rebuild the derived discovery files          (no requests at all)

It never verifies a link, never touches a stream URL, never loads the
manual/private sources, and never writes anything under data/movies/<category>/.
The catalogue it reads is whatever the last real scan published.

FAILURE RULE: every writer this calls already keeps last-good data on
failure - a trending fetch that fails reuses the stored snapshot, and a
genre index that comes back empty keeps the one on disk. This script adds
one more guard on top: if the published catalogue cannot be read at all, it
exits without writing anything, because "no catalogue" must never be
mistaken for "an empty catalogue".

Exit codes: 0 refreshed, 0 also for "nothing to do", 1 only for a real
failure that wrote nothing.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scanner import movie_discovery  # noqa: E402
from scanner import movie_featured  # noqa: E402
from scanner import movie_genre_index  # noqa: E402
from scanner import movie_trending  # noqa: E402
from scanner import provider_health  # noqa: E402

#: Resolved against the WORKING DIRECTORY, not this file's location.
#:
#: The writers below all use relative output paths ("data/movies/discovery"),
#: so anchoring the reader to __file__ instead would have this script read
#: one checkout and write into another whenever it is run from anywhere but
#: the repository root - the same class of bug scanner/paths.py exists to
#: prevent, and one a test caught here.
MOVIES_ROOT = Path(os.environ.get("CLICKTV_MOVIES_ROOT") or Path("data") / "movies")

#: Directories under data/movies/ that are outputs, not categories.
_NON_CATEGORY_DIRS = {"discovery", "genres"}


def load_published_catalogue(root: Path = MOVIES_ROOT) -> Dict[str, Any]:
    """The published catalogue, in the shape process_movies() returns.

    Read-only: this is the output of the last real scan, and nothing here
    may alter it.
    """
    paginated: Dict[str, Any] = {}
    if not root.exists():
        return paginated
    for category_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        if category_dir.name in _NON_CATEGORY_DIRS:
            continue
        pages: Dict[str, Any] = {}
        for page_file in sorted(category_dir.glob("page-*.json")):
            try:
                pages[page_file.name] = json.loads(page_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                # A single unreadable page is not a reason to abandon the
                # refresh; the rest of the catalogue is still valid.
                continue
        if pages:
            paginated[category_dir.name] = {"index": {}, "page_contents": pages}
    return paginated


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-trending",
        action="store_true",
        help="rebuild the derived files only, make no external request at all",
    )
    args = parser.parse_args(argv)

    print("Movie discovery refresh (no stream scanning)")

    paginated = load_published_catalogue()
    movie_count = sum(1 for _ in movie_discovery.iter_published_movies(paginated))
    if not paginated or not movie_count:
        # "No catalogue" is not "an empty catalogue". Writing derived files
        # from nothing would blank every discovery row at once.
        print("   published catalogue is empty or unreadable - nothing written")
        return 1
    print(f"   read {movie_count} published movie(s) from {len(paginated)} category folder(s)")

    if args.skip_trending:
        print("   trending refresh skipped by request")
    else:
        summary = movie_trending.generate(paginated)
        if summary.get("skipped"):
            print(f"   trending: {summary['skipped']}")
        else:
            print(
                f"   trending: {summary.get('matched', 0)} of "
                f"{summary.get('external_items', 0)} external titles playable here "
                f"({summary.get('freshness', 'unknown')})"
            )

    genres = movie_genre_index.generate(paginated)
    print(
        f"   genres: {genres.get('total_indexed', 0)} entries across "
        f"{genres.get('written', 0)} index file(s)"
        + (f", {genres['preserved']} kept at last-good" if genres.get("preserved") else "")
    )

    just_added = movie_discovery.generate_just_added(paginated)
    print(f"   just added: {just_added['count']} film(s) in the last {just_added['window_days']} days")

    latest = movie_discovery.generate_latest(paginated)
    excluded = latest.get("excluded", {})
    print(
        f"   latest: {latest['count']} of {latest['eligible']} with a real release date "
        f"({excluded.get('no_exact_release_date', 0)} without one)"
    )

    index = movie_discovery.generate_search_index(paginated)
    print(
        f"   browse index: {index['count']} title(s)"
        + (" (kept at last-good)" if index.get("preserved") else "")
    )

    # Featured before home, because home.json's Featured row is read from
    # featured.json. Twice a day is exactly the ~12h refresh the Featured
    # plan asks for, which is why this job is extended rather than a third
    # workflow being added beside it.
    featured = movie_featured.generate(paginated)
    document = featured.get("document") or {}
    if featured.get("preserved"):
        print(f"   featured: {featured['reason']} ({featured['count']} item(s) kept)")
    else:
        print(
            f"   featured: {document.get('count', 0)} of {document.get('slots', 0)} slot(s) "
            f"({document.get('manual_count', 0)} manual, {document.get('auto_count', 0)} auto)"
            + (f" - {document['shortfall_reason']}" if document.get("shortfall_reason") else "")
        )

    home = movie_discovery.generate_home(paginated)
    print(f"   home: {home['counts']} ({home['featured_status']})")

    provider_health.save()
    line = provider_health.summary_line()
    if line:
        print(f"   providers: {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
