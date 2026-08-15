#!/usr/bin/env python3
"""Hide twice-confirmed player failures while retaining complete records."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORTS = (
    ROOT / "reports" / "browser-full-bangla-confirmation-channel.json",
    ROOT / "reports" / "browser-full-bangla-confirmation-movie.json",
)


def _atomic_write(path: Path, payload: Any) -> None:
    temp = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(temp, path)


def _load_failed_names(paths: list[Path]) -> dict[str, set[str]]:
    failed: dict[str, set[str]] = {"channel": set(), "movie": set()}
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(f"Browser confirmation report not found: {path}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        for result in payload.get("results") or []:
            if not isinstance(result, dict) or result.get("ok") is not False:
                continue
            kind = str(result.get("kind") or "").strip().casefold()
            name = str(result.get("name") or "").strip()
            if kind in failed and name:
                failed[kind].add(name)
    return failed


def _hide_from_payload(path: Path, list_key: str, failed_names: set[str]) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = payload.get(list_key)
    if not isinstance(items, list):
        return []
    hidden = [dict(item) for item in items if isinstance(item, dict) and str(item.get("name") or "") in failed_names]
    if not hidden:
        return []
    payload[list_key] = [item for item in items if not (isinstance(item, dict) and str(item.get("name") or "") in failed_names)]
    payload["count"] = len(payload[list_key])
    _atomic_write(path, payload)
    return hidden


def main() -> int:
    report_paths = [Path(value).resolve() for value in sys.argv[1:]] or list(DEFAULT_REPORTS)
    failed_names = _load_failed_names(report_paths)
    retained: list[dict[str, Any]] = []
    manifest_path = ROOT / "data" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    for path in (ROOT / "data" / "channels" / "bangla.json", ROOT / "state" / "last-good" / "bangla.json"):
        if not path.is_file():
            continue
        hidden = _hide_from_payload(path, "channels", failed_names["channel"])
        if path.parent.name == "channels":
            retained.extend({"kind": "channel", "file": str(path.relative_to(ROOT)), "record": item} for item in hidden)
            if hidden:
                count = len(json.loads(path.read_text(encoding="utf-8")).get("channels") or [])
                for entry in (manifest.get("channels") or {}).values():
                    if isinstance(entry, dict) and Path(str(entry.get("url") or "")).name == path.name:
                        entry["count"] = count

    for category_dir in (ROOT / "data" / "movies" / "bangla",):
        index_path = category_dir / "index.json"
        if not index_path.is_file():
            continue
        index = json.loads(index_path.read_text(encoding="utf-8"))
        category_hidden = 0
        for entry in index.get("pages", []):
            page_path = ROOT / str(entry.get("path") or "")
            if not page_path.is_file():
                continue
            hidden = _hide_from_payload(page_path, "items", failed_names["movie"])
            retained.extend({"kind": "movie", "file": str(page_path.relative_to(ROOT)), "record": item} for item in hidden)
            count = len(json.loads(page_path.read_text(encoding="utf-8")).get("items") or [])
            entry["count"] = count
            category_hidden += len(hidden)
        if category_hidden:
            total = sum(int(entry.get("count") or 0) for entry in index.get("pages", []))
            index["count"] = total
            _atomic_write(index_path, index)
            for entry in (manifest.get("movies") or {}).values():
                if isinstance(entry, dict) and Path(str(entry.get("index") or "")).parent.name == category_dir.name:
                    entry["count"] = total
                    entry["total_pages"] = int(index.get("total_pages") or len(index.get("pages") or []))

    for entry in retained:
        record = entry["record"]
        record["verified"] = False
        record["publish_allowed"] = False
        record["verification_status"] = "failed_player_twice"
        record["verification_mode"] = "local_browser_30s_plus_proxy_probe"
        record["verification_note"] = "Retained outside the visible catalogue after two real-player failures."

    _atomic_write(manifest_path, manifest)
    existing_report_path = ROOT / "reports" / "confirmed-player-failures.json"
    existing_records: list[dict[str, Any]] = []
    if existing_report_path.is_file():
        existing_payload = json.loads(existing_report_path.read_text(encoding="utf-8"))
        existing_records = [entry for entry in existing_payload.get("records") or [] if isinstance(entry, dict)]

    combined: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in existing_records + retained:
        record = entry.get("record") if isinstance(entry, dict) else None
        if not isinstance(record, dict):
            continue
        key = (str(entry.get("kind") or ""), str(record.get("name") or ""))
        combined[key] = entry
    retained = list(combined.values())

    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "policy": "two real-browser failures; preserve record and playback profiles; hide visible card",
        "count": len(retained),
        "newly_hidden_count": sum(1 for entry in retained if entry not in existing_records),
        "confirmation_reports": [str(path.relative_to(ROOT)) for path in report_paths],
        "records": retained,
    }
    _atomic_write(existing_report_path, report)
    print(
        "Confirmed player failures hidden: "
        f"new={len([entry for entry in retained if entry not in existing_records])}; retained_total={len(retained)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
