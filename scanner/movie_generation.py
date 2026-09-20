"""ধাপ ৯ / S-07 - one stamp, so the movie surfaces cannot disagree silently.

S-07's risk, in the plan's own words: "ক্যাটালগ ও সার্চ ইনডেক্স আলাদা সময়ে লেখা
হলে UI পুরোনো ফল দেখাতে পারে" - if the catalogue and the search index are
written at different moments, the site can show results for films the pages no
longer contain. It is a one-star item because it has not been caught happening.

Except it has, once, and the fix is in this codebase already. From
`movies.process_movies`:

> scanner/output.py keeps the previous movie pages when the incoming total
> collapses, which is what saved the catalogue on the 17th ... Discovery was
> written from the 377 anyway, so Movie Home advertised films the catalogue did
> not contain - 40 of 40 in Just Added and 4 of 5 in the Featured hero pointed
> at ids with no page behind them.

That guard fixed one path. This makes the whole class of it *detectable*: every
movie surface a publish writes carries the same generation, so "these files
describe the same scan" stops being an assumption and becomes a check.

Why not extend the snapshot publisher instead
----------------------------------------------
`snapshot_publish.py` already does the full version-and-swap for events - three
round-robin slots behind one pointer, with generation numbers - and doing the
same for movies is the tempting answer. It is the wrong one here: the movie
pages are published at stable URLs the site reads directly
(`data/movies/dubbed/index.json` and the rest), and moving them under
`data/snapshots/sN/` would change every one of those URLs and break the front
end. S-07 asks for a generation id and an atomic publish; the atomic part -
`_atomic_replace_directory` per category - is already there, and this adds the
part that is missing.

What a generation is
--------------------
A monotonic counter plus the timestamp that produced it. The counter is what
makes "older" meaningful; the timestamp is what makes a report readable.
Stamped into the payload rather than kept in a side file, because a side file
is one more thing that can be out of step with what it describes.

Stdlib only. Stamps and compares; publishes nothing.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

SCHEMA_VERSION = 1

#: Where the counter lives. Small, committed, and the only mutable part - the
#: generation each surface carries is written into the surface itself.
DEFAULT_STATE_PATH = Path("state") / "movie-generation.json"

#: The field every stamped surface carries.
FIELD = "catalog_generation"
FIELD_AT = "catalog_generation_at"

#: The surfaces one movie publish writes, relative to the data root. A file
#: missing from this list is a file whose staleness nobody would notice, which
#: is the whole failure S-07 describes - so the list is the contract and the
#: consistency check reads it rather than globbing.
SURFACE_GLOBS: Tuple[str, ...] = (
    "movies/*/index.json",
    "movies/*/page-*.json",
    "movies/search-index.json",
    "movies/discovery/*.json",
    "movies/genres/*.json",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=str(path.parent), delete=False,
        prefix=f".{path.name}.", suffix=".tmp",
    )
    try:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    finally:
        handle.close()
    os.replace(handle.name, path)


def load(path: Optional[str | Path] = None) -> Dict[str, Any]:
    target = Path(path or DEFAULT_STATE_PATH)
    try:
        with open(target, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    try:
        current = int(payload.get("generation") or 0)
    except (TypeError, ValueError):
        current = 0
    return {
        "schema_version": SCHEMA_VERSION,
        "generation": max(0, current),
        "generated_at": payload.get("generated_at") or "",
    }


def allocate(path: Optional[str | Path] = None) -> Dict[str, Any]:
    """The next generation, persisted before anything is stamped with it.

    Written first on purpose. A crash between allocating and publishing wastes
    a number, which costs nothing; reusing one would let two different
    catalogues claim to be the same generation, which is the one thing this
    must never allow.
    """
    target = Path(path or DEFAULT_STATE_PATH)
    state = load(target)
    state["generation"] = int(state["generation"]) + 1
    state["generated_at"] = _utc_now()
    try:
        _atomic_write(target, state)
    except OSError:
        # An unwritable state file is not a reason to refuse to publish. The
        # generation is still returned and still stamped; it simply will not
        # advance next time, which the consistency check will show.
        pass
    return state


def stamp(payload: Any, generation: int, at: str = "") -> Any:
    """Mark one payload as belonging to this generation. Adds only.

    A list payload is returned untouched - only the mapping that wraps a
    surface carries the stamp, because a bare array has nowhere to put one and
    inventing a wrapper would change the published shape.
    """
    if not isinstance(payload, dict):
        return payload
    payload[FIELD] = int(generation)
    payload[FIELD_AT] = at or _utc_now()
    return payload


def stamp_paginated(
    paginated: Dict[str, Any], generation: int, at: str = ""
) -> Dict[str, Any]:
    """Stamp a `{category: {index, page_contents}}` payload, in place.

    Both the index and every page, because the failure being guarded against is
    a page that belongs to a different scan than the index counting it.
    """
    moment = at or _utc_now()
    for payload in (paginated or {}).values():
        if not isinstance(payload, dict):
            continue
        stamp(payload.get("index"), generation, moment)
        pages = payload.get("page_contents")
        if isinstance(pages, dict):
            for page in pages.values():
                stamp(page, generation, moment)
    return paginated


def _read(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def published_generations(
    data_root: str | Path = "data",
) -> Dict[str, List[str]]:
    """`generation -> the files carrying it`, over every movie surface.

    "unstamped" is its own bucket rather than being ignored: a surface that
    carries no generation is exactly as unverifiable as one carrying the wrong
    generation, and quietly skipping it would make the check pass by looking
    away.
    """
    root = Path(data_root)
    found: Dict[str, List[str]] = {}
    for pattern in SURFACE_GLOBS:
        for path in sorted(root.glob(pattern)):
            payload = _read(path)
            if payload is None:
                continue
            value = payload.get(FIELD)
            key = str(int(value)) if isinstance(value, int) else "unstamped"
            found.setdefault(key, []).append(
                path.relative_to(root).as_posix())
    return found


def check(data_root: str | Path = "data") -> Dict[str, Any]:
    """Do the published movie surfaces all describe the same scan?

    Reports rather than raises. A mismatch means the site may be describing a
    catalogue it does not have, which is worth a loud warning and a number in
    the scan summary - but refusing to publish because *last* run was
    inconsistent would make a transient fault permanent.
    """
    generations = published_generations(data_root)
    stamped = {key: files for key, files in generations.items()
               if key != "unstamped"}
    unstamped = generations.get("unstamped") or []
    numbers = sorted(int(key) for key in stamped)

    # Three states, not two. Surfaces that all predate this feature carry no
    # stamp and are consistent *with each other* - reporting them as a
    # disagreement would raise a warning on the first run after this ships,
    # before anything has been stamped, and a check that cries wolf once is a
    # check people learn to ignore. A *mix* of stamped and unstamped is the
    # real fault, because then something was rewritten and something was not.
    not_yet_stamped = not numbers and bool(unstamped)
    consistent = not_yet_stamped or (len(numbers) <= 1 and not unstamped)

    return {
        "checked_at": _utc_now(),
        "generations": {key: len(files) for key, files in sorted(generations.items())},
        "distinct_generations": len(numbers),
        "current_generation": numbers[-1] if numbers else 0,
        "unstamped_files": len(unstamped),
        "not_yet_stamped": not_yet_stamped,
        "consistent": consistent,
        # Named, not just counted: "which file is stale" is the only part of
        # the answer anybody can act on.
        "sample_mismatch": (
            [] if consistent else
            sorted(
                file
                for key, files in generations.items()
                if key == "unstamped" or (numbers and int(key) != numbers[-1])
                for file in files
            )[:20]
        ),
    }


def describe(report: Dict[str, Any]) -> str:
    if report.get("not_yet_stamped"):
        return (
            f"movie surfaces carry no generation yet "
            f"({report.get('unstamped_files')} file(s)); the next publish "
            f"stamps them"
        )
    if report.get("consistent"):
        return (
            f"movie surfaces all at generation "
            f"{report.get('current_generation')}"
        )
    return (
        f"movie surfaces DISAGREE: {report.get('distinct_generations')} "
        f"generation(s) plus {report.get('unstamped_files')} unstamped file(s); "
        f"the site may describe a catalogue it does not have"
    )
