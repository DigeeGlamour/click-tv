#!/usr/bin/env python3
"""Validate the generated Click TV public build.

Usage:
    python3 scripts/validate-pages.py dist

This validator never modifies the build. It checks public-file safety,
manifest consistency, channel/movie/event payloads, link limits, and the
final HTTPS priority policy.

HTTPS policy:
- Channels and events remain strict: an equal/higher-confidence HTTPS backup
  must not remain behind an HTTP primary.
- Movies are browser-aware: a browser-friendly HTTP HLS/DASH/MP4/WebM source
  may remain primary when the equal/higher-confidence HTTPS backup is a less
  compatible MKV/AVI/WMV/FLV source.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


if hasattr(sys.stdout, "reconfigure"):
    # Windows PowerShell may default to cp1252, while channel names and
    # validator messages legitimately contain Bangla/Unicode text.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else "dist").resolve()

ERRORS: list[str] = []
WARNINGS: list[str] = []
COUNTS = {"channels": 0, "movies": 0, "series": 0, "episodes": 0, "events": 0}
PLAYBACK_IDS: set[str] = set()

CHANNELS = {
    "Bangla": "bangla",
    "Sports": "sports",
    "Indian": "indian",
    "Cartoon": "cartoon",
    "Islamic": "islamic",
    "Foreign News": "foreign-news",
    "Infotainments": "infotainments",
    "Other": "other",
}

MOVIES = {
    "Bangla": "bangla",
    "Hindi": "hindi",
    "English": "english",
    "Dubbed": "dubbed",
    "South Indian": "south-indian",
    "Premium": "premium",
    "Mix": "mix",
}

REQUIRED_FILES = (
    "index.html",
    "runtime-config.json",
    "app.webmanifest",
    "sw.js",
    "_headers",
    "assets/css/app.css",
    "assets/css/series.css",
    "assets/css/final-design.css",
    "assets/js/app.js",
    "assets/js/series.js",
    "data/manifest.json",
    "data/playback-sources.json",
    "data/today-match.json",
    "data/upcoming.json",
    "data/allowed-hosts.json",
    "data/series/manifest.json",
)

PRIVATE_DIRECTORIES = {
    ".github",
    "config",
    "manual",
    "reports",
    "scanner",
    "state",
    "tests",
    "working",
    "workers",
}

PRIVATE_FILES = {
    ".env",
    ".dev.vars",
    "requirements.txt",
    "scan.py",
    "wrangler.toml",
}

PRIVATE_SUFFIXES = {".py", ".pyc", ".pem", ".p12", ".pfx"}

HTTP_URL_PATTERN = re.compile(r"^https?://", re.IGNORECASE)

SECRET_KEY_PATTERN = re.compile(
    r"token|secret|password|authorization|cookie|private[_-]?key|api[_-]?key",
    re.IGNORECASE,
)

CONFIDENCE_RANK = {
    "verified_global": 6,
    "verified_bd": 6,
    "verified": 6,
    "verified_proxy": 5,
    "stale_last_good": 4,
    "geo_pending": 3,
    "bd_protected_pending": 3,
    "retryable_pending": 2,
    "host_deferred": 1,
    "metadata_only": 0,
    "": 0,
}

FAILED_STATUSES = {
    "failed",
    "failed_bd",
    "rejected_low_quality",
    "quarantine",
}

BROWSER_SUPPORT_RANK = {
    "hls": 70,
    "m3u8": 70,
    "dash": 68,
    "mpd": 68,
    "mp4": 60,
    "m4v": 60,
    "webm": 58,
    "mov": 48,
    "mpegts": 42,
    "mpeg-ts": 42,
    "ts": 42,
    "m2ts": 40,
    "mkv": 20,
    "matroska": 20,
    "avi": 12,
    "wmv": 10,
    "asf": 10,
    "flv": 8,
}


def add_error(message: str) -> None:
    ERRORS.append(message)


def add_warning(message: str) -> None:
    WARNINGS.append(message)


def relative_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def require_file(file_name: str) -> Path | None:
    path = ROOT / file_name

    if not path.is_file():
        add_error(f"Required file পাওয়া যায়নি: {file_name}")
        return None

    if path.stat().st_size == 0:
        add_error(f"Required file empty: {file_name}")
        return None

    return path


def load_json(path: Path | None, label: str) -> Any | None:
    if path is None:
        return None

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError as error:
        add_error(f"{label} UTF-8 নয়: {error}")
    except json.JSONDecodeError as error:
        add_error(
            f"{label} invalid JSON, line {error.lineno}, "
            f"column {error.colno}: {error.msg}"
        )
    except OSError as error:
        add_error(f"{label} পড়া যায়নি: {error}")

    return None


def resolve_public_path(value: Any, label: str) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        add_error(f"{label} path missing")
        return None

    raw_path = value.strip().replace("\\", "/")
    parsed = urlparse(raw_path)

    if parsed.scheme or parsed.netloc:
        add_error(f"{label} local path হওয়া উচিত: {raw_path}")
        return None

    path = (ROOT / parsed.path.lstrip("/")).resolve()

    try:
        path.relative_to(ROOT)
    except ValueError:
        add_error(f"{label} build root-এর বাইরে যাচ্ছে: {raw_path}")
        return None

    return path


def get_items(data: Any, *keys: str) -> list[Any] | None:
    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        for key in keys:
            value = data.get(key)
            if isinstance(value, list):
                return value

    return None


def get_primary_url(item: dict[str, Any]) -> str:
    for key in ("url", "stream_url", "link"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    return ""


def normalize_catalog_identity(value: Any) -> str:
    text = str(value or "").casefold()
    text = re.sub(r"\b(?:19|20)\d{2}\b", " ", text)
    text = re.sub(r"\b(?:4k|2k|uhd|fhd|full\s*hd|hd|sd|1080p?|720p?|576p?|480p?|360p?|web[- ]?dl|webrip|bluray|brrip|hdrip)\b", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def normalize_movie_identity(item: dict[str, Any]) -> str:
    name = str(item.get("name") or item.get("title") or "").strip()
    year_match = re.search(r"\b(?:19|20)\d{2}\b", name)
    year = str(item.get("year") or (year_match.group(0) if year_match else "")).strip()
    return f"{normalize_catalog_identity(name)}:{year}"


def normalized_primary_url(item: dict[str, Any]) -> str:
    return get_primary_url(item).split("|", 1)[0].strip().casefold()


def get_backup_objects(item: dict[str, Any]) -> list[dict[str, Any]]:
    raw_backups = item.get("backups", [])

    if not isinstance(raw_backups, list):
        return []

    results: list[dict[str, Any]] = []

    for backup in raw_backups:
        if isinstance(backup, str):
            url = backup.strip()
            if url:
                results.append({"url": url})
        elif isinstance(backup, dict):
            results.append(backup)

    return results


def declared_resolution_height(item: dict[str, Any]) -> int:
    try:
        height = int(item.get("resolution_height") or 0)
    except (TypeError, ValueError):
        height = 0
    if height > 0:
        return height

    text = " ".join(
        str(item.get(key) or "")
        for key in ("resolution", "quality", "label", "name", "url")
    )
    dimension = re.search(r"(?:^|\D)\d{3,4}\s*[xX]\s*(\d{3,4})(?:\D|$)", text)
    if dimension:
        return int(dimension.group(1))
    progressive = re.search(r"(?:^|\D)(\d{3,4})\s*[pP](?:\D|$)", text)
    if progressive:
        return int(progressive.group(1))
    if re.search(r"\b(?:4k|uhd)\b", text, re.IGNORECASE):
        return 2160
    if re.search(r"\b(?:2k|qhd)\b", text, re.IGNORECASE):
        return 1440
    if re.search(r"\b(?:fhd|full\s*hd)\b", text, re.IGNORECASE):
        return 1080
    if re.search(r"\bhd\b", text, re.IGNORECASE):
        return 720
    return 0


def stream_confidence(item: dict[str, Any]) -> int:
    status = str(item.get("verification_status") or "").strip().lower()

    if status in FAILED_STATUSES:
        return 0

    rank = CONFIDENCE_RANK.get(status, 0)
    confirmed = item.get("verified") is True or item.get("is_valid") is True
    publish_allowed = item.get("publish_allowed") is True

    if status in {
        "verified_global",
        "verified_bd",
        "verified",
        "verified_proxy",
    }:
        return rank if confirmed else 0

    if confirmed and not status:
        return 6

    if status in {
        "stale_last_good",
        "geo_pending",
        "bd_protected_pending",
        "retryable_pending",
        "host_deferred",
    }:
        return rank if publish_allowed else 0

    return rank


def normalize_media_hint(value: Any) -> str:
    if value is None:
        return ""

    normalized = str(value).strip().lower()

    if not normalized:
        return ""

    normalized = normalized.split(";", 1)[0].strip()

    aliases = {
        "application/vnd.apple.mpegurl": "hls",
        "application/x-mpegurl": "hls",
        "application/dash+xml": "dash",
        "video/mp2t": "mpegts",
        "video/x-matroska": "mkv",
        "video/quicktime": "mov",
        "video/x-msvideo": "avi",
        "video/x-ms-wmv": "wmv",
        "video/x-flv": "flv",
    }

    if normalized in aliases:
        return aliases[normalized]

    normalized = normalized.rsplit("/", 1)[-1].lstrip(".")
    return aliases.get(normalized, normalized)


def source_media_kind(source: dict[str, Any]) -> str:
    for key in (
        "stream_type",
        "format",
        "container",
        "extension",
        "ext",
        "mime_type",
        "content_type",
    ):
        hint = normalize_media_hint(source.get(key))
        if hint in BROWSER_SUPPORT_RANK:
            return hint

    url = get_primary_url(source)

    if not url:
        return ""

    try:
        path = urlparse(url).path.lower()
    except ValueError:
        path = url.lower()

    suffix = Path(path).suffix.lower().lstrip(".")

    if suffix in BROWSER_SUPPORT_RANK:
        return suffix

    lowered = url.lower()

    if ".m3u8" in lowered:
        return "hls"

    if ".mpd" in lowered:
        return "dash"

    return ""


def browser_support_score(source: dict[str, Any]) -> int:
    media_kind = source_media_kind(source)

    if media_kind:
        return BROWSER_SUPPORT_RANK.get(media_kind, 30)

    browser_support = str(source.get("browser_support") or "").strip().lower()
    browser_support_scores = {
        "preferred": 60,
        "supported": 52,
        "limited": 20,
        "unsupported": 0,
    }

    if browser_support in browser_support_scores:
        return browser_support_scores[browser_support]

    drm = str(source.get("drm") or source.get("drm_type") or "").strip().lower()

    if drm:
        return 55

    return 35


def browser_support_label(source: dict[str, Any]) -> str:
    media_kind = source_media_kind(source)

    if not media_kind:
        browser_support = str(source.get("browser_support") or "").strip()
        return browser_support or "unknown"

    if media_kind in {"hls", "m3u8"}:
        return "HLS"

    if media_kind in {"dash", "mpd"}:
        return "DASH"

    return media_kind.upper()


def validate_https_priority(
    item: dict[str, Any],
    primary_url: str,
    backup_objects: list[dict[str, Any]],
    label: str,
    name: str,
    media_kind: str,
) -> None:
    if not primary_url.lower().startswith("http://"):
        return

    https_backups = [
        backup
        for backup in backup_objects
        if get_primary_url(backup).lower().startswith("https://")
    ]

    if not https_backups:
        return

    primary_rank = stream_confidence(item)

    if media_kind != "movie":
        strongest_https_rank = max(
            stream_confidence(backup) for backup in https_backups
        )

        if strongest_https_rank > 0 and strongest_https_rank >= primary_rank:
            add_error(
                f"{label} সমমান বা বেশি বিশ্বাসযোগ্য HTTPS backup থাকা "
                f"সত্ত্বেও HTTP primary: {name}"
            )
        else:
            add_warning(
                f"{label} HTTP primary রাখা হয়েছে কারণ HTTPS backup "
                f"কম বিশ্বাসযোগ্য: {name}"
            )

        return

    qualifying_https = [
        backup
        for backup in https_backups
        if stream_confidence(backup) > 0
        and stream_confidence(backup) >= primary_rank
    ]

    if not qualifying_https:
        add_warning(
            f"{label} HTTP primary রাখা হয়েছে কারণ HTTPS backup "
            f"কম বিশ্বাসযোগ্য: {name}"
        )
        return

    primary_support = browser_support_score(item)
    strongest_https = max(
        qualifying_https,
        key=lambda backup: (
            browser_support_score(backup),
            stream_confidence(backup),
        ),
    )
    https_support = browser_support_score(strongest_https)

    if https_support >= primary_support:
        add_error(
            f"{label} সমমান বা বেশি বিশ্বাসযোগ্য এবং সমমান বা ভালো "
            f"browser-compatible HTTPS backup থাকা সত্ত্বেও HTTP primary: "
            f"{name}"
        )
        return

    add_warning(
        f"{label} HTTP primary রাখা হয়েছে কারণ এটি browser-এ বেশি "
        f"ব্যবহারযোগ্য ({browser_support_label(item)}) এবং HTTPS backup "
        f"কম compatible ({browser_support_label(strongest_https)}): {name}"
    )


def validate_stream_item(
    item: Any,
    label: str,
    *,
    allow_metadata_only: bool = False,
    media_kind: str = "stream",
) -> None:
    if not isinstance(item, dict):
        add_error(f"{label} item object নয়")
        return

    name_value = item.get("name")

    if not isinstance(name_value, str) or not name_value.strip():
        add_error(f"{label} name missing")
        name = "<unnamed>"
    else:
        name = name_value.strip()

    primary_url = get_primary_url(item)
    playback_id = str(item.get("playback_id") or "").strip()
    metadata_only = item.get("metadata_only") is True

    # Events are exempt on purpose. A live match feed is an ABR master or a
    # bare chunk list that rarely declares RESOLUTION, and the Today Match /
    # Upcoming sources are already curated high-resolution providers. Holding
    # events to a declared 720p only ever deleted matches that were live and
    # playable. Channels and movies keep the rule.
    if media_kind in {"channel", "movie"} and not (
        allow_metadata_only and metadata_only
    ):
        primary_height = declared_resolution_height(item)
        if primary_height < 720:
            add_error(
                f"{label} resolution must be known and at least 720p: "
                f"{name} ({primary_height or 'unknown'})"
            )

    if playback_id:
        if not re.fullmatch(r"ctv_[a-f0-9]{32}", playback_id):
            add_error(f"{label} invalid playback_id: {name}")
        elif playback_id not in PLAYBACK_IDS:
            add_error(f"{label} playback_id catalogue-এ নেই: {name}")

    if not primary_url and not playback_id and not (allow_metadata_only and metadata_only):
        add_error(f"{label} primary URL missing: {name}")
    elif primary_url and not HTTP_URL_PATTERN.match(primary_url):
        add_warning(f"{label} non-HTTP(S) URL: {name}")

    raw_backups = item.get("backups", [])

    if raw_backups is None:
        raw_backups = []

    if not isinstance(raw_backups, list):
        add_error(f"{label} backups array নয়: {name}")
        return

    if len(raw_backups) > 5:
        add_error(f"{label} ৫টির বেশি backup: {name} ({len(raw_backups)})")

    backup_objects = get_backup_objects(item)
    standby_objects = item.get("standby", [])
    if not isinstance(standby_objects, list):
        add_error(f"{label} standby array invalid: {name}")
        standby_objects = []
    for stream_number, stream in enumerate([*backup_objects, *standby_objects], start=1):
        if not isinstance(stream, dict):
            continue
        stream_height = declared_resolution_height(stream)
        # Same exemption as the primary stream above: an event's backup feed is
        # the same kind of undeclared-resolution live match link.
        if media_kind in {"channel", "movie"} and stream_height < 720:
            add_error(
                f"{label} backup/standby #{stream_number} is below 720p or unknown: "
                f"{name} ({stream_height or 'unknown'})"
            )
    backup_urls = [
        get_primary_url(backup)
        for backup in backup_objects
        if get_primary_url(backup)
    ]
    all_urls = [url for url in [primary_url, *backup_urls] if url]

    if len(all_urls) > 6:
        add_error(f"{label} primaryসহ মোট ৬টির বেশি link: {name}")

    active_streams = [item, *backup_objects]
    active_identities = [
        (
            get_primary_url(stream),
            str(stream.get("playback_id") or "").strip(),
            json.dumps(stream.get("headers") or {}, sort_keys=True),
            json.dumps(stream.get("drm") or {}, sort_keys=True),
        )
        for stream in active_streams
    ]
    if len(set(active_identities)) != len(active_identities):
        add_warning(f"{label} duplicate primary/backup configuration: {name}")

    validate_https_priority(
        item,
        primary_url,
        backup_objects,
        label,
        name,
        media_kind,
    )


def validate_public_safety() -> None:
    if not ROOT.is_dir():
        add_error(f"Build folder পাওয়া যায়নি: {ROOT}")
        return

    for directory_name in PRIVATE_DIRECTORIES:
        if (ROOT / directory_name).exists():
            add_error(f"Private folder public build-এ আছে: {directory_name}/")

    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue

        if path.name.lower() in PRIVATE_FILES:
            add_error(f"Private file public build-এ আছে: {relative_path(path)}")

        if path.suffix.lower() in PRIVATE_SUFFIXES:
            add_error(
                f"Source/private file public build-এ আছে: {relative_path(path)}"
            )

        if path.name in {".gitkeep", ".DS_Store", "Thumbs.db"}:
            add_warning(f"অপ্রয়োজনীয় file আছে: {relative_path(path)}")


def validate_runtime_config() -> None:
    data = load_json(require_file("runtime-config.json"), "runtime-config.json")

    if not isinstance(data, dict):
        if data is not None:
            add_error("runtime-config.json root object হতে হবে")
        return

    if data.get("schema_version") != 1:
        add_error("runtime-config.json schema_version অবশ্যই 1 হতে হবে")

    data_manifest = data.get("data_manifest")

    if (
        not isinstance(data_manifest, str)
        or data_manifest.lstrip("/") != "data/manifest.json"
    ):
        add_error("data_manifest অবশ্যই /data/manifest.json হতে হবে")

    if data.get("default_network_mode") not in {
        "auto",
        "stable",
        "low",
        "low-delay",
        "low_delay",
    }:
        add_error(
            "default_network_mode auto, stable অথবা low-delay হতে হবে"
        )

    play_proxies = data.get("play_proxies")

    if not isinstance(play_proxies, list) or not play_proxies:
        add_error("play_proxies non-empty array হতে হবে")
    else:
        seen: set[str] = set()

        for number, proxy in enumerate(play_proxies, start=1):
            if not isinstance(proxy, str) or not proxy.startswith("https://"):
                add_error(f"play_proxies[{number}] valid HTTPS URL নয়")
                continue

            normalized = proxy.rstrip("/")

            if normalized in seen:
                add_error(f"Duplicate playback proxy: {normalized}")

            seen.add(normalized)

    for key in data:
        if SECRET_KEY_PATTERN.search(str(key)):
            add_error(f"runtime-config.json-এ sensitive key আছে: {key}")

    serialized = json.dumps(data, ensure_ascii=False).lower()

    if "/verify" in serialized or "live-checker-workerjs" in serialized:
        add_error("runtime-config.json-এ verification API রাখা যাবে না")


def validate_webmanifest() -> None:
    data = load_json(require_file("app.webmanifest"), "app.webmanifest")

    if not isinstance(data, dict):
        if data is not None:
            add_error("app.webmanifest root object হতে হবে")
        return

    for field in ("name", "short_name", "start_url", "display"):
        value = data.get(field)

        if not isinstance(value, str) or not value.strip():
            add_error(f"app.webmanifest missing field: {field}")

    icons = data.get("icons")

    if not isinstance(icons, list) or not icons:
        add_warning("PWA icons নেই")
        return

    for number, icon in enumerate(icons, start=1):
        if not isinstance(icon, dict):
            add_error(f"app.webmanifest icons[{number}] object নয়")
            continue

        icon_path = resolve_public_path(icon.get("src"), f"icons[{number}].src")

        if icon_path is not None and not icon_path.is_file():
            add_warning(
                f"PWA icon এখনো পাওয়া যায়নি: {relative_path(icon_path)}"
            )


def validate_frontend_files() -> None:
    index_path = require_file("index.html")
    service_worker_path = require_file("sw.js")
    require_file("_headers")

    if index_path is not None:
        try:
            index_html = index_path.read_text(encoding="utf-8")
        except OSError as error:
            add_error(f"index.html পড়া যায়নি: {error}")
            index_html = ""

        for reference in (
            "runtime-config.json",
            "data/manifest.json",
            "app.webmanifest",
            "sw.js",
            "assets/css/final-design.css",
            "assets/js/app.js",
            "assets/js/series.js",
        ):
            if reference not in index_html:
                add_error(f"index.html-এ required reference নেই: {reference}")

        for marker in (
            "API_URLS",
            "live-checker-workerjs",
            "live-checker-2-workerjs",
            "VERIFY_TOKEN",
            "const swCode =",
            "navigator.serviceWorker.register(URL.createObjectURL",
        ):
            if marker in index_html:
                add_error(f"index.html-এ নিষিদ্ধ পুরোনো logic আছে: {marker}")

    if service_worker_path is None:
        return

    try:
        service_worker_code = service_worker_path.read_text(encoding="utf-8")
    except OSError as error:
        add_error(f"sw.js পড়া যায়নি: {error}")
        return

    has_fetch_handler = (
        'addEventListener("fetch"' in service_worker_code
        or "addEventListener('fetch'" in service_worker_code
    )

    if not has_fetch_handler:
        add_error("sw.js-এ fetch handler নেই")

    if ".m3u8" not in service_worker_code or ".ts" not in service_worker_code:
        add_error("sw.js stream bypass rules অসম্পূর্ণ")


def validate_playback_catalog() -> None:
    path = require_file("data/playback-sources.json")
    data = load_json(path, "data/playback-sources.json")
    if not isinstance(data, dict):
        if data is not None:
            add_error("data/playback-sources.json root object হতে হবে")
        return
    if data.get("schema_version") != 1:
        add_error("data/playback-sources.json schema_version অবশ্যই 1 হতে হবে")
    records = data.get("records")
    if not isinstance(records, dict):
        add_error("data/playback-sources.json records object হতে হবে")
        return
    if data.get("count") != len(records):
        add_error("data/playback-sources.json count records-এর সঙ্গে মিলছে না")

    for playback_id, profile in records.items():
        if not re.fullmatch(r"ctv_[a-f0-9]{32}", str(playback_id)):
            add_error(f"Playback catalogue invalid ID: {playback_id}")
            continue
        PLAYBACK_IDS.add(str(playback_id))
        if not isinstance(profile, dict):
            add_error(f"Playback catalogue profile object নয়: {playback_id}")
            continue
        if profile.get("status") != "active":
            add_error(f"Playback catalogue profile active নয়: {playback_id}")
        url = profile.get("url")
        if not isinstance(url, str) or not HTTP_URL_PATTERN.match(url.strip()):
            add_error(f"Playback catalogue URL invalid: {playback_id}")
        headers = profile.get("headers", {})
        if not isinstance(headers, dict):
            add_error(f"Playback catalogue headers object নয়: {playback_id}")


def validate_allowed_hosts() -> None:
    path = require_file("data/allowed-hosts.json")
    data = load_json(path, "data/allowed-hosts.json")

    if not isinstance(data, dict):
        if data is not None:
            add_error("data/allowed-hosts.json root object হতে হবে")
        return

    hosts = data.get("hosts")

    if not isinstance(hosts, list) or not hosts:
        add_error("data/allowed-hosts.json hosts non-empty array হতে হবে")
        return

    normalized_hosts: list[str] = []

    for number, host in enumerate(hosts, start=1):
        if not isinstance(host, str) or not host.strip():
            add_error(f"allowed-hosts host #{number} invalid")
            continue

        normalized = host.strip().lower()

        if "://" in normalized or "/" in normalized:
            add_error(f"allowed-hosts host URL নয়, hostname হতে হবে: {host}")
            continue

        normalized_hosts.append(normalized)

    if len(normalized_hosts) != len(set(normalized_hosts)):
        add_error("data/allowed-hosts.json-এ duplicate host আছে")

    if data.get("count") != len(hosts):
        add_error("data/allowed-hosts.json count mismatch")


def validate_channel_category(
    category_name: str,
    category_slug: str,
    manifest_entry: Any,
) -> None:
    if not isinstance(manifest_entry, dict):
        add_error(f"manifest.channels.{category_name} object নয়")
        return

    channel_path = resolve_public_path(
        manifest_entry.get("url"),
        f"channels.{category_name}.url",
    )

    if channel_path is None:
        return

    expected = f"data/channels/{category_slug}.json"

    if relative_path(channel_path) != expected:
        add_error(
            f"{category_name} channel path ভুল: {relative_path(channel_path)}"
        )

    if not channel_path.is_file():
        add_error(f"Channel JSON পাওয়া যায়নি: {relative_path(channel_path)}")
        return

    data = load_json(channel_path, relative_path(channel_path))
    items = get_items(data, "channels", "items")

    if not isinstance(data, dict) or items is None:
        add_error(f"{relative_path(channel_path)} valid channel object নয়")
        return

    actual_count = len(items)

    if data.get("count") != actual_count:
        add_error(f"{relative_path(channel_path)} count mismatch")

    if manifest_entry.get("count") != actual_count:
        add_error(f"manifest {category_name} count mismatch")

    seen_names: dict[str, str] = {}
    seen_urls: dict[str, str] = {}
    for number, item in enumerate(items, start=1):
        validate_stream_item(
            item,
            f"{category_name} channel #{number}",
            media_kind="channel",
        )
        if isinstance(item, dict):
            name = str(item.get("name") or item.get("title") or "").strip()
            name_key = normalize_catalog_identity(name)
            url_key = normalized_primary_url(item)
            if name_key and name_key in seen_names:
                add_error(f"{category_name} duplicate channel name: {seen_names[name_key]} / {name}")
            elif name_key:
                seen_names[name_key] = name
            if url_key and url_key in seen_urls:
                add_error(f"{category_name} duplicate channel URL: {seen_urls[url_key]} / {name}")
            elif url_key:
                seen_urls[url_key] = name

    COUNTS["channels"] += actual_count


def validate_event_file(
    manifest: dict[str, Any],
    manifest_key: str,
    expected_path: str,
    allow_metadata_only: bool,
) -> None:
    entry = manifest.get(manifest_key)

    if not isinstance(entry, dict):
        add_error(f"manifest.{manifest_key} object নয়")
        return

    event_path = resolve_public_path(
        entry.get("url"),
        f"manifest.{manifest_key}.url",
    )

    if event_path is None:
        return

    if relative_path(event_path) != expected_path:
        add_error(
            f"manifest.{manifest_key}.url ভুল: {relative_path(event_path)}"
        )

    if not event_path.is_file():
        add_error(f"Event JSON পাওয়া যায়নি: {relative_path(event_path)}")
        return

    data = load_json(event_path, relative_path(event_path))
    items = get_items(data, "items", "events")

    if not isinstance(data, dict) or items is None:
        add_error(f"{relative_path(event_path)} valid event object নয়")
        return

    actual_count = len(items)

    if data.get("count") != actual_count:
        add_error(f"{relative_path(event_path)} count mismatch")

    if entry.get("count") != actual_count:
        add_error(f"manifest.{manifest_key} count mismatch")

    if bool(entry.get("visible")) != (actual_count > 0):
        add_warning(
            f"manifest.{manifest_key}.visible count-এর সঙ্গে মিলছে না"
        )

    for number, item in enumerate(items, start=1):
        validate_stream_item(
            item,
            f"{manifest_key} event #{number}",
            allow_metadata_only=allow_metadata_only,
            media_kind="event",
        )

    COUNTS["events"] += actual_count


def resolve_movie_page_path(
    index_path: Path,
    page_entry: dict[str, Any],
    category_name: str,
    page_number: int,
) -> Path | None:
    value = page_entry.get("path") or page_entry.get("file")

    if isinstance(value, str) and "/" not in value and "\\" not in value:
        path = (index_path.parent / value).resolve()

        try:
            path.relative_to(ROOT)
        except ValueError:
            add_error(
                f"{category_name} page {page_number} build root-এর বাইরে যাচ্ছে"
            )
            return None

        return path

    return resolve_public_path(
        value,
        f"{category_name} page {page_number}.path",
    )


def validate_movie_category(
    category_name: str,
    category_slug: str,
    manifest_entry: Any,
) -> None:
    if not isinstance(manifest_entry, dict):
        add_error(f"manifest.movies.{category_name} object নয়")
        return

    index_path = resolve_public_path(
        manifest_entry.get("index"),
        f"movies.{category_name}.index",
    )

    if index_path is None:
        return

    expected_index = f"data/movies/{category_slug}/index.json"

    if relative_path(index_path) != expected_index:
        add_error(
            f"{category_name} movie index path ভুল: {relative_path(index_path)}"
        )

    if not index_path.is_file():
        add_error(f"Movie index পাওয়া যায়নি: {relative_path(index_path)}")
        return

    index_data = load_json(index_path, relative_path(index_path))

    if not isinstance(index_data, dict) or not isinstance(
        index_data.get("pages"), list
    ):
        add_error(f"{relative_path(index_path)} valid movie index নয়")
        return

    pages = index_data["pages"]

    if index_data.get("slug") != category_slug:
        add_error(f"{relative_path(index_path)} slug mismatch")

    if index_data.get("total_pages") != len(pages):
        add_error(f"{category_name} index total_pages mismatch")

    if manifest_entry.get("total_pages") != len(pages):
        add_error(f"{category_name} manifest total_pages mismatch")

    total_items = 0
    seen_numbers: set[int] = set()
    seen_movie_names: dict[str, str] = {}
    seen_movie_urls: dict[str, str] = {}

    for position, page_entry in enumerate(pages, start=1):
        if not isinstance(page_entry, dict) or not isinstance(
            page_entry.get("page"), int
        ):
            add_error(f"{category_name} page entry #{position} invalid")
            continue

        page_number = page_entry["page"]

        if page_number in seen_numbers:
            add_error(f"{category_name} duplicate page number: {page_number}")

        seen_numbers.add(page_number)

        page_path = resolve_movie_page_path(
            index_path,
            page_entry,
            category_name,
            page_number,
        )

        if page_path is None:
            continue

        if not page_path.is_file():
            add_error(f"Movie page পাওয়া যায়নি: {relative_path(page_path)}")
            continue

        page_data = load_json(page_path, relative_path(page_path))
        items = get_items(page_data, "items", "movies")

        if not isinstance(page_data, dict) or items is None:
            add_error(f"{relative_path(page_path)} valid movie page নয়")
            continue

        page_count = len(items)
        total_items += page_count

        if page_data.get("count") != page_count:
            add_error(
                f"{category_name} page {page_number} payload count mismatch"
            )

        if page_entry.get("count") != page_count:
            add_error(
                f"{category_name} page {page_number} index count mismatch"
            )

        if page_data.get("page") != page_number:
            add_error(f"{relative_path(page_path)} page number mismatch")

        for item_number, item in enumerate(items, start=1):
            validate_stream_item(
                item,
                (
                    f"{category_name} movie page {page_number} "
                    f"item #{item_number}"
                ),
                media_kind="movie",
            )
            if isinstance(item, dict):
                name = str(item.get("name") or item.get("title") or "").strip()
                name_key = normalize_movie_identity(item)
                url_key = normalized_primary_url(item)
                if name_key and name_key in seen_movie_names:
                    add_error(f"{category_name} duplicate movie name: {seen_movie_names[name_key]} / {name}")
                elif name_key:
                    seen_movie_names[name_key] = name
                if url_key and url_key in seen_movie_urls:
                    add_error(f"{category_name} duplicate movie URL: {seen_movie_urls[url_key]} / {name}")
                elif url_key:
                    seen_movie_urls[url_key] = name

    expected_numbers = set(range(1, len(pages) + 1))

    if seen_numbers != expected_numbers:
        add_error(f"{category_name} movie page numbering ধারাবাহিক নয়")

    if index_data.get("count") != total_items:
        add_error(f"{category_name} movie index total count mismatch")

    if manifest_entry.get("count") != total_items:
        add_error(f"{category_name} movie manifest total count mismatch")

    COUNTS["movies"] += total_items



def validate_series_data() -> None:
    manifest_path = require_file("data/series/manifest.json")
    series_manifest = load_json(manifest_path, "data/series/manifest.json")
    if not isinstance(series_manifest, dict):
        add_error("data/series/manifest.json root object হতে হবে")
        return

    categories = series_manifest.get("categories")
    if not isinstance(categories, dict):
        add_error("data/series/manifest.json categories object missing")
        return

    total_series = 0
    total_episodes = 0
    for category_name, slug in MOVIES.items():
        entry = categories.get(category_name)
        if not isinstance(entry, dict):
            add_error(f"Series category missing: {category_name}")
            continue
        index_path = resolve_public_path(entry.get("index"), f"series.{category_name}.index")
        expected = f"data/series/{slug}/index.json"
        if index_path is None:
            continue
        if relative_path(index_path) != expected:
            add_error(f"{category_name} series index path ভুল: {relative_path(index_path)}")
        if not index_path.is_file():
            add_error(f"Series index পাওয়া যায়নি: {expected}")
            continue
        index_data = load_json(index_path, expected)
        items = index_data.get("items") if isinstance(index_data, dict) else None
        if not isinstance(items, list):
            add_error(f"{expected} items array নয়")
            continue
        if index_data.get("count") != len(items) or entry.get("count") != len(items):
            add_error(f"{category_name} series count mismatch")
        total_series += len(items)
        for number, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                add_error(f"{category_name} series #{number} object নয়")
                continue
            detail_path = resolve_public_path(item.get("series_manifest"), f"{category_name} series #{number}.series_manifest")
            if detail_path is None or not detail_path.is_file():
                add_error(f"{category_name} series detail missing: {item.get('name', number)}")
                continue
            detail = load_json(detail_path, relative_path(detail_path))
            seasons = detail.get("seasons") if isinstance(detail, dict) else None
            if not isinstance(seasons, list):
                add_error(f"{relative_path(detail_path)} seasons array নয়")
                continue
            counted = 0
            for season in seasons:
                if not isinstance(season, dict):
                    continue
                season_path = resolve_public_path(season.get("path"), "series season path")
                if season_path is None or not season_path.is_file():
                    add_error(f"Series season file missing: {season.get('path')}")
                    continue
                payload = load_json(season_path, relative_path(season_path))
                episodes = payload.get("items") if isinstance(payload, dict) else None
                if not isinstance(episodes, list):
                    add_error(f"{relative_path(season_path)} items array নয়")
                    continue
                counted += len(episodes)
                for episode_number, episode in enumerate(episodes, start=1):
                    validate_stream_item(
                        episode,
                        f"{category_name} episode #{episode_number}",
                        media_kind="movie",
                    )
            if int(item.get("total_episodes") or 0) != counted:
                add_error(f"Series total_episodes mismatch: {item.get('name', number)}")
            total_episodes += counted

    declared_series = int(
        series_manifest.get("total_series")
        if series_manifest.get("total_series") is not None
        else series_manifest.get("count") or 0
    )
    if declared_series != total_series:
        add_error("data/series/manifest.json total count mismatch")

    declared_episodes = series_manifest.get("total_episodes")
    if declared_episodes is not None and int(declared_episodes or 0) != total_episodes:
        add_error("data/series/manifest.json total episode count mismatch")
    COUNTS["series"] += total_series
    COUNTS["episodes"] += total_episodes


def validate_data_manifest() -> None:
    manifest = load_json(
        require_file("data/manifest.json"),
        "data/manifest.json",
    )

    if not isinstance(manifest, dict):
        if manifest is not None:
            add_error("data/manifest.json root object হতে হবে")
        return

    if manifest.get("schema_version") != 1:
        add_error("data/manifest.json schema_version অবশ্যই 1 হতে হবে")

    channel_manifest = manifest.get("channels")
    movie_manifest = manifest.get("movies")

    if not isinstance(channel_manifest, dict):
        add_error("manifest channels object missing")
        channel_manifest = {}

    if not isinstance(movie_manifest, dict):
        add_error("manifest movies object missing")
        movie_manifest = {}

    for category_name in [*channel_manifest, *movie_manifest]:
        if str(category_name).strip().lower() in {"all", "movie", "movies"}:
            add_error(f"নিষিদ্ধ category manifest-এ আছে: {category_name}")

    missing_channels = set(CHANNELS) - set(channel_manifest)
    missing_movies = set(MOVIES) - set(movie_manifest)
    extra_movies = set(movie_manifest) - set(MOVIES)

    if missing_channels:
        add_error(f"Channel category missing: {sorted(missing_channels)}")

    if missing_movies:
        add_error(f"Movie category missing: {sorted(missing_movies)}")

    if extra_movies:
        add_error(f"অনুমোদিত নয় এমন movie category: {sorted(extra_movies)}")

    for category_name, slug in CHANNELS.items():
        if category_name in channel_manifest:
            validate_channel_category(
                category_name,
                slug,
                channel_manifest[category_name],
            )

    for category_name, slug in MOVIES.items():
        if category_name in movie_manifest:
            validate_movie_category(
                category_name,
                slug,
                movie_manifest[category_name],
            )

    validate_event_file(
        manifest,
        "today_match",
        "data/today-match.json",
        False,
    )
    validate_event_file(
        manifest,
        "upcoming",
        "data/upcoming.json",
        True,
    )

    validate_series_data()


def print_summary() -> None:
    print("\n[Click TV Validator] Summary")
    print(f"  Channels: {COUNTS['channels']}")
    print(f"  Movies: {COUNTS['movies']}")
    print(f"  Series: {COUNTS['series']}")
    print(f"  Episodes: {COUNTS['episodes']}")
    print(f"  Events: {COUNTS['events']}")
    print(f"  Warnings: {len(WARNINGS)}")
    print(f"  Errors: {len(ERRORS)}")

    if WARNINGS:
        print("\nWarnings:")
        for message in WARNINGS:
            print(f"  [WARN] {message}")

    if ERRORS:
        print("\nErrors:")
        for message in ERRORS:
            print(f"  [ERROR] {message}")


def main() -> int:
    print(f"[Click TV Validator] Build folder: {ROOT}")

    validate_public_safety()

    for file_name in REQUIRED_FILES:
        require_file(file_name)

    validate_runtime_config()
    validate_webmanifest()
    validate_frontend_files()
    validate_playback_catalog()
    validate_allowed_hosts()
    validate_data_manifest()
    print_summary()

    if ERRORS:
        print("\n[Click TV Validator] Validation failed.")
        return 1

    print("\n[Click TV Validator] Validation successful.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
