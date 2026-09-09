#!/usr/bin/env python3
"""What this browser's MSE will and will not accept, measured not assumed.

Phase 1 recorded Zee Bangla failing with MEDIA_ERR_DECODE while its audio track
decoded normally. That is a claim about the DECODER, and a claim about a decoder
has to be checked against the decoder rather than argued from the container
bytes. This asks the same browser, directly, which codec strings its
MediaSource accepts - including the interlaced-profile string the Phase 0 parse
found on that route.

It changes nothing and touches no stream: MediaSource.isTypeSupported is a pure
query about local decoder support.

Usage: python3 scripts/decoder-capability-probe.py [--out reports/decoder-capability.json]
"""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Each entry is (label, mime). The H.264 strings are the ones that matter for
#: the route under investigation: High profile level 4.0 is what Phase 0 parsed
#: out of the Zee Bangla SPS, and MPEG-1/2 Layer II is its audio.
PROBES = [
    ("h264_high_4.0_in_mp4", 'video/mp4; codecs="avc1.640028"'),
    ("h264_high_4.0_in_mp2t", 'video/mp2t; codecs="avc1.640028"'),
    ("h264_baseline_in_mp4", 'video/mp4; codecs="avc1.42E01E"'),
    ("h264_main_in_mp4", 'video/mp4; codecs="avc1.4D401F"'),
    ("aac_lc", 'audio/mp4; codecs="mp4a.40.2"'),
    ("mpeg_audio_layer2_bare", "audio/mpeg"),
    ("mpeg_audio_layer2_explicit", 'audio/mpeg; codecs="mp2"'),
    ("mpeg_audio_in_mp2t", 'video/mp2t; codecs="mp4a.40.34"'),
    ("mp2t_h264_and_mpeg_audio", 'video/mp2t; codecs="avc1.640028,mp4a.40.34"'),
    ("hevc_hvc1", 'video/mp4; codecs="hvc1.1.6.L93.B0"'),
    ("hevc_hev1", 'video/mp4; codecs="hev1.1.6.L93.B0"'),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="reports/decoder-capability.json")
    ap.add_argument("--base", default="https://clicktv.pages.dev/")
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    payload = {
        "mode": "decoder_capability_probe",
        "note": (
            "MediaSource.isTypeSupported answers only for THIS browser build. A "
            "'false' here is evidence of a decoder limitation, which the model "
            "classifies as advisory:device_or_browser_unsupported: "
            "non-escalatable and capped at environment scope. It is never "
            "evidence that a stream is broken."
        ),
        "engines": {},
    }

    with sync_playwright() as pw:
        for engine_name in ("chromium", "webkit"):
            launcher = getattr(pw, engine_name)
            args_list = (
                ["--no-sandbox", "--disable-dev-shm-usage"]
                if engine_name == "chromium"
                else []
            )
            try:
                browser = launcher.launch(args=args_list)
            except Exception as exc:
                payload["engines"][engine_name] = {"error": str(exc)[:200]}
                continue
            try:
                page = browser.new_context().new_page()
                page.goto(args.base, wait_until="commit", timeout=60000)
                page.wait_for_timeout(2500)
                result = page.evaluate(
                    """(probes) => {
                        const out = {
                          userAgent: navigator.userAgent,
                          mediaSource: typeof MediaSource !== 'undefined',
                          support: {},
                        };
                        for (const [label, mime] of probes) {
                          try {
                            out.support[label] = (typeof MediaSource !== 'undefined')
                              ? MediaSource.isTypeSupported(mime) : null;
                          } catch (e) { out.support[label] = 'threw: ' + e; }
                        }
                        return out;
                    }""",
                    PROBES,
                )
                result["mime_by_label"] = {label: mime for label, mime in PROBES}
                payload["engines"][engine_name] = result
            except Exception as exc:
                payload["engines"][engine_name] = {"error": str(exc)[:200]}
            finally:
                browser.close()

    target = os.path.join(ROOT, args.out)
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    with open(target, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")

    for engine, data in payload["engines"].items():
        print(f"\n{engine}:")
        if "error" in data:
            print(f"  unavailable: {data['error']}")
            continue
        for label, ok in (data.get("support") or {}).items():
            print(f"  {'yes' if ok is True else 'NO ' if ok is False else '?  '} {label}")
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
