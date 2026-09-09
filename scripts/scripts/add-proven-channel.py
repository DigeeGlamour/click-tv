#!/usr/bin/env python3
"""Create a card for a channel that has no card, on a route a browser proved.

Normally a scan creates cards, and it should. But a scan creates a card only for
a route its own network check accepted, and that check runs from a GitHub runner
in a US datacentre while the audience is in Bangladesh. Measured repeatedly on
2026-08-29/30: the SonyLIV URL the CI recorded as "HTTP 403: Forbidden" answered
HTTP 200 with a live manifest from Bangladesh minutes later, and the Sony family
lost every card when hotstarplugx/plugsony.cstds.workers.dev were deleted, while
stream.ottplus.bd carried the same channels at 720p the whole time.

So a channel can be missing from the catalogue and perfectly playable. This adds
it back - but only on the strongest evidence the project recognises, which is
stronger than the check a scan makes:

  * two independent 120 s browser sessions through the site's own attempt plan,
    each reaching the sustained-playback floor. HTTP 200 is not enough here,
    because HTTP 200 is exactly what every dead card also returns.
  * the channel must route to the requested category, and pass that category's
    publish allowlist if it has one. This cannot smuggle a card past either.
  * a channel that already has a card is left alone - use add-proven-route.py
    to change an existing one.

The proof is registered in state/sustained-playback-proof.json and
state/route-preference.json, so the next scan keeps the channel and prefers this
route rather than undoing the work.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.parse
from typing import Any, Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from scanner import category_allowlist  # noqa: E402
from scanner import route_evidence as rev  # noqa: E402
from scanner import route_preference  # noqa: E402
from scanner import sustained_proof  # noqa: E402

CATEGORY_FILE = {
    "Bangla": "bangla.json", "Cartoon": "cartoon.json",
    "Foreign News": "foreign-news.json", "Indian": "indian.json",
    "Infotainments": "infotainments.json", "Islamic": "islamic.json",
    "Other": "other.json", "Sports": "sports.json",
}


def slug(name: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", str(name or "").casefold()).strip("-")
    return text or "channel"


def proven_entry(report_path: str, entry_name: str) -> Optional[Dict[str, Any]]:
    path = report_path if os.path.isabs(report_path) else os.path.join(ROOT, report_path)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return None
    for result in payload.get("results") or ():
        if str(result.get("name")) != entry_name:
            continue
        if not result.get("proven"):
            return None
        observations = result.get("observations") or []
        return {
            "pass_count": int(result.get("pass_count") or 0),
            "window_seconds": payload.get("window_seconds"),
            "browser_profile": payload.get("browser_profile"),
            "media_progress_seconds": [
                (o.get("playback_metrics") or {}).get("media_progress_seconds")
                for o in observations
            ],
            "cumulative_stall_seconds": [
                (o.get("playback_metrics") or {}).get("cumulative_stall_seconds")
                for o in observations
            ],
            "route": (observations[0] or {}).get("attempt_route") if observations else "",
            "evidence_report": report_path,
        }
    return None


def measure_height(url: str) -> int:
    """The route's real height, or 0. Read from the media, never guessed."""
    import urllib.request

    from scanner import media_probe

    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
            )
        }

        def _get(target: str, limit: int) -> bytes:
            with urllib.request.urlopen(
                urllib.request.Request(target, headers=headers), timeout=15
            ) as response:
                return response.read(limit)

        body = _get(url, 262144)
        head = body[:16384].decode("utf-8", "replace")
        if "#EXT-X-STREAM-INF" in head:
            return media_probe.master_playlist_height(head)
        if "#EXTINF" in head:
            if "METHOD=AES-128" in head or "METHOD=SAMPLE-AES" in head:
                return 0
            segments = [
                line.strip() for line in head.splitlines()
                if line.strip() and not line.startswith("#")
            ]
            if segments:
                body = _get(urllib.parse.urljoin(url, segments[0]), 524288)
        decoded = media_probe.sps_from_transport_stream(body)
        return media_probe.plausible(decoded.get("height")) if decoded else 0
    except Exception:  # noqa: BLE001 - a probe must never invent a number
        return 0


def label_for(height: int) -> str:
    return (
        "FHD" if height >= 1080 else "HD" if height >= 720
        else "SD" if height >= 480 else "LD"
    )


def _remove_from_other_categories(
    data_dir: str,
    keep_category: str,
    channel: str,
    *,
    dry_run: bool = False,
) -> List[Any]:
    """Drop this channel's card from every category but the one it now belongs
    to. Returns [(category, remaining_count), ...] for what was touched."""
    touched: List[Any] = []
    wanted = str(channel or "").casefold()
    for category, filename in CATEGORY_FILE.items():
        if category == keep_category:
            continue
        path = os.path.join(data_dir, filename)
        if not os.path.isfile(path):
            continue
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        cards = payload.get("channels") or []
        kept = [
            card for card in cards
            if str(card.get("name") or "").casefold() != wanted
        ]
        if len(kept) == len(cards):
            continue
        touched.append((category, len(kept)))
        if dry_run:
            continue
        payload["channels"] = kept
        payload["count"] = len(kept)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    return touched


def main(argv: Any = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="",
                        help="where data/channels lives; for tests")
    parser.add_argument("--channel", required=True)
    parser.add_argument("--category", required=True, choices=sorted(CATEGORY_FILE))
    parser.add_argument("--url", required=True)
    parser.add_argument("--proven-name", required=True,
                        help="the entry name in the playback report")
    parser.add_argument("--report", required=True)
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--stream-type", default="hls")
    parser.add_argument("--proxy-mode", default="proxy_first")
    parser.add_argument("--header-profile", default="android_tv")
    parser.add_argument("--floor", type=int, default=720)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    evidence = proven_entry(args.report, args.proven_name)
    if not evidence:
        print(f"refused: '{args.proven_name}' is not PROVEN in {args.report} "
              f"(needs {rev.REQUIRED_FRESH_SESSIONS} full 120 s passes)")
        return 1
    if int(evidence["pass_count"]) < rev.REQUIRED_FRESH_SESSIONS:
        print(f"refused: only {evidence['pass_count']} full PASS")
        return 1
    print("proof: %d full PASS, media %s s, stall %s s"
          % (evidence["pass_count"], evidence["media_progress_seconds"],
             evidence["cumulative_stall_seconds"]))

    if not category_allowlist.is_allowed(args.category, args.channel):
        print(f"refused: '{args.channel}' is not on the publish allowlist for "
              f"{args.category}")
        return 1

    from scanner.normalizer import Normalizer

    routed = Normalizer().detect_tv_category(args.channel)
    if routed != args.category:
        print(f"refused: the router sends '{args.channel}' to {routed or 'nowhere'}, "
              f"not {args.category} - a card here would move next scan")
        return 1

    data_dir = args.data_dir or os.path.join(ROOT, "data", "channels")
    path = os.path.join(data_dir, CATEGORY_FILE[args.category])
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    cards: List[Dict[str, Any]] = payload.get("channels") or []
    if any(str(c.get("name") or "").casefold() == args.channel.casefold() for c in cards):
        print(f"'{args.channel}' already has a card; use add-proven-route.py")
        return 0

    # The same channel sitting in another category is not a second channel, it
    # is the card the router used to produce. JOO MUSIC routed to Other until it
    # was added to the Indian identity registry, and writing the Indian card
    # without removing the Other one put the identical URL on the site twice -
    # which the catalogue's own alias guard catches as two cards collapsing into
    # one group, with one of them destined to be lost.
    elsewhere = _remove_from_other_categories(
        data_dir, args.category, args.channel, dry_run=args.dry_run
    )
    for category, count in elsewhere:
        print("removed the %s copy of '%s' (%d card%s left there); the router "
              "now sends it to %s"
              % (category, args.channel, count, "" if count == 1 else "s",
                 args.category))

    height = measure_height(args.url)
    below_floor_reason = ""
    if height and height < args.floor:
        # The same named-exception rule the verifier applies, called through the
        # verifier so the two cannot disagree: the channel must be listed in
        # config/settings.json under resolution.below_floor_exceptions AND
        # route_preference must hold a sustained-playback proof for this exact
        # route. Config alone publishes nothing.
        from scanner import verifier

        settings = json.loads(
            open(os.path.join(ROOT, "config", "settings.json"), encoding="utf-8").read()
        )
        allowed, why = verifier._below_floor_exception(
            {"name": args.channel, "url": args.url, "resolution_height": height,
             "source_pipeline": "tv"},
            settings,
        )
        if not allowed:
            print(f"refused: measured {height}p, below the {args.floor}p floor "
                  f"and no named exception applies ({why or 'not listed'})")
            return 1
        below_floor_reason = why
        print(f"below the floor at {height}p by a named exception: {why[:90]}")
    if not height:
        print("note: the stream would not declare a height; the card is written "
              "without one and the Pages validator will judge it")

    card: Dict[str, Any] = {
        "id": slug(args.channel),
        "name": args.channel,
        "primary_stream_key": hashlib.sha256(args.url.encode("utf-8")).hexdigest(),
        "sport_type": "",
        "logo": "",
        "category": args.category,
        "url": args.url,
        "header_profile": args.header_profile,
        "proxy_mode": args.proxy_mode,
        "stream_type": args.stream_type,
        "requires_headers": False,
        "inherit_manifest_query": False,
        "verification_mode": "phase1_120s_browser_x2",
        "verification_status": "verified_sustained_playback",
        "verification_badge": "Verified",
        "verified": True,
        "publish_allowed": True,
        "source_pipeline": "tv",
        "original_source_pipeline": "tv",
        "content_kind": "live_tv",
        "routing_reason": "configured_tv_pipeline",
        "source_id": args.source_id,
        "metadata_only": False,
        "available_link_count": 1,
        "backups": [],
        "source_ids": [args.source_id],
        "verification_note": (
            "Two independent 120 s browser sessions each played this route to "
            f"the full PASS floor. See {args.report}."
        ),
    }
    if height:
        card["resolution"] = label_for(height)
        card["resolution_height"] = height
    if below_floor_reason:
        card["resolution_exception"] = True
        card["quality_below_preferred"] = True
        card["quality_policy_note"] = (
            f"Below the {args.floor}p floor at {height}p by a named per-channel "
            f"exception with browser proof: {below_floor_reason}"
        )

    print("card: %s -> %s (%s)"
          % (args.channel, CATEGORY_FILE[args.category],
             f"{height}p" if height else "no declared height"))

    if args.dry_run:
        print("\n(dry run: nothing written)")
        return 0

    cards.append(card)
    cards.sort(key=lambda c: str(c.get("name") or "").casefold())
    payload["channels"] = cards
    payload["count"] = len(cards)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")

    written, why = sustained_proof.record("channel", args.channel, evidence)
    print(f"proof registry: {'recorded' if written else why}")
    pref, pref_why = route_preference.record(
        "channel", args.channel, args.url, evidence
    )
    print(f"route preference: {'recorded' if pref else pref_why}")
    print(f"wrote {os.path.relpath(path, ROOT)}: {len(cards)} card(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
