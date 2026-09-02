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
    from scanner import category_allowlist
    from scanner.channel_logos import enrich_channel_logos
    from scanner.content_router import is_vod_candidate
    from scanner.merger import merge_candidates, pin_t_sports_first
    from scanner.player_compatibility import is_confirmed_player_failure, is_player_proven, load_failure_keys, load_proof_keys, mark_confirmed_player_failures, mark_unproven_player_items
except ImportError:
    module_dir = str(Path(__file__).resolve().parent)
    if module_dir not in sys.path:
        sys.path.insert(0, module_dir)
    import category_allowlist  # type: ignore[no-redef]
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


#: Collected across both merge passes so the run reports one number, and so a
#: hidden channel is a name in a file rather than a channel that quietly
#: stopped existing.
_PLAYABLE_PRIMARY_REPORT: List[Dict[str, str]] = []


def _enforce_playable_primary(cards: List[Dict[str, Any]], pass_name: str) -> None:
    """Never lead with a route a real browser could not decode.

    Promotes a playable backup where there is one, and holds the card back
    where there is not. Reports both, and writes the whole decision out to
    reports/unplayable-primary.json for the run.
    """
    try:
        from scanner import unplayable_primary
    except ImportError:  # pragma: no cover - direct-module import path
        import unplayable_primary  # type: ignore

    promoted, hidden, report = unplayable_primary.enforce(cards)
    # One physical route, one entry. The same URL arriving from two sources
    # with different headers is two playback ids, so nothing keyed on the id
    # notices - and the published Zee Bangla card offered one dead rgkkw
    # route twice. Repaired once by a script; the 19:00 channels scan put it
    # straight back and failed the run, so the rule belongs here.
    folded = unplayable_primary.dedupe_backup_urls(cards)
    if folded:
        print(f"   duplicate physical URLs folded out of backups "
              f"({pass_name}): {len(folded)}")
        for row in folded[:8]:
            print(f"      {row['name']}: dropped {row['dropped']}")
    for row in report:
        row["pass"] = pass_name
    _PLAYABLE_PRIMARY_REPORT.extend(report)
    if promoted or hidden:
        print(f"   measured-unplayable primaries ({pass_name}): {promoted} "
              f"replaced by a playable backup, {hidden} card(s) held back")
        for row in report[:12]:
            print(f"      {row['action']}: {row['name']} "
                  f"({row['category']}) - {row['reason'][:56]}")
    try:
        target = Path("reports") / "unplayable-primary.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({
            "generated_at": __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc).isoformat(),
            "promoted": sum(1 for r in _PLAYABLE_PRIMARY_REPORT
                            if r["action"].startswith("promoted")),
            "hidden": sum(1 for r in _PLAYABLE_PRIMARY_REPORT
                          if r["action"].startswith("hidden")),
            "reason": "A route measured unplayable in a real browser may not "
                      "lead a card. Returns automatically once any route is "
                      "measured playable again.",
            "records": _PLAYABLE_PRIMARY_REPORT,
        }, indent=1, ensure_ascii=False), encoding="utf-8")
    except (OSError, TypeError, ValueError):  # pragma: no cover - reporting only
        pass


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
    # Off by default: see the note beside the gate below for why an exact-URL
    # proof ledger cannot be a publish requirement for a rotating IPTV source.
    bangla_requires_player_proof = isinstance(bd_settings, dict) and bool(
        bd_settings.get("bangla_requires_player_proof", False)
    )

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
    # The Bangla-only player-proof gate is a one-way ratchet unless it is
    # opt-in. `is_player_proven` matches (name, url, header_profile, proxy_mode,
    # stream_type, requires_headers) against a ledger that a human regenerates
    # by hand (state/player-playback-proof.json, last built 2026-08-18). IPTV
    # sources rotate their URLs constantly, so the fingerprint stops matching
    # within days and the channel is hidden even though this run verified it -
    # measured on 2026-08-20: 29 Bangla channels reached `verified_global`, 9
    # published. Six of the 20 hidden ones (ATN Bangla, NTV, RTV, Somoy TV,
    # Jago News 24, Channel S) were in the ledger by name and lost only to a
    # changed URL. Confirmed browser failures are a separate, evidence-based
    # gate and still apply to every category above.
    if strict_player_publish and bangla_requires_player_proof:
        proof_keys = load_proof_keys()
        bangla_cards = [card for card in known_cards if str(card.get("category") or "") == "Bangla"]
        mark_unproven_player_items(bangla_cards, "channel")
        known_cards = [
            card for card in known_cards
            if str(card.get("category") or "") != "Bangla"
            or is_player_proven(card, "channel", proof_keys)
        ]

    published_identity_keys: Set[str] = set()
    rejected_by_allowlist: List[Dict[str, Any]] = []

    for card in known_cards:
        if not isinstance(card, dict):
            continue

        canonical_category = _canonical_tv_category(card.get("category"))
        if not canonical_category:
            canonical_category = "Other"

        card_copy = dict(card)
        card_copy["category"] = canonical_category

        # A curated category publishes only the names it lists. Dropped here
        # rather than re-routed to Other, because the point of the list is that
        # the card should not exist - moving it would leave the card.
        if not category_allowlist.is_allowed(canonical_category, card_copy.get("name")):
            rejected_by_allowlist.append(
                {
                    "name": str(card_copy.get("name") or ""),
                    "category": canonical_category,
                    "reason": "not on the publish allowlist for this category",
                }
            )
            continue

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

    # A route a real browser could not decode must not be the one a card leads
    # with. The merge already ranks those last, but ranking only helps when
    # there is something else to rank: when the dead route is all that
    # survived, the card publishes it with a green badge, because HTTP
    # verification asked the runner for the URL and got a 200 - rgkkw.live
    # answers 200 and produces 0.12 seconds of video.
    #
    # Run here, on the cards that are actually about to be written, and not on
    # the merge candidates - that was tried and it condemned 100 cards instead
    # of the 22 that publish with a dead primary, because a candidate can still
    # be carrying a route this scan is about to replace.
    # The published categories only. `quarantine` holds cards the allowlist
    # already refused, and re-deciding those here would put names in the
    # report that were never going to reach a viewer either way.
    _enforce_playable_primary(
        [card for name, cards in categorized.items() if name != "quarantine"
         for card in cards if isinstance(card, dict)],
        "published",
    )

    _write_allowlist_report(categorized, rejected_by_allowlist)
    _report_proven_channels_that_are_missing(categorized)

    return categorized




def _report_proven_channels_that_are_missing(categorized) -> None:
    """Name any channel measured playing in a browser that is not published.

    The registry records channels a real browser was watched playing for two
    full windows. When one of those is not in the catalogue the scan just
    wrote, that is worth a line: it is the difference between a source that
    stopped answering and a rule quietly removing something the owner
    restored on purpose.

    Desh TV went on 2026-09-02 because every source now reports it at 410p
    and the catalogue floor is 720p. That is a decision about the product,
    not a bug to fix here - but it was only discoverable by reading a test
    failure, which is the wrong place to learn it.
    """
    try:
        from scanner import sustained_proof  # noqa: PLC0415
        from scanner import channel_alias  # noqa: PLC0415
    except ImportError:  # pragma: no cover - direct module execution
        return
    try:
        registry = sustained_proof.load()
    except Exception:  # noqa: BLE001 - a registry read must never break a scan
        return

    proven = {
        channel_alias.canonical_channel_name(row.get("name")): str(row.get("name") or "")
        for row in (registry.get("proofs") or {}).values()
        if str(row.get("name") or "").strip()
    }
    if not proven:
        return
    published = {
        channel_alias.canonical_channel_name(card.get("name"))
        for name, cards in categorized.items() if name != "quarantine"
        for card in cards if isinstance(card, dict)
    }
    missing = sorted(proven[key] for key in set(proven) - published)
    if not missing:
        return
    print(f"   proven channels not in this catalogue: {len(missing)}")
    for name in missing[:10]:
        print(f"      {name} - measured playing before, no publishable "
              f"route this scan")

def _write_allowlist_report(
    categorized: Dict[str, List[Dict[str, Any]]],
    rejected: List[Dict[str, Any]],
) -> None:
    """Say what a curated category refused and what it did not receive.

    A filter that drops cards without saying which ones is the same shape of
    problem as a scan that hides a channel without saying why. Both halves
    matter: what was refused, and which of the requested names produced no card
    at all this run - the second is how the owner finds out a channel they asked
    for has no working source left.
    """
    restricted = [
        category for category in categorized
        if category_allowlist.is_restricted(category)
    ]
    if not restricted and not rejected:
        return
    payload: Dict[str, Any] = {
        "mode": "category_publish_allowlist",
        "note": (
            "Categories listed in config/channel-categories.json under "
            "publish_allowlist publish only the names they list. Anything else "
            "is dropped from that category rather than moved to Other."
        ),
        "rejected_count": len(rejected),
        "rejected": sorted(rejected, key=lambda row: str(row.get("name") or "")),
        "categories": {},
    }
    for category in sorted(restricted):
        cards = categorized.get(category) or []
        payload["categories"][category] = {
            "requested": len(category_allowlist.allowed_names(category)),
            "published": len(cards),
            "published_names": sorted(
                str(card.get("name") or "") for card in cards
            ),
            "requested_but_not_published": category_allowlist.missing_from(
                cards, category
            ),
        }
    try:
        target = Path("reports") / "category-allowlist.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=1) + "\n",
            encoding="utf-8",
        )
    except OSError:  # noqa: BLE001 - a report must never fail a scan
        pass


if __name__ == "__main__":
    result = process_tv_channels()
    for category, items in result.items():
        print(f"TV Category '{category}': {len(items)} channels")
