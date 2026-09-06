"""A source that stopped answering must not delete the fixtures it published.

Measured on 2026-09-06. `sm-sports-data` answered HTTP 200 with an empty body
from 14:04:15Z onwards - the feed's own `total_matches` fell to 0 while forty
matches were being played - and stayed that way. Its Today Match cards survived,
because scanner/live_protection.py carries a live card whose source stopped
listing it. Its Upcoming cards had no such rule, so the first full scan after the
outage published `published_upcoming: 0` for it and 16 real fixtures left the
page in one commit. Twenty-six cards left the list; ten of them were the same
fixture republished under another feed's spelling, which the identity rules
relate, so sixteen is what was actually lost:

    14:09  upcoming-targeted   Upcoming 143   sm-sports-data pubU 36
    14:11  today               Upcoming 123   sm-sports-data pubU  0   <- here
    14:13  upcoming-targeted   Upcoming 143       (the targeted carry-through)
    14:29  today               Upcoming 121   and again

The oscillation is not a recovery. A targeted trigger republishes the snapshot it
already had, so it kept handing back a list assembled before the outage; the full
scan is the one that decides, and it decided to delete.

The rule here is deliberately narrow, and rests on one principle: **the source
that asserts a fixture is the only one that can withdraw it.** A feed that merely
contributed a stream to somebody else's fixture never had standing to say the
match is off, so its silence is not a removal - and a fixture is held only while
the feed that scheduled it is provably unable to speak.

Four bounds, so a hold can never become a haunting:

  * the authority must be *in outage*, which needs evidence on both sides: it
    returned nothing this scan AND it was returning something recently. A source
    that has always been empty protects nothing.
  * the hold is measured from the first scan that held the card and expires,
    whatever the source does afterwards.
  * the card is still checked against its own clock, exactly as an ordinary
    Upcoming card is, so a kickoff that passes retires it on schedule.
  * held cards re-enter the ordinary pipeline before the archive filter, the
    both-tabs filter, the duplicate fold and the sport filter, so a held card is
    subject to every rule a freshly scanned one is.

Nothing here names a source. The evidence is per-source health the scan already
records, and per-card provenance the card already carries.
"""
from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

DEFAULT_HEALTH_FILE = Path("state") / "source-health.json"

#: The event feeds. A channel or movie source cannot assert a fixture.
DEFAULT_PIPELINE = "today_match"

#: A health record older than this was not written by the scan asking the
#: question, so it is evidence about some earlier scan and is not read.
DEFAULT_RECORD_MAX_AGE_MINUTES = 45

#: How recently a source must have produced records for its silence to read as
#: an outage rather than as the way it always is. `0matbank-trysports-cricket-
#: live` and `sayanpal-sonyliv-backup` were both EMPTY on the same scan and
#: neither has published a fixture in days; they protect nothing, and should
#: not.
DEFAULT_MEMORY_HOURS = 6

#: How long one fixture may be held while its authority is silent. Three hours,
#: the same window scanner/live_protection.py already uses for the analogous
#: question on the Today tab (unscheduled_carry_hours), so the two halves of one
#: decision are not two different numbers.
DEFAULT_HOLD_MINUTES = 180

PRODUCTIVE = "PRODUCTIVE"
OUTAGE = "OUTAGE"
UNKNOWN = "UNKNOWN"

#: FINAL_3 part 3's vocabulary. EMPTY is `raw_items == 0` on an HTTP that
#: worked; UNREACHABLE is an HTTP that did not.
EMPTY = "EMPTY"
UNREACHABLE = "UNREACHABLE"

_SUCCESS_STATUSES = frozenset({"success", "success_empty"})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def parse_time(value: Any) -> Optional[datetime]:
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


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def load_health(path: Any = None) -> Dict[str, Any]:
    target = Path(path) if path is not None else DEFAULT_HEALTH_FILE
    try:
        with open(target, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    sources = payload.get("sources")
    return sources if isinstance(sources, dict) else {}


def read_source_states(
    health: Optional[Dict[str, Any]] = None,
    *,
    now: Optional[datetime] = None,
    path: Any = None,
    pipeline: str = DEFAULT_PIPELINE,
    since: Optional[datetime] = None,
    record_max_age_minutes: int = DEFAULT_RECORD_MAX_AGE_MINUTES,
    memory_hours: int = DEFAULT_MEMORY_HOURS,
) -> Dict[str, Dict[str, Any]]:
    """What each event source did in THIS scan: PRODUCTIVE, OUTAGE or UNKNOWN.

    UNKNOWN is the answer whenever the record does not belong to this scan, and
    it protects nothing: absence of evidence is not evidence of an outage, and
    the safe direction here is the behaviour that already existed.
    """
    records = health if health is not None else load_health(path)
    reference = now or _now()
    cutoff = since or (reference - timedelta(minutes=max(1, record_max_age_minutes)))
    memory_floor = reference - timedelta(hours=max(0, memory_hours))

    states: Dict[str, Dict[str, Any]] = {}
    for source_id, record in (records or {}).items():
        if not isinstance(record, dict):
            continue
        record_pipeline = str(record.get("pipeline") or "").strip().lower()
        if pipeline and record_pipeline and record_pipeline != pipeline:
            continue

        url = str(record.get("url") or "").strip()
        checked = parse_time(record.get("last_scan") or record.get("last_failure"))
        entry: Dict[str, Any] = {
            "source_id": str(source_id),
            "url": url,
            "state": UNKNOWN,
            "content_state": "",
            "reason": "",
            "checked_at": checked.isoformat() if checked else "",
            "last_productive": str(record.get("last_productive") or ""),
            "last_productive_items": _int(record.get("last_productive_items")),
        }
        states[str(source_id)] = entry

        if checked is None or checked < cutoff:
            entry["reason"] = "no health record from this scan"
            continue

        status = str(record.get("status") or "").strip().lower()
        raw_items = _int(record.get("raw_items"))
        if status == "disabled":
            entry["reason"] = "source disabled"
            continue
        if status in _SUCCESS_STATUSES and raw_items > 0:
            entry["state"] = PRODUCTIVE
            entry["reason"] = f"answered with {raw_items} record(s)"
            continue

        # Everything that reaches here produced nothing. The request either
        # worked and the body was empty, or it did not work at all.
        entry["content_state"] = (
            EMPTY if status in _SUCCESS_STATUSES else UNREACHABLE)
        productive_at = parse_time(record.get("last_productive"))
        if productive_at is None or entry["last_productive_items"] <= 0:
            entry["reason"] = (
                f"{entry['content_state'].lower()} this scan, and no scan on "
                "record ever saw it produce anything")
            continue
        if productive_at < memory_floor:
            entry["reason"] = (
                f"{entry['content_state'].lower()} this scan, but it last "
                f"produced records at {productive_at.isoformat()}, beyond the "
                f"{memory_hours}h memory")
            continue

        entry["state"] = OUTAGE
        entry["reason"] = (
            f"{entry['content_state'].lower()} this scan; last produced "
            f"{entry['last_productive_items']} record(s) at "
            f"{productive_at.isoformat()}")

    return states


def _text(value: Any) -> str:
    return str(value or "").strip()


def contributing_source_ids(card: Dict[str, Any]) -> List[str]:
    """Every source that put anything at all on this card."""
    found: List[str] = []

    def add(value: Any) -> None:
        text = _text(value)
        if text and text not in found:
            found.append(text)

    add(card.get("source_id"))
    for value in card.get("source_ids") or ():
        add(value)
    for entry in card.get("source_provenance") or ():
        if isinstance(entry, dict):
            add(entry.get("source_id"))
    for channel in card.get("channels") or ():
        if isinstance(channel, dict):
            for value in channel.get("source_ids") or ():
                add(value)
    return found


def fixture_authority(
    card: Dict[str, Any],
    states: Dict[str, Dict[str, Any]],
) -> str:
    """The source that scheduled this fixture, or "" when nothing says.

    `schedule_source_url` is written by the schedule resolver and names the feed
    the kickoff, competition and status came from - which is the feed asserting
    that the fixture exists. It is asked first because the other fields do not
    distinguish "this source scheduled the match" from "this source supplied a
    stream that was attached to it", and on 2026-09-06 that distinction was the
    whole question: `Baltika Kaliningrad Vs Lokomotiv Moscow` listed three
    sources, two of them healthy, and only one of them had ever said when the
    match starts.
    """
    schedule_url = _text(card.get("schedule_source_url")).split("|", 1)[0]
    if schedule_url:
        for source_id, entry in states.items():
            url = _text(entry.get("url")).split("|", 1)[0]
            if url and url == schedule_url:
                return source_id
    primary = _text(card.get("source_id"))
    if primary:
        return primary
    contributors = contributing_source_ids(card)
    if len(contributors) == 1:
        return contributors[0]
    return ""


def hold_upcoming_through_outage(
    upcoming_items: List[Dict[str, Any]],
    previous_items: Iterable[Dict[str, Any]],
    today_items: Iterable[Dict[str, Any]],
    *,
    states: Dict[str, Dict[str, Any]],
    now: Optional[datetime] = None,
    still_upcoming: Callable[[Dict[str, Any]], bool],
    fixture_key: Callable[[Dict[str, Any]], str],
    is_ended: Optional[Callable[[Dict[str, Any]], bool]] = None,
    is_same_fixture: Optional[Callable[[Dict[str, Any], Dict[str, Any]], bool]] = None,
    hold_minutes: int = DEFAULT_HOLD_MINUTES,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Re-add previously published Upcoming cards whose author went silent.

    Returns the new Upcoming list and an accounting of every decision, refusals
    included - a fixture this declines to hold is a fixture that left the page,
    and that has to be as visible as the ones it keeps.
    """
    reference = now or _now()
    kept = list(upcoming_items)

    outages = {
        source_id for source_id, entry in (states or {}).items()
        if entry.get("state") == OUTAGE
    }
    stats: Dict[str, Any] = {
        "sources_in_outage": sorted(outages),
        "held": 0,
        "held_names": [],
        "considered": 0,
        "refused": {},
        "hold_expired": 0,
    }
    if not outages:
        return kept, stats

    published = [card for card in list(kept) + list(today_items)
                 if isinstance(card, dict)]
    published_ids = set()
    published_keys = set()
    for card in published:
        event_id = _text(card.get("id"))
        if event_id:
            published_ids.add(event_id)
        key = fixture_key(card)
        if key:
            published_keys.add(key)

    def already_here(previous: Dict[str, Any]) -> bool:
        """Whether this scan published this fixture, under any spelling.

        The id and the key are exact, and a fixture that arrived from a
        different feed this time arrives under that feed's spelling of it. The
        duplicate fold settles that within one tab; nothing settles it across
        two, so the semantic question is asked here, before anything is added.
        """
        event_id = _text(previous.get("id"))
        if event_id and event_id in published_ids:
            return True
        key = fixture_key(previous)
        if key and key in published_keys:
            return True
        if is_same_fixture is None:
            return False
        return any(is_same_fixture(previous, card) for card in published)

    def refuse(reason: str) -> None:
        stats["refused"][reason] = stats["refused"].get(reason, 0) + 1

    for previous in previous_items or ():
        if not isinstance(previous, dict):
            continue
        if already_here(previous):
            continue
        stats["considered"] += 1

        authority = fixture_authority(previous, states)
        if not authority:
            refuse("no fixture authority on the card")
            continue
        if authority not in outages:
            state = str((states.get(authority) or {}).get("state") or UNKNOWN)
            refuse(f"authority {state.lower()} this scan")
            continue
        if is_ended is not None and is_ended(previous):
            refuse("fixture has ended")
            continue
        if not still_upcoming(previous):
            refuse("past its own clock")
            continue

        since = parse_time(previous.get("source_outage_hold_since")) or reference
        held_for = (reference - since).total_seconds() / 60.0
        if held_for > max(0, hold_minutes):
            stats["hold_expired"] += 1
            refuse("hold window exhausted")
            continue

        # A deep copy: the pipeline stages after this one relabel channels
        # and strip embed streams in place, and the previous snapshot this
        # was read from must not change under them.
        card = copy.deepcopy(previous)
        card["source_outage_hold_since"] = since.isoformat()
        card["source_outage_hold_scans"] = _int(
            previous.get("source_outage_hold_scans")) + 1
        card["source_outage_hold_minutes"] = int(held_for)
        card["source_outage_authority"] = authority
        reason = (
            f"source outage: {authority} "
            f"{(states.get(authority) or {}).get('reason') or 'produced nothing this scan'}"
        )
        card["source_outage_hold_reason"] = reason
        # Live protection's own vocabulary, so every report that already knows
        # how to say "this scan did not find it" says it about these cards too.
        if not _text(card.get("carried_forward_reason")):
            card["carried_forward_reason"] = reason
        kept.append(card)
        stats["held"] += 1
        if len(stats["held_names"]) < 25:
            stats["held_names"].append(_text(card.get("name")))

    return kept, stats
