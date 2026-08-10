from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scanner.output import publish_scan_outputs


class ChannelCategoryMigrationTests(unittest.TestCase):
    @staticmethod
    def _cards(count: int, prefix: str) -> list[dict[str, object]]:
        return [
            {
                "id": f"{prefix}-{index}",
                "name": f"{prefix} {index}",
                "url": f"https://example.test/{prefix}/{index}.m3u8",
                "category": "Bangla",
                "source_pipeline": "tv",
                "verified": True,
                "publish_allowed": True,
            }
            for index in range(count)
        ]

    @staticmethod
    def _write(path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def test_new_identity_migration_replaces_old_category_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data = root / "data"
            state = root / "state"
            reports = root / "reports"
            settings = root / "settings.json"
            migration_id = "channel-category-identity-v2-test"

            self._write(settings, {
                "failure_protection": {
                    "maximum_drop_percentage": 70,
                    "minimum_previous_count": 10,
                },
                "content_routing": {
                    "cleanup_polluted_tv_once": True,
                    "cleanup_migration_id": migration_id,
                    "cleanup_polluted_tv_categories": ["Bangla"],
                    "cleanup_minimum_incoming_tv": 1,
                },
                "notifications": {"telegram_enabled": False},
            })
            old_payload = {
                "category": "Bangla",
                "count": 100,
                "channels": self._cards(100, "old"),
            }
            self._write(data / "channels" / "bangla.json", old_payload)
            self._write(state / "last-good" / "bangla.json", old_payload)

            first = publish_scan_outputs(
                channels_data={"Bangla": self._cards(10, "new")},
                settings_path=str(settings),
                data_dir=str(data),
                state_dir=str(state),
                reports_dir=str(reports),
                scan_mode="channels",
            )
            first_entry = first["manifest_summary"]["channels"]["Bangla"]
            self.assertEqual(first_entry["count"], 10)
            self.assertFalse(first_entry["protected"])
            self.assertTrue(
                (state / "migrations" / f"{migration_id}.json").exists()
            )

            second = publish_scan_outputs(
                channels_data={"Bangla": self._cards(1, "later")},
                settings_path=str(settings),
                data_dir=str(data),
                state_dir=str(state),
                reports_dir=str(reports),
                scan_mode="channels",
            )
            second_entry = second["manifest_summary"]["channels"]["Bangla"]
            self.assertEqual(second_entry["count"], 10)
            self.assertEqual(second_entry["incoming_count"], 1)
            self.assertTrue(second_entry["protected"])


if __name__ == "__main__":
    unittest.main()
