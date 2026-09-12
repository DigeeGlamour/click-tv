# TMDB / Provider Request Policy (PART 04)

`scanner/provider_health.py` owns every outbound metadata request. The
adapters in `scanner/metadata_providers.py` no longer talk to `urllib`
directly — they name the provider a request belongs to and the policy layer
does the rest.

## Pacing

| Setting | Default | Env |
|---|---|---|
| Max requests/second per provider | `5` (low end of the plan's 5–10 target) | `MOVIE_METADATA_MAX_RPS` |
| Per-request timeout | 8s | — |
| Max lookups per scan run | 40 | `MOVIE_METADATA_LOOKUP_BUDGET` |

Pacing is enforced as a minimum interval between two requests to the *same*
provider, so a 429 is avoided rather than merely handled.

## Retry rules

| Response | Behaviour |
|---|---|
| 2xx | success; provider marked healthy |
| **429** | honour `Retry-After` if sent, else the bounded ladder **2s → 5s → 15s → 30s**; after the 4th wait the provider cools down for 30 min |
| 500 / 502 / 503 / 504 | same bounded ladder |
| transport error (DNS/timeout/unparseable) | same bounded ladder |
| **401 / 403** | **never retried.** Provider marked `unhealthy_auth` for 6h, admin warning printed, chain continues without it |
| 404 / other 4xx | a real answer ("not found"), never retried, not counted as ill health |

Two caps keep a bad provider day inside the scan's `time_budget_seconds.movies`:

- `MAX_SLEEP_PER_PROVIDER_SECONDS = 90` — total sleep this process will
  spend on one provider.
- **Cooling-down short-circuit** — once a provider exhausts its ladder,
  every later call to it returns instantly without a request. One bad day
  costs one ladder, not one ladder per movie.

## No key rotation — by design

There is no multi-key pool, no round-robin, no "try the other account"
path in this codebase, and `tests/test_provider_rate_policy.py` asserts
that no such function exists. Rotating credentials to get around a rate
limit is not what the limit is for, and TMDB's own guidance is to respect
the 429. Redundancy here means the other six providers plus the persistent
last-good cache — not more TMDB keys.

A 401/403 is likewise never worked around by swapping credentials: it is
surfaced as an administrator warning to replace the secret.

## Cache-first + refresh TTL

`movie_metadata_cache.enrich()` now runs in two passes:

1. Apply everything the cache already knows (fill-only). A movie with
   usable metadata costs **zero** requests.
2. Spend the run's budget — **never-resolved movies first**, then
   refresh-eligible ones with whatever is left.

Refresh TTL: `REFRESH_TTL_DAYS_STABLE = 90` for settled titles,
`REFRESH_TTL_DAYS_RECENT = 14` for anything released in the last year
(ratings and vote counts still move). A movie that matched nothing stays on
the existing 7-day `RETRY_AFTER_FAILURE_DAYS` cooldown.

**Outage guard:** if every provider is cooling down or key-broken, the
remaining lookups are abandoned for the run (`skipped_unavailable`) rather
than recorded as failures — otherwise a TMDB outage would leave a 7-day
cooldown on 40 movies that were never actually tried. Cached data is left
exactly as it was; a failed lookup never erases a previously-resolved field.

## Metrics — `state/provider-health.json`

Per provider: `status`, `requests`, `successes`, `retries`, `rate_limited`,
`auth_failures`, `server_errors`, `transport_errors`, `not_found`,
`skipped_unavailable`, `sleep_seconds`, `last_status`, `last_success`,
`last_429`, `last_auth_failure`, `next_retry_at`.

Plus a `cache` block: `cached_hits`, `fetched`, `refreshed`, `failed`,
`skipped_budget`, `skipped_cooldown`, `skipped_unavailable`.

Written atomically, corrupt-safe on load, and a one-line summary is printed
per scan (`movie metadata: cache hits 912, lookups 40, tmdb 61req/1x429`).
No URL, header or key value is ever written to this file or logged — there
is a test asserting exactly that.

`next_retry_at` is persisted, so a 429 late in one run is still respected at
the start of the next.

## Trending

`/trending/movie/week` and `/trending/tv/week` are a separate lightweight
path (PART 06) — two requests per refresh, outside the per-movie lookup
budget, but through this same policy layer.
