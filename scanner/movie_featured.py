"""Featured / Hero selection (CLICK_TV_FEATURED_HERO_SYSTEM_PLAN_BN.txt).

Featured is not Trending, and this module exists because the two keep being
confused. Trending (PART 06) answers "what is the world watching this week".
Featured answers "what should the top of Click TV's movie home show right
now", and that is an editorial question with an operator in the loop.

So the answer is a hybrid, in this order:

    manual pin  ->  real auto signals  ->  nothing

Manual first, because a pin is a human decision and no score should be able
to outvote it. Then auto, scored from signals that are all real and all
already in the repository: the external trending snapshot, actual release
dates, our own first_seen ledger, the Premium category, the artwork we
actually hold, our own playback verification, and how complete the metadata
is. Then nothing - if only two titles genuinely qualify, two is what the
Hero gets. A fifth slot filled with a weak title is a worse answer than a
missing slot, and a fifth slot filled with an invented one is not an answer
at all.

WHAT IS DELIBERATELY NOT A SIGNAL HERE:

  - available_link_count as popularity. It counts verified sources, not
    viewers. It survives only as a playback-reliability tie-break worth a
    tenth of one weight, and a test holds that line.
  - Random selection, or "the top trending title" as a shortcut. Both would
    present a mechanical pick as an editorial one.
  - Any fake rating, release date, plot or badge. Every displayed fact is
    copied from the catalogue or the metadata cache, or it is omitted.

NO STREAM DATA LEAVES THIS MODULE. featured.json carries ids and display
metadata; the url, backups, headers and playback_id stay in the category
pages, where the player already reads them. FORBIDDEN_FIELDS below is
asserted by a test against real output.

FAILURE RULE: a build that produces nothing never overwrites a featured.json
that has something. That is the last-good guarantee, and it lives in
write_featured() rather than in the caller, so every path gets it.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import unicodedata
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    from scanner import paths
except ImportError:  # pragma: no cover - direct-module import path
    import paths  # type: ignore

DISCOVERY_ROOT = os.path.join("data", "movies", "discovery")
FEATURED_PATH = os.path.join(DISCOVERY_ROOT, "featured.json")
TRENDING_PATH = os.path.join(DISCOVERY_ROOT, "trending.json")

CONFIG_PATH = os.path.join("config", "movie-featured.json")
MANUAL_PATH = paths.state_path("manual-featured.json")
HISTORY_PATH = paths.state_path("movie-featured-history.json")

#: The label the Hero shows. The plan fixes this wording because it is the
#: only claim that stays true whatever the selection was built from - an
#: internally-selected item must never be presented as "Trending".
DEFAULT_LABEL = "FEATURED ON CLICK TV"

#: Everything the Hero renders. Note what is absent.
DISPLAY_FIELDS = (
    "id",
    "type",
    "name",
    "category",
    "year",
    "poster",
    "backdrop",
    "artwork_kind",
    "rating",
    "rating_source",
    "genres",
    "plot",
    "release_date",
    "quality",
    "badges",
    "series_manifest",
    "default_season",
    "total_seasons",
    "total_episodes",
)

#: Never allowed into featured.json. Asserted by tests against real output.
FORBIDDEN_FIELDS = (
    "url",
    "stream_url",
    "link",
    "links",
    "backups",
    "standby",
    "headers",
    "request_headers",
    "cookie",
    "authorization",
    "user_agent",
    "drm",
    "playback_id",
    "proxy_mode",
    "force_proxy",
    "header_profile",
    "inherit_manifest_query",
    "source_id",
    "source_name",
)

DEFAULT_CONFIG: Dict[str, Any] = {
    "slots": 5,
    "minimum_slots": 3,
    "maximum_slots": 6,
    "refresh_hours": 12,
    "hero_rotate_seconds": 6.5,
    "cooldown_hours": 48,
    "max_same_category": 2,
    "max_series_slots": 2,
    "just_added_window_days": 14,
    "latest_window_days": 365,
    "premiere_window_days": 30,
    # A title the lifecycle is only republishing under grace has not been
    # confirmed playable recently. It stays in the catalogue - that is what
    # grace is for - but it does not get auto-promoted to the Hero.
    "exclude_verification_status": ["stale_last_good"],
    "curated_ids": [],
    "weights": {
        "trending": 40,
        "latest": 20,
        "just_added": 12,
        "premium": 10,
        "artwork": 8,
        "playback": 6,
        "metadata": 4,
    },
}

#: Fields counted for metadata completeness, in the plan's own list order.
COMPLETENESS_FIELDS = (
    "year",
    "category",
    "genres",
    "rating",
    "backdrop",
    "plot",
    "release_date",
)

_WHITESPACE = re.compile(r"\s+")
_NON_WORD = re.compile(r"[^0-9a-zঀ-৿ ]+")


# --------------------------------------------------------------------------
# small shared helpers
# --------------------------------------------------------------------------


def _now(now: Optional[_dt.datetime] = None) -> _dt.datetime:
    return now or _dt.datetime.now(_dt.timezone.utc)


def parse_stamp(value: Any) -> Optional[_dt.datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = _dt.datetime.fromisoformat(
            text[:-1] + "+00:00" if text.endswith("Z") else text
        )
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    return parsed


def _text(value: Any) -> str:
    return _WHITESPACE.sub(" ", str(value or "").strip())


def load_json(file_path: str) -> Dict[str, Any]:
    try:
        with open(file_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def atomic_write_json(file_path: str, payload: Dict[str, Any]) -> bool:
    """Temp file, fsync, rename. A killed process leaves the old file whole."""
    try:
        target_dir = os.path.dirname(file_path) or "."
        os.makedirs(target_dir, exist_ok=True)
        temp_path = os.path.join(
            target_dir, f".{os.path.basename(file_path)}.{os.getpid()}.tmp"
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


def normalize_title(value: Any) -> str:
    """A title reduced to something two spellings of it can agree on."""
    text = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
    text = _NON_WORD.sub(" ", text)
    return _WHITESPACE.sub(" ", text).strip()


# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------


def load_config(path: Optional[str] = None) -> Dict[str, Any]:
    """Defaults, overlaid with whatever the config file actually sets.

    Every key is optional and an unknown key is ignored, so an operator can
    change one weight without restating the whole document, and a typo
    degrades to the default rather than to zero.
    """
    config = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    stored = load_json(path or CONFIG_PATH)
    for key, value in stored.items():
        if key == "weights":
            if isinstance(value, dict):
                for weight_key, weight_value in value.items():
                    if weight_key in config["weights"]:
                        try:
                            config["weights"][weight_key] = max(0.0, float(weight_value))
                        except (TypeError, ValueError):
                            continue
            continue
        if key not in config:
            continue
        if isinstance(config[key], bool):
            config[key] = bool(value)
        elif isinstance(config[key], list):
            if isinstance(value, list):
                config[key] = [v for v in value if isinstance(v, str)]
        elif isinstance(config[key], (int, float)):
            try:
                config[key] = type(config[key])(value)
            except (TypeError, ValueError):
                continue
    # A slot count outside the plan's own bounds is a configuration mistake,
    # not an instruction to build an eleven-item carousel.
    config["slots"] = max(1, min(int(config["slots"]), int(config["maximum_slots"])))
    config["minimum_slots"] = max(0, min(int(config["minimum_slots"]), config["slots"]))
    return config


# --------------------------------------------------------------------------
# manual pins
# --------------------------------------------------------------------------


def load_manual(path: Optional[str] = None) -> List[Dict[str, Any]]:
    payload = load_json(path or MANUAL_PATH)
    items = payload.get("items")
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def manual_window_state(
    entry: Dict[str, Any], now: Optional[_dt.datetime] = None
) -> str:
    """"active", "pending", "expired" or "disabled" for one manual pin.

    A pin with no window is simply active: the absence of an end date means
    "until I remove it", which is what an operator pinning something means.
    """
    if entry.get("enabled") is False:
        return "disabled"
    reference = _now(now)
    start = parse_stamp(entry.get("start_at"))
    end = parse_stamp(entry.get("end_at"))
    if start is not None and reference < start:
        return "pending"
    if end is not None and reference >= end:
        return "expired"
    return "active"


def active_manual(
    items: Iterable[Dict[str, Any]], now: Optional[_dt.datetime] = None
) -> List[Dict[str, Any]]:
    """Manual pins that apply right now, highest priority first.

    Deterministic to the last tie: priority descending, then the order the
    operator wrote them in. Two pins at priority 100 resolve the same way on
    every machine and every run, which is what makes the output diffable.
    """
    reference = _now(now)
    ordered: List[Tuple[float, int, Dict[str, Any]]] = []
    for index, entry in enumerate(items or []):
        if not isinstance(entry, dict):
            continue
        if not _text(entry.get("id")):
            continue
        if manual_window_state(entry, reference) != "active":
            continue
        try:
            priority = float(entry.get("priority", 0) or 0)
        except (TypeError, ValueError):
            priority = 0.0
        ordered.append((priority, index, entry))
    ordered.sort(key=lambda row: (-row[0], row[1]))
    return [entry for _, _, entry in ordered]


# --------------------------------------------------------------------------
# rotation history / cooldown
# --------------------------------------------------------------------------


def load_history(path: Optional[str] = None) -> Dict[str, Any]:
    payload = load_json(path or HISTORY_PATH)
    entries = payload.get("items")
    return entries if isinstance(entries, dict) else {}


def in_cooldown(
    history: Dict[str, Any],
    key: str,
    *,
    cooldown_hours: float,
    now: Optional[_dt.datetime] = None,
) -> bool:
    if cooldown_hours <= 0:
        return False
    record = history.get(key)
    if not isinstance(record, dict):
        return False
    last = parse_stamp(record.get("last_featured_at"))
    if last is None:
        return False
    return (_now(now) - last) < _dt.timedelta(hours=cooldown_hours)


def record_history(
    history: Dict[str, Any],
    keys: Iterable[str],
    *,
    now: Optional[_dt.datetime] = None,
) -> Dict[str, Any]:
    """Stamp this run's picks, and recompute the 30-day count from the truth.

    featured_count_30d is derived from a kept list of run timestamps rather
    than incremented, so it cannot drift: a rolled-back run, a duplicate
    invocation or a clock jump all correct themselves on the next build.
    """
    reference = _now(now)
    cutoff = reference - _dt.timedelta(days=30)
    updated: Dict[str, Any] = {k: v for k, v in history.items() if isinstance(v, dict)}
    for key in keys:
        if not key:
            continue
        record = dict(updated.get(key) or {})
        runs = [
            stamp
            for stamp in (record.get("runs") or [])
            if isinstance(stamp, str) and (parse_stamp(stamp) or cutoff) >= cutoff
        ]
        runs.append(reference.isoformat())
        record["last_featured_at"] = reference.isoformat()
        record["runs"] = runs[-60:]
        record["featured_count_30d"] = len(record["runs"])
        updated[key] = record
    # Entries nothing has featured for a month stop being interesting.
    return {
        key: record
        for key, record in updated.items()
        if (parse_stamp(record.get("last_featured_at")) or reference) >= cutoff
    }


def save_history(history: Dict[str, Any], path: Optional[str] = None) -> bool:
    return atomic_write_json(
        path or HISTORY_PATH,
        {
            "version": 1,
            "updated_at": _now().isoformat(),
            "note": (
                "When each title last held a Featured slot, so the Hero rotates "
                "instead of showing the same five films for a fortnight. Manual "
                "pins are recorded here but are never blocked by it."
            ),
            "items": history,
        },
    )


# --------------------------------------------------------------------------
# candidates
# --------------------------------------------------------------------------


def identity_key(record: Dict[str, Any]) -> str:
    """The strongest stable identity this record carries.

    tmdb -> imdb -> our own id, exactly as the plan orders them. A shared
    external id is what catches the same film published twice under two
    categories; our own id is the floor, and it always exists.
    """
    tmdb = _text(record.get("tmdb_id"))
    if tmdb:
        return f"tmdb:{tmdb}"
    imdb = _text(record.get("imdb_id"))
    if imdb:
        return f"imdb:{imdb}"
    return f"id:{_text(record.get('id'))}"


def visual_key(record: Dict[str, Any]) -> str:
    """Title+year, for the duplicate no external id will catch.

    The Bangla print and the Dubbed print of one film are two legitimate
    catalogue entries with two ids and two streams. Both in the Hero is one
    poster shown twice, so the second one loses its slot - and neither entry
    is merged, renamed or deleted to achieve it.
    """
    title = normalize_title(record.get("name"))
    if not title:
        return ""
    return f"{title}|{_text(record.get('year'))}"


def is_playable(record: Dict[str, Any]) -> bool:
    """Does this record actually lead to a stream?

    Read-only and shape-only: it never dereferences a URL and never touches
    playback. A series is playable when it has a manifest to open, which is
    what the series flow needs.
    """
    if record.get("metadata_only") is True:
        return False
    if record.get("type") == "series" or record.get("content_kind") == "series":
        return bool(_text(record.get("series_manifest")))
    if _text(record.get("playback_id")) or _text(record.get("url")):
        return True
    for field in ("backups", "standby"):
        value = record.get(field)
        if isinstance(value, list):
            for source in value:
                if isinstance(source, str) and source.strip():
                    return True
                if isinstance(source, dict) and (source.get("url") or source.get("playback_id")):
                    return True
    return False


def poster_of(record: Dict[str, Any]) -> str:
    for field in ("logo", "poster", "image"):
        value = _text(record.get(field))
        if value:
            return value
    return ""


def plot_of(record: Dict[str, Any]) -> str:
    for field in ("plot", "overview", "description", "synopsis"):
        value = _text(record.get(field))
        if value:
            return value
    return ""


def quality_label(record: Dict[str, Any]) -> str:
    """A resolution badge, from our own probe or from nothing.

    resolution_height is measured by the scanner against the real stream.
    Nothing here reads the title, the filename or the poster - "4K" in a
    title is a claim somebody else made, and repeating it would make it
    ours.
    """
    try:
        height = int(record.get("resolution_height") or 0)
    except (TypeError, ValueError):
        height = 0
    if height >= 2160:
        return "4K"
    if height >= 1080:
        return "Full HD"
    if height >= 720:
        return "HD"
    return ""


def eligibility(record: Dict[str, Any]) -> Tuple[bool, str]:
    """The minimum bar from the plan's own checklist, with the reason why.

    The reason is returned rather than logged so the build can report how
    many candidates fell at which gate - "Featured has three items" is a
    fact, and "because 1,290 titles have no artwork" is the answer to the
    question it provokes.
    """
    if not _text(record.get("id")):
        return False, "no stable id"
    if record.get("is_active") is False:
        return False, "inactive"
    if not is_playable(record):
        return False, "no playable stream"
    if not _text(record.get("name")):
        return False, "no title"
    if not poster_of(record) and not _text(record.get("backdrop")):
        return False, "no usable artwork"
    return True, ""


# --------------------------------------------------------------------------
# scoring
# --------------------------------------------------------------------------


def trending_score(rank: Optional[int], total: int, weight: float) -> float:
    """Full weight at rank 1, decaying linearly to the bottom of the list."""
    if not rank or rank < 1 or total < 1:
        return 0.0
    if total == 1:
        return float(weight)
    share = 1.0 - ((rank - 1) / float(total))
    return round(max(0.0, min(1.0, share)) * weight, 4)


def latest_score(
    release_date: Any, weight: float, *, window_days: int, now: Optional[_dt.datetime] = None
) -> float:
    """How recently this was RELEASED. Never first_seen, never the year.

    A bare year is not a release date and scores nothing: turning "2010"
    into 2010-01-01 would invent a day the provider never gave, and the
    invented date would then rank a film.
    """
    text = _text(release_date)[:10]
    if len(text) != 10:
        return 0.0
    try:
        released = _dt.date.fromisoformat(text)
    except ValueError:
        return 0.0
    today = _now(now).date()
    days = (today - released).days
    if days < 0 or window_days <= 0:
        return 0.0
    return round(max(0.0, 1.0 - (days / float(window_days))) * weight, 4)


def just_added_score(
    first_seen_at: Any, weight: float, *, window_days: int, now: Optional[_dt.datetime] = None
) -> float:
    """How recently this ARRIVED HERE. Our own ledger, no provider involved.

    A 2010 film added yesterday scores full marks here and nothing at all in
    latest_score, which is exactly the distinction the two rows exist for.
    """
    arrived = parse_stamp(first_seen_at)
    if arrived is None or window_days <= 0:
        return 0.0
    days = (_now(now) - arrived).total_seconds() / 86400.0
    if days < 0:
        # A future stamp is a clock problem, not an arrival.
        return 0.0
    return round(max(0.0, 1.0 - (days / float(window_days))) * weight, 4)


def premium_score(record: Dict[str, Any], weight: float, curated_ids: Iterable[str]) -> float:
    if _text(record.get("category")).casefold() == "premium":
        return float(weight)
    if _text(record.get("id")) in set(curated_ids or ()):
        return float(weight)
    return 0.0


def artwork_score(record: Dict[str, Any], weight: float) -> Tuple[float, str]:
    """Full marks for a real backdrop, partial for a poster, and a label.

    The label matters as much as the number: the Hero renders a 16:9
    backdrop sharp and a 2:3 poster as a blurred wash behind its own sharp
    thumbnail, because stretching a poster across a banner is the ugly
    shortcut the plan names outright.
    """
    if _text(record.get("backdrop")):
        return float(weight), "backdrop"
    if poster_of(record):
        return round(weight * 0.4, 4), "poster_fallback"
    return 0.0, "none"


def playback_score(record: Dict[str, Any], weight: float) -> float:
    """How confident we are that pressing Play works.

    available_link_count appears here and ONLY here, capped at a tenth of
    this one weight - a tie-break between two otherwise equal titles, never
    a popularity signal. Four verified servers cannot outrank a real trend.
    """
    status = _text(record.get("verification_status")).casefold()
    if status in ("verified_global", "manual_trusted"):
        base = 0.8
    elif status == "stale_last_good":
        base = 0.3
    elif record.get("verified") is True:
        base = 0.6
    else:
        base = 0.2
    if record.get("segment_verified") is True:
        base += 0.1
    try:
        links = int(record.get("available_link_count") or 0)
    except (TypeError, ValueError):
        links = 0
    base += min(links, 4) * 0.025
    return round(min(1.0, base) * weight, 4)


def metadata_score(record: Dict[str, Any], weight: float) -> float:
    present = 0
    for field in COMPLETENESS_FIELDS:
        value = plot_of(record) if field == "plot" else record.get(field)
        if value not in (None, "", [], {}):
            present += 1
    return round((present / float(len(COMPLETENESS_FIELDS))) * weight, 4)


def score_candidate(
    record: Dict[str, Any],
    *,
    config: Dict[str, Any],
    trending_rank: Optional[int] = None,
    trending_total: int = 0,
    now: Optional[_dt.datetime] = None,
) -> Tuple[float, Dict[str, float], str]:
    """One candidate's total, its breakdown, and its artwork kind.

    The breakdown is published in featured.json on purpose. "Why is this
    film in slot 2" should be answerable from the output file alone, without
    re-running anything.
    """
    weights = config["weights"]
    artwork, artwork_kind = artwork_score(record, weights["artwork"])
    breakdown = {
        "trending": trending_score(trending_rank, trending_total, weights["trending"]),
        "latest": latest_score(
            record.get("release_date"),
            weights["latest"],
            window_days=int(config["latest_window_days"]),
            now=now,
        ),
        "just_added": just_added_score(
            record.get("first_seen_at"),
            weights["just_added"],
            window_days=int(config["just_added_window_days"]),
            now=now,
        ),
        "premium": premium_score(record, weights["premium"], config.get("curated_ids") or []),
        "artwork": artwork,
        "playback": playback_score(record, weights["playback"]),
        "metadata": metadata_score(record, weights["metadata"]),
    }
    return round(sum(breakdown.values()), 4), breakdown, artwork_kind


# --------------------------------------------------------------------------
# badges
# --------------------------------------------------------------------------


def badges_for(
    record: Dict[str, Any],
    *,
    config: Dict[str, Any],
    trending_rank: Optional[int] = None,
    trending_fresh: bool = False,
    now: Optional[_dt.datetime] = None,
) -> List[str]:
    """Only badges that describe a state this record is actually in.

    NEW means it really arrived inside the Just Added window. TRENDING means
    it is really in a trending snapshot that is really still fresh. PREMIERE
    means a real release date inside the premiere window - never a guess
    from the year. Nothing here is decorative.
    """
    reference = _now(now)
    badges: List[str] = []

    arrived = parse_stamp(record.get("first_seen_at"))
    if arrived is not None:
        age_days = (reference - arrived).total_seconds() / 86400.0
        if 0 <= age_days <= float(config["just_added_window_days"]):
            badges.append("NEW")

    if trending_fresh and trending_rank:
        badges.append("TRENDING")

    released = _text(record.get("release_date"))[:10]
    if len(released) == 10:
        try:
            days = (reference.date() - _dt.date.fromisoformat(released)).days
            if 0 <= days <= int(config["premiere_window_days"]):
                badges.append("PREMIERE")
        except ValueError:
            pass

    if _text(record.get("category")).casefold() == "premium":
        badges.append("PREMIUM")
    if record.get("type") == "series" or record.get("content_kind") == "series":
        badges.append("SERIES")
    return badges


# --------------------------------------------------------------------------
# the build
# --------------------------------------------------------------------------


def display_entry(
    record: Dict[str, Any],
    *,
    source: str,
    rank: int,
    score: Optional[float],
    breakdown: Optional[Dict[str, float]],
    artwork_kind: str,
    badges: List[str],
    manual: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """One Hero slot, built field by field.

    Field by field rather than by copying the record: that is the only
    construction a stream URL cannot slip into six months from now when
    somebody adds a field to the catalogue.
    """
    kind = "series" if (record.get("type") == "series" or record.get("content_kind") == "series") else "movie"
    entry: Dict[str, Any] = {
        "id": _text(record.get("id")),
        # The key the cooldown is keyed on, published so the rotation is
        # auditable and so one film cannot be recorded under two keys.
        "identity": identity_key(record),
        "type": kind,
        "source": source,
        "featured_rank": rank,
        "featured_score": score,
        "name": _text(record.get("name")),
        "category": _text(record.get("category")),
        "artwork_kind": artwork_kind,
    }
    for field in ("year", "backdrop", "rating", "rating_source", "release_date"):
        value = record.get(field)
        if value not in (None, "", [], {}):
            entry[field] = value
    poster = poster_of(record)
    if poster:
        entry["poster"] = poster
    genres = record.get("genres")
    if isinstance(genres, list) and genres:
        entry["genres"] = [g for g in genres if isinstance(g, str)]
    plot = plot_of(record)
    if plot:
        entry["plot"] = plot
    quality = quality_label(record)
    if quality:
        entry["quality"] = quality
    if badges:
        entry["badges"] = badges
    if breakdown:
        entry["score_breakdown"] = breakdown
    if kind == "series":
        for field in ("series_manifest", "default_season", "total_seasons", "total_episodes"):
            value = record.get(field)
            if value not in (None, "", [], {}):
                entry[field] = value
    if manual:
        # UI-only decoration, and only these two. A manual pin may relabel
        # the kicker; it may not restate the title, the year or the rating,
        # which come from the catalogue on every path.
        for field in ("custom_label", "custom_kicker"):
            value = _text(manual.get(field))
            if value:
                entry[field] = value[:60]
        note = _text(manual.get("note"))
        if note:
            entry["manual_note"] = note[:160]
    return entry


def build_featured(
    candidates: Iterable[Dict[str, Any]],
    *,
    config: Optional[Dict[str, Any]] = None,
    manual_items: Optional[Iterable[Dict[str, Any]]] = None,
    trending_document: Optional[Dict[str, Any]] = None,
    history: Optional[Dict[str, Any]] = None,
    now: Optional[_dt.datetime] = None,
) -> Dict[str, Any]:
    """Manual pins, then scored auto picks, then honestly fewer.

    The order of the gates is the plan's flow chart, and each one records
    why it rejected what it rejected. Nothing is ever added to reach a slot
    count.
    """
    settings = config or load_config()
    reference = _now(now)
    weights = settings["weights"]
    slots = int(settings["slots"])
    history = dict(history or {})

    pool = [record for record in candidates if isinstance(record, dict)]
    by_id: Dict[str, Dict[str, Any]] = {}
    for record in pool:
        record_id = _text(record.get("id"))
        if record_id:
            by_id.setdefault(record_id, record)

    # --- the trending snapshot, used only while it is still true ----------
    trending_doc = trending_document or {}
    freshness = _text(trending_doc.get("freshness")).casefold()
    trending_fresh = freshness in ("fresh", "usable", "")
    trending_entries = trending_doc.get("movies")
    trending_rank: Dict[str, int] = {}
    if isinstance(trending_entries, list) and trending_fresh:
        for entry in trending_entries:
            if not isinstance(entry, dict):
                continue
            entry_id = _text(entry.get("id"))
            try:
                rank = int(entry.get("trending_rank") or 0)
            except (TypeError, ValueError):
                rank = 0
            if entry_id and rank > 0:
                trending_rank.setdefault(entry_id, rank)
    trending_total = len(trending_rank)
    trending_available = trending_total > 0

    rejected: Dict[str, int] = {}

    def reject(reason: str) -> None:
        rejected[reason] = rejected.get(reason, 0) + 1

    used_identity: set = set()
    used_visual: set = set()
    category_count: Dict[str, int] = {}
    series_count = 0
    chosen: List[Dict[str, Any]] = []

    def take(record: Dict[str, Any], entry: Dict[str, Any]) -> None:
        nonlocal series_count
        used_identity.add(identity_key(record))
        vkey = visual_key(record)
        if vkey:
            used_visual.add(vkey)
        category = _text(record.get("category")).casefold()
        category_count[category] = category_count.get(category, 0) + 1
        if entry["type"] == "series":
            series_count += 1
        chosen.append(entry)

    # --- 1. manual pins ---------------------------------------------------
    manual_active = active_manual(manual_items or [], reference)
    manual_considered = len(manual_active)
    manual_dropped: List[Dict[str, str]] = []
    for pin in manual_active:
        if len(chosen) >= slots:
            manual_dropped.append({"id": _text(pin.get("id")), "reason": "beyond slot count"})
            continue
        record = by_id.get(_text(pin.get("id")))
        if record is None:
            manual_dropped.append({"id": _text(pin.get("id")), "reason": "not in catalogue"})
            continue
        ok, why = eligibility(record)
        if not ok:
            manual_dropped.append({"id": _text(pin.get("id")), "reason": why})
            continue
        if identity_key(record) in used_identity:
            manual_dropped.append({"id": _text(pin.get("id")), "reason": "duplicate"})
            continue
        _, _, artwork_kind = score_candidate(
            record, config=settings, now=reference
        )
        entry = display_entry(
            record,
            source="manual",
            rank=len(chosen) + 1,
            # Manual pins are not scored. Publishing a number here would
            # invite somebody to compare it with an auto score, and the
            # whole point of a pin is that it does not compete.
            score=None,
            breakdown=None,
            artwork_kind=artwork_kind,
            badges=badges_for(
                record,
                config=settings,
                trending_rank=trending_rank.get(_text(record.get("id"))),
                trending_fresh=trending_available,
                now=reference,
            ),
            manual=pin,
        )
        status = _text(record.get("verification_status")).casefold()
        if status in {s.casefold() for s in settings.get("exclude_verification_status") or []}:
            # Honoured, because a pin outranks a score - but said out loud,
            # because the operator should know what they pinned.
            entry["manual_verification_note"] = (
                f"pinned while verification is '{status}'"
            )
        take(record, entry)

    # --- 2. auto candidates ------------------------------------------------
    excluded_status = {s.casefold() for s in settings.get("exclude_verification_status") or []}
    scored: List[Tuple[float, str, Dict[str, Any], Dict[str, float], str]] = []
    for record in pool:
        record_id = _text(record.get("id"))
        ok, why = eligibility(record)
        if not ok:
            reject(why)
            continue
        if _text(record.get("verification_status")).casefold() in excluded_status:
            reject("verification not recent enough")
            continue
        if identity_key(record) in used_identity:
            reject("duplicate")
            continue
        total, breakdown, artwork_kind = score_candidate(
            record,
            config=settings,
            trending_rank=trending_rank.get(record_id),
            trending_total=trending_total,
            now=reference,
        )
        scored.append((total, record_id, record, breakdown, artwork_kind))

    # Score descending, id ascending. The id tie-break is neutral by design:
    # it carries no popularity, no server count and no source order, so two
    # equally-scored films resolve the same way on every run.
    scored.sort(key=lambda row: (-row[0], row[1]))

    def fill(*, ignore_cooldown: bool) -> None:
        nonlocal series_count
        for total, record_id, record, breakdown, artwork_kind in scored:
            if len(chosen) >= slots:
                return
            ident = identity_key(record)
            if ident in used_identity:
                continue
            vkey = visual_key(record)
            if vkey and vkey in used_visual:
                continue
            if not ignore_cooldown and in_cooldown(
                history, ident, cooldown_hours=float(settings["cooldown_hours"]), now=reference
            ):
                continue
            category = _text(record.get("category")).casefold()
            is_series = record.get("type") == "series" or record.get("content_kind") == "series"
            # The diversity caps are a maximum, not a preference, and they do
            # NOT relax to fill slots. Relaxing them is how a Hero ends up
            # showing five titles from one language - the exact outcome the
            # plan names. Fewer real items, reported, is the correct answer.
            if category_count.get(category, 0) >= int(settings["max_same_category"]):
                continue
            if is_series and series_count >= int(settings["max_series_slots"]):
                continue
            take(
                record,
                display_entry(
                    record,
                    source="auto",
                    rank=len(chosen) + 1,
                    score=total,
                    breakdown=breakdown,
                    artwork_kind=artwork_kind,
                    badges=badges_for(
                        record,
                        config=settings,
                        trending_rank=trending_rank.get(record_id),
                        trending_fresh=trending_available,
                        now=reference,
                    ),
                ),
            )

    fill(ignore_cooldown=False)

    # Cooldown - and only cooldown - relaxes below the minimum. It is a
    # rotation preference about not showing the same films for a fortnight,
    # not a claim about the content, and everything it readmits already
    # passed eligibility and was already scored: nothing weak is added, the
    # same real titles simply come round sooner in a small catalogue. The
    # relaxation is recorded in the output rather than hidden.
    minimum = int(settings["minimum_slots"])
    cooldown_relaxed = False
    if len(chosen) < minimum:
        before = len(chosen)
        fill(ignore_cooldown=True)
        cooldown_relaxed = len(chosen) > before

    for index, entry in enumerate(chosen, start=1):
        entry["featured_rank"] = index

    # --- what this was actually built from --------------------------------
    signals_used = sorted({
        name
        for entry in chosen
        for name, value in (entry.get("score_breakdown") or {}).items()
        if value
    })

    shortfall = ""
    if len(chosen) < slots:
        shortfall = (
            f"{len(chosen)} of {slots} slots filled from real eligible candidates; "
            "no filler was added"
        )

    return {
        "version": 1,
        "updated_at": reference.isoformat(),
        "refresh_interval_hours": settings["refresh_hours"],
        "hero_rotate_seconds": settings["hero_rotate_seconds"],
        # The Hero's own wording, carried with the data. A view cannot
        # rename this row into "Trending".
        "label": DEFAULT_LABEL,
        "slots": slots,
        "count": len(chosen),
        "manual_count": sum(1 for entry in chosen if entry["source"] == "manual"),
        "auto_count": sum(1 for entry in chosen if entry["source"] == "auto"),
        "weights": dict(weights),
        "signals": {
            "trending_available": trending_available,
            "trending_freshness": freshness or "absent",
            "trending_matched": trending_total,
            "signals_contributing": signals_used,
        },
        "selection": {
            "candidates_considered": len(pool),
            "eligible": len(scored),
            "manual_considered": manual_considered,
            "manual_dropped": manual_dropped,
            "rejected": rejected,
            "cooldown_hours": settings["cooldown_hours"],
            "cooldown_relaxed": cooldown_relaxed,
            # Never relaxed. Kept in the output so the reason a row is short
            # is legible without re-running anything.
            "diversity_relaxed": False,
            "max_same_category": settings["max_same_category"],
            "max_series_slots": settings["max_series_slots"],
        },
        "shortfall_reason": shortfall,
        "items": chosen,
    }


def write_featured(
    document: Dict[str, Any], path: Optional[str] = None
) -> Dict[str, Any]:
    """Write it - unless writing it would destroy something better.

    A build that yields nothing is either a genuinely empty catalogue or a
    failure upstream, and from here those look identical. The safe reading
    is the second one, so an existing file with items wins and the reason is
    returned rather than swallowed.
    """
    target = path or FEATURED_PATH
    items = document.get("items")
    if not items:
        existing = load_json(target)
        if existing.get("items"):
            return {
                "written": False,
                "preserved": True,
                "count": len(existing["items"]),
                "reason": "build produced no items; last-good featured.json kept",
            }
    written = atomic_write_json(target, document)
    return {
        "written": written,
        "preserved": False,
        "count": len(items or []),
        "reason": "" if written else "write failed",
    }


# --------------------------------------------------------------------------
# candidate collection from what is on disk
# --------------------------------------------------------------------------


def collect_candidates(
    paginated: Dict[str, Any],
    *,
    series_root: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Every published movie and series, as candidate records.

    Read-only against the catalogue the last real scan published. Nothing
    here fetches, verifies or writes, which is what lets the Featured
    refresh run in fifteen seconds beside a forty-minute scan.
    """
    try:
        from scanner import movie_discovery
    except ImportError:  # pragma: no cover - direct-module import path
        import movie_discovery  # type: ignore

    candidates: List[Dict[str, Any]] = []
    for movie in movie_discovery.iter_published_movies(paginated):
        record = dict(movie)
        record["type"] = "movie"
        candidates.append(record)

    root = series_root or movie_discovery.SERIES_ROOT
    for series in movie_discovery.iter_published_series(root):
        record = dict(series)
        record["type"] = "series"
        candidates.append(record)
    return candidates


def generate(
    paginated: Dict[str, Any],
    *,
    output_path: Optional[str] = None,
    config_path: Optional[str] = None,
    manual_path: Optional[str] = None,
    history_path: Optional[str] = None,
    trending_path: Optional[str] = None,
    series_root: Optional[str] = None,
    now: Optional[_dt.datetime] = None,
) -> Dict[str, Any]:
    """Build featured.json and stamp the rotation history.

    History is only written when the document is: a build that was preserved
    at last-good did not feature anything, and recording that it did would
    put real titles into cooldown for a run that never reached a viewer.
    """
    settings = load_config(config_path)
    document = build_featured(
        collect_candidates(paginated, series_root=series_root),
        config=settings,
        manual_items=load_manual(manual_path),
        trending_document=load_json(trending_path or TRENDING_PATH),
        history=load_history(history_path),
        now=now,
    )
    result = write_featured(document, output_path)
    if result.get("written"):
        # Recorded under the same identity the cooldown reads, so a film with
        # a tmdb id is never stamped under one key and checked under another.
        history = record_history(
            load_history(history_path),
            [_text(entry.get("identity")) for entry in document["items"]],
            now=now,
        )
        save_history(history, history_path)
    result["document"] = document
    return result
