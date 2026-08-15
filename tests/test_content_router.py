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

    def test_today_event_only_rejects_ordinary_entertainment_channel(self):
        item = Normalizer().normalize_candidate({
            "name": "Zee Bangla",
            "url": "https://example.test/zee/index.m3u8",
            "group_title": "Entertainment",
            "source_pipeline": "today_match",
            "force_output": "today_match",
            "content_filter": "live_event_only",
            "headers": {},
        })
        self.assertIsNone(item)

    def test_today_event_only_keeps_playable_sports_channel_and_source_group(self):
        item = Normalizer().normalize_candidate({
            "name": "Willow Sports",
            "url": "https://example.test/willow/index.m3u8",
            "group_title": "Live Sports",
            "source_pipeline": "today_match",
            "force_output": "today_match",
            "content_filter": "live_event_only",
            "headers": {},
        })
        self.assertIsNotNone(item)
        self.assertEqual(item["category"], "today_match")
        self.assertEqual(item["source_category"], "Live Sports")

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
            "Channel 1",
            "Channel 1 NEWS",
            "Channel 9",
            "Citizen TV",
            "Desh Bangla TV",
            "DTV USA",
            "Global TV",
            "Green TV",
            "Music Bangla 2025",
            "My TV",
            "News 21 Bangla TV",
            "Rajdhani TV",
            "Ruposhi Bangla",
            "Channel S",
            "NRB TV",
            "TBN24",
        ):
            self.assertEqual(normalizer.detect_tv_category(name), "Bangla", name)

    def test_other_catalogue_known_identities_reach_specific_categories(self):
        normalizer = Normalizer()
        cases = {
            "92 News": "Foreign News",
            "GNN": "Foreign News",
            "Samaa TV": "Foreign News",
            "FOX 11 Green Bay WI": "Foreign News",
            "NEWS | News24": "Indian",
            "Pictures": "Indian",
            "Pasand TV": "Indian",
            "Prudent Media": "Indian",
            "Subin TV": "Indian",
            "Unique TV": "Indian",
            "Caze TV BR": "Sports",
            "Fox Deportes": "Sports",
            "Outer Delhi Warriors vs South Delhi Superstarz (2026)": "Sports",
            "Funny Junior": "Cartoon",
            "FASHION ONE": "Infotainments",
            "MQTV": "Islamic",
            "TLC": "Infotainments",
        }
        for name, expected in cases.items():
            self.assertEqual(normalizer.detect_tv_category(name), expected, name)

    def test_ampersand_channel_variants_merge_to_indian_canonical_names(self):
        normalizer = Normalizer()
        cases = {
            "Picture": "&Pictures",
            "Pictures": "&Pictures",
            "AND PICTURES": "&Pictures",
            "TV": "&TV",
            "And tv": "&TV",
        }
        for incoming, canonical in cases.items():
            normalized = normalizer.normalize_candidate({
                "name": incoming,
                "url": "https://example.test/live.m3u8",
                "source_pipeline": "tv",
                "headers": {},
            })
            self.assertEqual(normalized["name"], canonical, incoming)
            self.assertEqual(normalized["category"], "Indian", incoming)

    def test_duplicate_brand_spellings_merge_before_backup_selection(self):
        normalizer = Normalizer()
        cases = {
            "HUM": "HUM TV",
            "081 HUM MASALA": "HUM Masala",
            "HUM SITERY world": "HUM Sitaray",
            "Luxel": "Luxell",
            "JAGONEWS24": "Jago News 24",
            "NRB": "NRB TV",
            "SRKTV Bangla": "SRK TV",
            "Music Bangla 2025": "Bangla Music",
            "Ekattor": "Ekattor TV",
        }
        for incoming, canonical in cases.items():
            self.assertEqual(normalizer.clean_title(incoming), canonical, incoming)

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
