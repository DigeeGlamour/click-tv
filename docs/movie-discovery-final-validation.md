# Movie Discovery — Core Validation Checkpoint (PART 11)

Branch: `movie-system-part01-03` · Base: `origin/main` @ `1272f27` · Date: 2026-09-11

**This is a checkpoint, not the finish line.** PART 11 validates the core
discovery system built in PARTs 01–10. PARTs 12–23 (browse/filter UI,
search, detail contract, web series, continue watching, related content,
lifecycle, match confidence, UI states, deep links, analytics, responsive
lock) are still outstanding. The Movie project is not complete until PART 23
passes.

## Verification matrix — 26/26 PASS

Each row was executed against the real repository, not asserted from the
plan. The harness drives the actual modules and reports the evidence.

| Result | Check | Evidence |
|---|---|---|
| PASS | Trending ranked by external signal, not `available_link_count` | 1-server hit scores 100.01 vs 5-server 50.05 |
| PASS | Unplayable external hits excluded | external title with no stream here → not shown |
| PASS | MoviesDatabase popularity disabled until verified | live-verified: `entries: 0` → disabled |
| PASS | Existing catalogue not bulk-marked newly added | 300 legacy films → 0 in Just Added |
| PASS | Just Added window 14 days, signal `first_seen_at` | `signal: first_seen_at` |
| PASS | Latest ordered by `release_date`, not `first_seen_at` | `['new-release', 'old-new-arrival']` |
| PASS | A bare year never becomes a release date | `normalize("2010") == ""` |
| PASS | Frontend genre set is exactly the 8 from the reference | Action, Comedy, Horror, Romance, Thriller, Animation, Sci-Fi, Crime |
| PASS | Provider spellings canonicalise to one bucket | Science Fiction / Sci-Fi / Science-Fiction → Sci-Fi |
| PASS | Provider `N/A` sentinel never becomes a genre | `canonical_genres(['N/A']) == []` |
| PASS | Rating labels honest (IMDb vs TMDB never swapped) | OMDb→IMDb, TMDB→TMDB |
| PASS | No rating synthesised when provider has none | OMDb `N/A` → no `rating` field |
| PASS | Metadata cache handles missing / corrupt / present | missing→empty, corrupt→empty, present→read back |
| PASS | A failed lookup never erases cached metadata | rating 8.0 survived a failed lookup |
| PASS | 429 bounded ladder then cooldown, **no key rotation** | 5 attempts / 4 waits / `cooling_down` |
| PASS | 401 stops immediately, no retry storm | `requests=1 retries=0`, `last_error="HTTP 401 - credential rejected"` |
| PASS | Missing OMDb key skips that provider only | no key → `None`, zero requests |
| PASS | TMDB down: fallback chain still resolves real metadata | resolved via `omdb` |
| PASS | Discovery home is lightweight | 4,682 bytes for 15 cards |
| PASS | No playback/secret field in discovery output | url/backups/headers/drm/playback_id all absent |
| PASS | Featured empty and labelled (no fake Featured) | `awaiting_featured_system` |
| PASS | Browser makes no metadata API call, holds no key | `site/` and `dist/` clean |
| PASS | All 7 existing categories intact | Dubbed, Bangla, Hindi, South Indian, English, Premium, Mix |
| PASS | Mix remains the scanner's fallback category | unknown → Mix |
| PASS | Premium and Mix stay separate | premium → Premium |
| PASS | No player / Live Sports / Live TV / Notice file touched | 31 files changed, none protected |

## Required tests (plan §PART 11)

| Required | Covered by | Status |
|---|---|---|
| Scanner normal run | `test_movie_visibility`, `test_manual_movies*` (paginate → publish path) | PASS |
| TMDB mocked down | `test_metadata_providers`, verification row 18 | PASS |
| OMDb missing key | `test_metadata_providers`, verification row 17 | PASS |
| Metadata cache present/missing/corrupt | `test_movie_metadata_cache` | PASS |
| 429 handling | `test_provider_rate_policy` (25 tests) | PASS |
| Legacy migration | `test_movie_just_added` (byte-identical ledger proof) | PASS |
| New movie discovery | `test_movie_just_added` | PASS |
| Genre mapping | `test_movie_genre_rating_artwork` (35 tests) | PASS |
| Latest ordering | `test_movie_latest` (23 tests) | PASS |
| Trending matching | `test_movie_trending` (30 tests) | PASS |
| Discovery JSON build | `test_movie_discovery_home`, `test_movie_discovery_refresh` | PASS |
| Frontend movie play smoke test | player handoff unchanged — `app.js` not touched in PARTs 01–11 | PASS (by non-modification) |
| Desktop/mobile movie page smoke test | **not applicable at PART 11** — no UI integration exists yet; it is PART 12's scope | Deferred to PART 12 |

**Full movie-scoped suite: 330 tests across 18 files, all passing.** The
repository's 4,000-test suite is deliberately not run: it rewrites the real
`state/` and `reports/` of the working tree (documented in
`tests/test_state_survives_validation.py`, reproduced and reverted during
PART 06).

## Master-plan section coverage (A–Q + C2)

| Section | Subject | Where it lives |
|---|---|---|
| A, B | Current system audit | `docs/movie-discovery-audit.md` (PART 01) |
| C | Live-site API-free architecture | verified row 22; all providers server-side |
| C2 | TMDB rate-limit / multi-key policy | `scanner/provider_health.py` (PART 04) |
| D | Provider adapters & roles | `scanner/metadata_providers.py` (PART 03) |
| E, F | Trending + TMDB-down behaviour | `scanner/movie_trending.py` (PART 06) |
| G | Just Added | `scanner/movie_discovery.py`, `movie_identity_alias.py` (PART 07) |
| H | Latest | `scanner/movie_discovery.py` (PART 08) |
| I, J, K | Genre / Rating / Artwork | `movie_genres.py`, `metadata_providers.py` (PART 05) |
| L | Proposed new files | all present — see file inventory below |
| M | Discovery home JSON | `scanner/movie_discovery.py` (PART 09) |
| N, O | Provider health + GitHub Actions | `provider_health.py`, `movie-discovery-refresh.yml` (PART 10) |
| P, Q | Implementation order + final recommendation | this document |

### File inventory (plan §L)

| Planned | Present |
|---|---|
| `state/movie-metadata-cache.json` | yes (written by scan) |
| `state/movie-first-seen.json` | yes — **pre-existing, 15,483 historical entries, untouched** |
| `state/movie-trending-cache.json` | yes |
| `state/provider-health.json` | yes |
| `state/movie-identity-aliases.json` | yes (added PART 07, not in the original plan) |
| `data/movies/discovery/home.json` | yes |
| `data/movies/discovery/trending.json` | yes |
| `data/movies/discovery/just-added.json` | yes |
| `data/movies/discovery/latest.json` | yes |
| `data/movies/genres/*.json` | yes — 8 files (plan listed 5; the Category/Genre reference's 8 is authoritative) |

## Plan vs repo — deviations found and how they were handled

1. **The plan assumed no `first_seen_at` system existed.** The repo already
   had a working one with 15,483 real historical timestamps. The plan's
   `legacy_existing = true / first_seen_at = null` migration was therefore
   **not** applied — it would have destroyed real history to solve a
   problem that no longer existed. Documented in PART 01's audit; the
   production ledger is preserved and proven untouched by test.

2. **The plan's genre list (5) vs the Category/Genre reference (8).** The
   reference file and the owner's instruction are authoritative: 8 frontend
   genres. Backend keeps every other real genre it is told about.

3. **`scan.yml` referenced secrets that were never actually set.** Only
   `TMDB_API_KEY` existed in the repository; `TMDB_API_TOKEN`,
   `OMDB_API_KEY` and `FANART_API_KEY` resolved to empty strings. Set
   during PART 03 along with the new RapidAPI pair.

4. **MoviesDatabase popularity does not verify.** `GET /titles?list=most_pop_movies`
   answers HTTP 200 with `entries: 0`. Per the plan's own condition it stays
   disabled rather than being wired to something that returns nothing.

5. **A reader/writer path mismatch in the refresh script** (PART 10) was
   caught by test and fixed — it anchored the catalogue reader to `__file__`
   while the writers use working-directory-relative paths.

## Known limitations (honest, not defects)

- **Metadata backfill has not run in production yet.** The enrichment
  budget is 150 lookups per scan against ~1,670 published movies, so
  genres, ratings, backdrops and release dates fill in over roughly a
  fortnight of daily scans. Today that means `latest.json` is legitimately
  empty with `no_exact_release_date: 1663` stating why, and the genre
  indexes read `"count": 0`. **These are not filled with fake data**, and
  the reason is visible in the output rather than hidden.
- **`trending.json` is empty until `tmdb_id` backfills**, since matching an
  external hit to our catalogue needs an id or a title+year match.
- **No Featured source exists yet** — the row is empty and labelled
  `awaiting_featured_system`. The Featured/Hero plan is separate work.
- **No UI integration yet at this checkpoint.** The movie browse/filter,
  search and detail work is PARTs 12–14.
- `movie_retention.py` writes its state non-atomically (found in PART 01's
  audit). Out of scope for PARTs 02–11; recorded for a future part.

## Protected surfaces

31 files changed across PARTs 01–11. **Zero** touch the player, Live
Sports, Today/Upcoming Match, Live TV, Notice, `site/`, `dist/`, stream
URLs or backup URLs. Verified programmatically (row 26) against the merge
base on every run.
