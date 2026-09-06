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

#: How long a finished fixture keeps its place on Today Match. Fallback
#: only: the live value is config/settings.json ->
#: event_lifecycle.post_match_grace_minutes, which asks for 20. Kept at 0 -
#: what the code did before the grace existed, an immediate retirement - so
#: a missing config reproduces the old behaviour rather than inventing a
#: window nobody configured.
DEFAULT_POST_MATCH_GRACE_MINUTES = 0

#: Where a card's `end_time` came from - see PROMPT 14. Defined here, and
#: re-exported by schedule_resolver, because this is the module that has to
#: consult it: schedule_resolver already imports this one, so the reverse
#: would be a cycle, and two copies of the rule would be worse than either.
END_SOURCE_PROVIDER = "provider"
END_SOURCE_SPORT = "sport"
END_SOURCE_ASSUMED = "assumed"
END_TIME_SOURCES = (END_SOURCE_PROVIDER, END_SOURCE_SPORT, END_SOURCE_ASSUMED)


def end_time_provenance(card: Dict[str, Any]) -> str:
    """How much a card's `end_time` is worth, read safely.

    A card published before this field existed carries an end_time and no
    provenance. It reads as "assumed", which is what it is: every one of
    them was `kickoff + provider_event_hours`. Silence is never evidence
    of a provider, and an unrecognised value is not either - so nothing
    can be promoted to `provider` by accident, only by a writer saying so.
    """
    value = str(card.get("end_time_source") or "").strip().lower()
    return value if value in END_TIME_SOURCES else END_SOURCE_ASSUMED


def end_time_is_provider_stated(card: Dict[str, Any]) -> bool:
    """True only when the card itself says a provider stated the end."""
    return end_time_provenance(card) == END_SOURCE_PROVIDER

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

#: How long a cricket match lasts, by format. The single "cricket" entry
#: above is 8 hours, written for a Test day, and on 2026-09-05 every one of
#: the 25 cricket cards on the site was T20 or shorter - so one number for
#: the sport is wrong for almost all of it.
#:
#: FINAL_2 ধাপ ੩ sets five of these. `Hundred` it names as a token to
#: detect but gives no length for, so the length comes from the evidence
#: already in this repository: config/event-fixtures.json has carried
#: `the-hundred-2026: duration_minutes = 210` since before any of this. It
#: is kept as its own entry rather than folded into T20 - a hundred balls
#: an innings is not twenty overs an innings, and the config already knew.
#:
#: Keyed by the strings in sport_filter.CRICKET_FORMATS. Deliberately not
#: imported from there: this module is the one Live TV code paths reach,
#: and sport_filter must stay out of them.
CRICKET_FORMAT_MINUTES = {
    "T10": 150,
    "T20": 240,
    "ODI": 480,
    # A day of a Test, not the Test. See the note in
    # schedule_resolver._sport_end_minutes: this length says when to stop
    # showing a card, and it is never evidence that a Test has finished.
    "Test": 480,
    "Hundred": 210,
    # Cricket, format unknown. Longer than a T20 and shorter than an ODI,
    # because guessing either would be worse than admitting neither.
    "unknown": 300,
}

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


def verified_end_passed(
    card: Dict[str, Any],
    now: datetime,
    grace_minutes: int = DEFAULT_ESTIMATE_GRACE_MINUTES,
) -> bool:
    """Has the fixture's OWN published end time passed?

    Distinct from `estimate_passed`, and the distinction is the whole point.
    `estimated_end` falls back to a guess from the sport when a card has no end
    time, and a guess must never retire anything on its own. This asks only
    about a real `end_time` on a card whose schedule an authority verified - the
    fixture stating, in its own words, when it finishes.

    That is an end signal, not a supporting hint, and it has to outrank a live
    link probe. A probe proves the LINK works; it says nothing about whether the
    MATCH is on. Most of these links are 24-hour channel feeds - Willow, Star
    Sports - which answer forever.

    Measured on 2026-08-30: Today Match held 444 cards, 295 of them from more
    than a day earlier, because live protection carried 434 forward on
    probe_alive=411 with released_stale=0. `Sri Lanka vs India 1st Test` had a
    verified end_time of 2026-08-19 and was still being published as LIVE_NOW
    eleven days later, its channel still answering, exactly as it always will.

    What "the fixture stating in its own words when it finishes" requires
    is the fixture actually having said it. `schedule_verified` alone does
    not show that and never did: this system stamps it on every card it
    resolves, including the ones whose end time it invented itself. On
    2026-09-05 that was 344 of 344 published cards, 343 of them exactly
    240 minutes past kickoff - so this function was reading its own
    arithmetic back as an authority, which is FINAL_1 রায় ১০.

    So the end must be `provider` - stated by the feed or the catalogue.
    A `sport` estimate is a good guess about a format and an `assumed` one
    is a guess about nothing; neither ends a match. They still support the
    softer `estimate_passed` path, with its own grace, exactly as before.
    A Test day's 480 minutes is the case that matters most: five more days
    of the match may remain, and nothing here may call that a finish.
    """
    if not end_time_is_provider_stated(card):
        return False
    if card.get("schedule_verified") is not True:
        return False
    end = None
    for field in ("end_time", "end_at"):
        end = parse_time(card.get(field))
        if end is not None:
            break
    if end is None:
        return False
    return now >= end + timedelta(minutes=max(0, int(grace_minutes)))


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
    #: When the first genuine end signal for this fixture was seen. Set
    #: once and carried forward unchanged; the post-match grace is
    #: measured from it, so a second FT on the next scan must not move
    #: it or the card would be held for another full grace period.
    ended_seen_at: str = ""
    #: How many consecutive scans have seen an end signal that a trusted
    #: authority was contradicting at the time. Reset to 0 the moment the
    #: contradiction stops, in either direction.
    contradicted_end_confirmations: int = 0

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
    post_match_grace_minutes: int = DEFAULT_POST_MATCH_GRACE_MINUTES,
) -> LifecycleVerdict:
    """Section 21's end-detection decision for one Today Match card.

    The order of tests is the order of authority. A strong end signal wins; then
    the four still-live protections; then, and only when the authority is silent,
    the multi-signal path through END_PENDING.
    """
    reference = now or datetime.now(timezone.utc)

    # 1. A currently-playing session is the strongest protection in the
    #    system, and it protects the thing that matters: the stream a
    #    viewer already has open. It does not protect the LISTING for ever.
    #
    #    FINAL_2 ধাপ ੪ settles the question it raises - "FT আসার পরেও কেউ
    #    দেখতে থাকলে কী হবে?" - as: grace শেষ হলে card তালিকা থেকে যাবে,
    #    কিন্তু চলমান playback থামবে না. Those are two different things and
    #    nothing in this file confuses them: retiring a card removes a row
    #    from today-match.json. It revokes no URL, ends no session and
    #    writes nothing to state/playing-sessions.json, which this system
    #    only ever reads.
    #
    #    Without the second half of this condition an ended fixture with one
    #    viewer left open would sit on Today Match for ever, which is the
    #    "চিরদিন Today-তে আটকে থাকবে" case. It still leaves through
    #    END_PENDING and the full post-match grace, never abruptly.
    if signals.currently_playing and not signals.strong_end:
        if not verified_end_passed(card, reference):
            return LifecycleVerdict(
                LIVE, True, "a viewer is watching this event right now",
                protections=["currently_playing"],
            )

    # 2. An authority saying "finished" in its own words is enough to stop
    #    calling this live - but not to make the card vanish from under the
    #    people who were just watching it. FINAL_2 ধাপ ੪: the state goes
    #    END_PENDING and the card stays published, for the post-match grace.
    #
    #    `ended_seen_at` is stamped on the first sighting and carried
    #    forward untouched afterwards. The grace runs from when the match
    #    was first seen to be over, not from the most recent scan that
    #    noticed - otherwise every scan would restart it and the card
    #    would never leave.
    # 1b. Two sources disagreeing is not an authority speaking.
    #
    #    One feed carrying FT while THIS scan's fixture authority says the
    #    match is in progress is the case FINAL_2 ধাপ ੪ asks not to act on:
    #    "এক source ENDED, বিশ্বস্ত source LIVE হলে সাথে সাথে মুছবেন না". A single
    #    stale row in one playlist is the ordinary way this happens.
    #
    #    So the end is held, not discarded: the disputed sightings are
    #    counted, and once `confirmations_required` scans in a row have
    #    seen the same thing the end is credible and the lifecycle moves
    #    on. That is the existing confirmation mechanism and the existing
    #    central `confirmations_required`, not a new source ranking.
    #
    #    Nothing is stamped while the dispute stands - in particular not
    #    `ended_seen_at`, which would start the post-match grace on
    #    evidence that may evaporate on the next scan.
    disputed = 0
    if signals.strong_end and signals.authority_live is True:
        disputed = _contradicted_end_count(card) + 1
        if disputed < max(1, int(confirmations_required)):
            return LifecycleVerdict(
                LIVE, True,
                "an end signal is disputed by a trusted authority still calling this live",
                protections=["authority_live"],
                contradicted_end_confirmations=disputed,
            )

    if signals.strong_end or signals.authority_live is False:
        seen = _ended_seen_at(card, reference)
        if _post_match_grace_remains(seen, reference, post_match_grace_minutes):
            return LifecycleVerdict(
                END_PENDING, True,
                "finished, holding for the post-match grace",
                confirmations=0,
                protections=(
                    ["currently_playing"] if signals.currently_playing else []
                ),
                ended_seen_at=seen,
                # Carried, not cleared. Dropping it here restarted the
                # tally on the next scan, so a permanently disputed
                # fixture cycled LIVE / END_PENDING / LIVE and never
                # reached the grace at all.
                contradicted_end_confirmations=disputed,
            )
        return LifecycleVerdict(
            ENDED, False, "authoritative finished status", confirmations=0,
            ended_seen_at=seen,
            contradicted_end_confirmations=disputed,
        )

    # 2b. The fixture's own verified end time, which is the same authority
    #     speaking - it said in advance when this would finish, and it has.
    #     Placed above the still-live protections deliberately: those are led by
    #     a link probe, and a link probe cannot tell a live match from a channel
    #     that happens to broadcast all day. Without this the protections held
    #     434 finished matches on Today Match indefinitely.
    if verified_end_passed(card, reference):
        seen = _ended_seen_at(card, reference)
        if _post_match_grace_remains(seen, reference, post_match_grace_minutes):
            return LifecycleVerdict(
                END_PENDING, True,
                "the fixture's own verified end time has passed - holding for the post-match grace",
                confirmations=0,
                protections=(
                    ["currently_playing"] if signals.currently_playing else []
                ),
                ended_seen_at=seen,
            )
        return LifecycleVerdict(
            ENDED, False,
            "the fixture's own verified end time has passed",
            confirmations=0,
            ended_seen_at=seen,
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


def _contradicted_end_count(card: Dict[str, Any]) -> int:
    """How many scans in a row have seen a disputed end signal."""
    try:
        return max(0, int(card.get("contradicted_end_confirmations") or 0))
    except (TypeError, ValueError):
        return 0


def _post_match_grace_remains(
    ended_seen_at: str,
    now: datetime,
    grace_minutes: int,
) -> bool:
    """Is the fixture still inside its post-match grace?

    Measured from the FIRST end signal, never from this scan - which is
    what stops a repeated FT holding the card open for ever. The
    boundary is inclusive of the last minute and exclusive of the
    grace itself: at `ended_seen_at + grace` exactly, the grace is
    over and the card goes.

    A grace of zero, which is the fallback, means no holding at all -
    the behaviour this system had before the window existed.
    """
    minutes = max(0, int(grace_minutes))
    if minutes == 0:
        return False
    seen = parse_time(ended_seen_at)
    if seen is None:
        return False
    return now < seen + timedelta(minutes=minutes)


def _ended_seen_at(card: Dict[str, Any], now: datetime) -> str:
    """When this fixture was FIRST seen to be over.

    What the card already says, whenever it says anything readable. A
    fixture that is finished on one scan is finished on the next, and
    every one of those scans would otherwise write its own "now" and
    push the removal another grace period into the future.

    Only an unreadable or absent value is replaced, and then with this
    scan's reference time rather than a wall clock, so a run started at
    a fixed `now` stays reproducible.
    """
    existing = str(card.get("ended_seen_at") or "").strip()
    if existing and parse_time(existing) is not None:
        return existing
    return now.isoformat()


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
    # Written whenever the verdict carries one, and never cleared: the
    # grace has to be measured from the first sighting, and a card that
    # went END_PENDING and then briefly looked live again must not get a
    # fresh window out of it. The only writer is `_ended_seen_at` below,
    # which prefers what the card already says.
    if verdict.ended_seen_at:
        updated["ended_seen_at"] = verdict.ended_seen_at
    # Zero is written as well as cleared: the count has to be able to go
    # back down, or a single disputed scan would leave a card one tick
    # from being retired for ever afterwards.
    if verdict.contradicted_end_confirmations:
        updated["contradicted_end_confirmations"] = (
            verdict.contradicted_end_confirmations
        )
    else:
        updated.pop("contradicted_end_confirmations", None)
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


#: How long before kickoff a fixture moves onto Today Match.
#:
#: The tab is named for the day but was routed purely on status, so a match at
#: 20:00 sat on Upcoming at 19:55 and only crossed over when the scanner marked
#: it LIVE_NOW - after the whistle. A viewer opening the site at 19:45 pressed
#: Today Match and did not find the match they came for.
#:
#: Deliberately the same number as events.targeted_window_minutes. That window
#: tells the scanner which fixtures to hunt links for; this one decides which
#: tab they appear on. Different jobs, but a fixture arriving on Today Match
#: before anything is looking for its link would sit there with nothing to show.
DEFAULT_TODAY_ROUTING_MINUTES = 30


def minutes_to_kickoff(card: Dict[str, Any], now: Optional[datetime] = None) -> Optional[float]:
    """Minutes until this fixture starts. Negative once it has. None with no clock."""
    start = parse_time(card.get("start_time") or card.get("start_at"))
    if start is None:
        return None
    reference = now or datetime.now(timezone.utc)
    return (start - reference).total_seconds() / 60.0


def event_destination(
    card: Dict[str, Any],
    now: Optional[datetime] = None,
    routing_minutes: int = DEFAULT_TODAY_ROUTING_MINUTES,
) -> str:
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

    `routing_minutes` is how long before kickoff a fixture crosses over. It
    comes from config/settings.json -> event_lifecycle.move_to_today_minutes,
    passed in by the caller, and it is the SAME key the targeted hunt reads.
    That is the point of it being an argument: the two were separate numbers
    that happened to be equal, and the day they stopped being equal a card
    would arrive on Today Match with nothing looking for its link. The default
    below is a fallback for a caller that has no settings to hand, never a
    second opinion about the timing.
    """
    status = str(card.get("schedule_status") or card.get("status") or "").strip().upper()
    if status == "ENDED":
        return "ended"
    if status in ROUTE_LIVE_STATUSES:
        return "today_match"

    # A fixture close enough to kickoff belongs on Today Match whatever its
    # status says, because that is the tab a viewer looks at when a match is
    # about to start. STARTING_SOON and LINK_UPDATING are exactly the states
    # this covers - the second one especially, since a match at its kickoff
    # with the scanner still hunting is the last thing that should be filed
    # under "upcoming".
    if status in ROUTE_UPCOMING_STATUSES:
        remaining = minutes_to_kickoff(card, now)
        if remaining is not None and remaining <= max(0, int(routing_minutes)):
            return "today_match"
        return "upcoming"

    return str(card.get("source_pipeline") or "").strip().lower()
