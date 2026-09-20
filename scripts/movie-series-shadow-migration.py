#!/usr/bin/env python3
"""ধাপ ৩ক - the mandatory rehearsal before any card is rebuilt.

    python scripts/movie-series-shadow-migration.py --print

Writes exactly one file, `reports/movie-series-migration-dryrun.json`, and
touches nothing else. No catalogue is rewritten, no state is updated, no card
moves. ধাপ ৩খ does not begin until the stream-coverage identity in this report
balances:

    before_stream_count = after_movie_streams
                        + after_series_streams
                        + merged_backups
                        + pending_visible_streams

The four terms are a partition of the links the site serves today, which is
what makes the equation worth anything: every link is in exactly one of them,
so a link that would go missing during the real migration has nowhere to hide.

The report also carries the two warnings ধাপ ৩ক asks for:

  over-merge   one proposed show that is really several - "The Office" US and
               UK are different shows with one name. Reported, never acted on:
               a year is NOT evidence of a different show (House of the Dragon
               is 2022, 2024 and 2026 here), so this list is for a person.
  under-merge  one show left in two cards - a proposed show that a published
               series already covers, or two proposed shows where one key is a
               prefix of the other.

Read-only. Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import movie_baseline, movie_coverage, series_signal  # noqa: E402

REPORT_PATH = Path("reports") / "movie-series-migration-dryrun.json"


def _links(card: Dict[str, Any]) -> int:
    count = 1 if str(card.get("url") or "").strip() else 0
    for backup in card.get("backups") or ():
        url = backup.get("url") if isinstance(backup, dict) else backup
        if str(url or "").strip():
            count += 1
    return count


def _backup_links(card: Dict[str, Any]) -> int:
    return max(0, _links(card) - (1 if str(card.get("url") or "").strip() else 0))


def _published_show_keys(root: Path) -> Dict[str, str]:
    """`show_key -> the series file it lives in`, for series already published.

    A proposed show that one of these already covers is an under-merge: the
    migration would create a second card for a show the site already has.
    """
    found: Dict[str, str] = {}
    base = root / "data" / "series"
    for path in sorted(base.rglob("index.json")) if base.is_dir() else ():
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
            name = str(item.get("name") or item.get("title") or "")
            key = series_signal.show_key(
                series_signal.detect(name).get("base_show") or name)
            if key:
                found.setdefault(key, path.relative_to(root).as_posix())
    return found


def build(root: Path) -> Dict[str, Any]:
    cards = movie_baseline.published_movies(root)
    signals = series_signal.classify_rows(
        [series_signal.detect(card.get("name")) for _, card in cards]
    )

    before_stream_count = 0
    after_movie_streams = 0
    after_series_streams = 0
    merged_backups = 0
    pending_visible_streams = 0

    shows: Dict[str, Dict[str, Any]] = {}
    unkeyed: List[Dict[str, Any]] = []
    pending: List[Dict[str, Any]] = []

    for (category, card), signal in zip(cards, signals):
        links = _links(card)
        before_stream_count += links
        name = str(card.get("name") or "")
        identity = str(card.get("id") or "")

        if not signal.get("is_series_signal"):
            after_movie_streams += links
            continue

        if signal.get("classification_pending"):
            # ধারা ৪.৬ tier 3. Stays exactly where it is - a visible movie card
            # - until evidence arrives. Counted on its own so the equation
            # cannot hide it inside the ordinary movie total.
            pending_visible_streams += links
            pending.append({
                "id": identity, "name": name, "category": category,
                "season": signal.get("season"), "links": links,
                "show_key": signal.get("show_key"),
                "reason": "no_evidence_of_series_beyond_a_season_marker",
            })
            continue

        key = signal.get("show_key") or ""
        if not key:
            # A marker with no show name in front of it cannot be grouped, and
            # guessing a group for it would be the over-merge this step exists
            # to catch. It stays a movie card.
            after_movie_streams += links
            unkeyed.append({"id": identity, "name": name, "links": links})
            continue

        primary = 1 if str(card.get("url") or "").strip() else 0
        after_series_streams += primary
        merged_backups += _backup_links(card)

        show = shows.setdefault(key, {
            "show_key": key,
            "proposed_show_title": signal.get("base_show"),
            "categories": set(),
            "years_seen": set(),
            "seasons": set(),
            "cards": [],
            "episode_streams": 0,
            "backup_streams": 0,
        })
        show["categories"].add(category)
        if signal.get("year"):
            show["years_seen"].add(int(signal["year"]))
        if signal.get("season") is not None:
            show["seasons"].add(int(signal["season"]))
        show["episode_streams"] += primary
        show["backup_streams"] += _backup_links(card)
        show["cards"].append({
            "id": identity,
            "name": name,
            "category": category,
            "season": signal.get("season"),
            "episode": signal.get("episode"),
            "episode_end": signal.get("episode_end"),
            "part": signal.get("part"),
            "evidence_tier": signal.get("evidence_tier"),
            "detected_pattern": signal.get("detected_pattern"),
            "season_label": series_signal.season_label(signal),
            "links": links,
        })

    show_rows: List[Dict[str, Any]] = []
    for key in sorted(shows):
        show = shows[key]
        show_rows.append({
            **{field: show[field] for field in (
                "show_key", "proposed_show_title", "episode_streams",
                "backup_streams")},
            "categories": sorted(show["categories"]),
            "years_seen": sorted(show["years_seen"]),
            "seasons": sorted(show["seasons"]),
            "cards_merged": len(show["cards"]),
            "cards": sorted(
                show["cards"],
                key=lambda entry: (
                    entry["season"] if entry["season"] is not None else 99,
                    entry["episode"] if entry["episode"] is not None else 999,
                    entry["name"],
                ),
            ),
        })

    # --- the two warnings ধাপ ৩ক asks for ------------------------------

    over_merge = [
        {
            "show_key": row["show_key"],
            "proposed_show_title": row["proposed_show_title"],
            "years_seen": row["years_seen"],
            "cards_merged": row["cards_merged"],
            "note": (
                "several release years under one show key. A year is NOT "
                "evidence of a different show - House of the Dragon is 2022, "
                "2024 and 2026 in this catalogue and is one show - so this "
                "needs a person, not a rule."
            ),
            "sample_titles": [card["name"] for card in row["cards"][:4]],
        }
        for row in show_rows
        if len(row["years_seen"]) > 1
    ]

    published = _published_show_keys(root)
    under_merge: List[Dict[str, Any]] = []
    for row in show_rows:
        if row["show_key"] in published:
            under_merge.append({
                "show_key": row["show_key"],
                "proposed_show_title": row["proposed_show_title"],
                "kind": "already_published_as_a_series",
                "existing": published[row["show_key"]],
                "note": "the migration would create a second card for a show "
                        "the site already publishes",
            })
    keys = [row["show_key"] for row in show_rows]
    for key in keys:
        for other in keys:
            if key != other and other.startswith(key + "-"):
                under_merge.append({
                    "show_key": key,
                    "kind": "prefix_of_another_proposed_show",
                    "other_show_key": other,
                    "note": "one show may be split across two keys",
                })

    identity_total = (
        after_movie_streams + after_series_streams
        + merged_backups + pending_visible_streams
    )
    balanced = identity_total == before_stream_count

    tiers: Dict[str, int] = defaultdict(int)
    for signal in signals:
        if signal.get("is_series_signal"):
            tiers[str(signal.get("evidence_tier"))] += 1

    return {
        "schema_version": 1,
        "kind": "movie_series_migration_dryrun",
        "generated_at": movie_coverage._utc_now(),
        "note": (
            "ধাপ ৩ক shadow migration. Nothing was published, moved or "
            "rewritten. ধাপ ৩খ may not start unless stream_coverage.balanced "
            "is true."
        ),
        "classifier_version": series_signal.CLASSIFIER_VERSION,
        "catalogue_before": {
            "movie_cards": len(cards),
            "stream_count": before_stream_count,
        },
        "catalogue_after_proposed": {
            "movie_cards": len(cards) - sum(
                row["cards_merged"] for row in show_rows),
            "series_shows_created": len(show_rows),
            "cards_absorbed_into_series": sum(
                row["cards_merged"] for row in show_rows),
            "cards_left_pending_as_movies": len(pending),
        },
        "stream_coverage": {
            "before_stream_count": before_stream_count,
            "after_movie_streams": after_movie_streams,
            "after_series_streams": after_series_streams,
            "merged_backups": merged_backups,
            "pending_visible_streams": pending_visible_streams,
            "sum_of_terms": identity_total,
            "balanced": balanced,
            "difference": before_stream_count - identity_total,
        },
        "evidence_tiers": dict(sorted(tiers.items())),
        "over_merge_warnings": over_merge,
        "under_merge_warnings": under_merge,
        "unkeyed_series_signals": unkeyed,
        "pending_as_movie_cards": pending,
        "proposed_shows": show_rows,
    }


def format_report(report: Dict[str, Any]) -> str:
    coverage = report["stream_coverage"]
    after = report["catalogue_after_proposed"]
    lines = [
        "MOVIE -> SERIES SHADOW MIGRATION  (dry run, nothing published)",
        "",
        "  movie cards now              %6d" % report["catalogue_before"]["movie_cards"],
        "  movie cards after            %6d" % after["movie_cards"],
        "  series shows created         %6d" % after["series_shows_created"],
        "  cards absorbed               %6d" % after["cards_absorbed_into_series"],
        "  cards left pending as movies %6d" % after["cards_left_pending_as_movies"],
        "",
        "  evidence tiers               %s" % report["evidence_tiers"],
        "",
        "STREAM COVERAGE",
        "  before_stream_count          %6d" % coverage["before_stream_count"],
        "    after_movie_streams        %6d" % coverage["after_movie_streams"],
        "    after_series_streams       %6d" % coverage["after_series_streams"],
        "    merged_backups             %6d" % coverage["merged_backups"],
        "    pending_visible_streams    %6d" % coverage["pending_visible_streams"],
        "  sum of terms                 %6d" % coverage["sum_of_terms"],
        "  BALANCED                     %6s" % coverage["balanced"],
        "",
        "  over-merge warnings          %6d" % len(report["over_merge_warnings"]),
        "  under-merge warnings         %6d" % len(report["under_merge_warnings"]),
        "  unkeyed series signals       %6d" % len(report["unkeyed_series_signals"]),
    ]
    largest = sorted(
        report["proposed_shows"], key=lambda row: -row["cards_merged"])[:6]
    if largest:
        lines.append("")
        lines.append("LARGEST PROPOSED SHOWS")
        for row in largest:
            lines.append(
                "  %-42s %3d cards -> 1" % (
                    row["proposed_show_title"][:42], row["cards_merged"]))
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--out", default=str(REPORT_PATH))
    parser.add_argument("--print", dest="show", action="store_true")
    parser.add_argument(
        "--require-balanced", action="store_true",
        help="Exit non-zero unless the stream coverage identity balances, "
             "which is the gate ধাপ ৩খ has to pass.",
    )
    arguments = parser.parse_args()

    root = Path(arguments.root)
    report = build(root)

    out = Path(arguments.out)
    if not out.is_absolute():
        out = root / out
    movie_coverage._atomic_write(out, report)
    print(f"   wrote {out}", file=sys.stderr)

    if arguments.show:
        print(format_report(report))

    if not report["stream_coverage"]["balanced"]:
        print(
            "\nSTREAM COVERAGE DOES NOT BALANCE - ধাপ ৩খ must not start.",
            file=sys.stderr,
        )
        return 1 if arguments.require_balanced else 0
    print("\nStream coverage balances.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
