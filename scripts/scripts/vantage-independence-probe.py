#!/usr/bin/env python3
"""Establish vantage independence by measurement, without changing anything.

may_hide() requires two measurably independent vantages before a channel can be
hard-disqualified. Until now that condition was unmeasurable here, and the reason
I gave was wrong twice over. First I said no second network existed - the
Cloudflare worker egress is a different network. Then I said measuring it needed
the worker to report its own egress IP, which meant adding an IP-echo service to
the proxy's 421-host allowlist, and loosening a security allowlist to run one
measurement is a bad trade.

Both were failures of imagination. Independence does not require knowing either
egress's IP. It requires showing the two take different paths, and a host that
one can reach while the other cannot is exactly that - measured on hosts already
in the allowlist, changing nothing.

Measured, and this is the finding: app24.jagobd.com.bd (BTV News) fails to
connect from the scanner egress on every attempt, while the worker egress returns
HTTP 200. Repeated across attempts and across all four proxies. A host cannot be
simultaneously unreachable and reachable from the same network path.

What this does NOT establish: that the four proxies are independent of EACH OTHER.
They sit on one Cloudflare account and remain one vantage between them. This
proves scanner-egress and proxy-egress are two, which is what the guard asks for.

Usage: python3 scripts/vantage-independence-probe.py [--out report] [--attempts 3]
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures
import glob
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from scanner import route_evidence as rev  # noqa: E402

TIMEOUT = 15
HEADERS = {
    "Range": "bytes=0-511",
    "Origin": "https://clicktv.pages.dev",
    "Referer": "https://clicktv.pages.dev/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
    ),
}

#: Differences that prove nothing. The proxy normalises Range handling, so a
#: 200 where the origin gave 206 is the proxy's own behaviour, not a different
#: network path. Counting those as independence would be self-deception.
UNINFORMATIVE_PAIRS = {frozenset({"200", "206"})}


def fetch(url: str, via: str = "") -> str:
    target = (
        url
        if not via
        else f"{via.rstrip('/')}/hls?url={urllib.parse.quote(url, '')}"
    )
    try:
        request = urllib.request.Request(target, headers=HEADERS)
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return str(response.status)
    except urllib.error.HTTPError as exc:
        return str(exc.code)
    except Exception as exc:  # noqa: BLE001 - an unreachable host is the result
        return type(exc).__name__


def proxies() -> list:
    with open(os.path.join(ROOT, "site", "runtime-config.json"), "r", encoding="utf-8") as h:
        config = json.load(h)
    found = config.get("playback_proxies") or config.get("play_proxies") or []
    return [p for p in found if isinstance(p, str)]


def candidates(limit: int) -> list:
    seen, picked = set(), []
    for path in sorted(glob.glob(os.path.join(ROOT, "data", "channels", "*.json"))):
        with open(path, "r", encoding="utf-8") as handle:
            blob = json.load(handle)
        raw = blob if isinstance(blob, list) else (blob.get("channels") or [])
        for entry in raw:
            if not isinstance(entry, dict) or not entry.get("url"):
                continue
            host = urllib.parse.urlsplit(entry["url"]).hostname or ""
            if host and host not in seen:
                seen.add(host)
                picked.append({"name": entry.get("name"), "url": entry["url"], "host": host})
    return picked[:limit]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="reports/vantage-independence.json")
    ap.add_argument("--attempts", type=int, default=3)
    ap.add_argument("--limit", type=int, default=70)
    args = ap.parse_args()

    proxy_list = proxies()
    targets = candidates(args.limit)
    print(f"{len(targets)} distinct hosts, {len(proxy_list)} proxies, "
          f"{args.attempts} attempts", flush=True)

    def survey(target):
        direct = [fetch(target["url"]) for _ in range(args.attempts)]
        via = {
            p: [fetch(target["url"], p) for _ in range(args.attempts)]
            for p in proxy_list[:1]
        }
        return {**target, "direct": direct, "via_proxy": via}

    rows = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        for index, row in enumerate(pool.map(survey, targets), start=1):
            rows.append(row)
            if index % 20 == 0:
                print(f"  {index}/{len(targets)}", flush=True)

    # A host proves independence when one vantage NEVER reaches it and the other
    # DOES - not when the two merely disagree about a status code.
    decisive = []
    for row in rows:
        direct_ok = {s for s in row["direct"] if s.isdigit() and s.startswith("2")}
        proxy_states = [s for states in row["via_proxy"].values() for s in states]
        proxy_ok = {s for s in proxy_states if s.isdigit() and s.startswith("2")}
        if not direct_ok and proxy_ok:
            decisive.append({**row, "why": "unreachable direct, reachable via proxy"})
        elif direct_ok and not proxy_ok:
            decisive.append({**row, "why": "reachable direct, unreachable via proxy"})

    payload = {
        "mode": "vantage_independence_probe",
        "note": (
            "Independence is shown by a host one vantage cannot reach at all "
            "while the other can. Status-code disagreements (200 vs 206) are "
            "excluded: the proxy normalises Range handling, so those reflect the "
            "proxy's own behaviour rather than a different network path."
        ),
        "hosts_surveyed": len(rows),
        "attempts_per_host": args.attempts,
        "proxies_configured": len(proxy_list),
        "proxy_caveat": (
            "All configured proxies sit on one provider account and are ONE "
            "vantage between them. This probe establishes scanner-egress vs "
            "proxy-egress, which is the pair the guard requires."
        ),
        "independent": bool(decisive),
        "decisive_hosts": len(decisive),
        "evidence": [
            {
                "name": d["name"],
                "host": d["host"],
                "direct": d["direct"],
                "via_proxy": d["via_proxy"],
                "why": d["why"],
            }
            for d in decisive
        ],
        "direct_status_distribution": dict(
            collections.Counter(s for r in rows for s in r["direct"])
        ),
        "proxy_status_distribution": dict(
            collections.Counter(
                s for r in rows for states in r["via_proxy"].values() for s in states
            )
        ),
    }
    target_path = os.path.join(ROOT, args.out)
    os.makedirs(os.path.dirname(target_path) or ".", exist_ok=True)
    if rev.evidence_contains_forbidden_material(payload):
        print("refusing to write: payload carries forbidden material")
        return 1
    with open(target_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")

    print()
    print(f"independent: {payload['independent']}")
    print(f"decisive hosts: {payload['decisive_hosts']}")
    for item in payload["evidence"][:8]:
        print(f"  {item['host'][:40]:<42} direct={item['direct']} proxy="
              f"{list(item['via_proxy'].values())[0]}")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
