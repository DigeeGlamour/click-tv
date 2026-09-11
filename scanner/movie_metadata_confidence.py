"""What a provider is allowed to change, and on what evidence.

The failure this exists to prevent is specific and common: a film shares its
title with another film, a provider returns the wrong one, and the catalogue
quietly acquires the wrong poster, the wrong year and the wrong rating. The
data looks complete afterwards, which is exactly what makes it dangerous - a
viewer cannot tell a confident wrong answer from a right one.

So confidence stops being a label a provider prints about itself and becomes a
statement about THIS match: did a trusted external id line up, did the
normalised title match exactly, did the year agree. A provider that says
"high" about a candidate whose title does not match is not believed.

Three levels, with different powers:

    high    apply the normalised metadata, including the identity fields that
            everything else will later be matched on.
    medium  apply only the descriptive fields, never the identity fields and
            never the category - a wrong tmdb_id is not a wrong poster, it is
            every future refresh going to the wrong film.
    low     apply nothing. Keep what is there, record why, and leave the item
            for a later pass or a human.

On top of that sit manual overrides, which are facts an admin asserted and a
provider may not touch. `lock_fields` makes that explicit per item.

Nothing here logs a key, a token or a header: the log line carries the item id,
the provider name, the candidate's title/year/id, the level and the reason.
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

try:
    from scanner import paths
except ImportError:  # pragma: no cover - direct-module import path
    import paths  # type: ignore

CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW = "low"

DEFAULT_MANUAL_PATH = paths.state_path("manual-movie-metadata.json")

#: Fields an admin may assert. Factual or administrative only.
MANUAL_FIELDS: Tuple[str, ...] = (
    "tmdb_id",
    "imdb_id",
    "category",
    "poster",
    "backdrop",
    "release_date",
    "genres",
    "runtime_minutes",
    "plot",
    "director",
    "cast_top",
    "original_title",
    "search_aliases",
)

#: Fields a manual override may NOT set, at any confidence, ever.
#:
#: A rating is a measurement someone else took. Typing one in by hand produces
#: a number with a source label attached to it that did not issue it, which is
#: the one dishonesty this whole metadata system is built to avoid.
MANUAL_FORBIDDEN_FIELDS: Tuple[str, ...] = (
    "rating",
    "rating_source",
    "rating_votes",
    "metadata_confidence",
)

#: Fields only a high-confidence match may write. Get one of these wrong and
#: every future refresh follows the mistake to the same wrong film.
IDENTITY_FIELDS: Tuple[str, ...] = ("tmdb_id", "imdb_id")

#: The scanner's fallback category. A film sits here because nothing has
#: established where it belongs - which is a statement worth keeping until
#: something confident replaces it.
FALLBACK_CATEGORY = "Mix"

_ARTICLES = ("the ", "a ", "an ")
_PUNCTUATION = re.compile(r"[^\w\s]", re.UNICODE)
_SPACES = re.compile(r"\s+")
_YEAR_IN_TEXT = re.compile(r"(?<!\d)(19|20)\d{2}(?!\d)")


def normalize_title(value: Any) -> str:
    """Casefold, strip accents and punctuation, drop a leading article.

    "The Gray Man" and "gray man" are the same film written twice; "Gray Man 2"
    is not, so digits are kept.
    """
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = _PUNCTUATION.sub(" ", text).casefold()
    text = _SPACES.sub(" ", text).strip()
    for article in _ARTICLES:
        if text.startswith(article):
            text = text[len(article):]
            break
    return text


def year_of(value: Any) -> Optional[int]:
    """A four-digit year from a year, a date or a title. None when unknown."""
    if isinstance(value, int) and 1800 < value < 2200:
        return value
    text = str(value or "").strip()
    if not text:
        return None
    match = _YEAR_IN_TEXT.search(text)
    return int(match.group(0)) if match else None


def _titles_of(record: Mapping[str, Any]) -> Set[str]:
    titles = {
        normalize_title(record.get("name")),
        normalize_title(record.get("title")),
        normalize_title(record.get("original_title")),
        # What a provider says it actually matched, when it says so.
        normalize_title(record.get("match_title")),
    }
    aliases = record.get("search_aliases") or record.get("aliases") or ()
    if isinstance(aliases, (list, tuple)):
        titles.update(normalize_title(alias) for alias in aliases)
    titles.discard("")
    return titles


def _content_kind(record: Mapping[str, Any]) -> str:
    kind = str(
        record.get("content_kind")
        or record.get("media_type")
        or record.get("type")
        or ""
    ).strip().casefold()
    if kind in ("tv", "series", "show", "episode"):
        return "series"
    if kind in ("person", "people", "actor"):
        return "person"
    if kind in ("movie", "film"):
        return "movie"
    return ""


def match_confidence(
    local: Mapping[str, Any], candidate: Mapping[str, Any]
) -> Tuple[Optional[str], str]:
    """How much of this candidate may be believed, and why.

    Returns ``(level, reason)``; a level of ``None`` means the candidate is
    rejected outright and nothing about it may be applied.
    """
    local_kind = _content_kind(local) or "movie"
    candidate_kind = _content_kind(candidate)
    if candidate_kind == "person":
        return None, "candidate is a person, not a title"
    if candidate_kind and candidate_kind != local_kind:
        return None, f"content type mismatch: local {local_kind}, candidate {candidate_kind}"

    for field in IDENTITY_FIELDS:
        mine = str(local.get(field) or "").strip()
        theirs = str(candidate.get(field) or "").strip()
        if mine and theirs:
            if mine == theirs:
                return CONFIDENCE_HIGH, f"trusted external id match on {field}"
            return None, f"{field} disagrees: {mine} vs {theirs}"

    local_titles = _titles_of(local)
    candidate_titles = _titles_of(candidate)

    if not candidate_titles:
        # The candidate did not say which title it is. That is not evidence
        # against the match, so it is not scored as one: the provider's own
        # assessment stands, and the reason records that nothing was verified
        # here. A provider that reports `match_title` gets checked properly.
        label = str(candidate.get("metadata_confidence") or "").strip().casefold()
        if label in (CONFIDENCE_HIGH, CONFIDENCE_MEDIUM, CONFIDENCE_LOW):
            return label, "provider label; candidate carried no title to verify"
        return CONFIDENCE_MEDIUM, "candidate carried no title to verify"

    exact_title = bool(local_titles & candidate_titles)

    local_year = year_of(local.get("year") or local.get("release_date") or local.get("name"))
    candidate_year = year_of(
        candidate.get("year") or candidate.get("match_year") or candidate.get("release_date")
    )

    if not exact_title:
        return CONFIDENCE_LOW, "no exact title match"
    if local_year is None or candidate_year is None:
        return CONFIDENCE_MEDIUM, "exact title, but no year to confirm it"
    gap = abs(local_year - candidate_year)
    if gap == 0:
        return CONFIDENCE_HIGH, "exact title and exact year"
    if gap <= 1:
        return CONFIDENCE_MEDIUM, "exact title, year off by one"
    return CONFIDENCE_LOW, f"exact title but the year conflicts by {gap}"


# --- manual overrides -------------------------------------------------------


def load_manual(path: Optional[str] = None) -> Dict[str, Any]:
    """The admin-asserted metadata file. A missing or broken file is empty."""
    target = path or DEFAULT_MANUAL_PATH
    try:
        with open(target, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return {"version": 1, "movies": {}}
    if not isinstance(payload, dict):
        return {"version": 1, "movies": {}}
    movies = payload.get("movies")
    if not isinstance(movies, dict):
        payload["movies"] = {}
    payload.setdefault("version", 1)
    return payload


def manual_entry(manual: Mapping[str, Any], identity: str) -> Dict[str, Any]:
    entry = (manual.get("movies") or {}).get(identity)
    return entry if isinstance(entry, dict) else {}


def manual_values(entry: Mapping[str, Any]) -> Dict[str, Any]:
    """The asserted values, with anything a human may not assert removed."""
    values: Dict[str, Any] = {}
    for field in MANUAL_FIELDS:
        if field in MANUAL_FORBIDDEN_FIELDS:
            continue
        value = entry.get(field)
        if value in (None, "", [], {}):
            continue
        values[field] = value
    return values


def locked_fields(entry: Mapping[str, Any]) -> Set[str]:
    """Fields a provider refresh may not overwrite.

    Anything explicitly asserted is locked whether or not it is listed:
    writing a value down by hand is the assertion, and `lock_fields` is for
    locking a field whose current value should simply be left alone.
    """
    locked: Set[str] = set(manual_values(entry))
    declared = entry.get("lock_fields")
    if isinstance(declared, (list, tuple)):
        locked.update(str(field) for field in declared if str(field))
    return locked


# --- applying a match -------------------------------------------------------


def allowed_fields(confidence: Optional[str]) -> Optional[Set[str]]:
    """Which fields this confidence level may write. None means "nothing"."""
    if confidence == CONFIDENCE_HIGH:
        return None  # meaning: no extra restriction beyond locks
    if confidence == CONFIDENCE_MEDIUM:
        return {"__descriptive__"}
    return set()


def plan_application(
    existing: Mapping[str, Any],
    incoming: Mapping[str, Any],
    *,
    confidence: Optional[str],
    reason: str = "",
    manual: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Work out what may actually be written, and record what was refused.

    Nothing here mutates anything; the caller applies `apply` and stores the
    rest as the audit trail.
    """
    entry = manual or {}
    locked = locked_fields(entry)
    asserted = manual_values(entry)

    result: Dict[str, Any] = {
        "confidence": confidence,
        "reason": reason,
        "apply": {},
        "refused": {},
        "state": "applied",
    }

    if confidence is None:
        result["state"] = "rejected"
        return result
    if confidence == CONFIDENCE_LOW:
        # Not destructive, not partially applied, not "a bit" believed.
        result["state"] = "unresolved"
        result["refused"] = {field: "low confidence" for field in incoming
                             if field not in ("metadata_source", "metadata_sources")}
        return result

    for field, value in incoming.items():
        if value in (None, "", [], {}):
            continue
        if field in ("metadata_source", "metadata_sources", "metadata_confidence"):
            result["apply"][field] = value
            continue
        if field in locked:
            result["refused"][field] = "locked by manual override"
            continue
        if field in asserted:
            result["refused"][field] = "manual value asserted"
            continue
        if confidence == CONFIDENCE_MEDIUM and field in IDENTITY_FIELDS:
            result["refused"][field] = "identity field needs a high-confidence match"
            continue
        if field == "category":
            result["refused"][field] = "category is not a provider's to set"
            continue
        existing_value = existing.get(field)
        if existing_value not in (None, "", [], {}):
            result["refused"][field] = "already resolved; fill-only"
            continue
        result["apply"][field] = value

    return result


def resolve_category(
    current: Any,
    proposed: Any,
    *,
    confidence: Optional[str],
    manual: Optional[Mapping[str, Any]] = None,
    source_asserted: bool = False,
) -> Tuple[str, str]:
    """The category to keep, and why.

    Order of authority: a manual assertion, then the category the source
    itself stated, then - only out of the Mix fallback, and only on a
    high-confidence match - a provider's suggestion.

    Premium and Mix are business categories, not facts about a film, so no
    provider moves a title between them.
    """
    current_text = str(current or "").strip()
    proposed_text = str(proposed or "").strip()
    entry = manual or {}

    manual_category = manual_values(entry).get("category")
    if manual_category:
        return str(manual_category), "manual override"
    if "category" in locked_fields(entry):
        return current_text, "locked by manual override"
    if source_asserted and current_text:
        return current_text, "the source stated this category"
    if not proposed_text or proposed_text == current_text:
        return current_text, "unchanged"
    if current_text != FALLBACK_CATEGORY:
        return current_text, "only the Mix fallback may be reclassified"
    if confidence != CONFIDENCE_HIGH:
        return current_text, "reclassifying out of Mix needs a high-confidence match"
    if proposed_text == FALLBACK_CATEGORY:
        return current_text, "unchanged"
    return proposed_text, "high-confidence reclassification out of Mix"


# --- duplicate external ids -------------------------------------------------


def external_id_conflicts(records: Mapping[str, Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Local items claiming the same external id. Reported, never merged.

    Two different films holding one tmdb_id means at least one of them is
    matched wrong. Merging them would destroy a real stream to tidy up a
    metadata mistake, so the conflict is surfaced and both are left intact.
    """
    conflicts: List[Dict[str, Any]] = []
    for field in IDENTITY_FIELDS:
        claims: Dict[str, List[str]] = {}
        for identity, record in (records or {}).items():
            if not isinstance(record, Mapping):
                continue
            value = str(record.get(field) or "").strip()
            if not value:
                continue
            claims.setdefault(value, []).append(str(identity))
        for value, holders in claims.items():
            if len(holders) > 1:
                conflicts.append({
                    "field": field,
                    "external_id": value,
                    "items": sorted(holders),
                    "action": "flagged; streams preserved, nothing merged",
                })
    conflicts.sort(key=lambda row: (row["field"], row["external_id"]))
    return conflicts


# --- logging ----------------------------------------------------------------

_SECRET_HINTS = ("key", "token", "secret", "authorization", "cookie", "password", "header")


def log_line(
    identity: str,
    provider: Any,
    candidate: Mapping[str, Any],
    confidence: Optional[str],
    reason: str,
) -> str:
    """A one-line, secret-free record of a match decision."""
    title = str(candidate.get("name") or candidate.get("title") or "").strip()
    year = year_of(candidate.get("year") or candidate.get("release_date"))
    external = ""
    for field in IDENTITY_FIELDS:
        value = str(candidate.get(field) or "").strip()
        if value:
            external = f" {field}={value}"
            break
    verdict = confidence or "rejected"
    return (
        f"metadata match {identity} <- {provider}: {verdict} "
        f"({reason}) candidate={title or '?'}"
        f"{f' ({year})' if year else ''}{external}"
    )


def scrub(payload: Any) -> Any:
    """Drop anything whose key looks like a credential, at any depth."""
    if isinstance(payload, Mapping):
        return {
            key: scrub(value)
            for key, value in payload.items()
            if not any(hint in str(key).casefold() for hint in _SECRET_HINTS)
        }
    if isinstance(payload, (list, tuple)):
        return [scrub(item) for item in payload]
    return payload


__all__ = [
    "CONFIDENCE_HIGH",
    "CONFIDENCE_MEDIUM",
    "CONFIDENCE_LOW",
    "DEFAULT_MANUAL_PATH",
    "MANUAL_FIELDS",
    "MANUAL_FORBIDDEN_FIELDS",
    "IDENTITY_FIELDS",
    "FALLBACK_CATEGORY",
    "normalize_title",
    "year_of",
    "match_confidence",
    "load_manual",
    "manual_entry",
    "manual_values",
    "locked_fields",
    "plan_application",
    "resolve_category",
    "external_id_conflicts",
    "log_line",
    "scrub",
]
