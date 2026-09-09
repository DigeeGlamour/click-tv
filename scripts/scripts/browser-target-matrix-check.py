#!/usr/bin/env python3
"""Real-browser check of the movie transport across the declared target matrix.

The declared target matrix is desktop Chrome, Android Chrome, iPhone Safari and
Android TV. A verdict measured on one of them says nothing about the others -
this project special-cases TV user agents in the player itself - so the layout
and the phone-size decision are checked on every profile rather than inferred
from one.

Chromium covers the Chrome profiles and Android TV; WebKit covers iPhone Safari,
which is the same engine family Safari ships.

Scope, stated honestly: this verifies player initialisation, the phone-size
decision and control-bar geometry. It does NOT perform the 120 s sustained
playback acceptance, which needs a machine able to decode video for two minutes
and is therefore out of reach in a small CI container.

Usage:
    python3 scripts/browser-target-matrix-check.py [base_url]
Exit code 0 when every profile passes.
"""
from __future__ import annotations

import json
import sys

from playwright.sync_api import sync_playwright

DEFAULT_BASE = "https://clicktv.pages.dev/"

DESKTOP_CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
)
ANDROID_CHROME_UA = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/151.0.0.0 Mobile Safari/537.36"
)
IPHONE_SAFARI_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
)
ANDROID_TV_UA = (
    "Mozilla/5.0 (Linux; Android 14; BRAVIA 4K GB ) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36 SmartTV"
)

PROFILES = [
    {
        "id": "desktop_chrome",
        "engine": "chromium",
        "viewport": {"width": 1366, "height": 768},
        "ua": DESKTOP_CHROME_UA,
        "expect_phone_sized": False,
        "is_touch": False,
    },
    {
        "id": "android_chrome",
        "engine": "chromium",
        "viewport": {"width": 412, "height": 915},
        "ua": ANDROID_CHROME_UA,
        "expect_phone_sized": True,
        "is_touch": True,
    },
    {
        "id": "iphone_safari",
        "engine": "webkit",
        "viewport": {"width": 390, "height": 844},
        "ua": IPHONE_SAFARI_UA,
        "expect_phone_sized": True,
        "is_touch": True,
    },
    {
        "id": "android_tv",
        "engine": "chromium",
        "viewport": {"width": 1920, "height": 1080},
        "ua": ANDROID_TV_UA,
        "expect_phone_sized": False,
        "is_touch": False,
    },
]

CONTROL_IDS = [
    "movieLockBtn",
    "muteBtn",
    "skipBackBtn",
    "prevChBtn",
    "playPauseBtn",
    "nextChBtn",
    "skipFwdBtn",
    "movieRotateBtn",
    "speedBtn",
    "qualityBtn",
    "networkBtn",
    "pipBtn",
    "aspectBtn",
    "fullscreenBtn",
]

# The player must never render these for a movie, on any profile.
FORBIDDEN_FOR_MOVIES = {"movieRotateBtn", "pipBtn"}

MEASURE_JS = """
([ids, fullscreen]) => {
  state.view = VIEW.MOVIE;
  state.currentItem = { _sourceKind: VIEW.MOVIE, name: 'Matrix Probe' };
  updateContextualPlayerButtons();
  const wrap = document.getElementById('videoContainer');
  if (fullscreen) {
    wrap.classList.add('clicktv-mobile-fullscreen');
    setPlayerControlVisible('movieLockBtn', true);
    setPlayerControlVisible('skipBackBtn', true);
    setPlayerControlVisible('skipFwdBtn', true);
    setPlayerControlVisible('aspectBtn', true);
  } else {
    wrap.classList.remove('clicktv-mobile-fullscreen');
    updateContextualPlayerButtons();
  }
  const boxes = {};
  for (const id of ids) {
    const el = document.getElementById(id);
    if (!el) { boxes[id] = null; continue; }
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') { boxes[id] = null; continue; }
    const r = el.getBoundingClientRect();
    boxes[id] = { x: r.x, y: r.y, w: r.width, h: r.height };
  }
  return {
    boxes,
    phoneSized: (typeof isPhoneSizedPlayer === 'function') ? isPhoneSizedPlayer() : null,
    htmlClass: document.documentElement.className,
    centrePlayDisplay: getComputedStyle(document.getElementById('centerPlayBtn')).display,
    controlsBackground: getComputedStyle(document.getElementById('playerControls')).backgroundImage,
  };
}
"""


def overlaps(a, b):
    if not a or not b:
        return False
    ax2, ay2 = a["x"] + a["w"], a["y"] + a["h"]
    bx2, by2 = b["x"] + b["w"], b["y"] + b["h"]
    ix = max(0.0, min(ax2, bx2) - max(a["x"], b["x"]))
    iy = max(0.0, min(ay2, by2) - max(a["y"], b["y"]))
    return ix > 2 and iy > 2


def find_overlaps(boxes):
    visible = [k for k, v in boxes.items() if v]
    found = []
    for i in range(len(visible)):
        for j in range(i + 1, len(visible)):
            if overlaps(boxes[visible[i]], boxes[visible[j]]):
                found.append((visible[i], visible[j]))
    return found


def run_profile(pw, profile, base_url):
    result = {"profile": profile["id"], "engine": profile["engine"], "checks": [], "ok": True}

    def check(name, passed, detail=""):
        result["checks"].append({"check": name, "pass": bool(passed), "detail": detail})
        if not passed:
            result["ok"] = False

    launcher = getattr(pw, profile["engine"])
    args = ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--mute-audio"]
    browser = launcher.launch(args=args if profile["engine"] == "chromium" else [])
    try:
        context = browser.new_context(
            viewport=profile["viewport"],
            user_agent=profile["ua"],
            has_touch=profile["is_touch"],
            is_mobile=profile["is_touch"] if profile["engine"] == "chromium" else False,
        )
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        page.goto(base_url, wait_until="commit", timeout=45000)
        page.wait_for_timeout(3500)

        loaded = page.evaluate("() => typeof updateContextualPlayerButtons === 'function'")
        check("player script loaded", loaded)
        if not loaded:
            return result

        compact = page.evaluate(MEASURE_JS, [CONTROL_IDS, False])
        check(
            "phone-size decision matches profile",
            compact["phoneSized"] == profile["expect_phone_sized"],
            f"got {compact['phoneSized']}, expected {profile['expect_phone_sized']}",
        )
        check("no JS page error on load", not errors, "; ".join(errors[:2]))

        compact_overlaps = find_overlaps(compact["boxes"])
        check("compact bar: no overlapping controls", not compact_overlaps, str(compact_overlaps))

        shown = {k for k, v in compact["boxes"].items() if v}
        leaked = shown & FORBIDDEN_FOR_MOVIES
        check("rotate/pip never shown for a movie", not leaked, str(sorted(leaked)))
        check(
            "centre tap-to-resume overlay is suppressed",
            compact["centrePlayDisplay"] == "none",
            compact["centrePlayDisplay"],
        )

        if profile["expect_phone_sized"]:
            check(
                "compact bar has no opaque backdrop",
                compact["controlsBackground"] in ("none", ""),
                compact["controlsBackground"][:60],
            )
            fs = page.evaluate(MEASURE_JS, [CONTROL_IDS, True])
            fs_overlaps = find_overlaps(fs["boxes"])
            check("fullscreen bar: no overlapping controls", not fs_overlaps, str(fs_overlaps))
            fs_shown = {k for k, v in fs["boxes"].items() if v}
            check(
                "fullscreen bar keeps an exit control",
                "fullscreenBtn" in fs_shown,
                str(sorted(fs_shown)),
            )
            check(
                "fullscreen bar exposes volume and resolution",
                {"muteBtn", "qualityBtn"} <= fs_shown,
                str(sorted(fs_shown)),
            )
        context.close()
    finally:
        browser.close()
    return result


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_BASE
    results = []
    with sync_playwright() as pw:
        for profile in PROFILES:
            try:
                results.append(run_profile(pw, profile, base))
            except Exception as exc:  # a crashed profile is a reported failure
                results.append(
                    {
                        "profile": profile["id"],
                        "engine": profile["engine"],
                        "ok": False,
                        "checks": [{"check": "profile ran", "pass": False, "detail": str(exc)[:200]}],
                    }
                )

    total = sum(len(r["checks"]) for r in results)
    passed = sum(1 for r in results for c in r["checks"] if c["pass"])
    print(json.dumps({"base_url": base, "results": results,
                      "checks_total": total, "checks_passed": passed,
                      "profiles_ok": sum(1 for r in results if r["ok"]),
                      "profiles_total": len(results)}, indent=2))
    print()
    for r in results:
        mark = "PASS" if r["ok"] else "FAIL"
        print(f"[{mark}] {r['profile']:<16} ({r['engine']})")
        for c in r["checks"]:
            print(f"    {'ok ' if c['pass'] else 'FAIL'} {c['check']}"
                  + (f"  -> {c['detail']}" if not c["pass"] and c["detail"] else ""))
    print(f"\nchecks: {passed}/{total} passed | profiles: "
          f"{sum(1 for r in results if r['ok'])}/{len(results)} passed")
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
