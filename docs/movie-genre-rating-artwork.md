# Real Genre, Rating & Artwork (PART 05)

## Genre

`scanner/movie_genres.py` settles provider spelling once, before anything is
cached or indexed. TMDB says "Science Fiction" for a film and
"Sci-Fi & Fantasy" for a series, OMDb and Cinemeta say "Sci-Fi", TVMaze says
"Science-Fiction" — all of it lands in **Sci-Fi**.

**Frontend set — exactly eight**, per the final Category/Genre reference:

```
Action · Comedy · Horror · Romance · Thriller · Animation · Sci-Fi · Crime
```

Backend keeps every real genre it is told about (Drama, Documentary,
Adventure, …). They simply have no chip to be clicked yet — the backend is
not made lossy just because the frontend is currently selective.

- Multi-genre is native: `"genres": ["Action", "Crime", "Thriller"]` puts one
  movie in three index files.
- Provider priority is PART 03's fill-only chain: TMDB → OMDb → Cinemeta →
  MoviesDatabase → (TVMaze for series) → (AniList for anime).
- **Nothing is invented.** A title with no provider genres keeps none. OMDb's
  `"N/A"` sentinel is discarded rather than becoming a genre called "N/a" —
  that bug was caught by a test in this PART and fixed at source.

### Genre index — `data/movies/genres/*.json`

Eight small id-only files:

```json
{"genre": "Action", "updated_at": "…", "count": 42, "items": ["film-id-1", "film-id-5"]}
```

Ids, not cards: the same film is in Action and Thriller, and duplicating
full records would make the genre data several times the size of the
catalogue it describes. Order is the catalogue's own (most recently added
first), so a genre page opens on new arrivals.

**Last-good rule:** a genre whose freshly-built list is empty keeps whatever
is already on disk. An empty list is legitimate only while the backfill has
genuinely not reached that genre; far more often it means a provider outage,
and blanking a good index is exactly what the plan forbids.

## Rating

Honest label, always travelling with the value:

| Provider | `rating` | `rating_source` |
|---|---|---|
| OMDb `imdbRating` | 8.8 | `IMDb` |
| TMDB `vote_average` | 7.4 | `TMDB` |
| Cinemeta `imdbRating` | 8.8 | `IMDb` |
| MoviesDatabase `averageRating` | 8.8 | `IMDb` |
| TVMaze `rating.average` | 9.2 | `TVMaze` |
| AniList `averageScore` (÷10) | 8.8 | `AniList` |

A TMDB user score is never presented as IMDb. A provider with no rating
yields **no** `rating` field at all — never a zero, never a placeholder.
There is no rating generation anywhere in this codebase (verified by grep
and asserted by test); the frontend badge only ever renders a value the data
actually carries, and claims no source it was not given.

## Artwork

**Poster chain — untouched.** `scanner/movies.py`'s existing priority
(manual/source explicit → `state/manual-movie-posters.json` → previously
generated page → TMDB → Fanart/Cinemeta/OMDb/TVMaze/AniList supplementary)
is exactly as it was. This PART did not modify it.

**Backdrop chain** (new field, section K order):

1. Cached previous backdrop — the cache's fill-only merge means a stored
   backdrop is never replaced.
2. TMDB
3. Fanart.tv
4. Cinemeta
5. TVMaze / AniList
6. Poster-derived — **last resort only**

Each provider's artwork is held aside as a *candidate* and the winner chosen
by priority, rather than whichever provider happened to answer first.
`backdrop_source` records which one won.

The poster-derived fallback is applied **to the movie only and never
written to the cache** — caching it would make a stretched poster permanent,
because fill-only merging would then refuse the real TMDB/Fanart backdrop
when it finally arrived. `backdrop_source: "poster"` lets a consumer that
needs a true 16:9 image decline it.

**Artwork failure never removes a movie.** Every artwork path degrades to
"no backdrop"; a movie with no poster and no backdrop is still published
unchanged. Asserted by test, including the case where the lookup raises.

## Lookup budget

Raised from 40 to **150 per run** (`MOVIE_METADATA_LOOKUP_BUDGET`), measured
against the real catalogue: ~1,670 published movies, ~4 requests for a
matched title and ~6 for an unmatched one, paced at 5/s — roughly two to
three minutes of the movie scan's 2,400s budget, with the whole catalogue
enriched in under a fortnight of daily scans.

Genres, ratings and backdrops therefore appear gradually as the backfill
progresses. A genre index reading `"count": 0` today is honest, not broken.
