"""
Today Match and Upcoming Events Processor

Reads event candidates from working/bd-results.json, merges duplicate sources,
removes stale/expired cards, keeps only playable Today Match cards, and returns
stable payloads for scanner/output.py.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    from scanner.live_protection import probe_card_is_playable, protect_live_events
    from scanner.event_lifecycle import (
        ROUTE_LIVE_STATUSES,
        ROUTE_UPCOMING_STATUSES,
        authority_says_live,
        classify_state,
        event_destination,
    )
    from scanner.targeted_scan import fixture_key, has_valid_link
    from scanner.source_coverage import build_source_coverage, write_source_coverage
    from scanner.merger import (
        event_sport,
        load_previous_primary_keys,
        merge_candidates,
        normalize_event_key,
        participant_fold_key,
        same_real_fixture,
        sport_sort_index,
    )
    from scanner.schedule_resolver import (
        DEFAULT_FIXTURE_AUTHORITY_SOURCES,
        DEFAULT_PROVIDER_EVENT_HOURS,
        attach_streams_to_fixtures,
        enrich_event_candidates,
        reuse_published_event_ids,
    )
except ImportError:
    module_dir = str(Path(__file__).resolve().parent)
    if module_dir not in sys.path:
        sys.path.insert(0, module_dir)
    from live_protection import probe_card_is_playable, protect_live_events
    from event_lifecycle import (
        ROUTE_LIVE_STATUSES,
        ROUTE_UPCOMING_STATUSES,
        authority_says_live,
        classify_state,
        event_destination,
    )
    from targeted_scan import fixture_key, has_valid_link
    from source_coverage import build_source_coverage, write_source_coverage
    from merger import (
        event_sport,
        load_previous_primary_keys,
        merge_candidates,
        normalize_event_key,
        participant_fold_key,
        same_real_fixture,
        sport_sort_index,
    )
    from schedule_resolver import (
        DEFAULT_FIXTURE_AUTHORITY_SOURCES,
        DEFAULT_PROVIDER_EVENT_HOURS,
        attach_streams_to_fixtures,
        enrich_event_candidates,
        reuse_published_event_ids,
    )


DEFAULT_TODAY_MAX_AGE_HOURS = 12
DEFAULT_UPCOMING_PAST_GRACE_HOURS = 3
DEFAULT_UPCOMING_FUTURE_DAYS = 120

FAILED_STATUSES = {
    "failed",
    "failed_bd",
    "404_quarantined",
    "rejected_low_quality",
    "quarantine",
}

CONFIRMED_PLAYABLE_STATUSES = {
    "verified",
    "verified_global",
    "verified_proxy",
    "verified_bd",
}


def _utc_now_dt() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now() -> str:
    return _utc_now_dt().isoformat()


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


def _safe_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def _parse_datetime(
    value: Any,
    default_timezone: timezone | ZoneInfo = timezone.utc,
) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None

    # "18 Aug 2026, 10:00 PM (BD Time)" names its own timezone explicitly -
    # unlike the bare "7 PM BDT" case below, which is anchored to whatever
    # timezone the caller already supplies for this source, a feed that
    # states its offset must be read in that offset regardless of what the
    # caller passes, or every kickoff would be misread by up to six hours.
    explicit_bd_time = bool(
        re.search(r"(?i)\(\s*(?:BD\s*Time|BDT)\s*\)\s*$", text)
    )
    # A parenthetical timezone label, rather than the bare trailing
    # "BDT"/"UTC" token every pattern below already tolerates, would
    # otherwise defeat all of them at once.
    text = re.sub(r"(?i)\s*\((?:BD\s*Time|BDT|BST|UTC|GMT)\)\s*$", "", text).strip()
    if explicit_bd_time:
        default_timezone = timezone(timedelta(hours=6), name="BDT")

    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        parsed = None

    if parsed is None:
        for pattern in (
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d %I:%M:%S %p",
            "%Y-%m-%d %I:%M %p",
            "%d-%m-%Y %H:%M:%S",
            "%d-%m-%Y %H:%M",
            "%d-%m-%Y %I:%M:%S %p",
            "%d-%m-%Y %I:%M %p",
            "%d %b %Y, %H:%M:%S",
            "%d %b %Y, %H:%M",
            "%d %b %Y, %I:%M:%S %p",
            "%d %b %Y, %I:%M %p",
        ):
            try:
                parsed = datetime.strptime(text, pattern)
                break
            except ValueError:
                continue

    if parsed is None:
        relative_text = re.sub(r"(?i)\s*(?:BDT|BST|UTC|GMT)\s*$", "", text).strip()
        local_now = datetime.now(default_timezone)
        tomorrow_match = re.fullmatch(
            r"(?i)tomorrow\s+(\d{1,2}(?::\d{2})?(?:\s*[AP]M)?)",
            relative_text,
        )
        if tomorrow_match:
            clock_text = tomorrow_match.group(1).strip()
            for pattern in ("%I:%M %p", "%I %p", "%H:%M"):
                try:
                    clock = datetime.strptime(clock_text, pattern).time()
                    parsed = datetime.combine(
                        (local_now + timedelta(days=1)).date(),
                        clock,
                        tzinfo=default_timezone,
                    )
                    break
                except ValueError:
                    continue

        if parsed is None:
            for pattern in ("%a, %b %d %I:%M %p", "%a, %b %d %I %p", "%b %d %I:%M %p"):
                try:
                    partial = datetime.strptime(relative_text, pattern)
                    candidate = partial.replace(year=local_now.year, tzinfo=default_timezone)
                    if candidate < local_now - timedelta(days=30):
                        candidate = candidate.replace(year=local_now.year + 1)
                    parsed = candidate
                    break
                except ValueError:
                    continue

    if parsed is None:
        # Daily sports feeds commonly provide only "7 PM BDT" or
        # "Live at 11:30 PM BDT". Anchor those values to today's date in the
        # configured source timezone so freshness and ordering remain useful.
        time_only = re.sub(r"(?i)^\s*live\s+at\s+", "", text)
        time_only = re.sub(r"(?i)\s*(?:BDT|BST|UTC|GMT)\s*$", "", time_only).strip()
        for pattern in ("%I:%M:%S %p", "%I:%M %p", "%I %p", "%H:%M:%S", "%H:%M"):
            try:
                clock = datetime.strptime(time_only, pattern).time()
                local_now = datetime.now(default_timezone)
                parsed = datetime.combine(local_now.date(), clock, tzinfo=default_timezone)
                break
            except ValueError:
                continue

    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=default_timezone)
    return parsed.astimezone(timezone.utc)


def _sort_time(
    value: Any,
    default_timezone: timezone | ZoneInfo = timezone.utc,
) -> str:
    parsed = _parse_datetime(value, default_timezone)
    if parsed is None:
        return "9999-12-31T23:59:59+00:00"
    return parsed.isoformat()


def _event_sort_key(
    item: Dict[str, Any],
    default_timezone: timezone | ZoneInfo = timezone.utc,
) -> Tuple[int, str, str, str]:
    # Requirement 11. Cricket first, football second, every other sport after -
    # then the existing kickoff/competition/name order inside each group.
    sport_rank = sport_sort_index(item.get("sport_type") or event_sport(item))
    start_time = _sort_time(item.get("start_time"), default_timezone)
    competition = re.sub(
        r"\s+",
        " ",
        str(item.get("competition") or "").strip(),
    ).casefold()
    name = re.sub(
        r"\s+",
        " ",
        str(item.get("name") or "").strip(),
    ).casefold()
    return sport_rank, start_time, competition, name


def _primary_url(item: Dict[str, Any]) -> str:
    for key in ("url", "stream_url", "link"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return ""


def _backup_urls(item: Dict[str, Any]) -> List[str]:
    backups = item.get("backups")
    if not isinstance(backups, list):
        return []

    urls: List[str] = []
    for backup in backups[:5]:
        if isinstance(backup, str):
            value = backup.strip()
        elif isinstance(backup, dict):
            value = str(
                backup.get("url")
                or backup.get("stream_url")
                or backup.get("link")
                or ""
            ).strip()
        else:
            value = ""
        if value:
            urls.append(value)
    return urls


def _is_playable(item: Dict[str, Any]) -> bool:
    if item.get("metadata_only") is True:
        return False
    if item.get("publish_allowed") is False:
        return False
    status = str(item.get("verification_status") or "").strip().lower()
    if status in FAILED_STATUSES:
        return False
    # An URL, or even an HTTP 200 response, is not enough for Today Match.
    # These verified states are assigned only after protocol-aware validation;
    # for HLS that includes a readable media playlist and media segment.
    confirmed = item.get("verified") is True or item.get("is_valid") is True
    if not confirmed or status not in CONFIRMED_PLAYABLE_STATUSES:
        return False
    return bool(_primary_url(item) or _backup_urls(item))


def _is_today_fresh(
    item: Dict[str, Any],
    now: datetime,
    max_age_hours: int,
) -> bool:
    schedule_status = str(
        item.get("schedule_status") or item.get("status") or ""
    ).strip().upper()
    if schedule_status == "ENDED":
        return False

    end_time = _parse_datetime(
        item.get("end_time"),
        item.get("_source_timezone", timezone.utc),
    )
    # Today Match is a live surface, not a recent-results archive.  Remove a
    # card as soon as its authoritative fixture end_time is reached.
    if end_time is not None and end_time <= now:
        return False

    # An official multi-day fixture remains current until its authoritative
    # end time.  The generic age guard below is only a fallback for feeds that
    # do not carry a verified schedule.
    if item.get("schedule_verified") is True and end_time is not None:
        return True

    start_time = _parse_datetime(
        item.get("start_time"),
        item.get("_source_timezone", timezone.utc),
    )
    if start_time is None:
        return True

    if start_time > now + timedelta(hours=6):
        return False
    if start_time < now - timedelta(hours=max_age_hours):
        return False
    return True


def _is_upcoming_fresh(
    item: Dict[str, Any],
    now: datetime,
    past_grace_hours: int,
    future_days: int,
) -> bool:
    start_time = _parse_datetime(
        item.get("start_time"),
        item.get("_source_timezone", timezone.utc),
    )
    if start_time is None:
        return True
    if start_time < now - timedelta(hours=past_grace_hours):
        return False
    if start_time > now + timedelta(days=future_days):
        return False
    return True


#: Kept as module names because tests and older callers import them from here.
#: The rule itself now lives in scanner/event_lifecycle.py so that the merge can
#: group by the same destination this function routes to - see event_destination.
LIVE_SCHEDULE_STATUSES = ROUTE_LIVE_STATUSES
UPCOMING_SCHEDULE_STATUSES = ROUTE_UPCOMING_STATUSES

#: Grouping and routing must agree, so there is exactly one implementation.
_destination_for = event_destination


def _stamp_final_routing(card: Dict[str, Any], destination: str) -> None:
    """Make the published fields agree with where the card actually went.

    `category` and `source_pipeline` were copied from the feed the candidate
    arrived in and then never revisited, while `_destination_for` routes on the
    schedule status. A live fixture configured under an "upcoming" feed therefore
    published into Today Match still labelled `category: "upcoming"` - so the file
    it sits in and the field describing it disagreed, and any consumer trusting
    the field put the card in the wrong tab.

    Provenance is not lost: the feed the candidate came from stays available as
    `original_source_pipeline` and in `source_provenance`/`source_ids`.
    """
    original = str(
        card.get("original_source_pipeline") or card.get("source_pipeline") or ""
    ).strip().lower()
    if original:
        card["original_source_pipeline"] = original
    card["category"] = destination
    card["source_pipeline"] = destination
    card["event_type"] = destination
    if original and original != destination:
        card["routing_changed_from"] = original
        card["routing_reason"] = "schedule_status_routing"


#: Section 12's honest-fallback naming, given its own brand rather than the
#: backend-plumbing "Server-1"/"Streamed-1" a viewer has no reason to
#: recognise. Chosen and ordered by direct request. Sorted best-quality-first
#: exactly like the numbers they replace, so this only reads sensibly for as
#: long as that ordering promise holds.
GENERIC_CHANNEL_NAMES: Tuple[str, ...] = (
    "Click Live", "Click Plus", "Click Max", "Click Ultra",
    "Click Prime", "Click Pro", "Click Edge", "Click X",
    "Click One", "Click Go", "Click Now", "Click Play",
)


def _generic_channel_label(index: int) -> str:
    if 0 <= index < len(GENERIC_CHANNEL_NAMES):
        return GENERIC_CHANNEL_NAMES[index]
    # More generic channels on one card than the named series covers is not
    # expected in practice, but a card must still publish something rather
    # than run out of names.
    return f"Click {index + 1}"


def _relabel_generic_channels(card: Dict[str, Any]) -> None:
    """Rename every honest-fallback channel to its place in the shared brand.

    channels[] is already in final publish order by the time this runs -
    ordering, carried-card absorption and embed appending have all already
    happened - so a channel whose real broadcaster could not be named is
    simply relabelled in the order it already appears: native generic slots
    ahead of provider-embed ones, exactly as published, sharing one sequence
    rather than two separately-numbered ones.
    """
    channels = card.get("channels")
    if not isinstance(channels, list):
        return
    index = 0
    for channel in channels:
        if not isinstance(channel, dict):
            continue
        if str(channel.get("name_confidence")) != "generic":
            continue
        label = _generic_channel_label(index)
        channel["name"] = label
        channel["normalized_name"] = label.casefold().replace(" ", "-")
        index += 1


def _payload(
    items: List[Dict[str, Any]],
    event_type: str,
    filtered_stale: int,
    filtered_unplayable: int,
    source_timezone: timezone | ZoneInfo = timezone.utc,
    allowed_sports: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    candidates = [item for item in items if isinstance(item, dict)]
    for item in candidates:
        if not str(item.get("sport_type") or "").strip():
            item["sport_type"] = event_sport(item)
    if allowed_sports is not None:
        allowed_set = {str(s).strip().lower() for s in allowed_sports if str(s).strip()}
        candidates = [
            item for item in candidates
            if str(item.get("sport_type") or "").lower() in allowed_set
        ]
    ordered = sorted(
        candidates,
        key=lambda item: _event_sort_key(item, source_timezone),
    )
    for item in ordered:
        item.pop("_source_timezone", None)
        _relabel_generic_channels(item)
    return {
        "type": event_type,
        "updated_at": _utc_now(),
        "count": len(ordered),
        "filtered_stale": filtered_stale,
        "filtered_unplayable": filtered_unplayable,
        "items": ordered,
    }


def _stamp_channel_names(candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Sections 6 and 11. Record each stream's broadcaster before the title changes.

    The schedule resolver rewrites a candidate's name to the canonical fixture
    title, which is what makes one match one card - and which also deletes the
    broadcaster the source had appended to it. "Al Nassr Vs Al Fateh FANCODE"
    becomes "Al Nassr vs Al Fateh", and by merge time there is nothing left to
    tell FANCODE from FOX DEPORTES.

    So the channel is resolved here, on the raw titles, and written to the
    explicit `channel_name` field - which is priority 1 of section 11's order, so
    every later stage simply reads it instead of guessing again.
    """
    try:
        from scanner.channel_resolver import load_alias_map, resolve_channel_name
    except Exception:  # pragma: no cover - optional layer
        return {"stamped": 0, "unresolved": 0}

    aliases = load_alias_map()
    stats = {"stamped": 0, "unresolved": 0, "names": {}}
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        if str(candidate.get("channel_name") or "").strip():
            continue
        try:
            channel = resolve_channel_name(candidate, candidate.get("name"), aliases)
        except Exception:  # pragma: no cover - never break a scan over a name
            continue
        if not channel.resolved:
            stats["unresolved"] += 1
            continue
        candidate["channel_name"] = channel.name
        candidate["channel_normalized_name"] = channel.normalized
        candidate["channel_name_confidence"] = channel.confidence
        candidate["channel_name_source"] = channel.source_field
        stats["stamped"] += 1
        stats["names"][channel.name] = int(stats["names"].get(channel.name, 0)) + 1
    return stats


def _streamed_enrichment(
    settings: Dict[str, Any],
    reference_now: datetime,
    targeted_window_minutes: int,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Sections 22-25 and 31-33. What Streamed adds to this scan, if anything.

    Returns (fixture candidates, report). Every failure path returns an empty
    list: section 32 requires that an unavailable or slow provider leaves the
    existing GitHub/native pipeline behaving exactly as it does without it, so
    nothing here is allowed to raise or to block.
    """
    try:
        from scanner.streamed_provider import (
            collect_streamed_candidates,
            write_health,
        )
    except Exception as error:  # pragma: no cover - optional layer
        return [], {"available": False, "reason": f"module unavailable: {error}"}

    try:
        candidates, health = collect_streamed_candidates(
            settings,
            targeted_window_minutes=targeted_window_minutes,
            now=reference_now,
        )
        try:
            write_health(health)
        except Exception:  # pragma: no cover - reporting only
            pass
        return candidates, health.report()
    except Exception as error:  # pragma: no cover - never break a scan
        return [], {"available": False, "reason": f"unexpected: {type(error).__name__}"}


def _append_embed_channels(card: Dict[str, Any]) -> int:
    """Sections 19/26/27. A provider feed is a channel too - listed last.

    The provider's feeds are the reason an event can have several selectable
    broadcasters at all when the native playlists only carry one. They are built
    with the same channel builder as the native side so a reader sees one
    structure, and then appended strictly behind every native channel.

    Section 27 is enforced by construction here: the embed channels go on the
    end, the existing default_channel_id is not touched while any native channel
    exists, and nothing already in channels[] is reordered or removed. So a
    healthy native primary is never demoted by a provider answering.
    """
    embeds = card.get("embed_backups")
    if not isinstance(embeds, list) or not embeds:
        return 0
    try:
        from scanner.channel_groups import build_event_channels, default_channel_id
    except Exception:  # pragma: no cover - optional layer
        return 0

    existing = card.get("channels")
    existing = list(existing) if isinstance(existing, list) else []

    try:
        embed_channels, _ = build_event_channels(
            str(card.get("id") or ""),
            str(card.get("name") or ""),
            [
                {
                    **entry,
                    "renderer": "embed",
                    "name": entry.get("name"),
                    "channel_name": entry.get("name"),
                    "source_id": f"streamed:{entry.get('provider') or 'streamed'}",
                }
                for entry in embeds
                if isinstance(entry, dict)
            ],
            aliases={},
        )
    except Exception:  # pragma: no cover - a provider must never break a scan
        return 0

    # The provider's own embed API carries no broadcaster field at all - only
    # an internal server key, a stream number and a URL - so "name" here is
    # always the same honest placeholder normalize_embed_streams() built
    # ("Streamed 1", "Streamed 2"...), never a genuinely resolved broadcaster.
    # Passing it through as an explicit channel_name earns it "explicit"
    # confidence from the resolver, same as a real name would get, which
    # keeps it out of the generic-channel relabelling pass below. It is
    # exactly the case that pass exists for, so it is marked as such here.
    for channel in embed_channels:
        if isinstance(channel, dict):
            channel["name_confidence"] = "generic"
            channel["name_source"] = "generic"

    if not embed_channels:
        return 0

    # A card carried forward scan after scan (a long-running Test match the
    # live playlist did not re-list this round) keeps whatever channels[] it
    # already published, embed entries included - so an id already present
    # here used to mean "skip it, it is already there", which also skipped
    # ever refreshing it. That silently froze a stale embed channel's fields
    # in place forever, including the "explicit"-confidence bug above: a
    # card that got its embed channels before that fix landed kept showing
    # them under their old raw "Streamed 1"/"Streamed 2" names on every
    # later scan, because this function never got a chance to re-mark them.
    # An id already published is refreshed with this scan's rebuild instead
    # of being left untouched; only a genuinely new id is appended.
    fresh_by_id = {
        str(channel.get("id") or ""): channel
        for channel in embed_channels
        if isinstance(channel, dict) and channel.get("id")
    }
    refreshed = 0
    merged: List[Dict[str, Any]] = []
    for channel in existing:
        channel_id = str(channel.get("id") or "") if isinstance(channel, dict) else ""
        if channel_id in fresh_by_id:
            merged.append(fresh_by_id.pop(channel_id))
            refreshed += 1
        else:
            merged.append(channel)
    added = list(fresh_by_id.values())
    merged.extend(added)

    if not added and not refreshed:
        return 0

    card["channels"] = merged
    card["channel_count"] = len(card["channels"])
    card["embed_channel_count"] = sum(
        1 for channel in card["channels"]
        if isinstance(channel, dict) and str(channel.get("renderer") or "") == "embed"
    )

    # Section 27, the part that is easy to get wrong. "No native channel" is not
    # the same as "no native stream": section 12 refuses to name a broadcaster it
    # cannot identify, so a card can have a perfectly healthy native primary and
    # still no native entry in channels[]. Making an embed the default there
    # would reorder the playback plan and demote that primary - exactly the
    # demotion section 27 forbids. So the default is only offered to an embed
    # when the card has no native stream of its own to play.
    has_native_primary = bool(
        str(card.get("playback_id") or "").strip()
        or str(card.get("url") or "").strip()
    ) and card.get("metadata_only") is not True
    if not str(card.get("default_channel_id") or "").strip() and not has_native_primary:
        card["default_channel_id"] = default_channel_id(card["channels"])
    return len(added)


def _apply_streamed_enrichment(
    cards: List[Dict[str, Any]],
    provider_candidates: List[Dict[str, Any]],
    attach_embed_streams: bool = False,
) -> Dict[str, Any]:
    """Attach provider artwork and embed backups to the canonical cards.

    Matching is by the existing canonical event key, so section 23 holds: the
    provider's own match id never becomes the Click TV event_id, it only points
    at the fixture the existing matcher already resolved.

    Section 27: an embed lands in embed_backups[], strictly after every native
    option, and never inside backups[] - those are native URLs the player and the
    proxy Worker already know how to handle, and a healthy native primary is
    never demoted because a provider answered.
    """
    if not provider_candidates:
        return {"matched": 0, "artwork": 0, "embed_backups": 0,
                "embed_channels": 0, "unmatched": 0}

    by_key: Dict[str, Dict[str, Any]] = {}
    # Sections 1/25, together. Matching on the plain name key alone meant the
    # provider's "Sri Lanka vs India" never met the card the catalogue names
    # "Sri Lanka vs India 1st Test", and the sides being listed the other way round
    # missed too - so a fixture the provider had a poster, badges and an embed for
    # published with none of them. The participants-only fold key is the same
    # weaker second opinion the merge uses, and it is confirmed the same way:
    # same_real_fixture still has to agree on sport, competition and kickoff before
    # anything is attached.
    by_fold: Dict[str, List[Dict[str, Any]]] = {}
    for candidate in provider_candidates:
        key = normalize_event_key(candidate.get("name", ""))
        if key:
            by_key.setdefault(key, candidate)
        fold = participant_fold_key(candidate)
        if fold:
            by_fold.setdefault(fold, []).append(candidate)

    def provider_for(card: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        exact = by_key.get(normalize_event_key(card.get("name", "")))
        if exact is not None:
            return exact
        fold = participant_fold_key(card)
        if not fold:
            return None
        # Only one provider fixture may answer, and it must pass the merge's own
        # identity test. Two answers means the participants are ambiguous today.
        agreeing = [
            candidate for candidate in by_fold.get(fold, [])
            if same_real_fixture(card, candidate)
        ]
        return agreeing[0] if len(agreeing) == 1 else None

    stats = {"matched": 0, "artwork": 0, "embed_backups": 0,
             "embed_channels": 0, "unmatched": 0, "matched_by_participants": 0}
    used: set = set()
    for card in cards:
        key = normalize_event_key(card.get("name", ""))
        provider = provider_for(card)
        if provider is None:
            continue
        if by_key.get(key) is not provider:
            stats["matched_by_participants"] += 1
        used.add(key)
        used.add(normalize_event_key(provider.get("name", "")))
        stats["matched"] += 1
        card["provider_enriched"] = "streamed"
        card["provider_event_id"] = str(provider.get("provider_event_id") or "")

        # Section 25. Provider badges/posters go in front of the existing artwork
        # chain as candidates to try, never as a replacement for it.
        artwork = provider.get("provider_artwork")
        if isinstance(artwork, list) and artwork:
            existing = card.get("artwork_candidates")
            merged = list(artwork) + (existing if isinstance(existing, list) else [])
            seen: set = set()
            card["artwork_candidates"] = [
                url for url in merged
                if str(url).strip() and not (str(url) in seen or seen.add(str(url)))
            ]
            stats["artwork"] += 1

        # Section 10. The two team badges and the event poster, named separately so
        # the card can draw "home badge VS away badge" instead of two initials.
        # Only filled in where the card has nothing of its own: a poster the
        # playlist already supplied is the fixture's own artwork and stays.
        for field in ("provider_poster_url", "home_badge_url", "away_badge_url"):
            value = str(provider.get(field) or "").strip()
            if value and not str(card.get(field) or "").strip():
                card[field] = value
        if not str(card.get("logo") or "").strip():
            poster = str(provider.get("provider_poster_url") or "").strip()
            if poster:
                card["logo"] = poster
                stats["poster_filled"] = int(stats.get("poster_filled", 0)) + 1

        if attach_embed_streams:
            embeds = provider.get("provider_embed_streams")
            if isinstance(embeds, list) and embeds:
                card["embed_backups"] = [
                    {
                        "name": str(entry.get("name") or "Streamed"),
                        "provider": str(entry.get("provider") or "streamed"),
                        "playback_type": "embed",
                        "embed_url": str(entry.get("embed_url") or ""),
                        "language": str(entry.get("language") or ""),
                        "hd": bool(entry.get("hd")),
                        "verification_status": "provider_embed",
                        "verified": False,
                    }
                    for entry in embeds
                    if str(entry.get("embed_url") or "").strip()
                ]
                card["embed_backup_count"] = len(card["embed_backups"])
                stats["embed_backups"] += len(card["embed_backups"])
                stats["embed_channels"] += _append_embed_channels(card)

        # Section 24: the provider is a routing hint, never a status authority.
        hint = str(provider.get("provider_routing_hint") or "")
        if hint:
            card["provider_routing_hint"] = hint

    stats["unmatched"] = len([k for k in by_key if k not in used])
    return stats


def _apply_supplementary_sports_artwork(cards: List[Dict[str, Any]]) -> Dict[str, Any]:
    """TheSportsDB/Highlightly/Sportmonks, tried only where Streamed left a
    card with no poster of its own at all.

    Unlike Streamed, none of these three are matched against a fetched
    catalogue - TheSportsDB's and Sportmonks' lookups are genuine team-name
    searches, and Highlightly has no name search at all (matched by pulling
    one date's matches and comparing team names, same as here). The two
    sides are read off the card's own title with the same participants-only
    split the merge itself uses (team_pair_key), so nothing here needs a
    second identity check the way Streamed's fold-key match does.
    """
    stats = {"attempted": 0, "poster_filled": 0, "badge_filled": 0}
    try:
        from scanner.schedule_resolver import team_pair_key
        from scanner.sports_poster_providers import (
            thesportsdb_event_artwork,
            highlightly_match_artwork,
        )
    except Exception:  # pragma: no cover - optional layer
        return stats

    for card in cards:
        if not isinstance(card, dict):
            continue
        has_poster = bool(
            str(card.get("logo") or "").strip()
            or str(card.get("provider_poster_url") or "").strip()
        )
        has_badges = bool(
            str(card.get("home_badge_url") or "").strip()
            and str(card.get("away_badge_url") or "").strip()
        )
        if has_poster and has_badges:
            continue

        pair = team_pair_key(str(card.get("name") or ""))
        if "|" not in pair:
            continue
        home_team, away_team = pair.split("|", 1)
        stats["attempted"] += 1

        try:
            artwork = thesportsdb_event_artwork(home_team, away_team)
        except Exception:  # pragma: no cover - never break a scan
            artwork = {}
        if not artwork:
            sport = str(card.get("sport_type") or "football")
            start = str(card.get("start_time") or "")
            date = start[:10] if len(start) >= 10 and start[4] == "-" else ""
            try:
                artwork = highlightly_match_artwork(home_team, away_team, sport, date)
            except Exception:  # pragma: no cover - never break a scan
                artwork = {}

        if not artwork:
            continue

        best_poster = artwork.get("poster") or artwork.get("thumbnail") or artwork.get("banner") or ""
        if best_poster and not has_poster:
            card.setdefault("provider_poster_url", best_poster)
            if not str(card.get("logo") or "").strip():
                card["logo"] = best_poster
            stats["poster_filled"] += 1

        if artwork.get("home_badge") and not str(card.get("home_badge_url") or "").strip():
            card["home_badge_url"] = artwork["home_badge"]
            stats["badge_filled"] += 1
        if artwork.get("away_badge") and not str(card.get("away_badge_url") or "").strip():
            card["away_badge_url"] = artwork["away_badge"]
            stats["badge_filled"] += 1

    return stats


def _authority_states(
    candidates: List[Dict[str, Any]],
    previous_items: List[Dict[str, Any]],
) -> Dict[str, Optional[bool]]:
    """Section 21. What the fixture authority says in THIS scan, per event id.

    A carried-forward card still holds the status its last successful scan wrote,
    which is a memory rather than a statement. So the verdict is taken from this
    scan's enriched candidates - which include fixtures the schedule resolver
    knows about even when no stream was found for them - and matched to the
    previously published cards by id and by normalized name. An event nothing in
    this scan mentions has no verdict at all, and section 21 treats that as
    "authority unavailable" rather than "finished".
    """
    by_id: Dict[str, Optional[bool]] = {}
    by_name: Dict[str, Optional[bool]] = {}
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        verdict = authority_says_live(candidate)
        if verdict is None:
            continue
        event_id = str(candidate.get("id") or "")
        if event_id:
            by_id.setdefault(event_id, verdict)
        key = normalize_event_key(candidate.get("name", ""))
        if key:
            by_name.setdefault(key, verdict)

    states: Dict[str, Optional[bool]] = {}
    for previous in previous_items:
        if not isinstance(previous, dict):
            continue
        event_id = str(previous.get("id") or "")
        if not event_id:
            continue
        if event_id in by_id:
            states[event_id] = by_id[event_id]
            continue
        key = normalize_event_key(previous.get("name", ""))
        if key and key in by_name:
            states[event_id] = by_name[key]
    return states


def _playing_event_ids(path: str | Path = "state/playing-sessions.json") -> set:
    """Section 21. The events a viewer is watching right now.

    The frontend writes this when playback starts and clears it when playback
    stops; the file is optional and an unreadable one simply means "nobody is
    known to be watching", which costs nothing because every other protection
    still applies.
    """
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return set()
    values: Any = payload.get("event_ids") if isinstance(payload, dict) else payload
    if not isinstance(values, list):
        return set()
    return {str(value).strip() for value in values if str(value).strip()}


def process_events(
    bd_results_path: str = "working/bd-results.json",
    settings_path: str = "config/settings.json",
    fixture_path: str = "config/event-fixtures.json",
    *,
    now: Optional[datetime] = None,
    targeted_window_minutes: int = 0,
    targeted_keys: Optional[set] = None,
) -> Dict[str, Dict[str, Any]]:
    """Return freshness-checked today_match and upcoming payloads.

    targeted_window_minutes implements requirement 4: with a window set, only
    fixtures kicking off inside it are treated as scan targets. Every other
    future fixture keeps the card it already has, so a five-minute trigger can
    chase the links of the matches about to start without re-verifying a
    hundred fixtures that are still hours away.

    targeted_keys narrows that further, and is the corrected behaviour: the
    caller has already decided which individual fixtures this trigger may work
    on, having excluded the ones that produced a valid link on an earlier tick.
    A fixture inside the window but absent from targeted_keys is therefore left
    exactly as published - it is not re-scanned every five minutes.
    """
    results = _load_required_results(bd_results_path)
    settings = _load_optional_json(settings_path)
    event_settings = settings.get("events") if isinstance(settings.get("events"), dict) else {}
    fixture_path = str(event_settings.get("fixture_catalogue") or fixture_path)
    timezone_name = str(
        event_settings.get("timezone")
        or settings.get("timezone")
        or "UTC"
    ).strip()
    try:
        source_timezone: timezone | ZoneInfo = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        source_timezone = (
            timezone(timedelta(hours=6), name="BDT")
            if timezone_name.casefold() in {"asia/dhaka", "bdt"}
            else timezone.utc
        )


    today_max_age_hours = _safe_int(
        event_settings.get("today_max_age_hours"),
        DEFAULT_TODAY_MAX_AGE_HOURS,
        2,
        48,
    )
    upcoming_past_grace_hours = _safe_int(
        event_settings.get("upcoming_past_grace_hours"),
        DEFAULT_UPCOMING_PAST_GRACE_HOURS,
        0,
        24,
    )
    upcoming_future_days = _safe_int(
        event_settings.get("upcoming_future_days"),
        DEFAULT_UPCOMING_FUTURE_DAYS,
        1,
        365,
    )

    raw_event_candidates = [
        dict(item)
        for item in results
        if str(item.get("source_pipeline") or "").strip().lower()
        in {"today_match", "upcoming"}
    ]

    configured_authority = event_settings.get("fixture_authority_sources")
    authority_source_ids = (
        {str(value).strip() for value in configured_authority if str(value).strip()}
        if isinstance(configured_authority, list)
        else None
    )
    provider_event_hours = _safe_int(
        event_settings.get("provider_event_hours"),
        DEFAULT_PROVIDER_EVENT_HOURS,
        1,
        24,
    )

    reference_now = now or _utc_now_dt()

    # Sections 22-23. Streamed fixtures join the same unified candidate pool and
    # go through the same canonical matcher as everything else. They are metadata
    # only, so they can enrich a fixture but never contribute a native stream.
    streamed_candidates, streamed_report = _streamed_enrichment(
        settings, reference_now, targeted_window_minutes
    )
    if streamed_candidates:
        raw_event_candidates = raw_event_candidates + [
            dict(candidate) for candidate in streamed_candidates
        ]

    # Sections 6/11. Resolve the broadcaster while the raw titles still carry it.
    channel_stamp_stats = _stamp_channel_names(raw_event_candidates)

    # Sections 6/11 root cause. The enrichment gate correctly refuses to let a
    # stream-only playlist entry publish an event card of its own, but it used to
    # delete the candidate as well - and those entries are exactly the ones whose
    # titles carry a broadcaster. They were destroyed one stage before the stage
    # that exists to marry a stream to a fixture, so a card and its channels were
    # both in the scan and never met. The pool keeps them alive without letting
    # any of them past the gate.
    attachment_pool: List[Dict[str, Any]] = []

    event_candidates, schedule_stats = enrich_event_candidates(
        raw_event_candidates,
        fixture_path=fixture_path,
        timezone_name=timezone_name,
        now=reference_now,
        future_days=upcoming_future_days,
        authority_source_ids=authority_source_ids,
        provider_event_hours=provider_event_hours,
        attachment_pool=attachment_pool,
    )

    # Guide 30.7: the fixture exists first, then a matching stream is attached
    # to it. Without this an Upcoming card and the stream that could play it
    # stay in separate groups and the card publishes with nothing to play.
    event_candidates, attach_stats = attach_streams_to_fixtures(
        event_candidates,
        authority_source_ids or set(DEFAULT_FIXTURE_AUTHORITY_SOURCES),
        attachment_pool=attachment_pool,
    )
    schedule_stats.update(attach_stats)

    merged = merge_candidates(
        event_candidates,
        settings_path=settings_path,
        # Requirement 16: a healthy primary keeps its place across scans.
        previous_primary_keys=load_previous_primary_keys(),
    )

    # Guide 30.8: an event that moved from Upcoming to Today Match keeps the
    # card it already had rather than appearing as a new one.
    schedule_stats["reused_event_ids"] = reuse_published_event_ids(merged)

    now = reference_now
    skip_live_protection = False
    today_items: List[Dict[str, Any]] = []
    upcoming_items: List[Dict[str, Any]] = []
    today_stale = 0
    today_unplayable = 0
    upcoming_stale = 0

    for card in merged:
        if not isinstance(card, dict):
            continue

        card_copy = dict(card)
        card_copy["_source_timezone"] = source_timezone
        pipeline = _destination_for(card_copy)

        if pipeline == "today_match":
            if not _is_playable(card_copy):
                today_unplayable += 1
                continue
            if not _is_today_fresh(card_copy, now, today_max_age_hours):
                today_stale += 1
                continue
            _stamp_final_routing(card_copy, "today_match")
            card_copy["status"] = str(
                card_copy.get("schedule_status")
                or card_copy.get("status")
                or ("CHANNEL_LIVE" if card_copy.get("today_source_channel") else "LIVE_NOW")
            )
            # Section 21's lifecycle, stamped on a card this scan actually saw.
            card_copy["lifecycle_state"] = classify_state(card_copy, now)
            today_items.append(card_copy)

        elif pipeline == "upcoming":
            if not _is_upcoming_fresh(
                card_copy,
                now,
                upcoming_past_grace_hours,
                upcoming_future_days,
            ):
                upcoming_stale += 1
                continue
            _stamp_final_routing(card_copy, "upcoming")
            card_copy["status"] = str(
                card_copy.get("schedule_status") or card_copy.get("status") or "UPCOMING"
            )
            card_copy["lifecycle_state"] = classify_state(card_copy, now)
            upcoming_items.append(card_copy)

    # Requirement 4, corrected. A targeted trigger publishes the snapshot it
    # already had, with only its targeted fixtures refreshed.
    #
    # This has to start from what is published rather than from what this scan
    # produced. A targeted scan deliberately verifies a handful of candidates,
    # so its merged output only ever contains the targeted fixtures - filtering
    # that output would silently drop every other Upcoming card. Untargeted
    # fixtures are therefore copied through verbatim, still freshness-checked so
    # a finished match cannot linger, and a target that yielded nothing this time
    # keeps the card it already had.
    if targeted_window_minutes > 0:
        horizon = reference_now + timedelta(minutes=targeted_window_minutes)
        previous_upcoming_payload = _load_optional_json(Path("data") / "upcoming.json")
        previous_upcoming_items = [
            item
            for item in (previous_upcoming_payload.get("items") or [])
            if isinstance(item, dict)
        ]

        refreshed: Dict[str, Dict[str, Any]] = {}
        targeted = 0
        skipped_already_scanned = 0
        skipped_outside_window = 0
        for card in upcoming_items:
            start = _parse_datetime(card.get("start_time"), source_timezone)
            in_window = bool(start and reference_now <= start <= horizon)
            if not in_window:
                skipped_outside_window += 1
                continue
            if targeted_keys is not None and fixture_key(card) not in targeted_keys:
                skipped_already_scanned += 1
                continue
            targeted += 1
            refreshed[str(card.get("id") or "")] = card

        kept: List[Dict[str, Any]] = []
        seen_ids: set = set()
        for previous in previous_upcoming_items:
            event_id = str(previous.get("id") or "")
            seen_ids.add(event_id)
            fresh = refreshed.get(event_id)
            if fresh is not None:
                kept.append(fresh)
                continue
            carried = dict(previous)
            carried["_source_timezone"] = source_timezone
            if not _is_upcoming_fresh(
                carried, now, upcoming_past_grace_hours, upcoming_future_days
            ):
                upcoming_stale += 1
                continue
            kept.append(carried)
        for event_id, fresh in refreshed.items():
            if event_id not in seen_ids:
                kept.append(fresh)

        upcoming_items = kept
        schedule_stats["targeted_window_minutes"] = targeted_window_minutes
        schedule_stats["targeted_fixtures"] = targeted
        schedule_stats["targeted_skipped_already_scanned"] = skipped_already_scanned
        schedule_stats["targeted_skipped_outside_window"] = skipped_outside_window
        schedule_stats["targeted_carried_published_cards"] = len(
            [card for card in kept if str(card.get("id") or "") not in refreshed]
        )
        schedule_stats["targeted_keys_supplied"] = (
            -1 if targeted_keys is None else len(targeted_keys)
        )
        schedule_stats["targeted_resolved_now"] = sum(
            1 for card in refreshed.values() if has_valid_link(card)
        )

        # Today Match is not this trigger's business either. Its published cards
        # are copied through untouched - no re-verification and no liveness
        # probing, both of which belong to the Today Match scan - except that a
        # targeted fixture whose link arrived right at kickoff is allowed to
        # promote into it, because that promotion IS the work being done.
        previous_today_payload = _load_optional_json(Path("data") / "today-match.json")
        previous_today_published = [
            item
            for item in (previous_today_payload.get("items") or [])
            if isinstance(item, dict)
        ]
        promoted = {
            str(card.get("id") or ""): card
            for card in today_items
            if targeted_keys is None or fixture_key(card) in targeted_keys
        }
        kept_today: List[Dict[str, Any]] = []
        seen_today: set = set()
        for previous in previous_today_published:
            event_id = str(previous.get("id") or "")
            seen_today.add(event_id)
            kept_today.append(promoted.get(event_id) or dict(previous))
        for event_id, card in promoted.items():
            if event_id not in seen_today:
                kept_today.append(card)
        schedule_stats["targeted_promoted_to_today"] = len(
            [key for key in promoted if key not in seen_today]
        )
        today_items = kept_today
        skip_live_protection = True

    # Requirement 6, corrected. A live event that this scan simply failed to
    # fetch is carried forward with its previous card rather than deleted, for
    # as many consecutive scans as it takes. Only an authoritative ENDED/FT, or
    # a probe proving every link on the card is dead, actually retires it.
    if skip_live_protection:
        # A targeted trigger already published Today Match verbatim. Running
        # protection here would probe cards this trigger never scanned and
        # retire them on behalf of a scan that did not look for them.
        schedule_stats["live_protection"] = {"skipped": "targeted scan"}
    else:
        previous_today = _load_optional_json(Path("data") / "today-match.json")
        previous_today_items = [
            item for item in (previous_today.get("items") or []) if isinstance(item, dict)
        ]
        today_items, protection_stats = protect_live_events(
            today_items,
            previous_today_items,
            probe=probe_card_is_playable,
            # Section 21: a fresh authority verdict, and the sessions a viewer is
            # watching - the strongest protection there is.
            authority_states=_authority_states(event_candidates, previous_today_items),
            playing_event_ids=_playing_event_ids(),
        )
        schedule_stats["live_protection"] = protection_stats

    attach_embed_streams = False
    events_cfg = settings.get("events")
    if isinstance(events_cfg, dict):
        attach_embed_streams = bool(events_cfg.get("attach_embed_streams", False))

    schedule_stats["channel_names"] = channel_stamp_stats
    schedule_stats["streamed_provider"] = streamed_report
    schedule_stats["streamed_enrichment"] = _apply_streamed_enrichment(
        today_items + upcoming_items, streamed_candidates, attach_embed_streams=attach_embed_streams
    )
    schedule_stats["sports_poster_enrichment"] = _apply_supplementary_sports_artwork(
        today_items + upcoming_items
    )

    allowed_sports = None
    if isinstance(events_cfg, dict):
        allowed_sports = events_cfg.get("allowed_sports")

    result = {
        "today_match": _payload(
            today_items,
            "today_match",
            filtered_stale=today_stale,
            filtered_unplayable=today_unplayable,
            source_timezone=source_timezone,
            allowed_sports=allowed_sports,
        ),
        "upcoming": _payload(
            upcoming_items,
            "upcoming",
            filtered_stale=upcoming_stale,
            filtered_unplayable=0,
            source_timezone=source_timezone,
            allowed_sports=allowed_sports,
        ),
    }
    # Requirement 3. One row per configured source, with the exact stage a
    # contribution was lost at and why.
    try:
        coverage = build_source_coverage(
            configured_sources=[
                {"id": source_id}
                for source_id in sorted({
                    str(c.get("source_id") or "")
                    for c in raw_event_candidates
                    if str(c.get("source_id") or "")
                })
            ],
            raw_candidates=raw_event_candidates,
            parsed_candidates=raw_event_candidates,
            matched_candidates=event_candidates,
            published_items=today_items + upcoming_items,
        )
        write_source_coverage(coverage)
        result_coverage = coverage
    except Exception as error:  # pragma: no cover - reporting must never break a scan
        result_coverage = {"error": str(error)}

    result["source_coverage"] = result_coverage
    result["schedule"] = {
        "timezone": timezone_name,
        **schedule_stats,
    }
    return result


if __name__ == "__main__":
    result = process_events()
    print(
        "Events processed: "
        f"today={result['today_match']['count']}, "
        f"upcoming={result['upcoming']['count']}, "
        f"today_stale={result['today_match']['filtered_stale']}, "
        f"today_unplayable={result['today_match']['filtered_unplayable']}, "
        f"time_corrected={result['schedule']['corrected']}"
    )
