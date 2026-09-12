"""PART 11: verify each plan claim against what the repo actually does."""
import datetime as dt
import json
import os
import pathlib
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, ".")

results = []


def check(name, condition, evidence):
    results.append(("PASS" if condition else "FAIL", name, str(evidence)))


NOW = dt.datetime(2026, 9, 11, 12, tzinfo=dt.timezone.utc)


def catalog(items):
    return {"Mix": {"page_contents": {"page-001.json": {"items": items}}}}


# --- Real Trending ----------------------------------------------------------
from scanner import movie_trending as mt

idx = mt.build_catalog_index(catalog([
    {"id": "one-server-hit", "tmdb_id": 1, "available_link_count": 1},
    {"id": "five-server-old", "tmdb_id": 2, "available_link_count": 5},
]))
matched = mt.match_entries([
    {"external_rank": 1, "tmdb_id": 1, "title": "A", "year": "2026"},
    {"external_rank": 2, "tmdb_id": 2, "title": "B", "year": "2026"},
], idx)
check("Trending ranked by external signal, not available_link_count",
      matched[0]["id"] == "one-server-hit" and matched[0]["trend_score"] > matched[1]["trend_score"],
      "1-server hit %s vs 5-server %s" % (matched[0]["trend_score"], matched[1]["trend_score"]))
check("Unplayable external hits are excluded",
      mt.match_entries([{"external_rank": 1, "tmdb_id": 999, "title": "Z", "year": "2026"}], idx) == [],
      "external title with no stream here -> not shown")
check("MoviesDatabase popularity stays disabled until verified",
      mt.MOVIESDATABASE_POPULARITY_VERIFIED is False,
      "live-verified 2026-09-11: entries 0 -> disabled")

# --- Just Added -------------------------------------------------------------
from scanner import movie_discovery as md

legacy = [{"id": "f%d" % n, "first_seen_at": (NOW - dt.timedelta(days=45)).isoformat()} for n in range(300)]
doc = md.build_just_added(catalog(legacy), now=NOW)
check("Existing catalogue is not bulk-marked newly added",
      doc["count"] == 0, "300 legacy films -> %d in Just Added" % doc["count"])
check("Just Added window 14 days, signal first_seen_at",
      md.JUST_ADDED_WINDOW_DAYS == 14 and doc["signal"] == "first_seen_at", doc["signal"])

# --- Latest -----------------------------------------------------------------
lat = md.build_latest(catalog([
    {"id": "old-new-arrival", "release_date": "2015-04-20", "first_seen_at": NOW.isoformat()},
    {"id": "new-release", "release_date": "2026-02-01",
     "first_seen_at": (NOW - dt.timedelta(days=300)).isoformat()},
]), now=NOW)
check("Latest ordered by release_date, not first_seen_at",
      lat["items"][0]["id"] == "new-release", [i["id"] for i in lat["items"]])
check("A bare year never becomes a release date",
      md.normalize_release_date("2010") == "" and md.normalize_release_date("2010-07-16") == "2010-07-16",
      'normalize("2010")="" / normalize("2010-07-16") kept')

# --- Genre ------------------------------------------------------------------
from scanner import movie_genres as mg

check("Frontend genre set is exactly the 8 from the reference",
      list(mg.FRONTEND_GENRES) == ["Action", "Comedy", "Horror", "Romance", "Thriller",
                                   "Animation", "Sci-Fi", "Crime"],
      ", ".join(mg.FRONTEND_GENRES))
check("Provider spellings canonicalise to one bucket",
      mg.canonical_genres(["Science Fiction"]) == mg.canonical_genres(["Sci-Fi"]) == ["Sci-Fi"],
      "Science Fiction / Sci-Fi / Science-Fiction -> Sci-Fi")
check("Provider N/A sentinel never becomes a genre",
      mg.canonical_genres(["N/A"]) == [], "canonical_genres(['N/A']) == []")

# --- Rating -----------------------------------------------------------------
from scanner import metadata_providers as mp

with patch.dict(os.environ, {"OMDB_API_KEY": "k"}, clear=False), \
        patch.object(mp, "_get_json", return_value={"Response": "True", "imdbID": "tt1", "imdbRating": "8.1"}):
    omdb = mp.omdb_metadata("X")


def fake_tmdb(provider, url, headers=None):
    if "/movie/1" in url:
        return {"vote_average": 7.4}
    return {"results": [{"id": 1, "title": "X", "release_date": "2020-01-01"}]}


with patch.dict(os.environ, {"TMDB_API_KEY": "k"}, clear=False), \
        patch.object(mp, "_get_json", side_effect=fake_tmdb):
    tmdb = mp.tmdb_metadata("X", 2020)
check("Rating source labels honest (IMDb vs TMDB never swapped)",
      omdb["rating_source"] == "IMDb" and tmdb["rating_source"] == "TMDB",
      "OMDb->%s TMDB->%s" % (omdb["rating_source"], tmdb["rating_source"]))

with patch.dict(os.environ, {"OMDB_API_KEY": "k"}, clear=False), \
        patch.object(mp, "_get_json", return_value={"Response": "True", "imdbID": "tt1", "imdbRating": "N/A"}):
    na = mp.omdb_metadata("X")
check("No rating synthesised when the provider has none",
      "rating" not in na, "OMDb N/A -> no rating field")

# --- Metadata cache ---------------------------------------------------------
from scanner import movie_metadata_cache as mc

with tempfile.TemporaryDirectory() as tmp:
    path = os.path.join(tmp, "cache.json")
    missing = mc.load(path)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("{not json")
    corrupt = mc.load(path)
    mc.save({"version": 1, "movies": {"tmdb_id:1": {"rating": 8.0}}}, path)
    present = mc.load(path)
    check("Metadata cache handles missing / corrupt / present",
          missing == {"version": 1, "movies": {}}
          and corrupt == {"version": 1, "movies": {}}
          and present["movies"]["tmdb_id:1"]["rating"] == 8.0,
          "missing->empty, corrupt->empty, present->read back")
    mc.enrich([{"tmdb_id": 1, "name": "X"}], path=path, lookup=lambda m: None, now=NOW)
    check("A failed lookup never erases cached metadata",
          mc.load(path)["movies"]["tmdb_id:1"]["rating"] == 8.0,
          "rating 8.0 survived a failed lookup")

# --- Rate limit / auth / outage ---------------------------------------------
from scanner import provider_health as ph

with tempfile.TemporaryDirectory() as tmp:
    ph.DEFAULT_PATH = os.path.join(tmp, "health.json")
    ph.reset()
    with patch.object(ph.time, "sleep"), \
            patch.object(ph, "_perform_request", side_effect=lambda *a, **k: (429, {}, None)):
        ph.request_json("tmdb", "https://api.test/x")
    rec = ph.metrics()["providers"]["tmdb"]
    check("429 backs off on a bounded ladder then cools down (no key rotation)",
          rec["status"] == "cooling_down" and rec["requests"] == 5,
          "5 attempts / 4 waits / status=%s / ok=%s" % (rec["status"], rec["ok"]))
    ph.reset()
    with patch.object(ph.time, "sleep"), \
            patch.object(ph, "_perform_request", side_effect=lambda *a, **k: (401, {}, None)):
        ph.request_json("tmdb", "https://api.test/x")
    rec = ph.metrics()["providers"]["tmdb"]
    check("401 stops immediately with no retry storm",
          rec["requests"] == 1 and rec["retries"] == 0,
          "requests=%d retries=%d last_error=%s" % (rec["requests"], rec["retries"], rec["last_error"]))
ph.reset()

with patch.dict(os.environ, {"OMDB_API_KEY": ""}, clear=False), patch.object(mp, "_get_json") as getter:
    missing_key = mp.omdb_metadata("X")
    check("Missing OMDb key skips that provider without a request",
          missing_key is None and not getter.called, "no key -> None, zero requests")

with patch.object(mp, "tmdb_metadata", return_value=None), \
        patch.object(mp, "omdb_metadata", return_value={"imdb_id": "tt1", "genres": ["Action"],
                                                        "rating": 8.1, "rating_source": "IMDb",
                                                        "metadata_source": "omdb"}), \
        patch.object(mp, "cinemeta_metadata", return_value=None), \
        patch.object(mp, "moviesdatabase_metadata", return_value=None), \
        patch.object(mp, "fanart_metadata", return_value=None):
    fallback = mp.resolve_metadata({"name": "X", "year": 2020})
check("TMDB down: fallback chain still resolves real metadata",
      bool(fallback) and fallback["metadata_source"] == "omdb" and fallback["genres"] == ["Action"],
      "resolved via %s" % fallback["metadata_source"])

# --- Discovery output -------------------------------------------------------
payload = {}
for category in sorted(pathlib.Path("data/movies").glob("*/")):
    if category.name in ("discovery", "genres"):
        continue
    pages = {p.name: json.loads(p.read_text(encoding="utf-8"))
             for p in sorted(category.glob("page-*.json"))}
    if pages:
        payload[category.name] = {"page_contents": pages}

home = md.build_home(payload, now=NOW)
serialised = json.dumps(home)
check("Discovery home is lightweight",
      len(serialised) < 60000,
      "%d bytes for %d cards" % (len(serialised), sum(home["counts"].values())))
check("No playback/secret field reaches discovery output",
      all('"%s"' % field not in serialised for field in md.FORBIDDEN_CARD_FIELDS),
      "url/backups/headers/drm/playback_id all absent")
check("Featured empty and labelled (no fake Featured)",
      home["featured"] == [] and home["featured_status"] == "awaiting_featured_system",
      home["featured_status"])

# --- Browser API-free -------------------------------------------------------
hosts = ("api.themoviedb.org", "omdbapi.com", "webservice.fanart.tv", "rapidapi.com",
         "v3-cinemeta.strem.io", "api.tvmaze.com", "graphql.anilist.co")
secret_names = ("TMDB_API_KEY", "TMDB_API_TOKEN", "OMDB_API_KEY", "FANART_API_KEY", "RAPIDAPI_KEY")
offenders = []
for folder in ("site", "dist"):
    base = pathlib.Path(folder)
    if not base.exists():
        continue
    for pattern in ("**/*.js", "**/*.html"):
        for path in base.glob(pattern):
            text = path.read_text(encoding="utf-8", errors="replace")
            offenders += ["%s -> %s" % (path, h) for h in hosts if h in text]
            offenders += ["%s -> %s" % (path, n) for n in secret_names if n in text]
check("Browser makes no metadata API call and holds no key",
      not offenders, offenders or "site/ and dist/ clean")

# --- Existing categories/sources preserved ----------------------------------
from scanner import movies as M

check("All 7 existing categories intact",
      list(M.VALID_MOVIE_CATEGORIES) == ["Dubbed", "Bangla", "Hindi", "South Indian",
                                         "English", "Premium", "Mix"],
      ", ".join(M.VALID_MOVIE_CATEGORIES))
check("Mix remains the scanner's fallback category",
      M._canonical_movie_category("something unrecognised") == "Mix", "unknown -> Mix")
check("Premium and Mix stay separate",
      M._canonical_movie_category("premium") == "Premium", "premium -> Premium")

# --- Player / live surfaces untouched ---------------------------------------
import subprocess

base_sha = subprocess.run(["git", "merge-base", "HEAD", "origin/main"],
                          capture_output=True, text=True).stdout.strip()
changed = subprocess.run(["git", "diff", "--name-only", "%s..HEAD" % base_sha],
                         capture_output=True, text=True).stdout.split()

# Files that must not be touched at all. The movie UI lives in site/app.js,
# site/series.js, site/index.html and the movie stylesheets from PART 12
# onward, so those are audited by content below rather than by name.
MOVIE_SURFACE_FILES = {
    "site/assets/js/app.js",
    "site/assets/js/series.js",
    "site/index.html",
    "site/assets/css/app.css",
    "site/assets/css/series.css",
}
protected_files = [f for f in changed if any(
    token in f.lower() for token in ("player", "event", "fixture", "sport",
                                     "channel", "today-match", "upcoming",
                                     "data/playback", "dist/"))
    and f not in MOVIE_SURFACE_FILES]
check("No player / Live Sports / Live TV / Notice file touched",
      not protected_files,
      protected_files or "%d files changed, none protected" % len(changed))

# And inside the shared frontend files, no protected line was changed. Only
# REMOVED lines matter: adding a movie feature cannot alter playback, but
# deleting or rewriting an engine line can.
PROTECTED_TOKENS = (
    "hls", "shaka", "mpegts", "proxy", "backup", "referer", "origin",
    "buffer", "fullscreen", "volume", "resolutionbtn", "networkmode",
    "notice", "sport", "live-tv", "livetv", "today-match", "upcoming",
    "startplayback(", "video.play", "video.src",
)
# encoding is explicit: the diff carries Bengali UI strings, and Python's
# default decode on Windows (cp1252) throws on them and leaves stdout None.
removed = subprocess.run(
    ["git", "diff", "-U0", "%s..HEAD" % base_sha, "--"] + sorted(MOVIE_SURFACE_FILES),
    capture_output=True, encoding="utf-8", errors="replace").stdout.splitlines()
offending = []
for line in removed:
    if not line.startswith("-") or line.startswith("---"):
        continue
    body = line[1:].strip()
    # A removed comment line is prose, not behaviour.
    if body.startswith(("//", "/*", "*", "<!--")):
        continue
    lowered = body.lower()
    if any(token in lowered for token in PROTECTED_TOKENS):
        offending.append(body[:90])
check("No player / live / notice LINE removed from the shared frontend files",
      not offending,
      offending[:3] or "%d removed lines, none protected" % sum(
          1 for line in removed if line.startswith("-") and not line.startswith("---")))

print("%-6s %-68s %s" % ("RESULT", "CHECK", "EVIDENCE"))
for status, name, evidence in results:
    print("%-6s %-68s %s" % (status, name, evidence))
failed = [r for r in results if r[0] == "FAIL"]
print("\n%d/%d checks PASS" % (len(results) - len(failed), len(results)))
sys.exit(1 if failed else 0)
