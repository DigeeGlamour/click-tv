"""When a movie was first seen, and what year it is from.

Two measured problems, one visible symptom.

The catalogue looked frozen: the same films every time, nothing new. It was not
frozen - between the 2026-08-22 and 2026-08-27 scans, 510 ids appeared that had
not been there before. They were invisible because nothing ordered by recency
and, for discovered movies, nothing knew the year either:

    category      total   year set   manual
    bangla           30         30       30
    mix             448          0        0
    hindi           179         22       22

`year` was only ever written by the manual card builder, so all 731 discovered
movies fell to the same sort bucket and were ordered by title. A 2026 release
called "Turbozaurs" sat on page 5 behind "100 percent Love (2012)".

The year is not missing from the data, only from the fields: it is in the title
("72 HOURS (2026) Dual") and usually in the id slug ("72-hours-2026-dual").
Reading it there costs nothing and needs no external lookup.

Recency cannot be derived that way. A scan sees a catalogue, not a history, so
first_seen_at is kept outside the cards - the same reason route preferences
live outside them: every scan rebuilds cards from their sources and would erase
anything written on them.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

DEFAULT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "state",
    "movie-first-seen.json",
)

#: How long a movie counts as new. One week: long enough that a viewer who
#: opens the site twice a week still sees the badge, short enough that "new"
#: keeps meaning something with a daily scan.
NEW_BADGE_DAYS = 7

#: Entries for movies that have not been seen for this long are dropped, so the
#: file cannot grow without bound as the upstream playlist churns. Deliberately
#: much longer than the badge window: a film that leaves the source for a month
#: and returns should not be announced as new, and the honest way to hold that
#: is to still remember it.
RETENTION_DAYS = 180

_EARLIEST_FILM_YEAR = 1900

#: Numbers in these shapes are not years, and every one of them was found in
#: real titles from this project's own sources:
#:   "1080p", "2160p"          resolution
#:   "S01E11-15", "E01 05"      season and episode
#:   "x264", "H.265", "AAC2.0"  codec and audio
#:   "10Bit", "5.1"             depth and channels
_NOT_A_YEAR = re.compile(
    r"(?:"
    r"\d{3,4}\s*[pi]\b"
    r"|\bx\s*26\d\b"
    r"|\bh\s*\.?\s*26\d\b"
    r"|\b(?:s|e|ep|season|episode)\s*\d+"
    r"|\b\d+\s*bit\b"
    r"|\baac\s*\d"
    r"|\bddp?\s*\d"
    r")",
    re.IGNORECASE,
)

_YEAR_IN_BRACKETS = re.compile(r"[(\[]\s*((?:19|20)\d{2})\s*[)\]]")
_BARE_YEAR = re.compile(r"(?<![\d.])((?:19|20)\d{2})(?![\d.])")


def _upper_year_bound(now: Optional[_dt.datetime] = None) -> int:
    """Next calendar year, so an announced-early release is still accepted."""
    reference = now or _dt.datetime.now(_dt.timezone.utc)
    return reference.year + 1


def year_from_text(text: Any, *, now: Optional[_dt.datetime] = None) -> int:
    """Film year read out of a title or slug. 0 when nothing convincing.

    A bracketed year wins outright, because that is how these titles mark it
    and it cannot be confused with anything else. Failing that, bare
    four-digit numbers are considered, but only after the spans that merely
    look like years - resolutions, episode numbers, codecs - have been struck
    out. "Reply 1988 (2015) S01E11-15 480p" has to yield 2015, not 1988, 1988
    being part of the programme's name; the bracketed rule gets that right.
    """
    raw = str(text or "")
    if not raw.strip():
        return 0

    ceiling = _upper_year_bound(now)

    bracketed = [
        int(value)
        for value in _YEAR_IN_BRACKETS.findall(raw)
        if _EARLIEST_FILM_YEAR <= int(value) <= ceiling
    ]
    if bracketed:
        # The last bracketed year, so "Reply 1988 (2015)" reads 2015 even
        # when an earlier bracket belongs to the name.
        return bracketed[-1]

    masked = _NOT_A_YEAR.sub(" ", raw)
    candidates = [
        int(value)
        for value in _BARE_YEAR.findall(masked)
        if _EARLIEST_FILM_YEAR <= int(value) <= ceiling
    ]
    if not candidates:
        return 0
    # The newest plausible year. A dual-audio re-release names both the
    # original and the release year; the release is what a viewer sorting by
    # "newest" means.
    return max(candidates)


def year_for_movie(movie: Dict[str, Any], *, now: Optional[_dt.datetime] = None) -> int:
    """Existing year if it has one, otherwise read it from title then id."""
    if not isinstance(movie, dict):
        return 0
    existing = str(movie.get("year") or "").strip()
    if existing:
        try:
            value = int(existing)
        except ValueError:
            value = year_from_text(existing, now=now)
        if _EARLIEST_FILM_YEAR <= value <= _upper_year_bound(now):
            return value

    for field in ("name", "title", "label", "id"):
        value = year_from_text(movie.get(field), now=now)
        if value:
            return value
    return 0


def movie_key(movie: Dict[str, Any]) -> str:
    """Stable identity for remembering when a movie was first seen.

    The id is a slug the pipeline already derives from the title
    ("100-percent-love-2012") and it held across the two scans this was
    checked against, so it is used directly. Titles are the fallback because a
    missing id must not silently reset a film's age.
    """
    if not isinstance(movie, dict):
        return ""
    identifier = str(movie.get("id") or "").strip().casefold()
    if identifier:
        return identifier
    title = str(movie.get("name") or movie.get("title") or "").strip().casefold()
    title = re.sub(r"[^a-z0-9]+", "-", title).strip("-")
    return f"title:{title}" if title else ""


def load(path: Optional[str] = None) -> Dict[str, Any]:
    target = path or DEFAULT_PATH
    try:
        with open(target, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return {"version": 1, "seen": {}}
    if not isinstance(payload, dict):
        return {"version": 1, "seen": {}}
    payload.setdefault("version", 1)
    if not isinstance(payload.get("seen"), dict):
        payload["seen"] = {}
    return payload


def _parse_stamp(value: Any) -> Optional[_dt.datetime]:
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


def stamp_first_seen(
    movies: Iterable[Dict[str, Any]],
    *,
    path: Optional[str] = None,
    now: Optional[_dt.datetime] = None,
    persist: bool = True,
) -> Tuple[int, int]:
    """Give every movie a first_seen_at, remembering across scans.

    Returns (newly recorded, already known). Writes nothing when `persist` is
    false, which is what tests and dry runs want.

    A movie already in the store keeps its original stamp. That is the whole
    point: a film first published in June must not read as new in August
    because a scan saw it again.
    """
    reference = now or _dt.datetime.now(_dt.timezone.utc)
    stamp = reference.isoformat()
    store = load(path)
    seen = store["seen"]

    fresh = 0
    known = 0
    for movie in movies or ():
        if not isinstance(movie, dict):
            continue
        key = movie_key(movie)
        if not key:
            continue
        record = seen.get(key)
        if isinstance(record, dict) and record.get("first_seen_at"):
            movie["first_seen_at"] = record["first_seen_at"]
            record["last_seen_at"] = stamp
            known += 1
        else:
            seen[key] = {"first_seen_at": stamp, "last_seen_at": stamp}
            movie["first_seen_at"] = stamp
            fresh += 1

    if persist:
        _prune(seen, reference)
        _write(store, path)
    return fresh, known


def _prune(seen: Dict[str, Any], now: _dt.datetime) -> int:
    cutoff = now - _dt.timedelta(days=RETENTION_DAYS)
    stale = [
        key
        for key, record in list(seen.items())
        if not isinstance(record, dict)
        # last_seen_at is omitted when it equals first_seen_at, which is the
        # common case for a seeded entry and was a third of the file.
        or (
            _parse_stamp(
                record.get("last_seen_at") or record.get("first_seen_at")
            )
            or now
        )
        < cutoff
    ]
    for key in stale:
        seen.pop(key, None)
    return len(stale)


def _write(store: Dict[str, Any], path: Optional[str] = None) -> bool:
    target = path or DEFAULT_PATH
    store["note"] = (
        "When each movie was first published, kept outside the cards because "
        "every scan rebuilds those from their sources. Ordering the catalogue "
        "by this is what makes a newly added film visible; without it a 2026 "
        "release sorted alphabetically onto page 5."
    )
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as handle:
            json.dump(store, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
    except OSError:
        return False
    return True


def is_new(
    movie: Dict[str, Any],
    *,
    now: Optional[_dt.datetime] = None,
    days: int = NEW_BADGE_DAYS,
) -> bool:
    reference = now or _dt.datetime.now(_dt.timezone.utc)
    first_seen = _parse_stamp((movie or {}).get("first_seen_at"))
    if first_seen is None:
        return False
    return (reference - first_seen) <= _dt.timedelta(days=days)


def enrich(
    movies: List[Dict[str, Any]],
    *,
    path: Optional[str] = None,
    now: Optional[_dt.datetime] = None,
    persist: bool = True,
) -> Dict[str, int]:
    """Fill in year, first_seen_at and is_new. Returns a small summary.

    Never removes or hides anything - it only adds fields the ordering needs.
    """
    reference = now or _dt.datetime.now(_dt.timezone.utc)
    records = [movie for movie in (movies or []) if isinstance(movie, dict)]

    years_added = 0
    for movie in records:
        if not str(movie.get("year") or "").strip():
            year = year_for_movie(movie, now=reference)
            if year:
                movie["year"] = year
                movie["year_source"] = "title"
                years_added += 1

    fresh, known = stamp_first_seen(
        records, path=path, now=reference, persist=persist
    )

    badged = 0
    for movie in records:
        new_flag = is_new(movie, now=reference)
        movie["is_new"] = new_flag
        if new_flag:
            badged += 1

    return {
        "movies": len(records),
        "years_recovered_from_title": years_added,
        "first_seen_recorded": fresh,
        "first_seen_known": known,
        "marked_new": badged,
    }
