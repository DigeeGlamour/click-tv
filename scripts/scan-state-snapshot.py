#!/usr/bin/env python3
"""Snapshot the few facts a before/after comparison of a scan needs.

Written because the first attempt at that comparison printed None for almost
every row. The cause was not subtle: the throwaway script that produced it
lived in a scratch directory that was cleared between runs, so "before" was
read from a file that no longer existed and every lookup returned None. A
comparison table full of None reads as "nothing measured", which is exactly the
wrong impression when the point is to show what a scan changed.

Two things fix that here. The script lives in the repository, and it can read
its inputs either from a working tree or from a git ref - so "before" can be
taken from the committed state, which is what the deployed site is actually
serving, rather than from a directory a scan is midway through rewriting.

Reads only. Writes nothing but its own JSON on stdout.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.parse
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Optional

FILES = {
    "indian": "data/channels/indian.json",
    "preference": "state/route-preference.json",
    "evidence": "state/route-evidence-cache.json",
    "audit": "reports/visibility-model-audit.json",
    "first_seen": "state/movie-first-seen.json",
}

MOVIE_CATEGORIES = (
    "bangla", "dubbed", "english", "hindi", "mix", "premium", "south-indian",
)


class Source:
    """Reads repository files from a working tree or from a git ref."""

    def __init__(self, root: Path, ref: Optional[str] = None) -> None:
        self.root = root
        self.ref = ref

    def read(self, relative: str) -> Optional[str]:
        if self.ref:
            result = subprocess.run(
                ["git", "show", f"{self.ref}:{relative}"],
                cwd=str(self.root), capture_output=True, text=True,
            )
            return result.stdout if result.returncode == 0 else None
        path = self.root / relative
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return None

    def json(self, relative: str) -> Optional[Any]:
        text = self.read(relative)
        if not text:
            return None
        try:
            return json.loads(text)
        except ValueError:
            return None

    def glob_movie_pages(self):
        """Movie page files, from the tree or from the ref's tree listing."""
        if self.ref:
            result = subprocess.run(
                ["git", "ls-tree", "-r", "--name-only", self.ref, "data/movies/"],
                cwd=str(self.root), capture_output=True, text=True,
            )
            names = result.stdout.split() if result.returncode == 0 else []
            return [n for n in names if "/page-" in n and n.endswith(".json")]
        base = self.root / "data" / "movies"
        return [
            str(p.relative_to(self.root))
            for p in sorted(base.glob("*/page-*.json"))
        ]


def _card(source: Source, name: str) -> Dict[str, Any]:
    payload = source.json(FILES["indian"])
    rows = payload if isinstance(payload, list) else (payload or {}).get("channels") or []
    card = next(
        (r for r in rows if isinstance(r, dict) and r.get("name") == name), {}
    )
    split = urllib.parse.urlsplit(str(card.get("url") or ""))
    return {
        "present": bool(card),
        "host": split.hostname,
        "port": split.port,
        "stream_type": card.get("stream_type"),
        "verification_status": card.get("verification_status"),
        "publish_allowed": card.get("publish_allowed"),
        "resolution": card.get("resolution"),
        "resolution_height": card.get("resolution_height"),
        "resolution_exception": card.get("resolution_exception"),
        "backup_hosts": [
            urllib.parse.urlsplit(str(b.get("url") or "")).hostname
            for b in (card.get("backups") or [])
            if isinstance(b, dict)
        ],
        "backup_count": len(card.get("backups") or []),
    }


def _movies(source: Source) -> Dict[str, Any]:
    total = 0
    with_year = 0
    with_first_seen = 0
    marked_new = 0
    per_category: Dict[str, int] = {}
    first_page_titles = []
    for relative in source.glob_movie_pages():
        payload = source.json(relative)
        if payload is None:
            continue
        items = (
            payload
            if isinstance(payload, list)
            else (payload.get("items") or payload.get("movies") or [])
        )
        if not isinstance(items, list):
            continue
        slug = relative.split("/")[2] if relative.count("/") >= 2 else "?"
        per_category[slug] = per_category.get(slug, 0) + len(items)
        for movie in items:
            if not isinstance(movie, dict):
                continue
            total += 1
            if str(movie.get("year") or "").strip():
                with_year += 1
            if str(movie.get("first_seen_at") or "").strip():
                with_first_seen += 1
            if movie.get("is_new") is True:
                marked_new += 1
        if relative.endswith("mix/page-001.json"):
            first_page_titles = [
                str(m.get("name") or "")[:52]
                for m in items[:6]
                if isinstance(m, dict)
            ]
    return {
        "total": total,
        "with_year": with_year,
        "with_first_seen_at": with_first_seen,
        "marked_new": marked_new,
        "per_category": dict(sorted(per_category.items())),
        "mix_page_001_first_titles": first_page_titles,
    }


def _evidence(source: Source) -> Dict[str, Any]:
    payload = source.json(FILES["evidence"]) or {}
    routes = payload.get("routes") or {}
    vantages: Counter = Counter()
    dual = 0
    for records in routes.values():
        ids = set()
        for record in records:
            identifier = (record.get("test_vantage") or {}).get("id")
            vantages[identifier] += 1
            ids.add(identifier)
        if len(ids) > 1:
            dual += 1
    return {
        "routes": len(routes),
        "records": sum(len(v) for v in routes.values()),
        "by_vantage": dict(vantages),
        "dual_vantage_routes": dual,
    }


def snapshot(source: Source, label: str) -> Dict[str, Any]:
    preference = source.json(FILES["preference"]) or {}
    entry = (preference.get("preferred") or {}).get("channel|zee bangla") or {}
    audit = source.json(FILES["audit"]) or {}
    first_seen = source.json(FILES["first_seen"]) or {}
    return {
        label: {
            "zee_card": _card(source, "Zee Bangla"),
            "star_jalsha_card": _card(source, "Star Jalsha"),
            "preference": {
                "route_id": entry.get("route_id"),
                "recorded_at": entry.get("recorded_at"),
                "pass_count": entry.get("pass_count"),
                "superseded": len(entry.get("superseded") or []),
            },
            "evidence_cache": _evidence(source),
            "audit": {
                "mode": audit.get("mode"),
                "enforced": (audit.get("enforcement") or {}).get("enforced"),
                "decisions_seen": audit.get("decisions_seen"),
                "model_would_hide": audit.get("model_would_hide"),
                "hmac_key_id": (audit.get("hmac_key") or {}).get("key_id"),
            },
            "movies": _movies(source),
            "first_seen_store_entries": len((first_seen.get("seen") or {})),
        }
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", help="repository root to read from")
    parser.add_argument("label", help="key to nest the snapshot under")
    parser.add_argument(
        "--ref",
        default="",
        help="read from this git ref instead of the working tree",
    )
    args = parser.parse_args()
    source = Source(Path(args.root), args.ref or None)
    json.dump(
        snapshot(source, args.label), sys.stdout, indent=2, ensure_ascii=False
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
