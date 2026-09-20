"""ধাপ ০ - what the Movie catalogue looked like before any of this started.

The master plan's first step is one sentence: "ফেরার পথ ছাড়া কিছু শুরু নয়" -
nothing begins without a way back. Every step after it (title cleaning, series
merge, backup merge, poster policy) rewrites cards, and each one is a chance to
lose a stream that nobody notices for a week.

So this module answers two questions, and only those two:

    1. What is live right now?      -> a baseline record, small enough to commit
    2. Can we get exactly that back? -> a restore that verifies, not one that hopes

What a backup is here
---------------------
The durable store is the repository itself: every scan commits `data/movies`,
`data/series` and `data/manifest.json`, and those commits are pushed to GitHub.
Copying 5.5MB of JSON into the same repository would not add a second copy, it
would add a second *claim* about the same bytes - and a claim that drifts is
worse than no claim.

The baseline therefore records the commit those files came from plus a sha256
of every one of them, and the restore reads the files back out of that commit
and checks each digest before anything is written. A restore that cannot prove
it produced the recorded bytes refuses to run. That is the difference between a
rollback and a hope.

`--copy-to` still makes a physical copy for an operator who wants one on disk;
it is deliberately not the default and deliberately not inside the repository.

Why the stream inventory is in here
-----------------------------------
INVARIANT ২ (ধারা ৪.০) asks whether something that was reaching viewers still
reaches them. That question needs an "before" to compare against, and the only
honest source for it is the published catalogue on disk - not the last scan's
output. The 2026-09-17 movies run finished with `final_publishable: 0` and
`movie_output_preserved: true`: the guard correctly kept the previous pages, so
the run's own output describes nothing that is live. Reading `data/movies`
instead gives the 1,667 cards and 2,159 links a viewer can actually open.

Stdlib only, like the rest of the scanner.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

SCHEMA_VERSION = 1

#: Where the record lives. Small (a few hundred KB of digests), committed, and
#: read by the restore tool and by Phase 1's coverage gate alike.
DEFAULT_BASELINE_PATH = Path("state") / "movie-baseline.json"

#: The published surfaces ধাপ ০ is responsible for. Live TV, Sports, Today and
#: Upcoming are deliberately absent: this plan does not touch them, so putting
#: them under a movie rollback would invite a restore that reverts an unrelated
#: pipeline by accident.
CATALOGUE_DIRECTORIES: Tuple[str, ...] = (
    "data/movies",
    "data/series",
)

CATALOGUE_FILES: Tuple[str, ...] = ()

#: Files every pipeline writes. Digested so the record is complete, and
#: deliberately NOT restorable as part of a movie rollback.
#:
#: `data/manifest.json` carries the Today Match and Upcoming counts as well as
#: the movie ones, and the event scans rewrite it every few minutes. Restoring
#: it from a movie baseline would roll those counts back to whenever the
#: baseline was taken - the cross-pipeline damage this module's own scope note
#: says it avoids. It was in `CATALOGUE_FILES` until a drift check reported it
#: as changed within hours of Phase 0, which is how the hazard surfaced: a
#: check that goes red on every unrelated scan is one nobody reads, and behind
#: it was a restore that would have reverted another pipeline's work.
SHARED_FILES: Tuple[str, ...] = (
    "data/manifest.json",
)

#: State the Movie pipeline cannot be rebuilt without. Digested, never copied:
#: `route-evidence-cache.json` alone is 25MB and `stream-history.json` 31MB, and
#: neither is a movie file - they are listed here only where a movie step will
#: write to them, which is why the list is short.
STATE_FILES: Tuple[str, ...] = (
    "state/movie-metadata-cache.json",
    "state/movie-first-seen.json",
    "state/movie-identity-aliases.json",
    "state/movie-poster-validity.json",
    "state/movie-retention.json",
    "state/movie-trending-cache.json",
    "state/movie-featured-history.json",
    "state/manual-movie-posters.json",
    "state/manual-movie-remote-cache.json",
    "state/provider-health.json",
    "state/source-health.json",
)

#: The seven category slugs `scanner/movies.py` publishes. `discovery` and
#: `genres` are derived surfaces and are captured by the directory walk, but
#: they are not counted as catalogue totals - counting them is how a naive
#: total reads 2,179 for a 1,667-film catalogue.
MOVIE_CATEGORY_SLUGS: Tuple[str, ...] = (
    "bangla", "dubbed", "english", "hindi", "mix", "premium", "south-indian",
)


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _relative(path: Path, root: Path) -> str:
    """Repository-relative, forward slashes, on every platform.

    The manifest is compared against `git` output and read on Linux runners
    after being written on Windows, so a backslash in a recorded path would be
    a digest that never matches anything.
    """
    return PurePosixPath(path.resolve().relative_to(root.resolve()).as_posix()).as_posix()


def normalize_newlines(payload: bytes) -> bytes:
    """CRLF -> LF, so a digest means the same thing on Windows and on Linux.

    This is not cosmetic. `core.autocrlf` is true on the owner's Windows
    machine, so `data/movies/bangla/page-001.json` is 76,413 bytes on disk with
    1,795 CRLF pairs and 74,618 bytes in the git object with none. A digest
    taken from disk therefore never matches the blob the restore reads back,
    and the rollback refuses itself on the platform it is most likely to be run
    from. Measured, not assumed - it is what made the first run of the rollback
    proof fail.

    Every file this module digests is JSON written by the scanner, so there is
    no binary content whose meaning a CRLF collapse could change.
    """
    return payload.replace(b"\r\n", b"\n")


def digest_bytes(payload: bytes) -> str:
    """sha256 of the newline-normalised content."""
    return hashlib.sha256(normalize_newlines(payload)).hexdigest()


def digest_file(path: Path) -> str:
    return digest_bytes(path.read_bytes())


def normalized_size(path: Path) -> int:
    """Byte length after newline normalisation - the size git records."""
    return len(normalize_newlines(path.read_bytes()))


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=str(path.parent), delete=False,
        prefix=f".{path.name}.", suffix=".tmp",
    )
    try:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    finally:
        handle.close()
    os.replace(handle.name, path)


def _load_json(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as stream:
        return json.load(stream)


# ---------------------------------------------------------------------------
# git
# ---------------------------------------------------------------------------

def _git(root: Path, *args: str) -> Tuple[int, str]:
    """Run git and return (returncode, stdout). Never raises.

    A machine without git, or a directory that is not a checkout, has to be a
    baseline without a commit rather than a crash - the counts and digests are
    still worth recording, and the restore tool is the only part that needs the
    commit to exist.
    """
    try:
        completed = subprocess.run(
            ("git", "-C", str(root), *args),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
    except (OSError, ValueError):
        return 1, ""
    return completed.returncode, (completed.stdout or "").strip()


def git_state(root: Path) -> Dict[str, Any]:
    code, commit = _git(root, "rev-parse", "HEAD")
    if code != 0 or not commit:
        return {"available": False, "commit": "", "branch": "", "dirty": None}
    _, branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    status_code, status = _git(root, "status", "--porcelain")
    return {
        "available": True,
        "commit": commit,
        "branch": branch,
        # `None` rather than False when the status call itself failed: "we did
        # not check" and "we checked and it was clean" are different facts.
        "dirty": bool(status) if status_code == 0 else None,
    }


def read_from_commit(root: Path, commit: str, relative_path: str) -> Optional[bytes]:
    """The bytes of one file as that commit holds them, or None."""
    try:
        completed = subprocess.run(
            ("git", "-C", str(root), "show", f"{commit}:{relative_path}"),
            capture_output=True,
        )
    except (OSError, ValueError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout


# ---------------------------------------------------------------------------
# what is published right now
# ---------------------------------------------------------------------------

def _catalogue_file_paths(root: Path) -> List[Path]:
    found: List[Path] = []
    for directory in CATALOGUE_DIRECTORIES:
        base = root / directory
        if not base.is_dir():
            continue
        found.extend(
            path for path in base.rglob("*.json") if path.is_file()
        )
    for relative in CATALOGUE_FILES:
        path = root / relative
        if path.is_file():
            found.append(path)
    return sorted(set(found), key=lambda path: _relative(path, root))


def _page_items(payload: Any) -> List[Dict[str, Any]]:
    """The cards a published page holds, under either spelling.

    `scanner/output.py` sanitises `items` and `movies` interchangeably and both
    appear on disk, so reading one of them is how a count comes out short.
    """
    if not isinstance(payload, dict):
        return []
    for key in ("items", "movies", "episodes"):
        value = payload.get(key)
        if isinstance(value, list):
            return [entry for entry in value if isinstance(entry, dict)]
    return []


def _movie_stream_urls(item: Dict[str, Any]) -> List[str]:
    """Primary plus backups, in published order, blanks removed."""
    urls: List[str] = []
    primary = str(item.get("url") or "").strip()
    if primary:
        urls.append(primary)
    for backup in item.get("backups") or ():
        if isinstance(backup, dict):
            candidate = str(backup.get("url") or "").strip()
        else:
            candidate = str(backup or "").strip()
        if candidate:
            urls.append(candidate)
    return urls


def published_movies(root: str | Path) -> List[Tuple[str, Dict[str, Any]]]:
    """Every published movie card as (category_slug, card).

    Only the seven catalogue categories. `data/movies/discovery` and
    `data/movies/genres` re-list films that are already counted, and a total
    that includes them says 2,179 where the catalogue holds 1,667.
    """
    collected: List[Tuple[str, Dict[str, Any]]] = []
    root = Path(root)
    for slug in MOVIE_CATEGORY_SLUGS:
        directory = root / "data" / "movies" / slug
        if not directory.is_dir():
            continue
        for page in sorted(directory.glob("page-*.json")):
            try:
                payload = _load_json(page)
            except (OSError, ValueError):
                continue
            for item in _page_items(payload):
                collected.append((slug, item))
    return collected


def published_episodes(root: str | Path) -> List[Tuple[str, Dict[str, Any]]]:
    """Every published series episode as (series_relative_path, episode)."""
    collected: List[Tuple[str, Dict[str, Any]]] = []
    root = Path(root)
    base = root / "data" / "series"
    if not base.is_dir():
        return collected
    for path in sorted(base.rglob("*.json")):
        if path.name in {"manifest.json", "index.json"}:
            continue
        try:
            payload = _load_json(path)
        except (OSError, ValueError):
            continue
        for item in _page_items(payload):
            collected.append((_relative(path, root), item))
    return collected


def stream_inventory(root: str | Path) -> List[str]:
    """Sorted `kind|identity|url` lines - one per playable link on the site.

    This is the "before" side of INVARIANT ২. It is a flat sorted list rather
    than a nested structure because the only operations Phase 1 needs are
    membership and set difference, and a sorted list of strings is the cheapest
    thing that both a digest and a diff can be taken of.
    """
    root = Path(root)
    return inventory_lines(published_movies(root), published_episodes(root))


def inventory_lines(
    movies: Iterable[Tuple[str, Dict[str, Any]]],
    episodes: Iterable[Tuple[str, Dict[str, Any]]] = (),
) -> List[str]:
    """The same lines, from cards held in memory rather than read from disk.

    The format lives here once because both sides of INVARIANT ২ have to be
    written by the same rule: the "before" comes off disk, the "after" comes
    out of the payload a scan is about to publish, and two spellings of the
    same line would make every stream look moved.
    """
    lines: List[str] = []
    for slug, item in movies or ():
        if not isinstance(item, dict):
            continue
        identity = str(item.get("id") or item.get("name") or "").strip()
        for url in _movie_stream_urls(item):
            lines.append(f"movie|{slug}|{identity}|{url}")
    for source_path, episode in episodes or ():
        if not isinstance(episode, dict):
            continue
        identity = str(episode.get("id") or episode.get("episode_key") or "").strip()
        for url in _movie_stream_urls(episode):
            lines.append(f"episode|{source_path}|{identity}|{url}")
    return sorted(set(lines))


def episodes_from_prepared_series(
    prepared: Optional[Dict[str, Any]],
) -> List[Tuple[str, Dict[str, Any]]]:
    """`(scope, episode)` for the series a scan is about to publish.

    `scanner/series.publish_prepared_series` runs *after* the movie publish, so
    at gate time the episodes on disk are the previous run's. Using those would
    be accurate today and wrong exactly when it matters most: the moment ধাপ ৩
    starts moving movie cards into series, an episode that had just arrived
    would read as a lost stream.

    The staging shape is walked defensively - seasons, then episodes, then
    either a `url` or a `links` list - because `prepare_manual_series` hands
    back the staging records rather than the normalised ones, and a shape
    change here must degrade to "found nothing" rather than to a crash in a
    publish path.
    """
    collected: List[Tuple[str, Dict[str, Any]]] = []
    items = (prepared or {}).get("items")
    if not isinstance(items, list):
        return collected
    for series in items:
        if not isinstance(series, dict):
            continue
        series_id = str(
            series.get("id") or series.get("name") or ""
        ).strip()
        scope = "prepared:%s" % (series_id or "unknown")
        for season in series.get("seasons") or ():
            if not isinstance(season, dict):
                continue
            for episode in season.get("episodes") or ():
                if not isinstance(episode, dict):
                    continue
                record = dict(episode)
                record.setdefault(
                    "id",
                    "%s-s%02d-%s" % (
                        series_id,
                        _int_or(season.get("number"), 0),
                        str(record.get("episode_key")
                            or record.get("episode_label") or "").strip(),
                    ),
                )
                links = record.get("links")
                if isinstance(links, list) and links:
                    primary = links[0] if isinstance(links[0], dict) else {}
                    record.setdefault("url", primary.get("url") or "")
                    record["backups"] = [
                        link for link in links[1:] if isinstance(link, dict)
                    ]
                collected.append((scope, record))
    return collected


def _int_or(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def cards_from_paginated(
    movies_data: Optional[Dict[str, Any]],
) -> List[Tuple[str, Dict[str, Any]]]:
    """`(category_slug, card)` for everything a scan is about to publish.

    `scanner/movies.process_movies` returns the paginated payload and
    `scanner/output.py` writes it; between those two points is the only moment
    where "what is about to be live" exists as data, which is exactly where the
    no-loss gate has to ask its question.
    """
    collected: List[Tuple[str, Dict[str, Any]]] = []
    for category, payload in (movies_data or {}).items():
        if not isinstance(payload, dict):
            continue
        pages = payload.get("page_contents")
        if not isinstance(pages, dict):
            continue
        index = payload.get("index")
        slug = str(
            (index or {}).get("slug") if isinstance(index, dict) else ""
        ) or str(category)
        for _, page in sorted(pages.items()):
            for item in _page_items(page):
                collected.append((slug, item))
    return collected


def catalogue_counts(root: str | Path) -> Dict[str, Any]:
    root = Path(root)
    movies = published_movies(root)
    episodes = published_episodes(root)

    per_category: Dict[str, int] = {slug: 0 for slug in MOVIE_CATEGORY_SLUGS}
    movie_primary = 0
    movie_backups = 0
    for slug, item in movies:
        per_category[slug] = per_category.get(slug, 0) + 1
        urls = _movie_stream_urls(item)
        if urls:
            movie_primary += 1
            movie_backups += len(urls) - 1

    series_total = 0
    manifest_path = root / "data" / "series" / "manifest.json"
    if manifest_path.is_file():
        try:
            payload = _load_json(manifest_path)
            series_total = int((payload or {}).get("total_series") or 0)
        except (OSError, ValueError, TypeError):
            series_total = 0

    episode_links = sum(len(_movie_stream_urls(episode)) for _, episode in episodes)

    return {
        "movies_total": len(movies),
        "movies_per_category": per_category,
        "movies_with_stream": movie_primary,
        "movie_backup_links": movie_backups,
        "movie_links_total": movie_primary + movie_backups,
        "series_total": series_total,
        "episodes_total": len(episodes),
        "episode_links_total": episode_links,
    }


def verification_counts(root: str | Path) -> Dict[str, int]:
    """How the published catalogue says each link was last established.

    Recorded because it is the number ধারা ৮ measures the whole plan against -
    927 verified_global / 390 stale_last_good / 350 manual_trusted on the day
    the baseline was taken - and a "before" that is not written down is an
    argument later, not a measurement.
    """
    counts: Dict[str, int] = {}
    for _, item in published_movies(Path(root)):
        status = str(item.get("verification_status") or "unknown").strip() or "unknown"
        counts[status] = counts.get(status, 0) + 1
    return dict(sorted(counts.items()))


# ---------------------------------------------------------------------------
# build / verify / restore
# ---------------------------------------------------------------------------

def build_baseline(
    root: str | Path = ".",
    *,
    label: str = "",
    now: Optional[str] = None,
) -> Dict[str, Any]:
    """The record of what is live, ready to be written and committed."""
    base = Path(root).resolve()
    git = git_state(base)
    commit = str(git.get("commit") or "")

    def measure(relative: str) -> Optional[Dict[str, Any]]:
        """Digest one file from the commit if there is one, else from disk.

        The commit is preferred because the restore reads the commit, so
        recording anything else would let the record and the rollback disagree
        - and they did: running this repository's test suite rewrites seven of
        the eleven movie state files in place, so a disk-read baseline taken
        after a local test run would enshrine those scribbles as "what was
        live". Reading the commit makes the two agree by construction.
        """
        if commit:
            payload = read_from_commit(base, commit, relative)
            if payload is not None:
                return {
                    "path": relative,
                    "sha256": digest_bytes(payload),
                    "bytes": len(normalize_newlines(payload)),
                }
            return None
        path = base / relative
        if not path.is_file():
            return None
        return {
            "path": relative,
            "sha256": digest_file(path),
            # Normalised length, not the on-disk length, so the record reads
            # the same whether it was taken on Windows or on a runner.
            "bytes": normalized_size(path),
        }

    catalogue: List[Dict[str, Any]] = []
    for path in _catalogue_file_paths(base):
        relative = _relative(path, base)
        measured = measure(relative)
        if measured is None:
            # On disk but not in the commit: a file this run produced and has
            # not committed. Recorded from disk and flagged, because a restore
            # cannot bring it back and the record must not pretend otherwise.
            catalogue.append({
                "path": relative,
                "sha256": digest_file(path),
                "bytes": normalized_size(path),
                "in_commit": False,
            })
            continue
        catalogue.append(measured)

    shared: List[Dict[str, Any]] = []
    for relative in SHARED_FILES:
        measured = measure(relative)
        if measured is None:
            shared.append({"path": relative, "sha256": "", "bytes": 0,
                           "present": False})
            continue
        shared.append({**measured, "present": True})

    state: List[Dict[str, Any]] = []
    for relative in STATE_FILES:
        measured = measure(relative)
        if measured is None:
            state.append({"path": relative, "sha256": "", "bytes": 0,
                          "present": False})
            continue
        state.append({**measured, "present": True})

    inventory = stream_inventory(base)

    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "movie_baseline",
        "label": label or "phase-0-baseline",
        "created_at": now or _utc_now(),
        "note": (
            "ধাপ ০ - the Movie catalogue and its state as they stood before the "
            "v3.4 master plan was implemented. Restore reads the recorded "
            "commit and refuses unless every sha256 below matches."
        ),
        "git": git,
        "digest_source": "commit" if commit else "worktree",
        "counts": catalogue_counts(base),
        "verification_status_counts": verification_counts(base),
        "stream_inventory": {
            "lines": len(inventory),
            "sha256": digest_bytes("\n".join(inventory).encode("utf-8")),
            # The lines themselves, not only their digest. INVARIANT ২ (ধারা
            # ৪.০) needs the "was reaching viewers" side as data it can take a
            # set difference against, and a digest can only answer "did
            # anything change". ~300KB for 2,475 links, which is the cheapest
            # honest way to make the baseline self-contained.
            "entries": inventory,
        },
        "catalogue_files": catalogue,
        # Recorded, never restored. See SHARED_FILES.
        "shared_files": shared,
        "state_files": state,
    }


def write_baseline(
    payload: Dict[str, Any],
    path: str | Path = DEFAULT_BASELINE_PATH,
    *,
    root: str | Path = ".",
) -> Path:
    target = Path(path)
    if not target.is_absolute():
        target = Path(root) / target
    _atomic_write_json(target, payload)
    return target


def load_baseline(
    path: str | Path = DEFAULT_BASELINE_PATH,
    *,
    root: str | Path = ".",
) -> Dict[str, Any]:
    target = Path(path)
    if not target.is_absolute():
        target = Path(root) / target
    payload = _load_json(target)
    if not isinstance(payload, dict) or payload.get("kind") != "movie_baseline":
        raise ValueError(f"Not a movie baseline record: {target}")
    return payload


def recorded_inventory(baseline: Dict[str, Any]) -> List[str]:
    """The stream inventory a baseline recorded, verified against its digest.

    Verified rather than trusted: this list is the "before" side of INVARIANT ২,
    and a publish is blocked on the comparison, so a list that has been edited
    by hand must not quietly change the answer.
    """
    payload = baseline.get("stream_inventory")
    if not isinstance(payload, dict):
        return []
    entries = [str(line) for line in (payload.get("entries") or ())]
    expected = str(payload.get("sha256") or "")
    if expected and entries:
        actual = digest_bytes("\n".join(entries).encode("utf-8"))
        if actual != expected:
            raise ValueError(
                "state/movie-baseline.json stream inventory does not match its "
                "own digest; re-run scripts/movie-baseline-backup.py"
            )
    return entries


def verify_worktree(
    baseline: Dict[str, Any],
    root: str | Path = ".",
    *,
    include_state: bool = False,
) -> List[Dict[str, Any]]:
    """Every way the working tree differs from the baseline, named.

    Returns a list of findings rather than a bool, because "which file" is the
    only part of the answer that is actionable. An empty list means the tree is
    byte for byte what was recorded.

    State is off by default: `movie-metadata-cache.json` and friends move on
    every scan by design, so comparing them would report drift as damage.
    """
    base = Path(root).resolve()
    findings: List[Dict[str, Any]] = []

    groups: List[Tuple[str, Sequence[Dict[str, Any]]]] = [
        ("catalogue", baseline.get("catalogue_files") or ()),
    ]
    if include_state:
        groups.append(("state", baseline.get("state_files") or ()))

    for group, entries in groups:
        recorded_paths = set()
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            relative = str(entry.get("path") or "")
            if not relative:
                continue
            recorded_paths.add(relative)
            if group == "state" and entry.get("present") is False:
                continue
            path = base / relative
            if not path.is_file():
                findings.append({
                    "group": group, "path": relative, "problem": "missing",
                })
                continue
            actual = digest_file(path)
            if actual != str(entry.get("sha256") or ""):
                findings.append({
                    "group": group, "path": relative, "problem": "changed",
                    "expected_sha256": entry.get("sha256"), "actual_sha256": actual,
                })

        if group == "catalogue":
            # A file the baseline never saw is as much a difference as one that
            # changed: a stray page-011.json publishes cards nobody recorded.
            for path in _catalogue_file_paths(base):
                relative = _relative(path, base)
                if relative not in recorded_paths:
                    findings.append({
                        "group": group, "path": relative, "problem": "unrecorded",
                    })

    return findings


#: How many paths one `git checkout` call is given. Comfortably under every
#: platform's argument limit, and the catalogue is 191 files today.
_CHECKOUT_BATCH = 100


class RestoreRefused(RuntimeError):
    """Raised instead of writing files this module cannot prove are right."""


def restore(
    baseline: Dict[str, Any],
    root: str | Path = ".",
    *,
    dry_run: bool = True,
    include_state: bool = False,
) -> Dict[str, Any]:
    """Put the recorded catalogue back, or refuse and say why.

    Two properties, in this order:

    1. Nothing is written unless everything verifies. Every file is read out of
       the commit and digested first; a single mismatch refuses the whole
       restore. A restore that writes as it goes and fails halfway leaves the
       catalogue in a state that was never live, which is worse than the state
       it was asked to fix.

    2. git does the writing. The first version wrote the blob bytes straight to
       disk and was wrong on Windows: with `core.autocrlf=true` the checkout
       filter turns LF into CRLF, so a file written as the blob holds it is
       flagged `M` by `git status` for ever after, even though `git diff` shows
       no change. Reimplementing git's filters here would be a second, worse
       copy of them, so `git checkout <commit> -- <paths>` is used instead and
       the result is re-verified afterwards.

    Because git is doing the writing, the restored files land in the index as
    well as the working tree, which is what a rollback wants: the revert is
    staged and ready to commit.
    """
    base = Path(root).resolve()
    git = baseline.get("git") or {}
    commit = str(git.get("commit") or "")
    if not commit:
        raise RestoreRefused(
            "The baseline records no commit, so there is nothing to restore "
            "from. Re-run the backup inside a git checkout."
        )

    entries: List[Dict[str, Any]] = [
        entry for entry in (baseline.get("catalogue_files") or ())
        if isinstance(entry, dict) and entry.get("path")
    ]
    if include_state:
        entries.extend(
            entry for entry in (baseline.get("state_files") or ())
            if isinstance(entry, dict) and entry.get("path")
            and entry.get("present") is not False
        )

    staged: List[str] = []
    problems: List[Dict[str, Any]] = []
    for entry in entries:
        relative = str(entry["path"])
        payload = read_from_commit(base, commit, relative)
        if payload is None:
            problems.append({"path": relative, "problem": "absent_from_commit"})
            continue
        actual = digest_bytes(payload)
        expected = str(entry.get("sha256") or "")
        if actual != expected:
            problems.append({
                "path": relative, "problem": "digest_mismatch",
                "expected_sha256": expected, "actual_sha256": actual,
            })
            continue
        staged.append(relative)

    if problems:
        raise RestoreRefused(
            f"{len(problems)} file(s) in commit {commit[:12]} do not match the "
            f"baseline; nothing was written. First: {problems[0]}"
        )

    written = 0
    if not dry_run:
        # Batched: 191 catalogue files today, but a command line has a limit and
        # a catalogue only grows.
        for start in range(0, len(staged), _CHECKOUT_BATCH):
            batch = staged[start:start + _CHECKOUT_BATCH]
            code, _ = _git(base, "checkout", commit, "--", *batch)
            if code != 0:
                raise RestoreRefused(
                    f"git checkout failed after writing {written} file(s); the "
                    f"catalogue is part-restored. Re-run this command."
                )
            written += len(batch)

        # Ask again, on disk, now that git's own filters have run. A restore
        # that cannot prove its result is the recorded content is not a
        # rollback, and this is the check that would have caught the CRLF bug
        # instead of the test having to.
        leftover = [
            finding for finding in verify_worktree(
                baseline, base, include_state=include_state)
            if finding.get("problem") != "unrecorded"
        ]
        if leftover:
            raise RestoreRefused(
                f"{len(leftover)} file(s) still differ from the baseline after "
                f"the checkout. First: {leftover[0]}"
            )

    return {
        "commit": commit,
        "files_checked": len(staged),
        "files_written": written,
        "dry_run": bool(dry_run),
        "verified": True,
    }


def copy_to(
    baseline: Dict[str, Any],
    destination: str | Path,
    root: str | Path = ".",
) -> Dict[str, Any]:
    """A physical copy of the recorded catalogue, for an operator who wants one.

    Deliberately outside the repository and deliberately not the default: a
    second copy of the same bytes inside the same repo is a claim that drifts,
    not a backup. This exists for the case where the git history itself is what
    someone is worried about.
    """
    base = Path(root).resolve()
    target = Path(destination).resolve()
    copied = 0
    skipped: List[str] = []
    for entry in baseline.get("catalogue_files") or ():
        if not isinstance(entry, dict) or not entry.get("path"):
            continue
        relative = str(entry["path"])
        source = base / relative
        if not source.is_file():
            skipped.append(relative)
            continue
        out = target / relative
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(source.read_bytes())
        copied += 1
    _atomic_write_json(target / "movie-baseline.json", baseline)
    return {"destination": str(target), "files_copied": copied, "skipped": skipped}
