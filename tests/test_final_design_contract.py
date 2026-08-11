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
        cls.reference_css = (cls.root / "site/assets/css/reference-design.css").read_text(encoding="utf-8")
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

    def test_reference_design_is_external_and_preserves_the_three_column_contract(self) -> None:
        self.assertIn("reference-design.css?v=20260812-reference-v16", self.index)
        self.assertIn("app.js?v=20260812-reference-v9", self.index)
        self.assertIn('class="desktop-category-rail"', self.index)
        self.assertIn('id="desktopCategoryTitle"', self.index)
        self.assertIn('id="mobileBottomSearchBtn"', self.index)
        self.assertIn('id="mobileBottomNoticeBtn"', self.index)
        self.assertIn('grid-template-columns:215px minmax(0,1fr) clamp(390px,24vw,460px)!important', self.reference_css)
        self.assertIn("video.style.setProperty('object-fit', 'contain', 'important')", self.app)
        self.assertIn('grid-template-rows:auto auto!important', self.reference_css)
        self.assertIn('aspect-ratio:16/9!important', self.reference_css)
        self.assertIn('justify-content:flex-start!important', self.reference_css)
        self.assertIn('if (!video.paused)', self.app)
        self.assertIn('}, 3200);', self.app)
        self.assertIn('id="currentResolutionBadge"', self.index)
        self.assertIn('id="currentResolutionValue"', self.index)
        self.assertIn('height:84px!important;min-height:84px!important;max-height:84px!important', self.reference_css)
        self.assertIn('color:var(--ref-green)', self.reference_css)
        self.assertIn("video.addEventListener('resize', updateStreamInfoBadge)", self.app)
        self.assertIn('width:min(78vw,380px)!important', self.reference_css)
        self.assertIn('width:min(80vw,370px)!important', self.reference_css)
        self.assertIn('grid-auto-rows:76px!important', self.reference_css)
        self.assertIn('grid-auto-rows:196px!important', self.reference_css)
        self.assertIn("list.classList.add('movie-search-grid')", self.app)
        self.assertIn("if (opening) populateFullscreenDrawer('')", self.app)
        self.assertIn('resetFullscreenDrawerSearch();', self.app)
        self.assertIn('drawerGlobalCatalogPromise', self.app)
        self.assertIn('loadFullscreenGlobalCatalog()', self.app)
        self.assertIn("if (!normalized && seriesModule?.populateFullscreenDrawer?.('')) return", self.app)
        self.assertIn('--ref-notice-h:22px', self.reference_css)
        self.assertIn('--ref-header-h:54px', self.reference_css)
        self.assertIn('aspect-ratio:16/9!important', self.reference_css)
        self.assertIn('grid-template-areas:"rail player catalog"!important', self.reference_css)
        self.assertIn('.sticky-notice[hidden],.sticky-notice.notice-dismissed{display:none!important}', self.reference_css)
        self.assertIn('.app-header .search-wrap.search-open{width:235px!important}', self.reference_css)
        self.assertIn('.video-meta .meta-subtitle-row{display:none!important}', self.reference_css)
        self.assertIn('scrollbar-width:none!important', self.reference_css)
        self.assertIn(".series-episode-list{grid-template-columns:repeat(2,minmax(0,1fr))!important", self.reference_css)
        self.assertLess(len(self.index.encode("utf-8")), 25_000)

    def test_only_requested_mobile_design_tweaks_are_locked(self) -> None:
        # Mobile main-category labels are deliberately larger than the desktop 12px.
        self.assertRegex(
            self.css,
            r"\.final-main-button\s*\{[^}]*font-size:13px!important",
        )
        self.assertRegex(
            self.css,
            r"\.final-main-button\s*\{[^}]*font-size:12\.7px!important",
        )

        # Phone action text stays readable while only its red outline box is compressed.
        self.assertRegex(
            self.css,
            r"\.app-header \.header-glow-btn\s*\{[^}]*height:32px!important;[^}]*padding:0 7px!important",
        )
        self.assertRegex(
            self.css,
            r"\.app-header \.header-glow-btn\s*\{[^}]*height:31px!important;[^}]*padding:0 6px!important",
        )

        # On phones, the fixed shell contains the player/navigation while only the card area scrolls.
        self.assertRegex(
            self.css,
            r"html,body\s*\{[^}]*height:100dvh!important;[^}]*overflow:hidden!important",
        )
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

    def test_manual_broad_category_can_be_ignored(self) -> None:
        normalizer = Normalizer()
        normalized = normalizer.normalize_candidate({
            "name": "Angel TV Europe",
            "url": "https://example.test/live.m3u8",
            "group_title": "TV: Bangla",
            "source_pipeline": "manual",
            "manual_can_override_category": False,
            "headers": {},
        })
        self.assertEqual(normalized["category"], "Other")


if __name__ == "__main__":
    unittest.main()
