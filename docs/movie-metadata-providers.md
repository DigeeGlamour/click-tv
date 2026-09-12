# Multi-Provider Metadata Adapters & Fallback (PART 03)

`scanner/metadata_providers.py` — separate module from the existing
`scanner/poster_providers.py` (poster-URL-only, wired into the untouched
poster chain in `movies.py`). This module resolves the richer PART 02
schema as one normalized dict for `scanner/movie_metadata_cache.py`.

## Provider roles & fallback order (`resolve_metadata()`)

1. **TMDB** (primary) — `/search/movie` + `/movie/{id}` detail. Needs
   `TMDB_API_KEY` or `TMDB_API_TOKEN` (already a GitHub Secret, already used
   by the existing poster chain). Gives tmdb_id, imdb_id, release_date,
   genres, rating(+votes), backdrop.
2. **OMDb** (secondary) — needs `OMDB_API_KEY` (already a secret). Fills
   whatever TMDB left missing; never a Trending source.
3. **Cinemeta** — public, no key; only useful once an `imdb_id` is known
   (from TMDB/OMDb/MoviesDatabase). Never fabricates a `release_date` from
   its year-only `releaseInfo`.
4. **RapidAPI MoviesDatabase** — needs `RAPIDAPI_KEY` (+ optional
   `RAPIDAPI_MOVIES_HOST`, defaults to `moviesdatabase.p.rapidapi.com`).
   Response shape **verified live** on 2026-09-11 (title search → imdb id;
   `?info=genres` → genre names; `/ratings` → averageRating/numVotes) — see
   the module's docstring for exact quoted shapes. The endpoint
   `scanner/poster_providers.py` previously found returning HTTP 502 is a
   *different* endpoint (keyword search); title-search worked. This
   endpoint was also observed to intermittently return `{"results": null}`
   on a server-side query timeout — handled as a normal "no match" outcome,
   not an error.
5. **Fanart.tv** — needs `FANART_API_KEY` (already a secret) + a known
   `tmdb_id`. **Backdrop only** — verified it can never contribute rating,
   genre, or release_date (see `fanart_metadata`'s return shape).
6. **TVMaze** — public, no key. Only consulted when `kind="series"`.
7. **AniList** — public, no key. Only consulted when `kind="anime"`.
   Confirmed live on 2026-09-11 that the public API can simply be disabled
   ("temporarily disabled due to severe stability issues") — degrading to
   `None` is the observed common case, not a hypothetical.

## Merge rule

Fill-only: the first provider to set a field wins; a later, lower-priority
provider can only fill a field still empty. `metadata_source` records the
first (highest-priority) contributing provider; `metadata_sources` (extra,
additive field) lists every provider that contributed anything, for
debugging. Early-exit once `tmdb_id`/`imdb_id` + genres + rating +
release_date + backdrop are all present, so a strong TMDB/OMDb match does
not spend budget calling every remaining provider.

## Safety properties (tested in `tests/test_metadata_providers.py`, 27 tests)

- Missing credential → that provider returns `None`, no request is made,
  the rest of the chain still runs.
- A provider timeout/malformed response/exception → `None`, never raises;
  `resolve_metadata` wraps every call in `_safe_call`.
- No secret value is ever logged (providers are silent; no request URL is
  printed anywhere in this module).
- Bounded timeout (8s) per request, no retry loops (bounded = zero retries
  here; real retry/backoff policy is PART 04's scope).

## Wiring (`scanner/movies.py`)

`_annotate_metadata(prepared, allow_lookup=retain_recent_dropouts)` — the
exact same gate `_retain_recent_dropouts` already uses. A real provider
network call can only happen on the true scan-publish path; every test and
any ad-hoc call to `paginate_movie_list()` only ever applies the existing
cache. Verified in `tests/test_movie_metadata_wiring.py`.

## GitHub Secrets

Correction to the PART 01 audit: `scan.yml` already *referenced*
`TMDB_API_TOKEN`, `OMDB_API_KEY` and `FANART_API_KEY` in its env block, but
`gh secret list` showed only `TMDB_API_KEY` was actually configured in the
repository — the other three resolved to an empty string (harmlessly, since
every adapter already treats a missing key as "skip this provider").

As part of PART 03, the following were set directly as GitHub Actions
repository secrets (via `gh secret set`, using the token the user provided
for this purpose — values were never written into any file in this repo,
only passed as one-off shell environment variables during the `gh` calls
and live adapter smoke-tests):

- `OMDB_API_KEY`
- `FANART_API_KEY`
- `RAPIDAPI_KEY`
- `RAPIDAPI_MOVIES_HOST` (`moviesdatabase.p.rapidapi.com`)

`.github/workflows/scan.yml` was updated to pass `RAPIDAPI_KEY` and
`RAPIDAPI_MOVIES_HOST` into the scan step's env (and `RAPIDAPI_KEY` into the
log-masking step, alongside the other provider secrets) — the same pattern
already used for `OMDB_API_KEY`/`FANART_API_KEY`. `TMDB_API_TOKEN` remains
unset, which is fine: `tmdb_metadata()`/the existing poster chain both fall
back to `TMDB_API_KEY` alone.

This means OMDb and Fanart, in particular, will make their **first-ever**
real API calls in production on the next movies scan (cron `37 4 * * *`
UTC, or a manual `workflow_dispatch(mode=movies)`), each gated by the same
40-lookups-per-run budget as every other provider.
