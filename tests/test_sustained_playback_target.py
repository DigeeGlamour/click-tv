"""The real-browser harness must measure protected published cards verbatim."""

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "sustained_playback_check",
    ROOT / "scripts" / "sustained-playback-check.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class SustainedPlaybackTargetTests(unittest.TestCase):
    def test_protected_playback_id_and_drm_reach_attempt_planner(self):
        drm = {"type": "clearkey", "clear_keys": "kid:key"}
        item = MODULE.build_harness_item(
            {
                "playback_id": "ctv_primary",
                "stream_type": "dash",
                "proxy_mode": "proxy_only",
                "requires_headers": True,
                "requires_credentials": True,
                "protected_source": True,
                "drm": drm,
                "backups": [
                    {
                        "playback_id": "ctv_backup",
                        "stream_type": "dash",
                        "drm": drm,
                    }
                ],
            },
            "Star Jalsha",
        )

        self.assertEqual(item["playback_id"], "ctv_primary")
        self.assertEqual(item["drm"], drm)
        self.assertTrue(item["protected_source"])
        self.assertEqual(item["backups"][0]["playback_id"], "ctv_backup")
        self.assertEqual(item["backups"][0]["drm"], drm)

    def test_legacy_string_backup_still_works(self):
        item = MODULE.build_harness_item(
            {
                "url": "https://primary.test/live.m3u8",
                "backups": ["https://backup.test/live.m3u8"],
            },
            "Channel",
        )
        self.assertEqual(
            item["backups"], [{"url": "https://backup.test/live.m3u8"}]
        )

    def test_dash_measurement_uses_shaka_and_proxy_drm_resolution(self):
        harness = MODULE.PLAY_AND_MEASURE
        self.assertIn("kind === 'dash'", harness)
        self.assertIn("ensureShakaLibrary", harness)
        self.assertIn("resolveProtectedDrm", harness)
        self.assertIn("engine = 'shaka'", harness)


if __name__ == "__main__":
    unittest.main()
