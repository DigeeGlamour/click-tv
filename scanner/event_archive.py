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
from datetime import datetime, timedelta, timezone
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

#: Statuses that end a LISTING without ending the FIXTURE.
#:
#: Both live in `event_lifecycle.STRONG_END_STATUSES`, which is precisely why
#: they have to be named again here. A postponed match is rescheduled and a
#: suspended one is still being played, and either arrives in the same status
#: field an FT does - so `has_strong_end_signal` cannot tell them apart, and
#: every caller that archives on a strong end signal would remember a
#: postponement as a retirement. An archive row bars the fixture from ever
#: coming back, so this refusal outranks every piece of end evidence a card
#: can carry.
#:
#: Deliberately just these two: they are the two FINAL_2's non-finished
#: states name, and a third added on expectation rather than evidence would
#: be a lifecycle rule invented in a housekeeping file.
NEVER_ARCHIVED_STATUSES = frozenset({"POSTPONED", "SUSPENDED"})

#: What `authority_status` may say for this fixture to count as over. Written
#: only by scanner/authority_status.py, and only when the evidence earned it:
#: two independent upstream families agreeing, or one repeating itself for
#: `confirmations_required` scans.
TERMINAL_AUTHORITY_STATUSES = frozenset({
    "FINISHED", "ABANDONED", "CANCELLED", "NO_RESULT",
})


def _lifecycle():
    """The lifecycle module, or None. Imported the way the identity helpers
    below are, so this file keeps working whether the scanner is run as a
    package or from inside its own directory."""
    for path in ("scanner.event_lifecycle", "event_lifecycle"):
        try:
            return __import__(path, fromlist=["statuses_of"])
        except Exception:  # noqa: BLE001 - never break a scan on a report
            continue
    return None


def _statuses(card: Dict[str, Any]) -> List[str]:
    """Every status the card carries, asked of the lifecycle layer.

    No local copy of the field list: `event_lifecycle._STATUS_FIELDS` is the
    one that `has_strong_end_signal` reads, and a second table here would be
    free to drift from it. `authority_end_status` is added because a
    non-finished authority verdict is RECORDED there and deliberately never
    promoted into `authority_status` - which is exactly where a POSTPONED
    lands, and exactly what the refusal below has to see.
    """
    module = _lifecycle()
    if module is None:
        return []
    fields = tuple(getattr(module, "_STATUS_FIELDS", ())) + (
        "authority_end_status",
    )
    normalize = getattr(module, "_normalize_status", None)
    out: List[str] = []
    for field in fields:
        text = _text(card.get(field))
        if not text:
            continue
        try:
            out.append(normalize(text) if normalize else text.upper())
        except Exception:  # noqa: BLE001
            out.append(text.upper())
    return out


def archive_refusal(card: Dict[str, Any]) -> str:
    """The status that forbids remembering this fixture as retired, or "".

    Asked before any other question, because being wrong here is permanent:
    the fixture is barred from both tabs for as long as the row stands.
    """
    for status in _statuses(card):
        if status in NEVER_ARCHIVED_STATUSES:
            return status
    return ""


def terminal_status(card: Dict[str, Any]) -> str:
    """The specific word that says this fixture is over, or "".

    Kept beside the provenance so an archive row records not just that a
    retirement happened but what was actually said - "ABANDONED" and
    "FINISHED" are both terminal and are not the same fact.
    """
    if archive_refusal(card):
        return ""
    module = _lifecycle()
    authority = _text(card.get("authority_status")).upper().replace(" ", "_")
    if authority in TERMINAL_AUTHORITY_STATUSES:
        return authority
    if module is None:
        return ""
    strong = getattr(module, "STRONG_END_STATUSES", frozenset())
    for status in _statuses(card):
        if status in strong:
            return status
    return ""


def terminal_provenance(
    card: Dict[str, Any],
    *,
    now: Optional[datetime] = None,
    post_match_grace_minutes: int = 0,
) -> str:
    """What, on this card alone, says the fixture is over - and by whose word.

    The order is the order of authority, and it is the order
    `event_lifecycle.decide` already reads: a refusal first, then the
    lifecycle's own retirement verdict, then this scan's fixture authority,
    then a feed's word, then the fixture's own provider-STATED end time, then
    a post-match grace that has run out.

    Returns "" when the card says nothing - which is the answer for a fixture
    that merely stopped being listed. That is the whole reason this function
    exists: absence has no provenance, so absence can never reach the archive.
    """
    if archive_refusal(card):
        return ""
    state = _text(card.get("lifecycle_state")).upper()
    if state in ARCHIVED_STATES:
        return "lifecycle_%s" % state.lower()
    authority = _text(card.get("authority_status")).upper().replace(" ", "_")
    if authority in TERMINAL_AUTHORITY_STATUSES:
        return "authority_%s" % authority.lower()
    module = _lifecycle()
    if module is not None:
        strong = getattr(module, "STRONG_END_STATUSES", frozenset())
        for status in _statuses(card):
            if status in strong:
                return "feed_%s" % status.lower()
    reference = now or datetime.now(timezone.utc)
    if module is not None:
        passed = getattr(module, "verified_end_passed", None)
        try:
            # Provider-STATED only. FINAL_1 রায় ১০: an assumed or
            # sport-length end time never retires a card, so it never
            # archives one either.
            if callable(passed) and passed(card, reference):
                return "provider_end_time"
        except Exception:  # noqa: BLE001
            pass
    seen = _parse(card.get("ended_seen_at"))
    if seen is not None and reference >= seen + timedelta(
            minutes=max(0, int(post_match_grace_minutes))):
        return "post_match_grace_expired"
    return ""


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


def _normalized_participants(card: Dict[str, Any]) -> List[str]:
    """The two sides as the dedupe layer canonicalizes them, for the record.

    Evidence, not a matcher input. `_entry_as_card` below still hands the
    identity helpers the same four fields it always did, so what counts as
    one fixture stays decided in exactly one place and this cannot quietly
    widen it.
    """
    helper = _identity_helper("fixture_dedupe", "sides")
    if helper is None:
        return []
    try:
        pair = helper(card)
    except Exception:  # noqa: BLE001 - never break a scan on a report
        return []
    return [str(side) for side in pair] if pair else []


def _fixture_gender(card: Dict[str, Any]) -> str:
    """Women, men or unstated - the shared helper's answer.

    Recorded because it is the difference between two real fixtures. The
    matching rule already refuses to fold a women's fixture into a men's one;
    this makes the archive row say which of them it retired.
    """
    helper = _identity_helper("fixture_dedupe", "fixture_gender")
    if helper is None:
        return ""
    try:
        return str(helper(card) or "")
    except Exception:  # noqa: BLE001
        return ""


def _provider_source_ids(card: Dict[str, Any]) -> List[str]:
    """Every feed that carried this fixture, by id.

    A retirement is remembered against one identity, but the fixture may have
    arrived from several feeds under several spellings, and the row should say
    which. Not a witness count - see scanner/upstream_family.py for why seven
    mirrors of one upstream are one witness.
    """
    out = {_text(card.get("source_id"))}
    raw = card.get("source_ids")
    if isinstance(raw, (list, tuple)):
        out.update(_text(value) for value in raw)
    out.discard("")
    return sorted(out)


def _authority_event_ids(card: Dict[str, Any]) -> Dict[str, str]:
    """How the fixture authorities named this fixture, if any of them did.

    The canonical identity no stream feed can supply: `espn-header
    401914323`, `livescore 1870766`. Written by scanner/authority_status.py
    when it stamps a verdict, so a later scan can tell that the fixture in
    front of it is the one an authority retired even if every feed renames it.
    """
    raw = card.get("authority_event_ids")
    if not isinstance(raw, dict):
        return {}
    return {str(name): _text(value) for name, value in sorted(raw.items())
            if _text(value)}


def archive_entry(
    card: Dict[str, Any],
    now: datetime,
    *,
    provenance: str = "",
    post_match_grace_minutes: int = 0,
) -> Dict[str, Any]:
    """The thin record. Identity and lifecycle evidence, nothing else.

    `competition` and `sport_type` are here because FINAL_2's identity rule
    names them: when the provider id does not match, a fixture is recognised
    by normalized teams + competition + kickoff. Without them the second tier
    cannot be asked at all. They are identity evidence, not card content -
    still no channels, no streams, no artwork, no playback record.

    The provenance half answers the question the nine-field row could not:
    WHY this fixture is remembered as retired. A row with an empty
    `terminal_provenance` is a row that should not exist, and being able to
    see that in the file is the point - the archive is the one structure here
    whose mistakes are permanent.

    `provenance` is what a caller that watched the lifecycle decide passes
    in, and it is preferred over anything readable on the card, because a
    card retired by a verdict never gets to carry that verdict: an ENDED
    verdict has `publish=False`, so the row is gone before anything could
    stamp it. That is exactly why retirements were going unrecorded.
    """
    stated = _text(provenance) or terminal_provenance(
        card, now=now, post_match_grace_minutes=post_match_grace_minutes)
    families = card.get("authority_end_families")
    return {
        "id": _text(card.get("id")),
        "fixture_id": _text(card.get("fixture_id")),
        # The bridge across an id change, so a retirement survives one.
        # Written by scanner/schedule_resolver.py when an event keeps the
        # card it already had under a new id.
        "previous_event_id": _text(
            card.get("previous_event_id") or card.get("previous_id")),
        "name": _text(card.get("name")),
        "participants": _normalized_participants(card),
        "gender": _fixture_gender(card),
        "competition": _text(card.get("competition")),
        "sport_type": _text(card.get("sport_type")),
        "start_time": _text(card.get("start_time") or card.get("start_at")),
        "provider_source_ids": _provider_source_ids(card),
        "authority_event_ids": _authority_event_ids(card),
        "terminal_status": terminal_status(card),
        "terminal_provenance": stated,
        "terminal_authorities": (
            [str(name) for name in families]
            if isinstance(families, (list, tuple)) else []),
        "ended_seen_at": _text(card.get("ended_seen_at")),
        "authority_finished_seen_at": _text(
            card.get("authority_finished_seen_at")),
        "lifecycle_state": _text(card.get("lifecycle_state")) or "ENDED",
        "archived_at": now.isoformat(),
    }


def archive_retired(
    cards: Iterable[Dict[str, Any]],
    *,
    now: Optional[datetime] = None,
    path: Path | str = ARCHIVE_FILE,
    archive: Optional[Dict[str, Any]] = None,
    post_match_grace_minutes: int = 0,
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
    refused: List[str] = []
    absent: List[str] = []
    for card in cards:
        if not isinstance(card, dict):
            continue
        # The backstop, applied to every caller rather than trusted to each
        # of them separately. POSTPONED is a strong end signal, so any caller
        # archiving on `has_strong_end_signal` would bar a rescheduled
        # fixture for good.
        stop = archive_refusal(card)
        if stop:
            refused.append("%s (%s)" % (
                _text(card.get("name")) or _text(card.get("id")), stop))
            continue
        identity = archive_identity(card)
        if not identity or identity in fixtures:
            continue
        entry = archive_entry(
            card, reference,
            provenance=_text(card.get("_terminal_provenance")),
            post_match_grace_minutes=post_match_grace_minutes,
        )
        if not entry["terminal_provenance"]:
            # Nothing said this fixture was over - not an authority, not a
            # feed, not its own provider-stated end time, not a grace that
            # ran out. It stopped being listed, and that is a different fact
            # which never becomes this one.
            #
            # Enforced here rather than trusted to each caller. A caller that
            # DID watch the lifecycle decide passes `_terminal_provenance`,
            # which is honoured above; a caller working from a row it found
            # missing has to show evidence like everyone else. Measured on
            # origin/main: 338 of 367 departures had no evidence at all, and
            # every one of them was a card that came back.
            absent.append(_text(card.get("name")) or _text(card.get("id")))
            continue
        fixtures[identity] = entry
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
    return {
        "added": added,
        "total": len(fixtures),
        # Named, not merely counted: a refusal is a decision this file made
        # about a real fixture, and a scan report that cannot say which one
        # is a report nobody can check.
        "refused": len(refused),
        "refused_names": refused[:20],
        "absence_only": len(absent),
        "absence_only_names": absent[:20],
    }


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
