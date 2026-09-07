"""Which upstream a feed is a view of, and how many witnesses that really is.

`source_ids` counts feeds, not witnesses. Seven of ours are FanCode: on
2026-09-07 they carried 39 distinct match_ids between them, 35 of those
present in all seven, and not one disagreement on a name or a kickoff. That
is one upstream stating one thing seven times, and reading it as seven
independent confirmations is the mistake this module exists to stop.

Every grouping below was measured before it was written:

  FanCode      7 feeds, one upstream. The 35-of-39 overlap above.
  SonyLIV      5 feeds, one upstream. Four of them state no kickoff at all.
  AXS-Bing     2 feeds. The registry's own note records axsports as a strict
               subset of bingstream - same 87 ids, same 208 stream_links.
  Tapmad       2 feeds, one upstream and one id space: srhady's
               `EntityId 15804` is sm's `tapmad-15804`, and 37 of 39 sm rows
               joined a srhady row across 15 aligned snapshots.
  LiveScore    its own upstream, and the first that is fixture-first.
  ESPN         its own upstream. A different id space from LiveScore's -
               ESPN cricket id 1547444 against LiveScore Eid 1862137 for
               different fixtures - so neither one's id may be read as the
               other's.

A feed nobody has classified is its own family rather than a member of
`UNKNOWN`: two unclassified feeds are not evidence of each other.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List

#: Feed id -> the upstream it is a view of.
FAMILY_BY_SOURCE: Dict[str, str] = {
    # FanCode, seven views of one upstream.
    "sm-fancode": "FanCode",
    "sportlive-fancode-backup": "FanCode",
    "drmlive-fancode-mirror": "FanCode",
    "sayanpal-fancode-mirror": "FanCode",
    "vk1817-fancode-mirror": "FanCode",
    "dartv-fancode-mirror": "FanCode",
    "iptvflixbd-fancode-data": "FanCode",
    # SonyLIV, five views of one upstream.
    "srhady-sonyliv-live": "SonyLIV",
    "sportlive-sonyliv-backup": "SonyLIV",
    "kajju-sonyliv-backup": "SonyLIV",
    "drmlive-sonyliv-backup": "SonyLIV",
    "sayanpal-sonyliv-backup": "SonyLIV",
    # One upstream behind both, per the registry's own measurement.
    "srhady-axsports-live": "AXS-Bing",
    "srhady-bingstream": "AXS-Bing",
    # Tapmad. The second id is not configured today; it is named here so that
    # adding it can never quietly become a second witness.
    "srhady-tapmad-bd": "Tapmad",
    "sm-tapmad-channels": "Tapmad",
    # One feed each.
    "srhady-primevideo-sports": "PrimeVideo",
    "srhady-willow-event": "Willow",
    "srhady-crichd-footy-live": "CricHD",
    "sm-sports-data": "SM-Aggregate",
    "0matbank-trysports-cricket-live": "TrySports",
    "0matbank-trysports-football-live": "TrySports",
    # Fixture authorities. Separate upstreams, separate id spaces.
    "livescore": "LiveScore",
    "espn-header": "ESPN",
    # Internal producers, so a report can name them without inventing a feed.
    "official-fixture-catalogue": "Catalogue",
    "streamed-fixtures": "Streamed",
}

#: Families that state fixtures rather than streams. Used only for reporting
#: in this step - nothing routes on it.
AUTHORITY_FAMILIES = frozenset({"LiveScore", "ESPN"})


def family_for(source_id: Any) -> str:
    """The upstream family of one feed id.

    An unclassified id becomes its own family, prefixed so a report can see
    at a glance that nobody has classified it. Folding the unknown together
    would make two strangers corroborate each other.
    """
    text = str(source_id or "").strip()
    if not text:
        return ""
    known = FAMILY_BY_SOURCE.get(text)
    if known:
        return known
    return "unclassified:%s" % text


def families_for(source_ids: Iterable[Any]) -> List[str]:
    """The distinct upstream families behind a card's contributing feeds."""
    seen: List[str] = []
    for source_id in source_ids or ():
        family = family_for(source_id)
        if family and family not in seen:
            seen.append(family)
    return sorted(seen)


def independent_witness_count(source_ids: Iterable[Any]) -> int:
    """How many independent upstreams actually stated this.

    Not `len(source_ids)`. A card carrying all seven FanCode feeds answers 1.
    """
    return len(families_for(source_ids))


def describe(source_ids: Iterable[Any]) -> Dict[str, Any]:
    """Family attribution for one card, for a report to carry verbatim."""
    ids = [str(value).strip() for value in (source_ids or ()) if str(value).strip()]
    families = families_for(ids)
    return {
        "source_ids": ids,
        "source_id_count": len(ids),
        "upstream_families": families,
        "independent_witness_count": len(families),
        "mirrors_collapsed": max(0, len(ids) - len(families)),
    }
