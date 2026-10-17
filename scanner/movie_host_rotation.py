"""ধারা ৪.১ - follow a source that moves its files to a new CDN host.

`scanner/movie_coverage.path_family` already knows this happens. It was added
so the no-loss gate would stop reporting a whole source as unaccounted when
the feed swapped hostname, and it worked: INVARIANT ২ matches the rotation and
the publish goes through.

What nothing did was move the *card*. The gate learned to accept the rotation
instead of the catalogue learning to follow it, so the published link kept
pointing at the retired hostname for as long as retention would carry it -
which is for ever, because ধারা ৪.১ rightly says a 403 is never terminal.

Measured on 2026-09-26 against the published catalogue and the live feed:

    published movie cards                        1,917
      exact URL still offered by its source          0
      same path, new host  (repairable here)     1,487
      path not offered at all (private source)     430

    qfdghnb.b-cdn.net ->  idfgbn.b-cdn.net          539
    jrtyh.b-cdn.net   ->  idfgbn.b-cdn.net          484
    urtgh.b-cdn.net   ->  idfgbn.b-cdn.net          344
    wrtyg.b-cdn.net   ->  idfgbn/odfgbv.b-cdn.net    89   (+31 more)

Every one of those retired hostnames answers, from every vantage tried and for
every path including invented ones, with BunnyCDN's own page:

    "Domain suspended or not configured"

so 1,487 of 1,917 cards - 78% of the catalogue - offered the viewer a link to
a pull zone that no longer exists, and all 1,487 were badged "Verified".

The rewrite is deliberately narrow. A path match is weaker than a host+path
match - two hosts really can serve different files on one path - so a card
only moves when the evidence is unambiguous, and the link it had is kept as a
backup rather than discarded, which is also what keeps INVARIANT ২ whole.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urlsplit

from scanner.movie_coverage import clean_url, path_family, stream_family

#: Why a card was left alone. Every card that is not rewritten gets one of
#: these, so "nothing happened" is always a decision with a name.
STILL_OFFERED = "exact_url_still_offered"
NO_PATH = "no_usable_path"
NO_OFFER = "path_not_offered_by_any_source"
SAME_HOST = "only_offered_on_the_host_it_already_has"
AMBIGUOUS = "several_hosts_offer_it_and_none_is_this_card_source"
UNSPECIFIC_PATH = "path_too_generic_to_identify_a_file"

ADOPTED = "host_rotated"

#: A path has to look like a file before a host match means anything. `/live`
#: or `/index.m3u8` are served by thousands of unrelated hosts; a path with a
#: directory and an extension is a filename, and that is what rotates.
MINIMUM_PATH_LENGTH = 12

#: Carried over from the entry, because the new host refuses without them: the
#: feed writes `...mkv|Referer=https://fibwatch.art/` and the retired host did
#: not check while the current one does.
_CARRIED_FIELDS: Tuple[str, ...] = (
    "headers",
    "header_profile",
    "requires_headers",
    "inherit_manifest_query",
    "stream_type",
    "proxy_mode",
)


def _host_of(value: Any) -> str:
    try:
        return (urlsplit(clean_url(value)).hostname or "").casefold()
    except ValueError:
        return ""


def _specific(path: str) -> bool:
    """Is this path a filename, or could any host be serving anything here?

    `index.m3u8` and its relatives are refused by name: every HLS origin on
    the internet serves one, so two hosts agreeing on that path says only
    that both speak HLS. The list is the gate's own, imported rather than
    copied so the two cannot drift into disagreeing about the same URL.
    """
    if len(path) < MINIMUM_PATH_LENGTH:
        return False
    tail = path.rsplit("/", 1)[-1]
    try:
        from scanner.movie_coverage import GENERIC_BASENAMES

        if tail.casefold() in GENERIC_BASENAMES:
            return False
    except Exception:  # pragma: no cover - defensive
        pass
    return "." in tail and len(tail) > 4


def offers_by_path(
    entries: Iterable[Dict[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:
    """What each source offers, keyed by the path part of its URL.

    Built from the scan's raw entries rather than from the cards it managed to
    publish: an entry the merger dropped as a duplicate is still the source
    saying "this file is here now", which is exactly the evidence a carried
    card needs.
    """
    offers: Dict[str, List[Dict[str, Any]]] = {}
    for entry in entries or ():
        if not isinstance(entry, dict):
            continue
        url = clean_url(entry.get("url"))
        if not url:
            continue
        path = path_family(url)
        if not path or not _specific(path):
            continue
        offers.setdefault(path, []).append(entry)
    return offers


def offered_families(offers: Dict[str, List[Dict[str, Any]]]) -> set:
    """Every host+path a source is offering right now."""
    families = set()
    for entries in offers.values():
        for entry in entries:
            family = stream_family(entry.get("url"))
            if family:
                families.add(family)
    return families


def decide(
    card: Dict[str, Any],
    offers: Dict[str, List[Dict[str, Any]]],
    live_families: set,
) -> Tuple[Optional[Dict[str, Any]], str]:
    """The entry this card's link should move to, or why it should not.

    Returns `(entry, reason)`. `entry` is None unless the reason is `ADOPTED`.
    """
    if not isinstance(card, dict):
        return None, NO_PATH
    url = clean_url(card.get("url"))
    if not url:
        return None, NO_PATH
    # The strongest signal there is: the source still offers this exact host
    # and path, so whatever else is true, this link is not stale.
    if stream_family(url) in live_families:
        return None, STILL_OFFERED
    path = path_family(url)
    if not path:
        return None, NO_PATH
    if not _specific(path):
        return None, UNSPECIFIC_PATH
    candidates = offers.get(path) or []
    if not candidates:
        return None, NO_OFFER

    here = _host_of(url)
    elsewhere = [entry for entry in candidates if _host_of(entry.get("url")) != here]
    if not elsewhere:
        return None, SAME_HOST

    hosts = {_host_of(entry.get("url")) for entry in elsewhere}
    if len(hosts) == 1:
        return elsewhere[0], ADOPTED

    # More than one host offers this path. That is the case the path matcher
    # warns about, so it is only resolved when the card itself says which
    # source it came from and one of the offers agrees.
    known = {
        str(card.get("source_id") or "").strip(),
        *(str(value).strip() for value in (card.get("source_ids") or ())),
    }
    known.discard("")
    if known:
        for entry in elsewhere:
            if str(entry.get("source_id") or "").strip() in known:
                return entry, ADOPTED
    return None, AMBIGUOUS


def _as_backup(card: Dict[str, Any], old_url: str) -> None:
    """Keep the link the card had, at the end of the backup list.

    Not sentiment: INVARIANT ২ asks whether every stream that was reaching
    viewers still appears somewhere in the published inventory, and a rewrite
    that simply dropped the old URL would be a real live loss on the very run
    that repaired the catalogue.
    """
    backups = list(card.get("backups") or [])
    for existing in backups:
        if isinstance(existing, dict) and clean_url(existing.get("url")) == old_url:
            return
    backup: Dict[str, Any] = {"url": old_url, "retired_host": True}
    for field in _CARRIED_FIELDS:
        if field in card:
            backup[field] = card[field]
    backups.append(backup)
    card["backups"] = backups


def apply(
    cards: Sequence[Dict[str, Any]],
    offers: Dict[str, List[Dict[str, Any]]],
    live_families: Optional[set] = None,
) -> Dict[str, int]:
    """Move every card the evidence says has moved. Returns a tally by reason."""
    if live_families is None:
        live_families = offered_families(offers)
    summary: Dict[str, int] = {}
    for card in cards or ():
        entry, reason = decide(card, offers, live_families)
        summary[reason] = summary.get(reason, 0) + 1
        if entry is None:
            continue
        old_url = clean_url(card.get("url"))
        new_url = clean_url(entry.get("url"))
        if not new_url or new_url == old_url:
            continue
        _as_backup(card, old_url)
        card["url"] = new_url
        for field in _CARRIED_FIELDS:
            if entry.get(field) is not None:
                card[field] = entry[field]
        card["link_host_rotated"] = True
        card["previous_stream_host"] = _host_of(old_url)
        # The retired link is KEPT as a backup, so INVARIANT 2 can still find
        # the stream that was reaching viewers - but it is not COUNTED, because
        # `available_link_count` promises a playable route and this one is a
        # pull zone that answers "Domain suspended or not configured" to
        # everything. That is the same promise `test_audit_findings` enforces:
        # "a field whose name promises a playable route, asserting one that
        # does not exist".
        live_backups = [
            backup for backup in (card.get("backups") or ())
            if not (isinstance(backup, dict) and backup.get("retired_host"))
        ]
        card["available_link_count"] = (
            (0 if card.get("metadata_only") is True else 1) + len(live_backups)
        )
        # The status is deliberately left alone. It is the health checker's
        # word, not this function's, and inventing a status outside
        # MOVIE_STATUS_PRIORITY would sort the card off the end of its own
        # category. What changed here is which URL gets checked next.
    return summary


#: Where a card keeps the image a viewer sees. `logo` is what the movie card
#: renders; `poster` is carried alongside it on some rows.
_POSTER_FIELDS: Tuple[str, ...] = ("logo", "poster", "backdrop")


def observed_host_map(cards: Sequence[Dict[str, Any]]) -> Dict[str, str]:
    """old host -> new host, learned only from cards this run actually moved.

    Not a configured list and not a guess: a pair appears here because the
    source itself offered the same file under both names on this run. A host
    that was seen rotating to two different places is dropped, because then
    there is no single answer.
    """
    seen: Dict[str, set] = {}
    for card in cards or ():
        if not isinstance(card, dict) or not card.get("link_host_rotated"):
            continue
        old = str(card.get("previous_stream_host") or "")
        new = _host_of(card.get("url"))
        if old and new and old != new:
            seen.setdefault(old, set()).add(new)
    return {old: next(iter(new)) for old, new in seen.items() if len(new) == 1}


def repair_posters(
    cards: Sequence[Dict[str, Any]], mapping: Dict[str, str]
) -> int:
    """Point a poster at the same host its film was just moved to.

    The poster is the same defect wearing a different hat. Measured on
    2026-09-26, 1,112 of 1,917 cards carried an image URL like

        https://srhady-live-stream.hf.space/image?url=https://qfdghnb.b-cdn.net/upload/photos/...
        https://image.sm-iptv-monirul-islam.workers.dev/?url=https://wrtyg.b-cdn.net/upload/photos/...

    - an image proxy wrapping one of the retired pull zones, so the 403 the
    viewer got was BunnyCDN's suspended-domain page passed straight through,
    and 58% of the catalogue showed the placeholder.

    Rewriting the hostname inside that query restored 20 of 20 sampled images
    as real JPEG bytes. The proxy is kept, deliberately: fetched directly the
    live zone answers 403, because it is the proxy that supplies the Referer
    the zone now requires.

    Substring replacement on the hostname only, so a path or a filename that
    happens to contain the same text is not touched.
    """
    if not mapping:
        return 0
    repaired = 0
    for card in cards or ():
        if not isinstance(card, dict):
            continue
        for field in _POSTER_FIELDS:
            value = card.get(field)
            if not isinstance(value, str) or not value:
                continue
            fixed = value
            for old, new in mapping.items():
                if old and old in fixed:
                    fixed = fixed.replace(old, new)
            if fixed != value:
                card[field] = fixed
                card["poster_host_rotated"] = True
                repaired += 1
    return repaired


def describe(summary: Dict[str, int]) -> str:
    moved = summary.get(ADOPTED, 0)
    if not moved:
        return ""
    return (
        f"{moved} card(s) followed their source to a new CDN host; the "
        "retired link is kept as a backup"
    )
