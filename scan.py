"""
Main Scanner CLI Controller

Orchestrates the complete Live Signal pipeline:

Source Loader -> Normalizer -> Global Verifier -> BD Protection Verifier
-> TV / Movies / Events processors -> Atomic Output Publisher

Usage:
    python scan.py all
    python scan.py channels
    python scan.py movies
    python scan.py events
    python scan.py today
    python scan.py upcoming
    python scan.py collect
    python scan.py normalize
    python scan.py verify-global
    python scan.py verify-bd
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

from scanner.bd_verifier import verify_bd_candidates
from scanner.fast_pipeline import run_fast_verification_pipeline
from scanner.normalizer import normalize_all_candidates
from scanner.planner import plan_candidates
from scanner.source_loader import collect_candidates
from scanner.verifier import verify_all_candidates


PROJECT_ROOT = Path(__file__).resolve().parent
CANDIDATES_PATH = PROJECT_ROOT / "working" / "candidates.json"
SOURCE_HEALTH_PATH = PROJECT_ROOT / "state" / "source-health.json"

SUCCESS_SOURCE_STATUSES = {
    "success",
    "success_empty",
    "disabled",
}


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


def _load_required_json(file_path: str | Path) -> Dict[str, Any]:
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Required JSON file not found: {path}")

    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(
            f"Required JSON file could not be read: {path}: {error}"
        ) from error

    if not isinstance(data, dict):
        raise ValueError(f"Required JSON root must be an object: {path}")

    return data


def _parse_iso_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None

    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
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

        pipeline = str(raw_record.get("pipeline") or "").strip().lower()
        if active_set and pipeline and pipeline not in active_set:
            continue

        checked_at = _parse_iso_datetime(
            raw_record.get("last_scan")
            or raw_record.get("last_failure")
        )
        if checked_at is None or checked_at < started_at:
            continue

        current.append((str(source_id), raw_record))

    return current


def _guard_empty_collection(
    candidate_payload: Dict[str, Any],
    started_at: datetime,
) -> None:
    """
    Allow a legitimate empty result only when at least one current active source
    completed successfully (including success_empty). Abort when every active
    source failed so movies/events are not accidentally wiped.
    """
    raw_items = candidate_payload.get("items")
    if not isinstance(raw_items, list) or raw_items:
        return

    active_pipelines = candidate_payload.get("active_pipelines")
    if not isinstance(active_pipelines, list):
        active_pipelines = []

    records = _current_source_records(started_at, active_pipelines)
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
) -> Dict[str, Any]:
    """
    Normalize source-loader output and replace working/candidates.json.

    verifier.py reads this same file, so raw candidates must never be passed
    directly to the verifier.
    """
    raw_items = candidate_payload.get("items")
    if not isinstance(raw_items, list):
        raise ValueError(
            "Source-loader result is invalid or missing list field 'items'"
        )

    normalized_items = normalize_all_candidates(raw_items)
    if not isinstance(normalized_items, list):
        raise ValueError("normalize_all_candidates() must return a list")

    if raw_items and not normalized_items:
        raise RuntimeError(
            "Normalizer rejected every collected candidate; verification "
            "was stopped to prevent an accidental empty publish"
        )

    mode = str(candidate_payload.get("mode") or "all").strip().lower()
    planned_items, planning_summary = plan_candidates(
        normalized_items,
        mode=mode,
    )

    if normalized_items and not planned_items:
        raise RuntimeError(
            "Pre-verification planning removed every candidate; verification "
            "was stopped to preserve existing published data"
        )

    normalized_payload = dict(candidate_payload)
    normalized_payload["raw_candidate_count"] = len(raw_items)
    normalized_payload["normalized_candidate_count"] = len(normalized_items)
    normalized_payload["planned_candidate_count"] = len(planned_items)
    normalized_payload["total_candidates"] = len(planned_items)
    normalized_payload["normalized_at"] = _utc_now()
    normalized_payload["planning"] = planning_summary
    normalized_payload["items"] = planned_items

    _atomic_write_json(CANDIDATES_PATH, normalized_payload)
    return normalized_payload


def run_collection_and_normalization(
    mode: str,
    started_at: datetime | None = None,
) -> Dict[str, Any]:
    """Collect current sources, validate collection health, then normalize."""
    _ensure_project_root()
    run_started_at = started_at or datetime.now(timezone.utc)
    collected = collect_candidates(mode=mode)

    if not isinstance(collected, dict):
        raise ValueError("collect_candidates() must return a dictionary")

    _guard_empty_collection(collected, run_started_at)
    return _normalize_candidate_payload(collected)


def normalize_working_candidates() -> Dict[str, Any]:
    """Normalize the existing working/candidates.json without fetching again."""
    _ensure_project_root()
    payload = _load_required_json(CANDIDATES_PATH)
    return _normalize_candidate_payload(payload)


def _source_errors_since(
    started_at: datetime,
    active_pipelines: List[str],
) -> List[Dict[str, Any]]:
    errors: List[Dict[str, Any]] = []

    for source_id, record in _current_source_records(
        started_at,
        active_pipelines,
    ):
        status = str(record.get("status") or "").strip().lower()
        if not status or status in SUCCESS_SOURCE_STATUSES:
            continue

        errors.append(
            {
                "type": "source_fetch_error",
                "source_id": source_id,
                "source_name": str(
                    record.get("source_name") or source_id
                ),
                "pipeline": str(record.get("pipeline") or ""),
                "status": status,
                "http_status": record.get("http_status", 0),
                "attempts": record.get("attempts", 0),
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
    clean_url = str(raw_url or "").split("|", 1)[0].strip()
    if not clean_url:
        return ""

    try:
        parts = urlsplit(clean_url)
        return urlunsplit(
            (parts.scheme, parts.netloc, parts.path, "", "")
        )
    except Exception:
        return ""


def _sanitize_error_text(value: Any) -> str:
    """
    Remove query strings from any URLs embedded inside an error message.
    """
    text = str(value or "")

    def replace_url(match: re.Match[str]) -> str:
        raw = match.group(0).rstrip(".,);]}>")
        suffix = match.group(0)[len(raw):]
        return _safe_report_url(raw) + suffix

    return re.sub(r"https?://[^\s\"'<>]+", replace_url, text)


def _safe_report_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """
    Keep reports useful without publishing signed queries, cookies,
    authorization headers, DRM secrets, or other playback credentials.
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
        if value not in (None, "", [], {}):
            safe[field_name] = value

    error_value = (
        item.get("verification_error")
        or item.get("error_reason")
        or ""
    )
    if error_value:
        safe["verification_error"] = _sanitize_error_text(error_value)

    safe_url = _safe_report_url(item.get("url"))
    if safe_url:
        safe["url"] = safe_url

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
            raw_item.get("verification_status") or ""
        ).strip().lower()

        if status == "rejected_low_quality":
            rejected.append(_safe_report_item(raw_item))
        elif status == "quarantine":
            quarantined.append(_safe_report_item(raw_item))
        elif status in {"failed", "failed_bd"}:
            safe_item = _safe_report_item(raw_item)
            if safe_item.get("verification_error"):
                safe_item["type"] = "stream_verification_error"
                failed.append(safe_item)

    return rejected, quarantined, failed


def _sanitize_channel_quarantine(
    channels_data: Dict[str, Any] | None,
) -> None:
    """
    output.py writes channels_data['quarantine'] into a report. Replace those
    full playback cards with safe report-only copies first.
    """
    if not isinstance(channels_data, dict):
        return

    quarantine = channels_data.get("quarantine")
    if not isinstance(quarantine, list):
        return

    channels_data["quarantine"] = [
        _safe_report_item(item)
        for item in quarantine
        if isinstance(item, dict)
    ]


def _process_events_for_mode(
    mode: str,
) -> Dict[str, Dict[str, Any]]:
    """
    Import events lazily so channels/movies modes remain independent.
    """
    try:
        from scanner.events import process_events
    except ImportError as error:
        raise RuntimeError(
            "scanner/events.py is required for all/events/today/upcoming mode"
        ) from error

    event_result = process_events()
    if not isinstance(event_result, dict):
        raise ValueError("process_events() must return a dictionary")

    if mode in {"today", "today_match"}:
        payload = event_result.get("today_match")
        if not isinstance(payload, dict):
            raise ValueError(
                "process_events() did not return 'today_match' payload"
            )
        return {"today_match": payload}

    if mode == "upcoming":
        payload = event_result.get("upcoming")
        if not isinstance(payload, dict):
            raise ValueError(
                "process_events() did not return 'upcoming' payload"
            )
        return {"upcoming": payload}

    return event_result


def run_pipeline(mode: str = "all") -> Dict[str, Any]:
    """Run the complete scanner pipeline for one supported mode."""
    _ensure_project_root()

    mode_clean = str(mode or "all").strip().lower()
    pipeline_modes = {
        "all",
        "full-audit",
        "channels",
        "tv",
        "channels-discovery",
        "movies",
        "movies-discovery",
        "events",
        "today",
        "today_match",
        "upcoming",
    }
    if mode_clean not in pipeline_modes:
        raise ValueError(f"Unsupported pipeline mode: {mode_clean}")

    print("==================================================")
    print(
        "🚀 LIVE SIGNAL SCANNER - STARTING MODE: "
        f"{mode_clean.upper()}"
    )
    print("==================================================")

    run_started_at = datetime.now(timezone.utc)

    print("\n[Step 1a/5] Fetching sources and detecting formats...")
    normalized = run_collection_and_normalization(
        mode_clean,
        started_at=run_started_at,
    )

    print("\n[Step 1b/5] Normalization completed...")
    planning = normalized.get("planning")
    initial_wave = 0
    if isinstance(planning, dict):
        initial_wave = int(planning.get("initial_wave_candidates", 0) or 0)

    print(
        "   Candidates: "
        f"{normalized.get('raw_candidate_count', 0)} raw -> "
        f"{normalized.get('normalized_candidate_count', 0)} normalized -> "
        f"{normalized.get('planned_candidate_count', 0)} candidate pool"
    )
    if initial_wave:
        print(
            "   Adaptive first wave: "
            f"{initial_wave} candidates; later candidates run only when needed"
        )
    if isinstance(planning, dict):
        dropped = planning.get("dropped")
        if isinstance(dropped, dict):
            print(
                "   Planner reductions: "
                f"exact duplicates={dropped.get('exact_duplicates', 0)}, "
                f"unknown TV={dropped.get('unknown_tv_category', 0)}, "
                f"per-item cap={dropped.get('per_item_cap', 0)}, "
                f"global cap={dropped.get('global_cap', 0)}"
            )
        rerouted = planning.get("rerouted_counts")
        if isinstance(rerouted, dict) and rerouted:
            print(
                "   Content routing corrections: "
                + ", ".join(
                    f"{key}={value}"
                    for key, value in sorted(rerouted.items())
                )
            )

    source_errors = _source_errors_since(
        run_started_at,
        list(normalized.get("active_pipelines") or []),
    )

    print(
        "\n[Steps 2+3/5] Running adaptive Global + BD verification pipeline..."
    )
    print(
        "   Completed Global results enter BD verification immediately while "
        "remaining Global checks continue."
    )
    bd_summary = run_fast_verification_pipeline()

    rejected_items, verifier_quarantine, stream_errors = (
        _verification_report_items(bd_summary)
    )
    source_errors.extend(stream_errors)

    channels_data = None
    movies_data = None
    events_data = None

    if mode_clean in {"all", "full-audit", "tv", "channels", "channels-discovery"}:
        print("\n[Step 4a/5] Processing Live TV channels...")
        from scanner.channels import process_tv_channels

        channels_data = process_tv_channels()
        _sanitize_channel_quarantine(channels_data)

    if mode_clean in {"all", "full-audit", "movies", "movies-discovery"}:
        print("\n[Step 4b/5] Processing Movie VOD pagination...")
        from scanner.movies import process_movies

        movies_data = process_movies()

    if mode_clean in {
        "all",
        "full-audit",
        "events",
        "today",
        "today_match",
        "upcoming",
    }:
        print(
            "\n[Step 4c/5] Processing Today Match and Upcoming events..."
        )
        events_data = _process_events_for_mode(mode_clean)

    print(
        "\n[Step 5/5] Publishing atomic JSON outputs, manifest and reports..."
    )
    from scanner.output import publish_scan_outputs

    summary = publish_scan_outputs(
        channels_data=channels_data,
        movies_data=movies_data,
        events_data=events_data,
        source_error_items=source_errors,
        rejected_low_quality_items=rejected_items,
        extra_quarantine_items=verifier_quarantine,
        scan_mode=mode_clean,
    )

    print("\n==================================================")
    print("✅ SCAN COMPLETED")
    print(f"   Status: {summary.get('status', 'unknown')}")
    print("   Manifest: data/manifest.json")
    print("==================================================")
    return summary


def _run_collect_command() -> None:
    _ensure_project_root()
    started_at = datetime.now(timezone.utc)
    normalized = run_collection_and_normalization(
        "all",
        started_at=started_at,
    )
    print(
        "Collected and normalized "
        f"{normalized.get('normalized_candidate_count', 0)} candidates"
    )


def _run_normalize_command() -> None:
    normalized = normalize_working_candidates()
    print(
        "Normalized "
        f"{normalized.get('normalized_candidate_count', 0)} candidates"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Live Signal IPTV, Events and VOD Scanner CLI"
    )
    parser.add_argument(
        "mode",
        nargs="?",
        default="all",
        choices=[
            "all",
            "full-audit",
            "channels",
            "tv",
            "channels-discovery",
            "movies",
            "movies-discovery",
            "events",
            "today",
            "today_match",
            "upcoming",
            "collect",
            "normalize",
            "verify-global",
            "verify-bd",
        ],
        help="Scanner operation to execute (default: all)",
    )

    args = parser.parse_args()
    mode_choice = str(args.mode).lower()

    try:
        if mode_choice == "collect":
            _run_collect_command()
        elif mode_choice == "normalize":
            _run_normalize_command()
        elif mode_choice == "verify-global":
            normalize_working_candidates()
            summary = verify_all_candidates()
            print(
                "Global verification completed: "
                f"{summary.get('total_verified', 0)} verified"
            )
        elif mode_choice == "verify-bd":
            _ensure_project_root()
            summary = verify_bd_candidates()
            print(
                "BD verification completed: "
                f"{summary.get('total_publishable', 0)} publishable"
            )
        else:
            run_pipeline(mode_choice)

        return 0
    except KeyboardInterrupt:
        print("\nScanner cancelled by user.", file=sys.stderr)
        return 130
    except Exception as error:
        print(
            f"\n❌ SCANNER FAILED: {error}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
