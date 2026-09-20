"""Supplementary movie/TV artwork, tried only after TMDB has nothing.

TMDB stays the primary, authoritative lookup (scanner/movies.py) - it is the
one source here that validates title *and* year together before trusting a
match. Every provider below is a fallback for when TMDB simply does not have
the title at all, so each is used more permissively: a title-only match, no
year cross-check. That is an accepted trade for "some poster" over "no
poster" on a fallback path, not something the primary lookup would allow.

Every provider degrades to "" on any failure - a missing API key, a network
error, a provider outage - exactly like the TMDB lookup it follows. A poster
enrichment failure must never remove or break the item it was decorating.

Real, live-tested findings this was built against:
  - RapidAPI's MoviesDatabase endpoint returned HTTP 502 with
    "API (not working)" on every request tried - a provider-side outage, not
    a key problem. Not integrated; nothing here depends on it.
  - Cloudflare in front of Highlightly's own sports API rejects a bare/
    non-browser User-Agent outright (its own error code 1010) - the browser-
    like default this module's requests carry is required, not decorative.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

REQUEST_TIMEOUT_SECONDS = 10
_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

OMDB_URL = "https://www.omdbapi.com/"
FANART_MOVIE_URL = "https://webservice.fanart.tv/v3/movies/{id}"
TVMAZE_SEARCH_URL = "https://api.tvmaze.com/singlesearch/shows"
CINEMETA_URL = "https://v3-cinemeta.strem.io/meta/{kind}/{imdb_id}.json"
ANILIST_URL = "https://graphql.anilist.co"


def _get_json(url: str, *, headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    request_headers = {"Accept": "application/json", "User-Agent": _USER_AGENT}
    request_headers.update(headers or {})
    request = urllib.request.Request(url, headers=request_headers)
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
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


def _post_json(url: str, body: Dict[str, Any], *, headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    request_headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": _USER_AGENT,
    }
    request_headers.update(headers or {})
    request = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), headers=request_headers, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
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


def omdb_poster_lookup(title: str, year: int = 0) -> str:
    """OMDb, by title (and year when known). Empty without OMDB_API_KEY."""
    api_key = os.getenv("OMDB_API_KEY", "").strip()
    if not api_key or not str(title or "").strip():
        return ""
    params = {"t": str(title).strip(), "apikey": api_key}
    if year:
        params["y"] = str(year)
    payload = _get_json(OMDB_URL + "?" + urllib.parse.urlencode(params))
    poster = str(payload.get("Poster") or "").strip()
    return poster if poster and poster.upper() != "N/A" else ""


def tvmaze_poster_lookup(title: str) -> str:
    """TVMaze, by title - public, no key. Aimed at TV shows/drama series,
    not literal broadcast-channel branding, which TVMaze does not catalogue."""
    if not str(title or "").strip():
        return ""
    params = {"q": str(title).strip()}
    payload = _get_json(TVMAZE_SEARCH_URL + "?" + urllib.parse.urlencode(params))
    image = payload.get("image") if isinstance(payload.get("image"), dict) else {}
    poster = str(image.get("original") or image.get("medium") or "").strip()
    return poster


def fanart_movie_poster_lookup(tmdb_id: Any) -> str:
    """Fanart.tv, by an already-known TMDB id - an enhancement over TMDB's
    own poster, not a title search; Fanart.tv has no search-by-title
    endpoint at all. Empty without FANART_API_KEY or a usable id."""
    api_key = os.getenv("FANART_API_KEY", "").strip()
    tmdb_id_text = str(tmdb_id or "").strip()
    if not api_key or not tmdb_id_text.isdigit():
        return ""
    url = FANART_MOVIE_URL.format(id=tmdb_id_text) + "?" + urllib.parse.urlencode({"api_key": api_key})
    payload = _get_json(url)
    posters = payload.get("movieposter")
    if isinstance(posters, list) and posters:
        first = posters[0]
        if isinstance(first, dict):
            return str(first.get("url") or "").strip()
    return ""


def cinemeta_poster_lookup(imdb_id: Any, media_kind: str = "movie") -> str:
    """Cinemeta (Stremio), by an already-known IMDb id - public, no key,
    but id-based like Fanart.tv, not title-searchable."""
    imdb_id_text = str(imdb_id or "").strip()
    if not imdb_id_text.startswith("tt"):
        return ""
    kind = "series" if str(media_kind or "").strip().casefold() in {"series", "tv", "show"} else "movie"
    payload = _get_json(CINEMETA_URL.format(kind=kind, imdb_id=imdb_id_text))
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    return str(meta.get("poster") or "").strip()


def anilist_poster_lookup(title: str) -> str:
    """AniList, by title - public, no key, anime-specific. Kept as a last
    resort: a title-only match against an anime-only catalogue can
    coincidentally hit a same-named non-anime title, which is an acceptable
    risk this far down the fallback chain but not earlier in it."""
    if not str(title or "").strip():
        return ""
    query = (
        "query ($search: String) { Media(search: $search, type: ANIME) { "
        "coverImage { extraLarge large } } }"
    )
    payload = _post_json(ANILIST_URL, {"query": query, "variables": {"search": str(title).strip()}})
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    media = data.get("Media") if isinstance(data.get("Media"), dict) else {}
    cover = media.get("coverImage") if isinstance(media.get("coverImage"), dict) else {}
    return str(cover.get("extraLarge") or cover.get("large") or "").strip()


#: Which function answers for which provider name. The names are the ones
#: `provider_router` and `provider_health` already use, so artwork lookups are
#: counted against the same quota and the same breaker as metadata lookups to
#: the same provider - two ledgers for one API key is how a budget gets spent
#: twice.
_POSTER_LOOKUPS = {
    "fanart": lambda ctx: fanart_movie_poster_lookup(ctx["tmdb_id"]),
    "cinemeta": lambda ctx: cinemeta_poster_lookup(
        ctx["imdb_id"], ctx["media_kind"]),
    "omdb": lambda ctx: omdb_poster_lookup(ctx["title"], ctx["year"]),
    "tvmaze": lambda ctx: tvmaze_poster_lookup(ctx["title"]),
    "anilist": lambda ctx: anilist_poster_lookup(ctx["title"]),
}


#: The order this module used before ধাপ ৬. Kept as the tail of the routed
#: order so nothing that used to be asked stops being asked.
_FIXED_CHAIN = ("fanart", "cinemeta", "omdb", "tvmaze", "anilist")

#: Providers that answer from an exact external id rather than from a title.
_ID_KEYED = frozenset({"fanart", "cinemeta"})


def _is_available(provider: str) -> bool:
    """Health and quota only - capability is the router's separate question."""
    try:
        from scanner import provider_router

        return provider_router.available(provider)
    except Exception:  # noqa: BLE001 - never let a health check cost artwork
        return True


def supplementary_poster_lookup(
    title: str,
    year: int = 0,
    *,
    tmdb_id: Any = "",
    imdb_id: Any = "",
    media_kind: str = "movie",
) -> str:
    """ধাপ ৬'s Artwork Router - first non-empty result wins, order not fixed.

    The order used to be written here: Fanart, Cinemeta, OMDb, TVMaze, AniList.
    ধারা ৪.৭ is explicit that a fixed chain is a description and not an
    instruction, and ধাপ ৬ asks for an "Artwork Router (কোটা/হেলথ/capability
    অনুযায়ী ... স্থির ক্রম নয়)". So the order now comes from `provider_router`,
    which excludes a provider that is cooling down, tripped or out of budget
    instead of walking into it, and which already knows that Fanart needs a
    `tmdb_id` and Cinemeta an `imdb_id`.

    Falls back to the original fixed order if the router cannot be consulted -
    a routing failure should cost the ordering, not the artwork.
    """
    context = {
        "title": title, "year": year, "tmdb_id": tmdb_id,
        "imdb_id": imdb_id, "media_kind": media_kind,
    }
    kind = "series" if str(media_kind or "").casefold() in (
        "series", "tv", "show") else "movie"

    try:
        from scanner import provider_router

        order = provider_router.order(
            capability=provider_router.CAPABILITY_ARTWORK,
            kind=kind,
            have={"tmdb_id": tmdb_id, "imdb_id": imdb_id, "title": title},
        )
    except Exception:  # noqa: BLE001 - routing must never cost the artwork
        order = []

    # The router decides the ORDER and skips a provider that is down or out of
    # budget. It does not get to remove one the fixed chain would have tried:
    # its `kinds` filter is about where a provider is *likely* to help - AniList
    # for anime, TVMaze for television - and a long shot that used to be taken
    # and now is not is a regression dressed up as routing. So anything the
    # router did not rank is appended in the original order, minus whatever is
    # currently unavailable.
    routed = [name for name in order if name in _POSTER_LOOKUPS]
    providers = list(routed)
    for name in _FIXED_CHAIN:
        if name in providers:
            continue
        if not _is_available(name):
            continue
        providers.append(name)

    # An id-keyed provider always goes before a title-keyed one, whatever the
    # router's weights say. This is not a preference, it is about what kind of
    # answer each gives: Fanart asked for tmdb_id 27205 returns art for that
    # exact film, while OMDb asked for "Inception" returns art for whatever it
    # thinks that title means - and a title match against an anime-only
    # catalogue can land on an unrelated same-named title. A stable partition,
    # so the routed order still decides everything within each group.
    providers.sort(key=lambda name: name not in _ID_KEYED)

    for name in providers:
        lookup = _POSTER_LOOKUPS.get(name)
        if lookup is None:
            continue
        try:
            poster = lookup(context)
        except Exception:  # pragma: no cover - a provider must never break a scan
            poster = ""
        if poster:
            return poster
    return ""
