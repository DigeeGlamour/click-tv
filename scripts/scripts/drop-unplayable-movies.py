#!/usr/bin/env python3
"""Remove published movies that a browser could not play and whose route is dead.

The scanner already does this: a title whose route stops verifying is carried
for one scan of grace and then dropped. What it cannot do is act between scans,
and the movie scan runs once a day into a schedule that delivers a fraction of
what it asks for - so a title whose host went dark stays on the site, looking
playable, for as long as a day.

Measured on 2026-08-30: the owner's full UI audit found 262 movie failures, and
247 of them sit on wrtgbn.b-cdn.net, a host that answers HTTP 403 to every
request from here regardless of referer, and answered 403 to the CI runner too.
Three independent measurements agreeing, and the card still said play me.

Two conditions, both required, so this can never remove a title on one bad
signal:

  1. the UI audit recorded it as FAIL - a real Chrome click that produced no
     decoded frame;
  2. its route does not answer 200/206 when asked directly from here, with the
     headers the playback catalogue stores for it.

A movie that failed the audit but whose route answers is left alone: the audit
runs four browsers at once on one connection and a timeout there is not proof
of anything. A movie that answers but was never audited is left alone too.

Run with --dry-run first.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import glob
import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36")


def _context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def playback_records() -> Dict[str, Any]:
    found: Dict[str, Any] = {}
    for path in glob.glob(os.path.join(ROOT, "data", "playback", "*.json")):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                found.update(json.load(handle).get("records") or {})
        except (OSError, ValueError):
            continue
    return found


def audit_failures(report_path: str) -> set:
    try:
        with open(report_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return set()
    return {
        str(row.get("name") or "")
        for row in payload.get("results") or ()
        if row.get("kind") == "movie" and row.get("status") == "FAIL"
    }


def probe(item: Dict[str, Any]) -> Dict[str, Any]:
    headers = dict(item.get("headers") or {})
    headers.setdefault("User-Agent", UA)
    headers["Range"] = "bytes=0-1023"
    url = urllib.parse.quote(str(item["url"]).split("|", 1)[0], safe=":/?&=%#")
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url, headers=headers), timeout=20, context=_context()
        ) as response:
            response.read(64)
            return dict(item, status=response.status)
    except urllib.error.HTTPError as failure:
        return dict(item, status=failure.code)
    except Exception as failure:  # noqa: BLE001 - an unreachable host is data
        return dict(item, status=0, error=type(failure).__name__)


def _rebuild_index(directory: str, *, dry_run: bool = False) -> Any:
    """Make a category's index.json agree with the pages actually on disk.

    Counts, page totals and status breakdowns are all recomputed from the page
    files rather than adjusted, so the index cannot drift from them.
    """
    index_path = os.path.join(directory, "index.json")
    if not os.path.isfile(index_path):
        return None
    with open(index_path, "r", encoding="utf-8") as handle:
        index = json.load(handle)

    pages: List[Dict[str, Any]] = []
    total = 0
    totals: Dict[str, int] = {}
    manual_total = 0
    for number, path in enumerate(
        sorted(glob.glob(os.path.join(directory, "page-*.json"))), start=1
    ):
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        movies = payload.get("movies") or payload.get("items") or []
        counts: Dict[str, int] = {}
        manual = 0
        for movie in movies:
            status = str(movie.get("verification_status") or "unknown")
            counts[status] = counts.get(status, 0) + 1
            totals[status] = totals.get(status, 0) + 1
            manual += int(bool(movie.get("manual_trusted")))
        manual_total += manual
        total += len(movies)
        pages.append({
            "page": number,
            "file": os.path.basename(path),
            "path": os.path.relpath(path, ROOT).replace(os.sep, "/"),
            "count": len(movies),
            "manual_trusted_count": manual,
            "status_counts": counts,
        })

    index["count"] = total
    index["total_pages"] = len(pages)
    index["status_counts"] = totals
    index["manual_trusted_count"] = manual_total
    index["pages"] = pages
    if not dry_run:
        with open(index_path, "w", encoding="utf-8") as handle:
            json.dump(index, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    return total, len(pages)


def main(argv: Any = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit", required=True,
                        help="a full_live_playback_audit.py report")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--out", default="reports/dropped-unplayable-movies.json")
    args = parser.parse_args(argv)

    failed = audit_failures(args.audit)
    if not failed:
        print("no movie failures in %s; nothing to do" % args.audit)
        return 0
    print("audit recorded %d movie failure(s)" % len(failed))

    records = playback_records()
    candidates: List[Dict[str, Any]] = []
    for path in sorted(glob.glob(os.path.join(ROOT, "data", "movies", "**", "*.json"),
                                 recursive=True)):
        if os.path.basename(path) == "index.json":
            continue
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError):
            continue
        for movie in payload.get("movies") or payload.get("items") or ():
            if not isinstance(movie, dict):
                continue
            name = str(movie.get("name") or movie.get("title") or "")
            if name not in failed:
                continue
            record = records.get(str(movie.get("playback_id") or "")) or {}
            url = movie.get("url") or record.get("url")
            if not url:
                continue
            candidates.append({"file": path, "name": name, "url": url,
                               "headers": record.get("headers") or {}})

    print("of those, %d are published with a route to check" % len(candidates))
    if not candidates:
        return 0

    checked: List[Dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        for result in pool.map(probe, candidates):
            checked.append(result)

    doomed = {row["name"] for row in checked if row["status"] not in (200, 206)}
    spared = [row for row in checked if row["status"] in (200, 206)]
    print("route does not answer: %d  -> will be removed" % len(doomed))
    print("route answers anyway  : %d  -> left alone (audit ran four browsers "
          "at once; a timeout there proves nothing)" % len(spared))

    by_host: Dict[str, int] = {}
    for row in checked:
        if row["name"] in doomed:
            host = urllib.parse.urlsplit(row["url"]).hostname or "?"
            by_host[host] = by_host.get(host, 0) + 1
    for host, count in sorted(by_host.items(), key=lambda pair: -pair[1]):
        print("   %-44s %4d" % (host[:44], count))

    removed_rows: List[Dict[str, Any]] = []
    files_changed = 0
    for path in sorted({row["file"] for row in checked if row["name"] in doomed}):
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        key = "movies" if "movies" in payload else "items"
        before = payload.get(key) or []
        after = [
            movie for movie in before
            if str(movie.get("name") or movie.get("title") or "") not in doomed
        ]
        if len(after) == len(before):
            continue
        removed_rows.extend(
            {"name": str(m.get("name") or m.get("title") or ""),
             "file": os.path.relpath(path, ROOT)}
            for m in before if m not in after
        )
        if args.dry_run:
            files_changed += 1
            continue
        payload[key] = after
        if "count" in payload:
            payload["count"] = len(after)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        files_changed += 1

    # Every movie category is paginated: index.json carries the total, the page
    # count and a per-page breakdown, and the Pages validator refuses a build
    # where those disagree with the pages on disk. Removing titles without
    # rebuilding it fails the build - which is how Cloudflare ends up serving
    # yesterday's data while every push looks fine.
    touched_dirs = sorted({os.path.dirname(row["file"]) for row in checked
                           if row["name"] in doomed})
    for directory in touched_dirs:
        rebuilt = _rebuild_index(directory, dry_run=args.dry_run)
        if rebuilt is not None:
            print("index %-22s -> %d movie(s), %d page(s)"
                  % (os.path.basename(directory), rebuilt[0], rebuilt[1]))

    if not args.dry_run:
        report = {
            "mode": "dropped_unplayable_movies",
            "note": ("Removed only where a real-Chrome click produced no decoded "
                     "frame AND the route did not answer. The scanner removes "
                     "these on its next movie scan; this closes the gap."),
            "audit": args.audit,
            "removed_count": len(removed_rows),
            "by_host": by_host,
            "removed": sorted(removed_rows, key=lambda row: row["name"]),
        }
        out = args.out if os.path.isabs(args.out) else os.path.join(ROOT, args.out)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=1)
            handle.write("\n")

    print("\n%s: %d movie(s) removed across %d file(s)"
          % ("dry run" if args.dry_run else "done", len(removed_rows), files_changed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
