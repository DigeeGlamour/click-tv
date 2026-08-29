#!/usr/bin/env python3
"""Every card that a past commit published and the current one does not, with a reason.

"The catalogue went from 653 to 610" is not a finding, it is a number. What the
number needs is a row per card saying whether the route is gone, whether it is
gone only from the machine that asked, or whether it was refused on quality -
because those three call for three different actions, and only one of them is a
regression.

Reasons come from measurement, not from the old card's stored verdict:

  * the exact route the older commit published is re-fetched now, from wherever
    this runs;
  * where it answers, the height is read out of the media - the HLS master's
    RESOLUTION, or the H.264 SPS decoded from the first transport-stream
    segment - so "below the floor" is a measured claim and not a guess;
  * a card whose route is protected has no public URL to re-probe and is
    reported as exactly that rather than being scored.

The vantage matters and is recorded: run from a GitHub runner this answers "is
it reachable from CI", run from Dhaka it answers "is it reachable for the
audience". The two disagree, which is the point.

Usage:
    python scripts/removed-card-audit.py --before 150d3487c [--after HEAD] \
        [--vantage bangladesh-residential] [--out reports/removed-card-audit.json]
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures
import json
import os
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from scanner import media_probe  # noqa: E402

CATEGORY_FILES = (
    "bangla", "cartoon", "foreign-news", "indian",
    "infotainments", "islamic", "other", "sports",
)
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
)


def _context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def catalogue(sha: str) -> Dict[str, Dict[str, Any]]:
    """Every published card at this commit, keyed by name."""
    found: Dict[str, Dict[str, Any]] = {}
    for name in CATEGORY_FILES:
        path = "data/channels/%s.json" % name
        result = subprocess.run(
            ["git", "show", "%s:%s" % (sha, path)],
            cwd=ROOT, capture_output=True,
        )
        if result.returncode:
            continue
        try:
            payload = json.loads(result.stdout.decode("utf-8"))
        except ValueError:
            continue
        for card in payload.get("channels") or ():
            if isinstance(card, dict) and card.get("name"):
                found[str(card["name"])] = dict(card, _category=payload.get("category"))
    return found


def probe(card: Dict[str, Any]) -> Dict[str, Any]:
    url = str(card.get("url") or "").split("|", 1)[0]
    if not url:
        return {"http_status": None, "measured_height": 0, "body_kind": "",
                "note": "protected route: the card carries no public URL"}

    headers = {"User-Agent": UA}
    for key, value in (card.get("headers") or {}).items():
        headers[str(key)] = str(value)

    def _get(target: str, limit: int) -> bytes:
        request = urllib.request.Request(target, headers=headers)
        with urllib.request.urlopen(request, timeout=20, context=_context()) as response:
            return response.read(limit)

    started = time.time()
    try:
        body = _get(url, 262144)
        status = 200
    except urllib.error.HTTPError as failure:
        return {"http_status": failure.code, "measured_height": 0, "body_kind": "",
                "note": str(failure.reason)[:60], "seconds": round(time.time() - started, 2)}
    except Exception as failure:  # noqa: BLE001 - an unreachable host is data
        return {"http_status": 0, "measured_height": 0, "body_kind": "",
                "note": "%s: %s" % (type(failure).__name__, str(failure)[:60]),
                "seconds": round(time.time() - started, 2)}

    head = body[:16384].decode("utf-8", "replace")
    kind, height = "", 0
    if "#EXT-X-STREAM-INF" in head:
        kind = "hls_master"
        height = media_probe.master_playlist_height(head)
    elif "#EXTINF" in head:
        kind = "hls_media"
        segments = [
            line.strip() for line in head.splitlines()
            if line.strip() and not line.startswith("#")
        ]
        if segments:
            try:
                segment = _get(urllib.parse.urljoin(url, segments[0]), 524288)
                decoded = media_probe.sps_from_transport_stream(segment)
                if decoded:
                    height = media_probe.plausible(decoded.get("height"))
            except Exception:  # noqa: BLE001
                pass
    elif body[:1] == b"\x47":
        kind = "mpegts"
        decoded = media_probe.sps_from_transport_stream(body)
        if decoded:
            height = media_probe.plausible(decoded.get("height"))

    return {"http_status": status, "measured_height": height, "body_kind": kind,
            "note": "", "bytes": len(body), "seconds": round(time.time() - started, 2)}


def verdict(row: Dict[str, Any], floor: int) -> str:
    status = row.get("http_status")
    if status is None:
        return "not_probeable_protected_route"
    if status == 0:
        return "unreachable_from_this_vantage"
    if status in {404, 410}:
        return "route_gone"
    if status != 200:
        return "answered_http_%d_from_this_vantage" % status
    height = row.get("measured_height") or 0
    if height and height < floor:
        return "below_the_%dp_floor_at_%dp" % (floor, height)
    if height:
        return "reachable_at_%dp" % height
    return "reachable_but_resolution_unreadable"


def main(argv: Any = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--before", required=True)
    parser.add_argument("--after", default="HEAD")
    parser.add_argument("--vantage", default="unspecified")
    parser.add_argument("--floor", type=int, default=720)
    parser.add_argument("--workers", type=int, default=5)
    parser.add_argument("--out", default="reports/removed-card-audit.json")
    args = parser.parse_args(argv)

    before = catalogue(args.before)
    after = catalogue(args.after)
    if not before:
        print("no catalogue at %s" % args.before, file=sys.stderr)
        return 1
    gone = sorted(set(before) - set(after))
    print("%s: %d cards\n%s: %d cards\nremoved: %d"
          % (args.before, len(before), args.after, len(after), len(gone)),
          file=sys.stderr)

    rows: List[Dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(probe, before[name]): name for name in gone}
        for future in concurrent.futures.as_completed(futures):
            name = futures[future]
            card = before[name]
            row = {
                "name": name,
                "category": card.get("_category") or card.get("category"),
                "source_id": card.get("source_id"),
                "published_resolution": card.get("resolution") or "",
                "published_height": card.get("resolution_height") or 0,
            }
            row.update(future.result())
            row["verdict"] = verdict(row, args.floor)
            rows.append(row)

    order = {name: index for index, name in enumerate(gone)}
    rows.sort(key=lambda row: order[row["name"]])
    for row in rows:
        print("  %-32s %-13s %-9s %-6s %s"
              % (str(row["name"])[:32], str(row["category"])[:13],
                 row["http_status"], row["measured_height"] or "-", row["verdict"]),
              file=sys.stderr)

    summary = collections.Counter(row["verdict"].split("_at_")[0] for row in rows)
    payload = {
        "mode": "removed_card_audit",
        "note": (
            "Reachability and resolution measured from the vantage named below, "
            "not from the catalogue's stored verdict. A card that is unreachable "
            "here may be perfectly reachable elsewhere; that is why the vantage "
            "is recorded."
        ),
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
        "vantage": args.vantage,
        "before_commit": args.before,
        "after_commit": args.after,
        "before_count": len(before),
        "after_count": len(after),
        "removed_count": len(gone),
        "added_count": len(set(after) - set(before)),
        "added": sorted(set(after) - set(before)),
        "floor": args.floor,
        "verdict_summary": dict(summary.most_common()),
        "cards": rows,
    }
    out = args.out if os.path.isabs(args.out) else os.path.join(ROOT, args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=1, ensure_ascii=False)
        handle.write("\n")
    print("\nwrote %s" % args.out)
    for key, value in summary.most_common():
        print("  %-42s %d" % (key, value))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
