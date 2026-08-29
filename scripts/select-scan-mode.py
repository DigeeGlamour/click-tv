#!/usr/bin/env python3
"""Decide what this workflow run should scan, given how little of the schedule survives.

The workflow declares five crons - roughly 370 scheduled events a day. GitHub
delivered ten of them on 2026-08-27, nine on 08-28 and nine on 08-29, having
delivered ninety-five on 08-22. That is GitHub's documented behaviour, not a
defect here: a `schedule` event is delayed under load and *skipped* if the delay
reaches the next occurrence, and the `*/5` cron guarantees a next occurrence
five minutes away. Nothing in this repository can make GitHub honour it.

What the repository can decide is what the surviving runs do. Under the old
rule each run scanned exactly the mode its own cron named, so a mode whose cron
was skipped simply did not happen:

    channels   cron 17 0,6,12,18  ->  last real scan 2026-08-28 22:44 UTC
    movies     cron 37 4          ->  last real scan 2026-08-27 15:13 UTC

Sixteen and forty-seven hours stale, with the site publishing both. Meanwhile
the frequent modes ran on almost every surviving run, because with four crons
competing for ten slots the two dense ones win.

So a run now also carries whichever catalogue mode is overdue. It does not
replace the scheduled mode - `today` still refreshes - it runs the overdue one
first and then the scheduled one. A channels scan is about fifteen minutes and
the job's ceiling is six hours, so the cost is affordable and the alternative is
a catalogue that silently stops updating.

Deliberately narrow:

  * only `channels` and `movies` are ever added, because only those two have a
    cadence sparse enough for a skip to matter for a day;
  * a manual `workflow_dispatch` is never second-guessed - somebody asked for a
    specific mode;
  * a run whose own mode is already the overdue one adds nothing;
  * at most one catch-up per run, so a long-idle repository recovers over
    several runs instead of one six-hour job;
  * "overdue" is measured from the mode's own last *completed* scan as recorded
    in reports/scan-summary-<mode>.json, not from a clock guess.

Usage:
    python scripts/select-scan-mode.py --scheduled today
prints two lines for $GITHUB_OUTPUT:
    mode=today
    catchup=channels
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from typing import Any, Dict, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: How long a catalogue mode may go unscanned before any surviving run picks it
#: up. Each is its own cron interval plus half, so an ordinary on-time schedule
#: never triggers a catch-up and only a genuinely skipped one does.
#:
#:   channels  cron every 6 h   -> 9 h
#:   movies    cron every 24 h  -> 36 h
STALE_AFTER_HOURS: Dict[str, float] = {
    "channels": 9.0,
    "movies": 36.0,
}

#: Checked in this order, and the first overdue one wins. Channels first: it is
#: the catalogue every viewer opens, and it is the cheaper of the two.
CATCHUP_ORDER = ("channels", "movies")

#: Modes that must never be displaced or added to. `all` already covers
#: everything, and the twice-daily `upcoming` refresh is itself sparse.
NO_CATCHUP_MODES = frozenset({"all", "channels", "movies"})


def _summary_path(mode: str, reports_dir: str) -> str:
    return os.path.join(reports_dir, "scan-summary-%s.json" % mode)


def last_scan_at(mode: str, reports_dir: str) -> Optional[datetime.datetime]:
    """When this mode last completed, or None if it never has here."""
    try:
        with open(_summary_path(mode, reports_dir), "r", encoding="utf-8") as handle:
            payload: Dict[str, Any] = json.load(handle)
    except (OSError, ValueError):
        return None
    raw = str(payload.get("last_scan") or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed


def hours_stale(
    mode: str,
    reports_dir: str,
    now: Optional[datetime.datetime] = None,
) -> Optional[float]:
    """Hours since this mode last completed, or None when it never has.

    None is not "fresh". A repository with no summary for a mode has never
    scanned it here, which is the strongest possible case for scanning it.
    """
    when = last_scan_at(mode, reports_dir)
    if when is None:
        return None
    moment = now or datetime.datetime.now(datetime.timezone.utc)
    return (moment - when).total_seconds() / 3600.0


def choose(
    scheduled: str,
    *,
    event_name: str = "schedule",
    reports_dir: str = "",
    now: Optional[datetime.datetime] = None,
) -> Tuple[str, str, str]:
    """Return (mode, catchup, why). `catchup` is "" when nothing is overdue."""
    mode = str(scheduled or "").strip().lower()
    reports = reports_dir or os.path.join(ROOT, "reports")

    if event_name != "schedule":
        return mode, "", "manual dispatch: the requested mode runs on its own"
    if mode in NO_CATCHUP_MODES:
        return mode, "", "%s already refreshes a catalogue; nothing added" % mode

    for candidate in CATCHUP_ORDER:
        if candidate == mode:
            continue
        stale = hours_stale(candidate, reports, now=now)
        if stale is None:
            return mode, candidate, "%s has no recorded scan here" % candidate
        limit = STALE_AFTER_HOURS[candidate]
        if stale > limit:
            return (
                mode,
                candidate,
                "%s last completed %.1f h ago, over its %.1f h limit"
                % (candidate, stale, limit),
            )
    return mode, "", "every catalogue mode is within its freshness limit"


def main(argv: Any = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scheduled", required=True)
    parser.add_argument("--event-name", default="schedule")
    parser.add_argument("--reports-dir", default="")
    args = parser.parse_args(argv)

    mode, catchup, why = choose(
        args.scheduled,
        event_name=args.event_name,
        reports_dir=args.reports_dir,
    )
    for candidate in CATCHUP_ORDER:
        stale = hours_stale(candidate, args.reports_dir or os.path.join(ROOT, "reports"))
        print(
            "  %-9s last scan %s"
            % (candidate, "never" if stale is None else "%.1f h ago" % stale),
            file=sys.stderr,
        )
    print("  decision: %s" % why, file=sys.stderr)

    lines = ["mode=%s" % mode, "catchup=%s" % catchup]
    target = os.environ.get("GITHUB_OUTPUT", "")
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
