"""
Today Match and Upcoming Events Processor

Reads verified/protected candidates from working/bd-results.json, merges event
sources into stable cards, suppresses an Upcoming duplicate only when the same
Today Match has a genuinely verified playable stream, and returns payloads for
scanner/output.py.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

try:
    from scanner.merger import merge_candidates
except ImportError:
    module_dir = str(Path(__file__).resolve().parent)
    if module_dir not in sys.path:
        sys.path.insert(0, module_dir)
    from merger import merge_candidates


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_required_results(file_path: str | Path) -> List[Dict[str, Any]]:
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"BD results file not found: {file_path}")

    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(
            f"BD results file could not be read: {file_path}: {error}"
        ) from error

    if not isinstance(data, dict) or "results" not in data:
        raise ValueError(
            f"BD results file is invalid or missing 'results': {file_path}"
        )

    results = data.get("results")
    if not isinstance(results, list):
        raise ValueError(
            f"BD results field 'results' must be a list: {file_path}"
        )

    return [item for item in results if isinstance(item, dict)]


def _sort_time(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "9999-12-31T23:59:59+00:00"
    return text


def _event_sort_key(item: Dict[str, Any]) -> Tuple[str, str, str]:
    start_time = _sort_time(item.get("start_time"))
    competition = re.sub(
        r"\s+",
        " ",
        str(item.get("competition") or "").strip(),
    ).casefold()
    name = re.sub(
        r"\s+",
        " ",
        str(item.get("name") or "").strip(),
    ).casefold()
    return start_time, competition, name


def _payload(items: List[Dict[str, Any]], event_type: str) -> Dict[str, Any]:
    ordered = sorted(
        [item for item in items if isinstance(item, dict)],
        key=_event_sort_key,
    )
    return {
        "type": event_type,
        "updated_at": _utc_now(),
        "count": len(ordered),
        "items": ordered,
    }


def process_events(
    bd_results_path: str = "working/bd-results.json",
    settings_path: str = "config/settings.json",
) -> Dict[str, Dict[str, Any]]:
    """
    Return both event payloads:
      - today_match
      - upcoming

    Calling merge_candidates once with both pipelines is intentional: the
    merger contains the Today-Match-wins-over-Upcoming dedupe rule.
    """
    results = _load_required_results(bd_results_path)

    event_candidates = [
        dict(item)
        for item in results
        if str(item.get("source_pipeline") or "").strip().lower()
        in {"today_match", "upcoming"}
    ]

    merged = merge_candidates(
        event_candidates,
        settings_path=settings_path,
    )

    today_items: List[Dict[str, Any]] = []
    upcoming_items: List[Dict[str, Any]] = []

    for card in merged:
        if not isinstance(card, dict):
            continue

        pipeline = str(card.get("source_pipeline") or "").strip().lower()
        card_copy = dict(card)

        if pipeline == "today_match":
            card_copy["event_type"] = "today_match"
            today_items.append(card_copy)
        elif pipeline == "upcoming":
            card_copy["event_type"] = "upcoming"
            upcoming_items.append(card_copy)

    return {
        "today_match": _payload(today_items, "today_match"),
        "upcoming": _payload(upcoming_items, "upcoming"),
    }


if __name__ == "__main__":
    result = process_events()
    print(
        "Events processed: "
        f"today={result['today_match']['count']}, "
        f"upcoming={result['upcoming']['count']}"
    )
