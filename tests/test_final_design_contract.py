from __future__ import annotations

import unittest
from pathlib import Path

from scanner.normalizer import Normalizer


class FinalDesignContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.index = (cls.root / "site/index.html").read_text(encoding="utf-8")
        cls.css = (cls.root / "site/assets/css/final-design.css").read_text(encoding="utf-8")
        cls.app = (cls.root / "site/assets/js/app.js").read_text(encoding="utf-8")

    def test_approved_layout_and_navigation_contract(self) -> None:
        for element_id in (
            'id="mobileMainNav"',
            'id="desktopMainNav"',
            'id="mobileSubNav"',
            'id="desktopSubNav"',
            'id="sidebarScrollArea"',
            'id="videoContainer"',
            'id="mobileSearchToggleBtn"',
        ):
            self.assertIn(element_id, self.index)

        for label in (
            "Live Sports",
            "Live TV",
            "Movies",
            "Drama",
            "Favorites",
            "Today Match",
            "Upcoming Match",
            "Infotainments",
            "Premium",
        ):
            self.assertIn(label, self.app)

        navigation_block = """const FINAL_MAIN_GROUPS = Object.freeze([
  ['sports', 'Live Sports'],
  ['live-tv', 'Live TV'],
  ['movies', 'Movies'],
  ['drama', 'Drama'],
  ['favorites', 'Favorites']
]);"""
        self.assertIn(navigation_block, self.app)
        self.assertIn("final-design.css", self.index)
        self.assertIn("series.js", self.index)

    def test_only_requested_mobile_design_tweaks_are_locked(self) -> None:
        # Mobile main-category labels are deliberately larger than the desktop 12px.
        self.assertIn(".final-main-button{font-size:14px!important", self.css)
        self.assertIn(".final-main-button{font-size:13.5px!important", self.css)

        # Phone action text stays readable while only its red outline box is compressed.
        self.assertIn("height:31px!important;padding:0 5px!important", self.css)
        self.assertIn("height:30px!important;padding:0 4px!important;font-size:11px!important", self.css)

        # On phones, the fixed shell contains the player/navigation while only the card area scrolls.
        self.assertIn("body{overflow:hidden!important;height:100dvh!important}", self.css)
        self.assertIn(".sidebar-scroll-area{", self.css)
        self.assertIn("overflow-y:auto!important", self.css)

    def test_existing_player_controls_and_notices_remain_in_dom(self) -> None:
        for element_id in (
            'id="videoPlayer"',
            'id="playerControls"',
            'id="qualityBtn"',
            'id="networkBtn"',
            'id="fullscreenBtn"',
            'id="qualityAvailabilityBadge"',
            'id="muteNoticeText"',
            'id="resumeBadge"',
            'id="playerMsg"',
            'id="fsDrawer"',
        ):
            self.assertIn(element_id, self.index)


    def test_every_static_app_id_reference_exists_in_final_html(self) -> None:
        import re

        referenced = set(re.findall(r"\$\('([^']+)'\)", self.app))
        referenced.update(re.findall(r'\$\("([^"]+)"\)', self.app))
        available = set(re.findall(r'id="([^"]+)"', self.index))
        self.assertEqual(sorted(referenced - available), [])

    def test_new_category_routing(self) -> None:
        normalizer = Normalizer()
        candidates = [
            ({"name": "Discovery Kids", "group_title": "Kids"}, "Cartoon"),
            ({"name": "National Geographic", "group_title": "Documentary"}, "Infotainments"),
            ({"name": "Unknown Regional 99", "group_title": ""}, "Other"),
            ({"name": "Disney Hotstar Original Movie", "group_title": "Disney Hotstar", "source_pipeline": "movies", "url": "https://example.test/movie.mkv"}, "Premium"),
        ]

        for raw, expected in candidates:
            candidate = {
                "url": "https://example.test/live.m3u8",
                "source_pipeline": "tv",
                "headers": {},
                **raw,
            }
            normalized = normalizer.normalize_candidate(candidate)
            self.assertEqual(normalized["category"], expected)


if __name__ == "__main__":
    unittest.main()
