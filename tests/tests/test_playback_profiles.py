import json
import tempfile
import unittest
from pathlib import Path

from scanner.output import publish_scan_outputs
from scanner.playback_profiles import (
    PlaybackProfileCollector,
    load_public_catalog_records,
)


class PlaybackProfileTests(unittest.TestCase):
    def test_sensitive_source_moves_to_public_catalog(self):
        collector = PlaybackProfileCollector("channels", "2026-08-09T00:00:00+00:00")
        public = collector.sanitize_item({
            "id": "one",
            "name": "Protected",
            "url": "https://media.example/live.m3u8?token=very-secret&quality=hd",
            "headers": {"Cookie": "session=secret", "Referer": "https://example.com/"},
            "drm": {"type": "clearkey", "key_id": "kid", "license_key": "secret-key"},
            "stream_type": "hls",
        })

        self.assertNotIn("url", public)
        self.assertNotIn("headers", public)
        self.assertEqual(public["proxy_mode"], "proxy_only")
        self.assertTrue(public["playback_id"].startswith("ctv_"))
        serialized_public = json.dumps(public)
        self.assertNotIn("very-secret", serialized_public)
        self.assertNotIn("session=secret", serialized_public)
        self.assertNotIn("secret-key", serialized_public)

        record = collector.catalog_bundle()["records"][public["playback_id"]]
        self.assertIn("very-secret", record["url"])
        self.assertEqual(record["headers"]["Cookie"], "session=secret")
        self.assertEqual(record["drm"]["license_key"], "secret-key")

    def test_normal_source_gets_id_and_exact_headers_in_catalog(self):
        collector = PlaybackProfileCollector("channels")
        public = collector.sanitize_item({
            "url": "https://media.example/live.m3u8",
            "headers": {"Referer": "https://example.com/", "User-Agent": "Scanner"},
        })
        self.assertEqual(public["url"], "https://media.example/live.m3u8")
        self.assertNotIn("headers", public)
        self.assertTrue(public["playback_id"].startswith("ctv_"))
        record = collector.records[public["playback_id"]]
        self.assertEqual(record["headers"]["Referer"], "https://example.com/")
        self.assertEqual(record["headers"]["User-Agent"], "Scanner")

    def test_publish_writes_public_catalog(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "config").mkdir()
            (root / "config" / "settings.json").write_text("{}", encoding="utf-8")
            publish_scan_outputs(
                channels_data={"Bangla": [{
                    "id": "protected",
                    "name": "Protected",
                    "url": "https://media.example/live.m3u8?token=secret",
                }]},
                settings_path=str(root / "config" / "settings.json"),
                data_dir=str(root / "data"),
                state_dir=str(root / "state"),
                reports_dir=str(root / "reports"),
                scan_mode="channels",
            )
            public = json.loads((root / "data" / "channels" / "bangla.json").read_text(encoding="utf-8"))
            index = json.loads((root / "data" / "playback-sources.json").read_text(encoding="utf-8"))
            # The catalogue is sharded: the index only declares the shards, and
            # the credentialed record lives in data/playback/<prefix>.json.
            records = load_public_catalog_records(root / "data")
            self.assertNotIn("secret", json.dumps(public))
            self.assertEqual(index["count"], 1)
            self.assertEqual(len(records), 1)
            self.assertIn("secret", json.dumps(records))
            self.assertTrue((root / "reports" / "scan-summary-channels.json").exists())

    def test_allowed_hosts_include_protected_media_license_certificate_and_standby(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "config").mkdir()
            (root / "config" / "settings.json").write_text("{}", encoding="utf-8")
            publish_scan_outputs(
                channels_data={"Bangla": [{
                    "id": "drm-source",
                    "name": "DRM Source",
                    "url": "https://media.example/live.mpd?token=secret",
                    "resolution_height": 1080,
                    "drm": {
                        "type": "fairplay",
                        "license_url": "https://license.example/fps",
                        "license_headers": {"Authorization": "Bearer token"},
                        "certificate_url": "https://cert.example/fps.cer",
                    },
                    "standby": [{
                        "url": "https://standby.example/live.mpd",
                        "resolution_height": 1080,
                    }],
                }]},
                settings_path=str(root / "config" / "settings.json"),
                data_dir=str(root / "data"),
                state_dir=str(root / "state"),
                reports_dir=str(root / "reports"),
                scan_mode="channels",
            )
            allowed = json.loads((root / "data" / "allowed-hosts.json").read_text(encoding="utf-8"))
            self.assertTrue({
                "media.example", "license.example", "cert.example", "standby.example"
            }.issubset(set(allowed["hosts"])))


if __name__ == "__main__":
    unittest.main()
