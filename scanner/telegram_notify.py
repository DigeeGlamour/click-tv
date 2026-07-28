#!/usr/bin/env python3
"""
Send Telegram success notification only after GitHub push completes.
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path
from typing import Any, Dict


PROJECT_ROOT = Path(__file__).resolve().parent.parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(
        0,
        str(PROJECT_ROOT),
    )


from scanner.output import send_telegram_alert


def load_json(
    path: Path,
) -> Dict[str, Any]:
    try:
        data = json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )

        if isinstance(data, dict):
            return data

        return {}

    except Exception:
        return {}


def safe_int(
    value: Any,
) -> int:
    try:
        return max(
            0,
            int(value or 0),
        )

    except (
        TypeError,
        ValueError,
    ):
        return 0


def build_message(
    mode: str,
    commit: str,
    branch: str,
) -> str:
    summary = load_json(
        Path(
            "reports/scan-summary.json"
        )
    )

    totals = summary.get(
        "totals"
    )

    if not isinstance(
        totals,
        dict,
    ):
        totals = {}

    mode_clean = str(
        mode
        or summary.get("mode")
        or "channels"
    ).strip().lower()

    status = str(
        summary.get("status")
        or "completed"
    )

    updated_at = str(
        summary.get("last_scan")
        or ""
    )

    titles = {
        "channels": (
            "📺 <b>TV CHANNEL SCAN + GITHUB PUSH SUCCESSFUL</b>"
        ),
        "today": (
            "⚽ <b>TODAY MATCH SCAN + GITHUB PUSH SUCCESSFUL</b>"
        ),
        "upcoming": (
            "🗓️ <b>UPCOMING MATCH SCAN + GITHUB PUSH SUCCESSFUL</b>"
        ),
        "movies": (
            "🎬 <b>MOVIE SCAN + GITHUB PUSH SUCCESSFUL</b>"
        ),
        "all": (
            "✅ <b>ALL SCANS + GITHUB PUSH SUCCESSFUL</b>"
        ),
    }

    lines = [
        titles.get(
            mode_clean,
            "✅ <b>SCAN + GITHUB PUSH SUCCESSFUL</b>",
        ),
        "",
        (
            "<b>Mode:</b> "
            f"{html.escape(mode_clean)}"
        ),
        (
            "<b>Status:</b> "
            f"{html.escape(status)}"
        ),
        "<b>GitHub Push:</b> Success ✅",
        (
            "<b>Branch:</b> "
            f"{html.escape(branch)}"
        ),
        (
            "<b>Commit:</b> "
            f"{html.escape(commit)}"
        ),
    ]

    if updated_at:
        lines.append(
            "<b>Updated At:</b> "
            f"{html.escape(updated_at)}"
        )

    if mode_clean == "channels":
        lines.extend(
            [
                (
                    "<b>TV Channels:</b> "
                    f"{safe_int(totals.get('channels'))}"
                ),
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

    elif mode_clean == "today":
        lines.append(
            "<b>Today Matches:</b> "
            f"{safe_int(totals.get('today_match'))}"
        )

    elif mode_clean == "upcoming":
        lines.append(
            "<b>Upcoming Matches:</b> "
            f"{safe_int(totals.get('upcoming'))}"
        )

    elif mode_clean == "movies":
        lines.extend(
            [
                (
                    "<b>Movies:</b> "
                    f"{safe_int(totals.get('movies'))}"
                ),
                (
                    "<b>Rejected Low Quality:</b> "
                    f"{safe_int(summary.get('rejected_low_quality'))}"
                ),
            ]
        )

    elif mode_clean == "all":
        lines.extend(
            [
                (
                    "<b>TV Channels:</b> "
                    f"{safe_int(totals.get('channels'))}"
                ),
                (
                    "<b>Today Matches:</b> "
                    f"{safe_int(totals.get('today_match'))}"
                ),
                (
                    "<b>Upcoming Matches:</b> "
                    f"{safe_int(totals.get('upcoming'))}"
                ),
                (
                    "<b>Movies:</b> "
                    f"{safe_int(totals.get('movies'))}"
                ),
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

    lines.append(
        "<b>Source/Stream Warnings:</b> "
        f"{safe_int(summary.get('source_errors'))}"
    )

    return "\n".join(
        lines
    )


def main() -> int:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "mode",
        choices=[
            "channels",
            "today",
            "upcoming",
            "movies",
            "all",
        ],
    )

    parser.add_argument(
        "commit"
    )

    parser.add_argument(
        "branch",
        nargs="?",
        default="main",
    )

    args = parser.parse_args()

    message = build_message(
        args.mode,
        args.commit,
        args.branch,
    )

    sent = send_telegram_alert(
        message
    )

    if sent:
        print(
            "Telegram success notification sent after GitHub push."
        )

    else:
        print(
            "Telegram notification was not sent. GitHub push is already "
            "successful; check TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID."
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
