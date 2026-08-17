"""Requirement 3 - Today Match source coverage report.

For every configured source the scan records the same six columns:

    Fetched -> Parsed -> Matched -> Published -> Dropped -> Drop Reason

The point is diagnostic honesty. When a source contributes nothing, the report
says whether it was never fetched, fetched but unparseable, parsed but matched
to no fixture, or matched and then dropped at verification - and names the
reason. Guessing from a count of zero is what made the earlier "source has
matches but the scanner found none" reports impossible to act on.
"""
from __future__ import annotations

import json
import os
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List

REPORT_FILE = Path("reports/today-source-coverage.json")

DROP_REASONS = {
    "verification_failed": "reached verification and no link passed",
    "not_publishable": "verified but the publish gate rejected it",
    "no_event_identity": "no reliable event or channel identity",
    "duplicate_stream": "exact duplicate of a stream already kept",
    "merged_into_other_source": "folded into a card another source leads",
    "routed_elsewhere": "published under a different pipeline",
}


def _atomic_write(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=str(path.parent), delete=False,
        prefix=f".{path.name}.", suffix=".tmp",
    )
    try:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    finally:
        handle.close()
    os.replace(handle.name, path)


def _source_ids(item: Dict[str, Any]) -> List[str]:
    """Every source that contributed to a published card, not just the winner -
    otherwise a source whose stream became a backup looks like it published
    nothing."""
    ids: List[str] = []
    primary = str(item.get("source_id") or "").strip()
    if primary:
        ids.append(primary)
    for field in ("source_ids", "alias_source_ids"):
        value = item.get(field)
        if isinstance(value, list):
            ids.extend(str(entry).strip() for entry in value if str(entry).strip())
    provenance = item.get("source_provenance")
    if isinstance(provenance, list):
        for entry in provenance:
            if isinstance(entry, dict):
                candidate = str(entry.get("source_id") or "").strip()
                if candidate:
                    ids.append(candidate)
    for backup in item.get("backups") or []:
        if isinstance(backup, dict):
            candidate = str(backup.get("source_id") or "").strip()
            if candidate:
                ids.append(candidate)
    return ids


def build_source_coverage(
    configured_sources: Iterable[Dict[str, Any]],
    raw_candidates: Iterable[Dict[str, Any]],
    parsed_candidates: Iterable[Dict[str, Any]],
    matched_candidates: Iterable[Dict[str, Any]],
    published_items: Iterable[Dict[str, Any]],
    fetch_errors: Dict[str, str] | None = None,
) -> Dict[str, Any]:
    errors = dict(fetch_errors or {})
    fetched = Counter(str(c.get("source_id") or "") for c in raw_candidates)
    parsed = Counter(str(c.get("source_id") or "") for c in parsed_candidates)
    matched = Counter(str(c.get("source_id") or "") for c in matched_candidates)

    published = Counter()
    for item in published_items:
        for source_id in set(_source_ids(item)):
            published[source_id] += 1

    rows: List[Dict[str, Any]] = []
    known = [str(s.get("id") or s.get("source_id") or "").strip() for s in configured_sources]
    for source_id in [s for s in known if s] or sorted(set(fetched) | set(parsed)):
        row_fetched = fetched.get(source_id, 0)
        row_parsed = parsed.get(source_id, 0)
        row_matched = matched.get(source_id, 0)
        row_published = published.get(source_id, 0)
        dropped = max(0, row_matched - row_published)

        if source_id in errors:
            reason = f"fetch failed: {errors[source_id]}"
        elif row_fetched == 0:
            reason = "nothing fetched from this source"
        elif row_parsed == 0:
            reason = "fetched but no entry parsed"
        elif row_matched == 0:
            reason = DROP_REASONS["no_event_identity"]
        elif dropped and row_published == 0:
            reason = DROP_REASONS["verification_failed"]
        elif dropped:
            reason = DROP_REASONS["merged_into_other_source"]
        else:
            reason = ""

        rows.append({
            "source_id": source_id,
            "fetched": row_fetched,
            "parsed": row_parsed,
            "matched": row_matched,
            "published": row_published,
            "dropped": dropped,
            "drop_reason": reason,
        })

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_count": len(rows),
        "totals": {
            "fetched": sum(r["fetched"] for r in rows),
            "parsed": sum(r["parsed"] for r in rows),
            "matched": sum(r["matched"] for r in rows),
            "published": sum(r["published"] for r in rows),
            "dropped": sum(r["dropped"] for r in rows),
        },
        "sources": rows,
    }


def write_source_coverage(report: Dict[str, Any], path: Path | str = REPORT_FILE) -> None:
    _atomic_write(Path(path), report)


def format_source_coverage(report: Dict[str, Any]) -> str:
    lines = [
        f"{'source':38s} {'fetch':>6s} {'parse':>6s} {'match':>6s} {'pub':>5s} {'drop':>5s}  reason",
    ]
    for row in report.get("sources", []):
        lines.append(
            f"{row['source_id'][:38]:38s} {row['fetched']:6d} {row['parsed']:6d} "
            f"{row['matched']:6d} {row['published']:5d} {row['dropped']:5d}  {row['drop_reason']}"
        )
    totals = report.get("totals", {})
    lines.append(
        f"{'TOTAL':38s} {totals.get('fetched', 0):6d} {totals.get('parsed', 0):6d} "
        f"{totals.get('matched', 0):6d} {totals.get('published', 0):5d} {totals.get('dropped', 0):5d}"
    )
    return "\n".join(lines)
