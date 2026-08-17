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
from typing import Any, Callable, Dict, List, Optional, Tuple

STATE_FILE = Path("state/live-event-protection.json")
DEFAULT_GRACE_MINUTES = 90
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


def protect_live_events(
    today_items: List[Dict[str, Any]],
    previous_items: List[Dict[str, Any]],
    *,
    grace_minutes: int = DEFAULT_GRACE_MINUTES,
    state_path: Path | str = STATE_FILE,
    now: datetime | None = None,
    probe: Optional[LinkProbe] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Carry forward a previously published live event that this scan missed.

    Removal requires an authoritative finish, or a probe that proves every link
    on the card is dead. Consecutive misses never remove anything.

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
    }
    carried: List[Dict[str, Any]] = []

    for previous in previous_items:
        if not isinstance(previous, dict):
            continue
        event_id = str(previous.get("id") or "")
        if not event_id or event_id in present_ids:
            misses.pop(event_id, None)
            continue

        # An authoritative finish is the one signal that removes a card without
        # asking the link anything.
        if is_authoritatively_ended(previous):
            misses.pop(event_id, None)
            stats["released_ended"] += 1
            continue

        record = misses.get(event_id) if isinstance(misses.get(event_id), dict) else {}
        count = int(record.get("count") or 0) + 1

        verdict: Optional[bool] = None
        if probe is not None:
            try:
                verdict = probe(previous)
            except Exception:
                verdict = None
        if verdict is True:
            stats["probe_alive"] += 1
        elif verdict is False:
            stats["probe_dead"] += 1
        else:
            stats["probe_inconclusive"] += 1

        if verdict is False:
            # The link is genuinely dead. Past its own end time plus grace this
            # is a finished match; before that it is a broken stream. Either
            # way the card goes.
            misses.pop(event_id, None)
            end_time = _parse(previous.get("end_time") or previous.get("end_at"))
            if end_time and reference > end_time + timedelta(minutes=max(0, grace_minutes)):
                stats["released_stale"] += 1
            else:
                stats["released_dead_link"] += 1
            continue

        # Alive, or unable to tell: preserve it no matter how many scans missed.
        misses[event_id] = {
            "count": count,
            "first_missed_at": record.get("first_missed_at") or reference.isoformat(),
            "last_probe": (
                "alive" if verdict is True
                else "inconclusive"
            ),
            "name": previous.get("name", ""),
        }
        preserved = dict(previous)
        preserved["carried_forward_misses"] = count
        preserved["carried_forward_reason"] = (
            "previous link still live and playable" if verdict is True
            else "link liveness could not be determined"
        )
        carried.append(preserved)
        stats["carried_forward"] += 1

    _atomic_write(path, {"updated_at": reference.isoformat(), "misses": misses})
    return today_items + carried, stats
