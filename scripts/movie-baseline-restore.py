#!/usr/bin/env python3
"""ধাপ ০ - put the recorded Movie catalogue back, or refuse and say why.

    python scripts/movie-baseline-restore.py                # dry run, default
    python scripts/movie-baseline-restore.py --apply
    python scripts/movie-baseline-restore.py --apply --include-state

Reads `state/movie-baseline.json`, reads every file it lists out of the commit
it names, and checks each sha256 *before* writing anything. If one file is
absent from that commit or digests differently, nothing is written at all - a
half-applied restore leaves the site in a state that was never live, which is
worse than the state it was asked to fix.

Dry run is the default on purpose. Restoring the catalogue is the one operation
in this plan that overwrites live data, so it asks to be typed.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import movie_baseline  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--baseline", default=str(movie_baseline.DEFAULT_BASELINE_PATH))
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually write the files. Without it this verifies only.",
    )
    parser.add_argument(
        "--include-state", action="store_true",
        help="Also restore the recorded state files. Off by default: a scan "
             "moves them legitimately, and rolling them back undoes work that "
             "was not part of what is being reverted.",
    )
    arguments = parser.parse_args()

    root = Path(arguments.root)
    try:
        baseline = movie_baseline.load_baseline(arguments.baseline, root=root)
    except (OSError, ValueError) as error:
        print(f"no usable baseline: {error}", file=sys.stderr)
        return 2

    try:
        result = movie_baseline.restore(
            baseline, root,
            dry_run=not arguments.apply,
            include_state=arguments.include_state,
        )
    except movie_baseline.RestoreRefused as error:
        print(json.dumps({
            "mode": "restore",
            "status": "refused",
            "reason": str(error),
        }, ensure_ascii=False, indent=2))
        return 1

    print(json.dumps({
        "mode": "restore",
        "status": "applied" if arguments.apply else "verified",
        "baseline_label": baseline.get("label"),
        **result,
    }, ensure_ascii=False, indent=2))

    if not arguments.apply:
        print(
            "\nVerified only. Every recorded file was read from the commit and "
            "matched its digest. Re-run with --apply to write them.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
