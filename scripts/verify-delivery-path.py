#!/usr/bin/env python3
"""Ask the real playback proxy for every published route, the way a viewer does.

This is the piece that makes a broken card repair itself, and it needs nothing
that does not already exist - no telemetry service, no KV, no new worker. The
playback proxies are already deployed and already serving; this simply asks them
the same question a browser asks.

Why it is needed at all. The scanner verifies a route with Python's own socket,
from a GitHub runner. The viewer reaches it through a Cloudflare Worker, from a
browser, on an HTTPS page. A route can answer 200 to the first and be
unreachable to the second - a bare IP, a host the proxy's allowlist does not
carry, a host that refuses Cloudflare's egress, a host that needs headers the
proxy does not send. Every one of those publishes a card that spins forever.

What it does with a refusal: writes it to state/measured-playback-failures.json,
which scanner/merger.py now ranks above every other signal. So the NEXT scan
sees the dead route demoted below any alternate the sources already carry, and
swaps it in on its own. That is the whole repair loop, and it runs inside the
existing schedule.

What it will not do is record an ambiguous answer. A timeout, a 429 or a 5xx is
a bad minute, not a dead route - this project has already deleted working
channels that way once. Only a refusal the proxy will give every time counts:

    "Target host not allowed"  the proxy's own allowlist has no such host
    error code: 1003           Cloudflare refuses a direct-IP fetch
    404 / 410                  the upstream says this path does not exist

A route that answers clears an earlier refusal THIS check recorded, so a host
that comes back is picked up again rather than staying demoted forever. It never
clears a browser measurement. The proxy returning a manifest means the bytes
arrive, not that a viewer can watch - rgkkw.live serves a perfectly good
playlist and produced 0.12 seconds of video across two 120-second Chrome
sessions. On the first run of this script that distinction was missing and it
put "Verified" back on thirteen channels a browser had measured dead.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import glob
import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from scanner import playback_evidence  # noqa: E402

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36")

#: The proxy only serves a request that carries the site's own origin, so the
#: check has to send it or every answer is a 403 about the origin instead of an
#: answer about the route.
SITE_ORIGIN = "https://clicktv.pages.dev"

#: Stamped on every row this script writes. It is also what lets it clear one
#: later: a row written by a browser measurement is never cleared from here.
VANTAGE = "delivery_path_proxy"

#: Bodies the proxy or the edge returns when the refusal is permanent. Matched
#: on the body rather than the status because both arrive as 403, and one of
#: them - the origin check - is about this script, not about the route.
PERMANENT_BODIES = (
    "target host not allowed",
    "error code: 1003",
)

#: Statuses that mean the upstream itself says the path is gone.
PERMANENT_STATUSES = frozenset({404, 410})

#: Statuses that prove nothing. A route is never demoted on one of these.
AMBIGUOUS_STATUSES = frozenset({0, 408, 429, 500, 502, 503, 504, 520, 521, 522,
                                523, 524, 525, 526, 527, 530, 567})


def _context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def proxies() -> List[str]:
    for name in ("site/runtime-config.json", "dist/runtime-config.json"):
        path = os.path.join(ROOT, name)
        try:
            with open(path, "r", encoding="utf-8") as handle:
                found = json.load(handle).get("play_proxies")
        except (OSError, ValueError):
            continue
        if isinstance(found, list) and found:
            return [str(value) for value in found
                    if str(value).lower().startswith("https://")]
    return []


def playback_catalogue() -> Dict[str, Any]:
    found: Dict[str, Any] = {}
    for path in glob.glob(os.path.join(ROOT, "data", "playback", "*.json")):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                found.update(json.load(handle).get("records") or {})
        except (OSError, ValueError):
            continue
    return found


CATALOGUE = playback_catalogue()


def url_of(stream: Any) -> str:
    if not isinstance(stream, dict):
        return str(stream or "").strip()
    for key in ("url", "stream_url", "link"):
        value = stream.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    record = CATALOGUE.get(str(stream.get("playback_id") or "")) or {}
    return str(record.get("url") or "").strip()


def published_routes() -> List[Dict[str, str]]:
    """Every route on every published card, primary and backup."""
    rows: List[Dict[str, str]] = []
    seen: set = set()
    files = sorted(glob.glob(os.path.join(ROOT, "data", "channels", "*.json")))
    for name in ("today-match.json", "upcoming.json"):
        path = os.path.join(ROOT, "data", name)
        if os.path.isfile(path):
            files.append(path)
    for path in files:
        if os.path.basename(path) == "index.json":
            continue
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError):
            continue
        key = next((k for k in ("channels", "events", "items")
                    if isinstance(payload.get(k), list)), None)
        if not key:
            continue
        for card in payload[key]:
            if not isinstance(card, dict):
                continue
            streams = [("primary", card)]
            streams += [(f"backup{i}", b)
                        for i, b in enumerate(card.get("backups") or [], start=1)]
            for label, stream in streams:
                url = url_of(stream)
                if not url or url in seen:
                    continue
                seen.add(url)
                rows.append({
                    "name": str(card.get("name") or ""),
                    "where": label,
                    "url": url,
                    "type": str(stream.get("stream_type") or "") if isinstance(stream, dict) else "",
                    "profile": str(stream.get("header_profile") or "") if isinstance(stream, dict) else "",
                })
    return rows


def ask_proxy(row: Dict[str, str], proxy: str, timeout: float) -> Tuple[str, str]:
    """(verdict, detail). verdict is "pass", "dead" or "unknown"."""
    target = str(row["url"]).split("|", 1)[0]
    endpoint = (
        proxy.rstrip("/") + "/hls?url=" + urllib.parse.quote(target, safe="")
        + ("&type=" + urllib.parse.quote(row["type"]) if row["type"] else "")
        + ("&profile=" + urllib.parse.quote(row["profile"]) if row["profile"] else "")
    )
    request = urllib.request.Request(endpoint, headers={
        "User-Agent": UA,
        "Origin": SITE_ORIGIN,
        "Referer": SITE_ORIGIN + "/",
        "Accept": "*/*",
    })
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=_context()) as response:
            body = response.read(2048)
            if response.status not in (200, 206):
                return "unknown", f"HTTP {response.status}"
            return "pass", f"HTTP {response.status}, {len(body)} bytes"
    except urllib.error.HTTPError as failure:
        body = ""
        try:
            body = (failure.read(400) or b"").decode("utf-8", "replace").strip()
        except Exception:  # noqa: BLE001 - a body we cannot read is not evidence
            body = ""
        lowered = body.lower()
        if failure.code in PERMANENT_STATUSES:
            return "dead", f"HTTP {failure.code} from the upstream"
        for marker in PERMANENT_BODIES:
            if marker in lowered:
                return "dead", f"the playback proxy refuses this route: {body[:120]}"
        if failure.code in AMBIGUOUS_STATUSES:
            return "unknown", f"HTTP {failure.code}"
        return "unknown", f"HTTP {failure.code}: {body[:120]}"
    except Exception as failure:  # noqa: BLE001 - a timeout is not evidence
        return "unknown", type(failure).__name__


def main(argv: Any = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--out", default="reports/delivery-path-check.json")
    args = parser.parse_args(argv)

    available = proxies()
    if not available:
        print("[Delivery Path] no https playback proxy configured; nothing to do")
        return 0
    rows = published_routes()
    if args.limit:
        rows = rows[:args.limit]
    print(f"[Delivery Path] {len(rows)} published route(s), "
          f"{len(available)} proxy/proxies")

    def check(index_and_row):
        index, row = index_and_row
        proxy = available[index % len(available)]
        verdict, detail = ask_proxy(row, proxy, args.timeout)
        # One retry on a different proxy before calling anything dead, so a
        # single unhealthy worker cannot demote a working route.
        if verdict == "dead" and len(available) > 1:
            second = available[(index + 1) % len(available)]
            again, detail2 = ask_proxy(row, second, args.timeout)
            if again != "dead":
                return dict(row, verdict="unknown",
                            detail=f"refused by one proxy, {again} on another: {detail2}")
        return dict(row, verdict=verdict, detail=detail)

    results: List[Dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        for done, result in enumerate(pool.map(check, enumerate(rows)), start=1):
            results.append(result)
            if done % 100 == 0:
                print(f"   {done}/{len(rows)}", flush=True)

    dead = [r for r in results if r["verdict"] == "dead"]
    ok = [r for r in results if r["verdict"] == "pass"]
    unknown = [r for r in results if r["verdict"] == "unknown"]
    print(f"\n   the proxy served      : {len(ok)}")
    print(f"   the proxy refuses     : {len(dead)}   -> recorded, so the next "
          f"scan prefers an alternate")
    print(f"   no verdict either way : {len(unknown)}  -> left alone on purpose")

    written = 0
    restored = 0
    if not args.dry_run:
        for row in dead:
            if playback_evidence.record(
                row["url"], row["detail"], sessions=1,
                media_progress_seconds=[0], window_seconds=0.0,
                evidence_report=args.out, vantage=VANTAGE,
            ):
                written += 1
        # A host that comes back should be picked up again rather than staying
        # demoted forever - but only if THIS check is what demoted it.
        #
        # The proxy returning a manifest means the bytes arrive. It does not
        # mean a viewer can watch: rgkkw.live serves a perfectly good playlist
        # and produced 0.12 seconds of video across two 120-second Chrome
        # sessions. Letting one HTTP request supersede a real browser
        # measurement would put "Verified" back on thirteen channels that were
        # measured dead, and it did exactly that on the first run of this
        # script - eighteen rows, every one of them browser evidence.
        #
        # So a row is only cleared when its own vantage says this script wrote
        # it. Browser evidence is cleared by a browser, and by nothing else.
        for row in ok:
            if not playback_evidence.unproven_reason(row["url"]):
                continue
            if playback_evidence.vantage_of(row["url"]) != VANTAGE:
                continue
            if playback_evidence.record_proof(
                row["url"], vantage=VANTAGE, sessions=1,
                media_progress_seconds=[0], window_seconds=0.0,
                evidence_report=args.out,
            ):
                restored += 1
        print(f"\n   newly recorded as dead : {written}")
        print(f"   cleared, host is back  : {restored}")

    for row in dead[:25]:
        print(f"      [{row['where']:8s}] {row['name'][:34]:36s} {row['detail'][:70]}")

    if not args.dry_run:
        out = args.out if os.path.isabs(args.out) else os.path.join(ROOT, args.out)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as handle:
            json.dump({
                "mode": "delivery_path_check",
                "note": ("Asked the live playback proxies for every published "
                         "route, with the site origin, exactly as the player "
                         "does. Only a permanent refusal is recorded; a "
                         "timeout, 429 or 5xx is left alone."),
                "checked": len(results),
                "served": len(ok),
                "refused": len(dead),
                "no_verdict": len(unknown),
                "recorded": written,
                "cleared": restored,
                "refusals": [
                    {"name": r["name"], "where": r["where"], "detail": r["detail"]}
                    for r in dead
                ],
            }, handle, ensure_ascii=False, indent=1)
            handle.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
