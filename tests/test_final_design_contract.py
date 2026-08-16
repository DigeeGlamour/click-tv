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
        cls.event_css = (cls.root / "site/assets/css/event-cards.css").read_text(encoding="utf-8")
        cls.filter_css = (cls.root / "site/assets/css/smart-filter.css").read_text(encoding="utf-8")
        cls.service_worker = (cls.root / "site/sw.js").read_text(encoding="utf-8")

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
        self.assertIn("reference-design.css?v=20260816-movie-controls-notice-v4", self.index)
        self.assertIn("app.js?v=20260817-card-clean-v1", self.index)
        self.assertIn('CACHE_VERSION = "click-tv-design-playback-20260817-v27-card-clean"', self.service_worker)
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
        self.assertIn('width:min(78vw,310px)!important', self.reference_css)
        self.assertIn('grid-auto-rows:76px!important', self.reference_css)
        self.assertIn('grid-auto-rows:174px!important', self.reference_css)
        self.assertIn("list.classList.add('movie-search-grid')", self.app)
        self.assertIn("populateFullscreenDrawer('');", self.app)
        self.assertIn('resetFullscreenDrawerSearch();', self.app)
        self.assertIn("if (key === 'Backspace' || code === 8) return", self.app)
        self.assertIn("posterWrap.style.setProperty('background-image'", self.app)
        self.assertIn('filter:drop-shadow(0 2px 5px rgba(0,0,0,.55))!important', self.reference_css)
        self.assertIn('drawerGlobalCatalogPromise', self.app)
        self.assertIn('loadFullscreenGlobalCatalog()', self.app)
        self.assertIn("if (!normalized && seriesModule?.populateFullscreenDrawer?.(''))", self.app)
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

    def test_ruman26_mobile_navigation_and_scroll_contract(self) -> None:
        for icon_key in ("sports", "live-tv", "movies", "drama", "favorites"):
            key_literal = icon_key if "-" not in icon_key else f"'{icon_key}'"
            self.assertIn(f"{key_literal}:", self.app)
        self.assertIn("sessionStorage.setItem(STORAGE_KEYS.noticeDismissed, '1')", self.app)
        self.assertIn("localStorage.removeItem(STORAGE_KEYS.noticeDismissed)", self.app)
        self.assertIn("mobileSubNavigation?.classList.toggle('sports-subnav'", self.app)
        self.assertIn('grid-template-columns:repeat(5,minmax(0,1fr))!important', self.reference_css)
        self.assertIn('.mobile-main-navigation .final-main-button[data-final-key="favorites"]{display:flex!important}', self.reference_css)
        self.assertIn('.mobile-main-navigation .final-main-button::before{content:none!important;display:none!important}', self.reference_css)
        self.assertIn('overflow-y:auto!important;overflow-x:hidden!important', self.reference_css)
        self.assertIn('grid-template-columns:repeat(2,minmax(0,1fr))!important', self.reference_css)
        self.assertIn('.sidebar-section.event-list-mode .sidebar-list.upcoming-grid{display:flex!important;flex-direction:column!important', self.reference_css)

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

    def test_modern_event_cards_keep_schedules_and_live_actions(self) -> None:
        self.assertIn("if (sourceKind === VIEW.UPCOMING) return Boolean(String(item.name || '').trim());", self.app)
        self.assertIn("'LIVE NOW'", self.app)
        # The action names what the click does. A card with a usable link plays
        # on either tab, so the label follows playability rather than the tab.
        self.assertIn("const showWatchAction = playable;", self.app)
        self.assertIn("showWatchAction ? (channelOnly ? 'Watch Channel' : 'Watch') : 'Details'", self.app)
        self.assertIn('showEventPreview(item)', self.app)
        self.assertIn('.event-ref-card', self.reference_css)
        self.assertIn('.event-status-pill.upcoming', self.reference_css)
        self.assertIn('.event-status-pill.live', self.reference_css)

    def test_event_card_upgrade_is_present_and_scoped(self) -> None:
        """The Today Match / Upcoming card upgrade.

        Everything it adds lives in one stylesheet that is scoped to the event
        list, so the channel grid, the movie grid and the player keep whatever
        the earlier stylesheets gave them.
        """
        self.assertIn("assets/css/event-cards.css", self.index)
        self.assertIn('id="sidebarCountDetail"', self.index)
        self.assertIn('id="eventPreviewFacts"', self.index)

        # Every rule in the new file is scoped to the event list or to the two
        # elements the upgrade adds outside it.
        allowed_prefixes = (
            ".sidebar-section.event-list-mode",
            ".sidebar-count-detail",
            ".event-preview-facts",
            ".event-preview-fact",
            "@keyframes eventEqualizer",
        )
        for line in self.event_css.splitlines():
            stripped = line.strip()
            if not stripped.startswith("."):
                continue
            for selector in stripped.split("{")[0].split(","):
                selector = selector.strip()
                if not selector:
                    continue
                self.assertTrue(
                    selector.startswith(allowed_prefixes),
                    f"event-cards.css must not reach outside the event list: {selector}",
                )

        # Guide sections that must be represented in the card factory.
        for marker in (
            "function eventSport(",             # 5, sport badge
            "function eventLivePhaseText(",     # 6, measured live phase
            "function eventStartedText(",       # 7, BDT start time
            "function eventStreamSummary(",     # 8, 9 and 19, stream readiness
            "function isChannelOnlyEventCard(", # 10, channel-only card
            "function stripStreamNoise(",       # 4, title without stream noise
            "function eventCountdownText(",     # 17, countdown
            "function eventVerificationLabel(", # 18, fixture verification
            "function toggleEventReminder(",    # 20, reminder
            "function updateEventCardClocks(",  # in-place clock tick
            "event-now-playing",                # 13, now playing marker
        ):
            self.assertIn(marker, self.app)

        # Guide 29 and 32: technical routing detail stays off the card face.
        card_source = self.app.split("function createEventCard(")[1].split("\nfunction ")[0]
        for forbidden in ("license_url", "requires_headers", "expires_at", "playbackBadgesHtml"):
            self.assertNotIn(forbidden, card_source)

        # Guide 34: the reminder and bookmark buttons never start playback.
        self.assertIn(".card-fav-btn, .card-remind-btn", self.app)

    def test_smart_filter_sits_in_the_events_header_and_only_filters(self) -> None:
        """The Smart Filter, against its own guide.

        The hard rule there is that the player, the sidebar, the main header
        and the card design keep every pixel they had. The filter therefore
        gets no layout of its own: its button lives inside the Events header
        row that already existed, and its stylesheet may not name anything
        outside its own classes.
        """
        import re

        # Placement: inside the existing right-side Events header (guide 2),
        # never in the player column (guide 19).
        header = self.index.split('class="sidebar-top-bar card-list-meta"')[1].split("</div>")[0]
        self.assertIn('id="eventFilterWrap"', header)
        self.assertIn('id="eventFilterBtn"', header)
        self.assertIn("assets/css/smart-filter.css", self.index)
        self.assertIn("/assets/css/smart-filter.css", self.service_worker)
        player_markup = self.index.split('id="videoContainer"')[1].split('class="sidebar-section')[0]
        self.assertNotIn("eventFilter", player_markup)

        # Guide 3: a dropdown, never a permanent row of sport tabs.
        self.assertIn('class="event-filter-menu"', self.index)
        self.assertNotIn("sport-tab", self.index)

        # Scoping: the stylesheet may only reach its own control and the
        # already-existing Events header row it sits in.
        allowed = (".event-filter", ".sidebar-section.event-list-mode")
        for line in self.filter_css.splitlines():
            stripped = line.strip()
            if not stripped.startswith("."):
                continue
            for selector in stripped.split("{")[0].split(","):
                selector = selector.strip()
                if not selector:
                    continue
                self.assertTrue(
                    selector.startswith(allowed),
                    f"smart-filter.css must not reach outside the filter: {selector}",
                )

        # Guide 9 and 10: one canonical sport field on the final card, and a
        # card that identifies no fixture is a channel rather than a sport.
        self.assertIn("sport_type:", self.app)
        self.assertIn("function eventSportType(", self.app)
        self.assertIn("return 'channel';", self.app)

        # Guide 8 and 24: the filter is the last stage and only hides, so it
        # runs before the sort block that establishes the existing order.
        sort_source = self.app.split("function applyFilterAndSort(")[1].split("\nfunction ")[0]
        self.assertIn("state.eventSportFilter !== 'all'", sort_source)
        self.assertLess(
            sort_source.index("state.eventSportFilter"),
            sort_source.index("state.currentSortMode"),
        )

        # Guide 5: every section change starts from All Events.
        view_source = self.app.split("async function selectMainView(")[1].split("\nfunction ")[0]
        self.assertIn("state.eventSportFilter = 'all';", view_source)

        # Guide 7, 20 and 21: choosing a sport re-renders the card list and
        # touches nothing else — no scan, no refetch, no playback call.
        filter_source = "\n".join(
            self.app.split(f"function {name}(")[1].split("\nfunction ")[0]
            for name in ("setEventSportFilter", "renderEventSportFilter", "eventSportCounts")
        )
        for forbidden in (
            "fetch(",
            "fetchJson",
            "startPlayback",
            "stopPlayback",
            "video.",
            "selectMainView",
            "loadRuntimeAndManifest",
            "refreshActiveEventCatalogue",
        ):
            self.assertNotIn(forbidden, filter_source, f"the filter must not call {forbidden}")

        # Guide 23: an empty result explains itself instead of going blank.
        self.assertIn("eventSportLabel(state.eventSportFilter)", self.app)

        # Guide 16 and 17: no card or header dimension is redefined here.
        for forbidden in re.findall(r"^\s*\.(?:event-ref-card|app-header|video-|sidebar-list)\b", self.filter_css, re.M):
            self.fail(f"smart-filter.css must not restyle {forbidden.strip()}")


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
