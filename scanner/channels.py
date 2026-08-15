"""
Live TV Channels Pipeline Processor

Reads verified/protected results from working/bd-results.json, keeps only the
normalized live-TV pipeline, merges duplicate streams into channel cards,
groups cards into the supported TV categories, routes exact-category misses to Other, and pins T Sports at the top of Sports.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Set

try:
    from scanner.channel_logos import enrich_channel_logos
    from scanner.content_router import is_vod_candidate
    from scanner.merger import merge_candidates, pin_t_sports_first
    from scanner.player_compatibility import is_confirmed_player_failure, is_player_proven, load_failure_keys, load_proof_keys, mark_confirmed_player_failures, mark_unproven_player_items
except ImportError:
    module_dir = str(Path(__file__).resolve().parent)
    if module_dir not in sys.path:
        sys.path.insert(0, module_dir)
    from channel_logos import enrich_channel_logos
    from content_router import is_vod_candidate
    from merger import merge_candidates, pin_t_sports_first
    from player_compatibility import is_confirmed_player_failure, is_player_proven, load_failure_keys, load_proof_keys, mark_confirmed_player_failures, mark_unproven_player_items


VALID_TV_CATEGORIES = (
    "Bangla",
    "Sports",
    "Indian",
    "Cartoon",
    "Islamic",
    "Infotainments",
    "Foreign News",
    "Other",
)


def _coalesce_exact_url_names(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Give equal playback URLs one canonical card name before merger ranking."""
    owners: Dict[str, str] = {}
    output: List[Dict[str, Any]] = []
    for candidate in candidates:
        item = dict(candidate)
        url_key = str(item.get("url") or "").split("|", 1)[0].strip().casefold()
        name = str(item.get("name") or "").strip()
        if url_key and url_key in owners:
            item["duplicate_alias_name"] = name
            item["name"] = owners[url_key]
        elif url_key and name:
            owners[url_key] = name
        output.append(item)
    return output

_CATEGORY_LOOKUP = {
    re.sub(r"[^a-z0-9]+", "", category.lower()): category
    for category in VALID_TV_CATEGORIES
}


def _load_required_results(file_path: str | Path) -> List[Dict[str, Any]]:
    """
    Load the required BD verification result file.

    Missing or malformed intermediate data raises an error instead of silently
    returning an empty result, which helps prevent accidental empty publishing.
    """
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


def _canonical_tv_category(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""

    key = re.sub(r"[^a-z0-9]+", "", text.lower())
    return _CATEGORY_LOOKUP.get(key, "")


def _identity_keys(item: Dict[str, Any]) -> Set[str]:
    """
    Build conservative card-identity keys so an unknown-category duplicate is
    not quarantined when the same channel was already published in a known
    category.
    """
    keys: Set[str] = set()

    for field_name in ("id", "tvg_id"):
        value = str(item.get(field_name) or "").strip().lower()
        if value:
            keys.add(f"{field_name}:{value}")

    name = str(item.get("name") or "").strip().lower()
    if name:
        normalized_name = re.sub(r"[^\w]+", "-", name).strip("-")
        if normalized_name:
            keys.add(f"name:{normalized_name}")

    url = str(item.get("url") or "").split("|", 1)[0].strip()
    if url:
        keys.add(f"url:{url}")

    return keys


def _empty_result() -> Dict[str, List[Dict[str, Any]]]:
    categorized: Dict[str, List[Dict[str, Any]]] = {
        category: [] for category in VALID_TV_CATEGORIES
    }
    categorized["quarantine"] = []
    return categorized


def process_tv_channels(
    bd_results_path: str = "working/bd-results.json",
    settings_path: str = "config/settings.json",
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Return live-TV cards grouped into:
    Bangla, Sports, Indian, Cartoon, Islamic, Foreign News, Other, quarantine.

    Manual entries are expected to have already been routed by normalizer.py.
    Therefore this stage accepts only source_pipeline == "tv"; unresolved
    manual movie/event entries are not allowed to leak into TV output.
    """
    candidates = _load_required_results(bd_results_path)
    settings_payload = json.loads(Path(settings_path).read_text(encoding="utf-8"))
    bd_settings = settings_payload.get("bd_verification") if isinstance(settings_payload, dict) else {}
    strict_player_publish = isinstance(bd_settings, dict) and bool(bd_settings.get("strict_player_publish", False))

    tv_candidates: List[Dict[str, Any]] = []
    for item in candidates:
        pipeline = str(item.get("source_pipeline") or "").strip().lower()
        if pipeline != "tv":
            continue

        # Final defensive barrier: a direct movie/VOD file must never leak into
        # data/channels even if an upstream source or future parser mislabels it.
        if is_vod_candidate(item):
            continue

        tv_candidates.append(dict(item))

    if not tv_candidates:
        return _empty_result()

    # Fill only missing/placeholder logos by reusing trusted metadata already
    # present in another source for the same canonical channel. This step does
    # not change stream URLs, verification status, category, or source order.
    tv_candidates = enrich_channel_logos(tv_candidates)

    known_candidates: List[Dict[str, Any]] = []
    unknown_candidates: List[Dict[str, Any]] = []

    for item in tv_candidates:
        canonical_category = _canonical_tv_category(item.get("category"))
        candidate_copy = dict(item)

        if canonical_category:
            candidate_copy["category"] = canonical_category
            known_candidates.append(candidate_copy)
        else:
            candidate_copy["category"] = "Other"
            candidate_copy["category_detection"] = "fallback_other"
            known_candidates.append(candidate_copy)

    categorized = _empty_result()

    # Merge known categories first so a recognized category always wins over
    # an unknown duplicate of the same channel.
    known_cards = merge_candidates(
        _coalesce_exact_url_names(known_candidates),
        settings_path=settings_path,
    )
    failure_keys = load_failure_keys()
    mark_confirmed_player_failures(known_cards, "channel")
    known_cards = [
        card for card in known_cards
        if not is_confirmed_player_failure(card, "channel", failure_keys)
    ]
    if strict_player_publish:
        proof_keys = load_proof_keys()
        bangla_cards = [card for card in known_cards if str(card.get("category") or "") == "Bangla"]
        mark_unproven_player_items(bangla_cards, "channel")
        known_cards = [
            card for card in known_cards
            if str(card.get("category") or "") != "Bangla"
            or is_player_proven(card, "channel", proof_keys)
        ]

    published_identity_keys: Set[str] = set()

    for card in known_cards:
        if not isinstance(card, dict):
            continue

        canonical_category = _canonical_tv_category(card.get("category"))
        if not canonical_category:
            canonical_category = "Other"

        card_copy = dict(card)
        card_copy["category"] = canonical_category
        categorized[canonical_category].append(card_copy)
        published_identity_keys.update(_identity_keys(card_copy))

    # Unknown-category candidates are merged separately. If the same channel
    # was already published above, its unknown duplicate is not quarantined.
    unknown_cards = merge_candidates(
        unknown_candidates,
        settings_path=settings_path,
    )
    mark_confirmed_player_failures(unknown_cards, "channel")
    unknown_cards = [
        card for card in unknown_cards
        if not is_confirmed_player_failure(card, "channel", failure_keys)
    ]

    for card in unknown_cards:
        if not isinstance(card, dict):
            continue

        identity = _identity_keys(card)
        if identity and identity.intersection(published_identity_keys):
            continue

        quarantined = dict(card)
        original_category = str(card.get("category") or "").strip()
        quarantined["category"] = "quarantine"
        quarantined["quarantine_original_category"] = original_category
        quarantined["quarantine_reason"] = (
            f"Unknown TV category: {original_category or 'missing'}"
        )
        categorized["quarantine"].append(quarantined)

    categorized["Sports"] = pin_t_sports_first(
        categorized["Sports"],
        "Sports",
    )

    return categorized


if __name__ == "__main__":
    result = process_tv_channels()
    for category, items in result.items():
        print(f"TV Category '{category}': {len(items)} channels")
