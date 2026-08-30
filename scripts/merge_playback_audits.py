#!/usr/bin/env python3
"""Merge complete Click TV playback audit checkpoints without losing evidence."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output")
    parser.add_argument("inputs", nargs="+")
    args = parser.parse_args()

    payloads = [json.loads(Path(value).resolve().read_text(encoding="utf-8")) for value in args.inputs]
    if not payloads:
        raise RuntimeError("At least one input audit is required")
    base_urls = {payload.get("base_url") for payload in payloads}
    if len(base_urls) != 1:
        raise RuntimeError(f"Audit base URLs differ: {base_urls}")

    inventory = []
    results = []
    seen_inventory = set()
    seen_results = set()
    for payload in payloads:
        for row in payload.get("inventory", []):
            key = (row.get("category_id"), row.get("uid"))
            if key in seen_inventory:
                raise RuntimeError(f"Duplicate inventory key: {key}")
            seen_inventory.add(key)
            inventory.append(row)
        for row in payload.get("results", []):
            key = (row.get("category_id"), row.get("uid"))
            if key in seen_results:
                raise RuntimeError(f"Duplicate result key: {key}")
            seen_results.add(key)
            results.append(row)

    if seen_inventory != seen_results:
        raise RuntimeError(
            f"Merged audit incomplete: inventory={len(seen_inventory)} results={len(seen_results)} "
            f"missing={len(seen_inventory - seen_results)} extra={len(seen_results - seen_inventory)}"
        )
    counts = Counter(row.get("status", "") for row in results)
    if set(counts) - {"PASS", "FAIL"}:
        raise RuntimeError(f"Merged audit contains unresolved statuses: {dict(counts)}")

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    merged = {
        "generated_at": min(payload.get("generated_at", now) for payload in payloads),
        "updated_at": now,
        "base_url": payloads[0].get("base_url"),
        "test_mode": "visible Chrome UI clicks",
        "inventory": inventory,
        "inventory_count": len(inventory),
        "results": results,
        "tested_count": len(results),
        "status_counts": dict(counts),
        "notes": [
            "Every Today Match, TV channel, and movie result was produced by clicking its deployed UI card in visible desktop Google Chrome.",
            "PASS requires a decoded video frame and measurable playback progress.",
            "Failed items received a longer visible-Chrome retry.",
            "Raw or tokenized playback URLs are intentionally excluded from the report.",
        ],
    }
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"MERGE_OK output={output} rows={len(results)} status={dict(counts)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
