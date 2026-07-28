#!/usr/bin/env python3
"""
GitHub push সফল হওয়ার পর পরিষ্কার Telegram summary পাঠাবে।

Warning আর একটি অস্পষ্ট মোট সংখ্যা হিসেবে দেখাবে না।
আলাদাভাবে দেখাবে:

- Source Fetch Warnings
- Failed Stream Candidates
- Output Safety Warnings
- Pipeline Budget Exhausted
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable


PROJECT_ROOT = Path(
    __file__
).resolve().parent.parent

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

        return (
            data
            if isinstance(data, dict)
            else {}
        )

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


def load_error_rows() -> list[Dict[str, Any]]:
    payload = load_json(
        Path(
            "reports/source-errors.json"
        )
    )

    rows = payload.get(
        "errors"
    )

    if not isinstance(
        rows,
        list,
    ):
        return []

    return [
        row
        for row in rows
        if isinstance(row, dict)
    ]


def warning_breakdown(
    rows: Iterable[Dict[str, Any]],
) -> Dict[str, int]:
    """
    Warning type অনুযায়ী আলাদা count তৈরি করবে।
    """
    counts = Counter()

    for row in rows:
        error_type = str(
            row.get("type") or ""
        ).strip().lower()

        if error_type == "source_fetch_error":
            counts["source_fetch"] += 1

        elif error_type == "stream_verification_error":
            counts["stream_failed"] += 1

        else:
            counts["output_safety"] += 1

    counts["total"] = sum(
        counts.values()
    )

    return dict(
        counts
    )


def display_status(
    raw_status: str,
    warnings: Dict[str, int],
    budget_exhausted: bool,
) -> str:
    """
    একটি public source list-এ dead stream পাওয়া স্বাভাবিক।

    শুধু individual stream candidate fail করলে পুরো scan-কে
    warning scan হিসেবে দেখানো হবে না।

    Serious warning:
    - Source fetch failure
    - Output/publish safety warning
    - Pipeline budget exhausted
    """
    serious_warning_count = (
        safe_int(
            warnings.get("source_fetch")
        )
        + safe_int(
            warnings.get("output_safety")
        )
        + (
            1
            if budget_exhausted
            else 0
        )
    )

    if serious_warning_count == 0:
        return "completed"

    return (
        raw_status
        or "completed_with_warnings"
    )


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

    performance = load_json(
        Path(
            "reports/pipeline-performance.json"
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

    raw_status = str(
        summary.get("status")
        or "completed"
    )

    updated_at = str(
        summary.get("last_scan")
        or ""
    )

    errors = load_error_rows()

    warnings = warning_breakdown(
        errors
    )

    budget_exhausted = bool(
        performance.get(
            "budget_exhausted"
        )
    )

    final_status = display_status(
        raw_status,
        warnings,
        budget_exhausted,
    )

    titles = {
        "channels": (
            "📺 <b>TV CHANNEL SCAN + "
            "GITHUB PUSH SUCCESSFUL</b>"
        ),

        "today": (
            "⚽ <b>TODAY MATCH SCAN + "
            "GITHUB PUSH SUCCESSFUL</b>"
        ),

        "upcoming": (
            "🗓️ <b>UPCOMING MATCH SCAN + "
            "GITHUB PUSH SUCCESSFUL</b>"
        ),

        "movies": (
            "🎬 <b>MOVIE SCAN + "
            "GITHUB PUSH SUCCESSFUL</b>"
        ),

        "all": (
            "✅ <b>ALL SCANS + "
            "GITHUB PUSH SUCCESSFUL</b>"
        ),
    }

    lines = [
        titles.get(
            mode_clean,
            (
                "✅ <b>SCAN + "
                "GITHUB PUSH SUCCESSFUL</b>"
            ),
        ),

        "",

        (
            "<b>Mode:</b> "
            f"{html.escape(mode_clean)}"
        ),

        (
            "<b>Scan Result:</b> "
            f"{html.escape(final_status)} ✅"
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

    lines.extend(
        [
            "",

            "⚠️ <b>Verification Details</b>",

            (
                "<b>Source Fetch Warnings:</b> "
                f"{safe_int(warnings.get('source_fetch'))}"
            ),

            (
                "<b>Failed Stream Candidates:</b> "
                f"{safe_int(warnings.get('stream_failed'))}"
            ),

            (
                "<b>Output Safety Warnings:</b> "
                f"{safe_int(warnings.get('output_safety'))}"
            ),

            (
                "<b>Pipeline Budget Exhausted:</b> "
                + (
                    "Yes ⚠️"
                    if budget_exhausted
                    else "No ✅"
                )
            ),
        ]
    )

    elapsed_seconds = safe_int(
        performance.get(
            "elapsed_seconds"
        )
    )

    if elapsed_seconds:
        minutes, seconds = divmod(
            elapsed_seconds,
            60,
        )

        lines.append(
            "<b>Verification Time:</b> "
            f"{minutes}m {seconds}s"
        )

    global_checked = safe_int(
        performance.get(
            "global_network_checked"
        )
    )

    bd_checked = safe_int(
        performance.get(
            "bd_proxy_submitted"
        )
    )

    adaptive_skipped = safe_int(
        performance.get(
            "adaptive_skipped"
        )
    )

    if (
        global_checked
        or bd_checked
        or adaptive_skipped
    ):
        lines.extend(
            [
                (
                    "<b>Global Checked:</b> "
                    f"{global_checked}"
                ),

                (
                    "<b>BD/Proxy Checked:</b> "
                    f"{bd_checked}"
                ),

                (
                    "<b>Adaptive Skipped:</b> "
                    f"{adaptive_skipped}"
                ),
            ]
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
            "Telegram notification was not sent. "
            "GitHub push is already successful; "
            "check TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID."
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
