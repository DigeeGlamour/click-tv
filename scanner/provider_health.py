"""Request policy and health state for the movie metadata providers (PART 04).

One place owns every outbound metadata request, so the rules below hold for
TMDB, OMDb, Cinemeta, Fanart, MoviesDatabase, TVMaze and AniList alike
rather than being re-argued per adapter:

  - Conservative pacing. A configurable maximum requests/second per
    provider (default 5, the low end of the plan's 5-10 target), enforced
    as a minimum interval between two requests to the same provider.

  - 429 respected, never evaded. Retry-After is honoured when the provider
    sends one; otherwise a bounded 2s -> 5s -> 15s -> 30s ladder. There is
    deliberately NO multi-key/multi-account rotation here: rotating keys to
    get around a rate limit is not what the limit is for, and TMDB's own
    guidance is to respect the 429. Redundancy in this system means the
    other six providers plus the persistent last-good cache, not more TMDB
    keys.

  - 5xx and transport failures retried on the same bounded ladder;
    everything else (404, malformed body) is a real answer, not a transient
    one, and is never retried.

  - 401/403 never retried at all. An invalid or suspended key does not get
    better by asking again 30 seconds later, and retrying is precisely how
    a suspension turns into a ban. The provider is marked unhealthy, a
    warning is printed for the administrator, and the rest of the chain
    carries on without it.

  - A provider that exhausts its retries stops being asked for the rest of
    the run (`cooling_down`), so a bad provider day costs one ladder, not
    one ladder per movie. This is what keeps the movie scan inside its
    time budget (config/settings.json time_budget_seconds.movies).

Nothing here ever raises, and nothing here ever logs a URL, header or key -
only the provider name, the status class and the counters.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional, Tuple

try:
    from scanner import paths
except ImportError:  # pragma: no cover - direct-module import path
    import paths  # type: ignore

DEFAULT_PATH = paths.state_path("provider-health.json")

#: Bounded retry ladder, in seconds. Four waits, then the provider is left
#: alone for the rest of the run - never an unbounded loop.
BACKOFF_LADDER_SECONDS: Tuple[int, ...] = (2, 5, 15, 30)

#: Conservative default pacing. The plan suggests 5-10 req/s as a target;
#: the low end is taken because a daily catalogue enrichment has no reason
#: to push a public API harder than that.
DEFAULT_MAX_REQUESTS_PER_SECOND = float(
    os.environ.get("MOVIE_METADATA_MAX_RPS", "5") or 5
)

#: Total time this process is willing to spend asleep waiting on one
#: provider. Past this the provider is cooled down instead, so a provider
#: having a bad day cannot eat the scan's time budget.
MAX_SLEEP_PER_PROVIDER_SECONDS = 90.0

#: How long a provider stays cooled down after exhausting its retries, and
#: after an auth failure. Persisted, so a 429 late in one run is still
#: respected at the start of the next one.
RATE_LIMIT_COOLDOWN_SECONDS = 30 * 60
AUTH_FAILURE_COOLDOWN_SECONDS = 6 * 60 * 60

REQUEST_TIMEOUT_SECONDS = 8
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_RETRYABLE_STATUSES = frozenset({500, 502, 503, 504})
_AUTH_STATUSES = frozenset({401, 403})

STATUS_HEALTHY = "healthy"
STATUS_COOLING_DOWN = "cooling_down"
STATUS_UNHEALTHY_AUTH = "unhealthy_auth"

_METRIC_FIELDS = (
    "requests",
    "successes",
    "retries",
    "rate_limited",
    "auth_failures",
    "server_errors",
    "transport_errors",
    "not_found",
    "skipped_unavailable",
)

#: Process-local state. A scan is one process, so this starts clean every
#: run and is seeded from the persisted file on first use.
_STATE: Dict[str, Any] = {}
_LAST_REQUEST_AT: Dict[str, float] = {}
_SLEPT_SECONDS: Dict[str, float] = {}
_LOADED_FROM: Optional[str] = None


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _iso(moment: Optional[_dt.datetime] = None) -> str:
    return (moment or _now()).isoformat()


def _parse_stamp(value: Any) -> Optional[_dt.datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = _dt.datetime.fromisoformat(
            text[:-1] + "+00:00" if text.endswith("Z") else text
        )
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    return parsed


def load(path: Optional[str] = None) -> Dict[str, Any]:
    """Load persisted health. Missing/corrupt file -> empty, never raises."""
    target = path or DEFAULT_PATH
    try:
        with open(target, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return {"version": 1, "providers": {}, "cache": {}}
    if not isinstance(payload, dict):
        return {"version": 1, "providers": {}, "cache": {}}
    payload.setdefault("version", 1)
    if not isinstance(payload.get("providers"), dict):
        payload["providers"] = {}
    if not isinstance(payload.get("cache"), dict):
        payload["cache"] = {}
    return payload


def save(path: Optional[str] = None) -> bool:
    """Atomic write: temp file + fsync + os.replace."""
    target = path or DEFAULT_PATH
    store = _state(path)
    store["updated_at"] = _iso()
    try:
        target_dir = os.path.dirname(target) or "."
        os.makedirs(target_dir, exist_ok=True)
        temp_path = os.path.join(
            target_dir,
            f".{os.path.basename(target)}.{os.getpid()}.{time.time_ns()}.tmp",
        )
        try:
            with open(temp_path, "w", encoding="utf-8") as handle:
                json.dump(store, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, target)
        finally:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
    except OSError:
        return False
    return True


def _state(path: Optional[str] = None) -> Dict[str, Any]:
    global _STATE, _LOADED_FROM
    target = path or DEFAULT_PATH
    if not _STATE or _LOADED_FROM != target:
        _STATE = load(target)
        _LOADED_FROM = target
    return _STATE


def reset(path: Optional[str] = None) -> None:
    """Drop process-local state. For tests and for a fresh run."""
    global _STATE, _LOADED_FROM
    _STATE = {}
    _LOADED_FROM = None
    _LAST_REQUEST_AT.clear()
    _SLEPT_SECONDS.clear()
    if path is not None:
        _state(path)


def _provider_record(provider: str, path: Optional[str] = None) -> Dict[str, Any]:
    providers = _state(path).setdefault("providers", {})
    record = providers.get(provider)
    if not isinstance(record, dict):
        record = {"status": STATUS_HEALTHY}
        providers[provider] = record
    for field in _METRIC_FIELDS:
        record.setdefault(field, 0)
    record.setdefault("sleep_seconds", 0.0)
    record.setdefault("status", STATUS_HEALTHY)
    record.setdefault("ok", record["status"] == STATUS_HEALTHY)
    record.setdefault("last_error", None)
    return record


def _note_error(record: Dict[str, Any], description: str) -> None:
    """Record what went wrong, in words that can never carry a secret.

    Only a status class and a short phrase - never a URL, a header or a
    key, because this file is committed to the repository.
    """
    record["ok"] = False
    record["last_error"] = description
    record["last_error_at"] = _iso()


def _bump(record: Dict[str, Any], field: str, amount: int = 1) -> None:
    record[field] = int(record.get(field) or 0) + amount


def _peek_record(provider: str, path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The record if one exists - without creating one. Asking whether a
    provider is available must not itself add it to the health file."""
    record = (_state(path).get("providers") or {}).get(provider)
    return record if isinstance(record, dict) else None


def is_available(provider: str, *, path: Optional[str] = None) -> bool:
    """False while a provider is cooling down or known key-broken."""
    record = _peek_record(provider, path)
    if record is None:
        return True
    status = str(record.get("status") or STATUS_HEALTHY)
    if status == STATUS_HEALTHY:
        return True
    next_retry = _parse_stamp(record.get("next_retry_at"))
    if next_retry is None or _now() >= next_retry:
        record["status"] = STATUS_HEALTHY
        record["ok"] = True
        record.pop("next_retry_at", None)
        return True
    return False


def _mark_unavailable(record: Dict[str, Any], status: str, cooldown_seconds: float) -> None:
    record["status"] = status
    record["ok"] = False
    record["next_retry_at"] = _iso(_now() + _dt.timedelta(seconds=cooldown_seconds))


def _throttle(provider: str) -> None:
    """Hold the minimum interval between two requests to one provider."""
    rps = DEFAULT_MAX_REQUESTS_PER_SECOND
    if rps <= 0:
        return
    minimum_interval = 1.0 / rps
    last = _LAST_REQUEST_AT.get(provider)
    now = time.monotonic()
    if last is not None:
        waited = now - last
        if waited < minimum_interval:
            time.sleep(minimum_interval - waited)
    _LAST_REQUEST_AT[provider] = time.monotonic()


def _can_sleep(provider: str, seconds: float) -> bool:
    spent = _SLEPT_SECONDS.get(provider, 0.0)
    return (spent + seconds) <= MAX_SLEEP_PER_PROVIDER_SECONDS


def _sleep(provider: str, seconds: float, record: Dict[str, Any]) -> None:
    _SLEPT_SECONDS[provider] = _SLEPT_SECONDS.get(provider, 0.0) + seconds
    record["sleep_seconds"] = round(float(record.get("sleep_seconds") or 0.0) + seconds, 2)
    time.sleep(seconds)


def _retry_after_seconds(value: Any) -> Optional[float]:
    """Retry-After as seconds. Accepts the delta-seconds form; an HTTP-date
    is not parsed here and simply falls through to the backoff ladder."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        seconds = float(text)
    except ValueError:
        return None
    if seconds < 0:
        return None
    # A provider asking for an implausibly long wait is treated as "not now"
    # rather than obeyed literally inside one scan.
    return min(seconds, float(MAX_SLEEP_PER_PROVIDER_SECONDS))


def _perform_request(
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    body: Optional[Dict[str, Any]] = None,
    timeout: int = REQUEST_TIMEOUT_SECONDS,
) -> Tuple[int, Dict[str, Any], Optional[float]]:
    """One HTTP attempt. Returns (status, payload, retry_after_seconds).

    Status 0 means a transport-level failure (DNS, connection, timeout,
    undecodable body) - no HTTP status was ever seen.
    """
    request_headers = {"Accept": "application/json", "User-Agent": _USER_AGENT}
    if body is not None:
        request_headers["Content-Type"] = "application/json"
    request_headers.update(headers or {})
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        url, data=data, headers=request_headers, method="POST" if data else "GET"
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(2_000_000)
            status = int(getattr(response, "status", 200) or 200)
        payload = json.loads(raw.decode("utf-8", errors="replace"))
        return status, (payload if isinstance(payload, dict) else {}), None
    except urllib.error.HTTPError as error:
        retry_after = None
        try:
            retry_after = _retry_after_seconds(error.headers.get("Retry-After"))
        except Exception:  # noqa: BLE001 - a malformed header must not raise
            retry_after = None
        return int(getattr(error, "code", 0) or 0), {}, retry_after
    except (
        urllib.error.URLError,
        TimeoutError,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        ValueError,
    ):
        return 0, {}, None


def request_json(
    provider: str,
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    body: Optional[Dict[str, Any]] = None,
    timeout: int = REQUEST_TIMEOUT_SECONDS,
    path: Optional[str] = None,
) -> Dict[str, Any]:
    """One policy-governed request. Returns {} on any failure, never raises."""
    record = _provider_record(provider, path)
    if not is_available(provider, path=path):
        _bump(record, "skipped_unavailable")
        return {}

    for attempt in range(len(BACKOFF_LADDER_SECONDS) + 1):
        _throttle(provider)
        _bump(record, "requests")
        status, payload, retry_after = _perform_request(
            url, headers=headers, body=body, timeout=timeout
        )
        record["last_status"] = status

        if 200 <= status < 300:
            _bump(record, "successes")
            record["last_success"] = _iso()
            record["status"] = STATUS_HEALTHY
            record["ok"] = True
            record["last_error"] = None
            record.pop("next_retry_at", None)
            return payload

        if status in _AUTH_STATUSES:
            # Never retried: a rejected credential is not a transient fault,
            # and a retry storm against a suspended key is how it stays
            # suspended. The other providers carry on without this one.
            _bump(record, "auth_failures")
            record["last_auth_failure"] = _iso()
            _note_error(record, f"HTTP {status} - credential rejected")
            _mark_unavailable(record, STATUS_UNHEALTHY_AUTH, AUTH_FAILURE_COOLDOWN_SECONDS)
            print(
                f"   metadata provider {provider}: HTTP {status} - credential "
                f"rejected. Provider disabled for this run; replace the secret. "
                f"No key rotation is attempted by design."
            )
            return {}

        transient = status == 429 or status in _RETRYABLE_STATUSES or status == 0
        if not transient:
            # 404 and friends are a real answer: the title is simply not there.
            _bump(record, "not_found")
            return {}

        if status == 429:
            _bump(record, "rate_limited")
            record["last_429"] = _iso()
            _note_error(record, "HTTP 429 - rate limited")
        elif status == 0:
            _bump(record, "transport_errors")
            _note_error(record, "transport failure - no HTTP response")
        else:
            _bump(record, "server_errors")
            _note_error(record, f"HTTP {status} - provider server error")

        if attempt >= len(BACKOFF_LADDER_SECONDS):
            break
        delay = retry_after if retry_after is not None else float(BACKOFF_LADDER_SECONDS[attempt])
        if not _can_sleep(provider, delay):
            break
        _bump(record, "retries")
        _sleep(provider, delay, record)

    cooldown = (
        RATE_LIMIT_COOLDOWN_SECONDS
        if int(record.get("last_status") or 0) == 429
        else 60.0
    )
    _mark_unavailable(record, STATUS_COOLING_DOWN, cooldown)
    return {}


#: The providers a movie metadata lookup can draw on. If every one of them
#: is cooling down or key-broken there is nothing left to ask, and the
#: enrichment pass should stop rather than record failures.
METADATA_PROVIDERS = ("tmdb", "omdb", "cinemeta", "moviesdatabase", "fanart", "tvmaze", "anilist")


def any_metadata_provider_available(*, path: Optional[str] = None) -> bool:
    return any(is_available(provider, path=path) for provider in METADATA_PROVIDERS)


def record_cache_stats(summary: Dict[str, Any], *, path: Optional[str] = None) -> None:
    """Fold one enrich() summary into the run's cache metrics."""
    if not isinstance(summary, dict):
        return
    cache = _state(path).setdefault("cache", {})
    for key in ("cached_hits", "fetched", "failed", "skipped_budget", "skipped_cooldown", "refreshed"):
        value = summary.get(key)
        if isinstance(value, int):
            cache[key] = int(cache.get(key) or 0) + value
    cache["updated_at"] = _iso()


def metrics(*, path: Optional[str] = None) -> Dict[str, Any]:
    """A copy of the current counters, for logging and tests."""
    store = _state(path)
    return {
        "providers": json.loads(json.dumps(store.get("providers", {}))),
        "cache": json.loads(json.dumps(store.get("cache", {}))),
    }


def summary_line(*, path: Optional[str] = None) -> str:
    """One printable line: what the run asked of the providers."""
    store = _state(path)
    cache = store.get("cache", {}) or {}
    parts = [
        f"cache hits {int(cache.get('cached_hits') or 0)}",
        f"lookups {int(cache.get('fetched') or 0)}",
    ]
    for provider, record in sorted((store.get("providers") or {}).items()):
        if not isinstance(record, dict) or not int(record.get("requests") or 0):
            continue
        detail = f"{provider} {int(record.get('requests') or 0)}req"
        if int(record.get("rate_limited") or 0):
            detail += f"/{int(record['rate_limited'])}x429"
        if int(record.get("retries") or 0):
            detail += f"/{int(record['retries'])}retry"
        if str(record.get("status")) != STATUS_HEALTHY:
            detail += f"/{record.get('status')}"
        parts.append(detail)
    return ", ".join(parts)
