"""Requirement 4 - the near-kickoff targeted Upcoming scan.

The trigger fires every five minutes. That must not mean the same fixture is
scanned every five minutes, and it must not mean it is scanned only once
either. A fixture inside the window is scanned **once per five-minute slot**,
and it keeps being scanned until it has a usable link.

It was once-only, and the cost of that is in the committed ledger: eleven
fixtures, every one of them at `attempts: 1`, three resolved and eight not -
permanently not, because the first empty attempt was also the last. A source
that publishes a link ten minutes before kickoff could never be heard.

A ledger in state/upcoming-targeting.json records the attempts:

    {"fixtures": {"<key>": {
        "attempted": true,            # the suppression flag
        "attempted_at": "...",        # the first attempt
        "attempts": 1,
        "last_attempt_at": "...",     # the most recent attempt
        "last_attempt_bucket": "2026-09-04T07:35Z",
        "name": "...", "start_time": "...",
        "resolved": true,             # a usable link was found
        "resolved_at": "...",
        "first_link_at": "...",       # when a link FIRST existed
        "last_success_at": "...",     # when one was last confirmed
        "route_id": "...",            # redacted identity, informational
        "url_public_template": "..."}}}

`resolved` and `last_attempt_bucket` are what suppress further targeting now,
through `retry_skip_reason`: a fixture with a WORKING link has nothing left to
hunt for, and a slot that has already spent its attempt does not spend a second
one. `attempted` is history rather than policy - kept because it is what an
older ledger carries, and because "was this ever tried" is a fair question.

"Working" is checked, not assumed. `resolved` records what was true when the
attempt ran, and these feeds rotate and expire their URLs, so a fixture
resolved at T-25 can be dead at kickoff - the moment it matters. When the
fixture's own published card no longer passes `has_valid_link`, the same test
that set `resolved`, the fixture is reopened and the slot rule applies to it
like any other target. `link_lost_at` records that it happened. A resolved
fixture whose card is still playable is never re-hunted.
Nothing outside the window is a target at all, so no source is fetched and no
stream is verified on behalf of a fixture that is still hours away.

The window runs from `now + window_minutes` down to `now - 10 minutes`, so a
match that has already started can still be given a link. Ten minutes is not
arbitrary: it is how long config/settings.json keeps a kicked-off fixture on
the Upcoming tab, and data/upcoming.json is the list this planner reads.

How many attempts a fixture gets is therefore a consequence, not a target. It
is however many five-minute slots the trigger actually reached while the
fixture was inside the window.

Every reader of these fields tolerates their absence - a ledger written by an
older build loads, and `attempt_count`/`last_attempt_bucket`/`first_link_at`/
`last_success_at` answer 0 or "" for it rather than raising.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set
from scanner import route_evidence as rev
from scanner.lifecycle_config import targeted_timings
from scanner.live_protection import ENDED_STATUSES

STATE_FILE = Path("state/upcoming-targeting.json")
DEFAULT_WINDOW_MINUTES = 15

# How far PAST kickoff a fixture may still be hunted for. A match that has
# started and has no link is the one case where a link matters most, and the
# window used to close at kickoff exactly - so the last chance to find one was
# always before it could possibly be needed.
#
# 10 rather than more, because it is what the rest of the system already
# agrees on: config/settings.json keeps a kicked-off fixture on the Upcoming
# tab for upcoming_past_grace_minutes = 10, and data/upcoming.json is where
# `known_upcoming_fixtures` reads from. Hunting past the point where the
# fixture leaves that file would be hunting for something not in the list.
DEFAULT_RETRY_AFTER_KICKOFF_MINUTES = 10

# The width of one attempt slot, in minutes, matching the five-minute trigger.
# A bucket is what lets "once per trigger" be expressed without counting
# triggers: two runs inside the same five minutes - which a queued or retried
# workflow produces - floor to the same bucket string.
BUCKET_MINUTES = 5

# How long after its kickoff a resolved fixture's ledger entry is kept before
# being pruned. Long enough that a late scan cannot re-target it, short enough
# that the file does not grow without bound.
LEDGER_RETENTION_HOURS = 12

_PLAYABLE_STATUSES = frozenset({
    "verified",
    "verified_global",
    "verified_proxy",
    "verified_bd",
})


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
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def fixture_key(item: Dict[str, Any]) -> str:
    """A stable per-fixture key.

    The published card id is used when there is one, because that is what
    survives the Upcoming -> Today promotion. Otherwise the normalized name and
    the kickoff date/hour identify the fixture well enough to be remembered
    across a five-minute gap.
    """
    if not isinstance(item, dict):
        return ""
    for field_name in ("id", "event_id", "fixture_id"):
        value = str(item.get(field_name) or "").strip()
        if value:
            return value

    name = str(item.get("name") or item.get("event_name") or "").strip().casefold()
    name = re.sub(r"[^a-z0-9]+", "-", name).strip("-")
    if not name:
        return ""
    start = parse_time(item.get("start_time") or item.get("start_at"))
    stamp = start.strftime("%Y%m%dT%H") if start else "no-kickoff"
    return f"{name}@{stamp}"


def _slug(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").casefold()).strip("-")


def _name_normalizers() -> List[Any]:
    """Every event-name normalizer used elsewhere in the scanner.

    The candidate pool is filtered by the planner, which has its own event-key
    normalizer, while merging uses the merger's. A target therefore publishes
    its name under every spelling so whichever stage does the comparison finds
    it. Imported lazily so this module stays importable on its own.
    """
    functions: List[Any] = []
    try:
        from scanner.merger import normalize_event_key

        functions.append(normalize_event_key)
    except Exception:  # pragma: no cover - optional
        pass
    try:
        from scanner.planner import _event_key

        functions.append(_event_key)
    except Exception:  # pragma: no cover - optional
        pass
    return functions


def match_keys_for(item: Dict[str, Any]) -> Set[str]:
    """Every string a later stage might use to recognise this fixture."""
    keys: Set[str] = set()
    if not isinstance(item, dict):
        return keys
    for field_name in ("id", "event_id", "fixture_id"):
        value = str(item.get(field_name) or "").strip().casefold()
        if value:
            keys.add(value)
    name = str(item.get("name") or item.get("event_name") or "").strip()
    if name:
        keys.add(_slug(name))
        for normalize in _name_normalizers():
            try:
                candidate = str(normalize(name) or "").strip().casefold()
            except Exception:  # pragma: no cover - a normalizer must not break a scan
                continue
            if candidate:
                keys.add(candidate)
    keys.discard("")
    return keys


def has_valid_link(item: Dict[str, Any]) -> bool:
    """Did this fixture end up with a link that can actually be played?

    Verified status alone is not enough: an Upcoming card is allowed to be
    metadata only, and one of those must stay targetable.

    A published card carries no stream URL - it carries the playback_id the
    proxy resolves - so a playback_id counts as a link just as a direct URL
    does. Judging on the URL alone would mean no published card ever resolved
    and the trigger would keep chasing a fixture it had already found.
    """
    if not isinstance(item, dict):
        return False
    if item.get("metadata_only") is True:
        return False
    if item.get("publish_allowed") is False:
        return False
    url = str(item.get("url") or item.get("stream_url") or "").strip()
    playback_id = str(item.get("playback_id") or "").strip()
    if not playback_id and not url.lower().startswith(("http://", "https://")):
        return False
    status = str(
        item.get("verification_status") or item.get("status") or ""
    ).strip().lower()
    return bool(item.get("verified") is True or status in _PLAYABLE_STATUSES)


def load_ledger(path: Path | str = STATE_FILE) -> Dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"fixtures": {}}
    if not isinstance(payload, dict):
        return {"fixtures": {}}
    fixtures = payload.get("fixtures")
    payload["fixtures"] = fixtures if isinstance(fixtures, dict) else {}
    return payload


def save_ledger(ledger: Dict[str, Any], path: Path | str = STATE_FILE) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=str(target.parent), delete=False,
        prefix=f".{target.name}.", suffix=".tmp",
    )
    try:
        json.dump(ledger, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    finally:
        handle.close()
    os.replace(handle.name, target)


def is_resolved(ledger: Dict[str, Any], key: str) -> bool:
    record = (ledger.get("fixtures") or {}).get(key)
    return isinstance(record, dict) and record.get("resolved") is True


def is_attempted(ledger: Dict[str, Any], key: str) -> bool:
    """Has this fixture ever been targeted?

    History, not policy. This WAS the suppression test, and being the
    suppression test is what stopped a fixture whose one scan found
    nothing from ever being scanned again. `retry_skip_reason` is the
    gate now.

    Kept because it is the only thing that reads a ledger from a build
    which recorded attempts and nothing else, and because "has this ever
    been tried" is a real question for a report to ask.
    """
    record = (ledger.get("fixtures") or {}).get(key)
    if not isinstance(record, dict):
        return False
    # `attempts` is read too, so a ledger written by the previous build - which
    # only counted attempts - still suppresses correctly after an upgrade.
    #
    # Through `attempt_count`, not `int()` directly: a bare int() raised
    # ValueError on any non-numeric value and took the whole scan down with it.
    # This file is committed, and the push path rebases it with a line-based
    # `-X theirs` text merge that has already corrupted generated JSON in this
    # repository once - which is why a "Repair merge damage" step exists. A
    # damaged count must degrade to "no attempt recorded", never to a crash.
    return bool(
        record.get("attempted") is True
        or attempt_count(ledger, key) > 0
        or record.get("resolved") is True
    )


def attempt_bucket(when: datetime, minutes: int = BUCKET_MINUTES) -> str:
    """The five-minute slot `when` falls in, as a sortable UTC string.

    Floored, never rounded: 07:34:59 and 07:30:00 are the same slot, so a
    trigger that starts late still belongs to the slot it was scheduled for.
    """
    width = max(1, int(minutes))
    moment = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
    moment = moment.astimezone(timezone.utc)
    floored = moment.replace(minute=(moment.minute // width) * width,
                             second=0, microsecond=0)
    return floored.strftime("%Y-%m-%dT%H:%MZ")


def _record(ledger: Dict[str, Any], key: str) -> Dict[str, Any]:
    """The entry for `key`, or an empty one. Never raises on an old ledger."""
    record = (ledger.get("fixtures") or {}).get(key)
    return record if isinstance(record, dict) else {}


def attempt_count(ledger: Dict[str, Any], key: str) -> int:
    """How many targeted attempts this fixture has had. 0 if never seen."""
    try:
        return max(0, int(_record(ledger, key).get("attempts") or 0))
    except (TypeError, ValueError):
        return 0


def last_attempt_bucket(ledger: Dict[str, Any], key: str) -> str:
    """The slot of the most recent attempt, or "" for a ledger without one.

    Empty for every entry written before this field existed, which is what
    keeps an upgrade from suppressing or duplicating anything.
    """
    return str(_record(ledger, key).get("last_attempt_bucket") or "")


def first_link_at(ledger: Dict[str, Any], key: str) -> str:
    """When a usable link was FIRST seen for this fixture, or ""."""
    return str(_record(ledger, key).get("first_link_at") or "")


def last_success_at(ledger: Dict[str, Any], key: str) -> str:
    """When a usable link was LAST seen for this fixture, or ""."""
    record = _record(ledger, key)
    return str(record.get("last_success_at") or record.get("resolved_at") or "")


def ever_had_a_link(ledger: Dict[str, Any], key: str) -> bool:
    """Has a usable link EVER been found for this fixture?

    Distinct from `is_resolved`, which answers only for right now. The
    refresh path needs the wider question: the first failed refresh writes
    `resolved: false`, and asking `is_resolved` on the next slot would then
    answer "nothing was ever found here" about a fixture whose stream had
    been playing five minutes earlier. It would get one refresh attempt and
    then be abandoned - the once-only fault again, wearing a new hat.
    """
    record = _record(ledger, key)
    return bool(
        record.get("resolved") is True
        or record.get("first_link_at")
        or record.get("last_success_at")
        or record.get("resolved_at")
        or record.get("link_lost_at")
    )


#: Why a fixture inside the window was not targeted on this trigger.
SKIP_RESOLVED = "resolved"
SKIP_SAME_BUCKET = "same_bucket"


def link_has_died(item: Optional[Dict[str, Any]]) -> bool:
    """Has the link a resolved fixture was resolved ON stopped working?

    Judged through `has_valid_link` - the same predicate `record_outcome`
    uses to decide `resolved` in the first place. Nothing new verifies
    anything here.

    The distinction that matters is between a card that FAILS and a card
    that SAYS NOTHING, because most of them say nothing. Measured on the
    published data for 2026-09-04: all 124 cards in data/upcoming.json are
    `metadata_only: true`, `verification_status: metadata_only`, with no url
    and no playback_id - which is by design, since an Upcoming card is a
    fixture announcement and its stream lives on the Today Match card once
    it has one. Reading "no link on this card" as "the link died" would
    therefore reopen every resolved fixture on every slot forever, which is
    the opposite of the intent.

    So death requires the card to name a route and that route to fail:

      item is None          silence - not in this trigger's list at all
      metadata_only         silence - the card carries no stream by design
      no url, no playback_id  silence - nothing is being claimed
      a route that fails    DIED - reopen it

    Silence leaves `resolved` standing, which is the conservative half of
    the choice: a working stream is never rescanned on a guess.
    """
    if item is None:
        return False
    if item.get("metadata_only") is True:
        return False
    named_route = (
        str(item.get("playback_id") or "").strip()
        or str(item.get("url") or item.get("stream_url") or "").strip()
    )
    if not named_route:
        return False
    return not has_valid_link(item)


def retry_skip_reason(
    ledger: Dict[str, Any],
    key: str,
    *,
    now: Optional[datetime] = None,
    item: Optional[Dict[str, Any]] = None,
    bucket_minutes: int = BUCKET_MINUTES,
) -> str:
    """Why this fixture is not a target, or "" when it is one.

    Two reasons, and having had an attempt is no longer either of them.

    SKIP_RESOLVED     it has a usable link and that link still works.
                      Nothing to hunt for, so nothing is fetched for it.
    SKIP_SAME_BUCKET  this five-minute slot has already spent its attempt
                      on it. Two runs inside one slot - a queued workflow,
                      a re-run, a catch-up - must not both scan it.

    `resolved` is a memory, not a guarantee. Stream URLs behind these
    feeds rotate and expire, and a fixture resolved at T-25 can be dead by
    kickoff - which is the moment it matters. So a resolved fixture whose
    own published card no longer carries a playable link is reopened, and
    the slot rule then applies to it exactly as to any other target. A
    resolved fixture whose card is still playable is left alone: this must
    not become a reason to rescan working streams.

    An entry written before `last_attempt_bucket` existed reports "" for
    its slot, which matches no real slot, so it becomes eligible once.
    That is deliberate: reading a missing slot as "already done here"
    would leave every pre-upgrade unresolved fixture suppressed until its
    entry is pruned, which is the exact fault being removed.
    """
    if is_resolved(ledger, key) and not link_has_died(item):
        return SKIP_RESOLVED
    bucket = last_attempt_bucket(ledger, key)
    if bucket and bucket == attempt_bucket(now or _now(), bucket_minutes):
        return SKIP_SAME_BUCKET
    return ""


def is_retry_eligible(
    ledger: Dict[str, Any],
    key: str,
    *,
    now: Optional[datetime] = None,
    item: Optional[Dict[str, Any]] = None,
    bucket_minutes: int = BUCKET_MINUTES,
) -> bool:
    """May this trigger target this fixture? The suppression test."""
    return retry_skip_reason(
        ledger, key, now=now, item=item, bucket_minutes=bucket_minutes
    ) == ""


@dataclass
class TargetPlan:
    """What one targeted trigger is allowed to work on."""

    window_minutes: int = DEFAULT_WINDOW_MINUTES
    after_kickoff_minutes: int = DEFAULT_RETRY_AFTER_KICKOFF_MINUTES
    retry_interval_minutes: int = BUCKET_MINUTES
    targets: Set[str] = field(default_factory=set)
    match_keys: Set[str] = field(default_factory=set)
    target_names: List[str] = field(default_factory=list)
    already_attempted: int = 0
    already_resolved: int = 0
    reopened_dead_link: int = 0
    refresh_considered: int = 0
    refresh_healthy: int = 0
    refresh_unresolved: int = 0
    same_bucket: int = 0
    outside_window: int = 0
    considered: int = 0
    kickoff_from: Optional[datetime] = None
    kickoff_to: Optional[datetime] = None

    @property
    def should_scan(self) -> bool:
        """No target means no fetch, no verification and no publish."""
        return bool(self.targets)

    def accepts(self, candidate: Dict[str, Any]) -> bool:
        """Is verifying this candidate work this trigger is allowed to do?

        Two ways to qualify. It names one of the targets - that is the normal
        case. Or its own kickoff falls inside the window, which makes it a
        fixture about to start by its own timestamp even when its title spells
        the teams differently from the published card. Without the second test a
        source that writes "AUS v BAN" where the card says "Australia vs
        Bangladesh" would be filtered out and the target would never get a link.
        """
        if not self.targets or not isinstance(candidate, dict):
            return False
        if match_keys_for(candidate) & self.match_keys:
            return True
        if self.kickoff_from is None or self.kickoff_to is None:
            return False
        start = parse_time(candidate.get("start_time") or candidate.get("start_at"))
        return bool(start and self.kickoff_from <= start <= self.kickoff_to)

    def summary(self) -> Dict[str, Any]:
        return {
            "window_minutes": self.window_minutes,
            "after_kickoff_minutes": self.after_kickoff_minutes,
            "retry_interval_minutes": self.retry_interval_minutes,
            "targets": len(self.targets),
            "target_names": self.target_names[:20],
            # Kept under its old name: the total skipped by the ledger, which
            # is what scan.py prints and what every earlier report meant.
            "already_attempted_skipped": self.already_attempted,
            "resolved_skipped": self.already_resolved,
            "reopened_dead_link": self.reopened_dead_link,
            "refresh_candidates_considered": self.refresh_considered,
            "refresh_healthy_left_alone": self.refresh_healthy,
            "refresh_never_resolved_skipped": self.refresh_unresolved,
            "same_bucket_skipped": self.same_bucket,
            "outside_window_skipped": self.outside_window,
            "fixtures_considered": self.considered,
            "candidate_match_keys": len(self.match_keys),
        }


def select_targets(
    fixtures: Iterable[Dict[str, Any]],
    ledger: Dict[str, Any],
    *,
    now: Optional[datetime] = None,
    window_minutes: int = DEFAULT_WINDOW_MINUTES,
    after_kickoff_minutes: int = DEFAULT_RETRY_AFTER_KICKOFF_MINUTES,
    retry_interval_minutes: int = BUCKET_MINUTES,
    refresh_candidates: Iterable[Dict[str, Any]] = (),
) -> TargetPlan:
    """Pick the fixtures this trigger may scan.

    A fixture is a target when its kickoff is inside
    [now - after_kickoff, now + window], it does not already have a working
    link, and this five-minute slot has not already spent an attempt on it.
    Everything else is skipped: it is not fetched, not verified, and its
    previously published card is left alone.

    "Does not already have a working link" is two questions, not one. A
    fixture the ledger has never resolved obviously has none. A fixture the
    ledger DID resolve is left alone only while its published card still
    carries a playable link - because these URLs rotate and expire, and a
    link found at T-25 can be dead by kickoff.

    Two separate faults are behind that sentence.

    It used to read "and it has not already been scanned once", which meant
    a fixture whose one attempt came back empty was never tried again - not
    at -10, not at -5, not after the source finally published a link. The
    committed ledger showed what that cost: eleven fixtures, every one at
    attempts 1, eight of them still unresolved and permanently so.

    And the window used to end at kickoff, so the search stopped at the exact
    moment a viewer might first look for the match. A source that publishes
    its link when play starts could not be heard at all. It now runs to
    `now - after_kickoff`, which by default is ten minutes past kickoff.

    The number of attempts a fixture actually receives is whatever the number
    of five-minute slots the trigger managed to reach inside that window -
    seven if every tick lands, fewer when GitHub delays or drops one, and
    fewer again for a fixture first seen halfway through. Nothing here counts
    attempts or expects a particular number of them; eligibility is decided
    per slot, from the clock, so a missed tick costs one slot and no more.
    """
    reference = now or _now()
    ahead = max(1, int(window_minutes))
    behind = max(0, int(after_kickoff_minutes))
    interval = max(1, int(retry_interval_minutes))
    horizon = reference + timedelta(minutes=ahead)
    earliest = reference - timedelta(minutes=behind)
    plan = TargetPlan(
        window_minutes=ahead,
        after_kickoff_minutes=behind,
        retry_interval_minutes=interval,
        kickoff_from=earliest,
        kickoff_to=horizon,
    )

    # A fixture promoted to Today takes its stream with it and leaves the
    # Upcoming list, so the only card that can show a resolved stream has died
    # is the Today one. It is keyed here rather than walked separately, because
    # the same fixture can briefly appear in both lists and must be decided
    # ONCE. `fixture_key` is the published card id - the thing that survives
    # the Upcoming -> Today move - so this is identity, not a name comparison.
    refresh_by_key: Dict[str, Dict[str, Any]] = {}
    for candidate in refresh_candidates:
        if not isinstance(candidate, dict):
            continue
        candidate_key = fixture_key(candidate)
        if candidate_key:
            refresh_by_key.setdefault(candidate_key, candidate)

    def consider_refresh(key: str, card: Dict[str, Any]) -> None:
        """The narrow rule for a card that already had a link.

        Three ways out, and every one of them is the common case:

          never had a link nothing was ever found for it, so nothing was
                           lost. The ordinary ladder owns it - from the
                           Upcoming list, or from the waiting Today cards
                           once it has been promoted.
          still playable   the whole point. A working stream is not rescanned,
                           whatever else is true of it.
          same slot        a refresh is an attempt like any other.

        The window is deliberately not applied. A live match is hours past its
        kickoff - the working card in the published data is 300 minutes past -
        so a T+10 bound would make this unreachable, which is the fault being
        fixed. What bounds it instead: the card must still name a route, must
        not call itself finished, and its ledger entry is pruned twelve hours
        after kickoff.
        """
        plan.refresh_considered += 1
        if not ever_had_a_link(ledger, key):
            plan.refresh_unresolved += 1
            return
        if not link_has_died(card):
            plan.refresh_healthy += 1
            plan.already_resolved += 1
            return
        if last_attempt_bucket(ledger, key) == attempt_bucket(
                reference, interval):
            plan.already_attempted += 1
            plan.same_bucket += 1
            return
        plan.targets.add(key)
        plan.match_keys |= match_keys_for(card)
        plan.reopened_dead_link += 1
        name = str(card.get("name") or "").strip()
        if name:
            plan.target_names.append(name)

    for item in fixtures:
        if not isinstance(item, dict):
            continue
        key = fixture_key(item)
        if not key:
            continue
        plan.considered += 1

        # The same fixture in both lists is one fixture, and the Today card is
        # the one that knows about the stream: an Upcoming card is an
        # announcement and says nothing either way. So when both exist, the
        # route-bearing card decides, and the announcement is not consulted.
        promoted = refresh_by_key.pop(key, None)
        if promoted is not None:
            consider_refresh(key, promoted)
            continue

        start = parse_time(item.get("start_time") or item.get("start_at"))
        if start is None or not (earliest <= start <= horizon):
            plan.outside_window += 1
            continue
        reason = retry_skip_reason(
            ledger, key, now=reference, item=item, bucket_minutes=interval
        )
        if reason:
            plan.already_attempted += 1
            if reason == SKIP_RESOLVED:
                plan.already_resolved += 1
            else:
                plan.same_bucket += 1
            continue
        if is_resolved(ledger, key):
            # It was resolved and is being hunted again, which only happens
            # when its own card lost the link. Counted so a report can say
            # "this many streams died near kickoff" rather than leaving it
            # indistinguishable from a fixture that never had one.
            plan.reopened_dead_link += 1

        plan.targets.add(key)
        plan.match_keys |= match_keys_for(item)
        name = str(item.get("name") or "").strip()
        if name:
            plan.target_names.append(name)

    # Whatever is left is on Today and not on Upcoming, which is the ordinary
    # shape of a promoted fixture.
    for key, card in sorted(refresh_by_key.items()):
        consider_refresh(key, card)

    return plan


def record_outcome(
    ledger: Dict[str, Any],
    plan: TargetPlan,
    published_items: Iterable[Dict[str, Any]],
    *,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Write back what this trigger achieved.

    Every target is marked `attempted` here, so none of them can be targeted
    again - link found or not. `resolved` only records which of them ended up
    with a playable link, for the scan report.
    """
    reference = now or _now()
    fixtures: Dict[str, Any] = ledger.setdefault("fixtures", {})

    published: Dict[str, Dict[str, Any]] = {}
    for item in published_items:
        key = fixture_key(item)
        if key:
            published[key] = item

    for key in sorted(plan.targets):
        record = fixtures.get(key) if isinstance(fixtures.get(key), dict) else {}
        item = published.get(key)
        entry: Dict[str, Any] = {
            # `attempted`/`attempted_at` are the FIRST attempt and never move.
            "attempted": True,
            "attempted_at": record.get("attempted_at") or reference.isoformat(),
            # Incremented rather than pinned at 1. A fixture can be targeted
            # once per five-minute slot now, so this count is the ladder as
            # the ledger sees it, and it is what a report can show to say how
            # hard a link was hunted for before kickoff.
            "attempts": attempt_count(ledger, key) + 1,
            # Written now, read by nothing yet. The retry ladder needs to know
            # which slot the last attempt belonged to before it can allow a
            # second one, and a field that only appears the first time a retry
            # happens would leave every pre-existing entry undecidable.
            "last_attempt_at": reference.isoformat(),
            "last_attempt_bucket": attempt_bucket(
                reference, plan.retry_interval_minutes),
            "name": str((item or {}).get("name") or record.get("name") or ""),
            "start_time": str((item or {}).get("start_time") or record.get("start_time") or ""),
        }
        if item is not None and has_valid_link(item):
            entry["resolved"] = True
            entry["resolved_at"] = reference.isoformat()
            # `first_link_at` is carried forward once set - it answers "how
            # long before kickoff did a link first exist", which a later write
            # must not overwrite. `last_success_at` is the opposite: always the
            # most recent, so a link that rotates mid-match can be seen to have
            # been re-confirmed.
            entry["first_link_at"] = (
                str(record.get("first_link_at") or "") or reference.isoformat()
            )
            entry["last_success_at"] = reference.isoformat()
            # A link that died and was found again is a recovery, and the
            # recovery is only legible if the loss is still on the record.
            # Dropping it here would have made the second success
            # indistinguishable from an uneventful first one.
            if record.get("link_lost_at"):
                entry["link_lost_at"] = str(record["link_lost_at"])
            # Redacted, and the value is informational only - nothing reads
            # it back. It was storing the resolved stream URL verbatim, and
            # this file is committed to a public repository: eight of the
            # thirty-three URLs in the committed ledger carried live `token=`
            # query values. The rest of this project stores route identities
            # through route_evidence for exactly this reason; this one writer
            # was missed.
            entry["route_id"] = rev.normalize_source_identity(
                str(item.get("url") or "")
            )
            entry["url_public_template"] = rev.redact_public_template(
                str(item.get("url") or "")
            )
        else:
            entry["resolved"] = False
            # No link this time. Anything a previous attempt established about
            # links is kept rather than dropped, so the history survives a
            # later empty attempt - and survives a link that worked once,
            # died, and was hunted again without success.
            for carried in ("first_link_at", "last_success_at"):
                if record.get(carried):
                    entry[carried] = str(record[carried])
            # Which is worth saying out loud in the ledger, because "never
            # had a link" and "had one and lost it" are different problems.
            if record.get("resolved") is True:
                entry["link_lost_at"] = (
                    str(record.get("link_lost_at") or "") or reference.isoformat()
                )
            elif record.get("link_lost_at"):
                entry["link_lost_at"] = str(record["link_lost_at"])
        fixtures[key] = entry

    _prune(fixtures, reference)
    ledger["updated_at"] = reference.isoformat()
    return ledger


def _prune(fixtures: Dict[str, Any], reference: datetime) -> None:
    cutoff = reference - timedelta(hours=LEDGER_RETENTION_HOURS)
    for key in list(fixtures):
        record = fixtures.get(key)
        if not isinstance(record, dict):
            fixtures.pop(key, None)
            continue
        start = parse_time(record.get("start_time"))
        attempted = parse_time(record.get("attempted_at") or record.get("last_targeted_at"))
        stamp = start or attempted
        if stamp is not None and stamp < cutoff:
            fixtures.pop(key, None)


def known_upcoming_fixtures(
    data_dir: Path | str = "data",
    fixture_path: Path | str = "config/event-fixtures.json",
) -> List[Dict[str, Any]]:
    """The fixture list a targeted trigger reasons about, read locally only.

    data/upcoming.json is the authority: those are the cards a full Upcoming
    scan already published, each with the kickoff this decision needs. The
    fixture catalogue is read too, for the case where it carries an explicit
    fixture list, but it is optional. Both are on disk, so deciding whether
    anything is inside the window costs no network request at all - which is
    what makes a five-minute trigger cheap.
    """
    fixtures: List[Dict[str, Any]] = []
    seen: Set[str] = set()

    def absorb(items: Any) -> None:
        if not isinstance(items, list):
            return
        for item in items:
            if not isinstance(item, dict):
                continue
            key = fixture_key(item)
            if not key or key in seen:
                continue
            seen.add(key)
            fixtures.append(item)

    published = Path(data_dir) / "upcoming.json"
    try:
        payload = json.loads(published.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {}
    if isinstance(payload, dict):
        absorb(payload.get("items"))

    try:
        catalogue = json.loads(Path(fixture_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        catalogue = {}
    if isinstance(catalogue, dict):
        for key in ("fixtures", "items", "events", "matches"):
            absorb(catalogue.get(key))
    elif isinstance(catalogue, list):
        absorb(catalogue)

    return fixtures


#: Fields a published card states its finished-ness in. Read through
#: live_protection.ENDED_STATUSES so there is one vocabulary, not two.
_ENDED_FIELDS = ("schedule_status", "status", "lifecycle_state",
                 "original_status")


def _names_a_route(item: Dict[str, Any]) -> bool:
    if item.get("metadata_only") is True:
        return False
    if str(item.get("playback_id") or "").strip():
        return True
    url = str(item.get("url") or item.get("stream_url") or "").strip().lower()
    return url.startswith(("http://", "https://"))


def _has_ended(item: Dict[str, Any]) -> bool:
    for field_name in _ENDED_FIELDS:
        if str(item.get(field_name) or "").strip().upper() in ENDED_STATUSES:
            return True
    return False


def known_today_refresh_candidates(
    data_dir: Path | str = "data",
) -> List[Dict[str, Any]]:
    """Today Match cards that NAME a route - the dead-link refresh list.

    A fixture promoted to Today takes its stream with it and leaves the
    Upcoming list, so `known_upcoming_fixtures` cannot see it. That is the
    one place a resolved stream actually lives, and therefore the only
    place its death can be observed. Without this the reopen rule is real
    code that nothing can ever reach.

    Deliberately narrow, because this list is NOT the ordinary ladder:

      * only cards naming a playback_id or an http(s) url. A card carrying
        no route is saying nothing about a stream, and a list of those
        would be a mass-reopen of every fixture that never found a link.
      * `metadata_only` excluded outright, which is what a Today card
        still waiting for its first link looks like - LINK UPDATING. The
        ordinary ladder owns those, and this must not quietly widen it.
        It reaches them through `waiting_today_fixtures`, which is the
        exact complement of this list.
      * nothing a card calls finished, read through
        live_protection.ENDED_STATUSES so the two agree.

    Local read only, like the rest of the planner: no network, no probe.
    """
    published = Path(data_dir) / "today-match.json"
    try:
        payload = json.loads(published.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return []

    candidates: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        key = fixture_key(item)
        if not key or key in seen:
            continue
        if not _names_a_route(item) or _has_ended(item):
            continue
        seen.add(key)
        candidates.append(item)
    return candidates


def waiting_today_fixtures(
    data_dir: Path | str = "data",
) -> List[Dict[str, Any]]:
    """Today Match cards that name NO route - still waiting for a link.

    The ordinary ladder used to find every one of these on the Upcoming
    list, because a fixture with no link stayed there. It does not any
    more: a trigger that sees a fixture cross the routing threshold now
    promotes it to Today Match link or no link, which is what the
    lifecycle asks for. Without this function that promotion would end
    the hunt for exactly the fixtures that still need one - they would
    leave the Upcoming list at T-25 and never be looked for again.

    The opposite of `known_today_refresh_candidates`, which takes the
    cards that DO name a route. Between them every Today card is
    accounted for exactly once, and neither list can claim the same
    fixture as the other.

    This is not a widening of the window. These cards go through the
    ordinary path in `select_targets` - same kickoff window, same
    five-minute slot gate, same ledger - so a promoted fixture is
    hunted on exactly the terms it was hunted on before it moved.

    Local read only, like the rest of the planner: no network, no probe.
    """
    published = Path(data_dir) / "today-match.json"
    try:
        payload = json.loads(published.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return []

    waiting: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        key = fixture_key(item)
        if not key or key in seen:
            continue
        if _names_a_route(item) or _has_ended(item):
            continue
        seen.add(key)
        waiting.append(item)
    return waiting


def ladder_candidates(
    data_dir: Path | str = "data",
    fixture_path: Path | str = "config/event-fixtures.json",
) -> List[Dict[str, Any]]:
    """Every fixture the ordinary ladder may chase, each one once.

    The Upcoming list first, because that is where a fixture spends most
    of its life, then the Today cards still waiting for a first link.
    Keyed on `fixture_key` - the published card id - so a fixture caught
    mid-move, present in a stale copy of both files, is one candidate.
    """
    candidates = known_upcoming_fixtures(
        data_dir=data_dir, fixture_path=fixture_path
    )
    seen: Set[str] = {
        key for key in (fixture_key(item) for item in candidates) if key
    }
    for card in waiting_today_fixtures(data_dir=data_dir):
        key = fixture_key(card)
        if not key or key in seen:
            continue
        seen.add(key)
        candidates.append(card)
    return candidates


def plan_targeted_upcoming_scan(
    *,
    data_dir: Path | str = "data",
    fixture_path: Path | str = "config/event-fixtures.json",
    state_path: Path | str = STATE_FILE,
    now: Optional[datetime] = None,
    window_minutes: Optional[int] = None,
    after_kickoff_minutes: Optional[int] = None,
    retry_interval_minutes: Optional[int] = None,
    settings_path: Path | str = "config/settings.json",
) -> TargetPlan:
    """Decide, before anything is fetched, what this trigger should scan.

    The three timings come from config/settings.json -> event_lifecycle,
    through `targeted_timings`, and an argument overrides one only when a
    caller passes it - which in production nothing does. The module-level
    constants remain as the fallback inside that loader, so a missing or
    unreadable config still produces a working plan rather than an error.
    """
    timings = targeted_timings(settings_path=settings_path)
    return select_targets(
        ladder_candidates(data_dir=data_dir, fixture_path=fixture_path),
        load_ledger(state_path),
        now=now,
        window_minutes=(
            timings["window_minutes"] if window_minutes is None
            else window_minutes
        ),
        after_kickoff_minutes=(
            timings["after_kickoff_minutes"] if after_kickoff_minutes is None
            else after_kickoff_minutes
        ),
        retry_interval_minutes=(
            timings["retry_interval_minutes"] if retry_interval_minutes is None
            else retry_interval_minutes
        ),
        refresh_candidates=known_today_refresh_candidates(data_dir=data_dir),
    )
