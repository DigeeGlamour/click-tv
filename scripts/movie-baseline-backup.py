#!/usr/bin/env python3
"""ধাপ ০ - record what the Movie catalogue looks like before anything changes.

    python scripts/movie-baseline-backup.py
    python scripts/movie-baseline-backup.py --label pre-series-migration
    python scripts/movie-baseline-backup.py --copy-to ../clicktv-baseline
    python scripts/movie-baseline-backup.py --check        # changes nothing

Writes `state/movie-baseline.json`: the commit the published files came from,
a sha256 of every one of them, the card and link counts, and a digest of the
full stream inventory. `scripts/movie-baseline-restore.py` reads it back.

`--check` compares the working tree against an existing baseline and exits
non-zero if anything drifted. That is the form the tests and any later phase
use, because it writes nothing.
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
    parser.add_argument(
        "--root", default=str(ROOT),
        help="Repository root (default: the checkout this script lives in).",
    )
    parser.add_argument(
        "--baseline", default=str(movie_baseline.DEFAULT_BASELINE_PATH),
        help="Where the record is written, relative to --root.",
    )
    parser.add_argument("--label", default="", help="A name for this baseline.")
    parser.add_argument(
        "--copy-to", default="",
        help="Also write a physical copy of the catalogue here. Keep it "
             "outside the repository.",
    )
    parser.add_argument(
        "--check", action="store_true",
        help="Compare the working tree against the existing baseline and "
             "write nothing. Exits 1 on any difference.",
    )
    parser.add_argument(
        "--include-state", action="store_true",
        help="With --check, also compare the state files. Off by default "
             "because state moves on every scan by design.",
    )
    arguments = parser.parse_args()

    root = Path(arguments.root)

    if arguments.check:
        try:
            baseline = movie_baseline.load_baseline(arguments.baseline, root=root)
        except (OSError, ValueError) as error:
            print(f"no usable baseline: {error}", file=sys.stderr)
            return 2
        findings = movie_baseline.verify_worktree(
            baseline, root, include_state=arguments.include_state
        )
        print(json.dumps({
            "mode": "check",
            "baseline_label": baseline.get("label"),
            "baseline_commit": (baseline.get("git") or {}).get("commit", ""),
            "differences": len(findings),
            "findings": findings[:50],
        }, ensure_ascii=False, indent=2))
        if findings:
            print(
                f"\n{len(findings)} difference(s) from the baseline.",
                file=sys.stderr,
            )
            return 1
        print("\nWorking tree matches the baseline exactly.", file=sys.stderr)
        return 0

    baseline = movie_baseline.build_baseline(root, label=arguments.label)
    written = movie_baseline.write_baseline(baseline, arguments.baseline, root=root)

    counts = baseline["counts"]
    git = baseline["git"]
    print(json.dumps({
        "mode": "backup",
        "baseline": str(written),
        "label": baseline["label"],
        "commit": git.get("commit", ""),
        "dirty": git.get("dirty"),
        "counts": counts,
        "verification_status_counts": baseline["verification_status_counts"],
        "catalogue_files": len(baseline["catalogue_files"]),
        "state_files": sum(
            1 for entry in baseline["state_files"] if entry.get("present")
        ),
        "stream_inventory": baseline["stream_inventory"],
    }, ensure_ascii=False, indent=2))

    if arguments.copy_to:
        result = movie_baseline.copy_to(baseline, arguments.copy_to, root)
        print(json.dumps({"mode": "copy", **result}, ensure_ascii=False, indent=2))

    if not git.get("commit"):
        print(
            "\nWARNING: no git commit was recorded, so this baseline cannot be "
            "restored from. Run it inside a checkout.",
            file=sys.stderr,
        )
        return 1
    if git.get("dirty"):
        print(
            "\nNOTE: the working tree has uncommitted changes. The digests "
            "above describe the files on disk; restore reads the commit. "
            "Commit first if you want the two to agree.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
