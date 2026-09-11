"""Lightweight per-genre id indexes: data/movies/genres/*.json (PART 05).

Eight small files, one per frontend genre, each holding only the ids of the
movies in it:

    {"genre": "Action", "updated_at": "...", "count": 42,
     "items": ["remote-manual-some-film-2026", ...]}

Ids rather than whole cards, deliberately. The same movie is in Action and
in Thriller; duplicating its full record into both files would make the
genre data several times the size of the catalogue it describes, and would
go stale independently of it. The card data already exists in the category
pages, so an index only has to say which ids belong.

Order is the catalogue's own order - most recently added first - so a genre
page opens on new arrivals without needing a second sort.

LAST-GOOD RULE: a genre whose freshly-built list is empty keeps whatever it
had on disk. An empty list is a legitimate answer only while the metadata
backfill has genuinely not reached that genre yet; it is much more often
the symptom of a provider outage upstream, and overwriting a good index
with nothing is exactly the failure mode the plan forbids.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    from scanner import movie_genres
except ImportError:  # pragma: no cover - direct-module import path
    import movie_genres  # type: ignore

DEFAULT_ROOT = os.path.join("data", "movies", "genres")


def _iso(now: Optional[_dt.datetime] = None) -> str:
    return (now or _dt.datetime.now(_dt.timezone.utc)).isoformat()


def _atomic_write_json(file_path: str, payload: Dict[str, Any]) -> bool:
    try:
        target_dir = os.path.dirname(file_path) or "."
        os.makedirs(target_dir, exist_ok=True)
        temp_path = os.path.join(
            target_dir,
            f".{os.path.basename(file_path)}.{os.getpid()}.{time.time_ns()}.tmp",
        )
        try:
            with open(temp_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, file_path)
        finally:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
    except OSError:
        return False
    return True


def _load_existing(file_path: str) -> Dict[str, Any]:
    try:
        with open(file_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def iter_published_movies(paginated: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    """Every movie record in a process_movies() payload, in published order."""
    if not isinstance(paginated, dict):
        return
    for category_payload in paginated.values():
        if not isinstance(category_payload, dict):
            continue
        pages = category_payload.get("page_contents")
        if not isinstance(pages, dict):
            continue
        for page_name in sorted(pages):
            page = pages.get(page_name)
            if not isinstance(page, dict):
                continue
            items = page.get("items") or page.get("movies") or []
            if not isinstance(items, list):
                continue
            for item in items:
                if isinstance(item, dict):
                    yield item


def build(paginated: Dict[str, Any], *, now: Optional[_dt.datetime] = None) -> Dict[str, Dict[str, Any]]:
    """Genre slug -> index document, for the eight frontend genres."""
    buckets: Dict[str, List[str]] = {genre: [] for genre in movie_genres.FRONTEND_GENRES}
    seen: Dict[str, set] = {genre: set() for genre in movie_genres.FRONTEND_GENRES}

    for movie in iter_published_movies(paginated):
        movie_id = str(movie.get("id") or "").strip()
        if not movie_id:
            continue
        for genre in movie_genres.frontend_genres(movie.get("genres") or []):
            if movie_id in seen[genre]:
                continue
            seen[genre].add(movie_id)
            buckets[genre].append(movie_id)

    stamp = _iso(now)
    return {
        movie_genres.GENRE_SLUGS[genre]: {
            "genre": genre,
            "updated_at": stamp,
            "count": len(items),
            "items": items,
        }
        for genre, items in buckets.items()
    }


def write(
    indexes: Dict[str, Dict[str, Any]], *, root: str = DEFAULT_ROOT
) -> Tuple[int, int]:
    """Write each index. Returns (written, preserved).

    An empty new list never replaces a non-empty existing one.
    """
    written = 0
    preserved = 0
    for slug, payload in indexes.items():
        file_path = os.path.join(root, f"{slug}.json")
        if not payload.get("items"):
            existing = _load_existing(file_path)
            if existing.get("items"):
                preserved += 1
                continue
        if _atomic_write_json(file_path, payload):
            written += 1
    return written, preserved


def generate(
    paginated: Dict[str, Any],
    *,
    root: str = DEFAULT_ROOT,
    now: Optional[_dt.datetime] = None,
) -> Dict[str, Any]:
    """Build and write every genre index. Returns a small summary."""
    indexes = build(paginated, now=now)
    written, preserved = write(indexes, root=root)
    return {
        "written": written,
        "preserved": preserved,
        "counts": {slug: payload["count"] for slug, payload in sorted(indexes.items())},
        "total_indexed": sum(payload["count"] for payload in indexes.values()),
    }
