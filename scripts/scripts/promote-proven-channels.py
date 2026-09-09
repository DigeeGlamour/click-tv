#!/usr/bin/env python3
"""Phase 3: restore channels that a 120 s measurement proved playable.

Sixteen live channels were removed from data/channels/ on the strength of a
name-keyed ledger entry saying two browser attempts produced no frame. Phase 1
measured all sixteen for a full 120 s, twice, 120 s apart, through the site's own
attempt plan. Seven of them play perfectly - Channel 24 reached 123.94 s of media
in a 120 s window with zero stall, both times. The evidence they were removed on
was wrong.

This puts exactly those seven back, and nothing else. The rules it will not bend:

  * a channel is restored ONLY if reports/phase1-sustained-playback.json marks it
    proven, which needs two independent full PASSes;
  * the record written is the one preserved in the failure ledger, byte for byte -
    no URL, credential, header profile or proxy mode is invented or altered;
  * a channel already present in the catalogue is left alone;
  * the failure-ledger entry for a restored channel is removed, because leaving it
    would let the next scan hide the channel again on the evidence just disproved;
  * nothing is hidden, ever, by this script.

Run with --dry-run first; it prints exactly what it would do and writes nothing.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from scanner import route_evidence as rev  # noqa: E402
from scanner import sustained_proof  # noqa: E402

PHASE1 = "reports/phase1-sustained-playback.json"
LEDGER = "reports/confirmed-player-failures.json"

#: Fields that described the disproved failure. They are dropped so a restored
#: card does not carry a verdict the measurement has just contradicted.
STALE_FAILURE_FIELDS = (
    "player_visibility",
    "verification_note",
    "network_verification_status",
)


def load(path: str) -> Any:
    with open(os.path.join(ROOT, path), "r", encoding="utf-8") as handle:
        return json.load(handle)


def save(path: str, payload: Any) -> None:
    full = os.path.join(ROOT, path)
    with open(full, "r", encoding="utf-8") as handle:
        raw = handle.read()
    newline = "\r\n" if "\r\n" in raw else "\n"
    trailing = raw.endswith(("\n", "\r\n"))
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    if newline != "\n":
        text = text.replace("\n", newline)
    with open(full, "w", encoding="utf-8", newline="") as handle:
        handle.write(text + (newline if trailing else ""))


def proven_names(phase1: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Names whose Phase 1 result carries two full PASSes, with their evidence."""
    out = {}
    for result in phase1.get("results") or ():
        if not result.get("proven"):
            continue
        passes = [
            o for o in (result.get("observations") or ())
            if o.get("verdict") == rev.PROVEN
        ]
        if len(passes) < rev.REQUIRED_FRESH_SESSIONS:
            # Defensive: `proven` and the observation list must agree.
            continue
        out[str(result.get("name"))] = {
            "pass_count": len(passes),
            "window_seconds": phase1.get("window_seconds"),
            "sessions_separated_by": phase1.get("session_separation_seconds"),
            "browser_profile": phase1.get("browser_profile"),
            "media_progress_seconds": [
                (o.get("playback_metrics") or {}).get("media_progress_seconds")
                for o in passes
            ],
            "cumulative_stall_seconds": [
                (o.get("playback_metrics") or {}).get("cumulative_stall_seconds")
                for o in passes
            ],
        }
    return out


def catalogue_path(ledger_file: str) -> str:
    """The ledger stores Windows-style paths; normalise to this platform."""
    return str(ledger_file or "").replace("\\", "/")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    phase1 = load(PHASE1)
    ledger = load(LEDGER)
    proven = proven_names(phase1)
    if not proven:
        print("nothing is proven; nothing to restore")
        return 0

    print(f"proven by measurement: {len(proven)}")
    for name, ev in proven.items():
        print(f"  {name:<18} {ev['pass_count']} full PASS, "
              f"media {ev['media_progress_seconds']}s, "
              f"stall {ev['cumulative_stall_seconds']}s")

    # Record the proof OUTSIDE the card, before touching the catalogue. Measured:
    # the first scan after the restoration rebuilt every card from its sources and
    # erased the status, mode and note the restoration had written, so a card is
    # not somewhere proof can live.
    for name, ev in proven.items():
        written, why = sustained_proof.record(
            "channel",
            name,
            {
                "pass_count": ev["pass_count"],
                "window_seconds": ev["window_seconds"],
                "session_separation_seconds": ev["sessions_separated_by"],
                "browser_profile": ev["browser_profile"],
                "media_progress_seconds": ev["media_progress_seconds"],
                "cumulative_stall_seconds": ev["cumulative_stall_seconds"],
                "evidence_report": PHASE1,
            },
            path=None if not args.dry_run else os.devnull,
        )
        print(f"  proof registry: {name} -> {'recorded' if written else why}")

    restored: List[str] = []
    skipped: List[str] = []
    by_file: Dict[str, List[Dict[str, Any]]] = {}
    keep_records: List[Dict[str, Any]] = []

    for entry in ledger.get("records") or ():
        record = entry.get("record") or {}
        name = str(record.get("name") or "")
        if name not in proven:
            keep_records.append(entry)
            continue
        target = catalogue_path(entry.get("file") or "")
        if not target:
            skipped.append(f"{name}: ledger entry names no catalogue file")
            keep_records.append(entry)
            continue
        by_file.setdefault(target, []).append({"name": name, "record": record})

    for target, items in sorted(by_file.items()):
        full = os.path.join(ROOT, target)
        if not os.path.exists(full):
            for item in items:
                skipped.append(f"{item['name']}: {target} does not exist")
            continue
        payload = load(target)
        container = payload if isinstance(payload, list) else (
            payload.get("channels") or payload.get("items")
        )
        if container is None:
            for item in items:
                skipped.append(f"{item['name']}: cannot find the list in {target}")
            continue
        present = {
            str(c.get("name") or "") for c in container if isinstance(c, dict)
        }
        added = 0
        for item in items:
            if item["name"] in present:
                skipped.append(f"{item['name']}: already in {target}")
                continue
            card = dict(item["record"])
            # The measurement contradicts the stored failure, so the failure
            # verdict does not travel with the restored card.
            for field in STALE_FAILURE_FIELDS:
                card.pop(field, None)
            card["publish_allowed"] = True
            card["verification_status"] = "verified_sustained_playback"
            card["verification_mode"] = "phase1_120s_browser_x2"
            card["verification_note"] = (
                "Restored after two independent 120 s browser sessions each "
                "played this card to the full PASS floor. See "
                "reports/phase1-sustained-playback.json."
            )
            container.append(card)
            restored.append(item["name"])
            added += 1
        if added and not args.dry_run:
            save(target, payload)
        print(f"{target}: {'would add' if args.dry_run else 'added'} {added}")

    if restored and not args.dry_run:
        removed = len(ledger.get("records") or ()) - len(keep_records)
        ledger["records"] = keep_records
        ledger["phase1_disproved_removals"] = restored
        ledger["phase1_disproved_note"] = (
            "These entries were removed because a full 120 s measurement, run "
            "twice, disproved them. Leaving them would let the next scan hide "
            "the channel again on evidence already shown to be wrong."
        )
        save(LEDGER, ledger)
        print(f"{LEDGER}: removed {removed} disproved entr(ies)")

    print(f"\nrestored: {len(restored)}")
    for name in restored:
        print(f"  + {name}")
    if skipped:
        print(f"skipped: {len(skipped)}")
        for reason in skipped:
            print(f"  - {reason}")
    if args.dry_run:
        print("\n(dry run: nothing was written)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
