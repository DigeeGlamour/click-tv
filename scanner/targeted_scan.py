"""Requirement 4 - the -15 minute targeted Upcoming scan.

The trigger fires every five minutes. That must not mean the same fixture is
scanned every five minutes. Each fixture is scanned **exactly once**, on the
first trigger that finds its kickoff inside the T-15 window, and never again -
whether or not that scan found it a link. There is deliberately no retry at -10
or at -5 minutes.

A ledger in state/upcoming-targeting.json records that single attempt:

    {"fixtures": {"<key>": {"attempted": true, "attempted_at": "...",
                            "resolved": true, "url": "https://..."}}}

`attempted` is what suppresses further targeting; `resolved` only records the
outcome for the scan report. Once a fixture is attempted it is skipped on every
later trigger until its entry is pruned, well after kickoff. Nothing outside the
window is a target at all, so no source is fetched and no stream is verified on
behalf of a fixture that is still hours away.
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

STATE_FILE = Path("state/upcoming-targeting.json")
DEFAULT_WINDOW_MINUTES = 15

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
    """Has this fixture already had its one targeted scan?

    This, not `resolved`, is the suppression test. A fixture whose single scan
    found nothing must not be tried again on the next five-minute trigger.
    """
    record = (ledger.get("fixtures") or {}).get(key)
    if not isinstance(record, dict):
        return False
    # `attempts` is read too, so a ledger written by the previous build - which
    # only counted attempts - still suppresses correctly after an upgrade.
    return bool(
        record.get("attempted") is True
        or int(record.get("attempts") or 0) > 0
        or record.get("resolved") is True
    )


@dataclass
class TargetPlan:
    """What one targeted trigger is allowed to work on."""

    window_minutes: int = DEFAULT_WINDOW_MINUTES
    targets: Set[str] = field(default_factory=set)
    match_keys: Set[str] = field(default_factory=set)
    target_names: List[str] = field(default_factory=list)
    already_attempted: int = 0
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
            "targets": len(self.targets),
            "target_names": self.target_names[:20],
            "already_attempted_skipped": self.already_attempted,
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
) -> TargetPlan:
    """Pick the fixtures this trigger may scan.

    A fixture is a target when its kickoff is inside [now, now + window] and it
    has not already been scanned once. Everything else is skipped: it is not
    fetched, not verified, and its previously published card is left alone.
    There is deliberately no retry - a fixture already attempted at -15 minutes
    is not attempted again at -10 or at -5.
    """
    reference = now or _now()
    horizon = reference + timedelta(minutes=max(1, int(window_minutes)))
    plan = TargetPlan(
        window_minutes=max(1, int(window_minutes)),
        kickoff_from=reference,
        kickoff_to=horizon,
    )

    for item in fixtures:
        if not isinstance(item, dict):
            continue
        key = fixture_key(item)
        if not key:
            continue
        plan.considered += 1

        start = parse_time(item.get("start_time") or item.get("start_at"))
        if start is None or not (reference <= start <= horizon):
            plan.outside_window += 1
            continue
        if is_attempted(ledger, key):
            plan.already_attempted += 1
            continue

        plan.targets.add(key)
        plan.match_keys |= match_keys_for(item)
        name = str(item.get("name") or "").strip()
        if name:
            plan.target_names.append(name)

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
            # The single attempt. Nothing clears this until the entry is pruned.
            "attempted": True,
            "attempted_at": record.get("attempted_at") or reference.isoformat(),
            "attempts": 1,
            "name": str((item or {}).get("name") or record.get("name") or ""),
            "start_time": str((item or {}).get("start_time") or record.get("start_time") or ""),
        }
        if item is not None and has_valid_link(item):
            entry["resolved"] = True
            entry["resolved_at"] = reference.isoformat()
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


def plan_targeted_upcoming_scan(
    *,
    data_dir: Path | str = "data",
    fixture_path: Path | str = "config/event-fixtures.json",
    state_path: Path | str = STATE_FILE,
    now: Optional[datetime] = None,
    window_minutes: int = DEFAULT_WINDOW_MINUTES,
) -> TargetPlan:
    """Decide, before anything is fetched, what this trigger should scan."""
    return select_targets(
        known_upcoming_fixtures(data_dir=data_dir, fixture_path=fixture_path),
        load_ledger(state_path),
        now=now,
        window_minutes=window_minutes,
    )
