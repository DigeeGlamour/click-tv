"""Which of this run's generated files may be restored over a rebase.

The restore exists because `git rebase -X theirs` merges JSON by hunk: on
2026-09-06 it interleaved two runs' regenerated arrays and put duplicate cards
on the site. Keeping this run's own files whole fixes that - but a run may only
keep a surface it actually produced.

A catalogue run does not scan events. It still republishes the event files,
because the snapshot publisher mirrors every surface into the slot it is writing
- from the copy that run checked out when it started. Restoring those whole puts
an old list back on the site. Measured twice on real history:

    69e1afcdd  10:15:06  movies    snapshot generation 460 -> 456,
                                   data/upcoming.json updated_at 08:47:49,
                                   88 minutes of events undone
    d7923f8ab  16:37:13  channels  snapshot generation 500 -> 491,
                                   data/upcoming.json updated_at 15:28:40,
                                   62 minutes undone - Today Match went back
                                   from 26 cards to 38, Upcoming 104 to 113

Both were internally consistent - every count matched its file - so
validate-pages.py and detect-merge-corruption.py had nothing to object to. The
fault is not corruption; it is age.

The question each run is asked is therefore not "is my copy older" but "did I
produce this at all". `reports/event-schedule.json` answers it: `process_events`
writes it on every run that scans events, and a run that does not scan events
leaves it exactly as checked out. So a run whose event schedule is byte for byte
its merge base's did not scan events, and does not restore the event surfaces.

Deliberately NOT a rule about which copy is newer. A full Today scan can be
overtaken at the push by a five-minute targeted trigger and finish with an older
timestamp than the snapshot it is rebasing onto - and its list is the fresher
one, because it rescanned every source to build it. Age would throw that away.
Authorship does not.

Nothing here knows what a scan mode is called.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from typing import Optional

#: What the snapshot publisher writes on every run, events or not, and what only
#: an events run has any authority over.
EVENT_SURFACES = ("data/manifest.json", "data/today-match.json",
                  "data/upcoming.json")
EVENT_SURFACE_PREFIX = "data/snapshots/"

#: The receipt an events scan leaves. scanner/events.py writes it at the end of
#: process_events with this run's own `generated_at`.
EVENT_RECEIPT = "reports/event-schedule.json"


def raw(ref: str, path: str) -> Optional[bytes]:
    result = subprocess.run(["git", "show", "%s:%s" % (ref, path)],
                            capture_output=True)
    return None if result.returncode else result.stdout


def generated_at(ref: str, path: str) -> Optional[str]:
    body = raw(ref, path)
    if body is None:
        return None
    try:
        payload = json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    return str(payload.get("generated_at") or "") or None


def scanned_events(ours: str, base: str) -> bool:
    """Whether this run ran the event pipeline.

    A missing receipt on our side means there is nothing to compare and no
    reason to hold anything back: the answer is the behaviour that existed
    before this rule.
    """
    mine = generated_at(ours, EVENT_RECEIPT)
    if mine is None:
        return True
    theirs = generated_at(base, EVENT_RECEIPT)
    return theirs is None or mine != theirs


def is_event_surface(path: str) -> bool:
    return path in EVENT_SURFACES or path.startswith(EVENT_SURFACE_PREFIX)


def exists(ref: str, path: str) -> bool:
    return subprocess.run(["git", "cat-file", "-e", "%s:%s" % (ref, path)],
                          capture_output=True).returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ours", required=True,
                        help="the commit this run produced")
    parser.add_argument("--base", required=True,
                        help="the commit this run branched from")
    parser.add_argument("--theirs",
                        help="what was on main. Needed to name the surfaces "
                             "this run must TAKE from there rather than merely "
                             "not restore - the rebase replays this run's own "
                             "change first, so leaving a file out of the "
                             "restore does not undo it.")
    parser.add_argument("--out-theirs",
                        help="file to write those NUL-separated paths to")
    parser.add_argument("--out", required=True,
                        help="file to write the NUL-separated result to. Not "
                             "stdout: bash strips a NUL out of a command "
                             "substitution, silently joining every path into "
                             "one, and a NUL is the only separator a filename "
                             "cannot contain.")
    parser.add_argument("paths", nargs="*")
    args = parser.parse_args()

    paths = [path for path in args.paths if path.strip()]
    if scanned_events(args.ours, args.base):
        print("  this run scanned events; every regenerated file is its own",
              file=sys.stderr)
        keep, skipped = paths, []
    else:
        keep = [path for path in paths if not is_event_surface(path)]
        skipped = [path for path in paths if is_event_surface(path)]
        print("  this run did not scan events - %s is unchanged since it "
              "branched" % EVENT_RECEIPT, file=sys.stderr)
        for path in skipped:
            print("    not restored: %s" % path, file=sys.stderr)
        if skipped:
            print("  %d event surface(s) left to whoever published them last"
                  % len(skipped), file=sys.stderr)

    def write(path, paths):
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write("\0".join(paths) + ("\0" if paths else ""))

    write(args.out, keep)

    if args.out_theirs:
        # Not restoring a file is not the same as not publishing it. The rebase
        # replays this run's own commit, so a surface it rewrote is already
        # applied by the time the restore runs, and the only way to leave it to
        # whoever published it last is to check that version out.
        take = [path for path in skipped if args.theirs and exists(args.theirs, path)]
        write(args.out_theirs, take)
        if take:
            print("  %d of them will be taken from the newer publish instead"
                  % len(take), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
