#!/usr/bin/env python3
"""Send Telegram success or failure messages for the scanner workflow."""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

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


_BN_DIGITS = str.maketrans("0123456789", "০১২৩৪৫৬৭৮৯")

#: Every Telegram message goes to the owner in Bangla (the owner's request,
#: 2026-10-21): Bangla words, Bangla digits. Commit hashes, links and test
#: names stay as they are - they are identifiers, not prose.
MODE_BN = {
    "channels": "টিভি চ্যানেল",
    "today": "আজকের ম্যাচ",
    "upcoming": "আসন্ন ম্যাচ",
    "upcoming-targeted": "আসন্ন ম্যাচ (নির্দিষ্ট)",
    "movies": "মুভি",
    "events": "খেলার ইভেন্ট",
    "all": "সব",
}


def bn(value: Any) -> str:
    """A number (or any text) with its digits written in Bangla."""
    return str(value).translate(_BN_DIGITS)


def mode_bn(mode: str) -> str:
    return MODE_BN.get(mode, mode)


def time_bn(stamp: str) -> str:
    """An ISO timestamp as Bangladesh time, in Bangla."""
    from datetime import datetime, timedelta, timezone
    try:
        moment = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return bn(stamp)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    local = moment.astimezone(timezone(timedelta(hours=6)))
    return bn(local.strftime("%Y-%m-%d %H:%M")) + " (বাংলাদেশ সময়)"


def held_movie_loss_lines(path: Optional[Path] = None) -> List[str]:
    """Previously live movie streams this publish let go within the no-loss
    gate's allowance - announced, never silent."""
    report = load_json(path or PROJECT_ROOT / "reports" / "movie-source-coverage.json")
    two = report.get("invariant_2") if isinstance(report.get("invariant_2"), dict) else {}
    lost = safe_int(two.get("unexplained_live_loss"))
    invariants = report.get("invariants") if isinstance(report.get("invariants"), dict) else {}
    if not lost or invariants.get("block"):
        return []
    lines = ["", "🧹 <b>%s টি আগের চালু মুভি স্ট্রিম আর পাওয়া যায়নি, তাই শুধু সেগুলো বাদ গেছে; বাকি সব প্রকাশ হয়েছে।</b>" % bn(lost)]
    for item in (two.get("lost_streams") or two.get("lost_sample") or [])[:5]:
        line = str(item.get("inventory_line") or item.get("stream_family") or "")
        lines.append("• " + html.escape(line.split("|")[2] if line.count("|") >= 2 else line[:120]))
    return lines


def _format_duration(seconds: Any) -> str:
    total = safe_int(seconds)
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return bn(f"{hours} ঘণ্টা {minutes} মিনিট {secs} সেকেন্ড")
    if minutes:
        return bn(f"{minutes} মিনিট {secs} সেকেন্ড")
    return bn(f"{secs} সেকেন্ড")


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
    if raw_status == "completed":
        display_status = "সম্পন্ন ✅"
    elif raw_status.startswith("completed"):
        display_status = "সম্পন্ন, কিছু সতর্কতাসহ ⚠️"
    else:
        display_status = raw_status
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
        "channels": "📺 <b>টিভি চ্যানেল স্ক্যান সফল, গিটহাবে আপডেট হয়েছে</b>",
        "today": "⚽ <b>আজকের ম্যাচ স্ক্যান সফল, গিটহাবে আপডেট হয়েছে</b>",
        "upcoming": "🗓️ <b>আসন্ন ম্যাচ স্ক্যান সফল, গিটহাবে আপডেট হয়েছে</b>",
        "movies": "🎬 <b>মুভি স্ক্যান সফল, গিটহাবে আপডেট হয়েছে</b>",
        "all": "✅ <b>সব স্ক্যান সফল, গিটহাবে আপডেট হয়েছে</b>",
    }

    def row(label: str, value: Any) -> str:
        return f"<b>{label}:</b> {bn(value)}"

    lines = [
        titles.get(mode_clean, "✅ <b>স্ক্যান সফল, গিটহাবে আপডেট হয়েছে</b>"),
        "",
        f"<b>ধরন:</b> {html.escape(mode_bn(mode_clean))}",
        f"<b>স্ক্যানের ফল:</b> {html.escape(display_status)}",
        "<b>গিটহাবে পাঠানো:</b> সফল ✅",
        f"<b>ব্রাঞ্চ:</b> {html.escape(branch)}",
        f"<b>কমিট:</b> {html.escape(commit)}",
    ]

    if updated_at:
        lines.append(f"<b>আপডেটের সময়:</b> {html.escape(time_bn(updated_at))}")

    channels_row = row("টিভি চ্যানেল", safe_int(totals.get("channels")))
    today_row = row("আজকের ম্যাচ", safe_int(totals.get("today_match")))
    upcoming_row = row("আসন্ন ম্যাচ", safe_int(totals.get("upcoming")))
    movies_row = row("মুভি", safe_int(totals.get("movies")))
    quarantined_row = row("আটকে রাখা চ্যানেল", safe_int(summary.get("quarantined_channels")))
    low_quality_row = row("কম মানের কারণে বাদ", safe_int(summary.get("rejected_low_quality")))

    if mode_clean == "channels":
        lines.extend([channels_row, quarantined_row, low_quality_row])
    elif mode_clean == "today":
        lines.append(today_row)
    elif mode_clean == "upcoming":
        lines.append(upcoming_row)
    elif mode_clean == "movies":
        lines.extend([movies_row, low_quality_row])
    elif mode_clean == "all":
        lines.extend([channels_row, today_row, upcoming_row, movies_row,
                      quarantined_row, low_quality_row])

    if mode_clean in {"movies", "all"}:
        lines.extend(
            [
                "",
                "🎯 <b>মুভির অবস্থা</b>",
                row("সব জায়গা থেকে চলে", _status_count(status_counts, "verified_global", "verified_bd", "verified")),
                row("প্রক্সি দিয়ে চলে", _status_count(status_counts, "verified_proxy")),
                row("আগের ভালো লিংক রাখা হয়েছে", _status_count(status_counts, "stale_last_good")),
                row("দেশভিত্তিক বাধা, যাচাই বাকি", _status_count(status_counts, "geo_pending", "bd_protected_pending")),
                row("আবার যাচাই করা হবে", _status_count(status_counts, "retryable_pending")),
                row("সার্ভার পরে যাচাই হবে", _status_count(status_counts, "host_deferred")),
                row("৪০৪ - আটকে রাখা", safe_int(summary.get("quarantined_movies"))),
            ]
        )

    if mode_clean in {"channels", "movies", "all"}:
        lines.extend(
            [
                "",
                "⚠️ <b>যাচাইয়ের বিস্তারিত</b>",
                row("সোর্স আনতে সতর্কতা", warnings["source_fetch"]),
                row("না চলা স্ট্রিম", failed_streams),
                row("আউটপুট নিরাপত্তা সতর্কতা", output_safety_count),
                (
                    "<b>সময়সীমা শেষ হয়েছে:</b> "
                    + ("হ্যাঁ ⚠️" if performance.get("budget_exhausted") is True else "না ✅")
                ),
                f"<b>যাচাইয়ে সময় লেগেছে:</b> {_format_duration(performance.get('elapsed_seconds'))}",
                row("সরাসরি যাচাই", safe_int(performance.get("global_network_checked"))),
                row("বিডি/প্রক্সি দিয়ে যাচাই", safe_int(performance.get("bd_proxy_submitted"))),
                row("যাচাই ছাড়া রাখা (সম্প্রতি ভালো ছিল)", safe_int(performance.get("adaptive_skipped"))),
            ]
        )

    if summary.get("movie_output_preserved") is True:
        lines.extend(
            [
                "",
                "🛡️ <b>নতুন মুভি তালিকায় অনেক কিছু হারিয়ে গেছে, তাই সাইটে আগের তালিকাই রাখা হয়েছে।</b>",
            ]
        )
    elif mode_clean in {"movies", "all"}:
        lines.extend(held_movie_loss_lines())

    return "\n".join(lines)[:4000]


def read_details(path: str, limit: int = 8) -> List[str]:
    """The test gate's findings, one per line: `BLOCK <test> - <reason>` for
    a code fault that stopped the scan, `AUDIT <test> - <reason>` for a fact
    about today's data that did not. Missing or unreadable is simply none -
    an alert must never fail because its garnish is absent."""
    if not path:
        return []
    try:
        with open(path, encoding="utf-8") as handle:
            lines = [line.strip() for line in handle if line.strip()]
    except OSError:
        return []
    return lines[:limit]


def quarantine_lines(limit: int = 8) -> List[str]:
    """Items the build held back this run (scripts/quarantine-bad-items.py),
    so a card that disappeared is announced rather than noticed."""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "reports", "item-quarantine.json")
    try:
        with open(path, encoding="utf-8") as handle:
            report = json.load(handle)
    except (OSError, ValueError):
        return []
    if not report.get("applied"):
        return []
    lines = []
    for entry in (report.get("removed") or [])[:limit]:
        reason = "; ".join(entry.get("reasons") or [])
        lines.append("QUARANTINE %s %s - %s" % (
            SURFACE_BN.get(str(entry.get("surface")), entry.get("surface")),
            entry.get("name") or entry.get("id"), reason))
    return lines


SURFACE_BN = {"channels": "চ্যানেল", "movies": "মুভি", "events": "ম্যাচ"}

#: The machine tags read_details/quarantine_lines put first, in Bangla.
_DETAIL_TAG_BN = {
    "BLOCK": "কোডের টেস্ট ব্যর্থ:",
    "AUDIT": "ডেটা যাচাইয়ে সমস্যা (স্ক্যান থামেনি):",
    "QUARANTINE": "আটকে রাখা হয়েছে:",
}


def _detail_line_bn(line: str) -> str:
    tag, _, rest = line.partition(" ")
    if tag in _DETAIL_TAG_BN:
        return _DETAIL_TAG_BN[tag] + " " + rest
    return line


def _detail_lines(details: List[str], heading: str) -> List[str]:
    if not details:
        return []
    out = ["", heading]
    for line in details:
        out.append("• " + html.escape(_detail_line_bn(line)[:300]))
    return out


def build_failure_message(
    mode: str,
    commit: str,
    branch: str,
    run_url: str,
    consecutive: int = 0,
    details: Optional[List[str]] = None,
) -> str:
    mode_clean = str(mode or "unknown").strip().lower()
    lines = [
        "❌ <b>ক্লিক টিভি স্ক্যান বা গিটহাবে আপডেট ব্যর্থ হয়েছে</b>",
        "",
        f"<b>ধরন:</b> {html.escape(mode_bn(mode_clean))}",
        f"<b>ব্রাঞ্চ:</b> {html.escape(str(branch or 'main'))}",
        f"<b>যে কমিট থেকে শুরু:</b> {html.escape(str(commit or 'অজানা'))}",
        "<b>সাইটে প্রকাশ হয়েছে:</b> না",
        "<b>করণীয়:</b> ব্যর্থ রানের লগ খুলে দেখুন; সাইটে আগের ডেটাই আছে, কিছু বদলায়নি।",
    ]
    if consecutive > 1:
        lines.insert(2, f"<b>পরপর ব্যর্থ রান:</b> {bn(int(consecutive))}")
    lines.extend(_detail_lines(
        [line for line in (details or []) if line.startswith("BLOCK")],
        "🧪 <b>যে কারণে থেমেছে</b>"))
    if run_url:
        safe_url = html.escape(str(run_url), quote=True)
        lines.extend(["", f'<a href="{safe_url}">ব্যর্থ গিটহাব রানটি খুলুন</a>'])
    return "\n".join(lines)[:4000]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode")
    parser.add_argument("commit")
    parser.add_argument("branch", nargs="?", default="main")
    parser.add_argument("--failure", action="store_true")
    parser.add_argument("--run-url", default="")
    parser.add_argument("--details-file", default="")
    parser.add_argument("--consecutive", type=int, default=0)
    args = parser.parse_args()
    details = read_details(args.details_file)

    if args.failure:
        message = build_failure_message(
            args.mode,
            args.commit,
            args.branch,
            args.run_url,
            consecutive=args.consecutive,
            details=details,
        )
    else:
        message = build_message(args.mode, args.commit, args.branch)
        audits = [line for line in details if line.startswith("AUDIT")]
        audits.extend(quarantine_lines())
        if audits:
            message = (message + "\n".join(_detail_lines(
                audits, "⚠️ <b>যেগুলো আটকে রাখা বা চিহ্নিত হয়েছে (বাকি সব প্রকাশ হয়েছে)</b>")))[:4000]
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
