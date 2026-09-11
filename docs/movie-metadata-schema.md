# Movie Metadata Schema & Persistent Cache (PART 02)

## Schema fields (added onto a movie record, fill-only, never overwrite)

```
tmdb_id, imdb_id, release_date, genres, rating, rating_source,
rating_votes, backdrop, metadata_source, metadata_updated_at,
metadata_confidence
```

All backward compatible — none of these existed before, and existing fields
(`id`, `year`, `first_seen_at`, `is_new`, poster chain, playback fields,
verification fields, etc.) are untouched.

## Where it lives

- Cache file: `state/movie-metadata-cache.json` — `scanner/movie_metadata_cache.py`.
- Path resolved via `scanner.paths.state_path()` (same pattern as
  `movie_recency.py`) so `CLICKTV_STATE_ROOT` test-isolation and the CI
  worktree redirect both apply — the real committed cache can never be
  corrupted by a local/CI test run.
- Atomic save (temp file + `fsync` + `os.replace`), corrupt/missing file
  loads as an empty cache — never raises, never crashes a scan.

## Identity — a deliberate 4th, separate namespace

`scanner/merger.py` already had `_movie_identity_key` (imdb_id → tmdb_id →
normalized `title:year`); PART 02 adds a public alias `movie_identity_key()`
so the metadata cache can reuse it exactly instead of reimplementing it.

This is **not** the same identity as `movie_recency.movie_key()` (which
keys on the scanner's generated `id` / a title slug). The two intentionally
stay separate:

| Ledger | Key | Question it answers |
|---|---|---|
| `state/movie-first-seen.json` | `movie.id` → `title:slug` | "Have I already published this exact card before?" |
| `state/movie-metadata-cache.json` | `imdb_id`/`tmdb_id`/`title:year` | "What real-world metadata belongs to this film?" |

Re-keying the first-seen ledger to the new identity would risk resetting
15,483 already-correct historical timestamps for zero benefit — see
`docs/movie-discovery-audit.md` §2/§7. Nothing in PART 02/03 touches
`movie_recency.py`'s identity or its `state/movie-first-seen.json` data,
only its `_write()` write-safety (see below).

## "Existing old movies must not become Just Added" — status: already true

The audit found a real, working `first_seen_at`/`is_new` system already in
production with 15,483 historical entries dating back to 2026-07-28. A movie
already recorded keeps its original stamp forever; the deployment itself is
now far past the 7-day "new" window. No destructive migration was needed or
performed — this requirement was already satisfied by the existing system.
The only change made to it is a safety hardening, not a behavior change:

- `movie_recency._write()` now writes atomically (temp file + `fsync` +
  `os.replace`) instead of a plain `open(...,"w")`, so a crash mid-write can
  no longer corrupt the 15k-entry ledger. `load()` was already safe.

## Cache API (`scanner/movie_metadata_cache.py`)

- `canonical_identity(movie) -> str`
- `load(path=None) / save(store, path=None) -> bool`
- `upsert(store, identity, fields_or_None, now=None) -> record` — fill-only
  merge; `fields=None` records a failed attempt (for the retry cooldown)
  without touching any previously cached value.
- `enrich(movies, *, lookup=None, budget=None, persist=True) -> summary` —
  applies cached fields onto every movie (fill-only); with a `lookup`
  callable supplied, resolves genuinely new/uncached movies up to `budget`
  (default 40/run, env `MOVIE_METADATA_LOOKUP_BUDGET`) fresh lookups, so a
  15k-movie cold cache backfills incrementally across many scans instead of
  blowing the scan's `time_budget_seconds.movies` (2400s) in one run. A
  movie that resolved nothing is not retried for 7 days.

## Wiring into the scanner (movies.py)

`paginate_movie_list()` now calls `_annotate_metadata(prepared, allow_lookup=...)`
right after the existing `_annotate_recency(prepared)` call — same
fail-safe-wrapper pattern already used for recency/retention, so a bug here
can never take a scan down. In this PART, `allow_lookup=False` (no provider
module exists yet), so this only ever applies existing cache entries — zero
network calls, zero behavior change to production output until PART 03
lands the adapters and flips this to the true-publish-path gate.

## Tests

`tests/test_movie_metadata_cache.py` (17 tests) covers: corrupt/missing
cache load, atomic save, identity priority + independence from
`movie_recency.movie_key`, cache-only fill with no network call, manual
field preservation (cache never overwrites), last-good preservation on
lookup failure, budget capping, and failure-retry cooldown. All existing
movie test suites (`test_movie_visibility`, `test_manual_movies`,
`test_manual_movie_integrity`, `test_manual_movie_sources`,
`test_movie_route_probe` — 62 tests) still pass unmodified.
