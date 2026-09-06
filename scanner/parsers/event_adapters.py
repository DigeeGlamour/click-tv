"""Per-source readers for the eleven Today Match / Upcoming JSON feeds.

Each feed has its own shape, and a shape-guessing parser loses data on most of
them. Measured on 2026-08-20 against the live files, `json_parser` returned zero
records for four of the eleven and incomplete records for two more:

    sonyliv       expected  1 -> 0    root is `live_matches`; the stream lives at
                                      playback_info.resultObj.videoURL
    primevideo    expected  5 -> 0    `stream_url` is a dict, not a list
    willow-live   expected 19 -> 0    `stream_url_alpha`/`_bravo` dicts, and the
                                      same key is a bare "" on upcoming records
    footy-live    expected 23 -> 0    keys carry spaces: "match name",
                                      "Start time", "Tour/Group name"
    axsports      expected 13 -> 10   `videoURL` ranked ahead of `stream_link`
    bingstream    expected 13 -> 10   same

So every feed gets a reader that knows its own layout. The readers agree on one
output shape - the same flat candidate the rest of the pipeline already consumes
- and on three rules that the audit showed matter:

1.  Nothing is dropped silently. Every record is either emitted or counted in
    `ADAPTER_STATS[source_id]["skipped"]` with a reason, and the counts reach
    reports/source-parse-report.json.
2.  A record with no playable link is still emitted, as `metadata_only`. An
    upcoming fixture has no stream yet and must still reach the Upcoming tab;
    sm-sportsdata additionally names 233 channels whose `stream_url` is empty.
3.  Routing is not decided here. Each record carries `status_raw` and, where the
    feed states it, `source_says_ended`; the schedule layer decides the tab.

Three levels, because the feeds genuinely have three:

    match  ->  channel (a button)  ->  server (a backup inside that button)

A dict of CDN names under one match is five servers of one channel, not five
channels - "Amazon Server" is not a broadcaster. And in `link_live[]`,
`stream_link` and `videoURL` on the same entry point at the same host and
directory, so they are two forms of one server; `videoURL` carries an expiring
token (measured ~24h) and `stream_link` does not, so the tokenless form leads.
"""

from __future__ import annotations

import collections
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

#: Per-source parse accounting, read by the scan report. Reset per run.
ADAPTER_STATS: Dict[str, Dict[str, Any]] = {}

BDT = timezone(timedelta(hours=6), "BDT")
#: FanCode is an Indian service and publishes Indian Standard Time. Reading its
#: clock as Bangladesh time put every FanCode fixture exactly thirty minutes
#: early - the half-hour between UTC+5:30 and UTC+6:00. Measured 2026-09-02:
#: eight fixtures across five countries, all out by the same thirty minutes,
#: and `Real Sociedad vs RC Celta` published 18:30 when LaLiga and
#: thesportsdb both say 19:00.
IST = timezone(timedelta(hours=5, minutes=30), "IST")
PKT = timezone(timedelta(hours=5), "PKT")

#: The zones a feed can name inside the value itself.
_NAMED_ZONES = {
    "IST": IST, "PKT": PKT, "BDT": BDT, "BD TIME": BDT,
    "UTC": timezone.utc, "GMT": timezone.utc,
    "BST": timezone(timedelta(hours=1), "BST"),
}

#: Which reader handles which configured source id.
ADAPTER_BY_SOURCE: Dict[str, str] = {
    "srhady-sonyliv-live": "sonyliv",
    "srhady-axsports-live": "link_live",
    "srhady-bingstream": "link_live",
    "srhady-tapmad-bd": "tapmad",
    "srhady-primevideo-sports": "server_dict",
    "srhady-willow-event": "server_dict",
    "0matbank-trysports-cricket-live": "named_streams",
    "0matbank-trysports-football-live": "named_streams",
    "sm-sports-data": "named_streams",
    "sm-fancode": "fancode",
    "srhady-crichd-footy-live": "spaced_keys",
    "sportlive-fancode-backup": "sportlive_fancode",
    "sportlive-sonyliv-backup": "sportlive_sonyliv",

    # Added 2026-08-30. The three SonyLiv feeds carry the same shape as
    # sportlive-sonyliv-backup - matches[] keyed by isLive, one row per audio
    # language - and are the only new feeds whose streams reached a media
    # segment from Bangladesh: 9/9, 9/9 and 3/3.
    "kajju-sonyliv-backup": "sportlive_sonyliv",
    "drmlive-sonyliv-backup": "sportlive_sonyliv",
    "sayanpal-sonyliv-backup": "sportlive_sonyliv",

    # The FanCode mirrors publish the same rows as sportlive-fancode-backup,
    # including google_m3u8_hex and akamai_m3u8_hex, so they read the same way.
    # None of them plays from here - every one carries the same India-minted
    # signature - and they are configured below the SonyLiv feeds for that
    # reason. What they add is metadata: fixture names, kickoff times, team art,
    # and the regional CDN alternatives.
    "drmlive-fancode-mirror": "sportlive_fancode",
    "sayanpal-fancode-mirror": "sportlive_fancode",
    "vk1817-fancode-mirror": "sportlive_fancode",
    "dartv-fancode-mirror": "sportlive_fancode",
    "iptvflixbd-fancode-data": "sportlive_fancode",
}


def reset_adapter_stats() -> None:
    ADAPTER_STATS.clear()


def _stats(source_id: str) -> Dict[str, Any]:
    entry = ADAPTER_STATS.setdefault(
        source_id,
        {
            "total_records": 0,
            "parsed": 0,
            "skipped": 0,
            "channels": 0,
            "servers": 0,
            "metadata_only": 0,
            "with_drm": 0,
            "with_headers": 0,
            "source_says_ended": 0,
            "recovered_by_deep_scan": 0,
            "routed_today": 0,
            "routed_upcoming": 0,
            "routed_ended": 0,
            "candidates": 0,
            "status_counts": collections.Counter(),
            "skip_reasons": collections.Counter(),
            "unknown_fields": collections.Counter(),
        },
    )
    return entry


def _skip(source_id: str, index: int, reason: str) -> None:
    entry = _stats(source_id)
    entry["skipped"] += 1
    entry["skip_reasons"][reason] += 1


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------

_VERSUS = re.compile(r"\s+(?:vs\.?|v\.?|versus)\s+", re.IGNORECASE)

#: Quality words a feed uses as a stream label, best first. Used only to order
#: servers inside one channel, never to name the channel.
_QUALITY_RANK = {
    "4K": 0, "UHD": 0,
    "FHD": 1, "FULLHD": 1, "1080P": 1,
    "WHD": 2,
    "HD": 3, "720P": 3,
    "GHD": 4,
    "SD": 5, "LOW": 6,
}

#: CDN preference for a dict of servers, best first. Measured playability was
#: equal across them, so this is only a stable tie-break.
_SERVER_RANK = [
    "amazon", "akamai", "original", "fastly", "fistly", "cloudfront",
]


def _quality_rank(label: Any) -> int:
    text = re.sub(r"[^A-Z0-9]", "", str(label or "").upper())
    return _QUALITY_RANK.get(text, 4)


def _server_rank(label: Any) -> int:
    text = str(label or "").casefold()
    for index, name in enumerate(_SERVER_RANK):
        if name in text:
            return index
    return len(_SERVER_RANK)


def _split_inline_headers(url: Any) -> Tuple[str, Dict[str, str]]:
    """`http://h/x.m3u8?|Referer=a&Origin=b` -> (url, {Referer: a, Origin: b}).

    158 of sm-sportsdata's 773 URLs carry their headers this way.
    """
    text = str(url or "").strip()
    if "|" not in text:
        return text, {}
    head, _, tail = text.partition("|")
    # The feeds write the separator as "...m3u8?|Referer=..." - the "?" belongs
    # to the separator, not to the URL, and leaving it makes the same stream
    # look like two different ones to every identity check downstream.
    head = head.rstrip()
    while head.endswith(("?", "&")):
        head = head[:-1].rstrip()
    headers: Dict[str, str] = {}
    for part in tail.split("&"):
        key, _, value = part.partition("=")
        key = key.strip()
        value = value.strip()
        if key and value:
            headers[key] = value
    return head.strip(), headers


def _clearkey(raw: Any) -> Dict[str, Any]:
    """`kid:key` -> a ClearKey DRM block. Anything else -> {}."""
    text = str(raw or "").strip()
    if text.count(":") != 1:
        return {}
    kid, key = (part.strip() for part in text.split(":", 1))
    if not re.fullmatch(r"[0-9a-fA-F]{16,}", kid) or not re.fullmatch(r"[0-9a-fA-F]{16,}", key):
        return {}
    return {
        "type": "clearkey",
        "protected": True,
        "keys": [{"kid": kid, "k": key}],
        "clearkey": text,
    }


def _epoch(value: Any, unit: str = "s") -> str:
    """Epoch seconds or milliseconds -> ISO 8601 UTC. "" when unusable."""
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return ""
    if number <= 0:
        return ""
    if unit == "ms":
        number /= 1000.0
    try:
        return datetime.fromtimestamp(number, timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return ""


#: Every clock spelling these feeds actually use, measured on the event
#: registry as it stood on 2026-08-20, when eleven feeds were registered.
_TIME_PATTERNS = (
    "%Y-%m-%d %H:%M:%S",            # tapmad, sm-sportsdata (12 of 158)
    "%d/%m/%Y %I:%M:%S %p",         # sm-sportsdata (146 of 158)
    "%I:%M:%S %p %d-%m-%Y",         # fancode
    "%I:%M %p %d-%m-%Y",            # axsports/bingstream bd_time
    "%d %b %Y, %I:%M %p",           # trysports
    "%d %B %Y, %I:%M %p",
    "%d %b %Y %I:%M %p",
    "%Y-%m-%dT%H:%M:%S.%f%z",       # footy-live
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S",
)

_TZ_SUFFIX = re.compile(
    r"(?i)\s*\(?\s*(?:BD\s*TIME|BDT|BST|IST|UTC|GMT|PKT)\s*\)?\s*$"
)
#: willow-live: "Tomorrow 5 AM BDT", "Sat, Aug 22 5 AM BDT", "3:15 PM BDT"
_WEEKDAY_PREFIX = re.compile(r"(?i)^(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*,?\s*")
_BARE_CLOCK = re.compile(r"(?i)^(\d{1,2})(?::(\d{2}))?\s*(am|pm)$")
_DATE_CLOCK = re.compile(
    r"(?i)^([a-z]{3,9})\s+(\d{1,2})\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)$"
)


def _parse_clock(value: Any, now: Optional[datetime] = None,
                 tz: Optional[timezone] = None) -> str:
    """Any of the measured spellings -> ISO 8601 UTC. "" when unreadable.

    `tz` is the zone a naive value is in. It defaults to Bangladesh time,
    which is what most of these feeds publish and what every caller assumed
    before FanCode's Indian clock was found to be half an hour off.

    A zone named inside the value itself wins over `tz`: the suffix used to be
    stripped and thrown away, so "5 PM IST" was read as 5 PM in Dhaka.
    """
    text = " ".join(str(value or "").split())
    if not text:
        return ""
    if text.isdigit():
        return _epoch(text, "ms" if len(text) >= 12 else "s")

    zone = tz or BDT
    named = _TZ_SUFFIX.search(text)
    if named:
        spelled = " ".join(named.group(0).strip(" ()").split()).upper()
        zone = _NAMED_ZONES.get(spelled, zone)
    reference = (now or datetime.now(timezone.utc)).astimezone(zone)
    cleaned = _TZ_SUFFIX.sub("", text).strip()

    day_shift = 0
    if re.match(r"(?i)^tomorrow\b", cleaned):
        day_shift = 1
        cleaned = re.sub(r"(?i)^tomorrow\s*", "", cleaned)
    elif re.match(r"(?i)^today\b", cleaned):
        cleaned = re.sub(r"(?i)^today\s*", "", cleaned)
    cleaned = _WEEKDAY_PREFIX.sub("", cleaned).strip()

    for pattern in _TIME_PATTERNS:
        try:
            parsed = datetime.strptime(cleaned, pattern)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=zone)
        return parsed.astimezone(timezone.utc).isoformat()

    match = _DATE_CLOCK.match(cleaned)
    if match:
        month, day, hour, minute, meridiem = match.groups()
        try:
            month_number = datetime.strptime(month[:3], "%b").month
        except ValueError:
            month_number = 0
        if month_number:
            hour24 = int(hour) % 12 + (12 if meridiem.lower() == "pm" else 0)
            year = reference.year
            if month_number < reference.month - 6:
                year += 1
            try:
                return datetime(
                    year, month_number, int(day), hour24, int(minute or 0), tzinfo=zone
                ).astimezone(timezone.utc).isoformat()
            except ValueError:
                return ""

    match = _BARE_CLOCK.match(cleaned)
    if match:
        hour, minute, meridiem = match.groups()
        hour24 = int(hour) % 12 + (12 if meridiem.lower() == "pm" else 0)
        stamp = reference.replace(
            hour=hour24, minute=int(minute or 0), second=0, microsecond=0
        ) + timedelta(days=day_shift)
        return stamp.astimezone(timezone.utc).isoformat()
    return ""


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text and text.casefold() not in {"none", "null"}:
            return text
    return ""


def _collapse_self_versus(text: str) -> str:
    """"Cycling Vs Cycling" -> "Cycling". Otherwise unchanged.

    sm-sportsdata builds every event name as "teamA Vs teamB" including the
    events that have no two sides: measured on 2026-08-20, 8 of its 157 records
    set teamA and teamB to the same string, and the site published cards reading
    "Horse Racing Vs Horse Racing" and "Golf Eventos Vs Golf Eventos". The two
    sides are compared, not just the words, so a real "Nepal vs Nepal A" is left
    alone.
    """
    match = _VERSUS.search(text)
    if not match:
        return text
    left = text[: match.start()].strip(" -|:,")
    right = text[match.end():].strip(" -|:,")
    if left and left.casefold() == right.casefold():
        return left
    return text


#: A date appended to a fixture title. The kickoff already has its own field,
#: so carrying it in the name says the same thing twice and pushes the teams
#: off the card: `Indore Hawks vs Chennai Strikers 2 Sep 2026`.
_TITLE_DATE = re.compile(
    r"\s*[-,|]?\s*\b(?:\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{2,4}|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2}(?:,)?\s+\d{2,4}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\s*$",
    re.IGNORECASE)

#: A status or quality word a feed sticks on the end of the title. The card
#: shows the status from its own field, so `Costa Rica vs Bulgaria Live` reads
#: as though "Live" were part of Bulgaria's name.
_TITLE_STATUS = re.compile(
    r"\s*[-,|]?\s*\b(?:live(?:\s+now)?|now\s+live|hd|fhd|uhd|sd|4k|full\s*match|highlights?|replay|stream(?:ing)?)\s*$",
    re.IGNORECASE)


def _tidy_fixture_title(text: str) -> str:
    """Strip the date and the status a feed appended to a fixture name.

    Both belong to fields of their own - `start_time` and `status` - and the
    card renders them from there. Repeating them inside the name is how
    `Indore Hawks vs Chennai Strikers 2 Sep 2026` and `Costa Rica vs Bulgaria
    Live` reached the front page.
    """
    cleaned = " ".join(str(text or "").split())
    for _ in range(3):
        before = cleaned
        cleaned = _TITLE_DATE.sub("", cleaned).strip(" -,|:")
        cleaned = _TITLE_STATUS.sub("", cleaned).strip(" -,|:")
        if cleaned == before:
            break
    # Never strip a title down to nothing, or to one side of the fixture.
    if not cleaned or not _VERSUS.search(cleaned):
        return " ".join(str(text or "").split())
    return cleaned


def _match_name(*parts: Any) -> str:
    """The cleanest "A vs B" available from the parts given, in order."""
    for part in parts:
        text = " ".join(str(part or "").split())
        if text and _VERSUS.search(text):
            # "Series - 1st Test - England vs Pakistan" -> "England vs Pakistan"
            tail = text.rsplit(" - ", 1)[-1].strip()
            chosen = tail if _VERSUS.search(tail) else text
            return _tidy_fixture_title(_collapse_self_versus(chosen))
    return _first_text(*parts)


_URL_TEXT = re.compile(r"(?i)^https?://\S+$")


def _deep_urls(value: Any, limit: int = 8) -> List[str]:
    """Every http(s) string inside a nested structure, in document order.

    The known key names cover what the registered feeds serve today. This is
    the
    safety net for the day one of them moves its link one level deeper: the
    rule is that a stream URL must be found even when it is nested inside
    objects or arrays, and a key-name lookup alone cannot promise that.
    """
    found: List[str] = []

    def walk(node: Any) -> None:
        if len(found) >= limit:
            return
        if isinstance(node, str):
            text = node.strip()
            if _URL_TEXT.match(text) and text not in found:
                found.append(text)
            return
        if isinstance(node, dict):
            for item in node.values():
                walk(item)
            return
        if isinstance(node, list):
            for item in node:
                walk(item)

    walk(value)
    return found


def _server(
    url: Any,
    *,
    label: str = "",
    quality: Any = "",
    headers: Optional[Dict[str, str]] = None,
    drm: Optional[Dict[str, Any]] = None,
    has_token: Optional[bool] = None,
) -> Optional[Dict[str, Any]]:
    clean, inline = _split_inline_headers(url)
    if not clean or not clean.lower().startswith(("http://", "https://")):
        return None
    merged = dict(headers or {})
    merged.update(inline)
    if has_token is None:
        has_token = bool(re.search(r"(?i)[?&](?:token|hdnea|s|sig|signature)=", clean))
    return {
        "url": clean,
        "server_label": label or "",
        "quality_rank": _quality_rank(quality),
        "server_rank": _server_rank(label),
        "has_token": bool(has_token),
        "headers": merged,
        "drm": dict(drm or {}),
    }


def _order_servers(servers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Tokenless first, then quality, then CDN preference, then stable order.

    Identical routes are folded. A feed can name the same URL under several
    keys - sonyliv.json gives dai_url, pub_url and video_url byte-identical on
    every live row - and offering one route three times spends three attempt
    slots to learn the same thing once. Two entries differing in headers or DRM
    are NOT the same route and both survive.
    """
    ordered = sorted(
        (item for item in servers if item),
        key=lambda item: (
            1 if item["has_token"] else 0,
            item["quality_rank"],
            item["server_rank"],
        ),
    )
    seen: set = set()
    unique: List[Dict[str, Any]] = []
    for item in ordered:
        identity = (
            item.get("url"),
            json.dumps(item.get("headers") or {}, sort_keys=True, default=str),
            json.dumps(item.get("drm") or {}, sort_keys=True, default=str),
        )
        if identity in seen:
            continue
        seen.add(identity)
        unique.append(item)
    return unique


def _channel(name: str, servers: List[Dict[str, Any]], logo: str = "") -> Dict[str, Any]:
    return {
        "channel_name": " ".join(str(name or "").split()),
        "channel_logo": str(logo or "").strip(),
        "servers": _order_servers(servers),
    }


def _record(
    source_id: str,
    *,
    name: str,
    status_raw: str,
    channels: List[Dict[str, Any]],
    logos: Iterable[Any] = (),
    competition: str = "",
    round_label: str = "",
    sport: str = "",
    start_time: str = "",
    end_time: str = "",
    # Does `end_time` say when the MATCH finishes?
    #
    # Not every end a feed carries is one. SonyLiv sends
    # `contractEndDate` - when its licence to carry the content expires -
    # which this file has always used to work out LIVE vs FINISHED, and
    # which is fine for that. It is not a finishing time: measured on
    # 2026-09-05, `Fazilka Falcons vs Bathinda Royals` carried one 915
    # minutes after its own start, for a T20.
    #
    # So the adapter that read the value says whether it means what it
    # looks like, because that is the only place the answer is known.
    # Default False: silence is not evidence.
    end_time_stated: bool = False,
    source_says_ended: Optional[bool] = None,
    identity: str = "",
) -> Dict[str, Any]:
    kept = [entry for entry in channels if entry and entry["servers"]]
    logo = _first_text(*logos)
    return {
        "source_id": source_id,
        # Cleaned here because this is the one exit every adapter uses.
        # _match_name only tidies the branch that already reads as "A vs B",
        # so a name built from team_1/team_2 or taken straight from a title
        # slipped past it - five titles were still carrying a date or a
        # trailing "Live" after the first attempt.
        "name": _tidy_fixture_title(name),
        "status_raw": str(status_raw or ""),
        "competition": str(competition or "").strip(),
        "round": str(round_label or "").strip(),
        "sport": str(sport or "").strip().lower(),
        "start_time": start_time,
        "end_time": end_time,
        "end_time_stated": bool(end_time_stated and str(end_time or "").strip()),
        "source_says_ended": source_says_ended,
        "logo": logo,
        "logo_candidates": [str(x or "").strip() for x in logos if str(x or "").strip()],
        "identity": str(identity or "").strip(),
        "channels": kept,
        "metadata_only": not kept,
    }


# ---------------------------------------------------------------------------
# S1 - srhady/SonyLiv/sonyliv_playlist.json
# ---------------------------------------------------------------------------

def _window_status(start_iso: str, end_iso: str) -> str:
    """UPCOMING / LIVE / FINISHED from a start-end window. "UNKNOWN" with none."""
    now = datetime.now(timezone.utc)

    def read(text: str) -> Optional[datetime]:
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

    start = read(start_iso)
    end = read(end_iso)
    if end is not None and now > end:
        return "FINISHED"
    if start is not None and now < start:
        return "UPCOMING"
    if start is not None:
        return "LIVE"
    return "UNKNOWN"


def adapt_sonyliv(payload: Dict[str, Any], source_id: str) -> List[Dict[str, Any]]:
    """`live_matches[]`, each with `match_info` and `playback_info`.

    No status field at all: `isLive` and `isOnAir` carry it. The title is the
    series ("Pakistan Tour of England 2026") and the episodeTitle the round, so
    the fixture name is built from emfAttributes.home_team/away_team - which is
    what lets this feed's "England vs Pakistan" merge with the other five
    feeds' spelling of the same match instead of publishing a second card.
    """
    records: List[Dict[str, Any]] = []
    rows = payload.get("live_matches")
    if not isinstance(rows, list):
        return records
    stats = _stats(source_id)
    stats["total_records"] += len(rows)

    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            _skip(source_id, index, "record is not an object")
            continue
        info = row.get("match_info")
        if not isinstance(info, dict):
            _skip(source_id, index, "match_info missing")
            continue
        emf = info.get("emfAttributes") if isinstance(info.get("emfAttributes"), dict) else {}

        home = _first_text(emf.get("home_team"))
        away = _first_text(emf.get("away_team"))
        name = f"{home} vs {away}" if home and away else _first_text(
            info.get("episodeTitle"), info.get("title")
        )
        if not name:
            _skip(source_id, index, "no usable name")
            continue

        start_iso = _epoch(info.get("contractStartDate"), "ms")
        end_iso = _epoch(info.get("contractEndDate"), "ms")
        live = bool(info.get("isLive")) and bool(info.get("isOnAir"))
        # No status field exists here. isLive/isOnAir is the feed's own word;
        # when it says no, the contract window decides rather than a guess -
        # contentSubtype only ever says "LIVE_SPORT", which answers nothing.
        if live:
            status = "LIVE"
        else:
            status = _window_status(start_iso, end_iso)

        result = (row.get("playback_info") or {}).get("resultObj")
        result = result if isinstance(result, dict) else {}
        drm = {} if not info.get("isEncrypted") else {"type": "unknown", "protected": True}

        servers = [_server(result.get("videoURL"), label="Main", quality="FHD", drm=drm)]
        # Empty today, but the field exists and would be extra servers.
        extra = result.get("multiLanguageVideoURL")
        if isinstance(extra, list):
            for entry in extra:
                if isinstance(entry, dict):
                    servers.append(
                        _server(
                            entry.get("videoURL") or entry.get("url"),
                            label=_first_text(entry.get("audioLanguageName"), "Alt"),
                            quality="FHD",
                            drm=drm,
                        )
                    )
                elif isinstance(entry, str):
                    servers.append(_server(entry, label="Alt", quality="FHD", drm=drm))

        channel = _channel(
            _first_text(emf.get("broadcast_channel")) or "SonyLIV",
            servers,
            _first_text(emf.get("masthead_logo")),
        )
        genres = info.get("genres")
        sport = genres[0] if isinstance(genres, list) and genres else ""

        records.append(
            _record(
                source_id,
                name=name,
                status_raw=status,
                channels=[channel],
                logos=(
                    emf.get("thumbnail"),
                    emf.get("landscape_thumb"),
                    emf.get("portrait_thumb"),
                    emf.get("tv_background_image"),
                ),
                competition=_first_text(info.get("title")),
                round_label=_first_text(info.get("episodeTitle")),
                sport=sport,
                start_time=start_iso,
                # `contractEndDate`. Still used above to decide LIVE vs
                # FINISHED, which is what it is good for, and deliberately
                # NOT offered as a finishing time - see `end_time_stated`.
                end_time=end_iso,
                source_says_ended=True if status == "FINISHED" else None,
                identity=_first_text(info.get("contentId")),
            )
        )
    return records


# ---------------------------------------------------------------------------
# S2 / S7 - srhady/axsports/live_sports.json, srhady/bingstream/playlist.json
# ---------------------------------------------------------------------------

def adapt_link_live(payload: Dict[str, Any], source_id: str) -> List[Dict[str, Any]]:
    """`matches[]` with `link_live[]`.

    The entries in `link_live[]` are CDN edges and renditions of the same feed -
    measured: `livecdn-tc-...` and `livecdn-bp-...` on an identical path - so
    they are servers, not channels. The one thing that does name a channel here
    is `stream_name`/`chanel_id`, present on 29 of the 181 records across the
    two feeds; those are numbered IPTV channels and each is its own button.

    Everything else is one channel. `display_name` and `line` were tried as a
    channel key first and that was wrong: "Phnom Penh W vs Kaya W" ships an
    FHD/other rendition and a WHD/web one, and keying on them published the one
    match as two unnamed buttons - "Server-1" beside "Server-2" - which is the
    duplicate-button complaint, not a choice of channels. Across all 181 records
    quality never varies without `line` varying with it, so no record loses a
    genuine distinction by this: the renditions become the backups of one
    button, which is also the failover the viewer actually wants.

    `stream_link` and `videoURL` on one entry are the same server twice - same
    host, same directory, index.m3u8 against chunks.m3u8 - and only the second
    carries an expiring token, so the tokenless form leads.
    """
    records: List[Dict[str, Any]] = []
    rows = payload.get("matches")
    if not isinstance(rows, list):
        return records
    stats = _stats(source_id)
    stats["total_records"] += len(rows)

    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            _skip(source_id, index, "record is not an object")
            continue
        name = _first_text(row.get("name"))
        if not name:
            _skip(source_id, index, "no name")
            continue

        headers: Dict[str, str] = {}
        referer = _first_text(row.get("referer"))
        if referer:
            headers["Referer"] = referer
            headers["Origin"] = referer.rstrip("/")

        grouped: "collections.OrderedDict[Tuple[str, str], List[Dict[str, Any]]]"
        grouped = collections.OrderedDict()
        links = row.get("link_live")
        for entry in links if isinstance(links, list) else []:
            if not isinstance(entry, dict):
                continue
            try:
                if int(entry.get("vip_only") or 0) > 0:
                    _stats(source_id)["skip_reasons"]["stream vip_only"] += 1
                    continue
            except (TypeError, ValueError):
                pass
            quality = _first_text(entry.get("display_name"))
            channel_key = (
                _first_text(entry.get("stream_name"), entry.get("chanel_id")),
                "",
            )
            bucket = grouped.setdefault(channel_key, [])
            bucket.append(
                _server(
                    entry.get("stream_link"),
                    label=_first_text(entry.get("extra_title")),
                    quality=quality,
                    headers=headers,
                    has_token=False,
                )
            )
            if entry.get("videoURL"):
                bucket.append(
                    _server(
                        entry.get("videoURL"),
                        label=_first_text(entry.get("extra_title")),
                        quality=quality,
                        headers=headers,
                        has_token=True,
                    )
                )

        channels = [
            _channel(key[0] or "", servers)
            for key, servers in grouped.items()
        ]

        ended = bool(row.get("has_ended")) or None if row.get("has_ended") is not None else None
        records.append(
            _record(
                source_id,
                name=name,
                status_raw=_first_text(row.get("status")),
                channels=channels,
                logos=(
                    row.get("localteam_logo"),
                    row.get("visitorteam_logo"),
                    row.get("league_logo"),
                ),
                competition=_first_text(row.get("league_name")),
                start_time=_epoch(row.get("start_at")) or _parse_clock(row.get("bd_time")),
                source_says_ended=True if row.get("has_ended") is True else None,
                identity=_first_text(row.get("id")),
            )
        )
    return records


# ---------------------------------------------------------------------------
# S3 - srhady/tapmad-bd/tapmad_bd.json
# ---------------------------------------------------------------------------

def adapt_tapmad(payload: Dict[str, Any], source_id: str) -> List[Dict[str, Any]]:
    """`Matches[]` with one `stream_url`, present only on the Live records.

    The ten Upcoming records carry no link at all and are emitted as metadata
    so the fixture still reaches the Upcoming tab.
    """
    records: List[Dict[str, Any]] = []
    rows = payload.get("Matches")
    if not isinstance(rows, list):
        return records
    stats = _stats(source_id)
    stats["total_records"] += len(rows)

    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            _skip(source_id, index, "record is not an object")
            continue
        name = _first_text(row.get("VideoName"))
        if not name:
            _skip(source_id, index, "no VideoName")
            continue
        servers = [_server(row.get("stream_url"), label="Tapmad", quality="HD")]
        records.append(
            _record(
                source_id,
                name=name,
                status_raw=_first_text(row.get("Status")),
                channels=[_channel("Tapmad", servers)],
                logos=(row.get("ThumbnailStandard"), row.get("ThumbnailTV")),
                competition=_first_text(row.get("CategoryName")),
                round_label=_first_text(row.get("StageName")),
                start_time=_parse_clock(row.get("EventStartDate")),
                identity=_first_text(row.get("EntityId")),
            )
        )
    return records


# ---------------------------------------------------------------------------
# S4 / S11 - primevideo_sports.json, willow-event/live_sports.json
# ---------------------------------------------------------------------------

_SERVER_DICT_KEYS = ("stream_url", "stream_url_alpha", "stream_url_bravo")


def adapt_server_dict(payload: Dict[str, Any], source_id: str) -> List[Dict[str, Any]]:
    """`Matches[]` whose streams are a dict of CDN name -> URL.

    The dict keys are servers of one channel, not separate channels. willow
    carries two such dicts - `stream_url_alpha` and `stream_url_bravo` - and
    measured against the live file they are genuinely different feeds
    (`pdx-nitro` against `iad-nitro`), so those are two channels of five
    servers each rather than ten channels.

    The same key is a bare "" on the nineteen upcoming records, so the type is
    checked before iterating; that alone is why json_parser returned nothing.
    """
    records: List[Dict[str, Any]] = []
    rows = payload.get("Matches")
    if not isinstance(rows, list):
        return records
    stats = _stats(source_id)
    stats["total_records"] += len(rows)

    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            _skip(source_id, index, "record is not an object")
            continue
        title = _first_text(row.get("title"), row.get("synopsis"))
        if not title:
            _skip(source_id, index, "no title")
            continue
        drm = _clearkey(row.get("drm_key"))

        channels: List[Dict[str, Any]] = []
        for key in _SERVER_DICT_KEYS:
            block = row.get(key)
            if not isinstance(block, dict) or not block:
                continue
            servers = [
                _server(url, label=name, quality="FHD", drm=drm)
                for name, url in block.items()
            ]
            suffix = {"stream_url_alpha": "", "stream_url_bravo": " 2"}.get(key, "")
            channels.append(_channel(f"{_feed_brand(source_id)}{suffix}", servers))

        records.append(
            _record(
                source_id,
                name=_match_name(title, row.get("synopsis")),
                status_raw=_first_text(row.get("status")),
                channels=channels,
                logos=(row.get("cover_image"),),
                competition=_series_of(title),
                start_time=_parse_clock(row.get("time")),
                identity=_first_text(row.get("match_id")),
            )
        )
    return records


def _feed_brand(source_id: str) -> str:
    if "primevideo" in source_id:
        return "Prime Video"
    if "willow" in source_id:
        return "Willow"
    return "Server"


def _series_of(title: Any) -> str:
    """"Series 2026 - 1st Test - A vs B" -> "Series 2026 - 1st Test"."""
    text = " ".join(str(title or "").split())
    if " - " in text and _VERSUS.search(text.rsplit(" - ", 1)[-1]):
        return text.rsplit(" - ", 1)[0]
    return ""


# ---------------------------------------------------------------------------
# S5 / S6 / S8 - trysports cricket+football, sm-sportsdata
# ---------------------------------------------------------------------------

def adapt_named_streams(payload: Dict[str, Any], source_id: str) -> List[Dict[str, Any]]:
    """`matches[]` with `streams[]` that name their own channel.

    Three things the audit found here and nothing else handles:

    - sm-sportsdata names 542 channels but fills only 311 `stream_url`s. A
      named channel with no link is kept as a metadata-only channel rather than
      dropped, because dropping it loses the channel and keeping it as a button
      would publish a dead link.
    - 158 of its URLs carry their headers inside the URL after a `|`.
    - `drm_key` is absent on 481 streams and a `kid:key` string on 63.

    trysports football names every stream after the match itself
    ("LA Galaxy vs San Jose Earthquakes (HD)"), which is not a broadcaster, so
    that name is dropped and the channel falls through to the generic label.
    """
    records: List[Dict[str, Any]] = []
    rows = payload.get("matches")
    if not isinstance(rows, list):
        return records
    stats = _stats(source_id)
    stats["total_records"] += len(rows)

    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            _skip(source_id, index, "record is not an object")
            continue
        info = row.get("eventInfo") if isinstance(row.get("eventInfo"), dict) else {}
        name = _match_name(
            row.get("event_name"), row.get("title"), row.get("name")
        )
        if not name and info:
            team_a = _first_text(info.get("teamA"))
            team_b = _first_text(info.get("teamB"))
            if team_a and team_b:
                name = f"{team_a} vs {team_b}"
        if not name:
            _skip(source_id, index, "no event name")
            continue

        base_headers: Dict[str, str] = {}
        raw_headers = row.get("headers")
        if isinstance(raw_headers, dict):
            for key, value in raw_headers.items():
                text = str(value or "").strip()
                if text:
                    base_headers[str(key)] = text

        grouped: "collections.OrderedDict[str, Dict[str, Any]]" = collections.OrderedDict()
        named_without_link = 0
        for stream in row.get("streams") if isinstance(row.get("streams"), list) else []:
            if not isinstance(stream, dict):
                continue
            label = _first_text(stream.get("channel_name"))
            # A stream named after its own match names no broadcaster.
            if label and _same_fixture(label, name):
                label = ""
            quality = "HD" if stream.get("hd") is True else (
                "SD" if stream.get("hd") is False else label
            )
            url = stream.get("stream_url") or stream.get("direct_stream_url")
            if not str(url or "").strip():
                # Nested deeper than the known keys - recovered rather than lost.
                deeper = _deep_urls(
                    {
                        key: value for key, value in stream.items()
                        if key not in {"channel_poster", "poster", "logo", "image"}
                    }
                )
                if deeper:
                    url = deeper[0]
                    stats["recovered_by_deep_scan"] += 1
            server = _server(
                url,
                label=label,
                quality=quality,
                headers=base_headers,
                drm=_clearkey(stream.get("drm_key")),
            )
            key = _channel_key(label)
            bucket = grouped.setdefault(
                key,
                {
                    "name": _strip_quality(label),
                    "logo": _first_text(stream.get("channel_poster")),
                    "servers": [],
                },
            )
            if server is None:
                named_without_link += 1
                continue
            bucket["servers"].append(server)

        if named_without_link:
            _stats(source_id)["skip_reasons"]["stream named but url empty"] += named_without_link

        channels = [
            _channel(bucket["name"], bucket["servers"], bucket["logo"])
            for bucket in grouped.values()
        ]
        records.append(
            _record(
                source_id,
                name=name,
                status_raw=_first_text(row.get("status")),
                channels=channels,
                logos=(
                    row.get("poster"),
                    info.get("teamAFlag"),
                    info.get("teamBFlag"),
                    info.get("event_logo"),
                ),
                competition=_first_text(info.get("eventName")),
                sport=_first_text(row.get("category"), row.get("Category")),
                start_time=_parse_clock(
                    _first_text(row.get("start_time_bd"), info.get("startTime"))
                ),
                source_says_ended=(
                    True
                    if str(row.get("status") or "").strip().upper()
                    in {"FINISHED", "ENDED", "FT", "COMPLETED"}
                    else None
                ),
                identity=_first_text(row.get("id")),
            )
        )
    return records


# The \b matters. Without it the LOW alternative matched the last three
# letters of "Willow" and the most important cricket channel on the site was
# published as "Wil".
_QUALITY_TAIL = re.compile(
    r"(?i)\s*[\(\[]?\s*\b(?:4k|uhd|fhd|full\s*hd|hd|sd|whd|ghd|low(?:\s*quality)?|"
    r"\d{3,4}p)\s*[\)\]]?\s*$"
)


def _strip_quality(label: Any) -> str:
    """"WILLOW FHD" -> "WILLOW", "Willow (SD)" -> "Willow". Never emptied."""
    text = " ".join(str(label or "").split())
    if not text:
        return ""
    stripped = _QUALITY_TAIL.sub("", text).strip(" -–—:|")
    return " ".join(stripped.split()) or text


def _channel_key(label: Any) -> str:
    text = _strip_quality(label).casefold()
    return re.sub(r"[^a-z0-9]+", " ", text).strip() or "__unnamed__"


def _same_fixture(label: Any, name: Any) -> bool:
    """Is this stream label just the match title again?"""
    def key(value: Any) -> str:
        text = _strip_quality(value).casefold()
        text = re.sub(r"[^a-z0-9]+", " ", text)
        return " ".join(text.split())

    left = key(label)
    right = key(name)
    if not left or not right:
        return False
    return left == right or left in right or right in left


# ---------------------------------------------------------------------------
# S9 - sm-monirulislam/Fancode_Auto_Update_Playlist/fancode_data.json
# ---------------------------------------------------------------------------

def adapt_fancode(payload: Dict[str, Any], source_id: str) -> List[Dict[str, Any]]:
    """`matches[]` with one `stream_link`, present only on the three LIVE rows."""
    records: List[Dict[str, Any]] = []
    rows = payload.get("matches")
    if not isinstance(rows, list):
        return records
    stats = _stats(source_id)
    stats["total_records"] += len(rows)

    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            _skip(source_id, index, "record is not an object")
            continue
        name = _match_name(row.get("match_name"), row.get("title"))
        if not name:
            team_a = _first_text(row.get("team_1"))
            team_b = _first_text(row.get("team_2"))
            name = f"{team_a} vs {team_b}" if team_a and team_b else ""
        if not name:
            _skip(source_id, index, "no match name")
            continue
        servers = [_server(row.get("stream_link"), label="FanCode", quality="HD")]
        records.append(
            _record(
                source_id,
                name=name,
                status_raw=_first_text(row.get("status")),
                channels=[_channel("FanCode", servers)],
                logos=(row.get("src"),),
                competition=_first_text(row.get("event_name")),
                sport=_first_text(row.get("event_category")),
                start_time=_parse_clock(row.get("startTime"), tz=IST),
                identity=_first_text(row.get("match_id")),
            )
        )
    return records


# ---------------------------------------------------------------------------
# S10 - srhady/crichd-speical-live-event/Footy_Live.json
# ---------------------------------------------------------------------------

def adapt_spaced_keys(payload: Dict[str, Any], source_id: str) -> List[Dict[str, Any]]:
    """`matches[]` whose keys carry spaces: "match name", "Start time".

    `Channels[]` was empty on all 24 records when this was written
    (`total_links: 0`), so its filled shape is unverified. Every plausible
    spelling of a URL and a name is read, and anything unrecognised is counted
    in `unknown_fields` rather than passed over in silence.
    """
    records: List[Dict[str, Any]] = []
    rows = payload.get("matches")
    if not isinstance(rows, list):
        return records
    stats = _stats(source_id)
    stats["total_records"] += len(rows)

    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            _skip(source_id, index, "record is not an object")
            continue
        name = _first_text(row.get("match name"), row.get("match_name"))
        if not name:
            _skip(source_id, index, "no 'match name'")
            continue

        headers: Dict[str, str] = {}
        referer = _first_text(row.get("referer"))
        if referer:
            headers["Referer"] = referer
            headers["Origin"] = referer.rstrip("/")
        agent = _first_text(row.get("User agent"), row.get("user_agent"))
        if agent:
            headers["User-Agent"] = agent

        grouped: "collections.OrderedDict[str, Dict[str, Any]]" = collections.OrderedDict()
        channels_raw = row.get("Channels")
        for entry in channels_raw if isinstance(channels_raw, list) else []:
            if isinstance(entry, str):
                server = _server(entry, quality="HD", headers=headers)
                if server:
                    grouped.setdefault("__unnamed__", {"name": "", "logo": "", "servers": []})
                    grouped["__unnamed__"]["servers"].append(server)
                continue
            if not isinstance(entry, dict):
                continue
            url = _first_text(*(entry.get(key) for key in (
                "url", "link", "stream_url", "stream_link", "Url", "Link",
                "Stream", "stream", "m3u8",
                # This feed writes "match name" and "Start time", so its
                # Channels[] is far more likely to write "Stream link" than
                # "stream_link". Both are read.
                "Stream link", "Stream Link", "Stream url", "Stream URL",
                "Channel link", "Channel Link", "Channel url", "Channel URL",
            )))
            if not url:
                # Still nothing under any spelling: recovered from wherever in
                # the entry it actually sits, rather than dropped.
                deeper = _deep_urls(
                    {
                        key: value for key, value in entry.items()
                        if str(key).strip().lower() not in {"logo", "poster", "image"}
                    }
                )
                if deeper:
                    url = deeper[0]
                    stats["recovered_by_deep_scan"] += 1
            label = _first_text(*(entry.get(key) for key in (
                "name", "channel", "channel_name", "Channel", "Name",
                "Channel Name", "title",
            )))
            for key in entry:
                if key not in {
                    "url", "link", "stream_url", "stream_link", "Url", "Link",
                    "Stream", "stream", "m3u8", "name", "channel", "channel_name",
                    "Channel", "Name", "Channel Name", "title", "referer",
                    "User agent", "user_agent", "logo", "Logo", "quality",
                    "Stream link", "Stream Link", "Stream url", "Stream URL",
                    "Channel link", "Channel Link", "Channel url", "Channel URL",
                }:
                    stats["unknown_fields"][f"Channels[].{key}"] += 1
            server = _server(
                url,
                label=label,
                quality=_first_text(entry.get("quality")) or "HD",
                headers=headers,
            )
            bucket = grouped.setdefault(
                _channel_key(label),
                {
                    "name": _strip_quality(label),
                    "logo": _first_text(entry.get("logo"), entry.get("Logo")),
                    "servers": [],
                },
            )
            if server is not None:
                bucket["servers"].append(server)

        channels = [
            _channel(bucket["name"], bucket["servers"], bucket["logo"])
            for bucket in grouped.values()
        ]
        end_time = _parse_clock(row.get("End time"))
        records.append(
            _record(
                source_id,
                name=name,
                status_raw=_first_text(row.get("Status")),
                channels=channels,
                logos=(row.get("Team 1 Logo"), row.get("Team 2 Logo")),
                competition=_first_text(row.get("Tour/Group name")),
                sport=_first_text(row.get("Category")),
                start_time=_parse_clock(row.get("Start time")),
                end_time=end_time,
                # A fixture row with "Start time" and "End time" beside each
                # other. Measured on 2026-09-05: 24 rows carrying one, and
                # `Milan vs Juventus` reads 18:45 -> 21:00 - a football
                # match, not a rights window.
                end_time_stated=True,
                source_says_ended=_ended_by_clock(end_time),
                identity="",
            )
        )
    return records


def _ended_by_clock(end_time: str) -> Optional[bool]:
    if not end_time:
        return None
    try:
        parsed = datetime.fromisoformat(end_time)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return True if datetime.now(timezone.utc) > parsed else None


# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# sportlive18/Fancode-New-Auto-Update/fancode.json
#
# A second Fancode feed, and a different shape from the one adapt_fancode
# reads: that one carries a single `stream_link`, this one carries `adfree_url`
# and `dai_url` side by side. Measured on 2026-08-28: 40 rows, 5 LIVE and 35
# UPCOMING, and on every LIVE row the two URLs were byte-identical - so the
# ad-free preference is implemented because it is the stated rule, not because
# this snapshot exercised it. The 35 UPCOMING rows carried no URL at all, which
# is why the metadata matters: it is the only thing they have.
# ---------------------------------------------------------------------------

def adapt_sportlive_fancode(payload: Dict[str, Any], source_id: str) -> List[Dict[str, Any]]:
    """`matches[]` with adfree_url preferred over dai_url."""
    records: List[Dict[str, Any]] = []
    rows = payload.get("matches")
    if not isinstance(rows, list):
        return records
    stats = _stats(source_id)
    stats["total_records"] += len(rows)

    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            _skip(source_id, index, "record is not an object")
            continue
        name = _match_name(row.get("match_name"), row.get("title"))
        if not name:
            team_a = _first_text(row.get("team_1"))
            team_b = _first_text(row.get("team_2"))
            name = f"{team_a} vs {team_b}" if team_a and team_b else ""
        if not name:
            _skip(source_id, index, "no match name")
            continue

        # The feed names a User-Agent on the rows that carry a stream, and the
        # stream is served only to that agent. Dropping it would turn a
        # playable route into a 403 that looks like a dead one.
        headers: Dict[str, str] = {}
        agent = _first_text(row.get("user-agent"), row.get("user_agent"))
        if agent:
            headers["User-Agent"] = agent

        servers = [
            # Ad-free first, by the owner's rule. Both are kept: when they
            # differ, the second is a real fallback rather than a duplicate,
            # and _order_servers drops nothing that plays.
            _server(row.get("adfree_url"), label="FanCode ad-free",
                    quality="HD", headers=headers),
            _server(row.get("dai_url"), label="FanCode",
                    quality="HD", headers=headers),
        ]
        records.append(
            _record(
                source_id,
                name=name,
                status_raw=_first_text(row.get("status")),
                channels=[_channel("FanCode", servers, logo=_first_text(row.get("src")))],
                logos=(row.get("src"),),
                competition=_first_text(row.get("event_name")),
                sport=_first_text(row.get("event_category")),
                start_time=_parse_clock(row.get("startTime"), tz=IST),
                identity=_first_text(row.get("match_id")),
            )
        )
    return records


# ---------------------------------------------------------------------------
# sportlive18/Sonyliv-Playlist-Autoupdate/sonyliv.json
#
# Measured on 2026-08-28: 20 rows, 8 with isLive true. There is no start-time
# field at all - only isLive - so this adapter routes on that and leaves the
# start time empty rather than inventing one; the lifecycle router owns the
# rest. dai_url, pub_url and video_url were byte-identical on every live row,
# so all three are offered and _order_servers folds the duplicates.
#
# The same fixture appears once per audio language, distinguished by the
# `[ENG]`/`[HIN]` suffix on match_name and by the contentId suffix. Those are
# one event with two servers, not two events: the language is carried on the
# server label so the merge keeps a single card.
# ---------------------------------------------------------------------------

_SONYLIV_LANG_SUFFIX = re.compile(r"\s*\[([A-Z]{2,4})\]\s*$")


def adapt_sportlive_sonyliv(payload: Dict[str, Any], source_id: str) -> List[Dict[str, Any]]:
    """`matches[]` keyed by isLive, one row per audio language."""
    records: List[Dict[str, Any]] = []
    rows = payload.get("matches")
    if not isinstance(rows, list):
        return records
    stats = _stats(source_id)
    stats["total_records"] += len(rows)

    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            _skip(source_id, index, "record is not an object")
            continue
        raw_name = _first_text(row.get("match_name"), row.get("event_name"))
        if not raw_name:
            _skip(source_id, index, "no match name")
            continue

        # The language belongs on the server, not in the event name, or the
        # same fixture becomes two cards.
        language = ""
        suffix = _SONYLIV_LANG_SUFFIX.search(raw_name)
        if suffix:
            language = suffix.group(1)
            raw_name = _SONYLIV_LANG_SUFFIX.sub("", raw_name).strip()
        if not language:
            identifier = _first_text(row.get("contentId"))
            if "_" in identifier:
                language = identifier.rsplit("_", 1)[-1].upper()
        name = _match_name(raw_name) or raw_name

        is_live = row.get("isLive")
        if isinstance(is_live, str):
            is_live = is_live.strip().casefold() in {"true", "1", "yes", "live"}
        status_raw = "LIVE" if is_live else "UPCOMING"

        channel_name = _first_text(row.get("broadcast_channel")) or "SonyLIV"
        label_bits = [bit for bit in (channel_name, language) if bit]
        label = " ".join(label_bits)
        servers = [
            _server(row.get("dai_url"), label=label, quality="HD"),
            _server(row.get("pub_url"), label=label, quality="HD"),
            _server(row.get("video_url"), label=label, quality="HD"),
        ]
        records.append(
            _record(
                source_id,
                name=name,
                status_raw=status_raw,
                channels=[_channel(channel_name, servers, logo=_first_text(row.get("src")))],
                logos=(row.get("src"),),
                competition=_first_text(row.get("event_name")),
                sport=_first_text(row.get("event_category")),
                identity=_first_text(row.get("contentId")),
            )
        )
    return records


ADAPTERS: Dict[str, Callable[[Dict[str, Any], str], List[Dict[str, Any]]]] = {
    "sonyliv": adapt_sonyliv,
    "link_live": adapt_link_live,
    "tapmad": adapt_tapmad,
    "server_dict": adapt_server_dict,
    "named_streams": adapt_named_streams,
    "fancode": adapt_fancode,
    "spaced_keys": adapt_spaced_keys,
    "sportlive_fancode": adapt_sportlive_fancode,
    "sportlive_sonyliv": adapt_sportlive_sonyliv,
}


def adapter_name_for(source_info: Dict[str, Any]) -> str:
    """The reader this source declares, or the one mapped to its id."""
    declared = str((source_info or {}).get("adapter") or "").strip()
    if declared:
        return declared
    return ADAPTER_BY_SOURCE.get(str((source_info or {}).get("id") or ""), "")


def parse_event_source(
    content: str,
    source_info: Dict[str, Any],
) -> Optional[List[Dict[str, Any]]]:
    """Run this source's own reader. None when no reader is configured."""
    adapter = adapter_name_for(source_info)
    handler = ADAPTERS.get(adapter)
    if handler is None:
        return None
    source_id = str(source_info.get("id") or adapter)
    # None, not [] - the caller reads None as "this reader does not apply" and
    # goes on to the m3u and generic-JSON parsers. Returning an empty list here
    # would claim the source was read and found empty, so a feed that answered
    # with an error page or switched to m3u would silently publish nothing.
    try:
        payload = json.loads(str(content or "").lstrip("﻿"))
    except (TypeError, ValueError) as error:
        _stats(source_id)["skip_reasons"][f"payload not JSON: {error}"] += 1
        return None
    if not isinstance(payload, dict):
        _stats(source_id)["skip_reasons"]["payload root is not an object"] += 1
        return None

    records = handler(payload, source_id)
    stats = _stats(source_id)
    for record in records:
        stats["parsed"] += 1
        stats["status_counts"][record["status_raw"] or "(none)"] += 1
        stats["channels"] += len(record["channels"])
        stats["servers"] += sum(len(c["servers"]) for c in record["channels"])
        if record["metadata_only"]:
            stats["metadata_only"] += 1
        if record["source_says_ended"]:
            stats["source_says_ended"] += 1
        for channel in record["channels"]:
            for server in channel["servers"]:
                if server["drm"]:
                    stats["with_drm"] += 1
                if server["headers"]:
                    stats["with_headers"] += 1
    return records


def adapter_report() -> Dict[str, Any]:
    """The per-source accounting, JSON-safe, for reports/."""
    out: Dict[str, Any] = {}
    for source_id, entry in ADAPTER_STATS.items():
        out[source_id] = {
            **{
                key: value
                for key, value in entry.items()
                if not isinstance(value, collections.Counter)
            },
            "status_counts": dict(entry["status_counts"]),
            "skip_reasons": dict(entry["skip_reasons"]),
            "unknown_fields": dict(entry["unknown_fields"]),
        }
    return out

# ---------------------------------------------------------------------------
# flatten to the candidate shape the rest of the pipeline already consumes
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Per-record LIVE / UPCOMING routing
# ---------------------------------------------------------------------------

# Measured, not assumed. The 442 records served on 2026-08-20 - by the
# eleven feeds registered at that date - carry exactly seven distinct
# status strings:
#
#   LIVE 17 | Live 3 | LIVE_NOW 5 | 1H 2      -> playing now
#   NS 173 | UPCOMING 155 | Upcoming 10       -> not started
#   FINISHED 75                               -> over
#
# The extra tokens below are the neighbouring codes from those same three
# vocabularies (the API-Football short codes axsports and bingstream use, and
# sm-sportsdata's own words), so a feed that starts saying "HT" or "FT"
# tomorrow is routed instead of falling through to the clock.
LIVE_STATUS_TOKENS = frozenset({
    "live", "livenow", "live_now", "inplay", "in_play", "playing", "onair",
    "on_air", "1h", "2h", "ht", "et", "bt", "p", "int", "1st_half",
    "2nd_half", "halftime", "half_time", "break",
})
UPCOMING_STATUS_TOKENS = frozenset({
    "ns", "upcoming", "notstarted", "not_started", "scheduled", "sched",
    "pre", "pregame", "tba", "tbd", "fixture", "soon",
})
ENDED_STATUS_TOKENS = frozenset({
    "finished", "ft", "aet", "pen", "ap", "ended", "end", "over", "complete",
    "completed", "result", "abandoned", "aban", "postponed", "postp", "pst",
    "cancelled", "canceled", "canc", "awarded", "awd", "walkover", "wo",
    "suspended", "susp",
})


def _status_token(value: Any) -> str:
    return re.sub(r"[^a-z0-9_]+", "", str(value or "").strip().lower())


def record_pipeline(record: Dict[str, Any], default: str = "today_match") -> str:
    """Which tab this one record belongs to, decided after parsing.

    Every event source is registered once, in
    config/sources/today-match.json, because every one of them mixes states
    inside a single file - axsports serves 3 live and 84 not-started rows from
    the same array. Routing therefore cannot come from which config file a
    source sits in, the way it did while the same URL had to be listed twice;
    it comes from the status the record itself carries, which is what the rule
    "LIVE/Today/Upcoming classification happens after parsing" asks for.

    An ended record still returns the Today Match pipeline. It never publishes
    - merger refuses a stream whose own feed says the match is over - but it
    has to stay in the candidate list, because a feed saying "FINISHED" is
    exactly the authority verdict that retires the card a previous scan
    published.
    """
    token = _status_token(record.get("status_raw"))
    if record.get("source_says_ended") is True or token in ENDED_STATUS_TOKENS:
        return default
    if token in LIVE_STATUS_TOKENS:
        return default
    if token in UPCOMING_STATUS_TOKENS:
        return "upcoming"

    # No status word this vocabulary knows: fall back to the clock, and if
    # there is no clock either, let the presence of a stream decide. A record
    # with no stream and no schedule can only ever be an Upcoming card, so
    # sending it to Today Match would be the one choice that loses it.
    clock = _window_status(
        str(record.get("start_time") or ""), str(record.get("end_time") or "")
    )
    if clock == "UPCOMING":
        return "upcoming"
    if clock == "FINISHED":
        return default
    return default if record.get("channels") else "upcoming"


def flatten_records(
    records: Iterable[Dict[str, Any]],
    source_info: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """One candidate per server, carrying its channel name.

    The pipeline downstream is built around a flat candidate per stream -
    json_parser emits that, and scanner/channel_groups.py regroups them into
    channels afterwards. So the three-level record is flattened here rather
    than teaching every later stage a new shape.

    A record with no server at all still emits one metadata-only candidate,
    because an upcoming fixture has no stream yet and must still reach the
    Upcoming tab.
    """
    source_id = str(source_info.get("id") or "")
    source_name = str(source_info.get("name") or source_id)
    source_url = str(source_info.get("url") or "")
    try:
        priority = int(source_info.get("priority", 100))
    except (TypeError, ValueError):
        priority = 100
    pipeline = str(
        source_info.get("pipeline")
        or source_info.get("source_pipeline")
        or source_info.get("_pipeline")
        or "today_match"
    )
    broadcaster = str(source_info.get("broadcaster") or "").strip()

    out: List[Dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        routed = record_pipeline(record, pipeline)
        entry = _stats(str(record.get("source_id") or source_id))
        if record.get("source_says_ended") is True:
            entry["routed_ended"] += 1
        elif routed == "upcoming":
            entry["routed_upcoming"] += 1
        else:
            entry["routed_today"] += 1
        common = {
            "name": record["name"],
            "logo": record["logo"],
            "group_title": record.get("sport") or "",
            "status": record["status_raw"],
            "original_status": record["status_raw"],
            "start_time": record.get("start_time") or "",
            "end_time": record.get("end_time") or "",
            "end_time_stated": bool(record.get("end_time_stated")),
            "competition": record.get("competition") or "",
            "event_url": "",
            "tvg_id": record.get("identity") or "",
            "parser": "event_adapter",
            "adapter": adapter_name_for(source_info),
            "source_id": source_id,
            "source_name": source_name,
            "source_url": source_url,
            "source_priority": priority,
            "source_pipeline": routed,
            "configured_source_pipeline": pipeline,
            "routed_by_record_status": True,
            "category_mode": source_info.get("category_mode", "detect"),
            "manual_can_override_category": source_info.get(
                "manual_can_override_category", True
            ),
            "force_category": source_info.get("force_category", ""),
            "force_output": source_info.get("force_output", ""),
            "default_category": source_info.get("default_category", ""),
            "content_filter": source_info.get("content_filter", ""),
            "status_filter": list(source_info.get("status_filter") or []),
            "bd_candidate": bool(source_info.get("bd_candidate", False)),
            "preserve_source_headers": bool(
                source_info.get("preserve_source_headers", True)
            ),
            "preserve_drm": bool(source_info.get("preserve_drm", True)),
            "allow_without_stream": bool(
                source_info.get("allow_without_stream", True)
            ),
            "source_says_ended": record.get("source_says_ended"),
            "round_label": record.get("round") or "",
            "logo_candidates": list(record.get("logo_candidates") or []),
        }

        if not record["channels"]:
            out.append({
                **common,
                "url": "",
                "headers": {},
                "drm": {},
                "channel_name": broadcaster,
                "tvg_name": broadcaster,
                "stream_index": 0,
                "metadata_only": True,
            })
            entry["candidates"] += 1
            continue

        index = 0
        for channel in record["channels"]:
            label = channel["channel_name"] or broadcaster
            for server in channel["servers"]:
                out.append({
                    **common,
                    "url": server["url"],
                    "headers": dict(server["headers"]),
                    "drm": dict(server["drm"]),
                    "channel_name": label,
                    "tvg_name": label,
                    "channel_logo": channel.get("channel_logo") or "",
                    "server_label": server.get("server_label") or "",
                    "quality_rank": server.get("quality_rank"),
                    "has_token": server.get("has_token"),
                    "stream_index": index,
                    "metadata_only": False,
                })
                entry["candidates"] += 1
                index += 1

        # An upcoming fixture keeps its match data even when every link
        # it shipped with turns out to be dead - which is the normal
        # case, because a link published six hours before kickoff is not
        # serving yet. The streams above are kept exactly as they are;
        # this companion carries the fixture on its own so a card is
        # still built when none of them verify. merger prefers a
        # playable candidate over it whenever one exists, so it costs
        # nothing when the links do work.
        if routed == "upcoming":
            out.append({
                **common,
                "url": "",
                "headers": {},
                "drm": {},
                "channel_name": broadcaster,
                "tvg_name": broadcaster,
                "stream_index": index,
                "metadata_only": True,
                "metadata_companion": True,
            })
            entry["candidates"] += 1
    return out


def parse_event_source_flat(
    content: str,
    source_info: Dict[str, Any],
) -> Optional[List[Dict[str, Any]]]:
    """The reader plus the flattener, for source_loader to call."""
    records = parse_event_source(content, source_info)
    if records is None:
        return None
    return flatten_records(records, source_info)
