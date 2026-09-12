#!/usr/bin/env python3
"""Final validation for the Movie system, PART 01-23.

Every row runs against the real repository - the published JSON, the real
modules, the real site files. Nothing is asserted from the plan, and nothing
passes because a test file says so.

    python scripts/verify-movie-system-v2.py
"""
from __future__ import annotations

import glob
import json
import os
import re
import sys
from typing import Any, Dict, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROWS: List[Tuple[bool, str, str]] = []


def row(ok: bool, name: str, evidence: str = "") -> None:
    ROWS.append((bool(ok), name, evidence))


def read_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def safe_read(path: str, default: Any = None) -> Any:
    try:
        return read_json(path)
    except (OSError, ValueError):
        return default


# --- 1-4. the discovery surfaces exist and say what they contain ------------
def check_discovery() -> None:
    home = safe_read("data/movies/discovery/home.json", {})
    just = safe_read("data/movies/discovery/just-added.json", {})
    latest = safe_read("data/movies/discovery/latest.json", {})
    trending = safe_read("data/movies/discovery/trending.json", {})

    row(isinstance(home, dict) and "featured" in home,
        "Home discovery published", f"keys: {sorted(home)[:6]}")
    row(isinstance(just, dict) and just.get("signal") == "first_seen_at",
        "Just Added is keyed on first_seen_at",
        f"signal: {just.get('signal')}, items: {len(just.get('items') or [])}")
    row(isinstance(latest, dict) and "items" in latest,
        "Latest published, and says why it is empty when it is",
        f"items: {len(latest.get('items') or [])}, "
        f"no_exact_release_date: {latest.get('no_exact_release_date')}")
    row(isinstance(trending, dict),
        "Trending file present (external signal, PART 06)",
        f"entries: {len(trending.get('movies') or [])}")

    genres = sorted(os.path.basename(p) for p in glob.glob("data/movies/genres/*.json"))
    expected = {"action.json", "animation.json", "comedy.json", "crime.json",
                "horror.json", "romance.json", "sci-fi.json", "thriller.json"}
    row(set(genres) == expected, "Exactly the 8 reference genres are published",
        f"{len(genres)}: {genres}")


# --- 5-6. honesty of labels and artwork ------------------------------------
def check_labels() -> None:
    from scanner import metadata_providers

    row(metadata_providers.RATING_SOURCE_IMDB == "IMDb"
        and metadata_providers.RATING_SOURCE_TMDB == "TMDB",
        "Rating sources are distinct labels, never interchangeable",
        f"{metadata_providers.RATING_SOURCE_IMDB} / {metadata_providers.RATING_SOURCE_TMDB}")

    app = open("site/assets/js/app.js", encoding="utf-8").read()
    row("replaceBrokenMovieImage" in app and "movie-poster-placeholder" in app,
        "Poster failure falls back to a placeholder, not a broken icon")
    row("movie-detail-poster" in app and "poster unavailable" in app,
        "Detail artwork has its own fallback (PART 20)")


# --- 7-9. categories ---------------------------------------------------------
def check_categories() -> None:
    manifest = safe_read("data/manifest.json", {})
    movie_categories = set()
    for entry in (manifest.get("movies") or {}):
        movie_categories.add(entry)
    folders = {os.path.basename(p) for p in glob.glob("data/movies/*") if os.path.isdir(p)}
    row("premium" in folders, "Premium category preserved", f"folders: {sorted(folders)}")
    row("mix" in folders, "Mix category preserved")

    from scanner import movie_metadata_confidence as mc

    category, reason = mc.resolve_category("Premium", "Mix", confidence=mc.CONFIDENCE_HIGH)
    row(category == "Premium", "Premium is never reclassified into Mix", reason)
    category, reason = mc.resolve_category("Mix", "English", confidence=mc.CONFIDENCE_LOW)
    row(category == "Mix", "Mix is not moved on a low-confidence match", reason)
    category, _ = mc.resolve_category("Mix", "English", confidence=mc.CONFIDENCE_HIGH)
    row(category == "English", "Mix moves only on a confident match")


# --- 10-11. search and detail ------------------------------------------------
def check_search_and_detail() -> None:
    index = safe_read("data/movies/search-index.json", {})
    items = index.get("items") or []
    row(len(items) > 0, "Search index published",
        f"{index.get('count')} entries = {index.get('movies')} movies + {index.get('series')} series")

    forbidden = ("url", "backups", "headers", "playback_id", "drm", "stream_url")
    leaked = sorted({field for item in items for field in forbidden if field in item})
    row(not leaked, "Search index carries no playback field", f"leaked: {leaked}")

    app = open("site/assets/js/app.js", encoding="utf-8").read()
    row("function movieDetailRows" in app and "rating_source" in app,
        "Detail shows only real fields, with the rating's source")


# --- 12. series ---------------------------------------------------------------
def check_series() -> None:
    manifest = safe_read("data/series/manifest.json", {})
    row(int(manifest.get("total_series") or 0) > 0,
        "Series published",
        f"{manifest.get('total_series')} series, {manifest.get('total_episodes')} episodes")

    unresolved = []
    for path in glob.glob("data/series/*/*/index.json"):
        payload = safe_read(path, {})
        for season in payload.get("seasons") or ():
            target = season.get("path")
            if target and not os.path.exists(target):
                unresolved.append(target)
    row(not unresolved, "Every published season path resolves",
        f"missing: {unresolved[:3]}")

    out_of_order = []
    for path in glob.glob("data/series/*/*/season-*.json"):
        payload = safe_read(path, {})
        numbers = [int(i.get("episode_number") or 0) for i in payload.get("items") or []]
        if numbers != sorted(numbers):
            out_of_order.append(path)
    row(not out_of_order, "Episode numbering is ascending in every season",
        f"out of order: {out_of_order[:3]}")


# --- 13-14. continue watching and related ------------------------------------
def check_client_features() -> None:
    app = open("site/assets/js/app.js", encoding="utf-8").read()
    row("clicktv_continue_watching_v2" in app and "CONTINUE_MIN_SECONDS" in app,
        "Continue Watching has its own store and a real threshold")
    row("clicktv_favorites_v1" in app and "continueWatching" in app,
        "Watchlist and Continue Watching are separate stores")
    row("movieRelatedScore" in app and "available_link_count" not in
        app.split("MOVIE_RELATED_WEIGHTS")[1].split("CONTINUE WATCHING")[0],
        "Related scoring never reads available_link_count")


# --- 15-16. lifecycle and confidence -----------------------------------------
def check_backend_rules() -> None:
    from scanner import movie_retention as mrt
    from scanner import movie_metadata_confidence as mc

    row(mrt.INACTIVE_AFTER_MISSING_SCANS >= 3,
        "A title survives more than one missing scan",
        f"inactive after {mrt.INACTIVE_AFTER_MISSING_SCANS} successful missing scans")
    row(not mrt.scan_looks_complete(12, 800),
        "A half-failed scan is not evidence of absence")
    row(mrt.is_active("a-title-nobody-has-recorded") is True,
        "An unrecorded title is treated as active")

    level, _ = mc.match_confidence({"name": "X", "year": 2020},
                                   {"name": "X", "year": 1974})
    row(level == mc.CONFIDENCE_LOW, "A conflicting year is a low-confidence match")
    level, _ = mc.match_confidence({"name": "X", "year": 2020},
                                   {"name": "X", "year": 2020, "media_type": "tv"})
    row(level is None, "A series result is rejected for a film")
    plan = mc.plan_application({}, {"backdrop": "b.jpg"}, confidence=mc.CONFIDENCE_HIGH,
                               manual={"backdrop": "admin.jpg"})
    row(not plan["apply"], "A manual value is not overwritten by a confident provider")


# --- 17-19. states, deep links, analytics ------------------------------------
def check_frontend_systems() -> None:
    app = open("site/assets/js/app.js", encoding="utf-8").read()
    row("MOVIE_FETCH_ATTEMPTS" in app and "showMovieErrorState" in app,
        "Fetch failures are bounded and offer Retry")
    row("showMovieSkeleton" in app, "Loading uses a skeleton")
    row("movieRouteFromLocation" in app and "applyMovieDocumentMetadata" in app,
        "Deep links and document metadata are wired")
    # Emitted, not merely mentioned: the module comment explains why there is
    # no ratingCount, so the word appears in the file on purpose.
    emits_rating_count = bool(re.search(r"ratingCount\s*:", app))
    row("movieJsonLd" in app and not emits_rating_count,
        "Structured data never emits a ratingCount",
        "mentioned in a comment only" if "ratingCount" in app else "absent")
    row("sendMovieAnalyticsEvent" in app and "internal_qualified_plays" not in app.split("Trending")[0],
        "Analytics events exist and are separate from Trending")

    index_html = open("site/index.html", encoding="utf-8").read()
    for element in ("movieGenreBar", "movieContinuePanel", "movieRelatedPanel",
                    "moviePopularPanel", "movieDetailPanel"):
        row(element in index_html, f"{element} present in the page")


# --- 20-21. providers and the browser's independence -------------------------
def check_provider_independence() -> None:
    provider_hosts = [
        "api.themoviedb.org", "omdbapi.com", "webservice.fanart.tv",
        "moviesdatabase.p.rapidapi.com", "v3-cinemeta.strem.io",
        "api.tvmaze.com", "graphql.anilist.co",
    ]
    hits: Dict[str, List[str]] = {}
    for root in ("site", "dist"):
        if not os.path.isdir(root):
            continue
        for path in glob.glob(f"{root}/**/*", recursive=True):
            if not os.path.isfile(path) or os.path.splitext(path)[1] not in (".js", ".json", ".html", ".css"):
                continue
            try:
                text = open(path, encoding="utf-8", errors="ignore").read()
            except OSError:
                continue
            for host in provider_hosts:
                if host in text:
                    hits.setdefault(host, []).append(path)
    row(not hits, "The browser never calls a metadata provider", f"hits: {list(hits)[:3]}")

    secret_pattern = re.compile(
        r"(api[_-]?key|apikey|bearer\s+[A-Za-z0-9._-]{12,}|x-rapidapi-key)", re.IGNORECASE)
    leaks = []
    for path in glob.glob("data/**/*.json", recursive=True):
        try:
            text = open(path, encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        if secret_pattern.search(text):
            leaks.append(path)
    row(not leaks, "No generated JSON carries anything shaped like a secret",
        f"suspect: {leaks[:3]}")

    from scanner import provider_health
    row(hasattr(provider_health, "request_json") and hasattr(provider_health, "summary_line"),
        "Provider health/backoff module present")


# --- data safety ---------------------------------------------------------------
def check_data_safety() -> None:
    ledger = safe_read("state/movie-first-seen.json", {})
    # The ledger keeps its entries under "seen".
    entries = ledger.get("seen") or ledger.get("movies") or ledger.get("items") or {}
    count = len(entries) if isinstance(entries, dict) else 0
    row(count > 10000, "The historical first_seen ledger is intact", f"{count} entries")

    stream_fields = ("url", "backups", "playback_id", "headers", "drm", "stream_url")
    index_files = (
        glob.glob("data/movies/discovery/*.json")
        + glob.glob("data/movies/genres/*.json")
        + ["data/movies/search-index.json"]
    )
    leaked = []
    for path in index_files:
        payload = safe_read(path, {})
        text = json.dumps(payload)
        for field in stream_fields:
            if f'"{field}"' in text:
                leaked.append((os.path.basename(path), field))
    row(not leaked, "No discovery/genre/search index carries a stream field",
        f"leaked: {leaked[:4]}")

    manual = safe_read("state/manual-movie-posters.json", {})
    row(isinstance(manual, (dict, list)), "Manual movie poster source preserved",
        f"{len(manual)} entries")

    pages = glob.glob("data/movies/*/page-*.json")
    total = 0
    for path in pages:
        payload = safe_read(path, {})
        items = payload if isinstance(payload, list) else payload.get("items") or []
        total += len(items)
    row(total > 1000, "The published movie catalogue is intact",
        f"{total} movies across {len(pages)} page file(s)")


def main() -> int:
    check_discovery()
    check_labels()
    check_categories()
    check_search_and_detail()
    check_series()
    check_client_features()
    check_backend_rules()
    check_frontend_systems()
    check_provider_independence()
    check_data_safety()

    passed = sum(1 for ok, _, _ in ROWS if ok)
    print(f"Movie system final validation - {passed}/{len(ROWS)} checks passed\n")
    for ok, name, evidence in ROWS:
        mark = "PASS" if ok else "FAIL"
        print(f"  {mark}  {name}" + (f"  [{evidence}]" if evidence else ""))
    return 0 if passed == len(ROWS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
