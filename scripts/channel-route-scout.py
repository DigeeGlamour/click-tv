#!/usr/bin/env python3
"""Ask every configured TV source what routes it holds for named channels, and probe them.

Written because Zee Bangla's published card had exactly one route and that route
answered HTTP 500 from Bangladesh on 2026-08-29, so the card was live on the site
with nothing behind it. The question "does any configured source still carry this
channel" had no cheap answer: the scanner knows, but only inside a full scan.

What this does and does not do:

  * It reads the same source list the scanner reads - config/sources/tv.json plus
    the manual playlist - so a candidate it finds is one the scanner could
    legitimately publish, not something invented here.
  * It applies the owner's channel-identity rule. "Zee Bangla HD" is another
    source of Zee Bangla. "Zee Bangla Cinema" and "Zee Bangla Sonar" are
    different channels and are never offered as substitutes.
  * It probes each candidate once and records the status. A probe is
    reachability, nothing more: HTTP 200 with a parseable manifest is exactly
    what every hidden channel's primary also returns. Promotion still needs two
    120 s browser sessions, which is a separate step.
  * It writes one report and changes no catalogue file.

Usage:
    python scripts/channel-route-scout.py --channel "Zee Bangla" \
        --channel "Star Jalsha" --out reports/channel-route-scout.json
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Iterable, List, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from scanner import route_evidence as rev  # noqa: E402

#: Words that make a name a DIFFERENT channel rather than another source of the
#: same one. Same list scripts/add-proven-route.py enforces; restated rather
#: than imported so this file cannot be run against a rule it has not stated.
DIFFERENT_CHANNEL_WORDS = (
    "cinema", "sonar", "sansar", "movies", "music", "natok", "cine", "gold",
)

#: "[BD] Zee Bangla" is Zee Bangla behind a Bangladeshi CDN, not another
#: channel. Leading bracketed tags are stripped before matching; the word check
#: above still runs on the untouched name, so no prefix smuggles Cinema past it.
GROUP_TAG = re.compile(r"^\s*(?:[\[(][^\])]*[\])]\s*)+")
QUALITY_SUFFIX = re.compile(r"\s*(hd|sd|fhd|uhd|4k|full\s*hd)\s*$", re.IGNORECASE)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
)


def canonical(name: str) -> str:
    text = GROUP_TAG.sub("", str(name or "")).strip()
    text = QUALITY_SUFFIX.sub("", text).strip()
    return re.sub(r"\s+", " ", text).casefold()


def same_channel(wanted: str, candidate: str) -> bool:
    """Whether `candidate` names another source of `wanted`."""
    raw = str(candidate or "").casefold()
    base = canonical(wanted)
    if any(word in raw for word in DIFFERENT_CHANNEL_WORDS):
        # Only a problem when the wanted channel does not itself carry the word:
        # "Jalsha Movies" is allowed to match "Jalsha Movies HD".
        if not any(word in base for word in DIFFERENT_CHANNEL_WORDS):
            return False
    return canonical(candidate) == base


def parse_m3u(text: str, source_id: str, source_name: str) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    current: Dict[str, Any] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#EXTINF"):
            group = re.search(r'group-title="([^"]*)"', line)
            current = {
                "name": line.split(",", 1)[1].strip() if "," in line else "",
                "group": group.group(1) if group else "",
                "props": [],
                "source_id": source_id,
                "source_name": source_name,
            }
        elif current and line.startswith("#"):
            current["props"].append(line)
        elif current and line:
            current["url"] = line
            entries.append(current)
            current = {}
    return entries


def headers_of(entry: Dict[str, Any]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for prop in entry.get("props") or ():
        if prop.startswith("#EXTHTTP:"):
            try:
                for key, value in json.loads(prop[len("#EXTHTTP:"):]).items():
                    out[str(key)] = str(value)
            except ValueError:
                pass
        elif prop.startswith("#EXTVLCOPT:http-user-agent="):
            out["User-Agent"] = prop.split("=", 1)[1]
        elif prop.startswith("#EXTVLCOPT:http-referrer="):
            out["Referer"] = prop.split("=", 1)[1]
        elif prop.startswith("#EXTVLCOPT:http-cookie="):
            out["Cookie"] = prop.split("=", 1)[1]
    out.setdefault("User-Agent", UA)
    return out


def fetch(url: str, timeout: int = 40) -> Tuple[int, bytes, str]:
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    try:
        response = urllib.request.urlopen(
            urllib.request.Request(url, headers={"User-Agent": UA}),
            timeout=timeout,
            context=context,
        )
        return response.status, response.read(), ""
    except urllib.error.HTTPError as error:
        return error.code, b"", str(error.reason)[:80]
    except Exception as error:  # noqa: BLE001 - a source failure is data here
        return 0, b"", "%s: %s" % (type(error).__name__, str(error)[:80])


def probe(entry: Dict[str, Any], timeout: int = 20) -> Dict[str, Any]:
    url = str(entry.get("url") or "").split("|", 1)[0]
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    started = time.time()
    try:
        response = urllib.request.urlopen(
            urllib.request.Request(url, headers=headers_of(entry)),
            timeout=timeout,
            context=context,
        )
        body = response.read(8192)
        status, error = response.status, ""
    except urllib.error.HTTPError as failure:
        body, status, error = b"", failure.code, str(failure.reason)[:60]
    except Exception as failure:  # noqa: BLE001
        body, status = b"", 0
        error = "%s: %s" % (type(failure).__name__, str(failure)[:70])
    head = body[:400].decode("utf-8", "replace")
    return {
        "name": entry.get("name"),
        "group": entry.get("group"),
        "source_id": entry.get("source_id"),
        "source_name": entry.get("source_name"),
        "url": url,
        "url_public_template": rev.redact_public_template(url),
        "http_status": status,
        "bytes": len(body),
        "seconds": round(time.time() - started, 2),
        "error": error,
        "looks_like": (
            "hls_master" if "#EXT-X-STREAM-INF" in head
            else "hls_media" if "#EXTINF" in head
            else "dash" if "<MPD" in head
            else "mpegts" if body[:1] == b"\x47"
            else ""
        ),
        "requires_headers": len(headers_of(entry)) > 1,
        "drm": any("license_key" in p or "clearkey" in p.lower()
                   for p in (entry.get("props") or ())),
    }


def sources() -> Iterable[Tuple[str, str, str]]:
    with open(os.path.join(ROOT, "config", "sources", "tv.json"), encoding="utf-8") as handle:
        for source in json.load(handle).get("sources") or ():
            if source.get("enabled", True):
                yield str(source.get("id")), str(source.get("name")), str(source.get("url"))


def main(argv: Any = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", action="append", required=True)
    parser.add_argument("--out", default="reports/channel-route-scout.json")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--skip-probe", action="store_true")
    args = parser.parse_args(argv)

    entries: List[Dict[str, Any]] = []
    source_rows: List[Dict[str, Any]] = []

    manual = os.path.join(ROOT, "manual", "manual.m3u")
    if os.path.isfile(manual):
        with open(manual, encoding="utf-8", errors="replace") as handle:
            found = parse_m3u(handle.read(), "manual-playlist-1", "manual.m3u")
        entries.extend(found)
        source_rows.append({"source_id": "manual-playlist-1", "http_status": 200,
                            "parsed_entries": len(found)})

    for source_id, source_name, url in sources():
        status, body, error = fetch(url)
        found = parse_m3u(body.decode("utf-8", "replace"), source_id, source_name) if body else []
        entries.extend(found)
        source_rows.append({"source_id": source_id, "http_status": status,
                            "bytes": len(body), "parsed_entries": len(found),
                            "fetch_error": error})
        print("  %-32s HTTP %-4s %8d B  %5d entries %s"
              % (source_id, status, len(body), len(found), error), file=sys.stderr)

    wanted = [str(c) for c in args.channel]
    candidates: List[Dict[str, Any]] = []
    seen = set()
    for entry in entries:
        for channel in wanted:
            if not same_channel(channel, entry.get("name") or ""):
                continue
            key = (channel, str(entry.get("url") or "").split("|", 1)[0])
            if key in seen:
                continue
            seen.add(key)
            row = dict(entry)
            row["wanted"] = channel
            candidates.append(row)
    print("\n  %d candidate route(s) across %d channel(s)"
          % (len(candidates), len(wanted)), file=sys.stderr)

    results: List[Dict[str, Any]] = []
    if not args.skip_probe and candidates:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(probe, c): c for c in candidates}
            for future in concurrent.futures.as_completed(futures):
                row = future.result()
                row["wanted"] = futures[future]["wanted"]
                results.append(row)
                print("  %-18s %-26s HTTP %-4s %-11s %s"
                      % (row["wanted"][:18], str(row["name"])[:26], row["http_status"],
                         row["looks_like"], row["source_id"]), file=sys.stderr)
    else:
        results = [dict(c, http_status=None) for c in candidates]

    results.sort(key=lambda r: (str(r.get("wanted")), -(r.get("bytes") or 0)))
    payload = {
        "mode": "channel_route_scout",
        "note": (
            "Reachability only. HTTP 200 with a parseable manifest is exactly "
            "what a hidden channel's primary also returns, so nothing here is "
            "a playback proof; a candidate earns promotion by passing two "
            "independent 120 s browser sessions, not by appearing in this file."
        ),
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
        "channels": wanted,
        "sources": source_rows,
        "candidate_count": len(results),
        "reachable": sum(1 for r in results if r.get("http_status") == 200),
        "candidates": results,
    }
    out = os.path.join(ROOT, args.out) if not os.path.isabs(args.out) else args.out
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=1, ensure_ascii=False)
        handle.write("\n")
    print("\nwrote %s: %d candidate(s), %d reachable"
          % (args.out, len(results), payload["reachable"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
