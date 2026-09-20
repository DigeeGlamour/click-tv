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

### Commit / push

`phase-0: record the movie catalogue baseline and prove the rollback`
→ `be5a7af58da3da2ad24339c2ba03199e781e33ed`, pushed to `origin/main` and
verified by `git ls-remote` (remote SHA == local HEAD). The rollback was
re-verified after the rebase: 192/192 files still read back from commit
`9eda889` and matched.

### Next task

Phase 1 — **NO-LOSS COVERAGE GATE** (ধারা ৪.০).

---

## PHASE 1 — No-Loss Coverage Gate

**Plan reference:** ধারা ৪.০ (architecture, authoritative) and ধারা ৭ ধাপ ১.

### Goal

Two machine-checkable invariants, and a publish that stops when either fails:

* **INVARIANT ১ — ingestion accounting.** Every candidate an enabled Movie
  source produced reaches exactly one of five destinations.
* **INVARIANT ২ — live preservation.** `unexplained_live_loss = 0`, or the
  publish is BLOCKed.

### Before state

Nothing existed. `grep` for `unexplained_live_loss`, `movie-source-coverage`,
`in_scope_entries` and `no_loss` across the repository returned no hits.

What did exist and is reused rather than rebuilt:

* `scanner/source_coverage.py` — the same idea, fully built, **for
  `today_match`**: per-source rows, a drop-reason taxonomy, `check_invariants`,
  and wiring into `scan-summary.json → coverage_invariant_failures`. Phase 1 is
  that proven shape applied to movies, not a new concept.
* `movie_failure_protection` (40% drop guard) and `movie_retention` (one scan
  of grace). A-04 said the threshold was *replaced* by this arithmetic — the
  plan replaced it as the primary mechanism, it did not ask for it to be
  deleted, so both now run and a test holds that.
* `scanner/merger.movie_identity_key` — whose own docstring says it is "the
  canonical identity other movie subsystems should key on too, so it lives here
  once rather than being reimplemented". The gate keys on it.
* `scanner/content_router.classify_candidate` — the only thing asked whether an
  entry is a movie at all.

### Files changed

New: `scanner/movie_coverage.py`, `scripts/movie-coverage-audit.py`,
`tests/test_movie_coverage.py` (42 tests),
`tests/test_movie_no_loss_gate.py` (12 tests),
`reports/movie-source-coverage.json`.

Changed: `scan.py` (capture raw entries, build the gate, pass it to publish),
`scanner/output.py` (accept the gate, block on it, surface its failures),
`scanner/movie_baseline.py` (inventory format shared by both sides),
`.github/workflows/scan.yml` (the two gate modules added to `REQUIRED_FILES`,
so a run that starts without the gate says which file is missing instead of
publishing ungated — the same reason every other path on that list is there).

No workflow change was needed to *run* the new tests: `scan.yml` already runs
the full suite for every mode except `upcoming-targeted`, in a throwaway
worktree. That worktree exists because the suite writes into the real `state/`
and `reports/` — the same behaviour observed locally during this work, where a
suite run left 22 files modified. It is why nothing produced by a test run was
staged in either of these commits.

### Implementation

**Scope (ধারা ৪.০).** `out_of_scope` is exactly three things: a disabled source
or entry, content the router says is not a movie/series, and an empty or
invalid record. **Category is never one of them** — the v৩.৪ correction, and the
one with a dedicated regression test, because v৩.৩ said the opposite and a real
playing film whose only fault is a wrong feed tag would have vanished from the
accounting. `movies._canonical_movie_category` already funnels unknown
categories to `Mix`, so this writes down existing behaviour rather than changing
it.

**Five destinations, never six.** `confirmed_unavailable` is stream health, a
separate axis, and a test asserts `len(DISPOSITIONS) == 5`.

**Hard rejection is four reasons.** 403, 451, geo-blocks, timeouts, 5xx, expired
tokens and repeated failure from our own vantage all route to quarantine, with
this codebase's own evidence behind the rule (1,312 posters answer 403 from
Bangladesh and 200 from a GitHub runner).

**Where the gate runs.** Between `process_movies()` and the publish — the only
moment both sides of the question exist. The "before" inventory is read from
`data/` *before* `process_movies`, so nothing the run does can change what it
claims was live. The report is **passed to** `publish_scan_outputs`, not read
back off disk, so the gate can only ever judge the run it belongs to.

**Fail closed.** A gate that cannot be built, or a report that is truncated or
malformed, blocks. This was a real hole found by a test: the first version fell
through to a fresh `check_invariants`, which on an empty report compared 0
against 0, passed, and let the publish proceed.

### Root causes found during implementation

Three, all found by running the gate against live data rather than fixtures.
All three would have made the gate produce confident nonsense.

1. **The public source rotates its CDN host.** On 2026-09-20 the feed served
   `https://afghbn.b-cdn.net/s3/upload/videos/2026/09/[Fibwatch.Com]Seven.Snipers...`
   while the published card for the same film held `https://jrtyh.b-cdn.net/...`
   — same path, different host. Host+path matching therefore recognised **0 of
   32,983** feed rows against 1,313 published cards, and the gate reported
   `published_movie: 0` with `identity_balanced: true`. Fixed with a layered
   lookup — `exact_stream` → `path_rotated` → `content_only` — that records
   *which* layer matched, so a weak match is never presented as a strong one.

2. **Private catalogue entries carry their streams in `links[]`,** not a
   top-level `url`. Read as one entry with no URL, all 858 of them were marked
   `empty_or_invalid_record` — the most valuable part of the system reported as
   invalid. Fixed: one entry per link, which is also what INVARIANT ১ counts.

3. **Identity was being reimplemented.** A local title normaliser could not
   match "Seven Snipers (2026) Dual 1080P" in a feed to "Seven Snipers (2026)
   Dual" on a card. Replaced with `merger.movie_identity_key`, the canonical
   one.

A fourth, smaller: 34 of the catalogue's 2,475 inventory lines are the same
episode published under two categories (`lingam-2026` under both `dubbed` and
`premium`). INVARIANT ২ counts distinct streams, and both numbers are reported
so the collapse is visible rather than silent.

### Tests run

```
tests/test_movie_coverage.py        42 tests   PASS
tests/test_movie_no_loss_gate.py    12 tests   PASS
tests/test_movie_baseline.py        24 tests   PASS
```

Each test names the clause of ধারা ৪.০ it holds in place. The four corrections
with the most review history behind them each have a dedicated guard: category
never excludes; 403/geo/timeout never terminal; no sixth bucket; a quarantine
spike warns and does not block.

### Data validation results — `reports/movie-source-coverage.json`

Public sources fetched live, private source from its committed snapshot,
compared against the published catalogue:

```
INVARIANT 1 - ingestion accounting
  raw entries                 34,578
  out of scope                     0
  TOTAL IN SCOPE              34,578
    published_movie                   3,574
    published_series_episode             19
    merged_stream                     1,863
    quarantined_unresolved           29,122
    definitive_rejected                   0
  identity balanced             True     (every source row balances)

INVARIANT 2 - live preservation
  previously live               2,441 distinct streams (2,475 lines, 34 duplicated)
  currently visible             2,441   all at the exact_stream layer
  merged                            0
  visible pending                   0
  terminal evidence                 0
  UNEXPLAINED LIVE LOSS             0   ← Phase 1's completion condition
  publish                     allowed
```

Per source: `sm-movie-combined` 32,983 entries (live fetch), `hopeful-research-latest`
1,594 (cached snapshot), `bollywood-movies-collector` 1 (live fetch, and
`state/source-health.json` independently records `raw_items: 1` for it).

`quarantined_unresolved: 29,122` is large and correct: the last successful
publish was 2026-09-17, the feed has moved since, and most feed rows have never
passed verification. Per ধারা ৪.০ a quarantine spike is a WARNING, never a
BLOCK, and a test holds that.

`duplicate_entry_keys: 11,910` — the combined playlist repeats entries. Reported
as a non-blocking check.

### No-Loss results

**INVARIANT ১: PASS** — identity balances overall and per source row.
**INVARIANT ২: PASS — `unexplained_live_loss = 0`.**

### Honest limitation on the evidence

INVARIANT ২ is authoritative here: it compares two committed catalogues, and
both are files in this repository.

INVARIANT ১ is a *within-run* measurement — a feed changes between runs, so
entries fetched today are not the entries the last publish saw. The audit script
therefore records per source row how its evidence was obtained
(`live_fetch` / `cached_snapshot` / `unavailable`), so a zero that means "not
reachable" can never be read as a zero that means "nothing there". The
authoritative INVARIANT ১ is the one `scan.py` writes during a real run, where
the entries and the publish belong to the same moment. That path is implemented
and unit-tested; it will produce its first real report on the next scheduled
movies run.

### Regression checks

Full suite: 4,502 tests. The same 5 failures as the pre-change baseline, and no
others — confirmed by removing the new files and reproducing all 5
(`test_event_channel_card_design`, `test_final_card_design_contract`,
`test_final_coherence`, `test_provider_rate_policy` ×2). All five are
pre-existing and unrelated to movies-no-loss work.

Live TV, Sports, Today, Upcoming and the player core are untouched: a run that
passes no `movie_coverage` behaves exactly as before, and a test holds that.

### Requirement status

| Plan requirement | Status |
|---|---|
| §4.0 INVARIANT ১ — ingestion accounting, five destinations | **IMPLEMENTED** |
| §4.0 INVARIANT ২ — live preservation, `unexplained_live_loss = 0` | **IMPLEMENTED** |
| §4.0 `reports/movie-source-coverage.json` with both invariants | **IMPLEMENTED** |
| §4.0 publish BLOCK on real damage | **IMPLEMENTED** |
| §4.0 quarantine spike = WARNING, not BLOCK | **IMPLEMENTED** |
| §4.0 category never causes `out_of_scope` | **ALREADY SATISFIED → now enforced and tested** |
| §4.0 entry identity = source + stream family + header profile + content | **IMPLEMENTED** |
| §4.0 `definitive_rejected` limited to four hard reasons | **IMPLEMENTED** |
| §4.0 `confirmed_unavailable` is not a sixth bucket | **IMPLEMENTED** (asserted) |
| A-04 threshold guard | **ALREADY SATISFIED** — kept alongside, not deleted |

### Known limitations

1. INVARIANT ১'s in-scan report has not yet been produced by a real run (no
   private-source token and no network verification budget locally). The audit
   report stands in, and is explicitly labelled `evidence: audit`.
2. `path_rotated` and `content_only` are weaker than an exact stream match. They
   are recorded as such in `match_layers`, and INVARIANT ২ on the real catalogue
   currently matches **2,441/2,441 at the exact layer** — the weaker layers are
   carrying nothing today.
3. `definitive_rejected` is 0 because no artefact in the repository records one
   of the four hard reasons. `reports/dropped-unplayable-movies.json` records
   255 removals for "no decoded frame and the route did not answer", which is
   explicitly *not* a hard reason, so those are quarantine, not rejection.

### Next task

Phase 2 — private source fallback (S-03): `use_last_valid_cache`,
`directory_sources` and a last-good snapshot, so a transient GitHub or network
failure can never cost the 350 owner-curated `manual_trusted` items.
