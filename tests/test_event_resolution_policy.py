"""A live match whose stream verified must not be thrown away for lacking a
declared resolution.

Live event playlists are usually an ABR master or a bare chunk list with no
RESOLUTION attribute, so `detected_height` comes back as 0 even though the
stream answered and is playable. The event branch of the resolution policy used
to quarantine exactly those, which is how Today Match kept publishing an empty
tab while the configured source playlists plainly held live fixtures. TV
already had `preserve_unknown_working_tv` for the same situation; events now
have the matching `preserve_unknown_working_event`.

A dead link is unaffected: it fails earlier, during verification, and never
reaches this policy at all.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner.verifier import _apply_resolution_policy  # noqa: E402


def _settings(**overrides):
    resolution = {
        "event_minimum_height": 720,
        "allow_unknown_event_resolution": False,
        "manual_can_override_resolution": False,
    }
    resolution.update(overrides)
    return {"resolution": resolution}


class UnknownEventResolutionTests(unittest.TestCase):
    def test_verified_event_without_a_declared_resolution_is_published(self):
        for pipeline in ("today_match", "upcoming"):
            with self.subTest(pipeline=pipeline):
                item = {"source_pipeline": pipeline, "name": "Sri Lanka vs India"}
                published, status, reason = _apply_resolution_policy(
                    item, _settings(preserve_unknown_working_event=True), 0
                )
                self.assertTrue(published, reason)
                self.assertEqual(status, "verified_global")
                self.assertTrue(item["quality_unknown"])
                self.assertIn("resolution", item["quality_policy_note"])

    def test_the_rescue_can_still_be_switched_off(self):
        item = {"source_pipeline": "today_match"}
        published, status, reason = _apply_resolution_policy(
            item, _settings(preserve_unknown_working_event=False), 0
        )
        self.assertFalse(published)
        self.assertEqual(status, "quarantine")
        self.assertEqual(reason, "Event resolution could not be determined")

    def test_rescue_is_on_by_default_when_the_key_is_absent(self):
        item = {"source_pipeline": "today_match"}
        published, status, _ = _apply_resolution_policy(item, _settings(), 0)
        self.assertTrue(published)
        self.assertEqual(status, "verified_global")

    def test_a_genuinely_low_resolution_event_is_still_rejected(self):
        """The rescue is only for *unknown* resolution. A stream that really
        reports 360p must not sneak through with it."""
        item = {"source_pipeline": "today_match"}
        published, status, reason = _apply_resolution_policy(
            item, _settings(preserve_unknown_working_event=True), 360
        )
        self.assertFalse(published)
        self.assertEqual(status, "rejected_low_quality")
        self.assertIn("360p", reason)

    def test_an_event_meeting_the_minimum_is_untouched(self):
        item = {"source_pipeline": "today_match"}
        published, status, _ = _apply_resolution_policy(
            item, _settings(preserve_unknown_working_event=True), 720
        )
        self.assertTrue(published)
        self.assertNotIn("quality_unknown", item)

    def test_the_live_project_settings_enable_the_rescue(self):
        import json

        settings = json.loads(
            (ROOT / "config" / "settings.json").read_text(encoding="utf-8")
        )
        resolution = settings["resolution"]
        self.assertIs(resolution["preserve_unknown_working_event"], True)
        # "live হলেই add" - no height floor for events at all.
        self.assertEqual(resolution["event_minimum_height"], 0)
        self.assertIs(resolution["allow_unknown_event_resolution"], True)


class ValidatorExemptsEveryEventStreamTests(unittest.TestCase):
    """The Pages validator enforces 720p in two separate places: once for an
    item's primary stream and once for each backup/standby beside it. Exempting
    only the first still failed the build on a live match whose *backup* had no
    declared resolution, so both gates are pinned here together.
    """

    def _validator(self):
        import importlib.util

        path = ROOT / "scripts" / "validate-pages.py"
        spec = importlib.util.spec_from_file_location("validate_pages", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _errors_for(self, item, media_kind):
        module = self._validator()
        module.ERRORS.clear()
        module.PLAYBACK_IDS.clear()
        module.validate_stream_item(item, "label", media_kind=media_kind)
        return [e for e in module.ERRORS if "720p" in e]

    def _event(self):
        return {
            "name": "Dinamo Vladivostok vs FK Sokol Saratov",
            "url": "https://live.test/match.m3u8",
            "backups": [{"url": "https://live.test/backup.m3u8"}],
        }

    def test_an_event_with_an_undeclared_resolution_backup_is_accepted(self):
        self.assertEqual(self._errors_for(self._event(), "event"), [])

    def test_a_channel_with_the_same_shape_is_still_rejected(self):
        errors = self._errors_for(self._event(), "channel")
        self.assertTrue(
            any("backup/standby" in e for e in errors),
            f"channel backups must keep the 720p rule; got {errors}",
        )
        self.assertTrue(any("resolution must be known" in e for e in errors))


if __name__ == "__main__":
    unittest.main()
