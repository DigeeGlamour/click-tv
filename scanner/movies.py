"""
Movie VOD Pipeline, Manual Movie Loader and Pagination Processor

The scanner keeps verified/protected discovered movies and also loads trusted manual movies from ``manual/movies.txt`` and
``manual/movies.json``.

Manual movie rules:
- manual movie links are not sent through the network verification pipeline;
- manual movies are always pinned before discovered movies in their category;
- multiple links are kept as primary + backups;
- posters are resolved in this order:
  1. explicit ``logo``/``poster`` in a manual movie entry;
  2. cached poster from state/manual-movie-posters.json;
  3. matching poster already present in generated movie pages;
  4. TMDB search using TMDB_API_TOKEN or TMDB_API_KEY;
- a poster lookup failure never removes the movie;
- pagination output remains compatible with scanner/output.py.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

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

# Human-friendly aliases accepted by manual/movies.json.
_CATEGORY_LOOKUP.update(
    {
        "banglamovie": "Bangla",
        "banglamovies": "Bangla",
        "bengalimovie": "Bangla",
        "bengalimovies": "Bangla",
        "hindimovie": "Hindi",
        "hindimovies": "Hindi",
        "englishmovie": "English",
        "englishmovies": "English",
        "hollywood": "English",
        "dubbedmovie": "Dubbed",
        "dubbedmovies": "Dubbed",
        "southindianmovie": "South Indian",
        "southindianmovies": "South Indian",
        "tamil": "South Indian",
        "telugu": "South Indian",
        "malayalam": "South Indian",
        "kannada": "South Indian",
    }
)

_CATEGORY_PRIORITY = {
    category: index
    for index, category in enumerate(VALID_MOVIE_CATEGORIES)
}

DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 500
DEFAULT_MANUAL_MOVIES_PATH = "manual/movies.json"
DEFAULT_MANUAL_MOVIES_TEXT_PATH = "manual/movies.txt"
DEFAULT_POSTER_CACHE_PATH = "state/manual-movie-posters.json"
DEFAULT_GENERATED_MOVIES_ROOT = "data/movies"
DEFAULT_REMOTE_SOURCES_PATH = "manual/movie-sources.json"
DEFAULT_REMOTE_CACHE_PATH = "state/manual-movie-remote-cache.json"
DEFAULT_CONFLICT_REPORT_PATH = "reports/manual-movie-conflicts.json"
DEFAULT_MISSING_POSTER_REPORT_PATH = "reports/manual-movie-poster-missing.json"
REMOTE_FETCH_MAX_BYTES = 5_000_000
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/w500"
TMDB_SEARCH_URL = "https://api.themoviedb.org/3/search/movie"

def _source_url(source: Any) -> str:
    if isinstance(source, str):
        return source.strip()
    if not isinstance(source, dict):
        return ""
    return str(
        source.get("url")
        or source.get("stream_url")
        or source.get("link")
        or ""
    ).strip()

def _stream_type_from_source(source: Any) -> str:
    if isinstance(source, dict):
        explicit = str(
            source.get("stream_type")
            or source.get("type")
            or source.get("format")
            or ""
        ).strip().lower()
        if explicit in {"hls", "dash", "media", "mpegts"}:
            return explicit

    url = _source_url(source)
    try:
        path = (urllib.parse.urlparse(url).path or "").lower()
    except Exception:
        path = url.split("?", 1)[0].lower()

    if path.endswith(".m3u8"):
        return "hls"
    if path.endswith(".mpd"):
        return "dash"
    if path.endswith((".ts", ".mpegts", ".flv")):
        return "mpegts"
    return "media"

def _browser_source_rank(source: Any) -> int:
    url = _source_url(source)
    stream_type = _stream_type_from_source(source)
    try:
        path = (urllib.parse.urlparse(url).path or "").lower()
    except Exception:
        path = url.split("?", 1)[0].lower()

    if stream_type == "hls":
        return 0
    if stream_type == "dash":
        return 1
    if path.endswith((".mp4", ".m4v", ".webm")):
        return 2
    if path.endswith(".mov"):
        return 3
    if path.endswith((".mkv", ".avi", ".wmv", ".flv")):
        return 6
    if stream_type == "mpegts":
        return 5
    return 4

def _source_dict(source: Any, parent: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(source, str):
        return {
            "url": source,
            "header_profile": parent.get("header_profile", ""),
            "proxy_mode": parent.get("proxy_mode", "auto"),
            "stream_type": _stream_type_from_source(source),
        }
    if isinstance(source, dict):
        copy = dict(source)
        copy["url"] = _source_url(copy)
        copy.setdefault("header_profile", parent.get("header_profile", ""))
        copy.setdefault("proxy_mode", parent.get("proxy_mode", "auto"))
        copy.setdefault("stream_type", _stream_type_from_source(copy))
        return copy
    return {}

def _reorder_browser_sources(movie: Dict[str, Any]) -> Dict[str, Any]:
    """Prefer HLS/DASH/MP4/WebM without deleting conditional MKV backups."""
    movie_copy = dict(movie)
    sources: List[Dict[str, Any]] = []

    primary_url = _source_url(movie_copy)
    if primary_url:
        primary = dict(movie_copy)
        primary["url"] = primary_url
        sources.append(primary)

    backups = movie_copy.get("backups")
    if isinstance(backups, list):
        for backup in backups[:5]:
            normalized = _source_dict(backup, movie_copy)
            if normalized.get("url"):
                sources.append(normalized)

    deduped: List[Tuple[int, Dict[str, Any]]] = []
    seen = set()
    for order, source in enumerate(sources):
        url = _source_url(source)
        if not url or url in seen:
            continue
        seen.add(url)
        deduped.append((order, source))

    deduped.sort(
        key=lambda pair: (
            _browser_source_rank(pair[1]),
            0 if _source_url(pair[1]).lower().startswith("https://") else 1,
            pair[0],
        )
    )

    if not deduped:
        movie_copy["browser_support"] = "unavailable"
        return movie_copy

    best = deduped[0][1]
    for key in (
        "url",
        "header_profile",
        "proxy_mode",
        "stream_type",
        "verification_mode",
        "verification_status",
        "resolution",
        "width",
        "height",
        "bitrate",
        "source_id",
    ):
        if best.get(key) not in (None, ""):
            movie_copy[key] = best.get(key)

    movie_copy["url"] = _source_url(best)
    movie_copy["backups"] = [
        {
            key: value
            for key, value in source.items()
            if key not in {"headers", "cookie", "authorization"}
        }
        for _, source in deduped[1:6]
    ]

    best_rank = _browser_source_rank(best)
    movie_copy["browser_support"] = (
        "preferred" if best_rank <= 2
        else "conditional" if best_rank <= 4
        else "limited"
    )
    movie_copy["available_link_count"] = 1 + len(movie_copy["backups"])
    return movie_copy


MOVIE_STATUS_PRIORITY = {
    "manual_trusted": -1,
    "verified_global": 0,
    "verified_bd": 0,
    "verified": 0,
    "verified_proxy": 1,
    "stale_last_good": 2,
    "geo_pending": 3,
    "bd_protected_pending": 3,
    "retryable_pending": 4,
    "host_deferred": 5,
}


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


def _atomic_write_json(file_path: str | Path, data: Dict[str, Any]) -> None:
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")

    try:
        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass


def _safe_page_size(value: Any, default: int = DEFAULT_PAGE_SIZE) -> int:
    try:
        page_size = int(value)
    except (TypeError, ValueError):
        page_size = default

    if page_size <= 0:
        page_size = default

    return min(page_size, MAX_PAGE_SIZE)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clean_scalar(value: Any) -> str:
    if value is None or isinstance(value, (dict, list)):
        return ""
    return str(value).strip()


def _canonical_movie_category(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "Mix"

    key = re.sub(r"[^a-z0-9]+", "", text.lower())
    return _CATEGORY_LOOKUP.get(key, "Mix")


def _normalize_title(value: Any) -> str:
    text = str(value or "").strip().casefold()
    text = re.sub(r"^\s*\[\s*18\+\s*\]\s*", "", text)
    text = re.sub(
        r"\b(?:official|movie|film|full|4k|2k|uhd|fhd|full\s*hd|hd|sd|"
        r"2160p|1440p|1080p|720p|480p|360p|web[ ._-]?dl|webrip|"
        r"hdrip|hdtc|hevc|av1|x264|x265|esub)\b",
        " ",
        text,
    )
    text = re.sub(r"\b(?:19|20)\d{2}\b", " ", text)
    text = re.sub(r"[^\w]+", "-", text, flags=re.UNICODE)
    return text.strip("-")


def _display_title(value: Any) -> str:
    text = _clean_scalar(value)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _slugify(value: Any) -> str:
    text = str(value or "").casefold()
    text = re.sub(r"^\s*\[\s*18\+\s*\]\s*", "", text)
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-") or "manual-movie"


def _parse_year(value: Any) -> int:
    if isinstance(value, int) and 1900 <= value <= 2100:
        return value

    text = str(value or "").strip()
    match = re.search(r"\b((?:19|20)\d{2})\b", text)
    if match:
        return int(match.group(1))
    return 0


def _year_from_urls(raw_item: Dict[str, Any]) -> int:
    values: List[str] = []

    primary = _clean_scalar(raw_item.get("url"))
    if primary:
        values.append(primary)

    raw_links = raw_item.get("links")
    if isinstance(raw_links, list):
        for entry in raw_links:
            if isinstance(entry, str):
                values.append(entry)
            elif isinstance(entry, dict):
                url = _clean_scalar(entry.get("url") or entry.get("link"))
                if url:
                    values.append(url)

    for value in values:
        year = _parse_year(value)
        if year:
            return year
    return 0


def _movie_identity(item: Dict[str, Any]) -> str:
    # Manual/scanned duplicates should merge by title + year, not by a
    # source-generated ID that can differ between providers.
    title = _normalize_title(item.get("name") or item.get("title"))
    year = _parse_year(item.get("year"))

    if not year:
        year = _parse_year(item.get("name"))

    if title:
        return f"title:{title}:{year or 'unknown'}"

    for field_name in ("imdb_id", "tmdb_id", "tvg_id", "id"):
        value = str(item.get(field_name) or "").strip().casefold()
        if value:
            return f"{field_name}:{value}"

    source_id = str(item.get("source_id") or "").strip().casefold()
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


def _movie_sort_key(movie: Dict[str, Any]) -> Tuple[int, int, int, int, int, str, str]:
    """
    Ordering inside every movie category:
    1. every trusted manual movie before discovered movies;
    2. newest valid year first;
    3. local manual before remote manual only when year/order ties;
    4. source-file order for manual movies;
    5. verification confidence, title and ID for deterministic output.
    """
    is_manual = bool(
        movie.get("manual_source") is True
        or str(movie.get("verification_status") or "").casefold() == "manual_trusted"
        or str(movie.get("source_id") or "").casefold().startswith("manual-movie")
    )
    manual_rank = 0 if is_manual else 1

    year = _parse_year(movie.get("year"))
    unknown_year = 1 if year == 0 else 0

    source_tier = _safe_int(movie.get("manual_source_tier"), 9)
    manual_position = _safe_int(movie.get("manual_position"), 999999)
    status = str(movie.get("verification_status") or "").strip().casefold()
    status_priority = MOVIE_STATUS_PRIORITY.get(status, 99)

    name = str(movie.get("name") or movie.get("title") or "").strip()
    normalized_name = re.sub(r"\s+", " ", name).casefold()
    movie_id = str(movie.get("id") or movie.get("tvg_id") or "")

    return (
        manual_rank,
        unknown_year,
        -year,
        source_tier if is_manual else 9,
        manual_position if is_manual else 999999,
        f"{status_priority:03d}:{normalized_name}",
        movie_id,
    )

def _poster_identity(name: Any, year: Any) -> str:
    return f"{_normalize_title(name)}:{_parse_year(year) or 'unknown'}"


def _valid_poster_url(value: Any) -> str:
    text = _clean_scalar(value)
    if text.startswith(("https://", "http://", "data:image/")):
        return text
    return ""


def _load_generated_poster_map(
    generated_root: str | Path = DEFAULT_GENERATED_MOVIES_ROOT,
) -> Dict[str, str]:
    poster_map: Dict[str, str] = {}
    root = Path(generated_root)

    if not root.exists():
        return poster_map

    for page_path in sorted(root.glob("*/page-*.json")):
        try:
            with page_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue

        items = payload.get("items") if isinstance(payload, dict) else []
        if not isinstance(items, list):
            continue

        for item in items:
            if not isinstance(item, dict):
                continue

            logo = _valid_poster_url(item.get("logo") or item.get("poster"))
            if not logo:
                continue

            name = item.get("name") or item.get("title")
            year = item.get("year") or _parse_year(item.get("name"))
            identity = _poster_identity(name, year)
            if identity and identity not in poster_map:
                poster_map[identity] = logo

            # Also keep a title-only fallback for records whose year is absent.
            title_only = f"{_normalize_title(name)}:unknown"
            if title_only not in poster_map:
                poster_map[title_only] = logo

    return poster_map


def _load_poster_cache(cache_path: str | Path) -> Dict[str, str]:
    payload = _load_optional_json(cache_path)
    raw_posters = payload.get("posters")
    if not isinstance(raw_posters, dict):
        return {}

    return {
        str(key): value
        for key, raw_value in raw_posters.items()
        if (value := _valid_poster_url(raw_value))
    }


def _save_poster_cache(
    cache_path: str | Path,
    posters: Dict[str, str],
) -> None:
    _atomic_write_json(
        cache_path,
        {
            "version": 1,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "posters": dict(sorted(posters.items())),
        },
    )


def _tmdb_credentials() -> Tuple[str, str]:
    token = os.getenv("TMDB_API_TOKEN", "").strip()
    api_key = os.getenv("TMDB_API_KEY", "").strip()
    return token, api_key


def _tmdb_request_json(query: str, year: int = 0) -> Dict[str, Any]:
    token, api_key = _tmdb_credentials()
    if not token and not api_key:
        return {}

    params: Dict[str, Any] = {
        "query": query,
        "include_adult": "true",
        "language": "en-US",
        "page": "1",
    }
    if year:
        params["primary_release_year"] = str(year)
    if api_key:
        params["api_key"] = api_key

    request_url = TMDB_SEARCH_URL + "?" + urllib.parse.urlencode(params)
    headers = {
        "Accept": "application/json",
        "User-Agent": "Click-TV-Movie-Poster-Resolver/1.0",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = urllib.request.Request(request_url, headers=headers)

    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            raw = response.read(2_000_000)
        payload = json.loads(raw.decode("utf-8", errors="replace"))
        return payload if isinstance(payload, dict) else {}
    except (
        urllib.error.HTTPError,
        urllib.error.URLError,
        TimeoutError,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        ValueError,
    ):
        return {}


def _clean_poster_query(value: Any) -> str:
    text = _display_title(value)
    text = re.sub(r"^\s*\[\s*18\+\s*\]\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*\((?:19|20)\d{2}\)\s*$", "", text)
    return " ".join(text.split())


def _tmdb_poster_lookup(name: Any, year: int = 0) -> str:
    """Return only a strongly matching poster; never change trusted metadata."""
    query = _clean_poster_query(name)
    if not query:
        return ""

    payload = _tmdb_request_json(query, year)
    results = payload.get("results") if isinstance(payload, dict) else []
    if not isinstance(results, list):
        return ""

    normalized_query = _normalize_title(query)
    best_result: Optional[Dict[str, Any]] = None
    best_score = -1

    for result in results:
        if not isinstance(result, dict):
            continue
        poster_path = _clean_scalar(result.get("poster_path"))
        if not poster_path:
            continue

        titles = {
            _normalize_title(result.get("title")),
            _normalize_title(result.get("original_title")),
        }
        titles.discard("")
        if normalized_query not in titles:
            continue

        release_year = _parse_year(result.get("release_date"))
        if year and release_year != year:
            continue

        score = 100
        if year and release_year == year:
            score += 100
        try:
            score += min(20, int(float(result.get("popularity") or 0) // 10))
        except (TypeError, ValueError):
            pass

        if score > best_score:
            best_score = score
            best_result = result

    if not best_result:
        return ""

    poster_path = _clean_scalar(best_result.get("poster_path"))
    if not poster_path:
        return ""
    if not poster_path.startswith("/"):
        poster_path = "/" + poster_path
    return TMDB_IMAGE_BASE + poster_path

def _resolve_manual_poster(
    raw_item: Dict[str, Any],
    *,
    cache: Dict[str, str],
    generated_posters: Dict[str, str],
) -> str:
    explicit = _valid_poster_url(
        raw_item.get("logo")
        or raw_item.get("poster")
        or raw_item.get("image")
    )
    if explicit:
        return explicit

    if raw_item.get("poster_lookup") is False:
        return ""

    name = raw_item.get("poster_query") or raw_item.get("name") or raw_item.get("title")
    # The supplied manual year is trusted. URL/filename years never overwrite it.
    year = _parse_year(raw_item.get("poster_year") or raw_item.get("year"))
    identity = _poster_identity(name, year)

    for source in (cache, generated_posters):
        poster = _valid_poster_url(source.get(identity))
        if poster:
            cache[identity] = poster
            return poster

    poster = _tmdb_poster_lookup(name, year)
    if poster:
        cache[identity] = poster
    return poster

def _resolution_height(value: Any) -> int:
    if isinstance(value, (int, float)):
        return max(0, int(value))

    text = str(value or "").strip().casefold()
    match = re.search(r"(\d{3,4})\s*p", text)
    if match:
        return int(match.group(1))

    match = re.search(r"\d+\s*[x×]\s*(\d{3,4})", text)
    if match:
        return int(match.group(1))

    if "4k" in text or "2160" in text or "uhd" in text:
        return 2160
    if "2k" in text or "1440" in text:
        return 1440
    if "1080" in text or "fhd" in text or "full hd" in text:
        return 1080
    if "720" in text or text == "hd":
        return 720
    if "480" in text or text == "sd":
        return 480
    return 0


def _stream_type_for_url(url: str, explicit: Any = "") -> str:
    value = _clean_scalar(explicit).casefold()
    if value in {"hls", "dash", "media", "mpegts"}:
        return value

    clean_url = url.split("?", 1)[0].casefold()
    if clean_url.endswith(".m3u8"):
        return "hls"
    if clean_url.endswith(".mpd"):
        return "dash"
    if clean_url.endswith((".ts", ".m2ts")):
        return "mpegts"
    return "media"



def _manual_link_preference(link: Dict[str, Any]) -> Tuple[int, int, int]:
    """Prefer broadly compatible 1080p links before HEVC/AV1/4K backups."""
    url = str(link.get("url") or "").casefold()
    codec = str(link.get("codec") or "").casefold()
    height = _safe_int(link.get("resolution_height"), 0)

    if not codec:
        if "av1" in url:
            codec = "av1"
        elif "hevc" in url or "x265" in url or "h265" in url:
            codec = "hevc"

    codec_penalty = 2 if codec == "av1" else 1 if codec == "hevc" else 0
    height_penalty = 0 if height == 1080 else 1 if height == 720 else 2 if height >= 1440 else 1
    original_position = _safe_int(link.get("_manual_link_position"), 999999)
    return codec_penalty, height_penalty, original_position


def _parse_manual_movies_text(content: str) -> List[Dict[str, Any]]:
    """
    Parse the non-technical manual/movies.txt format used by the project owner.

    Example:
        Movie-1
        Movie name: Example
        Movie Category: Bangla Movies
        Movie year: 2026

        RESOLUTION 1: HD 1080P
        STREAM Link 1: https://example.com/movie.mkv
    """
    if not isinstance(content, str) or not content.strip():
        return []

    items: List[Dict[str, Any]] = []
    current: Dict[str, Any] = {}
    resolutions: Dict[int, str] = {}
    links_by_number: Dict[int, Dict[str, Any]] = {}

    def flush() -> None:
        nonlocal current, resolutions, links_by_number

        name = _display_title(current.get("name"))
        if name and links_by_number:
            links = [
                links_by_number[number]
                for number in sorted(links_by_number)
                if links_by_number[number].get("url")
            ]
            current["links"] = links
            current.setdefault("category", "Bangla")
            current.setdefault("poster_lookup", True)
            current.setdefault("enabled", True)
            items.append(dict(current))

        current = {}
        resolutions = {}
        links_by_number = {}

    for raw_line in content.lstrip("\ufeff").splitlines():
        line = raw_line.strip()

        if not line or re.fullmatch(r"=+", line):
            continue

        if re.fullmatch(r"Movie-\d+", line, flags=re.IGNORECASE):
            flush()
            continue

        field_match = re.match(r"([^:]+):\s*(.*)$", line)
        if not field_match:
            continue

        raw_key = field_match.group(1).strip()
        value = field_match.group(2).strip()
        key = raw_key.casefold()

        if key == "movie name":
            current["name"] = value
            continue

        if key == "movie category":
            current["category"] = value
            continue

        if key == "movie year":
            current["year"] = _parse_year(value) or ""
            continue

        if key in {"poster", "poster url", "logo", "logo url"}:
            current["logo"] = value
            continue

        if key in {"poster query", "tmdb query"}:
            current["poster_query"] = value
            continue

        if key in {"poster year", "tmdb year"}:
            current["poster_year"] = _parse_year(value) or ""
            continue

        if key == "enabled":
            current["enabled"] = value.casefold() not in {"0", "false", "no", "off"}
            continue

        resolution_match = re.fullmatch(
            r"resolution\s+(\d+)",
            key,
            flags=re.IGNORECASE,
        )
        if resolution_match:
            link_number = int(resolution_match.group(1))
            resolutions[link_number] = value
            continue

        link_match = re.fullmatch(
            r"stream\s+link\s+(\d+)",
            key,
            flags=re.IGNORECASE,
        )
        if link_match:
            link_number = int(link_match.group(1))
            resolution_text = resolutions.get(link_number, "")
            height = _resolution_height(resolution_text or value)
            codec = ""
            combined = f"{resolution_text} {value}".casefold()
            if "av1" in combined:
                codec = "av1"
            elif "hevc" in combined or "x265" in combined or "h265" in combined:
                codec = "hevc"

            link: Dict[str, Any] = {
                "url": value,
                "label": resolution_text or f"Link {link_number}",
                "resolution": resolution_text or (f"{height}p" if height else ""),
                "resolution_height": height,
                "_manual_link_position": link_number,
            }
            if codec:
                link["codec"] = codec

            links_by_number[link_number] = link
            continue

    flush()
    return items


def _load_manual_json_items(
    manual_movies_path: str | Path,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    path = Path(manual_movies_path)
    if not path.exists():
        return {}, []

    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(
            f"Manual movie JSON is invalid: {manual_movies_path}: {error}"
        ) from error

    if isinstance(payload, list):
        return {}, [item for item in payload if isinstance(item, dict)]

    if not isinstance(payload, dict):
        raise ValueError(
            f"Manual movie JSON root must be an object or list: {manual_movies_path}"
        )

    defaults = (
        dict(payload.get("defaults"))
        if isinstance(payload.get("defaults"), dict)
        else {}
    )
    raw_items = payload.get("items")
    if raw_items is None:
        raw_items = []
    if not isinstance(raw_items, list):
        raise ValueError(
            f"Manual movie JSON must contain an 'items' list: {manual_movies_path}"
        )

    items: List[Dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        item_copy = dict(item)
        item_copy.setdefault("_manual_origin", "json")
        items.append(item_copy)
    return defaults, items


def _load_manual_text_items(
    manual_movies_text_path: str | Path,
) -> List[Dict[str, Any]]:
    path = Path(manual_movies_text_path)
    if not path.exists():
        return []

    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        raise ValueError(
            f"Manual movie text could not be read: {manual_movies_text_path}: {error}"
        ) from error

    items = _parse_manual_movies_text(content)
    for item in items:
        item.setdefault("_manual_origin", "text")
    return items


def _manual_link_object(
    raw_link: Any,
    defaults: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    if isinstance(raw_link, str):
        link_data: Dict[str, Any] = {"url": raw_link}
    elif isinstance(raw_link, dict):
        link_data = dict(raw_link)
    else:
        return None

    url = _clean_scalar(link_data.get("url") or link_data.get("link"))
    if not url:
        return None

    resolution = (
        link_data.get("resolution")
        or link_data.get("quality")
        or defaults.get("resolution")
        or ""
    )
    height = _safe_int(
        link_data.get("resolution_height")
        or link_data.get("height"),
        0,
    )
    if height <= 0:
        height = _resolution_height(resolution or url)

    result: Dict[str, Any] = {
        "url": url,
        "headers": (
            dict(link_data.get("headers"))
            if isinstance(link_data.get("headers"), dict)
            else dict(defaults.get("headers") or {})
        ),
        "header_profile": _clean_scalar(
            link_data.get("header_profile")
            or defaults.get("header_profile")
            or "android_tv"
        ),
        "proxy_mode": _clean_scalar(
            link_data.get("proxy_mode")
            or defaults.get("proxy_mode")
            or "direct_first"
        ),
        "stream_type": _stream_type_for_url(
            url,
            link_data.get("stream_type")
            or defaults.get("stream_type"),
        ),
        "requires_headers": bool(
            link_data.get(
                "requires_headers",
                defaults.get("requires_headers", False),
            )
        ),
        "inherit_manifest_query": bool(
            link_data.get(
                "inherit_manifest_query",
                defaults.get("inherit_manifest_query", False),
            )
        ),
    }

    if resolution:
        result["resolution"] = str(resolution)
    if height:
        result["resolution_height"] = height

    for field_name in ("label", "codec", "edition", "language", "provider"):
        value = _clean_scalar(link_data.get(field_name))
        if value:
            result[field_name] = value

    return result


def _manual_movie_card(
    raw_item: Dict[str, Any],
    *,
    defaults: Dict[str, Any],
    manual_position: int,
    poster_cache: Dict[str, str],
    generated_posters: Dict[str, str],
) -> Optional[Dict[str, Any]]:
    name = _display_title(raw_item.get("name") or raw_item.get("title"))
    if not name:
        return None

    category = _canonical_movie_category(
        raw_item.get("category")
        or defaults.get("category")
        or "Bangla"
    )
    year = _parse_year(raw_item.get("year"))

    raw_links: List[Any] = []
    if isinstance(raw_item.get("links"), list):
        raw_links.extend(raw_item["links"])

    primary_url = _clean_scalar(raw_item.get("url") or raw_item.get("link"))
    if primary_url:
        raw_links.insert(
            0,
            {
                "url": primary_url,
                "resolution": raw_item.get("resolution", ""),
                "resolution_height": raw_item.get("resolution_height", 0),
                "header_profile": raw_item.get("header_profile", ""),
                "proxy_mode": raw_item.get("proxy_mode", ""),
                "stream_type": raw_item.get("stream_type", ""),
                "requires_headers": raw_item.get("requires_headers", False),
                "inherit_manifest_query": raw_item.get(
                    "inherit_manifest_query",
                    False,
                ),
                "headers": raw_item.get("headers", {}),
            },
        )

    links: List[Dict[str, Any]] = []
    seen_urls: set[str] = set()
    for raw_link in raw_links:
        link = _manual_link_object(raw_link, defaults)
        if not link:
            continue
        url = link["url"]
        if url in seen_urls:
            continue
        seen_urls.add(url)
        links.append(link)

    if not links:
        return None

    # A manual link can explicitly opt out of compatibility sorting by setting
    # preserve_link_order=true on the movie item.
    if raw_item.get("preserve_link_order") is not True:
        links.sort(key=_manual_link_preference)

    for link in links:
        link.pop("_manual_link_position", None)

    primary = links[0]
    backups = links[1:]
    poster = _resolve_manual_poster(
        raw_item,
        cache=poster_cache,
        generated_posters=generated_posters,
    )

    origin = str(raw_item.get("_manual_origin") or "json").casefold()
    explicit_id = _clean_scalar(raw_item.get("id"))
    id_prefix = "remote-manual" if origin == "remote" else "manual"
    movie_id = explicit_id or f"{id_prefix}-{_slugify(name)}-{year or 'unknown'}"

    card: Dict[str, Any] = {
        "id": movie_id,
        "name": name,
        "logo": poster,
        "category": category,
        "url": primary["url"],
        "headers": primary.get("headers", {}),
        "header_profile": primary.get("header_profile", "android_tv"),
        "proxy_mode": primary.get("proxy_mode", "direct_first"),
        "stream_type": primary.get("stream_type", "media"),
        "requires_headers": bool(primary.get("requires_headers", False)),
        "inherit_manifest_query": bool(
            primary.get("inherit_manifest_query", False)
        ),
        "verification_mode": "manual_local",
        "verification_status": "manual_trusted",
        "verification_badge": "Manual",
        "verification_note": "Trusted manual movie; scanner network verification skipped",
        "verified": True,
        "publish_allowed": True,
        "skip_verification": True,
        "manual_source": True,
        "manual_position": manual_position,
        "manual_source_tier": 2 if origin == "remote" else 0 if origin == "text" else 1,
        "source_pipeline": "movies",
        "original_source_pipeline": (
            "remote_manual_movies" if origin == "remote" else "manual_movies"
        ),
        "content_kind": "movie",
        "routing_reason": (
            "remote_manual_movie_source" if origin == "remote" else "manual_movie_file"
        ),
        "source_id": (
            _clean_scalar(raw_item.get("_remote_source_id"))
            if origin == "remote"
            else "manual-movies-text"
            if origin == "text"
            else "manual-movies-json"
        ),
        "source_name": (
            _clean_scalar(raw_item.get("_remote_source_name"))
            or _clean_scalar(raw_item.get("_remote_source_url"))
            if origin == "remote"
            else "manual/movies.txt"
            if origin == "text"
            else "manual/movies.json"
        ),
        "source_priority": 100000,
        "metadata_only": False,
        "available_link_count": len(links),
        "backups": backups,
    }

    if year:
        card["year"] = year
    else:
        card["year"] = ""

    for field_name in (
        "resolution",
        "resolution_height",
        "label",
        "codec",
        "edition",
        "language",
        "provider",
    ):
        value = primary.get(field_name)
        if value not in (None, ""):
            card[field_name] = value

    for field_name in ("description", "overview", "rating", "tmdb_id", "imdb_id"):
        value = raw_item.get(field_name)
        if value not in (None, ""):
            card[field_name] = value

    return card



def _load_remote_cache(cache_path: str | Path) -> Dict[str, Any]:
    payload = _load_optional_json(cache_path)
    if not isinstance(payload.get("sources"), dict):
        payload["sources"] = {}
    payload.setdefault("version", 1)
    return payload


def _fetch_remote_text(url: str, timeout_seconds: int) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "text/plain, application/json;q=0.9, */*;q=0.1",
            "Cache-Control": "no-cache",
            "User-Agent": "Click-TV-Manual-Movie-Importer/1.0",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        raw = response.read(REMOTE_FETCH_MAX_BYTES + 1)
    if len(raw) > REMOTE_FETCH_MAX_BYTES:
        raise ValueError("Remote manual movie source exceeded size limit")
    return raw.decode("utf-8", errors="replace")


def _remote_source_items(
    sources_path: str | Path,
    cache_path: str | Path,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    config = _load_optional_json(sources_path)
    if not config or config.get("enabled") is False:
        return [], {"enabled": False, "sources": []}

    raw_sources = config.get("sources")
    if not isinstance(raw_sources, list):
        raise ValueError(f"Remote movie source config must contain a sources list: {sources_path}")

    timeout_seconds = max(5, min(60, _safe_int(config.get("timeout_seconds"), 20)))
    use_cache = config.get("use_last_valid_cache", True) is not False
    cache = _load_remote_cache(cache_path)
    cache_sources = cache.setdefault("sources", {})
    output_items: List[Dict[str, Any]] = []
    source_report: List[Dict[str, Any]] = []
    cache_changed = False

    for source_order, source in enumerate(raw_sources, start=1):
        if not isinstance(source, dict) or source.get("enabled") is False:
            continue

        source_id = _clean_scalar(source.get("id")) or f"remote-source-{source_order}"
        source_name = _clean_scalar(source.get("name")) or source_id
        source_url = _clean_scalar(source.get("url"))
        category = _canonical_movie_category(source.get("category"))
        cached_entry = cache_sources.get(source_id)
        cached_items = (
            cached_entry.get("items")
            if isinstance(cached_entry, dict) and isinstance(cached_entry.get("items"), list)
            else []
        )

        parsed_items: List[Dict[str, Any]] = []
        status = "failed"
        message = ""
        fetched_at = ""

        if not source_url.startswith(("https://", "http://")):
            message = "invalid_source_url"
        else:
            try:
                content = _fetch_remote_text(source_url, timeout_seconds)
                parsed_items = _parse_manual_movies_text(content)
                if parsed_items:
                    status = "fresh"
                    fetched_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                    cache_sources[source_id] = {
                        "name": source_name,
                        "url": source_url,
                        "category": category,
                        "fetched_at": fetched_at,
                        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                        "items": parsed_items,
                    }
                    cache_changed = True
                else:
                    message = "remote_source_empty_or_unparseable"
            except (
                urllib.error.HTTPError,
                urllib.error.URLError,
                TimeoutError,
                OSError,
                UnicodeError,
                ValueError,
            ) as error:
                message = f"{type(error).__name__}: {error}"

        if not parsed_items and use_cache and cached_items:
            parsed_items = [dict(item) for item in cached_items if isinstance(item, dict)]
            status = "cached"
            fetched_at = _clean_scalar(cached_entry.get("fetched_at")) if isinstance(cached_entry, dict) else ""

        for item_position, item in enumerate(parsed_items, start=1):
            item_copy = dict(item)
            item_copy["category"] = category
            item_copy["_manual_origin"] = "remote"
            item_copy["_remote_source_id"] = source_id
            item_copy["_remote_source_name"] = source_name
            item_copy["_remote_source_url"] = source_url
            item_copy["_remote_source_order"] = source_order
            item_copy["_remote_item_position"] = item_position
            item_copy.setdefault("poster_lookup", True)
            item_copy.setdefault("enabled", True)
            output_items.append(item_copy)

        source_report.append(
            {
                "id": source_id,
                "name": source_name,
                "category": category,
                "url": source_url,
                "status": status,
                "item_count": len(parsed_items),
                "last_fetched_at": fetched_at,
                "message": message,
            }
        )

    if cache_changed or not Path(cache_path).exists():
        cache["version"] = 1
        cache["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        _atomic_write_json(cache_path, cache)

    return output_items, {
        "version": 1,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "enabled": True,
        "total_items": len(output_items),
        "sources": source_report,
    }


def _link_years(raw_item: Dict[str, Any]) -> List[int]:
    years: List[int] = []
    raw_links = raw_item.get("links")
    if isinstance(raw_links, list):
        for entry in raw_links:
            url = _source_url(entry)
            year = _parse_year(urllib.parse.unquote(url))
            if year and year not in years:
                years.append(year)
    primary = _clean_scalar(raw_item.get("url") or raw_item.get("link"))
    year = _parse_year(urllib.parse.unquote(primary))
    if year and year not in years:
        years.append(year)
    return years


def _collect_manual_conflicts(raw_items: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    conflicts: List[Dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        name = _display_title(item.get("name") or item.get("title"))
        declared_year = _parse_year(item.get("year"))
        source_years = _link_years(item)
        origin = str(item.get("_manual_origin") or "").casefold()
        if origin == "remote":
            source_name = (
                _clean_scalar(item.get("_remote_source_name"))
                or _clean_scalar(item.get("_remote_source_url"))
                or "remote-manual-source"
            )
        elif origin == "text":
            source_name = "manual/movies.txt"
        else:
            source_name = "manual/movies.json"

        if not declared_year:
            conflicts.append(
                {
                    "movie": name,
                    "category": _canonical_movie_category(item.get("category")),
                    "source": source_name,
                    "type": "missing_year",
                    "message": "Manual year is missing; metadata was not guessed from the URL.",
                    "url_years": source_years,
                }
            )
        elif source_years and any(year != declared_year for year in source_years):
            conflicts.append(
                {
                    "movie": name,
                    "category": _canonical_movie_category(item.get("category")),
                    "source": source_name,
                    "type": "year_mismatch",
                    "declared_year": declared_year,
                    "url_years": source_years,
                    "message": "Trusted manual year was preserved; stream filename year differs.",
                }
            )

        raw_links = item.get("links")
        if isinstance(raw_links, list):
            for link_index, link in enumerate(raw_links, start=1):
                if not isinstance(link, dict):
                    continue
                declared_height = _resolution_height(link.get("resolution") or link.get("label"))
                url_height = _resolution_height(urllib.parse.unquote(_source_url(link)))
                if declared_height and url_height and declared_height != url_height:
                    conflicts.append(
                        {
                            "movie": name,
                            "category": _canonical_movie_category(item.get("category")),
                            "source": source_name,
                            "type": "resolution_mismatch",
                            "link_number": link_index,
                            "declared_height": declared_height,
                            "url_height": url_height,
                            "message": "Trusted manual resolution label was preserved; URL text differs.",
                        }
                    )
    return conflicts


def _write_manual_reports(
    *,
    cards: Iterable[Dict[str, Any]],
    raw_items: Iterable[Dict[str, Any]],
    source_report: Dict[str, Any],
    conflict_report_path: str | Path,
    missing_poster_report_path: str | Path,
    source_report_path: str | Path,
) -> None:
    conflicts = _collect_manual_conflicts(raw_items)
    missing = []
    for card in cards:
        if not isinstance(card, dict) or not card.get("manual_source"):
            continue
        if _valid_poster_url(card.get("logo")):
            continue
        missing.append(
            {
                "id": card.get("id"),
                "name": card.get("name"),
                "year": card.get("year"),
                "category": card.get("category"),
                "source_id": card.get("source_id"),
                "source_name": card.get("source_name"),
            }
        )

    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _atomic_write_json(
        conflict_report_path,
        {"version": 1, "updated_at": now, "count": len(conflicts), "items": conflicts},
    )
    _atomic_write_json(
        missing_poster_report_path,
        {"version": 1, "updated_at": now, "count": len(missing), "items": missing},
    )
    _atomic_write_json(source_report_path, source_report)


def _all_source_objects(movie: Dict[str, Any]) -> List[Dict[str, Any]]:
    sources: List[Dict[str, Any]] = []
    primary = _source_dict(movie, movie)
    if primary.get("url"):
        sources.append(primary)
    backups = movie.get("backups")
    if isinstance(backups, list):
        for backup in backups:
            normalized = _source_dict(backup, movie)
            if normalized.get("url"):
                sources.append(normalized)
    return sources


def _merge_preferred_movie(preferred: Dict[str, Any], secondary: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(preferred)
    combined_sources = _all_source_objects(preferred) + _all_source_objects(secondary)
    seen: set[str] = set()
    deduped: List[Dict[str, Any]] = []
    for source in combined_sources:
        url = _source_url(source)
        if not url or url in seen:
            continue
        seen.add(url)
        deduped.append(source)
        if len(deduped) >= 6:
            break
    if deduped:
        primary = deduped[0]
        merged["url"] = primary["url"]
        for field_name in (
            "headers", "header_profile", "proxy_mode", "stream_type",
            "requires_headers", "inherit_manifest_query", "resolution",
            "resolution_height", "label", "codec", "edition", "language", "provider",
        ):
            if field_name in primary:
                merged[field_name] = primary[field_name]
        merged["backups"] = [dict(source) for source in deduped[1:]]
        merged["available_link_count"] = len(deduped)
    if not _valid_poster_url(merged.get("logo")):
        fallback_poster = _valid_poster_url(secondary.get("logo"))
        if fallback_poster:
            merged["logo"] = fallback_poster
    return merged

def load_manual_movies(
    manual_movies_path: str | Path = DEFAULT_MANUAL_MOVIES_PATH,
    manual_movies_text_path: str | Path = DEFAULT_MANUAL_MOVIES_TEXT_PATH,
    poster_cache_path: str | Path = DEFAULT_POSTER_CACHE_PATH,
    generated_movies_root: str | Path = DEFAULT_GENERATED_MOVIES_ROOT,
    remote_sources_path: str | Path = DEFAULT_REMOTE_SOURCES_PATH,
    remote_cache_path: str | Path = DEFAULT_REMOTE_CACHE_PATH,
    conflict_report_path: str | Path = DEFAULT_CONFLICT_REPORT_PATH,
    missing_poster_report_path: str | Path = DEFAULT_MISSING_POSTER_REPORT_PATH,
    source_report_path: str | Path = "reports/manual-movie-sources.json",
) -> List[Dict[str, Any]]:
    """
    Load trusted manual movies from three layers:
    1. manual/movies.txt (primary owner-managed source);
    2. manual/movies.json (advanced local source);
    3. category-wise remote sources from manual/movie-sources.json.

    Name, year, category and resolution are preserved from the manual source.
    Poster lookup is the only metadata enrichment.
    """
    json_defaults, json_items = _load_manual_json_items(manual_movies_path)
    text_items = _load_manual_text_items(manual_movies_text_path)
    remote_items, remote_report = _remote_source_items(
        remote_sources_path,
        remote_cache_path,
    )

    poster_cache = _load_poster_cache(poster_cache_path)
    generated_posters = _load_generated_poster_map(generated_movies_root)
    cards: List[Dict[str, Any]] = []

    # Lower source tier wins duplicates: text (0), JSON (1), remote (2).
    combined: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    combined.extend((item, {}) for item in text_items)
    combined.extend((item, json_defaults) for item in json_items)
    combined.extend((item, {}) for item in remote_items)

    for position, (raw_item, defaults) in enumerate(combined, start=1):
        if raw_item.get("enabled") is False:
            continue
        card = _manual_movie_card(
            raw_item,
            defaults=defaults,
            manual_position=position,
            poster_cache=poster_cache,
            generated_posters=generated_posters,
        )
        if card:
            cards.append(card)

    deduplicated: List[Dict[str, Any]] = []
    index_by_identity: Dict[str, int] = {}

    for card in cards:
        identity = _movie_identity(card)
        existing_index = index_by_identity.get(identity)
        if existing_index is None:
            index_by_identity[identity] = len(deduplicated)
            deduplicated.append(card)
            continue

        existing = deduplicated[existing_index]
        current_tier = _safe_int(card.get("manual_source_tier"), 9)
        existing_tier = _safe_int(existing.get("manual_source_tier"), 9)
        if current_tier < existing_tier:
            deduplicated[existing_index] = _merge_preferred_movie(card, existing)
        else:
            deduplicated[existing_index] = _merge_preferred_movie(existing, card)

    # Stable source-file order within each source tier.
    counters: Dict[int, int] = {}
    for card in deduplicated:
        tier = _safe_int(card.get("manual_source_tier"), 9)
        counters[tier] = counters.get(tier, 0) + 1
        card["manual_position"] = counters[tier]

    _save_poster_cache(poster_cache_path, poster_cache)
    _write_manual_reports(
        cards=deduplicated,
        raw_items=[combined_item for combined_item, _ in combined],
        source_report=remote_report,
        conflict_report_path=conflict_report_path,
        missing_poster_report_path=missing_poster_report_path,
        source_report_path=source_report_path,
    )
    return deduplicated

def _merge_manual_over_discovered(
    discovered_movies: Iterable[Dict[str, Any]],
    manual_movies: Iterable[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Manual metadata wins; discovered sources may be appended as backups."""
    ordered: List[Dict[str, Any]] = []
    index_by_identity: Dict[str, int] = {}

    for movie in discovered_movies:
        if not isinstance(movie, dict):
            continue
        identity = _movie_identity(movie)
        if identity in index_by_identity:
            continue
        index_by_identity[identity] = len(ordered)
        ordered.append(dict(movie))

    for manual_movie in manual_movies:
        if not isinstance(manual_movie, dict):
            continue
        identity = _movie_identity(manual_movie)
        existing_index = index_by_identity.get(identity)
        if existing_index is None:
            index_by_identity[identity] = len(ordered)
            ordered.append(dict(manual_movie))
        else:
            ordered[existing_index] = _merge_preferred_movie(
                dict(manual_movie),
                ordered[existing_index],
            )
    return ordered

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
    status_counts: Dict[str, int] = {}
    for movie in ordered_movies:
        status = str(movie.get("verification_status") or "unknown").strip() or "unknown"
        status_counts[status] = status_counts.get(status, 0) + 1

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
            "status_counts": status_counts,
            "status_order": list(MOVIE_STATUS_PRIORITY),
            "items": page_items,
        }

    index_payload: Dict[str, Any] = {
        "category": canonical_category,
        "slug": category_slug,
        "count": total_count,
        "page_size": safe_page_size,
        "total_pages": total_pages,
        "status_counts": status_counts,
        "status_order": list(MOVIE_STATUS_PRIORITY),
        "pages": page_entries,
    }

    return {
        "index": index_payload,
        "page_contents": page_contents,
    }


def process_movies(
    bd_results_path: str = "working/bd-results.json",
    settings_path: str = "config/settings.json",
    manual_movies_path: str = DEFAULT_MANUAL_MOVIES_PATH,
    manual_movies_text_path: str = DEFAULT_MANUAL_MOVIES_TEXT_PATH,
    remote_sources_path: str = DEFAULT_REMOTE_SOURCES_PATH,
) -> Dict[str, Dict[str, Any]]:
    candidates = _load_required_results(bd_results_path)
    settings = _load_optional_json(settings_path)
    page_size = _safe_page_size(settings.get("movie_page_size", DEFAULT_PAGE_SIZE))

    movie_candidates = [
        dict(item)
        for item in candidates
        if str(item.get("source_pipeline") or "").strip().lower() == "movies"
    ]

    resolved_candidates = _resolve_category_precedence(movie_candidates)
    discovered_movies = merge_candidates(
        resolved_candidates,
        settings_path=settings_path,
    )
    manual_movies = load_manual_movies(
        manual_movies_path=manual_movies_path,
        manual_movies_text_path=manual_movies_text_path,
        remote_sources_path=remote_sources_path,
    )
    merged_movies = _merge_manual_over_discovered(
        discovered_movies,
        manual_movies,
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
        # Manual primary/backup order is already compatibility-aware and trusted.
        if movie_copy.get("manual_source"):
            movie_copy["available_link_count"] = 1 + len(movie_copy.get("backups") or [])
            movie_copy["browser_support"] = (
                "preferred" if _browser_source_rank(movie_copy) <= 2 else "conditional"
            )
        else:
            movie_copy = _reorder_browser_sources(movie_copy)
        grouped_movies[category].append(movie_copy)

    return {
        category: paginate_movie_list(
            movies=grouped_movies[category],
            category_name=category,
            page_size=page_size,
        )
        for category in VALID_MOVIE_CATEGORIES
    }

