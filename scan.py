"""
Main Scanner CLI Controller

Orchestrates the complete Live Signal pipeline:

Source Loader -> Normalizer -> Global Verifier -> BD Protection Verifier
-> TV / Movies / Events processors -> Atomic Output Publisher

Usage:
    python scan.py channels
    python scan.py today
    python scan.py upcoming
    python scan.py movies
    python scan.py all
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple
from urllib.parse import urlsplit, urlunsplit

from scanner.fast_pipeline import run_fast_verification_pipeline
from scanner.normalizer import normalize_all_candidates
from scanner.planner import plan_candidates
from scanner.source_loader import collect_candidates
from scanner.security import redact_sensitive_text
from scanner.targeted_scan import (
    DEFAULT_WINDOW_MINUTES as TARGETED_WINDOW_MINUTES,
    TargetPlan,
    load_ledger,
    plan_targeted_upcoming_scan,
    record_outcome,
    save_ledger,
)


PROJECT_ROOT = Path(__file__).resolve().parent
CANDIDATES_PATH = PROJECT_ROOT / "working" / "candidates.json"
SCAN_PROGRESS_PATH = PROJECT_ROOT / "working" / "scan-progress.json"
SOURCE_HEALTH_PATH = PROJECT_ROOT / "state" / "source-health.json"

SUCCESS_SOURCE_STATUSES = {
    "success",
    "success_empty",
    "disabled",
}

# Requirement 4. The five-minute trigger that only chases the links of fixtures
# about to start. Spelled both ways because a workflow input and a CLI argument
# have historically used different separators.
TARGETED_UPCOMING_MODES = {
    "upcoming-targeted",
    "upcoming_targeted",
}


def _force_utf8_console() -> None:
    """
    Progress messages contain emoji. A Windows console defaults to the legacy
    cp1252 code page, so the very first `print("🚀 ...")` raises
    UnicodeEncodeError and the whole scan dies before a single source is read -
    while the same code runs fine on the Linux GitHub Actions runner. Switch
    stdout/stderr to UTF-8 so local PC runs behave like CI, and fall back to
    replacing unencodable characters if the stream cannot be reconfigured at
    all (it has been swapped for a plain buffer by a test harness, say).
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_project_root() -> None:
    """
    Scanner modules use repository-relative paths. Always execute them from the
    repository root even when scan.py is launched from another directory.
    """
    os.chdir(PROJECT_ROOT)


def _atomic_write_json(file_path: str | Path, data: Dict[str, Any]) -> None:
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(
        f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp"
    )

    try:
        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass


def _write_scan_progress(mode: str, stage: str, **details: Any) -> None:
    payload: Dict[str, Any] = {
        "schema_version": 1,
        "updated_at": _utc_now(),
        "mode": str(mode),
        "stage": str(stage),
    }
    payload.update(details)
    _atomic_write_json(SCAN_PROGRESS_PATH, payload)



def _targeted_window_minutes() -> int:
    """How long before kickoff the targeted trigger starts hunting for a link.

    Read from config/settings.json rather than hard-coded, because it is a
    scheduling decision about the owner's site and not a property of the code.
    The trigger runs every five minutes, so the value is really a number of
    attempts before the whistle: 15 gives three, 10 gives two.

    Not to be confused with `upcoming_past_grace_minutes`, which is the other
    side of kickoff - how long a fixture may still sit on Upcoming afterwards
    without a link.
    """
    try:
        with open(PROJECT_ROOT / "config" / "settings.json", "r", encoding="utf-8") as handle:
            events_settings = (json.load(handle).get("events") or {})
        value = int(events_settings.get("targeted_window_minutes") or 0)
    except (OSError, ValueError, TypeError, AttributeError):
        return TARGETED_WINDOW_MINUTES
    if value <= 0 or value > 240:
        return TARGETED_WINDOW_MINUTES
    return value

def _load_required_json(file_path: str | Path) -> Dict[str, Any]:
    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(
            f"Required JSON file not found: {path}"
        )

    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(
            f"Required JSON file could not be read: {path}: {error}"
        ) from error

    if not isinstance(data, dict):
        raise ValueError(
            f"Required JSON root must be an object: {path}"
        )

    return data


def _parse_iso_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()

    if not text:
        return None

    try:
        parsed = datetime.fromisoformat(
            text.replace("Z", "+00:00")
        )
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)


def _current_source_records(
    started_at: datetime,
    active_pipelines: List[str],
) -> List[Tuple[str, Dict[str, Any]]]:
    """
    Return only source-health records updated during this run and belonging to
    the active mode. Historical merged records must not leak into this scan.
    """
    if not SOURCE_HEALTH_PATH.exists():
        return []

    try:
        health = _load_required_json(SOURCE_HEALTH_PATH)
    except (FileNotFoundError, ValueError):
        return []

    records = health.get("sources")

    if not isinstance(records, dict):
        return []

    active_set = {
        str(value or "").strip().lower()
        for value in active_pipelines
        if str(value or "").strip()
    }

    current: List[Tuple[str, Dict[str, Any]]] = []

    for source_id, raw_record in records.items():
        if not isinstance(raw_record, dict):
            continue

        pipeline = str(
            raw_record.get("pipeline") or ""
        ).strip().lower()

        if active_set and pipeline and pipeline not in active_set:
            continue

        checked_at = _parse_iso_datetime(
            raw_record.get("last_scan")
            or raw_record.get("last_failure")
        )

        if checked_at is None or checked_at < started_at:
            continue

        current.append(
            (str(source_id), raw_record)
        )

    return current


def _guard_empty_collection(
    candidate_payload: Dict[str, Any],
    started_at: datetime,
) -> None:
    """
    Allow a legitimate empty result only when at least one current active source
    completed successfully. Abort when every active source failed so published
    data is not accidentally wiped.
    """
    raw_items = candidate_payload.get("items")

    if not isinstance(raw_items, list) or raw_items:
        return

    active_pipelines = candidate_payload.get(
        "active_pipelines"
    )

    if not isinstance(active_pipelines, list):
        active_pipelines = []

    records = _current_source_records(
        started_at,
        active_pipelines,
    )

    successful = any(
        str(record.get("status") or "").strip().lower()
        in {"success", "success_empty"}
        for _, record in records
    )

    if not successful:
        raise RuntimeError(
            "Source collection returned zero candidates and no active source "
            "completed successfully; publishing was stopped to preserve "
            "existing channel, movie and event data"
        )


def _normalize_candidate_payload(
    candidate_payload: Dict[str, Any],
    targeted_filter: Any = None,
) -> Dict[str, Any]:
    """
    Normalize source-loader output and replace working/candidates.json.

    The verifier reads this same file, so raw candidates must never be passed
    directly to the verifier.
    """
    raw_items = candidate_payload.get("items")

    if not isinstance(raw_items, list):
        raise ValueError(
            "Source-loader result is invalid or missing list field 'items'"
        )

    normalized_items = normalize_all_candidates(raw_items)

    if not isinstance(normalized_items, list):
        raise ValueError(
            "normalize_all_candidates() must return a list"
        )

    if raw_items and not normalized_items:
        raise RuntimeError(
            "Normalizer rejected every collected candidate; verification "
            "was stopped to prevent an accidental empty publish"
        )

    mode = str(
        candidate_payload.get("mode") or "all"
    ).strip().lower()

    planned_items, planning_summary = plan_candidates(
        normalized_items,
        mode=mode,
        targeted_filter=targeted_filter,
    )

    preserve_existing_output = False
    preservation_reason = ""

    if not planned_items:
        if mode in {"today", "upcoming"} | TARGETED_UPCOMING_MODES:
            # A sports-event feed can legitimately have no currently publishable
            # fixture after date/category planning.  This is not a scanner
            # failure: keep the last known-good event JSON and allow an `all`
            # run to continue to its remaining pipelines and Git push.
            preserve_existing_output = True
            preservation_reason = (
                "No publishable event candidates remained after collection, "
                "normalization and planning"
            )
        else:
            raise RuntimeError(
                "Pre-verification planning produced zero candidates; "
                "verification was stopped to preserve existing published data"
            )

    normalized_payload = dict(candidate_payload)

    normalized_payload["raw_candidate_count"] = len(
        raw_items
    )
    normalized_payload["normalized_candidate_count"] = len(
        normalized_items
    )
    normalized_payload["planned_candidate_count"] = len(
        planned_items
    )
    normalized_payload["total_candidates"] = len(
        planned_items
    )
    normalized_payload["normalized_at"] = _utc_now()
    normalized_payload["planning"] = planning_summary
    normalized_payload["items"] = planned_items
    normalized_payload["preserve_existing_output"] = preserve_existing_output
    if preservation_reason:
        normalized_payload["preservation_reason"] = preservation_reason

    _atomic_write_json(
        CANDIDATES_PATH,
        normalized_payload,
    )

    return normalized_payload


def _complete_preserved_event_run(
    mode: str,
    normalized: Dict[str, Any],
    run_started_at: datetime,
) -> Dict[str, Any]:
    """Finish a valid zero-event run without replacing published event data."""
    manifest_path = PROJECT_ROOT / "data" / "manifest.json"
    manifest = _load_required_json(manifest_path)
    totals = {
        "channels": sum(
            int((entry or {}).get("count") or 0)
            for entry in (manifest.get("channels") or {}).values()
            if isinstance(entry, dict)
        ),
        "movies": sum(
            int((entry or {}).get("count") or 0)
            for entry in (manifest.get("movies") or {}).values()
            if isinstance(entry, dict)
        ),
        "today_match": int(
            ((manifest.get("today_match") or {}).get("count") or 0)
        ),
        "upcoming": int(
            ((manifest.get("upcoming") or {}).get("count") or 0)
        ),
    }
    summary = {
        "last_scan": _utc_now(),
        "status": "completed_preserved",
        "mode": mode,
        "preserved_existing_output": True,
        "preservation_reason": str(
            normalized.get("preservation_reason")
            or "No publishable event candidates were available"
        ),
        "raw_candidate_count": int(normalized.get("raw_candidate_count") or 0),
        "normalized_candidate_count": int(
            normalized.get("normalized_candidate_count") or 0
        ),
        "planned_candidate_count": 0,
        "source_errors": 0,
        "totals": totals,
        "manifest_summary": manifest,
    }
    reports_root = PROJECT_ROOT / "reports"
    _atomic_write_json(reports_root / "scan-summary.json", summary)
    _atomic_write_json(reports_root / f"scan-summary-{mode}.json", summary)
    _write_scan_progress(
        mode,
        "completed_preserved",
        started_at=run_started_at.isoformat(),
        summary_status=summary["status"],
        preserved_existing_output=True,
    )
    print("\n==================================================")
    print("SCAN COMPLETED - EXISTING EVENT DATA PRESERVED")
    print(f"   Mode: {mode}")
    print(f"   Reason: {summary['preservation_reason']}")
    print("   GitHub validation and auto-push may continue safely.")
    print("==================================================")
    return summary


def _complete_untargeted_run(
    mode: str,
    plan: TargetPlan,
    run_started_at: datetime,
) -> Dict[str, Any]:
    """Finish a targeted trigger that had no fixture to chase.

    Requirement 4: no fixture inside the -15 minute window means no source is
    fetched, nothing is verified and nothing is published. The existing
    Today/Upcoming snapshot is left exactly as it is.
    """
    manifest_path = PROJECT_ROOT / "data" / "manifest.json"
    manifest = _load_required_json(manifest_path)
    summary = {
        "last_scan": _utc_now(),
        "status": "completed_no_target",
        "mode": mode,
        "preserved_existing_output": True,
        "preservation_reason": (
            "No Upcoming fixture is inside the "
            f"{plan.window_minutes}-minute targeted window, or every fixture in "
            "it has already had its one targeted scan"
        ),
        "targeted_scan": plan.summary(),
        "raw_candidate_count": 0,
        "normalized_candidate_count": 0,
        "planned_candidate_count": 0,
        "source_errors": 0,
        "totals": {
            "channels": sum(
                int((entry or {}).get("count") or 0)
                for entry in (manifest.get("channels") or {}).values()
                if isinstance(entry, dict)
            ),
            "movies": sum(
                int((entry or {}).get("count") or 0)
                for entry in (manifest.get("movies") or {}).values()
                if isinstance(entry, dict)
            ),
            "today_match": int(((manifest.get("today_match") or {}).get("count") or 0)),
            "upcoming": int(((manifest.get("upcoming") or {}).get("count") or 0)),
        },
        "manifest_summary": manifest,
    }
    # Nothing to chase does not mean nothing to correct. A fixture promoted to
    # Today Match by an earlier trigger can still be sitting on Upcoming, and
    # with no fixture inside the window this path is the only one that runs for
    # long stretches - so the contradiction would stay on the site until some
    # other scan happened along.
    #
    # This keeps the promise made below it. No source is fetched, nothing is
    # verified and no card is replaced; it only removes an Upcoming copy of a
    # match the published Today Match already carries as live.
    tidied = _drop_upcoming_already_live()
    if tidied:
        summary["upcoming_cards_already_live_removed"] = tidied
        summary["totals"]["upcoming"] = max(
            0, int(summary["totals"].get("upcoming") or 0) - tidied
        )

    reports_root = PROJECT_ROOT / "reports"
    _atomic_write_json(reports_root / "scan-summary.json", summary)
    _atomic_write_json(reports_root / f"scan-summary-{mode}.json", summary)
    _write_scan_progress(
        mode,
        "completed_no_target",
        started_at=run_started_at.isoformat(),
        summary_status=summary["status"],
        targets=len(plan.targets),
        already_attempted_skipped=plan.already_attempted,
        outside_window_skipped=plan.outside_window,
    )
    print("\n==================================================")
    print("TARGETED SCAN SKIPPED - NOTHING TO CHASE")
    print(f"   Mode: {mode}")
    print(f"   Window: -{plan.window_minutes} minutes")
    print(
        f"   Fixtures considered: {plan.considered}; "
        f"outside window: {plan.outside_window}; "
        f"already scanned once: {plan.already_attempted}"
    )
    print("   No source was fetched and no published data was replaced.")
    print("==================================================")
    return summary


def run_collection_and_normalization(
    mode: str,
    started_at: datetime | None = None,
    targeted_filter: Any = None,
) -> Dict[str, Any]:
    """
    Collect current sources, validate collection health, then normalize.
    """
    _ensure_project_root()

    run_started_at = (
        started_at
        or datetime.now(timezone.utc)
    )

    collected = collect_candidates(mode=mode)

    if not isinstance(collected, dict):
        raise ValueError(
            "collect_candidates() must return a dictionary"
        )

    _guard_empty_collection(
        collected,
        run_started_at,
    )

    return _normalize_candidate_payload(
        collected,
        targeted_filter=targeted_filter,
    )


def normalize_working_candidates() -> Dict[str, Any]:
    """
    Normalize the existing working/candidates.json without fetching again.
    """
    _ensure_project_root()

    payload = _load_required_json(
        CANDIDATES_PATH
    )

    return _normalize_candidate_payload(
        payload
    )


def _source_errors_since(
    started_at: datetime,
    active_pipelines: List[str],
) -> List[Dict[str, Any]]:
    errors: List[Dict[str, Any]] = []

    for source_id, record in _current_source_records(
        started_at,
        active_pipelines,
    ):
        status = str(
            record.get("status") or ""
        ).strip().lower()

        if not status or status in SUCCESS_SOURCE_STATUSES:
            continue

        errors.append(
            {
                "type": "source_fetch_error",
                "source_id": source_id,
                "source_name": str(
                    record.get("source_name")
                    or source_id
                ),
                "pipeline": str(
                    record.get("pipeline")
                    or ""
                ),
                "status": status,
                "http_status": record.get(
                    "http_status",
                    0,
                ),
                "attempts": record.get(
                    "attempts",
                    0,
                ),
                "error": _sanitize_error_text(
                    record.get("error")
                    or "Source collection failed"
                ),
                "timestamp": str(
                    record.get("last_scan")
                    or record.get("last_failure")
                    or _utc_now()
                ),
            }
        )

    return errors


def _safe_report_url(raw_url: Any) -> str:
    clean_url = str(
        raw_url or ""
    ).split("|", 1)[0].strip()

    if not clean_url:
        return ""

    try:
        parts = urlsplit(clean_url)

        return urlunsplit(
            (
                parts.scheme,
                parts.netloc,
                parts.path,
                "",
                "",
            )
        )
    except Exception:
        return ""


def _sanitize_error_text(value: Any) -> str:
    """
    Remove query strings from any URLs embedded inside an error message.
    """
    text = str(value or "")

    def replace_url(
        match: re.Match[str],
    ) -> str:
        raw = match.group(0).rstrip(
            ".,);]}>"
        )

        suffix = match.group(0)[len(raw):]

        return (
            _safe_report_url(raw)
            + suffix
        )

    return re.sub(
        r"https?://[^\s\"'<>]+",
        replace_url,
        text,
    )


#: What a route failure is actually evidence of. Without this every row in
#: reports/source-errors-*.json reads the same, and they are not the same:
#: measured on 2026-08-29, 661 of the 972 today-mode route failures were HTTP
#: 403 from the runner's US datacentre egress, and the SonyLIV URL recorded
#: "HTTP 403: Forbidden" there answered HTTP 200 with a real live manifest from
#: a Bangladeshi connection minutes later. Reading that row as a dead stream is
#: how a working source ends up looking like a broken one.
_VANTAGE_SHAPED_STATUSES = frozenset({401, 403, 407, 451})
_TRANSIENT_STATUSES = frozenset({408, 425, 429})
_PERMANENT_STATUSES = frozenset({400, 404, 410})
_TRANSIENT_ERROR_WORDS = ("timed out", "timeout", "connection", "reset",
                          "temporarily", "network is unreachable",
                          "dns", "getaddrinfo", "ssl", "certificate")
#: A 200 that is not a stream. Separate from the rest because it is the one
#: class that says the request succeeded and the content is still unusable -
#: 292 of one source's routes answered a non-standard HTTP 567 in the same run,
#: which is a server saying no, while these are a server saying yes and sending
#: something that is not media.
_UNPLAYABLE_BODY_WORDS = ("does not contain #extm3u", "no playable segment",
                          "stream url is empty", "not a playlist",
                          "empty response")


def _failure_class(
    item: Dict[str, Any],
) -> str:
    """Whether a failure is about the stream, the asker, or the moment.

    Five values, and the distinction each one carries:

      permanent        400/404/410 - the file is not there, from anywhere.
      vantage_shaped   401/403/407/451 - this egress may not have it. Says
                       nothing about a viewer on a different network.
      transient        408/425/429 and every 5xx, plus timeouts, resets, DNS
                       and TLS failures - the asker did not get through this
                       time. Any 5xx counts, including the non-standard ones a
                       CDN invents: 292 routes on one source answered HTTP 567
                       in a single run.
      unplayable_body  HTTP 200 carrying something that is not media.
      unknown          anything else, including a bare error with no status.
    """
    try:
        status = int(item.get("http_status") or 0)
    except (TypeError, ValueError):
        status = 0

    text = str(
        item.get("verification_error")
        or item.get("error_reason")
        or ""
    ).casefold()

    if status == 200 and any(word in text for word in _UNPLAYABLE_BODY_WORDS):
        return "unplayable_body"
    if status in _PERMANENT_STATUSES:
        return "permanent"
    if status in _VANTAGE_SHAPED_STATUSES:
        return "vantage_shaped"
    if status in _TRANSIENT_STATUSES or 500 <= status <= 599:
        return "transient"

    if not text:
        return ""
    if any(word in text for word in _UNPLAYABLE_BODY_WORDS):
        return "unplayable_body"
    if any(word in text for word in _TRANSIENT_ERROR_WORDS):
        return "transient"
    return "unknown"


def _safe_report_item(
    item: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Keep reports useful without publishing signed queries, cookies,
    authorization headers, DRM secrets, or playback credentials.
    """
    safe: Dict[str, Any] = {}

    for field_name in (
        "id",
        "name",
        "source_id",
        "source_pipeline",
        "category",
        "verification_status",
        "verification_mode",
        "verification_note",
        "quarantine_reason",
        "quarantine_original_category",
        "http_status",
        "resolution",
        "resolution_height",
    ):
        value = item.get(field_name)

        if value not in (
            None,
            "",
            [],
            {},
        ):
            safe[field_name] = value

    error_value = (
        item.get("verification_error")
        or item.get("error_reason")
        or ""
    )

    if error_value:
        safe["verification_error"] = (
            _sanitize_error_text(
                error_value
            )
        )

    safe_url = _safe_report_url(
        item.get("url")
    )

    if safe_url:
        safe["url"] = safe_url

    failure_class = _failure_class(item)

    if failure_class:
        safe["failure_class"] = failure_class

    return safe


def _verification_report_items(
    bd_summary: Dict[str, Any],
) -> Tuple[
    List[Dict[str, Any]],
    List[Dict[str, Any]],
    List[Dict[str, Any]],
]:
    results = bd_summary.get("results")

    if not isinstance(results, list):
        return [], [], []

    rejected: List[Dict[str, Any]] = []
    quarantined: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []

    for raw_item in results:
        if not isinstance(raw_item, dict):
            continue

        status = str(
            raw_item.get(
                "verification_status"
            )
            or ""
        ).strip().lower()

        if status == "rejected_low_quality":
            rejected.append(
                _safe_report_item(
                    raw_item
                )
            )

        elif status == "quarantine":
            quarantined.append(
                _safe_report_item(
                    raw_item
                )
            )

        elif status in {
            "failed",
            "failed_bd",
        }:
            safe_item = _safe_report_item(
                raw_item
            )

            if safe_item.get(
                "verification_error"
            ):
                safe_item["type"] = (
                    "stream_verification_error"
                )

                failed.append(
                    safe_item
                )

    return (
        rejected,
        quarantined,
        failed,
    )


def _sanitize_channel_quarantine(
    channels_data: Dict[str, Any] | None,
) -> None:
    """
    Replace complete playback cards inside quarantine reports with safe copies.
    """
    if not isinstance(
        channels_data,
        dict,
    ):
        return

    quarantine = channels_data.get(
        "quarantine"
    )

    if not isinstance(
        quarantine,
        list,
    ):
        return

    channels_data["quarantine"] = [
        _safe_report_item(item)
        for item in quarantine
        if isinstance(item, dict)
    ]



def _drop_upcoming_already_live() -> int:
    """Remove any Upcoming card the published Today Match already has as live.

    Reads and rewrites only the two published event files - the flat pair and
    every snapshot slot, since publishing switches slots by one os.replace() of
    the manifest and the flat copy can be spotless while the live one is not.

    Returns how many cards were removed. Never raises: this is tidying, and a
    scan must not fail because of it.
    """
    import glob as _glob

    removed_total = 0
    try:
        from scanner.events import _drop_upcoming_cards_already_live
    except ImportError:
        return 0

    pairs = [(PROJECT_ROOT / "data" / "today-match.json",
              PROJECT_ROOT / "data" / "upcoming.json")]
    for today_path in sorted(_glob.glob(
        str(PROJECT_ROOT / "data" / "snapshots" / "*" / "today-match.json")
    )):
        pairs.append((Path(today_path),
                      Path(today_path).with_name("upcoming.json")))

    for today_path, upcoming_path in pairs:
        try:
            if not today_path.is_file() or not upcoming_path.is_file():
                continue
            with open(today_path, "r", encoding="utf-8") as handle:
                today = json.load(handle)
            with open(upcoming_path, "r", encoding="utf-8") as handle:
                upcoming = json.load(handle)
            duplicates = _drop_upcoming_cards_already_live(
                today.get("items") or [], upcoming.get("items") or []
            )
            if not duplicates:
                continue
            doomed = {id(card) for card in duplicates}
            upcoming["items"] = [
                card for card in (upcoming.get("items") or [])
                if id(card) not in doomed
            ]
            upcoming["count"] = len(upcoming["items"])
            _atomic_write_json(upcoming_path, upcoming)
            removed_total += len(duplicates)
        except (OSError, ValueError, TypeError):
            continue
    return removed_total


def _process_events_for_mode(
    mode: str,
    targeted_plan: TargetPlan | None = None,
) -> Dict[str, Dict[str, Any]]:
    """
    Process Today Match and Upcoming Match separately according to mode.
    """
    try:
        from scanner.events import process_events
    except ImportError as error:
        raise RuntimeError(
            "scanner/events.py is required for all/today/upcoming mode"
        ) from error

    # Requirement 4: the pre-kickoff targeted upcoming scan. The plan names the
    # individual fixtures this trigger may touch; every other Upcoming card is
    # republished exactly as it already stands.
    targeted_window = (
        targeted_plan.window_minutes
        if targeted_plan is not None
        else (_targeted_window_minutes() if mode in TARGETED_UPCOMING_MODES else 0)
    )
    event_result = process_events(
        targeted_window_minutes=targeted_window,
        targeted_keys=(targeted_plan.targets if targeted_plan is not None else None),
    )

    if not isinstance(event_result, dict):
        raise ValueError(
            "process_events() must return a dictionary"
        )

    # Today Match and Upcoming are no longer decided by which source file an
    # entry came from - a live fixture is Today Match wherever it was
    # configured, and a not-started one is Upcoming. Both modes therefore
    # collect both event source groups and publish both surfaces; restricting
    # a mode to one output would silently discard every card that the schedule
    # status routed to the other side.
    if mode in {"today", "today_match", "upcoming"} | TARGETED_UPCOMING_MODES:
        published: Dict[str, Dict[str, Any]] = {}
        for key in ("today_match", "upcoming"):
            payload = event_result.get(key)
            if not isinstance(payload, dict):
                raise ValueError(
                    f"process_events() did not return '{key}' payload"
                )
            published[key] = payload
        return published

    return event_result


def run_pipeline(
    mode: str = "all",
) -> Dict[str, Any]:
    """
    Run the complete scanner pipeline for one supported mode.
    """
    _ensure_project_root()

    mode_clean = str(
        mode or "all"
    ).strip().lower()

    pipeline_modes = {
        "channels",
        "today",
        "upcoming",
        # Requirement 4: the same upcoming pipeline, but only the fixtures about
        # to start are treated as scan targets.
        "upcoming-targeted",
        "movies",
        "all",
    }

    if mode_clean not in pipeline_modes:
        raise ValueError(
            f"Unsupported pipeline mode: {mode_clean}"
        )

    if mode_clean == "all":
        settings = _load_required_json(PROJECT_ROOT / "config" / "settings.json")
        pipeline_settings = settings.get("pipeline")
        if not isinstance(pipeline_settings, dict):
            pipeline_settings = {}
        if bool(pipeline_settings.get("sequential_full_scan", True)):
            ordered_modes = ("upcoming", "today", "channels", "movies")
            summaries: Dict[str, Any] = {}
            started = _utc_now()
            for index, child_mode in enumerate(ordered_modes, start=1):
                _write_scan_progress(
                    "all",
                    "pipeline_start",
                    current_pipeline=child_mode,
                    pipeline_index=index,
                    pipeline_total=len(ordered_modes),
                    started_at=started,
                )
                summaries[child_mode] = run_pipeline(child_mode)
            result = {
                "status": "completed",
                "mode": "all",
                "execution": "sequential",
                "pipeline_order": list(ordered_modes),
                "pipelines": summaries,
            }
            _write_scan_progress(
                "all",
                "completed",
                pipeline_index=len(ordered_modes),
                pipeline_total=len(ordered_modes),
                started_at=started,
            )
            return result

    print(
        "=================================================="
    )
    print(
        "🚀 LIVE SIGNAL SCANNER - STARTING MODE: "
        f"{mode_clean.upper()}"
    )
    print(
        "=================================================="
    )

    run_started_at = datetime.now(
        timezone.utc
    )
    # Requirement 4, corrected. The targeting decision is taken BEFORE anything
    # is fetched, from local files only: the published Upcoming cards and the
    # ledger of fixtures already resolved. A trigger with no target does no
    # source work at all, which is what makes a five-minute cron affordable and
    # what stops the same fixture being re-scanned every five minutes.
    targeted_plan: TargetPlan | None = None
    if mode_clean in TARGETED_UPCOMING_MODES:
        targeted_plan = plan_targeted_upcoming_scan(
            data_dir=PROJECT_ROOT / "data",
            fixture_path=PROJECT_ROOT / "config" / "event-fixtures.json",
            state_path=PROJECT_ROOT / "state" / "upcoming-targeting.json",
            now=run_started_at,
            window_minutes=_targeted_window_minutes(),
        )
        print(
            f"   Targeted window: -{targeted_plan.window_minutes} minutes; "
            f"targets={len(targeted_plan.targets)}, "
            f"already scanned once={targeted_plan.already_attempted}, "
            f"outside window={targeted_plan.outside_window}"
        )
        for name in targeted_plan.target_names:
            print(f"      target: {name}")
        if not targeted_plan.should_scan:
            return _complete_untargeted_run(
                mode_clean,
                targeted_plan,
                run_started_at,
            )

    _write_scan_progress(mode_clean, "source_collection", started_at=run_started_at.isoformat())

    print(
        "\n[Step 1a/5] Fetching sources and detecting formats..."
    )

    normalized = run_collection_and_normalization(
        mode_clean,
        started_at=run_started_at,
        targeted_filter=(
            targeted_plan.accepts if targeted_plan is not None else None
        ),
    )

    if normalized.get("preserve_existing_output") is True:
        return _complete_preserved_event_run(
            mode_clean,
            normalized,
            run_started_at,
        )

    _write_scan_progress(
        mode_clean,
        "verification",
        started_at=run_started_at.isoformat(),
        raw_candidates=normalized.get("raw_candidate_count", 0),
        planned_candidates=normalized.get("planned_candidate_count", 0),
    )

    print(
        "\n[Step 1b/5] Normalization completed..."
    )

    planning = normalized.get(
        "planning"
    )

    initial_wave = 0

    if isinstance(planning, dict):
        initial_wave = int(
            planning.get(
                "initial_wave_candidates",
                0,
            )
            or 0
        )

    print(
        "   Candidates: "
        f"{normalized.get('raw_candidate_count', 0)} raw -> "
        f"{normalized.get('normalized_candidate_count', 0)} normalized -> "
        f"{normalized.get('planned_candidate_count', 0)} candidate pool"
    )

    if initial_wave:
        print(
            "   Adaptive first wave: "
            f"{initial_wave} candidates; "
            "later candidates run only when needed"
        )

    if isinstance(planning, dict):
        dropped = planning.get(
            "dropped"
        )

        if isinstance(dropped, dict):
            print(
                "   Planner reductions: "
                f"exact duplicates={dropped.get('exact_duplicates', 0)}, "
                f"unknown TV={dropped.get('unknown_tv_category', 0)}, "
                f"per-item cap={dropped.get('per_item_cap', 0)}, "
                f"global cap={dropped.get('global_cap', 0)}"
            )

        rerouted = planning.get(
            "rerouted_counts"
        )

        if (
            isinstance(rerouted, dict)
            and rerouted
        ):
            print(
                "   Content routing corrections: "
                + ", ".join(
                    f"{key}={value}"
                    for key, value in sorted(
                        rerouted.items()
                    )
                )
            )

    source_errors = _source_errors_since(
        run_started_at,
        list(
            normalized.get(
                "active_pipelines"
            )
            or []
        ),
    )

    print(
        "\n[Steps 2+3/5] Running adaptive Global + BD "
        "verification pipeline..."
    )

    print(
        "   Completed Global results enter BD verification immediately "
        "while remaining Global checks continue."
    )

    bd_summary = run_fast_verification_pipeline()
    _write_scan_progress(
        mode_clean,
        "processing",
        started_at=run_started_at.isoformat(),
        verified_candidates=bd_summary.get("total_processed", bd_summary.get("total_candidates", 0)),
        status_counts=bd_summary.get("status_counts", {}),
    )

    (
        rejected_items,
        verifier_quarantine,
        stream_errors,
    ) = _verification_report_items(
        bd_summary
    )

    source_errors.extend(
        stream_errors
    )

    channels_data = None
    movies_data = None
    events_data = None
    prepared_series = None

    if mode_clean in {
        "channels",
        "all",
    }:
        print(
            "\n[Step 4a/5] Processing Live TV channels..."
        )

        from scanner.channels import (
            process_tv_channels,
        )

        channels_data = process_tv_channels()

        _sanitize_channel_quarantine(
            channels_data
        )

    if mode_clean in {
        "movies",
        "all",
    }:
        print(
            "\n[Step 4b/5] Processing Movie VOD pagination..."
        )

        from scanner.movies import (
            process_movies,
        )

        movies_data = process_movies()

        print("   Validating mixed TXT Series / Season / Episode catalogue...")
        from scanner.series import prepare_manual_series
        prepared_series = prepare_manual_series(project_root=PROJECT_ROOT)
        print(
            f"   Series ready: {prepared_series.get('series', 0)} Series / "
            f"{prepared_series.get('episodes', 0)} Episodes"
        )

    if mode_clean in {
        "today",
        "upcoming",
        "all",
    } | TARGETED_UPCOMING_MODES:
        if mode_clean == "today":
            print(
                "\n[Step 4c/5] Processing Today Match only..."
            )

        elif mode_clean == "upcoming":
            print(
                "\n[Step 4c/5] Processing Upcoming Match only..."
            )

        elif mode_clean in TARGETED_UPCOMING_MODES:
            print(
                "\n[Step 4c/5] Processing the targeted Upcoming fixtures only..."
            )

        else:
            print(
                "\n[Step 4c/5] Processing Today Match "
                "and Upcoming Match..."
            )

        events_data = _process_events_for_mode(
            mode_clean,
            targeted_plan,
        )

    print(
        "\n[Step 5/5] Publishing atomic JSON outputs, "
        "manifest and reports..."
    )
    _write_scan_progress(mode_clean, "publishing", started_at=run_started_at.isoformat())

    from scanner.output import (
        publish_scan_outputs,
    )

    summary = publish_scan_outputs(
        channels_data=channels_data,
        movies_data=movies_data,
        events_data=events_data,
        source_error_items=source_errors,
        rejected_low_quality_items=rejected_items,
        extra_quarantine_items=verifier_quarantine,
        scan_mode=mode_clean,
    )

    # Requirement 4. Remember what this trigger achieved. A fixture that now has
    # a valid link is marked resolved and is never targeted again; one that came
    # back empty keeps its attempt count and stays targetable on the next tick.
    if targeted_plan is not None:
        # Today Match counts too: a fixture whose link was found right at
        # kickoff is routed straight to Today, and that is the clearest possible
        # proof it no longer needs chasing.
        published_upcoming = []
        if isinstance(events_data, dict):
            for key in ("upcoming", "today_match"):
                payload = events_data.get(key)
                if isinstance(payload, dict) and isinstance(payload.get("items"), list):
                    published_upcoming.extend(
                        item for item in payload["items"] if isinstance(item, dict)
                    )
        ledger_path = PROJECT_ROOT / "state" / "upcoming-targeting.json"
        ledger = record_outcome(
            load_ledger(ledger_path),
            targeted_plan,
            published_upcoming,
            now=run_started_at,
        )
        save_ledger(ledger, ledger_path)
        resolved_now = sum(
            1
            for key in targeted_plan.targets
            if (ledger.get("fixtures") or {}).get(key, {}).get("resolved") is True
        )
        summary["targeted_scan"] = {
            **targeted_plan.summary(),
            "resolved_after_this_scan": resolved_now,
        }
        print(
            f"   Targeted fixtures resolved: {resolved_now}/"
            f"{len(targeted_plan.targets)}; every target is now marked "
            "attempted and will not be scanned again"
        )

    if prepared_series is not None:
        from scanner.series import publish_prepared_series
        series_summary = publish_prepared_series(
            prepared_series,
            project_root=PROJECT_ROOT,
        )
        from scanner.output import refresh_allowed_hosts
        refresh_allowed_hosts(PROJECT_ROOT / "data")
        summary["series"] = series_summary
        print(
            f"   Series published: {series_summary.get('series', 0)} Series / "
            f"{series_summary.get('episodes', 0)} Episodes"
        )

    print(
        "\n=================================================="
    )
    print(
        "✅ SCAN COMPLETED"
    )
    print(
        f"   Status: {summary.get('status', 'unknown')}"
    )
    print(
        "   Manifest: data/manifest.json"
    )
    print(
        "=================================================="
    )
    _write_scan_progress(
        mode_clean,
        "completed",
        started_at=run_started_at.isoformat(),
        summary_status=summary.get("status", "unknown"),
    )

    return summary


def _flush_visibility_audit() -> None:
    """Write the audit-only record of what the route-evidence model would do.

    Advisory output. It changed nothing during the run; see
    scanner/visibility_audit.py for why the model is connected before it is
    given authority.
    """
    try:
        from scanner import visibility_audit

        written = visibility_audit.flush()
        if written:
            summary = visibility_audit.summary()
            print(
                f"   visibility model audit: {summary['decisions_seen']} hide "
                f"decision(s) seen, model would keep "
                f"{summary['model_would_keep']} "
                + (
                    "(enforced where the route has evidence)"
                    if visibility_audit.ENFORCE_MODEL_DECISION
                    else "(advisory only)"
                )
            )
    except Exception:  # noqa: BLE001 - auditing must never fail a scan
        pass


def main() -> int:
    _force_utf8_console()

    parser = argparse.ArgumentParser(
        description=(
            "Live Signal IPTV, Today Match, Upcoming Match "
            "and Movie Scanner"
        )
    )

    parser.add_argument(
        "mode",
        nargs="?",
        default="channels",
        choices=[
            "channels",
            "today",
            "upcoming",
            "upcoming-targeted",
            "movies",
            "all",
        ],
        help=(
            "Scanner mode: channels, today, upcoming, upcoming-targeted, "
            "movies, or all. upcoming-targeted only chases links for fixtures "
            "that are about to start (requirement 4)."
        ),
    )

    args = parser.parse_args()

    mode_choice = str(
        args.mode
    ).lower()

    try:
        run_pipeline(
            mode_choice
        )
        _flush_visibility_audit()
        return 0

    except KeyboardInterrupt:
        print(
            "\nScanner cancelled by user.",
            file=sys.stderr,
        )
        return 130

    except Exception as error:
        safe_error = redact_sensitive_text(error)
        _write_scan_progress(mode_choice, "failed", error=safe_error)
        print(
            f"\n❌ SCANNER FAILED: {safe_error}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
