#!/usr/bin/env python3
"""Find a Zee Bangla route this browser can actually decode.

The current route is settled by measurement: 1080i H.264 with zero IDR frames,
and fifteen mpegts.js build/config variants each decode exactly one frame before
stopping. The player side is exhausted, so the remaining question is whether the
sources already configured contain a route with a different structure - one with
real IDR frames, or HLS instead of raw TS.

This asks that structurally, IP-independently, before spending a browser session
on anything. Identical bytes give the same answer from any egress, so a keyframe
count is worth measuring here even though a reachability result would not be.

It changes nothing. No catalogue file is written and no route is promoted; a
candidate that looks good earns a real 120 s browser run, which is a separate
step.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from scanner import route_evidence as rev  # noqa: E402

#: The owner's rule, and it matters more than any structural finding here:
#: "Zee Bangla" is the channel. "Zee Bangla HD" is another SOURCE of that same
#: channel and is acceptable. "Zee Bangla Cinema" and "Zee Bangla Sonar" are
#: DIFFERENT CHANNELS entirely - substituting one of those would put the wrong
#: programme on the card, which is worse than a channel that stutters. The first
#: pass of this scout listed them as candidates; that was wrong.
#: Playlists prefix names with a group tag - "[BD] Zee Bangla" is the same
#: channel as "Zee Bangla", carried by a Bangladeshi CDN. The first version of
#: this pattern anchored at the start of the string and rejected it as "name
#: does not match the channel", which lost a real HLS candidate. Leading
#: bracketed or parenthesised tags are stripped before matching; the
#: DIFFERENT_CHANNEL_WORDS check below still runs on the untouched name, so no
#: prefix can smuggle Cinema or Sonar past the owner's rule.
GROUP_TAG = re.compile(r"^\s*(?:[\[(][^\])]*[\])]\s*)+")

SAME_CHANNEL = re.compile(
    r"^\s*zee\s*bangla(\s*(hd|sd|fhd|full\s*hd))?\s*$", re.IGNORECASE
)

#: Qualifiers that mark a separate channel rather than a variant of one.
DIFFERENT_CHANNEL_WORDS = (
    "cinema", "sonar", "sansar", "movies", "music", "natok", "cine",
)


def is_same_channel(name: str) -> tuple:
    """(accepted, reason) for a candidate name."""
    text = str(name or "")
    for word in DIFFERENT_CHANNEL_WORDS:
        if word in text.lower():
            return False, f"'{word}' marks a different channel, not another source"
    if SAME_CHANNEL.match(GROUP_TAG.sub("", text)):
        return True, "same channel"
    return False, "name does not match the channel"


UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
)
SAMPLE = 1_500_000
TIMEOUT = 20


def fetch(url: str, limit: int = SAMPLE) -> tuple:
    try:
        request = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.status, response.read(limit), response.headers.get(
                "Content-Type"
            ) or ""
    except urllib.error.HTTPError as exc:
        return exc.code, b"", ""
    except Exception as exc:  # noqa: BLE001
        return type(exc).__name__, b"", ""


def ts_keyframes(data: bytes) -> dict:
    """Count IDR NALs and non-IDR I-slices in an MPEG-TS sample.

    The distinction is the whole point: an IDR is a random-access point a decoder
    can start from, an open-GOP I-slice is not - which is exactly why the current
    route renders one frame and stops.
    """
    if len(data) < 376:
        return {"parsed": False}
    offset = data.find(b"\x47")
    if offset < 0:
        return {"parsed": False}
    idr = non_idr_i = sps = packets = 0
    position = offset
    while position + 188 <= len(data):
        packet = data[position:position + 188]
        position += 188
        if packet[0] != 0x47:
            shifted = data.find(b"\x47", position)
            if shifted < 0:
                break
            position = shifted
            continue
        packets += 1
        adaptation = (packet[3] >> 4) & 0x03
        payload_start = 4
        if adaptation in (2, 3):
            payload_start += 1 + packet[4]
        if payload_start >= 188:
            continue
        payload = packet[payload_start:]
        for match in re.finditer(b"\x00\x00\x01", payload):
            index = match.end()
            if index >= len(payload):
                continue
            nal = payload[index] & 0x1F
            if nal == 5:
                idr += 1
            elif nal == 1:
                non_idr_i += 1
            elif nal == 7:
                sps += 1
    return {
        "parsed": True, "packets": packets, "idr_nals": idr,
        "non_idr_slices": non_idr_i, "sps": sps,
        "has_random_access_point": idr > 0,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", required=True)
    ap.add_argument("--out", default="reports/zee-alternative-scout.json")
    args = ap.parse_args()

    with open(args.candidates, "r", encoding="utf-8") as handle:
        candidates = json.load(handle)

    results = []
    rejected = []
    for candidate in candidates:
        accepted, reason = is_same_channel(candidate.get("name"))
        if not accepted:
            rejected.append({"name": candidate.get("name"), "reason": reason})
            continue
        url = str(candidate.get("url") or "")
        host = urllib.parse.urlsplit(url).hostname or ""
        status, body, content_type = fetch(url)
        row = {
            "name": candidate.get("name"),
            "host": host,
            "source": candidate.get("from"),
            "url_public_template": rev.redact_public_template(url),
            "status": status,
            "content_type": content_type[:50],
            "bytes": len(body),
            "kind": None,
            "keyframes": None,
        }
        text = body[:400].decode("utf-8", "replace")
        if text.lstrip().startswith("#EXTM3U"):
            row["kind"] = "hls_manifest"
            # An HLS manifest means segmented delivery, which carries a
            # random-access point per segment by construction - the property the
            # current raw-TS route lacks.
            row["variant_lines"] = text.count("#EXT-X-STREAM-INF")
            row["segment_lines"] = text.count("#EXTINF")
        elif body[:1] == b"\x47" or b"\x47" in body[:376]:
            row["kind"] = "raw_mpegts"
            row["keyframes"] = ts_keyframes(body)
        elif body:
            row["kind"] = "other"
        results.append(row)
        mark = ""
        if row["kind"] == "hls_manifest":
            mark = "HLS (segmented)"
        elif row["keyframes"] and row["keyframes"].get("has_random_access_point"):
            mark = f"IDR={row['keyframes']['idr_nals']}  <-- decodable"
        elif row["keyframes"]:
            mark = (f"IDR=0 I-slices={row['keyframes']['non_idr_slices']} "
                    "(same problem)")
        print(f"  {str(row['name'])[:26]:<28} {host[:30]:<32} "
              f"{str(status):<12} {mark}", flush=True)

    promising = [
        r for r in results
        if r["kind"] == "hls_manifest"
        or (r["keyframes"] or {}).get("has_random_access_point")
    ]
    payload = {
        "mode": "zee_alternative_scout",
        "note": (
            "Structural only, and structure is IP-independent - identical bytes "
            "answer the same from any egress. A promising candidate here has NOT "
            "been proven to play; it has earned a 120 s browser acceptance run, "
            "which is a separate step."
        ),
        "candidates": len(results),
        "rejected_as_a_different_channel": len(rejected),
        "rejected": rejected,
        "promising": len(promising),
        "promising_routes": promising,
        "results": results,
    }
    path = os.path.join(ROOT, args.out)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if rev.evidence_contains_forbidden_material(payload):
        # Redacted templates should prevent this; refusing beats leaking.
        payload = {"mode": "zee_alternative_scout",
                   "error": "withheld: payload carried forbidden material",
                   "candidates": len(results), "promising": len(promising)}
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    print(f"\npromising: {len(promising)}/{len(results)}")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
