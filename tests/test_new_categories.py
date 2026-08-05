from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scanner.channels import process_tv_channels
from scanner.movies import paginate_movie_list


class NewCategoryAndOrderingTests(unittest.TestCase):
    def test_unknown_live_channel_routes_to_other(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            results = root / "working/bd-results.json"
            results.parent.mkdir(parents=True)
            results.write_text(
                json.dumps(
                    {
                        "results": [
                            {
                                "id": "unknown-live",
                                "name": "Community Channel",
                                "category": "Local Community",
                                "url": "https://example.com/community/index.m3u8",
                                "source_pipeline": "tv",
                                "content_kind": "live_tv",
                                "verification_status": "verified_global",
                                "verified": True,
                                "publish_allowed": True,
                                "source_id": "test",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            settings = root / "config/settings.json"
            settings.parent.mkdir(parents=True)
            settings.write_text("{}", encoding="utf-8")
            output = process_tv_channels(str(results), str(settings))
            self.assertEqual(len(output["Other"]), 1)
            self.assertEqual(output["Other"][0]["category"], "Other")
            self.assertEqual(output["quarantine"], [])

    def test_movie_order_is_year_first_then_manual_first(self) -> None:
        movies = [
            {"id": "other-2026", "name": "Other 2026", "year": 2026, "url": "https://example.com/other-2026.mkv", "verification_status": "verified_global", "verified": True, "publish_allowed": True},
            {"id": "manual-2025", "name": "Manual 2025", "year": 2025, "url": "https://example.com/manual-2025.mkv", "verification_status": "manual_trusted", "manual_source": True, "publish_allowed": True},
            {"id": "manual-2026", "name": "Manual 2026", "year": 2026, "url": "https://example.com/manual-2026.mkv", "verification_status": "manual_trusted", "manual_source": True, "publish_allowed": True},
            {"id": "other-2025", "name": "Other 2025", "year": 2025, "url": "https://example.com/other-2025.mkv", "verification_status": "verified_global", "verified": True, "publish_allowed": True},
        ]
        payload = paginate_movie_list(movies, "Premium", page_size=100)
        names = [item["name"] for item in payload["page_contents"]["page-001.json"]["items"]]
        self.assertEqual(names, ["Manual 2026", "Other 2026", "Manual 2025", "Other 2025"])


if __name__ == "__main__":
    unittest.main()
