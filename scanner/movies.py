"""
Movie VOD Pipeline and Pagination Processor

Reads verified/protected candidates from working/bd-results.json, keeps only the
normalized movie pipeline, resolves category conflicts using the required
movie-category priority, merges duplicate stream sources into cards, and builds
index/page payloads for deterministic 100-item pagination.

The actual atomic filesystem write is handled by scanner/output.py.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

try:
    from scanner.merger import merge_candidates
except ImportError:
    module_dir = str(Path(__file__).resolve().parent)
    if module_dir not in sys.path:
        sys.path.insert(0, module_dir)
    from merger import merge_candidates


VALID_MOVIE_CATEGORIES = (
    "Dubbed",
    "Bangla",
    "Hindi",
    "South Indian",
    "English",
    "Mix",
)

CATEGORY_SLUGS = {
    "Dubbed": "dubbed",
    "Bangla": "bangla",
    "Hindi": "hindi",
    "South Indian": "south-indian",
    "English": "english",
    "Mix": "mix",
}

_CATEGORY_LOOKUP = {
    re.sub(r"[^a-z0-9]+", "", category.lower()): category
    for category in VALID_MOVIE_CATEGORIES
}

_CATEGORY_PRIORITY = {
    category: index
    for index, category in enumerate(VALID_MOVIE_CATEGORIES)
}

DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 500


def _load_required_results(file_path: str | Path) -> List[Dict[str, Any]]:
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"BD results file not found: {file_path}")

    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(
            f"BD results file could not be read: {file_path}: {error}"
        ) from error

    if not isinstance(data, dict) or "results" not in data:
        raise ValueError(
            f"BD results file is invalid or missing 'results': {file_path}"
        )

    results = data.get("results")
    if not isinstance(results, list):
        raise ValueError(
            f"BD results field 'results' must be a list: {file_path}"
        )

    return [item for item in results if isinstance(item, dict)]


def _load_optional_json(file_path: str | Path) -> Dict[str, Any]:
    path = Path(file_path)
    if not path.exists():
        return {}

    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}


def _safe_page_size(value: Any, default: int = DEFAULT_PAGE_SIZE) -> int:
    try:
        page_size = int(value)
    except (TypeError, ValueError):
        page_size = default

    if page_size <= 0:
        page_size = default

    return min(page_size, MAX_PAGE_SIZE)


def _canonical_movie_category(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "Mix"

    key = re.sub(r"[^a-z0-9]+", "", text.lower())
    return _CATEGORY_LOOKUP.get(key, "Mix")


def _normalize_title(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(
        r"\b(?:official|movie|film|full|4k|2k|uhd|fhd|full\s*hd|hd|sd|"
        r"2160p|1440p|1080p|720p|480p|360p)\b",
        " ",
        text,
    )
    text = re.sub(r"[^\w]+", "-", text)
    return text.strip("-")


def _movie_identity(item: Dict[str, Any]) -> str:
    for field_name in ("id", "imdb_id", "tmdb_id", "tvg_id"):
        value = str(item.get(field_name) or "").strip().lower()
        if value:
            return f"{field_name}:{value}"

    title = _normalize_title(item.get("name") or item.get("title"))
    year = str(item.get("year") or "").strip()
    source_id = str(item.get("source_id") or "").strip().lower()

    if title:
        return f"title:{title}:{year}"

    return (
        f"fallback:{source_id}:"
        f"{item.get('stream_index', item.get('source_index', 0))}"
    )


def _resolve_category_precedence(
    candidates: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Resolve category conflicts: Dubbed > Bangla > Hindi > South Indian > English > Mix."""
    chosen_category: Dict[str, str] = {}

    for item in candidates:
        identity = _movie_identity(item)
        category = _canonical_movie_category(item.get("category"))
        current = chosen_category.get(identity)

        if current is None or _CATEGORY_PRIORITY[category] < _CATEGORY_PRIORITY[current]:
            chosen_category[identity] = category

    resolved: List[Dict[str, Any]] = []
    for item in candidates:
        item_copy = dict(item)
        item_copy["category"] = chosen_category[_movie_identity(item)]
        resolved.append(item_copy)

    return resolved


def _movie_sort_key(movie: Dict[str, Any]) -> Tuple[str, str, str]:
    name = str(movie.get("name") or movie.get("title") or "").strip()
    normalized_name = re.sub(r"\s+", " ", name).casefold()
    year = str(movie.get("year") or "")
    movie_id = str(movie.get("id") or movie.get("tvg_id") or "")
    return normalized_name, year, movie_id


def paginate_movie_list(
    movies: List[Dict[str, Any]],
    category_name: str,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> Dict[str, Any]:
    canonical_category = _canonical_movie_category(category_name)
    category_slug = CATEGORY_SLUGS[canonical_category]
    safe_page_size = _safe_page_size(page_size)
    ordered_movies = sorted(
        [movie for movie in movies if isinstance(movie, dict)],
        key=_movie_sort_key,
    )

    total_count = len(ordered_movies)
    total_pages = (
        (total_count + safe_page_size - 1) // safe_page_size
        if total_count
        else 0
    )

    page_entries: List[Dict[str, Any]] = []
    page_contents: Dict[str, Dict[str, Any]] = {}

    for page_number in range(1, total_pages + 1):
        page_filename = f"page-{page_number:03d}.json"
        relative_path = f"data/movies/{category_slug}/{page_filename}"
        start_index = (page_number - 1) * safe_page_size
        page_items = ordered_movies[
            start_index : start_index + safe_page_size
        ]

        page_entries.append(
            {
                "page": page_number,
                "file": page_filename,
                "path": relative_path,
                "count": len(page_items),
            }
        )

        page_contents[page_filename] = {
            "category": canonical_category,
            "slug": category_slug,
            "page": page_number,
            "page_size": safe_page_size,
            "count": len(page_items),
            "total_count": total_count,
            "total_pages": total_pages,
            "items": page_items,
        }

    index_payload: Dict[str, Any] = {
        "category": canonical_category,
        "slug": category_slug,
        "count": total_count,
        "page_size": safe_page_size,
        "total_pages": total_pages,
        "pages": page_entries,
    }

    return {
        "index": index_payload,
        "page_contents": page_contents,
    }


def process_movies(
    bd_results_path: str = "working/bd-results.json",
    settings_path: str = "config/settings.json",
) -> Dict[str, Dict[str, Any]]:
    candidates = _load_required_results(bd_results_path)
    settings = _load_optional_json(settings_path)
    page_size = _safe_page_size(
        settings.get("movie_page_size", DEFAULT_PAGE_SIZE)
    )

    movie_candidates = [
        dict(item)
        for item in candidates
        if str(item.get("source_pipeline") or "").strip().lower() == "movies"
    ]

    resolved_candidates = _resolve_category_precedence(movie_candidates)
    merged_movies = merge_candidates(
        resolved_candidates,
        settings_path=settings_path,
    )

    grouped_movies: Dict[str, List[Dict[str, Any]]] = {
        category: [] for category in VALID_MOVIE_CATEGORIES
    }

    for movie in merged_movies:
        if not isinstance(movie, dict):
            continue
        category = _canonical_movie_category(movie.get("category"))
        movie_copy = dict(movie)
        movie_copy["category"] = category
        grouped_movies[category].append(movie_copy)

    return {
        category: paginate_movie_list(
            movies=grouped_movies[category],
            category_name=category,
            page_size=page_size,
        )
        for category in VALID_MOVIE_CATEGORIES
    }


if __name__ == "__main__":
    results = process_movies()
    for category, payload in results.items():
        index = payload["index"]
        print(
            f"Movie Category '{category}': "
            f"{index['count']} movies across "
            f"{index['total_pages']} page(s)"
        )
