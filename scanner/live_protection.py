"""Requirement 6 - already-LIVE event protection.

A Today Match scan reads live third-party playlists. One of them timing out, or
returning a short body, is routine; treating that as "the match is over" wipes a
card the viewer may be watching. So a live event that disappears from a scan is
never removed because of how many scans missed it.

Corrected rule: a missing live event is removed for exactly two reasons.

  * an authoritative ENDED/FT status arrives, or
  * its previously published link is genuinely dead - every link on the card,
    primary and backups, fails a live playability probe.

The number of consecutive misses is recorded for the scan report only. While the
previous link still answers as live, valid and playable, the card is carried
forward however many scans in a row missed it. An inconclusive probe (no probe
available, or the probe itself errored) always preserves the card, because
"unable to check" must never look like "confirmed dead".
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

try:
    from scanner.event_lifecycle import (
        END_PENDING,
        ENDED,
        LIVE,
        LifecycleSignals,
        apply_verdict,
        authority_says_live,
        decide as decide_lifecycle,
        estimate_passed,
        has_strong_end_signal,
    )
except ImportError:  # pragma: no cover - direct module execution
    from event_lifecycle import (  # type: ignore
        END_PENDING,
        ENDED,
        LIVE,
        LifecycleSignals,
        apply_verdict,
        authority_says_live,
        decide as decide_lifecycle,
        estimate_passed,
        has_strong_end_signal,
    )

STATE_FILE = Path("state/live-event-protection.json")
DEFAULT_GRACE_MINUTES = 90
# The published contract: one primary plus at most five backups.
MAX_PUBLISHED_BACKUPS = 5
ENDED_STATUSES = frozenset({"ENDED", "FT", "FINISHED", "COMPLETED", "AET", "PEN", "AWD", "WO"})

# A probe verdict. True = the link is live and playable, False = confirmed dead,
# None = could not be determined and therefore must not remove anything.
LinkProbe = Callable[[Dict[str, Any]], Optional[bool]]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _load(path: Path) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


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


def is_authoritatively_ended(card: Dict[str, Any]) -> bool:
    for field in ("schedule_status", "status", "original_status"):
        if str(card.get(field) or "").strip().upper() in ENDED_STATUSES:
            return True
    return False


def card_link_urls(card: Dict[str, Any]) -> List[str]:
    """Every playable link on a published card, primary first.

    A card whose primary rotated away but whose backup still plays is not dead,
    so the probe has to see the backups too.
    """
    urls: List[str] = []
    seen: set[str] = set()

    def add(value: Any) -> None:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            urls.append(text)

    for field in ("url", "stream_url", "link"):
        add(card.get(field))
    for field in ("backups", "links", "sources", "standby"):
        children = card.get(field)
        if not isinstance(children, list):
            continue
        for child in children:
            if isinstance(child, str):
                add(child)
            elif isinstance(child, dict):
                for key in ("url", "stream_url", "link"):
                    add(child.get(key))
    return urls


def card_playback_ids(card: Dict[str, Any]) -> List[str]:
    """The playback_id of the primary and of every backup, in that order."""
    ids: List[str] = []
    seen: set[str] = set()

    def add(value: Any) -> None:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            ids.append(text)

    add(card.get("playback_id"))
    for backup in card.get("backups") or []:
        if isinstance(backup, dict):
            add(backup.get("playback_id"))
    return ids


def resolve_card_streams(
    card: Dict[str, Any],
    data_root: Path | str = "data",
) -> List[Tuple[str, Dict[str, str]]]:
    """Every (url, headers) pair this card can actually be played from.

    A published card carries no stream URL - only a playback_id, because the
    real URL and its headers live in the playback catalogue where the proxy
    Worker reads them. Probing the card therefore means resolving those profiles
    first; probing what the card itself carries would find nothing at all and
    report every event as unverifiable.
    """
    streams: List[Tuple[str, Dict[str, str]]] = []
    seen: set[str] = set()
    root = Path(data_root)

    def add(url: Any, headers: Any) -> None:
        text = str(url or "").strip()
        if not text or text in seen:
            return
        seen.add(text)
        streams.append((text, headers if isinstance(headers, dict) else {}))

    shard_cache: Dict[str, Dict[str, Any]] = {}
    for playback_id in card_playback_ids(card):
        prefix = playback_id[4:] if playback_id.startswith("ctv_") else playback_id
        shard = prefix[:2].lower()
        if shard not in shard_cache:
            try:
                payload = json.loads(
                    (root / "playback" / f"{shard}.json").read_text(encoding="utf-8")
                )
            except (OSError, UnicodeError, json.JSONDecodeError):
                payload = {}
            records = payload.get("records") if isinstance(payload, dict) else None
            shard_cache[shard] = records if isinstance(records, dict) else {}
        profile = shard_cache[shard].get(playback_id)
        if isinstance(profile, dict):
            add(profile.get("url"), profile.get("headers"))

    # A card that still carries its own link (an older snapshot, or a test
    # fixture) is probed directly.
    card_headers = card.get("headers") if isinstance(card.get("headers"), dict) else {}
    for url in card_link_urls(card):
        add(url, card_headers)
    return streams


def probe_card_is_playable(
    card: Dict[str, Any],
    *,
    timeout_seconds: int = 6,
    data_root: Path | str = "data",
) -> Optional[bool]:
    """Default probe: is any link on this card still live and playable?

    Returns True on the first link that answers with a real live manifest or
    media body, False when every link was reached and rejected, and None when
    no link could be judged at all (network down, no profile to resolve). None
    preserves the card - see the module docstring.
    """
    streams = resolve_card_streams(card, data_root=data_root)
    if not streams:
        return None

    try:
        from scanner.verifier import (  # local import: keeps this module importable offline
            _build_request_headers,
            _fetch_once,
            _looks_like_html,
            _split_pipe_headers,
        )
    except Exception:  # pragma: no cover - a probe that cannot run is inconclusive
        return None

    reached_and_rejected = False

    for raw_url, headers_source in streams:
        request_url, pipe_headers = _split_pipe_headers(raw_url)
        if not request_url.lower().startswith(("http://", "https://")):
            continue
        try:
            result = _fetch_once(
                url=request_url,
                headers=_build_request_headers(headers_source, pipe_headers),
                timeout=max(2, int(timeout_seconds)),
                verify_ssl=False,
                max_bytes=64 * 1024,
            )
        except Exception:
            # A transport error is not proof of death; try the next link.
            continue

        status = int(result.get("status_code") or 0)
        body = result.get("body") or b""
        content_type = str(result.get("content_type") or "").lower()

        if result.get("ok") is not True:
            # 404/403/410 and 5xx are answers from the origin: this link is gone.
            if status:
                reached_and_rejected = True
            continue

        if not body or _looks_like_html(body, content_type):
            reached_and_rejected = True
            continue

        text = body[:4096].decode("utf-8", "ignore").upper()
        if "#EXTM3U" in text:
            # An HLS playlist that lists no media at all is an empty shell.
            if "#EXT-X-ENDLIST" in text and "#EXTINF" not in text:
                reached_and_rejected = True
                continue
            return True
        if "<MPD" in text or "URN:MPEG:DASH" in text:
            return True
        if content_type.startswith(("video/", "audio/")) or "mp2t" in content_type:
            return True
        reached_and_rejected = True

    return False if reached_and_rejected else None


def _reconcile_layer():
    """Lazy handles for reconciling a carried card with a canonical one.

    All optional: if any part is unavailable the reconciler is skipped entirely
    and protection behaves exactly as it did before, which is the safe direction.
    """
    try:
        from scanner.merger import same_real_fixture
        from scanner.channel_groups import (
            ROLE_BACKUP, ROLE_PRIMARY, channel_id_for, _public_stream,
        )
        from scanner.channel_resolver import load_alias_map, resolve_channel_name
    except Exception:  # pragma: no cover - optional layer
        return None
    return {
        "same_fixture": same_real_fixture,
        "channel_id_for": channel_id_for,
        "public_stream": _public_stream,
        "resolve_channel": resolve_channel_name,
        "aliases": load_alias_map(),
        "primary": ROLE_PRIMARY,
        "backup": ROLE_BACKUP,
    }


# Exactly what a published backup entry carries. Copying a card wholesale into a
# backup slot would drag the card's own name, id, lifecycle fields and - worst -
# its nested backups list in with it, so the fields are named rather than
# filtered. Nothing here can hold a raw URL, header or DRM key: a published
# stream is reached through its playback_id and section 17 keeps it that way.
_BACKUP_FIELDS = (
    "header_profile", "proxy_mode", "stream_type", "requires_headers",
    "inherit_manifest_query", "verification_mode", "verification_status",
    "verification_badge", "verified", "publish_allowed", "source_id",
    "playback_id", "protected_source", "requires_credentials",
    "credential_hints", "host", "resolution", "resolution_height", "drm",
)


def _carried_streams(card: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The carried card's own streams, primary first, deduplicated.

    A published card keeps no raw URL - only a playback_id, which is content
    addressed over the effective playback configuration and is therefore the
    stream's true identity. So that is what identity and de-duplication use here.
    """
    streams: List[Dict[str, Any]] = []
    seen: Set[str] = set()

    def add(entry: Any, is_primary: bool) -> None:
        if not isinstance(entry, dict):
            return
        playback_id = str(entry.get("playback_id") or "").strip()
        if not playback_id or playback_id in seen:
            return
        seen.add(playback_id)
        stream = {
            field: entry[field] for field in _BACKUP_FIELDS
            if entry.get(field) not in (None, "")
        }
        stream["playback_id"] = playback_id
        stream["_was_primary"] = is_primary
        streams.append(stream)

    add(card, True)
    for backup in card.get("backups") or []:
        add(backup, False)
    return streams


def _absorb_carried_card(
    canonical: Dict[str, Any],
    carried: Dict[str, Any],
    layer: Dict[str, Any],
) -> int:
    """Move a carried card's streams and its broadcaster onto the canonical card.

    Requirements this has to hold to at once:

      * the event does not disappear - it is present, under its canonical card;
      * no proven-playable stream is lost - the carried primary is placed at the
        front of the canonical card's backups, so it is the first thing tried
        after the canonical primary;
      * the canonical primary is not touched, so requirement 14/16 stickiness and
        any pinned session on the canonical card are unaffected;
      * the carried card's broadcaster becomes a selectable channel of the
        canonical event, which is what sections 6-10 ask for.

    Returns how many streams were absorbed.
    """
    incoming = _carried_streams(carried)
    if not incoming:
        return 0

    existing = [b for b in (canonical.get("backups") or []) if isinstance(b, dict)]
    known = {str(b.get("playback_id") or "").strip() for b in existing}
    known.discard("")
    known.add(str(canonical.get("playback_id") or "").strip())
    fresh = [s for s in incoming if str(s.get("playback_id")) not in known]

    # The carried primary was proven playable, so it leads the fresh arrivals.
    fresh.sort(key=lambda s: 0 if s.get("_was_primary") else 1)

    merged: List[Dict[str, Any]] = []
    for index, stream in enumerate(fresh + existing):
        entry = {k: v for k, v in stream.items() if k != "_was_primary"}
        entry["name"] = f"Backup-{index + 1}"
        merged.append(entry)
        if len(merged) >= MAX_PUBLISHED_BACKUPS:
            break
    canonical["backups"] = merged
    canonical["available_link_count"] = 1 + len(merged)

    # Sections 6-10. The carried card's title is where its broadcaster lives.
    channel_name = layer["resolve_channel"](
        {"name": carried.get("name"), "channel_name": carried.get("channel_name")},
        carried.get("name") or "",
        layer["aliases"],
    )
    if channel_name.resolved:
        channel_id = layer["channel_id_for"](canonical.get("id") or "", channel_name)
        channels = [c for c in (canonical.get("channels") or []) if isinstance(c, dict)]
        if channel_id and not any(str(c.get("id")) == channel_id for c in channels):
            published = []
            for index, stream in enumerate(incoming):
                entry = layer["public_stream"](
                    stream, f"{channel_id}--{index + 1}",
                    layer["primary"] if index == 0 else layer["backup"],
                )
                # A carried stream has no URL to hash, so its content-addressed
                # playback id is its variant identity.
                entry["variant_key"] = str(stream.get("playback_id") or "")
                published.append(entry)
            channels.append({
                "id": channel_id,
                "name": channel_name.name,
                "normalized_name": channel_name.normalized,
                "logo": str(carried.get("logo") or ""),
                "name_confidence": channel_name.confidence,
                "name_source": channel_name.source_field,
                "provider": str(carried.get("source_id") or ""),
                "source_ids": sorted({str(carried.get("source_id") or "").strip()} - {""}),
                "primary_stream_id": published[0]["id"],
                "stream_count": len(published),
                "backup_count": max(0, len(published) - 1),
                "verified": bool(carried.get("verified")),
                "verification_status": str(carried.get("verification_status") or ""),
                "playback_types": ["native"],
                "renderer": "native",
                "streams": published,
                "absorbed_from_event_id": str(carried.get("id") or ""),
            })
            canonical["channels"] = channels
            canonical["channel_count"] = len(channels)
            # Section 27's rule, in its other shape: the canonical card's own
            # primary is not inside this channel, so making the channel the
            # default would reorder the playback plan and demote a working
            # primary. The default is left as it was.
            canonical.setdefault("default_channel_id", "")

    # Event-id continuity: whatever was pinned or bookmarked against the carried
    # id can still be resolved to the card that replaced it.
    absorbed = [
        value for value in (canonical.get("absorbed_event_ids") or [])
        if isinstance(value, str) and value
    ]
    carried_id = str(carried.get("id") or "")
    if carried_id and carried_id not in absorbed:
        absorbed.append(carried_id)
    canonical["absorbed_event_ids"] = absorbed
    canonical["reconciled_duplicate_count"] = len(absorbed)
    return len(incoming)


def _canonicalness(card: Dict[str, Any], layer: Dict[str, Any]) -> Tuple[int, int, int]:
    """How well this card can stand as the event's single card.

    Section 5's rule decides it: a title that still carries a broadcaster
    ("India vs Sri Lanka Willow") is the channel's name for the match, and a
    title that does not ("Sri Lanka vs India 1st Test") is the match's own. The
    second is the card to keep. A live card outranks one that is winding down,
    and more links breaks the remaining ties.
    """
    name = str(card.get("name") or "")
    try:
        resolved = layer["resolve_channel"](
            {"name": name}, name, layer["aliases"]
        ).resolved
    except Exception:  # pragma: no cover - never rank-crash a scan
        resolved = False
    return (
        0 if resolved else 1,
        1 if str(card.get("lifecycle_state") or "") != END_PENDING else 0,
        len(card.get("backups") or []),
    )


def _reconcile_carried_cards(
    today_items: List[Dict[str, Any]],
    carried: List[Dict[str, Any]],
    playing: Set[str],
    misses: Dict[str, Any],
    stats: Dict[str, Any],
    layer: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """One real match, one card - including when both cards were carried.

    A carried card has never been through the merge, so §1/§3 never saw it and
    the same fixture could publish twice under two spellings. The duplicate that
    shipped was not "fresh card beside carried card" either: on a scan where the
    live playlist is empty, BOTH cards are carried, so there is no fresh card to
    fold into and the reconciliation has to happen among the carried set itself.

    Rules, in order:
      * a card the viewer is watching is never touched, at all;
      * a card this scan actually produced is a host and is never absorbed;
      * among carried cards the most canonical one is kept and the rest fold into
        it, so the surviving card is the match's own title rather than a
        broadcaster's name for it.
    """
    untouchable: List[Dict[str, Any]] = []
    foldable: List[Dict[str, Any]] = []
    for card in carried:
        if str(card.get("id") or "") in playing:
            stats["reconciled_playing_kept_separate"] += 1
            untouchable.append(card)
        else:
            foldable.append(card)

    # Most canonical first, so it becomes the host for the others.
    foldable.sort(key=lambda card: _canonicalness(card, layer), reverse=True)

    hosts: List[Dict[str, Any]] = [
        item for item in today_items if isinstance(item, dict)
    ]
    kept: List[Dict[str, Any]] = []
    for card in foldable:
        event_id = str(card.get("id") or "")
        canonical = None
        for host in hosts:
            if str(host.get("id") or "") == event_id:
                continue
            try:
                if layer["same_fixture"](host, card, layer["aliases"]):
                    canonical = host
                    break
            except Exception:  # pragma: no cover - never break a scan over this
                continue
        if canonical is None:
            hosts.append(card)
            kept.append(card)
            continue
        absorbed = _absorb_carried_card(canonical, card, layer)
        # The event is present - under its canonical card - so the miss streak
        # for the duplicate id is over and must not keep counting toward
        # retirement.
        misses.pop(event_id, None)
        stats["carried_forward"] = max(0, int(stats.get("carried_forward", 0)) - 1)
        if str(card.get("lifecycle_state") or "") == END_PENDING:
            stats["end_pending"] = max(0, int(stats.get("end_pending", 0)) - 1)
        stats["reconciled_into_canonical"] += 1
        stats["reconciled_streams"] += absorbed

    return kept + untouchable


def protect_live_events(
    today_items: List[Dict[str, Any]],
    previous_items: List[Dict[str, Any]],
    *,
    grace_minutes: int = DEFAULT_GRACE_MINUTES,
    state_path: Path | str = STATE_FILE,
    now: datetime | None = None,
    probe: Optional[LinkProbe] = None,
    playing_event_ids: Optional[Set[str]] = None,
    authority_states: Optional[Dict[str, Optional[bool]]] = None,
    confirmations_required: int = 3,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Carry forward a previously published live event that this scan missed.

    Section 21 owns the decision: a strong end signal retires an event at once,
    any one still-live protection keeps it, and when the authority is silent the
    event drops to END_PENDING - still published - until the estimated end has
    passed, every link is proven dead, and several consecutive scans have seen no
    live signal. Consecutive misses alone never remove anything.

    playing_event_ids carries the sessions a viewer is currently watching; those
    are the strongest protection in the system and are never retired here.

    authority_states carries THIS scan's fixture-authority verdict per event id:
    True for in-progress, False for finished, and absent when the authority said
    nothing. It has to be passed in, because a carried-forward card still holds
    the "LIVE_NOW" its last successful scan wrote - a remembered status, not a
    current statement. Reading that back as "the authority still says live" would
    protect every event forever and section 21's END_PENDING path would never be
    reachable at all.

    Returns the item list to publish and a stats dict for the scan report.
    """
    reference = now or _now()
    path = Path(state_path)
    state = _load(path)
    misses: Dict[str, Any] = state.get("misses") if isinstance(state.get("misses"), dict) else {}

    present_ids = {
        str(item.get("id") or "") for item in today_items if isinstance(item, dict)
    }
    present_ids.discard("")

    stats = {
        "carried_forward": 0,
        "released_ended": 0,
        "released_dead_link": 0,
        "released_stale": 0,
        # Retired by the corrected rule. Kept at zero so existing reports and
        # dashboards that read this key keep working.
        "released_exhausted": 0,
        "probe_alive": 0,
        "probe_dead": 0,
        "probe_inconclusive": 0,
        # Section 21 lifecycle accounting.
        "end_pending": 0,
        "released_confirmed": 0,
        "protected_playing": 0,
        "lifecycle_states": {},
        # Sections 1/3. A carried card that turns out to be a card this scan
        # already published, under a different spelling of the same fixture.
        "reconciled_into_canonical": 0,
        "reconciled_streams": 0,
        "reconciled_playing_kept_separate": 0,
    }
    carried: List[Dict[str, Any]] = []
    playing = {str(value) for value in (playing_event_ids or set()) if str(value)}
    authority = dict(authority_states or {})
    reconciler = _reconcile_layer()

    for previous in previous_items:
        if not isinstance(previous, dict):
            continue
        event_id = str(previous.get("id") or "")
        if not event_id or event_id in present_ids:
            misses.pop(event_id, None)
            continue

        record = misses.get(event_id) if isinstance(misses.get(event_id), dict) else {}
        count = int(record.get("count") or 0) + 1

        # A strong end signal retires the card without asking the link anything.
        strong_end = has_strong_end_signal(previous) or is_authoritatively_ended(previous)

        verdict: Optional[bool] = None
        if not strong_end and probe is not None:
            try:
                verdict = probe(previous)
            except Exception:
                verdict = None
        if verdict is True:
            stats["probe_alive"] += 1
        elif verdict is False:
            stats["probe_dead"] += 1
        elif not strong_end:
            stats["probe_inconclusive"] += 1

        # Section 21. One probe answers for the card as a whole: the probe walks
        # the primary and every backup, so "alive" means something on this card
        # is playable and "dead" means all of it was reached and rejected.
        # The authority verdict for THIS scan only. A stale LIVE_NOW on the
        # carried card is not evidence; a stale finished status still is, because
        # nothing turns a finished match back on.
        fresh_authority = authority.get(event_id)
        if fresh_authority is None and authority_says_live(previous) is False:
            fresh_authority = False

        signals = LifecycleSignals(
            authority_live=fresh_authority,
            strong_end=strong_end,
            primary_playable=verdict,
            backup_playable=verdict,
            currently_playing=event_id in playing,
            estimate_passed=estimate_passed(previous, reference, grace_minutes),
            consecutive_non_live_scans=count,
            seen_in_this_scan=False,
        )
        decision = decide_lifecycle(
            previous, signals, now=reference,
            confirmations_required=confirmations_required,
        )
        states = stats["lifecycle_states"]
        states[decision.state] = int(states.get(decision.state, 0)) + 1

        if decision.state == ENDED:
            misses.pop(event_id, None)
            if strong_end or signals.authority_live is False:
                stats["released_ended"] += 1
            elif signals.estimate_passed:
                stats["released_stale"] += 1
                stats["released_confirmed"] += 1
            else:
                stats["released_dead_link"] += 1
                stats["released_confirmed"] += 1
            continue

        # LIVE or END_PENDING: the card is published either way.
        misses[event_id] = {
            "count": count,
            "first_missed_at": record.get("first_missed_at") or reference.isoformat(),
            "last_probe": (
                "alive" if verdict is True
                else ("dead" if verdict is False else "inconclusive")
            ),
            "lifecycle_state": decision.state,
            "name": previous.get("name", ""),
        }
        preserved = apply_verdict(previous, decision)
        preserved["carried_forward_misses"] = count
        preserved["carried_forward_reason"] = decision.reason
        carried.append(preserved)
        stats["carried_forward"] += 1
        if decision.state == END_PENDING:
            stats["end_pending"] += 1
        if "currently_playing" in decision.protections:
            stats["protected_playing"] += 1

    # Sections 1/3, across the protection boundary. Everything above decided
    # whether each event survives; this decides how many cards it survives as.
    if reconciler is not None and carried:
        carried = _reconcile_carried_cards(
            today_items, carried, playing, misses, stats, reconciler
        )

    _atomic_write(path, {"updated_at": reference.isoformat(), "misses": misses})
    return today_items + carried, stats
