#!/usr/bin/env python3
"""Fast reachability probe over the movie routes, from this vantage.

The browser sweep costs minutes per title and is the only thing that can produce
a PASS. It is not the right tool for answering "what is wrong with these 215
routes", because the first six all failed the same two ways: a CORS refusal on
the direct route and HTTP 403 through the proxy. This probe answers that question
for every route in minutes instead of hours.

What it does NOT do, and must never be read as doing: prove playback. A 200 here
is not a working movie - HTTP 200 with a valid manifest was measured on every
hidden channel's primary while the channel was stored as failed. This is a
reachability observation, classified through the same
route_evidence.classify_transport the rest of the model uses, and it can never
hide anything.

Usage: python3 scripts/movie-route-probe.py --targets targets.json [--out report]
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from scanner import route_evidence as rev  # noqa: E402

TIMEOUT = 12
SAMPLE_BYTES = 2048
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
)


def probe(url: str) -> Dict[str, Any]:
    """One ranged GET. Returns status, error kind, content type and CORS state."""
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Range": f"bytes=0-{SAMPLE_BYTES - 1}",
            # Asked for deliberately: a browser sends this on a cross-origin
            # fetch, and its absence in the response is why every direct route
            # failed with "Failed to fetch" rather than with a status.
            "Origin": "https://clicktv.pages.dev",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            body = response.read(SAMPLE_BYTES)
            return {
                "status": response.status,
                "error_kind": "",
                "content_type": response.headers.get("Content-Type") or "",
                "bytes": len(body),
                "cors_allow_origin": response.headers.get(
                    "Access-Control-Allow-Origin"
                ),
            }
    except urllib.error.HTTPError as exc:
        return {
            "status": exc.code,
            "error_kind": "",
            "content_type": (exc.headers or {}).get("Content-Type") or "",
            "bytes": 0,
            "cors_allow_origin": (exc.headers or {}).get(
                "Access-Control-Allow-Origin"
            ),
        }
    except urllib.error.URLError as exc:
        reason = str(getattr(exc, "reason", exc)).lower()
        if "timed out" in reason:
            kind = "connect_timeout"
        elif "name or service" in reason or "resolve" in reason:
            kind = "dns"
        elif "certificate" in reason or "ssl" in reason:
            kind = "tls"
        else:
            kind = "network"
        return {"status": None, "error_kind": kind, "content_type": "",
                "bytes": 0, "cors_allow_origin": None}
    except Exception as exc:  # noqa: BLE001 - a probe failure is a result
        return {"status": None, "error_kind": "network", "content_type": "",
                "bytes": 0, "cors_allow_origin": None,
                "detail": str(exc)[:120]}


def proxy_list() -> list:
    """The configured play proxies, which the vantage probe established are a
    DIFFERENT egress from the scanner's own."""
    try:
        with open(os.path.join(ROOT, "site", "runtime-config.json"), "r", encoding="utf-8") as h:
            config = json.load(h)
    except (OSError, ValueError):
        return []
    found = config.get("playback_proxies") or config.get("play_proxies") or []
    return [p for p in found if isinstance(p, str)]


def probe_via(url: str, proxy: str) -> Dict[str, Any]:
    """Same probe, through the proxy egress.

    Worth doing only because vantage independence is now measured rather than
    assumed: reports/vantage-independence.json shows hosts the scanner cannot
    reach at all while the proxy returns 200. So a 403 from both egresses says
    something a 403 from one does not.
    """
    return probe(f"{proxy.rstrip('/')}/hls?url={urllib.parse.quote(url, '')}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--targets", required=True)
    ap.add_argument("--out", default="reports/movie-route-probe.json")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--via-proxy", action="store_true",
                    help="also probe through the proxy egress, which the vantage "
                         "probe established is a different network path")
    args = ap.parse_args()

    with open(args.targets, "r", encoding="utf-8") as handle:
        targets = json.load(handle)

    proxies = proxy_list()

    def run(target: Dict[str, Any]) -> Dict[str, Any]:
        url = str(target.get("url") or "")
        result = probe(url) if url else {"status": None, "error_kind": "network"}
        # Second vantage. A route blocked from BOTH egresses is blocked by the
        # host for datacentre traffic generally, not by one egress being on a
        # blocklist - and those are different findings.
        via = probe_via(url, proxies[0]) if (url and proxies and args.via_proxy) else None
        verdict = rev.classify_transport(
            result.get("status"),
            error_kind=result.get("error_kind") or "",
            content_type=result.get("content_type") or "",
        )
        return {
            "name": target.get("name"),
            "url_public_template": rev.redact_public_template(url),
            "url_scheme": url.split(":", 1)[0] if ":" in url else "",
            "status": result.get("status"),
            "error_kind": result.get("error_kind"),
            "content_type": (result.get("content_type") or "")[:60],
            "bytes_sampled": result.get("bytes"),
            "cors_allow_origin": result.get("cors_allow_origin"),
            "browser_direct_possible": bool(result.get("cors_allow_origin")),
            "transport_class": verdict,
            "escalatable": rev.is_escalatable(verdict),
            "proxy_status": (via or {}).get("status"),
            "proxy_error_kind": (via or {}).get("error_kind"),
            "blocked_from_both_vantages": bool(
                via
                and str(result.get("status")) == "403"
                and str(via.get("status")) == "403"
            ),
        }

    results: List[Dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        for index, row in enumerate(pool.map(run, targets), start=1):
            results.append(row)
            if index % 25 == 0:
                print(f"  probed {index}/{len(targets)}", flush=True)

    from collections import Counter

    payload = {
        "mode": "movie_route_reachability_probe",
        "note": (
            "Reachability only. A 200 here is NOT a working movie: HTTP 200 with "
            "a valid manifest was measured on every hidden channel's primary "
            "while the channel was stored as failed. Nothing here can hide or "
            "promote anything; only the 120 s browser acceptance can."
        ),
        "routes": len(results),
        "by_status": dict(Counter(str(r["status"]) for r in results)),
        "by_transport_class": dict(Counter(r["transport_class"] for r in results)),
        "by_scheme": dict(Counter(r["url_scheme"] for r in results)),
        "cors_permits_browser_direct": sum(
            1 for r in results if r["browser_direct_possible"]
        ),
        "escalatable_count": sum(1 for r in results if r["escalatable"]),
        "second_vantage_used": bool(args.via_proxy and proxies),
        "blocked_from_both_vantages": sum(
            1 for r in results if r.get("blocked_from_both_vantages")
        ),
        "by_proxy_status": dict(
            Counter(str(r.get("proxy_status")) for r in results)
        ) if args.via_proxy else None,
        "results": results,
    }
    target_path = os.path.join(ROOT, args.out)
    os.makedirs(os.path.dirname(target_path) or ".", exist_ok=True)
    with open(target_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")

    print(f"\nroutes: {payload['routes']}")
    print(f"by status: {payload['by_status']}")
    print(f"by class:  {payload['by_transport_class']}")
    print(f"by scheme: {payload['by_scheme']}")
    print(f"CORS permits a browser direct fetch: "
          f"{payload['cors_permits_browser_direct']}/{payload['routes']}")
    print(f"escalatable: {payload['escalatable_count']}")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
