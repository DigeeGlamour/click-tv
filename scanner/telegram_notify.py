#!/usr/bin/env python3
"""Send Telegram success or failure messages for the scanner workflow."""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List

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


def _format_duration(seconds: Any) -> str:
    total = safe_int(seconds)
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def _as_dict_list(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _warning_breakdown(source_report: Dict[str, Any]) -> Dict[str, int]:
    source_fetch = 0
    failed_stream = 0
    output_safety = 0

    for item in _as_dict_list(source_report.get("errors")):
        item_type = str(item.get("type") or "").strip().casefold()
        if item_type == "stream_verification_error":
            failed_stream += 1
        elif item_type in {
            "movie_output_safety_warning",
            "output_safety_warning",
            "sudden_drop_protection",
        }:
            output_safety += 1
        elif item_type == "content_routing_migration" and str(
            item.get("status") or ""
        ).casefold() == "completed":
            continue
        else:
            source_fetch += 1

    return {
        "source_fetch": source_fetch,
        "failed_stream": failed_stream,
        "output_safety": output_safety,
    }


def _status_count(status_counts: Dict[str, Any], *names: str) -> int:
    return sum(safe_int(status_counts.get(name)) for name in names)


def build_message(mode: str, commit: str, branch: str) -> str:
    summary = load_json(Path("reports/scan-summary.json"))
    source_report = load_json(Path("reports/source-errors.json"))
    bd_report = load_json(Path("reports/bd-verification.json"))
    performance = load_json(Path("reports/pipeline-performance.json"))
    safety_report = load_json(Path("reports/output-safety.json"))
    quarantine_report = load_json(Path("reports/quarantine.json"))

    totals = summary.get("totals")
    if not isinstance(totals, dict):
        totals = {}

    status_counts = bd_report.get("status_counts")
    if not isinstance(status_counts, dict):
        status_counts = summary.get("movie_verification_status_counts")
    if not isinstance(status_counts, dict):
        status_counts = {}

    mode_clean = str(mode or summary.get("mode") or "channels").strip().lower()
    raw_status = str(summary.get("status") or "completed")
    display_status = "completed ✅" if raw_status.startswith("completed") else raw_status
    updated_at = str(summary.get("last_scan") or "")
    warnings = _warning_breakdown(source_report)

    failed_from_status = _status_count(status_counts, "failed", "failed_bd")
    failed_streams = max(warnings["failed_stream"], failed_from_status)
    output_safety_count = max(
        warnings["output_safety"],
        safe_int(safety_report.get("count")),
        safe_int(summary.get("output_safety_warnings")),
    )

    titles = {
        "channels": "📺 <b>TV CHANNEL SCAN + GITHUB PUSH SUCCESSFUL</b>",
        "today": "⚽ <b>TODAY MATCH SCAN + GITHUB PUSH SUCCESSFUL</b>",
        "upcoming": "🗓️ <b>UPCOMING MATCH SCAN + GITHUB PUSH SUCCESSFUL</b>",
        "movies": "🎬 <b>MOVIES SCAN + GITHUB PUSH SUCCESSFUL</b>",
        "all": "✅ <b>ALL SCANS + GITHUB PUSH SUCCESSFUL</b>",
    }

    lines = [
        titles.get(mode_clean, "✅ <b>SCAN + GITHUB PUSH SUCCESSFUL</b>"),
        "",
        f"<b>Mode:</b> {html.escape(mode_clean)}",
        f"<b>Scan Result:</b> {html.escape(display_status)}",
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
                f"<b>Quarantined Channels:</b> {safe_int(summary.get('quarantined_channels'))}",
                f"<b>Rejected Low Quality:</b> {safe_int(summary.get('rejected_low_quality'))}",
            ]
        )
    elif mode_clean == "today":
        lines.append(f"<b>Today Matches:</b> {safe_int(totals.get('today_match'))}")
    elif mode_clean == "upcoming":
        lines.append(f"<b>Upcoming Matches:</b> {safe_int(totals.get('upcoming'))}")
    elif mode_clean == "movies":
        lines.extend(
            [
                f"<b>Movies:</b> {safe_int(totals.get('movies'))}",
                f"<b>Rejected Low Quality:</b> {safe_int(summary.get('rejected_low_quality'))}",
            ]
        )
    elif mode_clean == "all":
        lines.extend(
            [
                f"<b>TV Channels:</b> {safe_int(totals.get('channels'))}",
                f"<b>Today Matches:</b> {safe_int(totals.get('today_match'))}",
                f"<b>Upcoming Matches:</b> {safe_int(totals.get('upcoming'))}",
                f"<b>Movies:</b> {safe_int(totals.get('movies'))}",
                f"<b>Quarantined Channels:</b> {safe_int(summary.get('quarantined_channels'))}",
                f"<b>Rejected Low Quality:</b> {safe_int(summary.get('rejected_low_quality'))}",
            ]
        )

    if mode_clean in {"movies", "all"}:
        lines.extend(
            [
                "",
                "🎯 <b>Movie Status Breakdown</b>",
                f"<b>Verified Global:</b> {_status_count(status_counts, 'verified_global', 'verified_bd', 'verified')}",
                f"<b>Verified Proxy:</b> {_status_count(status_counts, 'verified_proxy')}",
                f"<b>Stale Last Good:</b> {_status_count(status_counts, 'stale_last_good')}",
                f"<b>Geo Pending:</b> {_status_count(status_counts, 'geo_pending', 'bd_protected_pending')}",
                f"<b>Retryable Pending:</b> {_status_count(status_counts, 'retryable_pending')}",
                f"<b>Host Deferred:</b> {_status_count(status_counts, 'host_deferred')}",
                f"<b>404 Quarantined:</b> {safe_int(summary.get('quarantined_movies'))}",
            ]
        )

    if mode_clean in {"channels", "movies", "all"}:
        lines.extend(
            [
                "",
                "⚠️ <b>Verification Details</b>",
                f"<b>Source Fetch Warnings:</b> {warnings['source_fetch']}",
                f"<b>Failed Stream Candidates:</b> {failed_streams}",
                f"<b>Output Safety Warnings:</b> {output_safety_count}",
                (
                    "<b>Pipeline Budget Exhausted:</b> "
                    + ("Yes ⚠️" if performance.get("budget_exhausted") is True else "No ✅")
                ),
                f"<b>Verification Time:</b> {_format_duration(performance.get('elapsed_seconds'))}",
                f"<b>Global Checked:</b> {safe_int(performance.get('global_network_checked'))}",
                f"<b>BD/Proxy Checked:</b> {safe_int(performance.get('bd_proxy_submitted'))}",
                f"<b>Adaptive Skipped:</b> {safe_int(performance.get('adaptive_skipped'))}",
            ]
        )

    if summary.get("movie_output_preserved") is True:
        lines.extend(
            [
                "",
                "🛡️ <b>Previous movie output was preserved because the new scan dropped too sharply.</b>",
            ]
        )

    return "\n".join(lines)[:4000]


def build_failure_message(mode: str, commit: str, branch: str, run_url: str) -> str:
    mode_clean = str(mode or "unknown").strip().lower()
    lines = [
        "❌ <b>CLICK TV SCAN OR GITHUB PUSH FAILED</b>",
        "",
        f"<b>Mode:</b> {html.escape(mode_clean)}",
        f"<b>Branch:</b> {html.escape(str(branch or 'main'))}",
        f"<b>Started From:</b> {html.escape(str(commit or 'unknown'))}",
        "<b>Published:</b> No",
        "<b>Action:</b> Open the failed workflow log; previous published data is unchanged.",
    ]
    if run_url:
        safe_url = html.escape(str(run_url), quote=True)
        lines.extend(["", f'<a href="{safe_url}">Open failed GitHub Actions run</a>'])
    return "\n".join(lines)[:4000]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode")
    parser.add_argument("commit")
    parser.add_argument("branch", nargs="?", default="main")
    parser.add_argument("--failure", action="store_true")
    parser.add_argument("--run-url", default="")
    args = parser.parse_args()

    if args.failure:
        message = build_failure_message(
            args.mode,
            args.commit,
            args.branch,
            args.run_url,
        )
    else:
        message = build_message(args.mode, args.commit, args.branch)
    sent = send_telegram_alert(message)
    if sent:
        label = "failure" if args.failure else "success"
        print(f"Telegram {label} notification sent.")
    else:
        print(
            "Telegram notification was not sent; check the configured "
            "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID secrets."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
