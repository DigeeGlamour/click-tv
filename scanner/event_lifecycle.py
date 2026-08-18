"""Section 21 - when is a live event actually over.

A sport's duration is not proof of anything. Football goes to extra time and
penalties, cricket stops for rain and runs for five days, a tennis match can last
twice its estimate. So the scheduled or estimated end time is a *supporting*
signal here and never a reason on its own to take a Today Match card away.

The lifecycle is:

    UPCOMING -> STARTING -> LIVE -> END_PENDING -> ENDED

and the only two things that reach ENDED are:

  * a **strong end signal** - an authoritative fixture source saying FT,
    FINISHED, ENDED, FINAL or an equivalent sport-specific finished state; or
  * **multi-signal confirmation** while the authority is unavailable: the
    estimated end has passed AND every link on the card is genuinely dead AND
    repeated scans have stopped seeing it live.

Anything short of that stays LIVE or drops to END_PENDING, and END_PENDING still
publishes the card. Four conditions each independently keep an event live:
the authority still calls it in progress, the primary is still playable, any
backup is still playable, or a viewer is watching it right now. The last of those
is the strongest protection in the system - section 21 says a currently-playing
session is never interrupted by a background scan, and that even an authoritative
END may retire the catalogue entry without stopping the player.

This module is the decision layer only. It reads signals and returns a verdict;
scanner/live_protection.py owns the probing and the state file, and the frontend
owns the player session.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

# Lifecycle states, in order.
UPCOMING = "UPCOMING"
STARTING = "STARTING"
LIVE = "LIVE"
END_PENDING = "END_PENDING"
ENDED = "ENDED"
LIFECYCLE_ORDER = (UPCOMING, STARTING, LIVE, END_PENDING, ENDED)

# Section 21's strong end signals. An authority using any of these has told us
# the match is over, and that is enough on its own.
STRONG_END_STATUSES = frozenset({
    "FT", "FINISHED", "ENDED", "END", "FINAL", "FULL_TIME", "FULLTIME",
    "COMPLETED", "COMPLETE", "RESULT", "AET", "PEN", "AWD", "WO", "ABANDONED",
    "CANCELLED", "CANCELED", "POSTPONED", "NO_RESULT", "STUMPS_FINAL",
    "MATCH_ENDED", "MATCH_OVER", "AFTER_EXTRA_TIME", "PENALTIES",
    "RETIRED", "WALKOVER", "FORFEIT",
})

# Statuses that positively assert the match is running.
LIVE_STATUSES = frozenset({
    "LIVE", "LIVE_NOW", "IN_PROGRESS", "INPLAY", "IN_PLAY", "PLAYING",
    "CHANNEL_LIVE", "HT", "HALF_TIME", "HALFTIME", "BREAK", "INNINGS_BREAK",
    "DRINKS", "RAIN_DELAY", "DELAYED", "SUSPENDED", "STUMPS", "INTERVAL",
    "1H", "2H", "ET", "EXTRA_TIME", "PENALTY_SHOOTOUT", "SET_BREAK",
    "TIMEOUT", "QUARTER_BREAK", "RESUMING",
})

STARTING_STATUSES = frozenset({
    "STARTING", "STARTING_SOON", "ABOUT_TO_START", "PRE_MATCH", "PREMATCH",
    "WARMUP", "LINK_UPDATING", "TOSS",
})

UPCOMING_STATUSES = frozenset({
    "UPCOMING", "NOT_STARTED", "SCHEDULED", "TBD", "FIXTURE",
})

# How many consecutive scans with no live signal are needed before END_PENDING
# may become ENDED. One quiet scan is routine; three in a row, with a dead link
# and the estimate passed, is a pattern.
DEFAULT_CONFIRMATIONS_REQUIRED = 3

# How long after the estimated end the estimate counts as "passed". Generous on
# purpose: an estimate that is merely optimistic must not start the clock.
DEFAULT_ESTIMATE_GRACE_MINUTES = 90

# Fallback durations, used only to estimate an end time when the fixture did not
# publish one. Deliberately long, because over-estimating costs nothing here and
# under-estimating is what section 21 exists to prevent.
SPORT_DURATION_MINUTES = {
    "cricket": 8 * 60,
    "football": 150,
    "tennis": 5 * 60,
    "basketball": 165,
    "baseball": 4 * 60,
    "hockey": 165,
    "rugby": 140,
    "volleyball": 165,
    "golf": 8 * 60,
    "motorsport": 4 * 60,
    "esports": 5 * 60,
}
DEFAULT_DURATION_MINUTES = 4 * 60

_STATUS_FIELDS = (
    "authority_status", "fixture_status", "schedule_status", "status",
    "original_status", "provider_status",
)


def _normalize_status(value: Any) -> str:
    text = str(value or "").strip().upper()
    return re.sub(r"[\s\-]+", "_", text)


def statuses_of(card: Dict[str, Any]) -> List[str]:
    """Every status the card carries, most authoritative field first."""
    return [
        _normalize_status(card.get(field))
        for field in _STATUS_FIELDS
        if str(card.get(field) or "").strip()
    ]


def has_strong_end_signal(card: Dict[str, Any]) -> bool:
    """Section 21. Did an authority say, in its own words, that this is over?"""
    return any(status in STRONG_END_STATUSES for status in statuses_of(card))


def authority_says_live(card: Dict[str, Any]) -> Optional[bool]:
    """True if an authority asserts in-progress, False if it asserts finished,
    None if it said nothing usable - which must never be read as finished."""
    seen = statuses_of(card)
    if not seen:
        return None
    for status in seen:
        if status in STRONG_END_STATUSES:
            return False
        if status in LIVE_STATUSES:
            return True
    return None


def parse_time(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def estimated_end(card: Dict[str, Any]) -> Optional[datetime]:
    """The card's own end time, or a generous estimate from its sport.

    Only ever used as a supporting signal - see the module docstring.
    """
    for field in ("end_time", "end_at", "estimated_end_time"):
        parsed = parse_time(card.get(field))
        if parsed is not None:
            return parsed
    start = parse_time(card.get("start_time") or card.get("start_at"))
    if start is None:
        return None
    sport = str(card.get("sport_type") or "").strip().lower()
    minutes = SPORT_DURATION_MINUTES.get(sport, DEFAULT_DURATION_MINUTES)
    return start + timedelta(minutes=minutes)


def estimate_passed(
    card: Dict[str, Any],
    now: datetime,
    grace_minutes: int = DEFAULT_ESTIMATE_GRACE_MINUTES,
) -> bool:
    end = estimated_end(card)
    if end is None:
        return False
    return now > end + timedelta(minutes=max(0, int(grace_minutes)))


@dataclass
class LifecycleSignals:
    """Everything the decision needs, gathered by the caller."""

    authority_live: Optional[bool] = None
    strong_end: bool = False
    primary_playable: Optional[bool] = None
    backup_playable: Optional[bool] = None
    currently_playing: bool = False
    estimate_passed: bool = False
    consecutive_non_live_scans: int = 0
    seen_in_this_scan: bool = False

    @property
    def any_link_playable(self) -> Optional[bool]:
        """True if something is playable, False if everything checked is dead,
        None if nothing could be checked at all."""
        verdicts = [v for v in (self.primary_playable, self.backup_playable) if v is not None]
        if not verdicts:
            return None
        return any(verdicts)


@dataclass
class LifecycleVerdict:
    state: str
    publish: bool
    reason: str
    confirmations: int = 0
    protections: List[str] = field(default_factory=list)

    @property
    def retired(self) -> bool:
        return self.state == ENDED


def classify_state(card: Dict[str, Any], now: datetime) -> str:
    """The state a card is in before any end-detection reasoning."""
    if has_strong_end_signal(card):
        return ENDED
    seen = statuses_of(card)
    if any(status in LIVE_STATUSES for status in seen):
        return LIVE
    if any(status in STARTING_STATUSES for status in seen):
        return STARTING
    start = parse_time(card.get("start_time") or card.get("start_at"))
    if start is not None:
        if now < start - timedelta(minutes=15):
            return UPCOMING
        if now < start:
            return STARTING
        return LIVE
    if any(status in UPCOMING_STATUSES for status in seen):
        return UPCOMING
    return LIVE


def decide(
    card: Dict[str, Any],
    signals: LifecycleSignals,
    *,
    now: Optional[datetime] = None,
    confirmations_required: int = DEFAULT_CONFIRMATIONS_REQUIRED,
) -> LifecycleVerdict:
    """Section 21's end-detection decision for one Today Match card.

    The order of tests is the order of authority. A strong end signal wins; then
    the four still-live protections; then, and only when the authority is silent,
    the multi-signal path through END_PENDING.
    """
    reference = now or datetime.now(timezone.utc)

    # 1. A currently-playing session is the strongest protection in the system.
    #    The catalogue entry may still be retired below on a strong end signal,
    #    but nothing here ever removes the card out from under a viewer.
    if signals.currently_playing and not signals.strong_end:
        return LifecycleVerdict(
            LIVE, True, "a viewer is watching this event right now",
            protections=["currently_playing"],
        )

    # 2. An authority saying "finished" in its own words is enough.
    if signals.strong_end or signals.authority_live is False:
        return LifecycleVerdict(
            ENDED, False, "authoritative finished status", confirmations=0,
        )

    protections: List[str] = []
    if signals.authority_live is True:
        protections.append("authority_live")
    if signals.primary_playable is True:
        protections.append("primary_playable")
    if signals.backup_playable is True:
        protections.append("backup_playable")

    # 3. Any single still-live protection keeps the card, whatever the clock says.
    if protections:
        return LifecycleVerdict(
            LIVE, True,
            "still live: " + ", ".join(protections),
            protections=protections,
        )

    # 4. The authority is silent and nothing is known to be playable. This is
    #    END_PENDING, not ENDED - and END_PENDING still publishes the card.
    playable = signals.any_link_playable
    if playable is None:
        return LifecycleVerdict(
            END_PENDING, True,
            "no authority and no usable link verdict - holding, not retiring",
            confirmations=signals.consecutive_non_live_scans,
        )

    confirmations = int(signals.consecutive_non_live_scans)
    confirmed = (
        signals.estimate_passed
        and playable is False
        and confirmations >= max(1, int(confirmations_required))
    )
    if confirmed:
        return LifecycleVerdict(
            ENDED, False,
            (
                "multi-signal confirmation: estimated end passed, every link dead, "
                f"and {confirmations} consecutive scans with no live signal"
            ),
            confirmations=confirmations,
        )

    missing: List[str] = []
    if not signals.estimate_passed:
        missing.append("estimated end not passed")
    if playable is not False:
        missing.append("links not proven dead")
    if confirmations < max(1, int(confirmations_required)):
        missing.append(
            f"only {confirmations} of {confirmations_required} confirming scans"
        )
    return LifecycleVerdict(
        END_PENDING, True,
        "END_PENDING, not ENDED: " + "; ".join(missing),
        confirmations=confirmations,
    )


def apply_verdict(card: Dict[str, Any], verdict: LifecycleVerdict) -> Dict[str, Any]:
    """Stamp the lifecycle decision onto a published card."""
    updated = dict(card)
    updated["lifecycle_state"] = verdict.state
    updated["lifecycle_reason"] = verdict.reason
    if verdict.state == END_PENDING:
        updated["end_pending"] = True
        updated["end_pending_confirmations"] = verdict.confirmations
    else:
        updated.pop("end_pending", None)
        updated.pop("end_pending_confirmations", None)
    if verdict.protections:
        updated["lifecycle_protections"] = list(verdict.protections)
    return updated


# ── Where an event belongs, decided once ──────────────────────────────────────
# Routing statuses. These live here rather than in scanner/events.py because the
# merge has to group by the tab an event will *land in*, and the routing decision
# is made from the schedule status. Two copies of this rule drifted apart once
# already: grouping keyed on `source_pipeline` while routing keyed on status, so a
# live fixture arriving from an "upcoming" feed was grouped away from the same
# fixture arriving from a "today" feed and then routed into the same tab beside
# it - one real match, two cards.
ROUTE_LIVE_STATUSES = frozenset({"LIVE_NOW", "LIVE", "CHANNEL_LIVE", "IN_PROGRESS"})
ROUTE_UPCOMING_STATUSES = frozenset({
    "UPCOMING", "STARTING_SOON", "LINK_UPDATING", "NOT_STARTED", "SCHEDULED",
})


def event_destination(card: Dict[str, Any]) -> str:
    """Decide Today Match vs Upcoming from the event, not from its source file.

    Routing used to read `source_pipeline`, so a match stayed wherever its
    playlist happened to be configured. A live fixture that arrived from an
    "upcoming" feed was therefore filed as Upcoming and then dropped for having
    started in the past - which is how `Sri Lanka vs India 1st Test`, carrying
    five working streams, vanished from both tabs. The schedule status is what
    actually decides where an event belongs; the source group is only a hint for
    anything with no resolved status at all.

    Returns "today_match", "upcoming", "ended", or whatever the source pipeline
    says when no status resolved.
    """
    status = str(card.get("schedule_status") or card.get("status") or "").strip().upper()
    if status in ROUTE_LIVE_STATUSES:
        return "today_match"
    if status in ROUTE_UPCOMING_STATUSES:
        return "upcoming"
    if status == "ENDED":
        return "ended"
    return str(card.get("source_pipeline") or "").strip().lower()
