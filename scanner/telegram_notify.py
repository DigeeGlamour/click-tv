#!/usr/bin/env python3
"""Send Telegram success only after GitHub push has completed."""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path
from typing import Any, Dict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scanner.output import send_telegram_alert


def load_json(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def safe_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def build_message(mode: str, commit: str, branch: str) -> str:
    summary = load_json(Path("reports/scan-summary.json"))
    totals = summary.get("totals")
    if not isinstance(totals, dict):
        totals = {}

    mode_clean = str(mode or summary.get("mode") or "channels").strip().lower()
    status = str(summary.get("status") or "completed")
    updated_at = str(summary.get("last_scan") or "")

    titles = {
        "channels": "📺 <b>TV SCAN + GITHUB PUSH SUCCESSFUL</b>",
        "events": "⚽ <b>EVENT SCAN + GITHUB PUSH SUCCESSFUL</b>",
        "movies": "🎬 <b>MOVIE SCAN + GITHUB PUSH SUCCESSFUL</b>",
    }

    lines = [
        titles.get(mode_clean, "✅ <b>SCAN + GITHUB PUSH SUCCESSFUL</b>"),
        "",
        f"<b>Mode:</b> {html.escape(mode_clean)}",
        f"<b>Status:</b> {html.escape(status)}",
        "<b>GitHub Push:</b> Success ✅",
        f"<b>Branch:</b> {html.escape(branch)}",
        f"<b>Commit:</b> {html.escape(commit)}",
    ]

    if updated_at:
        lines.append(f"<b>Updated At:</b> {html.escape(updated_at)}")

    if mode_clean == "channels":
        lines.extend(
            [
                f"<b>TV Channels:</b> {safe_int(totals.get('channels'))}",
                (
                    "<b>Quarantined Channels:</b> "
                    f"{safe_int(summary.get('quarantined_channels'))}"
                ),
                (
                    "<b>Rejected Low Quality:</b> "
                    f"{safe_int(summary.get('rejected_low_quality'))}"
                ),
            ]
        )
    elif mode_clean == "events":
        lines.extend(
            [
                f"<b>Today Matches:</b> {safe_int(totals.get('today_match'))}",
                f"<b>Upcoming Matches:</b> {safe_int(totals.get('upcoming'))}",
            ]
        )
    elif mode_clean == "movies":
        lines.extend(
            [
                f"<b>Movies:</b> {safe_int(totals.get('movies'))}",
                (
                    "<b>Rejected Low Quality:</b> "
                    f"{safe_int(summary.get('rejected_low_quality'))}"
                ),
            ]
        )

    lines.append(
        "<b>Source/Stream Warnings:</b> "
        f"{safe_int(summary.get('source_errors'))}"
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["channels", "events", "movies"])
    parser.add_argument("commit")
    parser.add_argument("branch", nargs="?", default="main")
    args = parser.parse_args()

    message = build_message(args.mode, args.commit, args.branch)
    sent = send_telegram_alert(message)
    if sent:
        print("Telegram success notification sent after GitHub push.")
    else:
        print(
            "Telegram notification was not sent. GitHub push is already "
            "successful; check TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
