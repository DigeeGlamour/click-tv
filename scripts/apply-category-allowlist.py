#!/usr/bin/env python3
"""Apply a category's publish allowlist to the catalogue that is already live.

scanner/channels.py enforces the list on every scan, which is where it belongs.
What a scan cannot do is reach the catalogue that was published before the list
existed - and between a config change and the next channels scan there are
hours, during which the site keeps serving the cards the owner asked to remove.

This writes exactly what the next scan would write: the cards a restricted
category is allowed to publish, in their existing order, and nothing else. It
never adds a card, never edits one, and never touches a category with no list.

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

from scanner import category_allowlist  # noqa: E402


def main(argv: Any = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--data-dir", default=os.path.join(ROOT, "data", "channels"))
    parser.add_argument("--out", default="reports/category-allowlist.json")
    args = parser.parse_args(argv)

    summary: Dict[str, Any] = {}
    rejected_rows: List[Dict[str, str]] = []
    files_changed = 0

    for path in sorted(glob.glob(os.path.join(args.data_dir, "*.json"))):
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        cards = payload.get("channels") if isinstance(payload, dict) else payload
        if not isinstance(cards, list):
            continue
        category = str(
            (payload.get("category") if isinstance(payload, dict) else "") or ""
        )
        if not category or not category_allowlist.is_restricted(category):
            continue

        # `apply` both filters and orders: the list is the running order the
        # owner wrote, not only the set of names.
        kept = category_allowlist.apply(cards, category)
        dropped = category_allowlist.rejected(cards, category)
        missing = category_allowlist.missing_from(kept, category)

        summary[category] = {
            "requested": len(category_allowlist.allowed_names(category)),
            "before": len(cards),
            "published": len(kept),
            "dropped": len(dropped),
            "published_names": sorted(str(c.get("name") or "") for c in kept),
            "requested_but_not_published": missing,
        }
        rejected_rows.extend(
            {"name": name, "category": category,
             "reason": "not on the publish allowlist for this category"}
            for name in dropped
        )

        print("%s: %d card(s) -> %d kept, %d dropped, %d requested name(s) "
              "produced no card"
              % (category, len(cards), len(kept), len(dropped), len(missing)))
        print("   order: " + ", ".join(
            str(c.get("name") or "") for c in kept[:6]) + " ...")
        for name in sorted(dropped)[:400]:
            print("   drop  %s" % name)
        for name in missing:
            print("   MISSING (asked for, no card)  %s" % name)

        if kept != cards and not args.dry_run:
            if isinstance(payload, dict):
                payload["channels"] = kept
                payload["count"] = len(kept)
            else:
                payload = kept
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            files_changed += 1

    if not summary:
        print("no restricted category found; nothing to do")
        return 0

    report = {
        "mode": "category_publish_allowlist",
        "note": (
            "Applied to the already-published catalogue. scanner/channels.py "
            "enforces the same list on every scan; this only closes the gap "
            "between a config change and the next scan."
        ),
        "rejected_count": len(rejected_rows),
        "rejected": sorted(rejected_rows, key=lambda row: row["name"]),
        "categories": summary,
    }
    if not args.dry_run:
        out = args.out if os.path.isabs(args.out) else os.path.join(ROOT, args.out)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=1)
            handle.write("\n")

    print("\n%s: %d file(s) %s"
          % ("dry run" if args.dry_run else "done", files_changed,
             "would change" if args.dry_run else "written"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
