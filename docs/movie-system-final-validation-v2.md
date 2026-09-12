# Movie System — Final Validation (PART 01–23)

Branch: `movie-system-part01-03` · Base: `origin/main` @ `1272f27` · Date: 2026-09-12

This supersedes `docs/movie-discovery-final-validation.md`, which was written
at the PART 11 checkpoint and covered the data system only. Everything below
was run against the real repository — the published JSON, the real modules,
the real site files, and real browsers at real viewport sizes.

**Scope note.** This validates the MAIN WORKING PLAN, PART 01–23. It does not
validate `CLICK_TV_FEATURED_HERO_SYSTEM_PLAN_BN.txt`, which is a separate
plan with its own implementation and review still outstanding. The Featured
row is deliberately empty and labelled `awaiting_featured_system`.

---

## PART 01–23 status

| PART | Subject | Status | Where it lives |
|---|---|---|---|
| 01 | System audit & freeze | PASS | `docs/movie-discovery-audit.md` |
| 02 | Metadata cache schema | PASS | `scanner/movie_metadata_cache.py` |
| 03 | Provider adapters | PASS | `scanner/metadata_providers.py` |
| 04 | Provider health & rate policy | PASS | `scanner/provider_health.py` |
| 05 | Genres / ratings / artwork | PASS | `scanner/movie_genres.py`, `metadata_providers.py` |
| 06 | Trending (external signal) | PASS | `scanner/movie_trending.py` |
| 07 | Just Added | PASS | `scanner/movie_discovery.py`, `movie_identity_alias.py` |
| 08 | Latest | PASS | `scanner/movie_discovery.py` |
| 09 | Discovery home JSON | PASS | `scanner/movie_discovery.py` |
| 10 | Refresh workflow | PASS | `.github/workflows/movie-discovery-refresh.yml` |
| 11 | Core validation checkpoint | PASS | `scripts/verify-movie-discovery.py` — 27/27 |
| 12 | Category + genre filter state | PASS | `site/assets/js/app.js` |
| 13 | Local search | PASS | `data/movies/search-index.json` |
| 14 | Detail metadata contract | PASS | `movieDetailRows` |
| 15 | Web Series / season / episode | PASS | `scanner/series.py`, `site/assets/js/series.js` |
| 16 | Continue Watching + resume | PASS | `clicktv_continue_watching_v2` |
| 17 | Related content | PASS | `movieRelatedScore` |
| 18 | Lifecycle | PASS | `scanner/movie_retention.py` |
| 19 | Metadata confidence + manual override | PASS | `scanner/movie_metadata_confidence.py` |
| 20 | Loading / empty / error / offline | PASS | `showMovieSkeleton`, `showMovieErrorState` |
| 21 | Deep links + SEO foundation | PASS (Tier 1) | `movieRouteFromLocation`, `movieJsonLd` |
| 22 | Internal analytics + Popular on Click TV | PASS (inactive until configured) | `scanner/movie_popularity.py`, `workers/playback-telemetry` |
| 23 | Responsive lock + final regression | PASS | this document |

---

## Final feature matrix — verified against the repo

`python scripts/verify-movie-system-v2.py` → **45/45**.

| # | Feature | Evidence |
|---|---|---|
| 1 | Real Trending | external-signal file present; 0 entries until `tmdb_id` backfills |
| 2 | Real Just Added | `signal: first_seen_at`, 40 items |
| 3 | Real Latest | 0 items, `no_exact_release_date` states why |
| 4 | Real Genres | exactly the 8 reference genres published |
| 5 | Honest rating labels | `IMDb` and `TMDB` are distinct constants, never swapped |
| 6 | Poster/backdrop fallback | card and detail both fall back; no broken-image icon survives |
| 7 | Premium | preserved; never reclassified into Mix |
| 8 | Mix | preserved; moves out only on a high-confidence match |
| 9 | Category + Genre | reachable at every tested width |
| 10 | Search | 1,723-entry local index, no playback field |
| 11 | Movie Detail | real fields only, rating carries its source |
| 12 | Series/Season/Episode | 69 series, 303 episodes; every season path resolves; numbering ascending |
| 13 | Continue Watching / Resume | own store, 30s threshold, separate from watchlist |
| 14 | Related content | never reads `available_link_count` |
| 15 | Lifecycle | inactive after 3 successful missing scans; half-failed scan is not evidence |
| 16 | Confidence + manual override | conflicting year → low; series result rejected for a film; manual value wins |
| 17 | Loading/empty/error/offline | bounded retry + Retry button, skeletons, SW cache |
| 18 | Deep links / SEO metadata | `?movie=`, `?series=`; no `ratingCount` ever emitted |
| 19 | Popular on Click TV | built, labelled from data, dormant until an endpoint is configured |
| 20 | Provider health / cache resilience | backoff module present; failed lookup never erases cache |
| 21 | Browser metadata-API-free | zero provider hostnames in `site/` or `dist/` |
| 22 | Existing player smoke | one `<video>`, control bar and Notice present at all 11 viewports |

---

## Responsive matrix

`node scripts/browser-movie-responsive-check.mjs` → **218/218**, no CSS changes
required. The existing responsive system already carried the new movie
surfaces, because every one of them was added with the same patterns
(additive CSS, auto-fill grids, the existing mobile breakpoints).

| Width | Overflow | Category | Genre | Detail | Player |
|---|---|---|---|---|---|
| 360 | none | reachable | chips scroll, ≤2 rows | fits | 1 element |
| 390 | none | reachable | chips scroll | fits | 1 element |
| 412 | none | reachable | chips scroll | fits | 1 element |
| 480 | none | reachable | chips scroll | fits | 1 element |
| 768 | none | reachable | visible | fits | 1 element |
| 1024 | none | reachable | visible | fits | 1 element |
| 1366 | none | reachable | visible | fits | 1 element |
| 1440 | none | reachable | visible | fits | 1 element |
| 1920 | none | reachable | visible | fits | 1 element |
| mobile landscape (844×390) | none | reachable | chips scroll | fits | 1 element |
| tablet landscape (1180×820) | none | reachable | visible | fits | 1 element |

Touch targets measured on mobile widths: genre chips ≥28px, category buttons
≥30px, detail Play/Watchlist ≥28px. See *Known limitations* — these are
comfortably tappable but below the 44px ideal, and raising them would change
the locked desktop proportions they share.

---

## Player and protected surfaces — unchanged

`scripts/verify-movie-discovery.py` now checks the rule that actually
matters. Its PART 11 form asserted that no file under `site/` had changed at
all, which was true only before the movie UI existed; from PART 12 the movie
browse, detail, filter, series, continue, related, states, deep-link and
popular surfaces all live in `site/assets/js/app.js`. The row now reads the
diff **line by line**:

- **71 files changed, none protected** — nothing under `player`, `event`,
  `fixture`, `sport`, `channel`, `today-match`, `upcoming`, `data/playback`
  or `dist/`.
- **58 lines removed from the shared frontend files, none protected** — no
  removed line touches HLS, Shaka, MPEGTS, proxy, backup, referer/origin,
  buffer, fullscreen, volume, resolution, network mode, Notice, Sports or
  Live TV. Comment-only removals are excluded from the check and counted
  separately.

Every browser suite additionally asserts, at every viewport it runs:
exactly one `<video>` element, the player control bar present, and the
Notice bar present. Nothing in PARTs 15–23 adds a second player, and the
only playback reference anywhere in the movie code is a *call* to the
existing `startPlayback`.

CSS across PARTs 15–23 is **purely additive** — no existing rule is modified,
so no existing style can regress.

---

## Data and source integrity

| Check | Result |
|---|---|
| Historical `first_seen` ledger | **15,483 entries, intact and git-unmodified** |
| Published movie catalogue | 1,667 movies across 21 page files |
| Premium / Mix categories | both preserved |
| Manual movie poster source | preserved |
| Stream URL in discovery/genre/search indexes | **none** |
| Anything secret-shaped in generated JSON | **none** |
| Provider hostnames in `site/` or `dist/` | **none** |
| Published season paths that do not resolve | **none** |
| Episode numbering out of order | **none** |
| Duplicate external id conflicts | detection implemented (`external_id_conflicts`); reported, never merged |

---

## Untracked / required file audit

Four untracked files were found at the start of this batch and **deliberately
not committed**:

| File | Finding |
|---|---|
| `state/movie-identity-aliases.json` | local artifact; 97 identities, several of them test fixtures (`title:bangla four k:2026`) |
| `state/movie-metadata-cache.json` | local artifact holding only `metadata_last_attempt_at` stamps — committing it would install a 7-day failure cooldown on ~300 real movies |
| `state/movie-trending-cache.json` | local artifact: empty snapshot + `last_failed_fetch_at` |
| `state/provider-health.json` | local artifact: `failed: 96` from runs with no API keys |

All four are produced by runs **without** provider credentials. In production
`.github/workflows/scan.yml` does `git add data/ state/`, so the real scan
generates and commits them with real secrets. They were removed locally
rather than committed or gitignored — gitignoring would break the production
persistence the workflow depends on.

**Nothing required is missing:** `git status --porcelain --ignored data/`
reports no untracked or ignored file under `data/`, so every JSON the site
fetches is committed. `data/movies/discovery/`, `data/movies/genres/` and
`data/movies/search-index.json` were committed during this work — they had
never been committed before, which would have left a deployed site with no
search and no genre browse.

---

## Provider status

| Provider | Role | Status |
|---|---|---|
| TMDB | primary metadata | key configured; returns 401 in this environment |
| OMDb | IMDb id/rating/release | key configured; 401 here |
| Fanart.tv | artwork | key configured; 401 here |
| RapidAPI MoviesDatabase | popularity | **disabled by design** — live-verified `entries: 0` |
| Cinemeta | fallback metadata | public, no key |
| TVMaze | series only | public, no key |
| AniList | anime | public, no key |

The 401s are an environment limitation of this machine, not a code defect:
the backoff policy treats a 401 as no-retry and disables the provider for the
run, which is exactly what it is designed to do. Metadata backfill has
therefore not run, which is why genres, ratings and release dates are still
largely empty — and why `latest.json` legitimately reads 0 with
`no_exact_release_date` stating the reason rather than being filled in.

---

## Generated data

| File | Size | Count |
|---|---|---|
| `data/movies/search-index.json` | 626 KB | 1,723 (1,663 movies + 60 series) |
| `data/movies/discovery/just-added.json` | 14.7 KB | 40 |
| `data/movies/discovery/home.json` | 5.5 KB | just_added 15, featured 0 |
| `data/movies/discovery/latest.json` | 275 B | 0 (`no_exact_release_date: 1663`) |
| `data/movies/discovery/trending.json` | — | 0 until `tmdb_id` backfills |
| `data/movies/genres/*.json` | 893 B | 8 files, 0 each |
| `data/movies/discovery/popular-clicktv.json` | — | not published (no analytics endpoint configured) |
| `state/movie-analytics-aggregate.json` | — | not published (same) |

---

## SEO: what is and is not claimed

**Tier 1 — built and working now.** Stable deep links (`?movie=`,
`?series=`, `&season=&episode=`), correct `document.title`, a real
description, a canonical link pointing at the stable detail route, and
JSON-LD built only from fields the record actually carries. A plain visit
emits no structured data at all and keeps the site's own title.

**Tier 2 — not built, and deliberately not claimed.** All of the above is
written by JavaScript after load. Browsers and some crawlers execute it;
several social scrapers do not, and will keep seeing the static tags in
`index.html`. Per-title link previews would need pre-rendered detail HTML or
a Cloudflare Pages Function serving per-id meta tags. Neither is implemented,
and no part of this work should be read as solving crawler-side SEO.

---

## Analytics: architecture and current state

No backend was invented. `workers/playback-telemetry` already existed with
KV, CORS, sanitising and a `/health` endpoint the client already checked, so
it gained `/event` and `/events` beside `/report` — different key prefix,
different shape, different TTL. `/report`'s handler is untouched.

The system is **dormant**: with no `telemetry_url` in `runtime-config.json`
every client function is a no-op, no `popular-clicktv.json` is published, and
the row is absent. To enable it: configure the worker URL, then run
`scripts/aggregate-movie-analytics.py --events-url .../events --token ...`
on a schedule.

Privacy is structural rather than promised — the payload is built field by
field from a fixed list rather than by spreading an item, the worker
re-sanitises to the same list, aggregation keeps a day rather than a moment,
and no session id survives into the aggregate. The browser test intercepts
the real beacon and asserts the body carries no URL, backup, header, token or
playback id.

---

## Tests

| Suite | Result |
|---|---|
| Python movie + series suite | **525 tests, OK** |
| `scripts/verify-movie-discovery.py` (PART 11) | **27/27** |
| `scripts/verify-movie-system-v2.py` (PART 23) | **45/45** |
| browser: browse (PART 12) | 37 |
| browser: search (PART 13) | 22 |
| browser: detail (PART 14) | 50 |
| browser: series (PART 15) | 117 |
| browser: continue (PART 16) | 68 |
| browser: related (PART 17) | 90 |
| browser: states (PART 20) | 40 |
| browser: deeplink (PART 21) | 60 |
| browser: popular (PART 22) | 31 |
| browser: responsive (PART 23) | 218 |
| **Browser total** | **733 checks, 0 failures** |

The repository's full ~4,000-test suite is deliberately not run: it rewrites
the real `state/` and `reports/` of the working tree, which is documented in
`tests/test_state_survives_validation.py` and was reproduced and reverted
during PART 06.

### Failed tests

None outstanding. Failures found and fixed during this batch are listed under
*Plan vs repo findings*.

---

## Plan vs repo findings

1. **A complete Web Series system already existed** (`scanner/series.py`,
   1,034-line `series.js`, four test files). PART 15 extended it rather than
   rebuilding it, and fixed two real defects in it: episodes came out in
   source order, and cards printed `E02 · Episode 04` — a list position
   dressed as an episode number.

2. **`episode_number` is a position, not the source's episode number.** It is
   half of the stored progress identity, so it was kept and the real
   published number added alongside as `episode_start_number` /
   `episode_end_number`.

3. **Episode progress wrote to localStorage on every `timeupdate`** — roughly
   four stringified maps a second while an episode played. Throttled in
   PART 16.

4. **PART 17 was built the plan's documented alternative way.** The plan
   preferred precomputing `data/movies/related/{id}.json`; the browse index
   the movie system already fetches carries every signal the scoring needs
   and (by PART 13's contract) no playback field, so a second generated index
   would have duplicated metadata for nothing.

5. **The retention grace was 1 scan; the plan requires ~3.** Raised, with the
   published grace and the lifecycle threshold now the same number. Two
   existing tests pinned the old value and were updated, with the reason in
   the class docstring.

6. **A half-failed scan was counted as absence.** The existing guard only
   skipped an *empty* incoming list, but a run that dies part-way returns a
   short list. Fixed in PART 18.

7. **`metadata_confidence` was a provider self-label** ("tmdb" always said
   high). PART 19 computes it from the match, and TMDB/OMDb now report which
   candidate they chose so the match can actually be checked.

8. **The derived discovery data had never been committed.** A deployed site
   would have had no search index and no genre browse. Committed.

9. **The app cleared the query string during bootstrap**, so a deep link was
   gone from the address bar before the catalogue could resolve it. Found by
   the PART 21 test; the opening route is now captured first.

10. **The PART 11 protected-surface check had gone stale** — it asserted no
    `site/` file had changed, which stopped being the requirement at PART 12.
    Upgraded to check removed lines rather than file names.

11. **Headless Chromium has no H.264/MKV decoder**, so no catalogue title
    decodes in tests (verified: the player correctly exhausts proxy fallback
    and reports failure). Where a test needs real media — the PART 16
    threshold, seek and completion rules — only the *bytes* are substituted
    with a WAV the browser can decode; the item, the handoff, the events and
    the rules are all the production path. Production code was not altered to
    make a test pass.

---

## Known limitations

1. **Metadata backfill has not run in production.** Genres, ratings, release
   dates and backdrops are still largely empty, so the genre indexes read 0,
   `latest.json` is empty with its reason stated, and `trending.json` cannot
   match external hits to the catalogue yet. Nothing is filled with
   placeholder data; the emptiness is visible and explained in the output.

2. **Featured is empty** and labelled `awaiting_featured_system`. The
   Featured/Hero plan is separate work, not started.

3. **Popular on Click TV is dormant** until a telemetry endpoint is
   configured. The row is absent rather than empty.

4. **Tier 2 SEO is not implemented** — see above. JS-written metadata is not
   a guarantee for every social crawler.

5. **Touch targets are 28–34px on mobile**, comfortably tappable but below
   the 44px ideal. Raising them means changing proportions shared with the
   locked desktop design, so it was left alone deliberately.

6. **`movie_retention.py` writes its state non-atomically** (found in the
   PART 01 audit, still true). Out of scope for these PARTs; worth a future
   fix.

7. **Provider 401s in this environment** mean the enrichment path has been
   exercised against mocks and against real 401 handling, but not against a
   successful live TMDB/OMDb response.

8. **The analytics worker route is written but not deployed.** `/event` and
   `/events` exist in `workers/playback-telemetry/src/index.js` and would
   need a `wrangler deploy` plus an allowed origin before anything reaches
   them.

---

## Follow-up items

1. Run the movie scan with working provider credentials and let the metadata
   backfill populate genres, ratings, release dates and backdrops; Latest,
   Trending and the genre indexes fill themselves from there.
2. Implement and review `CLICK_TV_FEATURED_HERO_SYSTEM_PLAN_BN.txt`.
3. Decide on Tier 2 SEO (pre-rendered detail HTML or a Pages Function) if
   per-title social previews are wanted.
4. Deploy the telemetry worker's new routes and schedule
   `scripts/aggregate-movie-analytics.py` if Popular on Click TV is wanted.
5. Make `movie_retention.py`'s state write atomic.
6. Full regression and a single push, per the owner's plan.
