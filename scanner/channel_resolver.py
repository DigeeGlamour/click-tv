"""Sections 11, 12 and 34 - what broadcaster is this feed, if we can tell.

A fixture's streams arrive labelled every way a playlist author felt like
labelling them: "Willow Live", "SL vs IND Server 2 HD", "tvg-name=Sony Ten 1",
or nothing useful at all. The channel layer groups streams by broadcaster, so
this module answers one question per stream - which channel is it - and, just as
importantly, admits when it does not know.

The priority order is fixed by section 11:

  1. an explicit channel_name
  2. tvg-name
  3. reliable source/provider metadata
  4. group-title / feed metadata, when it actually looks like a channel
  5. a known alias map
  6. the stream title, cleaned

Only the first four are treated as confident on their own. A name recovered from
a cleaned title is confident only when what survives the cleaning still looks
like a broadcaster rather than the leftovers of a match title, because section 12
is explicit: an unresolved channel means the event card behaves exactly as it
does today and no channel selector is shown. Inventing "Unknown 1" or "Server 3"
as a visible broadcaster is worse than showing nothing.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

ALIAS_FILE = Path("config/channel-aliases.json")

# Confidence levels, ordered. "explicit" and "metadata" are trusted as they
# stand; "alias" is a curated mapping; "derived" came out of a stream title and
# has to survive the extra plausibility test below; "unknown" means no channel.
CONFIDENCE_ORDER = ("unknown", "derived", "alias", "metadata", "explicit")

# Section 11's removal list. Everything here is stream decoration, not a
# broadcaster: quality labels, server numbers, backup markers, the words that
# make a title a match title, and token noise.
_QUALITY = r"(?:4k|2k|8k|uhd|fhd|full\s*hd|hd|sd|hq|lq|low|high|auto)"
_RESOLUTION = r"(?:\d{3,4}\s*p|\d{3,4}x\d{3,4}|\d+\s*(?:kbps|mbps|fps))"
_NOISE_PATTERNS: Tuple[str, ...] = (
    r"\bserver\s*[-_]?\s*\d+\b",
    r"\bs\s*[-_]?\s*\d+\b(?!\w)",
    r"\b(?:link|stream|feed|option|opt|src|source)\s*[-_]?\s*\d+\b",
    r"\bbackup\s*\d*\b",
    r"\bstandby\b",
    r"\bmirror\s*\d*\b",
    r"\balt(?:ernate)?\s*\d*\b",
    r"\blive\b",
    r"\bnow\b",
    r"\bonline\b",
    r"\bfree\b",
    r"\bwatch\b",
    r"\bstreaming\b",
    r"\bmulti\s*audio\b",
    r"\bbangla\b(?=\s*(?:commentary|comm))",
    r"\bcommentary\b",
    r"\b(?:eng|hin|ben|tam|tel|urd|ara)\s*(?:audio|comm(?:entary)?)\b",
    rf"\b{_QUALITY}\b",
    rf"\b{_RESOLUTION}\b",
    r"\btoken\b.*$",
    r"\b(?:exp|expires|expiry|md5|hdnea|auth|sig|signature)\b.*$",
    r"\bproxy\b",
    r"\bvip\b",
    r"\bpremium\b",
    r"\btest\b",
    r"\bnew\b",
    r"\bworking\b",
    r"\bupdated?\b",
)
_NOISE = tuple(re.compile(pattern, re.IGNORECASE) for pattern in _NOISE_PATTERNS)

# "team A vs team B" and friends. A title that is mostly this is a match title,
# never a channel name.
_VERSUS = re.compile(r"\b(?:versus|vs\.?|v\.?)\b", re.IGNORECASE)
_MATCH_SHAPE = re.compile(
    r"^(?P<left>.{2,60}?)\s+(?:versus|vs\.?|v\.?)\s+(?P<right>.{2,60})$",
    re.IGNORECASE,
)

# Round/stage wording that belongs to a fixture, not a broadcaster.
_FIXTURE_WORDS = re.compile(
    r"\b(?:\d+(?:st|nd|rd|th)\s+(?:test|odi|t20|match|leg|round|day|session|innings|quarter|half|set)"
    r"|test|odi|t20i?|match\s*\d+|day\s*\d+|matchday|round\s*\d+|group\s*[a-h]\b"
    r"|semi\s*final|quarter\s*final|final|playoff|qualifier|friendly|friendlies"
    r"|league|liga|serie|bundesliga|eredivisie|ligue|cup|trophy|series|tour|championship"
    r"|open|masters|grand\s*prix|classic|derby|women|men|u\d{2})\b",
    re.IGNORECASE,
)

# A broadcaster almost always carries one of these, or is a known short brand.
_BROADCASTER_HINT = re.compile(
    r"\b(?:tv|sports?|sport|network|channel|espn|sky|bein|dazn|willow|fox|bt|nbc|cbs|abc|"
    r"star|sony|ten|zee|jio|hotstar|astro|supersport|setanta|eurosport|viaplay|movistar|"
    r"canal|rmc|dsport|gtv|btv|maasranga|nagorik|tsports|t\s?sports|ptv|geo|a\s?sports|"
    r"cricbuzz|fancode|arena|amazon|prime|peacock|paramount|itv|bbc|rai|"
    r"mediaset|tnt|usa|tudn|telemundo|univision|band|globo|sportv|osn|ssc|thmanyah|"
    r"shahid|adsports|dubai|alkass|varzish|yas|hd\d|"
    # Broadcasters this project's own playlists actually name.
    r"cricgo|criclife|crichd|cricfree|cricket\s*gateway|fancode|willowtv|toffee|"
    r"rabbitholebd|ariana|tapmad|flowsports|starzplay|jiocinema|sonyliv|"
    r"gazi|rtv|ekattor|jamuna|deepto|duronto)\b",
    re.IGNORECASE,
)

# What survives fixture-word removal but is still not part of a name: an orphaned
# ordinal ("1st" once "Test" has gone), a date, a stray number, a Roman-numeral
# reserve-team suffix, or a one/two-letter initial.
_RESIDUE_PATTERNS = (
    r"\b\d+(?:st|nd|rd|th)\b",
    r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)\w*\b",
    r"\b(?:19|20)\d{2}\b",
    r"\b[ivx]{1,4}\b",
    # Only long numbers: a year, a date, a stream id. A one- or two-digit number
    # is a feed number and section 10 needs it kept - "Sony Sports Ten 1" and
    # "Ten 3" are different feeds, and dropping the digit would merge them into
    # one channel and hide a working alternative from the viewer.
    r"\b\d{3,}\b",
)
_RESIDUE = tuple(re.compile(pattern, re.IGNORECASE) for pattern in _RESIDUE_PATTERNS)

_GROUP_TITLE_CHANNELISH = re.compile(
    r"\b(?:tv|sports?|channel|network|hd|live\s*tv)\b", re.IGNORECASE
)

# Category and section labels. A playlist's group-title is very often one of
# these, and a category is not a broadcaster: publishing "Sports" as a channel
# name would put a feed the source never named in front of the viewer.
# Single words that really are a broadcaster on their own. A one-word candidate
# recovered from a stream title has to be one of these (or a curated alias),
# because a match title reduced to one leftover word - "Arsenal vs Chelsea
# Premier League" leaving "Premier" - looks exactly like a brand otherwise.
_STRONG_BRAND = frozenset("""
willow espn sky bein dazn fox nbc cbs abc star sony zee jio hotstar astro
supersport setanta eurosport viaplay movistar canal rmc dsport gtv btv maasranga
nagorik tsports ptv geo cricbuzz fancode itv bbc rai mediaset tnt tudn telemundo
univision globo sportv osn ssc shahid dubai alkass varzish yas peacock paramount
starzplay thmanyah adsports flowsports willowtv tapmad ariana rabbitholebd toffee
""".split())

# A bare hint word is a label, not a broadcaster: "TV", "Sports", "Channel".
_HINT_ONLY = re.compile(
    r"^(?:tv|sports?|sport|channel|network|feed|hd|live)$", re.IGNORECASE
)

_CATEGORY_ONLY = re.compile(
    r"^(?:"
    r"live|live\s*tv|live\s*events?|live\s*sports?|sports?|sport\s*tv|all\s*sports?|"
    r"events?|matches|match\s*cent(?:er|re)|fixtures|schedule|today|today\s*match(?:es)?|"
    r"upcoming|upcoming\s*match(?:es)?|highlights|replays?|"
    r"cricket|football|soccer|tennis|basketball|baseball|hockey|golf|rugby|volleyball|"
    r"motorsport|racing|esports?|boxing|mma|ufc|wwe|athletics|"
    r"general|entertainment|movies?|series|news|music|kids|cartoon|islamic|religious|"
    r"bangla|indian|international|foreign|others?|misc|uncategor(?:ized|ised)|vip|adult"
    r")$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ChannelName:
    """A resolved broadcaster, or an honest admission that there isn't one."""

    name: str = ""
    normalized: str = ""
    confidence: str = "unknown"
    source_field: str = ""

    @property
    def resolved(self) -> bool:
        """Section 12. Only a resolved name may produce a channel group."""
        return bool(self.name) and self.confidence != "unknown"

    def rank(self) -> int:
        try:
            return CONFIDENCE_ORDER.index(self.confidence)
        except ValueError:
            return 0


def normalize_channel_name(value: Any) -> str:
    """A comparison key: two spellings of one broadcaster collapse to one."""
    text = str(value or "").casefold()
    text = re.sub(r"&", " and ", text)
    for pattern in _NOISE:
        text = pattern.sub(" ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = re.sub(r"\b(?:channel|tv channel)\b", " ", text)
    words = [word for word in text.split() if word]
    # "Sony Ten 1" and "Sony Ten1" are the same feed; "Ten 1" and "Ten 3" are not,
    # so trailing feed numbers are kept.
    return "-".join(words)


def load_alias_map(path: Path | str = ALIAS_FILE) -> Dict[str, str]:
    """config/channel-aliases.json, as {normalized alias: display name}.

    The file is a curated channel catalogue in the repository. Both its keys and
    any "aliases" list it carries are accepted, so an entry can be written
    whichever way reads better.
    """
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}

    aliases: Dict[str, str] = {}

    def register(alias: Any, display: Any) -> None:
        key = normalize_channel_name(alias)
        name = str(display or "").strip()
        if not key or not name:
            return
        # A one- or two-character key, or a bare category word, would match far
        # too much: "Live TV" normalizes to "tv", and mapping that to a brand
        # would rename every category label in the catalogue.
        if len(key) < 3 or _CATEGORY_ONLY.match(key.replace("-", " ")):
            return
        aliases.setdefault(key, name)

    if isinstance(payload, dict):
        # The repository file keeps its aliases under "channel_aliases", plus a
        # curated "pinned_channels" list per category. Both are read, and a bare
        # {name: [aliases]} mapping is accepted too.
        handled = False
        for container in ("channel_aliases", "channels", "aliases", "map"):
            value = payload.get(container)
            if isinstance(value, dict):
                for key, entry in value.items():
                    _register_alias_entry(key, entry, register)
                handled = True
            elif isinstance(value, list):
                for entry in value:
                    if isinstance(entry, dict):
                        display = str(
                            entry.get("canonical_name") or entry.get("name")
                            or entry.get("display") or ""
                        ).strip()
                        register(display, display)
                        for alias in entry.get("aliases") or []:
                            register(alias, display or alias)
                handled = True
        pinned = payload.get("pinned_channels")
        if isinstance(pinned, dict):
            for entries in pinned.values():
                if not isinstance(entries, list):
                    continue
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    display = str(entry.get("canonical_name") or entry.get("name") or "").strip()
                    register(display, display)
                    for alias in entry.get("aliases") or []:
                        register(alias, display or alias)
            handled = True
        if handled:
            return aliases
        for key, entry in payload.items():
            _register_alias_entry(key, entry, register)
        return aliases
    if isinstance(payload, list):
        for entry in payload:
            if isinstance(entry, dict):
                display = str(entry.get("name") or "").strip()
                register(display, display)
                for alias in entry.get("aliases") or []:
                    register(alias, display or alias)
    return aliases


def _register_alias_entry(key: Any, entry: Any, register) -> None:
    if isinstance(entry, str):
        register(key, entry)
        register(entry, entry)
    elif isinstance(entry, list):
        display = str(key).strip()
        register(key, display)
        for alias in entry:
            register(alias, display)
    elif isinstance(entry, dict):
        display = str(entry.get("name") or key or "").strip()
        register(key, display)
        register(display, display)
        for alias in entry.get("aliases") or []:
            register(alias, display)


def _keeps_a_word(word: str) -> bool:
    """Is this word part of a name, or leftover initials?

    A short alphabetic fragment is fixture debris - "Al Adalah vs Al Fayha" used
    to leave "Al Al". A short *number* is the opposite: it is the feed number that
    tells "Sony Sports Ten 1" from "Ten 3", and section 10 needs those to stay two
    channels, so digits are always kept.
    """
    core = re.sub(r"[^A-Za-z0-9]", "", word)
    if not core:
        return False
    if core.isdigit():
        return True
    return len(core) > 2 or core.upper() in _KEPT_SHORT_WORDS


def strip_stream_noise(title: Any, event_name: Any = "") -> str:
    """Section 11's cleaning pass over a stream title.

    Removes the match itself, the versus separator, server/backup markers,
    quality and resolution labels, and token noise. What is left is a candidate
    broadcaster name - which still has to pass looks_like_channel().
    """
    text = str(title or "")
    if "|" in text and "=" not in text and "://" not in text:
        # A pipe in a title separates the fixture from its channel, either way
        # round. Keep whichever side survives cleaning as a plausible channel.
        parts = [part.strip() for part in text.split("|") if part.strip()]
        for part in reversed(parts):
            candidate = strip_stream_noise(part, event_name)
            if candidate and looks_like_channel(candidate, True):
                return candidate
        text = parts[0] if parts else text
    elif "|" in text:
        # Pipe-delimited header blob: only the label side can be a name.
        text = text.split("|", 1)[0]
    text = re.sub(r"https?://\S+", " ", text)

    # The fixture's own participants, so "Sri Lanka vs India Willow" leaves Willow.
    for token in _event_tokens(event_name):
        text = re.sub(rf"\b{re.escape(token)}\b", " ", text, flags=re.IGNORECASE)

    # No "A vs B" special case any more. Removing the fixture's own words
    # above already deletes the participants, so whatever survives is the
    # candidate. The old branch cut everything after the second participant -
    # which is exactly where "1st Test Australia vs Bangladesh Willow" keeps
    # its broadcaster, and it was thrown away every time.
    text = _VERSUS.sub(" ", text)
    text = _FIXTURE_WORDS.sub(" ", text)
    for pattern in _NOISE:
        text = pattern.sub(" ", text)
    text = re.sub(r"[\[\](){}<>_/\\]+", " ", text)
    text = re.sub(r"[-â€“â€”:,.]+", " ", text)
    # Whatever the fixture-word pass left stranded: an orphaned ordinal once
    # "Test" has gone, a date, a stray number, a Roman-numeral reserve-team
    # suffix, or a one/two-letter initial. "Al Adalah vs Al Fayha" used to
    # leave "Al Al" and "1st Test ... Willow" used to leave "1st Willow".
    for pattern in _RESIDUE:
        text = pattern.sub(" ", text)
    text = " ".join(word for word in text.split() if _keeps_a_word(word))
    return " ".join(text.split()).strip()


# Words that trail a broadcaster rather than starting a new idea: a region, a
# language, a feed variant. They are what tells "Fox Deportes" and "Willow Xtra"
# apart from "Guyana Amazon Warriors", where a real word follows the brand.
_TRAILING_MARKER = frozenset("""
deportes espanol espanyol english arabic arabia bangla hindi tamil telugu urdu
br bra es esp nz aus au uk usa us ind in bd pak pk sa mena eu row global intl
xtra extra alt alternate plus prime max premium main second secondary
cricket football soccer tennis golf racing motor rugby
""".split())


def _trailing_broadcaster_span(words: List[str]) -> int:
    """Index of the first word that can still belong to a broadcaster.

    Walks in from the end while every word could be part of a channel name - a
    known brand, a broadcaster hint like "sports" or "tv", a feed number, a
    region or language marker, or an initial. The first ordinary word ends the
    walk, because a broadcaster does not sit in front of one.
    """
    index = len(words)
    while index > 0:
        word = words[index - 1]
        if (
            not word
            or word.isdigit()
            or len(word) <= 3
            or word in _STRONG_BRAND
            or word in _TRAILING_MARKER
            or _BROADCASTER_HINT.fullmatch(word)
        ):
            index -= 1
            continue
        break
    # A title made only of brand-ish words - "Sony Sports Ten 4" - is a channel
    # rather than a fixture, so there are no participants to protect and every
    # word stays. Stripping them left "Sony 4", which would have split one feed
    # from its own variants.
    return index


def _event_tokens(event_name: Any) -> List[str]:
    """Words from the fixture title worth deleting from a channel candidate."""
    text = str(event_name or "").strip()
    if not text:
        return []

    # The canonical key is the participants and the round - never the
    # broadcaster. A raw title carries both, so deleting every word of it leaves
    # nothing, which is how a real channel name got thrown away.
    tokens = set()
    canonical = ""
    for module in ("scanner.merger", "merger"):
        try:
            normalize_event_key = __import__(module, fromlist=["normalize_event_key"]).normalize_event_key
            canonical = normalize_event_key(text)
            break
        except Exception:
            continue

    if canonical:
        words = canonical.split("-")
        exempt = _trailing_broadcaster_span(words)
        for index, word in enumerate(words):
            if len(word) < 2 or word.isdigit():
                continue
            # A participant name that happens to include a broadcaster word must
            # not delete that word: "Ireland CricGo 2" is one source's sloppy
            # title, and removing "CricGo" from it loses the only channel there.
            #
            # But the exemption only holds where a broadcaster can actually be:
            # at the end. "Guyana Amazon Warriors" is a cricket team, and
            # exempting "Amazon" wherever it appeared made it the broadcaster of
            # every Guyana fixture - which truncated the fixture key and
            # published the card as "Antigua and Barbuda Falcons vs Guyana".
            if index >= exempt and (
                word in _STRONG_BRAND or _BROADCASTER_HINT.fullmatch(word)
            ):
                continue
            tokens.add(word)
    else:
        for part in _VERSUS.split(text):
            for word in re.split(r"[^A-Za-z0-9]+", _FIXTURE_WORDS.sub(" ", part)):
                if len(word) >= 2 and word.casefold() not in _STRONG_BRAND:
                    tokens.add(word)

    # Longest first, so "Bangladesh" is removed before "Bang".
    return sorted(tokens, key=len, reverse=True)


def looks_like_channel(
    candidate: Any,
    single_word_needs_brand: bool = False,
    aliases: Optional[Dict[str, str]] = None,
) -> bool:
    """Is this plausibly a broadcaster rather than fixture leftovers?

    Deliberately strict. A false positive puts an invented broadcaster in front
    of the viewer, which section 12 forbids; a false negative just means the
    event keeps the card it has today.

    single_word_needs_brand is set when the candidate was inferred rather than
    stated - from a group-title, or from a cleaned stream title. One leftover
    word from a match title is indistinguishable from a brand, so in that case a
    single word is only accepted when it really is a known broadcaster.
    """
    text = str(candidate or "").strip()
    if len(text) < 2 or len(text) > 48:
        return False
    if _VERSUS.search(text):
        return False
    letters = re.sub(r"[^A-Za-z]", "", text)
    if len(letters) < 2:
        return False
    if re.fullmatch(r"(?:unknown|unnamed|untitled|na|n/a|none|null|other|misc)\s*\d*",
                    text, re.IGNORECASE):
        return False
    # A bare category label is not a broadcaster: a playlist group-title is
    # very often "Sports" or "Cricket", and publishing that as a channel would
    # invent a feed name the source never gave.
    if _CATEGORY_ONLY.match(" ".join(text.split())):
        return False
    words = [word for word in re.split(r"\s+", text) if word]
    if len(words) > 5:
        return False
    if _HINT_ONLY.match(text):
        return False

    # A curated alias is proof on its own.
    if aliases and normalize_channel_name(text) in aliases:
        return True

    # Every word a known brand: "Willow", "Fox Cricket", "DAZN".
    alpha_tokens = [
        token for token in re.split(r"[^a-z0-9]+", text.casefold()) if token
    ]
    word_tokens = [token for token in alpha_tokens if not token.isdigit()]
    if word_tokens and all(token in _STRONG_BRAND for token in word_tokens):
        return True

    if _BROADCASTER_HINT.search(text):
        return True

    if single_word_needs_brand:
        # Inferred from a title. With no brand word and no curated alias there is
        # no way to tell a broadcaster from the debris of a match title - "II
        # NPL", "W W" and "SS II" all passed the old permissive test - so
        # section 12 applies and this event simply gets no channel.
        return False

    # Stated outright by the source, so a plain short name is trusted.
    if len(words) <= 3 and re.fullmatch(r"[A-Za-z][A-Za-z0-9'&.\- ]{1,30}", text):
        return not _FIXTURE_WORDS.search(text)
    return False


# Two-letter fragments that are genuinely part of a broadcaster's name.
_KEPT_SHORT_WORDS = frozenset({"TV", "BT", "BE", "AD", "SS", "DD", "GO", "ON", "10", "11"})

_NOT_A_NAME = frozenset({"true", "false", "none", "null", "nan", "0", "1", "-", "n/a"})


def _first_nonempty(item: Dict[str, Any], fields: Iterable[str]) -> Tuple[str, str]:
    for field in fields:
        raw = item.get(field)
        # A flag field sharing a name with a metadata field would otherwise
        # publish "True" as a broadcaster - which really happened.
        if isinstance(raw, bool) or not isinstance(raw, (str, int, float)):
            continue
        value = str(raw).strip()
        if value and value.casefold() not in _NOT_A_NAME:
            return value, field
    return "", ""


def resolve_channel_name(
    item: Dict[str, Any],
    event_name: Any = "",
    aliases: Optional[Dict[str, str]] = None,
) -> ChannelName:
    """Section 11's priority order, applied to one stream candidate."""
    if not isinstance(item, dict):
        return ChannelName()
    alias_map = aliases if aliases is not None else {}

    def finish(name: str, confidence: str, field: str) -> ChannelName:
        display = " ".join(str(name).split()).strip(" -â€“â€”:|")
        normalized = normalize_channel_name(display)
        if not display or not normalized:
            return ChannelName()
        # A curated alias always supplies the display spelling - but only for a
        # key specific enough to mean one broadcaster.
        aliased = (
            None
            if len(normalized) < 3 or _CATEGORY_ONLY.match(normalized.replace("-", " "))
            else alias_map.get(normalized)
        )
        if aliased:
            display = aliased
            if confidence == "derived":
                confidence = "alias"
        return ChannelName(display, normalized, confidence, field)

    # 1. An explicit channel name.
    value, field = _first_nonempty(item, ("channel_name", "channelName", "channel"))
    if value and looks_like_channel(strip_stream_noise(value, event_name) or value):
        return finish(value, "explicit", field)

    # 2. tvg-name.
    value, field = _first_nonempty(item, ("tvg_name", "tvg-name", "tvgName"))
    if value:
        cleaned = strip_stream_noise(value, event_name)
        if looks_like_channel(cleaned or value):
            return finish(cleaned or value, "explicit", field)

    # 3. Reliable source/provider metadata.
    value, field = _first_nonempty(
        item,
        ("broadcaster", "provider_channel", "channel_title", "network", "feed_name",
         "today_source_channel", "source_channel"),
    )
    if value:
        cleaned = strip_stream_noise(value, event_name)
        if looks_like_channel(cleaned or value):
            return finish(cleaned or value, "metadata", field)

    # 4. group-title, but only when it reads like a channel rather than a
    #    category ("Sports", "Live Events" and friends are not broadcasters).
    value, field = _first_nonempty(item, ("group_title", "group-title", "groupTitle"))
    if value and _GROUP_TITLE_CHANNELISH.search(value):
        cleaned = strip_stream_noise(value, event_name)
        if (
            cleaned
            and looks_like_channel(cleaned, True, alias_map)
            and _BROADCASTER_HINT.search(cleaned)
        ):
            return finish(cleaned, "metadata", field)

    # 5. The alias map, consulted against every label the stream carries.
    for field in ("name", "title", "tvg_id", "tvg-id", "channel_id", "group_title"):
        raw = str(item.get(field) or "").strip()
        if not raw:
            continue
        for probe in (raw, strip_stream_noise(raw, event_name)):
            key = normalize_channel_name(probe)
            if key and key in alias_map:
                return finish(alias_map[key], "alias", field)

    # 6. The cleaned stream title, accepted only if it still looks like a channel.
    raw_title, field = _first_nonempty(item, ("name", "title", "stream_name"))
    if raw_title:
        cleaned = strip_stream_noise(raw_title, event_name)
        if cleaned and looks_like_channel(cleaned, True, alias_map):
            return finish(cleaned, "derived", field)

    # Section 12: nothing reliable. No invented broadcaster.
    return ChannelName()


def resolve_stream_channels(
    streams: Iterable[Dict[str, Any]],
    event_name: Any = "",
    aliases: Optional[Dict[str, str]] = None,
) -> List[Tuple[Dict[str, Any], ChannelName]]:
    """Resolve every stream of one fixture, sharing the alias map.

    A stream whose own labels are useless can still inherit a channel from a
    sibling that shares its lineage - the same host and stream path - because
    those really are the same feed. That is the only inheritance allowed: it
    never invents a name, it only reuses one the source already proved.
    """
    alias_map = aliases if aliases is not None else load_alias_map()
    resolved: List[Tuple[Dict[str, Any], ChannelName]] = [
        (stream, resolve_channel_name(stream, event_name, alias_map))
        for stream in streams
        if isinstance(stream, dict)
    ]

    best_by_lineage: Dict[str, ChannelName] = {}
    for stream, channel in resolved:
        if not channel.resolved:
            continue
        key = _lineage_key(stream)
        current = best_by_lineage.get(key)
        if current is None or channel.rank() > current.rank():
            best_by_lineage[key] = channel

    filled: List[Tuple[Dict[str, Any], ChannelName]] = []
    for stream, channel in resolved:
        if not channel.resolved:
            inherited = best_by_lineage.get(_lineage_key(stream))
            if inherited is not None:
                channel = ChannelName(
                    inherited.name, inherited.normalized, inherited.confidence,
                    "lineage",
                )
        filled.append((stream, channel))
    return filled


def _lineage_key(stream: Dict[str, Any]) -> str:
    """Same host and same stream path: genuinely the same feed."""
    try:
        from scanner.merger import _channel_lineage
    except Exception:  # pragma: no cover - direct module execution
        try:
            from merger import _channel_lineage  # type: ignore
        except Exception:
            return str(stream.get("url") or "")
    return _channel_lineage(stream)
