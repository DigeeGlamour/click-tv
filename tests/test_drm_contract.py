import unittest

from scanner.drm import normalize_drm


class DrmContractTests(unittest.TestCase):
    def test_kodi_widevine_license_is_not_parsed_as_clearkey(self):
        drm = normalize_drm({
            "license_type": "com.widevine.alpha",
            "license_key": (
                "https://license.example/wv|Authorization=Bearer%20abc&X-Device=tv|R{SSM}|"
            ),
        })
        self.assertEqual(drm["type"], "widevine")
        self.assertEqual(drm["license_url"], "https://license.example/wv")
        self.assertEqual(drm["license_headers"]["Authorization"], "Bearer abc")
        self.assertEqual(drm["license_request_template"], "R{SSM}")
        self.assertNotIn("clear_keys", drm)

    def test_unambiguous_hex_pair_becomes_clearkey(self):
        drm = normalize_drm({
            "license_key": "59f50679c9e60963bd0cb6640992aaaa:8685817c4d31f322e08940feeae2855a"
        })
        self.assertEqual(drm["type"], "clearkey")
        self.assertIn("clear_keys", drm)

    def test_properties_only_are_not_fake_drm(self):
        self.assertEqual(normalize_drm({"properties": {"manifest_headers": "a=b"}}), {})


if __name__ == "__main__":
    unittest.main()
