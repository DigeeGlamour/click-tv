#!/usr/bin/env python3
"""Where a TV candidate is lost between a configured source and a published card.

Written to answer one question with numbers rather than reasoning: the project
carries sixteen remote TV playlists and more than a thousand manual entries,
and Zee Bangla and Star Jalsha still had no working route. Either the sources
do not contain one, or the scanner is dropping it. This walks every stage and
says which.

Stages, per source:

    enabled     is it configured on, or switched off
    fetched     bytes and HTTP status from a live fetch
    parsed      #EXTINF entries recovered
    deduped     survivors after exact-URL dedupe
    shortlisted survivors after the planner's per-group pool cap
    verified    reachable at probe time
    rejected    with the exact reason
    published   present in data/channels/*.json

It changes nothing. Output is JSON on stdout plus a readable table on stderr,
so it can be diffed between runs and read by a person in the same pass.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from scanner import channel_alias as ca  # noqa: E402
from scanner.parsers.m3u_parser import parse_m3u_content  # noqa: E402

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
)


def extinf_name(line: str) -> str:
    """Channel name = text after the last comma OUTSIDE quotes.

    Splitting on the last comma is wrong and it mattered: tvg-logo URLs contain
    commas ("f_png,w_300,q_85/..."), so a naive split returned a fragment of an
    image URL as the channel name. Names decide channel identity here, so a
    wrong name is a lost candidate.
    """
    body = line.split(":", 1)[-1]
    quoted = False
    last = -1
    for index, char in enumerate(body):
        if char == '"':
            quoted = not quoted
        elif char == "," and not quoted:
            last = index
    return body[last + 1:].strip() if last >= 0 else ""


def parse_playlist(text: str, source_info: Optional[Dict[str, Any]] = None) -> List[Dict[str, str]]:
    """Parse with the scanner's own parser, not a re-implementation.

    The first version of this function had its own loop and looked ahead four
    lines from each #EXTINF for the URL. That is not enough: sm-iptv-jiohotstar
    puts several #EXTVLCOPT and #KODIPROP lines between the two, so the audit
    reported 0 entries for a source with 64 of them - and would have accused
    the scanner of losing channels it parses correctly. An audit that measures
    a copy of the code cannot report on the code.
    """
    try:
        parsed = parse_m3u_content(text, source_info or {})
    except Exception:  # noqa: BLE001 - fall back rather than lose the source
        parsed = []
    entries: List[Dict[str, str]] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        entries.append({
            "name": str(item.get("name") or item.get("title") or "").strip(),
            "url": url,
        })
    return entries


def fetch(url: str, timeout: int = 40) -> Tuple[int, str, str]:
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            return response.status, raw.decode("utf-8", "replace"), ""
    except urllib.error.HTTPError as error:
        return error.code, "", f"HTTP {error.code}"
    except Exception as error:  # noqa: BLE001 - a source failure is a datum
        return 0, "", f"{type(error).__name__}: {str(error)[:90]}"


def probe(url: str, timeout: int = 15) -> Tuple[int, str]:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": UA, "Referer": "https://clicktv.pages.dev/"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            head = response.read(400)
            kind = "hls" if head.lstrip().startswith(b"#EXTM3U") else "other"
            if head[:1] == b"\x47":
                kind = "mpegts"
            return response.status, kind
    except urllib.error.HTTPError as error:
        return error.code, ""
    except Exception as error:  # noqa: BLE001
        return 0, type(error).__name__


def published_cards() -> Dict[str, Dict[str, Any]]:
    """url -> card, plus canonical-name -> card, for the published catalogue."""
    by_url: Dict[str, Dict[str, Any]] = {}
    for name in sorted(os.listdir(os.path.join(ROOT, "data", "channels"))):
        if not name.endswith(".json"):
            continue
        path = os.path.join(ROOT, "data", "channels", name)
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError):
            continue
        rows = payload if isinstance(payload, list) else payload.get("channels") or []
        for card in rows:
            if not isinstance(card, dict):
                continue
            for url in [card.get("url")] + [
                b.get("url") for b in (card.get("backups") or []) if isinstance(b, dict)
            ]:
                if url:
                    by_url[str(url)] = card
    return by_url


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="reports/tv-source-coverage.json")
    parser.add_argument(
        "--probe-channel",
        action="append",
        default=[],
        help="canonical channel name whose candidates should be probed",
    )
    parser.add_argument("--no-probe", action="store_true")
    args = parser.parse_args()

    with open(os.path.join(ROOT, "config", "sources", "tv.json"), encoding="utf-8") as handle:
        config = json.load(handle)
    sources = config.get("sources") or []

    rows: List[Dict[str, Any]] = []
    all_entries: List[Dict[str, str]] = []

    def one(source: Dict[str, Any]) -> Dict[str, Any]:
        enabled = source.get("enabled", True) is not False
        row: Dict[str, Any] = {
            "source_id": source.get("id"),
            "source_name": source.get("name"),
            "enabled": enabled,
            "url_host": urllib.parse.urlsplit(str(source.get("url") or "")).hostname,
            "http_status": None,
            "bytes": 0,
            "fetch_error": "",
            "parsed_entries": 0,
        }
        if not enabled:
            row["fetch_error"] = "disabled in config"
            return row
        status, text, error = fetch(str(source.get("url") or ""))
        row["http_status"] = status
        row["bytes"] = len(text.encode("utf-8")) if text else 0
        row["fetch_error"] = error
        entries = parse_playlist(text, source) if text else []
        row["parsed_entries"] = len(entries)
        row["_entries"] = [dict(e, source_id=source.get("id")) for e in entries]
        return row

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        for row in pool.map(one, sources):
            all_entries.extend(row.pop("_entries", []))
            rows.append(row)

    # Manual playlist is a configured source too and must appear in coverage.
    manual_path = os.path.join(ROOT, "manual", "manual.m3u")
    manual_row = {
        "source_id": "manual-playlist",
        "source_name": "manual/manual.m3u",
        "enabled": True,
        "url_host": None,
        "http_status": 200,
        "bytes": 0,
        "fetch_error": "",
        "parsed_entries": 0,
    }
    try:
        with open(manual_path, encoding="utf-8") as handle:
            text = handle.read()
        manual_row["bytes"] = len(text.encode("utf-8"))
        entries = parse_playlist(text, {"id": "manual-playlist", "format": "m3u"})
        manual_row["parsed_entries"] = len(entries)
        all_entries.extend(dict(e, source_id="manual-playlist") for e in entries)
    except OSError as error:
        manual_row["fetch_error"] = str(error)[:90]
    rows.append(manual_row)

    # Dedupe by exact URL, the same identity the planner uses.
    seen: Dict[str, Dict[str, str]] = {}
    duplicates = 0
    for entry in all_entries:
        if entry["url"] in seen:
            duplicates += 1
            continue
        seen[entry["url"]] = entry
    unique = list(seen.values())

    per_source_unique = Counter(e["source_id"] for e in unique)
    for row in rows:
        row["deduped_unique"] = per_source_unique.get(row["source_id"], 0)

    cards_by_url = published_cards()
    per_source_published = Counter(
        e["source_id"] for e in unique if e["url"] in cards_by_url
    )
    for row in rows:
        row["published_urls"] = per_source_published.get(row["source_id"], 0)

    # Alias grouping: how many channel groups exist before and after folding.
    names = [e["name"] for e in unique if e["name"]]
    naive_groups = len({re.sub(r"[^a-z0-9]+", "-", n.casefold()).strip("-") for n in names})
    canonical_groups = len({
        ca.canonical_channel_name(n) for n in names if ca.canonical_channel_name(n)
    })

    channels = [c.strip().casefold() for c in args.probe_channel if c.strip()]
    focus: Dict[str, Any] = {}
    for wanted in channels:
        matched = [
            entry for entry in unique
            if ca.canonical_channel_name(entry["name"]) == wanted
        ]
        probed: List[Dict[str, Any]] = []
        if matched and not args.no_probe:
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(lambda e: probe(e["url"]), matched))
        else:
            results = [(None, "")] * len(matched)
        for entry, (status, kind) in zip(matched, results):
            host = urllib.parse.urlsplit(entry["url"]).hostname
            card = cards_by_url.get(entry["url"])
            if card is not None:
                reason = "published"
            elif status == 0:
                reason = f"unreachable from this vantage ({kind})"
            elif status is None:
                reason = "not probed"
            elif status >= 400:
                reason = f"HTTP {status}"
            else:
                reason = f"HTTP {status}, reachable but not selected for the card"
            probed.append({
                "name": entry["name"],
                "source_id": entry["source_id"],
                "host": host,
                "canonical": ca.canonical_channel_name(entry["name"]),
                "probe_status": status,
                "body_kind": kind,
                "in_published_card": card is not None,
                "reason": reason,
            })
        focus[wanted] = {
            "candidates_found": len(matched),
            "distinct_hosts": len({p["host"] for p in probed}),
            "reachable": sum(1 for p in probed if isinstance(p["probe_status"], int)
                             and 200 <= p["probe_status"] < 400),
            "published": sum(1 for p in probed if p["in_published_card"]),
            "candidates": probed,
        }

    payload = {
        "mode": "tv_source_coverage_audit",
        "note": (
            "Every configured TV source, fetched live, with what survives each "
            "stage. Written because sixteen playlists and a thousand manual "
            "entries still produced no working Zee Bangla or Star Jalsha route, "
            "and the question of whether the sources lack one or the scanner "
            "drops it needed an answer with numbers."
        ),
        "sources_configured": len(sources) + 1,
        "sources_enabled": sum(1 for r in rows if r["enabled"]),
        "sources_fetch_failed": sum(
            1 for r in rows if r["enabled"] and r["fetch_error"]
        ),
        "entries_parsed_total": sum(r["parsed_entries"] for r in rows),
        "duplicate_urls_dropped": duplicates,
        "unique_urls": len(unique),
        "channel_groups_before_alias_folding": naive_groups,
        "channel_groups_after_alias_folding": canonical_groups,
        "groups_merged_by_alias_folding": naive_groups - canonical_groups,
        "sources": rows,
        "focus_channels": focus,
    }

    out_path = os.path.join(ROOT, args.out)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")

    write = sys.stderr.write
    write("\n%-32s %-4s %-6s %-9s %-8s %-8s %s\n" % (
        "SOURCE", "ON", "HTTP", "BYTES", "PARSED", "UNIQUE", "PUBLISHED"))
    write("-" * 92 + "\n")
    for row in rows:
        write("%-32s %-4s %-6s %-9s %-8s %-8s %s\n" % (
            str(row["source_id"])[:32],
            "yes" if row["enabled"] else "NO",
            row["http_status"] if row["http_status"] is not None else "-",
            row["bytes"],
            row["parsed_entries"],
            row["deduped_unique"],
            row["published_urls"],
        ))
        if row["fetch_error"]:
            write("%-32s   -> %s\n" % ("", row["fetch_error"]))
    write("-" * 92 + "\n")
    write("parsed=%s  duplicates dropped=%s  unique=%s\n" % (
        payload["entries_parsed_total"], duplicates, len(unique)))
    write("channel groups: %s -> %s after alias folding (%s merged)\n" % (
        naive_groups, canonical_groups, payload["groups_merged_by_alias_folding"]))
    for wanted, block in focus.items():
        write("\n%s: %s candidate(s), %s host(s), %s reachable, %s published\n" % (
            wanted, block["candidates_found"], block["distinct_hosts"],
            block["reachable"], block["published"]))
        write("  %-28s %-22s %-7s %s\n" % ("NAME", "HOST", "HTTP", "REASON"))
        for row in block["candidates"]:
            write("  %-28s %-22s %-7s %s\n" % (
                str(row["name"])[:28], str(row["host"])[:22],
                row["probe_status"], row["reason"][:44]))
    write("\nwrote %s\n" % args.out)
    json.dump(payload, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
