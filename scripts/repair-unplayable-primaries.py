"""Apply the playable-primary rule to the channel catalogue already published.

The scan enforces this from now on, but the files in the repository were
written before it existed, and the CI check reads those files. So this walks
data/channels/*.json once and applies exactly the same decision - promote a
playable backup, or hold the card back - through the same module, so there is
no second implementation to drift.

Prints what it changed and writes nothing else.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scanner import playback_evidence, unplayable_primary  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def _dedupe_backups(rows) -> int:
    """One route, one entry. Returns how many duplicates were removed.

    The rule now lives in the scan, so this calls it rather than keeping a
    second copy that can drift: the committed data and the next scan have
    to agree about what a duplicate is.
    """
    return len(unplayable_primary.dedupe_backup_urls(rows))


def main() -> int:
    total_promoted = total_hidden = 0
    for path in sorted((ROOT / "data" / "channels").glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = payload if isinstance(payload, list) else payload.get("channels")
        if not isinstance(rows, list):
            continue

        promoted, hidden, report = unplayable_primary.enforce(rows)
        deduped = _dedupe_backups(rows)
        if not report and not deduped:
            continue
        # A held-back card leaves the published file; that is what
        # publish_allowed False means everywhere else in the scanner.
        kept = [row for row in rows
                if not (isinstance(row, dict) and row.get("publish_allowed") is False)]
        if isinstance(payload, list):
            payload = kept
        else:
            payload["channels"] = kept
            if isinstance(payload.get("count"), int):
                payload["count"] = len(kept)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")

        total_promoted += promoted
        total_hidden += hidden
        print(f"{path.name}: {promoted} promoted, {hidden} held back, "
              f"{deduped} duplicate backup(s) removed, "
              f"{len(rows)} -> {len(kept)} cards")
        for row in report:
            print(f"   {row['action']}: {row['name']} - {row['reason'][:56]}")

    print(f"\ntotal: {total_promoted} promoted, {total_hidden} held back")

    left = []
    for path in sorted((ROOT / "data" / "channels").glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = payload if isinstance(payload, list) else (payload.get("channels") or [])
        for row in rows:
            url = str(row.get("url") or "")
            if url and playback_evidence.unproven_reason(url):
                left.append(f"{row.get('name')} ({path.name})")
    print(f"cards still leading with a measured-unplayable route: {len(left)}")
    for name in left:
        print("   ", name)
    return 1 if left else 0


if __name__ == "__main__":
    sys.exit(main())
