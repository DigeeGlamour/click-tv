"""CLICK_TV_SPORTS_CHANNEL_CARD_DESIGN_UPDATED.md, section by section.

The parts of the card design that can be proved without a browser: what the
stylesheet is allowed to reach, what the card factory must contain, and - most
of all - section 2's hard lock and section 16's "do not show". The rendered
geometry, the responsive rules and every interaction are proved in a real browser
by channeluitest.mjs and cardtest.mjs; these are the assertions that must hold
even when no browser is available.
"""

import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SITE = ROOT / "site"
APP = (SITE / "assets/js/app.js").read_text(encoding="utf-8")
INDEX = (SITE / "index.html").read_text(encoding="utf-8")
SW = (SITE / "sw.js").read_text(encoding="utf-8")
CHANNEL_CSS = (SITE / "assets/css/event-channel-cards.css").read_text(encoding="utf-8")
EVENT_CSS = (SITE / "assets/css/event-cards.css").read_text(encoding="utf-8")
FINAL_CSS = (ROOT / "site" / "assets" / "css" / "final-match-cards.css").read_text(
    encoding="utf-8")


def _code_only(text: str) -> str:
    """Source with comments removed, so an assertion reads code and not prose.

    Without this, "no innerHTML" in a comment satisfies a search for innerHTML
    and the assertion proves nothing.
    """
    without_block = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    return "\n".join(
        re.sub(r"//.*$", "", line) for line in without_block.splitlines()
    )


class Section2TheHardLockHolds(unittest.TestCase):
    """The design work is confined to the Today/Upcoming event-card area.

    The new stylesheet is the thing that could break this, so it is the thing
    that gets checked: it may not name the player, the video element, the
    header, the sidebar's own width, the navigation or the player controls.
    """

    FORBIDDEN = (
        "#videoPlayer", "#videoContainer", ".video-container", ".video-section",
        "#playerControls", ".player-controls", ".app-header", ".header-left",
        ".header-right", ".header-center", ".youtube-layout", ".final-shell",
        ".desktop-category-rail", ".desktop-main-navigation", ".final-main-nav",
        ".final-sub-nav", ".video-meta", ".meta-title-row", "#playerClock",
        ".ctrl-btn", ".progress-container", "#qualityBtn", "#networkBtn",
        ".side-panel", ".sidebar-scroll-area",
    )

    def test_the_new_stylesheet_names_nothing_that_is_locked(self):
        for token in self.FORBIDDEN:
            self.assertNotIn(token, CHANNEL_CSS, token)

    def test_it_sets_no_geometry_on_the_sidebar_or_the_player(self):
        """A width on .sidebar-section would change the locked proportions."""
        for selector in (".sidebar-section{", ".sidebar-section {",
                         ".sidebar-list{", ".sidebar-list {"):
            self.assertNotIn(selector, CHANNEL_CSS, selector)

    def test_every_rule_is_scoped_to_the_event_list(self):
        allowed = (
            ".sidebar-section.event-list-mode", "@keyframes eventChannelEq",
            "@keyframes tmChannelPulse", "@keyframes tmChannelSwitching",
        )
        for line in CHANNEL_CSS.splitlines():
            stripped = line.strip()
            if not stripped.startswith((".", "@keyframes")):
                continue
            for selector in stripped.split("{")[0].split(","):
                selector = selector.strip()
                if not selector:
                    continue
                self.assertTrue(
                    selector.startswith(allowed),
                    f"event-channel-cards.css must not reach outside the event "
                    f"list: {selector}",
                )

    def test_the_locked_event_row_keeps_its_own_height(self):
        """Section 2 plus cardtest's uniform-height assertion: the selector is a
        sibling of the row, never a child of it, so the row is still 152px and
        every card in the list is still the same height as every other."""
        self.assertIn("height:152px!important", EVENT_CSS)
        self.assertNotIn("152px", _code_only(CHANNEL_CSS))
        # The shell must not impose a height either.
        shell = re.search(r"\.event-card-shell\{([^}]*)\}", CHANNEL_CSS)
        self.assertIsNotNone(shell)
        self.assertIn("height:auto!important", shell.group(1))

    def test_the_channel_buttons_are_built_inside_the_card(self):
        """The approved design puts them there.

        The older architecture hung the strip beside the row in a shell so the
        row could keep one locked height. The design the owner signed off has
        the buttons in the card's own `.card-lower`, which makes the card one
        node and its height its own content's business - which is also what
        the masonry needs.
        """
        self.assertIn('<div class="card-lower">${todayChannelPillsHtml(item)}</div>', APP)
        self.assertNotIn("shell.appendChild(card)", APP)


class Section4And5TheChannelChip(unittest.TestCase):
    def test_the_strip_and_chip_exist_in_the_card_factory(self):
        for marker in (
            "function eventChannelStripHtml(",
            "function channelChipSummary(",
            "function channelChipIconHtml(",
            "function updateEventChannelStrip(",
            "function bindEventChannelStrip(",
            "function pruneStaleChannelSelections(",
        ):
            self.assertIn(marker, APP, marker)

    def test_the_summary_is_primary_backups_and_optional_dupes(self):
        self.assertIn("} Primary`", APP)
        self.assertIn("'Backup' : 'Backups'", APP)
        self.assertIn("} Dupes removed`", APP)

    def test_the_dupe_note_only_appears_when_the_scanner_reported_some(self):
        self.assertIn("dropped_variant_count", APP)
        self.assertIn("if (dupes > 0)", APP)

    def test_desktop_gives_two_to_four_per_row_and_mobile_two(self):
        """Section 4, asserted as the rule rather than as one literal.

        The floor is what decides the count, and it used to be pinned at 128px -
        which is narrower than the chip's own summary needs, so three chips fitted
        the grid and then ellipsised their text to "1 Pri... 0 Ba...". The floor is
        therefore checked as a minimum, and no rule may ask for more than four.
        """
        floors = [
            int(value)
            for value in re.findall(r"repeat\(auto-fit,minmax\((\d+)px,1fr\)\)", CHANNEL_CSS)
        ]
        self.assertTrue(floors, "the strip must size itself with auto-fit")
        self.assertGreaterEqual(min(floors), 128, "a chip narrower than this cannot show its summary")
        # Only rules that *reduce* the count may pin an exact number.
        for count in re.findall(r"grid-template-columns:repeat\((\d+),", CHANNEL_CSS):
            self.assertLessEqual(int(count), 4, "section 4 caps a row at four chips")
        self.assertIn('[data-columns="2"]', CHANNEL_CSS)
        self.assertIn('[data-columns="1"]', CHANNEL_CSS)
        mobile = CHANNEL_CSS.split("@media (max-width:1000px)", 1)[1]
        self.assertIn("repeat(2,minmax(0,1fr))", mobile)

    def test_the_chip_icon_uses_contain_like_the_card_artwork(self):
        icon = re.search(r"\.event-channel-chip-icon img\{([^}]*)\}", CHANNEL_CSS)
        self.assertIsNotNone(icon)
        self.assertIn("object-fit:contain!important", icon.group(1))


class Section6And8SelectedAndPlaying(unittest.TestCase):
    def test_selected_has_its_own_visible_state(self):
        self.assertIn(".event-channel-chip.is-selected", CHANNEL_CSS)
        self.assertIn("is-selected", APP)
        self.assertIn('aria-pressed', APP)

    def test_the_highlight_is_the_themes_green(self):
        block = CHANNEL_CSS.split(".event-channel-chip.is-selected{", 1)[1].split("}", 1)[0]
        self.assertIn("rgba(37,230,165", block)

    def test_playing_is_shown_separately_from_selected(self):
        self.assertIn(".event-channel-chip.is-playing", CHANNEL_CSS)
        self.assertIn("event-card-shell.is-playing-event", CHANNEL_CSS)
        self.assertIn("is-playing-event", APP)

    def test_the_playing_event_highlight_cannot_move_a_neighbour(self):
        block = CHANNEL_CSS.split(".event-card-shell.is-playing-event{", 1)[1].split("}", 1)[0]
        for shifting in ("transform", "margin", "padding", "width", "height"):
            self.assertNotIn(shifting, block, shifting)


class Section7ChannelClickBehaviour(unittest.TestCase):
    def test_a_chip_click_selects_that_channel_and_nothing_else(self):
        self.assertIn("selectEventChannel(eventChannelId(item), channelId)", APP)
        # It must not bubble into the card's own play handler.
        self.assertIn("event.stopPropagation();", APP)

    def test_the_card_click_handler_ignores_the_strip(self):
        self.assertIn(
            ".card-fav-btn, .card-remind-btn, .event-channel-strip", APP,
        )

    def test_selection_reorders_the_existing_plan_rather_than_reloading(self):
        """Section 7 and 18: the click costs one playback start. No catalogue
        fetch, no list rebuild."""
        body = APP.split("function bindEventChannelStrip(", 1)[1].split("\nfunction ", 1)[0]
        for forbidden in ("fetchJson", "loadCatalogue", "renderCurrentList",
                          "location.reload", "refreshCatalogue"):
            self.assertNotIn(forbidden, body, forbidden)
        self.assertIn("function channelStreamOrder(", APP)
        self.assertIn("function orderSourcesByChannel(", APP)


class Section9And13NoFakeChannelBar(unittest.TestCase):
    def test_no_resolved_channel_renders_no_strip_at_all(self):
        self.assertIn("if (channels.length < 1) return '';", APP)
        self.assertIn("event-card-no-channels", APP)

    def test_a_card_with_no_channel_says_so_in_its_own_words(self):
        # Never an empty row, and never an invented broadcaster.
        body = APP.split("function todayChannelPillsHtml(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("channel-pill muted", body)
        self.assertIn("চ্যানেল শীঘ্রই যোগ হবে", body)
        self.assertIn("event-card-no-channels", APP)

    def test_the_chip_name_comes_only_from_the_published_channel(self):
        """Section 9: no invented name. The label is channel.name and nothing
        else - no provider id, no source id, no renderer, no placeholder."""
        body = _code_only(
            APP.split("function eventChannelStripHtml(", 1)[1].split("\nfunction ", 1)[0])
        self.assertIn("String(channel.name || '').trim()", body)
        # The chip's visible text and its accessible name are both `label`, and
        # `label` is the published channel name with nothing added to it.
        self.assertIn('class="event-channel-chip-name">${escapeHtml(label)}', body)
        self.assertIn('aria-label="${escapeHtml(label)}"', body)
        # No fallback name exists anywhere in the builder, so an unnamed channel
        # cannot acquire one. ("Channel options" labels the group, not a
        # broadcaster, which is why the chip's own label is what is checked.)
        for invented in ("Unknown", "Server", "Feed", "Untitled", "provider",
                         "source_id", "renderer", "|| 'Channel"):
            self.assertNotIn(invented, body, invented)


class Section16NothingTechnicalReachesTheCard(unittest.TestCase):
    """The chip may read a name, a logo, roles and counts. Nothing else."""

    SECRET_FIELDS = (
        "url", "final_url", "headers", "header_profile", "cookie", "drm",
        "license", "token", "authorization", "proxy_mode", "playback_id",
        "requires_headers", "credential_hints", "protected_source",
        "inherit_manifest_query",
    )

    def _chip_source(self):
        parts = []
        for name in ("eventChannelStripHtml", "channelChipSummary",
                     "channelChipIconHtml"):
            body = APP.split(f"function {name}(", 1)[1].split("\nfunction ", 1)[0]
            parts.append(body)
        return "\n".join(parts)

    def test_the_chip_builder_reads_no_technical_field(self):
        source = self._chip_source()
        for field in self.SECRET_FIELDS:
            self.assertNotIn(f"channel.{field}", source, field)
            self.assertNotIn(f"stream.{field}", source, field)
            self.assertNotIn(f"entry.{field}", source, field)

    def test_the_chip_markup_carries_only_a_channel_id(self):
        source = self._chip_source()
        attributes = set(re.findall(r'data-([a-z-]+)=', source))
        self.assertTrue(attributes <= {"channel-id", "channel-strip", "columns",
                                       "channel-art"}, attributes)

    def test_the_stylesheet_has_no_content_that_could_leak(self):
        for token in ("token", "cookie", "drm", "license", "http://", "https://"):
            self.assertNotIn(token, CHANNEL_CSS.lower(), token)


class Section18RefreshDoesNotDisturbPlayback(unittest.TestCase):
    def test_the_keyed_node_is_the_shell(self):
        self.assertIn("[data-event-shell], .event-ref-card[data-uid]", APP)

    def test_the_playing_card_keeps_its_exact_node_and_its_strip(self):
        block = APP.split("if (previous && isPinnedSession(item)) {", 1)[1]
        block = block.split("return;", 1)[0]
        self.assertIn("updateEventChannelStrip(previous, item)", block)
        self.assertNotIn("createEventCard", _code_only(block))
        self.assertNotIn("innerHTML", _code_only(block))

    def test_a_healthy_selection_survives_a_channel_list_update(self):
        body = APP.split("function pruneStaleChannelSelections(", 1)[1]
        body = body.split("\nfunction ", 1)[0]
        # An empty list is not evidence the choice went away.
        self.assertIn("if (!channels.length) return;", body)
        self.assertIn("delete state.channelSelection[key]", body)

    def test_state_is_refreshed_in_place_rather_than_rebuilt(self):
        body = APP.split("function updateEventChannelStrip(", 1)[1]
        body = body.split("\nfunction ", 1)[0]
        self.assertIn("classList.toggle('is-selected'", body)
        self.assertNotIn("innerHTML", _code_only(body))


class Section17SmartFilterIsUntouched(unittest.TestCase):
    def test_the_filter_still_exists_and_still_orders_sports(self):
        for marker in ("function renderEventSportFilter(",
                       "function setEventSportFilter(",
                       "function isEventSportFilterOpen("):
            self.assertIn(marker, APP, marker)

    def test_an_event_hides_with_its_channels_because_they_share_a_node(self):
        """Section 17: filtering hides the event and its channels together. They
        do, by construction - the buttons are inside the card the filter
        removes, which is one node."""
        self.assertIn('<div class="card-lower">${todayChannelPillsHtml(item)}</div>', APP)

    def test_the_filter_stylesheet_is_not_touched_by_this_phase(self):
        self.assertIn("assets/css/smart-filter.css", INDEX)


class Section21EmbedFallbackStaysProviderAgnostic(unittest.TestCase):
    def test_no_renderer_label_is_shown_on_the_card(self):
        body = APP.split("function eventChannelStripHtml(", 1)[1].split("\nfunction ", 1)[0]
        for label in ("embed", "Embed", "native", "Native", "streamed", "Streamed"):
            self.assertNotIn(label, body, label)

    def test_the_native_first_and_embed_last_plumbing_is_untouched(self):
        for marker in ("function embedRoutes(", "function tryEmbedFallback(",
                       "function mountEmbedRenderer(", "function unmountEmbedRenderer(",
                       "function applyEmbedControlMode("):
            self.assertIn(marker, APP, marker)

    def test_the_embed_stylesheet_still_owns_the_control_hiding(self):
        embed_css = (SITE / "assets/css/embed-player.css").read_text(encoding="utf-8")
        self.assertIn("[data-embed-disabled]", embed_css)
        self.assertIn("pointer-events: none", embed_css)


class TheAssetsAreShippedAndVersionedTogether(unittest.TestCase):
    def test_the_stylesheet_is_linked_and_precached(self):
        self.assertIn("assets/css/event-channel-cards.css", INDEX)
        self.assertIn('"/assets/css/event-channel-cards.css"', SW)

    def test_every_versioned_asset_moves_in_lockstep(self):
        """All four assets carry one version, and the worker cache moves with it.

        The version is read out of index.html rather than written here. Pinning the
        literal made this test fail on every deliberate bump and pass again as soon
        as someone edited the string - so it never actually checked the thing that
        matters, which is that no asset is left behind on the old version.
        """
        versions = dict(
            re.findall(r"(event-cards\.css|event-channel-cards\.css|embed-player\.css|app\.js)\?v=([\w.-]+)", INDEX)
        )
        for asset in ("event-cards.css", "event-channel-cards.css",
                      "embed-player.css", "app.js"):
            self.assertIn(asset, versions, f"{asset} must be version-stamped")
        self.assertEqual(
            len(set(versions.values())), 1,
            f"every asset must move together, got {versions}",
        )
        cache = re.search(r'CACHE_VERSION\s*=\s*"([^"]+)"', SW)
        self.assertIsNotNone(cache, "the service worker must declare a cache version")
        stamp = next(iter(set(versions.values())))
        # The asset stamp is "<date>-<slug>-vN" and the cache is
        # "click-tv-<slug>-<date>-vNN"; the slug is what ties them together.
        slug = "-".join(stamp.split("-")[1:-1])
        self.assertIn(slug, cache.group(1),
                      f"cache version {cache.group(1)!r} does not name asset build {slug!r}")


class TodayMatchCardV2(unittest.TestCase):
    """The Today Match redesign, against a supplied reference file: a
    minimal poster-led card - serial badge, category badge, league name,
    title, channel buttons and nothing else - scoped to every item on the
    Today Match tab, with the Upcoming tab's existing card left untouched in
    every respect. The rendered geometry and the real masonry
    layout are proved in a real browser separately; these assertions are
    what must hold even without one.
    """

    def test_the_minimal_card_is_only_reached_from_the_today_match_tab(self):
        body = APP.split("function createEventCard(item, visualIndex) {", 1)[1].split(
            "\nfunction ", 1)[0]
        self.assertIn("state.view === VIEW.EVENT", body)
        self.assertNotIn("state.view === VIEW.EVENT && liveLike", body)
        self.assertIn("createTodayMatchCardV2(", body)
        self.assertIn("createUpcomingTeamRow(", body)

    def test_the_minimal_card_carries_none_of_the_hidden_fields(self):
        body = APP.split("function createTodayMatchCardV2(", 1)[1].split(
            "\nfunction createEventCard", 1)[0]
        for forbidden in (
            "event-status-pill", "event-now-playing", "event-card-time",
            "event-card-streams", "event-verified-tick", "card-fav-btn",
            "card-remind-btn", "event-card-action", "event-card-phase",
        ):
            self.assertNotIn(forbidden, body, forbidden)

    def test_the_minimal_card_keeps_serial_badge_category_league_and_title(self):
        body = APP.split("function createTodayMatchCardV2(", 1)[1].split(
            "\nfunction createEventCard", 1)[0]
        for required in ("rank-tag", "sport-tag", "league-tag", "match-title",
                         "poster-caption", "gold-rule"):
            self.assertIn(required, body)

    def test_a_single_channel_gets_one_full_width_button(self):
        """It used to render nothing at all, so a card with one source showed
        neither its name nor any play affordance - and the sources are not
        interchangeable: Tapmad, Sony Sports Ten 5 and Willow differ in quality
        and in whether they work. The owner asked for one full-width button in
        the same chip the multi-channel strip already uses, labelled with the
        play glyph, the channel and its quality band."""
        # The minimal branch, sliced to where the full strip begins.
        strip = APP[APP.index("if (minimal) {"):]
        strip = strip[:strip.index("const columns = Math.min(")]
        self.assertIn("channels.length === 1", strip)
        self.assertIn("tm-channels-one", strip)
        self.assertIn("tm-channel-solo", strip)
        self.assertIn("channelQualityBand(only)", strip)
        # The same chip class, so it inherits the existing look exactly.
        self.assertIn("event-channel-chip tm-channel", strip)

    def test_two_or_more_channels_are_untouched(self):
        """The owner asked for the multi-channel case to stay exactly as it
        was, so the change must not have reached it."""
        # The minimal branch, sliced to where the full strip begins.
        strip = APP[APP.index("if (minimal) {"):]
        strip = strip[:strip.index("const columns = Math.min(")]
        self.assertIn("const chips = channels.map((channel) => {", strip)
        self.assertIn('class="event-channel-strip tm-channels"', strip)

    def test_the_minimal_channel_button_carries_only_the_name(self):
        body = APP.split("function eventChannelStripHtml(item, minimal = false) {", 1)[1].split(
            "\nfunction ", 1)[0]
        minimal_branch = body.split("if (minimal) {", 1)[1].split("\n  if (channels.length < 1)", 1)[0]
        self.assertNotIn("channelChipIconHtml", minimal_branch)
        self.assertNotIn("channelChipSummary", minimal_branch)

    def test_the_tab_decides_which_of_the_two_cards_is_built(self):
        # Two finalised designs, one per tab, sharing nothing but the data:
        # the poster card on Today Match, the two-team row on Upcoming.
        body = APP.split("function createEventCard(item, visualIndex) {", 1)[1].split(
            "\nfunction ", 1)[0]
        self.assertIn("state.view === VIEW.EVENT", body)
        self.assertLess(
            body.index("createTodayMatchCardV2("),
            body.index("createUpcomingTeamRow("),
        )

    def test_the_today_match_list_is_one_packing_grid(self):
        # The approved design packs the two columns itself: one grid of 1px
        # rows, each card spanning the rows its own content needs. Two fixed
        # DOM columns could not do that - a card could only sit under the card
        # above it, however tall that one happened to be, which is exactly the
        # empty space under a short card the owner asked to be rid of.
        self.assertIn("function ensureTodayGrid()", APP)
        self.assertIn("function layoutTodayMasonry()", APP)
        self.assertIn("grid-template-columns:1fr 1fr!important", FINAL_CSS)
        self.assertIn("grid-auto-rows:1px!important", FINAL_CSS)
        # The span still comes from the card's own measured height with a
        # floor of one row - that is the packing rule and it has not changed.
        #
        # This used to assert one exact expression,
        # "card.style.gridRowEnd = `span ${Math.max(1, span)}`". The write is
        # now guarded so it only happens when the span really changes, because
        # the unguarded version re-laid out every card on every scroll tick
        # that reached it. The arithmetic below is the part that decides the
        # layout; see tests/test_today_match_frontend.py for why the reset
        # pass that used to precede it had to go.
        self.assertIn("Math.ceil((height + rowGap) / (rowHeight + rowGap))", APP)
        self.assertIn("card.style.gridRowEnd = next", APP)
        self.assertNotIn("column-count:2!important", EVENT_CSS)

    def test_the_masonry_columns_are_scoped_away_from_upcoming(self):
        # The class that turns the list into columns is toggled only for
        # VIEW.EVENT - Upcoming keeps sidebar-list.upcoming-grid's existing
        # single-column flex layout, unmodified.
        body = APP.split("function renderCurrentList(reset = true, options = {}) {", 1)[1].split(
            "\nfunction ", 1)[0]
        self.assertIn("sidebarList.classList.toggle('today-grid', state.view === VIEW.EVENT)", body)

    def test_a_card_is_appended_whole_into_the_one_grid(self):
        # One node, one grid cell, so there is no mid-card break to guard
        # against. The alternating split into two column containers is gone:
        # the grid decides where a card lands from its own height.
        for fn_name in ("appendNextChunk", "reconcileEventCards"):
            body = APP.split(f"function {fn_name}(", 1)[1].split("\nfunction ", 1)[0]
            self.assertIn("ensureTodayGrid()", body, fn_name)
            self.assertNotIn("index % 2", body, fn_name)


class NoPlaceholdersAreLeftBehind(unittest.TestCase):
    """The delivery rule: no TODO, no placeholder, no "later"."""

    FILES = (
        "site/assets/js/app.js",
        "site/assets/css/event-channel-cards.css",
        "site/assets/css/event-cards.css",
        "site/assets/css/embed-player.css",
        "site/sw.js",
        "site/index.html",
    )

    def test_no_shipped_frontend_file_carries_an_unfinished_marker(self):
        for name in self.FILES:
            text = (ROOT / name).read_text(encoding="utf-8")
            for marker in ("TODO", "FIXME", "XXX:", "placeholder for",
                           "not implemented", "coming soon"):
                self.assertNotIn(
                    marker, text, f"{name} still contains {marker!r}",
                )


if __name__ == "__main__":
    unittest.main()
