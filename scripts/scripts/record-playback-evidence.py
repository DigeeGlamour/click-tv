#!/usr/bin/env python3
"""Write a finished sustained-playback report into the measured-playback ledger.

The 120 s harness produces a report; the ledger is what the merger reads when it
decides whether a card may call a route "Verified". Until now the two were
joined by hand, which is how Star Jalsha's rgkkw backup came to sit in the
ledger as measured-unplayable and ship a green "Verified" badge at the same
time.

The rules it applies, and why each one is where it is:

  two passes      the route is proven. Any recorded failure for it is marked
                  superseded - not deleted, so both vantages stay readable.
  no passes       a measured failure, tagged with the vantage it was measured
                  from.
  exactly one     nothing is written. One pass and one inconclusive session is
                  neither a proof nor a failure, and recording it as either
                  would be the guess this ledger exists to prevent.

A card that publishes no URL - a protected route that exposes only a playback
id - is resolved through data/playback/*.json, because the ledger is keyed by
URL and a DRM route that never starts is exactly the case worth recording.

Usage:
    python scripts/record-playback-evidence.py \
        --report reports/phase1-sustained-playback.json \
        --vantage bangladesh-residential [--dry-run]
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

from scanner import playback_evidence  # noqa: E402
from scanner import route_evidence as rev  # noqa: E402


def playback_catalogue() -> Dict[str, str]:
    """playback_id -> the URL the proxy would actually fetch."""
    found: Dict[str, str] = {}
    for path in sorted(glob.glob(os.path.join(ROOT, "data", "playback", "*.json"))):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError):
            continue
        for key, record in (payload.get("records") or {}).items():
            if isinstance(record, dict) and record.get("url"):
                found[str(key)] = str(record["url"])
    return found


def reason_from(observations: List[Dict[str, Any]]) -> str:
    for observation in observations:
        for text in (observation.get("reasons") or ()):
            if str(text or "").strip():
                return str(text).strip()
        metrics = observation.get("playback_metrics") or {}
        for text in (metrics.get("fatal_errors") or ()):
            if str(text or "").strip():
                return str(text).strip()[:300]
    return "measured unplayable with no reason reported by the harness"


def main(argv: Any = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True)
    parser.add_argument("--vantage", required=True)
    parser.add_argument("--targets", default="",
                        help="the target list the report was produced from, "
                             "used only to recover a protected route's id")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    path = args.report if os.path.isabs(args.report) else os.path.join(ROOT, args.report)
    with open(path, "r", encoding="utf-8") as handle:
        report = json.load(handle)

    by_name: Dict[str, Dict[str, Any]] = {}
    if args.targets:
        target_path = (
            args.targets if os.path.isabs(args.targets)
            else os.path.join(ROOT, args.targets)
        )
        try:
            with open(target_path, "r", encoding="utf-8") as handle:
                for target in json.load(handle):
                    by_name[str(target.get("name"))] = target
        except (OSError, ValueError):
            by_name = {}

    catalogue = playback_catalogue()
    window = float(report.get("window_seconds") or 120.0)
    written = 0

    for result in report.get("results") or ():
        name = str(result.get("name") or "")
        observations = result.get("observations") or []
        if not observations:
            continue

        target = by_name.get(name) or {}
        url = str(target.get("url") or "").strip()
        if not url:
            url = catalogue.get(str(target.get("playback_id") or ""), "")
        if not url:
            print("  %-44s skipped: no URL to key on" % name[:44])
            continue

        passes = [o for o in observations if o.get("verdict") == rev.PROVEN]
        progress = [
            (o.get("playback_metrics") or {}).get("media_progress_seconds")
            for o in observations
        ]

        if len(passes) >= rev.REQUIRED_FRESH_SESSIONS:
            action = "proof (supersedes any recorded failure)"
            if not args.dry_run:
                ok = playback_evidence.record_proof(
                    url, vantage=args.vantage, sessions=len(observations),
                    media_progress_seconds=progress, window_seconds=window,
                    evidence_report=args.report,
                )
                action += " -> written" if ok else " -> nothing recorded here"
        elif passes:
            action = "one pass only: nothing written"
        else:
            action = "measured failure"
            if not args.dry_run:
                ok = playback_evidence.record(
                    url, reason_from(observations), sessions=len(observations),
                    media_progress_seconds=progress, window_seconds=window,
                    evidence_report=args.report, vantage=args.vantage,
                )
                action += " -> written" if ok else " -> refused (a later pass stands)"
                written += int(bool(ok))

        print("  %-44s %-22s %s" % (
            name[:44], str(progress)[:22], action))

    print("\n%s: %d failure row(s) written, vantage=%s"
          % ("dry run" if args.dry_run else "ledger updated", written, args.vantage))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
