#!/usr/bin/env python3
"""How many finished runs in a row have failed, counting the one asking.

    gh run list --workflow scan.yml --limit 40 --json status,conclusion > runs.json
    python scripts/failure-streak.py runs.json        # prints e.g. 3

Newest first, as `gh run list` returns them. Runs still in flight are not a
verdict and are skipped; so are cancelled and skipped runs, which on this
workflow are concurrency evictions rather than outcomes. The run calling this
is still in progress and so is not in the list - it has failed, or it would
not be asking - which is the `+ 1`.
"""
from __future__ import annotations

import json
import sys
from typing import Any, Iterable


def streak(runs: Iterable[Any]) -> int:
    count = 1
    for run in runs or ():
        if not isinstance(run, dict) or run.get("status") != "completed":
            continue
        if run.get("conclusion") in ("cancelled", "skipped"):
            continue
        if run.get("conclusion") != "failure":
            break
        count += 1
    return count


def main(argv: list) -> int:
    try:
        with open(argv[1], encoding="utf-8") as handle:
            runs = json.load(handle)
    except (IndexError, OSError, ValueError):
        runs = []
    print(streak(runs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
