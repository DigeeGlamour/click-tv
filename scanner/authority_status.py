"""When a fixture authority may end a card, and when it may only be recorded.

The shadow layer proved the case rather than argued it. On 2026-09-07 at 06:29Z
`Toluca vs Monterrey` was published as `LIVE_NOW` with `lifecycle_state: LIVE`,
three hours after a 90-minute game, with `lifecycle_reason: "still live:
primary_playable, backup_playable"` - a link probe holding it there. ESPN
(`401914323`, STATUS_FULL_TIME, state `post`) and LiveScore (`1870766`, FT,
Esid 6) both said FINISHED. Two independent upstreams, agreeing, ignored.

This module decides what that evidence is allowed to do. It writes no state of
its own: it stamps `authority_status`, a field `event_lifecycle._STATUS_FIELDS`
has listed as the MOST authoritative slot since it was written and which
nothing has ever filled. Everything after that is the end machinery that
already exists - `has_strong_end_signal`, `decide()`, `ended_seen_at`, the
20-minute `post_match_grace_minutes`, END_PENDING, ENDED, the archive - and
none of it is reimplemented here.

Four rules do the work.

**Witnesses are upstreams, not feeds.** Two authorities agreeing means two
`upstream_family` values agreeing. Mirrors of one upstream are one witness, and
`upstream_family.py` holds the map.

**One authority is evidence, not proof.** A single family calling a fixture
finished has to say so for `confirmations_required` consecutive scans before it
counts - the same confirmation mechanism the lifecycle already uses for a
disputed end, with the same central setting.

**A terminal state is not always a finish.** FINISHED, ABANDONED, CANCELLED
and NO_RESULT all mean the fixture will not continue, so each may end a card -
and each keeps its own word in `authority_end_status`, because a report that
said "finished" about an abandoned match would be losing the only interesting
part. POSTPONED and SUSPENDED are NOT ends and are recorded only: postponed
means it will be played later, suspended means play stopped and may resume.
Both appear in `event_lifecycle`'s vocabularies - POSTPONED among the strong
ends, SUSPENDED among the live states - so writing either into
`authority_status` would make the lifecycle assert something this module does
not believe. Neither is written.

**Silence is never an end.** An unmatched fixture, an unavailable authority, an
UNKNOWN status and a disagreement between authorities all leave the card
exactly as the existing rules left it. UNVERIFIED is a statement about our
evidence, not about the match: the first shadow run had 41 of them, most of
them small domestic cricket leagues neither authority carries.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    from scanner import authority_shadow, event_lifecycle, fixture_authority
    from scanner import upstream_family
except ImportError:                                              # pragma: no cover
    import authority_shadow                                      # type: ignore
    import event_lifecycle                                       # type: ignore
    import fixture_authority                                     # type: ignore
    import upstream_family                                       # type: ignore

FINISHED = fixture_authority.FINISHED
ABANDONED = fixture_authority.ABANDONED
CANCELLED = fixture_authority.CANCELLED
NO_RESULT = fixture_authority.NO_RESULT
POSTPONED = fixture_authority.POSTPONED
SUSPENDED = fixture_authority.SUSPENDED
LIVE = fixture_authority.LIVE
UPCOMING = fixture_authority.UPCOMING
UNKNOWN = fixture_authority.UNKNOWN

#: States that mean this fixture will not continue. Each may end a card, and
#: each keeps its own word rather than being flattened into "finished".
TERMINAL_STATES = (FINISHED, ABANDONED, CANCELLED, NO_RESULT)

#: Recorded, never applied. Postponed means later, suspended means paused -
#: and the task is explicit that neither is an ordinary finish.
RECORD_ONLY_STATES = (POSTPONED, SUSPENDED)

#: The word written into `authority_status` for each terminal state. Every one
#: of these is already in `event_lifecycle.STRONG_END_STATUSES`, which is the
#: whole reason this mapping can be a lookup rather than a new rule.
LIFECYCLE_WORD = {
    FINISHED: "FINISHED",
    ABANDONED: "ABANDONED",
    CANCELLED: "CANCELLED",
    NO_RESULT: "NO_RESULT",
}

# --- confidence ----------------------------------------------------------
HIGH = "HIGH"
LOW = "LOW"
CONFLICT = "CONFLICT"
NONE = "NONE"


def _text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def _parse(value: Any) -> Optional[_dt.datetime]:
    return event_lifecycle.parse_time(value)


def _iso(stamp: _dt.datetime) -> str:
    return stamp.astimezone(_dt.timezone.utc).isoformat()


class Evidence:
    """What the authorities said about one card, and how much it is worth."""

    __slots__ = ("status", "families", "authorities", "confidence", "verdict",
                 "conflict_reasons", "matched", "raw")

    def __init__(self, status: str = "", families: Optional[List[str]] = None,
                 authorities: Optional[Dict[str, Dict[str, Any]]] = None,
                 confidence: str = NONE, verdict: str = "",
                 conflict_reasons: Optional[List[str]] = None,
                 matched: bool = False, raw: Optional[Dict[str, Any]] = None):
        self.status = status
        self.families = families or []
        self.authorities = authorities or {}
        self.confidence = confidence
        self.verdict = verdict
        self.conflict_reasons = conflict_reasons or []
        self.matched = matched
        self.raw = raw or {}

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATES

    @property
    def is_live(self) -> bool:
        return self.status == LIVE

    def describe(self) -> List[str]:
        """One line per authority, for the card and the report to carry."""
        out = []
        for name in sorted(self.authorities):
            entry = self.authorities[name]
            out.append("%s %s=%s (%s, event %s)" % (
                entry.get("upstream_family") or name,
                name,
                entry.get("status_raw") or entry.get("status"),
                entry.get("status_code") or "-",
                entry.get("event_id") or "-"))
        return out


def read(row: Dict[str, Any]) -> Evidence:
    """Turn one `authority_shadow` row into evidence.

    The row is what the shadow layer already produced - the same matcher, the
    same `same_fixture`, the same verdicts. Nothing is matched a second time
    here, so the status the lifecycle acts on and the status the report shows
    cannot drift apart.
    """
    matched: Dict[str, Dict[str, Any]] = {}
    for name, entry in (row.get("authorities") or {}).items():
        if not isinstance(entry, dict) or entry.get("result") != "matched":
            continue
        status = _text(entry.get("status"))
        if not status or status == UNKNOWN:
            continue
        matched[name] = entry

    if not matched:
        return Evidence(verdict=_text(row.get("verdict")),
                        conflict_reasons=list(row.get("conflict_reasons") or []),
                        raw=row)

    families = sorted({_text(entry.get("upstream_family"))
                       or upstream_family.family_for(name)
                       for name, entry in matched.items()})
    statuses = {_text(entry.get("status")) for entry in matched.values()}

    if len(statuses) > 1:
        return Evidence(
            status="", families=families, authorities=matched,
            confidence=CONFLICT, verdict=_text(row.get("verdict")),
            conflict_reasons=list(row.get("conflict_reasons") or []),
            matched=True, raw=row)

    status = statuses.pop()
    # Independent upstreams, not feeds. Two mirrors of one upstream saying the
    # same thing twice is one witness saying it once.
    confidence = HIGH if len(families) >= 2 else LOW
    return Evidence(status=status, families=families, authorities=matched,
                    confidence=confidence, verdict=_text(row.get("verdict")),
                    conflict_reasons=list(row.get("conflict_reasons") or []),
                    matched=True, raw=row)


def _card_is_playable(card: Dict[str, Any]) -> bool:
    """Does this card still carry a route a viewer could use?

    Only for recording the stream-versus-authority conflict. It changes no
    decision: the card keeps its place for the post-match grace either way,
    and retiring a card has never stopped a stream - it removes a row from
    today-match.json and revokes nothing.
    """
    if card.get("verified") is True or card.get("is_valid") is True:
        return True
    if _text(card.get("url")):
        return True
    for backup in card.get("backups") or []:
        if isinstance(backup, dict) and (_text(backup.get("url"))
                                         or _text(backup.get("playback_id"))):
            return True
        if isinstance(backup, str) and backup.strip():
            return True
    return bool(_text(card.get("playback_id")))


def stamp(
    card: Dict[str, Any],
    evidence: Evidence,
    *,
    now: _dt.datetime,
    confirmations_required: int = event_lifecycle.DEFAULT_CONFIRMATIONS_REQUIRED,
    post_match_grace_minutes: int = 0,
) -> str:
    """Record the evidence on the card, and apply it only when it has earned it.

    Returns the action taken, for the run's accounting: `""` when nothing was
    applied, `"applied"` when `authority_status` now carries a terminal state,
    `"pending_confirmation"` while a single authority is still being counted,
    `"live"` when the authorities confirm play, `"recorded"` for a state that
    is deliberately never applied, `"conflict"` when they disagree.

    Mutates the card. Never removes it - that is `decide()`'s business, and
    the grace and the archive are already its business too.
    """
    required = max(1, int(confirmations_required))
    card.pop("authority_end_conflict", None)

    if evidence.confidence == CONFLICT:
        card["authority_end_status"] = ""
        card["authority_end_confidence"] = CONFLICT
        card["authority_end_families"] = evidence.families
        card["authority_end_evidence"] = evidence.describe()
        card["authority_end_conflict"] = (
            "independent authorities disagree: " + "; ".join(evidence.describe()))
        # Deliberately nothing else. A disagreement between authorities is the
        # one case where acting is worse than waiting.
        return CONFLICT.lower()

    if not evidence.matched or not evidence.status:
        # No authority reached this fixture. The existing rules keep it exactly
        # as they had it - see the module docstring on UNVERIFIED.
        return ""

    card["authority_end_status"] = evidence.status
    card["authority_end_confidence"] = evidence.confidence
    card["authority_end_families"] = evidence.families
    card["authority_end_evidence"] = evidence.describe()
    card["authority_independent_count"] = len(evidence.families)

    if evidence.is_live:
        # An authority confirming play is worth as much as one confirming an
        # end, and it is what stops a stale FT in one playlist from retiring a
        # match that is actually being played. `decide()` reads this through
        # `authority_says_live` and holds the end for the confirmation count.
        card["authority_status"] = "LIVE"
        card.pop("authority_finished_seen_at", None)
        card.pop("authority_finished_confirmations", None)
        card.pop("authority_remove_after", None)
        return "live"

    if evidence.status in RECORD_ONLY_STATES:
        # Recorded and left alone. Writing POSTPONED into `authority_status`
        # would land it in STRONG_END_STATUSES and end the card; writing
        # SUSPENDED would land it in LIVE_STATUSES and assert play. Neither is
        # what the authority said.
        card.pop("authority_finished_seen_at", None)
        card.pop("authority_finished_confirmations", None)
        card.pop("authority_remove_after", None)
        return "recorded"

    if not evidence.is_terminal:
        # UPCOMING, and anything else that is neither live nor terminal. It may
        # not promote a card and it may not end one; a future kickoff against a
        # source calling it live is a conflict for the report, and the bounded
        # start-window rules already own that decision.
        card.pop("authority_finished_seen_at", None)
        card.pop("authority_finished_confirmations", None)
        card.pop("authority_remove_after", None)
        return "recorded"

    # A fixture whose kickoff has not arrived cannot have finished. An
    # authority saying otherwise is a disagreement with the clock, not an end -
    # and `same_fixture` matches on a 45-minute kickoff window, so the most
    # likely reading is that the two sides are talking about different
    # meetings of the same two clubs. Recorded, never applied.
    kickoff = _parse(card.get("start_time") or card.get("start_at"))
    if kickoff is not None and now < kickoff:
        card["authority_end_conflict"] = (
            "authority says %s while kickoff is still %d minute(s) away"
            % (evidence.status,
               int((kickoff - now).total_seconds() // 60)))
        card.pop("authority_finished_seen_at", None)
        card.pop("authority_finished_confirmations", None)
        card.pop("authority_remove_after", None)
        return "conflict"

    # A terminal state. Two independent upstreams are enough at once; one has
    # to repeat itself.
    confirmations = 1
    if evidence.confidence == LOW:
        try:
            previous = int(card.get("authority_finished_confirmations") or 0)
        except (TypeError, ValueError):
            previous = 0
        confirmations = max(1, previous + 1)
        card["authority_finished_confirmations"] = confirmations
        if confirmations < required:
            card.pop("authority_remove_after", None)
            return "pending_confirmation"
    else:
        card["authority_finished_confirmations"] = confirmations

    seen = _text(card.get("authority_finished_seen_at")) or _iso(now)
    card["authority_finished_seen_at"] = seen
    card["authority_status"] = LIFECYCLE_WORD[evidence.status]

    stamp_time = _parse(seen) or now
    card["authority_remove_after"] = _iso(
        stamp_time + _dt.timedelta(minutes=max(0, int(post_match_grace_minutes))))

    if _card_is_playable(card):
        # The stream-versus-authority case, recorded rather than guessed at.
        # It changes nothing: the card holds its place for the same grace, and
        # no session is touched.
        card["authority_end_conflict"] = (
            "authority says %s while the card still carries a route" % evidence.status)
    return "applied"


def apply(
    cards: Iterable[Dict[str, Any]],
    by_authority: Dict[str, List[Dict[str, Any]]],
    health: Dict[str, Any],
    *,
    now: _dt.datetime,
    confirmations_required: int = event_lifecycle.DEFAULT_CONFIRMATIONS_REQUIRED,
    post_match_grace_minutes: int = 0,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Stamp every card, and return the rows the report will need.

    The rows are `authority_shadow.compare` output, so the report at the end of
    the scan is built from the same comparison the lifecycle acted on rather
    than a second, later one that could disagree with it.
    """
    rows: List[Dict[str, Any]] = []
    stats = {
        "applied": 0,
        "pending_confirmation": 0,
        "live": 0,
        "recorded": 0,
        "conflict": 0,
        "unmatched": 0,
        "by_status": {},
        "by_confidence": {},
        "applied_fixtures": [],
        "conflicts": [],
    }
    for card in cards:
        if not isinstance(card, dict):
            continue
        row = authority_shadow.compare(card, by_authority, health)
        rows.append(row)
        evidence = read(row)
        action = stamp(
            card, evidence, now=now,
            confirmations_required=confirmations_required,
            post_match_grace_minutes=post_match_grace_minutes)
        if not action:
            stats["unmatched"] += 1
            continue
        stats[action] = int(stats.get(action, 0)) + 1
        if evidence.status:
            stats["by_status"][evidence.status] = int(
                stats["by_status"].get(evidence.status, 0)) + 1
        stats["by_confidence"][evidence.confidence] = int(
            stats["by_confidence"].get(evidence.confidence, 0)) + 1
        if action == "applied":
            stats["applied_fixtures"].append({
                "name": _text(card.get("name")),
                "fixture_id": _text(card.get("fixture_id")),
                "status": evidence.status,
                "confidence": evidence.confidence,
                "upstream_families": evidence.families,
                "evidence": evidence.describe(),
                "authority_finished_seen_at": _text(
                    card.get("authority_finished_seen_at")),
                "remove_after": _text(card.get("authority_remove_after")),
                "was_playable": bool(card.get("authority_end_conflict")),
                "lifecycle_state_before": _text(card.get("lifecycle_state")),
            })
        if _text(card.get("authority_end_conflict")) or action == "conflict":
            stats["conflicts"].append({
                "name": _text(card.get("name")),
                "reason": _text(card.get("authority_end_conflict")),
                "status": evidence.status,
                "confidence": evidence.confidence,
                "upstream_families": evidence.families,
            })
    return rows, stats
