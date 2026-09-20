"""ধাপ ১ - the NO-LOSS COVERAGE GATE for the Movie pipeline (ধারা ৪.০).

This is not an optional improvement. It is the arithmetic that proves the
scanner lost nothing, and the master plan puts it first for a concrete reason:
every step after it - title cleaning, series merge, backup merge, poster policy
- creates a chance to lose an entry, and without this gate a series merge can
eat a stream and nobody finds out.

Two separate questions, two separate sums. v৩.১ tried to answer both with one
identity and it was logically impossible: an entry that returned a confirmed
404 is not "live", so it cannot sit inside a sum of live things.

    INVARIANT ১ - INGESTION ACCOUNTING
        "সোর্সে যা ছিল, তার প্রতিটি কোথায় গেল?"

        TOTAL_IN_SCOPE_ENTRIES = published_movie
                               + published_series_episode
                               + merged_stream
                               + quarantined_unresolved
                               + definitive_rejected

        One set, not a sum of two: every candidate parsed from an enabled
        source, after out_of_scope is filtered out. Not "live" - a
        proven-dead entry is inside it too, because it also has to go
        somewhere.

    INVARIANT ২ - LIVE PRESERVATION
        "যা চলছিল, তা কি এখনো দর্শকের কাছে পৌঁছাচ্ছে?"

        unexplained_live_loss = 0, or publish BLOCKs.

Two axes, never one
-------------------
`confirmed_unavailable` is *not* a sixth bucket and cannot become one. A
content stays `published_movie` while the health of its link moves
independently:

    axis 1 - CONTENT DISPOSITION   the five buckets above
    axis 2 - STREAM HEALTH         healthy / failing / confirmed_unavailable
                                   / dead   (state/movie-link-health.json)

`scanner/poster_validity.py` already runs a three-verdict model of exactly this
shape for artwork; this is the same idea applied to streams, not a new one.

What can never put an entry out of scope
----------------------------------------
Only three things do: a disabled source or entry, content that is explicitly
not a movie or series (a live channel, an event - what `content_router` sends
to another pipeline), and an empty or invalid record.

**Category is never one of them.** v৩.৩ said an entry outside the category
allowlist was out of scope and v৩.৪ struck it out, because a real, playing film
whose only fault is a wrong tag in the feed would then vanish from the
accounting - the exact thing this gate exists to prevent. An unknown category
becomes `Mix` with `category_pending`, stays inside the count, and stays
published. `scanner/movies.py::_canonical_movie_category` already funnels
unknown categories to Mix, so this writes down an existing behaviour rather
than changing one.

Stdlib only.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple
from urllib.parse import urlsplit, urlunsplit

REPORT_FILE = Path("reports") / "movie-source-coverage.json"

SCHEMA_VERSION = 1

#: ধারা ৪.০ - the five destinations. There is no sixth silent state, and a
#: change here is a change to the plan, not to the code.
DISPOSITIONS: Tuple[str, ...] = (
    "published_movie",
    "published_series_episode",
    "merged_stream",
    "quarantined_unresolved",
    "definitive_rejected",
)

#: The only three reasons an entry may be left out of the count.
OUT_OF_SCOPE_DISABLED = "disabled_source_or_entry"
OUT_OF_SCOPE_NON_MOVIE = "not_movie_or_series_content"
OUT_OF_SCOPE_INVALID = "empty_or_invalid_record"

OUT_OF_SCOPE_REASONS: Tuple[str, ...] = (
    OUT_OF_SCOPE_DISABLED,
    OUT_OF_SCOPE_NON_MOVIE,
    OUT_OF_SCOPE_INVALID,
)

#: ধারা ৪.০, the hard list. Four conditions, nothing else. 403/451, geo-blocks,
#: timeouts, 5xx, expired tokens and repeated failure from our own vantage are
#: all explicitly *not* here: 1,312 posters in this very codebase answer 403
#: from Bangladesh and 200 from a GitHub runner, and the same is true of
#: streams.
DEFINITIVE_REJECT_REASONS: Tuple[str, ...] = (
    "invalid_url_syntax",
    "confirmed_404_or_410",
    "confirmed_not_media",
    "source_removed_with_no_alternative",
)

#: Reasons an entry may sit in quarantine. Quarantine means unresolved, never
#: invisible: an entry that is published today stays published with a pending
#: flag, and one that was never published stays unpublished but is named here
#: with a reason instead of disappearing.
QUARANTINE_REASONS: Tuple[str, ...] = (
    "classification_pending",
    "verification_did_not_pass",
    "not_publishable",
    "duplicate_of_published_stream",
    "restricted_from_our_vantage",
    "no_terminal_evidence_and_not_published",
)

_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
_NON_WORD = re.compile(r"[^\w]+", re.UNICODE)

#: Query parameters that are a *version* of an entry, not a different entry -
#: ধারা ৪.০, "সাময়িক টোকেন/query আলাদা সংস্করণ, আলাদা entry নয়".
_VOLATILE_QUERY_HINTS = (
    "token", "auth", "sig", "signature", "expire", "expires", "hash", "hdnts",
    "md5", "key", "session", "st", "e", "policy",
)


# ---------------------------------------------------------------------------
# io
# ---------------------------------------------------------------------------

def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=str(path.parent), delete=False,
        prefix=f".{path.name}.", suffix=".tmp",
    )
    try:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    finally:
        handle.close()
    os.replace(handle.name, path)


def write_movie_coverage(
    report: Dict[str, Any], path: Path | str = REPORT_FILE
) -> None:
    _atomic_write(Path(path), report)


# ---------------------------------------------------------------------------
# identity - ধারা ৪.০, "entry identity - শুধু URL যথেষ্ট নয়"
# ---------------------------------------------------------------------------

def clean_url(value: Any) -> str:
    """The playable URL with any inline `|Referer=`/`|User-Agent=` suffix cut.

    This codebase parses those suffixes (scanner/parsers/), so the header part
    belongs in the header profile, not in the URL.
    """
    return str(value or "").split("|", 1)[0].strip()


def stream_family(value: Any) -> str:
    """Host plus stable path - the part of a URL that identifies the stream.

    The query is dropped when it looks like a token or an expiry, because a
    re-signed URL is the same entry with a new signature. A query that carries
    real routing (an id, a channel) is kept, so two genuinely different streams
    on one path do not collapse into one.
    """
    url = clean_url(value)
    if not url:
        return ""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url.casefold()
    query = parts.query
    if query:
        lowered = query.casefold()
        if any(hint in lowered for hint in _VOLATILE_QUERY_HINTS):
            query = ""
    return urlunsplit((
        (parts.scheme or "").casefold(),
        (parts.netloc or "").casefold(),
        parts.path or "",
        query,
        "",
    ))


def path_family(value: Any) -> str:
    """The path part of a stream family, without the host.

    A second, weaker matching layer, and it exists because of a measurement:
    `sm-movie-combined` serves the same file from a **rotating** CDN host. On
    2026-09-20 the feed offered

        https://afghbn.b-cdn.net/s3/upload/videos/2026/09/[Fibwatch.Com]Seven.Snipers.(2026).Dual.1080P.mkv

    while the published card for the same film held

        https://jrtyh.b-cdn.net/s3/upload/videos/2026/09/...

    Same path, different host, and host+path matching therefore recognised
    **none** of 32,983 feed rows against 1,313 published cards from that source.
    A gate that reports every entry as unaccounted is not a strict gate, it is a
    broken one.

    Weaker on purpose: two different hosts really can serve different content on
    the same path, so a path match is recorded as a path match and never
    presented as an exact one.
    """
    url = clean_url(value)
    if not url:
        return ""
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    path = parts.path or ""
    if not path or path == "/":
        return ""
    return path


def header_profile(entry: Dict[str, Any]) -> str:
    """The Referer/Origin/User-Agent context this stream needs, as one token.

    Two entries with the same URL and different headers are two usable streams,
    and deduping on the URL alone is how a working backup gets thrown away.
    """
    if not isinstance(entry, dict):
        return ""
    named = str(entry.get("header_profile") or "").strip().casefold()
    if named:
        return named
    parts = []
    for key in ("http_referrer", "referer", "referrer", "origin", "user_agent",
                "http_user_agent"):
        value = str(entry.get(key) or "").strip().casefold()
        if value:
            parts.append(f"{key}={value}")
    raw = str(entry.get("url") or "")
    if "|" in raw:
        parts.append(raw.split("|", 1)[1].strip().casefold())
    return "&".join(sorted(set(parts)))


def content_identity(entry: Dict[str, Any]) -> str:
    """The canonical movie identity - the merger's, not a second one.

    `scanner/merger.movie_identity_key` says so in its own docstring: "the
    canonical identity other movie subsystems should key on too, so it lives
    here once rather than being reimplemented". It is imdb_id -> tmdb_id ->
    normalised title:year, and its title normaliser strips the release noise
    (1080p, Dual Audio, WEB-DL, HEVC ...) that would otherwise make
    "Seven Snipers (2026) Dual 1080P" in a feed and "Seven Snipers (2026) Dual"
    on a card look like two different films.

    The local fallback below is only for the case where the merger cannot be
    imported; it deliberately mirrors the same rule rather than inventing a
    softer one.
    """
    if not isinstance(entry, dict):
        return ""
    try:
        from scanner.merger import movie_identity_key

        key = movie_identity_key(entry)
        # `fallback:` keys are positional, not identities - two unrelated rows
        # at the same index would match each other.
        if key and not key.startswith("fallback:"):
            return key
        return ""
    except Exception:  # noqa: BLE001 - never let identity take a report down
        pass

    name = str(entry.get("name") or entry.get("title") or "").strip()
    if not name:
        return ""
    lowered = name.casefold()
    years = _YEAR.findall(lowered)
    year = ""
    for candidate in (entry.get("year"), years[0] if years else ""):
        text = str(candidate or "").strip()
        if text.isdigit() and len(text) == 4:
            year = text
            break
    stripped = _YEAR.sub(" ", lowered)
    slug = _NON_WORD.sub("-", stripped).strip("-")
    return f"title:{slug}:{year}"


def entry_key(entry: Dict[str, Any]) -> str:
    """ধারা ৪.০ - source_id + stable_stream_family + header_profile + content.

    All four parts, because dropping any one of them merges entries that are
    really separate: the same file on two sources, the same URL with two header
    profiles, or two films that happen to share a path.
    """
    if not isinstance(entry, dict):
        return ""
    source_id = str(entry.get("source_id") or "").strip().casefold()
    return "␟".join((
        source_id,
        stream_family(entry.get("url")),
        header_profile(entry),
        content_identity(entry),
    ))


# ---------------------------------------------------------------------------
# scope - ধারা ৪.০
# ---------------------------------------------------------------------------

def classify_scope(
    entry: Dict[str, Any],
    *,
    source_enabled: bool = True,
) -> Tuple[bool, str]:
    """`(in_scope, reason)`. The reason is empty when the entry is in scope.

    Category is never consulted. That is the whole point of the v৩.৪
    correction, and a test holds it in place.
    """
    if not isinstance(entry, dict):
        return False, OUT_OF_SCOPE_INVALID

    if not source_enabled:
        return False, OUT_OF_SCOPE_DISABLED
    if entry.get("enabled") is False:
        return False, OUT_OF_SCOPE_DISABLED

    url = clean_url(entry.get("url"))
    name = str(entry.get("name") or entry.get("title") or "").strip()
    if not url or not name:
        return False, OUT_OF_SCOPE_INVALID
    try:
        parts = urlsplit(url)
    except ValueError:
        return False, OUT_OF_SCOPE_INVALID
    if not parts.scheme or not parts.netloc:
        return False, OUT_OF_SCOPE_INVALID

    # The one place the router is asked. It answers "is this a movie/series at
    # all", and a `tv` or event answer is the plan's "স্পষ্টভাবে মুভি/সিরিজ নয়".
    try:
        from scanner.content_router import classify_candidate

        routed, _reason = classify_candidate(entry)
    except Exception:  # noqa: BLE001 - a router failure must not drop an entry
        return True, ""
    if routed != "movies":
        return False, OUT_OF_SCOPE_NON_MOVIE

    return True, ""


# ---------------------------------------------------------------------------
# the published side
# ---------------------------------------------------------------------------

def _stream_records(card: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Primary first, then backups, each as a dict carrying its own source_id."""
    records: List[Dict[str, Any]] = []
    primary = clean_url(card.get("url"))
    if primary:
        records.append({
            "url": primary,
            "source_id": str(card.get("source_id") or ""),
            "header_profile": card.get("header_profile") or "",
            "primary": True,
        })
    for backup in card.get("backups") or ():
        if not isinstance(backup, dict):
            continue
        url = clean_url(backup.get("url"))
        if not url:
            continue
        records.append({
            "url": url,
            "source_id": str(backup.get("source_id") or card.get("source_id") or ""),
            "header_profile": backup.get("header_profile") or "",
            "primary": False,
        })
    return records


def published_index(
    movies: Iterable[Tuple[str, Dict[str, Any]]],
    episodes: Iterable[Tuple[str, Dict[str, Any]]] = (),
) -> Dict[str, Any]:
    """Everything the site currently offers, indexed the ways matching needs.

    Three indexes rather than one, because a raw feed row can be recognised by
    its exact stream, by its stream family alone (a re-signed URL), or only by
    the content it names.
    """
    by_family: Dict[str, Dict[str, Any]] = {}
    by_path: Dict[str, Dict[str, Any]] = {}
    by_content: Dict[str, List[Dict[str, Any]]] = {}
    by_card_id: Dict[str, List[Dict[str, Any]]] = {}
    pending_content: Set[str] = set()

    def absorb(kind: str, scope: str, card: Dict[str, Any]) -> None:
        identity = content_identity(card)
        card_id = str(card.get("id") or "")
        is_pending = any(bool(card.get(flag)) for flag in PENDING_FLAGS)
        if is_pending and identity:
            pending_content.add(identity)
        for record in _stream_records(card):
            family = stream_family(record["url"])
            if not family:
                continue
            entry = {
                "kind": kind,
                "scope": scope,
                "card_id": card_id,
                "content_identity": identity,
                "primary": record["primary"],
                "source_id": record["source_id"],
                "pending": is_pending,
            }
            by_family.setdefault(family, entry)
            path = path_family(record["url"])
            if path:
                by_path.setdefault(path, entry)
            if identity:
                by_content.setdefault(identity, []).append(entry)
            if card_id:
                by_card_id.setdefault(card_id, []).append(entry)

    for scope, card in movies:
        if isinstance(card, dict):
            absorb("movie", scope, card)
    for scope, card in episodes:
        if isinstance(card, dict):
            absorb("episode", scope, card)

    return {
        "by_family": by_family,
        "by_path": by_path,
        "by_content": by_content,
        "by_card_id": by_card_id,
        "pending_content": pending_content,
    }


def locate(index: Dict[str, Any], entry: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], str]:
    """Where a stream is on the site, and how confidently we know it.

    Three layers, strongest first, and the layer is returned so a report can
    say `host_rotated` instead of quietly treating a weak match as an exact
    one:

        exact_stream    host + stable path agree
        path_rotated    the path agrees and the host does not - a rotating CDN
        content_only    no stream matched, but the film itself is published
    """
    family = stream_family(entry.get("url"))
    if family:
        match = index["by_family"].get(family)
        if match is not None:
            return match, "exact_stream"

    path = path_family(entry.get("url"))
    if path:
        match = index["by_path"].get(path)
        if match is not None:
            return match, "path_rotated"

    identity = content_identity(entry)
    if identity:
        matches = index["by_content"].get(identity)
        if matches:
            return matches[0], "content_only"

    return None, ""


#: The flags that make an entry "visible pending" rather than simply published.
#: ধারা ৪.০/৪.৬/৪.৭ name these four; an entry carrying any of them is still on
#: the site, which is what INVARIANT ২ cares about.
PENDING_FLAGS: Tuple[str, ...] = (
    "classification_pending",
    "category_pending",
    "metadata_pending",
    "artwork_pending",
)


# ---------------------------------------------------------------------------
# the private catalogue's raw entries
# ---------------------------------------------------------------------------

PRIVATE_CACHE_PATH = Path("state") / "manual-movie-remote-cache.json"


def private_snapshot_entries(
    root: Any = ".",
    source_id: str = "",
    *,
    cache_path: Any = PRIVATE_CACHE_PATH,
) -> List[Dict[str, Any]]:
    """The owner's catalogue rows, one entry per playable link.

    The private repository does not come through `collect_candidates` - it is
    fetched inside `scanner/movies.load_manual_movies` - so without this the
    gate would report `raw_entries: 0` for the most valuable source in the
    system and call the accounting balanced. `state/manual-movie-remote-cache.json`
    is written by that same fetch, so after `process_movies` it holds this
    run's rows.

    One entry per **link**, not per card. A row carries its streams in `links`,
    and reading it as one entry with no top-level `url` marked all 858 of them
    `empty_or_invalid_record` on the first run - the private catalogue reported
    as invalid.
    """
    path = Path(cache_path)
    if not path.is_absolute():
        path = Path(root) / path
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return []
    cached = (payload or {}).get("sources")
    if not isinstance(cached, dict):
        return []

    # The cache spells the same repository differently from the published
    # cards (`hopeful-research:<hash>` against `hopeful-research-latest:<hash>`),
    # so attribution is by prefix and everything lands on the configured row.
    # A row per spelling would split one source into several.
    prefix = str(source_id or "").split(":", 1)[0]
    stem = prefix.rsplit("-", 1)[0] if "-" in prefix else prefix

    entries: List[Dict[str, Any]] = []
    for key, record in cached.items():
        if not isinstance(record, dict):
            continue
        if prefix and not (str(key).startswith(prefix) or (stem and str(key).startswith(stem))):
            continue
        for item in record.get("items") or ():
            if not isinstance(item, dict):
                continue
            links = [
                link for link in (item.get("links") or ())
                if isinstance(link, dict) and str(link.get("url") or "").strip()
            ]
            if not links:
                links = [{"url": item.get("url") or item.get("link") or ""}]
            for position, link in enumerate(links, start=1):
                entry = dict(item)
                entry.pop("links", None)
                entry["url"] = link.get("url") or ""
                for field in ("label", "resolution", "resolution_height"):
                    if link.get(field) is not None:
                        entry[field] = link[field]
                entry["source_id"] = source_id or str(key)
                entry["source_file"] = str(key)
                entry["source_link_position"] = position
                entry.setdefault("source_pipeline", "movies")
                if not entry.get("category"):
                    entry["category"] = record.get("category") or ""
                entries.append(entry)
    return entries


# ---------------------------------------------------------------------------
# INVARIANT ১
# ---------------------------------------------------------------------------

def _disposition_for(
    entry: Dict[str, Any],
    index: Dict[str, Any],
    terminal: Dict[str, str],
    quarantine_reasons: Dict[str, str],
) -> Tuple[str, str]:
    """`(disposition, reason)` for one in-scope entry. Never returns nothing.

    Order matters: a terminal verdict is only allowed to win when the entry is
    not actually published, because a card on the site is not "rejected"
    whatever some report says about one of its links.
    """
    key = entry_key(entry)
    family = stream_family(entry.get("url"))

    match, layer = locate(index, entry)
    if match is not None:
        if layer == "content_only":
            # The film is on the site, this particular stream is not: the entry
            # was folded into that card. A merge, not a loss.
            return "merged_stream", "folded_into_published_card"
        if not match["primary"]:
            return "merged_stream", (
                "published_as_backup" if layer == "exact_stream"
                else "published_as_backup_host_rotated"
            )
        if match["kind"] == "episode":
            return "published_series_episode", (
                "published_as_episode" if layer == "exact_stream"
                else "published_as_episode_host_rotated"
            )
        return "published_movie", (
            "published_as_movie" if layer == "exact_stream"
            else "published_as_movie_host_rotated"
        )

    reason = terminal.get(key) or terminal.get(family or "\u0000")
    if reason:
        return "definitive_rejected", reason

    quarantine = (
        quarantine_reasons.get(key)
        or quarantine_reasons.get(family or "\u0000")
        or "no_terminal_evidence_and_not_published"
    )
    return "quarantined_unresolved", quarantine


def _source_row(source_id: str, name: str, enabled: bool) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "source_id": source_id,
        "source_name": name,
        "enabled": bool(enabled),
        "raw_entries": 0,
        "out_of_scope": 0,
        "out_of_scope_reasons": {},
    }
    for disposition in DISPOSITIONS:
        row[disposition] = 0
    row["definitive_rejected_reasons"] = {}
    row["quarantined_reasons"] = {}
    row["total_in_scope_entries"] = 0
    row["identity_balanced"] = True
    return row


# ---------------------------------------------------------------------------
# INVARIANT ২
# ---------------------------------------------------------------------------

def _inventory_families(lines: Iterable[str]) -> Dict[str, str]:
    """`stream family -> inventory line`, from `movie_baseline.stream_inventory`.

    The inventory is `kind|scope|identity|url`, which is the shape ধাপ ০ writes
    and commits, so INVARIANT ২ compares against something that was recorded
    before this code existed rather than against a number this code invented.
    """
    found: Dict[str, str] = {}
    for line in lines or ():
        text = str(line or "")
        parts = text.split("|", 3)
        if len(parts) != 4:
            continue
        family = stream_family(parts[3])
        if family:
            found.setdefault(family, text)
    return found


def _inventory_identity(line: str) -> str:
    parts = str(line or "").split("|", 3)
    return parts[2] if len(parts) == 4 else ""


def live_preservation(
    previous_inventory: Sequence[str],
    current_inventory: Sequence[str],
    index: Dict[str, Any],
    terminal_families: Dict[str, str],
) -> Dict[str, Any]:
    """INVARIANT ২ - is what was reaching viewers still reaching them?

    Every previously live stream must end in exactly one of four places, and
    the fifth possibility - none of them - is the number that blocks a publish.

    The unit is the distinct stream, not the inventory line. 34 of this
    catalogue's 2,475 lines are the same episode published under two
    categories - `lingam-2026` appears under both `dubbed` and `premium` - and
    counting one stream twice would make the arithmetic describe the catalogue's
    duplication rather than its preservation. Both numbers are reported, so the
    collapse is visible rather than silent.
    """
    previous = _inventory_families(previous_inventory)
    current = _inventory_families(current_inventory)

    currently_visible = 0
    merged = 0
    visible_pending = 0
    terminal_evidence = 0
    match_layers: Counter = Counter()
    lost: List[Dict[str, Any]] = []

    for family, line in sorted(previous.items()):
        url = line.split("|", 3)[3] if line.count("|") >= 3 else ""
        probe = {"url": url, "name": ""}

        if family in current:
            match = index["by_family"].get(family)
            match_layers["exact_stream"] += 1
            if match is not None and match.get("pending"):
                visible_pending += 1
            else:
                currently_visible += 1
            continue

        # Not at the same address. Ask the weaker layers before calling it a
        # loss: a rotating CDN host moves every URL of a source at once, and
        # reading that as 32,983 losses would block every publish for ever.
        match, layer = locate(index, probe)
        if match is not None:
            match_layers[layer] += 1
            if match.get("pending"):
                visible_pending += 1
            elif layer == "exact_stream":
                currently_visible += 1
            else:
                merged += 1
            continue

        # Last resort: the card is still on the site under the id ধাপ ০
        # recorded, but none of its streams are the ones we knew. That is a
        # card whose links were all replaced, which is a merge rather than a
        # loss.
        #
        # The inventory's third field is a *card id*, so only the card index
        # can answer it - an earlier version also consulted `by_content` here,
        # which is keyed by `title:...:year` and could therefore never match a
        # card id. Dead code that looks like a safety net is worse than no
        # safety net, so it is gone: a card whose id changed but whose streams
        # did not is already caught by the stream layers above, and one where
        # both changed is genuinely something new and should be flagged.
        recorded_id = _inventory_identity(line)
        matches = list(index["by_card_id"].get(recorded_id) or ())
        if matches:
            match_layers["card_id"] += 1
            if all(entry.get("pending") for entry in matches):
                visible_pending += 1
            else:
                merged += 1
            continue

        if family in terminal_families:
            terminal_evidence += 1
            continue

        lost.append({"stream_family": family, "inventory_line": line})

    block_reasons: List[str] = []
    if lost:
        block_reasons.append(
            "%d previously live stream(s) are neither published, merged, "
            "visible-pending nor terminally explained" % len(lost)
        )

    return {
        "previously_live": len(previous),
        "previously_live_inventory_lines": len(previous_inventory or ()),
        "duplicate_inventory_lines": max(
            0, len(previous_inventory or ()) - len(previous)),
        "currently_visible": currently_visible,
        "merged_stream": merged,
        "visible_pending": visible_pending,
        "terminal_evidence": terminal_evidence,
        "unexplained_live_loss": len(lost),
        "match_layers": dict(sorted(match_layers.items())),
        "block_reasons": block_reasons,
        "lost_sample": lost[:25],
    }


# ---------------------------------------------------------------------------
# the report
# ---------------------------------------------------------------------------

def build_movie_coverage(
    *,
    configured_sources: Iterable[Dict[str, Any]],
    raw_entries: Iterable[Dict[str, Any]],
    published_movies: Iterable[Tuple[str, Dict[str, Any]]],
    published_episodes: Iterable[Tuple[str, Dict[str, Any]]] = (),
    previous_inventory: Sequence[str] = (),
    current_inventory: Sequence[str] = (),
    terminal_records: Iterable[Dict[str, Any]] = (),
    quarantine_records: Iterable[Dict[str, Any]] = (),
    evidence: str = "scan",
    now: Optional[str] = None,
) -> Dict[str, Any]:
    """The whole gate, as data. Decides nothing; counts, and says where.

    `evidence` records how the raw entries were obtained - `scan` when a real
    run supplied them, `audit` when the offline auditor fetched the sources
    itself. A reader can then tell a report that watched a publish from one
    that reconstructed it, which is the difference between a measurement and a
    reconstruction.
    """
    sources: Dict[str, Dict[str, Any]] = {}
    enabled_by_id: Dict[str, bool] = {}
    for configured in configured_sources or ():
        if not isinstance(configured, dict):
            continue
        source_id = str(
            configured.get("id") or configured.get("source_id") or ""
        ).strip()
        if not source_id:
            continue
        enabled = bool(configured.get("enabled", True))
        enabled_by_id[source_id] = enabled
        sources[source_id] = _source_row(
            source_id,
            str(configured.get("name") or configured.get("source_name") or source_id),
            enabled,
        )

    movies = [pair for pair in published_movies or ()]
    episodes = [pair for pair in published_episodes or ()]
    index = published_index(movies, episodes)

    terminal: Dict[str, str] = {}
    terminal_families: Dict[str, str] = {}
    for record in terminal_records or ():
        if not isinstance(record, dict):
            continue
        reason = str(record.get("reason") or "").strip()
        if reason not in DEFINITIVE_REJECT_REASONS:
            # A reason outside the hard list is not terminal. 403, geo, timeout
            # and repeated failure from our vantage all land here, and land in
            # quarantine instead, which is the v৩.২ correction.
            continue
        family = stream_family(record.get("url"))
        if family:
            terminal_families[family] = reason
            terminal[family] = reason
        key = str(record.get("entry_key") or "")
        if key:
            terminal[key] = reason

    quarantine_reasons: Dict[str, str] = {}
    for record in quarantine_records or ():
        if not isinstance(record, dict):
            continue
        reason = str(record.get("reason") or "").strip()
        if not reason:
            continue
        family = stream_family(record.get("url"))
        if family:
            quarantine_reasons[family] = reason
        key = str(record.get("entry_key") or "")
        if key:
            quarantine_reasons[key] = reason

    unconfigured: Dict[str, Dict[str, Any]] = {}
    totals = Counter()
    seen_keys: Set[str] = set()
    duplicate_entry_keys = 0

    for entry in raw_entries or ():
        if not isinstance(entry, dict):
            continue
        source_id = str(entry.get("source_id") or "").strip()
        if source_id in sources:
            row = sources[source_id]
        else:
            row = unconfigured.setdefault(
                source_id or "(unattributed)",
                _source_row(source_id or "(unattributed)",
                            str(entry.get("source_name") or source_id), True),
            )
        row["raw_entries"] += 1
        totals["raw_entries"] += 1

        in_scope, reason = classify_scope(
            entry, source_enabled=enabled_by_id.get(source_id, True)
        )
        if not in_scope:
            row["out_of_scope"] += 1
            row["out_of_scope_reasons"][reason] = (
                row["out_of_scope_reasons"].get(reason, 0) + 1)
            totals["out_of_scope"] += 1
            totals[f"out_of_scope:{reason}"] += 1
            continue

        key = entry_key(entry)
        if key in seen_keys:
            duplicate_entry_keys += 1
        seen_keys.add(key)

        disposition, detail = _disposition_for(
            entry, index, terminal, quarantine_reasons)
        row[disposition] += 1
        row["total_in_scope_entries"] += 1
        totals[disposition] += 1
        totals["total_in_scope_entries"] += 1
        if disposition == "definitive_rejected":
            row["definitive_rejected_reasons"][detail] = (
                row["definitive_rejected_reasons"].get(detail, 0) + 1)
        elif disposition == "quarantined_unresolved":
            row["quarantined_reasons"][detail] = (
                row["quarantined_reasons"].get(detail, 0) + 1)

    for row in list(sources.values()) + list(unconfigured.values()):
        bucket_sum = sum(row[disposition] for disposition in DISPOSITIONS)
        row["identity_balanced"] = bucket_sum == row["total_in_scope_entries"]
        row["bucket_sum"] = bucket_sum

    invariant_two = live_preservation(
        previous_inventory, current_inventory, index, terminal_families)

    total_in_scope = totals["total_in_scope_entries"]
    bucket_total = sum(totals[disposition] for disposition in DISPOSITIONS)

    report: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": "movie_source_coverage",
        "generated_at": now or _utc_now(),
        "evidence": evidence,
        "note": (
            "ধারা ৪.০ NO-LOSS COVERAGE GATE. INVARIANT ১ accounts for every "
            "candidate an enabled Movie source produced; INVARIANT ২ asks "
            "whether what was reaching viewers still does. "
            "unexplained_live_loss must be 0 or the publish is blocked."
        ),
        "configured_source_count": len(sources),
        "invariant_1": {
            "name": "ingestion_accounting",
            "raw_entries": totals["raw_entries"],
            "out_of_scope": totals["out_of_scope"],
            "out_of_scope_reasons": {
                reason: totals[f"out_of_scope:{reason}"]
                for reason in OUT_OF_SCOPE_REASONS
                if totals[f"out_of_scope:{reason}"]
            },
            "total_in_scope_entries": total_in_scope,
            **{disposition: totals[disposition] for disposition in DISPOSITIONS},
            "bucket_sum": bucket_total,
            "identity_balanced": bucket_total == total_in_scope,
            "duplicate_entry_keys": duplicate_entry_keys,
        },
        "invariant_2": {"name": "live_preservation", **invariant_two},
        "published": {
            "movies": len(movies),
            "episodes": len(episodes),
            "stream_families": len(index["by_family"]),
            "pending_content": len(index["pending_content"]),
        },
        "sources": [sources[key] for key in sorted(sources)],
        "unconfigured_sources": [unconfigured[key] for key in sorted(unconfigured)],
    }
    report["invariants"] = check_invariants(report)
    return report


def check_invariants(report: Dict[str, Any]) -> Dict[str, Any]:
    """The checks the gate has to survive, and what blocks a publish.

    Separated from the counting so a report loaded from disk can be re-checked
    without being rebuilt, which is what the publish gate does.

    BLOCK is reserved for real damage (ধারা ৪.০): the identity not balancing,
    or a previously live entry gone with no explanation. A quarantine spike is
    a WARNING and never a BLOCK - a new public source can legitimately bring in
    a day's worth of messy titles, and stopping the whole site's update for
    that would be wrong. v৩.০'s ">10% quarantine growth blocks" rule was
    struck out for exactly this reason.
    """
    checks: List[Dict[str, Any]] = []

    def check(name: str, passed: bool, detail: str = "",
              blocking: bool = True) -> None:
        checks.append({
            "name": name, "passed": bool(passed),
            "detail": detail, "blocking": bool(blocking),
        })

    one = report.get("invariant_1") or {}
    two = report.get("invariant_2") or {}
    rows = [row for row in (report.get("sources") or []) if isinstance(row, dict)]
    extra = [row for row in (report.get("unconfigured_sources") or [])
             if isinstance(row, dict)]

    # INVARIANT ১ - the identity itself.
    total = int(one.get("total_in_scope_entries") or 0)
    bucket_sum = int(one.get("bucket_sum") or 0)
    check(
        "INVARIANT_1 total_in_scope_entries == sum of the five dispositions",
        total == bucket_sum,
        "in scope %d, buckets %d" % (total, bucket_sum),
    )

    unbalanced = [
        row.get("source_id") for row in rows + extra
        if not row.get("identity_balanced")
    ]
    check(
        "INVARIANT_1 every source row balances",
        not unbalanced,
        "unbalanced: %s" % unbalanced[:5],
    )

    # raw = out_of_scope + in_scope, per row and overall. A row where these
    # disagree has lost an entry between the two counters.
    leaky = [
        row.get("source_id") for row in rows + extra
        if int(row.get("raw_entries") or 0)
        != int(row.get("out_of_scope") or 0) + int(row.get("total_in_scope_entries") or 0)
    ]
    check(
        "INVARIANT_1 raw_entries == out_of_scope + total_in_scope_entries",
        not leaky,
        "leaking: %s" % leaky[:5],
    )

    # Only the three sanctioned reasons may put an entry out of scope. A
    # category-shaped reason appearing here is the v৩.৪ regression this check
    # exists to catch.
    illegal = sorted(
        set(one.get("out_of_scope_reasons") or {}) - set(OUT_OF_SCOPE_REASONS)
    )
    check(
        "INVARIANT_1 out_of_scope uses only the three sanctioned reasons",
        not illegal,
        "illegal reasons: %s" % illegal,
    )

    # definitive_rejected is the one bucket an entry cannot come back from, so
    # every reason in it has to be on the hard list.
    bad_terminal: Set[str] = set()
    for row in rows + extra:
        bad_terminal |= (
            set(row.get("definitive_rejected_reasons") or {})
            - set(DEFINITIVE_REJECT_REASONS)
        )
    check(
        "INVARIANT_1 definitive_rejected uses only the four hard reasons",
        not bad_terminal,
        "outside the hard list: %s" % sorted(bad_terminal)[:5],
    )

    # INVARIANT ২ - the one number the whole gate is named for.
    lost = int(two.get("unexplained_live_loss") or 0)
    check(
        "INVARIANT_2 unexplained_live_loss == 0",
        lost == 0,
        "unexplained live loss: %d" % lost,
    )

    previously_live = int(two.get("previously_live") or 0)
    accounted = sum(int(two.get(field) or 0) for field in (
        "currently_visible", "merged_stream", "visible_pending",
        "terminal_evidence", "unexplained_live_loss",
    ))
    check(
        "INVARIANT_2 every previously live stream has exactly one outcome",
        previously_live == accounted,
        "previously live %d, accounted %d" % (previously_live, accounted),
    )

    # Not blocking: worth seeing, never worth stopping a publish for.
    check(
        "no duplicate entry keys among in-scope entries",
        not int(one.get("duplicate_entry_keys") or 0),
        "duplicates: %d" % int(one.get("duplicate_entry_keys") or 0),
        blocking=False,
    )
    silent = [
        row.get("source_id") for row in rows
        if not int(row.get("raw_entries") or 0) and row.get("enabled")
    ]
    check(
        "an enabled source that produced nothing still has a row",
        True,
        "produced nothing: %s" % silent[:5],
        blocking=False,
    )

    failed = [entry for entry in checks if not entry["passed"]]
    blocking = [entry for entry in failed if entry["blocking"]]

    reasons = [entry["name"] + (": " + entry["detail"] if entry["detail"] else "")
               for entry in blocking]
    reasons.extend(str(item) for item in (two.get("block_reasons") or []))

    return {
        "checked_at": _utc_now(),
        "passed": len(checks) - len(failed),
        "failed": len(failed),
        "failures": [entry["name"] for entry in failed],
        "block": bool(blocking),
        "block_reasons": reasons,
        "checks": checks,
    }


def publish_blocked(report: Any) -> Tuple[bool, List[str]]:
    """`(blocked, reasons)` for the publish gate, from a built or loaded report.

    Three cases, and the difference between the last two is the whole point:

      * `None`            no gate ran. Not a block - a channels or events run
                          has nothing to say about movies.
      * a usable report   its own verdict.
      * anything else     a block. A truncated or malformed coverage report is
                          a gate that could not answer, and an unanswered gate
                          that waves a publish through is worse than no gate,
                          because it looks like one.

    The middle case used to swallow the third: a report whose `invariants` was
    not a mapping fell through to a fresh `check_invariants`, which on a report
    with no `invariant_1`/`invariant_2` compared 0 against 0, passed, and let
    the publish proceed.
    """
    if report is None:
        return False, []
    if not isinstance(report, dict):
        return True, ["the no-loss coverage report is not a mapping"]

    invariants = report.get("invariants")
    if invariants is not None and not isinstance(invariants, dict):
        return True, [
            "the no-loss coverage report's invariants block is malformed, so "
            "the gate could not answer"
        ]
    if invariants is None:
        missing = [
            field for field in ("invariant_1", "invariant_2")
            if not isinstance(report.get(field), dict)
        ]
        if missing:
            return True, [
                "the no-loss coverage report is incomplete (missing %s), so "
                "the gate could not answer" % ", ".join(missing)
            ]
        invariants = check_invariants(report)

    return bool(invariants.get("block")), list(invariants.get("block_reasons") or [])


def format_movie_coverage(report: Dict[str, Any]) -> str:
    """The same numbers, for a person reading a scan log."""
    one = report.get("invariant_1") or {}
    two = report.get("invariant_2") or {}
    invariants = report.get("invariants") or {}
    lines = [
        "MOVIE SOURCE COVERAGE  (%s evidence, %s)"
        % (report.get("evidence", "?"), report.get("generated_at", "")),
        "",
        "INVARIANT 1 - ingestion accounting",
        "  raw entries              %8d" % int(one.get("raw_entries") or 0),
        "  out of scope             %8d  %s"
        % (int(one.get("out_of_scope") or 0), one.get("out_of_scope_reasons") or {}),
        "  TOTAL IN SCOPE           %8d" % int(one.get("total_in_scope_entries") or 0),
    ]
    for disposition in DISPOSITIONS:
        lines.append("    %-26s %8d" % (disposition, int(one.get(disposition) or 0)))
    lines.extend([
        "  identity balanced        %8s" % one.get("identity_balanced"),
        "",
        "INVARIANT 2 - live preservation",
        "  previously live          %8d" % int(two.get("previously_live") or 0),
        "  currently visible        %8d" % int(two.get("currently_visible") or 0),
        "  merged                   %8d" % int(two.get("merged_stream") or 0),
        "  visible pending          %8d" % int(two.get("visible_pending") or 0),
        "  terminal evidence        %8d" % int(two.get("terminal_evidence") or 0),
        "  UNEXPLAINED LIVE LOSS    %8d" % int(two.get("unexplained_live_loss") or 0),
        "",
        "  publish %s" % ("BLOCKED" if invariants.get("block") else "allowed"),
    ])
    for reason in invariants.get("block_reasons") or ():
        lines.append("    - %s" % reason)
    return "\n".join(lines)
