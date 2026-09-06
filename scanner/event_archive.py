"""What happened to a fixture after it left the tabs.

FINAL_2 ধাপ ৪, in its own words:

    purge মানে delete নয় — `state/event-archive.json`-এ সরানো। কারণ একই fixture
    যদি পরের scan-এ আবার feed-এ ফিরে আসে, archive না থাকলে সে নতুন card হিসেবে
    Upcoming-এ ফিরে যাবে।

That is the whole reason this file exists. A retired fixture is dropped from
today-match.json, and the feed it came from has no idea: the same row is still
sitting in the same playlist, and the next scan reads it as a fixture it has
never seen. Without a record of the retirement it comes back - and because its
kickoff is now in the past, it comes back as tomorrow's Upcoming card.

What is kept is deliberately thin. The archive answers one question - "has this
already ended?" - so it holds identity and lifecycle evidence and nothing else.
No channels, no streams, no logos, no provenance of routes. A card is roughly
forty fields; an archive row is seven.

Identity carries the date, so it blocks a resurrection without blocking a
rematch. `provider:india-vs-pakistan|asia cup|2026-09-05` and the same two
sides meeting again in November are different rows, because they are different
fixtures.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

ARCHIVE_FILE = Path("state") / "event-archive.json"

#: There is deliberately no expiry here. A retirement is remembered until
#: something removes it, because FINAL_2 names no retention duration and any
#: number invented to fill that gap is a lifecycle rule in disguise: the day
#: after it lapses, a row still sitting in a source playlist comes back as a
#: new card, which is the exact fault the archive exists to prevent.

#: The states that mean a fixture is finished with, rather than merely absent.
ARCHIVED_STATES = frozenset({"ENDED", "PURGED"})


def _text(value: Any) -> str:
    return str(value or "").strip()


def _parse(value: Any) -> Optional[datetime]:
    text = _text(value).replace("Z", "+00:00")
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def archive_identity(card: Dict[str, Any]) -> str:
    """The identity a retirement is remembered under.

    `fixture_id` first, because it already carries the competition and the
    date - which is exactly what keeps a rematch in November from being
    mistaken for September's fixture. Falling back to the card id alone would
    lose the date, so the kickoff date is appended to it.
    """
    fixture_id = _text(card.get("fixture_id"))
    if fixture_id:
        return fixture_id.casefold()
    event_id = _text(card.get("id"))
    if not event_id:
        return ""
    start = _parse(card.get("start_time") or card.get("start_at"))
    day = start.astimezone(timezone.utc).date().isoformat() if start else ""
    return ("%s|%s" % (event_id, day)).casefold()


def load_archive(path: Path | str = ARCHIVE_FILE) -> Dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"fixtures": {}}
    if not isinstance(payload, dict):
        return {"fixtures": {}}
    fixtures = payload.get("fixtures")
    if not isinstance(fixtures, dict):
        payload["fixtures"] = {}
    return payload


def save_archive(archive: Dict[str, Any], path: Path | str = ARCHIVE_FILE) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(archive, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def _entry_as_card(entry: Dict[str, Any]) -> Dict[str, Any]:
    """The archived fixture, in the shape the dedupe helpers read."""
    return {
        "id": _text(entry.get("id")),
        "fixture_id": _text(entry.get("fixture_id")),
        "name": _text(entry.get("name")),
        "competition": _text(entry.get("competition")),
        "sport_type": _text(entry.get("sport_type")),
        "start_time": _text(entry.get("start_time")),
    }


def _identity_helper(module_name: str, attribute: str):
    for path in ("scanner.%s" % module_name, module_name):
        try:
            module = __import__(path, fromlist=[attribute])
        except Exception:  # noqa: BLE001 - a report never breaks a scan
            continue
        helper = getattr(module, attribute, None)
        if callable(helper):
            return helper
    return None


def _same_fixture(card: Dict[str, Any], archived: Dict[str, Any]) -> bool:
    """Is this the fixture that was retired? Asked of the layers that decide.

    Two opinions already exist and the archive forms none of its own:

      * `merger.same_real_fixture` - the merge layer's, and the one FINAL_2
        describes: normalized participants, sport, competition, kickoff bucket.
      * `fixture_dedupe.same_fixture` - the narrower rule the published tabs
        fold on, which relates "Leeds" to "Leeds United" when the other side is
        identical and the kickoffs agree.

    The second is asked only when the two competitions are compatible by the
    merge layer's own test, because the tab-level rule does not look at the
    competition at all - it leans on the kickoff instead. So a cup tie between
    the same two sides is not mistaken for the league fixture, which is the one
    thing this must never do.
    """
    merge_verdict = _identity_helper("merger", "same_real_fixture")
    if merge_verdict is not None:
        try:
            if merge_verdict(card, archived):
                return True
        except Exception:  # noqa: BLE001 - never break a scan on a report
            pass

    compatible = _identity_helper("merger", "_competitions_compatible")
    identity = _identity_helper("merger", "canonical_event_identity")
    tab_verdict = _identity_helper("fixture_dedupe", "same_fixture")
    if tab_verdict is None or compatible is None or identity is None:
        return False
    try:
        left, right = identity(card), identity(archived)
        if not compatible(left[2], right[2], left[3], right[3]):
            return False
        return bool(tab_verdict(card, archived))
    except Exception:  # noqa: BLE001 - never break a scan on a report
        return False


def is_archived(card: Dict[str, Any], archive: Dict[str, Any]) -> bool:
    """Has this fixture already been retired - however a source spells it?

    Two tiers, in FINAL_2's order. The stored identity first, which is the
    provider fixture id and costs one lookup. Then the same question the
    dedupe layer answers, because a second source can carry the same match
    under its own id: measured on 2026-09-05,
    `provider:brighton-vs-leeds|premier league|2026-09-05` was archived as
    ended while `Brighton Hove Albion Vs Leeds United` was published live
    from another feed.
    """
    fixtures = archive.get("fixtures") or {}
    if not fixtures:
        return False
    identity = archive_identity(card)
    if identity and identity in fixtures:
        return True

    kickoff = _parse(card.get("start_time") or card.get("start_at"))
    if kickoff is None:
        # Without a kickoff the second tier cannot be asked safely - every
        # helper it uses requires one - and guessing is how a rematch gets
        # blocked.
        return False
    day = kickoff.astimezone(timezone.utc).date()
    for entry in fixtures.values():
        if not isinstance(entry, dict):
            continue
        stamp = _parse(entry.get("start_time"))
        # A day either side, so a kickoff near midnight in another zone is
        # still compared. The helpers below decide; this only keeps the
        # comparison from running against every fixture ever retired.
        if stamp is None or abs(
                (stamp.astimezone(timezone.utc).date() - day).days) > 1:
            continue
        if _same_fixture(card, _entry_as_card(entry)):
            return True
    return False


def archive_entry(card: Dict[str, Any], now: datetime) -> Dict[str, Any]:
    """The thin record. Identity and lifecycle evidence, nothing else.

    `competition` and `sport_type` are here because FINAL_2's identity rule
    names them: when the provider id does not match, a fixture is recognised
    by normalized teams + competition + kickoff. Without them the second tier
    cannot be asked at all. They are identity evidence, not card content -
    still no channels, no streams, no artwork.
    """
    return {
        "id": _text(card.get("id")),
        "fixture_id": _text(card.get("fixture_id")),
        "name": _text(card.get("name")),
        "competition": _text(card.get("competition")),
        "sport_type": _text(card.get("sport_type")),
        "start_time": _text(card.get("start_time") or card.get("start_at")),
        "ended_seen_at": _text(card.get("ended_seen_at")),
        "lifecycle_state": _text(card.get("lifecycle_state")) or "ENDED",
        "archived_at": now.isoformat(),
    }


def archive_retired(
    cards: Iterable[Dict[str, Any]],
    *,
    now: Optional[datetime] = None,
    path: Path | str = ARCHIVE_FILE,
    archive: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Record every retired fixture, once.

    An identity already present is left exactly as it was: the first
    retirement is the one that happened, and a later scan seeing the same
    finished fixture again is not a second retirement.
    """
    reference = now or datetime.now(timezone.utc)
    payload = load_archive(path) if archive is None else archive
    fixtures = payload.setdefault("fixtures", {})
    added = 0
    for card in cards:
        if not isinstance(card, dict):
            continue
        identity = archive_identity(card)
        if not identity or identity in fixtures:
            continue
        fixtures[identity] = archive_entry(card, reference)
        added += 1
    payload["updated_at"] = reference.isoformat()
    payload["count"] = len(fixtures)
    payload["note"] = (
        "Fixtures that have finished and left the tabs. Read to stop a source "
        "re-listing one from coming back as a new card. Identity carries the "
        "date, so a rematch is a different fixture."
    )
    if archive is None:
        save_archive(payload, path)
    return {"added": added, "total": len(fixtures)}


def drop_resurrected(
    cards: List[Dict[str, Any]],
    archive: Dict[str, Any],
) -> tuple[List[Dict[str, Any]], List[str]]:
    """Remove cards for fixtures that have already been retired.

    Returns the surviving cards and the names that were dropped, so a scan
    report can say what happened rather than a card silently disappearing.
    """
    kept: List[Dict[str, Any]] = []
    dropped: List[str] = []
    for card in cards:
        if isinstance(card, dict) and is_archived(card, archive):
            dropped.append(_text(card.get("name")) or _text(card.get("id")))
            continue
        kept.append(card)
    return kept, dropped
