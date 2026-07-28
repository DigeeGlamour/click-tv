"""
Content Type Router

Corrects source-list mistakes before verification. Public IPTV playlists often
mix live channels, direct movie files, series, and events in the same source.
The source's configured pipeline is therefore treated as a hint, not absolute
truth.

The router is deliberately conservative:
- explicit event pipelines always win;
- direct VOD file extensions are routed to movies;
- strong movie/VOD path and group markers can route HLS/DASH VOD to movies;
- normal live manifests remain TV unless strong VOD evidence exists;
- every reroute records the original pipeline and reason for debugging.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Tuple
from urllib.parse import unquote, urlparse


VOD_EXTENSIONS = {
    ".avi",
    ".flv",
    ".m4v",
    ".mkv",
    ".mov",
    ".mp4",
    ".mpeg",
    ".mpg",
    ".webm",
}

LIVE_EXTENSIONS = {
    ".m3u8",
    ".m3u",
    ".mpd",
    ".ts",
}

MOVIE_PATH_MARKERS = (
    "/movie/",
    "/movies/",
    "/film/",
    "/films/",
    "/vod/",
    "/video/movie/",
    "/bollywood/",
    "/hollywood/",
    "/hindidub/",
    "/hindi-dub/",
    "/indianbangla/",
    "/bangla-movie/",
    "/bangla_movies/",
    "/south-indian/",
    "/south_movie/",
    "/series/",
    "/web-series/",
    "/webseries/",
    "/natok/",
    "/telefilm/",
)

MOVIE_GROUP_MARKERS = (
    "movie",
    "movies",
    "film",
    "films",
    "vod",
    "bollywood",
    "hollywood",
    "hindi dubbed",
    "dubbed movie",
    "bangla movie",
    "bengali movie",
    "south indian",
    "web series",
    "web-series",
    "series",
    "natok",
    "telefilm",
)

EVENT_PIPELINES = {"today_match", "upcoming"}


def _clean_text(value: Any) -> str:
    text = unquote(str(value or "")).replace("_", " ").replace("-", " ")
    return " ".join(text.casefold().split())


def _clean_url(value: Any) -> str:
    return str(value or "").split("|", 1)[0].strip()


def _url_extension(url: str) -> str:
    try:
        return Path(urlparse(url).path).suffix.casefold()
    except Exception:
        return ""


def _has_movie_path(url: str) -> bool:
    try:
        path = unquote(urlparse(url).path).casefold()
    except Exception:
        path = unquote(url).casefold()
    normalized = "/" + path.lstrip("/")
    return any(marker in normalized for marker in MOVIE_PATH_MARKERS)


def _has_movie_group_or_name(candidate: Dict[str, Any]) -> bool:
    group = _clean_text(candidate.get("group_title") or candidate.get("category"))
    name = _clean_text(candidate.get("name"))
    combined = f" {group} {name} "

    if any(marker in combined for marker in MOVIE_GROUP_MARKERS):
        return True

    # A title containing a movie year is only supporting evidence. Require an
    # additional VOD/release token so TV channel names containing a year do not
    # get rerouted accidentally.
    has_year = bool(re.search(r"\b(?:19|20)\d{2}\b", combined))
    has_release_token = bool(
        re.search(
            r"\b(?:webrip|web[ .-]?dl|hdrip|brrip|dvdrip|hdtc|dual audio|"
            r"x264|x265|hevc|720p|1080p|2160p)\b",
            combined,
        )
    )
    return has_year and has_release_token


def classify_candidate(candidate: Dict[str, Any]) -> Tuple[str, str]:
    """Return ``(pipeline, reason)`` for one raw/normalized candidate."""
    pipeline = str(candidate.get("source_pipeline") or "tv").strip().casefold()
    explicit_type = str(candidate.get("content_kind") or candidate.get("content_type") or "").strip().casefold()
    force_output = str(candidate.get("force_output") or "").strip().casefold()

    if force_output in EVENT_PIPELINES:
        return force_output, "explicit_event_output"
    if pipeline in EVENT_PIPELINES:
        return pipeline, "configured_event_pipeline"

    if explicit_type in {"movie", "movies", "vod", "film", "series"}:
        return "movies", "explicit_content_type"
    if explicit_type in {"live", "livetv", "tv", "channel"}:
        return "tv", "explicit_content_type"

    url = _clean_url(candidate.get("url"))
    extension = _url_extension(url)
    strong_path = _has_movie_path(url)
    strong_metadata = _has_movie_group_or_name(candidate)

    if extension in VOD_EXTENSIONS:
        return "movies", f"vod_extension:{extension}"

    if strong_path:
        return "movies", "movie_path_marker"

    # HLS/DASH can be either live or VOD. Only reroute it when title/group
    # metadata contains strong movie evidence.
    if extension in LIVE_EXTENSIONS and strong_metadata:
        return "movies", "movie_metadata_on_manifest"

    if pipeline == "movies":
        return "movies", "configured_movie_pipeline"

    # Manual entries keep their established default routing unless explicit
    # markers above prove that they are movies/events.
    if pipeline == "manual":
        return "tv", "manual_default_tv"

    return "tv", "configured_tv_pipeline"


def route_candidate(candidate: Dict[str, Any]) -> Dict[str, Any]:
    """Return a routed copy and preserve transparent routing metadata."""
    item = dict(candidate or {})
    original = str(item.get("source_pipeline") or "tv").strip().casefold()
    routed, reason = classify_candidate(item)

    item["original_source_pipeline"] = original
    item["source_pipeline"] = routed
    item["content_kind"] = (
        "event" if routed in EVENT_PIPELINES else "movie" if routed == "movies" else "live_tv"
    )
    item["routing_reason"] = reason
    item["pipeline_rerouted"] = bool(original != routed)
    return item


def is_vod_candidate(candidate: Dict[str, Any]) -> bool:
    routed, _ = classify_candidate(candidate)
    return routed == "movies"


def is_live_tv_candidate(candidate: Dict[str, Any]) -> bool:
    routed, _ = classify_candidate(candidate)
    return routed == "tv"
