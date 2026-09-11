# Movie Discovery System — Audit & Freeze (PART 01)

Repo: `click-tv` (branch `movie-system-part01-03`, based on `origin/main` @ `1272f27`)
Audit তারিখ: 2026-09-11
Scope: শুধু Movie system (scanner/movies.py + related modules, movie JSON, movie scan workflow, movie frontend flow, player handoff-এর entry point)। Live Sports/Live TV/Notice কোনোভাবে touch/analyze করা হয়নি এই doc-এর বাইরে ছাড়া শুধু "শেয়ার্ড ফাইল" চিহ্নিত করার জন্য।

এই ডকুমেন্ট শুধু **map/freeze** — কোনো functional change এই PART-এ করা হয়নি।

---

## 1. Scanner flow (scanner/movies.py)

Entry point: `process_movies()` (`scanner/movies.py:3397`), `scan.py`-এর `python scan.py movies` mode থেকে call হয়।

Pipeline order:
1. `working/bd-results.json` থেকে verified candidates load, `source_pipeline == "movies"` filter।
2. `_resolve_category_precedence()` — একই identity-তে category conflict হলে priority অনুযায়ী resolve (Dubbed > Bangla > Hindi > South Indian > English > Premium > Mix)।
3. `merger.merge_candidates()` — discovered candidates merge, dedup via `_movie_identity_key` (merger.py)।
4. `load_manual_movies()` — manual layers (manual/movies.txt, manual/movies.json, remote sources) merge + poster resolve + missing-year resolve + dedup।
5. Manual liveness probe (`_annotate_manual_movie_liveness`)।
6. Playback-URL dedup (`_deduplicate_movies_by_playback_url`)।
7. Player-failure filtering।
8. Manual-over-discovered merge (manual name identity সবসময় জিতে)।
9. Dedup + player-failure filtering আবার।
10. `bd_verification.strict_player_publish` gate (শুধু Bangla)।
11. Category-wise grouping।
12. Discovered cards → `_reorder_browser_sources` (browser-compat rank, backups ≤5)।
13. Manual integrity validation।
14. প্রতিটি category-এর জন্য `paginate_movie_list()` → recency annotate → sort → paginate → output।

`paginate_movie_list()`-এর ভেতরে (`scanner/movies.py:3185`):
- `_enforce_movie_runtime_direct_first` per movie।
- `_retain_recent_dropouts` → `scanner/movie_retention.retain()` (শুধু real publish path, `retain_recent_dropouts=True` হলে)।
- `_annotate_recency(prepared)` → `scanner/movie_recency.enrich()` — **এখানেই `year`, `first_seen_at`, `is_new` set হয়, sort-এর আগে**।
- `sorted(prepared, key=_movie_sort_key)` — sort key: `(-first_seen_day, unknown_year_flag, -year, manual_rank, source_tier, manual_position, status_priority, movie_id)`। **অর্থাৎ recency (first_seen_at) আজই catalogue-এর primary/leading sort key।**
- Page output: `data/movies/{slug}/page-{NNN}.json`, index: `data/movies/{slug}/index.json`।

## 2. Movie identity/dedup — ৩টা আলাদা scheme (সংরক্ষণ করতে হবে)

| Function | ব্যবহার | Priority |
|---|---|---|
| `merger._movie_identity_key` (merger.py:181) | discovered-candidate merge/dedup, category-precedence | `imdb_id` → `tmdb_id` → normalized `title:year` → raw `id` → `fallback:{source_id}:{index}` |
| `movies._movie_name_identity` (movies.py:502) | manual-over-discovered override (manual সবসময় জিতে, year mismatch হলেও) | normalized title → `imdb_id`/`tmdb_id`/`tvg_id`/`id` → fallback |
| `movie_recency.movie_key` / `movie_retention._item_key` (movie_recency.py:146) | first_seen/retention ledger key | `movie.id` (casefold) → `title:{slug}` |

**গুরুত্বপূর্ণ:** এই ৩টা scheme এখন ইচ্ছাকৃতভাবে আলাদা এবং **কোনোটাই বদলানো যাবে না**। PART 02-এর নতুন "canonical identity" (`tmdb_id → imdb_id → normalized title+year`, master plan অনুযায়ী) শুধু **নতুন metadata cache**-এর জন্য একটা চতুর্থ, সম্পূর্ণ আলাদা namespace হিসেবে যোগ হবে — প্রথম দুইটার সাথে বা first_seen ledger-এর সাথে merge/replace করা হবে না।

## 3. Movie JSON — বর্তমান fields (production sample, `data/movies/bangla/page-001.json`)

```json
{
  "id": "remote-manual-rongin-shurma-2026",
  "name": "Rongin Shurma",
  "logo": "https://image.tmdb.org/...",
  "category": "Bangla",
  "url": "...", "header_profile": "android_tv", "proxy_mode": "direct_first",
  "stream_type": "media", "requires_headers": false, "inherit_manifest_query": false,
  "verification_mode": "manual_local", "verification_status": "manual_trusted",
  "verification_badge": "Manual", "verification_note": "...", "verified": true,
  "publish_allowed": true, "skip_verification": true, "manual_source": true,
  "manual_position": 7, "manual_source_tier": 2, "source_pipeline": "movies",
  "original_source_pipeline": "remote_manual_movies", "content_kind": "movie",
  "routing_reason": "remote_manual_movie_source", "source_id": "...",
  "source_name": "...", "source_priority": 100000, "metadata_only": false,
  "available_link_count": 1, "backups": [], "year": 2026,
  "resolution": "HD 1080P", "resolution_height": 1080, "label": "HD 1080P",
  "standby": [], "manual_liveness_status": "live", "manual_liveness_http_status": 206,
  "manual_liveness_response_time_ms": 358, "segment_verified": true,
  "browser_support": "conditional", "force_proxy": false, "proxy_required": false,
  "first_seen_at": "2026-08-13T21:10:42Z", "is_new": false,
  "playback_id": "ctv_300edce7c6e6279f3d380130a4e16d8e"
}
```

এই list-এ **এখনো নেই**: `tmdb_id`/`imdb_id` (কখনো কখনো manual raw item-এ থাকলে চলে আসে, guaranteed না), `release_date`, `genres`, `rating`+`rating_source`, `backdrop`, `metadata_source`, `metadata_updated_at`, `metadata_confidence`। PART 02/03 এইগুলো **যোগ** করবে, কোনো existing field বদলাবে না।

## 4. Poster resolution chain (ইতিমধ্যে ভালো fallback pattern — PART 03-এ reuse করা হবে না, নতুন metadata adapter আলাদা মডিউলে থাকবে)

`_resolve_manual_poster()` (movies.py:1079):
1. Raw item-এ explicit poster field (logo/poster/poster_url/...).
2. `poster_lookup: False` হলে lookup skip।
3. `state/manual-movie-posters.json` cache (key: `normalized_title:year`)।
4. আগে publish হওয়া page-এর poster reuse (`_load_generated_poster_map`)।
5. TMDB search (`_tmdb_poster_lookup`, score ≥110 না হলে reject)।
6. Supplementary chain: Fanart(tmdb_id লাগে) → Cinemeta(imdb_id লাগে) → OMDb(title) → TVMaze(title) → AniList(title)।

এই poster chain **অপরিবর্তিত থাকবে** — PART 03-এর নতুন metadata adapter (genres/rating/release_date/backdrop-এর জন্য) সম্পূর্ণ আলাদা মডিউলে থাকবে, poster resolution-কে touch করবে না।

## 5. TMDB বর্তমান ব্যবহার

- Env: `TMDB_API_TOKEN` (Bearer) এবং/অথবা `TMDB_API_KEY` (query param) — দুটোর কোনোটাই না থাকলে no-op।
- Endpoints: `search/movie`, `search/multi` (poster+year resolution-এর জন্য) — `movie/{id}` detail endpoint বর্তমানে **ব্যবহার হয় না** (তাই `imdb_id`/`genres`/`vote_average` persist হয় না, শুধু poster URL বের করে ফেলে দেয়)।

## 6. Category assignment

`VALID_MOVIE_CATEGORIES = (Dubbed, Bangla, Hindi, South Indian, English, Premium, Mix)`, slug map fixed। Manual default category = Bangla; discovered default আসে `config/sources/movies.json` থেকে। অচেনা/খালি সবসময় **Mix**-এ পড়ে (Mix কখনো বাদ দেওয়া যাবে না — এটা scanner-এর fallback bucket, category/genre plan-এও এই নিয়ম নিশ্চিত করা আছে)।

## 7. first_seen_at / is_new — **ইতিমধ্যে বাস্তব সিস্টেম আছে (master-plan doc-এর ধারণার বিপরীতে)**

Master plan document (Section B) ধরে নিয়েছিল "বর্তমান movies.py-তে first_seen_at system নেই।" **এই ধারণা বর্তমান repo state-এর সাথে মেলে না** — সম্ভবত plan doc পুরনো কোনো snapshot দেখে লেখা হয়েছিল, অথবা repo ইতিমধ্যে এগিয়ে গেছে। বাস্তবতা:

- `scanner/movie_recency.py` একটা সম্পূর্ণ কার্যকরী first-seen সিস্টেম, production-এ চালু, `state/movie-first-seen.json`-এ **15,483টা** movie-র real historical timestamp আছে (তারিখ range: ২০২৬-০৭-২৮ থেকে চলমান)।
- নিয়ম ঠিক master plan-এর expectation-এর মতোই: একবার `first_seen_at` রেকর্ড হয়ে গেলে ভবিষ্যতে scan-এ movie আবার দেখা গেলেও stamp বদলায় না (`stamp_first_seen`, movie_recency.py:194-236, comment: *"a film first published in June must not read as new in August"*)।
- `is_new` badge ইতিমধ্যে প্রতিটা published movie card-এ আছে, 7-day window (`NEW_BADGE_DAYS=7`)।
- `first_seen_at` ইতিমধ্যে **primary sort key** — অর্থাৎ "recently added first" আজই default catalogue ordering।
- Store নিজে থেকেই self-bounded: 180 দিনের বেশি পুরনো (`last_seen_at`) entry `_prune()`-এ বাদ যায়।

**অতএব: "existing old movies যেন নতুন Just Added না হয়" এই ঝুঁকি বর্তমানে কার্যত শূন্য** — কারণ প্রায় সব চালু movie-র real historical `first_seen_at` ইতিমধ্যে রেকর্ড আছে (deployment-এর নিজের বয়সও এখন ৭ দিনের বেশি)। PART 02-এ কোনো destructive migration লাগবে না; শুধু verify + write-safety harden করতে হবে (নিচে দেখুন)।

**পাওয়া bug (fix করা হবে PART 02-এ, harden হিসেবে, logic বদলাবে না):** `movie_recency._write()` (line 260) এবং `movie_retention._write()` (line 70) plain `open(path,"w")` দিয়ে লেখে — atomic write না (temp-file + `os.replace` না)। মাঝপথে process kill হলে এই দুইটা state file corrupt হতে পারে। বিপরীতে `movies.py`-এর নিজের `_atomic_write_json` (poster cache-এর জন্য ব্যবহৃত) সঠিকভাবে atomic। `load()` অবশ্য দুইটাতেই already safe (corrupt/missing JSON → default `{}` ফেরত দেয়, crash করে না)।

## 8. movie_retention.py — আলাদা concern (recency না)

`state/movie-retention.json` first_seen-এর মতো দেখতে হলেও **সম্পূর্ণ আলাদা purpose**: এক scan-এ কোনো movie miss হলে ১ scan grace দেয় (`GRACE_SCANS=1`), তারপর drop করে। এটা churn/false-negative protection, "Just Added" concept না। এই ফাইলের `absent` entries কখনো time-based prune হয় না (কিছু entry-তে `misses: 8` পাওয়া গেছে) — এটা audit finding হিসেবে নোট করা হলো, এই PART-এ fix করা হচ্ছে না (out of PART 02/03 scope, retention logic বদলানো risk)।

## 9. Reusable helpers vs না

- `movies.py`-এর নিজের `_load_optional_json`/`_atomic_write_json` (movies.py:360,373) — real atomic pattern (temp file + fsync + `os.replace`), কিন্তু module-private (underscore-prefixed), অন্য মডিউল থেকে import করা হবে না।
- `scanner/persistence_store.py` — **movie metadata cache-এর জন্য reuse করা যাবে না**। এটা route-persistence (sports/live-tv route verdict) এর জন্য narrow, hard-coded allow-listed fields সহ purpose-built ledger। ভিন্ন schema, ভিন্ন pruning semantics।
- নতুন `state/movie-metadata-cache.json`-এর নিজস্ব ছোট atomic load/save থাকবে (movie_recency.py-এর pattern অনুসরণ করে, `scanner.paths.state_path()` দিয়ে path resolve — এটা জরুরি, কারণ নিচের §10 দেখুন)।

## 10. ⚠️ Test-suite state-corruption ঝুঁকি (protect করতে হবে)

`tests/test_state_survives_validation.py`-তে documented আছে: local `python -m unittest discover -s tests` চালালে `state/movie-first-seen.json`, `state/movie-retention.json` সহ আরো state file বাস্তব working tree-তে rewrite হয়ে যায়, কারণ বেশিরভাগ module `scanner.paths.state_path()` (যেটা `CLICKTV_STATE_ROOT` env override মানে) ব্যবহার করে ঠিকই, কিন্তু কিছু module `__file__`-anchored path ব্যবহার করে যেগুলো override মানে না। CI-তে এই সমস্যা `CLICKTV_STATE_ROOT`/worktree redirect + `git checkout -- state reports` দিয়ে সামলানো হয়।

**নতুন `movie-metadata-cache.json`-এর জন্য এই নিয়ম মানতে হবে:** অবশ্যই `scanner.paths.state_path("movie-metadata-cache.json")` ব্যবহার করতে হবে (movie_recency.py-এর মতো), যাতে test run real repo state নষ্ট না করে এবং CI-এর existing state-protection mechanism-এর আওতায় পড়ে।

## 11. GitHub Actions (.github/workflows/scan.yml)

- Movies cron: `"37 4 * * *"` (04:37 UTC / 10:37 Dhaka), অথবা `workflow_dispatch(mode=movies)`, অথবা catch-up mechanism।
- Movie-specific pre-step: private movie source repo checkout (`secrets.PRIVATE_MOVIE_SOURCE_TOKEN`, repo `0matbank/hopeful-research`)।
- Scan step env-এ ইতিমধ্যে ইনজেক্টেড secret: `TMDB_API_KEY`, `TMDB_API_TOKEN`, `FANART_API_KEY`, `OMDB_API_KEY`, `PRIVATE_MOVIE_SOURCE_TOKEN` (+ অ-movie secrets একই shared step-এ)।
- **RapidAPI (MoviesDatabase) secret এখনো নেই** — PART 03-এ যোগ করা হবে (`RAPIDAPI_KEY`, `RAPIDAPI_MOVIES_HOST`), শুধু GitHub Secrets-এ, কোথাও plaintext না।
- Commit step `git add data/ state/` (পুরো directory glob) — অর্থাৎ নতুন `state/movie-metadata-cache.json` automatically commit-এ ধরা পড়বে, workflow ফাইলে path পরিবর্তন লাগবে না।

## 12. Frontend movie flow (site/assets/js/app.js, ~10,486 লাইন, shared movies+live-tv+sports+player ফাইল)

- Fetch chain: `data/manifest.json` → `manifestMovieEntry(slug)` → `data/movies/<slug>/index.json` → `page-NNN.json` (`selectMovieSubcategory`, `loadNextMoviePage`, app.js:1517-1672)।
- Card render (`createMovieCard`, app.js:4226): `item.year`, `item.rating` (**বর্তমানে কোনো real data-তে populate না, dormant UI path**), `movieIsNew(item)` (`item.is_new`/`first_seen_at` থেকে), `item.logo`।
- Default sort: `movieAddedDay` (i.e. `first_seen_at`) descending, তারপর year — **"recently added first" আজই default UI ordering, আলাদা "Just Added" rail নেই**।
- একমাত্র cross-category preview: `loadMovieParentPreview()` — flat mixed sample, "Trending/Featured" concept না।
- কোনো genre tag/filter নেই আজ — শুধু language/type category chip (`MOVIE_ORDER`)।
- Header nav: `FINAL_MAIN_GROUPS` array (app.js:992) — Live Sports/Live TV/Movies/Drama/Favorites সব একই array/render function-এ, একই shared `app.js` ফাইলে।

## 13. Player handoff (touch করা হয়নি, শুধু contract note করা হলো)

- Card click → `state.currentItems`-এ আগে থেকে in-memory থাকা object খুঁজে → `startPlayback(item, true)` (app.js:4366-4388)।
- **পুরো normalized movie object reference দিয়ে pass হয়, কোনো re-fetch/আলাদা "get by id" call নেই।** `normalizeItem()` raw JSON-এর সব field spread করে (`...safeRaw`) রাখে, তাই নতুন metadata field (`genres`,`rating` ইত্যাদি) card object-এ এমনিই available হয়ে যাবে যখন frontend সেগুলো পড়া শুরু করবে (ভবিষ্যৎ PART, এই PART-এ না)।
- `playback_id` শুধু proxy URL builder-এর জন্য ব্যবহার হয় (`/hls?id=...`), কোনো metadata lookup key না।
- **কোনো movie detail page নেই আজ** — card click সরাসরি playback শুরু করে।

---

## Protected files/functions — PART 02/03-এ touch করা যাবে না (audit freeze)

- `site/assets/js/app.js` — player engine (HLS/Shaka/mpegts), `startPlayback`, `buildAttemptPlan`, proxy URL builder, সব sports/live-tv rendering, `FINAL_MAIN_GROUPS`/nav (PART 02/03-এ কোনো frontend change লাগছে না, তাই পুরো ফাইল untouched থাকবে)।
- `site/assets/js/series.js`, `site/index.html`।
- Sports/live-tv-only scanner modules: `verifier.py`, `bd_verifier.py`, `route_evidence*.py`, `route_preference.py`, `live_protection.py`, `event*.py`, `fixture*.py`, `sport_filter.py`, `schedule_resolver.py`, `source_health_settlement.py`, `team_identity.py`, `source_coverage.py`, `targeted_scan.py`, `authority_*.py`, `channel_*.py`, `streamed_provider.py`, `telegram_notify.py`, `persistence_store.py`, `drm.py`, `media_probe.py`।
- `scanner/movies.py`-এর existing functions — শুধু **additive** hook (নতুন wrapper function + ২ লাইন call) ছাড়া কিছু বদলানো হবে না। `_movie_identity_key`, `_movie_name_identity`, poster chain, category logic — অপরিবর্তিত।
- `scanner/movie_recency.py`, `scanner/movie_retention.py` — শুধু `_write()`-এর atomic-write hardening (PART 02), বাকি সব অপরিবর্তিত।
- `data/today-match.json`, `data/upcoming.json`, `config/event-fixtures.json`, `config/team-aliases.json`, Notice-related যেকোনো ফাইল।
- Existing stream/backup URL, verification behavior, manual movie source data — কিছু delete/replace হয়নি এবং হবে না।

## PART 01 Acceptance Checklist

- [x] Current scanner flow documented
- [x] Current movie JSON fields documented
- [x] Current poster/TMDB flow documented
- [x] Current GitHub secrets/env documented
- [x] Existing player entry point documented
- [x] No production behavior changed
- [x] No stream/source removed
- [x] No UI/player file changed unless only comment/doc was needed (এই PART-এ কোনো UI/player file touch হয়নি)

**PART 01 সম্পূর্ণ। এখন PART 02 শুরু হচ্ছে।**
