#!/usr/bin/env python3
"""Determine the resolution of a published card that declares none.

The scanner keeps a verified stream whose resolution it could not read and
marks it quality_unknown; the Pages validator accepts those under the same
policy. That keeps working Bangladeshi channels, which is right, but "unknown"
is not an answer - it is the absence of one, and 120 cards were carrying it.

So this asks the stream itself, with the same three kinds of evidence the
scanner already trusts:

  1. an HLS master playlist's RESOLUTION attribute
  2. the H.264 SPS decoded out of a transport stream, which gives the coded
     width and height and whether the picture is interlaced
  3. a tvg-resolution the playlist declared, checked against 1 or 2 when both
     are available

It changes nothing. Output is a report; deciding what to do with a channel that
turns out to be 480p is a policy question for a person.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import glob
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from scanner import route_evidence as rev  # noqa: E402
from scanner.media_probe import (  # noqa: E402
    master_playlist_height,
    sps_from_transport_stream,
)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
)
SAMPLE_BYTES = 512 * 1024


def fetch(url: str, limit: int = SAMPLE_BYTES, timeout: int = 20) -> Tuple[int, bytes]:
    request = urllib.request.Request(
        url, headers={"User-Agent": UA, "Referer": "https://clicktv.pages.dev/"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read(limit)
    except urllib.error.HTTPError as error:
        return error.code, b""
    except Exception:  # noqa: BLE001 - a probe failure is a datum
        return 0, b""


def determine(card: Dict[str, Any]) -> Dict[str, Any]:
    url = str(card.get("url") or "").strip()
    out: Dict[str, Any] = {
        "name": card.get("name"),
        "category": card.get("category"),
        "url_public_template": rev.redact_public_template(url) if url else "",
        "host": urllib.parse.urlsplit(url).hostname if url else None,
        "declared": card.get("resolution_height"),
        "evidence": None,
        "height": 0,
        "scan_type": None,
        "http": None,
    }
    if not url:
        out["evidence"] = "no url (playback_id only); not probed"
        return out

    status, body = fetch(url)
    out["http"] = status
    if not body:
        out["evidence"] = f"unreachable from this vantage (HTTP {status})"
        return out

    text = body[:8192].decode("utf-8", "replace")
    if text.lstrip().startswith("#EXTM3U"):
        height = master_playlist_height(text)
        if height:
            out.update(evidence="hls master RESOLUTION", height=height)
            return out
        # Media playlist: follow one segment and read the SPS.
        segments = [
            line.strip() for line in text.splitlines()
            if line.strip() and not line.startswith("#")
        ]
        if segments:
            segment_url = urllib.parse.urljoin(url, segments[0])
            seg_status, seg_body = fetch(segment_url)
            decoded = sps_from_transport_stream(seg_body) if seg_body else None
            if decoded:
                out.update(
                    evidence="h264 SPS from a media segment",
                    height=decoded["height"],
                    scan_type=decoded["scan_type"],
                )
                return out
            out["evidence"] = (
                f"hls media playlist, segment gave no SPS (HTTP {seg_status})"
            )
            return out
        out["evidence"] = "hls playlist with no segments"
        return out

    decoded = sps_from_transport_stream(body)
    if decoded:
        out.update(
            evidence="h264 SPS from the stream",
            height=decoded["height"],
            scan_type=decoded["scan_type"],
        )
        return out
    out["evidence"] = "reachable but no readable resolution"
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="reports/unknown-resolution-audit.json")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    unknown: List[Dict[str, Any]] = []
    for path in sorted(glob.glob(os.path.join(ROOT, "data", "channels", "*.json"))):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError):
            continue
        rows = payload if isinstance(payload, list) else payload.get("channels") or []
        for card in rows:
            if not isinstance(card, dict):
                continue
            try:
                height = int(card.get("resolution_height") or 0)
            except (TypeError, ValueError):
                height = 0
            if height == 0:
                unknown.append(card)

    if args.limit:
        unknown = unknown[: args.limit]

    rows: List[Dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        for row in pool.map(determine, unknown):
            rows.append(row)
            sys.stderr.write(
                "  %-30s %-34s %-6s %s\n" % (
                    str(row["name"])[:30], str(row["evidence"])[:34],
                    row["height"] or "-", row["scan_type"] or "",
                )
            )
            sys.stderr.flush()

    determined = [r for r in rows if r["height"] > 0]
    payload = {
        "mode": "unknown_resolution_audit",
        "note": (
            "Every published card that declares no resolution, asked directly. "
            "The scanner keeps these under the same policy the Pages validator "
            "now honours, which protects working Bangladeshi channels - but "
            "'unknown' is the absence of an answer, so this gets one where the "
            "stream will give it."
        ),
        "cards_without_resolution": len(rows),
        "determined": len(determined),
        "at_or_above_720": sum(1 for r in determined if r["height"] >= 720),
        "below_720": sum(1 for r in determined if r["height"] < 720),
        "interlaced": sum(1 for r in determined if r.get("scan_type") == "interlaced"),
        "undeterminable": len(rows) - len(determined),
        "cards": rows,
    }
    out_path = os.path.join(ROOT, args.out)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    sys.stderr.write(
        "\n  cards=%s determined=%s (>=720: %s, <720: %s) undeterminable=%s\n"
        "  wrote %s\n" % (
            len(rows), len(determined), payload["at_or_above_720"],
            payload["below_720"], payload["undeterminable"], args.out)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
