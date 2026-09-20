#!/usr/bin/env python3
"""ধাপ ১ - produce reports/movie-source-coverage.json outside a scan.

    python scripts/movie-coverage-audit.py                 # fetch sources live
    python scripts/movie-coverage-audit.py --offline       # catalogue only
    python scripts/movie-coverage-audit.py --print

Two different questions, and this script is honest about which of them it can
answer without a scan.

INVARIANT ২ - live preservation - is fully answerable here. It compares the
stream inventory ধাপ ০ recorded against the catalogue on disk now, and both of
those are committed files. `unexplained_live_loss` computed here is the real
number, not an estimate.

INVARIANT ১ - ingestion accounting - is a *within-run* measurement. A feed
changes between runs, so entries fetched today are not the entries the last
publish saw. This script therefore fetches what it can and records, per source
row, how its evidence was obtained:

    live_fetch        the source was fetched now and parsed with the real parser
    cached_snapshot   read from state/manual-movie-remote-cache.json
    unavailable       neither was possible; the row says so rather than
                      reporting zero as though it meant "nothing there"

The authoritative INVARIANT ১ is the one `scan.py` writes during a real run,
where the entries and the publish belong to the same moment.

Reads and fetches. Writes exactly one file: the report.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import movie_baseline  # noqa: E402
from scanner import movie_coverage  # noqa: E402

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
)

PRIVATE_CACHE = Path("state") / "manual-movie-remote-cache.json"
PRIVATE_CONFIG = Path("manual") / "movie-sources.json"


def _load_json(path: Path) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def configured_movie_sources(root: Path) -> List[Dict[str, Any]]:
    """Every source a movie scan is supposed to read, public and private.

    Read from the same files the loader reads. Deriving the list from what
    already contributed something is how a source that returned nothing becomes
    invisible to the report whose job is to say that it returned nothing -
    `scanner/source_coverage.py` carries the same note for Today Match, where
    ten of twenty-one configured sources were missing from the report.
    """
    sources: List[Dict[str, Any]] = []

    try:
        from scanner.source_loader import load_sources_config

        merged = load_sources_config(root / "config") or {}
    except Exception:  # noqa: BLE001 - fall back to the file itself
        merged = {}
    entries = merged.get("movies")
    if not isinstance(entries, list):
        payload = _load_json(root / "config" / "sources" / "movies.json") or {}
        entries = payload.get("sources") if isinstance(payload, dict) else []
    for entry in entries or ():
        if not isinstance(entry, dict):
            continue
        sources.append({
            "id": str(entry.get("id") or ""),
            "name": str(entry.get("name") or entry.get("id") or ""),
            "enabled": bool(entry.get("enabled", True)),
            "url": str(entry.get("url") or ""),
            "kind": "public_playlist",
            "priority": entry.get("priority"),
            "default_category": entry.get("default_category"),
        })

    private = _load_json(root / PRIVATE_CONFIG) or {}
    private_enabled = bool(private.get("enabled", True))
    for key in ("repository_sources", "directory_sources", "sources"):
        for entry in private.get(key) or ():
            if not isinstance(entry, dict):
                continue
            sources.append({
                "id": str(entry.get("id") or ""),
                "name": str(entry.get("name") or entry.get("id") or ""),
                "enabled": private_enabled and bool(entry.get("enabled", True)),
                "url": str(entry.get("repository") or entry.get("url") or ""),
                "kind": "private_" + key.replace("_sources", ""),
            })

    return [source for source in sources if source["id"]]


def fetch_public_entries(source: Dict[str, Any], timeout: int) -> Tuple[List[Dict[str, Any]], str]:
    """`(entries, evidence)` for one public playlist, fetched now."""
    url = source.get("url") or ""
    if not url:
        return [], "unavailable"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read()
    except (urllib.error.URLError, OSError, ValueError) as error:
        print(f"   {source['id']}: fetch failed ({error})", file=sys.stderr)
        return [], "unavailable"

    text = payload.decode("utf-8", errors="replace")
    try:
        from scanner.parsers.m3u_parser import parse_m3u_content

        parsed = parse_m3u_content(text, {
            "id": source["id"],
            "name": source.get("name") or source["id"],
            "url": url,
            "pipeline": "movies",
            "priority": source.get("priority", 100),
        })
    except Exception as error:  # noqa: BLE001
        print(f"   {source['id']}: parse failed ({error})", file=sys.stderr)
        return [], "unavailable"

    entries = []
    for item in parsed or ():
        if not isinstance(item, dict):
            continue
        record = dict(item)
        record.setdefault("source_id", source["id"])
        record.setdefault("source_pipeline", "movies")
        record.setdefault("category", source.get("default_category") or "")
        entries.append(record)
    return entries, "live_fetch"


def cached_private_entries(root: Path, source_id: str) -> Tuple[List[Dict[str, Any]], str]:
    """Entries for a private source, from the snapshot committed in state/.

    The private repository needs `PRIVATE_MOVIE_SOURCE_TOKEN`, which an audit
    run does not have, so the committed snapshot is the only evidence
    available. It is named as a snapshot in the row rather than presented as a
    live read, because it can be older than the last publish.

    The reading itself lives in `scanner/movie_coverage` so that the scan and
    this audit cannot disagree about what a private entry is - they read the
    same file the same way.
    """
    entries = movie_coverage.private_snapshot_entries(root, source_id)
    if not entries:
        return [], "unavailable"
    return entries, "cached_snapshot"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument(
        "--out", default=str(movie_coverage.REPORT_FILE),
        help="Where the report is written, relative to --root.",
    )
    parser.add_argument(
        "--offline", action="store_true",
        help="Do not fetch. INVARIANT ২ is still authoritative; INVARIANT ১ "
             "reports its sources as unavailable rather than guessing.",
    )
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument(
        "--print", dest="show", action="store_true",
        help="Also print the readable summary.",
    )
    parser.add_argument(
        "--require-pass", action="store_true",
        help="Exit non-zero if the gate would block a publish.",
    )
    arguments = parser.parse_args()

    root = Path(arguments.root)

    sources = configured_movie_sources(root)
    print(f"   configured movie sources: {len(sources)}", file=sys.stderr)

    raw_entries: List[Dict[str, Any]] = []
    evidence_by_source: Dict[str, str] = {}
    for source in sources:
        if not source["enabled"]:
            evidence_by_source[source["id"]] = "disabled"
            continue
        if source["kind"] == "public_playlist" and not arguments.offline:
            entries, evidence = fetch_public_entries(source, arguments.timeout)
        elif source["kind"] == "public_playlist":
            entries, evidence = [], "unavailable"
        else:
            entries, evidence = cached_private_entries(root, source["id"])
        evidence_by_source[source["id"]] = evidence
        raw_entries.extend(entries)
        print(
            f"   {source['id']}: {len(entries)} entr(ies) [{evidence}]",
            file=sys.stderr,
        )

    movies = movie_baseline.published_movies(root)
    episodes = movie_baseline.published_episodes(root)
    current = movie_baseline.stream_inventory(root)

    previous: List[str] = []
    try:
        baseline = movie_baseline.load_baseline(root=root)
        previous = movie_baseline.recorded_inventory(baseline)
    except (OSError, ValueError) as error:
        print(f"   no usable baseline inventory: {error}", file=sys.stderr)

    report = movie_coverage.build_movie_coverage(
        configured_sources=sources,
        raw_entries=raw_entries,
        published_movies=movies,
        published_episodes=episodes,
        previous_inventory=previous,
        current_inventory=current,
        evidence="audit",
    )

    # Say per row how the entries behind it were obtained, so a zero that means
    # "not reachable" is never read as a zero that means "nothing there".
    for row in report["sources"]:
        row["ingestion_evidence"] = evidence_by_source.get(
            row["source_id"], "unavailable")
    report["ingestion_evidence"] = {
        "note": (
            "INVARIANT ২ is authoritative here - it compares two committed "
            "catalogues. INVARIANT ১ is a within-run measurement; a feed moves "
            "between runs, so these rows describe the sources as they are now, "
            "not as the last publish saw them."
        ),
        "by_source": evidence_by_source,
        "baseline_inventory_lines": len(previous),
        "current_inventory_lines": len(current),
    }

    out = Path(arguments.out)
    if not out.is_absolute():
        out = root / out
    movie_coverage.write_movie_coverage(report, out)
    print(f"   wrote {out}", file=sys.stderr)

    if arguments.show:
        print(movie_coverage.format_movie_coverage(report))

    blocked, reasons = movie_coverage.publish_blocked(report)
    if blocked:
        print("\nGATE WOULD BLOCK:", file=sys.stderr)
        for reason in reasons:
            print(f"  - {reason}", file=sys.stderr)
        return 1 if arguments.require_pass else 0
    print("\nGate passes: both invariants hold.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
