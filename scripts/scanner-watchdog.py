#!/usr/bin/env python3
"""Say so when the scanner has stopped - from outside the scanner.

    GITHUB_TOKEN=... GITHUB_REPOSITORY=owner/repo python scripts/scanner-watchdog.py

A workflow cannot report that it has been switched off: it is not running.
On 2026-10-04 five of this repository's workflows were left
`disabled_manually`, and nothing said a word for thirteen days. A scanner that
fails every run at least sends a failure alert; a disabled one sends nothing,
and silence reads exactly like health.

So this runs on its own schedule, in its own workflow, and asks two things:

    1. Is every workflow in the repository enabled?
    2. When did the Live Signal Scanner last finish successfully?

If either answer is wrong it sends one Telegram message. It only reads - it
never re-enables a workflow, because a workflow switched off by hand may have
been switched off on purpose, and that is the owner's decision, not this
script's.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

#: The scanner's slowest cron is the 20-minute Today Match one, and GitHub
#: routinely drops scheduled runs under load (gaps of three to four hours were
#: measured on 2026-08-30). Six hours without a single success is past any
#: weather this repository has seen.
STALE_AFTER_HOURS = 6
SCANNER_WORKFLOW = "scan.yml"


def _api(path: str) -> Any:
    token = os.environ.get("GITHUB_TOKEN", "")
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repository}{path}",
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.github+json",
                 "User-Agent": "click-tv-watchdog"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read() or b"{}")


def bn(text: str) -> str:
    """Digits in Bangla - every Telegram message goes out in Bangla."""
    return text.translate(str.maketrans("0123456789", "০১২৩৪৫৬৭৮৯"))


def _parse(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def paused_workflows() -> set:
    """config/settings.json -> paused.workflows: switched off on purpose."""
    try:
        settings = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
        return {str(name) for name in (settings.get("paused") or {}).get("workflows") or []}
    except Exception:  # noqa: BLE001
        return set()


def findings(workflows: List[Dict[str, Any]], last_success: str,
             now: datetime) -> List[str]:
    problems = []
    paused = paused_workflows()
    for workflow in workflows:
        if str(workflow.get("path") or "").rsplit("/", 1)[-1] in paused:
            continue
        state = str(workflow.get("state") or "")
        if state and state != "active":
            problems.append(
                f"ওয়ার্কফ্লো \"{workflow.get('name')}\" বন্ধ আছে ({state}) - "
                "আবার চালু না করা পর্যন্ত এটি চলবে না।")
    if not last_success:
        problems.append("স্ক্যানারের কোনো সফল রানের রেকর্ড নেই।")
    else:
        age = (now - _parse(last_success)).total_seconds() / 3600.0
        if age > STALE_AFTER_HOURS:
            problems.append(bn(
                f"স্ক্যানার {age:.1f} ঘণ্টা ধরে সফলভাবে শেষ হয়নি "
                f"(শেষ সফল রান {last_success})।"))
    return problems


def main() -> int:
    workflows = _api("/actions/workflows?per_page=100").get("workflows") or []
    runs = _api(f"/actions/workflows/{SCANNER_WORKFLOW}/runs"
                "?status=success&per_page=1").get("workflow_runs") or []
    last_success = str(runs[0].get("updated_at") or "") if runs else ""
    problems = findings(workflows, last_success, datetime.now(timezone.utc))

    if not problems:
        print("watchdog: every workflow enabled, scanner succeeded recently")
        return 0
    for problem in problems:
        print("::warning title=Scanner watchdog::" + problem)

    from scanner.telegram_notify import send_telegram_alert
    import html
    message = "\n".join(
        ["🐕 <b>ক্লিক টিভি স্ক্যানার পাহারাদার</b>", ""]
        + ["• " + html.escape(problem) for problem in problems]
        + ["", "কোনো স্ক্যান সফল না হওয়া পর্যন্ত সাইটে আগের ডেটাই থাকবে।"])
    send_telegram_alert(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
