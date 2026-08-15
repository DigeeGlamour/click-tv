from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scanner.channel_logos import enrich_channel_logos


class ChannelLogoEnrichmentTests(unittest.TestCase):
    def _paths(self, root: Path):
        aliases = root / "config/channel-aliases.json"
        overrides = root / "manual/channel-logos.json"
        cache = root / "state/channel-logo-cache.json"
        report = root / "reports/channel-logo-enrichment.json"

        aliases.parent.mkdir(parents=True, exist_ok=True)
        overrides.parent.mkdir(parents=True, exist_ok=True)
        aliases.write_text(
            json.dumps(
                {
                    "channel_aliases": {
                        "Somoy TV": ["Somoy News", "Somoy TV HD"]
                    }
                }
            ),
            encoding="utf-8",
        )
        overrides.write_text(
            json.dumps({"version": 1, "enabled": True, "channels": []}),
            encoding="utf-8",
        )
        return aliases, overrides, cache, report

    def test_missing_logo_is_filled_from_same_canonical_channel(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            aliases, overrides, cache, report = self._paths(root)
            candidates = [
                {
                    "id": "somoy-tv",
                    "name": "Somoy TV",
                    "category": "Bangla",
                    "logo": "",
                    "source_id": "source-without-logo",
                    "source_pipeline": "tv",
                    "verified": True,
                },
                {
                    "id": "somoy-tv",
                    "name": "Somoy News",
                    "category": "Bangla",
                    "logo": "https://example.com/somoy.png",
                    "source_id": "source-with-logo",
                    "source_pipeline": "tv",
                    "verified": True,
                },
            ]

            enriched = enrich_channel_logos(
                candidates,
                aliases_path=aliases,
                overrides_path=overrides,
                cache_path=cache,
                report_path=report,
            )

            self.assertEqual(
                enriched[0]["logo"],
                "https://example.com/somoy.png",
            )
            self.assertEqual(
                enriched[1]["logo"],
                "https://example.com/somoy.png",
            )

            report_payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(report_payload["filled"]["current_scan_source"], 1)
            self.assertEqual(report_payload["after_missing"], 0)

    def test_existing_usable_logo_is_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            aliases, overrides, cache, report = self._paths(root)
            candidates = [
                {
                    "id": "channel-one",
                    "name": "Channel One",
                    "category": "Bangla",
                    "logo": "https://example.com/original.png",
                    "source_id": "original",
                    "source_pipeline": "tv",
                },
                {
                    "id": "channel-one",
                    "name": "Channel One HD",
                    "category": "Bangla",
                    "logo": "https://example.com/other.png",
                    "source_id": "other",
                    "source_pipeline": "tv",
                    "verified": True,
                    "source_priority": 999,
                },
            ]

            enriched = enrich_channel_logos(
                candidates,
                aliases_path=aliases,
                overrides_path=overrides,
                cache_path=cache,
                report_path=report,
            )

            self.assertEqual(
                enriched[0]["logo"],
                "https://example.com/original.png",
            )

    def test_placeholder_is_replaced_by_manual_override(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            aliases, overrides, cache, report = self._paths(root)
            overrides.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "enabled": True,
                        "channels": [
                            {
                                "canonical_name": "BTV",
                                "category": "Bangla",
                                "logo": "https://example.com/btv.png",
                                "aliases": ["BTV National"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            candidates = [
                {
                    "id": "btv-national",
                    "name": "BTV National",
                    "category": "Bangla",
                    "logo": "https://example.com/default-logo.png",
                    "source_pipeline": "tv",
                }
            ]

            enriched = enrich_channel_logos(
                candidates,
                aliases_path=aliases,
                overrides_path=overrides,
                cache_path=cache,
                report_path=report,
            )

            self.assertEqual(
                enriched[0]["logo"],
                "https://example.com/btv.png",
            )

    def test_previous_cache_is_used_when_current_sources_have_no_logo(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            aliases, overrides, cache, report = self._paths(root)
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "logos": {
                            "category:bangla|name:cached channel": {
                                "logo": "https://example.com/cached.png",
                                "source_id": "old-source",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            candidates = [
                {
                    "id": "cached-channel",
                    "name": "Cached Channel",
                    "category": "Bangla",
                    "logo": "",
                    "source_pipeline": "tv",
                }
            ]

            enriched = enrich_channel_logos(
                candidates,
                aliases_path=aliases,
                overrides_path=overrides,
                cache_path=cache,
                report_path=report,
            )

            self.assertEqual(
                enriched[0]["logo"],
                "https://example.com/cached.png",
            )

    def test_same_name_in_different_category_is_not_cross_matched(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            aliases, overrides, cache, report = self._paths(root)
            candidates = [
                {
                    "id": "world-tv",
                    "name": "World TV",
                    "category": "Foreign News",
                    "logo": "https://example.com/world-news.png",
                    "source_pipeline": "tv",
                },
                {
                    "id": "world-tv",
                    "name": "World TV",
                    "category": "Sports",
                    "logo": "",
                    "source_pipeline": "tv",
                },
            ]

            enriched = enrich_channel_logos(
                candidates,
                aliases_path=aliases,
                overrides_path=overrides,
                cache_path=cache,
                report_path=report,
            )

            self.assertEqual(enriched[1]["logo"], "")


if __name__ == "__main__":
    unittest.main()
