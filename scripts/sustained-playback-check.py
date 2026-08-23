#!/usr/bin/env python3
"""Real 120-second sustained-playback acceptance, run in a real browser.

This is Phase 1. It is the only thing in the project that can produce a PASS,
because the model treats every shorter observation as `unknown`: HTTP 200 with a
valid manifest was measured on every hidden channel's primary while the channel
was stored as failed, and the raw-TS route delivers 10.6 MB of clean media
before a clean early EOF. Neither says the channel plays.

What makes this measurement rather than a guess:

  * The route plan comes from the site's OWN `buildAttemptPlan(item)`, so the
    URLs, proxy modes and ordering are the ones a viewer's browser would use.
    Reimplementing that here would have measured a different player.
  * Progress is read from the media element's decoder counters, not from network
    activity. Bytes arriving is not a frame being shown.
  * Sessions for the same route are separated by more than the measured CDN
    cache TTL bound (17-23 s), so two observations cannot be one cached
    response counted twice.
  * The verdict is computed by scanner.route_evidence.classify_playback, the
    same function the unit tests pin, not by this script's own opinion.

Honest scope: this runs one browser profile per invocation. A FAIL measured here
is therefore environment-scoped by the model's own rule, and reaching `global`
needs the complete declared matrix.

Usage:
  python3 scripts/sustained-playback-check.py --targets targets.json \
      [--seconds 120] [--base URL] [--sessions 1] [--out report.json]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from scanner import route_evidence as rev  # noqa: E402

DEFAULT_BASE = "https://clicktv.pages.dev/"

CHROME_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--mute-audio",
    "--autoplay-policy=no-user-gesture-required",
    "--disable-gpu",
    "--use-gl=swiftshader",
    "--disable-features=UseChromeOSDirectVideoDecoder",
    # 1080i H.264 for two minutes in a small container: keep the decoder from
    # holding more than it needs.
    "--js-flags=--max-old-space-size=512",
]

#: In-page harness. Returns the sample series; the Python side does the
#: classification so the browser cannot vote on its own verdict.
PLAY_AND_MEASURE = r"""
async ([item, seconds, attemptIndex]) => {
  const out = {
    plan_length: 0, attempt_index: attemptIndex, attempt_url_kind: null,
    proxied: null, fatal_errors: [], samples: [], announced: [],
    progressed: [], first_frame_seconds: null, startup_seconds: null,
    engine: null, notes: [],
  };
  if (typeof buildAttemptPlan !== 'function') { out.fatal_errors.push('buildAttemptPlan missing'); return out; }

  let plan;
  try { plan = buildAttemptPlan(item) || []; }
  catch (e) { out.fatal_errors.push('buildAttemptPlan threw: ' + e); return out; }
  out.plan_length = plan.length;
  if (!plan.length) { out.fatal_errors.push('empty attempt plan'); return out; }
  if (attemptIndex >= plan.length) { out.notes.push('attempt index beyond plan'); return out; }

  // A plan entry is {source, sourceIndex, route, proxy}; the playable URL is
  // built by the site's own buildProxyUrl for a proxy route, and is the source
  // URL itself for a direct route. Rebuilding either here would test a
  // different player than the one viewers run.
  const attempt = plan[attemptIndex];
  const source = attempt.source || {};
  let url = '';
  if (attempt.route === 'proxy' && attempt.proxy) {
    if (typeof buildProxyUrl !== 'function') { out.fatal_errors.push('buildProxyUrl missing'); return out; }
    url = String(buildProxyUrl(attempt.proxy, source) || '');
  } else {
    url = String(source.url || '');
  }
  out.attempt_route = attempt.route || null;
  out.attempt_url_kind = source.stream_type ||
    (typeof inferStreamType === 'function' ? inferStreamType(source) : null);
  out.proxied = attempt.route === 'proxy';
  out.plan_routes = plan.map((a) => a.route + (a.proxy ? ':proxy' : ''));
  if (!url) { out.fatal_errors.push('attempt has no url'); return out; }

  // A dedicated element: the page's own player must not be driven into the
  // site's retry ladder while a fixed window is being measured.
  const video = document.createElement('video');
  video.muted = true; video.autoplay = true; video.playsInline = true;
  video.preload = 'auto';
  video.style.cssText = 'position:fixed;left:-9999px;width:320px;height:180px';
  document.body.appendChild(video);

  const t0 = performance.now();
  const elapsed = () => (performance.now() - t0) / 1000;
  let engine = null, hls = null, mp = null;

  const fatal = (msg) => { if (out.fatal_errors.length < 6) out.fatal_errors.push(String(msg).slice(0, 200)); };

  const kind = String(out.attempt_url_kind || '').toLowerCase();
  out.notes.push('playing ' + out.attempt_route + ' as ' + (kind || 'unknown'));
  try {
    if (kind === 'mpegts' || kind === 'media' || /\.ts(\?|$)/i.test(url)) {
      // mpegts.js is lazy-loaded by the site, so the global does not exist yet
      // when this harness starts - measured: the first working 120 s run
      // recorded "mpegts.js unavailable" for all 120 samples. Use the site's
      // OWN loader so the version and settings are the ones viewers get.
      let mpegtsLib = (typeof mpegts !== 'undefined') ? mpegts : null;
      if (!mpegtsLib && typeof ensureMpegTsLibrary === 'function') {
        try { mpegtsLib = await ensureMpegTsLibrary(); }
        catch (e) { fatal('ensureMpegTsLibrary failed: ' + e); }
      }
      if (!mpegtsLib || !mpegtsLib.isSupported()) { fatal('mpegts.js unavailable'); }
      else {
        const mpegts = mpegtsLib;
        engine = 'mpegts';
        mp = mpegts.createPlayer(
          { type: 'mpegts', isLive: true, url },
          { enableWorker: true, lazyLoad: true, liveBufferLatencyChasing: false,
            stashInitialSize: 1024 * 1024 }
        );
        mp.on(mpegts.Events.ERROR, (a, b, c) => fatal('mpegts ' + a + '/' + b + ' ' + JSON.stringify(c || {})));
        mp.on(mpegts.Events.MEDIA_INFO, (info) => {
          if (info?.hasVideo && !out.announced.includes('video')) out.announced.push('video');
          if (info?.hasAudio && !out.announced.includes('audio')) out.announced.push('audio');
        });
        mp.attachMediaElement(video); mp.load();
      }
    } else if (typeof Hls !== 'undefined' && Hls.isSupported() && !/\.mp4(\?|$)/i.test(url)) {
      engine = 'hls.js';
      hls = new Hls(typeof hlsConfigFor === 'function' ? hlsConfigFor(
        (typeof currentNetworkMode === 'function' ? currentNetworkMode() : 'auto'), false) : {});
      hls.on(Hls.Events.ERROR, (_e, d) => { if (d?.fatal) fatal('hls ' + d.type + '/' + d.details); });
      hls.on(Hls.Events.MANIFEST_PARSED, (_e, d) => {
        const lv = (d?.levels || [])[0] || {};
        if (lv.videoCodec || lv.width) { if (!out.announced.includes('video')) out.announced.push('video'); }
        if (lv.audioCodec || (hls.audioTracks || []).length) { if (!out.announced.includes('audio')) out.announced.push('audio'); }
      });
      hls.loadSource(url); hls.attachMedia(video);
    } else {
      engine = 'native';
      video.src = url;
    }
  } catch (e) { fatal('engine setup: ' + e); }
  out.engine = engine;

  video.addEventListener('error', () => fatal('media element error code ' +
    (video.error ? video.error.code : '?')));

  // play() can stay PENDING forever when the media never loads - measured: the
  // harness sat past a 300 s wall clock without ever entering the sampling loop
  // because of exactly this. A rejection is informative; a hang measures
  // nothing, so the call is raced against a bounded wait and sampling starts
  // either way.
  try {
    await Promise.race([
      video.play().then(() => 'played', (e) => { out.notes.push('play() rejected: ' + e); return 'rejected'; }),
      new Promise((r) => setTimeout(() => { out.notes.push('play() still pending after 15s'); r('pending'); }, 15000)),
    ]);
  } catch (e) { out.notes.push('play() threw: ' + e); }

  let firstFrameAt = null, startupAt = null;
  let lastTime = 0, stall = 0, prevFrames = 0, prevAudioBytes = 0;
  let videoProgressed = false, audioProgressed = false;
  const total = Math.ceil(seconds);

  // Early exit, and only on a verdict that is already decided. The FAIL floor
  // says "no first frame within 30 s" is a failure, so once that is true AND an
  // unrecovered fatal error is on record, the remaining 85 s cannot change the
  // classification - it can only burn wall clock. A 215-target run is a day
  // otherwise, most of it spent re-confirming failures already established.
  // This never shortens a session that might still PASS: a stream that has
  // produced a frame, or has no fatal error, runs the full window.
  const EARLY_EXIT_AFTER_SECONDS = 35;
  for (let i = 0; i < total; i++) {
    await new Promise((r) => setTimeout(r, 1000));
    if (i >= EARLY_EXIT_AFTER_SECONDS && firstFrameAt === null && out.fatal_errors.length) {
      out.early_exit_reason =
        'no first frame within ' + EARLY_EXIT_AFTER_SECONDS +
        's and an unrecovered fatal error: the FAIL floor is already met';
      out.observed_window_seconds = Math.round(elapsed());
      break;
    }
    const t = video.currentTime || 0;
    const frames = video.webkitDecodedFrameCount || 0;
    const abytes = video.webkitAudioDecodedByteCount || 0;
    if (frames > prevFrames) videoProgressed = true;
    if (abytes > prevAudioBytes) audioProgressed = true;
    if (firstFrameAt === null && (frames > 0 || video.videoWidth > 0)) firstFrameAt = elapsed();
    if (startupAt === null && t > 0.05) startupAt = elapsed();
    const advanced = t - lastTime;
    if (startupAt !== null && advanced < 0.10) stall += 1;
    out.samples.push({ s: Math.round(elapsed()), t: Number(t.toFixed(2)),
                       rs: video.readyState, f: frames, ab: abytes,
                       stall: Number(stall.toFixed(0)) });
    lastTime = t; prevFrames = frames; prevAudioBytes = abytes;
  }

  out.first_frame_seconds = firstFrameAt;
  out.startup_seconds = startupAt;
  out.media_progress_seconds = Number((video.currentTime || 0).toFixed(2));
  out.cumulative_stall_seconds = stall;
  if (videoProgressed) out.progressed.push('video');
  if (audioProgressed) out.progressed.push('audio');
  // Nothing was announced but something played: record what actually ran, so a
  // stream whose manifest declared no codecs is not failed for that alone.
  if (!out.announced.length) out.announced = out.progressed.slice();

  try { if (hls) hls.destroy(); if (mp) { mp.destroy(); } } catch (e) { /* teardown */ }
  try { video.pause(); video.removeAttribute('src'); video.load(); video.remove(); } catch (e) { /* teardown */ }
  return out;
}
"""


def measure_once(page, item: Dict[str, Any], seconds: float, attempt_index: int) -> Dict[str, Any]:
    return page.evaluate(PLAY_AND_MEASURE, [item, seconds, attempt_index])


def reclassify(path: str) -> int:
    """Recompute every verdict in an existing report from its raw metrics.

    Classification is a pure function of the recorded metrics, which is exactly
    why the harness stores them. When the classifier is corrected mid-run - it
    was, on 2026-08-23, to separate a decoder limitation from a route failure -
    the honest move is to re-derive the verdicts rather than spend another two
    hours re-measuring bytes that have not changed. Every changed verdict is
    listed so the correction is visible rather than silent.
    """
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)

    changes = []
    for result in payload.get("results") or ():
        for observation in result.get("observations") or ():
            metrics = observation.get("playback_metrics") or {}
            if not metrics:
                continue
            before = observation.get("verdict")
            verdict, reasons = rev.classify_playback(
                metrics, delivery_path=str(observation.get("attempt_route") or "")
            )
            if verdict != before:
                changes.append(
                    {
                        "name": result.get("name"),
                        "session": observation.get("session"),
                        "from": before,
                        "to": verdict,
                    }
                )
            observation["verdict"] = verdict
            observation["reasons"] = reasons
        passes = [
            o for o in (result.get("observations") or ())
            if o.get("verdict") == rev.PROVEN
        ]
        result["pass_count"] = len(passes)
        result["proven"] = len(passes) >= rev.REQUIRED_FRESH_SESSIONS

    payload["proven_routes"] = sum(
        1 for r in (payload.get("results") or ()) if r.get("proven")
    )
    payload["reclassified"] = True
    payload["reclassification_changes"] = changes
    payload["reclassification_note"] = (
        "Verdicts were recomputed from the recorded metrics after the classifier "
        "was corrected to treat a decoder limitation as "
        "advisory:device_or_browser_unsupported rather than playback_fail. The "
        "measurements themselves are unchanged."
    )
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    print(f"reclassified {path}: {len(changes)} verdict(s) changed")
    for change in changes[:30]:
        print(f"  {change['name']} s{change['session']}: "
              f"{change['from']} -> {change['to']}")
    return 0


def run(argv: List[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--targets", default="", help="JSON list of channel items")
    ap.add_argument("--seconds", type=float, default=rev.WINDOW_SECONDS)
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--sessions", type=int, default=1,
                    help="fresh sessions per route; the model needs "
                         f"{rev.REQUIRED_FRESH_SESSIONS} for a PASS to stand")
    ap.add_argument("--separation", type=float, default=rev.PERSISTENCE_MIN_WINDOW_SEPARATION_SECONDS)
    ap.add_argument("--attempts", type=int, default=1, help="attempt-plan entries per route")
    ap.add_argument("--profile", default="desktop_chrome")
    ap.add_argument("--out", default="reports/sustained-playback.json")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--force-sessions", action="store_true",
                    help="run every session even when the first verdict can "
                         "neither promote nor demote; use when the point is "
                         "to confirm reproducibility rather than reach a verdict")
    ap.add_argument("--resume", action="store_true",
                    help="keep measurements already in --out and skip those targets")
    ap.add_argument("--reclassify", default="",
                    help="recompute verdicts in an existing report and exit")
    args = ap.parse_args(argv)

    if args.reclassify:
        return reclassify(os.path.join(ROOT, args.reclassify))

    if not args.targets:
        ap.error("--targets is required unless --reclassify is used")
    with open(args.targets, "r", encoding="utf-8") as handle:
        targets = json.load(handle)
    if args.limit:
        targets = targets[: args.limit]

    # Resume. A 215-target run is roughly a day of wall clock, so it will be
    # interrupted; without this every interruption would restart from zero and
    # the run could never finish. Targets already present in the output report
    # keep their measurement and are not re-measured.
    already: Dict[str, Any] = {}
    if args.resume:
        try:
            with open(os.path.join(ROOT, args.out), "r", encoding="utf-8") as handle:
                prior = json.load(handle)
            for result in prior.get("results") or ():
                already[str(result.get("name"))] = result
        except (OSError, ValueError):
            already = {}
        if already:
            print(f"resuming: {len(already)} target(s) already measured", flush=True)

    from playwright.sync_api import sync_playwright

    results: List[Dict[str, Any]] = []
    started = time.time()
    out_path = os.path.join(ROOT, args.out)

    def write_report(partial: bool = False) -> None:
        """Persist after every route.

        A 17-route two-session run is roughly two hours of wall clock. Writing
        only at the end means a crash near the finish discards every completed
        measurement, so each route is flushed as soon as it is known.
        """
        payload = {
            "mode": "phase_1_sustained_playback",
            "complete": not partial,
            "base_url": args.base,
            "window_seconds": args.seconds,
            "sessions_per_route": args.sessions,
            "session_separation_seconds": args.separation,
            "browser_profile": args.profile,
            "required_fresh_sessions_for_proof": rev.REQUIRED_FRESH_SESSIONS,
            "elapsed_seconds": round(time.time() - started, 1),
            "scope_note": (
                "One browser profile only. By the model's own rule a FAIL here "
                f"is environment:{args.profile}; global scope needs the "
                f"complete declared matrix {list(rev.DECLARED_TARGET_MATRIX)}."
            ),
            "results": results,
            "proven_routes": sum(1 for r in results if r.get("proven")),
            "routes_tested": len(results),
        }
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=CHROME_ARGS)
        try:
            results.extend(already.values())
            for index, target in enumerate(targets, start=1):
                name = str(target.get("name") or f"target-{index}")
                if name in already:
                    continue
                item = {
                    "name": name,
                    "url": target.get("url"),
                    "stream_type": target.get("stream_type"),
                    "proxy_mode": target.get("proxy_mode"),
                    "header_profile": target.get("header_profile"),
                    "backups": [{"url": u} for u in (target.get("backups") or [])],
                    # A movie must not be presented as a live channel: the
                    # attempt plan, the mpegts isLive flag and the player's own
                    # contextual buttons all branch on this.
                    "_sourceKind": str(target.get("kind") or "channel"),
                    "content_kind": (
                        "movie" if str(target.get("kind") or "") == "movie"
                        else "live_tv"
                    ),
                }
                per_route: List[Dict[str, Any]] = []
                for attempt_index in range(max(1, args.attempts)):
                    for session in range(max(1, args.sessions)):
                        # The 120 s separation exists so two SUCCESSES cannot
                        # be one cached response counted twice. Re-confirming a
                        # failure needs no such gap, and paying it anyway is
                        # what made the movie sweep 235 s per title.
                        previous_passed = bool(
                            per_route and per_route[-1].get("verdict") == rev.PROVEN
                        )
                        if per_route and args.separation > 0 and previous_passed:
                            time.sleep(args.separation)
                        context = browser.new_context(
                            viewport={"width": 1366, "height": 768},
                            user_agent=(
                                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                "AppleWebKit/537.36 (KHTML, like Gecko) "
                                "Chrome/151.0.0.0 Safari/537.36"
                            ),
                        )
                        page = context.new_page()
                        page.on("pageerror", lambda e: None)
                        record: Dict[str, Any]
                        try:
                            page.goto(args.base, wait_until="commit", timeout=60000)
                            page.wait_for_function(
                                "() => typeof buildAttemptPlan === 'function'", timeout=45000
                            )
                            # A proxy_only source produces an EMPTY plan until
                            # the runtime config has supplied the play proxies,
                            # which measures nothing at all. Measured on the
                            # first run of this script: Zee Bangla is
                            # proxy-routed and returned plan_length 0.
                            try:
                                page.wait_for_function(
                                    "() => typeof playbackProxyList === 'function' "
                                    "&& playbackProxyList().length > 0",
                                    timeout=30000,
                                )
                            except Exception:
                                pass
                            # Measured: the plan is still empty for a few
                            # hundred ms after the proxy list appears, because
                            # rankHealthyProxies also reads persisted proxy
                            # health. A short settle avoids measuring nothing.
                            page.wait_for_timeout(4000)
                            print(
                                f"    -> measuring {name} for {args.seconds:.0f}s "
                                f"(attempt {attempt_index}, session {session})",
                                flush=True,
                            )
                            page.set_default_timeout(
                                int((args.seconds + 120) * 1000)
                            )
                            raw = measure_once(
                                page, item, args.seconds, attempt_index
                            )
                            metrics = {
                                "announced_render_tracks": raw.get("announced") or [],
                                "progressing_tracks": raw.get("progressed") or [],
                                "first_frame_seconds": raw.get("first_frame_seconds"),
                                "startup_seconds": raw.get("startup_seconds"),
                                "media_progress_seconds": raw.get("media_progress_seconds"),
                                "cumulative_stall_seconds": raw.get("cumulative_stall_seconds"),
                                "fatal_errors": raw.get("fatal_errors") or [],
                                "recovered_to_pass_floor": False,
                            }
                            # The route matters: a bare fetch refusal on a
                            # DIRECT route is a CORS fact, not an outage.
                            verdict, reasons = rev.classify_playback(
                                metrics,
                                delivery_path=str(raw.get("attempt_route") or ""),
                            )
                            record = {
                                "attempt_index": attempt_index,
                                "session": session,
                                "engine": raw.get("engine"),
                                "plan_length": raw.get("plan_length"),
                                "plan_routes": raw.get("plan_routes"),
                                "attempt_route": raw.get("attempt_route"),
                                "proxied": raw.get("proxied"),
                                # The window actually observed, which is
                                # shorter than the target only when the FAIL
                                # floor was already met. A PASS always runs the
                                # full window, so no reset can rely on a
                                # shortened observation.
                                "window_seconds": (
                                    raw.get("observed_window_seconds")
                                    or args.seconds
                                ),
                                "target_window_seconds": args.seconds,
                                "early_exit_reason": raw.get("early_exit_reason"),
                                "kind": "full_playback_session",
                                "playback_metrics": metrics,
                                "verdict": verdict,
                                "reasons": reasons,
                                "notes": raw.get("notes") or [],
                                "sample_count": len(raw.get("samples") or []),
                                "last_samples": (raw.get("samples") or [])[-3:],
                            }
                        except Exception as exc:  # a crash is a reported outcome
                            record = {
                                "attempt_index": attempt_index,
                                "session": session,
                                "window_seconds": args.seconds,
                                "kind": "full_playback_session",
                                "verdict": rev.UNKNOWN,
                                "reasons": [f"harness error: {str(exc)[:180]}"],
                                "playback_metrics": {},
                            }
                        finally:
                            try:
                                context.close()
                            except Exception:
                                pass
                        per_route.append(record)
                        mark = record["verdict"]
                        # A verdict that is neither PROVEN nor escalatable cannot
                        # change anything: it can never promote the item and can
                        # never hide it. Measured on the movie sweep, where every
                        # title returned advisory:vantage_blocked (403 through the
                        # proxy) or a CORS refusal on the direct route - a second
                        # session re-confirms an unusable verdict at full price.
                        decisive = (
                            mark == rev.PROVEN or rev.is_escalatable(str(mark))
                        )
                        if not decisive and session == 0 and not args.force_sessions:
                            record["sessions_skipped_reason"] = (
                                f"verdict {mark} is neither provable nor "
                                "escalatable; a second session cannot change it"
                            )
                        detail = record.get("reasons") or []
                        notes = record.get("notes") or []
                        fatal = (record.get("playback_metrics") or {}).get(
                            "fatal_errors"
                        ) or []
                        print(
                            f"[{index}/{len(targets)}] {name} a{attempt_index}"
                            f"s{session}: {mark}"
                            + (f" | {detail[0]}" if detail else "")
                            + f" | plan={record.get('plan_length')}"
                            f" route={record.get('attempt_route')}"
                            f" engine={record.get('engine')}"
                            + (f" | fatal={str(fatal[0])[:80]}" if fatal else "")
                            + (f" | note={str(notes[0])[:60]}" if notes else ""),
                            flush=True,
                        )
                        if not decisive and session == 0 and not args.force_sessions:
                            break

                passes = [r for r in per_route if r["verdict"] == rev.PROVEN]
                results.append(
                    {
                        "name": name,
                        "url_public_template": rev.redact_public_template(
                            str(target.get("url") or "")
                        ),
                        "distinct_sources": rev.distinct_sources(
                            [{"url": target.get("url")}]
                            + [{"url": u} for u in (target.get("backups") or [])]
                        ),
                        "independent_redundancy": rev.independent_redundancy(
                            [{"url": target.get("url")}]
                            + [{"url": u} for u in (target.get("backups") or [])]
                        ),
                        "browser_profile": args.profile,
                        "observations": per_route,
                        "pass_count": len(passes),
                        "proven": len(passes) >= rev.REQUIRED_FRESH_SESSIONS,
                        "verdict_scope": rev.resolve_verdict_scope(
                            rev.PLAYBACK_FAIL,
                            browser_profile=args.profile,
                            failed_profiles=[args.profile],
                        ),
                    }
                )
                write_report(partial=True)
        finally:
            browser.close()

    write_report(partial=False)
    proven = sum(1 for r in results if r.get("proven"))
    print(f"\nwrote {args.out}: {proven}/{len(results)} proven")
    return 0


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1:]))
