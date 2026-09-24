"""S-05 / ধারা ৪.২ - what we have decided about a show, remembered.

The same work was being done 37 times for Bachelor Point: every episode card
went through identity, metadata and poster resolution on its own, because
nothing recorded that all 37 are one show. This is the record that stops that,
and it is the third of the three new state files ধারা ৪.২ allows - the other
six the audit proposed were dropped because existing files already hold that
truth, and two files holding one truth is a bug by itself.

Provider-agnostic on purpose (v৩.৪ correction 4)
------------------------------------------------
An earlier draft keyed this on `tmdb_tv_id`. That contradicts the rest of the
plan, which routes every provider by capability, quota and health with no fixed
order. So the record holds `external_ids` - a map of whatever any provider was
able to state - plus `identity_source` naming how the identity was settled and
`disambiguation_context` for the case two shows share a name. `tmdb_tv_id`
remains only as a compatibility field for code that already reads it.

Version keys, and why they are not optional (ধারা ৪.৭)
-------------------------------------------------------
A cached answer is only as good as the rules that produced it. ধাপ ৩ changes
the title cleaner and the classifier; without a version in the key, every
answer derived from the old rules would sit behind its TTL and the whole
benefit of changing them would be invisible. The real risk here is not a "not
found" getting stuck - this codebase never stamps a failed lookup, so it
retries - it is a **wrong match** found from a dirty title being cached as
`applied` for 90 days. A record written by an older classifier is therefore
stale by definition, whatever its age.

Confidence is kept, never thrown away
-------------------------------------
Zero-API evidence is real evidence and is recorded with its own source:
`title_pattern` for an explicit SxxExx, `catalogue_sibling` for ধারা ৪.৬ tier 2,
`pack_signal` for tier 1. A provider may later raise the confidence of a record
but is never required to create one, which is what keeps ধারা ৪.৭'s "all APIs
down" contract true for classification as well as for metadata.

Stdlib only. Adds and updates; never deletes a record because a lookup failed.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

SCHEMA_VERSION = 1

DEFAULT_PATH = Path("state") / "movie-classification-cache.json"

TYPE_SERIES = "series"
TYPE_MOVIE = "movie"

#: How an identity was settled, strongest first. The first three cost nothing.
SOURCE_TITLE_PATTERN = "title_pattern"
SOURCE_CATALOGUE_SIBLING = "catalogue_sibling"
SOURCE_PACK_SIGNAL = "pack_signal"
SOURCE_PROVIDER = "provider"

#: Confidence by source. A provider answer can raise a record; nothing lowers
#: one, because the evidence that produced it did not stop being true.
CONFIDENCE = {
    SOURCE_TITLE_PATTERN: 0.95,
    SOURCE_CATALOGUE_SIBLING: 0.90,
    SOURCE_PACK_SIGNAL: 0.80,
    SOURCE_PROVIDER: 0.99,
}

#: A settled identity does not change. The TTL exists so a record can pick up
#: external ids and a canonical title once a provider is available, not because
#: "is this a series" goes stale.
REFRESH_TTL_DAYS = 120


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: Optional[datetime] = None) -> str:
    return (moment or _utc_now()).isoformat()


def _parse_stamp(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _classifier_version() -> int:
    try:
        from scanner.series_signal import CLASSIFIER_VERSION

        return int(CLASSIFIER_VERSION)
    except Exception:  # noqa: BLE001
        return 0


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


def load(path: Optional[str | Path] = None) -> Dict[str, Any]:
    """The store, or an empty one. A damaged file is never a scan failure."""
    target = Path(path or DEFAULT_PATH)
    try:
        with open(target, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    records = payload.get("shows")
    if not isinstance(records, dict):
        records = {}
    return {
        "schema_version": SCHEMA_VERSION,
        "updated_at": payload.get("updated_at") or "",
        "shows": records,
    }


def save(store: Dict[str, Any], path: Optional[str | Path] = None) -> bool:
    target = Path(path or DEFAULT_PATH)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "updated_at": _iso(),
        "classifier_version": _classifier_version(),
        "count": len(store.get("shows") or {}),
        "shows": store.get("shows") or {},
    }
    try:
        _atomic_write(target, payload)
    except OSError:
        return False
    return True


def is_stale(record: Dict[str, Any], now: Optional[datetime] = None) -> bool:
    """Whether this record was produced by rules that are no longer in force.

    A classifier change makes a record stale whatever its age, which is the
    whole point of carrying the version: ধাপ ৩ changes the rules, and answers
    derived from the old ones must not sit behind a TTL pretending to be
    current.
    """
    if not isinstance(record, dict):
        return True
    if int(record.get("classifier_version") or 0) != _classifier_version():
        return True
    verified = _parse_stamp(record.get("verified_at"))
    if verified is None:
        return True
    return (now or _utc_now()) - verified > timedelta(days=REFRESH_TTL_DAYS)


def get(
    store: Dict[str, Any],
    show_key: str,
    *,
    now: Optional[datetime] = None,
) -> Optional[Dict[str, Any]]:
    """The record for a show, or None when there is none or it is stale."""
    record = (store.get("shows") or {}).get(str(show_key or ""))
    if not isinstance(record, dict):
        return None
    if is_stale(record, now):
        return None
    return dict(record)


def remember(
    store: Dict[str, Any],
    *,
    show_key: str,
    canonical_show_title: str = "",
    content_type: str = TYPE_SERIES,
    identity_source: str = SOURCE_TITLE_PATTERN,
    external_ids: Optional[Dict[str, Any]] = None,
    disambiguation_context: Optional[Dict[str, Any]] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Record, or improve, what is known about one show.

    Fill-only in the directions that matter: an existing canonical title or
    external id is never replaced by an empty one, and confidence only ever
    rises. A provider that answers later strengthens a record the regex
    created; a provider that is unavailable leaves it exactly as it was, which
    is ধারা ৪.৭'s "all APIs down" contract applied to classification.
    """
    key = str(show_key or "").strip()
    if not key:
        raise ValueError("a classification record needs a show key")

    shows = store.setdefault("shows", {})
    record = shows.get(key)
    if not isinstance(record, dict):
        record = {
            "normalized_show_key": key,
            "canonical_show_title": "",
            "external_ids": {},
            "identity_source": "",
            "disambiguation_context": {},
            "tmdb_tv_id": "",
            "type": content_type,
            "confidence": 0.0,
            "classifier_version": _classifier_version(),
            "verified_at": "",
            "first_seen_at": _iso(now),
        }

    if canonical_show_title and not record.get("canonical_show_title"):
        record["canonical_show_title"] = canonical_show_title

    merged_ids = dict(record.get("external_ids") or {})
    for provider, value in (external_ids or {}).items():
        text = str(value or "").strip()
        if text:
            merged_ids[str(provider)] = text
    record["external_ids"] = merged_ids
    # Compatibility only. The router does not read it and nothing may depend on
    # TMDB being the provider that answered.
    if merged_ids.get("tmdb") and not record.get("tmdb_tv_id"):
        record["tmdb_tv_id"] = str(merged_ids["tmdb"])

    context = dict(record.get("disambiguation_context") or {})
    for field, value in (disambiguation_context or {}).items():
        text = str(value or "").strip()
        if text:
            context[str(field)] = text
    record["disambiguation_context"] = context

    confidence = CONFIDENCE.get(identity_source, 0.5)
    if confidence >= float(record.get("confidence") or 0.0):
        record["confidence"] = confidence
        record["identity_source"] = identity_source

    record["type"] = content_type
    record["classifier_version"] = _classifier_version()
    record["verified_at"] = _iso(now)
    shows[key] = record
    return dict(record)


def remember_from_signals(
    store: Dict[str, Any],
    signals: Iterable[Dict[str, Any]],
    *,
    now: Optional[datetime] = None,
) -> Dict[str, int]:
    """Record every show the catalogue itself already proves, at zero API cost.

    ধারা ৪.৬'s quota arithmetic depends on this: ~103 shows are proved outright
    by an explicit SxxExx and another 26 by a sibling, so a provider is needed
    only for the rest. A lookup not made is the cheapest kind.
    """
    # ধাপ ১৩ / A-06 asks for two more numbers from this loop, and they are the
    # two that say whether the cache is earning its place:
    #   cache_hits - a show this run did not have to prove again
    #   conflicts  - a show whose stored content type disagrees with the
    #                evidence in front of us, which is never resolved
    #                silently (ধারা ৪.৬) and so has to be countable
    summary = {"recorded": 0, "skipped_unproven": 0, "skipped_no_key": 0,
               "cache_hits": 0, "conflicts": 0}
    existing = store.get("shows") if isinstance(store, dict) else None
    existing = existing if isinstance(existing, dict) else {}
    source_by_tier = {
        "explicit_episode": SOURCE_TITLE_PATTERN,
        "sibling_episode": SOURCE_CATALOGUE_SIBLING,
        "pack_signal": SOURCE_PACK_SIGNAL,
    }
    for signal in signals or ():
        if not isinstance(signal, dict) or not signal.get("is_series_signal"):
            continue
        tier = str(signal.get("evidence_tier") or "")
        source = source_by_tier.get(tier)
        if source is None:
            # Tier 3. Nothing has been proved, so nothing is recorded - a
            # record here would be the invented certainty ধারা ৪.৬ forbids.
            summary["skipped_unproven"] += 1
            continue
        key = str(signal.get("show_key") or "")
        if not key:
            summary["skipped_no_key"] += 1
            continue
        known = existing.get(key)
        if isinstance(known, dict):
            summary["cache_hits"] += 1
            stored_type = str(known.get("content_type") or "").strip()
            if stored_type and stored_type != TYPE_SERIES:
                summary["conflicts"] += 1
        remember(
            store,
            show_key=key,
            canonical_show_title=str(signal.get("base_show") or ""),
            content_type=TYPE_SERIES,
            identity_source=source,
            now=now,
        )
        summary["recorded"] += 1
    return summary


def unresolved_show_keys(
    store: Dict[str, Any],
    signals: Iterable[Dict[str, Any]],
) -> List[str]:
    """The shows a provider would still have to be asked about, deduplicated.

    ধারা ৪.৬ plans the quota on this number and says so explicitly: not 108,
    but ~255 once the unproven season-only candidates are deduplicated to
    unique shows. Returning the list rather than the count is what lets a
    later phase spend a budget on it.
    """
    known = set(store.get("shows") or {})
    pending: List[str] = []
    for signal in signals or ():
        if not isinstance(signal, dict) or not signal.get("is_series_signal"):
            continue
        if str(signal.get("evidence_tier") or "") != "no_evidence":
            continue
        key = str(signal.get("show_key") or "")
        if key and key not in known and key not in pending:
            pending.append(key)
    return sorted(pending)
