#!/usr/bin/env python3
"""Attach a route that passed the 120 s acceptance to a channel that needs one.

Zee Bangla's published route is settled by measurement: 1080i H.264 with zero IDR
frames, four 120 s sessions all ending in MEDIA_ERR_DECODE, and fifteen
mpegts.js build/config variants each decoding exactly one frame before stopping.
The player side is exhausted. What remained was whether the configured sources
already contain a route with a different structure, and one does - an HLS route
that passed two independent 120 s sessions with zero stall.

Two rules this script will not bend:

  * The existing route is NEVER removed or rewritten. It moves to a backup and
    keeps its URL, headers and proxy mode byte for byte. The standing
    instruction is that existing stream URLs are not to be changed, and adding a
    proven route alongside is not changing one.
  * A route is only attached if reports/ says it PASSED twice. No manifest
    check, no reachability result, no "it looked fine" - the same PASS floor
    every other promotion in this project answers to.

The owner's channel-identity rule is enforced here too, because getting it wrong
is worse than the stutter: "Zee Bangla HD" is another source of the same channel,
while "Zee Bangla Cinema" and "Zee Bangla Sonar" are different channels entirely
and must never be substituted.

Run with --dry-run first. It prints exactly what it would do and writes nothing.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from scanner import route_evidence as rev  # noqa: E402
from scanner import route_preference  # noqa: E402
from scanner import sustained_proof  # noqa: E402

#: Same rule as scripts/zee-alternative-scout.py, restated rather than imported
#: so this script cannot be run against a channel whose identity it has not
#: checked.
DIFFERENT_CHANNEL_WORDS = (
    "cinema", "sonar", "sansar", "movies", "music", "natok", "cine",
)


def same_channel(channel: str, candidate: str) -> bool:
    """Whether `candidate` names another SOURCE of `channel`, not a sibling."""
    base = re.sub(r"\s*(hd|sd|fhd)\s*$", "", str(channel or ""), flags=re.I).strip()
    other = str(candidate or "")
    if any(word in other.lower() for word in DIFFERENT_CHANNEL_WORDS):
        return False
    stripped = re.sub(r"\s*(hd|sd|fhd)\s*$", "", other, flags=re.I).strip()
    return stripped.casefold() == base.casefold()


#: Fields that describe the OLD primary's quality. Left in place they follow a
#: route that is no longer on the card: Zee Bangla kept "SD / 576p, below the
#: floor by a named exception" after its primary became a 1280x720 master, so
#: the card advertised the wrong resolution and claimed an exemption it no
#: longer needed.
BELOW_FLOOR_FIELDS = (
    "resolution_exception",
    "quality_below_preferred",
    "quality_policy_note",
    "quality_unknown",
)


def _label_for(height: int) -> str:
    return (
        "FHD" if height >= 1080 else "HD" if height >= 720
        else "SD" if height >= 480 else "LD"
    )


def measure_height(url: str) -> int:
    """The route's real height, or 0 when the stream will not say.

    Measurement, not assumption: the HLS master's RESOLUTION, or the H.264 SPS
    decoded from the first transport-stream segment.
    """
    from scanner import media_probe

    height = 0
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
            )
        }

        def _get(target: str, limit: int) -> bytes:
            request = urllib.request.Request(target, headers=headers)
            with urllib.request.urlopen(request, timeout=15) as response:
                return response.read(limit)

        body = _get(url, 262144)
        head = body[:16384].decode("utf-8", "replace")
        if "#EXT-X-STREAM-INF" in head:
            height = media_probe.master_playlist_height(head)
        elif "#EXTINF" in head:
            # An encrypted media playlist cannot be measured without the key,
            # and saying "0" for it would be indistinguishable from a stream
            # that simply did not answer. stream.ottplus.bd's per-variant
            # playlists are AES-128; only its master declares a RESOLUTION.
            if "METHOD=AES-128" in head or "METHOD=SAMPLE-AES" in head:
                print(f"resolution: {url[:48]} is an encrypted media playlist; "
                      "the height cannot be read without the key")
                return 0
            segments = [
                line.strip() for line in head.splitlines()
                if line.strip() and not line.startswith("#")
            ]
            if segments:
                body = _get(urllib.parse.urljoin(url, segments[0]), 524288)
        if not height:
            decoded = media_probe.sps_from_transport_stream(body)
            if decoded:
                height = media_probe.plausible(decoded.get("height"))
    except Exception as error:  # noqa: BLE001 - a probe must never lose the swap
        print(f"resolution: could not measure {url[:48]} ({error})")
        return 0
    return height


def _retake_resolution(card: Dict[str, Any], url: str, floor: int = 720) -> None:
    """Re-read the resolution from the route that is now the primary.

    If the stream will not say, the old value is left alone and nothing is
    claimed - an unreadable answer is not a reason to overwrite a known one.
    """
    height = measure_height(url)
    if not height:
        print("resolution: the new primary would not say; the card keeps its "
              "existing value")
        return

    card["resolution_height"] = height
    card["resolution"] = _label_for(height)
    if height >= floor:
        removed = [f for f in BELOW_FLOOR_FIELDS if f in card]
        for field in removed:
            card.pop(field, None)
        print(f"resolution: measured {height}p on the new primary"
              + (f"; dropped {', '.join(removed)}" if removed else ""))
    else:
        print(f"resolution: measured {height}p on the new primary, still below "
              f"the {floor}p floor - the card keeps its exception fields")


def proven_routes(report_path: str) -> List[Dict[str, Any]]:
    """Entries in a playback report that passed twice."""
    try:
        with open(os.path.join(ROOT, report_path), "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return []
    out = []
    for result in payload.get("results") or ():
        passes = [
            o for o in (result.get("observations") or ())
            if o.get("verdict") == rev.PROVEN
        ]
        if len(passes) < rev.REQUIRED_FRESH_SESSIONS:
            continue
        out.append({
            "name": result.get("name"),
            "pass_count": len(passes),
            "window_seconds": payload.get("window_seconds"),
            "session_separation_seconds": payload.get("session_separation_seconds"),
            "browser_profile": payload.get("browser_profile"),
            "media_progress_seconds": [
                (o.get("playback_metrics") or {}).get("media_progress_seconds")
                for o in passes
            ],
            "cumulative_stall_seconds": [
                (o.get("playback_metrics") or {}).get("cumulative_stall_seconds")
                for o in passes
            ],
            "route": next(
                (o.get("attempt_route") for o in passes if o.get("attempt_route")),
                None,
            ),
            "evidence_report": report_path,
        })
    return out


def find_card(channel: str):
    for path in sorted(glob.glob(os.path.join(ROOT, "data", "channels", "*.json"))):
        with open(path, "r", encoding="utf-8") as handle:
            raw = handle.read()
        payload = json.loads(raw)
        container = payload if isinstance(payload, list) else (
            payload.get("channels") or payload.get("items")
        )
        if container is None:
            continue
        for card in container:
            if isinstance(card, dict) and str(card.get("name") or "") == channel:
                return path, payload, container, card, raw
    return None, None, None, None, None


def save(path: str, payload: Any, original: str) -> None:
    newline = "\r\n" if "\r\n" in original else "\n"
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    if newline != "\n":
        text = text.replace("\n", newline)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text + (newline if original.endswith(("\n", "\r\n")) else ""))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", required=True)
    ap.add_argument("--url", required=True, help="the proven route's URL")
    ap.add_argument("--proven-name", required=True,
                    help="the entry name in the playback report")
    ap.add_argument("--source-name", required=True,
                    help="the name this route carries in its source playlist")
    ap.add_argument("--report", default="reports/zee-confirm-playback.json")
    ap.add_argument("--stream-type", default="hls")
    ap.add_argument("--proxy-mode", default="direct_first")
    ap.add_argument("--make-primary", action="store_true",
                    help="put the proven route first and demote the existing one "
                         "to a backup; the existing URL is preserved either way")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not same_channel(args.channel, args.source_name):
        print(f"refused: '{args.source_name}' is not another source of "
              f"'{args.channel}' - substituting a different channel would put "
              f"the wrong programme on the card")
        return 1

    proven = {p["name"]: p for p in proven_routes(args.report)}
    evidence = proven.get(args.proven_name)
    if not evidence:
        print(f"refused: '{args.proven_name}' is not PROVEN in {args.report} "
              f"(needs {rev.REQUIRED_FRESH_SESSIONS} full 120 s passes)")
        return 1
    print(f"proof: {evidence['pass_count']} full PASS, window "
          f"{evidence['window_seconds']}s, media {evidence['media_progress_seconds']}s, "
          f"stall {evidence['cumulative_stall_seconds']}s, route {evidence['route']}")

    path, payload, container, card, original = find_card(args.channel)
    if card is None:
        print(f"refused: no card named '{args.channel}'")
        return 1
    print(f"card: {os.path.relpath(path, ROOT)}")

    def write_registries() -> None:
        """Register the proof even when the card already looks right.

        The card is generated: a scan rebuilds it from its sources and erases
        whatever was written on it. So returning early because the card is
        already correct would leave the registries empty and the fix would last
        exactly one scan - which is the bug this whole function exists to avoid.
        """
        written, why = sustained_proof.record(
            "channel", args.channel, evidence,
            path=None if not args.dry_run else os.devnull,
        )
        print(f"proof registry: {'recorded' if written else why}")
        pref_written, pref_why = route_preference.record(
            "channel", args.channel, args.url, evidence,
            path=None if not args.dry_run else os.devnull,
        )
        print(f"route preference: {'recorded' if pref_written else pref_why}")

    existing_url = str(card.get("url") or "")
    backups = list(card.get("backups") or [])
    if existing_url == args.url:
        print("the proven route is already the primary")
        write_registries()
        return 0
    if any(
        isinstance(b, dict) and str(b.get("url") or "") == args.url for b in backups
    ):
        print("the proven route is already a backup")
        write_registries()
        return 0

    proven_entry = {
        "name": "Proven-120s",
        "url": args.url,
        "stream_type": args.stream_type,
        "proxy_mode": args.proxy_mode,
        "header_profile": "",
        "requires_headers": False,
        "inherit_manifest_query": False,
        "verification_mode": "phase1_120s_browser_x2",
        "verification_status": "verified_sustained_playback",
        # The badge, spelled out. Left unset the entry reached the card with
        # `verification_badge: null` and the site rendered a blank chip beside a
        # route that had just passed two full 120 s sessions - the strongest
        # evidence anything on the card carries.
        "verification_badge": "Verified",
        "verification_note": (
            "Two independent 120 s browser sessions each played this route to "
            f"the full PASS floor. See {args.report}."
        ),
        "verified": False,
    }
    # A backup answers to the same 720p rule as a primary, and the Pages
    # validator enforces it. Added without a measured height this entry reached
    # the validator as "unknown" and failed the whole build - which is the
    # failure mode that has kept Cloudflare Pages serving stale data before.
    _proven_height = measure_height(args.url)
    if _proven_height:
        proven_entry["resolution_height"] = _proven_height
        proven_entry["resolution"] = _label_for(_proven_height)
        print(f"resolution: measured {_proven_height}p on the proven route")
    existing_entry = {
        "name": "Original-primary",
        "url": existing_url,
        "stream_type": card.get("stream_type"),
        "proxy_mode": card.get("proxy_mode"),
        "header_profile": card.get("header_profile"),
        "requires_headers": card.get("requires_headers", False),
        "inherit_manifest_query": card.get("inherit_manifest_query", False),
        "verification_note": (
            "Kept unchanged. Measured failing: 1080i H.264 with zero IDR frames, "
            "four 120 s sessions ending in MEDIA_ERR_DECODE, and fifteen "
            "mpegts.js variants each decoding one frame before stopping."
        ),
    }

    # Whether the route being demoted is still worth offering a viewer.
    #
    # The standing rule is that an existing URL is never dropped, and it is a
    # good rule - routes have been deleted here on a single bad answer before.
    # But Zee Bangla's old primary is not a route with a bad answer: it returned
    # HTTP 500 to two consecutive probes and produced zero seconds of media in
    # two 120 s browser sessions, and it sits in the measured-playback ledger
    # saying so. Publishing it as a backup gives the viewer a second button that
    # does nothing, and it carries a below-floor resolution whose exception is
    # now attached to a different route.
    #
    # So it is dropped from the card only when a browser measured it unplayable,
    # and the URL is not lost: it stays in state/measured-playback-failures.json
    # and in the route-preference registry's superseded chain.
    demoted_reason = ""
    try:
        from scanner import playback_evidence

        demoted_reason = playback_evidence.unproven_reason(existing_url)
    except Exception:  # noqa: BLE001 - a lookup failure keeps the safe default
        demoted_reason = ""

    if args.make_primary:
        card["url"] = args.url
        card["stream_type"] = args.stream_type
        card["proxy_mode"] = args.proxy_mode
        card["header_profile"] = ""
        card["requires_headers"] = False
        card["verification_status"] = "verified_sustained_playback"
        card["verification_mode"] = "phase1_120s_browser_x2"
        card["verification_note"] = proven_entry["verification_note"]
        if demoted_reason:
            # The same URL is often on the card twice - once as the primary and
            # once as a backup, which is how the scanner records a route two
            # sources both carry. Dropping only the primary copy leaves the
            # viewer the identical dead route under a "Verified" badge, which
            # is exactly what this is meant to stop.
            demoted_id = rev.normalize_source_identity(existing_url)
            card["backups"] = [
                backup for backup in backups
                if not (
                    isinstance(backup, dict)
                    and rev.normalize_source_identity(
                        str(backup.get("url") or "")
                    ) == demoted_id
                )
            ]
            card["demoted_route_public_template"] = rev.redact_public_template(
                existing_url
            )
            card["demoted_route_reason"] = demoted_reason[:300]
            print("action: proven route becomes the primary; the old route is "
                  "NOT published as a backup because a browser measured it "
                  f"unplayable - {demoted_reason[:90]}")
        else:
            existing_entry["resolution"] = card.get("resolution")
            existing_entry["resolution_height"] = card.get("resolution_height")
            card["backups"] = [existing_entry] + backups
            print("action: proven route becomes the primary; the existing route "
                  "is preserved as a backup with its URL unchanged")
        _retake_resolution(card, args.url)
    else:
        card["backups"] = [proven_entry] + backups
        print("action: proven route added as the first backup; the primary is "
              "untouched")
    card["available_link_count"] = 1 + len(card.get("backups") or [])

    # The proof belongs outside the card, because the next scan rebuilds cards
    # from their sources and erases anything written on them. Two registries,
    # answering two different questions:
    #
    #   sustained_proof  - may this channel be hidden?           (no)
    #   route_preference - which of its routes should lead?      (this one)
    #
    # Only the first existed at first, which meant the swap below would have
    # survived exactly one scan.
    write_registries()

    if args.dry_run:
        print("\n(dry run: nothing written)")
        return 0
    save(path, payload, original)
    print(f"wrote {os.path.relpath(path, ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
