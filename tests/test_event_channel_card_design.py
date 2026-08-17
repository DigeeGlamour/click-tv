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
        allowed = (".sidebar-section.event-list-mode", "@keyframes eventChannelEq")
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

    def test_the_strip_is_built_as_a_sibling_of_the_locked_row(self):
        self.assertIn("shell.appendChild(card)", APP)
        self.assertIn("shell.append(...htmlToNodes(stripHtml))", APP)


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
        self.assertIn("repeat(auto-fit,minmax(128px,1fr))", CHANNEL_CSS)
        self.assertIn('[data-columns="4"]', CHANNEL_CSS)
        self.assertIn('[data-columns="2"]', CHANNEL_CSS)
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

    def test_a_card_with_no_strip_is_returned_as_the_bare_row(self):
        block = APP.split("const stripHtml = eventChannelStripHtml(item);", 1)[1]
        block = block.split("return shell;", 1)[0]
        self.assertIn("if (!stripHtml) {", block)
        self.assertIn("return card;", block)

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
        do, by construction - the strip is inside the shell the filter removes."""
        self.assertIn("shell.appendChild(card)", APP)
        self.assertIn("shell.append(...htmlToNodes(stripHtml))", APP)

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
        version = "20260818-event-channel-cards-v1"
        for asset in ("event-cards.css", "event-channel-cards.css",
                      "embed-player.css", "app.js"):
            self.assertIn(f"{asset}?v={version}", INDEX, asset)
        self.assertIn("click-tv-event-channel-cards-20260818-v30", SW)


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
