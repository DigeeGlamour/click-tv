"""Per-source include rules, name cleanup and pre-network safety checks.

Three problems that all belong to one source rather than to the project:

  1. A playlist worth ingesting for fourteen channels does not need to be
     ingested for 1,409. jtvplus7.m3u carries 1,409 entries, of which 44 are in
     its Bengali group; verifying the rest costs a scan's budget and publishes
     nothing anyone asked for.

  2. A playlist can carry its author's credit inside the channel name -
     "Star Jalsha @rtxcric", group "Sports By @rtxcric". Measured: all 113
     entries of the Hotstar playlist do. Left alone, every one of them becomes
     a second card for a channel that already exists. Cleaned globally, an
     unrelated source that legitimately contains a matching word would be
     damaged - so the rules live with the source that needs them.

  3. Some entries cannot play and can be known not to play before a single
     request is made: an Akamai cookie whose `exp=` has passed, or a ClearKey
     declaration whose key is a URL rather than a key. Probing those spends
     time to learn what the entry already said.

Everything here is config-driven and per-source. A source that declares no
rules is returned unchanged, so adding this changes nothing for the sixteen
sources already configured.
"""
from __future__ import annotations

import re
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

#: A ClearKey key is two 16-byte hex values separated by a colon. Anything else
#: - most importantly a licence URL - is not something the player can use as a
#: static key, and declaring it as one produces a card that cannot decrypt.
_CLEARKEY = re.compile(r"^[0-9a-fA-F]{32}:[0-9a-fA-F]{32}$")

#: Akamai token cookies and query strings both spell the expiry this way.
_EXPIRY = re.compile(r"(?:^|[~&?])exp=(\d{9,13})(?:[~&]|$)")

#: Words that mark a kids or cartoon service, in the group, the name or the
#: category. Deliberately broader than a list of channel names: the source adds
#: channels, and "Bengali cartoon" is a description rather than three titles.
_KIDS_WORDS = (
    "kids", "cartoon", "toon", "children", "junior", "jr",
    "nick", "sonic", "pogo", "chutti", "yay", "cbeebies", "disney",
)

#: Words that mark the language. Both spellings appear in real group titles.
_BANGLA_WORDS = ("bangla", "bengali", "bangali", "bn")

#: Patterns that failed to compile, by pattern. A rule that cannot compile is a
#: configuration error, and silence is the wrong response: it is
#: indistinguishable from a rule that simply matched nothing.
INVALID_PATTERNS: Dict[str, str] = {}


def _text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _fold(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _text(value).casefold()).strip()


def _words(value: Any) -> List[str]:
    return [w for w in _fold(value).split() if w]


def looks_bangla(item: Dict[str, Any]) -> bool:
    """Whether this entry is a Bangla/Bengali service.

    Reads the group, the name and any declared language rather than one field,
    because the real source spreads it across all three: "Star Jalsha HD" sits
    in group "Bengali", while "SONY YAY Bengali" sits in group "SONY" and
    carries the language in its name.
    """
    haystack = " ".join(_words(item.get("group_title"))) + " " + \
        " ".join(_words(item.get("name"))) + " " + \
        " ".join(_words(item.get("language"))) + " " + \
        " ".join(_words(item.get("tvg_name")))
    return any(word in haystack.split() for word in _BANGLA_WORDS)


def looks_kids(item: Dict[str, Any]) -> bool:
    """Whether this entry is a kids or cartoon service."""
    haystack = (
        " ".join(_words(item.get("group_title"))) + " " +
        " ".join(_words(item.get("name"))) + " " +
        " ".join(_words(item.get("category")))
    ).split()
    return any(word in haystack for word in _KIDS_WORDS)


def is_bangla_kids(item: Dict[str, Any]) -> bool:
    """A Bengali cartoon/kids channel, however it is spelled.

    Not a list of three names. The owner asked for "other Bengali/Bangla
    Kids/Cartoon channels found in the source in future", so this reads the
    language signal and the kids signal independently and requires both -
    Nick Bangla, Sonic Bangla and SONY YAY Bengali all satisfy it today, and a
    channel the source adds tomorrow satisfies it without a code change.
    """
    return looks_bangla(item) and looks_kids(item)


def clearkey_is_usable(drm: Any) -> Tuple[bool, str]:
    """(usable, reason) for a declared ClearKey licence."""
    if not isinstance(drm, dict) or not drm:
        return True, ""
    licence_type = _text(drm.get("license_type") or drm.get("type")).casefold()
    if licence_type not in {"clearkey", "org.w3.clearkey"}:
        return True, ""
    key = _text(
        drm.get("license_key")
        or drm.get("clear_keys")
        or drm.get("clearkey")
        or drm.get("key")
    )
    if not key:
        return False, "declared clearkey with no key"
    if key.lower().startswith(("http://", "https://")):
        # A licence server URL is a different DRM flow. Published as ClearKey
        # it produces a card the player configures with a key it does not have.
        return False, "clearkey key is a remote URL, not a static key"
    for candidate in re.split(r"[,\s]+", key):
        if candidate and not _CLEARKEY.match(candidate):
            return False, f"clearkey key is not 32-hex:32-hex ({candidate[:24]})"
    return True, ""


def token_expiry_seconds_left(item: Dict[str, Any], now: Optional[float] = None) -> Optional[int]:
    """Seconds until this entry's signed token expires, or None if it has none.

    The expiry is not always in the URL. The measured source puts it in a
    Cookie: `__hdnea__=st=...~exp=...~acl=/*~hmac=...`, so both the URL and
    every header value are read.
    """
    reference = time.time() if now is None else now
    haystacks = [str(item.get("url") or "")]
    headers = item.get("headers")
    if isinstance(headers, dict):
        haystacks.extend(str(v) for v in headers.values())
    best: Optional[int] = None
    for text in haystacks:
        for match in _EXPIRY.finditer(text):
            raw = int(match.group(1))
            stamp = raw / 1000.0 if raw > 10_000_000_000 else float(raw)
            left = int(stamp - reference)
            best = left if best is None else min(best, left)
    return best


def token_is_expired(
    item: Dict[str, Any],
    *,
    grace_seconds: int = 0,
    now: Optional[float] = None,
) -> Tuple[bool, str]:
    """(expired, reason). An entry with no token is never expired."""
    left = token_expiry_seconds_left(item, now=now)
    if left is None:
        return False, ""
    if left <= grace_seconds:
        return True, f"signed token expired {abs(left)}s ago"
    return False, ""


def clean_name(name: Any, rules: Any) -> str:
    """Strip this source's own credit suffixes from a name or group title.

    `rules` is the source's `strip_patterns` - regular expressions that belong
    to this source only. There is no global list on purpose: "By @rtxcric" is
    noise in one playlist and could be part of a legitimate name in another.
    """
    text = _text(name)
    if not text or not isinstance(rules, (list, tuple)):
        return text
    for pattern in rules:
        try:
            text = re.sub(str(pattern), " ", text, flags=re.IGNORECASE)
        except re.error as error:
            # A pattern that will not compile did nothing and said nothing, so
            # a JSON-escaping mistake looked exactly like a rule that had no
            # matches - measured: all 113 names went through unchanged because
            # every stored pattern had a doubled backslash. Counted now.
            INVALID_PATTERNS[str(pattern)] = str(error)
            continue
    return " ".join(text.split()).strip(" -|,")


def _include_decision(
    item: Dict[str, Any],
    include: Dict[str, Any],
) -> Tuple[bool, str]:
    groups = [_fold(g) for g in (include.get("groups") or []) if _text(g)]
    names = {_fold(n) for n in (include.get("names") or []) if _text(n)}
    prefixes = [_fold(p) for p in (include.get("name_prefixes") or []) if _text(p)]
    patterns = [str(p) for p in (include.get("name_patterns") or []) if _text(p)]

    item_group = _fold(item.get("group_title"))
    item_name = _fold(item.get("name"))

    if groups and item_group and any(g == item_group for g in groups):
        return True, "group allowed"
    if names and item_name in names:
        return True, "name allowed"
    for prefix in prefixes:
        if prefix and item_name.startswith(prefix):
            return True, f"name prefix '{prefix}'"
    for pattern in patterns:
        try:
            if re.search(pattern, _text(item.get("name")), re.IGNORECASE):
                return True, "name pattern"
        except re.error:
            continue
    if include.get("bangla") and looks_bangla(item):
        return True, "bangla language signal"
    if include.get("bangla_kids") and is_bangla_kids(item):
        return True, "bangla kids/cartoon signal"
    return False, "not in this source's include rules"


def apply_source_rules(
    items: List[Dict[str, Any]],
    source_info: Dict[str, Any],
    *,
    now: Optional[float] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Filter and clean one source's parsed items. Returns (kept, telemetry).

    A source with no rules is returned unchanged and reports it, so this can
    sit in the common path without touching the sources already configured.
    Every drop is counted by reason - nothing is skipped silently.
    """
    rules = source_info.get("source_rules")
    telemetry: Dict[str, Any] = {
        "source_id": str(source_info.get("id") or ""),
        "parsed": len(items or []),
        "kept": len(items or []),
        "dropped": 0,
        "reasons": {},
        "renamed": 0,
        "rules_declared": bool(isinstance(rules, dict) and rules),
    }
    if not isinstance(rules, dict) or not rules:
        return list(items or []), telemetry

    strip_patterns = rules.get("strip_patterns")
    include = rules.get("include") if isinstance(rules.get("include"), dict) else None
    reject_expired = bool(rules.get("reject_expired_tokens"))
    grace = int(rules.get("expiry_grace_seconds") or 0)
    require_usable_clearkey = bool(rules.get("require_usable_clearkey"))
    force_category = _text(rules.get("bangla_kids_category"))

    def count(reason: str) -> None:
        telemetry["reasons"][reason] = telemetry["reasons"].get(reason, 0) + 1
        telemetry["dropped"] += 1

    kept: List[Dict[str, Any]] = []
    for item in items or ():
        if not isinstance(item, dict):
            count("record is not an object")
            continue

        if strip_patterns:
            for field in ("name", "group_title", "tvg_name"):
                original = _text(item.get(field))
                if not original:
                    continue
                cleaned = clean_name(original, strip_patterns)
                if cleaned and cleaned != original:
                    item[field] = cleaned
                    if field == "name":
                        item["source_name_original"] = original
                        telemetry["renamed"] += 1

        if include is not None:
            allowed, why = _include_decision(item, include)
            if not allowed:
                count(why)
                continue
            item["source_include_reason"] = why

        if reject_expired:
            expired, why = token_is_expired(item, grace_seconds=grace, now=now)
            if expired:
                count("expired signed token")
                item["quarantine_reason"] = why
                continue

        if require_usable_clearkey:
            usable, why = clearkey_is_usable(item.get("drm"))
            if not usable:
                count("unusable clearkey")
                item["quarantine_reason"] = why
                continue

        if force_category and is_bangla_kids(item):
            item["force_category"] = force_category
            item["language"] = item.get("language") or "Bangla"
            item["language_hint"] = "Bangla"

        kept.append(item)

    telemetry["kept"] = len(kept)
    return kept, telemetry
