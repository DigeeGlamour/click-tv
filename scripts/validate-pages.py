#!/usr/bin/env python3
"""Click TV public build validator.

Usage:
    python3 scripts/validate-pages.py dist
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else "dist").resolve()

ERRORS: list[str] = []
WARNINGS: list[str] = []

COUNTS = {
    "channels": 0,
    "movies": 0,
    "events": 0,
}


CHANNELS = {
    "Bangla": "bangla",
    "Sports": "sports",
    "Indian": "indian",
    "Cartoon": "cartoon",
    "Islamic": "islamic",
    "Foreign News": "foreign-news",
}


MOVIES = {
    "Bangla": "bangla",
    "Hindi": "hindi",
    "English": "english",
    "Dubbed": "dubbed",
    "South Indian": "south-indian",
    "Mix": "mix",
}


REQUIRED_FILES = (
    "index.html",
    "runtime-config.json",
    "app.webmanifest",
    "sw.js",
    "_headers",
    "data/manifest.json",
    "data/today-match.json",
    "data/upcoming.json",
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


PRIVATE_SUFFIXES = {
    ".py",
    ".pyc",
    ".pem",
    ".p12",
    ".pfx",
}


HTTP_URL_PATTERN = re.compile(
    r"^https?://",
    re.IGNORECASE,
)


SECRET_KEY_PATTERN = re.compile(
    (
        r"token|secret|password|authorization|cookie|"
        r"private[_-]?key|api[_-]?key"
    ),
    re.IGNORECASE,
)


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
        add_error(
            f"Required file পাওয়া যায়নি: {file_name}"
        )
        return None

    if path.stat().st_size == 0:
        add_error(
            f"Required file empty: {file_name}"
        )
        return None

    return path


def load_json(
    path: Path | None,
    label: str,
) -> Any | None:
    if path is None:
        return None

    try:
        return json.loads(
            path.read_text(encoding="utf-8")
        )

    except UnicodeDecodeError as error:
        add_error(
            f"{label} UTF-8 নয়: {error}"
        )

    except json.JSONDecodeError as error:
        add_error(
            f"{label} invalid JSON, "
            f"line {error.lineno}, "
            f"column {error.colno}: "
            f"{error.msg}"
        )

    except OSError as error:
        add_error(
            f"{label} পড়া যায়নি: {error}"
        )

    return None


def resolve_public_path(
    value: Any,
    label: str,
) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        add_error(
            f"{label} path missing"
        )
        return None

    raw_path = value.strip().replace("\\", "/")
    parsed = urlparse(raw_path)

    if parsed.scheme or parsed.netloc:
        add_error(
            f"{label} local path হওয়া উচিত: {raw_path}"
        )
        return None

    path = (
        ROOT / parsed.path.lstrip("/")
    ).resolve()

    try:
        path.relative_to(ROOT)

    except ValueError:
        add_error(
            f"{label} build root-এর বাইরে যাচ্ছে: "
            f"{raw_path}"
        )
        return None

    return path


def get_items(
    data: Any,
    *keys: str,
) -> list[Any] | None:
    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        for key in keys:
            value = data.get(key)

            if isinstance(value, list):
                return value

    return None


def get_primary_url(
    item: dict[str, Any],
) -> str:
    for key in (
        "url",
        "stream_url",
        "link",
    ):
        value = item.get(key)

        if isinstance(value, str) and value.strip():
            return value.strip()

    return ""


def get_backup_urls(
    item: dict[str, Any],
) -> list[str]:
    urls: list[str] = []

    raw_backups = item.get(
        "backups",
        [],
    )

    if not isinstance(raw_backups, list):
        return urls

    for backup in raw_backups:
        if isinstance(backup, str):
            if backup.strip():
                urls.append(
                    backup.strip()
                )

        elif isinstance(backup, dict):
            backup_url = get_primary_url(
                backup
            )

            if backup_url:
                urls.append(
                    backup_url
                )

    return urls


def validate_stream_item(
    item: Any,
    label: str,
    allow_metadata_only: bool = False,
) -> None:
    if not isinstance(item, dict):
        add_error(
            f"{label} item object নয়"
        )
        return

    name = item.get("name")

    if not isinstance(name, str) or not name.strip():
        add_error(
            f"{label} name missing"
        )
        name = "<unnamed>"

    primary_url = get_primary_url(
        item
    )

    metadata_only = (
        item.get("metadata_only") is True
    )

    if (
        not primary_url
        and not (
            allow_metadata_only
            and metadata_only
        )
    ):
        add_error(
            f"{label} primary URL missing: {name}"
        )

    elif (
        primary_url
        and not HTTP_URL_PATTERN.match(
            primary_url
        )
    ):
        add_warning(
            f"{label} non-HTTP(S) URL: {name}"
        )

    raw_backups = item.get(
        "backups",
        [],
    )

    if raw_backups is None:
        raw_backups = []

    if not isinstance(raw_backups, list):
        add_error(
            f"{label} backups array নয়: {name}"
        )
        return

    if len(raw_backups) > 5:
        add_error(
            f"{label} ৫টির বেশি backup: "
            f"{name} ({len(raw_backups)})"
        )

    backup_urls = get_backup_urls(
        item
    )

    all_urls = [
        url
        for url in [
            primary_url,
            *backup_urls,
        ]
        if url
    ]

    if len(all_urls) > 6:
        add_error(
            f"{label} primaryসহ মোট "
            f"৬টির বেশি link: {name}"
        )

    if len(set(all_urls)) != len(all_urls):
        add_warning(
            f"{label} duplicate primary/backup "
            f"link: {name}"
        )

    if (
        primary_url.startswith("http://")
        and any(
            url.startswith("https://")
            for url in backup_urls
        )
    ):
        add_error(
            f"{label} HTTPS backup থাকা সত্ত্বেও "
            f"HTTP primary: {name}"
        )


def validate_public_safety() -> None:
    if not ROOT.is_dir():
        add_error(
            f"Build folder পাওয়া যায়নি: {ROOT}"
        )
        return

    for directory_name in PRIVATE_DIRECTORIES:
        directory_path = (
            ROOT / directory_name
        )

        if directory_path.exists():
            add_error(
                "Private folder public build-এ আছে: "
                f"{directory_name}/"
            )

    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue

        if path.name.lower() in PRIVATE_FILES:
            add_error(
                "Private file public build-এ আছে: "
                f"{relative_path(path)}"
            )

        if path.suffix.lower() in PRIVATE_SUFFIXES:
            add_error(
                "Source/private file public build-এ আছে: "
                f"{relative_path(path)}"
            )

        if path.name in {
            ".gitkeep",
            ".DS_Store",
            "Thumbs.db",
        }:
            add_warning(
                "অপ্রয়োজনীয় file আছে: "
                f"{relative_path(path)}"
            )


def validate_runtime_config() -> None:
    data = load_json(
        require_file(
            "runtime-config.json"
        ),
        "runtime-config.json",
    )

    if not isinstance(data, dict):
        if data is not None:
            add_error(
                "runtime-config.json root object হতে হবে"
            )

        return

    if data.get("schema_version") != 1:
        add_error(
            "runtime-config.json schema_version "
            "অবশ্যই 1 হতে হবে"
        )

    data_manifest = data.get(
        "data_manifest"
    )

    if (
        not isinstance(
            data_manifest,
            str,
        )
        or data_manifest.lstrip("/")
        != "data/manifest.json"
    ):
        add_error(
            "data_manifest অবশ্যই "
            "/data/manifest.json হতে হবে"
        )

    network_mode = data.get(
        "default_network_mode"
    )

    if network_mode not in {
        "auto",
        "stable",
        "low",
    }:
        add_error(
            "default_network_mode auto, stable "
            "অথবা low হতে হবে"
        )

    play_proxies = data.get(
        "play_proxies"
    )

    if (
        not isinstance(
            play_proxies,
            list,
        )
        or not play_proxies
    ):
        add_error(
            "play_proxies non-empty array হতে হবে"
        )

    else:
        seen_proxies: set[str] = set()

        for number, proxy in enumerate(
            play_proxies,
            start=1,
        ):
            if (
                not isinstance(proxy, str)
                or not proxy.startswith(
                    "https://"
                )
            ):
                add_error(
                    f"play_proxies[{number}] "
                    "valid HTTPS URL নয়"
                )
                continue

            normalized_proxy = (
                proxy.rstrip("/")
            )

            if normalized_proxy in seen_proxies:
                add_error(
                    "Duplicate playback proxy: "
                    f"{normalized_proxy}"
                )

            seen_proxies.add(
                normalized_proxy
            )

    for key in data:
        if SECRET_KEY_PATTERN.search(
            str(key)
        ):
            add_error(
                "runtime-config.json-এ "
                f"sensitive key আছে: {key}"
            )

    serialized = json.dumps(
        data,
        ensure_ascii=False,
    ).lower()

    if (
        "/verify" in serialized
        or "live-checker-workerjs"
        in serialized
    ):
        add_error(
            "runtime-config.json-এ verification "
            "API রাখা যাবে না"
        )


def validate_webmanifest() -> None:
    data = load_json(
        require_file(
            "app.webmanifest"
        ),
        "app.webmanifest",
    )

    if not isinstance(data, dict):
        if data is not None:
            add_error(
                "app.webmanifest root object হতে হবে"
            )

        return

    for field in (
        "name",
        "short_name",
        "start_url",
        "display",
    ):
        value = data.get(field)

        if (
            not isinstance(value, str)
            or not value.strip()
        ):
            add_error(
                "app.webmanifest missing field: "
                f"{field}"
            )

    icons = data.get("icons")

    if (
        not isinstance(icons, list)
        or not icons
    ):
        add_warning(
            "PWA icons নেই"
        )
        return

    for number, icon in enumerate(
        icons,
        start=1,
    ):
        if not isinstance(icon, dict):
            add_error(
                f"app.webmanifest icons[{number}] "
                "object নয়"
            )
            continue

        icon_path = resolve_public_path(
            icon.get("src"),
            f"icons[{number}].src",
        )

        if (
            icon_path is not None
            and not icon_path.is_file()
        ):
            add_warning(
                "PWA icon এখনো পাওয়া যায়নি: "
                f"{relative_path(icon_path)}"
            )


def validate_frontend_files() -> None:
    index_path = require_file(
        "index.html"
    )

    service_worker_path = require_file(
        "sw.js"
    )

    require_file(
        "_headers"
    )

    if index_path is None:
        return

    try:
        index_html = index_path.read_text(
            encoding="utf-8"
        )

    except OSError as error:
        add_error(
            f"index.html পড়া যায়নি: {error}"
        )
        return

    required_references = (
        "runtime-config.json",
        "data/manifest.json",
        "app.webmanifest",
        "sw.js",
    )

    for reference in required_references:
        if reference not in index_html:
            add_error(
                "index.html-এ required reference নেই: "
                f"{reference}"
            )

    forbidden_markers = (
        "API_URLS",
        "live-checker-workerjs",
        "live-checker-2-workerjs",
        "VERIFY_TOKEN",
        "const swCode =",
        (
            "navigator.serviceWorker.register("
            "URL.createObjectURL"
        ),
    )

    for marker in forbidden_markers:
        if marker in index_html:
            add_error(
                "index.html-এ নিষিদ্ধ পুরোনো "
                f"logic আছে: {marker}"
            )

    if service_worker_path is None:
        return

    try:
        service_worker_code = (
            service_worker_path.read_text(
                encoding="utf-8"
            )
        )

    except OSError as error:
        add_error(
            f"sw.js পড়া যায়নি: {error}"
        )
        return

    has_fetch_handler = (
        'addEventListener("fetch"' in
        service_worker_code
        or
        "addEventListener('fetch'" in
        service_worker_code
    )

    if not has_fetch_handler:
        add_error(
            "sw.js-এ fetch handler নেই"
        )

    if (
        ".m3u8" not in service_worker_code
        or ".ts" not in service_worker_code
    ):
        add_error(
            "sw.js stream bypass rules অসম্পূর্ণ"
        )


def validate_channel_category(
    category_name: str,
    category_slug: str,
    manifest_entry: Any,
) -> None:
    if not isinstance(
        manifest_entry,
        dict,
    ):
        add_error(
            f"manifest.channels.{category_name} "
            "object নয়"
        )
        return

    channel_path = resolve_public_path(
        manifest_entry.get("url"),
        f"channels.{category_name}.url",
    )

    if channel_path is None:
        return

    expected_path = (
        f"data/channels/{category_slug}.json"
    )

    if relative_path(
        channel_path
    ) != expected_path:
        add_error(
            f"{category_name} channel path ভুল: "
            f"{relative_path(channel_path)}"
        )

    if not channel_path.is_file():
        add_error(
            "Channel JSON পাওয়া যায়নি: "
            f"{relative_path(channel_path)}"
        )
        return

    data = load_json(
        channel_path,
        relative_path(
            channel_path
        ),
    )

    channel_items = get_items(
        data,
        "channels",
        "items",
    )

    if (
        not isinstance(data, dict)
        or channel_items is None
    ):
        add_error(
            f"{relative_path(channel_path)} "
            "valid channel object নয়"
        )
        return

    actual_count = len(
        channel_items
    )

    if data.get("count") != actual_count:
        add_error(
            f"{relative_path(channel_path)} "
            "count mismatch"
        )

    if (
        manifest_entry.get("count")
        != actual_count
    ):
        add_error(
            f"manifest {category_name} "
            "count mismatch"
        )

    for number, item in enumerate(
        channel_items,
        start=1,
    ):
        validate_stream_item(
            item,
            (
                f"{category_name} "
                f"channel #{number}"
            ),
        )

    COUNTS["channels"] += actual_count


def validate_event_file(
    manifest: dict[str, Any],
    manifest_key: str,
    expected_path: str,
    allow_metadata_only: bool,
) -> None:
    manifest_entry = manifest.get(
        manifest_key
    )

    if not isinstance(
        manifest_entry,
        dict,
    ):
        add_error(
            f"manifest.{manifest_key} object নয়"
        )
        return

    event_path = resolve_public_path(
        manifest_entry.get("url"),
        f"manifest.{manifest_key}.url",
    )

    if event_path is None:
        return

    if relative_path(
        event_path
    ) != expected_path:
        add_error(
            f"manifest.{manifest_key}.url ভুল: "
            f"{relative_path(event_path)}"
        )

    if not event_path.is_file():
        add_error(
            "Event JSON পাওয়া যায়নি: "
            f"{relative_path(event_path)}"
        )
        return

    data = load_json(
        event_path,
        relative_path(event_path),
    )

    event_items = get_items(
        data,
        "items",
        "events",
    )

    if (
        not isinstance(data, dict)
        or event_items is None
    ):
        add_error(
            f"{relative_path(event_path)} "
            "valid event object নয়"
        )
        return

    actual_count = len(
        event_items
    )

    if data.get("count") != actual_count:
        add_error(
            f"{relative_path(event_path)} "
            "count mismatch"
        )

    if (
        manifest_entry.get("count")
        != actual_count
    ):
        add_error(
            f"manifest.{manifest_key} "
            "count mismatch"
        )

    expected_visibility = (
        actual_count > 0
    )

    if (
        bool(
            manifest_entry.get(
                "visible"
            )
        )
        != expected_visibility
    ):
        add_warning(
            f"manifest.{manifest_key}.visible "
            "count-এর সঙ্গে মিলছে না"
        )

    for number, item in enumerate(
        event_items,
        start=1,
    ):
        validate_stream_item(
            item,
            (
                f"{manifest_key} "
                f"event #{number}"
            ),
            allow_metadata_only,
        )

    COUNTS["events"] += actual_count


def validate_movie_category(
    category_name: str,
    category_slug: str,
    manifest_entry: Any,
) -> None:
    if not isinstance(
        manifest_entry,
        dict,
    ):
        add_error(
            f"manifest.movies.{category_name} "
            "object নয়"
        )
        return

    index_path = resolve_public_path(
        manifest_entry.get("index"),
        f"movies.{category_name}.index",
    )

    if index_path is None:
        return

    expected_index_path = (
        f"data/movies/{category_slug}/index.json"
    )

    if relative_path(
        index_path
    ) != expected_index_path:
        add_error(
            f"{category_name} movie index "
            "path ভুল: "
            f"{relative_path(index_path)}"
        )

    if not index_path.is_file():
        add_error(
            "Movie index পাওয়া যায়নি: "
            f"{relative_path(index_path)}"
        )
        return

    index_data = load_json(
        index_path,
        relative_path(index_path),
    )

    if (
        not isinstance(
            index_data,
            dict,
        )
        or not isinstance(
            index_data.get("pages"),
            list,
        )
    ):
        add_error(
            f"{relative_path(index_path)} "
            "valid movie index নয়"
        )
        return

    pages = index_data["pages"]

    if (
        index_data.get("slug")
        != category_slug
    ):
        add_error(
            f"{relative_path(index_path)} "
            "slug mismatch"
        )

    if (
        index_data.get("total_pages")
        != len(pages)
        or
        manifest_entry.get("total_pages")
        != len(pages)
    ):
        add_error(
            f"{category_name} "
            "total_pages mismatch"
        )

    total_movie_items = 0
    seen_page_numbers: set[int] = set()

    for position, page_entry in enumerate(
        pages,
        start=1,
    ):
        if (
            not isinstance(
                page_entry,
                dict,
            )
            or not isinstance(
                page_entry.get("page"),
                int,
            )
        ):
            add_error(
                f"{category_name} page entry "
                f"#{position} invalid"
            )
            continue

        page_number = page_entry["page"]

        if page_number in seen_page_numbers:
            add_error(
                f"{category_name} duplicate "
                f"page number: {page_number}"
            )

        seen_page_numbers.add(
            page_number
        )

        page_path_value = (
            page_entry.get("path")
            or page_entry.get("file")
        )

        if (
            isinstance(
                page_path_value,
                str,
            )
            and "/" not in page_path_value
        ):
            page_path = (
                index_path.parent
                / page_path_value
            ).resolve()

        else:
            page_path = resolve_public_path(
                page_path_value,
                (
                    f"{category_name} "
                    f"page {page_number}.path"
                ),
            )

        if page_path is None:
            continue

        if not page_path.is_file():
            add_error(
                "Movie page পাওয়া যায়নি: "
                f"{relative_path(page_path)}"
            )
            continue

        page_data = load_json(
            page_path,
            relative_path(page_path),
        )

        movie_items = get_items(
            page_data,
            "items",
            "movies",
        )

        if (
            not isinstance(
                page_data,
                dict,
            )
            or movie_items is None
        ):
            add_error(
                f"{relative_path(page_path)} "
                "valid movie page নয়"
            )
            continue

        actual_page_count = len(
            movie_items
        )

        total_movie_items += (
            actual_page_count
        )

        if (
            page_data.get("count")
            != actual_page_count
            or
            page_entry.get("count")
            != actual_page_count
        ):
            add_error(
                f"{category_name} page "
                f"{page_number} count mismatch"
            )

        if (
            page_data.get("page")
            != page_number
        ):
            add_error(
                f"{relative_path(page_path)} "
                "page number mismatch"
            )

        for item_number, movie_item in enumerate(
            movie_items,
            start=1,
        ):
            validate_stream_item(
                movie_item,
                (
                    f"{category_name} movie "
                    f"page {page_number} "
                    f"item #{item_number}"
                ),
            )

    expected_page_numbers = set(
        range(
            1,
            len(pages) + 1,
        )
    )

    if (
        seen_page_numbers
        != expected_page_numbers
    ):
        add_error(
            f"{category_name} movie "
            "page numbering ধারাবাহিক নয়"
        )

    if (
        index_data.get("count")
        != total_movie_items
        or
        manifest_entry.get("count")
        != total_movie_items
    ):
        add_error(
            f"{category_name} movie "
            "total count mismatch"
        )

    COUNTS["movies"] += (
        total_movie_items
    )


def validate_data_manifest() -> None:
    manifest = load_json(
        require_file(
            "data/manifest.json"
        ),
        "data/manifest.json",
    )

    if not isinstance(
        manifest,
        dict,
    ):
        if manifest is not None:
            add_error(
                "data/manifest.json "
                "root object হতে হবে"
            )

        return

    if manifest.get("schema_version") != 1:
        add_error(
            "data/manifest.json "
            "schema_version অবশ্যই 1 হতে হবে"
        )

    channel_manifest = manifest.get(
        "channels"
    )

    movie_manifest = manifest.get(
        "movies"
    )

    if not isinstance(
        channel_manifest,
        dict,
    ):
        add_error(
            "manifest channels object missing"
        )
        channel_manifest = {}

    if not isinstance(
        movie_manifest,
        dict,
    ):
        add_error(
            "manifest movies object missing"
        )
        movie_manifest = {}

    for category_name in [
        *channel_manifest,
        *movie_manifest,
    ]:
        if (
            str(category_name)
            .strip()
            .lower()
            in {
                "all",
                "movie",
                "movies",
            }
        ):
            add_error(
                "নিষিদ্ধ category manifest-এ আছে: "
                f"{category_name}"
            )

    missing_channels = (
        set(CHANNELS)
        - set(channel_manifest)
    )

    missing_movies = (
        set(MOVIES)
        - set(movie_manifest)
    )

    extra_movies = (
        set(movie_manifest)
        - set(MOVIES)
    )

    if missing_channels:
        add_error(
            "Channel category missing: "
            f"{sorted(missing_channels)}"
        )

    if missing_movies:
        add_error(
            "Movie category missing: "
            f"{sorted(missing_movies)}"
        )

    if extra_movies:
        add_error(
            "অনুমোদিত নয় এমন movie category: "
            f"{sorted(extra_movies)}"
        )

    for category_name, category_slug in (
        CHANNELS.items()
    ):
        if category_name in channel_manifest:
            validate_channel_category(
                category_name,
                category_slug,
                channel_manifest[
                    category_name
                ],
            )

    for category_name, category_slug in (
        MOVIES.items()
    ):
        if category_name in movie_manifest:
            validate_movie_category(
                category_name,
                category_slug,
                movie_manifest[
                    category_name
                ],
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


def main() -> int:
    print(
        "[Click TV Validator] "
        f"Build folder: {ROOT}"
    )

    validate_public_safety()

    for file_name in REQUIRED_FILES:
        require_file(
            file_name
        )

    validate_runtime_config()
    validate_webmanifest()
    validate_frontend_files()
    validate_data_manifest()

    print(
        "\n[Click TV Validator] Summary"
    )

    print(
        f"  Channels: {COUNTS['channels']}"
    )

    print(
        f"  Movies: {COUNTS['movies']}"
    )

    print(
        f"  Events: {COUNTS['events']}"
    )

    print(
        f"  Warnings: {len(WARNINGS)}"
    )

    print(
        f"  Errors: {len(ERRORS)}"
    )

    if WARNINGS:
        print("\nWarnings:")

        for message in WARNINGS:
            print(
                f"  [WARN] {message}"
            )

    if ERRORS:
        print("\nErrors:")

        for message in ERRORS:
            print(
                f"  [ERROR] {message}"
            )

        print(
            "\n[Click TV Validator] "
            "Validation failed."
        )

        return 1

    print(
        "\n[Click TV Validator] "
        "Validation successful."
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
