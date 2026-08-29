#!/usr/bin/env python3
"""Bring published badges into line with the measured-playback ledger.

The merger computes a card's badge from state/measured-playback-failures.json,
so a fresh scan gets this right on its own. What it cannot do is reach a
catalogue that was published BEFORE the measurement was taken - and between a
browser session and the next channels scan there can be many hours, during which
the site keeps offering a route it has been measured unable to play under a
green "Verified".

That gap is not hypothetical. On 2026-08-29 the published Star Jalsha card
offered three backups badged "Verified" - cache.devm3u.top, premiumtvs.space and
rgkkw.live - each of which had just produced under 30 seconds of media, or none
at all, in two 120 s sessions.

This changes nothing about what is published. It marks: the route stays, the
badge says the playback is unproven, and the measured reason travels with it -
exactly what the merger would write. A route the ledger says nothing about is
left alone, and a route whose failure has been superseded by a later pass gets
its ordinary badge back.

Run with --dry-run first.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from typing import Any, Dict, List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from scanner import merger  # noqa: E402
from scanner import playback_evidence  # noqa: E402


def _routes_of(card: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [card] + [
        backup for backup in (card.get("backups") or ())
        if isinstance(backup, dict)
    ]


def main(argv: Any = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--data-dir", default=os.path.join(ROOT, "data", "channels")
    )
    args = parser.parse_args(argv)

    changed_files = 0
    changed_routes = 0
    cleared_routes = 0

    for path in sorted(glob.glob(os.path.join(args.data_dir, "*.json"))):
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        cards = payload.get("channels") if isinstance(payload, dict) else payload
        if not isinstance(cards, list):
            continue

        touched = False
        for card in cards:
            if not isinstance(card, dict):
                continue
            for index, route in enumerate(_routes_of(card)):
                url = str(route.get("url") or "")
                if not url:
                    continue
                reason = playback_evidence.unproven_reason(url)
                before = str(route.get("verification_badge") or "")
                if reason:
                    if before == playback_evidence.BADGE:
                        continue
                    route["verification_badge"] = playback_evidence.BADGE
                    route["playback_unproven"] = True
                    route["playback_unproven_reason"] = reason
                    changed_routes += 1
                    touched = True
                    print("  %-30s %-11s %-9s -> %s | %s" % (
                        str(card.get("name"))[:30],
                        "primary" if index == 0 else route.get("name") or "backup",
                        before or "(none)", playback_evidence.BADGE, reason[:56]))
                elif route.get("playback_unproven"):
                    # A later pass superseded the failure. The badge goes back
                    # to whatever the verification status earns on its own.
                    route.pop("playback_unproven", None)
                    route.pop("playback_unproven_reason", None)
                    route["verification_badge"] = merger._verification_badge(route)
                    cleared_routes += 1
                    touched = True
                    print("  %-30s %-11s cleared -> %s" % (
                        str(card.get("name"))[:30],
                        "primary" if index == 0 else route.get("name") or "backup",
                        route["verification_badge"]))

        if touched and not args.dry_run:
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            changed_files += 1

    print("\n%s: %d route(s) marked unproven, %d cleared, %d file(s) %s"
          % ("dry run" if args.dry_run else "done", changed_routes,
             cleared_routes, changed_files,
             "would change" if args.dry_run else "written"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
