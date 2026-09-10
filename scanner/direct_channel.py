"""A direct channel is a stream somebody is carrying. It is not a fixture.

Some feeds publish a playable URL, a name and a logo, and nothing else: no
kickoff, no status, no teams as data. Measured on the Tapmad direct feed on
2026-09-10:

    top level   status, name, owner, channels_amount, last_update, response
    rows        2, both group "Cricket"
    fields      id, name, logo, group, url - 2 of 2 on every row
    kickoff     none
    status      none

The audit that opened this question measured 113 rows and six groups
(Football 41, Tennis 29, Cricket 28, Skating 9, MMA 5, Multisports 1). The
feed has changed shape since: the rows moved under `response`, and there are
two of them. So nothing here trusts a historical count, and the sport policy
is applied to whatever arrives rather than to what once did.

**No fixture is invented.** The rows are NAMED like fixtures - "England vs
Pakistan | Pakistan Tour of England 2026" - and that name is a title the feed
chose, not evidence. Without a kickoff there is no fixture to have a
lifecycle: no T-25, no UPCOMING, no end time, no authority match, no archive
identity. `scanner/schedule_resolver._today_source_channel_fallback` already
publishes such a row as `CHANNEL_LIVE` with the clock fields stripped, and
that is the shape this module feeds into. What is added here is the label
`entry_type = "direct_channel"`, so the difference is stated in the data
rather than inferred from an absence.

**The sport policy is explicit.** Today Match publishes cricket and football,
so those are the two sports a direct channel may reach it under. Every other
group is kept, parsed and reported as
`excluded_by_direct_channel_sport_policy` - never deleted from ingestion,
because a row nobody can see is still a row somebody has to be able to count.

**One upstream is one witness.** This feed and `srhady-tapmad-bd` are two
views of Tapmad - srhady's `EntityId 15804` is this feed's `tapmad-15804` -
and `scanner/upstream_family.py` has named `sm-tapmad-channels` as Tapmad
since before it was configured, precisely so that adding it could never
quietly become a second independent witness.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlsplit

#: What a direct-channel row is, said in the data.
ENTRY_TYPE = "direct_channel"

#: The status a direct channel publishes under. An existing state:
#: `_today_source_channel_fallback` writes it, `ROUTE_LIVE_STATUSES` routes it
#: to Today Match, and the site already renders it. No new card exists.
CHANNEL_STATUS = "CHANNEL_LIVE"

#: The sports Today Match publishes, and therefore the only sports a direct
#: channel may reach it under. Fallback only - the live value is
#: config/settings.json -> direct_channel.allowed_sports.
DEFAULT_ALLOWED_SPORTS: Tuple[str, ...] = ("cricket", "football")

#: Why a row was parsed and reported but not published.
EXCLUDED_BY_SPORT = "excluded_by_direct_channel_sport_policy"

#: Group text this feed uses, folded onto the sport vocabulary the rest of the
#: scanner speaks. Anything unrecognised keeps its own lowercased text, which
#: the allowlist then refuses - an unknown sport is not a permitted one.
_SPORT_BY_GROUP = {
    "cricket": "cricket",
    "football": "football",
    "soccer": "football",
}

#: The two identity domains, named so that a comparison between them can be
#: refused rather than merely avoided. A fixture is a scheduled match with
#: participants, a kickoff, an authority and a lifecycle; a direct channel is
#: a stream somebody is carrying, with a name and a logo and nothing else.
#: Both can arrive from the same upstream carrying the same URL, and that is
#: exactly the case this vocabulary exists for.
FIXTURE_DOMAIN = "fixture"
DIRECT_DOMAIN = ENTRY_TYPE

_SLUG = re.compile(r"[^a-z0-9]+")


def _text(value: Any) -> str:
    return str(value or "").strip()


def allowed_sports(settings: Optional[Dict[str, Any]] = None) -> Tuple[str, ...]:
    """The configured allowlist, or the default.

    Read from config rather than written into a condition, because "which
    sports does this surface publish" is one decision and it already has a
    home. An empty or unreadable list falls back rather than allowing
    everything: a policy that fails open is not a policy.
    """
    block = (settings or {}).get("direct_channel")
    if not isinstance(block, dict):
        return DEFAULT_ALLOWED_SPORTS
    configured = block.get("allowed_sports")
    if not isinstance(configured, list):
        return DEFAULT_ALLOWED_SPORTS
    cleaned = tuple(
        _text(entry).lower() for entry in configured if _text(entry))
    return cleaned or DEFAULT_ALLOWED_SPORTS


def sport_of(group: Any) -> str:
    """The sport this row belongs to, from its own group text."""
    text = _text(group).lower()
    if not text:
        return ""
    return _SPORT_BY_GROUP.get(text, text)


def channel_identity(row: Dict[str, Any], source_id: str = "") -> str:
    """A stable identity for one direct channel.

    The feed's own id first - `tapmad-15935` is the same channel tomorrow, and
    it is the id space `srhady-tapmad-bd` shares - then the URL's host and
    path, then the name. Never a `fixture_id`: this is not a fixture, and
    giving it one would let it into the fixture dedupe.
    """
    own = _text(row.get("id"))
    if own:
        return own
    url = _text(row.get("url"))
    if url:
        parts = urlsplit(url)
        stem = "%s%s" % (parts.netloc, parts.path)
        slug = _SLUG.sub("-", stem.lower()).strip("-")
        if slug:
            return slug
    name = _SLUG.sub("-", _text(row.get("name")).lower()).strip("-")
    if name:
        return "%s-%s" % (_text(source_id) or "channel", name)
    return ""


def classify(
    rows: Iterable[Dict[str, Any]],
    *,
    source_id: str = "",
    allowed: Iterable[str] = DEFAULT_ALLOWED_SPORTS,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Split the feed into what may publish and what may only be reported.

    Returns one entry per distinct channel identity, each carrying a primary
    URL and any backups, plus a diagnostics dict that accounts for every row
    that came in - published, excluded, or folded as a duplicate. The counts
    add up to the number of rows, which is the point of returning them.

    Grouping rules, and no others:

      same identity, several URLs   one entry, primary plus backups
      the same URL twice            kept once
      different identities          separate entries, whatever the sport

    A broadcaster is never folded into another broadcaster's backups, because
    identity is the key and two names are two identities.
    """
    permitted = {(_text(sport) or "").lower() for sport in allowed}
    permitted.discard("")

    entries: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    excluded: List[Dict[str, str]] = []
    seen_urls: Dict[str, str] = {}
    skipped: List[Dict[str, str]] = []
    duplicate_urls = 0
    total = 0

    for row in rows or ():
        if not isinstance(row, dict):
            continue
        total += 1
        name = _text(row.get("name"))
        url = _text(row.get("url"))
        identity = channel_identity(row, source_id)
        sport = sport_of(row.get("group"))

        if not name or not url or not identity:
            skipped.append({
                "identity": identity,
                "name": name,
                "reason": "row carries no %s" % (
                    "name" if not name else ("url" if not url else "identity")),
            })
            continue

        if sport not in permitted:
            excluded.append({
                "identity": identity,
                "name": name,
                "group": _text(row.get("group")),
                "sport": sport,
                "reason": EXCLUDED_BY_SPORT,
            })
            continue

        existing = entries.get(identity)
        if existing is None:
            entries[identity] = {
                "entry_type": ENTRY_TYPE,
                "channel_identity": identity,
                "name": name,
                "logo": _text(row.get("logo")),
                "sport": sport,
                "group": _text(row.get("group")),
                "source_id": _text(source_id),
                "url": url,
                "backups": [],
                "headers": row.get("headers") if isinstance(
                    row.get("headers"), dict) else {},
            }
            order.append(identity)
            seen_urls[url] = identity
            continue

        # Same channel again. A URL already on this entry is the same route
        # stated twice; a new one is a backup for the same channel.
        if url == existing["url"] or url in existing["backups"]:
            duplicate_urls += 1
            continue
        if url in seen_urls and seen_urls[url] != identity:
            # The same physical URL under a second identity. Kept once, on the
            # entry that claimed it first, so two names cannot both publish it.
            duplicate_urls += 1
            continue
        existing["backups"].append(url)
        seen_urls[url] = identity

    published = [entries[identity] for identity in order]
    diagnostics = {
        "rows": total,
        "published": len(published),
        "by_sport": _counted(entry["sport"] for entry in published),
        "excluded": len(excluded),
        "excluded_by_sport": _counted(entry["group"] for entry in excluded),
        "excluded_rows": excluded,
        "duplicate_urls_folded": duplicate_urls,
        "backups_attached": sum(len(entry["backups"]) for entry in published),
        "skipped": skipped,
        "allowed_sports": sorted(permitted),
    }
    return published, diagnostics


def _counted(values: Iterable[str]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for value in values:
        key = _text(value) or "(none)"
        counts[key] = counts.get(key, 0) + 1
    return counts


def is_direct_channel(item: Optional[Dict[str, Any]]) -> bool:
    """Whether this row or card belongs to the direct-channel domain.

    Read from `entry_type`, which the source registry declares and the adapter
    writes - never from a source id, a host or a title. Any feed of playable
    channels answers this the same way, and a feed of fixtures never does.
    """
    if not isinstance(item, dict):
        return False
    return _text(item.get("entry_type")).lower() == ENTRY_TYPE


def identity_domain(item: Optional[Dict[str, Any]]) -> str:
    """Which identity domain a row belongs to. Everything else is a fixture."""
    return DIRECT_DOMAIN if is_direct_channel(item) else FIXTURE_DOMAIN


def identity_key(item: Optional[Dict[str, Any]]) -> str:
    """The direct-channel domain's own key for a row or card.

    The channel's identity, never its title. Measured on 2026-09-10 this feed
    published `tapmad-16707` under two different titles inside one afternoon -
    "Rotterdam Dockers vs Glasgow Cosmic" and then "Belfast Wolves vs
    Amsterdam Flames" - for one unchanged URL. A name that changes under a
    stable id is display metadata; the id is the channel.
    """
    if not isinstance(item, dict):
        return ""
    for field in ("channel_identity", "identity"):
        value = _text(item.get(field))
        if value:
            return value
    return channel_identity(item)
