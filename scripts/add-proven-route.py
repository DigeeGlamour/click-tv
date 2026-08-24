#!/usr/bin/env python3
"""Attach a route that passed the 120 s acceptance to a channel that needs one.

Zee Bangla's published route is settled by measurement: 1080i H.264 with zero IDR
frames, four 120 s sessions all ending in MEDIA_ERR_DECODE, and fifteen
mpegts.js build/config variants each decoding exactly one frame before stopping.
The player side is exhausted. What remained was whether the configured sources
already contain a route with a different structure, and one does - an HLS route
that passed two independent 120 s sessions with zero stall.

Two rules this script will not bend:

  * The existing route is NEVER removed or rewritten. It moves to a backup and
    keeps its URL, headers and proxy mode byte for byte. The standing
    instruction is that existing stream URLs are not to be changed, and adding a
    proven route alongside is not changing one.
  * A route is only attached if reports/ says it PASSED twice. No manifest
    check, no reachability result, no "it looked fine" - the same PASS floor
    every other promotion in this project answers to.

The owner's channel-identity rule is enforced here too, because getting it wrong
is worse than the stutter: "Zee Bangla HD" is another source of the same channel,
while "Zee Bangla Cinema" and "Zee Bangla Sonar" are different channels entirely
and must never be substituted.

Run with --dry-run first. It prints exactly what it would do and writes nothing.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import urllib.parse
from typing import Any, Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from scanner import route_evidence as rev  # noqa: E402
from scanner import sustained_proof  # noqa: E402

#: Same rule as scripts/zee-alternative-scout.py, restated rather than imported
#: so this script cannot be run against a channel whose identity it has not
#: checked.
DIFFERENT_CHANNEL_WORDS = (
    "cinema", "sonar", "sansar", "movies", "music", "natok", "cine",
)


def same_channel(channel: str, candidate: str) -> bool:
    """Whether `candidate` names another SOURCE of `channel`, not a sibling."""
    base = re.sub(r"\s*(hd|sd|fhd)\s*$", "", str(channel or ""), flags=re.I).strip()
    other = str(candidate or "")
    if any(word in other.lower() for word in DIFFERENT_CHANNEL_WORDS):
        return False
    stripped = re.sub(r"\s*(hd|sd|fhd)\s*$", "", other, flags=re.I).strip()
    return stripped.casefold() == base.casefold()


def proven_routes(report_path: str) -> List[Dict[str, Any]]:
    """Entries in a playback report that passed twice."""
    try:
        with open(os.path.join(ROOT, report_path), "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return []
    out = []
    for result in payload.get("results") or ():
        passes = [
            o for o in (result.get("observations") or ())
            if o.get("verdict") == rev.PROVEN
        ]
        if len(passes) < rev.REQUIRED_FRESH_SESSIONS:
            continue
        out.append({
            "name": result.get("name"),
            "pass_count": len(passes),
            "window_seconds": payload.get("window_seconds"),
            "session_separation_seconds": payload.get("session_separation_seconds"),
            "browser_profile": payload.get("browser_profile"),
            "media_progress_seconds": [
                (o.get("playback_metrics") or {}).get("media_progress_seconds")
                for o in passes
            ],
            "cumulative_stall_seconds": [
                (o.get("playback_metrics") or {}).get("cumulative_stall_seconds")
                for o in passes
            ],
            "route": next(
                (o.get("attempt_route") for o in passes if o.get("attempt_route")),
                None,
            ),
            "evidence_report": report_path,
        })
    return out


def find_card(channel: str):
    for path in sorted(glob.glob(os.path.join(ROOT, "data", "channels", "*.json"))):
        with open(path, "r", encoding="utf-8") as handle:
            raw = handle.read()
        payload = json.loads(raw)
        container = payload if isinstance(payload, list) else (
            payload.get("channels") or payload.get("items")
        )
        if container is None:
            continue
        for card in container:
            if isinstance(card, dict) and str(card.get("name") or "") == channel:
                return path, payload, container, card, raw
    return None, None, None, None, None


def save(path: str, payload: Any, original: str) -> None:
    newline = "\r\n" if "\r\n" in original else "\n"
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    if newline != "\n":
        text = text.replace("\n", newline)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text + (newline if original.endswith(("\n", "\r\n")) else ""))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", required=True)
    ap.add_argument("--url", required=True, help="the proven route's URL")
    ap.add_argument("--proven-name", required=True,
                    help="the entry name in the playback report")
    ap.add_argument("--source-name", required=True,
                    help="the name this route carries in its source playlist")
    ap.add_argument("--report", default="reports/zee-confirm-playback.json")
    ap.add_argument("--stream-type", default="hls")
    ap.add_argument("--proxy-mode", default="direct_first")
    ap.add_argument("--make-primary", action="store_true",
                    help="put the proven route first and demote the existing one "
                         "to a backup; the existing URL is preserved either way")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not same_channel(args.channel, args.source_name):
        print(f"refused: '{args.source_name}' is not another source of "
              f"'{args.channel}' - substituting a different channel would put "
              f"the wrong programme on the card")
        return 1

    proven = {p["name"]: p for p in proven_routes(args.report)}
    evidence = proven.get(args.proven_name)
    if not evidence:
        print(f"refused: '{args.proven_name}' is not PROVEN in {args.report} "
              f"(needs {rev.REQUIRED_FRESH_SESSIONS} full 120 s passes)")
        return 1
    print(f"proof: {evidence['pass_count']} full PASS, window "
          f"{evidence['window_seconds']}s, media {evidence['media_progress_seconds']}s, "
          f"stall {evidence['cumulative_stall_seconds']}s, route {evidence['route']}")

    path, payload, container, card, original = find_card(args.channel)
    if card is None:
        print(f"refused: no card named '{args.channel}'")
        return 1
    print(f"card: {os.path.relpath(path, ROOT)}")

    existing_url = str(card.get("url") or "")
    if existing_url == args.url:
        print("the proven route is already the primary; nothing to do")
        return 0
    backups = list(card.get("backups") or [])
    if any(
        isinstance(b, dict) and str(b.get("url") or "") == args.url for b in backups
    ):
        print("the proven route is already a backup; nothing to do")
        return 0

    proven_entry = {
        "name": "Proven-120s",
        "url": args.url,
        "stream_type": args.stream_type,
        "proxy_mode": args.proxy_mode,
        "header_profile": "",
        "requires_headers": False,
        "inherit_manifest_query": False,
        "verification_mode": "phase1_120s_browser_x2",
        "verification_status": "verified_sustained_playback",
        "verification_note": (
            "Two independent 120 s browser sessions each played this route to "
            f"the full PASS floor. See {args.report}."
        ),
        "verified": False,
    }
    existing_entry = {
        "name": "Original-primary",
        "url": existing_url,
        "stream_type": card.get("stream_type"),
        "proxy_mode": card.get("proxy_mode"),
        "header_profile": card.get("header_profile"),
        "requires_headers": card.get("requires_headers", False),
        "inherit_manifest_query": card.get("inherit_manifest_query", False),
        "verification_note": (
            "Kept unchanged. Measured failing: 1080i H.264 with zero IDR frames, "
            "four 120 s sessions ending in MEDIA_ERR_DECODE, and fifteen "
            "mpegts.js variants each decoding one frame before stopping."
        ),
    }

    if args.make_primary:
        card["url"] = args.url
        card["stream_type"] = args.stream_type
        card["proxy_mode"] = args.proxy_mode
        card["header_profile"] = ""
        card["requires_headers"] = False
        card["verification_status"] = "verified_sustained_playback"
        card["verification_mode"] = "phase1_120s_browser_x2"
        card["verification_note"] = proven_entry["verification_note"]
        card["backups"] = [existing_entry] + backups
        print("action: proven route becomes the primary; the existing route is "
              "preserved as a backup with its URL unchanged")
    else:
        card["backups"] = [proven_entry] + backups
        print("action: proven route added as the first backup; the primary is "
              "untouched")
    card["available_link_count"] = 1 + len(card.get("backups") or [])

    # The proof belongs outside the card as well, because the next scan rebuilds
    # cards from their sources and erases anything written on them.
    written, why = sustained_proof.record(
        "channel", args.channel, evidence,
        path=None if not args.dry_run else os.devnull,
    )
    print(f"proof registry: {'recorded' if written else why}")

    if args.dry_run:
        print("\n(dry run: nothing written)")
        return 0
    save(path, payload, original)
    print(f"wrote {os.path.relpath(path, ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
