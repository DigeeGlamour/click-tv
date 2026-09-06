"""Merge two runs' event lists by fixture, not by line.

`git rebase -X theirs` merges JSON by hunk, which is how two runs' regenerated
arrays interleaved and put duplicate cards on the site. Keeping this run's files
whole fixes the duplicates and creates the opposite fault, because a run
publishes the list it checked out when it started:

    17:09:38  upcoming-targeted  94 fixtures     <- the trigger's checkout
    17:08:29  today              102 fixtures    <- a full scan, 8 more found
    17:14:02  upcoming-targeted  94 fixtures     <- all 8 gone again

The targeted list was byte for byte its own checkout: it kept 0 of the 8. They
came back at the next full scan and went again at the next overlapping trigger.
Measured over 31 publishes on 2026-09-06, 41 fixtures flickered in and out this
way, some missing for 19 minutes.

Neither "merge the text" nor "ours wins whole" is right, because neither knows
what a fixture is. This does the merge git would do if it could read the file -
three-way, keyed on fixture identity:

    in theirs, not in base, not in ours     they found it      keep it
    in base and theirs, not in ours         we retired it      drop it
    in ours and changed since base          we rescanned it    ours wins
    in ours unchanged, changed in theirs    they rescanned it  theirs wins

"We retired it" is what makes this safe to run on Today Match as well: a card
this run's live protection released is absent from ours and present in base, so
it is dropped rather than resurrected. A card this run never saw at all is
absent from base too, and is kept.

Three gates after the merge, each one a rule the scanner already enforces and
none of them new here: an archived fixture never comes back, a fixture may not
sit on both tabs, and two spellings of one fixture are one card.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import fixture_dedupe  # noqa: E402
from scanner.event_archive import drop_resurrected, load_archive  # noqa: E402
from scanner.targeted_scan import fixture_key  # noqa: E402

SURFACES = ("today-match", "upcoming")

#: How far past kickoff a fixture the other side found may still be added.
#: config/settings.json events.upcoming_past_grace_minutes, read there so the
#: two cannot drift.
DEFAULT_PAST_GRACE_MINUTES = 10


def _selector():
    """scripts/select-restorable-files.py, loaded by path.

    Both scripts need the same answer to "did this run scan events", and the
    answer is a receipt one of them already knows how to read. One definition,
    imported, rather than two that can disagree.
    """
    path = Path(__file__).resolve().parent / "select-restorable-files.py"
    spec = importlib.util.spec_from_file_location("select_restorable_files", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def blob(ref: str, path: str) -> Optional[Dict[str, Any]]:
    result = subprocess.run(["git", "show", "%s:%s" % (ref, path)],
                            capture_output=True, cwd=str(ROOT))
    if result.returncode:
        return None
    try:
        payload = json.loads(result.stdout.decode("utf-8", "replace"))
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def items_of(payload: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    items = payload.get("items")
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def key_of(item: Dict[str, Any]) -> str:
    """One fixture's identity. The scanner's own key, with the id as a fallback
    for a card the key cannot describe - never an empty string, because two
    unkeyable cards are not the same card."""
    key = fixture_key(item)
    if key:
        return "k:" + key
    event_id = str(item.get("id") or "").strip()
    return "i:" + event_id if event_id else "n:" + str(item.get("name") or "")


def index(items: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    found: Dict[str, Dict[str, Any]] = {}
    for item in items:
        found.setdefault(key_of(item), item)
    return found


def three_way(base: List[Dict[str, Any]],
              ours: List[Dict[str, Any]],
              theirs: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """The merge, in publication order: ours first, then what they added."""
    base_index, our_index, their_index = index(base), index(ours), index(theirs)
    stats = {"ours": 0, "they_added": 0, "we_retired": 0, "theirs_rescanned": 0}

    merged: List[Dict[str, Any]] = []
    for item in ours:
        key = key_of(item)
        was = base_index.get(key)
        their = their_index.get(key)
        # Unchanged on our side and changed on theirs: they rescanned it and we
        # only carried it. Their record is the one with this scan's work in it.
        if was is not None and their is not None and item == was and their != was:
            merged.append(their)
            stats["theirs_rescanned"] += 1
        else:
            merged.append(item)
            stats["ours"] += 1

    for item in theirs:
        key = key_of(item)
        if key in our_index:
            continue
        if key in base_index:
            # We had it and let it go. That is a decision, not an omission.
            stats["we_retired"] += 1
            continue
        merged.append(item)
        stats["they_added"] += 1
    return merged, stats


def _parse(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def past_grace_minutes(settings_path: Path) -> int:
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return DEFAULT_PAST_GRACE_MINUTES
    events = settings.get("events")
    if not isinstance(events, dict):
        return DEFAULT_PAST_GRACE_MINUTES
    try:
        return int(events.get("upcoming_past_grace_minutes"))
    except (TypeError, ValueError):
        return DEFAULT_PAST_GRACE_MINUTES


def still_publishable(item: Dict[str, Any], now: datetime, grace: int) -> bool:
    """Only asked of a fixture the other side added, and only about the clock.

    A card already on our list is not re-judged here: this step merges two
    publications, it does not re-run the lifecycle.
    """
    start = _parse(item.get("start_time") or item.get("start_at"))
    if start is None:
        return True
    return start >= now - timedelta(minutes=grace)


def write_surface(data_dir: Path, name: str, payload: Dict[str, Any],
                  items: List[Dict[str, Any]]) -> List[str]:
    """The flat mirror and the slot the manifest names, kept identical."""
    payload = dict(payload)
    payload["items"] = items
    payload["count"] = len(items)
    body = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"

    written: List[str] = []
    flat = data_dir / ("%s.json" % name)
    flat.write_text(body, encoding="utf-8", newline="\n")
    written.append(str(flat.relative_to(ROOT)))

    manifest = data_dir / "manifest.json"
    try:
        snapshot = json.loads(manifest.read_text(encoding="utf-8")).get("snapshot")
    except (OSError, ValueError):
        snapshot = None
    slot = str((snapshot or {}).get("slot") or "").strip()
    if slot:
        slot_file = data_dir / "snapshots" / slot / ("%s.json" % name)
        if slot_file.parent.is_dir():
            slot_file.write_text(body, encoding="utf-8", newline="\n")
            written.append(str(slot_file.relative_to(ROOT)))
    return written


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True, help="the commit this run branched from")
    parser.add_argument("--ours", required=True, help="the commit this run produced")
    parser.add_argument("--theirs", required=True, help="what was on main")
    parser.add_argument("--data-dir", default="data")
    args = parser.parse_args()

    # A run that never scanned events holds a stale copy of both lists. Merging
    # that with the live one would offer back every fixture the live one has
    # retired since - the resurrection this file exists to prevent, arriving by
    # the front door.
    if not _selector().scanned_events(args.ours, args.base):
        print("  this run did not scan events; its lists are not a side of any "
              "merge")
        return 0

    data_dir = ROOT / args.data_dir
    now = datetime.now(timezone.utc)
    grace = past_grace_minutes(ROOT / "config" / "settings.json")
    archive = load_archive()

    merged_lists: Dict[str, List[Dict[str, Any]]] = {}
    payloads: Dict[str, Dict[str, Any]] = {}
    changed_any = False

    for name in SURFACES:
        path = "data/%s.json" % name
        ours_payload = blob(args.ours, path)
        theirs_payload = blob(args.theirs, path)
        if ours_payload is None or theirs_payload is None:
            print("  %s: only one side published it; nothing to merge" % name)
            merged_lists[name] = items_of(ours_payload or theirs_payload)
            payloads[name] = ours_payload or theirs_payload or {}
            continue
        ours, theirs = items_of(ours_payload), items_of(theirs_payload)
        if ours == theirs:
            print("  %s: both sides published the same %d card(s)" % (name, len(ours)))
            merged_lists[name] = ours
            payloads[name] = ours_payload
            continue

        base = items_of(blob(args.base, path))
        merged, stats = three_way(base, ours, theirs)

        our_keys = set(index(ours))
        added = [item for item in merged if key_of(item) not in our_keys]
        stale = [item for item in added if not still_publishable(item, now, grace)]
        if stale:
            stale_keys = {key_of(item) for item in stale}
            merged = [item for item in merged if key_of(item) not in stale_keys]

        print("  %s: ours %d, theirs %d, base %d -> %d "
              "(%d theirs added, %d theirs rescanned, %d we retired, "
              "%d past the clock)"
              % (name, len(ours), len(theirs), len(base), len(merged),
                 stats["they_added"], stats["theirs_rescanned"],
                 stats["we_retired"], len(stale)))
        merged_lists[name] = merged
        payloads[name] = ours_payload
        changed_any = changed_any or merged != ours

    if not changed_any:
        print("  nothing to merge; this run's lists already carry both sides")
        return 0

    # An archived fixture never comes back, whichever side offered it.
    for name in SURFACES:
        merged_lists[name], resurrected = drop_resurrected(merged_lists[name], archive)
        if resurrected:
            print("  %s: %d archived fixture(s) refused" % (name, len(resurrected)))

    # One fixture, one tab. Today Match owns a fixture that is live.
    today_keys = {key_of(item) for item in merged_lists["today-match"]}
    before = len(merged_lists["upcoming"])
    merged_lists["upcoming"] = [item for item in merged_lists["upcoming"]
                                if key_of(item) not in today_keys]
    if before != len(merged_lists["upcoming"]):
        print("  upcoming: %d card(s) dropped for being live on Today Match"
              % (before - len(merged_lists["upcoming"])))

    # One fixture, one card - the same fold the scan runs, because a merge can
    # bring two feeds' spellings of one fixture together for the first time.
    for name in SURFACES:
        merged_lists[name], folded = fixture_dedupe.fold(
            merged_lists[name], lambda home, away, date: "")
        if folded:
            print("  %s: %d duplicate(s) folded" % (name, len(folded)))

    written: List[str] = []
    for name in SURFACES:
        written.extend(write_surface(data_dir, name, payloads.get(name) or {},
                                     merged_lists[name]))
    print("  merged event lists written:")
    for path in written:
        print("    %s" % path.replace("\\", "/"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
