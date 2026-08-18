"""Sections 22-25 and 31-33 - the Streamed provider, as an additive layer only.

Streamed does not replace anything. The existing GitHub/native sources remain the
playback backbone and the existing fixture authorities remain the authority
layer. Streamed contributes exactly three things:

  * fixture metadata - source match id, title, sport, start time, participants
  * artwork - team/player badges and event posters
  * last-resort playback - an embed URL, ranked below every native stream

And it is denied three things, deliberately:

  * Â§23 its match id never becomes the Click TV event_id. A Streamed event is
        normalized and then handed to the existing canonical fixture matcher, so
        the same fixture arriving from GitHub, a fixture authority and Streamed
        collapses onto one event.
  * Â§24 it is never a deletion authority. A fixture vanishing from Streamed's
        listing is not evidence the match ended; section 21 decides that.
  * Â§32 it is never allowed to damage a valid snapshot. A timeout, an error or a
        malformed response marks the provider unavailable and the scan continues
        on the existing sources as though Streamed did not exist.

Section 31 keeps the cost down: Upcoming ingests metadata and artwork freely, but
stream/embed endpoints are only resolved for fixtures inside the existing
targeted-scan window, fixtures already in progress, or an explicit on-demand
request. Nothing here continuously resolves endpoints for every future fixture.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

CACHE_FILE = Path("state/streamed-provider-cache.json")
HEALTH_FILE = Path("state/streamed-provider-health.json")

# Section 32. Short-lived, so duplicate calls inside one scan are free while a
# stale answer can never outlive the scan that fetched it.
DEFAULT_CACHE_SECONDS = 240
DEFAULT_TIMEOUT_SECONDS = 8
DEFAULT_MAX_BYTES = 4 * 1024 * 1024

PROVIDER_ID = "streamed"


@dataclass
class ProviderHealth:
    """Whether the provider answered, and what to tell the scan report."""

    available: bool = True
    reason: str = ""
    calls: int = 0
    cache_hits: int = 0
    errors: int = 0
    fixtures: int = 0
    embeds: int = 0
    artwork: int = 0

    def mark_unavailable(self, reason: str) -> None:
        self.available = False
        self.errors += 1
        if not self.reason:
            self.reason = reason

    def report(self) -> Dict[str, Any]:
        return {
            "provider": PROVIDER_ID,
            "available": self.available,
            "reason": self.reason,
            "calls": self.calls,
            "cache_hits": self.cache_hits,
            "errors": self.errors,
            "fixtures_ingested": self.fixtures,
            "embed_streams": self.embeds,
            "artwork_supplied": self.artwork,
        }


@dataclass
class StreamedSettings:
    """config/settings.json -> streamed_provider."""

    enabled: bool = False
    base_url: str = ""
    matches_path: str = "/api/matches/live"
    upcoming_path: str = "/api/matches/all-today"
    streams_path: str = "/api/stream/{source}/{id}"
    images_base: str = ""
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    cache_seconds: int = DEFAULT_CACHE_SECONDS
    max_embed_streams: int = 2
    #: What an embed fallback is called on the card. Provider-agnostic by
    #: configuration: section 21 says the card must not hard-code one provider,
    #: and section 16 says an internal server key must never reach the screen.
    embed_label: str = "Streamed"
    headers: Dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_settings(cls, settings: Any) -> "StreamedSettings":
        block = {}
        if isinstance(settings, dict):
            candidate = settings.get("streamed_provider")
            if isinstance(candidate, dict):
                block = candidate
        headers = block.get("headers")
        return cls(
            enabled=bool(block.get("enabled", False)),
            base_url=str(block.get("base_url") or "").strip().rstrip("/"),
            matches_path=str(block.get("matches_path") or cls.matches_path),
            upcoming_path=str(block.get("upcoming_path") or cls.upcoming_path),
            streams_path=str(block.get("streams_path") or cls.streams_path),
            images_base=str(block.get("images_base") or "").strip().rstrip("/"),
            timeout_seconds=_safe_int(block.get("timeout_seconds"), DEFAULT_TIMEOUT_SECONDS, 2, 60),
            cache_seconds=_safe_int(block.get("cache_seconds"), DEFAULT_CACHE_SECONDS, 30, 3600),
            max_embed_streams=_safe_int(block.get("max_embed_streams"), 2, 0, 6),
            embed_label=str(block.get("embed_label") or cls.embed_label).strip() or cls.embed_label,
            headers=headers if isinstance(headers, dict) else {},
        )

    @property
    def usable(self) -> bool:
        return bool(self.enabled and self.base_url)


def _safe_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


# ---------------------------------------------------------------- cache
def _load_cache(path: Path) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _atomic_write(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=str(path.parent), delete=False,
        prefix=f".{path.name}.", suffix=".tmp",
    )
    try:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    finally:
        handle.close()
    os.replace(handle.name, path)


class ProviderCache:
    """Section 32's short-lived cache, shared across one scan."""

    def __init__(self, path: Path | str = CACHE_FILE, ttl_seconds: int = DEFAULT_CACHE_SECONDS):
        self.path = Path(path)
        self.ttl = max(1, int(ttl_seconds))
        self._entries: Dict[str, Any] = _load_cache(self.path).get("entries") or {}
        self._dirty = False

    def get(self, key: str, now: Optional[float] = None) -> Optional[Any]:
        entry = self._entries.get(key)
        if not isinstance(entry, dict):
            return None
        stamp = float(entry.get("at") or 0)
        reference = time.time() if now is None else now
        if reference - stamp > self.ttl:
            return None
        return entry.get("payload")

    def put(self, key: str, payload: Any, now: Optional[float] = None) -> None:
        self._entries[key] = {
            "at": time.time() if now is None else now,
            "payload": payload,
        }
        self._dirty = True

    def flush(self, now: Optional[float] = None) -> None:
        if not self._dirty:
            return
        reference = time.time() if now is None else now
        fresh = {
            key: entry for key, entry in self._entries.items()
            if isinstance(entry, dict) and reference - float(entry.get("at") or 0) <= self.ttl * 4
        }
        _atomic_write(self.path, {"updated_at": reference, "entries": fresh})
        self._dirty = False


# ---------------------------------------------------------------- fetch
def fetch_json(
    url: str,
    *,
    settings: StreamedSettings,
    cache: Optional[ProviderCache] = None,
    health: Optional[ProviderHealth] = None,
    opener: Optional[Any] = None,
) -> Optional[Any]:
    """One provider call, cached, and never allowed to raise.

    Returns None on any failure and marks the provider unavailable, which is
    section 32's whole contract: the scan continues on existing sources.
    """
    monitor = health or ProviderHealth()
    if cache is not None:
        cached = cache.get(url)
        if cached is not None:
            monitor.cache_hits += 1
            return cached

    monitor.calls += 1
    try:
        if opener is not None:
            body = opener(url)
        else:
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "ClickTV-Scanner/1.0",
                    "Accept": "application/json",
                    **{str(k): str(v) for k, v in (settings.headers or {}).items()},
                },
            )
            with urllib.request.urlopen(request, timeout=settings.timeout_seconds) as response:
                body = response.read(DEFAULT_MAX_BYTES)
        payload = json.loads(body if isinstance(body, (str, bytes)) else json.dumps(body))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as error:
        monitor.mark_unavailable(f"{type(error).__name__}: {str(error)[:80]}")
        return None
    except (json.JSONDecodeError, UnicodeError, ValueError) as error:
        monitor.mark_unavailable(f"malformed response: {str(error)[:80]}")
        return None
    except Exception as error:  # pragma: no cover - a provider must never break a scan
        monitor.mark_unavailable(f"unexpected: {type(error).__name__}")
        return None

    if cache is not None:
        cache.put(url, payload)
    return payload


# ---------------------------------------------------------------- normalize
_SPORT_ALIASES = {
    "football": "football", "soccer": "football", "cricket": "cricket",
    "tennis": "tennis", "basketball": "basketball", "baseball": "baseball",
    "hockey": "hockey", "ice-hockey": "hockey", "rugby": "rugby",
    "golf": "golf", "motor-sports": "motorsport", "motorsport": "motorsport",
    "fight": "boxing", "boxing": "boxing", "mma": "mma", "afl": "afl",
    "darts": "darts", "volleyball": "volleyball", "billiards": "billiards",
    "other": "", "": "",
}


def _epoch_to_iso(value: Any) -> str:
    """Streamed publishes start times as epoch milliseconds."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    if number <= 0:
        return ""
    if number > 1e11:  # milliseconds
        number /= 1000.0
    try:
        return datetime.fromtimestamp(number, timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return ""


def _team_names(match: Dict[str, Any]) -> Tuple[str, str]:
    teams = match.get("teams") if isinstance(match.get("teams"), dict) else {}
    home = teams.get("home") if isinstance(teams.get("home"), dict) else {}
    away = teams.get("away") if isinstance(teams.get("away"), dict) else {}
    return (
        str(home.get("name") or "").strip(),
        str(away.get("name") or "").strip(),
    )


def _badge_urls(
    match: Dict[str, Any], settings: StreamedSettings
) -> Tuple[List[str], str, str, str]:
    """Section 25 artwork, as (ordered candidates, poster, home badge, away badge).

    The poster used to be read from a `poster` field on the match. The live API
    does not send one: the poster is addressed by *both* team badges,
    `/api/images/poster/{home}/{away}.webp`, so that field was always empty and no
    poster was ever requested - which is why cards fell straight through to two
    initials. The badges are also returned separately, because section 10 renders
    the two of them side by side with a VS between rather than as one image.

    Order: the event poster (one picture of this fixture), then each team's badge,
    then whatever the card already had. Initials remain the last resort only.
    """
    base = settings.images_base or f"{settings.base_url}/api/images"
    teams = match.get("teams") if isinstance(match.get("teams"), dict) else {}

    def badge_of(side: str) -> str:
        entry = teams.get(side) if isinstance(teams.get(side), dict) else {}
        value = str(entry.get("badge") or "").strip()
        if not value:
            return ""
        return value if value.startswith("http") else f"{base}/badge/{value}.webp"

    home_badge = badge_of("home")
    away_badge = badge_of("away")

    raw = match.get("teams") if isinstance(match.get("teams"), dict) else {}
    home_id = str((raw.get("home") or {}).get("badge") or "").strip() if isinstance(raw.get("home"), dict) else ""
    away_id = str((raw.get("away") or {}).get("badge") or "").strip() if isinstance(raw.get("away"), dict) else ""

    poster = str(match.get("poster") or "").strip()
    if poster.startswith("/"):
        # The live endpoint sends a complete path of its own, e.g.
        # "/api/images/proxy/<token>.webp" - already everything needed past
        # the site origin. Running it back through "{base}/poster/{poster}.webp"
        # nested that whole path inside another one and appended a second
        # ".webp", so every match with this shape of poster 404'd.
        poster = f"{settings.base_url}{poster}"
    elif poster and not poster.startswith("http"):
        poster = f"{base}/poster/{poster}.webp"
    elif not poster and home_id and away_id:
        poster = f"{base}/poster/{home_id}/{away_id}.webp"

    urls = [url for url in (poster, home_badge, away_badge) if url]
    return urls, poster, home_badge, away_badge


def normalize_match(match: Dict[str, Any], settings: StreamedSettings) -> Optional[Dict[str, Any]]:
    """One Streamed match as an event candidate for the existing matcher.

    Section 23: `provider_event_id` records where it came from, and that is all
    it is - the canonical event_id is decided by the existing fixture matcher,
    never by the provider.
    """
    if not isinstance(match, dict):
        return None
    title = str(match.get("title") or "").strip()
    home, away = _team_names(match)
    if home and away:
        title = f"{home} vs {away}" or title
    if not title:
        return None

    raw_sport = str(match.get("category") or match.get("sport") or "").strip().lower()
    start = _epoch_to_iso(match.get("date") or match.get("start") or match.get("startTime"))
    sources = match.get("sources") if isinstance(match.get("sources"), list) else []

    candidate: Dict[str, Any] = {
        "name": title,
        "provider": PROVIDER_ID,
        "provider_event_id": str(match.get("id") or "").strip(),
        "source_id": f"{PROVIDER_ID}-fixtures",
        "source_pipeline": "today_match" if match.get("popular") or _looks_live(match) else "upcoming",
        "sport_type": _SPORT_ALIASES.get(raw_sport, raw_sport if raw_sport else ""),
        "source_category": raw_sport,
        "metadata_only": True,
        "publish_allowed": True,
        # Section 24: a provider listing is a routing hint, never a status
        # authority. No LIVE/ENDED claim is copied out of it.
        "provider_routing_hint": "live" if _looks_live(match) else "scheduled",
        "provider_sources": [
            {
                "source": str(entry.get("source") or "").strip(),
                "id": str(entry.get("id") or "").strip(),
            }
            for entry in sources
            if isinstance(entry, dict) and str(entry.get("source") or "").strip()
        ],
    }
    if start:
        candidate["start_time"] = start
        candidate["start_at"] = start
    if home:
        candidate["home_team"] = home
    if away:
        candidate["away_team"] = away
    artwork, poster, home_badge, away_badge = _badge_urls(match, settings)
    if artwork:
        candidate["provider_artwork"] = artwork
    # Section 10 draws the two badges side by side with a VS between them, so they
    # are published separately as well as in the fallback chain.
    if poster:
        candidate["provider_poster_url"] = poster
    if home_badge:
        candidate["home_badge_url"] = home_badge
    if away_badge:
        candidate["away_badge_url"] = away_badge
    return candidate


def _looks_live(match: Dict[str, Any]) -> bool:
    start = _epoch_to_iso(match.get("date") or match.get("start"))
    if not start:
        return False
    try:
        parsed = datetime.fromisoformat(start)
    except ValueError:
        return False
    now = datetime.now(timezone.utc)
    return parsed <= now <= parsed + timedelta(hours=6)


def normalize_embed_streams(
    payload: Any,
    settings: StreamedSettings,
) -> List[Dict[str, Any]]:
    """Section 26. Provider streams as embed-renderer stream candidates."""
    entries = payload if isinstance(payload, list) else []
    streams: List[Dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        embed = str(entry.get("embedUrl") or entry.get("embed_url") or "").strip()
        if not embed.lower().startswith(("http://", "https://")):
            continue
        label = str(entry.get("source") or PROVIDER_ID).strip()
        number = entry.get("streamNo") or entry.get("stream_no")
        # Sections 5/16. The provider's `source` key is an internal server name -
        # "delta", "admin", "echo" - and it was being used as the channel's display
        # name, so the card offered the viewer a chip reading "admin 1". That is
        # internal plumbing on screen and it names no broadcaster at all. The
        # fallback is labelled for what it is instead: the aggregator it came
        # through, numbered when there is more than one. Section 34 still holds -
        # this never claims to be the match's broadcaster - and the internal key
        # stays in `provider_source` for reports, where it belongs.
        display = settings.embed_label or "Streamed"
        streams.append({
            "name": f"{display} {number}".strip() if number else display,
            "provider": PROVIDER_ID,
            "provider_source": label,
            "playback_type": "embed",
            "embed_url": embed,
            "stream_type": "embed",
            "language": str(entry.get("language") or "").strip(),
            "hd": bool(entry.get("hd")),
            "resolution_height": 1080 if entry.get("hd") else 720,
            "metadata_only": False,
            "publish_allowed": True,
            # An embed is never "verified" the way a native manifest is: nothing
            # here proved a video plays, only that a URL was published.
            "verified": False,
            "verification_status": "provider_embed",
            "verification_mode": "provider",
        })
        if len(streams) >= max(0, settings.max_embed_streams):
            break
    return streams


# ---------------------------------------------------------------- top level
def should_resolve_streams(
    candidate: Dict[str, Any],
    *,
    now: Optional[datetime] = None,
    targeted_window_minutes: int = 0,
    on_demand: bool = False,
) -> bool:
    """Section 31. Is this fixture allowed to cost a stream/embed lookup?

    Only inside the existing targeted window, or once the fixture is actually in
    progress, or when something explicitly asked. Never for every future fixture.
    """
    if on_demand:
        return True
    reference = now or datetime.now(timezone.utc)
    if str(candidate.get("provider_routing_hint") or "") == "live":
        return True
    start_text = str(candidate.get("start_time") or candidate.get("start_at") or "")
    try:
        start = datetime.fromisoformat(start_text.replace("Z", "+00:00"))
    except ValueError:
        return False
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if start <= reference:
        return True
    window = max(0, int(targeted_window_minutes))
    if not window:
        return False
    return start <= reference + timedelta(minutes=window)


def collect_streamed_candidates(
    settings: Any,
    *,
    targeted_window_minutes: int = 0,
    now: Optional[datetime] = None,
    cache: Optional[ProviderCache] = None,
    opener: Optional[Any] = None,
    health: Optional[ProviderHealth] = None,
) -> Tuple[List[Dict[str, Any]], ProviderHealth]:
    """Everything Streamed contributes to one scan, or nothing at all.

    Returns (candidates, health). On any failure the candidate list is empty and
    health.available is False - section 32 - so the caller carries on with the
    existing sources untouched.
    """
    provider = StreamedSettings.from_settings(settings)
    monitor = health or ProviderHealth()
    if not provider.usable:
        monitor.available = False
        monitor.reason = "disabled" if not provider.enabled else "no base_url configured"
        return [], monitor

    store = cache if cache is not None else ProviderCache(ttl_seconds=provider.cache_seconds)
    candidates: List[Dict[str, Any]] = []

    for path in (provider.matches_path, provider.upcoming_path):
        if not path:
            continue
        payload = fetch_json(
            f"{provider.base_url}{path}",
            settings=provider, cache=store, health=monitor, opener=opener,
        )
        if payload is None:
            continue
        for match in payload if isinstance(payload, list) else []:
            candidate = normalize_match(match, provider)
            if candidate is None:
                continue
            candidates.append(candidate)
            monitor.fixtures += 1
            if candidate.get("provider_artwork"):
                monitor.artwork += 1

    # Deduplicate by provider event id, keeping the first (live list wins).
    seen: Dict[str, Dict[str, Any]] = {}
    for candidate in candidates:
        key = candidate.get("provider_event_id") or candidate.get("name")
        seen.setdefault(str(key), candidate)
    candidates = list(seen.values())

    # Section 31: only the fixtures allowed to cost a lookup get embed streams.
    for candidate in candidates:
        if not should_resolve_streams(
            candidate, now=now, targeted_window_minutes=targeted_window_minutes
        ):
            continue
        embeds: List[Dict[str, Any]] = []
        for reference in candidate.get("provider_sources") or []:
            source = str(reference.get("source") or "")
            identifier = str(reference.get("id") or "")
            if not source or not identifier:
                continue
            url = provider.base_url + provider.streams_path.format(
                source=source, id=identifier
            )
            payload = fetch_json(
                url, settings=provider, cache=store, health=monitor, opener=opener
            )
            if payload is None:
                continue
            embeds.extend(normalize_embed_streams(payload, provider))
            if len(embeds) >= provider.max_embed_streams:
                break
        if embeds:
            candidate["provider_embed_streams"] = embeds[: provider.max_embed_streams]
            monitor.embeds += len(candidate["provider_embed_streams"])

    store.flush()
    return candidates, monitor


def write_health(health: ProviderHealth, path: Path | str = HEALTH_FILE) -> None:
    payload = dict(health.report())
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    _atomic_write(Path(path), payload)


def artwork_for(candidate: Dict[str, Any]) -> List[str]:
    """Section 25's artwork priority, as an ordered list of URLs to try.

    Badges before posters before whatever the event already had. The existing
    artwork chain stays in place behind these, so a provider image that fails to
    load simply falls through to it.
    """
    urls = candidate.get("provider_artwork")
    return [str(url) for url in urls if str(url).strip()] if isinstance(urls, list) else []
