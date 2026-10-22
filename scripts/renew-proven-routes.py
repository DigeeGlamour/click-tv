#!/usr/bin/env python3
"""Re-prove the preferred routes before their 14-day proof runs out.

    python scripts/renew-proven-routes.py              # entries 10+ days old
    python scripts/renew-proven-routes.py --channel "STAR JALSHA"
    python scripts/renew-proven-routes.py --dry-run    # list what is due

A route in state/route-preference.json leads its channel for
PREFERENCE_TTL_SECONDS (14 days) after it passed two 120 s browser sessions.
When the proof lapses the scanner ranks the channel's routes on its own again,
and on 2026-10-21 that is exactly how Zee Bangla went back to a route that
stuttered: its August proof had expired and nothing renewed it.

The proof has to be measured from a viewer's vantage - a browser in
Bangladesh - so it cannot run on GitHub's runners. This is the one command to
run on such a machine. For every due entry it finds the route on the
channel's published card, runs the same two-session acceptance
(scripts/sustained-playback-check.py), and records a fresh proof only for a
route that passes again. A route that fails is left to lapse: the registry
cannot be renewed by a claim.

The committed evidence is a redacted copy: every URL as its public template.
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from scanner import route_evidence as rev  # noqa: E402
from scanner import route_preference  # noqa: E402
from scanner import sustained_proof  # noqa: E402
from scanner.playback_profiles import redact_public_report  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "add_proven_route", os.path.join(ROOT, "scripts", "add-proven-route.py"))
ADD = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ADD)


def _age_days(entry: Dict[str, Any]) -> Optional[float]:
    try:
        stamp = dt.datetime.fromisoformat(str(entry.get("recorded_at")).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (dt.datetime.now(dt.timezone.utc) - stamp).total_seconds() / 86400.0


def _card_route(channel: str, route_id: str) -> Optional[Dict[str, Any]]:
    """The stream on the channel's published card that IS the registered route."""
    for path in sorted(glob.glob(os.path.join(ROOT, "data", "channels", "*.json"))):
        payload = json.load(open(path, encoding="utf-8"))
        cards = payload if isinstance(payload, list) else (payload.get("channels") or payload.get("items") or [])
        for card in cards:
            if str(card.get("name") or "").strip().casefold() != channel.strip().casefold():
                continue
            for stream in [card] + list(card.get("backups") or []):
                url = str(stream.get("url") or "")
                if url and route_preference.registry_route_id(url) == route_id:
                    return {"name": card["name"], "url": url,
                            "stream_type": stream.get("stream_type") or card.get("stream_type") or "hls",
                            "proxy_mode": stream.get("proxy_mode") or card.get("proxy_mode") or "direct_first",
                            "category": card.get("category") or ""}
    return None


def _redacted(report: Dict[str, Any]) -> Dict[str, Any]:
    def scrub(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: scrub(v) for k, v in value.items()}
        if isinstance(value, list):
            return [scrub(v) for v in value]
        if isinstance(value, str) and re.search(r"https?://", value):
            return re.sub(r"https?://[^\s\"']+", lambda m: rev.redact_public_template(m.group(0)), value)
        return value

    clean = scrub(redact_public_report(report))
    for result in clean.get("results") or ():
        template = str(result.get("url_public_template") or "")
        result["url_public_template"] = re.sub(
            r"/([A-Za-z0-9+=_\-]{32,})\.(m3u8|ts|mpd|mp4)$", r"/{seg}.\2", template)
    return clean


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--channel", action="append", default=[])
    ap.add_argument("--due-after-days", type=float, default=10.0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    registry = route_preference.load()
    wanted = {c.casefold() for c in args.channel}
    due, targets = [], []
    for key, entry in (registry.get("preferred") or {}).items():
        channel = str(entry.get("channel") or "")
        if wanted and channel.casefold() not in wanted:
            continue
        age = _age_days(entry)
        if not wanted and (age is None or age < args.due_after_days):
            continue
        route = _card_route(channel, str(entry.get("route_id") or ""))
        if route is None:
            print(f"skip  {channel}: its proven route is no longer on the card")
            continue
        label = f"{route['name']} [renewal]"
        due.append((channel, route, label))
        targets.append(dict(route, name=label))
        print(f"due   {channel}: proof {age:.1f} days old" if age is not None else f"due   {channel}")

    if not targets:
        print("nothing due")
        return 0
    if args.dry_run:
        return 0

    with tempfile.TemporaryDirectory() as tmp:
        target_file = os.path.join(tmp, "targets.json")
        json.dump(targets, open(target_file, "w", encoding="utf-8"), ensure_ascii=False)
        raw_report = os.path.relpath(os.path.join(tmp, "renewal.json"), ROOT)
        subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "sustained-playback-check.py"),
                        "--targets", target_file, "--sessions", "2", "--out", raw_report],
                       cwd=ROOT, check=False)
        payload = json.load(open(os.path.join(ROOT, raw_report), encoding="utf-8"))
        proven = {p["name"]: p for p in ADD.proven_routes(raw_report)}

    clean = _redacted(payload)
    if rev.evidence_contains_forbidden_material(clean):
        print("refused: the redacted report still carries credential-shaped material")
        return 1
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    report_rel = f"reports/route-renewal-{stamp}.json"
    with open(os.path.join(ROOT, report_rel), "w", encoding="utf-8", newline="\n") as handle:
        json.dump(clean, handle, indent=2, ensure_ascii=False)
        handle.write("\n")

    renewed = 0
    for channel, route, label in due:
        evidence = proven.get(label)
        if not evidence:
            print(f"lapse {channel}: did not pass twice again; its preference will expire")
            continue
        evidence = dict(evidence, evidence_report=report_rel)
        ok_proof, why_proof = sustained_proof.record("channel", channel, evidence)
        ok_pref, why_pref = route_preference.record("channel", channel, route["url"], evidence)
        print(f"renew {channel}: proof {'recorded' if ok_proof else why_proof}, "
              f"preference {'recorded' if ok_pref else why_pref}")
        renewed += int(ok_pref)
    print(f"{renewed} of {len(due)} renewed; evidence in {report_rel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
