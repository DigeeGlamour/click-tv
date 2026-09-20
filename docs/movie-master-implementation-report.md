# CLICK TV — Movie/Series Master Plan v3.4 · Cumulative Implementation Report

**Source of truth:** `CLICK_TV_MOVIE_MASTER_PLAN_BN.txt` v৩.৪ (১৯ সেপ্টেম্বর ২০২৬).
On any conflict, ধারা ৪ (architecture) and ধারা ৭ (implementation order) are final;
the appendices are history only.

This is the **single** cumulative report for the whole implementation. Every
phase appends its own section here rather than creating a new file, so the
history of what was changed, why, and what was proved stays in one place.

---

## Master Plan Update: v3.4 → v3.5

Received after Phase 4 was committed and pushed. **v3.5 does not supersede the
work already done** — it adds one section and splits one step.

The delta was established by diffing the two plans rather than by reading them
side by side. Six hunks changed out of 1,471 lines; everything else is
byte-identical.

| Hunk | Change |
|---|---|
| lines 4, 14 | version and revision-count header |
| after §4.8 | **NEW §4.9 — Dependency-aware parallel execution** (96 lines) |
| ধারা ৭ | ধাপ ১০ split into **১০ক** (scan frequency) and **১০খ** (parallel stages) |
| ধারা ৭ dependencies | new line: ধাপ ১০খ ← ধাপ ৭ (TTL) |
| §9 | the "no worker DAG" exclusion narrowed to "no nine separate GitHub Actions jobs"; the principle itself is restored as §4.9 |
| পরিশিষ্ট-গ | sixth review round recorded |

### What v3.5 adds

**§4.9 — "Parallel where independent, wait only where there is a real
dependency."** One run, three stages, bounded thread pools rather than nine
GitHub Actions jobs:

```
STAGE A  sequential   Source Fetch → Delta / Identity
STAGE B  parallel     Stream Verify · Series Classification · Metadata
                      · Artwork · Repair      (each its own bounded pool)
STAGE C  sequential   Aggregator → No-Loss validation (ধারা ৪.০) → atomic publish
```

The plan is honest about the size of the win: the code's own note says 150
lookups cost 2–3 minutes of a 40-minute budget, so running metadata beside
verification saves **~7% today**. It becomes worth doing only after ধাপ ৭ drops
verification from 2,475 links to ~354, and most of all during ধাপ ৫'s 600–800
lookups. That is why it is ধাপ ১০খ and not ধাপ ১.

### Status of the phases already delivered

| Phase | v3.5 impact |
|---|---|
| ০ baseline + rollback | **unaffected** |
| ১ No-Loss coverage gate | **unaffected** — §4.9 rule 4 protects it, does not change it |
| ২ private source fallback | **unaffected** |
| ৩ক shadow migration | **unaffected** |
| ৩খ classification + migration | **unaffected** |
| ৪ provider router | **unaffected** — §4.9 rule 3 reuses its per-provider budgets |

Nothing is reopened, rewritten or redone. §4.9 is additive.

### When ধাপ ১০খ is implemented

After **ধাপ ৭ (link health TTL + staggered sweep)** is complete and stable, in
the order v3.5 states: ৫ → ৬ → ৭ → ৮ → ৯ → ১০ক → **১০খ** → ১১ → ১২ → ১৩.

Its four safety rules are recorded here now so they are not rediscovered later,
and because two of them constrain work happening before ১০খ:

1. **No stage mutates shared data.** Each returns a result fragment
   (`content_id → changed fields`); one Aggregator merges them in a
   deterministic order. This one is a live constraint on today's code:
   `_annotate_recency`, `_annotate_metadata`, `_annotate_classification` and
   `_annotate_provider_pending` all mutate the movie dicts in place, which is
   correct while they run sequentially and a race the moment they do not.
2. **Single writer.** No worker writes or pushes production JSON; only Stage C.
3. **Bounded pool per stage, with coordinated host and API budgets.** Two
   stages hitting one host do not each get the full `per_host_limit`.
4. **A partial stage result is never counted as a loss.** A stage that stops on
   its time budget reports `partial`, and the Aggregator keeps those items'
   previous state. Without this a slow run makes the No-Loss gate BLOCK
   wrongly — or worse, publishes partial data.

Plus the deadlock rule: no worker waits on another worker's Future; all
dependencies live in the coordinator.

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

### Correction, made during Phase 3খ

`data/manifest.json` was in the movie catalogue file set, and the drift check
went red on it within hours — not because anything was damaged, but because the
event scans rewrite that file every few minutes. Two things were wrong behind
that noise:

* a check that goes red on every unrelated scan is one nobody reads;
* the restore behind it would have rolled **Today Match and Upcoming counts**
  back to whenever the movie baseline was taken — exactly the cross-pipeline
  damage this module's own scope note says it avoids.

`data/manifest.json` is now recorded under `shared_files`: digested so the
record stays complete, excluded from the drift check, and never written by a
restore. The baseline is 191 catalogue files, re-anchored at `008dc5d`, and
`data/movies` and `data/series` are byte-identical to the original `9eda889`
anchor, so nothing about the recorded catalogue changed. Two tests hold the
rule.

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

### Commit / push

`phase-1: enforce the no-loss coverage gate on every movie publish`
→ `a896e57cb9ff8de61523af1b0e5e341706abc465`, pushed to `origin/main` and
verified by `git ls-remote` (remote SHA == local HEAD).

### Next task

Phase 2 — private source fallback (S-03).

---

## PHASE 2 — Private source fallback (S-03)

**Plan reference:** ধারা ৭ ধাপ ২ — "কাজ কম, ঝুঁকি সবচেয়ে বেশি".

### Goal

A transient GitHub or network failure must never cost the owner's catalogue.
350 of the 1,667 published films are `manual_trusted` and all of them come from
one private repository.

### Before state

`manual/movie-sources.json`, exactly as the plan's S-03 evidence describes:

```json
"use_last_valid_cache": false     ← no fallback when a fetch fails
"require_fresh": true             ← fail if not fresh
"directory_sources": []           ← the local-checkout path is empty
```

### Root cause — and it is worse than the config suggests

Two faults, and only the first is visible in the config.

1. `require_fresh: true` + a failed archive fetch → `RuntimeError`, and
   `process_movies` takes the whole movie scan down with it. Turning
   `require_fresh` off does not fix it: the code then `continue`s and the
   private catalogue silently contributes **nothing**.

2. **The per-file cache could not have helped even if it were switched on.**
   It is consulted per *discovered* file, and a failed archive fetch discovers
   none — `discovered_sources` stays empty for that repository, so there is
   nothing for the cache branch to attach to. The fallback had to be *built*,
   not merely enabled.

### Files changed

* `scanner/movies.py` — the fallback chain, a cache-fallback source builder,
  `fallback_for` / `optional` directory semantics, and
  `degraded_repositories` in the source report.
* `manual/movie-sources.json` — `use_last_valid_cache: true` at both levels, and
  `working/private-movie-source` registered as an optional fallback checkout.
* `tests/test_movie_private_source_fallback.py` — 14 tests.

### Implementation

The preference order is exactly the one ধাপ ২ states:

```
fresh fetch  →  local checkout  →  last-good snapshot  →  (only now) fail
```

* **Local checkout** (`directory_sources` with `fallback_for`): real content
  someone put there on purpose, so it outranks the snapshot. It is **standby**
  on a healthy run and contributes nothing — otherwise every private title
  would be published twice under two source ids.
* **Last-good snapshot**: `_repository_cache_fallback_sources` rebuilds the
  discovered-file list from the cache with empty `content`, so the ordinary
  flow parses nothing, falls into the cache branch that already exists, and
  reports `status: "cached"`. The fallback runs *through* the existing code
  rather than around it.
* **Strict attribution**: only cache keys under the configured repository id
  are eligible. The real cache also holds keys from two earlier spellings of
  this source (`hopeful-research-bangla`, `hopeful-research:<hash>`), and
  serving those would resurrect a configuration that is no longer in force.
* **`require_fresh` keeps its name and gains its real meaning**: "never publish
  nothing". It raises only when there is no fallback at all.
* **An absent optional checkout reports `skipped_absent`, not `failed`.** A
  permanent red row in the artefact whose job is to make real damage visible is
  a report nobody reads.

### Root cause found during implementation

A test caught a defect in the first version: when the **checkout** served as
the fallback, its files are attributed to the directory source, so the
repository's own `require_any_valid_file` counter never moved and the run raised
anyway — the fallback publishing its films and the guard then undoing it. Fixed
by letting a checkout that stands in for a repository count as that
repository's content, and only for the repository it is declared a fallback for.

### Tests run

`tests/test_movie_private_source_fallback.py` — **14 tests, 14 pass**, covering
both fallbacks, their order, the standby rule, the strict-attribution rule, an
empty snapshot being refused as content, two consecutive failures being as
survivable as one, and four contract tests on the shipped config (including
that the checkout path is one `.gitignore` already covers — pointing elsewhere
would commit the private catalogue).

### Data validation results — against the real catalogue

GitHub simulated as completely unreachable, real `manual/movie-sources.json`,
real `state/manual-movie-remote-cache.json` (copied, not mutated):

```
degraded_repositories   {'hopeful-research-latest': 'last_good_snapshot:13_file(s)'}
recovered               694 movie items + 70 series items
row statuses            cached 13 · degraded_fallback 1 · skipped_absent 1
by category             South Indian 258 · Dubbed 160 · Hindi 105 · Bangla 80
                        · English 70 · Premium 21
cache integrity         858 items before, 858 after — a degraded run does not
                        damage the snapshot it is reading
```

**Before this change the same conditions raised `RuntimeError` and the entire
movie scan died.**

### No-Loss results

Phases 1 and 2 compose the way the plan intends:

* Without Phase 2, a private-source outage means the 350 `manual_trusted` cards
  are absent from the incoming catalogue → `unexplained_live_loss ≈ 350` →
  Phase 1 **BLOCKs** the publish and the site keeps yesterday's pages. Safe,
  but frozen.
* With Phase 2, the snapshot supplies those entries → the catalogue is complete
  → `unexplained_live_loss = 0` → the publish proceeds and the rest of the
  scan's work still reaches the site.

The gate is the safety net; the fallback is what stops it having to catch
anything.

### Regression checks

Full suite: **4,576 tests** (4,562 before this phase, +14). The same 5
pre-existing failures and no others. A healthy fetch behaves exactly as before
— same items, all rows `fresh`, `degraded_repositories` empty — and a test
holds that. Live TV, Sports, Today and Upcoming do not read
`manual/movie-sources.json`.

### Requirement status

| Plan requirement | Status |
|---|---|
| ধাপ ২ `use_last_valid_cache: true` | **IMPLEMENTED** (and made reachable, which it was not) |
| ধাপ ২ local checkout | **IMPLEMENTED** (`fallback_for`, standby on healthy runs) |
| ধাপ ২ last-good snapshot | **IMPLEMENTED** |
| §Master-safety 11 "Private source preferred primary থাকবে" | **ALREADY SATISFIED** (`_merge_manual_over_discovered`) — unchanged |
| §Master-safety 6 "সব API unavailable হলেও live content last-good দিয়ে থাকবে" | **IMPLEMENTED** for the private source |

### Known limitations

1. The snapshot is only as good as the last successful fetch. Its age is
   recorded per row (`last_fetched_at`) and the degradation is named in
   `degraded_repositories`, but nothing yet *alerts* on a repository that has
   been degraded for several runs. That belongs with ধাপ ১৩ (observability).
2. `working/private-movie-source` is empty in CI, so the checkout branch is
   exercised by tests rather than by a real run. It is ready for an operator or
   a workflow step to populate.
3. Nine of the twenty private files report `skipped_unparseable` every run. All
   nine are `history_skipped.txt`, they hold **0** cached items, and nothing is
   being lost — the repository's `ignore_filenames` lists `history.txt` but not
   `history_skipped.txt`. Left alone deliberately: it is report noise, not data
   loss, and changing the ignore list is outside ধাপ ২.

### Commit / push

`phase-2: give the private movie source a real fallback chain`
→ `62cffb3f9967d7358848e56fc0c84667dda87c1c`, pushed to `origin/main` and
verified by `git ls-remote`.

### Next task

Phase 3ক — Shadow Migration run.

---

## PHASE 3ক — Shadow Migration run (dry-run, nothing published)

**Plan reference:** ধারা ৭ ধাপ ৩ক, with the classification rules of ধারা ৪.৬.

### Goal

Rehearse the reorganisation of 563 cards before a single one moves, and refuse
to let ধাপ ৩খ start unless the stream-coverage identity balances.

### Before state

`grep` confirmed the plan's D-01 finding exactly: **there is no SxxExx regex
anywhere in the scanner**. `scanner/content_router.py` has one `\bs\d{1,2}\s*e\d{1,3}\b`
test, but it is used to decide *which pipeline* a row belongs to, never to
extract a season or an episode. Series exist only where the private TXT
catalogue states an explicit `Season:`/`Episode:` structure, so every episode
in a public M3U goes straight into the movie pipeline as its own card.

### Files changed

New: `scanner/series_signal.py`, `scripts/movie-series-shadow-migration.py`,
`tests/test_series_signal.py` (31 tests),
`tests/test_movie_series_shadow_migration.py` (15 tests),
`reports/movie-series-migration-dryrun.json`.

Changed: none. Nothing in the publish path was touched — this phase is a
rehearsal and writes one report.

### Implementation

`scanner/series_signal.py` reads season and episode from the title with regex
and **no API call at all**, before the title cleaner runs (the cleaner strips
exactly the kind of token the evidence lives in).

Two rules are absolute, and both have dedicated tests:

* **An episode number is never invented.** `S01` means the season is known and
  the episode is not. `S1 P01` is a *Part*, and a Part is not an Episode.
* **A series identity never contains a year.** `show_key` is built from the
  merger's canonical title normaliser and then deliberately drops the year the
  movie identity would append, because House of the Dragon is 2022, 2024 *and*
  2026 in this catalogue and is one show.

The three evidence tiers of ধারা ৪.৬, and what each may do:

| Tier | Evidence | May move to a series card |
|---|---|---|
| explicit_episode | `SxxExx` in the title | yes |
| pack_signal | "Complete / Full Season / Pack" | yes, as *Season N — Complete Season* |
| sibling_episode | the same show has an `SxxExx` row in this catalogue | yes, at **zero API cost** |
| no_evidence | a season marker and nothing else | **no** — stays a visible movie card with `classification_pending` |

### Root causes found during implementation

Both found by running the detector against real titles instead of trusting it.

1. **`S01E15` was being read as "episodes 1 to 5".** The range pattern's
   separator was optional, so `E15` matched as `1` then `5`. Left alone this
   would have corrupted **all 348** explicit episode numbers in the catalogue
   at once. Fixed by making the separator mandatory, and by refusing a "range"
   whose end is a resolution (`1080`, `720`, …), a year, above 200, or not
   greater than the start.

2. **The obvious season-only pattern loses real rows.** `S\s?(\d+)\b(?!\s?E)`
   rejects `Peaky Blinders S01 English` — because "English" starts with E. That
   is five real season-only rows lost, including the one the plan uses as its
   own worked example of tier 2.

A third, smaller: `Newton's 3rd Law` is in this catalogue, and a pattern
without an apostrophe guard reads `'s 3` as season 3.

### Tests run

```
tests/test_series_signal.py                    31 tests   PASS
tests/test_movie_series_shadow_migration.py    15 tests   PASS
```

Including title protection (1917, 2012, Drishyam 2, Blade Runner 2049,
Ocean's 8, Se7en, 300, MS Dhoni), the `S01E15` regression, all three range
spellings, Part-is-not-Episode, and a property test that the four
stream-coverage terms are a genuine partition.

### Data validation — the detector reproduces the plan's own audit

`scanner/series_signal.py` was written without reference to the plan's counts,
then measured against all 1,667 published cards:

| | Plan (counted by hand) | Detector |
|---|---|---|
| explicit `SxxExx` rows | ৩৫০ | **348** |
| season-marker-only rows | ২১৩ | **223** |
| tier 1 — pack signal | ১৬ | **16** ✔ exact |
| tier 2 — sibling-proven | ২৬ | **26** ✔ exact |
| tier 3 — no evidence | ১৭১ | **181** |
| Bachelor Point cards | ৩৭ | **37** ✔ exact |
| Sultan Salahuddin Ayyubi | ২৩ | **23** ✔ exact |
| Kurulus Osman | ১২ | **12** ✔ exact |
| Resort | ২২ | **23** |

### Data validation — `reports/movie-series-migration-dryrun.json`

```
movie cards now                1,667
movie cards after              1,277   (of which 181 carry classification_pending)
series shows created             114
cards absorbed into series       390
cards left pending as movies     181

STREAM COVERAGE
  before_stream_count          2,159
    after_movie_streams        1,554
    after_series_streams         390
    merged_backups                31
    pending_visible_streams      184
  sum of terms                 2,159
  BALANCED                      True      ← ধাপ ৩খ's precondition
```

Every one of the 2,159 links the site serves lands in exactly one term.
114 new shows + 70 already published = 184 shows, against the plan's estimate
of ~178.

### The finding that changes ধাপ ৩খ

**15 of the 114 proposed shows are already published as series.**

```
108 Base Hospital Uri · Bigg Boss · Brothers and Sisters ·
Cousins and Kalyanams · Descendants of the Sun · Dirilis Ertugrul ·
House of the Dragon · Khatron Ke Khiladi · Lanterns · Man vs Wild ·
Pritam and Pedro · Resort · Star Trek Strange New Worlds ·
Sultan Salahuddin Ayyubi · Thukra Ke Mera Pyaar
```

If ধাপ ৩খ created a card per proposed show, the site would end up with two
cards for each of these — the under-merge the plan asked this rehearsal to look
for, caught before anything moved. **ধাপ ৩খ must merge into the existing series
card, not create a new one**, and that is now a written precondition rather
than something to discover afterwards.

Four over-merge warnings (Dirilis Ertugrul, House of the Dragon, Reacher, My
Girlfriend is an Alien) are all the same thing: one show with several release
years. The plan verified three of them by hand as legitimately one show, and
the year-free identity handles them correctly — they are reported for a person,
never acted on. One prefix warning, `show:bigg-boss` against
`show:bigg-boss-ott`, is a false positive: those really are two shows, and the
report says so rather than merging them.

### No-Loss results

The rehearsal writes no catalogue, and a test proves it: the ধাপ ০ baseline
digests are re-verified before and after `build()` runs against the real
repository, and report zero differences both times.

### Requirement status

| Plan requirement | Status |
|---|---|
| ধাপ ৩ক dry run, nothing published | **IMPLEMENTED** |
| ধাপ ৩ক stream-coverage identity balances | **IMPLEMENTED — balances (2,159 = 2,159)** |
| ধাপ ৩ক over-merge warning | **IMPLEMENTED** (4, all benign, explained) |
| ধাপ ৩ক under-merge warning | **IMPLEMENTED** (16, and 15 of them change ধাপ ৩খ) |
| §4.6 season/episode by regex, zero API | **IMPLEMENTED** |
| §4.6 episode number never invented | **IMPLEMENTED** |
| §4.6 title protection (1917 / 2012 / Drishyam 2 / Blade Runner 2049) | **IMPLEMENTED** |
| §4.6 three evidence tiers | **IMPLEMENTED** |
| §4.6 unproven rows stay visible movie cards | **IMPLEMENTED** (181 rows, 184 links) |
| §4.6 series identity carries no year | **IMPLEMENTED** |
| §4.6 series identity priority 1 (external show id) | **NOT YET** — belongs to the classification cache (ধাপ ৩খ) |

### Known limitations

1. Series identity currently rests on priority 2 (normalised title). Priority 1
   (external show id) and priority 3 (first-air-year, for collisions only) need
   the classification cache, which is ধাপ ৩খ. The plan checked 262 distinct
   base names and found no collision today, so priority 2 is sufficient for
   this catalogue and the other two are future protection.
2. The dry run compares against the catalogue as published. It does not model
   what a *fresh scan* would produce, because that needs the verification
   budget and the private token.
3. `Resort` counts 23 here against the plan's 22, and season-only rows 223
   against 213. The catalogue has moved since the plan was written; no rule
   depends on the literal numbers, and the two tiers the plan counted exactly
   (16 and 26) still match exactly.

### Commit / push

`phase-3a: shadow migration run, and the precondition it uncovered`
→ `3294f9fcbb8bafcc399491079ab0f9f1de5292af`, pushed to `origin/main` and
verified by `git ls-remote`.

### Next task

Phase 3খ — content classification and title parsing.

---

## PHASE 3খ (part 1) — classification cache, and making the uncertainty visible

**Plan reference:** ধাপ ৩খ (D-01, S-05), ধারা ৪.২ · ৪.৬ · ৪.৭, and ধারা ৪.০'s
category rule.

### Why this phase is split

ধাপ ৩খ is the one destructive step in the plan: it rewrites 390 cards. The plan
requires every step to be independently shippable **and independently
rollback-able**, so it is being delivered in two halves:

* **part 1 (this section)** — record what is known, mark what is pending. Adds
  only: nothing moves, nothing is hidden, nothing is dropped.
* **part 2** — the actual regrouping of 390 cards into 114 shows, merging into
  the 15 series that already exist.

The order is not caution for its own sake. The flags part 1 adds are what
INVARIANT ২ reads to tell "still visible, classification pending" apart from
"gone". They have to exist *before* anything starts moving, or the first
migration runs with the gate half blind.

### Before state

No classification state of any kind. `grep` for `classification_cache`,
`classifier_version` and `normalized_show_key` returned nothing, which is
exactly what the plan's S-05 says.

### Files changed

New: `scanner/movie_classification.py`, `tests/test_movie_classification.py`
(33 tests).

Changed: `scanner/movies.py` — `_annotate_classification()` and the
`category_pending` flag in the grouping loop.

### Implementation

**`state/movie-classification-cache.json`**, the third and last of the three new
state files ধারা ৪.২ allows. Provider-agnostic, per the v৩.৪ correction: the
record carries an `external_ids` **map**, an `identity_source` naming how the
identity was settled, and a `disambiguation_context`; `tmdb_tv_id` survives only
as a compatibility mirror, and a record identified by TVmaze alone is a valid
record.

**Version invalidation** (ধারা ৪.৭): a record written by an older
`classifier_version` is stale whatever its age. ধাপ ৩ changes the rules, and
answers derived from the old ones must not sit behind a TTL pretending to be
current. The risk this guards is the one the plan identified by reading the
code — not a "not found" getting stuck, which this codebase already retries,
but a **wrong match from a dirty title** cached as applied for 90 days.

**Fill-only, and confidence only rises.** A provider that answers later
strengthens a record the regex created; a provider that is unavailable leaves
it exactly as it was. That is ধারা ৪.৭'s "all APIs down" contract applied to
classification as well as to metadata.

**`category_pending`** (ধারা ৪.০, v৩.৪ correction 2). The behaviour was already
correct — an unknown category has always gone to Mix, so nothing was being lost
— but nothing recorded *why* a film is in Mix. The flag is taken from the
pre-canonical value, because reading it afterwards would flag nothing at all:
by then every unknown category is already the string "Mix", which is known.

### Tests run

`tests/test_movie_classification.py` — **33 tests, 33 pass.** Provider-agnostic
shape, fill-only behaviour, classifier-version staleness, TTL, the zero-cost
evidence path, deduplicated unresolved shows, and — the one that matters most —
that the annotation adds only: no card added, removed or repointed, a plain
film left completely untouched, and a broken classifier leaving the catalogue
exactly as the previous behaviour produced it.

### Data validation — against all 1,667 published cards

```
series signals                     571
  with an explicit episode         348
  season-only                      223
  still classification_pending     181

shows proved at zero API cost      114
shows still needing a provider     152
cards that gained an episode
  the title never stated             0     ← the rule that cannot bend
```

The plan budgeted "~২৫৫ classification lookups" (≈108 confirmed shows plus 147
deduplicated unresolved). Measured: 114 + 152 = **266 unique shows, of which
only 152 need a provider at all** — cheaper than the plan's estimate, because
more shows were proved for free than it assumed.

### No-Loss results

Nothing moved, so INVARIANT ২ is unaffected. The part that matters for the gate
is that `classification_pending` and `category_pending` are both already in
`movie_coverage.PENDING_FLAGS`, so a flagged card counts as **visible pending**
rather than as a loss — asserted by a test that reads the gate's own constant.

### Requirement status

| Plan requirement | Status |
|---|---|
| §4.2 `state/movie-classification-cache.json` | **IMPLEMENTED** |
| §4.2 provider-agnostic `external_ids` + `identity_source` | **IMPLEMENTED** |
| §4.2 `tmdb_tv_id` as compatibility only | **IMPLEMENTED** |
| §4.7 `classifier_version` in the invalidation key | **IMPLEMENTED** |
| §4.0 unknown category → Mix, never dropped | **ALREADY SATISFIED** |
| §4.0 `category_pending` flag | **IMPLEMENTED** |
| §4.6 `classification_pending` on unproven rows | **IMPLEMENTED** |
| §4.6 episode number never invented | **IMPLEMENTED** (0 of 1,667 on real data) |
| A-06 observability counters | **PARTIALLY IMPLEMENTED** — `series_signal_candidates`, `confirmed_series`, `season_only_unknown_episode`, `classification_cache` counts are produced; the rest belong to ধাপ ১৩ |
| ধাপ ৩খ regroup 390 cards into shows | **NOT YET** — part 2 |

### Known limitations

1. `cleaner_version` is not yet part of the metadata cache key. ধারা ৪.৭ asks
   for `cleaner_version` **and** `classifier_version`; the classifier half is
   done, and the cleaner half belongs with ধাপ ৩খ part 2, which is what changes
   the cleaner.
2. The 152 unresolved shows need the Provider Router (ধাপ ৪) before they can be
   asked about. The list is produced and deduplicated so that step can spend a
   budget on it; nothing asks a provider yet.
3. The flags are written by `process_movies`, so they reach the published cards
   on the next real movie scan, not before.

### Commit / push

`phase-3b: record what the catalogue proves, and mark what is still pending`
→ `37068a2d2894bf434ef1a9efa111638c926e2227`, pushed to `origin/main` and
verified by `git ls-remote`.

### Next task

Phase 3খ part 2 — the migration itself.

---

## PHASE 3খ (part 2) — the migration

**Plan reference:** ধাপ ৩খ (D-01), gated on the ধাপ ৩ক rehearsal, with the
identity rules of ধারা ৪.৬.

### Goal

Move the 390 proven episode cards out of the movie catalogue and into the
series catalogue — Bachelor Point's 37 cards becoming one show with 37
episodes — without losing a stream, inventing an episode number, or creating a
second card for a show the site already publishes.

### Files changed

New: `scanner/series_migration.py`, `tests/test_series_migration.py`
(29 tests).

Changed: `scanner/movies.py` (`_migrate_series_cards`, called after
classification and before grouping), `scanner/series.py` (`_series_identity`
and episode provenance passthrough), `config/settings.json`
(`movie_series_migration`).

### Implementation

Migrated shows are written into the **staging catalogue**
`series.prepare_manual_series` already reads, not published directly. They
therefore go through the same normalisation, episode ordering, quality sort,
merge and publisher as the private catalogue's own shows. Nothing about series
publishing is re-implemented.

The step is controlled by `movie_series_migration` in `config/settings.json`
(`enabled`, `dry_run`), so it can be turned off or rehearsed without a code
change — which is what the plan means by every step being independently
rollback-able. The wrapper returns the **original** list on any failure, so a
migration that breaks publishes the catalogue exactly as it would have been
published without this step, never a partially emptied one.

### The ধারা ৪.৬ identity fix, and the live defect it repairs

`series._merge_duplicate_series` keyed on `(name, year, category)`. The year in
that key is exactly what ধারা ৪.৬ forbids, and it is not theoretical — it is
splitting shows in production **right now**:

```
Star Trek: Strange New Worlds   4 cards (2022, 2023, 2025, 2026)  2+2+2+6 episodes
Undekhi                         2 cards (2022, 2026)              5+1 episodes
```

The identity is now year-free. The **category stays in it**: which shelf a show
sits on is a publishing decision, not an identity claim, and moving shows
between categories is ধাপ ১১'s job. Nine shows are deliberately published under
two categories today (Premium is a cross-cutting shelf) and this leaves that
exactly as it was.

### Four faults found by running it against the real catalogue

Every one of these would have lost data or told a lie, and every one was found
by measuring rather than by reading.

1. **Two streams lost to an episode-key collision.** Two "Complete Season"
   cards in one season both got `episode_key: "complete-season"`, and
   `series._normalize_series` keeps "the first spelling" — right for a batch
   link beside its own episodes, wrong for two different uploads. Unnumbered
   entries now get a per-card, **digit-free** suffix. Digit-free matters:
   `episode_number_range` reads the first digit run in a key as the episode
   number, so `unspecified-2` would claim to be episode 2.

2. **Two sources for one episode would have dropped one.** Two cards for S01E05
   are one episode with two links, and they are now merged rather than emitted
   as two records for the pipeline to deduplicate destructively.

3. **`Resort` would have become a third card.** Its episodes are all filed
   under Mix while the show is published under Premium/South Indian, so the
   `(show_key, category)` merge would not have matched. A migrated show now
   adopts the category it is already published under.

4. **`Dirilis Ertugrul` would have become a second card.** The published series
   is literally named `"Dirilis Ertugrul (Season 1"` — a truncated line from
   the private TXT source, unbalanced bracket and season marker included — so
   it keyed as `show:dirilis-ertugrul-season-1`. Both sides now run the name
   through the same detector before taking the key.

A fifth, caught by a test rather than by data: a `Part 01` entry's **label**
contains a digit even when its key does not, so the parser reads it. A Part
number *is* stated by the source, so ordering by it reads the source rather
than inventing — but a Part is not an Episode, the label says so, and the
record carries `episode_number_stated: false` through to the published episode
so a sort position can never be mistaken for a number somebody claimed.

### Tests run

```
tests/test_series_migration.py        29 tests   PASS
tests/test_series_signal.py           31 tests   PASS
tests/test_movie_classification.py    33 tests   PASS
existing series suite (6 modules)     58 tests   PASS   (unchanged behaviour)
```

### Data validation — against the real catalogue

```
movie cards in                 1,667
  staying as movies            1,277   (181 of them classification_pending)
  migrated                       390
shows created                    114
  joining an existing series      15    ← every one the rehearsal predicted
  genuinely new                   99
episodes created                 390
streams migrated                 421
skipped                            0

links before  2,159  =  staying 1,738  +  migrated 421      NO STREAM LOST
```

Through the **real** series pipeline, with stand-ins for the private records:

```
series after merge        114 cards        (not 115, not 118)
episodes                  394             (390 migrated + 4 private)
migrated urls preserved   421 / 421
private urls preserved    4 / 4
Dirilis Ertugrul          1 card           (was about to be 2)
Resort                    1 card           (was about to be 3)
Star Trek SNW             1 card           (is 4 in production today)
unnumbered entries that
  acquired a number       0
numbered entries whose
  number changed          0
```

### No-Loss results

The split is a partition of every link, asserted as a property test and
verified on all 2,159 real links. Nothing relies on that alone: the Phase 1
gate runs on the same scan, and a migrated stream that failed to arrive in the
series catalogue would show up as `unexplained_live_loss` and block the
publish.

### Requirement status

| Plan requirement | Status |
|---|---|
| ধাপ ৩খ regroup proven cards into shows | **IMPLEMENTED** |
| ধাপ ৩খ merge into existing series, no second card | **IMPLEMENTED** (15/15) |
| §4.6 series identity carries no year | **IMPLEMENTED** — and repairs a live 4-way split |
| §4.6 episode number never invented | **IMPLEMENTED** (0 of 390) |
| §4.6 season-only → Complete Season / Unspecified | **IMPLEMENTED** (16 / 26) |
| §4.6 unproven rows stay visible movie cards | **IMPLEMENTED** (181) |
| §8 "একই মুভি একাধিক সোর্সে → ১টি কার্ড" | **IMPLEMENTED** for episodes |
| Master-safety 10 "Episode number invent করা যাবে না" | **IMPLEMENTED** |
| Master-safety 1 "কোনো live stream silently drop নয়" | **IMPLEMENTED** (partition + gate) |

### Known limitations

1. The migration runs on the next real movie scan; the published catalogue
   changes then, not now. Expect **1,667 → 1,277 movie cards** and
   **70 → ~169 series**, which the plan warned about in ধারা ১০.
2. `Dirilis Ertugrul (Season 1` keeps its malformed display name — the merge
   takes the first record's name. Fixing the name belongs to the private
   source, not to this step.
3. Bachelor Point migrates into **Mix**, because all 37 of its cards are in Mix
   today. ধাপ ১১ (Mix redistribution) is what moves it to Bangla.
4. Nine shows remain published under two categories. That is pre-existing, and
   consolidating it is ধাপ ১১'s question.

### Commit / push

`phase-3b: migrate 390 proven episode cards into the series catalogue`
→ `008dc5d479a594764f49cb2067985db24a76ecc3`, pushed to `origin/main` and
verified by `git ls-remote`.

### Next task

Phase 4 — Provider Router, quota and circuit breaker.

---

## PHASE 4 — Provider Router, quota and circuit breaker (S-06)

**Plan reference:** ধারা ৪.৭, and ধারা ৭ ধাপ ৪ — deliberately **before** the
backfill, because without it a provider that answers badly gets its answer
cached for 90 days, and that is harder to undo than running out of quota.

### Before state

`scanner/provider_health.py` was already good, and the plan says so
("failover নতুন করে বানাতে হবে না"): 429 with Retry-After, a bounded
2s/5s/15s/30s ladder, no retry on 401/403, per-provider pacing, persisted
cooldowns. Four things were missing.

| Missing | Why it matters |
|---|---|
| quota accounting | OMDb's free allowance can be spent by one run with nothing left for a fallback |
| a breaker that spans requests | the existing one opens only when a *single* request exhausts its ladder — 150 lookups failing once each never opened it |
| capability/health-driven order | the chain was a fixed sequence, which the plan calls "বর্তমানে কোডে যা আছে তার বর্ণনা মাত্র — চূড়ান্ত নির্দেশ নয়" |
| `cleaner_version` / `classifier_version` in the cache key | ধাপ ৩ changed both, so every answer the old rules produced sat behind a 90-day TTL |

### Files changed

New: `scanner/provider_router.py`, `tests/test_provider_router.py` (43 tests).

Changed: `scanner/provider_health.py` (quota, latency, cross-request breaker),
`scanner/metadata_providers.py` (the chain asks the router for its order),
`scanner/movie_metadata_cache.py` (the three-part invalidation key),
`scanner/movies.py` (`_annotate_provider_pending`), `config/settings.json`,
`tests/test_provider_rate_policy.py` (two stale fixtures).

### Implementation

**The order is computed, not written down.** `provider_router.order()` answers
per question — capability, content kind, and the ids actually held — and sorts
by configured weight, then remaining quota, then measured latency. A provider
that cannot answer is excluded rather than demoted: Fanart returns artwork and
nothing else, Cinemeta needs an `imdb_id`, TVMaze knows television, AniList
knows anime. Asking anyway spends a request to be told nothing.

**The defaults change nothing.** Weights reproduce the old sequence exactly, so
nothing moves until the config says otherwise. A migration that quietly
reorders seven providers on the day it ships is not one anybody can review.
Verified: `['tmdb','omdb','cinemeta','moviesdatabase']` for a movie with an
imdb_id, `['tmdb','omdb','cinemeta','tvmaze']` for a series,
`['tmdb','omdb','anilist']` for anime — identical to the code it replaced.

**It is a sequence, not a fan-out.** ধারা ৪.৭: "একই আইটেমের জন্য সব প্রোভাইডার
সমান্তরালে কল করা যাবে না — কোটা নষ্ট হবে." The walk re-asks the router after each
provider, because an id one of them returns can make the next one eligible —
Cinemeta and Fanart are unreachable until somebody produces an `imdb_id` or a
`tmdb_id`.

**Quota with a reserve.** A daily soft budget per provider, with
`reserved_for_fallback` held back from ordinary enrichment, because a budget
with no reserve is spent by the first thousand films of the day and the one
lookup that actually needed a fallback finds nothing left. Spending it is not
an error and marks nothing unhealthy — the router simply prefers somebody else.

**A breaker that spans requests.** Four consecutive failures open it for ten
minutes; a half-open test is allowed through afterwards, and a failed test
reopens it for twice as long, up to an hour. A **real "no result" resets the
count** rather than feeding it: ধারা ৪.৭ — "404 / no result → প্রোভাইডার ঠিক
আছে, শুধু এই আইটেম মেলেনি" — a provider that keeps saying "not here" is working
perfectly.

**The three-part invalidation key.** `metadata_schema` answers "which fields",
and now `cleaner_version` and `classifier_version` answer "from which title,
under which rules". The plan identified the real risk by reading the code: a
"not found" never gets stuck here because a failed lookup is never stamped and
is retried — the danger is a **wrong match** from a dirty title cached as
`applied` and believed for ninety days.

### Two pre-existing test failures, fixed honestly

`test_a_cached_movie_costs_no_lookup_at_all` and
`test_settled_metadata_gets_the_long_ttl_and_a_recent_release_the_short_one`
have been failing since before this work began. The code was right and the
fixtures were stale: they carried no `metadata_schema`, so raising it to 2 made
them due for a re-read whatever their age, and the tests silently stopped
measuring the TTL they are named for.

The fixtures now state the versions they mean to test, read from the module so
they cannot drift apart again. That is strengthening, not weakening: a test
called "a fresh cache entry costs no lookup" should set up an entry that is
genuinely current in every dimension. **The pre-existing failure count goes
from 4 to 2.**

### Two faults the suite caught in the first draft

1. **Fanart stopped being asked.** The first version ran the artwork round only
   when *nothing* had produced a backdrop, so a Cinemeta backdrop suppressed
   Fanart — and `BACKDROP_PRIORITY` ranks Fanart above Cinemeta. The old code
   asked Fanart whenever TMDB had supplied no artwork, regardless of Cinemeta.
   Fixed by asking an artwork provider only when it could **improve on the best
   candidate held so far**, which is that rule generalised, and which also stops
   spending a request on a provider that could only tie or lose.

2. **Latency could never decide anything.** Sorting on raw remaining quota made
   "unmetered" beat every metered provider outright — an infinite headroom
   always sorts first — so the measured-behaviour tiebreak was unreachable, and
   that is half of what makes the order dynamic. Headroom is now a three-value
   bucket (plenty / running low / none) and latency decides within it.

### Tests run

```
tests/test_provider_router.py       45 tests   PASS
metadata suite (7 modules)         150 tests   PASS   (unchanged behaviour)
full suite                       4,730 tests   3 pre-existing failures
```

The pre-existing failure count is down from 5 to 3: two were the stale cache
fixtures fixed above, and one (`test_final_coherence`) turned out to be an
artefact of a dirty working tree rather than a real failure — the suite writes
into the real `reports/` and `state/`, which is why CI runs it in a throwaway
worktree. The three that remain are site asset-versioning and stylesheet
contracts, unrelated to this work.

### Data validation — against the real catalogue

```
provider router   7/7 available
metadata_pending  1,371 of 1,667 cards
artwork_pending   0 of 1,667
```

1,371 pending matches the plan's measured metadata coverage (~21% resolved).
`artwork_pending: 0` is honest and is **not** a claim that artwork is fine:
every card carries a `logo`, but 72% of those point at a host that answers 403
from Bangladesh. "Has a URL" and "has a picture that loads" are different
questions, and the second one is ধাপ ৬'s.

### Requirement status

| Plan requirement | Status |
|---|---|
| §4.7 429 / Retry-After / cooldown ladder | **ALREADY SATISFIED** — unchanged |
| §4.7 401/403 never retried | **ALREADY SATISFIED** — unchanged |
| §4.7 `remaining_quota` · `reset_at` · `daily_soft_budget` | **IMPLEMENTED** |
| §4.7 `cooldown_until` | **ALREADY SATISFIED** (`next_retry_at`; reported under the plan's name rather than duplicated — two fields holding one truth is a bug the plan itself names) |
| §4.7 `average_latency_ms` · `last_http_status` | **IMPLEMENTED** |
| §4.7 circuit breaker 3–5 → open → half-open | **IMPLEMENTED** |
| §4.7 OMDb reserve for emergency fallback | **IMPLEMENTED** |
| §4.7 404 / no result → next provider, provider stays healthy | **IMPLEMENTED** |
| §4.7 no parallel calls for one item | **IMPLEMENTED** (asserted by construction) |
| §4.7 capability-aware, configurable routing | **IMPLEMENTED** |
| §4.7 negative cache: 429/timeout/5xx/auth never cached | **ALREADY SATISFIED** — a failed lookup is never stamped |
| §4.7 `cleaner_version` + `classifier_version` in the key | **IMPLEMENTED** |
| §4.7 `metadata_pending` / `artwork_pending` | **IMPLEMENTED** |
| §4.7 all APIs down → still publish | **ALREADY SATISFIED** — now recorded rather than only true |
| A-06 provider quota in the run report | **IMPLEMENTED** (`provider_router.snapshot`) |

### Known limitations

1. The budgets are guesses until a run measures real usage: OMDb 900/100 and
   MoviesDatabase 450/50. They are configuration, so correcting them costs no
   code change.
2. `high-confidence → chain থামাও` is still `_complete_enough`, the existing
   rule. The confidence policy in `movie_metadata_confidence.py` is not yet
   consulted for *stopping*, only for applying.
3. The backfill queue itself is ধাপ ৫. This phase produces the marks it will
   read.

### Commit / push

`phase-4: route metadata providers by capability, quota and health`
→ `7f24ed3e49ed73bc16158f8dd057c8c834b9366a` (pushed as `4f73bd9`), verified by
`git ls-remote`.

### Next task

Phase 5 — the controlled metadata/artwork backfill.

---

## PHASE 5 — Controlled metadata / artwork backfill (A-02)

**Plan reference:** ধারা ৭ ধাপ ৫, A-02. Both preconditions were met before it
started: ধাপ ৩খ (or the budget is spent on dirty titles) and ধাপ ৪ (or a
provider that answers badly has its answer cached for ninety days — the plan
calls that the harder damage to undo).

### Goal

Finish in one or two nights what the ordinary 150-lookup ceiling takes nine
days to do, without overrunning the scan or exhausting an allowance a later run
needs more.

### Before state

`movie_metadata_cache.enrich` was already doing the hard part, and none of it
was rebuilt: cache first, never-resolved before refresh, a seven-day cooldown
after a failed match, and an `availability` predicate that *abandons* the
remaining lookups rather than recording them as failures when every provider is
down. What it did not have was a budget that responds to anything —
`DEFAULT_LOOKUP_BUDGET` is a constant.

### Files changed

New: `scanner/movie_backfill.py`, `tests/test_movie_backfill.py` (30 tests).

Changed: `scanner/movies.py` (`_annotate_metadata` computes the budget and
records the run), `config/settings.json`.

### Implementation

**The number is not the point, and the plan says so twice.** A fixed 800 is a
promise the run cannot keep, so:

```
safe_batch = min(backfill_target, provider_remaining_budget, time_budget)
```

The **reason travels with the number**, because "we did 45" and "we did 45
because OMDb ran out" are different facts and only the second one tells anybody
what to change.

**`provider_remaining_lookups` is the maximum across providers, not the sum.**
One lookup is answered by one provider walking the chain until something
replies; two providers with 100 left each do not make 200 lookups possible,
they make the run survivable when the first one runs out. Artwork-only
providers are not counted as metadata headroom.

**A backfill never takes the whole scan.** It may have 35% of the remaining
time budget. Verification is what the catalogue is for, and a backfill that
pushes the run past its budget gets the run killed rather than the backfill
trimmed.

**Running out is an ordinary outcome.** "সব প্রোভাইডার শেষ → queue pause, পরের
রানে resume": the queue records that it paused and why, the catalogue publishes
exactly as it would have, and the next run carries on from where the cache left
it. An ordinary run that simply spent its 150 is *not* a paused backfill, and a
test holds that distinction.

**It ships switched off.** `movie_metadata_backfill.enabled` is `false`, so the
ordinary path is byte-for-byte unchanged and the elevated target is opt-in —
which is what "এক বা দুই রাত চালিয়ে আবার ১৫০-এ ফেরত" means in practice.

### Data validation — the arithmetic against real settings

```
backfill off                              150 lookups   (ordinary ceiling)
on, full time and quota                   700           limited by target
on, 30 of 40 minutes already gone         210           limited by time_budget
on, providers have 45 left                 45           limited by provider_quota
on, every provider exhausted                0  PAUSED   resumes next run
on, time budget fully spent                 0  PAUSED   resumes next run
```

### Tests run

`tests/test_movie_backfill.py` — **30 tests, 30 pass.** The three-way minimum
and which limit binds; the max-not-sum rule; that an unavailable or
artwork-only provider is not counted; pause versus an ordinary spent budget;
state that survives a damaged file; a run log that is pruned; and that the
shipped config has the backfill off.

### No-Loss results

Untouched. Nothing here drops, hides or reorders a card — it only decides how
many provider lookups a run may make. ধারা ৪.৭'s contract still holds: a film
whose lookups never happen publishes with `metadata_pending`.

### Requirement status

| Plan requirement | Status |
|---|---|
| A-02 `safe_batch = min(target, provider_remaining, time_budget)` | **IMPLEMENTED** |
| A-02 600–800 as a maximum target, not a fixed number | **IMPLEMENTED** (700, configurable) |
| A-02 one provider exhausted → next provider | **ALREADY SATISFIED** (ধাপ ৪ router) |
| A-02 all providers exhausted → pause, resume next run | **IMPLEMENTED** |
| A-02 live content always published | **ALREADY SATISFIED** |
| A-02 precondition ধাপ ৩খ | **MET** |
| A-02 precondition ধাপ ৪ | **MET** |

### Known limitations

1. `seconds_per_lookup` is 1.0, taken from the code's own note that 150 lookups
   cost two to three minutes. It is configuration, and the run log records
   what actually happened so it can be corrected from evidence.
2. The elevated run has not been executed — it needs live API keys and a real
   scan. The machinery, its limits and its pause/resume are unit-tested; the
   owner turns `enabled` on for a night when ready.
3. Artwork backfill rides on the same lookups (a provider answering for
   metadata returns artwork in the same response). A separate artwork-only
   pass belongs with ধাপ ৬.

### Commit / push

`phase-5: size the metadata backfill from what is actually available`
→ `2a5728a6d0efc9bf020b69212b2d83684885509f` (pushed as `94031d5`), verified.

### Next task

Phase 6 — poster policy enforcement.

---

## PHASE 6 — Poster policy enforcement (D-03)

**Plan reference:** ধারা ৭ ধাপ ৬, ধারা ৩ D-03, ধারা ৮'s replacement metric.
Depends on ধাপ ৪ (Artwork Router), which is done.

### Before state, measured

`poster_validity.py` has classified artwork as ok / dead / unknown for a while,
and the plan says so: the gap was never the classification, it was
"চূড়ান্ত poster নির্বাচনে **প্রয়োগ**". Measured on the real catalogue:

```
state/movie-poster-validity.json        208 verdicts, every one `ok`
  ... of which srhady-live-stream.hf.space       0
published posters on that host          1,197 of 1,667
published posters with any verdict        131 of 1,667
```

So the 1,197 were **never probed**. They held first place because the resolver
returned the feed's `logo` the moment its verdict was "not DEAD" — and "not
DEAD" includes "never probed", so nothing could ever displace them. That is the
whole of D-03 in one line of control flow.

### Files changed

New: `tests/test_movie_poster_policy.py` (17 tests).

Changed: `scanner/movies.py` (`_resolve_published_poster` ranks instead of
first-wins; `_verified_artwork_coverage`), `scanner/poster_providers.py` (the
fixed chain becomes an Artwork Router), `scanner/provider_router.py` (OMDb
declared artwork-capable).

### Implementation

**Rank, do not take the first thing that is not disqualified.** Candidates are
gathered in the plan's priority order — cached verified artwork → Artwork
Router → the source's own logo — and the list is walked twice: once accepting
only a **verified** (probed `ok`) candidate, then once accepting anything not
proven dead. A poster that is proven to load now beats one that merely arrived
first.

**The Artwork Router replaces the fixed chain.** `supplementary_poster_lookup`
used to hard-code Fanart → Cinemeta → OMDb → TVMaze → AniList. The order now
comes from `provider_router`, so a provider that is cooling down, tripped or
out of budget is skipped rather than walked into, and artwork lookups are
counted against the same quota and breaker as metadata lookups to the same API
key — two ledgers for one key is how a budget gets spent twice.

**Routing decides the order, never the membership.** The router's `kinds`
filter expresses where a provider is *likely* to help (AniList for anime,
TVMaze for television). Letting it remove a provider would mean a long shot
that used to be taken silently stops being taken — a regression dressed up as
routing. Anything the router does not rank is appended in the original order,
minus whatever is currently unavailable.

**Id-keyed providers always precede title-keyed ones**, whatever the weights
say. Fanart asked for `tmdb_id 27205` returns art for that exact film; OMDb
asked for "Inception" returns art for whatever it thinks that title means, and
a title match against an anime-only catalogue can land on an unrelated
same-named title. That was an existing, documented rule and a test caught the
first draft breaking it.

### The one place this deviates from a literal reading of ধাপ ৬

ধাপ ৬ lists "source tvg-logo (শুধু verified) → placeholder", and §8 says
"restricted/unknown পোস্টার চূড়ান্ত poster হিসেবে প্রকাশ নয়". Taken literally,
1,197 posters would become placeholders immediately.

They are not retired, and the reason is already in this codebase as a
**measurement**, not an opinion — `poster_validity.py`:

> the same URL answered 403 from a Bangladesh egress and 200 with real JPEG
> bytes from a GitHub runner on the same day … Retiring artwork on that
> evidence would blank a poster for every viewer who can see it, on the word of
> one network that cannot. Viewers behind the blocked vantage are not left with
> a broken image: the page swaps in the designed placeholder on the img error
> event, which is where a per-viewer failure belongs.

And those 1,197 are not even *restricted* — they are **unprobed**. ধারা ৮'s own
replacement metric is "যাচাইকৃত আর্টওয়ার্ক কভারেজ > ৯৫% — ছবিটি সত্যিই লোড হয়
কি না", which is raised by finding a verified alternative, not by deleting an
unverified one. So: a verified candidate always wins; a *proven dead* one is
never published; an unprobed one is kept as the last resort before a
placeholder, and counted separately so the coverage target is measurable.

This is the plan's own PLAN DEVIATION RULE applied — the existing
implementation encodes a measured fact the D-03 wording does not account for,
so it is reused and the difference is recorded rather than silently overridden.

### Tests run

```
tests/test_movie_poster_policy.py          17 tests   PASS
tests/test_movie_title_and_poster_health.py           PASS  (unchanged)
tests/test_poster_providers.py                        PASS  (unchanged)
```

Three faults the existing suite caught in the first draft, each a real rule:
the counters required callers to pre-seed keys; OMDb was not declared
artwork-capable although `omdb_poster_lookup` has always existed; and the
id-keyed-before-title-keyed rule was broken by the router's weights.

### Requirement status

| Plan requirement | Status |
|---|---|
| ধাপ ৬ priority: cached verified → Artwork Router → source logo | **IMPLEMENTED** |
| ধাপ ৬ Artwork Router by quota/health/capability, no fixed order | **IMPLEMENTED** |
| §3 D-03 ok/dead/restricted classification | **ALREADY SATISFIED** (`poster_validity.py`) |
| §3 D-03 applying it in final poster selection | **IMPLEMENTED** |
| §8 verified artwork coverage, provider-agnostic | **IMPLEMENTED** (`verified_artwork_coverage` in the report) |
| §8 "restricted/unknown পোস্টার চূড়ান্ত poster নয়" | **PARTIALLY — deviation recorded above**: proven-dead never published; unprobed kept as last resort, counted, and displaced by any verified alternative |
| Master-safety 3 "Poster না থাকলে Movie drop নয়" | **ALREADY SATISFIED** |

### Known limitations

1. Verified coverage will not jump on the first scan. A candidate can only be
   *verified* if it is probed, and `poster_validity` has a per-run time budget.
   The number is now measured every run, so the trend is visible instead of
   assumed.
2. The 1,197 unprobed hf.space posters stay until the router finds verified
   replacements, which needs the metadata identities ধাপ ৫ fills in.
3. `manual(verified)` is not a separate tier: manual posters already enter
   through `state/manual-movie-posters.json` into the same cache tier, which
   outranks the router and the source logo. Adding a fourth tier would be two
   places holding one truth.

### Commit / push

`phase-6: rank posters by verification instead of by arrival`
→ `496105721f04fb200aa29f3f133cf79d00330028` (pushed as `24e14e8`), verified.

### Next task

Phase 7 — link health TTL + staggered sweep.

---

## PHASE 7 — Link health TTL + staggered sweep (S-01)

**Plan reference:** ধারা ৪.২ · ৪.৩ · ৪.৪, ধারা ৭ ধাপ ৭. The plan calls it
"একক বৃহত্তম গতি-লাভ", and **v3.5's ধাপ ১০খ is gated on it**.

### Before state

`grep` for `next_verify_at`, `last_verified_at` and `movie-link-health`
returned exactly one hit across the whole repository — a comment in the Phase 1
module describing the file that did not exist yet. `skip_verification` existed
but only as a publish-side marker on manual and series items, never as a
pipeline gate.

So the measurement in the plan stands: 2,475 links re-checked every night in a
40-minute budget, the budget runs out, and 390 of them (23%) sit in
`stale_last_good` having never been reached. The scanner is not short of
workers — it spends its whole budget redoing yesterday's work.

### Files changed

New: `scanner/movie_link_health.py`, `tests/test_movie_link_health.py`
(49 tests).

Changed: `scanner/fast_pipeline.py` (`_partition_by_link_health` in front of
the run, `_record_link_health` after it), `config/settings.json`.

### Implementation

**One question in front of the existing pipeline** (ধারা ৪.৩), and everything
below it is unchanged — what changes is how many items reach it:

```
NEW / CHANGED / DUE / REPAIR / playback failure → verify, exactly as today
FRESH HEALTHY                                   → reuse state, zero network
```

The skip condition is all three of the plan's clauses together: `status ==
healthy` **and** the source revision is unchanged **and** `now <
next_verify_at`.

**The TTL ladder** (ধারা ৪.৪), verified by test against the plan's own numbers:

| | |
|---|---|
| new / changed / playback failure | immediately |
| recently failed | 1h → 6h → 24h → 3d |
| unstable public host | 36h (inside the plan's 24–48h) |
| healthy public link | 5d (inside the plan's 3–7d) |
| trusted private link | 7d |
| tokenized URL | its own expiry, less a 2h margin |

**The staggered sweep** — `hash(content_id) % 7`. Measured across 2,475 links
the buckets come out 320–375 a night against a perfect 353, which is the
plan's own ~354 estimate. Without it a link that stays healthy is never looked
at again, which is the mistake ধারা ৪.৪ names outright: "সুস্থ লিংক আর কখনো
দেখব না — এই ভুলটি এড়ানো যায়".

**Stale reasons, because "stale 0%" was never the claim.** ধারা ৪.৪ corrects it
explicitly: geo restriction, a host having a bad day and an expired token all
produce stale entries and all of them are *correct*. Every stale entry carries
one of `geo` / `host_down` / `token_expired` / `budget`, and only `budget` —
stale because the run ran out of time — is the one being driven to zero. It is
**recorded, not inferred**: a link the run never reached is a different fact
from one that answered 403, and the whole metric depends on telling them apart.

### Three safety properties, each with a test

1. **The first run changes nothing.** A link with no record is always due, so
   the first scan after this ships verifies exactly what it verifies today and
   the ledger seeds itself from real observations. There is no moment where
   this decides a link is healthy without having watched it be healthy.
2. **A carried item is still published.** It leaves the *probe* queue, never
   the run — it rejoins `final_results` before anything is written. Dropping it
   would make the Phase 1 gate rightly BLOCK the publish, which is the silent
   loss ধারা ৪.০ exists to prevent.
3. **Failure degrades to today.** If anything in the gate raises, the whole
   partition is abandoned and every item is verified.

### A fault found while testing

`verification_decision` required **both** revisions to be non-empty before
calling a link changed, so a record made before revisions were tracked could
never be marked changed. "We do not know what revision this came from" is not
the same claim as "it has not changed". Fixed: a supplied revision that differs
from the recorded one is a change, empty included. Costs one extra verification
per link, once, and then the revision is on file.

### Requirement status

| Plan requirement | Status |
|---|---|
| §4.2 `state/movie-link-health.json` with every named field | **IMPLEMENTED** |
| §4.2 `next_verify_at` | **IMPLEMENTED** |
| §4.2 `source_revision` cancels the TTL | **IMPLEMENTED** |
| §4.2 status incl. `confirmed_unavailable` | **IMPLEMENTED** |
| §4.3 the skip gate in front of fast_pipeline | **IMPLEMENTED** |
| §4.4 the full TTL ladder | **IMPLEMENTED** |
| §4.4 staggered sweep `hash % 7` | **IMPLEMENTED** |
| §4.4 a reason on every stale entry | **IMPLEMENTED** |
| §4.5 `preferred_primary` vs `active_primary` | **IMPLEMENTED** |
| §4.0 repeated vantage failure is never terminal | **IMPLEMENTED** (404/410 only) |
| ধাপ ৭ complete and stable → unblocks v3.5 ধাপ ১০খ | **code complete; stability needs real runs** |

### Known limitations

1. **The win is not yet measured on a real run.** The first scan verifies
   everything and seeds the ledger; the reduction appears from the second run
   onwards. The plan's estimate is 2,475 → ~354 checks a night.
2. `UNSTABLE_HOST_MARKERS` is a short list of observed facts (`workers.dev`,
   `hf.space`). Everything unlisted is treated as stable, which errs towards
   checking *less* often; the failure ladder catches a host that turns out
   worse than assumed.
3. The repair queue is ধাপ ৮. `verification_decision` already accepts
   `repair_queued` and gives it priority, so wiring it is a parameter, not a
   redesign.
4. `playback_failure_reported` is read from the candidate but nothing sets it
   yet — `playback-feedback.yml` exists and connecting it belongs with ধাপ ৮.

### Commit / push

`phase-7: check a link when it is due, not because it exists`
→ `f0a21156b53b81103dfe21f3f523833fec91ab80` (pushed as `522febf`), verified.

### Next task

Phase 8 — repair queue and its workflow.

---

## PHASE 8 — Repair queue + `movie-repair.yml` (S-02)

**Plan reference:** ধারা ৪.২ (the second new state file), ধারা ৪.৫, ধারা ৪.৮,
ধারা ৭ ধাপ ৮.

### Before state

`grep` for `repair_queue` / `movie-repair-queue` returned nothing, which is
S-02 exactly: a single dead link could only be fixed by running the whole
catalogue, and that run's budget is then divided across 2,475 links — so repair
was effectively a matter of luck.

### Files changed

New: `scanner/movie_repair_queue.py`, `scripts/movie-repair-run.py`,
`.github/workflows/movie-repair.yml`, `tests/test_movie_repair_queue.py`
(33 tests).

Changed: `scanner/fast_pipeline.py` (`_refresh_repair_queue` after the ledger
is written).

### Implementation

**Severity is about the viewer, not about us** (ধারা ৪.৫). P0 is "nobody can
watch this", which is why it outranks a dead primary with a working backup —
the second is a quality problem, the first is an outage. The queue is ordered
severity first and age only *within* a severity; ordered by age alone it would
spend its budget on P3s while a P0 waited.

**The queue is derived, not accumulated.** `refresh_from_catalogue` re-reads the
published cards and the Phase 7 ledger every scan, so a card that has quietly
healed leaves the queue without anybody remembering to remove it. A repair queue
that only grows is one nobody trusts.

**An unchecked link is not a fault.** Every link starts `unknown`; treating that
as broken would have put the entire catalogue in the repair queue on day one.

**Backoff that recognises an outage.** 1h → 4h → 12h → 24h → 48h, except a P0's
first retry, which waits 15 minutes. Re-queuing does **not** reset the attempt
count — a link that has failed five repairs has failed five repairs, and
resetting every scan would turn the backoff into a fixed interval. Past 12
attempts an entry stops being offered but is **kept**: it is the only record
that this content ever had a working link.

**Being queued is not a disposition.** ধারা ৪.০'s two axes: a card stays
`published_movie` while its link sits here as `confirmed_unavailable`. A test
asserts the card dict is byte-identical before and after a refresh.

**One writer.** `movie-repair.yml` runs every five hours in its own concurrency
group — it must not queue behind a forty-minute catalogue scan, since running
*between* scans is the point of it existing — and it **fails the job** if
`data/` changed. It commits only `state/movie-repair-queue.json` and
`state/movie-link-health.json`. The repair run promotes a live backup to
`active_primary` and records attempts; the next scan publishes. This is ধারা
৪.৯ rule 2 and the existing atomic-publish rule, which agree.

`preferred_primary` is never touched by a repair: it is the owner's policy and
does not change because a check failed. `active_primary` is this moment's
reality, and the two being separate fields is what lets a private link become
primary again by itself when it comes back (ধারা ৪.৫).

### Data validation — against the real catalogue

60 published cards with a synthetic ledger failing every tenth primary:

```
classified   P0 5 · P1 1
summary      queued 6, due now 6, no_live_link 5, exhausted 0
worst first  P0 ponman-2025-dual-audio, P0 remote-manual-bhaggyo-lokkhi-2025,
             P0 remote-manual-lucky-2026, ... then P1
```

The one card with a live backup was correctly classified P1 and sorted below
every P0.

### Tests run

`tests/test_movie_repair_queue.py` — **33 tests, 33 pass**, including the
ordering rule, the full backoff ladder, that re-queuing does not reset it, that
an exhausted entry is kept but not offered, that a healed card leaves by itself,
that an unchecked link is not queued, that the card is unchanged, and six
contract tests on the workflow itself.

### Requirement status

| Plan requirement | Status |
|---|---|
| §4.2 `state/movie-repair-queue.json` with every named field | **IMPLEMENTED** |
| §4.5 P0–P3 severities | **IMPLEMENTED** |
| §4.5 step 2 — promote a live backup to `active_primary` | **IMPLEMENTED** |
| §4.5 `preferred_primary` untouched by a repair | **IMPLEMENTED** |
| §4.5 success leaves the queue, failure backs off | **IMPLEMENTED** |
| §4.8 `movie-repair.yml` every 4–6h, reads only the queue | **IMPLEMENTED** |
| §4.8 "worker job নিজে push করবে না; একক publisher" | **IMPLEMENTED** (job fails if `data/` changed) |
| §4.5 steps 3–5 — find and verify new candidate URLs | **PARTIALLY** — see limitations |

### Known limitations

1. **Steps 3–5 of ধারা ৪.৫ are not implemented**: searching the already-fetched
   source snapshot for new candidates by canonical identity, verifying only
   those URLs, and adding up to five live backups. The queue, the severities,
   the promotion and the backoff are in place; candidate discovery needs the
   repair run to load a source snapshot, which is a network step inside a
   workflow that currently makes none. Recorded here rather than claimed.
2. The playback-failure trigger is not wired. Phase 7's
   `verification_decision` already accepts `playback_failed` and gives it top
   priority, and `playback-feedback.yml` already collects the telemetry —
   joining them is a small follow-up, not a redesign.
3. P3 (restricted / uncertain) is defined and classifiable but nothing
   currently sets `restricted=True`; it needs the per-route evidence model to
   say "restricted from our vantage", which `route_evidence` holds.

### Next task

Phase 9 — generation id + atomic publish (S-07), so the catalogue and the
search index can never describe different generations of the same scan.
