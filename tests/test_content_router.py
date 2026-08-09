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

    def test_movie_category_uses_path_markers(self):
        normalizer = Normalizer()
        item = normalizer.normalize_candidate(
            {
                "name": "A Movie (2026)",
                "url": "http://example.test/Movies/hindidub/a.mp4",
                "source_pipeline": "tv",
                "headers": {},
            }
        )
        self.assertIsNotNone(item)
        self.assertEqual(item["source_pipeline"], "movies")
        self.assertEqual(item["category"], "Dubbed")

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


if __name__ == "__main__":
    unittest.main()
