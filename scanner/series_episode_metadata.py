"""ধাপ ১২ / A-05 - real episode names, air dates and stills.

    এখন এপিসোড কার্ডে শুধু "Episode 01 / HD 1080P"।
    ★ শর্ত: সোর্সের season/episode পরিচয় কখনো overwrite করা যাবে না।

Measured on the published catalogue: every one of the 316 episode records
carries `episode_title: "Episode 01"` and nothing else - no name, no air date,
no still.

**The starred condition is the design.** A provider is asked what episode 3 of
season 2 is called; it is never asked *which* episode this file is. The source
already answered that, from the filename, and it is right about this file in a
way a title-matched provider cannot be. So this module writes three new fields
and is structurally incapable of touching the identity:

    episode_name   air_date   still        <- may be written
    season_number  episode_number          <- never, by name check
    episode_key    episode_label
    episode_title  title  number  id

`PROTECTED` is checked on the way in, not documented and hoped for: a provider
answer containing any of those keys has them dropped before the merge.

**One call per show, not per episode.** ধারা ৫ সংশোধন-২ already established
that the API is needed at show level only - 350 of 350 candidates carry an
explicit SxxExx, so no lookup is needed to know which episode a file is. With
70 shows, this whole feature is 70 requests, cached, not 316.

Providers are asked in the order `provider_router` gives for
`series_metadata` - by capability, quota and health, never a fixed primary.
TVMaze answers a show and all its episodes in a single request (`embed`),
which is what A-05 means by "সমর্থন করলে একই কলে".
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from scanner import paths

#: What this module may add to an episode record.
EPISODE_FIELDS: Tuple[str, ...] = ("episode_name", "air_date", "still")

#: What it may never write, whatever a provider sends. The source's own
#: season/episode identity comes from the filename and is authoritative.
PROTECTED: Tuple[str, ...] = (
    "season_number",
    "episode_number",
    "episode_key",
    "episode_label",
    "episode_title",
    "title",
    "name",
    "number",
    "id",
    "series_id",
    "series_name",
    "url",
    "backups",
)

DEFAULT_PATH = os.path.join(paths.state_root(), "series-episode-metadata.json")

#: Re-ask about a show this often. An ongoing show gains episodes; a finished
#: one does not, so `status` shortens it rather than lengthening it.
REFRESH_TTL_DAYS_ONGOING = 3
REFRESH_TTL_DAYS_SETTLED = 30

#: Shows looked up per run. 70 shows at one request each is small, but a cold
#: start should still not become the largest thing in a scan.
DEFAULT_SHOW_BUDGET = 20

#: Do not re-ask about a show nothing could resolve for this long.
RETRY_AFTER_FAILURE_DAYS = 7

SCHEMA_VERSION = 1

_FULL_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _now(now: Optional[_dt.datetime] = None) -> _dt.datetime:
    return now or _dt.datetime.now(_dt.timezone.utc)


def _parse_stamp(value: Any) -> Optional[_dt.datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = _dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    return parsed


def load(path: Optional[str] = None) -> Dict[str, Any]:
    target = path or DEFAULT_PATH
    try:
        with open(target, "r", encoding="utf-8") as handle:
            loaded = json.load(handle)
    except (OSError, ValueError):
        loaded = None
    if not isinstance(loaded, dict):
        loaded = {}
    loaded.setdefault("schema_version", SCHEMA_VERSION)
    shows = loaded.get("shows")
    loaded["shows"] = shows if isinstance(shows, dict) else {}
    return loaded


def save(store: Dict[str, Any], path: Optional[str] = None) -> bool:
    target = path or DEFAULT_PATH
    try:
        os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
        temporary = f"{target}.tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(store, handle, ensure_ascii=False, indent=2)
        os.replace(temporary, target)
        return True
    except OSError:
        return False


def show_key(series: Dict[str, Any]) -> str:
    """The year-free identity ধারা ৪.৬ settled on, reused rather than redefined.

    A show is one show across its seasons and across the years its seasons
    were released - which is the whole reason series identity dropped the year
    in ধাপ ৩ক, and two spellings of it would split a show in half again.
    """
    from scanner import series_signal

    name = str((series or {}).get("name") or (series or {}).get("series_name") or "")
    return series_signal.show_key(name)


def episode_ref(season: Any, number: Any) -> str:
    """`S02E07`, the key both sides agree on. Built from the SOURCE's numbers."""
    try:
        season_value = int(season)
        number_value = int(number)
    except (TypeError, ValueError):
        return ""
    if season_value < 0 or number_value <= 0:
        return ""
    return f"S{season_value:02d}E{number_value:02d}"


def sanitise(fields: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Keep only what this module is allowed to add.

    The starred condition, enforced rather than trusted: a provider answer is
    filtered to `EPISODE_FIELDS` on the way in, so nothing downstream has to
    remember not to write an identity field.
    """
    if not isinstance(fields, dict):
        return {}
    clean: Dict[str, Any] = {}
    for field in EPISODE_FIELDS:
        value = fields.get(field)
        if value in (None, "", [], {}):
            continue
        if field == "air_date" and not _FULL_DATE.match(str(value).strip()):
            continue
        clean[field] = value
    return clean


def refresh_ttl_days(record: Dict[str, Any]) -> int:
    status = str((record or {}).get("status") or "").strip().lower()
    if status in ("ended", "completed", "finished", "settled"):
        return REFRESH_TTL_DAYS_SETTLED
    return REFRESH_TTL_DAYS_ONGOING


def is_due(record: Optional[Dict[str, Any]], now: Optional[_dt.datetime] = None) -> bool:
    """Should this show be asked about?"""
    reference = _now(now)
    if not isinstance(record, dict):
        return True
    attempted = _parse_stamp(record.get("last_attempt_at"))
    if not record.get("episodes") and attempted is not None:
        return (reference - attempted) > _dt.timedelta(days=RETRY_AFTER_FAILURE_DAYS)
    updated = _parse_stamp(record.get("updated_at"))
    if updated is None:
        return True
    return (reference - updated) > _dt.timedelta(days=refresh_ttl_days(record))


# --------------------------------------------------------------------------
# Provider adapters. Each returns {"S02E07": {episode_name, air_date, still}}
# for a whole show, or None. None means "this provider could not", never
# "this show has no episodes".
# --------------------------------------------------------------------------

def tvmaze_episodes(series: Dict[str, Any]) -> Optional[Dict[str, Dict[str, Any]]]:
    """One request for the show and every episode it has (`embed=episodes`)."""
    import urllib.parse

    from scanner import metadata_providers

    title = str((series or {}).get("name") or "").strip()
    if not title:
        return None
    url = (
        "https://api.tvmaze.com/singlesearch/shows?"
        + urllib.parse.urlencode({"q": title, "embed": "episodes"})
    )
    payload = metadata_providers._get_json("tvmaze", url)
    if not isinstance(payload, dict):
        return None
    embedded = payload.get("_embedded")
    episodes = embedded.get("episodes") if isinstance(embedded, dict) else None
    if not isinstance(episodes, list):
        return None

    found: Dict[str, Dict[str, Any]] = {}
    for episode in episodes:
        if not isinstance(episode, dict):
            continue
        ref = episode_ref(episode.get("season"), episode.get("number"))
        if not ref:
            continue
        image = episode.get("image") if isinstance(episode.get("image"), dict) else {}
        found[ref] = sanitise({
            "episode_name": str(episode.get("name") or "").strip() or None,
            "air_date": str(episode.get("airdate") or "").strip() or None,
            "still": str(image.get("original") or image.get("medium") or "").strip()
            or None,
        })
    return {ref: fields for ref, fields in found.items() if fields} or None


def cinemeta_episodes(series: Dict[str, Any]) -> Optional[Dict[str, Dict[str, Any]]]:
    """Cinemeta lists a series' videos in its one meta response."""
    from scanner import metadata_providers

    imdb_id = str((series or {}).get("imdb_id") or "").strip()
    if not imdb_id.startswith("tt"):
        return None
    payload = metadata_providers._get_json(
        "cinemeta",
        f"https://v3-cinemeta.strem.io/meta/series/{imdb_id}.json",
    )
    meta = payload.get("meta") if isinstance(payload, dict) else None
    videos = meta.get("videos") if isinstance(meta, dict) else None
    if not isinstance(videos, list):
        return None

    found: Dict[str, Dict[str, Any]] = {}
    for video in videos:
        if not isinstance(video, dict):
            continue
        ref = episode_ref(video.get("season"), video.get("episode") or video.get("number"))
        if not ref:
            continue
        released = str(video.get("released") or video.get("firstAired") or "")[:10]
        found[ref] = sanitise({
            "episode_name": str(video.get("name") or video.get("title") or "").strip()
            or None,
            "air_date": released or None,
            "still": str(video.get("thumbnail") or "").strip() or None,
        })
    return {ref: fields for ref, fields in found.items() if fields} or None


def tmdb_episodes(series: Dict[str, Any]) -> Optional[Dict[str, Dict[str, Any]]]:
    """TMDB TV: find the show, then one call per season we actually publish.

    Seasons we do not have are never requested - the cost of this provider is
    a function of what the site carries, not of what TMDB knows.
    """
    import urllib.parse

    from scanner import metadata_providers

    title = str((series or {}).get("name") or "").strip()
    seasons = sorted({
        int(number) for number in (series or {}).get("season_numbers") or ()
        if str(number).strip().isdigit()
    })
    if not title or not seasons:
        return None

    # The same auth the movie adapters use - bearer token or api_key, whichever
    # this deployment has. Reused rather than re-derived: two spellings of how
    # to authenticate is how one of them silently stops working.
    params = {"query": title, "language": "en-US", "page": "1"}
    headers = metadata_providers._tmdb_auth_params_and_headers(params)
    if headers is None:
        return None

    search = metadata_providers._get_json(
        "tmdb",
        "https://api.themoviedb.org/3/search/tv?" + urllib.parse.urlencode(params),
        headers=headers,
    )
    results = search.get("results") if isinstance(search, dict) else None
    if not isinstance(results, list) or not results:
        return None
    show_id = (results[0] or {}).get("id")
    if not show_id:
        return None

    found: Dict[str, Dict[str, Any]] = {}
    for season_number in seasons:
        season_params: Dict[str, str] = {"language": "en-US"}
        season_headers = metadata_providers._tmdb_auth_params_and_headers(
            season_params)
        payload = metadata_providers._get_json(
            "tmdb",
            f"https://api.themoviedb.org/3/tv/{show_id}/season/{season_number}?"
            + urllib.parse.urlencode(season_params),
            headers=season_headers,
        )
        episodes = payload.get("episodes") if isinstance(payload, dict) else None
        if not isinstance(episodes, list):
            continue
        for episode in episodes:
            if not isinstance(episode, dict):
                continue
            ref = episode_ref(
                episode.get("season_number", season_number),
                episode.get("episode_number"),
            )
            if not ref:
                continue
            still_path = str(episode.get("still_path") or "").strip()
            found[ref] = sanitise({
                "episode_name": str(episode.get("name") or "").strip() or None,
                "air_date": str(episode.get("air_date") or "").strip() or None,
                "still": ("https://image.tmdb.org/t/p/w300" + still_path)
                if still_path else None,
            })
    return {ref: fields for ref, fields in found.items() if fields} or None


ADAPTERS = {
    "tvmaze": tvmaze_episodes,
    "cinemeta": cinemeta_episodes,
    "tmdb": tmdb_episodes,
}


def resolve(series: Dict[str, Any]) -> Tuple[Optional[Dict[str, Dict[str, Any]]], str]:
    """Ask providers in the router's order until one answers.

    `(episodes, provider)`; `(None, "")` when none could. Never raises: a show
    without episode names is a show that renders exactly as it does today.
    """
    from scanner import provider_router

    have: Dict[str, Any] = {}
    if str((series or {}).get("imdb_id") or "").strip():
        have["imdb_id"] = series["imdb_id"]
    if (series or {}).get("tmdb_id"):
        have["tmdb_id"] = series["tmdb_id"]

    try:
        order = provider_router.order(
            capability=provider_router.CAPABILITY_SERIES_METADATA,
            kind=provider_router.KIND_SERIES,
            have=have,
        )
    except Exception:  # noqa: BLE001 - a router fault is not a scan fault
        order = ["tvmaze", "cinemeta", "tmdb"]

    for provider in order:
        adapter = ADAPTERS.get(provider)
        if adapter is None:
            continue
        try:
            episodes = adapter(series)
        except Exception:  # noqa: BLE001 - one provider, never the run
            episodes = None
        if episodes:
            return episodes, provider
    return None, ""


def remember(
    store: Dict[str, Any],
    key: str,
    episodes: Optional[Dict[str, Dict[str, Any]]],
    *,
    provider: str = "",
    status: str = "",
    now: Optional[_dt.datetime] = None,
) -> Dict[str, Any]:
    """Record an answer, or the fact that there was not one.

    Fill-only across runs: a provider that answers for fewer episodes than
    last time cannot delete the names already known. An episode that aired,
    was named, and then dropped out of a provider's response is still that
    episode.
    """
    reference = _now(now)
    shows = store.setdefault("shows", {})
    record = shows.get(key)
    if not isinstance(record, dict):
        record = {}
        shows[key] = record

    record["last_attempt_at"] = reference.isoformat()
    if status:
        record["status"] = status
    if not episodes:
        record.setdefault("episodes", {})
        return record

    known = record.get("episodes")
    known = known if isinstance(known, dict) else {}
    for ref, fields in episodes.items():
        clean = sanitise(fields)
        if not clean:
            continue
        existing = known.get(ref)
        if not isinstance(existing, dict):
            known[ref] = clean
            continue
        for field, value in clean.items():
            existing.setdefault(field, value)
    record["episodes"] = known
    record["updated_at"] = reference.isoformat()
    if provider:
        record["source"] = provider
    return record


def apply_to_episode(
    episode: Dict[str, Any], record: Optional[Dict[str, Any]]
) -> bool:
    """Fill the three fields on one episode card. Returns whether it changed.

    Looked up by the SOURCE's own season and episode numbers - the provider
    never gets to say which episode this file is.
    """
    if not isinstance(episode, dict) or not isinstance(record, dict):
        return False
    episodes = record.get("episodes")
    if not isinstance(episodes, dict):
        return False
    ref = episode_ref(episode.get("season_number"), episode.get("episode_number"))
    if not ref:
        return False
    fields = sanitise(episodes.get(ref))
    if not fields:
        return False
    changed = False
    for field, value in fields.items():
        if field in PROTECTED:  # belt and braces: sanitise already dropped these
            continue
        if not episode.get(field):
            episode[field] = value
            changed = True
    return changed


def apply_all(
    series_list: Sequence[Dict[str, Any]],
    store: Optional[Dict[str, Any]] = None,
    *,
    episodes_of=None,
) -> Dict[str, Any]:
    """Fill every episode this catalogue has an answer for.

    Cache only - no provider is reached from here, so this is safe to run on
    the publish path.
    """
    shows = (store or {}).get("shows") or {}
    named = 0
    dated = 0
    stilled = 0
    touched_shows = 0

    for series in series_list or ():
        if not isinstance(series, dict):
            continue
        record = shows.get(show_key(series))
        if not isinstance(record, dict):
            continue
        episodes = (
            episodes_of(series) if episodes_of is not None
            else series.get("episodes") or ()
        )
        before = named
        for episode in episodes or ():
            if not isinstance(episode, dict):
                continue
            had_name = bool(episode.get("episode_name"))
            if apply_to_episode(episode, record):
                if not had_name and episode.get("episode_name"):
                    named += 1
                if episode.get("air_date"):
                    dated += 1
                if episode.get("still"):
                    stilled += 1
        if named > before:
            touched_shows += 1

    return {
        "shows_with_metadata": touched_shows,
        "episodes_named": named,
        "episodes_dated": dated,
        "episodes_with_still": stilled,
    }


def due_shows(
    series_list: Sequence[Dict[str, Any]],
    store: Optional[Dict[str, Any]] = None,
    *,
    budget: int = DEFAULT_SHOW_BUDGET,
    now: Optional[_dt.datetime] = None,
) -> List[Dict[str, Any]]:
    """Which shows are worth a request this run, newest-unknown first."""
    shows = (store or {}).get("shows") or {}
    candidates: List[Tuple[int, str, Dict[str, Any]]] = []
    seen: set = set()
    for series in series_list or ():
        if not isinstance(series, dict):
            continue
        key = show_key(series)
        if not key or key in seen:
            continue
        seen.add(key)
        record = shows.get(key)
        if not is_due(record, now):
            continue
        # A show nothing is known about comes before one being refreshed.
        rank = 0 if not isinstance(record, dict) or not record.get("episodes") else 1
        candidates.append((rank, key, series))
    candidates.sort(key=lambda item: (item[0], item[1]))
    return [series for _rank, _key, series in candidates[: max(0, int(budget))]]


def describe(summary: Dict[str, Any]) -> str:
    if not summary:
        return ""
    named = summary.get("episodes_named", 0)
    if not named:
        return ""
    return (
        f"episode metadata: {named} episode(s) named across "
        f"{summary.get('shows_with_metadata', 0)} show(s), "
        f"{summary.get('episodes_dated', 0)} dated, "
        f"{summary.get('episodes_with_still', 0)} with a still"
    )
