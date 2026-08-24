"""
Stream Merger & Deduplication Engine

Merges verified and protected stream candidates into unified cards.

Every playable card keeps 1 Primary + up to 5 active Backups. Further verified
playback configurations remain in a standby list for future promotion. Ranking is status-first:
verified_global/verified_bd -> verified_proxy -> stale_last_good -> geo_pending
-> retryable_pending -> host_deferred. HTTPS is preferred within the same tier.
"""

from __future__ import annotations

import json
import hashlib
import re
from scanner import route_preference
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

try:  # The routing rule the merge has to group by. Stdlib-only module, no cycle.
    from scanner.event_lifecycle import event_destination
except ImportError:  # pragma: no cover - direct-module import path
    from event_lifecycle import event_destination


def _load_json_file(file_path: str | Path) -> Dict[str, Any]:
    path = Path(file_path)
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _safe_int(value: Any, default: int, minimum: int = 0) -> int:
    try:
        result = int(value)
        return max(minimum, result)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _response_time_ms(stream: Dict[str, Any]) -> int:
    raw_value = stream.get("response_time_ms")
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        return 999999
    return value if value > 0 else 999999


def _verification_label(stream: Dict[str, Any]) -> str:
    """Preserve the real status instead of inventing verified_global."""
    if stream.get("metadata_only") is True:
        return "metadata_only"

    explicit = str(stream.get("verification_status") or "").strip().lower()
    if explicit:
        return explicit

    if stream.get("verified") is True or stream.get("is_valid") is True:
        return "verified"

    return ""


def _extract_hostname(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _stream_identity_key(stream: Dict[str, Any]) -> str:
    """Identify an exact playable setup, not merely an equal URL."""
    payload = {
        "url": str(stream.get("url") or "").strip(),
        "headers": stream.get("headers") if isinstance(stream.get("headers"), dict) else {},
        "drm": stream.get("drm") if isinstance(stream.get("drm"), dict) else {},
        "header_profile": str(stream.get("header_profile") or ""),
        "proxy_mode": str(stream.get("proxy_mode") or "auto"),
        "inherit_manifest_query": bool(stream.get("inherit_manifest_query", False)),
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _source_provenance(stream: Dict[str, Any]) -> List[Dict[str, str]]:
    records: List[Dict[str, str]] = []
    existing = stream.get("source_provenance")
    if isinstance(existing, list):
        records.extend(dict(item) for item in existing if isinstance(item, dict))
    source_id = str(stream.get("source_id") or "").strip()
    if source_id:
        records.append(
            {
                "source_id": source_id,
                "source_name": str(stream.get("source_name") or source_id).strip(),
                "source_url": str(stream.get("source_url") or "").strip(),
            }
        )
    unique: Dict[str, Dict[str, str]] = {}
    for record in records:
        source_id = str(record.get("source_id") or "").strip()
        if source_id:
            unique.setdefault(source_id, record)
    return list(unique.values())


def _merge_provenance(preferred: Dict[str, Any], duplicate: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(preferred)
    unique: Dict[str, Dict[str, str]] = {}
    for record in _source_provenance(preferred) + _source_provenance(duplicate):
        unique.setdefault(record["source_id"], record)
    merged["source_provenance"] = list(unique.values())
    merged["source_ids"] = list(unique)
    return merged


def _normalize_movie_title(value: Any) -> str:
    """Build a source-independent movie title key without removing episode data."""
    text = str(value or "").casefold()
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(
        r"\b(?:official|full\s*movie|movie|film|uncut|web[-\s]?dl|webrip|"
        r"hdrip|bluray|brrip|dvdrip|hdtc|camrip|amzn|amazon|netflix|"
        r"dsnp|hotstar|hoichoi|chorki|aha|esub|org|dual\s*audio|dual|"
        r"multi\s*audio|hindi\s*dubbed|bengali\s*dubbed|bangla\s*dubbed|"
        r"4k|2k|uhd|fhd|full\s*hd|hd|sd|2160p|1440p|1080p|720p|"
        r"576p|480p|360p|x264|x265|h\.?264|h\.?265|hevc|aac|"
        r"fibwatch\.?com)\b",
        " ",
        text,
    )
    text = re.sub(r"\.(?:mkv|mp4|m3u8|mov|avi|webm)\b", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _movie_identity_key(item: Dict[str, Any]) -> str:
    """Group the same title/year across different sources into one movie card."""
    for field_name in ("imdb_id", "tmdb_id"):
        value = str(item.get(field_name) or "").strip().casefold()
        if value:
            return f"{field_name}:{value}"

    raw_name = str(item.get("name") or item.get("title") or "").strip()
    explicit_year = str(item.get("year") or "").strip()
    year_match = re.search(r"\b(?:19|20)\d{2}\b", raw_name)
    year = explicit_year or (year_match.group(0) if year_match else "")
    title_without_year = re.sub(r"\b(?:19|20)\d{2}\b", " ", raw_name)
    normalized_title = _normalize_movie_title(title_without_year)

    if normalized_title:
        return f"title:{normalized_title}:{year}"

    fallback_id = str(item.get("id") or "").strip().casefold()
    if fallback_id:
        return f"id:{fallback_id}"

    return (
        f"fallback:{str(item.get('source_id') or '').casefold()}:"
        f"{item.get('stream_index', item.get('source_index', 0))}"
    )


def _verification_badge(stream: Dict[str, Any]) -> str:
    status = str(stream.get("verification_status") or "").strip().casefold()
    if status in {"verified_global", "verified_proxy", "verified_bd", "verified"}:
        return "Verified"
    if status == "stale_last_good":
        return "Last Good"
    if status in {"geo_pending", "bd_protected_pending"}:
        return "Geo/BD"
    if status == "retryable_pending":
        return "Temporary"
    if status == "host_deferred":
        return "Unconfirmed"
    return ""


def _parse_resolution_height(res_val: Any) -> int:
    if not res_val:
        return 0
    if isinstance(res_val, (int, float)):
        return max(0, int(res_val))

    text = str(res_val).strip().upper()

    m_dim = re.search(r"\d+\s*[X×]\s*(\d+)", text)
    if m_dim:
        return int(m_dim.group(1))

    m_p = re.search(r"(\d+)P", text)
    if m_p:
        return int(m_p.group(1))

    if "4K" in text or "UHD" in text:
        return 2160
    if "2K" in text:
        return 1440
    if "FHD" in text or "FULL HD" in text:
        return 1080
    if "HD" in text:
        return 720
    if "SD" in text:
        return 480

    try:
        return int(text)
    except ValueError:
        return 0


def _is_publishable_stream(stream: Dict[str, Any]) -> bool:
    """
    Publish only genuinely verified streams or explicitly protected BD streams.
    A status label by itself is never enough to publish a confirmed tier.
    """
    pipeline = str(stream.get("source_pipeline") or "").lower()

    # An explicit visibility denial always wins over HTTP verification.  This
    # is how a real-player failure remains recorded without leaking back into
    # the public catalogue on the next scan.
    if stream.get("publish_allowed") is False:
        return False

    # Section 21. A feed stating the match is over is the strongest
    # verdict there is, and five of the eleven event feeds supply one:
    # sm-sportsdata's FINISHED (75 records on 2026-08-20), axsports and
    # bingstream with has_ended, footy-live with an End time already
    # past. Verified playback proves the URL still answers, not that the
    # match is still on, so the ended verdict is read ahead of
    # verification rather than after it.
    if stream.get("source_says_ended") is True:
        return False

    if stream.get("metadata_only", False):
        return (
            pipeline == "upcoming"
            and bool(stream.get("allow_without_stream", False))
            and not str(stream.get("url") or "").strip()
        )

    status = str(stream.get("verification_status") or "").strip().lower()
    confirmed = (
        stream.get("verified") is True
        or stream.get("is_valid") is True
    )
    publish_allowed = stream.get("publish_allowed") is True

    # Pending/geo/last-good labels are not current playback proof for movies.
    if pipeline == "movies" and not confirmed:
        return False

    if status in {
        "failed",
        "failed_bd",
        "rejected_low_quality",
        "quarantine",
    }:
        return False

    if confirmed:
        return True

    return (
        publish_allowed
        and status in {
            "stale_last_good",
            "bd_protected_pending",
            "geo_pending",
            "retryable_pending",
            "host_deferred",
        }
    )


def _meets_resolution_contract(
    stream: Dict[str, Any],
    settings: Dict[str, Any],
) -> bool:
    if stream.get("metadata_only") is True:
        return True
    pipeline = str(stream.get("source_pipeline") or "tv").strip().casefold()
    resolution = settings.get("resolution")
    if not isinstance(resolution, dict):
        resolution = {}
    minimum_by_pipeline = {
        "tv": _safe_int(resolution.get("tv_minimum_height", 720), 720),
        "movies": _safe_int(resolution.get("movie_minimum_height", 720), 720),
        "today_match": _safe_int(resolution.get("event_minimum_height", 720), 720),
        "upcoming": _safe_int(resolution.get("event_minimum_height", 720), 720),
    }
    minimum = minimum_by_pipeline.get(pipeline, 720)
    if minimum <= 0:
        return True
    detected = _parse_resolution_height(
        stream.get("resolution_height")
        or stream.get("height")
        or stream.get("resolution")
    )
    return detected >= minimum


def _is_strongly_verified_today_match(stream: Dict[str, Any]) -> bool:
    """Require a real verified flag before Today Match suppresses Upcoming."""
    if str(stream.get("source_pipeline") or "").lower() != "today_match":
        return False
    if not stream.get("url") or stream.get("metadata_only"):
        return False

    confirmed = (
        stream.get("verified") is True
        or stream.get("is_valid") is True
    )
    if not confirmed:
        return False

    status = str(stream.get("verification_status") or "").strip().lower()
    return status in {
        "",
        "verified_global",
        "verified_proxy",
        "verified_bd",
        "verified",
    }


#: Which day or session of a multi-day fixture an entry is relaying.
_MULTI_DAY_LABEL = re.compile(
    r"(?i)(?:^|[\s\-|,(])"
    r"(?:day|session|innings|inning|part|stage)\s*[-:]?\s*\d{1,2}"
    r"(?:\s*(?:of|/)\s*\d{1,2})?"
    r"(?=$|[\s\-|,)])"
)

#: The fixture's own number inside its series. Must survive normalisation, or
#: the 1st Test and the 2nd Test collapse into one card.
_FIXTURE_ORDINAL_KINDS = (
    r"test|odi|t20i?|match|leg|round|final|semi[\s-]?final|quarter[\s-]?final"
)
_FIXTURE_ORDINAL = re.compile(
    rf"(?i)\b(\d{{1,2}})(?:st|nd|rd|th)\s+({_FIXTURE_ORDINAL_KINDS})\b"
)
#: The same ordinal once "1st test" has been canonicalised to "1 test".
_CANONICAL_ORDINAL = re.compile(
    rf"(?i)\b(\d{{1,2}})\s+({_FIXTURE_ORDINAL_KINDS})\b"
)


def _strip_multi_day_labels(text: str) -> str:
    """Guide 19: one fixture spanning several days stays one card.

    A Test is relayed as "1st Test", "1st Test Day 2", "- 1st Test - Day 3"
    and "Day 4 - 1st Test - ...". Those are the same match. The fixture's own
    ordinal ("1st Test") is preserved and pinned to the front, because the day
    labels sit in positions that otherwise caused the ordinal to be dropped -
    which would have merged the 1st Test with the 2nd.
    """
    # Canonicalise "1st test" to "1 test" first, so a re-appended ordinal and
    # one that survived in place produce the identical key.
    cleaned = _FIXTURE_ORDINAL.sub(r"\1 \2", text)
    cleaned = _MULTI_DAY_LABEL.sub(" ", cleaned)
    return " ".join(cleaned.split())


#: A round descriptor or a season year sitting in FRONT of the participants.
#: Providers write the series before the teams as often as after them:
#:
#:     "India tour of Sri Lanka 2026 1st Test Sri Lanka vs India"
#:     "Copa America 2026 Brazil vs Argentina"
#:
#: The "team A vs team B" extraction below anchors at the start of the title, so
#: the whole series name was swallowed into the left-hand side and the key came
#: out as "india-tour-of-sri-lanka-2026-1-test-sri-lanka-vs-india" - a second card
#: for a fixture already published as "sri-lanka-vs-india-1-test".
_COMPETITION_PREFIX_MARKER = re.compile(
    rf"(?i)\b(?:\d{{1,2}}\s+(?:{_FIXTURE_ORDINAL_KINDS})|(?:19|20)\d{{2}})\b"
)


def _strip_competition_prefix(left: str) -> str:
    """Drop a series/season prefix from the left-hand side of "A vs B".

    Only a *round ordinal* ("1 test", "2 odi") or a *four digit year* counts as
    the marker, and only the text up to and including the last such marker is
    removed. A team name does not carry either, so this cannot eat a participant;
    if removing the prefix would leave nothing usable, the original is kept.
    """
    matches = list(_COMPETITION_PREFIX_MARKER.finditer(left))
    if not matches:
        return left
    remainder = " ".join(left[matches[-1].end():].split())
    if len(remainder) < 3:
        return left
    return remainder


def normalize_event_key(name: str) -> str:
    text = str(name or "").casefold()
    text = text.replace("pheonix", "phoenix").replace("spirits", "spirit")
    if "|" in text:
        parts = [p.strip() for p in text.split("|") if p.strip()]
        for p in parts:
            if re.search(r"\b(?:vs|v|versus|tour of)\b", p):
                text = p
                break
        else:
            text = parts[0] if parts else text
    text = _strip_multi_day_labels(text)
    # Held aside because the "team A vs team B" extraction below cuts the title
    # at the first " - ", which is exactly where a title like
    # "Australia vs Bangladesh - 1st Test - Day 3" keeps its fixture number.
    # Losing it would merge the 1st Test with the 2nd.
    ordinal = _CANONICAL_ORDINAL.search(text)

    # Prefer the actual "team A vs team B" portion. Provider and competition
    # suffixes then become backup labels instead of separate match cards.
    # Guide 27 lists "vs", "v" and "versus" as the same separator. Only "vs"
    # and "v." were recognised, so "Yokohama FC v Jubilo Iwata" never matched
    # the same fixture as "Yokohama FC vs Jubilo Iwata".
    match = re.search(
        r"(?:^|[-|])\s*([^-|]+?)\s+(?:versus|vs\.?|v\.?)\s+([^|]+)", text
    )
    if match:
        left = _strip_competition_prefix(match.group(1))
        left = re.sub(r".*?\b(?:tour of|series|trophy|cup|championship)\b\s*", "", left, flags=re.IGNORECASE)
        left = re.sub(r".*?\b(?:\d+(?:st|nd|rd|th)?\s+(?:test|odi|t20i?|match))\b\s*", "", left, flags=re.IGNORECASE)
        right = match.group(2)
        right = re.split(r"\s+-\s+(?!(?:women|men)\b)", right, maxsplit=1)[0]
        gender = "women" if re.search(r"\bwom(?:e|a)n(?:'s|s)?\b", f"{left} {right}") else ""
        left = re.sub(r"\bwom(?:e|a)n(?:'s|s)?\b", " ", left)
        right = re.sub(r"\bwom(?:e|a)n(?:'s|s)?\b", " ", right)
        # An event with no two sides, written as if it had two. sm-sportsdata
        # builds every name as "teamA Vs teamB" and sets both to the same string
        # on 8 of its records, so "Horse Racing" and "Horse Racing Vs Horse
        # Racing" are one fixture under two spellings - the adapter writes the
        # short form now, and a card published under the long one before that
        # has to fold into it rather than sit beside it.
        if left.strip() and left.strip() == right.strip():
            text = f"{left.strip()} {gender}"
        else:
            text = f"{left.strip()} vs {right.strip()} {gender}"

    if ordinal and not _CANONICAL_ORDINAL.search(text):
        text = f"{text} {ordinal.group(1)} {ordinal.group(2)}"

    text = re.sub(
        r"(?i)\b(?:\d{1,2}\s+(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|"
        r"may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|"
        r"nov(?:ember)?|dec(?:ember)?)\s+(?:19|20)\d{2})\b",
        " ",
        text,
    )

    text = re.sub(
        r"(?i)\b(?:official\s+live|live\s+coverage|live\s+match|live\s+now|"
        r"today\s+match|upcoming|scheduled|fixture|not\s+started|live)\b",
        " ",
        text,
    )

    # Broadcaster names carried in the title. Guide 27: one match relayed by
    # six channels is one event, not six. "Willow" alone was stripped but
    # "Willow HD", "Sony Sports Ten 3", "T Sports HD" and "Fox Cricket" were
    # not, so the same fixture still produced several cards.
    text = re.sub(
        r"(?i)\b(?:fancode|tapmad|willow(?:\s+cricket)?|crichd|criclife|"
        r"sony\s*liv|sony\s*(?:sports\s*)?ten|sony\s*sports?|"
        r"star\s*sports?|t\s*sports?|fox\s*(?:cricket|sports?)|"
        r"ptv\s*sports?|a\s*sports?|astro\s*cricket|supersport|"
        r"server|alt|hindi|english|bd|pk)\s*\d*\b",
        " ",
        text,
    )

    text = re.sub(
        r"(?i)\b(?:4k|2k|uhd|fhd|full\s*hd|hd|sd|1080p|720p|480p|360p)\b",
        " ",
        text,
    )

    text = re.sub(r"[^\w\s]", " ", text)
    return " ".join(text.split()).strip().replace(" ", "-")


# Requirement 1 and 11. One canonical sport per event, resolved from the
# source's own category first, then the competition, then the name. Anything
# unrecognised stays "other" - nothing is invented.
_SPORT_RULES: Tuple[Tuple[str, str], ...] = (
    ("esports", r"esports?|e[\s-]?sports?|pubg|dota|valorant|counter[\s-]?strike|league\s+of\s+legends|mobile\s+legends|free\s+fire"),
    ("cricket", r"cricket|\bcric(?:life|hd)\b|t20i?|\bodi\b|test\s+match|\d{1,2}(?:st|nd|rd|th)\s+(?:test|odi|t20i?)|the\s+hundred|\bbbl\b|\bipl\b|\bpsl\b|\bcpl\b|\bbpl\b|\bdpl\b|ashes|vitality\s+blast|\btnpl\b|caribbean\s+premier\s+league|bangladesh\s+premier\s+league|indian\s+premier\s+league|lanka\s+premier\s+league|pakistan\s+super\s+league|big\s+bash(?:\s+league)?|major\s+league\s+cricket|county\s+championship|sheffield\s+shield|plunket\s+shield|tests?\s+series|willow|star\s+sports|sony\s+sports|sony\s+ten|t\s+sports|tsports|ptv\s+sports|a\s+sports|sky\s+sports\s+cricket|sky\s+cricket|fox\s+cricket|astro\s+cricket|super\s*sport\s+cricket|icc|asia\s+cup|ranji|duleep|trophy|tri[\s-]series"),
    ("motorsport", r"motorsport|formula\s?e?\b|\bf1\b|e[\s-]?prix|moto\s?gp|nascar|rally|grand\s+prix|race\s+\d|race\s+day|\bgt4\b|\bgt3\b|\badac\b|superbike|\bmxgp\b|motocross|indycar|cycling|\buci\b|tour\s+de"),
    ("golf", r"\bgolf\b|\bpga\b|\blpga\b|dp\s+world\s+tour|ryder\s+cup"),
    ("tennis", r"tennis|\batp\b|\bwta\b|padel|badminton|squash|roland\s+garros|wimbledon|\b[a-z]+\s+open\b(?!\s+cup)"),
    ("rugby", r"rugby|currie\s+cup|six\s+nations|super\s+rugby|\bnfl\b|american\s+football"),
    ("baseball", r"\bmlb\b|baseball|world\s+series|\bnpb\b"),
    ("basketball", r"basketball|\bnba\b|\bwnba\b|euroleague|basket"),
    ("volleyball", r"volleyball|beach\s+volley"),
    ("hockey", r"ice\s+hockey|\bnhl\b|\bkhl\b|field\s+hockey|\bfih\b|hockey"),
    ("racing", r"horse\s+racing|racecourse|steeplechase|greyhound"),
    ("football", r"football|soccer|premier\s+league|\bepl\b|bundesliga|eredivisie|divisie|serie\s+[ab]|la\s?liga|laliga|ligue\s?\d|s[uü]per\s+lig|\blig\b|liga|uefa|fifa|\bafc\b|\bcaf\b|concacaf|conmebol|libertadores|sudamericana|champions\s+league|europa\s+league|\befl\b|championship|friendlies|frauenliga|ekstraklasa|allsvenskan|superliga|eliteserien|primeira|primera|segunda|coppa|copa|coupe|pokal|deild|torneo\s+federal|\bnb\s+i{1,3}\b|\bjong\b|\bhnl\b|\bnwsl\b|\bnpl\b|\bmls\b|[akj][\s-]?league|\bcup\b|league|divisi[oó]n|division|\bfc\b|\bsc\b|united|manchester|liverpool|arsenal|chelsea|real\s+madrid|barcelona|bayern|juventus|milan|inter|psg|dortmund|atletico|tottenham|napoli|roma|bengaluru\s+fc|mohun\s+bagan|east\s+bengal|\bisl\b"),
)

_SPORT_PATTERNS: Tuple[Tuple[str, Any], ...] = tuple(
    (name, re.compile(pattern, re.IGNORECASE)) for name, pattern in _SPORT_RULES
)

# Requirement 11. Cricket first, football second, everything else after.
SPORT_ORDER: Tuple[str, ...] = ("cricket", "football")

_EVENT_PIPELINES = frozenset({"today_match", "upcoming"})


# "A vs B" as a whole word, for the fixture-title checks below. Defined as a
# named pattern because a literal backslash-b written through a shell heredoc
# silently became a backspace character here once already.
_VERSUS_WORD = re.compile(r"\b(?:vs|v|versus)\b", re.IGNORECASE)


def fixture_identity_key(item: Dict[str, Any], aliases: Optional[Dict[str, str]] = None) -> str:
    """Section 5. One real fixture, one key - whatever channel carried it.

    Source playlists routinely append the broadcaster to the fixture title:

        "Al Nassr Vs Al Fateh FANCODE"
        "Al Nassr Vs Al Fateh FOX DEPORTES"
        "Al Nassr Vs Al Fateh SporTV BR"

    Those are one match on three channels, but normalize_event_key() sees three
    different names and produces three keys - so the merge published three main
    cards for one fixture, which is exactly what section 5 forbids. The channel
    resolver already knows how to pick the broadcaster out of a title, so the
    broadcaster is removed here before the key is computed.

    The removal is deliberately conservative: it only applies when a channel was
    actually resolved, when the name really appears in the title, and when what
    is left still looks like a fixture. Anything else keeps today's key, because
    over-merging two different matches would be far worse than leaving a title
    with a channel suffix on it.
    """
    name = str(item.get("name") or "")
    base = normalize_event_key(name)
    if not name.strip():
        return base

    layer = _channel_layer()
    if layer is None:
        return base
    try:
        from scanner.channel_resolver import resolve_channel_name
    except Exception:  # pragma: no cover - optional layer
        return base

    try:
        channel = resolve_channel_name(item, name, aliases or {})
    except Exception:  # pragma: no cover - never break grouping over a name
        return base
    if not channel.resolved or not channel.name:
        return base

    # The broadcaster sits at the end of the title, and so does whatever trails
    # it - a region or language marker like "FOX DEPORTES" or "SporTV BR". Both
    # belong to the channel, not to the fixture, so the key is truncated at the
    # broadcaster rather than having its name spliced out: splicing left "Al
    # Nassr Vs Al Fateh DEPORTES" and still produced a second card.
    tokens = base.split("-") if base else []
    channel_tokens = [part for part in channel.normalized.split("-") if part]
    if not tokens or not channel_tokens:
        return base

    head = channel_tokens[0]
    cut = -1
    for index in range(len(tokens) - 1, -1, -1):
        if tokens[index] == head:
            cut = index
            break
    if cut <= 0:
        return base

    kept = tokens[:cut]
    # Refuse a truncation that would leave something that is no longer a fixture:
    # a broadcaster word can legitimately appear inside a team name, and cutting
    # there would merge unrelated matches.
    if "vs" not in kept and len(kept) < 3:
        return base
    if "vs" in kept and kept.index("vs") >= len(kept) - 1:
        return base

    candidate = "-".join(kept)
    return candidate if len(candidate) >= 4 else base


def fixture_display_name(item, aliases=None) -> str:
    """Section 5. The fixture's own title, without the broadcaster on the end.

    A card titled "Al Nassr Vs Al Fateh SporTV BR" names one channel out of three
    in its own headline, which is misleading once the channels sit underneath it.
    The broadcaster is trimmed off for display; if trimming would leave something
    that no longer reads like a fixture, the original title is kept.
    """
    name = str(item.get("name") or "").strip()
    if not name:
        return name
    try:
        from scanner.channel_resolver import resolve_channel_name
    except Exception:  # pragma: no cover - optional layer
        return name
    try:
        channel = resolve_channel_name(item, name, aliases or {})
    except Exception:  # pragma: no cover
        return name
    if not channel.resolved or not channel.name:
        return name

    trimmed = re.sub(
        r"[\s|:,-]*" + re.escape(channel.name) + r"[\s|:,-]*.*$",
        "",
        name,
        flags=re.IGNORECASE,
    )
    trimmed = " ".join(trimmed.split()).strip(" -|:,")
    if len(trimmed) < 6 or not _VERSUS_WORD.search(trimmed):
        return name
    return trimmed


def _channel_layer():
    """The Fixture -> Channels[] -> Streams[] builder, imported lazily.

    Lazy because scanner.channel_groups imports the channel resolver, which
    reaches back into this module for lineage - and because a merge must still
    work if the channel layer is unavailable for any reason.
    """
    try:
        from scanner.channel_groups import (
            DEFAULT_MAX_CHANNELS_PER_EVENT,
            DEFAULT_MAX_STREAMS_PER_CHANNEL,
            build_event_channels,
            default_channel_id,
            stream_variant_identity,
            summarize_channels,
        )
        from scanner.channel_resolver import load_alias_map
    except Exception:  # pragma: no cover - never break a merge over channels
        return None
    return {
        "build": build_event_channels,
        "default_id": default_channel_id,
        "variant": stream_variant_identity,
        "summary": summarize_channels,
        "aliases": load_alias_map,
        "max_streams_default": DEFAULT_MAX_STREAMS_PER_CHANNEL,
        "max_channels_default": DEFAULT_MAX_CHANNELS_PER_EVENT,
    }


def event_sport(item: Dict[str, Any]) -> str:
    """Canonical sport for one event candidate."""
    declared = str(item.get("source_category") or item.get("group_title") or item.get("category") or "").strip()
    if declared and not re.fullmatch(r"live|sports?|event|events?|other|general|channel|today\s*match|upcoming", declared, re.IGNORECASE):
        for name, pattern in _SPORT_PATTERNS:
            if pattern.search(declared):
                return name
    haystack = " ".join(
        str(item.get(field) or "") for field in (
            "source_category", "group_title", "category", "competition",
            "tournament", "league", "channel_name", "broadcaster", "name", "title"
        )
    )
    for name, pattern in _SPORT_PATTERNS:
        if pattern.search(haystack):
            return name
    return "other"


def sport_sort_index(sport: str) -> int:
    """Requirement 11 ordering: cricket, football, then the rest."""
    value = str(sport or "other").strip().lower()
    if value in SPORT_ORDER:
        return SPORT_ORDER.index(value)
    return len(SPORT_ORDER)


# Requirement 1, corrected. Two sources rarely agree on a kickoff to the minute,
# so identity comparison tolerates a difference rather than demanding equality.
# A fixed-width bucket cannot express that: two sources four minutes apart land
# in different buckets whenever the boundary falls between them, and the same
# match publishes twice. The tolerance is compared directly instead, so the
# allowance is the same wherever on the clock the kickoff happens to sit.
KICKOFF_TOLERANCE_MINUTES = 90


def _kickoff_epoch(item: Dict[str, Any]) -> Optional[int]:
    """Requirement 1. The fixture's kickoff as a UTC epoch, or None when the
    source did not state one - a missing kickoff is a wildcard, not a value."""
    raw = str(item.get("start_at") or item.get("start_time") or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


def kickoffs_within_tolerance(
    left: Optional[int],
    right: Optional[int],
    tolerance_minutes: int = KICKOFF_TOLERANCE_MINUTES,
) -> bool:
    """True when two kickoffs are close enough to be the same fixture.

    A missing kickoff on either side cannot contradict the other one, so it
    compares as compatible.
    """
    if left is None or right is None:
        return True
    return abs(int(left) - int(right)) <= max(0, int(tolerance_minutes)) * 60


#: Words that describe *which round* of a competition, not which competition.
#: A provider that has no series field routinely puts the round there instead:
#: `Sri Lanka vs India` arrived with competition "1st Test" while the catalogue
#: entry for the same match carried "India Tour of Sri Lanka 2026". Comparing
#: those two as competitions made them contradict, so one live Test published as
#: two cards. A round descriptor is therefore reduced to nothing, which makes it
#: behave like the missing field it stands in for.
_ROUND_ONLY_COMPETITION = re.compile(
    r"(?i)\b(?:\d{1,3}(?:st|nd|rd|th)?|first|second|third|fourth|fifth|"
    rf"only|final|finals|{_FIXTURE_ORDINAL_KINDS}|day|session|innings|inning|"
    r"stage|group|matchday|week|game|fixture|pool|series)\b"
)


#: Shortest span that counts as a fixture running over more than one day.
MULTI_DAY_WINDOW_SECONDS = 24 * 3600


def _time_epoch(value: Any) -> Optional[int]:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


def authoritative_fixture_window(
    item: Dict[str, Any]
) -> Optional[Tuple[int, int]]:
    """The catalogue's own [start, end] for a fixture that runs over days.

    A five-day Test is relayed once per day, so day 3 arrives with a kickoff two
    days after day 1's. Kickoff tolerance is 90 minutes, so comparing the two
    kickoffs said "different fixtures" and the same Test published as several
    cards. What actually settles it is the fixture window the catalogue states,
    which is exactly why `config/event-fixtures.json` carries an explicit `end`.

    Only the catalogue is trusted for this. A provider estimate of the end time is
    a guess - widening identity on a guess would merge two real matches - so a
    window is returned only when the fixture id names a catalogue series or the
    time was resolved against one, and only when it really spans a day or more.
    """
    start = _time_epoch(item.get("start_at") or item.get("start_time"))
    end = _time_epoch(item.get("end_time") or item.get("end_at"))
    if start is None or end is None or end - start < MULTI_DAY_WINDOW_SECONDS:
        return None
    fixture_id = str(item.get("fixture_id") or "").strip()
    catalogue_fixture = bool(fixture_id) and not fixture_id.startswith("provider:")
    verification = str(item.get("time_verification") or "").strip().casefold()
    if not catalogue_fixture and verification not in {"official_catalogue", "corrected"}:
        return None
    return (start, end)


def kickoffs_compatible(
    left: Dict[str, Any],
    right: Dict[str, Any],
    tolerance_minutes: int = KICKOFF_TOLERANCE_MINUTES,
) -> bool:
    """Whether two candidates' start times can belong to the same fixture.

    Either the two kickoffs are within tolerance of each other, or one side is a
    catalogue fixture running over several days and the other side's kickoff falls
    inside that window.
    """
    left_kick = _kickoff_epoch(left)
    right_kick = _kickoff_epoch(right)
    if kickoffs_within_tolerance(left_kick, right_kick, tolerance_minutes):
        return True
    grace = max(0, int(tolerance_minutes)) * 60
    for window, kickoff in (
        (authoritative_fixture_window(left), right_kick),
        (authoritative_fixture_window(right), left_kick),
    ):
        if window is None or kickoff is None:
            continue
        if window[0] - grace <= kickoff <= window[1] + grace:
            return True
    return False


def _normalized_competition(item: Dict[str, Any]) -> str:
    text = str(item.get("competition") or "").strip().casefold()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = re.sub(r"\b(?:20\d{2}|19\d{2})\b", " ", text)
    text = " ".join(text.split())
    if not text:
        return ""
    # If every word in the field is a round descriptor then the provider stated a
    # round, not a competition, and there is nothing here to contradict with.
    # What a round word leaves behind is often its designator - "Group A" leaves
    # "a", "Round II" leaves "ii" - and a bare letter or roman numeral is part of
    # the round, not the name of a competition.
    remainder = [
        word for word in _ROUND_ONLY_COMPETITION.sub(" ", text).split()
        if not re.fullmatch(r"[a-z]|[ivx]{1,4}|\d+", word)
    ]
    if not remainder:
        return ""
    return text


def canonical_event_identity(
    item: Dict[str, Any]
) -> Tuple[str, str, str, Optional[int]]:
    """Requirement 1: sport + normalized participants/round + competition +
    kickoff time. Each part is returned separately so a missing part can act
    as a wildcard when groups are reconciled, instead of splitting one real
    fixture into two cards just because one source omitted a field.

    The fourth part is the exact kickoff epoch (or None). It is compared with a
    tolerance rather than for equality - see kickoffs_within_tolerance.
    """
    return (
        event_sport(item),
        normalize_event_key(item.get("name", "")),
        _normalized_competition(item),
        _kickoff_epoch(item),
    )


def participant_fold_key(
    item: Dict[str, Any], aliases: Optional[Dict[str, str]] = None
) -> str:
    """Sections 1/3/5. The two participants, in no particular order.

    normalize_event_key preserves the order and the round wording a source
    happened to use, so one live match arrived as three cards:

        "Sri Lanka vs India 1st Test"   (fixture feed, round spelled out)
        "Sri Lanka vs India"            (playlist, no round)
        "India vs Sri Lanka Willow"     (playlist, sides swapped, broadcaster)

    Each held streams the other two did not, so the broadcasters that should have
    been channels of one event were split across three cards instead. This key
    exists to recognise that case and nothing more: the broadcaster is removed,
    the round descriptor is removed, the two sides are sorted.

    It is deliberately not the identity key. It is offered to the reconciler as a
    weaker second opinion, and the sport, competition and kickoff-tolerance
    checks still have to agree before anything folds - which is what keeps the
    same two teams meeting on two dates as two fixtures.
    """
    try:
        from scanner.schedule_resolver import team_pair_key, _gender
    except Exception:  # pragma: no cover - optional layer
        return ""
    name = ""
    try:
        name = fixture_display_name(item, aliases)
    except Exception:  # pragma: no cover - never break grouping over a name
        name = ""
    raw_name = str(item.get("name") or "")
    key = team_pair_key(name or raw_name)
    if "|" not in key:
        return ""
    left, right = (part.strip() for part in key.split("|", 1))
    # "Cpl T20 Vs Cpl T20" is a tournament placeholder wearing a fixture's
    # clothes, not a match between two sides.
    if not left or not right or left == right:
        return ""
    if len(left) < 3 or len(right) < 3:
        return ""
    # "Trent Rockets Women vs Oval Women" and "Trent Rockets vs Oval" are two
    # different fixtures on the same day. The participants-only key cannot see
    # the difference because "women" is one of the words it removes, so the
    # gender is carried in the key explicitly and a neutral title never folds
    # into a gendered one.
    return "|".join(sorted((left, right))) + "#" + _gender(raw_name or name)


def event_id_without_broadcaster(
    card_id: str, channels: List[Dict[str, Any]]
) -> str:
    """Sections 5/10. Take the broadcaster back out of the event id.

    The card id comes from whichever candidate ranked highest, and a playlist
    puts the broadcaster in the id as well as the title. So one fixture served by
    three channels published as

        id                 al-nassr-vs-al-fateh-sportv-br
        channels[].id      al-nassr-vs-al-fateh-sportv-br--fancode

    which reads as though FANCODE were a sub-feed of SporTV BR, and moves the
    whole event's id whenever the top-ranked feed changes. Cutting the resolved
    broadcaster off leaves the fixture, which is what both the event id and the
    channel namespace are supposed to be.

    Returns "" when there is nothing safe to cut - the caller then keeps the id
    it already had.
    """
    segments = [part for part in str(card_id or "").split("-") if part]
    if len(segments) < 3:
        return ""
    heads = {
        normalized.split("-")[0]
        for channel in channels or []
        for normalized in [str(channel.get("normalized_name") or "")]
        if normalized
    }
    heads.discard("")
    if not heads:
        return ""
    cut = -1
    for index in range(len(segments) - 1, 0, -1):
        if segments[index] in heads:
            cut = index
            break
    if cut <= 0:
        return ""
    kept = segments[:cut]
    # The same conservatism as the identity key: only accept a result that still
    # reads like a fixture, so a broadcaster-shaped team word cannot gut an id.
    if "vs" not in kept and len(kept) < 3:
        return ""
    return "-".join(kept)


def _competitions_compatible(
    left: str,
    right: str,
    left_kickoff: Optional[int] = None,
    right_kickoff: Optional[int] = None,
) -> bool:
    """Whether two competition names can describe one fixture.

    String equality split real fixtures in two. Measured on the eleven feeds at
    2026-08-20T16:40, all four duplicated Upcoming cards had the same
    participants and the same kickoff to the second, and differed only here:

        "caribbean premier league"  vs "caribbean premier league 16th match"
        "caribbean premier league"  vs "caribbean premier league 17th match"
        "icc world test championship"        vs "india tour of sri lanka"
        "australia vs bangladesh test series" vs "bangladesh tour of australia"

    The first two are one name with the round appended, so a whole-word prefix
    counts as the same competition. The last two are two different true
    descriptions of one tour, and no string work relates them - but two sides
    that share participants and share a kickoff to the second cannot be playing
    two different competitions at once, so the kickoff settles those.

    A kickoff merely within tolerance is deliberately not enough: that is the
    double-header case, where two legs really are two fixtures.
    """
    blank = {"", "other"}
    if left in blank or right in blank:
        return True
    if left == right:
        return True
    shorter, longer = sorted((left, right), key=len)
    if longer.startswith(shorter) and longer[len(shorter):len(shorter) + 1] in ("", " "):
        return True
    return (
        left_kickoff is not None
        and right_kickoff is not None
        and left_kickoff == right_kickoff
    )


def _identity_compatible(
    left: Tuple[str, str, str, Optional[int]],
    right: Tuple[str, str, str, Optional[int]],
    kickoff_tolerance_minutes: int = KICKOFF_TOLERANCE_MINUTES,
    left_fold: str = "",
    right_fold: str = "",
    left_window: Optional[Tuple[int, int]] = None,
    right_window: Optional[Tuple[int, int]] = None,
) -> bool:
    """Same participants/round, a compatible sport and competition, and two
    kickoffs close enough to be the same fixture.

    The windows are optional and only ever *widen* the kickoff check, for a
    catalogue fixture that runs over several days - see kickoffs_compatible.
    Passing neither reproduces the plain kickoff-tolerance behaviour exactly."""
    if not left[1] or left[1] != right[1]:
        # Same two participants, written differently. Everything below still has
        # to pass, so this widens what counts as the same name and relaxes
        # nothing else.
        if not left_fold or left_fold != right_fold:
            return False
    # "other" means the sport could not be determined, so it must behave like a
    # missing field rather than a value that contradicts a known sport.
    blank = {"", "other"}
    if left[0] not in blank and right[0] not in blank and left[0] != right[0]:
        return False
    # Competition is compared as a name rather than as a string - see
    # _competitions_compatible for the four fixtures that needed it.
    if not _competitions_compatible(left[2], right[2], left[3], right[3]):
        return False
    if kickoffs_within_tolerance(left[3], right[3], kickoff_tolerance_minutes):
        return True
    grace = max(0, int(kickoff_tolerance_minutes)) * 60
    for window, kickoff in ((left_window, right[3]), (right_window, left[3])):
        if window is None or kickoff is None:
            continue
        if window[0] - grace <= kickoff <= window[1] + grace:
            return True
    return False


def same_real_fixture(
    left: Dict[str, Any],
    right: Dict[str, Any],
    aliases: Optional[Dict[str, str]] = None,
) -> bool:
    """Whether two cards are the same real fixture, by the group loop's own rule.

    Live protection carries a missed event forward after the merge has finished,
    so a card that arrives that way has never been through the grouping above and
    can duplicate a card this scan already published - "India vs Sri Lanka
    Willow" beside "Sri Lanka vs India 1st Test". The reconciler needs exactly the
    decision the group loop makes, so it asks the same question through the same
    helpers rather than forming a second opinion that could drift away from this
    one. Sport, competition and kickoff tolerance all still have to agree.
    """
    if not isinstance(left, dict) or not isinstance(right, dict):
        return False
    return _identity_compatible(
        canonical_event_identity(left),
        canonical_event_identity(right),
        left_fold=participant_fold_key(left, aliases),
        right_fold=participant_fold_key(right, aliases),
        left_window=authoritative_fixture_window(left),
        right_window=authoritative_fixture_window(right),
    )


def _is_t_sports(channel: Dict[str, Any]) -> bool:
    name = str(channel.get("name", "")).lower()
    name_clean = re.sub(
        r"\b(?:live|official|4k|2k|uhd|fhd|full\s*hd|hd|sd|1080p|720p)\b",
        " ",
        name,
    )
    name_clean = re.sub(r"[^\w\s]", " ", name_clean)
    normalized = " ".join(name_clean.split()).strip()
    return normalized in {"t sports", "tsports"}


def pin_t_sports_first(channels: List[Dict[str, Any]], category: str = "Sports") -> List[Dict[str, Any]]:
    if category != "Sports" or not channels:
        return channels

    tsports_idx = -1
    for idx, item in enumerate(channels):
        if _is_t_sports(item):
            tsports_idx = idx
            break

    if tsports_idx > 0:
        tsports_card = channels.pop(tsports_idx)
        channels.insert(0, tsports_card)

    return channels


def _normalize_priority_name(value: Any) -> str:
    text = str(value or "").casefold()
    text = re.sub(
        r"\b(?:official|live|channel|4k|2k|uhd|fhd|full\s*hd|fullhd|"
        r"hd|sd|2160p|1440p|1080p|720p|576p|480p|360p)\b",
        " ",
        text,
    )
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _is_channel_only_event(item: Dict[str, Any]) -> bool:
    """An event-pipeline entry that names a channel and no fixture.

    Either the schedule resolver already marked it (`today_source_channel`, set
    by _today_source_channel_fallback), or it simply has no fixture shape and no
    kickoff to build one from. Both are the same thing for grouping: there is no
    match here, only a broadcaster.
    """
    if not isinstance(item, dict):
        return False
    if item.get("today_source_channel") is True:
        return True
    status = str(item.get("schedule_status") or item.get("status") or "").strip().upper()
    if status != "CHANNEL_LIVE":
        return False
    name = str(item.get("name") or "")
    return not _VERSUS_WORD.search(name)


def _channel_identity_key(item: Dict[str, Any]) -> str:
    """Return one card identity for a canonical channel brand.

    Alias normalization has already selected the canonical display name. Raw
    source IDs are provenance, not separate channel identities.
    """
    normalized = _normalize_priority_name(item.get("name"))
    if normalized:
        return normalized
    fallback_id = str(item.get("id") or item.get("tvg_id") or "").strip().casefold()
    return fallback_id or (
        f"{str(item.get('source_id') or 'unknown').casefold()}:"
        f"{item.get('stream_index', item.get('source_index', 0))}"
    )


def _configured_priority_index(
    card: Dict[str, Any],
    priority_entries: List[Dict[str, Any]],
) -> int:
    normalized_name = _normalize_priority_name(card.get("name"))

    for index, entry in enumerate(priority_entries):
        if not isinstance(entry, dict):
            continue
        aliases: List[str] = []
        canonical = str(entry.get("canonical_name") or "").strip()
        if canonical:
            aliases.append(canonical)
        raw_aliases = entry.get("aliases")
        if isinstance(raw_aliases, list):
            aliases.extend(str(alias) for alias in raw_aliases)

        normalized_aliases = {
            _normalize_priority_name(alias)
            for alias in aliases
            if _normalize_priority_name(alias)
        }
        if normalized_name in normalized_aliases:
            return index

    return len(priority_entries)


def pin_configured_channels_first(
    cards: List[Dict[str, Any]],
    category: str,
    pinned_config: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Pin configured channels inside one category without fake cards."""
    raw_entries = pinned_config.get(category)
    if not isinstance(raw_entries, list) or not raw_entries or not cards:
        return cards

    indexed_cards = list(enumerate(cards))
    indexed_cards.sort(
        key=lambda pair: (
            _configured_priority_index(pair[1], raw_entries),
            pair[0],
        )
    )
    return [card for _, card in indexed_cards]


def _verification_tier_score(stream: Dict[str, Any]) -> int:
    """Return a strict confidence tier; higher is better."""
    status = str(stream.get("verification_status") or "").strip().lower()
    confirmed = (
        stream.get("verified") is True
        or stream.get("is_valid") is True
    )
    publish_allowed = stream.get("publish_allowed") is True

    if status in {
        "failed",
        "failed_bd",
        "rejected_low_quality",
        "quarantine",
    }:
        return 0

    if status in {"verified_global", "verified_bd", "verified"}:
        return 6 if confirmed else 0

    if status == "verified_proxy":
        return 5 if confirmed else 0

    if confirmed and not status:
        return 6

    if status == "stale_last_good" and publish_allowed:
        return 4

    if status in {"geo_pending", "bd_protected_pending"} and publish_allowed:
        return 3

    if status == "retryable_pending" and publish_allowed:
        return 2

    if status == "host_deferred" and publish_allowed:
        return 1

    return 0


def _stream_quality_score(
    stream: Dict[str, Any],
) -> Tuple[int, int, int, int, int, int, int, int]:
    """
    Ranking score, higher is better:
    1. Verification Tier Score (Global > Proxy > Last-Good > Protected Pending)
    2. Manual-source flag
    3. Source priority
    4. Resolution height
    5. Lower response time
    6. Recent success
    7. Stability score
    8. Preserved metadata
    """
    tier_score = _verification_tier_score(stream)

    source_id = str(stream.get("source_id") or "").lower()
    source_pipeline = str(stream.get("source_pipeline") or "").lower()

    is_manual = 1 if (
        source_pipeline == "manual"
        or source_id.startswith("manual-")
        or stream.get("manual_source") is True
    ) else 0

    priority = _safe_int(stream.get("source_priority", 0), 0)

    res_val = (
        stream.get("resolution_height")
        or stream.get("height")
        or stream.get("resolution")
        or 0
    )
    resolution_height = _parse_resolution_height(res_val)

    response_time = _response_time_ms(stream)

    recent_success = 1 if (
        stream.get("recent_success") is True
        or stream.get("last_check_success") is True
    ) else 0

    stability_raw = (
        stream.get("stability_score")
        if stream.get("stability_score") is not None
        else stream.get("success_rate", 0)
    )
    stability_score = int(max(0.0, _safe_float(stability_raw, 0.0)) * 1000)

    has_request_metadata = 1 if (
        stream.get("drm")
        or stream.get("headers")
        or stream.get("header_profile")
        or stream.get("requires_headers")
    ) else 0

    return (
        tier_score,
        _playback_readiness(stream),
        is_manual,
        priority,
        resolution_height,
        -response_time,
        recent_success,
        stability_score,
        _direct_playback_score(stream),
        has_request_metadata,
    )


def _playback_readiness(stream: Dict[str, Any]) -> int:
    """Guide 29, points 2, 5 and 6: is everything needed to play it present?

    Two streams can both answer HTTP 200 and still differ: one carries a live
    token, a complete DRM block and the headers its origin insists on, the
    other is missing a licence URL or its token has already expired. Reading
    what verification already recorded costs nothing, so this never adds a
    request to a scan.
    """
    penalties = 0

    expiry = stream.get("expires_at") or stream.get("token_expires_at")
    if expiry:
        try:
            if 0 < int(expiry) < int(_now_epoch()):
                penalties += 1
        except (TypeError, ValueError):
            pass

    drm = stream.get("drm")
    if isinstance(drm, dict) and drm:
        declared = str(drm.get("type") or drm.get("scheme") or "").strip()
        has_route = any(
            str(drm.get(key) or "").strip()
            for key in ("license_url", "license_server", "server_url", "key", "keys")
        )
        # A DRM block naming a scheme but carrying no licence route cannot play.
        if declared and not has_route and drm.get("protected") is not True:
            penalties += 1

    if stream.get("requires_headers") is True:
        headers = stream.get("headers")
        if not isinstance(headers, dict) or not headers:
            if stream.get("protected_source") is not True:
                penalties += 1

    return 0 if penalties else 1


def _direct_playback_score(stream: Dict[str, Any]) -> int:
    """Guide 29, point 10: prefer direct playback where practical.

    Ranked below quality and speed on purpose - it only settles a tie between
    otherwise equal streams. A proxied route still works; it just spends a
    Worker request per manifest and per segment, so an equal direct stream is
    the better primary.
    """
    if str(stream.get("proxy_mode") or "").strip().lower() == "proxy_only":
        return 0
    if stream.get("protected_source") is True or stream.get("requires_credentials") is True:
        return 0
    return 1 if str(stream.get("url") or "").strip() else 0


def _now_epoch() -> int:
    from time import time

    return int(time())


def _effective_publish_allowed(stream: Dict[str, Any]) -> bool:
    if stream.get("publish_allowed") is not None:
        return stream.get("publish_allowed") is True
    if stream.get("metadata_only") is True:
        return True
    return (
        stream.get("verified") is True
        or stream.get("is_valid") is True
    )



def _channel_lineage(stream: Dict[str, Any]) -> str:
    """Requirement 2. Two entries belong to the same channel when they come
    from the same host and the same stream path, whatever their token, cookie,
    DRM or "Server 2" label happens to be. Those variants are still legitimate
    candidates, but a backup list filled with five of them gives the viewer
    nothing to fall back to when that one channel goes down."""
    url = str(stream.get("url") or "")
    host = _extract_hostname(url)
    try:
        path = urlparse(url).path or ""
    except ValueError:
        path = ""
    path = re.sub(r"/[^/]*\.(?:m3u8|mpd|ts|m4s)$", "/", path, flags=re.IGNORECASE)
    path = re.sub(r"\b(?:\d{3,4}p|hd|sd|fhd|uhd|low|high|backup|server\s*\d+)\b", "", path, flags=re.IGNORECASE)
    path = re.sub(r"[^a-z0-9/]+", "", path.lower())
    source = str(stream.get("source_id") or "").strip().lower()
    return f"{host}|{path}" if host else f"{source}|{path}"


def _apply_lineage_diversity(
    streams: List[Dict[str, Any]], limit: int
) -> List[Dict[str, Any]]:
    """One stream per channel first, then same-channel variants fill what is
    left. Nothing is discarded - only reordered - so a card still keeps every
    backup it earned when no independent alternative exists."""
    if not streams or limit <= 0:
        return []
    first_of_lineage: List[Dict[str, Any]] = []
    rest: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for stream in streams:
        lineage = _channel_lineage(stream)
        if lineage and lineage in seen:
            rest.append(stream)
            continue
        seen.add(lineage)
        first_of_lineage.append(stream)
    return (first_of_lineage + rest)[:limit]


def _apply_host_diversity(streams: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    if not streams or limit <= 0:
        return []

    selected: List[Dict[str, Any]] = []
    seen_hosts: set[str] = set()
    remaining: List[Dict[str, Any]] = []

    for s in streams:
        host = _extract_hostname(s.get("url", ""))
        if host and host not in seen_hosts:
            seen_hosts.add(host)
            selected.append(s)
        else:
            remaining.append(s)

    combined = selected + remaining
    return combined[:limit]


def rank_and_select_streams(
    streams: List[Dict[str, Any]],
    max_total: int = 6,
    max_backups: int = 5,
    prefer_https: bool = True,
    allow_http_fallback: bool = True,
    prefer_different_hosts: bool = True,
    previous_primary_identity: str = "",
    hysteresis_margin: int = 1,
    channel_name: str = "",
    channel_kind: str = "channel",
) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    if not streams:
        return None, []

    max_total = min(_safe_int(max_total, 6, 1), 6)
    max_backups = min(_safe_int(max_backups, 5, 0), 5)

    publishable_candidates = [s for s in streams if _is_publishable_stream(s)]
    if not publishable_candidates:
        return None, []

    playable_candidates = [
        s for s in publishable_candidates if s.get("url") and not s.get("metadata_only")
    ]
    metadata_candidates = [
        s for s in publishable_candidates if s.get("metadata_only") or not s.get("url")
    ]

    if not playable_candidates:
        if metadata_candidates:
            return metadata_candidates[0], []
        return None, []

    # Exact identity includes headers, Cookie/Authorization values, signed
    # query strings and DRM. Same URL with different credentials must survive.
    identity_map: Dict[str, Dict[str, Any]] = {}
    for s in playable_candidates:
        url = str(s.get("url", "")).strip()
        if not url:
            continue
        identity = _stream_identity_key(s)
        if identity not in identity_map:
            identity_map[identity] = s
        else:
            current = identity_map[identity]
            if _stream_quality_score(s) > _stream_quality_score(current):
                identity_map[identity] = _merge_provenance(s, current)
            else:
                identity_map[identity] = _merge_provenance(current, s)

    unique_streams = list(identity_map.values())
    if not unique_streams:
        return None, []

    # Enforce confidence tier first, then HTTPS preference within each tier.
    protocol_candidates: List[Dict[str, Any]] = []
    for stream in unique_streams:
        url_lower = str(stream.get("url", "")).lower()
        if url_lower.startswith("https://"):
            protocol_candidates.append(stream)
        elif url_lower.startswith("http://") and allow_http_fallback:
            protocol_candidates.append(stream)

    selected_streams: List[Dict[str, Any]] = []

    for tier in (6, 5, 4, 3, 2, 1):
        tier_streams = [
            stream
            for stream in protocol_candidates
            if _verification_tier_score(stream) == tier
        ]

        def _within_tier_score(stream: Dict[str, Any]) -> Tuple[int, ...]:
            quality = _stream_quality_score(stream)
            is_https = int(
                str(stream.get("url") or "").lower().startswith("https://")
            )
            protocol_score = is_https if prefer_https else 0
            return (protocol_score, *quality[1:])

        tier_streams.sort(key=_within_tier_score, reverse=True)

        remaining_slots = max_total - len(selected_streams)
        if remaining_slots <= 0:
            break

        if prefer_different_hosts:
            tier_selected = _apply_host_diversity(
                tier_streams,
                remaining_slots,
            )
        else:
            tier_selected = tier_streams[:remaining_slots]

        selected_streams.extend(tier_selected)

    if not selected_streams:
        return None, []

    # Requirement 16. A primary that is still healthy keeps its place. Ranking
    # is allowed to reorder everything behind it, but swapping the primary on
    # every scan because a rival was a few milliseconds faster is what makes a
    # running stream flap. Only a clearly better candidate takes over.
    # A route with two independent 120 s passes leads, ahead of both the ranking
    # and the incumbent hold. Every verification tier the scanner assigns is a
    # network observation; this one is decoded frames, so it outranks them.
    #
    # This exists because the Zee Bangla fix was written into the generated card
    # and the next scan would have rebuilt that card from its sources and erased
    # it - a fix with a shelf life of one scan. The registry sits outside the
    # cards, so a rebuild reads it instead.
    if channel_name:
        try:
            selected_streams, promoted = route_preference.promote_preferred(
                selected_streams, channel_kind, channel_name
            )
            if promoted:
                # Ahead of the hold below: an incumbent that has not passed the
                # acceptance must not keep its place over one that has.
                previous_primary_identity = ""
        except Exception:  # noqa: BLE001 - a preference failure must not break a merge
            pass

    if previous_primary_identity:
        held = next(
            (
                index
                for index, stream in enumerate(selected_streams)
                if _stream_identity_key(stream) == previous_primary_identity
            ),
            -1,
        )
        if held > 0:
            incumbent = selected_streams[held]
            challenger = selected_streams[0]
            if _playback_readiness(incumbent) > 0 and _verification_tier_score(incumbent) >= _verification_tier_score(challenger) - hysteresis_margin:
                selected_streams.insert(0, selected_streams.pop(held))

    primary = dict(selected_streams[0])
    # Requirement 2. Independent channels get the backup slots first; a second
    # variant of the channel already playing only fills a slot nothing else
    # wanted.
    backup_candidates = _apply_lineage_diversity(selected_streams[1:], max_backups)

    selected_identities = {
        _stream_identity_key(stream) for stream in selected_streams
    }
    primary["_standby_candidates"] = [
        stream
        for stream in protocol_candidates
        if _stream_identity_key(stream) not in selected_identities
    ]

    backups: List[Dict[str, Any]] = []
    for index, b_stream in enumerate(backup_candidates, start=1):
        backup_item = {
            "name": f"Backup-{index}",
            "url": b_stream.get("url", ""),
            "headers": b_stream.get("headers", {}),
            "header_profile": str(b_stream.get("header_profile") or ""),
            "proxy_mode": str(b_stream.get("proxy_mode") or "auto"),
            "stream_type": str(b_stream.get("stream_type") or ""),
            "requires_headers": bool(b_stream.get("requires_headers", False)),
            "inherit_manifest_query": bool(
                b_stream.get("inherit_manifest_query", False)
            ),
            "verification_mode": b_stream.get("verification_mode", "local"),
            "verification_status": _verification_label(b_stream),
            "verification_badge": _verification_badge(b_stream),
            "verified": bool(b_stream.get("verified", False)),
            "publish_allowed": _effective_publish_allowed(b_stream),
            "source_id": str(b_stream.get("source_id") or ""),
            "host": _extract_hostname(str(b_stream.get("url") or "")),
        }
        if b_stream.get("drm"):
            backup_item["drm"] = b_stream["drm"]
        if b_stream.get("resolution"):
            backup_item["resolution"] = b_stream["resolution"]

        backups.append(backup_item)

    return primary, backups



def _reconcile_event_groups(
    grouped: Dict[str, List[Dict[str, Any]]],
    aliases: Optional[Dict[str, str]] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """Fold event groups whose canonical identities are compatible."""
    event_keys = [key for key in grouped if key.split(":", 1)[0] in _EVENT_PIPELINES]
    if len(event_keys) < 2:
        return grouped

    identities: Dict[str, Tuple[str, str, str, Optional[int]]] = {}
    folds: Dict[str, str] = {}
    windows: Dict[str, Optional[Tuple[int, int]]] = {}
    for key in event_keys:
        members = grouped[key]
        if not members:
            continue
        identities[key] = canonical_event_identity(members[0])
        # A group's fold key is only used if every member agrees on it, so a
        # group that already mixes participants cannot pull another one in.
        member_folds = {participant_fold_key(member, aliases) for member in members}
        folds[key] = member_folds.pop() if len(member_folds) == 1 else ""
        # The widest catalogue window any member states. A multi-day fixture is
        # one card even though each day's relay starts at a different hour.
        spans = [
            span
            for span in (authoritative_fixture_window(member) for member in members)
            if span is not None
        ]
        windows[key] = (
            (min(span[0] for span in spans), max(span[1] for span in spans))
            if spans
            else None
        )

    # Every candidate group is compared against the group that leads it, never
    # against a group that has already been folded in. Kickoff tolerance is not
    # transitive - three fixtures 80 minutes apart would otherwise chain into
    # one card - so anchoring the comparison on the leader is what keeps a
    # merge decision bounded by the leader's own kickoff.
    merged_into: Dict[str, str] = {}
    for index, key in enumerate(event_keys):
        if key in merged_into or key not in identities:
            continue
        for other in event_keys[index + 1:]:
            if other in merged_into or other not in identities:
                continue
            if key.split(":", 1)[0] != other.split(":", 1)[0]:
                continue
            if _identity_compatible(
                identities[key],
                identities[other],
                left_fold=folds.get(key, ""),
                right_fold=folds.get(other, ""),
                left_window=windows.get(key),
                right_window=windows.get(other),
            ):
                merged_into[other] = key

    if not merged_into:
        return grouped

    for source, target in merged_into.items():
        grouped[target].extend(grouped[source])
        grouped.pop(source, None)
    return grouped


def load_previous_primary_keys(data_root: str | Path = "data") -> Dict[str, str]:
    """Requirement 16. Map each published event to the fingerprint of the
    primary it is already serving, so the next scan can keep it."""
    keys: Dict[str, str] = {}
    root = Path(data_root)
    for name in ("today-match.json", "upcoming.json"):
        payload = _load_json_file(root / name)
        for item in payload.get("items") or []:
            if not isinstance(item, dict):
                continue
            fingerprint = str(item.get("primary_stream_key") or "").strip()
            event_key = normalize_event_key(item.get("name", ""))
            if fingerprint and event_key:
                keys[event_key] = fingerprint
    return keys


def _first_group_logo(
    candidates: List[Dict[str, Any]],
    base_item: Dict[str, Any],
) -> str:
    """The first real artwork any member of the merged group supplies.

    The preferred item is asked first so a deliberate choice still wins; the
    rest are a fallback for the common case where only one relay of a channel
    carries a logo at all.
    """
    for item in [base_item, *(candidates or [])]:
        if not isinstance(item, dict):
            continue
        logo = str(item.get("logo") or "").strip()
        if logo:
            return logo
    return ""


def merge_candidates(
    candidates: List[Dict[str, Any]],
    settings_path: str = "config/settings.json",
    previous_primary_keys: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    settings = _load_json_file(settings_path)
    link_policy = settings.get("link_policy", {})
    if not isinstance(link_policy, dict):
        link_policy = {}

    if not isinstance(candidates, list):
        return []

    max_total = _safe_int(link_policy.get("maximum_total_links", 6), 6, 1)
    max_backups = _safe_int(link_policy.get("maximum_backups", 5), 5, 0)
    movie_max_total = _safe_int(
        link_policy.get("movie_maximum_total_links", 4), 4, 1
    )
    movie_max_backups = _safe_int(
        link_policy.get("movie_maximum_backups", 3), 3, 0
    )
    movie_max_total = min(movie_max_total, 6)
    movie_max_backups = min(movie_max_backups, 5, movie_max_total - 1)
    prefer_https = bool(link_policy.get("prefer_https", True))
    allow_http_fallback = bool(link_policy.get("allow_http_fallback", True))
    prefer_different_hosts = bool(link_policy.get("prefer_different_hosts", True))

    # 1. Check Today Match events with STRONGLY VERIFIED playable streams
    strongly_verified_today_event_keys: set[str] = set()
    _prefilter_layer = _channel_layer()
    _prefilter_aliases: Dict[str, str] = (
        _prefilter_layer["aliases"]() if _prefilter_layer else {}
    )
    for c in candidates:
        if not isinstance(c, dict):
            continue
        if _is_strongly_verified_today_match(c):
            # The same channel-stripped key the grouping uses, or a Today match
            # carrying a broadcaster suffix would not recognise its own Upcoming
            # duplicate.
            key = fixture_identity_key(c, _prefilter_aliases)
            if key:
                strongly_verified_today_event_keys.add(key)

    # 2. Filter out Upcoming duplicates ONLY IF Today Match has a STRONGLY VERIFIED stream
    filtered_candidates: List[Dict[str, Any]] = []
    for c in candidates:
        if not isinstance(c, dict):
            continue

        if c.get("source_pipeline") == "upcoming":
            key = fixture_identity_key(c, _prefilter_aliases)
            if key in strongly_verified_today_event_keys:
                continue
        filtered_candidates.append(c)

    # One alias-map read for the whole merge: grouping and channel building both
    # need it, and it is read from disk.
    _grouping_layer = _channel_layer()
    grouping_aliases: Dict[str, str] = (
        _grouping_layer["aliases"]() if _grouping_layer else {}
    )

    # settings.channel_layer declared these for a while with nothing reading
    # them, so the function defaults were what actually ran and editing the
    # config had no effect. The per-channel cap matters: at 4 a channel that a
    # match carries five times published a primary and three backups and threw
    # the fifth link away, when the rule is that every link of the same channel
    # becomes a backup. 6 matches link_policy.maximum_total_links.
    channel_layer_cfg = settings.get("channel_layer")
    if not isinstance(channel_layer_cfg, dict):
        channel_layer_cfg = {}
    default_max_streams = (
        _grouping_layer["max_streams_default"] if _grouping_layer else 4
    )
    default_max_channels = (
        _grouping_layer["max_channels_default"] if _grouping_layer else 8
    )
    max_streams_per_channel = _safe_int(
        channel_layer_cfg.get("max_streams_per_channel", default_max_streams),
        default_max_streams,
        1,
    )
    max_channels_per_event = _safe_int(
        channel_layer_cfg.get("max_channels_per_event", default_max_channels),
        default_max_channels,
        1,
    )

    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for c in filtered_candidates:
        pipeline = str(c.get("source_pipeline", "tv")).strip().casefold() or "tv"
        raw_id = str(c.get("id") or "").strip()

        if pipeline in ("today_match", "upcoming"):
            # Section 5. The broadcaster is stripped out first, so the same match
            # on Willow, Sony Ten and T Sports is one fixture with three channels
            # rather than three main cards.
            evt_key = fixture_identity_key(c, grouping_aliases)
            # A Today Match source also carries reusable sports channels, whose
            # titles are a broadcaster and not "A vs B" - so fixture_identity_key
            # has nothing to key on and returns "". The fallback below is
            # per-source, so one T Sports feed relayed by three sources became
            # three separate cards instead of one card with a primary and two
            # backups, and only whichever card won the race kept a logo.
            # A channel is grouped by its channel identity, exactly as the TV
            # pipeline already does a few lines below; different channels stay
            # different cards because the key is the channel name.
            if not evt_key and _is_channel_only_event(c):
                evt_key = f"channel:{_channel_identity_key(c)}"
            fallback_key = (
                raw_id
                or str(c.get("tvg_id") or "").strip()
                or f"{c.get('source_id', 'unknown')}:{c.get('stream_index', 0)}"
            )
            # Section 1. Group by the tab the event will land in, not by the feed
            # it arrived from. Routing decides Today vs Upcoming from the schedule
            # status, so grouping on `source_pipeline` split one live fixture into
            # two groups whenever one relay was configured under an "upcoming"
            # feed and another under a "today" feed - and then routed both into
            # Today Match, side by side, as two cards for one match.
            destination = event_destination(c)
            bucket = (
                destination if destination in ("today_match", "upcoming") else pipeline
            )
            group_key = f"{bucket}:{evt_key or fallback_key}"
        elif pipeline in {"movies", "movie", "vod", "film"}:
            group_key = f"movies:{_movie_identity_key(c)}"
        else:
            if (
                str(c.get("category") or "").strip().lower() == "sports"
                and _is_t_sports(c)
            ):
                card_id = "t-sports"
            else:
                card_id = _channel_identity_key(c)
            group_key = f"{pipeline}:{card_id}"

        if group_key not in grouped:
            grouped[group_key] = []
        grouped[group_key].append(c)

    # Requirement 1. The group key above is built from participants and round
    # only, because that is the one part every source supplies. Sport,
    # competition and kickoff window are then reconciled here: two groups fold
    # together when those parts agree or are missing on one side, and stay
    # apart when they genuinely disagree - which is what keeps "England vs
    # Pakistan" on two different dates as two fixtures.
    grouped = _reconcile_event_groups(grouped, grouping_aliases)

    merged_results: List[Dict[str, Any]] = []

    channel_alias_map: Dict[str, str] = grouping_aliases

    for group_key, stream_candidates in grouped.items():
        if not stream_candidates:
            continue

        publishable_candidates = [
            item
            for item in stream_candidates
            if _is_publishable_stream(item)
            and _meets_resolution_contract(item, settings)
        ]

        if not publishable_candidates:
            continue

        base_item = max(
            publishable_candidates,
            key=_stream_quality_score,
        )

        group_pipeline = str(base_item.get("source_pipeline") or "").strip().lower()
        selected_max_total = movie_max_total if group_pipeline == "movies" else max_total
        selected_max_backups = (
            movie_max_backups if group_pipeline == "movies" else max_backups
        )

        remembered_primary = ""
        if previous_primary_keys and group_pipeline in _EVENT_PIPELINES:
            remembered_primary = previous_primary_keys.get(
                normalize_event_key(base_item.get("name", "")), ""
            )

        primary, backups = rank_and_select_streams(
            publishable_candidates,
            max_total=selected_max_total,
            max_backups=selected_max_backups,
            prefer_https=prefer_https,
            allow_http_fallback=allow_http_fallback,
            prefer_different_hosts=prefer_different_hosts,
            previous_primary_identity=remembered_primary,
            channel_name=str(base_item.get("name") or ""),
            channel_kind="channel",
        )

        if not primary:
            continue

        card_url = str(primary.get("url") or "")
        card_headers = primary.get("headers", {})
        if not isinstance(card_headers, dict):
            card_headers = {}

        # Sections 6-10. One fixture, its broadcasters, and each broadcaster's
        # stream variants. Built from the whole publishable pool rather than the
        # primary/backup shortlist, because a channel the event-level ranking did
        # not pick is still a channel the viewer may want to select.
        event_channels: List[Dict[str, Any]] = []
        channel_stats: Dict[str, Any] = {}
        if group_pipeline in _EVENT_PIPELINES:
            layer = _channel_layer()
            if layer is not None:
                try:
                    event_channels, channel_stats = layer["build"](
                        base_item.get("id", "") or group_key,
                        base_item.get("name", ""),
                        publishable_candidates,
                        aliases=channel_alias_map,
                        default_variant_key=layer["variant"](primary),
                        max_streams_per_channel=max_streams_per_channel,
                        max_channels=max_channels_per_event,
                    )
                except Exception as error:  # pragma: no cover - reporting only
                    event_channels, channel_stats = [], {"error": str(error)}

        is_metadata_only = primary.get("metadata_only") is True
        v_mode = str(
            primary.get("verification_mode")
            or ("none" if is_metadata_only else "local")
        )
        v_status = _verification_label(primary)

        display_name = (
            fixture_display_name(base_item, channel_alias_map)
            if group_pipeline in _EVENT_PIPELINES and event_channels
            else base_item.get("name", "")
        )

        # The channel ids are namespaced by this, so the broadcaster has to come
        # out of it first - see event_id_without_broadcaster.
        card_id = str(base_item.get("id", "") or "")
        if group_pipeline in _EVENT_PIPELINES and event_channels:
            trimmed_id = event_id_without_broadcaster(card_id, event_channels)
            if trimmed_id:
                card_id = trimmed_id
                event_channels, channel_stats = layer["build"](
                    card_id,
                    base_item.get("name", ""),
                    publishable_candidates,
                    aliases=channel_alias_map,
                    default_variant_key=layer["variant"](primary),
                    max_streams_per_channel=max_streams_per_channel,
                    max_channels=max_channels_per_event,
                )

        merged_card: Dict[str, Any] = {
            "id": card_id,
            "name": display_name,
            # Requirement 16 reads this back next scan to keep a healthy
            # primary in place; requirement 11 sorts on the sport.
            "primary_stream_key": _stream_identity_key(primary),
            "sport_type": event_sport(base_item) if group_pipeline in _EVENT_PIPELINES else "",
            # Whichever member of the group actually carried artwork. The logo
            # used to be read off base_item alone, so one T Sports feed relayed
            # by three sources kept its logo only when the source that happened
            # to rank first was the one carrying it - and the same card
            # published without a logo on the next scan if the ranking moved.
            "logo": _first_group_logo(publishable_candidates, base_item),
            "category": base_item.get("category", ""),
            "url": card_url,
            "headers": card_headers,
            "header_profile": str(primary.get("header_profile") or ""),
            "proxy_mode": str(primary.get("proxy_mode") or "auto"),
            "stream_type": str(primary.get("stream_type") or ""),
            "requires_headers": bool(primary.get("requires_headers", False)),
            "inherit_manifest_query": bool(
                primary.get("inherit_manifest_query", False)
            ),
            "verification_mode": v_mode,
            "verification_status": v_status,
            "verification_badge": _verification_badge(primary),
            "verified": bool(primary.get("verified", False)),
            "publish_allowed": _effective_publish_allowed(primary),
            "source_pipeline": str(base_item.get("source_pipeline") or ""),
            "original_source_pipeline": str(
                base_item.get("original_source_pipeline") or ""
            ),
            "content_kind": str(base_item.get("content_kind") or ""),
            "routing_reason": str(base_item.get("routing_reason") or ""),
            "source_id": str(primary.get("source_id") or base_item.get("source_id") or ""),
            "metadata_only": is_metadata_only,
            "available_link_count": 1 + len(backups),
            "backups": backups,
        }

        # Section 17/18. channels[] is additive: every field the frontend, the
        # tests and the Worker already read stays exactly where it was, and a
        # card whose broadcaster could not be resolved simply carries no
        # channels[] at all (section 12).
        if event_channels:
            layer = _channel_layer()
            merged_card["channels"] = event_channels
            merged_card["channel_count"] = len(event_channels)
            merged_card["default_channel_id"] = (
                layer["default_id"](event_channels, layer["variant"](primary))
                if layer is not None else str(event_channels[0].get("id") or "")
            )
        if channel_stats:
            merged_card["channel_stats"] = channel_stats

        standby_candidates = primary.get("_standby_candidates")
        if isinstance(standby_candidates, list) and standby_candidates:
            standby: List[Dict[str, Any]] = []
            for index, stream in enumerate(standby_candidates, start=1):
                if not isinstance(stream, dict):
                    continue
                entry: Dict[str, Any] = {
                    "name": f"Standby-{index}",
                    "url": str(stream.get("url") or ""),
                    "headers": stream.get("headers") if isinstance(stream.get("headers"), dict) else {},
                    "drm": stream.get("drm") if isinstance(stream.get("drm"), dict) else {},
                    "header_profile": str(stream.get("header_profile") or ""),
                    "proxy_mode": str(stream.get("proxy_mode") or "auto"),
                    "requires_headers": bool(stream.get("requires_headers", False)),
                    "verification_status": _verification_label(stream),
                    "verified": bool(stream.get("verified", False)),
                    "source_id": str(stream.get("source_id") or ""),
                    "source_provenance": _source_provenance(stream),
                    "host": _extract_hostname(str(stream.get("url") or "")),
                }
                if stream.get("resolution"):
                    entry["resolution"] = stream["resolution"]
                if stream.get("resolution_height"):
                    entry["resolution_height"] = stream["resolution_height"]
                standby.append(entry)
            merged_card["standby_link_count"] = len(standby)
            merged_card["standby"] = standby

        provenance = _source_provenance(primary)
        if provenance:
            merged_card["source_provenance"] = provenance
            merged_card["source_ids"] = [item["source_id"] for item in provenance]

        if primary and primary.get("drm"):
            merged_card["drm"] = primary["drm"]
        if primary and primary.get("resolution"):
            merged_card["resolution"] = primary["resolution"]
        if primary and primary.get("resolution_height"):
            merged_card["resolution_height"] = primary["resolution_height"]
        for field_name in (
            "start_time",
            "start_at",
            "end_time",
            "competition",
            "fixture_id",
            "venue",
            "event_url",
            "status",
            "original_status",
            "schedule_status",
            "schedule_verified",
            "schedule_source_url",
            "source_start_time",
            "source_time_delta_minutes",
            "time_verification",
            "source_category",
            "today_source_channel",
        ):
            if base_item.get(field_name) not in (None, ""):
                merged_card[field_name] = base_item[field_name]

        merged_results.append(merged_card)

    pinned_config = settings.get("pinned_channels")
    if not isinstance(pinned_config, dict):
        pinned_config = {}

    # Reorder only inside each category. Other category/card positions stay
    # unchanged, and missing channels do not create empty/fake cards.
    for category_name in ("Sports", "Indian", "Cartoon"):
        category_indices = [
            index
            for index, card in enumerate(merged_results)
            if str(card.get("category") or "").strip() == category_name
        ]
        if not category_indices:
            continue

        category_cards = [merged_results[index] for index in category_indices]
        ordered_cards = pin_configured_channels_first(
            category_cards,
            category_name,
            pinned_config,
        )
        if category_name == "Sports" and not pinned_config.get("Sports"):
            ordered_cards = pin_t_sports_first(ordered_cards, "Sports")

        for card_index, original_position in enumerate(category_indices):
            merged_results[original_position] = ordered_cards[card_index]

    return merged_results
