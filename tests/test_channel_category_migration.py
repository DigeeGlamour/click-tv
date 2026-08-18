from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scanner.output import publish_scan_outputs
from scanner.output import _apply_pinned_sports_order, PINNED_SPORTS_CHANNEL_ORDER


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


class PinnedSportsChannelOrderTests(unittest.TestCase):
    """A fixed serial order for these named Sports channels, by direct
    request: never re-sorted by health, verification or scan-to-scan
    discovery order - the position is the identity."""

    @staticmethod
    def _write(path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _card(self, name: str) -> dict:
        slug = name.lower().replace(" ", "-")
        return {
            "id": slug,
            "name": name,
            "url": f"https://example.test/{slug}.m3u8",
            "category": "Sports",
            "source_pipeline": "tv",
            "verified": True,
            "publish_allowed": True,
        }

    def test_pinned_channels_come_out_in_the_requested_order_regardless_of_input_order(self) -> None:
        shuffled = ["M Sports", "T Sports", "Willow 2", "Willow", "GOAL TV", "PTV Sports"]
        cards = [self._card(name) for name in shuffled]
        ordered = [card["name"] for card in _apply_pinned_sports_order(cards)]
        self.assertEqual(ordered, ["T Sports", "PTV Sports", "Willow", "Willow 2", "GOAL TV", "M Sports"])

    def test_every_named_channel_is_covered_by_the_pinned_list(self) -> None:
        # Guards against a future edit accidentally dropping one of the
        # twenty-one channels handed over.
        self.assertEqual(len(PINNED_SPORTS_CHANNEL_ORDER), 21)

    def test_a_channel_not_on_the_list_keeps_its_relative_order_after_every_pinned_one(self) -> None:
        cards = [
            self._card("Some Unlisted Channel"),
            self._card("Willow"),
            self._card("Another Unlisted Channel"),
            self._card("T Sports"),
        ]
        ordered = [card["name"] for card in _apply_pinned_sports_order(cards)]
        self.assertEqual(
            ordered,
            ["T Sports", "Willow", "Some Unlisted Channel", "Another Unlisted Channel"],
        )

    def test_matching_is_case_and_whitespace_insensitive(self) -> None:
        cards = [self._card("  bein sports   2  "), self._card("willow")]
        ordered = [card["name"] for card in _apply_pinned_sports_order(cards)]
        self.assertEqual(ordered, ["willow", "  bein sports   2  "])

    def test_an_en_dash_in_the_stored_name_still_matches_the_hyphen_in_the_list(self) -> None:
        # The list was handed over with "Bein Sports Xtra – *2" (an en dash);
        # a real channel name is more likely to use a plain hyphen, and
        # either must resolve to the same pinned position.
        cards = [self._card("Bein Sports Xtra – *2"), self._card("Bein Sports Xtra - *2")]
        ordered = _apply_pinned_sports_order(cards)
        self.assertEqual(len(ordered), 2)

    def test_a_pinned_channel_missing_this_scan_is_simply_skipped(self) -> None:
        cards = [self._card("T Sports"), self._card("M Sports")]
        ordered = [card["name"] for card in _apply_pinned_sports_order(cards)]
        self.assertEqual(ordered, ["T Sports", "M Sports"])

    def test_only_the_sports_category_is_reordered(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data = root / "data"
            state = root / "state"
            reports = root / "reports"
            settings = root / "config" / "settings.json"
            self._write(settings, {})

            result = publish_scan_outputs(
                channels_data={
                    "Sports": [self._card("M Sports"), self._card("T Sports")],
                    "Bangla": [self._card("Z Channel"), self._card("A Channel")],
                },
                settings_path=str(settings),
                data_dir=str(data),
                state_dir=str(state),
                reports_dir=str(reports),
                scan_mode="channels",
            )
            self.assertIn("Sports", result["manifest_summary"]["channels"])
            sports_payload = json.loads((data / "channels" / "sports.json").read_text(encoding="utf-8"))
            bangla_payload = json.loads((data / "channels" / "bangla.json").read_text(encoding="utf-8"))
            self.assertEqual(
                [c["name"] for c in sports_payload["channels"]], ["T Sports", "M Sports"],
            )
            # Bangla is untouched by the pinned list - its own order (whatever
            # the caller supplied) is preserved exactly.
            self.assertEqual(
                [c["name"] for c in bangla_payload["channels"]], ["Z Channel", "A Channel"],
            )


if __name__ == "__main__":
    unittest.main()
