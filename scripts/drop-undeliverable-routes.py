#!/usr/bin/env python3
"""Strip published routes the delivery path can never fetch.

The verifier now refuses these at source (scanner/deliverability.py), so nothing
new arrives. This clears what is already published, because a scan that would
have dropped them runs once a day into a schedule that delivers a fraction of
what it asks for - and until it does, the card sits on the site claiming to be
playable.

The rule it enforces is the bare-IP one: Cloudflare's fetch() refuses a
direct-IP target with 403 "error code: 1003", and an HTTPS page cannot hand an
http:// stream to the video element directly, so a bare-IP route has no path to
any viewer. Measured 2026-08-30 against the live workers.

What it does per card:

  * a bare-IP BACKUP is removed;
  * a bare-IP PRIMARY is replaced by the first deliverable backup, which is
    promoted in place with its own url/headers/badge intact;
  * a card with nothing deliverable left is removed entirely.

A promoted backup is never given a badge it did not earn - it keeps whatever it
carried. Resolution is checked before promotion, because the published floor is
720p and a promotion that breaks it fails the Pages build, which is how
Cloudflare ends up serving yesterday's data while every push looks green.
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

from scanner import deliverability  # noqa: E402
from scanner import playback_evidence  # noqa: E402

#: Keys that describe a route rather than the card it sits on. When a backup is
#: promoted these move up; everything else - name, logo, category, ordering -
#: belongs to the card and stays exactly where it is.
ROUTE_FIELDS = (
    "url", "stream_url", "link", "playback_id", "headers", "drm", "header_profile", "proxy_mode",
    "stream_type", "requires_headers", "inherit_manifest_query",
    "verification_status", "verification_mode", "verification_badge",
    "verified", "publish_allowed", "verification_error", "resolution",
    "resolution_height", "resolution_label", "declared_resolution_height",
    "source_id", "source_name", "playback_unproven", "playback_unproven_reason",
    "transient_rescue_count", "vantage_note",
)


def playback_catalogue() -> Dict[str, Any]:
    found: Dict[str, Any] = {}
    for path in glob.glob(os.path.join(ROOT, "data", "playback", "*.json")):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                found.update(json.load(handle).get("records") or {})
        except (OSError, ValueError):
            continue
    return found


CATALOGUE = playback_catalogue()


#: The three spellings a route's address appears under across this catalogue.
#: scripts/validate-pages.py reads exactly these, in this order; a route whose
#: address is under `stream_url` is invisible to a check that only reads `url`,
#: which is how the first pass of this script left twenty event backups behind.
URL_FIELDS = ("url", "stream_url", "link")


def url_of(stream: Any) -> str:
    if not isinstance(stream, dict):
        return ""
    for key in URL_FIELDS:
        value = stream.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    record = CATALOGUE.get(str(stream.get("playback_id") or "")) or {}
    return str(record.get("url") or "").strip()


def is_undeliverable(stream: Any) -> bool:
    return bool(deliverability.undeliverable_reason(url_of(stream)))


def height_of(stream: Any) -> int:
    """Declared height, by whichever of the several field spellings is present."""
    if not isinstance(stream, dict):
        return 0
    for key in ("resolution_height", "declared_resolution_height", "height"):
        try:
            value = int(stream.get(key) or 0)
        except (TypeError, ValueError):
            value = 0
        if value:
            return value
    text = str(stream.get("resolution") or stream.get("resolution_label") or "")
    if "x" in text.lower():
        tail = text.lower().split("x")[-1]
        digits = "".join(c for c in tail if c.isdigit())
        return int(digits) if digits else 0
    digits = "".join(c for c in text if c.isdigit())
    return int(digits) if digits else 0


def promote(card: Dict[str, Any], backup: Dict[str, Any]) -> None:
    for key in ROUTE_FIELDS:
        if key in backup:
            card[key] = backup[key]
        elif key in card:
            card.pop(key, None)


def measured_dead(stream: Any) -> bool:
    """True when a real browser has already measured this route unplayable."""
    return bool(playback_evidence.unproven_reason(url_of(stream)))


def repair(card: Dict[str, Any]) -> str:
    """Returns "" (untouched), "backup_dropped", "promoted",
    "promoted_unproven" or "remove"."""
    backups = [b for b in (card.get("backups") or []) if isinstance(b, dict)]
    clean = [b for b in backups if not is_undeliverable(b)]
    dropped = len(backups) - len(clean)

    if not is_undeliverable(card):
        if dropped:
            card["backups"] = clean
            return "backup_dropped"
        return ""

    # The primary is undeliverable and has to go. Choosing its replacement is
    # where this could quietly make things worse: promoting a route a browser
    # has already measured unplayable just moves the lie from one field to
    # another. So the ledger gets first say - a route nothing has disproved is
    # taken ahead of one that has been, whatever their order in the card.
    #
    # Height is checked because the published floor is 720p and a promotion
    # that breaks it fails the Pages build, which is how Cloudflare ends up
    # serving yesterday's data while every push looks green.
    eligible = [(i, b) for i, b in enumerate(clean) if height_of(b) >= 720]
    fresh = [(i, b) for i, b in eligible if not measured_dead(b)]
    chosen, outcome = (fresh or eligible or [(None, None)])[0], (
        "promoted" if fresh else "promoted_unproven"
    )
    index, candidate = chosen
    if candidate is None:
        return "remove"
    promote(card, candidate)
    card["backups"] = clean[:index] + clean[index + 1:]
    # A route the ledger has already disproved keeps the card on the site but
    # must not keep a Verified badge. rebadge-measured-routes.py reads the same
    # ledger and would correct it on its next run; saying it here means the
    # card is never briefly published claiming more than it can do.
    if outcome == "promoted_unproven":
        reason = playback_evidence.unproven_reason(url_of(card))
        card["verification_badge"] = "Playback Unproven"
        card["verified"] = False
        card["playback_unproven"] = True
        card["playback_unproven_reason"] = reason
    return outcome


def main(argv: Any = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--out", default="reports/undeliverable-routes.json")
    args = parser.parse_args(argv)

    targets = sorted(glob.glob(os.path.join(ROOT, "data", "channels", "*.json")))
    # data/today-match.json is a mirror. What the site actually serves is the
    # snapshot slot data/manifest.json points at, and publishing switches slots
    # by one os.replace() of that manifest - so the flat file can be spotless
    # while the live one is not. Every slot is swept, not just the current one,
    # because the next publish may promote any of them.
    for extra in ("today-match.json", "upcoming.json"):
        for path in [os.path.join(ROOT, "data", extra),
                     *sorted(glob.glob(os.path.join(
                         ROOT, "data", "snapshots", "*", extra)))]:
            if os.path.isfile(path):
                targets.append(path)

    actions: List[Dict[str, Any]] = []
    files_changed = 0
    for path in targets:
        if os.path.basename(path) == "index.json":
            continue
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError):
            continue
        key = next((k for k in ("channels", "events", "items")
                    if isinstance(payload.get(k), list)), None)
        if not key:
            continue

        kept: List[Any] = []
        touched = False
        for card in payload[key]:
            if not isinstance(card, dict):
                kept.append(card)
                continue
            before_url = url_of(card)
            action = repair(card)
            if action:
                touched = True
                actions.append({
                    "file": os.path.relpath(path, ROOT).replace(os.sep, "/"),
                    "name": str(card.get("name") or ""),
                    "action": action,
                    "was": deliverability.host_of(before_url),
                    "now": deliverability.host_of(url_of(card)),
                })
            if action != "remove":
                kept.append(card)
        if not touched:
            continue
        payload[key] = kept
        if isinstance(payload.get("count"), int):
            payload["count"] = len(kept)
        files_changed += 1
        if args.dry_run:
            continue
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")

    by_action: Dict[str, int] = {}
    for row in actions:
        by_action[row["action"]] = by_action.get(row["action"], 0) + 1
    for action, count in sorted(by_action.items()):
        print("  %-16s %4d" % (action, count))
    for row in actions:
        print("   %-14s %-40s %s -> %s"
              % (row["action"], row["name"][:40], row["was"] or "-", row["now"] or "-"))

    if not args.dry_run:
        out = args.out if os.path.isabs(args.out) else os.path.join(ROOT, args.out)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as handle:
            json.dump({
                "mode": "dropped_undeliverable_routes",
                "rule": "a bare-IP host has no path to the viewer",
                "cloudflare_error": deliverability.CLOUDFLARE_DIRECT_IP_ERROR,
                "counts": by_action,
                "actions": actions,
            }, handle, ensure_ascii=False, indent=1)
            handle.write("\n")

    print("\n%s: %d route action(s) across %d file(s)"
          % ("dry run" if args.dry_run else "done", len(actions), files_changed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
