#!/usr/bin/env python3
"""Fold Click TV usage events into the Popular on Click TV row.

    python scripts/aggregate-movie-analytics.py --events exported.json
    python scripts/aggregate-movie-analytics.py --events-url https://telemetry.example/events --token "$EXPORT_TOKEN"

Reads events (a local export, or the telemetry worker's /events endpoint),
folds them into state/movie-analytics-aggregate.json as day-bucketed counts,
and writes data/movies/discovery/popular-clicktv.json.

Two refusals are deliberate. With no events reachable it writes nothing at
all rather than publishing an empty row over a good one - an analytics outage
must not erase yesterday's correct answer. And a title that is not in the
published catalogue is left out, so the row can never send someone to
something they cannot watch.

The token is read from the argument or the environment and is never written
to any output file.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import glob
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, List, Mapping

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scanner import movie_popularity  # noqa: E402

MOVIES_ROOT = os.environ.get("CLICKTV_MOVIES_ROOT") or os.path.join("data", "movies")
REQUEST_TIMEOUT_SECONDS = 20


def published_catalogue(root: str = MOVIES_ROOT) -> Dict[str, Dict[str, Any]]:
    """id -> published record, for every movie currently on the site.

    Reading what is published (rather than a state file) means a withdrawn
    title is absent here for exactly as long as it is absent from the site.
    """
    catalogue: Dict[str, Dict[str, Any]] = {}
    for path in sorted(glob.glob(os.path.join(root, "*", "page-*.json"))):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError):
            continue
        items = payload if isinstance(payload, list) else payload.get("items") or []
        for item in items if isinstance(items, list) else ():
            if not isinstance(item, dict):
                continue
            if item.get("is_active") is False:
                continue
            item_id = str(item.get("id") or "").strip()
            if item_id:
                catalogue[item_id] = item

    for path in sorted(glob.glob(os.path.join("data", "series", "*", "index.json"))):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError):
            continue
        for item in payload.get("items") or ():
            if isinstance(item, dict):
                item_id = str(item.get("id") or "").strip()
                if item_id:
                    catalogue[item_id] = item
    return catalogue


def read_events_file(path: str) -> List[Mapping[str, Any]]:
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, Mapping)]
    for key in ("events", "items", "reports"):
        rows = payload.get(key) if isinstance(payload, Mapping) else None
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, Mapping)]
    return []


def read_events_url(url: str, token: str) -> List[Mapping[str, Any]]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        payload = json.loads(response.read().decode("utf-8"))
    rows = payload.get("events") if isinstance(payload, Mapping) else None
    return [row for row in rows or () if isinstance(row, Mapping)]


def main() -> int:
    parser = argparse.ArgumentParser(description="Aggregate Click TV usage events.")
    parser.add_argument("--events", help="path to an exported events JSON file")
    parser.add_argument("--events-url", help="telemetry worker /events endpoint")
    parser.add_argument("--token", default=os.environ.get("TELEMETRY_EXPORT_TOKEN", ""),
                        help="bearer token for --events-url (or TELEMETRY_EXPORT_TOKEN)")
    parser.add_argument("--state", default=movie_popularity.DEFAULT_STATE_PATH)
    parser.add_argument("--output", default=movie_popularity.DEFAULT_OUTPUT_PATH)
    parser.add_argument("--movies-root", default=MOVIES_ROOT)
    args = parser.parse_args()

    print("Popular on Click TV - internal usage aggregation")

    events: List[Mapping[str, Any]] = []
    if args.events:
        try:
            events = read_events_file(args.events)
        except (OSError, ValueError) as error:
            print(f"   could not read {args.events}: {error}")
            return 1
    elif args.events_url:
        try:
            events = read_events_url(args.events_url, args.token)
        except (urllib.error.URLError, ValueError, OSError) as error:
            # Deliberately not a partial run: no events is not zero events.
            print(f"   events endpoint unreachable: {error}")
            print("   nothing written; the existing row is left as it is")
            return 1
    else:
        print("   no --events or --events-url given; nothing to aggregate")
        return 1

    print(f"   read {len(events)} raw event(s)")

    existing = movie_popularity.load_aggregate(args.state)
    store = movie_popularity.aggregate_events(events, existing=existing)
    counted = sum(len(rows) for rows in store["days"].values())
    print(f"   aggregate: {len(store['days'])} day bucket(s), {counted} title-day row(s)")
    movie_popularity.save_aggregate(store, args.state)

    catalogue = published_catalogue(args.movies_root)
    print(f"   catalogue: {len(catalogue)} active title(s)")

    document = movie_popularity.build_popular(store, catalogue=catalogue)
    written = movie_popularity.write_popular(document, args.output)
    print(
        f"   popular: {document['count']} title(s) with at least "
        f"{document['minimum_qualified_plays']} qualified play(s) in "
        f"{document['window_days']} days"
        + (f", {document['excluded_inactive']} excluded as inactive"
           if document["excluded_inactive"] else "")
    )
    if not written:
        print("   nothing published: kept the existing row rather than blanking it")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
