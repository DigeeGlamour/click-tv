"""Sections 6-10, 19, 26 and 27 - Fixture/Event -> Channels[] -> Streams[].

One real fixture is one card. Under it sit the broadcasters carrying that
fixture, and under each broadcaster sit the stream variants that broadcaster's
feed is available as. That is the whole shape:

    event (event_id)
      +- channel  "Willow"            primary + backups
      +- channel  "Sony Sports Ten 1" primary + backups
      +- channel  "Sony Sports Ten 3" primary

The rules this module implements, in the guide's own terms:

  Â§7  A stream's identity is its *effective* playback configuration - final URL,
      DRM and licence, token/query/expiry, cookie, referer, origin, user agent,
      required headers, renderer. Two entries with the same effective config are
      the same stream and one of them is dropped. Two entries that differ in any
      of it are separate variants of the same channel.
  Â§8  Five Willow entries do not become five buttons. Exact duplicates go, the
      rest become one Willow channel with a primary and backups.
  Â§9  Event-level fallback prefers a different broadcaster over another variant
      of the one that just failed - but a viewer who picked Willow keeps
      Willow's own variants first.
  Â§10 A channel belongs to its event. "Willow on match A" and "Willow on match B"
      are different channel groups, always, because the parent key is event_id.
  Â§19 Channel order: current selection, then the scanner default, then other
      healthy independent channels, then low-confidence groups. Inside a group:
      primary first, then backups by verified quality.
  Â§27 Native first. A Streamed embed is a backup, never a reason to demote a
      healthy native primary.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    from scanner.channel_resolver import (
        ChannelName,
        load_alias_map,
        normalize_channel_name,
        resolve_stream_channels,
    )
except ImportError:  # pragma: no cover - direct module execution
    from channel_resolver import (  # type: ignore
        ChannelName,
        load_alias_map,
        normalize_channel_name,
        resolve_stream_channels,
    )

# Section 26. A stream is played by one of two renderers.
PLAYBACK_NATIVE = "native"
PLAYBACK_EMBED = "embed"

ROLE_PRIMARY = "primary"
ROLE_BACKUP = "backup"

# Section 8. A channel keeps a bounded number of variants; the rest stay
# available as standby but do not inflate the published payload.
DEFAULT_MAX_STREAMS_PER_CHANNEL = 4
DEFAULT_MAX_CHANNELS_PER_EVENT = 8

_HEADER_KEYS_THAT_MATTER = (
    "cookie", "referer", "origin", "user-agent", "authorization",
    "x-forwarded-for", "x-playback-session-id",
)


def playback_type_of(stream: Dict[str, Any]) -> str:
    """Section 26. Which renderer plays this stream."""
    declared = str(stream.get("playback_type") or "").strip().lower()
    if declared in {PLAYBACK_NATIVE, PLAYBACK_EMBED}:
        return declared
    if str(stream.get("embed_url") or "").strip():
        return PLAYBACK_EMBED
    return PLAYBACK_NATIVE


def _normalized_headers(stream: Dict[str, Any]) -> Dict[str, str]:
    headers = stream.get("headers")
    if not isinstance(headers, dict):
        return {}
    return {
        str(name).strip().lower(): str(value).strip()
        for name, value in headers.items()
        if str(name).strip()
    }


def stream_variant_identity(stream: Dict[str, Any]) -> str:
    """Section 7. The effective playback configuration, as one comparison key.

    Everything that changes what actually gets played is in here, including the
    credentials: two URLs that look identical but carry different tokens are
    different streams, and collapsing them would throw away a working backup.

    A stream with no URL, no embed URL and no already-minted playback_id has
    nothing to play at all - it used to still hash to a non-empty key here
    (purely from header_profile/proxy_mode/etc.), so a metadata-only Upcoming
    placeholder with zero real streams still bucketed into a "channel" and
    published a broadcaster name next to a Primary role with nothing behind it.
    """
    if not isinstance(stream, dict):
        return ""
    if (
        not str(stream.get("url") or stream.get("stream_url") or "").strip()
        and not str(stream.get("embed_url") or "").strip()
        and not str(stream.get("playback_id") or "").strip()
    ):
        return ""

    headers = _normalized_headers(stream)
    drm = stream.get("drm") if isinstance(stream.get("drm"), dict) else {}
    identity = {
        "renderer": playback_type_of(stream),
        # The final/effective URL, or the embed URL for an embed renderer.
        "url": str(stream.get("url") or stream.get("stream_url") or "").strip(),
        "embed_url": str(stream.get("embed_url") or "").strip(),
        "stream_type": str(stream.get("stream_type") or stream.get("type") or "").strip().lower(),
        "header_profile": str(stream.get("header_profile") or "").strip(),
        "proxy_mode": str(stream.get("proxy_mode") or "").strip(),
        "inherit_manifest_query": bool(stream.get("inherit_manifest_query")),
        "requires_headers": bool(stream.get("requires_headers")),
        # DRM / ClearKey configuration, licence URL and key data.
        "drm": {str(k): str(v) for k, v in sorted(drm.items())} if drm else {},
        # Cookie, referer, origin, user agent and anything else required.
        "headers": {name: headers[name] for name in sorted(headers)},
    }
    digest = hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"sv_{digest[:24]}"


def _quality_height(stream: Dict[str, Any]) -> int:
    for field in ("resolution_height", "height"):
        try:
            value = int(stream.get(field) or 0)
        except (TypeError, ValueError):
            value = 0
        if value:
            return value
    label = str(stream.get("resolution") or "").upper()
    return {"UHD": 2160, "4K": 2160, "QHD": 1440, "FHD": 1080, "HD": 720, "SD": 480}.get(label, 0)


def _response_time(stream: Dict[str, Any]) -> int:
    try:
        value = int(stream.get("response_time_ms") or 0)
    except (TypeError, ValueError):
        return 10_000
    return value or 10_000


def stream_health_score(stream: Dict[str, Any]) -> Tuple[int, ...]:
    """How good is this variant, highest first.

    Section 27's native-first rule lives in the leading term: a native stream
    always outranks an embed, so a Streamed embed can only ever be a backup.
    """
    native = 1 if playback_type_of(stream) == PLAYBACK_NATIVE else 0
    verified = 1 if stream.get("verified") is True else 0
    publishable = 0 if stream.get("publish_allowed") is False else 1
    metadata_only = 0 if stream.get("metadata_only") else 1
    status = str(stream.get("verification_status") or "").strip().lower()
    status_rank = {
        "verified_bd": 4, "verified_proxy": 3, "verified_global": 2, "verified": 2,
    }.get(status, 0)
    https = 1 if str(stream.get("url") or "").lower().startswith("https://") else 0
    return (
        native, publishable, metadata_only, verified, status_rank,
        _quality_height(stream), https, -_response_time(stream),
    )


def _playback_id_for(stream: Dict[str, Any]) -> str:
    """The publish-time playback_id, from the one shared implementation."""
    try:
        from scanner.playback_profiles import stable_playback_id
    except Exception:  # pragma: no cover - direct module execution
        try:
            from playback_profiles import stable_playback_id  # type: ignore
        except Exception:
            return ""
    try:
        return stable_playback_id(stream)
    except Exception:  # pragma: no cover - never break a merge over an id
        return ""


def _slug(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").casefold()).strip("-")


def channel_id_for(event_id: Any, channel: ChannelName) -> str:
    """Section 10. A channel id is local to its event, always.

    The event is the parent key, so the same broadcaster carrying two different
    matches produces two ids that can never be confused for one another - and
    no amount of later regrouping can merge them.
    """
    normalized = channel.normalized or _slug(channel.name)
    event = _slug(event_id) or "event"
    return f"{event}--{normalized}" if normalized else ""


def _public_stream(
    stream: Dict[str, Any],
    stream_id: str,
    role: str,
) -> Dict[str, Any]:
    """One published stream entry.

    Raw URLs, headers, cookies and DRM keys stay out: the existing protected
    playback architecture already keeps them in the playback catalogue behind a
    playback_id, and section 17 is explicit that the public event JSON must not
    start leaking them. An embed stream has no secret to protect, so its
    embed_url is published as-is - that is the whole point of an embed.
    """
    renderer = playback_type_of(stream)
    entry: Dict[str, Any] = {
        "id": stream_id,
        "role": role,
        "playback_type": renderer,
        "variant_key": stream_variant_identity(stream),
        "provider": str(stream.get("provider") or stream.get("source_id") or "").strip(),
        "verification_status": str(stream.get("verification_status") or "").strip(),
        "verified": bool(stream.get("verified")),
    }
    if renderer == PLAYBACK_EMBED:
        entry["embed_url"] = str(stream.get("embed_url") or "").strip()
    else:
        # The id this stream will be published under. Computed here rather than
        # read off the candidate, because playback ids are minted at publish time
        # and channels[] is built during the merge - reading a field that is not
        # populated yet would leave every channel stream unplayable.
        playback_id = str(stream.get("playback_id") or "").strip() or _playback_id_for(stream)
        if playback_id:
            entry["playback_id"] = playback_id
    for field in ("resolution", "resolution_height", "stream_type", "host"):
        value = stream.get(field)
        if value not in (None, "", 0):
            entry[field] = value
    if stream.get("metadata_only"):
        entry["metadata_only"] = True
    return entry


def _channel_entry_from_kept(
    event_id: Any,
    channel: "ChannelName",
    kept: List[Dict[str, Any]],
    dropped_count: int,
    stats: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """One published channel entry from its already-deduplicated stream list.

    Shared by a named broadcaster bucket and a generic "Server-N" bucket, so
    the two are built, capped and published identically - the only difference
    between them is where the ChannelName came from.
    """
    identifier = channel_id_for(event_id, channel)
    if not identifier or not kept:
        return None

    published: List[Dict[str, Any]] = []
    for index, stream in enumerate(kept):
        role = ROLE_PRIMARY if index == 0 else ROLE_BACKUP
        stream_id = f"{identifier}--{index + 1}"
        published.append(_public_stream(stream, stream_id, role))
        if playback_type_of(stream) == PLAYBACK_EMBED:
            stats["embed_variants"] += 1

    entry: Dict[str, Any] = {
        "id": identifier,
        "name": channel.name,
        "normalized_name": channel.normalized,
        "logo": str(kept[0].get("logo") or kept[0].get("channel_logo") or ""),
        "name_confidence": channel.confidence,
        "name_source": channel.source_field,
        "provider": str(kept[0].get("provider") or kept[0].get("source_id") or ""),
        "source_ids": sorted({
            str(stream.get("source_id") or "").strip()
            for stream in kept
            if str(stream.get("source_id") or "").strip()
        }),
        "primary_stream_id": published[0]["id"],
        "stream_count": len(published),
        "backup_count": max(0, len(published) - 1),
        "verified": any(stream.get("verified") for stream in kept),
        "verification_status": str(kept[0].get("verification_status") or ""),
        "playback_types": sorted({playback_type_of(s) for s in kept}),
        # Section 26, as a single value a reader can branch on. "mixed" only
        # when one channel really does carry both kinds of stream.
        "renderer": (
            sorted({playback_type_of(s) for s in kept})[0]
            if len({playback_type_of(s) for s in kept}) == 1
            else "mixed"
        ),
        "streams": published,
        "_health": stream_health_score(kept[0]),
        "_variant_keys": {entry_["variant_key"] for entry_ in published},
    }
    if dropped_count:
        entry["dropped_variant_count"] = dropped_count
    return entry


def build_event_channels(
    event_id: Any,
    event_name: Any,
    streams: Sequence[Dict[str, Any]],
    *,
    aliases: Optional[Dict[str, str]] = None,
    max_streams_per_channel: int = DEFAULT_MAX_STREAMS_PER_CHANNEL,
    max_channels: int = DEFAULT_MAX_CHANNELS_PER_EVENT,
    default_variant_key: str = "",
    selected_channel_id: str = "",
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Group one fixture's streams into channels. Returns (channels, stats).

    A stream whose broadcaster resolved by name goes into that broadcaster's
    group, exactly as before. A stream that could not be named at all is not
    silently dropped from channels[] either, provided it genuinely is a
    distinct, playable feed: it becomes an honestly-labelled "Server-N" entry
    rather than either an invented brand name or an invisible stream. What it
    must never become is a second channel for content that is already
    published under a real name - an unnamed stream whose *effective playback
    configuration* is identical to an already-claimed one is a duplicate of
    that channel, not a new one, and is folded away exactly like any other
    exact duplicate.
    """
    alias_map = aliases if aliases is not None else load_alias_map()
    resolved = resolve_stream_channels(streams, event_name, alias_map)

    stats = {
        "streams_in": len(list(streams)),
        "unresolved_channel_streams": 0,
        "exact_duplicates_removed": 0,
        "channels": 0,
        "variants": 0,
        "embed_variants": 0,
        "generic_server_channels": 0,
    }

    # Section 7/8: one bucket per broadcaster, exact duplicates dropped on entry.
    buckets: Dict[str, Dict[str, Any]] = {}
    unnamed_streams: List[Dict[str, Any]] = []
    for stream, channel in resolved:
        if not channel.resolved:
            stats["unresolved_channel_streams"] += 1
            unnamed_streams.append(stream)
            continue
        variant_key = stream_variant_identity(stream)
        if not variant_key:
            continue
        bucket = buckets.setdefault(
            channel.normalized,
            {"channel": channel, "variants": {}},
        )
        # Keep the most confident spelling of the name for display.
        if channel.rank() > bucket["channel"].rank():
            bucket["channel"] = channel
        existing = bucket["variants"].get(variant_key)
        if existing is None:
            bucket["variants"][variant_key] = stream
            continue
        # Section 7/8: exact same effective config - this is a duplicate.
        stats["exact_duplicates_removed"] += 1
        if stream_health_score(stream) > stream_health_score(existing):
            bucket["variants"][variant_key] = stream

    channels: List[Dict[str, Any]] = []
    claimed_variant_keys: set = set()
    for bucket in buckets.values():
        channel: ChannelName = bucket["channel"]
        variants = sorted(
            bucket["variants"].values(), key=stream_health_score, reverse=True
        )
        if not variants:
            continue
        kept = variants[: max(1, int(max_streams_per_channel))]
        entry = _channel_entry_from_kept(
            event_id, channel, kept, len(variants) - len(kept), stats
        )
        if entry is None:
            continue
        claimed_variant_keys.update(bucket["variants"].keys())
        channels.append(entry)

    # Everything a real broadcaster's name could not be found for. A stream
    # whose exact configuration already belongs to a named channel above is a
    # duplicate of that channel - not a new one - and is dropped here exactly
    # as it would have been dropped inside that channel's own bucket. What
    # survives is grouped by identical configuration too, so five copies of
    # the same untraceable mirror still become one Server-N, not five.
    server_groups: Dict[str, Dict[str, Any]] = {}
    for stream in unnamed_streams:
        variant_key = stream_variant_identity(stream)
        if not variant_key:
            # Nothing to play at all - a metadata-only placeholder, not a
            # fourth kind of channel.
            continue
        if variant_key in claimed_variant_keys:
            # The exact same feed a named channel already publishes. Counted,
            # never duplicated as a channel of its own.
            stats["exact_duplicates_removed"] += 1
            continue
        group = server_groups.setdefault(variant_key, {"variants": []})
        group["variants"].append(stream)

    # Numbered in health order so "Server-1" is consistently the strongest of
    # the unnamed feeds, not whichever happened to appear first in the source
    # list.
    ordered_groups = sorted(
        server_groups.values(),
        key=lambda group: stream_health_score(
            max(group["variants"], key=stream_health_score)
        ),
        reverse=True,
    )
    for index, group in enumerate(ordered_groups, start=1):
        variants = sorted(group["variants"], key=stream_health_score, reverse=True)
        if len(variants) > 1:
            stats["exact_duplicates_removed"] += len(variants) - 1
        kept = variants[:1]
        label = f"Server-{index}"
        # This is a label being minted, not a title being parsed - it must not
        # go through normalize_channel_name(), which correctly treats the word
        # "server" as noise when it is stripping mirror/quality markers off a
        # *real* stream title. Run through that path, "Server-1" normalized to
        # "" and channel_id_for() silently refused to publish it at all.
        generic = ChannelName(
            name=label, normalized=f"server-{index}",
            confidence="generic", source_field="generic",
        )
        entry = _channel_entry_from_kept(event_id, generic, kept, 0, stats)
        if entry is None:
            continue
        stats["generic_server_channels"] += 1
        channels.append(entry)

    ordered = order_channels(
        channels,
        default_variant_key=default_variant_key,
        selected_channel_id=selected_channel_id,
    )[: max(1, int(max_channels))] if channels else []

    for entry in ordered:
        entry.pop("_health", None)
        entry.pop("_variant_keys", None)

    stats["channels"] = len(ordered)
    stats["variants"] = sum(len(entry["streams"]) for entry in ordered)
    return ordered, stats


def order_channels(
    channels: List[Dict[str, Any]],
    *,
    default_variant_key: str = "",
    selected_channel_id: str = "",
) -> List[Dict[str, Any]]:
    """Section 19's channel order.

    Current selection first, then whichever channel carries the scanner's chosen
    default stream, then healthy independent channels by quality, then the
    groups whose name was only inferred.
    """
    confidence_rank = {"explicit": 4, "metadata": 3, "alias": 2, "derived": 1}

    def sort_key(entry: Dict[str, Any]):
        selected = 1 if selected_channel_id and entry.get("id") == selected_channel_id else 0
        carries_default = (
            1
            if default_variant_key and default_variant_key in (entry.get("_variant_keys") or set())
            else 0
        )
        health = entry.get("_health") or ()
        # Section 27, at the event level as well as inside a channel: a channel
        # that can only be played by an embed ranks below every channel with a
        # native stream, however confidently its name was resolved. An explicit
        # channel_name on a provider embed must not outrank a real native feed.
        native = 1 if PLAYBACK_NATIVE in (entry.get("playback_types") or []) else 0
        return (
            -selected,
            -carries_default,
            -native,
            -confidence_rank.get(str(entry.get("name_confidence")), 0),
            tuple(-value for value in health),
            # A channel that has working backups is worth more to a viewer than
            # an equally healthy one with a single stream, because falling back
            # inside it does not cost a channel switch.
            -int(entry.get("stream_count") or 0),
            str(entry.get("name") or ""),
        )

    return sorted(channels, key=sort_key)


def default_channel_id(
    channels: Sequence[Dict[str, Any]],
    default_variant_key: str = "",
) -> str:
    """Which channel the scanner considers this event's default (Â§13).

    The channel that carries the stream the event-level ranking already chose,
    so the published default and the event's own primary never disagree.
    """
    if not channels:
        return ""
    if default_variant_key:
        for entry in channels:
            for stream in entry.get("streams") or []:
                if stream.get("variant_key") == default_variant_key:
                    return str(entry.get("id") or "")
    return str(channels[0].get("id") or "")


def event_failover_order(
    channels: Sequence[Dict[str, Any]],
    selected_channel_id: str = "",
) -> List[Dict[str, Any]]:
    """Sections 9 and 14 - the order playback should try, as flat stream refs.

    With a selected channel: its primary, then its backups, then the next best
    independent channel and its backups. Without one: the scanner default
    order, one channel at a time so a fallback lands on a different broadcaster
    rather than another variant of the one that just failed.
    """
    ordered: List[Dict[str, Any]] = []
    remaining = list(channels)

    if selected_channel_id:
        for index, entry in enumerate(remaining):
            if str(entry.get("id")) == selected_channel_id:
                remaining.insert(0, remaining.pop(index))
                break

    for entry in remaining:
        for stream in entry.get("streams") or []:
            ordered.append({
                "channel_id": entry.get("id"),
                "channel_name": entry.get("name"),
                "stream_id": stream.get("id"),
                "role": stream.get("role"),
                "playback_type": stream.get("playback_type"),
                "playback_id": stream.get("playback_id", ""),
                "embed_url": stream.get("embed_url", ""),
            })
    return ordered


def summarize_channels(channels: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """A compact description for the scan report."""
    return {
        "channel_count": len(channels),
        "stream_count": sum(len(entry.get("streams") or []) for entry in channels),
        "native_channels": sum(
            1 for entry in channels
            if PLAYBACK_NATIVE in (entry.get("playback_types") or [])
        ),
        "embed_channels": sum(
            1 for entry in channels
            if entry.get("playback_types") == [PLAYBACK_EMBED]
        ),
        "names": [str(entry.get("name") or "") for entry in channels],
    }
