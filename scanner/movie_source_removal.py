"""ধারা ৪.০ — the fourth definitive reason, which nothing had ever emitted.

**The measured deadlock.** The source retired the CDN host `jrtyh.b-cdn.net`.
`scanner/movie_retention.py` did exactly what it was written to do: a card the
source no longer lists is carried for GRACE_SCANS scans and then stopped. The
no-loss gate then did exactly what *it* was written to do: a previously live
stream that is not published, not merged, not visible-pending and has no
terminal evidence is an unexplained loss, and one of those blocks the publish.

Both were right, and together they stopped the site:

    2026-09-24 06:40 run   unexplained_live_loss 362   publish BLOCKED
    2026-09-24 08:41 run   unexplained_live_loss 534   publish BLOCKED

It grows because it feeds itself. The publish is blocked, so the previous pages
stay up, so those streams are "previously live" again next run, so the gate
blocks again. All 25 lost samples in the 08:41 report carried `misses: 11` in
the retention ledger against a grace of 3 - the source had not listed them for
eleven consecutive scans.

The plan already has the answer and names it as the fourth - and most dangerous
- member of `definitive_rejected`:

    ✔ সোর্স নিজেই entry সরিয়ে/নিষ্ক্রিয় করেছে **এবং** retention policy পাস
      **এবং** নিচের তিনটিই সত্য:
        • এই কনটেন্টের অন্য কোনো সোর্সে entry নেই
        • কোনো last-good playable stream সংরক্ষিত নেই
        • কোনো live ব্যাকআপ লিংক নেই
      ► সোর্স তালিকা থেকে সরে যাওয়া মানেই URL অচল নয়।

`movie_coverage.DEFINITIVE_REJECT_REASONS` has carried
`source_removed_with_no_alternative` since the gate was written. No code ever
produced one. This module is that code, and it is a separate file because this
is the only bucket an entry cannot come back from: it deserves its own tests
and its own reading.

**Why this cannot launder a vantage problem.** Nothing here looks at an HTTP
status, a timeout or a refusal. The only thing that can start a removal is the
source's own catalogue not listing the card - across more *complete* scans than
the retention grace allows - and any one of four counter-evidences stops it:

    the stream is in the payload about to be published
    the card is in the payload about to be published
    the link ledger says the link is healthy
    any source this run - including a last-good snapshot - still lists it

A 403 from Bangladesh, an expired token, a 5xx, a host that would not answer:
none of them is an input. A run that half-failed is not an input either, which
is what `complete_misses` is for - `scanner/movie_retention.py` counts a miss
toward removal only on a scan that saw enough of the category to be believed.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

#: The reason string, exactly as `movie_coverage.DEFINITIVE_REJECT_REASONS`
#: spells it. A different spelling is silently ignored by the gate, so this is
#: asserted against that tuple by the tests rather than copied by eye.
REASON = "source_removed_with_no_alternative"

#: Why a removal was NOT called terminal. Every one of these leaves the entry
#: exactly where it was - the gate keeps asking about it, which is the
#: conservative direction.
WITHIN_GRACE = "within_retention_grace"
STREAM_STILL_PUBLISHED = "stream_still_published"
CARD_STILL_PUBLISHED = "card_still_published"
LINK_STILL_HEALTHY = "link_still_healthy"
ANOTHER_SOURCE_HAS_IT = "another_source_still_lists_it"
NO_STREAM_RECORDED = "no_stream_recorded"


def _family(url: Any) -> str:
    """The gate's own notion of "the same stream", never a second opinion."""
    try:
        from scanner.movie_coverage import stream_family

        return stream_family(url)
    except Exception:  # noqa: BLE001 - one url, never the run
        return ""


def families_of(urls: Iterable[Any]) -> List[str]:
    found: List[str] = []
    for url in urls or ():
        family = _family(url)
        if family and family not in found:
            found.append(family)
    return found


def families_from_inventory(lines: Iterable[str]) -> Set[str]:
    """Families of `kind|scope|id|url` inventory lines.

    The inventory is what both halves of INVARIANT ২ are written from, so
    reading the payload through it keeps this module and the gate looking at
    the same streams.
    """
    found: Set[str] = set()
    for line in lines or ():
        text = str(line or "")
        if text.count("|") < 3:
            continue
        family = _family(text.split("|", 3)[3])
        if family:
            found.add(family)
    return found


def card_ids_from_cards(cards: Iterable[Any]) -> Set[str]:
    """Card ids of `(scope, card)` pairs, or of bare cards."""
    found: Set[str] = set()
    for item in cards or ():
        card = item[1] if isinstance(item, tuple) and len(item) == 2 else item
        if not isinstance(card, dict):
            continue
        identity = str(card.get("id") or "").strip().casefold()
        if identity:
            found.add(identity)
    return found


def healthy_families(store: Optional[Dict[str, Any]]) -> Set[str]:
    """Families the link ledger currently calls healthy.

    ধারা ৪.১'s two axes: this is stream health, and it is consulted here only
    ever as a *veto*. A link that answers is a live backup, so the content it
    belongs to has not lost its last way of being played.
    """
    found: Set[str] = set()
    links = (store or {}).get("links")
    if not isinstance(links, dict):
        return found
    try:
        from scanner.movie_link_health import STATUS_HEALTHY
    except Exception:  # noqa: BLE001
        STATUS_HEALTHY = "healthy"  # noqa: N806
    for record in links.values():
        if not isinstance(record, dict):
            continue
        if record.get("status") != STATUS_HEALTHY:
            continue
        family = str(record.get("stream_family") or "").strip() or _family(
            record.get("url"))
        if family:
            found.add(family)
    return found


def decide(
    entry: Dict[str, Any],
    *,
    published_families: Set[str],
    published_card_ids: Set[str],
    live_families: Set[str],
    source_families: Set[str],
    grace_scans: int,
) -> Tuple[List[str], str]:
    """`(families that are terminal, refusal reason)`.

    Exactly one of the two is ever non-empty. The refusal reason is recorded
    rather than discarded: "we declined to call 41 removals terminal, and here
    is why" is the sentence that makes this auditable from the report.
    """
    if not isinstance(entry, dict):
        return [], NO_STREAM_RECORDED

    misses = int(entry.get("complete_misses") or 0)
    if misses <= int(grace_scans):
        return [], WITHIN_GRACE

    families = families_of(entry.get("urls") or ())
    if not families:
        # Nothing to report as gone. A removal with no recorded stream cannot
        # explain a previously live stream, so it is not evidence of anything.
        return [], NO_STREAM_RECORDED

    identity = str(entry.get("content_id") or "").strip().casefold()
    if identity and identity in published_card_ids:
        return [], CARD_STILL_PUBLISHED

    for family in families:
        if family in published_families:
            return [], STREAM_STILL_PUBLISHED
        if family in live_families:
            return [], LINK_STILL_HEALTHY
        if family in source_families:
            return [], ANOTHER_SOURCE_HAS_IT

    return families, ""


def terminal_records(
    removed: Iterable[Dict[str, Any]],
    *,
    published_families: Optional[Set[str]] = None,
    published_card_ids: Optional[Set[str]] = None,
    live_families: Optional[Set[str]] = None,
    source_families: Optional[Set[str]] = None,
    grace_scans: Optional[int] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """`(records for the gate, summary)`.

    The records carry only a family and the reason. No entry key: a terminal
    verdict keyed by entry could reach an entry this run's sources DID produce,
    and `movie_coverage._disposition_for` would then have to defend against a
    removal that has already been contradicted.
    """
    if grace_scans is None:
        try:
            from scanner.movie_retention import GRACE_SCANS

            grace_scans = GRACE_SCANS
        except Exception:  # noqa: BLE001
            grace_scans = 3

    published_families = set(published_families or ())
    published_card_ids = set(published_card_ids or ())
    live_families = set(live_families or ())
    source_families = set(source_families or ())

    records: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    refused: Dict[str, int] = {}
    examples: List[str] = []
    considered = 0

    for entry in removed or ():
        considered += 1
        families, refusal = decide(
            entry,
            published_families=published_families,
            published_card_ids=published_card_ids,
            live_families=live_families,
            source_families=source_families,
            grace_scans=int(grace_scans),
        )
        if refusal:
            refused[refusal] = refused.get(refusal, 0) + 1
            continue
        for family in families:
            if family in seen:
                continue
            seen.add(family)
            records.append({"url": family, "reason": REASON})
        if len(examples) < 5:
            examples.append(
                f"{entry.get('content_id') or '?'} "
                f"({entry.get('complete_misses')} complete misses)"
            )

    summary = {
        "considered": considered,
        "removed_entries": considered - sum(refused.values()),
        "terminal_streams": len(records),
        "refused": dict(sorted(refused.items())),
        "examples": examples,
        "grace_scans": int(grace_scans),
    }
    return records, summary


def collect(
    *,
    published_cards: Sequence[Any] = (),
    current_inventory: Sequence[str] = (),
    raw_entries: Sequence[Dict[str, Any]] = (),
    health_store: Optional[Dict[str, Any]] = None,
    retention_path: Optional[str] = None,
    retention_store: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """The same decision, gathering its own evidence from this run's facts.

    `raw_entries` is every row the sources produced this run, which is where
    "no other source has it" and "no last-good snapshot has it" are answered
    together: a repository whose fetch failed contributes its last-good files
    through the ordinary path, so those rows are in here too.
    """
    from scanner import movie_retention

    removed = movie_retention.removed_entries(
        store=retention_store, path=retention_path)
    return terminal_records(
        removed,
        published_families=families_from_inventory(current_inventory),
        published_card_ids=card_ids_from_cards(published_cards),
        live_families=healthy_families(health_store),
        source_families={
            family for family in (
                _family(entry.get("url"))
                for entry in raw_entries or ()
                if isinstance(entry, dict)
            ) if family
        },
        grace_scans=movie_retention.GRACE_SCANS,
    )


def describe(summary: Dict[str, Any]) -> str:
    """One line for the scan log, or "" when there was nothing to say."""
    if not summary or not summary.get("considered"):
        return ""
    refused = summary.get("refused") or {}
    tail = ""
    if refused:
        tail = "; kept: " + ", ".join(
            f"{count} {reason}" for reason, count in refused.items())
    return (
        f"   source removals: {summary.get('removed_entries', 0)} of "
        f"{summary['considered']} past the retention grace with no alternative "
        f"({summary.get('terminal_streams', 0)} stream(s) reported gone){tail}"
    )
