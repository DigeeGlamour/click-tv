#!/usr/bin/env python3
"""Stamp every JS/CSS URL in the built site with a hash of its own contents.

Without this, a returning viewer keeps running the app.js they cached weeks ago.

site/index.html asks for `assets/js/app.js?v=20260819-today-match-crimson-v4`,
and that string is written by hand. site/sw.js caches script and style requests
cache-first, keyed on the full URL. So the moment app.js changes without someone
also remembering to change the query, the service worker keeps serving the old
file to everyone who has ever loaded the site - and it had not been changed
since 2026-08-19, across every fix made since. The code was right in the
repository and wrong in the browser, which is the hardest kind of bug to see.

This removes the remembering. The version is the first 12 hex of the SHA-256 of
the file being asked for, so it changes when and only when the file does, and a
cached copy is looked up under a URL that no longer exists.

The service worker's own CACHE_VERSION is stamped from the hash of the whole
shell, so a new release also retires the old cache rather than leaving it to
accumulate.

Runs against dist/ during the build. site/ is left untouched, so the source
tree keeps its readable version strings and no scan produces a diff here.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from typing import Dict, List, Tuple

#: `assets/js/app.js?v=anything` or `assets/css/app.css` with no query at all.
ASSET_REF = re.compile(
    r'(?P<path>(?:\./)?assets/(?:js|css)/[A-Za-z0-9_.-]+\.(?:js|css))'
    r'(?P<query>\?v=[^"\'\s>]*)?'
)

CACHE_VERSION = re.compile(
    r'(?P<head>const\s+CACHE_VERSION\s*=\s*")(?P<value>[^"]*)(?P<tail>")'
)


def digest(path: str) -> str:
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()[:12]


def stamp_html(dist: str, path: str, hashes: Dict[str, str]) -> Tuple[int, int]:
    with open(path, "r", encoding="utf-8") as handle:
        html = handle.read()

    changed = 0
    seen = 0

    def replace(match: "re.Match[str]") -> str:
        nonlocal changed, seen
        asset = match.group("path")
        relative = asset[2:] if asset.startswith("./") else asset
        version = hashes.get(relative)
        if not version:
            return match.group(0)
        seen += 1
        wanted = f"{asset}?v={version}"
        if match.group(0) != wanted:
            changed += 1
        return wanted

    stamped = ASSET_REF.sub(replace, html)
    if stamped != html:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(stamped)
    return seen, changed


def stamp_service_worker(path: str, shell_hash: str) -> bool:
    with open(path, "r", encoding="utf-8") as handle:
        source = handle.read()
    stamped, count = CACHE_VERSION.subn(
        lambda m: f"{m.group('head')}click-tv-{shell_hash}{m.group('tail')}", source
    )
    if not count:
        return False
    # The shell list is fetched by the worker itself on install, and those URLs
    # carry no query - so they must be stamped too or the worker warms its cache
    # with the unversioned copies it is trying to replace.
    if stamped != source:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(stamped)
    return True


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("dist", nargs="?", default="dist")
    args = parser.parse_args(argv)
    dist = os.path.abspath(args.dist)
    if not os.path.isdir(dist):
        print(f"[Asset Stamp] no such build folder: {dist}", file=sys.stderr)
        return 1

    hashes: Dict[str, str] = {}
    for folder in ("assets/js", "assets/css"):
        base = os.path.join(dist, folder)
        if not os.path.isdir(base):
            continue
        for name in sorted(os.listdir(base)):
            if not name.endswith((".js", ".css")):
                continue
            relative = f"{folder}/{name}"
            hashes[relative] = digest(os.path.join(base, name))

    if not hashes:
        print("[Asset Stamp] no js/css found; nothing to stamp")
        return 0

    total_seen = 0
    total_changed = 0
    for root, _dirs, files in os.walk(dist):
        for name in files:
            if not name.endswith(".html"):
                continue
            seen, changed = stamp_html(dist, os.path.join(root, name), hashes)
            total_seen += seen
            total_changed += changed

    shell = hashlib.sha256(
        "".join(f"{k}:{v}" for k, v in sorted(hashes.items())).encode("utf-8")
    ).hexdigest()[:12]
    worker = os.path.join(dist, "sw.js")
    stamped_worker = os.path.isfile(worker) and stamp_service_worker(worker, shell)

    print(f"[Asset Stamp] {len(hashes)} asset(s) hashed, "
          f"{total_seen} reference(s) stamped, {total_changed} rewritten")
    print(f"[Asset Stamp] service worker cache: "
          f"{'click-tv-' + shell if stamped_worker else 'NOT STAMPED'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
