"""Hide current catalogue cards that failed the bounded real-Chromium check.

The scanner/report history and public playback profile catalogue are retained.
Only the player-facing channel/movie JSON is filtered. Cached failures expire
after 12 hours, so a temporary CDN/browser fault is retried automatically.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CACHE_PATH = ROOT / "state" / "browser-playback-cache.json"
REPORT_PATH = ROOT / "reports" / "browser-playback-smoke.json"
FAIL_TTL_MS = 12 * 60 * 60 * 1000


def read_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return fallback


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.browser-smoke.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def fingerprint(value: Any) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()[:24]


def playback_key(item: dict[str, Any]) -> str:
    return str(item.get("playback_id") or item.get("url") or "").strip()


def item_key(kind: str, item: dict[str, Any]) -> str:
    identity = str(item.get("id") or item.get("playback_id") or item.get("name") or "").strip().lower()
    return f"{kind}:{identity}:{fingerprint(playback_key(item))}"


def status_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(str(item.get("verification_status") or "unknown") for item in items)
    return dict(sorted(counts.items()))


def main() -> int:
    cache = read_json(CACHE_PATH, {})
    records = cache.get("records") if isinstance(cache, dict) else {}
    if not isinstance(records, dict):
        records = {}
    now_ms = int(__import__("time").time() * 1000)

    def failed(kind: str, item: dict[str, Any]) -> bool:
        record = records.get(item_key(kind, item))
        return bool(
            isinstance(record, dict)
            and record.get("status") == "failed"
            and now_ms - int(record.get("checked_at_ms") or 0) < FAIL_TTL_MS
        )

    removed: list[dict[str, str]] = []
    manifest = read_json(DATA / "manifest.json", {})

    for path in sorted((DATA / "channels").glob("*.json")):
        payload = read_json(path, {})
        items = payload.get("channels") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            continue
        kept = []
        for item in items:
            if isinstance(item, dict) and failed("channel", item):
                removed.append({"kind": "channel", "id": str(item.get("id") or ""), "name": str(item.get("name") or "")})
            else:
                kept.append(item)
        if len(kept) == len(items):
            continue
        payload["channels"] = kept
        payload["count"] = len(kept)
        write_json(path, payload)
        category = str(payload.get("category") or "")
        if isinstance(manifest.get("channels", {}).get(category), dict):
            manifest["channels"][category]["count"] = len(kept)
            manifest["channels"][category]["visible"] = bool(kept)

    for category_dir in sorted((DATA / "movies").iterdir()):
        if not category_dir.is_dir():
            continue
        index_path = category_dir / "index.json"
        index = read_json(index_path, {})
        pages = index.get("pages") if isinstance(index, dict) else None
        if not isinstance(pages, list):
            continue
        all_items: list[dict[str, Any]] = []
        old_page_paths: list[Path] = []
        for page_entry in pages:
            page_path = category_dir / str(page_entry.get("file") or "")
            old_page_paths.append(page_path)
            page = read_json(page_path, {})
            for item in page.get("items", []) if isinstance(page, dict) else []:
                if isinstance(item, dict) and failed("movie", item):
                    removed.append({"kind": "movie", "id": str(item.get("id") or ""), "name": str(item.get("name") or "")})
                elif isinstance(item, dict):
                    all_items.append(item)
        previous_count = int(index.get("count") or len(all_items))
        if len(all_items) == previous_count:
            continue

        page_size = max(1, int(index.get("page_size") or 100))
        chunks = [all_items[offset : offset + page_size] for offset in range(0, len(all_items), page_size)] or [[]]
        total_pages = len(chunks)
        total_status = status_counts(all_items)
        new_pages = []
        for number, chunk in enumerate(chunks, start=1):
            file_name = f"page-{number:03d}.json"
            page_path = category_dir / file_name
            page_payload = {
                "category": index.get("category"),
                "slug": index.get("slug"),
                "page": number,
                "page_size": page_size,
                "count": len(chunk),
                "total_count": len(all_items),
                "total_pages": total_pages,
                "status_counts": total_status,
                "page_status_counts": status_counts(chunk),
                "manual_trusted_count": sum(str(item.get("verification_status")) == "manual_trusted" for item in chunk),
                "status_order": index.get("status_order", []),
                "items": chunk,
            }
            write_json(page_path, page_payload)
            new_pages.append({
                "page": number,
                "file": file_name,
                "path": f"data/movies/{category_dir.name}/{file_name}",
                "count": len(chunk),
                "manual_trusted_count": page_payload["manual_trusted_count"],
                "status_counts": page_payload["page_status_counts"],
            })
        for stale_path in old_page_paths[total_pages:]:
            if stale_path.is_file():
                stale_path.unlink()

        index["count"] = len(all_items)
        index["total_pages"] = total_pages
        index["status_counts"] = total_status
        index["manual_trusted_count"] = sum(str(item.get("verification_status")) == "manual_trusted" for item in all_items)
        index["pages"] = new_pages
        write_json(index_path, index)
        category = str(index.get("category") or "")
        if isinstance(manifest.get("movies", {}).get(category), dict):
            manifest["movies"][category]["count"] = len(all_items)
            manifest["movies"][category]["total_pages"] = total_pages
            manifest["movies"][category]["visible"] = bool(all_items)

    write_json(DATA / "manifest.json", manifest)
    report = read_json(REPORT_PATH, {})
    if isinstance(report, dict):
        report["hidden_from_player"] = removed
        report["hidden_count"] = len(removed)
        report["policy"] = "failed cards are retained in reports/playback catalogue but removed from player-facing JSON for 12 hours"
        write_json(REPORT_PATH, report)
    print(f"Browser smoke publication gate hid {len(removed)} current card(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
