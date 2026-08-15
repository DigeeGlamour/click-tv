"""
Adaptive Pipelined Verification Engine

Runs global verification and BD/proxy verification as an overlapping pipeline.
A completed global result is routed immediately to the BD stage when needed;
other global workers continue without waiting. Candidate groups expand lazily
only when they still need publishable links.

Safety properties:
- real manifest/media checks remain enabled;
- no cache result is treated as a fresh verification;
- public output is still published only after the whole pipeline finishes;
- permanent HTTP failures are not treated as host-wide failures;
- source/stream credentials stay out of reports;
- history/protection state mutations happen in the main thread.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import re
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Deque, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlsplit

from scanner.bd_verifier import (
    _eligible_for_github_protection,
    _extract_bd_rules as _extract_bd_rules_for_protection,
    _migrate_sensitive_history_keys,
    _report_item,
    _verify_via_proxy_workers,
    verify_bd_stream,
)
from scanner.player_compatibility import mark_confirmed_player_failures
from scanner.verifier import (
    _budget_exhausted_result,
    _extract_bd_rules as _extract_bd_rules_for_global,
    verify_single_stream,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_int(
    value: Any,
    default: int,
    minimum: int = 0,
    maximum: Optional[int] = None,
) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = default
    result = max(minimum, result)
    if maximum is not None:
        result = min(result, maximum)
    return result


def _load_json(path: str | Path) -> Dict[str, Any]:
    file_path = Path(path)
    if not file_path.exists():
        return {}
    try:
        with file_path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return {}


def _atomic_write_json(path: str | Path, payload: Dict[str, Any]) -> None:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = file_path.with_name(
        f".{file_path.name}.{os.getpid()}.{time.time_ns()}.tmp"
    )
    try:
        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, file_path)
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass


def _hostname(item: Dict[str, Any]) -> str:
    url = str(item.get("url") or "").split("|", 1)[0].strip()
    try:
        return (urlsplit(url).hostname or "").casefold()
    except Exception:
        return ""


def _path_group(item: Dict[str, Any], depth: int = 3) -> str:
    """Return host + a shallow path prefix, never the whole domain alone."""
    url = str(item.get("url") or "").split("|", 1)[0].strip()
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").casefold()
        segments = [segment.casefold() for segment in parsed.path.split("/") if segment]
    except Exception:
        return ""
    if not host:
        return ""
    safe_depth = max(1, min(5, int(depth)))
    prefix = "/" + "/".join(segments[:safe_depth]) if segments else "/"
    return f"{host}:{prefix}"


def _quarantine_404_result(item: Dict[str, Any], path_group: str) -> Dict[str, Any]:
    result = dict(item)
    result.update(
        verified=False,
        publish_allowed=False,
        verification_status="quarantine",
        verification_mode="same_run_404_path_sample",
        verification_checked_at=_utc_now(),
        verification_error=(
            "Path group quarantined for this scan after a 404 sample majority: "
            f"{path_group}"
        ),
        verification_error_kind="path_404_quarantine",
        quarantine_reason="path_group_404_sample_majority",
        http_status=404,
        response_time_ms=0,
    )
    return result


def _error_kind(item: Dict[str, Any]) -> str:
    explicit = str(
        item.get("verification_error_kind")
        or item.get("error_kind")
        or ""
    ).strip().casefold()
    if explicit:
        return explicit

    text = str(item.get("verification_error") or "").casefold()
    if "name or service not known" in text or "dns" in text:
        return "dns"
    if "certificate" in text or "ssl" in text or "tls" in text:
        return "ssl"
    if "connection refused" in text or "connection reset" in text:
        return "connection"
    if "network is unreachable" in text or "no route" in text:
        return "network"
    if "timed out" in text or "timeout" in text:
        return "timeout"
    return ""


def _pipeline_budget(settings: Dict[str, Any], mode: str) -> int:
    cfg = settings.get("pipeline")
    if not isinstance(cfg, dict):
        cfg = {}
    raw = cfg.get("time_budget_seconds")
    defaults = {
        "channels": 1500,
        "tv": 1500,
        "channels-discovery": 2100,
        "movies": 1800,
        "movies-discovery": 2400,
        "events": 600,
        "today": 420,
        "today_match": 420,
        "upcoming": 420,
        "all": 3000,
        "full-audit": 3300,
    }
    if isinstance(raw, dict):
        value = raw.get(mode, defaults.get(mode, 1800))
    else:
        value = raw or defaults.get(mode, 1800)
    return _safe_int(value, defaults.get(mode, 1800), 60, 6 * 60 * 60)


def _failure_result(item: Dict[str, Any], message: str, kind: str = "") -> Dict[str, Any]:
    result = dict(item)
    result.update(
        verified=False,
        publish_allowed=False,
        verification_status="failed",
        verification_mode="global",
        verification_checked_at=_utc_now(),
        verification_error=message,
        verification_error_kind=kind,
        response_time_ms=0,
        http_status=0,
    )
    return result


def _status_counts(items: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for item in items:
        status = str(item.get("verification_status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return counts


def _publishable(item: Dict[str, Any]) -> bool:
    if item.get("publish_allowed") is False:
        return False
    return item.get("verified") is True or item.get("publish_allowed") is True


def _apply_strict_player_visibility(
    items: List[Dict[str, Any]],
    settings: Dict[str, Any],
) -> int:
    """Hide unproven items from the site without deleting scan records."""
    config = settings.get("bd_verification", {})
    if not isinstance(config, dict) or not bool(config.get("strict_player_publish", False)):
        return 0

    hidden = 0
    for item in items:
        if item.get("verified") is True:
            continue
        if item.get("publish_allowed") is True:
            item["publish_allowed"] = False
            item["player_visibility"] = "hidden_unverified"
            item["verification_note"] = (
                str(item.get("verification_note") or "").strip()
                + " Hidden from Click TV because same-run playable media was not proven."
            ).strip()
            hidden += 1
    return hidden


def _is_movie_candidate(item: Dict[str, Any], mode: str) -> bool:
    pipeline = str(item.get("source_pipeline") or "").strip().casefold()
    return mode in {"movies", "all"} or pipeline == "movies"


def _normalize_priority_name(value: Any) -> str:
    text = str(value or "").casefold()
    text = re.sub(
        r"\b(?:official|live|channel|4k|2k|uhd|fhd|full\s*hd|fullhd|"
        r"hd|sd|2160p|1440p|1080p|720p|576p|480p|360p)\b",
        " ",
        text,
    )
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _is_priority_tv_candidate(
    item: Dict[str, Any],
    settings: Dict[str, Any],
) -> bool:
    """Force manual/configured priority TV candidates into the first wave."""
    if str(item.get("source_pipeline") or "").strip().casefold() != "tv":
        return False
    if item.get("force_verify") is True:
        return True

    source_id = str(item.get("source_id") or "").strip().casefold()
    original_pipeline = str(
        item.get("original_source_pipeline") or ""
    ).strip().casefold()
    if (
        source_id.startswith("manual-")
        or original_pipeline == "manual"
        or item.get("manual_source") is True
    ):
        return True

    protection = settings.get("channel_protection", {})
    if not isinstance(protection, dict):
        protection = {}
    if not bool(protection.get("always_verify_pinned_channels", True)):
        return False

    normalized_name = _normalize_priority_name(item.get("name"))
    if not normalized_name:
        return False

    pinned = settings.get("pinned_channels", {})
    if not isinstance(pinned, dict):
        return False

    for raw_entries in pinned.values():
        if not isinstance(raw_entries, list):
            continue
        for entry in raw_entries:
            if not isinstance(entry, dict):
                continue
            values: List[str] = []
            canonical = str(entry.get("canonical_name") or "").strip()
            if canonical:
                values.append(canonical)
            aliases = entry.get("aliases")
            if isinstance(aliases, list):
                values.extend(str(alias) for alias in aliases)
            normalized_aliases = {
                _normalize_priority_name(value)
                for value in values
                if _normalize_priority_name(value)
            }
            if normalized_name in normalized_aliases:
                return True

    return False


def _is_uncertain_result(item: Dict[str, Any]) -> bool:
    """Return True when a cloud result cannot prove that a movie is dead."""
    http_status = _safe_int(item.get("http_status"), 0)
    error_kind = _error_kind(item)
    status = str(item.get("verification_status") or "").strip().casefold()

    if http_status in {401, 403, 429, 451, 500, 502, 503, 504}:
        return True

    if error_kind in {
        "timeout",
        "dns",
        "ssl",
        "connection",
        "network",
        "host_circuit_open",
    }:
        return True

    return status in {
        "needs_bd_check",
        "bd_protected_pending",
        "geo_pending",
        "retryable_pending",
        "host_deferred",
        "stale_last_good",
    }


def _pending_movie_result(
    item: Dict[str, Any],
    *,
    status: Optional[str] = None,
    note: str = "",
    mode: str = "cloud_inconclusive",
) -> Dict[str, Any]:
    """Keep an uncertain movie publishable without claiming verification."""
    result = dict(item)
    http_status = _safe_int(result.get("http_status"), 0)
    error_kind = _error_kind(result)

    if status is None:
        if http_status in {429, 500, 502, 503, 504}:
            status = "retryable_pending"
        elif error_kind == "host_circuit_open":
            status = "host_deferred"
        else:
            status = "geo_pending"

    result.update(
        verified=False,
        publish_allowed=True,
        verification_status=status,
        verification_mode=mode,
        verification_checked_at=_utc_now(),
    )

    if note:
        result["verification_note"] = note

    return result


class Path404DispositionRegistry:
    """Quarantine only a host/path prefix when its small sample is mostly 404.

    This intentionally does not blacklist the entire domain. A CDN can have one
    retired folder while other folders still work.
    """

    def __init__(self, sample_size: int = 5, threshold: int = 4, depth: int = 3) -> None:
        self.sample_size = max(3, int(sample_size))
        self.threshold = max(2, min(self.sample_size, int(threshold)))
        self.depth = max(1, min(5, int(depth)))
        self.samples: Dict[str, int] = defaultdict(int)
        self.not_found: Dict[str, int] = defaultdict(int)
        self.verified: Dict[str, int] = defaultdict(int)
        self.classified: Dict[str, str] = {}

    def key(self, item: Dict[str, Any]) -> str:
        return _path_group(item, self.depth)

    def observe(self, item: Dict[str, Any], result: Dict[str, Any]) -> None:
        key = self.key(item)
        if not key or key in self.classified:
            return

        self.samples[key] += 1
        if result.get("verified") is True:
            self.verified[key] += 1
        elif _safe_int(result.get("http_status"), 0) in {404, 410}:
            self.not_found[key] += 1

        if (
            self.samples[key] >= self.sample_size
            and self.verified[key] == 0
            and self.not_found[key] >= self.threshold
        ):
            self.classified[key] = "mostly_not_found"

    def should_quarantine(self, item: Dict[str, Any]) -> bool:
        key = self.key(item)
        return bool(key and self.classified.get(key) == "mostly_not_found")

    def classification(self, item: Dict[str, Any]) -> str:
        return self.classified.get(self.key(item), "")


class HostDispositionRegistry:
    """Classify hosts from a very small same-run sample.

    When a movie host repeatedly returns only 403/451/transport failures and
    never returns a playable sample, checking thousands of URLs from the same
    cloud location is both slow and misleading. The remaining URLs are kept as
    host_deferred/geo_pending instead of being mass-failed.
    """

    def __init__(
        self,
        sample_size: int = 5,
        uncertain_threshold: int = 4,
    ) -> None:
        self.sample_size = max(3, int(sample_size))
        self.uncertain_threshold = max(
            2,
            min(self.sample_size, int(uncertain_threshold)),
        )
        self.samples: Dict[str, int] = defaultdict(int)
        self.verified: Dict[str, int] = defaultdict(int)
        self.uncertain: Dict[str, int] = defaultdict(int)
        self.permanent: Dict[str, int] = defaultdict(int)
        self.classified: Dict[str, str] = {}

    def observe(self, host: str, result: Dict[str, Any]) -> None:
        if not host or host in self.classified:
            return

        self.samples[host] += 1

        if result.get("verified") is True:
            self.verified[host] += 1
        else:
            http_status = _safe_int(result.get("http_status"), 0)
            error_kind = _error_kind(result)

            if http_status in {404, 410} or error_kind in {
                "invalid_content",
                "invalid_manifest",
                "html",
                "unsupported",
                "media_signature",
            }:
                self.permanent[host] += 1
            elif _is_uncertain_result(result):
                self.uncertain[host] += 1

        if (
            self.samples[host] >= self.sample_size
            and self.verified[host] == 0
            and self.uncertain[host] >= self.uncertain_threshold
        ):
            self.classified[host] = "cloud_inconclusive"

    def should_defer(self, host: str) -> bool:
        return self.classified.get(host) == "cloud_inconclusive"

    def classification(self, host: str) -> str:
        return self.classified.get(host, "")


def _should_submit_proxy_check(
    item: Dict[str, Any],
    protection_rules: Dict[str, Any],
    mode: str,
) -> bool:
    """Return True when an immediate same-run second pass can help.

    Movie 403/451/429/5xx/transport failures are eligible even when the host is
    not on the trusted BD list. This prevents a cloud-only failure from being
    treated as proof that the movie is dead.
    """
    if item.get("verified") is True:
        return False

    http_status = _safe_int(item.get("http_status"), 0)
    error_kind = _error_kind(item)
    permanent_codes = set(
        protection_rules.get("permanent_http_status_codes", {404, 410})
    )

    if http_status in permanent_codes:
        return False

    if error_kind in {
        "invalid_content",
        "invalid_manifest",
        "html",
        "unsupported",
        "media_signature",
    }:
        return False

    if http_status in {400, 405, 406, 409, 415, 416, 422}:
        return False

    if _is_movie_candidate(item, mode) and _is_uncertain_result(item):
        return True

    if not _eligible_for_github_protection(item, protection_rules):
        return False

    protect_codes = set(
        protection_rules.get("protect_http_status_codes", {401, 403, 451})
    )
    transient_codes = set(
        protection_rules.get(
            "transient_http_status_codes",
            {429, 500, 502, 503, 504},
        )
    )

    if http_status in protect_codes or http_status in transient_codes:
        return True

    if http_status == 0 and error_kind in {
        "timeout",
        "dns",
        "ssl",
        "connection",
        "network",
        "host_circuit_open",
    }:
        return True

    status = str(item.get("verification_status") or "").strip().casefold()
    return status in {
        "needs_bd_check",
        "bd_protected_pending",
        "geo_pending",
        "retryable_pending",
        "host_deferred",
        "stale_last_good",
    }


class HostCircuitRegistry:
    """Conservative transport-failure circuit breaker and per-host limiter."""

    def __init__(
        self,
        failure_threshold: int = 3,
        cooldown_seconds: int = 90,
        per_host_limit: int = 3,
    ) -> None:
        self.failure_threshold = max(2, failure_threshold)
        self.cooldown_seconds = max(15, cooldown_seconds)
        self.per_host_limit = max(1, per_host_limit)
        self.failures: Dict[str, int] = defaultdict(int)
        self.open_until: Dict[str, float] = {}
        self.inflight: Dict[str, int] = defaultdict(int)
        self.opened_count = 0
        self.skipped_count = 0

    def can_submit(self, host: str) -> bool:
        if not host:
            return True
        now = time.monotonic()
        until = self.open_until.get(host, 0.0)
        if until and now >= until:
            self.open_until.pop(host, None)
            self.failures[host] = 0
        return self.inflight[host] < self.per_host_limit and not self.is_open(host)

    def set_limit(self, new_limit: int) -> None:
        self.per_host_limit = max(1, int(new_limit))

    def is_open(self, host: str) -> bool:
        return bool(host and self.open_until.get(host, 0.0) > time.monotonic())

    def submitted(self, host: str) -> None:
        if host:
            self.inflight[host] += 1

    def completed(self, host: str, result: Dict[str, Any]) -> None:
        if host:
            self.inflight[host] = max(0, self.inflight[host] - 1)
        if not host:
            return

        if result.get("verified") is True:
            self.failures[host] = 0
            self.open_until.pop(host, None)
            return

        kind = _error_kind(result)
        # HTTP status failures and ordinary timeouts may be URL-specific. Only
        # strong transport failures open a host-wide circuit.
        if kind not in {"dns", "ssl", "connection", "network"}:
            return

        self.failures[host] += 1
        if self.failures[host] >= self.failure_threshold:
            if not self.is_open(host):
                self.opened_count += 1
            self.open_until[host] = time.monotonic() + self.cooldown_seconds


class ShardedExecutor:
    """
    Several independent ThreadPoolExecutor pools working on one shared queue.

    Example: pool_count=5 and workers_per_pool=20 creates five separate
    verification pools with 20 threads each (100 possible global requests).
    Submissions go to the least-busy pool so work is distributed evenly.
    """

    def __init__(
        self,
        pool_count: int,
        workers_per_pool: int,
        thread_name_prefix: str = "global-verify",
    ) -> None:
        self.pool_count = max(1, int(pool_count))
        self.workers_per_pool = max(1, int(workers_per_pool))
        self.thread_name_prefix = thread_name_prefix
        self.executors: List[concurrent.futures.ThreadPoolExecutor] = []
        self.inflight: List[int] = [0 for _ in range(self.pool_count)]
        self.cursor = 0

    @property
    def total_workers(self) -> int:
        return self.pool_count * self.workers_per_pool

    def __enter__(self) -> "ShardedExecutor":
        self.executors = [
            concurrent.futures.ThreadPoolExecutor(
                max_workers=self.workers_per_pool,
                thread_name_prefix=(
                    f"{self.thread_name_prefix}-pool-{index + 1}"
                ),
            )
            for index in range(self.pool_count)
        ]
        return self

    def _select_pool(self) -> int:
        minimum = min(self.inflight)
        for offset in range(self.pool_count):
            index = (self.cursor + offset) % self.pool_count
            if self.inflight[index] == minimum:
                self.cursor = (index + 1) % self.pool_count
                return index
        return 0

    def submit(self, fn: Any, *args: Any, **kwargs: Any) -> Tuple[concurrent.futures.Future, int]:
        if not self.executors:
            raise RuntimeError("ShardedExecutor is not running")
        pool_index = self._select_pool()
        future = self.executors[pool_index].submit(fn, *args, **kwargs)
        self.inflight[pool_index] += 1
        return future, pool_index

    def completed(self, pool_index: int) -> None:
        if 0 <= pool_index < len(self.inflight):
            self.inflight[pool_index] = max(0, self.inflight[pool_index] - 1)

    def loads(self) -> List[int]:
        return list(self.inflight)

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        for executor in self.executors:
            executor.shutdown(wait=True, cancel_futures=False)
        self.executors = []


def _mode_worker_profile(
    settings: Dict[str, Any],
    mode: str,
) -> Dict[str, int]:
    """Return mode-specific global and BD worker-pool settings.

    Large movie scans use five 20-thread global pools and five smaller BD pools.
    The BD pools remove the old end-of-scan bottleneck where global verification
    finished quickly but hundreds of proxy checks waited behind only eight threads.
    """
    pipeline_cfg = settings.get("pipeline")
    if not isinstance(pipeline_cfg, dict):
        pipeline_cfg = {}

    verification = settings.get("verification")
    if not isinstance(verification, dict):
        verification = {}

    bd_cfg = settings.get("bd_verification")
    if not isinstance(bd_cfg, dict):
        bd_cfg = {}

    configured_profiles = settings.get("worker_shards")
    if not isinstance(configured_profiles, dict):
        configured_profiles = {}

    defaults: Dict[str, Dict[str, int]] = {
        "channels": {
            "pool_count": 1,
            "workers_per_pool": 20,
            "minimum_total_inflight": 12,
            "bd_pool_count": 1,
            "bd_workers_per_pool": 6,
            "per_host_limit": 4,
            "tail_per_host_limit": 6,
            "tail_threshold": 120,
        },
        "tv": {
            "pool_count": 1,
            "workers_per_pool": 20,
            "minimum_total_inflight": 12,
            "bd_pool_count": 1,
            "bd_workers_per_pool": 6,
            "per_host_limit": 4,
            "tail_per_host_limit": 6,
            "tail_threshold": 120,
        },
        "today": {
            "pool_count": 1,
            "workers_per_pool": 20,
            "minimum_total_inflight": 12,
            "bd_pool_count": 1,
            "bd_workers_per_pool": 4,
            "per_host_limit": 4,
            "tail_per_host_limit": 6,
            "tail_threshold": 80,
        },
        "today_match": {
            "pool_count": 1,
            "workers_per_pool": 20,
            "minimum_total_inflight": 12,
            "bd_pool_count": 1,
            "bd_workers_per_pool": 4,
            "per_host_limit": 4,
            "tail_per_host_limit": 6,
            "tail_threshold": 80,
        },
        "upcoming": {
            "pool_count": 1,
            "workers_per_pool": 20,
            "minimum_total_inflight": 12,
            "bd_pool_count": 1,
            "bd_workers_per_pool": 4,
            "per_host_limit": 4,
            "tail_per_host_limit": 6,
            "tail_threshold": 80,
        },
        "movies": {
            "pool_count": 5,
            "workers_per_pool": 20,
            "minimum_total_inflight": 80,
            "bd_pool_count": 5,
            "bd_workers_per_pool": 8,
            "per_host_limit": 24,
            "tail_per_host_limit": 32,
            "tail_threshold": 700,
        },
        "all": {
            "pool_count": 5,
            "workers_per_pool": 20,
            "minimum_total_inflight": 80,
            "bd_pool_count": 5,
            "bd_workers_per_pool": 8,
            "per_host_limit": 24,
            "tail_per_host_limit": 32,
            "tail_threshold": 900,
        },
    }

    default_profile = defaults.get(mode, defaults["channels"])
    mode_config = configured_profiles.get(mode)
    if not isinstance(mode_config, dict):
        mode_config = {}

    fallback_global = _safe_int(
        pipeline_cfg.get(
            "global_workers",
            verification.get(
                "workers",
                settings.get("verification_workers", 20),
            ),
        ),
        default_profile["workers_per_pool"],
        1,
        160,
    )

    pool_count = _safe_int(
        mode_config.get("pool_count", default_profile["pool_count"]),
        default_profile["pool_count"],
        1,
        8,
    )
    workers_per_pool = _safe_int(
        mode_config.get(
            "workers_per_pool",
            default_profile["workers_per_pool"] if pool_count > 1 else fallback_global,
        ),
        default_profile["workers_per_pool"],
        1,
        32,
    )
    total_workers = pool_count * workers_per_pool

    minimum_total_inflight = _safe_int(
        mode_config.get(
            "minimum_total_inflight",
            default_profile["minimum_total_inflight"],
        ),
        default_profile["minimum_total_inflight"],
        1,
        total_workers,
    )

    legacy_bd_workers = _safe_int(
        mode_config.get(
            "bd_workers",
            pipeline_cfg.get(
                "bd_workers",
                bd_cfg.get("workers", default_profile["bd_workers_per_pool"]),
            ),
        ),
        default_profile["bd_workers_per_pool"],
        1,
        64,
    )

    bd_pool_count = _safe_int(
        mode_config.get("bd_pool_count", default_profile["bd_pool_count"]),
        default_profile["bd_pool_count"],
        1,
        8,
    )
    bd_workers_per_pool = _safe_int(
        mode_config.get(
            "bd_workers_per_pool",
            legacy_bd_workers if bd_pool_count == 1 else default_profile["bd_workers_per_pool"],
        ),
        default_profile["bd_workers_per_pool"],
        1,
        16,
    )
    total_bd_workers = bd_pool_count * bd_workers_per_pool

    per_host_limit = _safe_int(
        mode_config.get(
            "per_host_limit",
            pipeline_cfg.get("per_host_limit", default_profile["per_host_limit"]),
        ),
        default_profile["per_host_limit"],
        1,
        48,
    )
    tail_per_host_limit = _safe_int(
        mode_config.get(
            "tail_per_host_limit",
            default_profile["tail_per_host_limit"],
        ),
        default_profile["tail_per_host_limit"],
        per_host_limit,
        64,
    )
    tail_threshold = _safe_int(
        mode_config.get("tail_threshold", default_profile["tail_threshold"]),
        default_profile["tail_threshold"],
        20,
        5000,
    )

    shared_total_inflight_limit = _safe_int(
        mode_config.get(
            "shared_total_inflight_limit",
            pipeline_cfg.get(
                "shared_total_inflight_limit",
                total_workers + min(total_bd_workers, 20),
            ),
        ),
        total_workers + min(total_bd_workers, 20),
        max(total_workers, total_bd_workers),
        total_workers + total_bd_workers,
    )
    bd_reserved_inflight_slots = _safe_int(
        mode_config.get(
            "bd_reserved_inflight_slots",
            pipeline_cfg.get("bd_reserved_inflight_slots", min(20, total_bd_workers)),
        ),
        min(20, total_bd_workers),
        0,
        total_bd_workers,
    )
    bd_per_host_limit = _safe_int(
        mode_config.get(
            "bd_per_host_limit",
            pipeline_cfg.get("bd_per_host_limit", max(2, total_bd_workers // 5)),
        ),
        max(2, total_bd_workers // 5),
        1,
        total_bd_workers,
    )
    combined_per_host_limit = _safe_int(
        mode_config.get(
            "combined_per_host_limit",
            pipeline_cfg.get(
                "combined_per_host_limit",
                per_host_limit + bd_per_host_limit,
            ),
        ),
        per_host_limit + bd_per_host_limit,
        max(per_host_limit, bd_per_host_limit),
        per_host_limit + total_bd_workers,
    )
    host_sample_size = _safe_int(
        mode_config.get(
            "host_sample_size",
            settings.get("bd_verification", {}).get("host_sample_size", 5)
            if isinstance(settings.get("bd_verification"), dict)
            else 5,
        ),
        5,
        3,
        20,
    )
    host_sample_uncertain_threshold = _safe_int(
        mode_config.get(
            "host_sample_uncertain_threshold",
            settings.get("bd_verification", {}).get(
                "host_sample_uncertain_threshold",
                max(3, host_sample_size - 1),
            )
            if isinstance(settings.get("bd_verification"), dict)
            else max(3, host_sample_size - 1),
        ),
        max(3, host_sample_size - 1),
        2,
        host_sample_size,
    )
    host_second_pass_sample_size = _safe_int(
        mode_config.get(
            "host_second_pass_sample_size",
            settings.get("bd_verification", {}).get(
                "host_second_pass_sample_size",
                8,
            )
            if isinstance(settings.get("bd_verification"), dict)
            else 8,
        ),
        8,
        1,
        40,
    )

    path_404_sample_size = _safe_int(
        mode_config.get(
            "path_404_sample_size",
            bd_cfg.get("path_404_sample_size", 5),
        ),
        5,
        3,
        20,
    )
    path_404_threshold = _safe_int(
        mode_config.get(
            "path_404_threshold",
            bd_cfg.get("path_404_threshold", 4),
        ),
        4,
        2,
        path_404_sample_size,
    )
    path_group_depth = _safe_int(
        mode_config.get(
            "path_group_depth",
            bd_cfg.get("path_group_depth", 3),
        ),
        3,
        1,
        5,
    )

    return {
        "pool_count": pool_count,
        "workers_per_pool": workers_per_pool,
        "total_workers": total_workers,
        "minimum_total_inflight": minimum_total_inflight,
        "bd_pool_count": bd_pool_count,
        "bd_workers_per_pool": bd_workers_per_pool,
        "total_bd_workers": total_bd_workers,
        "per_host_limit": per_host_limit,
        "tail_per_host_limit": tail_per_host_limit,
        "tail_threshold": tail_threshold,
        "shared_total_inflight_limit": shared_total_inflight_limit,
        "bd_reserved_inflight_slots": bd_reserved_inflight_slots,
        "bd_per_host_limit": bd_per_host_limit,
        "combined_per_host_limit": combined_per_host_limit,
        "host_sample_size": host_sample_size,
        "host_sample_uncertain_threshold": host_sample_uncertain_threshold,
        "host_second_pass_sample_size": host_second_pass_sample_size,
        "path_404_sample_size": path_404_sample_size,
        "path_404_threshold": path_404_threshold,
        "path_group_depth": path_group_depth,
    }


def run_fast_verification_pipeline(
    candidates_path: str = "working/candidates.json",
    settings_path: str = "config/settings.json",
    global_output_path: str = "working/global-results.json",
    bd_output_path: str = "working/bd-results.json",
    history_path: str = "state/stream-history.json",
    protection_state_path: str = "state/bd-protection-state.json",
    bd_report_path: str = "reports/bd-verification.json",
    pipeline_report_path: str = "reports/pipeline-performance.json",
    checkpoint_path: str = "working/pipeline-checkpoint.json",
) -> Dict[str, Any]:
    candidates_data = _load_json(candidates_path)
    raw_items = candidates_data.get("items")
    if not isinstance(raw_items, list):
        raise ValueError("working/candidates.json is missing list field 'items'")

    items: List[Dict[str, Any]] = [
        dict(item) for item in raw_items if isinstance(item, dict)
    ]
    settings = _load_json(settings_path)
    mode = str(candidates_data.get("mode") or "all").strip().casefold()

    verification = settings.get("verification")
    if not isinstance(verification, dict):
        verification = {}
    bd_cfg = settings.get("bd_verification")
    if not isinstance(bd_cfg, dict):
        bd_cfg = {}
    pipeline_cfg = settings.get("pipeline")
    if not isinstance(pipeline_cfg, dict):
        pipeline_cfg = {}

    worker_profile = _mode_worker_profile(settings, mode)
    pool_count = worker_profile["pool_count"]
    workers_per_pool = worker_profile["workers_per_pool"]
    max_global_workers = worker_profile["total_workers"]
    min_global_workers = worker_profile["minimum_total_inflight"]
    bd_pool_count = worker_profile["bd_pool_count"]
    bd_workers_per_pool = worker_profile["bd_workers_per_pool"]
    max_bd_workers = worker_profile["total_bd_workers"]
    per_host_limit = worker_profile["per_host_limit"]
    tail_per_host_limit = worker_profile["tail_per_host_limit"]
    tail_threshold = worker_profile["tail_threshold"]
    shared_total_inflight_limit = worker_profile[
        "shared_total_inflight_limit"
    ]
    bd_reserved_inflight_slots = worker_profile[
        "bd_reserved_inflight_slots"
    ]
    bd_per_host_limit = worker_profile["bd_per_host_limit"]
    combined_per_host_limit = worker_profile[
        "combined_per_host_limit"
    ]
    host_sample_size = worker_profile["host_sample_size"]
    host_sample_uncertain_threshold = worker_profile[
        "host_sample_uncertain_threshold"
    ]
    host_second_pass_sample_size = worker_profile[
        "host_second_pass_sample_size"
    ]
    path_404_sample_size = worker_profile["path_404_sample_size"]
    path_404_threshold = worker_profile["path_404_threshold"]
    path_group_depth = worker_profile["path_group_depth"]
    host_failure_threshold = _safe_int(
        pipeline_cfg.get("host_failure_threshold", 3), 3, 2, 10
    )
    host_cooldown = _safe_int(
        pipeline_cfg.get("host_cooldown_seconds", 90), 90, 15, 600
    )
    progress_interval = _safe_int(
        verification.get("progress_interval", 25), 25, 1, 10_000
    )
    checkpoint_interval = _safe_int(
        pipeline_cfg.get("checkpoint_interval", 50), 50, 10, 1000
    )
    time_budget_seconds = _pipeline_budget(settings, mode)
    planning_cfg = settings.get("planning")
    if not isinstance(planning_cfg, dict):
        planning_cfg = {}
    exhaustive_verification = bool(
        planning_cfg.get("exhaustive_verification", False)
    )
    shortcut_deferral_enabled = not exhaustive_verification
    if exhaustive_verification:
        # An exhaustive run must not turn unprocessed candidates into pending
        # output merely because a short cloud-run budget elapsed.
        time_budget_seconds = 6 * 60 * 60

    global_bd_rules = _extract_bd_rules_for_global(settings)
    protection_rules = _extract_bd_rules_for_protection(settings)
    stream_history = _load_json(history_path)
    _migrate_sensitive_history_keys(stream_history)
    protection_state = _load_json(protection_state_path)

    # Group pools retain ranked later-wave candidates for on-demand expansion.
    groups: Dict[str, Dict[str, Any]] = {}
    priority_forced = 0
    for index, item in enumerate(items):
        item["_planner_index"] = index
        group = str(item.get("_verification_group") or f"single:{index}")
        state = groups.setdefault(
            group,
            {
                "initial": [],
                "remaining": deque(),
                "pending": 0,
                "publishable": 0,
                "checked": 0,
                "target": _safe_int(item.get("_verification_target"), 1, 1, 6),
            },
        )
        priority_candidate = _is_priority_tv_candidate(item, settings)
        if priority_candidate:
            item["_priority_forced_verification"] = True
            priority_forced += 1

        if priority_candidate or _safe_int(item.get("_verification_wave"), 0) == 0:
            state["initial"].append(item)
        else:
            state["remaining"].append(item)

    pending_global: Deque[Dict[str, Any]] = deque()
    for group in sorted(groups):
        initial = sorted(
            groups[group]["initial"],
            key=lambda item: (
                0 if item.get("_priority_forced_verification") is True else 1,
                _safe_int(item.get("_verification_rank"), 0),
            ),
        )
        for item in initial:
            pending_global.append(item)

    host_registry = HostCircuitRegistry(
        failure_threshold=host_failure_threshold,
        cooldown_seconds=host_cooldown,
        per_host_limit=per_host_limit,
    )
    host_disposition = HostDispositionRegistry(
        sample_size=host_sample_size,
        uncertain_threshold=host_sample_uncertain_threshold,
    )
    path_404_disposition = Path404DispositionRegistry(
        sample_size=path_404_sample_size,
        threshold=path_404_threshold,
        depth=path_group_depth,
    )
    bd_inflight_by_host: Dict[str, int] = defaultdict(int)
    host_second_pass_selected: Dict[str, int] = defaultdict(int)

    global_futures: Dict[
        concurrent.futures.Future,
        Tuple[Dict[str, Any], str, int],
    ] = {}
    bd_futures: Dict[
        concurrent.futures.Future,
        Tuple[Dict[str, Any], int, str],
    ] = {}
    pending_bd: Deque[Dict[str, Any]] = deque()
    global_results: List[Dict[str, Any]] = []
    final_results: List[Dict[str, Any]] = []
    adaptive_skipped: List[Dict[str, Any]] = []
    budget_fallbacks: List[Dict[str, Any]] = []

    global_completed = 0
    global_network_submitted = 0
    bd_selected = 0
    bd_submitted = 0
    bd_completed = 0
    host_sample_deferred = 0
    circuit_deferred = 0
    path_404_quarantined = 0
    next_global_progress = progress_interval
    next_bd_progress = progress_interval
    dynamic_limit = max_global_workers
    recent_outcomes: Deque[Tuple[str, int, int]] = deque(maxlen=100)
    last_pressure_rate = 0.0
    last_average_ms = 0.0
    tail_boost_active = False
    started = time.monotonic()
    budget_exhausted = False

    def write_checkpoint() -> None:
        _atomic_write_json(
            checkpoint_path,
            {
                "timestamp": _utc_now(),
                "mode": mode,
                "elapsed_seconds": int(time.monotonic() - started),
                "global_completed": global_completed,
                "global_submitted": global_network_submitted,
                "bd_completed": bd_completed,
                "bd_selected": bd_selected,
                "bd_submitted": bd_submitted,
                "bd_queued": len(pending_bd),
                "finalized": len(final_results),
                "pending_global": len(pending_global),
                "global_inflight": len(global_futures),
                "bd_inflight": len(bd_futures),
                "dynamic_global_limit": dynamic_limit,
                "worker_pools": {
                    "global_pool_count": pool_count,
                    "global_workers_per_pool": workers_per_pool,
                    "global_total_workers": max_global_workers,
                    "bd_pool_count": bd_pool_count,
                    "bd_workers_per_pool": bd_workers_per_pool,
                    "bd_total_workers": max_bd_workers,
                    "shared_total_inflight_limit": shared_total_inflight_limit,
                    "bd_reserved_inflight_slots": bd_reserved_inflight_slots,
                },
                "host_sample_deferred": host_sample_deferred,
                "circuit_deferred": circuit_deferred,
                "path_404_quarantined": path_404_quarantined,
                "path_404_groups": len(path_404_disposition.classified),
            },
        )

    def enqueue_next_for_group(group: str) -> None:
        state = groups[group]
        if state["pending"] > 0:
            return
        if state["publishable"] >= state["target"]:
            while state["remaining"]:
                skipped = dict(state["remaining"].popleft())
                skipped["adaptive_skip_reason"] = "group_target_satisfied"
                adaptive_skipped.append(skipped)
            return
        if state["remaining"]:
            pending_global.appendleft(state["remaining"].popleft())

    def finalize(item: Dict[str, Any]) -> None:
        nonlocal bd_completed, next_bd_progress
        final_results.append(item)
        group = str(item.get("_verification_group") or f"single:{item.get('_planner_index', 0)}")
        state = groups.get(group)
        if state is not None:
            state["pending"] = max(0, int(state["pending"]) - 1)
            state["checked"] += 1
            if _publishable(item):
                state["publishable"] += 1
            enqueue_next_for_group(group)

        if len(final_results) % checkpoint_interval == 0:
            write_checkpoint()

    def route_global_result(result: Dict[str, Any]) -> None:
        nonlocal bd_selected
        host = _hostname(result)

        if _is_movie_candidate(result, mode):
            path_404_disposition.observe(result, result)
            host_disposition.observe(host, result)

        if _should_submit_proxy_check(result, protection_rules, mode):
            # Once a host sample has already proved that the cloud location is
            # inconclusive, only a small same-run second-pass sample is useful.
            # The remaining URLs are preserved without flooding the proxy queue.
            if (
                shortcut_deferral_enabled
                and
                _is_movie_candidate(result, mode)
                and host_disposition.should_defer(host)
                and host_second_pass_selected[host]
                >= host_second_pass_sample_size
            ):
                finalize(
                    _pending_movie_result(
                        result,
                        note=(
                            "Host sample was cloud-inconclusive; the movie was "
                            "kept without claiming successful verification"
                        ),
                        mode="same_run_host_sample",
                    )
                )
                return

            pending_bd.append(dict(result))
            bd_selected += 1
            if host:
                host_second_pass_selected[host] += 1
            return

        processed = verify_bd_stream(
            candidate=result,
            settings=settings,
            stream_history=stream_history,
            protection_state=protection_state,
            bd_rules=protection_rules,
            proxy_result=(
                False,
                {
                    "proxy_error": "Proxy check not required",
                    "proxy_http_status": 0,
                    "proxy_name": "",
                },
            ),
        )
        finalize(processed)

    def fill_bd() -> None:
        nonlocal bd_submitted
        rotations = 0
        max_rotations = max(1, len(pending_bd))

        while (
            pending_bd
            and len(bd_futures) < max_bd_workers
            and (len(global_futures) + len(bd_futures))
            < shared_total_inflight_limit
        ):
            global_result = pending_bd.popleft()
            host = _hostname(global_result)

            if host and (
                bd_inflight_by_host[host] >= bd_per_host_limit
                or (
                    host_registry.inflight.get(host, 0)
                    + bd_inflight_by_host[host]
                )
                >= combined_per_host_limit
            ):
                pending_bd.append(global_result)
                rotations += 1
                if rotations >= max_rotations:
                    break
                continue

            rotations = 0
            future, pool_index = bd_executor.submit(
                _verify_via_proxy_workers,
                dict(global_result),
                settings,
            )
            bd_futures[future] = (global_result, pool_index, host)
            if host:
                bd_inflight_by_host[host] += 1
            bd_submitted += 1

    def mark_circuit_skipped(item: Dict[str, Any], host: str) -> None:
        nonlocal circuit_deferred
        host_registry.skipped_count += 1

        if _is_movie_candidate(item, mode):
            circuit_deferred += 1
            result = _pending_movie_result(
                item,
                status="host_deferred",
                note=(
                    "Host circuit opened after repeated cloud transport "
                    "failures; the movie was kept instead of being mass-failed"
                ),
                mode="same_run_host_circuit",
            )
            result["verification_error_kind"] = "host_circuit_open"
            result["verification_error"] = (
                "Host circuit temporarily open after repeated transport "
                f"failures: {host}"
            )
            global_results.append(result)
            finalize(result)
            return

        result = _failure_result(
            item,
            f"Host circuit temporarily open after repeated transport failures: {host}",
            "host_circuit_open",
        )
        global_results.append(result)
        route_global_result(result)

    def fill_global() -> None:
        nonlocal global_network_submitted, host_sample_deferred, path_404_quarantined
        if budget_exhausted:
            return

        reserve_for_bd = 0
        if pending_bd:
            reserve_for_bd = min(
                bd_reserved_inflight_slots,
                max(0, max_bd_workers - len(bd_futures)),
            )

        global_capacity = min(
            dynamic_limit,
            max(
                0,
                shared_total_inflight_limit
                - len(bd_futures)
                - reserve_for_bd,
            ),
        )

        rotations = 0
        max_rotations = max(1, len(pending_global))

        while pending_global and len(global_futures) < global_capacity:
            item = pending_global.popleft()
            group = str(
                item.get("_verification_group")
                or f"single:{item.get('_planner_index', 0)}"
            )
            host = _hostname(item)

            if (
                shortcut_deferral_enabled
                and
                _is_movie_candidate(item, mode)
                and path_404_disposition.should_quarantine(item)
            ):
                groups[group]["pending"] += 1
                path_404_quarantined += 1
                result = _quarantine_404_result(
                    item,
                    path_404_disposition.key(item),
                )
                global_results.append(result)
                finalize(result)
                rotations = 0
                continue

            if (
                shortcut_deferral_enabled
                and
                _is_movie_candidate(item, mode)
                and host_disposition.should_defer(host)
            ):
                groups[group]["pending"] += 1
                host_sample_deferred += 1
                result = _pending_movie_result(
                    item,
                    status="host_deferred",
                    note=(
                        "The host sample was cloud-inconclusive; remaining "
                        "movies were kept without repeating the same failure"
                    ),
                    mode="same_run_host_sample",
                )
                global_results.append(result)
                finalize(result)
                rotations = 0
                continue

            if shortcut_deferral_enabled and host_registry.is_open(host):
                groups[group]["pending"] += 1
                mark_circuit_skipped(item, host)
                rotations = 0
                continue

            host_at_capacity = bool(
                host
                and host_registry.inflight.get(host, 0) >= host_registry.per_host_limit
            )
            if host and (
                (shortcut_deferral_enabled and not host_registry.can_submit(host))
                or (not shortcut_deferral_enabled and host_at_capacity)
            ):
                pending_global.append(item)
                rotations += 1
                if rotations >= max_rotations:
                    break
                continue

            rotations = 0
            groups[group]["pending"] += 1
            host_registry.submitted(host)
            future, pool_index = global_executor.submit(
                verify_single_stream,
                dict(item),
                settings,
                global_bd_rules,
            )
            global_futures[future] = (item, host, pool_index)
            global_network_submitted += 1

    print(
        "   Adaptive pipeline: "
        f"pool={len(items)}, groups={len(groups)}, "
        f"global-pools={pool_count}x{workers_per_pool}={max_global_workers}, "
        f"bd-pools={bd_pool_count}x{bd_workers_per_pool}={max_bd_workers}, "
        f"shared-limit={shared_total_inflight_limit}, "
        f"global-per-host={per_host_limit}, "
        f"bd-per-host={bd_per_host_limit}, "
        f"combined-per-host={combined_per_host_limit}, "
        f"tail-per-host={tail_per_host_limit}, "
        f"host-sample={host_sample_size}/{host_sample_uncertain_threshold}, "
        f"404-path-sample={path_404_sample_size}/{path_404_threshold}/d{path_group_depth}, "
        f"priority-forced={priority_forced}, "
        f"exhaustive={exhaustive_verification}, "
        f"budget={time_budget_seconds}s",
        flush=True,
    )
    print(
        f"   Initial global queue: {len(pending_global)} candidates; "
        "later candidates will expand only when needed",
        flush=True,
    )

    with ShardedExecutor(
        pool_count=pool_count,
        workers_per_pool=workers_per_pool,
        thread_name_prefix="global-verify",
    ) as global_executor, ShardedExecutor(
        pool_count=bd_pool_count,
        workers_per_pool=bd_workers_per_pool,
        thread_name_prefix="bd-verify",
    ) as bd_executor:
        fill_global()
        fill_bd()

        while global_futures or bd_futures or pending_global or pending_bd:
            elapsed = time.monotonic() - started
            if elapsed >= time_budget_seconds and not budget_exhausted:
                budget_exhausted = True
                print(
                    "   Pipeline time budget reached; no new candidates will be scheduled",
                    flush=True,
                )

            # Wait briefly for either stage. Short polling keeps both queues
            # moving and allows live progress without blocking on one stage.
            fill_bd()
            combined = list(global_futures) + list(bd_futures)
            if not combined:
                if budget_exhausted and not pending_bd:
                    break
                if not budget_exhausted:
                    fill_global()
                fill_bd()
                if not global_futures and not bd_futures and (pending_global or pending_bd):
                    time.sleep(0.01)
                continue

            done, _ = concurrent.futures.wait(
                combined,
                timeout=0.25,
                return_when=concurrent.futures.FIRST_COMPLETED,
            )

            for future in done:
                if future in global_futures:
                    original, host, pool_index = global_futures.pop(future)
                    global_executor.completed(pool_index)
                    try:
                        result = future.result()
                    except Exception as error:
                        result = _failure_result(
                            original,
                            f"Unhandled global verifier exception: {error}",
                            "exception",
                        )

                    host_registry.completed(host, result)
                    global_results.append(result)
                    global_completed += 1
                    recent_outcomes.append(
                        (
                            _error_kind(result),
                            _safe_int(result.get("response_time_ms"), 0),
                            _safe_int(result.get("http_status"), 0),
                        )
                    )
                    route_global_result(result)

                    if global_completed >= next_global_progress:
                        elapsed_now = int(time.monotonic() - started)
                        print(
                            "   Global progress: "
                            f"{global_completed} completed, "
                            f"{len(global_futures)} running, "
                            f"{len(pending_global)} queued, "
                            f"pool-load={global_executor.loads()} "
                            f"({elapsed_now}s elapsed)",
                            flush=True,
                        )
                        while next_global_progress <= global_completed:
                            next_global_progress += progress_interval

                    # Transport-pressure controller. Dead/404 movie links do
                    # not reduce concurrency by themselves. Only real network
                    # pressure (timeouts, DNS/TLS/connection failures, 429/5xx,
                    # or very high latency) can reduce the total inflight limit.
                    if global_completed % 50 == 0 and len(recent_outcomes) >= 40:
                        pressure_count = 0
                        response_values: List[int] = []
                        for error_kind, response_ms, http_status in recent_outcomes:
                            if response_ms > 0:
                                response_values.append(response_ms)
                            if (
                                error_kind in {
                                    "timeout",
                                    "dns",
                                    "ssl",
                                    "connection",
                                    "network",
                                }
                                or http_status in {429, 502, 503, 504}
                            ):
                                pressure_count += 1

                        pressure_rate = pressure_count / len(recent_outcomes)
                        average_ms = (
                            sum(response_values) / len(response_values)
                            if response_values
                            else 0.0
                        )
                        last_pressure_rate = pressure_rate
                        last_average_ms = average_ms
                        old_limit = dynamic_limit
                        decrease_step = max(5, workers_per_pool // 2)
                        increase_step = max(2, workers_per_pool // 4)

                        if pressure_rate > 0.45 or average_ms > 7000:
                            dynamic_limit = max(
                                min_global_workers,
                                dynamic_limit - decrease_step,
                            )
                        elif pressure_rate < 0.20 and average_ms < 3500:
                            dynamic_limit = min(
                                max_global_workers,
                                dynamic_limit + increase_step,
                            )

                        if dynamic_limit != old_limit:
                            print(
                                "   Adaptive total inflight limit: "
                                f"{old_limit} -> {dynamic_limit} "
                                f"(network-pressure={pressure_rate:.0%}, "
                                f"avg={average_ms:.0f}ms)",
                                flush=True,
                            )

                elif future in bd_futures:
                    global_result, bd_pool_index, bd_host = bd_futures.pop(future)
                    bd_executor.completed(bd_pool_index)
                    if bd_host:
                        bd_inflight_by_host[bd_host] = max(
                            0,
                            bd_inflight_by_host[bd_host] - 1,
                        )
                    try:
                        proxy_result = future.result()
                    except Exception as error:
                        proxy_result = (
                            False,
                            {
                                "proxy_error": f"Proxy worker exception: {error}",
                                "proxy_http_status": 0,
                                "proxy_name": "",
                            },
                        )
                    try:
                        processed = verify_bd_stream(
                            candidate=global_result,
                            settings=settings,
                            stream_history=stream_history,
                            protection_state=protection_state,
                            bd_rules=protection_rules,
                            proxy_result=proxy_result,
                        )
                    except Exception as error:
                        if _is_movie_candidate(global_result, mode):
                            processed = _pending_movie_result(
                                global_result,
                                status="retryable_pending",
                                note=(
                                    "The same-run second pass raised an internal "
                                    "exception; the movie was kept without "
                                    "claiming successful verification"
                                ),
                                mode="pipeline_exception",
                            )
                            processed["verification_error"] = (
                                f"BD pipeline exception: {error}"
                            )
                        else:
                            processed = dict(global_result)
                            processed.update(
                                verified=False,
                                publish_allowed=bool(
                                    global_result.get("previously_published")
                                ),
                                verification_status=(
                                    "stale_last_good"
                                    if global_result.get("previously_published")
                                    else "failed_bd"
                                ),
                                verification_mode="pipeline_exception",
                                verification_error=(
                                    f"BD pipeline exception: {error}"
                                ),
                            )
                    bd_completed += 1
                    finalize(processed)

                    if bd_completed >= next_bd_progress or bd_completed == bd_selected:
                        elapsed_now = int(time.monotonic() - started)
                        print(
                            "   BD pipeline progress: "
                            f"{bd_completed}/{bd_selected} completed, "
                            f"{len(bd_futures)} running, "
                            f"{len(pending_bd)} queued, "
                            f"pool-load={bd_executor.loads()} "
                            f"({elapsed_now}s elapsed)",
                            flush=True,
                        )
                        while next_bd_progress <= bd_completed:
                            next_bd_progress += progress_interval

            if not budget_exhausted:
                remaining_global = len(pending_global) + len(global_futures)
                if (
                    mode in {"movies", "all"}
                    and not tail_boost_active
                    and 0 < remaining_global <= tail_threshold
                    and last_pressure_rate < 0.45
                    and (last_average_ms == 0 or last_average_ms < 5500)
                ):
                    host_registry.set_limit(tail_per_host_limit)
                    tail_boost_active = True
                    print(
                        "   Safe tail boost enabled: "
                        f"per-host {per_host_limit} -> {tail_per_host_limit} "
                        f"for final {remaining_global} global candidates",
                        flush=True,
                    )
                fill_bd()
                fill_global()
            fill_bd()

        # Let already-running work finish, but do not create new network work.
        # Movie scans are one-shot: an unprocessed uncertain movie is preserved
        # in this same output instead of depending on a future scheduled retry.
        if budget_exhausted:
            for item in list(pending_global):
                if _is_movie_candidate(item, mode):
                    group = str(
                        item.get("_verification_group")
                        or f"single:{item.get('_planner_index', 0)}"
                    )
                    groups[group]["pending"] += 1
                    fallback = _pending_movie_result(
                        item,
                        status="retryable_pending",
                        note=(
                            "The one-shot scan budget ended before a network "
                            "check; the movie was kept for direct user playback"
                        ),
                        mode="same_run_budget_fallback",
                    )
                    global_results.append(fallback)
                    budget_fallbacks.append(fallback)
                    finalize(fallback)
                elif item.get("previously_published") is True:
                    fallback = _budget_exhausted_result(item)
                    global_results.append(fallback)
                    processed = verify_bd_stream(
                        candidate=fallback,
                        settings=settings,
                        stream_history=stream_history,
                        protection_state=protection_state,
                        bd_rules=protection_rules,
                        proxy_result=(
                            False,
                            {
                                "proxy_error": "Budget fallback",
                                "proxy_http_status": 0,
                                "proxy_name": "",
                            },
                        ),
                    )
                    budget_fallbacks.append(processed)
                    final_results.append(processed)
            pending_global.clear()

    # Stable deterministic order for processors and reports.
    global_results.sort(key=lambda item: _safe_int(item.get("_planner_index"), 0))
    final_results.sort(key=lambda item: _safe_int(item.get("_planner_index"), 0))

    player_failure_hidden = 0
    player_failure_hidden += mark_confirmed_player_failures(
        [item for item in final_results if str(item.get("source_pipeline") or "").strip().casefold() == "tv"],
        "channel",
    )
    player_failure_hidden += mark_confirmed_player_failures(
        [item for item in final_results if str(item.get("source_pipeline") or "").strip().casefold() in {"movies", "movie", "vod", "film"}],
        "movie",
    )

    _atomic_write_json(history_path, stream_history)
    _atomic_write_json(protection_state_path, protection_state)

    strict_hidden = _apply_strict_player_visibility(final_results, settings)

    global_counts = _status_counts(global_results)
    final_counts = _status_counts(final_results)
    elapsed_seconds = max(0, int(time.monotonic() - started))

    global_payload: Dict[str, Any] = {
        "timestamp": _utc_now(),
        "mode": mode,
        "adaptive_pipeline": True,
        "candidate_pool": len(items),
        "total_network_checked": global_network_submitted,
        "total_global_completed": len(global_results),
        "total_adaptive_skipped": len(adaptive_skipped),
        "total_budget_fallbacks": len(budget_fallbacks),
        "strict_player_hidden": strict_hidden,
        "confirmed_player_failure_hidden": player_failure_hidden,
        "budget_exhausted": budget_exhausted,
        "elapsed_seconds": elapsed_seconds,
        "status_counts": global_counts,
        "results": global_results,
    }
    _atomic_write_json(global_output_path, global_payload)

    publishable = [item for item in final_results if _publishable(item)]
    bd_payload: Dict[str, Any] = {
        "timestamp": _utc_now(),
        "mode": mode,
        "adaptive_pipeline": True,
        "candidate_pool": len(items),
        "total_network_checked": global_network_submitted,
        "total_proxy_selected": bd_selected,
        "total_proxy_checked": bd_submitted,
        "total_processed": len(final_results),
        "total_verified": sum(1 for item in final_results if item.get("verified") is True),
        "total_publishable": len(publishable),
        "confirmed_player_failure_hidden": player_failure_hidden,
        "total_adaptive_skipped": len(adaptive_skipped),
        "total_budget_fallbacks": len(budget_fallbacks),
        "status_counts": final_counts,
        "results": final_results,
    }
    _atomic_write_json(bd_output_path, bd_payload)

    report_groups: Dict[str, List[Dict[str, Any]]] = {}
    for item in final_results:
        status = str(item.get("verification_status") or "unknown")
        report_groups.setdefault(status, []).append(_report_item(item))
    _atomic_write_json(
        bd_report_path,
        {
            "timestamp": _utc_now(),
            "mode": mode,
            "pipelined": True,
            "total_input_pool": len(items),
            "total_global_network_checked": global_network_submitted,
            "total_proxy_selected": bd_selected,
            "total_proxy_checked": bd_submitted,
            "total_processed": len(final_results),
            "total_publishable": len(publishable),
            "strict_player_hidden": strict_hidden,
            "status_counts": final_counts,
            "groups": report_groups,
        },
    )

    performance = {
        "timestamp": _utc_now(),
        "mode": mode,
        "elapsed_seconds": elapsed_seconds,
        "candidate_pool": len(items),
        "global_network_checked": global_network_submitted,
        "global_completed": len(global_results),
        "bd_proxy_selected": bd_selected,
        "bd_proxy_submitted": bd_submitted,
        "bd_proxy_completed": bd_completed,
        "final_processed": len(final_results),
        "final_publishable": len(publishable),
        "adaptive_skipped": len(adaptive_skipped),
        "budget_fallbacks": len(budget_fallbacks),
        "strict_player_hidden": strict_hidden,
        "budget_exhausted": budget_exhausted,
        "host_circuits_opened": host_registry.opened_count,
        "host_circuit_skips": host_registry.skipped_count,
        "host_sample_deferred": host_sample_deferred,
        "circuit_deferred": circuit_deferred,
        "cloud_inconclusive_hosts": len(host_disposition.classified),
        "path_404_quarantined": path_404_quarantined,
        "path_404_groups": len(path_404_disposition.classified),
        "priority_forced_verification": priority_forced,
        "final_global_inflight_limit": dynamic_limit,
        "workers": {
            "global_pool_count": pool_count,
            "global_workers_per_pool": workers_per_pool,
            "global_total_max": max_global_workers,
            "global_minimum_inflight": min_global_workers,
            "bd_pool_count": bd_pool_count,
            "bd_workers_per_pool": bd_workers_per_pool,
            "bd_total_max": max_bd_workers,
            "shared_total_inflight_limit": shared_total_inflight_limit,
            "bd_reserved_inflight_slots": bd_reserved_inflight_slots,
            "global_per_host": per_host_limit,
            "bd_per_host": bd_per_host_limit,
            "combined_per_host": combined_per_host_limit,
            "tail_per_host": tail_per_host_limit,
            "host_sample_size": host_sample_size,
            "host_sample_uncertain_threshold": (
                host_sample_uncertain_threshold
            ),
            "host_second_pass_sample_size": host_second_pass_sample_size,
            "path_404_sample_size": path_404_sample_size,
            "path_404_threshold": path_404_threshold,
            "path_group_depth": path_group_depth,
        },
    }
    _atomic_write_json(pipeline_report_path, performance)
    write_checkpoint()

    print(
        "   Pipeline completed: "
        f"global checked={global_network_submitted}, "
        f"BD selected={bd_selected}, "
        f"BD checked={bd_submitted}, "
        f"host deferred={host_sample_deferred + circuit_deferred}, "
        f"404 quarantined={path_404_quarantined}, "
        f"adaptive skipped={len(adaptive_skipped)}, "
        f"publishable={len(publishable)}, "
        f"elapsed={elapsed_seconds}s",
        flush=True,
    )
    return bd_payload
