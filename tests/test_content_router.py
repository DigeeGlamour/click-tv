import unittest

from scanner.content_router import route_candidate
from scanner.normalizer import Normalizer
from scanner.parsers.m3u_parser import parse_m3u_content


class ContentRouterTests(unittest.TestCase):
    def test_tv_source_direct_movie_is_rerouted(self):
        item = route_candidate(
            {
                "name": "100 Love (2012)",
                "url": "http://example.test/ftp/Movies/indianbangla/100-love.mkv",
                "source_pipeline": "tv",
            }
        )
        self.assertEqual(item["source_pipeline"], "movies")
        self.assertEqual(item["original_source_pipeline"], "tv")
        self.assertTrue(item["pipeline_rerouted"])

    def test_normal_live_manifest_stays_tv(self):
        item = route_candidate(
            {
                "name": "T Sports",
                "url": "https://example.test/live/index.m3u8",
                "source_pipeline": "tv",
            }
        )
        self.assertEqual(item["source_pipeline"], "tv")

    def test_tv_source_movie_is_dropped_under_strict_source_separation(self):
        normalizer = Normalizer()
        item = normalizer.normalize_candidate(
            {
                "name": "A Movie (2026)",
                "url": "http://example.test/Movies/hindidub/a.mp4",
                "source_pipeline": "tv",
                "headers": {},
            }
        )
        self.assertIsNone(item)

    def test_extinf_uses_last_unquoted_comma(self):
        content = (
            "#EXTM3U\n"
            "#EXTINF:-1 tvg-logo=https://img.test/a_CR35,0,380,562_.jpg "
            "group-title=\"Movies\",Aliens Ka Aagman (2026)\n"
            "http://example.test/movie.mp4\n"
        )
        items = parse_m3u_content(content, {"id": "test", "pipeline": "tv"})
        self.assertEqual(items[0]["name"], "Aliens Ka Aagman (2026)")

    def test_manual_category_override_switch_reaches_normalizer(self):
        content = (
            "#EXTM3U\n"
            "#EXTINF:-1 group-title=\"TV: Bangla\",Angel TV Europe\n"
            "https://example.test/live.m3u8\n"
        )
        items = parse_m3u_content(content, {
            "id": "manual-test",
            "pipeline": "manual",
            "manual_can_override_category": False,
        })
        normalized = Normalizer().normalize_candidate(items[0])
        self.assertFalse(items[0]["manual_can_override_category"])
        self.assertEqual(normalized["category"], "Other")

    def test_wrong_manual_bangla_group_cannot_override_channel_identity(self):
        normalizer = Normalizer()
        cases = {
            "Zee Bangla": "Indian",
            "ABP Ananda": "Indian",
            "DW English": "Foreign News",
            "BBC Earth": "Infotainments",
            "BBC Cbeebies": "Cartoon",
            "Star Sports 1 Hindi": "Sports",
            "Star Sports1": "Sports",
            "Islamic TV": "Islamic",
        }
        for name, expected in cases.items():
            normalized = normalizer.normalize_candidate({
                "name": name,
                "url": "https://example.test/live.m3u8",
                "group_title": "TV: Bangla",
                "source_pipeline": "manual",
                "manual_can_override_category": False,
                "headers": {},
            })
            self.assertEqual(normalized["category"], expected, name)

    def test_bangladesh_identity_recovers_channels_from_other(self):
        normalizer = Normalizer()
        for name in (
            "Desh Bangla TV",
            "Green TV",
            "Ruposhi Bangla",
            "Channel S",
            "NRB TV",
        ):
            self.assertEqual(normalizer.detect_tv_category(name), "Bangla", name)

    def test_indian_bengali_is_indian_not_bangladesh_category(self):
        normalizer = Normalizer()
        for name in (
            "Star Jalsha",
            "Zee Bangla",
            "Sony Aath",
            "Colors Bangla",
            "TV9 Bangla",
            "Enter10 Bangla",
        ):
            self.assertEqual(normalizer.detect_tv_category(name), "Indian", name)

    def test_ambiguous_unknown_is_not_guessed_from_bad_group(self):
        normalized = Normalizer().normalize_candidate({
            "name": "Unknown Regional 99",
            "url": "https://example.test/live.m3u8",
            "group_title": "TV: Bangla",
            "source_pipeline": "manual",
            "manual_can_override_category": False,
            "headers": {},
        })
        self.assertEqual(normalized["category"], "Other")

    def test_malformed_header_fragments_are_not_channels(self):
        normalizer = Normalizer()
        for name in (
            "EXTVLCOPT http referrer https www timesnownews com",
            "like Gecko) Chrome 145 0 0 0 Safari 537 36",
        ):
            self.assertIsNone(normalizer.normalize_candidate({
                "name": name,
                "url": "https://example.test/live.m3u8",
                "source_pipeline": "tv",
                "headers": {},
            }))

    def test_kodi_stream_headers_become_exact_playback_headers(self):
        content = (
            "#EXTM3U\n"
            "#EXTINF:-1 group-title=\"TV: Indian\",Sony Entertainment HD\n"
            "#KODIPROP:inputstream.adaptive.stream_headers="
            "Host=bldcmprod-cdn.toffeelive.com&"
            "cookie=Edge-Cache-Cookie%3DURLPrefix%3Dabc%3ASignature%3Dxyz&"
            "user-agent=okhttp%2F5.1.0&client-api-header=null\n"
            "https://bldcmprod-cdn.toffeelive.com/cdn/live/sony/playlist.m3u8\n"
        )
        items = parse_m3u_content(content, {
            "id": "toffee-test",
            "pipeline": "tv",
            "headers": {"User-Agent": "static-profile/1.0"},
        })
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["headers"]["Cookie"], "Edge-Cache-Cookie=URLPrefix=abc:Signature=xyz")
        self.assertEqual(items[0]["headers"]["User-Agent"], "okhttp/5.1.0")
        self.assertEqual(items[0]["headers"]["client-api-header"], "null")
        self.assertNotIn("Host", items[0]["headers"])
        self.assertEqual(items[0]["drm"], {})


if __name__ == "__main__":
    unittest.main()
