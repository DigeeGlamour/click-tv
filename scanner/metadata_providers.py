"""Multi-provider movie METADATA adapters + fallback orchestration (PART 03).

Separate from scanner/poster_providers.py on purpose: that module only ever
resolves a poster *URL string* and is wired into the existing, unrelated
poster-resolution chain in scanner/movies.py (untouched by this PART). This
module resolves the richer PART 02 schema (tmdb_id, imdb_id, release_date,
genres, rating, rating_source, rating_votes, backdrop) as one normalized
dict, for scanner/movie_metadata_cache.py to cache and apply.

Provider roles (see docs/movie-discovery-audit.md / master plan Section D -
these constraints are load-bearing, not stylistic):
  - TMDB: PRIMARY metadata + trend signal.
  - OMDb: SECONDARY metadata / IMDb id+rating+release fallback. Never a
    Trending source.
  - Cinemeta: public fallback, most useful once an imdb_id is already known.
  - Fanart.tv: artwork (backdrop) fallback ONLY - never contributes rating,
    genres or release_date, and is never a release/trending source.
  - RapidAPI MoviesDatabase: secondary/candidate metadata source. Response
    shape verified live against the real API on 2026-09-11 (see the
    docstrings on moviesdatabase_metadata below) - the previous attempt
    recorded in poster_providers.py hit a 502 on a different (keyword
    search) endpoint; the title-search + ratings + genres endpoints used
    here returned real data.
  - TVMaze: SERIES/TV metadata fallback only - never the primary movie
    provider. The orchestrator only calls it for kind="series".
  - AniList: ANIME metadata + trend signal, scoped to anime content only.
    The orchestrator only calls it for kind="anime". (Confirmed live on
    2026-09-11 that the public AniList API can simply be down - "temporarily
    disabled due to severe stability issues" - so this adapter degrading to
    None on any failure is not a hypothetical, it is the common case today.)

Every adapter function returns a partial dict of schema fields, or None.
Every adapter degrades to None on ANY failure - missing key, network error,
timeout, malformed response - exactly like scanner/poster_providers.py.
Nothing here ever raises, and nothing here ever logs a secret value (no
request URL or header is ever printed).
"""
from __future__ import annotations

import datetime as _dt
import difflib
import os
import re
import urllib.parse
from typing import Any, Dict, List, Optional

try:
    from scanner import movie_genres
    from scanner import provider_health
except ImportError:  # pragma: no cover - direct-module import path
    import movie_genres  # type: ignore
    import provider_health  # type: ignore

#: Honest rating labels (PART 05). The value and the label always travel
#: together: a TMDB user score is never allowed to be presented as IMDb.
RATING_SOURCE_IMDB = "IMDb"
RATING_SOURCE_TMDB = "TMDB"
RATING_SOURCE_TVMAZE = "TVMaze"
RATING_SOURCE_ANILIST = "AniList"

TMDB_SEARCH_MOVIE_URL = "https://api.themoviedb.org/3/search/movie"
TMDB_MOVIE_DETAIL_URL = "https://api.themoviedb.org/3/movie/{id}"
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/w780"

OMDB_URL = "https://www.omdbapi.com/"

CINEMETA_URL = "https://v3-cinemeta.strem.io/meta/{kind}/{imdb_id}.json"

FANART_MOVIE_URL = "https://webservice.fanart.tv/v3/movies/{id}"

MOVIESDATABASE_DEFAULT_HOST = "moviesdatabase.p.rapidapi.com"
MOVIESDATABASE_SEARCH_URL = "https://{host}/titles/search/title/{title}"
MOVIESDATABASE_GENRES_URL = "https://{host}/titles/{id}"
MOVIESDATABASE_RATINGS_URL = "https://{host}/titles/{id}/ratings"

TVMAZE_SEARCH_URL = "https://api.tvmaze.com/singlesearch/shows"

ANILIST_URL = "https://graphql.anilist.co"


# --------------------------------------------------------------------------
# HTTP goes through scanner/provider_health.py (PART 04), which owns the
# pacing, the 429/Retry-After handling, the bounded 2s/5s/15s/30s ladder,
# the no-retry rule for 401/403, and the per-provider counters. These two
# helpers only name the provider the request belongs to.
# --------------------------------------------------------------------------


def _get_json(
    provider: str, url: str, *, headers: Optional[Dict[str, str]] = None
) -> Dict[str, Any]:
    return provider_health.request_json(provider, url, headers=headers)


def _post_json(
    provider: str,
    url: str,
    body: Dict[str, Any],
    *,
    headers: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    return provider_health.request_json(provider, url, headers=headers, body=body)


def _title_similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.strip().casefold(), b.strip().casefold()).ratio()


def _coerce_year(value: Any) -> int:
    try:
        year = int(str(value or "0").strip()[:4])
    except (TypeError, ValueError):
        return 0
    return year if 1888 <= year <= 2100 else 0


def _clean_genres(values: Any) -> List[str]:
    """Provider genres -> canonical spellings (PART 05).

    Five providers spell the same genre five ways; scanner/movie_genres.py
    settles that once, here, before anything is cached or indexed.
    """
    if not isinstance(values, (list, tuple)):
        return []
    return movie_genres.canonical_genres(values)


# --------------------------------------------------------------------------
# TMDB - PRIMARY
# --------------------------------------------------------------------------


def _tmdb_auth_params_and_headers(params: Dict[str, str]) -> Optional[Dict[str, str]]:
    token = os.getenv("TMDB_API_TOKEN", "").strip()
    api_key = os.getenv("TMDB_API_KEY", "").strip()
    if not token and not api_key:
        return None
    headers: Dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    elif api_key:
        params["api_key"] = api_key
    return headers


def tmdb_metadata(title: str, year: int = 0) -> Optional[Dict[str, Any]]:
    """TMDB search + detail. None without a title or an API key."""
    title = str(title or "").strip()
    if not title:
        return None
    params = {"query": title, "include_adult": "true", "language": "en-US", "page": "1"}
    if year:
        params["primary_release_year"] = str(year)
    headers = _tmdb_auth_params_and_headers(params)
    if headers is None:
        return None

    search = _get_json(
        "tmdb", TMDB_SEARCH_MOVIE_URL + "?" + urllib.parse.urlencode(params), headers=headers
    )
    results = search.get("results") if isinstance(search.get("results"), list) else []
    if not results:
        return None

    best: Optional[Dict[str, Any]] = None
    best_score = 0.0
    for candidate in results:
        if not isinstance(candidate, dict):
            continue
        candidate_title = str(candidate.get("title") or candidate.get("name") or "")
        score = _title_similarity(title, candidate_title)
        candidate_year = _coerce_year((candidate.get("release_date") or "")[:4])
        if year and candidate_year:
            score -= min(abs(year - candidate_year) * 0.05, 0.3)
        if score > best_score:
            best_score = score
            best = candidate
    if best is None or best_score < 0.72:
        return None

    tmdb_id = best.get("id")
    detail_params: Dict[str, str] = {"language": "en-US"}
    detail_headers = _tmdb_auth_params_and_headers(detail_params) or {}
    detail = _get_json(
        "tmdb",
        TMDB_MOVIE_DETAIL_URL.format(id=tmdb_id) + "?" + urllib.parse.urlencode(detail_params),
        headers=detail_headers,
    )

    genres = _clean_genres(
        [g.get("name") for g in detail.get("genres", []) if isinstance(g, dict)]
    ) if isinstance(detail.get("genres"), list) else []
    backdrop_path = detail.get("backdrop_path") or best.get("backdrop_path")
    release_date = str(detail.get("release_date") or best.get("release_date") or "").strip()

    result: Dict[str, Any] = {
        "tmdb_id": tmdb_id,
        "imdb_id": str(detail.get("imdb_id") or "").strip() or None,
        "release_date": release_date or None,
        "genres": genres,
        "rating": detail.get("vote_average") or best.get("vote_average"),
        "rating_votes": detail.get("vote_count") or best.get("vote_count"),
        "rating_source": RATING_SOURCE_TMDB,
        "backdrop": (TMDB_IMAGE_BASE + backdrop_path) if backdrop_path else None,
        "metadata_source": "tmdb",
        "metadata_confidence": "high",
    }
    return {key: value for key, value in result.items() if value not in (None, "", [], {})}


# --------------------------------------------------------------------------
# OMDb - SECONDARY (never a Trending source)
# --------------------------------------------------------------------------


def _parse_omdb_date(value: str) -> Optional[str]:
    text = str(value or "").strip()
    if not text or text.upper() == "N/A":
        return None
    try:
        return _dt.datetime.strptime(text, "%d %b %Y").strftime("%Y-%m-%d")
    except ValueError:
        return None


def omdb_metadata(title: str, year: int = 0, imdb_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """OMDb, by imdb_id when known, else title+year. None without OMDB_API_KEY."""
    api_key = os.getenv("OMDB_API_KEY", "").strip()
    if not api_key:
        return None
    params: Dict[str, str] = {"apikey": api_key, "plot": "short"}
    if imdb_id:
        params["i"] = str(imdb_id).strip()
    elif str(title or "").strip():
        params["t"] = str(title).strip()
        if year:
            params["y"] = str(year)
    else:
        return None

    payload = _get_json("omdb", OMDB_URL + "?" + urllib.parse.urlencode(params))
    if str(payload.get("Response") or "").casefold() == "false" or not payload.get("imdbID"):
        return None

    rating_text = str(payload.get("imdbRating") or "").strip()
    rating = float(rating_text) if rating_text and rating_text.upper() != "N/A" else None
    votes_text = str(payload.get("imdbVotes") or "").replace(",", "").strip()
    votes = int(votes_text) if votes_text.isdigit() else None

    result: Dict[str, Any] = {
        "imdb_id": str(payload.get("imdbID") or "").strip() or None,
        "release_date": _parse_omdb_date(payload.get("Released")),
        "genres": _clean_genres(str(payload.get("Genre") or "").split(",")),
        "rating": rating,
        "rating_votes": votes,
        "rating_source": RATING_SOURCE_IMDB if rating is not None else None,
        "metadata_source": "omdb",
        "metadata_confidence": "high" if payload.get("imdbID") else "low",
    }
    return {key: value for key, value in result.items() if value not in (None, "", [], {})}


# --------------------------------------------------------------------------
# Cinemeta - public fallback, best once imdb_id is known
# --------------------------------------------------------------------------


_FULL_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def cinemeta_metadata(imdb_id: Any, kind: str = "movie") -> Optional[Dict[str, Any]]:
    """Cinemeta (Stremio), by an already-known IMDb id. Public, no key."""
    imdb_id_text = str(imdb_id or "").strip()
    if not imdb_id_text.startswith("tt"):
        return None
    normalized_kind = "series" if str(kind or "").casefold() in {"series", "tv", "show"} else "movie"
    payload = _get_json(
        "cinemeta", CINEMETA_URL.format(kind=normalized_kind, imdb_id=imdb_id_text)
    )
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    if not meta:
        return None

    # releaseInfo is usually a bare year ("2010"), sometimes a range for a
    # series ("2010-2015") - never fabricate a day/month from that, only use
    # it when it is already a full YYYY-MM-DD date.
    release_info = str(meta.get("releaseInfo") or "").strip()
    release_date = release_info if _FULL_DATE_RE.match(release_info) else None

    rating_text = str(meta.get("imdbRating") or "").strip()
    try:
        rating = float(rating_text) if rating_text else None
    except ValueError:
        rating = None

    result: Dict[str, Any] = {
        "genres": _clean_genres(meta.get("genres")),
        "release_date": release_date,
        "rating": rating,
        "rating_source": RATING_SOURCE_IMDB if rating is not None else None,
        "backdrop": str(meta.get("background") or "").strip() or None,
        "metadata_source": "cinemeta",
        "metadata_confidence": "medium",
    }
    return {key: value for key, value in result.items() if value not in (None, "", [], {})}


# --------------------------------------------------------------------------
# Fanart.tv - ARTWORK FALLBACK ONLY (never rating/genre/release/trending)
# --------------------------------------------------------------------------


def fanart_metadata(tmdb_id: Any) -> Optional[Dict[str, Any]]:
    """Fanart.tv backdrop only. None without FANART_API_KEY or a numeric tmdb_id."""
    api_key = os.getenv("FANART_API_KEY", "").strip()
    tmdb_id_text = str(tmdb_id or "").strip()
    if not api_key or not tmdb_id_text.isdigit():
        return None
    url = FANART_MOVIE_URL.format(id=tmdb_id_text) + "?" + urllib.parse.urlencode({"api_key": api_key})
    payload = _get_json("fanart", url)
    backgrounds = payload.get("moviebackground")
    if not isinstance(backgrounds, list) or not backgrounds:
        return None
    first = backgrounds[0]
    backdrop = str(first.get("url") or "").strip() if isinstance(first, dict) else ""
    if not backdrop:
        return None
    return {"backdrop": backdrop, "metadata_source": "fanart"}


# --------------------------------------------------------------------------
# RapidAPI MoviesDatabase - secondary/candidate metadata source.
#
# Response shape verified live on 2026-09-11 against the real API (not a
# skeleton guess):
#   search: GET /titles/search/title/{title}?exact=false&titleType=movie
#     -> {"results": [{"id": "tt1375666", "titleText": {"text": "Inception"},
#          "releaseYear": {"year": 2010}, "releaseDate": {"day":16,"month":7,
#          "year":2010}}, ...]}
#   genres: GET /titles/{imdb_id}?info=genres
#     -> {"results": {"genres": {"genres": [{"text": "Action"}, ...]}}}
#   ratings: GET /titles/{imdb_id}/ratings
#     -> {"results": {"averageRating": 8.8, "numVotes": 2866512}}
# The keyword-search endpoint (a different endpoint than the one used here)
# was the one scanner/poster_providers.py found returning HTTP 502 in an
# earlier attempt; title-search is a separate, working endpoint.
# --------------------------------------------------------------------------


def _moviesdatabase_headers() -> Optional[Dict[str, str]]:
    api_key = os.getenv("RAPIDAPI_KEY", "").strip()
    if not api_key:
        return None
    host = os.getenv("RAPIDAPI_MOVIES_HOST", "").strip() or MOVIESDATABASE_DEFAULT_HOST
    return {"x-rapidapi-key": api_key, "x-rapidapi-host": host}


def moviesdatabase_metadata(title: str, year: int = 0) -> Optional[Dict[str, Any]]:
    """RapidAPI MoviesDatabase: title search -> genres + ratings by imdb id.

    None without RAPIDAPI_KEY. Up to 3 bounded requests, only the first of
    which happens without a confirmed match.
    """
    title = str(title or "").strip()
    if not title:
        return None
    headers = _moviesdatabase_headers()
    if headers is None:
        return None
    host = headers["x-rapidapi-host"]

    search_url = (
        MOVIESDATABASE_SEARCH_URL.format(host=host, title=urllib.parse.quote(title))
        + "?"
        + urllib.parse.urlencode({"exact": "false", "titleType": "movie"})
    )
    search = _get_json("moviesdatabase", search_url, headers=headers)
    results = search.get("results") if isinstance(search.get("results"), list) else []
    if not results:
        return None

    best: Optional[Dict[str, Any]] = None
    best_score = 0.0
    for candidate in results:
        if not isinstance(candidate, dict):
            continue
        candidate_title = str((candidate.get("titleText") or {}).get("text") or "")
        score = _title_similarity(title, candidate_title)
        candidate_year = _coerce_year((candidate.get("releaseYear") or {}).get("year"))
        if year and candidate_year:
            score -= min(abs(year - candidate_year) * 0.05, 0.3)
        if score > best_score:
            best_score = score
            best = candidate
    if best is None or best_score < 0.72:
        return None

    imdb_id = str(best.get("id") or "").strip()
    if not imdb_id.startswith("tt"):
        return None

    release_date_parts = best.get("releaseDate") if isinstance(best.get("releaseDate"), dict) else {}
    release_date = None
    if release_date_parts.get("year") and release_date_parts.get("month") and release_date_parts.get("day"):
        try:
            release_date = "{:04d}-{:02d}-{:02d}".format(
                int(release_date_parts["year"]), int(release_date_parts["month"]), int(release_date_parts["day"])
            )
        except (TypeError, ValueError):
            release_date = None

    genres: List[str] = []
    genres_payload = _get_json(
        "moviesdatabase",
        MOVIESDATABASE_GENRES_URL.format(host=host, id=imdb_id) + "?" + urllib.parse.urlencode({"info": "genres"}),
        headers=headers,
    )
    genres_block = ((genres_payload.get("results") or {}).get("genres") or {}).get("genres")
    if isinstance(genres_block, list):
        genres = _clean_genres([g.get("text") for g in genres_block if isinstance(g, dict)])

    rating = None
    rating_votes = None
    ratings_payload = _get_json(
        "moviesdatabase", MOVIESDATABASE_RATINGS_URL.format(host=host, id=imdb_id), headers=headers
    )
    ratings_result = ratings_payload.get("results") if isinstance(ratings_payload.get("results"), dict) else {}
    if isinstance(ratings_result.get("averageRating"), (int, float)):
        rating = float(ratings_result["averageRating"])
    if isinstance(ratings_result.get("numVotes"), (int, float)):
        rating_votes = int(ratings_result["numVotes"])

    result: Dict[str, Any] = {
        "imdb_id": imdb_id,
        "release_date": release_date,
        "genres": genres,
        "rating": rating,
        "rating_votes": rating_votes,
        "rating_source": RATING_SOURCE_IMDB if rating is not None else None,
        "metadata_source": "moviesdatabase",
        "metadata_confidence": "medium",
    }
    return {key: value for key, value in result.items() if value not in (None, "", [], {})}


# --------------------------------------------------------------------------
# TVMaze - SERIES/TV fallback only (never the primary movie provider)
# --------------------------------------------------------------------------


def tvmaze_metadata(title: str) -> Optional[Dict[str, Any]]:
    """TVMaze, by title. Public, no key. Series/TV only - callers must gate this."""
    title = str(title or "").strip()
    if not title:
        return None
    payload = _get_json(
        "tvmaze", TVMAZE_SEARCH_URL + "?" + urllib.parse.urlencode({"q": title})
    )
    if not payload:
        return None

    rating_block = payload.get("rating") if isinstance(payload.get("rating"), dict) else {}
    image_block = payload.get("image") if isinstance(payload.get("image"), dict) else {}
    premiered = str(payload.get("premiered") or "").strip()

    result: Dict[str, Any] = {
        "genres": _clean_genres(payload.get("genres")),
        "release_date": premiered if _FULL_DATE_RE.match(premiered) else None,
        "rating": rating_block.get("average"),
        "rating_source": RATING_SOURCE_TVMAZE if rating_block.get("average") is not None else None,
        "backdrop": str(image_block.get("original") or "").strip() or None,
        "metadata_source": "tvmaze",
        "metadata_confidence": "medium",
    }
    return {key: value for key, value in result.items() if value not in (None, "", [], {})}


# --------------------------------------------------------------------------
# AniList - ANIME scope only (never used for general Bangla/Hindi/English
# movie metadata/trending)
# --------------------------------------------------------------------------


def anilist_metadata(title: str) -> Optional[Dict[str, Any]]:
    """AniList, by title. Public, no key, anime-only. Callers must gate this.

    Confirmed live (2026-09-11) that this public API is sometimes simply
    disabled ("temporarily disabled due to severe stability issues") -
    degrading to None here is the expected common case, not a bug path.
    """
    title = str(title or "").strip()
    if not title:
        return None
    query = (
        "query ($search: String) { Media(search: $search, type: ANIME) { "
        "genres startDate { year month day } averageScore bannerImage "
        "coverImage { extraLarge large } } }"
    )
    payload = _post_json(
        "anilist", ANILIST_URL, {"query": query, "variables": {"search": title}}
    )
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    media = data.get("Media") if isinstance(data.get("Media"), dict) else {}
    if not media:
        return None

    start = media.get("startDate") if isinstance(media.get("startDate"), dict) else {}
    release_date = None
    if start.get("year") and start.get("month") and start.get("day"):
        try:
            release_date = "{:04d}-{:02d}-{:02d}".format(
                int(start["year"]), int(start["month"]), int(start["day"])
            )
        except (TypeError, ValueError):
            release_date = None

    average_score = media.get("averageScore")
    rating = round(average_score / 10.0, 1) if isinstance(average_score, (int, float)) else None

    result: Dict[str, Any] = {
        "genres": _clean_genres(media.get("genres")),
        "release_date": release_date,
        "rating": rating,
        "rating_source": RATING_SOURCE_ANILIST if rating is not None else None,
        "backdrop": str(media.get("bannerImage") or "").strip() or None,
        "metadata_source": "anilist",
        "metadata_confidence": "medium",
    }
    return {key: value for key, value in result.items() if value not in (None, "", [], {})}


# --------------------------------------------------------------------------
# Orchestrator - fallback order + merge rules (PART 03's actual deliverable)
# --------------------------------------------------------------------------


_REQUIRED_FOR_COMPLETE = ("genres", "rating", "release_date")


def _complete_enough(result: Dict[str, Any], has_backdrop: bool = False) -> bool:
    """Enough to stop asking further providers: an external id, the three
    text fields, and artwork from somewhere."""
    has_identity = bool(result.get("tmdb_id") or result.get("imdb_id"))
    return (
        has_identity
        and has_backdrop
        and all(result.get(field) for field in _REQUIRED_FOR_COMPLETE)
    )


def _safe_call(func, *args) -> Optional[Dict[str, Any]]:
    try:
        return func(*args)
    except Exception:  # noqa: BLE001 - a provider bug must never break a scan
        return None


#: Backdrop priority (PART 05 section K). A previously cached backdrop
#: outranks all of these and never reaches here - the cache layer's
#: fill-only merge keeps it. The poster-derived last resort is applied on
#: the movie only, never cached, so a real backdrop can still arrive later.
BACKDROP_PRIORITY = ("tmdb", "fanart", "cinemeta", "tvmaze", "anilist", "moviesdatabase")


def _pick_backdrop(candidates: Dict[str, str]) -> str:
    for source in BACKDROP_PRIORITY:
        if candidates.get(source):
            return source
    return ""


def resolve_metadata(movie: Dict[str, Any], *, kind: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Resolve real metadata for one movie via the provider fallback chain.

    Returns a merged, normalized dict of PART 02 schema fields, or None if
    nothing could be resolved at all. Never raises. Fill-only merge: once a
    higher-priority provider sets a field, a later provider cannot change
    it - only fill genuinely missing fields. `metadata_source` records the
    first (highest-priority) provider that contributed anything.

    `kind` defaults to "movie"; callers pass "series"/"anime" to widen the
    chain to TVMaze/AniList for that specific content. Neither is ever
    consulted for plain movie content, per the provider-role constraints in
    this module's docstring.
    """
    if not isinstance(movie, dict):
        return None
    resolved_kind = str(kind or movie.get("content_kind") or "movie").casefold()
    if resolved_kind not in ("movie", "series", "anime"):
        resolved_kind = "movie"

    title = str(movie.get("name") or movie.get("title") or "").strip()
    if not title:
        return None
    year = _coerce_year(movie.get("year"))
    imdb_id = str(movie.get("imdb_id") or "").strip() or None
    tmdb_id = movie.get("tmdb_id")

    result: Dict[str, Any] = {}
    sources: List[str] = []
    # Backdrop is resolved by its own priority (PART 05 section K), not by
    # whichever provider happened to answer first: TMDB, then Fanart.tv,
    # then Cinemeta. So each provider's artwork is held aside as a
    # candidate rather than merged into `backdrop` directly.
    backdrop_candidates: Dict[str, str] = {}

    def merge(data: Optional[Dict[str, Any]], source: str) -> None:
        if not data:
            return
        changed = False
        for key, value in data.items():
            if key in ("metadata_source", "metadata_confidence"):
                continue
            if value in (None, "", [], {}):
                continue
            if key == "backdrop":
                backdrop_candidates.setdefault(source, str(value))
                changed = True
                continue
            if result.get(key) in (None, "", [], {}):
                result[key] = value
                changed = True
        if changed:
            sources.append(source)
            result.setdefault("metadata_confidence", data.get("metadata_confidence"))

    # 1. TMDB - primary.
    tmdb_data = _safe_call(tmdb_metadata, title, year)
    merge(tmdb_data, "tmdb")
    if tmdb_data:
        tmdb_id = tmdb_data.get("tmdb_id") or tmdb_id
        imdb_id = tmdb_data.get("imdb_id") or imdb_id

    # 2. OMDb - secondary metadata / imdb id+rating+release fallback.
    if not _complete_enough(result, bool(backdrop_candidates)):
        omdb_data = _safe_call(omdb_metadata, title, year, imdb_id)
        merge(omdb_data, "omdb")
        if omdb_data:
            imdb_id = omdb_data.get("imdb_id") or imdb_id

    # 3. Cinemeta - needs an imdb_id to be useful.
    if imdb_id and not _complete_enough(result, bool(backdrop_candidates)):
        merge(_safe_call(cinemeta_metadata, imdb_id, resolved_kind), "cinemeta")

    # 4. RapidAPI MoviesDatabase - secondary/candidate metadata source.
    if not _complete_enough(result, bool(backdrop_candidates)):
        moviesdatabase_data = _safe_call(moviesdatabase_metadata, title, year)
        merge(moviesdatabase_data, "moviesdatabase")
        if moviesdatabase_data:
            imdb_id = moviesdatabase_data.get("imdb_id") or imdb_id

    # 5. Fanart.tv - artwork fallback only, needs a tmdb_id. Skipped when
    #    TMDB already supplied artwork, since TMDB outranks it anyway.
    if tmdb_id and not backdrop_candidates.get("tmdb"):
        merge(_safe_call(fanart_metadata, tmdb_id), "fanart")

    # 6. TVMaze - series/TV only.
    if resolved_kind == "series" and not _complete_enough(result, bool(backdrop_candidates)):
        merge(_safe_call(tvmaze_metadata, title), "tvmaze")

    # 7. AniList - anime only.
    if resolved_kind == "anime":
        merge(_safe_call(anilist_metadata, title), "anilist")

    backdrop_source = _pick_backdrop(backdrop_candidates)
    if backdrop_source:
        result["backdrop"] = backdrop_candidates[backdrop_source]
        result["backdrop_source"] = backdrop_source

    if not result:
        return None

    result["metadata_source"] = sources[0] if sources else None
    if len(sources) > 1:
        result["metadata_sources"] = sources
    result.setdefault(
        "metadata_confidence", "high" if (result.get("tmdb_id") or result.get("imdb_id")) else "low"
    )
    result["metadata_updated_at"] = _dt.datetime.now(_dt.timezone.utc).isoformat()
    return {key: value for key, value in result.items() if value not in (None, "", [], {})}
