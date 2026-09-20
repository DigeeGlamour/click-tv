# CLICK TV — Movie/Series Master Plan v3.4 · Cumulative Implementation Report

**Source of truth:** `CLICK_TV_MOVIE_MASTER_PLAN_BN.txt` v৩.৪ (১৯ সেপ্টেম্বর ২০২৬).
On any conflict, ধারা ৪ (architecture) and ধারা ৭ (implementation order) are final;
the appendices are history only.

This is the **single** cumulative report for the whole implementation. Every
phase appends its own section here rather than creating a new file, so the
history of what was changed, why, and what was proved stays in one place.

---

## Baseline — measured before anything changed

Taken on 2026-09-20 from commit `9eda889b6d4e7eb22ba8d28e82a3dbf880f9497d`,
by reading the published catalogue on disk rather than the last scan's output.

| | Plan v3.4 (ধারা ১ / পরিশিষ্ট-ক) | Measured now | |
|---|---|---|---|
| Published movies | ১,৬৬৭ | **1,667** | ✔ |
| Playable movie links | ২,১৫৯ | **2,159** (1,667 primary + 492 backup) | ✔ |
| Series / episodes | ৭০ / ৩১৬ | **70 / 316** | ✔ |
| Total links to verify | ~২,৪৭৫ | **2,475** | ✔ |
| verified_global | ৯২৭ | **927** | ✔ |
| stale_last_good | ৩৯০ | **390** | ✔ |
| manual_trusted | ৩৫০ | **350** | ✔ |
| Poster on `srhady-live-stream.hf.space` | ১,১৯৭ (৭২%) | **1,197 (72%)** | ✔ |
| Poster on `image.tmdb.org` | ৩১৬ (১৯%) | **316 (19%)** | ✔ |
| Titles with explicit SxxExx | ৩৫০ | **348** | drifted |
| Titles with season marker only | ২১৩ | **223** | drifted |

Per-category: bangla 29 · dubbed 266 · english 71 · hindi 240 · mix 937 ·
premium 11 · south-indian 113.

The plan's measurements are real and still valid. The two title counts have
drifted by a few cards since the plan was written, so **no phase hard-codes
350/213** — every step measures.

### One fact the plan could not have known

The last movies scan (`reports/scan-summary-movies.json`, 2026-09-17) finished
with `final_publishable: 0`, `movie_output_preserved: true`, and BD verification
reporting `geo_pending: 21090`. The guard correctly kept the previous pages, so
**that run's output describes nothing that is live**. Every "what was live
before" question in this plan — INVARIANT ২ above all — is therefore answered
from `data/movies` on disk, never from a scan's own result.

---

## Requirement status legend

`IMPLEMENTED` · `ALREADY SATISFIED` · `PARTIALLY IMPLEMENTED → COMPLETED` · `BLOCKED`

---

## PHASE 0 — Current catalogue/state backup + rollback proof

**Plan reference:** ধারা ৭, ধাপ ০ — "ফেরার পথ ছাড়া কিছু শুরু নয়".

### Goal

A way back that has been *used*, not just written. Every later step (title
cleaning, series merge, backup merge, poster policy) rewrites cards, and each
one is a chance to lose a stream nobody notices for a week.

### Before state

No movie-specific backup or rollback path existed. What did exist:

* `scanner/movie_retention.py` — one scan of grace for a film that vanishes.
* `movie_failure_protection` in `config/settings.json` — keeps the previous
  pages when the incoming total drops more than 40%.
* `scripts/scan-state-snapshot.py` — reads a handful of facts for before/after
  comparison; not a restore path.
* `state/last-good/` — **channels only** (bangla, sports, indian, …). No movie
  equivalent.

None of these can answer "put the catalogue back exactly as it was".

### Root cause

The repository *is* the durable store — every scan commits `data/movies`,
`data/series` and `data/manifest.json`, and those commits are pushed to GitHub.
What was missing was not a second copy of the bytes but a **record of which
commit the live catalogue came from and a verified way to read it back**.

### Files changed

New:

* `scanner/movie_baseline.py` — the record, the drift check, the restore.
* `scripts/movie-baseline-backup.py` — CLI: write a baseline, or `--check` the
  working tree against one.
* `scripts/movie-baseline-restore.py` — CLI: verify, and with `--apply` restore.
* `tests/test_movie_baseline.py` — 22 tests, including the full round trip.
* `state/movie-baseline.json` — the recorded baseline (192 catalogue files,
  11 state files, digests + counts + stream-inventory digest).

Changed: none. Phase 0 touches no pipeline code.

### Implementation

Design decision — **the baseline records, it does not duplicate.** Copying 5.5MB
of JSON into the same repository would not add a second copy of the bytes, it
would add a second *claim* about them, and a claim that drifts is worse than no
claim. So the record holds:

* the commit, branch and dirty flag the published files came from;
* a sha256 and normalised byte length of all 192 catalogue files;
* a sha256 of the 11 movie state files (digested, never copied —
  `route-evidence-cache.json` alone is 25MB and is not a movie file);
* the card and link counts, and the verification-status breakdown;
* a digest of the full **stream inventory** — 2,475 `kind|scope|identity|url`
  lines, one per playable link on the site.

The stream inventory is in the baseline deliberately: INVARIANT ২ (ধারা ৪.০)
needs a "was reaching viewers" side to compare against, and this is it.

`restore` has two properties, in order:

1. **Nothing is written unless everything verifies.** Every file is read out of
   the recorded commit and digested first; one mismatch refuses the whole
   restore. A restore that writes as it goes and fails halfway leaves the
   catalogue in a state that was never live.
2. **git does the writing** (`git checkout <commit> -- <paths>`, batched),
   and the result is re-verified on disk afterwards.

`--copy-to` still makes a physical copy outside the repository for an operator
who is worried about the git history itself. It is not the default.

### Root cause found during implementation — CRLF

The first version of `restore` wrote blob bytes straight to disk and the
rollback proof failed. Cause, measured rather than guessed:

```
core.autocrlf = true            (global, on this Windows machine)
data/movies/bangla/page-001.json
    on disk : 76,413 bytes, 1,795 CRLF pairs
    in git  : 74,618 bytes, 0 CRLF pairs
```

Two consequences, both real bugs and not test artefacts:

* A digest taken from disk can never match the blob the restore reads back, so
  **the rollback refused itself on the platform it is most likely to be run
  from**. Fixed by digesting newline-normalised content, so a baseline written
  on Windows and checked on a Linux runner agree.
* Writing the blob's LF bytes to a working tree with `autocrlf=true` leaves the
  file permanently flagged `M` by `git status` even though `git diff` shows no
  change. Fixed by letting git's own checkout filters do the writing.

Reimplementing git's filters here would have been a second, worse copy of them.

### Tests run

`python -m unittest tests.test_movie_baseline` — **22 tests, 22 pass**
(3 skips are the real-repository class before a baseline exists; they run and
pass once the baseline is written).

Covered:

* counts — derived surfaces (`discovery/`, `genres/`) are recorded as files but
  never counted as catalogue totals; counting them is how a 1,667-film
  catalogue reports 2,179;
* every playable link inventoried, primary **and** backup;
* episodes counted separately from movies;
* recorded paths use forward slashes on every platform;
* drift naming — `changed` / `missing` / `unrecorded`, each with the file;
* state drift ignored unless asked for (state moves every scan by design);
* **the round trip**: publish → commit → baseline → damage three ways (a card
  rewritten, a page deleted, a page invented) → restore → identical content,
  and `git status --porcelain` empty;
* **the refusal path**: a tampered digest refuses *before* anything is written,
  proved by damaging a different file first and asserting it was not repaired;
* a file absent from the commit refuses;
* a baseline with no commit refuses.

### Data validation results

Against the live repository, not fixtures:

```
scripts/movie-baseline-backup.py         192 catalogue files, 11 state files
                                         1,667 movies / 2,159 links
                                         70 series / 316 episodes
                                         927 verified_global · 390 stale_last_good
                                                              · 350 manual_trusted
                                         stream inventory 2,475 lines
scripts/movie-baseline-restore.py        192/192 files read from commit 9eda889
                                         and matched their digest
scripts/movie-baseline-backup.py --check 0 differences
```

Every number matches the plan's independently measured baseline (ধারা ১,
পরিশিষ্ট-ক).

### No-Loss results

Not applicable yet — Phase 0 establishes the *anchor* the No-Loss gate compares
against. `stream_inventory` (2,475 lines) is that anchor and is now committed.

### Regression checks

Full suite: see "Regression" below. No pipeline code was changed, so no
behavioural regression surface exists in this phase; the suite is run anyway
because "it cannot have broken anything" is how things break.

### Requirement status

| Plan requirement | Status |
|---|---|
| ধাপ ০ — current catalogue backup | **IMPLEMENTED** |
| ধাপ ০ — current state backup | **IMPLEMENTED** (digest + verified restore; large non-movie state deliberately out of scope) |
| ধাপ ০ — rollback proof | **IMPLEMENTED** (round trip exercised against a real git repository, and against this repository's own 192 files) |
| §8 "Validator ব্যর্থ হলে আগের production অক্ষত" | **ALREADY SATISFIED** (`movie_failure_protection`, `movie_retention`) — recorded here so a later phase does not rebuild it |

### Known limitations

1. **State restore is opt-in and narrow.** `--include-state` restores the 11
   movie state files; it is off by default because a scan moves them
   legitimately and rolling them back undoes work that was not part of the
   revert. `route-evidence-cache.json` (25MB) and `stream-history.json` (31MB)
   are not movie files and are not covered.
2. **The restore needs the commit to still exist.** It is a repository-anchored
   rollback, which is correct while history is append-only. If history were
   ever rewritten, `--copy-to` is the escape hatch.
3. **A stray file is reported, not deleted.** `restore` puts back what was
   recorded and says the rest still differs, rather than deleting files on its
   own initiative.

### Next task

Phase 1 — **NO-LOSS COVERAGE GATE** (ধারা ৪.০): `reports/movie-source-coverage.json`
with INVARIANT ১ (ingestion accounting) and INVARIANT ২ (live preservation),
and `unexplained_live_loss = 0` as a publish BLOCK condition. Phase 1 is not
complete on compiling code — it is complete when that report proves both
invariants on real data.
