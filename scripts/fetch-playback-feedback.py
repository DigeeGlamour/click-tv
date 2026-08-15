#!/usr/bin/env python3
"""Download aggregated Click TV playback telemetry into reports/playback-feedback.json.

Environment variables:
    PLAYBACK_TELEMETRY_SUMMARY_URL
    PLAYBACK_TELEMETRY_EXPORT_TOKEN

If either variable is absent, the script exits successfully without changing files.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

OUTPUT = Path("reports/playback-feedback.json")


def main() -> int:
    url = os.environ.get("PLAYBACK_TELEMETRY_SUMMARY_URL", "").strip()
    token = os.environ.get("PLAYBACK_TELEMETRY_EXPORT_TOKEN", "").strip()
    if not url or not token:
        print("[Playback Feedback] Telemetry URL/token নেই; কাজ skip করা হয়েছে।")
        return 0

    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "Click-TV-Scanner/1.0",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        print(f"[Playback Feedback] Download failed: {error}", file=sys.stderr)
        return 1

    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        print("[Playback Feedback] Invalid summary payload", file=sys.stderr)
        return 1

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    suspected = sum(1 for item in payload["items"] if item.get("suspected_dead"))
    print(f"[Playback Feedback] Reports saved: {len(payload['items'])}; suspected dead: {suspected}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
