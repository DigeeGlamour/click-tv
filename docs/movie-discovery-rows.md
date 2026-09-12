# Discovery Rows: Just Added, Latest, Home (PARTs 07–09)

`scanner/movie_discovery.py` → `data/movies/discovery/`

| File | Signal | Cap |
|---|---|---|
| `just-added.json` | `first_seen_at`, 14-day rolling window | 40 |
| `latest.json` | `release_date`, newest first | 40 |
| `home.json` | all four rows as card summaries | 15/row |
| `trending.json` | TMDB weekly rank (PART 06) | — |

## The two rows answer different questions

```
JUST ADDED  - when did this arrive on Click TV?  -> first_seen_at
LATEST      - when was this released?            -> release_date
```

A 2010 film added today is **Just Added**, not Latest. A film released last
week that arrived here months ago is **Latest**, not Just Added. They are
built by separate functions from separate fields, and tests cover both
directions.

## PART 07 — Just Added

Purely internal: `first_seen_at` is our own observation, so this row needs
**no external API** and keeps working with every provider down.

**The existing production first-seen system is preserved and used as-is.**
Nothing resets, rebuilds, re-keys or migrates the 15,000+ historical
timestamps in `state/movie-first-seen.json`. A test places a real ledger
where the code would find it and asserts it returns byte-for-byte
identical.

### Identity reconciliation — `state/movie-identity-aliases.json`

PART 01's audit found two identities coexisting:

| Ledger | Key |
|---|---|
| `movie-first-seen.json` | `movie_recency.movie_key` — generated `id`, else title slug |
| metadata cache | `merger.movie_identity_key` — `tmdb_id` → `imdb_id` → title+year |

They disagree in one situation that matters: **a film's generated `id`
changes while the film does not** (a rename, a manual entry replaced by a
discovered one, a year finally filled in). The first-seen ledger then
correctly stamps the new key as seen today, and the film is announced as
newly added months after it actually arrived.

A **separate, additive** ledger records canonical identity → earliest
`first_seen_at` ever observed, so that case is spotted and corrected. Rules:

- The correction only ever moves a date **backwards**, to one the system
  has genuinely seen. Nothing can be made to look newer than it is.
- A title identity is trusted **only with a year** — two unrelated films
  share a title far more often than one film changes its id.
- `movie-first-seen.json` is never written to from here.
- Entries prune after 180 days, matching the first-seen ledger's own
  retention, so a film absent longer than that is genuinely new on return.

`is_new` is recomputed from the corrected date so the badge agrees with the
row.

## PART 08 — Latest

Ranked by `release_date` **alone**. Four things are explicitly forbidden
from influencing it, each with its own test: `first_seen_at`,
`available_link_count`, trend rank, and source order. Films released the
same day fall back to a neutral deterministic id tie-break.

**`normalize_release_date`** accepts a real `YYYY-MM-DD` calendar date (or
the date half of a timestamp) and nothing else:

- A bare year is **not** accepted — turning `"2010"` into `2010-01-01`
  invents a day and month the provider never gave.
- Impossible dates (`2026-02-31`) are rejected, not coerced.

**Missing-date policy: `excluded`** — stated in the output itself, with
counts for both exclusion reasons, so "why is this film not in Latest" is
answerable from the data:

```json
{"missing_date_policy": "excluded",
 "excluded": {"no_exact_release_date": 1663, "future_release": 0}}
```

Future releases are excluded (real data, but not a "latest release"); a
film released today is included.

## PART 09 — Home

One small file for the whole movie home.

**Measured on the real catalogue: `home.json` is 5.4 KB against a 3.6 MB
category catalogue — 0.2%.**

Card fields: `id, name, category, year, release_date, poster, backdrop,
rating, rating_source, genres` (+ row-specific `trending_rank`,
`first_seen_at`). `poster` comes from the catalogue's existing `logo`.
Absent values are omitted rather than serialised as null.

**No playback configuration ever reaches a discovery file** — no `url`,
`backups`, `headers`, `drm`, `playback_id`, `proxy_mode`. Asserted against
the full `FORBIDDEN_CARD_FIELDS` list in every row's tests. A click
resolves the real record from the existing category pages and hands off to
the **existing player entry point, untouched**.

### Featured — deliberately empty

The Featured/Hero system is a separate plan and is **not built yet**, so
nothing fills that row:

- not the top trending title,
- not the film with the most servers,
- not a random pick.

Each would be a fabricated editorial choice presented as a real one.
`featured_status` says which state it is in:

- `awaiting_featured_system` — empty, because no Featured source exists yet
- `carried_last_good` — a real Featured builder wrote entries and they
  survived this scan

Carried entries whose film is no longer in the catalogue are dropped: a
Featured slot pointing at something unplayable is worse than an empty shelf.

### Browser stays key-free

Everything is static JSON. Tests assert no file under `site/` or `dist/`
references `api.themoviedb.org`, `omdbapi.com`, `webservice.fanart.tv`,
`rapidapi.com`, Cinemeta, TVMaze or AniList, and that no provider secret
name appears in frontend or discovery files.

## Frontend wiring — deliberately not done here

These PARTs build the **data layer only**. `site/assets/js/app.js` is the
single file that also contains the player, Live Sports and Live TV, so
under the standing protection rules it was not touched. The existing
paginated category flow and the existing `startPlayback` handoff are
exactly as they were; `home.json` is ready for a UI change whenever that is
explicitly approved.

## Current real-data state

Genres, ratings, backdrops and release dates appear as PART 04's
150-lookups-per-run backfill progresses. Today that means `latest.json` is
legitimately empty with `no_exact_release_date: 1663` explaining why, and
the genre indexes read `"count": 0`. That is honest, not broken — and it is
visible in the output rather than hidden.
