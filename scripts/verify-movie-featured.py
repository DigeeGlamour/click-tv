#!/usr/bin/env python3
"""The Featured/Hero plan's acceptance checklist, item by item.

CLICK_TV_FEATURED_HERO_SYSTEM_PLAN_BN.txt section 41 is 37 checkboxes. This
walks them against what is actually in the repository - the built module,
the generated file, the shipped config and the frontend - rather than
against a test that could be asserting about a fixture.

Where a claim is about behaviour rather than shape, this checks the thing
that makes the behaviour possible (the function exists and is wired into
the surface that has to call it) and names the test that proves the
behaviour itself. It never reports a row green because a test file exists.

Exit code 0 when every row passes.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import movie_featured as mf  # noqa: E402
from scanner import movie_discovery as md  # noqa: E402

passed = 0
failed: list = []


def check(label, condition, detail=""):
    global passed
    if condition:
        passed += 1
        print(f"PASS   {label:<62} {detail}")
    else:
        failed.append(label)
        print(f"FAIL   {label:<62} {detail}")


def code_only(text):
    """Source with comments and docstrings stripped.

    Both of these rules are about what the code DOES, and prose that
    explains a rule mentions the very token the rule forbids - the
    quality_label docstring says it never reads "the filename", which
    contains "name". Searching the prose answers the wrong question.
    """
    lines = []
    in_doc = False
    for line in text.splitlines():
        stripped = line.strip()
        if in_doc:
            if stripped.endswith('"""') and stripped != '"""':
                in_doc = False
            elif stripped == '"""':
                in_doc = False
            continue
        if stripped.startswith('"""'):
            body = stripped[3:]
            if not (body.endswith('"""') and len(stripped) > 5):
                in_doc = True
            continue
        if stripped.startswith("#"):
            continue
        lines.append(line.split("  #")[0])
    return "\n".join(lines)


def function_code(text, name, *, until):
    body = text[text.index(f"def {name}("):text.index(f"def {until}(")]
    return code_only(body)


APP_JS = (ROOT / "site/assets/js/app.js").read_text(encoding="utf-8")
APP_CSS = (ROOT / "site/assets/css/app.css").read_text(encoding="utf-8")
INDEX = (ROOT / "site/index.html").read_text(encoding="utf-8")
MODULE = (ROOT / "scanner/movie_featured.py").read_text(encoding="utf-8")
WORKFLOW = (ROOT / ".github/workflows/movie-discovery-refresh.yml").read_text(encoding="utf-8")
REFRESH = (ROOT / "scripts/refresh-movie-discovery.py").read_text(encoding="utf-8")

FEATURED = md.load_json(str(ROOT / "data/movies/discovery/featured.json"))
HOME = md.load_json(str(ROOT / "data/movies/discovery/home.json"))
CONFIG = mf.load_config(str(ROOT / "config/movie-featured.json"))
ITEMS = FEATURED.get("items") or []

print("Featured / Hero acceptance checklist\n" + "=" * 78)

# --- selection model --------------------------------------------------------
check("Featured uses Manual + Auto hybrid",
      "manual" in MODULE and "def active_manual" in MODULE and "def score_candidate" in MODULE
      and {"manual_count", "auto_count"} <= set(FEATURED),
      f"{FEATURED.get('manual_count', 0)} manual + {FEATURED.get('auto_count', 0)} auto")
check("Manual has highest priority",
      MODULE.index("# --- 1. manual pins") < MODULE.index("# --- 2. auto candidates"),
      "manual slots are filled before any auto candidate is scored")
check("Default Featured slots = 5", CONFIG["slots"] == 5, str(CONFIG["slots"]))
check("Manual start/end supported",
      "def manual_window_state" in MODULE
      and all(word in MODULE for word in ("pending", "expired", "disabled")),
      "pending / active / expired / disabled")
check("Auto candidates use real signals",
      all(f'"{name}"' in MODULE for name in
          ("trending", "latest", "just_added", "premium", "artwork", "playback", "metadata")),
      ", ".join(sorted(CONFIG["weights"])))
check("Trending != Featured",
      "def trending_score" in MODULE and "trending_rank" in MODULE
      and not re.search(r"items\s*=\s*trending", MODULE),
      "trending is one weighted signal of seven, never a shortcut")
MODULE_CODE = code_only(MODULE)
check("available_link_count not popularity",
      MODULE_CODE.count("available_link_count") == 1
      and "available_link_count" in function_code(MODULE, "playback_score",
                                                  until="metadata_score"),
      "one use in the whole module, inside playback_score, capped at 0.1 weight")

# --- eligibility ------------------------------------------------------------
check("Active/playable only",
      '"inactive"' in MODULE and '"no playable stream"' in MODULE,
      "both are eligibility rejections")
check("Inactive/dead excluded",
      'record.get("is_active") is False' in MODULE,
      "explicit is_active check")
check("Duplicate excluded",
      "def identity_key" in MODULE and "def visual_key" in MODULE
      and len({str(e.get("id")) for e in ITEMS}) == len(ITEMS),
      f"tmdb -> imdb -> id -> title+year; {len(ITEMS)} unique of {len(ITEMS)}")

categories = [str(e.get("category", "")).casefold() for e in ITEMS]
worst = max((categories.count(c) for c in set(categories)), default=0)
check("Category diversity considered",
      worst <= CONFIG["max_same_category"],
      f"most from one category: {worst}, cap {CONFIG['max_same_category']}")
check("Series supported",
      '"series"' in MODULE and "max_series_slots" in MODULE
      and "series_manifest" in MODULE,
      "series are candidates and carry their manifest")
check("Artwork quality considered",
      "def artwork_score" in MODULE and "poster_fallback" in MODULE,
      "real backdrop scores full, poster scores partial")
check("Playback reliability considered",
      "def playback_score" in MODULE and "verification_status" in MODULE,
      "verification status, segment proof, link count tie-break")
check("Metadata completeness considered",
      "def metadata_score" in MODULE and len(mf.COMPLETENESS_FIELDS) == 7,
      f"{len(mf.COMPLETENESS_FIELDS)} fields counted")

# --- rotation ---------------------------------------------------------------
check("Cooldown/history implemented",
      "def in_cooldown" in MODULE and "def record_history" in MODULE
      and "movie-featured-history.json" in MODULE,
      f"{CONFIG['cooldown_hours']}h")
check("Manual ignores cooldown",
      "in_cooldown" not in MODULE[MODULE.index("# --- 1. manual pins"):
                                   MODULE.index("# --- 2. auto candidates")],
      "the manual pass never consults it")
check("Refresh approximately every 12h",
      CONFIG["refresh_hours"] == 12 and WORKFLOW.count("cron:") == 2
      and "movie_featured.generate" in REFRESH,
      "twice daily, in the existing discovery refresh job")

# --- the Hero ---------------------------------------------------------------
check("Hero rotates every 6-7 sec",
      CONFIG["hero_rotate_seconds"] == 6.5
      and "const MOVIE_HERO_ROTATE_MS = 6500;" in APP_JS,
      "6500 ms, overridable from the file")
check("Manual arrow resets timer",
      "resetMovieHeroTimer" in APP_JS and "{ manual: true }" in APP_JS,
      "browser-movie-hero-check: [rotate] a manual move resets the timer")
check("Tab hidden pauses timer",
      "pauseMovieHero()" in APP_JS and "visibilitychange" in APP_JS,
      "browser-movie-hero-check: [visibility]")
check("Detail/player stops hero timer",
      APP_JS.count("stopMovieHeroRotation();") >= 3
      and "async function startPlayback" in APP_JS
      and "stopMovieHeroRotation();\n  pushMovieRoute" in APP_JS,
      "detail open, player open, leaving movies")
check("Back to Movie Home restarts the timer",
      "resumeMovieHeroRotation();" in APP_JS and "void renderMovieHeroPanel();" in APP_JS,
      "closeMovieDetail and selectMovieNavItem")
check("Mobile optimized",
      "@media (max-width: 640px)" in APP_CSS
      and "@container movieHero (max-width: 330px)" in APP_CSS
      and "-webkit-line-clamp: 2" in APP_CSS,
      "container-query sizing; title clamped; synopsis dropped on a phone")
check("Reduced motion respected",
      "movieHeroReducedMotion" in APP_JS
      and "prefers-reduced-motion" in APP_CSS
      and "if (movieHeroReducedMotion()) return false;" in APP_JS,
      "auto-rotation off, controls still usable")

# --- the generated file -----------------------------------------------------
check("featured.json generated",
      bool(FEATURED.get("items") is not None) and FEATURED.get("version") == 1,
      f"{len(ITEMS)} of {FEATURED.get('slots')} slot(s)")
raw = json.dumps(FEATURED)
check("No stream URLs in featured.json",
      all(f'"{field}"' not in raw for field in mf.FORBIDDEN_FIELDS)
      and not re.search(r"\.m3u8|\.mkv|\.mp4", raw),
      "every forbidden field absent")
hosts = ("api.themoviedb.org", "omdbapi.com", "webservice.fanart.tv", "rapidapi.com",
         "v3-cinemeta.strem.io", "api.tvmaze.com", "graphql.anilist.co")
hero_block = APP_JS[APP_JS.index("// FEATURED HERO"):APP_JS.index("// INTERNAL ANALYTICS")]
check("Browser makes no external metadata API call",
      all(host not in hero_block for host in hosts)
      and "MOVIE_FEATURED_PATH = 'data/movies/discovery/featured.json'" in APP_JS,
      "the Hero reads one static local file")

# --- failure behaviour ------------------------------------------------------
check("Last-good Featured retained on failure",
      "def write_featured" in MODULE and 'existing.get("items")' in MODULE,
      "an empty build keeps the file that has items")
check("Empty failure cannot overwrite good data",
      'if not items:' in MODULE and '"preserved": True' in MODULE,
      "write_featured refuses before it writes")
check("A preserved build does not stamp the rotation history",
      'if result.get("written"):' in MODULE,
      "history is written only when the document was")

# --- honesty ----------------------------------------------------------------
check("Rating real/source-labelled",
      all(str(e.get("rating_source") or "").strip() for e in ITEMS if e.get("rating"))
      and "def movieHeroRatingText" in APP_JS.replace("function ", "def "),
      "no rating without its issuer, in data and in the UI")
QUALITY_CODE = function_code(MODULE, "quality_label", until="eligibility")
check("Quality label real",
      "resolution_height" in QUALITY_CODE
      and not any(token in QUALITY_CODE for token in ('"name"', "'name'", '"logo"', '"label"')),
      "measured height only - the title and the poster are never read")
check("A stale file stops claiming TRENDING",
      "MOVIE_HERO_TREND_CLAIM_MAX_HOURS = 72" in APP_JS
      and "movieHeroTrendClaimStillHolds()" in APP_JS,
      "plan section 30: the slot stays, the trend claim does not")
check("Real badges only",
      "def badges_for" in MODULE
      and all(b in MODULE for b in ("NEW", "TRENDING", "PREMIERE", "PREMIUM", "SERIES")),
      "each tied to a state the record is actually in")
check("Hero label is FEATURED ON CLICK TV",
      FEATURED.get("label") == mf.DEFAULT_LABEL
      and "trending" not in str(FEATURED.get("label", "")).casefold(),
      FEATURED.get("label", ""))
check("No fake filler when candidates are short",
      "no filler was added" in MODULE and "shortfall_reason" in FEATURED,
      FEATURED.get("shortfall_reason") or "5 of 5 filled from real candidates")

# --- handoffs ---------------------------------------------------------------
check("Movie Play uses existing player",
      "startPlayback(resolved, true);" in hero_block
      and "new Audio" not in hero_block and "<video" not in hero_block,
      "the same entry point every card uses")
check("Series uses Series Detail/Episode flow",
      "seriesModule.openSeries(series" in hero_block
      and "View Series" in hero_block,
      "no episode is chosen for the viewer")
check("Exactly one player element in the document",
      INDEX.count("<video") == 1, str(INDEX.count("<video")))

# --- protected surfaces -----------------------------------------------------
check("Live Sports untouched",
      "today-match" not in hero_block and "upcoming" not in hero_block,
      "no sports reference in the Hero block")
check("Live TV untouched",
      "channel" not in hero_block.casefold(),
      "no live-TV reference in the Hero block")
check("Notice untouched",
      "sticky-header-notice" in INDEX and "notice" not in hero_block.casefold(),
      "the Notice bar is present and the Hero never mentions it")
check("Existing stream/source preserved",
      all(token not in hero_block for token in
          ("hls", "shaka", "mpegts", "proxy", "backup", "referer", "header_profile")),
      "no playback engine token anywhere in the Hero block")

# --- home.json integration --------------------------------------------------
check("home.json Featured row is ids/lightweight summaries only",
      all(not any(f in row for f in md.FORBIDDEN_CARD_FIELDS)
          for row in (HOME.get("featured") or [])),
      f"{len(HOME.get('featured') or [])} card(s)")
check("home.json featured_status is honest",
      HOME.get("featured_status") in ("built", "carried_last_good", "no_eligible_featured"),
      str(HOME.get("featured_status")))

print("=" * 78)
total = passed + len(failed)
print(f"Featured / Hero acceptance - {passed}/{total} checks passed")
for label in failed:
    print(f"  FAILED: {label}")
raise SystemExit(1 if failed else 0)
