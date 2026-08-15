#!/usr/bin/env python3
"""Create the strict publish proof ledger from successful real-browser audits."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scanner.player_compatibility import playback_fingerprint


def _atomic_write(path: Path, payload: Any) -> None:
    temp = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(temp, path)


def _catalog_items() -> list[tuple[str, dict[str, Any]]]:
    records: list[tuple[str, dict[str, Any]]] = []
    channel_path = ROOT / "data" / "channels" / "bangla.json"
    channels = json.loads(channel_path.read_text(encoding="utf-8")).get("channels") or []
    records.extend(("channel", dict(item)) for item in channels if isinstance(item, dict))

    index = json.loads((ROOT / "data" / "movies" / "bangla" / "index.json").read_text(encoding="utf-8"))
    for entry in index.get("pages") or []:
        page_path = ROOT / str(entry.get("path") or "")
        payload = json.loads(page_path.read_text(encoding="utf-8"))
        records.extend(("movie", dict(item)) for item in payload.get("items") or [] if isinstance(item, dict))
    return records


def main() -> int:
    report_paths = [Path(value).resolve() for value in sys.argv[1:]]
    if not report_paths:
        raise SystemExit("Pass one or more successful browser audit report paths")

    passed: set[tuple[str, str]] = set()
    for report_path in report_paths:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
        for result in payload.get("results") or []:
            if isinstance(result, dict) and result.get("ok") is True:
                passed.add((str(result.get("kind") or ""), str(result.get("name") or "").strip()))

    proofs: list[dict[str, Any]] = []
    missing: list[str] = []
    for kind, item in _catalog_items():
        name = str(item.get("name") or item.get("title") or "").strip()
        if (kind, name) not in passed:
            missing.append(f"{kind}:{name}")
            continue
        fingerprint = playback_fingerprint(item)
        if not fingerprint:
            missing.append(f"{kind}:{name}:no-route")
            continue
        proofs.append({
            "kind": kind,
            "name": name,
            "year": item.get("year") if kind == "movie" else "",
            "category": str(item.get("category") or "Bangla"),
            "fingerprint": fingerprint,
            "route_count": 1 + len(item.get("backups") or []) + len(item.get("standby") or []),
        })

    if missing:
        raise RuntimeError(
            "Refusing incomplete player proof ledger; visible items without a successful audit: "
            + ", ".join(missing)
        )

    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "policy": "exact visible card and route-set fingerprint must have a decoded browser frame",
        "scope": {"channels": ["Bangla"], "movies": ["Bangla"]},
        "audit_reports": [str(path.relative_to(ROOT)).replace("\\", "/") for path in report_paths],
        "count": len(proofs),
        "proofs": proofs,
    }
    output_path = ROOT / "state" / "player-playback-proof.json"
    _atomic_write(output_path, payload)
    print(f"Player playback proof ledger updated: {len(proofs)} visible items")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

