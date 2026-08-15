"""
Channel Logo Enrichment Engine

Fills only missing or placeholder live-TV logos without changing stream URLs,
verification status, categories, primary/backup ordering, or playback metadata.

Resolution order:
1. manual/channel-logos.json override;
2. another candidate for the same canonical channel in the current scan;
3. state/channel-logo-cache.json from a previous successful scan.

Matching is intentionally conservative. It uses canonical aliases, exact
category + normalized channel name, exact tvg-id, and exact normalized card id.
No broad fuzzy matching or external logo API is used.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse


DEFAULT_ALIASES_PATH = "config/channel-aliases.json"
DEFAULT_OVERRIDES_PATH = "manual/channel-logos.json"
DEFAULT_CACHE_PATH = "state/channel-logo-cache.json"
DEFAULT_REPORT_PATH = "reports/channel-logo-enrichment.json"

DEFAULT_QUALITY_TOKENS = {
    "4k",
    "2k",
    "uhd",
    "fhd",
    "full hd",
    "fullhd",
    "hd",
    "sd",
    "2160p",
    "1440p",
    "1080p",
    "1080",
    "720p",
    "720",
    "576p",
    "480p",
    "360p",
}

DEFAULT_STATUS_TOKENS = {
    "official live",
    "live now",
    "live channel",
    "live tv",
    "server 1",
    "server 2",
    "server 3",
    "server1",
    "server2",
    "server3",
}

PLACEHOLDER_KEYWORDS = {
    "default-logo",
    "default_logo",
    "defaultlogo",
    "placeholder",
    "place-holder",
    "no-logo",
    "no_logo",
    "nologo",
    "logo-missing",
    "missing-logo",
    "blank-logo",
    "transparent.gif",
    "spacer.gif",
}

INVALID_LOGO_EXTENSIONS = {
    ".m3u",
    ".m3u8",
    ".mpd",
    ".ts",
    ".mp4",
    ".mkv",
    ".webm",
    ".avi",
    ".mov",
}


class _AliasResolver:
    def __init__(self, aliases_config: Dict[str, Any]) -> None:
        normalization = aliases_config.get("normalization")
        if not isinstance(normalization, dict):
            normalization = {}

        configured_quality = normalization.get("remove_quality_tokens")
        configured_status = normalization.get("remove_status_tokens")

        self.remove_tokens = set(DEFAULT_QUALITY_TOKENS)
        self.remove_tokens.update(DEFAULT_STATUS_TOKENS)

        if isinstance(configured_quality, list):
            self.remove_tokens.update(
                str(value).strip().casefold()
                for value in configured_quality
                if str(value or "").strip()
            )

        if isinstance(configured_status, list):
            self.remove_tokens.update(
                str(value).strip().casefold()
                for value in configured_status
                if str(value or "").strip()
            )

        self.alias_lookup: Dict[str, str] = {}
        raw_aliases = aliases_config.get("channel_aliases")
        if isinstance(raw_aliases, dict):
            for canonical_name, aliases in raw_aliases.items():
                canonical = self._base_name_key(canonical_name)
                if not canonical:
                    continue

                self.alias_lookup[canonical] = canonical

                if isinstance(aliases, str):
                    aliases = [aliases]
                if not isinstance(aliases, list):
                    continue

                for alias in aliases:
                    alias_key = self._base_name_key(alias)
                    if alias_key:
                        self.alias_lookup[alias_key] = canonical

    @staticmethod
    def _collapse(value: Any) -> str:
        text = str(value or "").casefold()
        text = re.sub(r"https?://\S+", " ", text)
        text = re.sub(r"[\[\]\(\)\{\}]", " ", text)
        text = re.sub(r"[_./\\:|?&=#\-]+", " ", text)
        text = re.sub(r"[^\w\s]+", " ", text, flags=re.UNICODE)
        return " ".join(text.split())

    def _base_name_key(self, value: Any) -> str:
        text = self._collapse(value)
        if not text:
            return ""

        for token in sorted(self.remove_tokens, key=len, reverse=True):
            token_key = self._collapse(token)
            if not token_key:
                continue
            text = re.sub(
                rf"(?<!\w){re.escape(token_key)}(?!\w)",
                " ",
                text,
            )

        return " ".join(text.split())

    def name_key(self, value: Any) -> str:
        base = self._base_name_key(value)
        if not base:
            return ""
        return self.alias_lookup.get(base, base)



def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()



def _load_json_object(file_path: str | Path) -> Dict[str, Any]:
    path = Path(file_path)
    if not path.exists():
        return {}

    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return {}

    return payload if isinstance(payload, dict) else {}



def _atomic_write_json(file_path: str | Path, payload: Dict[str, Any]) -> None:
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(
        f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp"
    )

    try:
        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
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



def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default



def _normalized_category(value: Any) -> str:
    text = str(value or "").strip().casefold()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text or "unknown"



def _normalized_identifier(value: Any) -> str:
    text = str(value or "").strip().casefold()
    if not text:
        return ""
    text = re.sub(r"[^\w]+", "-", text, flags=re.UNICODE).strip("-")
    return text



def _usable_logo_url(value: Any) -> str:
    logo = str(value or "").strip()
    if not logo:
        return ""

    if logo.startswith("//"):
        logo = "https:" + logo

    try:
        parsed = urlparse(logo)
    except ValueError:
        return ""

    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""

    lowered = logo.casefold()
    if any(keyword in lowered for keyword in PLACEHOLDER_KEYWORDS):
        return ""

    path = str(parsed.path or "").casefold()
    if any(path.endswith(extension) for extension in INVALID_LOGO_EXTENSIONS):
        return ""

    return logo



def _candidate_score(candidate: Dict[str, Any], logo: str) -> Tuple[int, ...]:
    source_id = str(candidate.get("source_id") or "").casefold()
    pipeline = str(candidate.get("source_pipeline") or "").casefold()

    manual = int(
        candidate.get("manual_source") is True
        or pipeline == "manual"
        or source_id.startswith("manual-")
    )
    verified = int(
        candidate.get("verified") is True
        or candidate.get("is_valid") is True
    )
    https = int(logo.casefold().startswith("https://"))
    source_priority = _safe_int(candidate.get("source_priority"), 0)
    logo_length = min(len(logo), 1000)

    return (
        manual,
        verified,
        source_priority,
        https,
        logo_length,
    )



def _candidate_keys(
    candidate: Dict[str, Any],
    resolver: _AliasResolver,
) -> List[str]:
    keys: List[str] = []

    name_key = resolver.name_key(
        candidate.get("name") or candidate.get("title")
    )
    category_key = _normalized_category(candidate.get("category"))
    if name_key:
        keys.append(f"category:{category_key}|name:{name_key}")

    tvg_id = _normalized_identifier(candidate.get("tvg_id"))
    if tvg_id:
        keys.append(f"tvg:{tvg_id}")

    card_id = _normalized_identifier(candidate.get("id"))
    if card_id:
        keys.append(f"category:{category_key}|id:{card_id}")

    return list(dict.fromkeys(keys))



def _override_keys(
    override: Dict[str, Any],
    resolver: _AliasResolver,
) -> List[str]:
    category = _normalized_category(override.get("category"))
    names: List[Any] = [
        override.get("canonical_name"),
        override.get("name"),
    ]

    aliases = override.get("aliases")
    if isinstance(aliases, str):
        aliases = [aliases]
    if isinstance(aliases, list):
        names.extend(aliases)

    keys: List[str] = []
    for name in names:
        name_key = resolver.name_key(name)
        if name_key:
            keys.append(f"category:{category}|name:{name_key}")

    tvg_ids = override.get("tvg_ids")
    if isinstance(tvg_ids, str):
        tvg_ids = [tvg_ids]
    if isinstance(tvg_ids, list):
        for tvg_id in tvg_ids:
            normalized = _normalized_identifier(tvg_id)
            if normalized:
                keys.append(f"tvg:{normalized}")

    ids = override.get("ids")
    if isinstance(ids, str):
        ids = [ids]
    if isinstance(ids, list):
        for card_id in ids:
            normalized = _normalized_identifier(card_id)
            if normalized:
                keys.append(f"category:{category}|id:{normalized}")

    return list(dict.fromkeys(keys))



def _best_registry_record(
    current: Optional[Dict[str, Any]],
    candidate: Dict[str, Any],
) -> Dict[str, Any]:
    if current is None:
        return candidate

    current_score = tuple(current.get("_score") or ())
    candidate_score = tuple(candidate.get("_score") or ())

    if candidate_score > current_score:
        return candidate
    if candidate_score < current_score:
        return current

    current_logo = str(current.get("logo") or "")
    candidate_logo = str(candidate.get("logo") or "")
    return candidate if candidate_logo < current_logo else current



def _build_manual_registry(
    overrides_config: Dict[str, Any],
    resolver: _AliasResolver,
) -> Dict[str, Dict[str, Any]]:
    if overrides_config.get("enabled") is False:
        return {}

    raw_channels = overrides_config.get("channels")
    if not isinstance(raw_channels, list):
        return {}

    registry: Dict[str, Dict[str, Any]] = {}
    for position, raw_override in enumerate(raw_channels):
        if not isinstance(raw_override, dict):
            continue
        if raw_override.get("enabled") is False:
            continue

        logo = _usable_logo_url(raw_override.get("logo"))
        if not logo:
            continue

        record = {
            "logo": logo,
            "source": "manual_override",
            "source_id": str(
                raw_override.get("id")
                or raw_override.get("canonical_name")
                or f"manual-logo-{position + 1}"
            ),
            "name": str(
                raw_override.get("canonical_name")
                or raw_override.get("name")
                or ""
            ),
            "category": str(raw_override.get("category") or ""),
            "_score": (1_000_000, -position),
        }

        for key in _override_keys(raw_override, resolver):
            registry[key] = _best_registry_record(registry.get(key), record)

    return registry



def _build_current_registry(
    candidates: Iterable[Dict[str, Any]],
    resolver: _AliasResolver,
) -> Dict[str, Dict[str, Any]]:
    registry: Dict[str, Dict[str, Any]] = {}

    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue

        logo = _usable_logo_url(candidate.get("logo"))
        if not logo:
            continue

        record = {
            "logo": logo,
            "source": "current_scan_source",
            "source_id": str(candidate.get("source_id") or ""),
            "name": str(candidate.get("name") or ""),
            "category": str(candidate.get("category") or ""),
            "_score": _candidate_score(candidate, logo),
        }

        for key in _candidate_keys(candidate, resolver):
            registry[key] = _best_registry_record(registry.get(key), record)

    return registry



def _build_cache_registry(cache_payload: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    raw_logos = cache_payload.get("logos")
    if not isinstance(raw_logos, dict):
        return {}

    registry: Dict[str, Dict[str, Any]] = {}
    for key, raw_record in raw_logos.items():
        if isinstance(raw_record, str):
            raw_record = {"logo": raw_record}
        if not isinstance(raw_record, dict):
            continue

        logo = _usable_logo_url(raw_record.get("logo"))
        if not logo:
            continue

        registry[str(key)] = {
            "logo": logo,
            "source": "previous_cache",
            "source_id": str(raw_record.get("source_id") or ""),
            "name": str(raw_record.get("name") or ""),
            "category": str(raw_record.get("category") or ""),
            "updated_at": str(raw_record.get("updated_at") or ""),
            "_score": (0,),
        }

    return registry



def _lookup_record(
    keys: List[str],
    registries: Iterable[Dict[str, Dict[str, Any]]],
) -> Optional[Dict[str, Any]]:
    for registry in registries:
        for key in keys:
            record = registry.get(key)
            if record and _usable_logo_url(record.get("logo")):
                return record
    return None



def _cache_record(record: Dict[str, Any], updated_at: str) -> Dict[str, Any]:
    return {
        "logo": str(record.get("logo") or ""),
        "source": str(record.get("source") or ""),
        "source_id": str(record.get("source_id") or ""),
        "name": str(record.get("name") or ""),
        "category": str(record.get("category") or ""),
        "updated_at": updated_at,
    }



def enrich_channel_logos(
    candidates: List[Dict[str, Any]],
    aliases_path: str | Path = DEFAULT_ALIASES_PATH,
    overrides_path: str | Path = DEFAULT_OVERRIDES_PATH,
    cache_path: str | Path = DEFAULT_CACHE_PATH,
    report_path: str | Path = DEFAULT_REPORT_PATH,
) -> List[Dict[str, Any]]:
    """
    Return copied candidates with missing/placeholder TV logos enriched.

    Existing usable logos are never overwritten. The function also updates a
    persistent logo cache and a diagnostic report. It does not make network
    requests and does not modify any playback or verification field.
    """
    if not isinstance(candidates, list):
        candidates = []

    aliases_config = _load_json_object(aliases_path)
    overrides_config = _load_json_object(overrides_path)
    cache_payload = _load_json_object(cache_path)
    resolver = _AliasResolver(aliases_config)

    copied_candidates = [
        dict(candidate)
        for candidate in candidates
        if isinstance(candidate, dict)
    ]

    manual_registry = _build_manual_registry(overrides_config, resolver)
    current_registry = _build_current_registry(copied_candidates, resolver)
    cache_registry = _build_cache_registry(cache_payload)

    before_missing = 0
    after_missing = 0
    preserved_existing = 0
    filled_manual = 0
    filled_current = 0
    filled_cache = 0
    filled_examples: List[Dict[str, Any]] = []
    unresolved: List[Dict[str, Any]] = []

    for candidate in copied_candidates:
        current_logo = _usable_logo_url(candidate.get("logo"))
        if current_logo:
            candidate["logo"] = current_logo
            preserved_existing += 1
            continue

        before_missing += 1
        candidate["logo"] = ""
        keys = _candidate_keys(candidate, resolver)
        record = _lookup_record(
            keys,
            (manual_registry, current_registry, cache_registry),
        )

        if record:
            candidate["logo"] = str(record.get("logo") or "")
            source = str(record.get("source") or "")
            if source == "manual_override":
                filled_manual += 1
            elif source == "current_scan_source":
                filled_current += 1
            else:
                filled_cache += 1

            if len(filled_examples) < 200:
                filled_examples.append(
                    {
                        "name": str(candidate.get("name") or ""),
                        "category": str(candidate.get("category") or ""),
                        "logo": str(candidate.get("logo") or ""),
                        "resolved_from": source,
                        "resolved_source_id": str(record.get("source_id") or ""),
                    }
                )
        else:
            after_missing += 1
            if len(unresolved) < 500:
                unresolved.append(
                    {
                        "name": str(candidate.get("name") or ""),
                        "category": str(candidate.get("category") or ""),
                        "id": str(candidate.get("id") or ""),
                        "tvg_id": str(candidate.get("tvg_id") or ""),
                        "source_id": str(candidate.get("source_id") or ""),
                    }
                )

    now = _utc_now()
    merged_cache: Dict[str, Any] = {}

    old_cache = cache_payload.get("logos")
    if isinstance(old_cache, dict):
        for key, raw_record in old_cache.items():
            if isinstance(raw_record, str):
                raw_record = {"logo": raw_record}
            if not isinstance(raw_record, dict):
                continue
            logo = _usable_logo_url(raw_record.get("logo"))
            if not logo:
                continue
            preserved_record = dict(raw_record)
            preserved_record["logo"] = logo
            merged_cache[str(key)] = preserved_record

    for registry in (current_registry, manual_registry):
        for key, record in registry.items():
            logo = _usable_logo_url(record.get("logo"))
            if not logo:
                continue
            merged_cache[key] = _cache_record(record, now)

    _atomic_write_json(
        cache_path,
        {
            "version": 1,
            "updated_at": now,
            "logos": dict(sorted(merged_cache.items())),
        },
    )

    report = {
        "version": 1,
        "generated_at": now,
        "total_tv_candidates": len(copied_candidates),
        "usable_logo_candidates": len(current_registry),
        "manual_override_keys": len(manual_registry),
        "cache_keys": len(merged_cache),
        "before_missing": before_missing,
        "preserved_existing": preserved_existing,
        "filled": {
            "manual_override": filled_manual,
            "current_scan_source": filled_current,
            "previous_cache": filled_cache,
            "total": filled_manual + filled_current + filled_cache,
        },
        "after_missing": after_missing,
        "filled_examples": filled_examples,
        "unresolved": unresolved,
    }
    _atomic_write_json(report_path, report)

    return copied_candidates
