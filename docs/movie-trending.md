# Real International Trending (PART 06)

`scanner/movie_trending.py` → `data/movies/discovery/trending.json`

## What changed in principle

`available_link_count` is not popularity. It counts how many verified
sources were found. A 2018 film with five working servers is not more
trending than this week's international hit with one, and saying so on the
site would be a claim the data never supported.

Trending is now built the other way round:

1. **Real external signal** — TMDB `/trending/movie/week` and
   `/trending/tv/week`, fetched **separately** (a film and a series are not
   ranked against each other at the source, so they are not here either).
2. **Matched to our own playable catalogue** — an external hit with no
   stream on Click TV is not shown at all. A trending row that cannot be
   clicked is worse than a short one.
3. **External rank preserved** — our order is TMDB's order. Ranks are
   renumbered 1…n only to close the gaps left by titles we cannot play;
   nothing is ever re-sorted. `external_rank` keeps TMDB's original number.

## available_link_count — tie-breaker only, provably

`_reliability_bonus()` is capped at **0.05** while one rank step is worth at
least 1.0, so more servers can never move a title past one TMDB ranked
above it. That ceiling is the whole reason the function exists rather than
the count being added to the score. There is a test named
`test_available_link_count_can_never_outrank_a_real_trend_position`.

## Matching

Identity comes from the same `merger.movie_identity_key` the rest of the
movie system uses, asked one field at a time: `tmdb_id` → `imdb_id` →
normalised `title + year`.

- A title with **no year is never matched on title alone** — two films share
  a title far more often than a remake trends in the same week, and a wrong
  match is worse than a missing row.
- One catalogue movie can occupy only one trending slot.

Recall grows as PART 04's backfill populates `tmdb_id` across the catalogue.
Until then the matched list is legitimately short — that is honest, not
broken.

## Freshness

| Snapshot age | State | Behaviour |
|---|---|---|
| 0–72h | `fresh` | normal trending, `trend_external_fresh: true` |
| 3–7 days | `stale` | still published, `trend_external_fresh: false` |
| 7+ days | `expired` | `international_claim: false`, list cut to top 10 |

A week-old snapshot is not "what is trending internationally", so the claim
is dropped rather than stale data being dressed up as current.

## Failure behaviour

- TMDB unreachable → the **last-good snapshot is kept** in
  `state/movie-trending-cache.json` and used; its age does the talking.
- Fetch failed **and** nothing matched → the existing `trending.json` is left
  exactly as it is. An outage never empties the row.
- Fetch succeeded and nothing matched → written honestly as an empty list
  (that is a real answer about our catalogue, not an outage).
- No snapshot has ever been fetched → nothing is written at all.
- No TMDB credential → no request is made and no snapshot claimed.

## Sources deliberately NOT wired in

- **MoviesDatabase popularity.** The plan permits it as a secondary signal
  *only once its endpoint and schema are verified*. Verified live on
  2026-09-11: `GET /titles?list=most_pop_movies` answers **HTTP 200 with
  `{"entries": 0, "results": []}`** — an empty list, not a ranking. It
  therefore stays disabled. `MOVIESDATABASE_POPULARITY_VERIFIED = False` is
  the single switch to flip if that ever changes.
- **AniList** — anime only, and there is no anime catalogue here yet.
- **Click TV view counts** — none are collected yet, and inventing them to
  pad a row is exactly the fake signal this PART exists to remove. The
  schema leaves room for them (PART 22's job).

## Output shape — lightweight

```json
{
  "version": 1,
  "updated_at": "…",
  "trend_updated_at": "…",
  "trend_sources": ["tmdb"],
  "trend_external_fresh": true,
  "freshness": "fresh",
  "snapshot_age_hours": 3.2,
  "international_claim": true,
  "movies": [
    {"id": "…", "name": "…", "tmdb_id": 27205, "is_trending": true,
     "external_rank": 3, "trending_rank": 1, "trend_score": 97.5,
     "trend_sources": ["tmdb"]}
  ],
  "tv": []
}
```

Ids and ranks only — no urls, no backups, no headers, no `playback_id`
(asserted by test). The card data already exists in the category pages.

## Cost

Two requests per scan run, through PART 04's policy layer, outside the
per-movie lookup budget — the "lightweight refresh path" the plan asks for.
