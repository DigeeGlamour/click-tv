"""ধারা ৪.৫ steps 3-5 - find a dead card's film where a source still serves it.

Steps 1, 2 and 6 of ধারা ৪.৫ shipped with ধাপ ৮: the queue checks the backups
a card already has, promotes a live one to ``active_primary``, and then clears
or backs off. Steps 3 to 5 did not, and the implementation report said so
rather than claiming them:

    ৩. আগে থেকেই আনা source snapshot-এ canonical identity দিয়ে প্রার্থী খোঁজা
    ৪. শুধু সেই নতুন URL যাচাই
    ৫. যতটি live পাওয়া যায় ততটি যোগ (সর্বোচ্চ ৫ ব্যাকআপ)

Why they matter here and not only in theory. On 2026-10-17 the public movie
source's whole catalogue - 38,658 rows - sat on ``lrtyuh.b-cdn.net``, which
answers every path, including the zone root, with BunnyCDN's "Domain suspended
or not configured" page. 1,521 of the 1,970 published cards pointed into that
zone or into the four hostnames it had rotated through before it. The same
provider publishes a second playlist whose files are on hosts that do serve,
and 328 of those 1,521 dead cards are films that second playlist carries.

``movie_host_rotation`` already follows a source that renames its host: same
file path, new hostname. This is the other half - the same *film*, offered
under a different path, on a different host, often by a different source
entirely. Path matching cannot see that; canonical identity can.

Three rules, because each is a way the cheap version goes wrong:

1. **The snapshot is handed in, never fetched here.** The plan says "আগে
   থেকেই আনা source snapshot" for a reason: a repair run that fetched per
   entry would be the catalogue scan it exists to avoid. One snapshot, read
   many times.
2. **Only the newly found URLs are probed.** Never the links the card already
   has - step 1 did those, and re-probing them is what made repair cost a scan.
3. **Nothing published is written here.** ধারা ৪.৯ rule 2 and this
   repository's atomic-publish rule both say one writer, and that writer is
   the scan's publisher. What is found is recorded in the link-health ledger
   against the card's content id; the next scan is what puts it on the card.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    from scanner.movie_coverage import content_identity, stream_family
except ImportError:  # pragma: no cover - direct-module execution path
    from movie_coverage import content_identity, stream_family  # type: ignore

#: ধারা ৪.৫ step 5 - "সর্বোচ্চ ৫ ব্যাকআপ".
MAX_BACKUPS = 5

#: How many candidates one entry may probe. A film a snapshot offers in nine
#: resolutions is nine URLs, and probing all of them for one card is the
#: behaviour this subsystem exists to not have. Five live backups is the
#: ceiling, so eight probes is already generous.
MAX_PROBES_PER_ENTRY = 8

#: Written on the ledger record so the scan can tell a link found here from
#: one the feed handed it, and so the next reader of the ledger knows why a
#: URL nobody published is sitting in it.
DISCOVERED_FLAG = "repair_discovered"

#: The published-output validator refuses a movie backup below 720p, and
#: refuses the card with it, unless the link was verified with no declared
#: resolution (`quality_unknown`). So a row that declares less than this is
#: never a candidate, and one that declares nothing is published only after
#: the probe has proved it plays.
MINIMUM_HEIGHT = 720

_DIMENSION = re.compile(r"(?:^|\D)\d{3,4}\s*[xX]\s*(\d{3,4})(?:\D|$)")
_PROGRESSIVE = re.compile(r"(?:^|\D)(\d{3,4})\s*[pP](?:\D|$)")


def declared_height(*texts: Any) -> int:
    """The resolution a row declares, read the way the validator reads it."""
    text = " ".join(str(t or "") for t in texts)
    match = _DIMENSION.search(text)
    if match:
        return int(match.group(1))
    match = _PROGRESSIVE.search(text)
    if match:
        return int(match.group(1))
    if re.search(r"\b(?:4k|uhd)\b", text, re.IGNORECASE):
        return 2160
    if re.search(r"\b(?:fhd|full\s*hd)\b", text, re.IGNORECASE):
        return 1080
    return 0


def _text(value: Any) -> str:
    return str(value or "").strip()


def index_snapshot(
    entries: Iterable[Dict[str, Any]],
) -> Dict[str, List[Dict[str, str]]]:
    """canonical identity -> the URLs a snapshot offers for that film.

    Order is the snapshot's own, which is the source's own, so a source that
    lists its best copy first keeps that preference. Two rows that are the
    same stream family collapse; a row with no identity or no URL is not an
    offer of anything.
    """
    offers: Dict[str, List[Dict[str, str]]] = {}
    seen: Dict[str, set] = {}
    for entry in entries or ():
        if not isinstance(entry, dict):
            continue
        url = _text(entry.get("url"))
        if not url:
            continue
        identity = content_identity(entry)
        if not identity:
            continue
        bucket = offers.setdefault(identity, [])
        taken = seen.setdefault(identity, set())
        family = stream_family(url) or url
        if family in taken:
            continue
        taken.add(family)
        name = _text(entry.get("name") or entry.get("title"))
        height = 0
        try:
            height = int(entry.get("resolution_height") or 0)
        except (TypeError, ValueError):
            height = 0
        height = height or declared_height(entry.get("resolution"), name, url)
        # A declared height below the floor is a known-low copy and is never a
        # candidate. An undeclared one is kept: once the probe proves it plays
        # it is published the way the verifier publishes any verified link of
        # unknown resolution - `quality_unknown`, which the validator accepts.
        if height and height < MINIMUM_HEIGHT:
            continue
        bucket.append({
            "url": url,
            "source_id": _text(entry.get("source_id")),
            "name": name,
            "height": str(height),
        })
    return offers


def _card_families(card: Dict[str, Any]) -> set:
    families = set()
    url = _text(card.get("url"))
    if url:
        families.add(stream_family(url) or url)
    for backup in card.get("backups") or ():
        if not isinstance(backup, dict):
            continue
        backup_url = _text(backup.get("url"))
        if backup_url:
            families.add(stream_family(backup_url) or backup_url)
    return families


def candidates_for(
    card: Dict[str, Any],
    offers: Dict[str, List[Dict[str, str]]],
    *,
    limit: int = MAX_PROBES_PER_ENTRY,
) -> List[Dict[str, str]]:
    """Step 3 - what a snapshot offers for this card that the card lacks.

    Matched on the merger's own canonical identity, so this cannot pair two
    different films that share a word, and it cannot pair a film with its own
    sequel: the identity carries the year.
    """
    if not isinstance(card, dict):
        return []
    identity = content_identity(card)
    if not identity:
        return []
    known = _card_families(card)
    found: List[Dict[str, str]] = []
    for offer in offers.get(identity) or ():
        family = stream_family(offer["url"]) or offer["url"]
        if family in known:
            continue
        known.add(family)
        found.append(offer)
        if len(found) >= max(0, int(limit)):
            break
    return found


def verify(
    candidates: Sequence[Dict[str, str]],
    probe: Callable[[str], bool],
    *,
    limit: int = MAX_BACKUPS,
) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
    """Step 4 - probe only these, and stop as soon as five have answered.

    Returns (live, dead). The probe is injected rather than imported, so this
    is testable without a network and so the caller owns the timeout, the
    headers and the per-host pacing - the repair run and the scan do not agree
    about any of those and should not have to.
    """
    live: List[Dict[str, str]] = []
    dead: List[Dict[str, str]] = []
    for candidate in candidates or ():
        if len(live) >= max(0, int(limit)):
            break
        try:
            answered = bool(probe(candidate["url"]))
        except Exception:  # noqa: BLE001 - a probe that raises is not a verdict
            answered = False
        (live if answered else dead).append(candidate)
    return live, dead


def record(
    store: Dict[str, Any],
    content_id: str,
    live: Sequence[Dict[str, str]],
    *,
    health_module: Any,
    now: Optional[Any] = None,
) -> int:
    """Step 5 - put what answered into the ledger, for the scan to publish.

    Not onto the card: the repair run is not the publisher. Each record is
    marked, so a later reader can tell a link found here from one the feed
    offered and the scan can cap how many of them it trusts.
    """
    written = 0
    for candidate in live or ():
        url = _text(candidate.get("url"))
        if not url:
            continue
        # The ledger is committed. A signed URL carries its token, and this
        # repository keeps tokens out of committed files - the ledger itself
        # stores families rather than URLs for exactly that reason. A
        # tokenized link would expire before the next scan used it anyway.
        if health_module.host_class(url) == health_module.HOST_TOKENIZED:
            continue
        identity = health_module.stream_id(
            url, source_id=_text(candidate.get("source_id")))
        written_record = health_module.record_result(
            store,
            identity=identity,
            url=url,
            content_id=_text(content_id),
            source_id=_text(candidate.get("source_id")),
            verification_status="verified_global",
            http_status=200,
            now=now,
        )
        if isinstance(written_record, dict):
            written_record[DISCOVERED_FLAG] = True
            written_record["url"] = url
            written_record["resolution_height"] = int(candidate.get("height") or 0)
        written += 1
    return written


def discovered_for(
    store: Dict[str, Any],
    content_id: str,
    *,
    health_module: Any,
    limit: int = MAX_BACKUPS,
) -> List[Dict[str, Any]]:
    """The links this found for one card, healthy and still worth offering.

    Read by the scan when it assembles the card. A record that has since gone
    unhealthy is not returned, so a candidate that worked once and then died
    does not become a permanent backup nobody rechecked.
    """
    wanted = _text(content_id)
    if not wanted:
        return []
    urls: List[Dict[str, Any]] = []
    for found in (store.get("links") or {}).values():
        if not isinstance(found, dict):
            continue
        if not found.get(DISCOVERED_FLAG):
            continue
        if _text(found.get("content_id")) != wanted:
            continue
        if found.get("status") != health_module.STATUS_HEALTHY:
            continue
        url = _text(found.get("url"))
        if url and url not in [u["url"] for u in urls]:
            urls.append({"url": url,
                         "source_id": _text(found.get("source_id")),
                         "resolution_height": int(found.get("resolution_height") or 0)})
        if len(urls) >= max(0, int(limit)):
            break
    return urls


def describe(worked: int, found: int, live: int, written: int) -> str:
    return (
        "repair candidates: %d entr(ies) searched, %d candidate(s) found, "
        "%d answered, %d recorded for the next scan"
        % (worked, found, live, written)
    )
