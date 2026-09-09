#!/usr/bin/env python3
"""Run every wired hide path against COPIES of the live catalogue, audit only.

Each hide path is reached the way the scanner reaches it, including its gating,
because that is the only version of the number worth reporting. Calling
`mark_unproven_player_items` over all 758 channels says it would hide 748; in a
real scan it is gated on `strict_player_publish` AND `bangla_requires_player_proof`
and applies to the Bangla category alone, which is 31 cards. Publishing the first
figure as if it were the second would overstate the exposure by more than 20x.

Nothing is written outside reports/. Every item handed to a hide path is a deep
copy, so no catalogue file is modified even in memory.

Usage: python3 scripts/visibility-model-audit.py [--out reports/visibility-model-audit.json]
"""
from __future__ import annotations

import argparse
import copy
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from scanner import browser_reachability as br  # noqa: E402
from scanner import player_compatibility as pc  # noqa: E402
from scanner import visibility_audit as va  # noqa: E402


def load_channels() -> list:
    items = []
    for path in sorted(glob.glob(os.path.join(ROOT, "data", "channels", "*.json"))):
        with open(path, "r", encoding="utf-8") as handle:
            blob = json.load(handle)
        raw = blob if isinstance(blob, list) else (blob.get("channels") or blob.get("items") or [])
        for entry in raw:
            if isinstance(entry, dict):
                items.append(copy.deepcopy(entry))
    return items


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="reports/visibility-model-audit.json")
    args = ap.parse_args()

    items = load_channels()
    bangla = [i for i in items if str(i.get("category") or "") == "Bangla"]
    print(f"loaded {len(items)} channel records ({len(bangla)} Bangla), all deep copies")

    va.reset()
    counts = {}
    # 1. Confirmed browser failures. Called on the whole card set in
    #    scanner/channels.py, so audited that way.
    counts["mark_confirmed_player_failures"] = pc.mark_confirmed_player_failures(
        copy.deepcopy(items), "channel"
    )
    # 2. Fingerprint-keyed proof gate. Bangla only, matching channels.py.
    counts["mark_unproven_player_items(Bangla only)"] = pc.mark_unproven_player_items(
        copy.deepcopy(bangla), "channel"
    )
    # 3. Unproven-this-run gate.
    hidden, _rows = br.mark_unproven_items(copy.deepcopy(items), "channel", True)
    counts["mark_unproven_items"] = hidden

    for name, value in counts.items():
        print(f"  {name}: {value} item(s) would be hidden by the CURRENT rules")

    written = va.flush(
        os.path.join(ROOT, args.out),
        provenance=(
            "Hide paths were called against deep copies of the live catalogue "
            f"({len(items)} channel records), each with the gating the scanner "
            "applies: mark_confirmed_player_failures over all cards, "
            f"mark_unproven_player_items over the Bangla category only ({len(bangla)} "
            "cards, matching scanner/channels.py), mark_unproven_items over all "
            "cards. No data/ file was opened for writing and no item was mutated "
            "outside its copy."
        ),
    )
    summary = va.summary()
    print(f"\naudit written to {written}")
    print(
        f"  decisions seen: {summary['decisions_seen']}  "
        f"model would hide: {summary['model_would_hide']}  "
        f"model would keep: {summary['model_would_keep']}"
    )
    for site, bucket in summary["per_site"].items():
        print(f"  {site}: {bucket}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
