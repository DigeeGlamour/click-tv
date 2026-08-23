#!/usr/bin/env python3
"""Try to make the raw-TS route decode, by varying the player rather than the URL.

Zee Bangla fails the same way every time: MEDIA_ERR_DECODE, audio decoding while
the video decoder produces no frames, on 1080i H.264 with zero IDR frames. Four
120 s sessions confirmed it is reproducible. What has NOT been tried is whether a
different mpegts.js build or a different demuxer configuration decodes it - and
that is a cheap question with a real chance of a yes, so leaving it unasked was
not defensible.

Each variant gets a short window on purpose. The question here is binary - does a
frame appear at all - and the FAIL floor already says no first frame within 30 s
is a failure. A variant that produces frames earns a full 120 s acceptance run
afterwards; one that does not has answered the question.

Nothing about the stream URL, credentials or the site's configuration is changed.
This only asks which player settings can decode bytes that already arrive.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

CHROME_ARGS = [
    "--no-sandbox", "--disable-dev-shm-usage", "--mute-audio",
    "--autoplay-policy=no-user-gesture-required", "--disable-gpu",
    "--use-gl=swiftshader",
]

#: mpegts.js builds to try. 1.7.3 is what the site loads today.
BUILDS = [
    ("1.7.3", "https://cdn.jsdelivr.net/npm/mpegts.js@1.7.3/dist/mpegts.js"),
    ("1.8.0", "https://cdn.jsdelivr.net/npm/mpegts.js@1.8.0/dist/mpegts.js"),
    ("1.8.2", "https://cdn.jsdelivr.net/npm/mpegts.js@1.8.2/dist/mpegts.js"),
]

#: Configurations worth trying, each with the reason it might matter.
CONFIGS = [
    ("site_default", {
        "enableWorker": True, "lazyLoad": True,
        "liveBufferLatencyChasing": False, "stashInitialSize": 1024 * 1024,
    }),
    ("no_worker", {
        # A worker-thread demuxer and the main-thread one have taken different
        # code paths for malformed or unusual streams before now.
        "enableWorker": False, "lazyLoad": True, "stashInitialSize": 1024 * 1024,
    }),
    ("large_stash_no_worker", {
        # More bytes buffered before the first append gives the demuxer a chance
        # to find a usable entry point in a stream with no IDR frame.
        "enableWorker": False, "lazyLoad": False, "stashInitialSize": 8 * 1024 * 1024,
    }),
    ("not_live_no_worker", {
        # Declared as non-live: mpegts.js relaxes its latency handling and does
        # not try to seek to the live edge, which is where an open-GOP stream
        # with no random-access point is most likely to fail.
        "enableWorker": False, "lazyLoad": False,
        "stashInitialSize": 4 * 1024 * 1024, "isLive": False,
    }),
    ("fix_audio_timestamp_off", {
        "enableWorker": False, "lazyLoad": False,
        "stashInitialSize": 4 * 1024 * 1024,
        "fixAudioTimestampGap": False,
    }),
]

PROBE = r"""
async ([buildUrl, config, url, seconds]) => {
  const out = {frames: 0, audioBytes: 0, currentTime: 0, errors: [], loaded: false,
               readyState: 0, videoWidth: 0};
  // Load the requested build fresh, replacing any global already present.
  try {
    delete window.mpegts;
    await new Promise((res, rej) => {
      const s = document.createElement('script');
      s.src = buildUrl; s.onload = res; s.onerror = () => rej(new Error('script load failed'));
      document.head.appendChild(s);
    });
  } catch (e) { out.errors.push('build load: ' + e); return out; }
  if (typeof window.mpegts === 'undefined') { out.errors.push('global missing'); return out; }
  out.loaded = true;
  if (!window.mpegts.isSupported()) { out.errors.push('isSupported false'); return out; }

  const video = document.createElement('video');
  video.muted = true; video.autoplay = true; video.playsInline = true;
  video.style.cssText = 'position:fixed;left:-9999px;width:320px;height:180px';
  document.body.appendChild(video);

  const isLive = config.isLive !== false;
  const opts = Object.assign({}, config); delete opts.isLive;
  let player = null;
  try {
    player = window.mpegts.createPlayer({type:'mpegts', isLive, url}, opts);
    player.on(window.mpegts.Events.ERROR, (a,b,c) => {
      if (out.errors.length < 5) out.errors.push(a + '/' + b);
    });
    player.attachMediaElement(video);
    player.load();
  } catch (e) { out.errors.push('createPlayer: ' + e); return out; }

  video.addEventListener('error', () => out.errors.push(
    'element error ' + (video.error ? video.error.code : '?')));

  try {
    await Promise.race([
      video.play().catch((e) => out.errors.push('play: ' + e)),
      new Promise((r) => setTimeout(r, 8000)),
    ]);
  } catch (e) { /* recorded above */ }

  for (let i = 0; i < seconds; i++) {
    await new Promise((r) => setTimeout(r, 1000));
    out.frames = video.webkitDecodedFrameCount || 0;
    out.audioBytes = video.webkitAudioDecodedByteCount || 0;
    out.currentTime = Number((video.currentTime || 0).toFixed(2));
    out.readyState = video.readyState;
    out.videoWidth = video.videoWidth || 0;
    if (out.frames > 0) break;   // the question is answered
  }
  try { player.pause(); player.unload(); player.detachMediaElement(); player.destroy(); } catch (e) {}
  try { video.remove(); } catch (e) {}
  return out;
}
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--targets", required=True)
    ap.add_argument("--seconds", type=int, default=35)
    ap.add_argument("--base", default="https://clicktv.pages.dev/")
    ap.add_argument("--out", default="reports/mpegts-variant-probe.json")
    args = ap.parse_args()

    with open(args.targets, "r", encoding="utf-8") as handle:
        targets = json.load(handle)

    from playwright.sync_api import sync_playwright

    results = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=CHROME_ARGS)
        try:
            for target in targets:
                context = browser.new_context(viewport={"width": 1280, "height": 720})
                page = context.new_page()
                page.goto(args.base, wait_until="commit", timeout=60000)
                page.wait_for_function(
                    "() => typeof buildAttemptPlan === 'function'", timeout=45000
                )
                page.wait_for_timeout(4000)
                # Resolve the playable URL through the site's own plan.
                url = page.evaluate(
                    """(item) => {
                        const plan = buildAttemptPlan(item) || [];
                        for (const a of plan) {
                          const src = a.source || {};
                          if (a.route === 'proxy' && a.proxy) return buildProxyUrl(a.proxy, src);
                          if (src.url) return src.url;
                        }
                        return '';
                    }""",
                    {
                        "name": target.get("name"), "url": target.get("url"),
                        "stream_type": target.get("stream_type"),
                        "proxy_mode": target.get("proxy_mode"),
                        "header_profile": target.get("header_profile"),
                        "backups": [], "_sourceKind": "channel",
                        "content_kind": "live_tv",
                    },
                )
                if not url:
                    print(f"{target.get('name')}: no playable url", flush=True)
                    context.close()
                    continue
                page.set_default_timeout((args.seconds + 90) * 1000)
                for build_name, build_url in BUILDS:
                    for config_name, config in CONFIGS:
                        try:
                            raw = page.evaluate(
                                PROBE, [build_url, config, url, args.seconds]
                            )
                        except Exception as exc:
                            raw = {"errors": [f"harness: {str(exc)[:120]}"],
                                   "frames": 0, "audioBytes": 0}
                        decoded = int(raw.get("frames") or 0) > 0
                        results.append({
                            "name": target.get("name"),
                            "build": build_name,
                            "config": config_name,
                            "decoded_video": decoded,
                            "frames": raw.get("frames"),
                            "audio_bytes": raw.get("audioBytes"),
                            "current_time": raw.get("currentTime"),
                            "video_width": raw.get("videoWidth"),
                            "errors": raw.get("errors"),
                        })
                        print(f"  {target.get('name')} | {build_name:<6} | "
                              f"{config_name:<22} | frames={raw.get('frames')} "
                              f"audio={raw.get('audioBytes')} "
                              f"{'DECODED' if decoded else ''}", flush=True)
                context.close()
        finally:
            browser.close()

    wins = [r for r in results if r["decoded_video"]]
    payload = {
        "mode": "mpegts_variant_probe",
        "note": (
            "Asks only whether any build or configuration decodes a video frame "
            "at all. A variant that does earns a full 120 s acceptance run; the "
            "short window here is not a substitute for one."
        ),
        "variants_tried": len(results),
        "variants_that_decoded": len(wins),
        "winners": wins,
        "results": results,
    }
    path = os.path.join(ROOT, args.out)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    print(f"\n{len(wins)}/{len(results)} variants decoded video")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
