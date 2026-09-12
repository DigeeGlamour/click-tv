# Featured / Hero — final validation

Worked from `CLICK_TV_FEATURED_HERO_SYSTEM_PLAN_BN.txt`, all 43 sections and
the 37-item acceptance checklist in section 41.

Nothing was pushed. Branch `movie-system-part01-03`, local commits only.

---

## 1. What was here before

Nothing. There was no Featured system and no Hero, and the repository said so
rather than pretending otherwise:

| Place | State before |
|---|---|
| `scanner/movie_discovery.py` | `_carried_featured()` — a placeholder that carried forward whatever a future builder might write, and invented nothing |
| `data/movies/discovery/home.json` | `"featured": []`, `"featured_status": "awaiting_featured_system"` |
| `site/assets/js/app.js` | one comment: *"Until a real Featured source exists the featured row is empty by design"* |
| `state/`, `config/` | no manual-featured, no history, no config |

So this was built, not rewritten. The placeholder's carry-forward behaviour was
kept as the fallback path, because it is still the right answer when
`featured.json` cannot be read.

## 2. Backend

`scanner/movie_featured.py` (new, ~980 lines). Manual pins first, then auto
picks, then honestly fewer:

```
manual pins (priority desc, deterministic)
    -> eligibility: stable id, active, playable, real title, usable artwork
    -> auto pool, scored out of 100
    -> duplicate / diversity / cooldown
    -> featured.json   (never blanked by a failed build)
```

**Scoring**, all weights configurable in `config/movie-featured.json`:

| Signal | Weight | Source |
|---|---|---|
| Trending | 40 | `trending.json` rank, only while the snapshot is fresh |
| Latest | 20 | real `release_date`; a bare year scores nothing |
| Just Added | 12 | our own `first_seen_at` ledger |
| Premium / curated | 10 | the Premium category |
| Artwork | 8 | real backdrop full, poster fallback 40% |
| Playback reliability | 6 | verification status, segment proof, link-count tie-break |
| Metadata completeness | 4 | 7 fields present, proportional |

`available_link_count` appears **once** in the whole module, inside
`playback_score`, capped at a tenth of one weight. It counts verified servers,
not viewers. A test proves it cannot reorder two differently-trending titles.

## 3. Manual Featured

`state/manual-featured.json`, shipped **empty**. An example pin would be a
fabricated editorial choice sitting in production data.

Fields: `id`, `enabled`, `priority`, `start_at`, `end_at`, `note`, and the two
UI-only decorations `custom_label` and `custom_kicker`. There is deliberately
**no way to set a title, year, rating, genre or release date** — a pin chooses
*which* title is shown, never *what is said about it*. A test asserts the
schema offers no such field.

Windows: `pending` before `start_at`, `active` between, `expired` after,
`disabled` when `enabled: false`. An expired pin blocks no slot. Priority
descending, then the order written — deterministic on every machine.

A pin over a title the auto pass would have skipped for stale verification is
**honoured** (manual outranks a score) and **flagged** in the output, because
the operator should know what they pinned.

## 4. Diversity, duplicates, cooldown

**Duplicates**: `tmdb_id` → `imdb_id` → own id, plus a normalized title+year
key for the Bangla/Dubbed copy that shares no external id. The Hero drops a
*slot*; no catalogue entry is merged, renamed or deleted.

**Diversity**: max 2 per primary category, max 2 series slots. This does
**not** relax to fill slots. Relaxing it was the first thing implemented and
it produced exactly what the plan forbids — five titles from one language — so
it is a hard maximum and a short row reports why.

**Cooldown**: 48h, keyed on the same identity the history records. Manual pins
ignore it entirely. It *does* relax below the minimum of 3, because it is a
rotation preference about freshness rather than a claim about the content, and
everything it readmits already passed eligibility and was already scored. The
relaxation is recorded in the output as `cooldown_relaxed`.

`state/movie-featured-history.json` recomputes `featured_count_30d` from a kept
list of run timestamps rather than incrementing, so a rolled-back run, a
duplicate invocation or a clock jump all self-correct.

## 5. Output and integration

`data/movies/discovery/featured.json` — ids plus display metadata, and a
published `score_breakdown` per slot so "why is this in slot 2" is answerable
from the file alone. Every entry is built **field by field**, never copied from
the catalogue record; that is the only construction a stream URL cannot slip
into when somebody adds a field six months from now.

`home.json` now reads that file for its Featured row, with three honest states:
`built`, `carried_last_good`, `no_eligible_featured`.

`.github/workflows/movie-discovery-refresh.yml` — the **existing** twice-daily
job was extended rather than a third workflow added; twice daily is exactly the
~12h the plan asks for. `state/movie-featured-history.json` was added to its
commit list. `state/manual-featured.json` deliberately was **not**: it is
operator input, never a job output, and staging it would let a refresh
overwrite a pin somebody just added. The movie scan cadence in `scan.yml` is
unchanged, and no sports or live-TV path can be staged by this job.

## 6. Frontend Hero

The demo reference is a 410px full-width cinematic banner. The production movie
home is a column beside the player, so this is a compact banner for that
column — tall enough to be the top of the page, short enough that the first row
of cards is still on screen.

**No CSS rule and no HTML element was changed or removed.** The old
stylesheet is an exact prefix of the new one, byte for byte once line endings
are ignored. The diff shows 254 deleted CSS lines and that number is entirely
line endings: the PART 16–22 blocks had been appended with a shell heredoc and
carried bare LF in an otherwise CRLF file, and a Python round-trip in this run
normalised them. Verified by comparing the two blobs directly, not by reading
the diff.

`app.js` has exactly **two** deleted lines — the two view-guard conditions
replaced in section 7 below. `index.html` has none.

| Behaviour | Implementation |
|---|---|
| Auto rotation | 6500 ms, overridable by `hero_rotate_seconds`, clamped 3–20 s |
| Manual next/prev/dot | resets the timer, so the next slide is a full interval |
| Desktop hover | pauses, only where a pointer can actually hover |
| Tab hidden | pauses; visible resumes |
| Detail open | stops |
| Player open | stops (one line added to `startPlayback`, nothing else) |
| Back to Movie Home | restarts |
| Swipe | horizontal intent only, so page scrolling never changes slide |
| Reduced motion | auto-rotation off, crossfade off, every control still usable |

**Buttons.** Movie: `Play` → the existing `startPlayback()`, and `Details` →
the existing movie detail. Series: a single `View Series` → the existing series
detail → season → episode → the same player. No episode is chosen for the
viewer, and there is no second player anywhere.

**Artwork.** No title in this catalogue has a backdrop yet, so what actually
renders is the poster fallback: a blurred ambient wash behind the poster shown
sharp at its own aspect ratio, rather than a 2:3 image stretched across a
banner. `artwork_kind` in the data decides which, not a guess about the image.

## 7. What the browser found

Three things that reading the code would not have.

**The view guard was wrong.** It asked `state.view === VIEW.MOVIE`, which the
loader sets *after* the row renderers run — so the Hero never appeared on the
first visit to Movies, only the second. Popular on Click TV (PART 22) had the
identical bug, invisible today only because its data file does not exist yet.
Both now use one guard that is true synchronously, and both re-check it after
their fetch so a slow response cannot paint over Live TV.

**The carousel controls overlapped the action buttons** at four of eleven
widths. They were absolutely positioned bottom-right and the buttons grew under
them. They now share a real flex row, so neither can cover the other.

**The Hero was 297px tall at 844×390.** The viewport said "tablet"; the movie
panel was 179px wide. A viewport media query cannot see that, so the Hero sizes
itself with **container queries** against its own box — which also fixed the
360px case the viewport had got right by accident.

## 8. Staleness

Plan section 30. The row label is always `FEATURED ON CLICK TV` and never
"Trending", so the row itself is honest whatever it was built from. The
per-item `TRENDING` badge is stamped at build time against a snapshot that was
fresh *then*, so it is dropped once the file is more than 72 hours old — and
when the file carries no timestamp at all, since an unknown age is not evidence
of freshness. The slot stays: a good Featured pick does not expire, only the
stronger claim about it does.

## 9. Last-good protection

`write_featured()` refuses to replace a `featured.json` that has items with one
that has none. A build that yields nothing is either a genuinely empty
catalogue or an upstream failure, and from there those look identical; the safe
reading is the second. The rotation history is stamped **only** when the
document was actually written, because recording a run that never reached a
viewer would put real titles into cooldown for nothing.

The refresh workflow commits only on a successful run, so the same guarantee
holds at the job level as well as inside the writer.

## 10. Results

| Suite | Result |
|---|---|
| `tests/test_movie_featured.py` | **86** tests |
| Python movie + series suite | **491** tests, OK |
| `scripts/browser-movie-hero-check.mjs` | **205** checks across 11 viewports |
| All movie browser suites | **938** checks total |
| `scripts/verify-movie-featured.py` | **46/46** acceptance rows |
| `scripts/verify-movie-discovery.py` | **33/33** |
| `scripts/verify-movie-system-v2.py` | **45/45** |

Diff for this run: **19 files changed, +4,975 / −292**. Of the 292, 254 are the
CSS line endings described above and 12 are a rewritten test docstring; the
behavioural deletions are the two `app.js` guard lines.

Generated from the live catalogue: **5 of 5 slots**, 0 manual + 5 auto, drawn
from 1,346 eligible candidates of 1,736 considered. Diversity held at 2 per
category. No filler.

## 11. Protected surfaces

`scripts/verify-movie-discovery.py` proves no player, Live Sports, Live TV or
Notice **line** was removed from the shared frontend files. That row itself had
to be fixed: it counted a *moved* line as a removed one, so a change that
deleted nothing made it go red. A removal is now only counted when the
identical line is not also added, matched one-for-one — and this was verified
by genuinely deleting a protected line in a throwaway commit and confirming the
row fails.

The Hero block contains no reference to HLS, Shaka, MPEGTS, proxy, backup,
referer, header profile, Notice, Sports, Live TV or a channel. Exactly one
`<video>` element, one `#playerControls`, one Notice bar, at every viewport.

## 12. Known limitations

1. **No backdrops anywhere.** The metadata backfill has never run in
   production, so all 1,667 titles have poster-only artwork. The Hero therefore
   always renders the poster fallback. Real backdrops raise the artwork score
   and change the look; nothing was faked to simulate it.
2. **No trending signal locally.** `trending.json` does not exist — TMDB
   returns 401 in this environment — so `trending` contributed 0 to every
   score. The picks came from Just Added, Premium, artwork, playback and
   metadata, and `signals_contributing` in the output says exactly that.
3. **Latest contributed nothing.** 1,663 of 1,667 titles have no real
   `release_date`, and a bare year is deliberately not accepted as one.
4. **Metadata scores are low** (1.14 of 4) for the same reason: only `year` and
   `category` are populated across the catalogue.
5. **No series reached a slot.** The top Premium series scored ~19, above rank
   3, but the two Premium movie slots had already taken the category's cap.
   That is the diversity rule working, not an absence of series support.
6. **Container queries** need Chromium 105+ / Safari 16+. Older browsers fall
   back to the viewport rules, which is the behaviour that existed before.
7. **Manual pins are untested in production** — the file ships empty by design.
   The path is covered by 11 unit tests.
8. **Rotation history is not committed here.** It is generated by the first
   production refresh; committing a local run's history would put five real
   titles into a 48h cooldown for a run no viewer saw.

## 13. Status

The Featured/Hero plan is implemented and its acceptance checklist passes
46/46 against real code, real output and real browser behaviour.

This does **not** make the whole Click TV movie project final or push-ready.
Nothing has been pushed, and final merge, reconcile and deploy remain a
separate step.
