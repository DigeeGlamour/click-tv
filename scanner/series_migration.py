"""ধাপ ৩খ (part 2) - move proven episode cards out of the movie catalogue.

563 cards in this catalogue are series content filed as films. 390 of them are
*proved* to be so by the title alone (ধারা ৪.৬ tiers 1-3), and this is the step
that puts them where they belong: Bachelor Point's 37 cards become one show
with 37 episodes.

What this module will not do
----------------------------
**It will not lose a stream.** Every card it moves keeps its primary and its
backups, and a card whose links would not survive the series pipeline is left
exactly where it is rather than pushed through it - see `survives_series_pipeline`.

**It will not invent an episode number.** A season-only card becomes a
"Complete Season" or "Unspecified" entry, never "Episode 1". ধারা ৪.৬ forbids
it and `episode_key` is chosen so that `series.episode_number_range` reads back
*nothing* for those entries rather than a number.

**It will not move an unproven card.** The 181 rows at ধারা ৪.৬ tier 3 stay
published as movie cards with `classification_pending`, which is what the plan
means by "অনিশ্চয়তা থাকলে আমরা অনুমান করব না, হারাবও না, এবং লুকাবও না".

**It will not create a show the site already publishes.** The ধাপ ৩ক rehearsal
found 15 of the 114 proposed shows already live as series, so migrated
episodes are staged into the same catalogue the private source writes and
merged there by show identity.

Reuse over rewriting
--------------------
Nothing here re-implements series publishing. Migrated shows are written in the
staging shape `scanner/series.prepare_manual_series` already reads, so they go
through the same normalisation, the same episode ordering, the same quality
sort and the same publisher as the private catalogue's own shows.

Stdlib only.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

#: Mirrored from `scanner/series._normalize_sources`, which drops any source
#: below this and then raises if none survive. A movie card whose links are all
#: below it must therefore never be handed to the series pipeline: the choice
#: there is between losing the streams and taking the scan down, and this step
#: is allowed to do neither. Measured on the current catalogue: all 421 links
#: on the 390 migrating cards are 720 or 1080, so the guard costs nothing today
#: and is the difference between a safe migration and a lucky one.
MIN_SOURCE_HEIGHT = 720

SKIP_BELOW_MIN_HEIGHT = "all_sources_below_the_series_minimum_height"
SKIP_NO_SHOW_KEY = "series_marker_with_no_show_name"
SKIP_NO_PLAYABLE_LINK = "no_playable_link"


def _height(link: Dict[str, Any]) -> int:
    try:
        return int(link.get("resolution_height") or 0)
    except (TypeError, ValueError):
        return 0


def card_links(card: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Primary first, then backups, each as a dict with a usable url."""
    links: List[Dict[str, Any]] = []
    if str(card.get("url") or "").strip():
        links.append(card)
    for backup in card.get("backups") or ():
        if isinstance(backup, dict) and str(backup.get("url") or "").strip():
            links.append(backup)
    return links


def survives_series_pipeline(card: Dict[str, Any]) -> bool:
    """Would `series._normalize_sources` keep at least one of these links?

    Asked before the move, not after, because after is too late: that function
    raises when nothing survives, and a raise in the middle of a migration is
    how a scan dies holding a half-moved catalogue.
    """
    return any(_height(link) >= MIN_SOURCE_HEIGHT for link in card_links(card))


def _category_priority() -> Dict[str, int]:
    """The owner's own category precedence, not a second one invented here."""
    try:
        from scanner.movies import _CATEGORY_PRIORITY

        return dict(_CATEGORY_PRIORITY)
    except Exception:  # noqa: BLE001
        return {}


def choose_category(counts: Counter) -> str:
    """The one category a show is published under, from its episodes'.

    Nine of the 114 migrating shows have episodes in two categories - Peaky
    Blinders is 8 in Mix and 1 in English, Money Heist 5 in Mix and 1 in Hindi.
    The tie is broken with `movies._CATEGORY_PRIORITY`, the precedence the
    owner already set for exactly this question, which has the side effect of
    pulling shows *out* of the Mix junk drawer - which is where A-03 wants them
    to end up anyway.
    """
    if not counts:
        return "Mix"
    priority = _category_priority()
    return min(
        counts,
        key=lambda category: (
            priority.get(category, len(priority) + 1), -counts[category], category
        ),
    )


def _alpha_token(seed: str, length: int = 6) -> str:
    """A short, deterministic, **digit-free** token.

    Digit-free is the requirement, not a preference.
    `series.episode_number_range` reads the first digit run it finds in an
    `episode_key` as the episode number, so "unspecified-2" would claim to be
    episode 2. Letters cannot be misread that way.
    """
    digest = hashlib.sha1(str(seed).encode("utf-8")).digest()
    alphabet = "abcdefghijklmnopqrstuvwxyz"
    return "".join(alphabet[byte % 26] for byte in digest[:length])


def _episode_key_and_label(
    signal: Dict[str, Any],
    card: Optional[Dict[str, Any]] = None,
) -> Tuple[str, str]:
    """`(episode_key, episode_label)` - and never "Episode 1" for an unknown.

    `series.episode_number_range` parses the key first and the label second. A
    numeric key is therefore a claim about which episode this is, so entries
    with no stated episode get a *non-numeric* key on purpose: the parser reads
    back nothing, `episode_start_number` stays None, and the frontend shows the
    label rather than a number nobody published.

    Unnumbered entries also get a per-card suffix. Without it, two "Complete
    Season" cards in one season share a key, and `series._normalize_series`
    drops the second - "the first spelling of it wins", which is right for a
    batch link beside its own episodes and wrong for two different uploads.
    Measured: exactly that cost 2 streams and 2 episodes on the first run of
    this migration against the real catalogue.
    """
    episode = signal.get("episode")
    end = signal.get("episode_end")
    if episode is not None and end is not None and end > episode:
        return f"{episode:02d}-{end:02d}", f"Episode {episode:02d}-{end:02d}"
    if episode is not None:
        return f"{episode:02d}", f"Episode {episode:02d}"

    suffix = _alpha_token(
        str((card or {}).get("id") or (card or {}).get("name") or signal.get("raw_title") or "")
    )
    if signal.get("pack_signal"):
        return f"complete-season-{suffix}", "Complete Season"
    if signal.get("part") is not None:
        # A Part is not an Episode, and the label says Part so that nobody
        # downstream can read it as one. The number stays in the label, where
        # it is a Part number, and out of the key, where it would be read as an
        # episode number.
        return f"part-{_alpha_token(str(signal['part']), 3)}-{suffix}", \
            f"Part {signal['part']:02d}"
    return f"unspecified-{suffix}", "Unspecified"


def _link_record(link: Dict[str, Any]) -> Dict[str, Any]:
    """One playback source, in the shape `series._source` reads.

    Every field the movie card carried about how to play this stream is passed
    through. Dropping `header_profile` or `requires_headers` here would produce
    an episode that exists and does not play, which is a subtler way of losing
    a stream than deleting it.
    """
    record: Dict[str, Any] = {"url": str(link.get("url") or "").strip()}
    for field in (
        "label", "resolution", "resolution_height", "header_profile",
        "stream_type", "requires_headers", "inherit_manifest_query",
        "codec", "language", "provider", "headers",
    ):
        if link.get(field) not in (None, ""):
            record[field] = link[field]
    record.setdefault("label", str(link.get("resolution") or "HD"))
    return record


def episode_record(card: Dict[str, Any], signal: Dict[str, Any]) -> Dict[str, Any]:
    """One staging episode, carrying every link the movie card had."""
    key, label = _episode_key_and_label(signal, card)
    links = [_link_record(link) for link in card_links(card)]
    record: Dict[str, Any] = {
        "episode_key": key,
        "episode_label": label,
        "links": links,
        "enabled": True,
        # Provenance, so a card that moved can always be traced back to the
        # movie id it was published under before ধাপ ৩খ ran.
        "migrated_from_movie_id": str(card.get("id") or ""),
        "migrated_from_title": str(card.get("name") or ""),
        "series_evidence_tier": str(signal.get("evidence_tier") or ""),
        # Explicit, because "no episode number" and "episode number 0" must
        # never be confusable downstream.
        "episode_number_stated": signal.get("episode") is not None,
    }
    if card.get("logo"):
        record["logo"] = card["logo"]
    return record


def published_show_categories(data_root: str | Path = "data") -> Dict[str, str]:
    """`show_key -> the category a show is already published under`.

    The ধাপ ৩ক rehearsal found 15 of the 114 proposed shows already live as
    series, and the merge that would fold a migrated show into one of them
    keys on `(show_key, category)`. Resort's episodes are all filed under Mix
    today while the show itself is published under Premium, so without this the
    migration would create a *third* Resort card instead of joining the one
    that exists - the under-merge the rehearsal exists to catch.

    Where a show is published under several categories - nine are, because
    Premium is a cross-cutting shelf - the owner's own precedence picks one.
    That leaves the existing duplication exactly as it is; consolidating it is
    ধাপ ১১'s question, not this step's.
    """
    try:
        from scanner.series_signal import detect, show_key
    except Exception:  # noqa: BLE001
        return {}

    def key_for(name: str) -> str:
        """The show key of a published series, season marker and all.

        A published name is not always clean. This catalogue has a series
        literally named "Dirilis Ertugrul (Season 1" - a truncated line from
        the private TXT source, unbalanced bracket and season marker included -
        which keys as `show:dirilis-ertugrul-season-1` and would therefore fail
        to match the migrated `show:dirilis-ertugrul`, quietly creating the
        second card this function exists to prevent. Running the name through
        the same detector the movie side uses strips the marker from both.
        """
        signal = detect(name)
        return show_key(signal.get("base_show") or name)

    priority = _category_priority()
    found: Dict[str, str] = {}
    root = Path(data_root) / "series"
    if not root.is_dir():
        return found
    for path in sorted(root.rglob("index.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        items = payload.get("items") or payload.get("series") or []
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            key = key_for(str(item.get("name") or ""))
            category = str(item.get("category") or "")
            if not key or not category:
                continue
            current = found.get(key)
            if current is None or priority.get(category, 99) < priority.get(current, 99):
                found[key] = category
    return found


def plan_migration(
    movies: Iterable[Dict[str, Any]],
    signals: Iterable[Dict[str, Any]],
    existing_show_categories: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Split the catalogue into what stays and what becomes a show.

    Returns the staging records rather than writing anything, so the caller -
    and a test - can inspect the whole decision before a single card moves.
    """
    existing = dict(existing_show_categories or {})
    staying: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    shows: Dict[str, Dict[str, Any]] = {}

    for card, signal in zip(movies, signals):
        if not isinstance(card, dict):
            continue
        if not signal.get("moves_to_series"):
            staying.append(card)
            continue

        key = str(signal.get("show_key") or "")
        if not key:
            staying.append(card)
            skipped.append({
                "id": card.get("id"), "name": card.get("name"),
                "reason": SKIP_NO_SHOW_KEY,
            })
            continue
        if not card_links(card):
            staying.append(card)
            skipped.append({
                "id": card.get("id"), "name": card.get("name"),
                "reason": SKIP_NO_PLAYABLE_LINK,
            })
            continue
        if not survives_series_pipeline(card):
            staying.append(card)
            skipped.append({
                "id": card.get("id"), "name": card.get("name"),
                "reason": SKIP_BELOW_MIN_HEIGHT,
            })
            continue

        show = shows.setdefault(key, {
            "show_key": key,
            "title": str(signal.get("base_show") or card.get("name") or ""),
            "categories": Counter(),
            "years": Counter(),
            "seasons": {},
            "logo": "",
            "cards": 0,
        })
        show["categories"][str(card.get("category") or "Mix")] += 1
        if signal.get("year"):
            show["years"][int(signal["year"])] += 1
        if not show["logo"] and card.get("logo"):
            show["logo"] = card["logo"]
        season = int(signal.get("season") or 1)
        record = episode_record(card, signal)
        bucket = show["seasons"].setdefault(season, {})
        already = bucket.get(record["episode_key"])
        if already is None:
            bucket[record["episode_key"]] = record
        else:
            # Two cards for the same numbered episode of the same show are one
            # episode with two sources, not two episodes. Merging their links
            # here is what stops `series._normalize_series` dropping the second
            # one outright - it keeps "the first spelling", which for a genuine
            # duplicate is right and for two different uploads loses a stream.
            known = {link["url"] for link in already["links"]}
            for link in record["links"]:
                if link["url"] not in known:
                    already["links"].append(link)
                    known.add(link["url"])
            already.setdefault("merged_from_movie_ids", []).append(
                record["migrated_from_movie_id"])
        show["cards"] += 1

    items: List[Dict[str, Any]] = []
    for key in sorted(shows):
        show = shows[key]
        # A show the site already publishes keeps that show's category, so the
        # migrated episodes join it instead of starting a second card beside
        # it. Only when the show is genuinely new does the episodes' own
        # category precedence decide.
        category = existing.get(key) or choose_category(show["categories"])
        # The year is display metadata only. It is deliberately NOT part of the
        # show's identity (ধারা ৪.৬) - keeping it there is what currently has
        # Star Trek: Strange New Worlds published as four separate cards.
        year = min(show["years"]) if show["years"] else 0
        items.append({
            "name": show["title"],
            "category": category,
            "year": year,
            "logo": show["logo"],
            "enabled": True,
            "show_key": key,
            "migrated_from_movies": True,
            "seasons": [
                {
                    "number": number,
                    "episodes": [
                        show["seasons"][number][key]
                        for key in sorted(show["seasons"][number])
                    ],
                }
                for number in sorted(show["seasons"])
            ],
        })

    return {
        "staying": staying,
        "series_items": items,
        "skipped": skipped,
        "summary": {
            "movie_cards_in": len(staying) + sum(
                show["cards"] for show in shows.values()),
            "movie_cards_staying": len(staying),
            "cards_migrated": sum(show["cards"] for show in shows.values()),
            "shows_created": len(items),
            "skipped": len(skipped),
            "episodes_created": sum(
                len(bucket)
                for show in shows.values()
                for bucket in show["seasons"].values()
            ),
            "streams_migrated": sum(
                len(episode["links"])
                for show in shows.values()
                for bucket in show["seasons"].values()
                for episode in bucket.values()
            ),
        },
    }


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


def stage_into_catalog(
    series_items: List[Dict[str, Any]],
    catalog_path: str | Path,
) -> int:
    """Append migrated shows to the staging catalogue the series step reads.

    Appended rather than published directly, so the migrated shows go through
    exactly the same normalisation, ordering and merge as the private
    catalogue's own - including `_merge_duplicate_series`, which is what folds
    a migrated show into the 15 that the site already publishes instead of
    creating a second card for it.

    A previous run's migrated records are replaced rather than added to, so
    running twice cannot double a show's episodes.
    """
    path = Path(catalog_path)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    existing = payload.get("items")
    if not isinstance(existing, list):
        existing = []
    kept = [
        item for item in existing
        if not (isinstance(item, dict) and item.get("migrated_from_movies"))
    ]

    payload["items"] = kept + list(series_items)
    payload["count"] = len(payload["items"])
    payload["migrated_show_count"] = len(series_items)
    _atomic_write(path, payload)
    return len(payload["items"])
