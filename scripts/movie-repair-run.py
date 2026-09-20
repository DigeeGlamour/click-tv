#!/usr/bin/env python3
"""ধাপ ৮ / S-02 - work the repair queue, not the catalogue.

    python scripts/movie-repair-run.py --print
    python scripts/movie-repair-run.py --limit 40 --apply

S-02 is that a single dead link can only be fixed by running the whole
catalogue, whose budget is then divided across 2,475 links - so repair is
effectively luck. This reads `state/movie-repair-queue.json` and works only
what is due, worst first.

What `--apply` does, and does not
---------------------------------
It records attempts and promotes a live backup to `active_primary`. It does
**not** publish: ধারা ৪.৯ rule 2 and this repository's existing atomic-publish
rule both say one writer, and that writer is the scan's publisher. A repair run
updates state; the next scan publishes it.

Without `--apply` nothing is written at all, which is the default because a
queue is worth reading long before it is worth acting on.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import movie_link_health as health  # noqa: E402
from scanner import movie_repair_queue as repair  # noqa: E402


def _promote_backup(store, entry) -> str:
    """ধারা ৪.৫ step 2 - a live backup becomes the active primary for now.

    `preferred_primary` is untouched: it is the owner's policy and does not
    change because a check failed. `active_primary` is this moment's reality,
    and the two being separate fields is what lets a private link become
    primary again by itself when it comes back.
    """
    links = store.get("links") or {}
    broken = links.get(entry.get("broken_link_id"))
    if isinstance(broken, dict):
        broken["active_primary"] = False
    content_id = entry.get("content_id")
    for identity, record in links.items():
        if not isinstance(record, dict):
            continue
        if record.get("content_id") != content_id:
            continue
        if identity == entry.get("broken_link_id"):
            continue
        if record.get("status") == health.STATUS_HEALTHY:
            record["active_primary"] = True
            return identity
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument(
        "--limit", type=int, default=50,
        help="How many entries this run may work. The point of S-02 is that "
             "this is small.",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Record attempts and promote live backups. Without it nothing "
             "is written.",
    )
    parser.add_argument("--print", dest="show", action="store_true")
    arguments = parser.parse_args()

    root = Path(arguments.root)
    queue = repair.load(root / repair.DEFAULT_PATH)
    store = health.load(root / health.DEFAULT_PATH)

    work = repair.due(queue, limit=arguments.limit)
    summary = repair.summarise(queue)

    promoted = 0
    attempted = 0
    if arguments.apply:
        for entry in work:
            identity = _promote_backup(store, entry)
            if identity:
                promoted += 1
            # A promotion is not a repair: the card is watchable again, but the
            # broken link is still broken and stays queued until it answers or
            # a scan replaces it.
            repair.record_attempt(
                queue, entry["key"], repaired=False,
                detail=f"promoted={identity}" if identity else "no_live_backup",
            )
            attempted += 1
        health.save(store, root / health.DEFAULT_PATH)
        repair.save(queue, root / repair.DEFAULT_PATH)

    report = {
        "mode": "repair_run",
        "applied": bool(arguments.apply),
        "queue": summary,
        "worked": len(work),
        "attempted": attempted,
        "backups_promoted": promoted,
        "sample": [
            {key: entry.get(key) for key in
             ("severity", "content_id", "attempts", "reason")}
            for entry in work[:10]
        ],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))

    if summary["no_live_link"]:
        print(
            f"\n{summary['no_live_link']} card(s) have no live link at all "
            "(P0) - a viewer can watch nothing for these.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
