"""CLICK_TV_SPORTS_CHANNEL_SYSTEM_UPDATED.md, section by section.

The tests section 20 and section 35 ask for, plus the hard locks. Each test names
the behaviour the guide asks for rather than the shape of the code that provides
it.
"""

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner.channel_groups import (  # noqa: E402
    PLAYBACK_EMBED,
    PLAYBACK_NATIVE,
    build_event_channels,
    channel_id_for,
    default_channel_id,
    event_failover_order,
    playback_type_of,
    stream_variant_identity,
)
from scanner.channel_resolver import (  # noqa: E402
    ChannelName,
    load_alias_map,
    normalize_channel_name,
    resolve_channel_name,
    strip_stream_noise,
)
from scanner.event_lifecycle import (  # noqa: E402
    END_PENDING,
    ENDED,
    LIVE,
    LifecycleSignals,
    decide,
    has_strong_end_signal,
)
from scanner.live_protection import protect_live_events  # noqa: E402
from scanner.events import _append_embed_channels, _stamp_channel_names  # noqa: E402
from scanner.merger import (  # noqa: E402
    _channel_lineage,
    _normalized_competition,
    event_id_without_broadcaster,
    fixture_display_name,
    fixture_identity_key,
    merge_candidates,
    participant_fold_key,
    same_real_fixture,
)
from scanner.schedule_resolver import (  # noqa: E402
    _key_sides,
    _pairs_match,
    attach_streams_to_fixtures,
    enrich_event_candidates,
    team_pair_key,
)
from scanner.streamed_provider import (  # noqa: E402
    ProviderCache,
    collect_streamed_candidates,
    normalize_embed_streams,
    normalize_match,
    should_resolve_streams,
    StreamedSettings,
)

NOW = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
ALIASES = load_alias_map(ROOT / "config" / "channel-aliases.json")
# A catalogue with no fixtures, so the enrichment gate is the only thing
# deciding whether a candidate publishes.
EMPTY_FIXTURES = ROOT / "tests" / "fixtures" / "empty-event-fixtures.json"


def stream(name, url, **extra):
    base = {
        "name": name,
        "url": url,
        "source_pipeline": "today_match",
        "verified": True,
        "verification_status": "verified_global",
        "publish_allowed": True,
        "resolution_height": 720,
    }
    base.update(extra)
    return base


# ------------------------------------------------------------------ sections 1-5
class Section5OneRealMatchIsOneCard(unittest.TestCase):
    """A broadcaster in the title must not split one fixture into many cards."""

    def test_the_same_fixture_on_three_channels_is_one_key(self):
        keys = {
            fixture_identity_key({"name": name}, ALIASES)
            for name in (
                "Al Nassr Vs Al Fateh FANCODE",
                "Al Nassr Vs Al Fateh FOX DEPORTES",
                "Al Nassr Vs Al Fateh SporTV BR",
            )
        }
        self.assertEqual(keys, {"al-nassr-vs-al-fateh"}, keys)

    def test_a_broadcaster_word_inside_a_team_name_is_not_stripped(self):
        self.assertEqual(
            fixture_identity_key({"name": "Sky Blues vs Arsenal"}, ALIASES),
            "sky-blues-vs-arsenal",
        )

    def test_two_different_fixtures_stay_two_keys(self):
        self.assertNotEqual(
            fixture_identity_key({"name": "Arsenal vs Chelsea Willow"}, ALIASES),
            fixture_identity_key({"name": "Real vs Barca Willow"}, ALIASES),
        )

    def test_the_card_title_drops_the_broadcaster(self):
        self.assertEqual(
            fixture_display_name({"name": "Al Nassr Vs Al Fateh SporTV BR"}, ALIASES),
            "Al Nassr Vs Al Fateh",
        )

    def test_a_title_with_no_broadcaster_is_left_alone(self):
        self.assertEqual(
            fixture_display_name({"name": "Sri Lanka vs India"}, ALIASES),
            "Sri Lanka vs India",
        )

    def test_a_real_merge_produces_one_card_with_several_channels(self):
        candidates = [
            stream("Al Nassr Vs Al Fateh FANCODE", "https://a.test/1.m3u8"),
            stream("Al Nassr Vs Al Fateh FOX DEPORTES", "https://b.test/2.m3u8"),
            stream("Al Nassr Vs Al Fateh SporTV BR", "https://c.test/3.m3u8"),
        ]
        cards = merge_candidates(candidates)
        self.assertEqual(len(cards), 1, [c.get("name") for c in cards])
        self.assertEqual(len(cards[0]["channels"]), 3)


class Section1And3IdentityRegressions(unittest.TestCase):
    """Two shipped patterns had their word boundaries turned into backspace
    characters, which silently switched the rules off."""

    def test_the_competition_year_is_stripped(self):
        self.assertEqual(
            _normalized_competition({"competition": "Premier League 2026"}),
            _normalized_competition({"competition": "Premier League"}),
        )

    def test_no_source_file_contains_a_stray_backspace(self):
        for path in sorted((ROOT / "scanner").glob("*.py")):
            self.assertNotIn(
                b"\x08", path.read_bytes(),
                f"{path.name} has a backspace where a word boundary belongs",
            )


# ------------------------------------------------------------------ sections 6-10
class Section7And8ChannelPrimaryAndBackups(unittest.TestCase):
    """Five Willow entries become one Willow channel."""

    def _willow_five(self):
        return [
            stream("Willow", "https://a.test/w/chunks.m3u8?token=1"),
            stream("Willow", "https://a.test/w/chunks.m3u8?token=1"),   # exact duplicate
            stream("Willow HD", "https://a.test/w/chunks.m3u8?token=2"),
            stream("Willow Server 3", "https://b.test/w/chunks.m3u8"),
            stream("Willow", "https://a.test/w/chunks.m3u8?token=1"),   # exact duplicate
        ]

    def test_exact_duplicates_are_removed_and_one_channel_remains(self):
        channels, stats = build_event_channels(
            "evt-1", "Sri Lanka vs India", self._willow_five(), aliases=ALIASES
        )
        self.assertEqual(len(channels), 1)
        self.assertEqual(channels[0]["name"], "Willow")
        self.assertEqual(stats["exact_duplicates_removed"], 2)

    def test_the_survivors_become_a_primary_and_backups(self):
        channels, _ = build_event_channels(
            "evt-1", "Sri Lanka vs India", self._willow_five(), aliases=ALIASES
        )
        roles = [s["role"] for s in channels[0]["streams"]]
        self.assertEqual(roles, ["primary", "backup", "backup"])
        self.assertEqual(channels[0]["backup_count"], 2)

    def test_a_different_token_is_a_different_variant(self):
        first = stream("Willow", "https://a.test/w.m3u8?token=1")
        second = stream("Willow", "https://a.test/w.m3u8?token=2")
        self.assertNotEqual(stream_variant_identity(first), stream_variant_identity(second))

    def test_a_different_cookie_or_referer_is_a_different_variant(self):
        base = stream("Willow", "https://a.test/w.m3u8")
        with_cookie = stream("Willow", "https://a.test/w.m3u8", headers={"Cookie": "a=1"})
        with_referer = stream("Willow", "https://a.test/w.m3u8", headers={"Referer": "https://x/"})
        keys = {
            stream_variant_identity(base),
            stream_variant_identity(with_cookie),
            stream_variant_identity(with_referer),
        }
        self.assertEqual(len(keys), 3)

    def test_a_different_drm_configuration_is_a_different_variant(self):
        plain = stream("Willow", "https://a.test/w.mpd")
        drm = stream("Willow", "https://a.test/w.mpd", drm={"type": "clearkey", "key": "abc"})
        self.assertNotEqual(stream_variant_identity(plain), stream_variant_identity(drm))

    def test_an_identical_configuration_is_one_stream(self):
        left = stream("Willow", "https://a.test/w.m3u8", headers={"Referer": "https://x/"})
        right = stream("Willow HD", "https://a.test/w.m3u8", headers={"Referer": "https://x/"})
        self.assertEqual(stream_variant_identity(left), stream_variant_identity(right))


class Section9IndependentChannelDiversity(unittest.TestCase):
    """Fallback prefers another broadcaster, unless the viewer picked one."""

    def _event(self):
        return build_event_channels(
            "evt-1", "Sri Lanka vs India",
            [
                stream("Willow", "https://a.test/w1.m3u8"),
                stream("Willow HD", "https://a.test/w2.m3u8"),
                stream("Willow Server 3", "https://a.test/w3.m3u8"),
                stream("Sony Sports Ten 1", "https://b.test/t1.m3u8"),
                stream("T Sports", "https://c.test/ts.m3u8"),
            ],
            aliases=ALIASES,
        )[0]

    def test_the_same_channel_variants_share_a_lineage(self):
        self.assertEqual(
            _channel_lineage({"url": "https://cdn.test/live/147/720p/chunks.m3u8?t=1"}),
            _channel_lineage({"url": "https://cdn.test/live/147/1080p/chunks.m3u8?t=2"}),
        )

    def test_three_broadcasters_become_three_channels(self):
        names = {entry["name"] for entry in self._event()}
        self.assertEqual(len(names), 3, names)

    def test_a_selected_channel_uses_its_own_backups_first(self):
        channels = self._event()
        willow = next(c for c in channels if c["name"] == "Willow")
        order = event_failover_order(channels, willow["id"])
        self.assertEqual(
            [step["channel_name"] for step in order[:3]], ["Willow"] * 3
        )
        self.assertNotEqual(order[3]["channel_name"], "Willow")

    def test_without_a_selection_the_channel_order_is_used(self):
        channels = self._event()
        order = event_failover_order(channels)
        self.assertEqual(order[0]["channel_name"], channels[0]["name"])


class Section10ChannelsNeverCrossMerge(unittest.TestCase):
    """A channel belongs to its event."""

    def test_willow_on_two_matches_is_two_channel_groups(self):
        first = build_event_channels(
            "event-a", "A vs B", [stream("Willow", "https://a.test/1.m3u8")], aliases=ALIASES
        )[0]
        second = build_event_channels(
            "event-b", "C vs D", [stream("Willow", "https://a.test/1.m3u8")], aliases=ALIASES
        )[0]
        self.assertNotEqual(first[0]["id"], second[0]["id"])
        self.assertIn("event-a", first[0]["id"])
        self.assertIn("event-b", second[0]["id"])

    def test_the_parent_key_is_the_event(self):
        identity = channel_id_for("event-x", ChannelName("Willow", "willow", "derived", "name"))
        self.assertTrue(identity.startswith("event-x--"))

    def test_willow_and_willow_two_stay_distinct_feeds(self):
        channels = build_event_channels(
            "evt-1", "A vs B",
            [
                stream("Willow", "https://a.test/1.m3u8"),
                stream("Willow 2", "https://a.test/2.m3u8"),
                stream("Willow Extra", "https://a.test/3.m3u8"),
            ],
            aliases=ALIASES,
        )[0]
        self.assertEqual(len(channels), 3, [c["name"] for c in channels])

    def test_ten_one_and_ten_three_are_different_channels(self):
        channels = build_event_channels(
            "evt-1", "A vs B",
            [
                stream("Sony Sports Ten 1", "https://a.test/1.m3u8"),
                stream("Sony Sports Ten 3", "https://a.test/3.m3u8"),
            ],
            aliases=ALIASES,
        )[0]
        self.assertEqual(len(channels), 2, [c["name"] for c in channels])


# ------------------------------------------------------------------ sections 11-12
class Section11ChannelNameResolver(unittest.TestCase):
    """The priority order, and what the cleaner must remove."""

    def test_an_explicit_channel_name_wins(self):
        resolved = resolve_channel_name(
            {"channel_name": "Willow Cricket", "tvg_name": "Something Else",
             "name": "A vs B Server 2"}, "A vs B", ALIASES,
        )
        self.assertEqual(resolved.name, "Willow Cricket")
        self.assertEqual(resolved.confidence, "explicit")

    def test_tvg_name_comes_next(self):
        resolved = resolve_channel_name(
            {"tvg_name": "Sony Sports Ten 1 HD", "name": "A vs B"}, "A vs B", ALIASES,
        )
        self.assertEqual(resolved.name, "Sony Sports Ten 1")

    def test_a_known_alias_supplies_the_display_spelling(self):
        resolved = resolve_channel_name({"name": "tsports"}, "A vs B", ALIASES)
        self.assertEqual(resolved.name, "T Sports")
        self.assertEqual(resolved.confidence, "alias")

    def test_a_cleaned_title_is_the_last_resort(self):
        resolved = resolve_channel_name(
            {"name": "1st Test Australia vs Bangladesh Willow"},
            "1st Test Australia vs Bangladesh Willow", ALIASES,
        )
        self.assertEqual(resolved.name, "Willow")
        self.assertEqual(resolved.confidence, "derived")

    def test_the_cleaner_removes_everything_the_guide_lists(self):
        for noisy in (
            "Willow Server 2", "Willow HD", "Willow FHD", "Willow 4K",
            "Willow LIVE", "Willow Backup", "Willow 1080p",
        ):
            self.assertEqual(
                normalize_channel_name(strip_stream_noise(noisy, "A vs B")), "willow", noisy,
            )

    def test_the_match_itself_is_removed(self):
        self.assertEqual(
            strip_stream_noise("Sri Lanka vs India Willow", "Sri Lanka vs India"), "Willow",
        )

    def test_a_feed_number_survives_the_cleaner(self):
        self.assertEqual(
            resolve_channel_name(
                {"name": "Sri Lanka vs India Sony Sports Ten 3"}, "Sri Lanka vs India", ALIASES,
            ).normalized,
            "sony-sports-ten-3",
        )


class Section12NoInventedChannels(unittest.TestCase):
    """When the name is not reliable, there is no channel at all."""

    def test_a_bare_match_title_yields_no_channel(self):
        self.assertFalse(
            resolve_channel_name({"name": "Sri Lanka vs India"}, "Sri Lanka vs India", ALIASES).resolved
        )

    def test_a_server_label_is_not_a_channel(self):
        for noisy in ("Server 3", "SL vs IND Server 2 HD", "Link 4", "Backup 2"):
            self.assertFalse(
                resolve_channel_name({"name": noisy}, "SL vs IND", ALIASES).resolved, noisy,
            )

    def test_unknown_and_unnamed_are_not_channels(self):
        for noisy in ("Unknown 1", "Unnamed", "N/A", "None"):
            self.assertFalse(
                resolve_channel_name({"name": noisy}, "A vs B", ALIASES).resolved, noisy,
            )

    def test_a_category_label_is_not_a_broadcaster(self):
        for label in ("Sports", "Cricket", "Live Events", "Football"):
            self.assertFalse(
                resolve_channel_name({"group_title": label}, "A vs B", ALIASES).resolved, label,
            )

    def test_a_boolean_field_is_not_a_broadcaster(self):
        self.assertFalse(
            resolve_channel_name(
                {"today_source_channel": True, "name": "A vs B"}, "A vs B", ALIASES,
            ).resolved
        )

    def test_an_unresolved_event_publishes_no_channels_at_all(self):
        channels, stats = build_event_channels(
            "evt-1", "Sri Lanka vs India",
            [stream("Sri Lanka vs India", "https://a.test/1.m3u8"),
             stream("Sri Lanka vs India Server 2", "https://b.test/2.m3u8")],
            aliases=ALIASES,
        )
        self.assertEqual(channels, [])
        self.assertEqual(stats["unresolved_channel_streams"], 2)


# ------------------------------------------------------------------ sections 13-14
class Section13And14SelectionAndFailover(unittest.TestCase):
    """The scanner's default and the viewer's choice are separate states."""

    def _channels(self):
        return build_event_channels(
            "evt-1", "Sri Lanka vs India",
            [
                stream("Sony Sports Ten 1", "https://b.test/t1.m3u8", resolution_height=1080),
                stream("Willow", "https://a.test/w1.m3u8"),
                stream("Willow HD", "https://a.test/w2.m3u8"),
            ],
            aliases=ALIASES,
            default_variant_key=stream_variant_identity(
                stream("Sony Sports Ten 1", "https://b.test/t1.m3u8", resolution_height=1080)
            ),
        )[0]

    def test_the_default_is_the_channel_carrying_the_event_primary(self):
        channels = self._channels()
        primary_key = stream_variant_identity(
            stream("Sony Sports Ten 1", "https://b.test/t1.m3u8", resolution_height=1080)
        )
        self.assertEqual(
            default_channel_id(channels, primary_key),
            next(c["id"] for c in channels if c["name"] == "Sony Sports Ten 1"),
        )

    def test_selecting_willow_puts_willow_first(self):
        channels = self._channels()
        willow = next(c for c in channels if c["name"] == "Willow")
        order = event_failover_order(channels, willow["id"])
        self.assertEqual(order[0]["channel_id"], willow["id"])
        self.assertEqual(order[0]["role"], "primary")

    def test_willow_primary_fails_then_willow_backup(self):
        channels = self._channels()
        willow = next(c for c in channels if c["name"] == "Willow")
        order = event_failover_order(channels, willow["id"])
        self.assertEqual(order[1]["channel_id"], willow["id"])
        self.assertEqual(order[1]["role"], "backup")

    def test_all_willow_variants_fail_then_the_next_channel(self):
        channels = self._channels()
        willow = next(c for c in channels if c["name"] == "Willow")
        order = event_failover_order(channels, willow["id"])
        after = [step for step in order if step["channel_id"] != willow["id"]]
        self.assertTrue(after)
        self.assertEqual(after[0]["role"], "primary")


class Section13And14InTheBrowserBundle(unittest.TestCase):
    """The shipping bundle really implements the selection and the order."""

    @classmethod
    def setUpClass(cls):
        cls.app = (ROOT / "site" / "assets" / "js" / "app.js").read_text(encoding="utf-8")

    def test_a_selection_is_remembered_per_event(self):
        self.assertIn("channelSelection: readJsonStorage(", self.app)
        self.assertIn("function selectEventChannel(", self.app)
        self.assertIn("state.channelSelection[eventChannelId(item)]", self.app)

    def test_the_selection_outranks_the_scanner_default(self):
        body = self.app.split("function activeChannelId(")[1].split("\nfunction ")[0]
        self.assertIn("state.channelSelection[eventChannelId(item)]", body)
        self.assertIn("item.default_channel_id", body)
        self.assertLess(
            body.index("state.channelSelection"), body.index("default_channel_id"),
        )

    def test_the_route_order_follows_the_channels(self):
        self.assertIn("function orderSourcesByChannel(", self.app)
        plan = self.app.split("function buildAttemptPlan(item) {")[1][:900]
        self.assertIn("orderSourcesByChannel(item, rankedSources)", plan)

    def test_a_click_on_the_playing_channel_does_not_restart_it(self):
        body = self.app.split("async function selectEventChannel(")[1].split("\nfunction ")[0]
        self.assertIn("activeChannelId(item) === String(channelId)", body)


# ------------------------------------------------------------------ sections 26-30
class Section26And27NativeFirst(unittest.TestCase):
    """An embed is a backup, never a reason to demote a native primary."""

    def test_a_renderer_is_declared_per_stream(self):
        self.assertEqual(playback_type_of({"url": "https://a.test/x.m3u8"}), PLAYBACK_NATIVE)
        self.assertEqual(
            playback_type_of({"embed_url": "https://p.test/embed/1"}), PLAYBACK_EMBED,
        )

    def test_an_embed_never_becomes_the_channel_primary(self):
        channels = build_event_channels(
            "evt-1", "A vs B",
            [
                {"channel_name": "Willow", "playback_type": "embed",
                 "embed_url": "https://p.test/embed/1", "provider": "streamed",
                 "publish_allowed": True},
                stream("Willow", "https://a.test/w.m3u8"),
            ],
            aliases=ALIASES,
        )[0]
        primary = channels[0]["streams"][0]
        self.assertEqual(primary["playback_type"], PLAYBACK_NATIVE)
        self.assertEqual(channels[0]["streams"][-1]["playback_type"], PLAYBACK_EMBED)

    def test_an_embed_only_channel_still_ranks_below_a_native_one(self):
        channels = build_event_channels(
            "evt-1", "A vs B",
            [
                {"channel_name": "Streamed Feed TV", "playback_type": "embed",
                 "embed_url": "https://p.test/embed/1", "publish_allowed": True},
                stream("Willow", "https://a.test/w.m3u8"),
            ],
            aliases=ALIASES,
        )[0]
        self.assertEqual(channels[0]["name"], "Willow")

    def test_a_native_stream_publishes_a_playback_id_not_a_url(self):
        channels = build_event_channels(
            "evt-1", "A vs B", [stream("Willow", "https://a.test/w.m3u8")], aliases=ALIASES,
        )[0]
        published = channels[0]["streams"][0]
        self.assertTrue(published["playback_id"].startswith("ctv_"))
        self.assertNotIn("url", published)
        self.assertNotIn("headers", published)

    def test_the_published_playback_id_is_the_one_the_catalogue_will_use(self):
        from scanner.playback_profiles import PlaybackProfileCollector, stable_playback_id

        candidate = stream("Willow", "https://a.test/w.m3u8", headers={"Referer": "https://x/"})
        collector = PlaybackProfileCollector("today", "2026-08-17T12:00:00+00:00")
        collector.sanitize_item(candidate)
        self.assertIn(stable_playback_id(candidate), collector.records)


class Section28And29And30EmbedRenderer(unittest.TestCase):
    """The embed renderer lives inside the existing shell and changes no geometry."""

    @classmethod
    def setUpClass(cls):
        cls.app = (ROOT / "site" / "assets" / "js" / "app.js").read_text(encoding="utf-8")
        cls.css = (ROOT / "site" / "assets" / "css" / "embed-player.css").read_text(encoding="utf-8")

    def test_the_iframe_is_mounted_inside_the_player_container(self):
        body = self.app.split("function mountEmbedRenderer(")[1].split("\nfunction ")[0]
        self.assertIn("videoContainer.appendChild(frame)", body)

    def test_the_player_geometry_is_never_restyled(self):
        for selector in (
            "#videoContainer", "#videoPlayer", ".video-section", ".youtube-layout",
            ".app-header", ".final-main-nav", ".desktop-category-rail",
        ):
            self.assertNotIn(selector, self.css, f"embed CSS must not restyle {selector}")
        for property_name in ("aspect-ratio", "max-width", "min-height"):
            self.assertNotIn(property_name, self.css, property_name)

    def test_the_iframe_fills_whatever_box_the_shell_already_is(self):
        self.assertIn(".embed-renderer", self.css)
        self.assertIn("position: absolute", self.css)
        self.assertIn("inset: 0", self.css)

    def test_going_back_to_native_destroys_the_iframe_and_its_session(self):
        body = self.app.split("function unmountEmbedRenderer(")[1].split("\nfunction ")[0]
        self.assertIn("about:blank", body)
        self.assertIn("existing.remove()", body)
        cleanup = self.app.split("async function cleanupPlayerEngine(")[1][:400]
        self.assertIn("unmountEmbedRenderer()", cleanup)

    def test_native_only_controls_are_disabled_not_removed(self):
        body = self.app.split("function applyEmbedControlMode(")[1].split("\nfunction ")[0]
        self.assertIn("qualityBtn", body)
        self.assertIn("networkBtn", body)
        self.assertIn("data-embed-disabled", body)
        # Restored on the way back.
        self.assertIn("removeAttribute('data-embed-disabled')", body)

    def test_a_disabled_control_keeps_its_space(self):
        self.assertIn("[data-embed-disabled]", self.css)
        self.assertNotIn("display: none", self.css.split("[data-embed-disabled]")[1][:120])

    def test_an_iframe_load_is_not_treated_as_proof_of_playback(self):
        body = self.app.split("function mountEmbedRenderer(")[1].split("\nfunction ")[0]
        self.assertIn("state.embedSession.loaded = true", body)
        self.assertNotIn("session.success = true", body)

    def test_an_embed_is_only_tried_after_the_native_plan_is_exhausted(self):
        body = self.app.split("function handlePlaybackPlanExhausted(")[1][:600]
        self.assertIn("tryEmbedFallback(session.item, reason)", body)


# ------------------------------------------------------------------ section 21
class Section21EndDetection(unittest.TestCase):
    """Time is never proof. Only a finished status or multi-signal confirmation."""

    def _card(self, **extra):
        card = {
            "id": "evt-1", "name": "Arsenal vs Chelsea", "sport_type": "football",
            "start_time": (NOW - timedelta(hours=7)).isoformat(),
            "end_time": (NOW - timedelta(hours=5)).isoformat(),
        }
        card.update(extra)
        return card

    def test_scheduled_end_passed_but_stream_playable_is_preserved(self):
        verdict = decide(self._card(), LifecycleSignals(
            estimate_passed=True, primary_playable=True, consecutive_non_live_scans=9,
        ), now=NOW)
        self.assertEqual(verdict.state, LIVE)
        self.assertTrue(verdict.publish)

    def test_scheduled_end_passed_but_authority_still_live_is_preserved(self):
        verdict = decide(self._card(), LifecycleSignals(
            authority_live=True, estimate_passed=True, primary_playable=False,
            backup_playable=False, consecutive_non_live_scans=9,
        ), now=NOW)
        self.assertEqual(verdict.state, LIVE)

    def test_a_playable_backup_alone_preserves_it(self):
        verdict = decide(self._card(), LifecycleSignals(
            estimate_passed=True, primary_playable=False, backup_playable=True,
            consecutive_non_live_scans=9,
        ), now=NOW)
        self.assertEqual(verdict.state, LIVE)

    def test_an_authoritative_finish_ends_it(self):
        for status in ("FT", "FINISHED", "ENDED", "FINAL", "AET", "PEN"):
            self.assertTrue(has_strong_end_signal({"schedule_status": status}), status)
            verdict = decide(self._card(schedule_status=status),
                             LifecycleSignals(strong_end=True), now=NOW)
            self.assertEqual(verdict.state, ENDED, status)

    def test_authority_unavailable_and_stream_alive_is_preserved(self):
        verdict = decide(self._card(), LifecycleSignals(
            authority_live=None, primary_playable=True, estimate_passed=True,
        ), now=NOW)
        self.assertEqual(verdict.state, LIVE)

    def test_authority_unavailable_and_dead_and_repeated_reaches_ended(self):
        verdict = decide(self._card(), LifecycleSignals(
            authority_live=None, primary_playable=False, backup_playable=False,
            estimate_passed=True, consecutive_non_live_scans=3,
        ), now=NOW)
        self.assertEqual(verdict.state, ENDED)

    def test_one_confirming_scan_is_only_end_pending(self):
        verdict = decide(self._card(), LifecycleSignals(
            authority_live=None, primary_playable=False, backup_playable=False,
            estimate_passed=True, consecutive_non_live_scans=1,
        ), now=NOW)
        self.assertEqual(verdict.state, END_PENDING)
        self.assertTrue(verdict.publish, "END_PENDING still publishes the card")

    def test_an_inconclusive_probe_never_reaches_ended(self):
        verdict = decide(self._card(), LifecycleSignals(
            authority_live=None, primary_playable=None, backup_playable=None,
            estimate_passed=True, consecutive_non_live_scans=99,
        ), now=NOW)
        self.assertEqual(verdict.state, END_PENDING)

    def test_a_currently_playing_event_is_never_removed(self):
        verdict = decide(self._card(), LifecycleSignals(
            authority_live=None, primary_playable=False, backup_playable=False,
            estimate_passed=True, consecutive_non_live_scans=50, currently_playing=True,
        ), now=NOW)
        self.assertEqual(verdict.state, LIVE)
        self.assertIn("currently_playing", verdict.protections)

    def test_football_extra_time_survives_its_estimate(self):
        verdict = decide(
            self._card(sport_type="football", end_time=(NOW - timedelta(minutes=20)).isoformat()),
            LifecycleSignals(authority_live=None, primary_playable=False,
                             backup_playable=False, consecutive_non_live_scans=5),
            now=NOW,
        )
        self.assertEqual(verdict.state, END_PENDING)

    def test_a_long_tennis_match_survives(self):
        verdict = decide(
            self._card(sport_type="tennis", end_time="",
                       start_time=(NOW - timedelta(hours=4)).isoformat()),
            LifecycleSignals(authority_live=None, primary_playable=False,
                             backup_playable=False, consecutive_non_live_scans=5),
            now=NOW,
        )
        self.assertEqual(verdict.state, END_PENDING)

    def test_a_delayed_multi_day_cricket_match_survives(self):
        verdict = decide(
            self._card(sport_type="cricket", end_time="",
                       start_time=(NOW - timedelta(hours=6)).isoformat()),
            LifecycleSignals(authority_live=None, primary_playable=False,
                             backup_playable=False, consecutive_non_live_scans=8),
            now=NOW,
        )
        self.assertEqual(verdict.state, END_PENDING)

    def test_a_stale_live_status_on_a_carried_card_is_not_an_authority(self):
        """The card still holds the LIVE_NOW its last good scan wrote. Reading
        that back as a current statement would make END_PENDING unreachable."""
        card = self._card(schedule_status="LIVE_NOW")
        with tempfile.TemporaryDirectory() as tmp:
            items, _ = protect_live_events(
                [], [card], state_path=Path(tmp) / "p.json", now=NOW,
                probe=lambda entry: False,
            )
        self.assertEqual(items[0]["lifecycle_state"], END_PENDING)

    def test_a_fresh_authority_verdict_is_honoured(self):
        card = self._card(schedule_status="")
        with tempfile.TemporaryDirectory() as tmp:
            items, _ = protect_live_events(
                [], [card], state_path=Path(tmp) / "p.json", now=NOW,
                probe=lambda entry: False, authority_states={"evt-1": True},
            )
        self.assertEqual(items[0]["lifecycle_state"], LIVE)


# ------------------------------------------------------------------ sections 22-35
class Section22To25StreamedIsAdditive(unittest.TestCase):
    """Streamed enriches; it never replaces and never deletes."""

    SETTINGS = {"streamed_provider": {"enabled": True, "base_url": "https://p.example"}}

    def _matches(self):
        live = int((NOW - timedelta(minutes=30)).timestamp() * 1000)
        soon = int((NOW + timedelta(minutes=10)).timestamp() * 1000)
        far = int((NOW + timedelta(hours=9)).timestamp() * 1000)
        return [
            {"id": "m1", "title": "Sri Lanka vs India", "category": "cricket", "date": live,
             "teams": {"home": {"name": "Sri Lanka", "badge": "sl"},
                       "away": {"name": "India", "badge": "in"}},
             "sources": [{"source": "alpha", "id": "a1"}]},
            {"id": "m2", "title": "Arsenal vs Chelsea", "category": "football", "date": soon,
             "sources": [{"source": "bravo", "id": "b1"}], "poster": "ars"},
            {"id": "m3", "title": "Real vs Barca", "category": "football", "date": far,
             "sources": [{"source": "charlie", "id": "c1"}]},
        ]

    def _opener(self, streams=None):
        payload = streams if streams is not None else [
            {"source": "alpha", "id": "a1", "streamNo": 1, "hd": True,
             "embedUrl": "https://p.example/embed/alpha/a1/1"}
        ]

        def opener(url):
            if "/api/stream/" in url:
                return json.dumps(payload)
            return json.dumps(self._matches())

        return opener

    def _collect(self, opener, window=15):
        with tempfile.TemporaryDirectory() as tmp:
            return collect_streamed_candidates(
                self.SETTINGS, targeted_window_minutes=window, now=NOW,
                cache=ProviderCache(Path(tmp) / "c.json"), opener=opener,
            )

    def test_the_provider_id_never_becomes_the_event_id(self):
        candidates, _ = self._collect(self._opener())
        for candidate in candidates:
            self.assertNotIn("id", candidate)
            self.assertTrue(candidate["provider_event_id"])

    def test_an_unavailable_provider_contributes_nothing_and_says_so(self):
        def boom(url):
            raise TimeoutError("slow")

        candidates, health = self._collect(boom)
        self.assertEqual(candidates, [])
        self.assertFalse(health.available)
        self.assertIn("TimeoutError", health.reason)

    def test_a_malformed_response_is_not_a_crash(self):
        candidates, health = self._collect(lambda url: "not json at all")
        self.assertEqual(candidates, [])
        self.assertFalse(health.available)

    def test_metadata_and_artwork_are_ingested(self):
        candidates, health = self._collect(self._opener())
        cricket = next(c for c in candidates if c["name"] == "Sri Lanka vs India")
        self.assertEqual(cricket["sport_type"], "cricket")
        self.assertTrue(cricket["start_time"])
        self.assertEqual(len(cricket["provider_artwork"]), 2)
        self.assertTrue(health.artwork)

    def test_a_provider_candidate_carries_no_native_stream(self):
        candidates, _ = self._collect(self._opener())
        for candidate in candidates:
            self.assertTrue(candidate["metadata_only"])
            self.assertNotIn("url", candidate)

    def test_the_listing_is_a_routing_hint_not_a_status(self):
        candidates, _ = self._collect(self._opener())
        for candidate in candidates:
            self.assertIn(candidate["provider_routing_hint"], {"live", "scheduled"})
            self.assertNotIn("schedule_status", candidate)

    def test_an_embed_stream_declares_the_embed_renderer(self):
        streams = normalize_embed_streams(
            [{"source": "alpha", "id": "a1", "streamNo": 2, "hd": False,
              "embedUrl": "https://p.example/embed/x"}],
            StreamedSettings.from_settings(self.SETTINGS),
        )
        self.assertEqual(streams[0]["playback_type"], PLAYBACK_EMBED)
        self.assertFalse(streams[0]["verified"], "an embed URL is not proof of playback")

    def test_a_match_with_no_title_is_skipped(self):
        self.assertIsNone(
            normalize_match({"id": "x", "sources": []},
                            StreamedSettings.from_settings(self.SETTINGS))
        )


class Section31StreamedUpcomingStrategy(unittest.TestCase):
    """Endpoints are not resolved for every future fixture."""

    def test_a_fixture_far_in_the_future_costs_no_lookup(self):
        self.assertFalse(should_resolve_streams(
            {"start_time": (NOW + timedelta(hours=9)).isoformat()},
            now=NOW, targeted_window_minutes=15,
        ))

    def test_a_fixture_inside_the_targeted_window_does(self):
        self.assertTrue(should_resolve_streams(
            {"start_time": (NOW + timedelta(minutes=10)).isoformat()},
            now=NOW, targeted_window_minutes=15,
        ))

    def test_a_live_fixture_does(self):
        self.assertTrue(should_resolve_streams(
            {"provider_routing_hint": "live"}, now=NOW, targeted_window_minutes=0,
        ))

    def test_an_explicit_on_demand_request_does(self):
        self.assertTrue(should_resolve_streams(
            {"start_time": (NOW + timedelta(days=3)).isoformat()},
            now=NOW, targeted_window_minutes=0, on_demand=True,
        ))


class Section33And34SnapshotAndNaming(unittest.TestCase):
    """Publishing rules that the provider must not be able to break."""

    def test_a_provider_name_is_not_shown_as_a_broadcaster(self):
        self.assertFalse(
            resolve_channel_name(
                {"provider": "streamed", "provider_source": "alpha", "name": "A vs B"},
                "A vs B", ALIASES,
            ).resolved
        )

    def test_an_embed_entry_without_a_url_is_dropped(self):
        streams = normalize_embed_streams(
            [{"source": "alpha", "id": "a1"}, {"embedUrl": "not-a-url"}],
            StreamedSettings.from_settings({"streamed_provider": {"enabled": True, "base_url": "https://p"}}),
        )
        self.assertEqual(streams, [])

    def test_the_snapshot_validator_still_checks_native_ids(self):
        source = (ROOT / "scanner" / "snapshot_publish.py").read_text(encoding="utf-8")
        self.assertIn("_validate_playback_references", source)
        self.assertIn("playback profile", source)

    def test_the_provider_is_disabled_by_default(self):
        settings = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
        self.assertIn("streamed_provider", settings)
        self.assertFalse(settings["streamed_provider"]["enabled"])


# ------------------------------------------------------------------ hard locks
class HardLocks(unittest.TestCase):
    """Nothing in this round may move the player or remove a feature."""

    def test_no_new_stylesheet_touches_the_player_or_the_frame(self):
        forbidden = (
            ".video-container", "#videoPlayer", "#videoContainer", ".video-section",
            ".youtube-layout", ".app-header", ".desktop-category-rail",
            ".final-main-nav", ".now-playing-bar",
        )
        for name in ("event-cards.css", "smart-filter.css", "embed-player.css"):
            sheet = (ROOT / "site" / "assets" / "css" / name).read_text(encoding="utf-8")
            for selector in forbidden:
                self.assertNotIn(selector, sheet, f"{name} must not restyle {selector}")

    def test_every_player_control_is_still_in_the_page(self):
        index = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
        for element in (
            'id="videoPlayer"', 'id="playerControls"', 'id="qualityBtn"',
            'id="networkBtn"', 'id="fullscreenBtn"', 'id="muteBtn"',
        ):
            self.assertIn(element, index)

    def test_the_earlier_rounds_behaviour_is_still_wired(self):
        app = (ROOT / "site" / "assets" / "js" / "app.js").read_text(encoding="utf-8")
        for feature in (
            "pinPlaybackSession", "preservePlayingSession", "reconcileEventCards",
            "resolveEventSnapshotPath", "setEventSportFilter",
        ):
            self.assertIn(feature, app, feature)

    def test_the_scanner_still_publishes_the_legacy_event_fields(self):
        channels = build_event_channels(
            "evt-1", "A vs B", [stream("Willow", "https://a.test/w.m3u8")], aliases=ALIASES,
        )[0]
        cards = merge_candidates([stream("A vs B Willow", "https://a.test/w.m3u8")])
        self.assertTrue(channels)
        card = cards[0]
        for field in ("url", "backups", "available_link_count", "verification_status",
                      "primary_stream_key", "sport_type"):
            self.assertIn(field, card, field)


# --------------------------------------------- the channels[] root-cause fixes
class SuppressedStreamsStayAvailableForAttachment(unittest.TestCase):
    """The enrichment gate must refuse to publish without also destroying.

    A stream-only playlist entry cannot become a card of its own - that is
    fixture authority and it stays. But deleting the candidate outright removed
    it before attach_streams_to_fixtures, the stage whose whole purpose is to
    hang a stream on a fixture, so a card and its broadcasters were both present
    in one scan and never introduced.
    """

    def _playlist_stream(self, name, **extra):
        candidate = stream(
            name, "https://cdn.test/live.m3u8",
            source_id="playlist-source", source_pipeline="upcoming",
            start_time=(NOW + timedelta(minutes=30)).isoformat(), **extra,
        )
        # Production resolves the broadcaster on the raw title before enrichment
        # rewrites it, so the candidate reaching the pool already carries one.
        _stamp_channel_names([candidate])
        return candidate

    def _authority_fixture(self, name):
        return {
            "name": name,
            "url": "",
            "source_id": "srhady-axsports-upcoming",
            "source_pipeline": "upcoming",
            "metadata_only": True,
            "allow_without_stream": True,
            "start_time": (NOW + timedelta(minutes=30)).isoformat(),
            "status": "UPCOMING",
        }

    def test_a_refused_stream_is_offered_rather_than_deleted(self):
        pool = []
        output, stats = enrich_event_candidates(
            [self._playlist_stream("Rays vs Orioles TNT SPORTS")],
            fixture_path=EMPTY_FIXTURES, now=NOW, attachment_pool=pool,
        )
        self.assertEqual(stats["unverified_suppressed"], 1)
        self.assertEqual(stats.get("pooled_for_attachment"), 1)
        # The gate itself is unchanged: it is still not published.
        self.assertEqual([item.get("name") for item in output], [])
        self.assertEqual(len(pool), 1)

    def test_passing_no_pool_keeps_the_previous_behaviour(self):
        output, stats = enrich_event_candidates(
            [self._playlist_stream("Rays vs Orioles TNT SPORTS")],
            fixture_path=EMPTY_FIXTURES, now=NOW,
        )
        self.assertEqual(output, [])
        self.assertNotIn("pooled_for_attachment", stats)

    def test_a_pooled_stream_joins_only_through_a_fixture(self):
        pool = [self._playlist_stream("Rays vs Orioles TNT SPORTS")]
        items = [self._authority_fixture("Baltimore Orioles vs Tampa Bay Rays")]
        output, stats = attach_streams_to_fixtures(
            items, {"srhady-axsports-upcoming"}, attachment_pool=pool,
        )
        self.assertEqual(stats["pool_attached"], 1)
        promoted = [i for i in output if i.get("attached_from_suppressed_pool")]
        self.assertEqual(len(promoted), 1)
        # It arrives wearing the fixture's identity, never its own.
        self.assertEqual(promoted[0]["name"], "Baltimore Orioles vs Tampa Bay Rays")
        self.assertEqual(promoted[0]["channel_name"], "TNT SPORTS")

    def test_a_pooled_stream_no_fixture_claims_stays_suppressed(self):
        pool = [self._playlist_stream("Someone vs Nobody TNT SPORTS")]
        items = [self._authority_fixture("Baltimore Orioles vs Tampa Bay Rays")]
        output, stats = attach_streams_to_fixtures(
            items, {"srhady-axsports-upcoming"}, attachment_pool=pool,
        )
        self.assertEqual(stats["pool_attached"], 0)
        self.assertEqual(stats["pool_unclaimed"], 1)
        self.assertEqual(len(output), 1)

    def test_a_dead_pooled_link_is_never_promoted(self):
        pool = [self._playlist_stream(
            "Rays vs Orioles TNT SPORTS",
            verification_status="failed", verified=False, publish_allowed=False,
        )]
        _, stats = attach_streams_to_fixtures(
            [self._authority_fixture("Baltimore Orioles vs Tampa Bay Rays")],
            {"srhady-axsports-upcoming"}, attachment_pool=pool,
        )
        self.assertEqual(stats["pool_attached"], 0)

    def test_only_a_usable_link_is_ever_pooled(self):
        pool = []
        enrich_event_candidates(
            [self._playlist_stream(
                "Rays vs Orioles TNT SPORTS", verification_status="failed",
                verified=False, publish_allowed=False,
            )],
            fixture_path=EMPTY_FIXTURES, now=NOW, attachment_pool=pool,
        )
        self.assertEqual(pool, [])


class TheTwoFeedsNameTheSameTeamsDifferently(unittest.TestCase):
    """A fixture feed and a playlist rarely spell a club the same way."""

    def test_a_round_in_front_of_the_participants_is_not_a_team(self):
        self.assertEqual(
            team_pair_key("1st Test Australia vs Bangladesh Willow"),
            "australia|bangladesh",
        )
        self.assertEqual(
            team_pair_key("Australia vs Bangladesh 2nd Test"), "australia|bangladesh"
        )
        self.assertEqual(
            team_pair_key("5th ODI Afghanistan vs Ireland"), "afghanistan|ireland"
        )

    def test_a_link_label_is_not_a_team(self):
        self.assertEqual(team_pair_key("Braves vs Diamondbacks Quality"),
                         "braves|diamondbacks")
        self.assertEqual(team_pair_key("R Racing Club vs Villarreal Link 1"),
                         "r racing club|villarreal")

    def test_a_year_in_a_club_name_survives(self):
        self.assertEqual(team_pair_key("Felgueiras 1932 vs AVS"), "felgueiras 1932|avs")

    def test_the_same_two_clubs_in_either_order_at_either_length(self):
        fixture = _key_sides("baltimore orioles|tampa bay rays")
        for spelling in ("rays|orioles", "orioles|rays",
                         "tampa bay rays|baltimore orioles"):
            self.assertTrue(_pairs_match(_key_sides(spelling), fixture), spelling)

    def test_a_shared_word_alone_is_not_a_match(self):
        fixture = _key_sides("boston red sox|chicago white sox")
        self.assertFalse(_pairs_match(_key_sides("sox|sox"), fixture))
        self.assertFalse(
            _pairs_match(_key_sides("red sox|new york yankees"), fixture)
        )

    def test_a_different_opponent_is_a_different_game(self):
        fixture = _key_sides("atlanta braves|minnesota twins")
        self.assertFalse(_pairs_match(_key_sides("braves|diamondbacks"), fixture))

    def test_two_fixtures_answering_to_one_stream_attach_to_neither(self):
        items = [
            {"name": "Boston Red Sox vs New York Mets", "url": "",
             "source_id": "auth", "metadata_only": True},
            {"name": "Chicago White Sox vs New York Mets", "url": "",
             "source_id": "auth", "metadata_only": True},
        ]
        pool = [stream("Sox vs Mets FANCODE", "https://cdn.test/a.m3u8",
                       source_id="playlist")]
        _, stats = attach_streams_to_fixtures(items, {"auth"}, attachment_pool=pool)
        self.assertEqual(stats["pool_attached"], 0)


class OneMatchIsOneCardHoweverItIsSpelled(unittest.TestCase):
    """Sections 1/3/5 - three spellings published three cards."""

    def test_reordered_participants_and_a_round_word_fold_together(self):
        keys = {
            participant_fold_key({"name": name}, ALIASES)
            for name in ("Sri Lanka vs India 1st Test", "Sri Lanka vs India",
                         "India vs Sri Lanka Willow")
        }
        self.assertEqual(len(keys), 1, keys)

    def test_a_tournament_placeholder_never_folds(self):
        self.assertEqual(participant_fold_key({"name": "Cpl T20 Vs Cpl T20"}, ALIASES), "")

    def test_a_womens_fixture_never_folds_into_the_mens(self):
        self.assertNotEqual(
            participant_fold_key({"name": "Trent Rockets Women vs Oval Women"}, ALIASES),
            participant_fold_key({"name": "Trent Rockets vs Oval"}, ALIASES),
        )

    def test_the_same_teams_on_two_dates_are_still_two_cards(self):
        cards = merge_candidates([
            stream("England vs Pakistan", "https://a.test/1.m3u8",
                   start_time="2026-08-17T12:00:00+00:00"),
            stream("Pakistan vs England", "https://a.test/2.m3u8",
                   start_time="2026-08-24T12:00:00+00:00"),
        ])
        self.assertEqual(len(cards), 2)


class ATeamNameIsNotABroadcaster(unittest.TestCase):
    """Section 12, in the place it actually failed."""

    def test_a_brand_word_inside_a_club_name_is_not_the_channel(self):
        for name in ("Antigua and Barbuda Falcons vs Guyana Amazon Warriors",
                     "Saint Lucia Kings vs Guyana Amazon Warriors - CPL 12th Match"):
            self.assertFalse(
                resolve_channel_name({"name": name}, name, ALIASES).resolved, name
            )

    def test_the_fixture_name_survives_intact(self):
        item = {"name": "Antigua and Barbuda Falcons vs Guyana Amazon Warriors"}
        self.assertEqual(fixture_display_name(item, ALIASES), item["name"])
        self.assertIn("amazon", fixture_identity_key(item, ALIASES))

    def test_a_brand_at_the_end_is_still_the_channel(self):
        for name, expected in (
            ("1st Test Australia vs Bangladesh Willow", "Willow"),
            ("Al Nassr Vs Al Fateh FOX DEPORTES", "FOX"),
            ("Sevilla Vs Rayo Vallecano beiN ENGLISH", "beiN ENGLISH"),
            ("India vs Sri Lanka Amazon", "Amazon"),
        ):
            self.assertEqual(
                resolve_channel_name({"name": name}, name, ALIASES).name, expected, name
            )

    def test_a_channel_only_title_keeps_all_of_its_words(self):
        for name in ("Sony Sports Ten 4", "Star Sports 2"):
            self.assertEqual(
                resolve_channel_name({"name": name}, "India vs Sri Lanka", ALIASES).name,
                name, name,
            )


class TheEventIdIsTheFixtureNotTheBroadcaster(unittest.TestCase):
    """Section 10 - channel ids are namespaced by the event id."""

    def test_the_broadcaster_comes_out_of_the_id(self):
        channels = [{"normalized_name": "sportv"}, {"normalized_name": "fancode"}]
        self.assertEqual(
            event_id_without_broadcaster("al-nassr-vs-al-fateh-sportv-br", channels),
            "al-nassr-vs-al-fateh",
        )

    def test_an_id_with_no_broadcaster_is_left_alone(self):
        self.assertEqual(
            event_id_without_broadcaster("sri-lanka-vs-india-1st-test",
                                         [{"normalized_name": "willow"}]),
            "",
        )

    def test_nothing_is_cut_that_would_leave_no_fixture(self):
        self.assertEqual(
            event_id_without_broadcaster("willow-cricket",
                                         [{"normalized_name": "willow"}]),
            "",
        )

    def test_the_published_card_and_its_channels_agree(self):
        cards = merge_candidates([
            stream("Al Nassr Vs Al Fateh SporTV BR", "https://a.test/1.mpd",
                   id="al-nassr-vs-al-fateh-sportv-br"),
            stream("Al Nassr Vs Al Fateh FANCODE", "https://a.test/2.mpd",
                   id="al-nassr-vs-al-fateh-fancode"),
        ])
        self.assertEqual(len(cards), 1)
        card = cards[0]
        self.assertEqual(card["id"], "al-nassr-vs-al-fateh")
        self.assertEqual(card["channel_count"], 2)
        for channel in card["channels"]:
            self.assertTrue(str(channel["id"]).startswith("al-nassr-vs-al-fateh--"),
                            channel["id"])


class AProviderChannelNeverDemotesANativeOne(unittest.TestCase):
    """Section 27, including the case that has no native channel to compare."""

    def _card(self, **extra):
        card = {
            "id": "evt-1",
            "name": "A vs B",
            "embed_backups": [
                {"name": "beIN SPORTS 1", "provider": "streamed",
                 "playback_type": "embed",
                 "embed_url": "https://embed.test/1"},
                {"name": "Sky Sports 1", "provider": "streamed",
                 "playback_type": "embed",
                 "embed_url": "https://embed.test/2"},
            ],
        }
        card.update(extra)
        return card

    def test_embed_channels_are_appended_behind_the_native_ones(self):
        native = build_event_channels(
            "evt-1", "A vs B", [stream("Willow", "https://a.test/w.m3u8")],
            aliases=ALIASES,
        )[0]
        card = self._card(channels=list(native),
                          default_channel_id=native[0]["id"],
                          playback_id="ctv_native")
        added = _append_embed_channels(card)
        self.assertEqual(added, 2)
        renderers = [channel["renderer"] for channel in card["channels"]]
        self.assertEqual(renderers[0], PLAYBACK_NATIVE)
        self.assertEqual(renderers[1:], [PLAYBACK_EMBED, PLAYBACK_EMBED])
        self.assertEqual(card["default_channel_id"], native[0]["id"])

    def test_a_working_native_stream_is_not_defaulted_away_from(self):
        """Section 12 often refuses to name a broadcaster for a healthy stream.

        No native channel entry then exists, but the native stream is still there
        and still has to lead - so the default is left unset rather than handed
        to an embed, which would reorder the playback plan.
        """
        card = self._card(playback_id="ctv_native", url="https://a.test/native.m3u8")
        self.assertEqual(_append_embed_channels(card), 2)
        self.assertEqual(card["channel_count"], 2)
        self.assertFalse(str(card.get("default_channel_id") or ""))

    def test_with_no_native_stream_at_all_an_embed_may_lead(self):
        card = self._card(metadata_only=True)
        self.assertEqual(_append_embed_channels(card), 2)
        self.assertTrue(str(card.get("default_channel_id") or ""))

    def test_a_card_the_provider_did_not_match_is_untouched(self):
        card = {"id": "evt-2", "name": "C vs D"}
        self.assertEqual(_append_embed_channels(card), 0)
        self.assertNotIn("channels", card)

    def test_every_channel_states_its_renderer(self):
        channels = build_event_channels(
            "evt-1", "A vs B",
            [stream("Willow", "https://a.test/w.m3u8"),
             stream("beIN SPORTS", "", embed_url="https://embed.test/3",
                    playback_type="embed")],
            aliases=ALIASES,
        )[0]
        for channel in channels:
            self.assertIn(channel["renderer"],
                          (PLAYBACK_NATIVE, PLAYBACK_EMBED, "mixed"))


# ------------------------------------- a carried card is not a second card
class ACarriedCardIsReconciledNotDuplicated(unittest.TestCase):
    """Sections 1/3 across the protection boundary.

    Live protection carries a missed event forward after the merge has already
    finished, so a carried card has never been through the grouping rules. In
    production that published one Test match twice - "Sri Lanka vs India 1st
    Test" from this scan beside "India vs Sri Lanka Willow" carried from the last
    one, each holding streams the other did not.

    Reconciling has to hold four things at once: the event is never lost, a
    session in progress is never touched, no proven-playable stream disappears,
    and the canonical primary keeps its place.
    """

    KICKOFF = (NOW - timedelta(hours=2)).isoformat()

    def _canonical(self, **extra):
        card = {
            "id": "sri-lanka-vs-india-1st-test",
            "name": "Sri Lanka vs India 1st Test",
            "sport_type": "cricket",
            "start_time": self.KICKOFF,
            "status": "LIVE_NOW",
            "schedule_status": "LIVE_NOW",
            "lifecycle_state": LIVE,
            "playback_id": "ctv_canonical_primary",
            "primary_stream_key": "canonical-key",
            "url": "https://origin.test/canonical.mpd",
            "stream_type": "dash",
            "verified": True,
            "verification_status": "verified_global",
            "backups": [
                {"name": "Backup-1", "playback_id": "ctv_canonical_b1",
                 "host": "a.test", "stream_type": "dash"},
                {"name": "Backup-2", "playback_id": "ctv_canonical_b2",
                 "host": "b.test", "stream_type": "dash"},
            ],
            "available_link_count": 3,
        }
        card.update(extra)
        return card

    def _carried(self, **extra):
        card = {
            "id": "india-vs-sri-lanka-willow",
            "name": "India vs Sri Lanka Willow",
            "sport_type": "cricket",
            "start_time": self.KICKOFF,
            "status": "LIVE_NOW",
            "schedule_status": "LIVE_NOW",
            "lifecycle_state": LIVE,
            "playback_id": "ctv_willow_primary",
            "url": "https://origin.test/willow.m3u8",
            "stream_type": "hls",
            "verified": True,
            "verification_status": "verified_global",
            "source_id": "srhady-cricket-live-matches",
            "backups": [
                {"name": "Backup-1", "playback_id": "ctv_willow_b1",
                 "host": "mz01.test", "stream_type": "hls"},
                {"name": "Backup-2", "playback_id": "ctv_willow_b2",
                 "host": "mz02.test", "stream_type": "hls"},
            ],
            "available_link_count": 3,
        }
        card.update(extra)
        return card

    def _run(self, today, previous, *, playing=(), probe=lambda card: True,
             authority=None):
        with tempfile.TemporaryDirectory() as folder:
            return protect_live_events(
                today, previous,
                state_path=Path(folder) / "protection.json",
                now=NOW, probe=probe,
                playing_event_ids=set(playing),
                authority_states=authority if authority is not None else {},
            )

    def test_the_grouping_rule_sees_them_as_one_fixture(self):
        self.assertTrue(same_real_fixture(self._canonical(), self._carried(), ALIASES))

    def test_two_different_matches_are_not_reconciled(self):
        other = self._carried(id="palermo-vs-lecce", name="Palermo vs Lecce")
        self.assertFalse(same_real_fixture(self._canonical(), other, ALIASES))
        published, stats = self._run([self._canonical()], [other])
        self.assertEqual(len(published), 2)
        self.assertEqual(stats["carried_forward"], 1)
        self.assertEqual(stats["reconciled_into_canonical"], 0)

    def test_one_real_match_becomes_one_card(self):
        published, stats = self._run([self._canonical()], [self._carried()])
        self.assertEqual(len(published), 1, [c.get("name") for c in published])
        self.assertEqual(published[0]["id"], "sri-lanka-vs-india-1st-test")
        self.assertEqual(stats["reconciled_into_canonical"], 1)
        self.assertEqual(stats["carried_forward"], 0)

    def test_the_event_is_never_released_by_reconciling(self):
        _, stats = self._run([self._canonical()], [self._carried()])
        for key in ("released_ended", "released_dead_link", "released_stale",
                    "released_confirmed"):
            self.assertEqual(stats[key], 0, key)

    def test_the_proven_playable_carried_stream_leads_the_backups(self):
        published, _ = self._run([self._canonical()], [self._carried()])
        backups = published[0]["backups"]
        self.assertEqual(backups[0]["playback_id"], "ctv_willow_primary")

    def test_every_carried_stream_stays_reachable(self):
        published, _ = self._run([self._canonical()], [self._carried()])
        card = published[0]
        reachable = {str(card.get("playback_id"))}
        reachable.update(str(b.get("playback_id")) for b in card["backups"])
        reachable.update(
            str(stream.get("playback_id"))
            for channel in card.get("channels") or []
            for stream in channel.get("streams") or []
        )
        for playback_id in ("ctv_willow_primary", "ctv_willow_b1", "ctv_willow_b2"):
            self.assertIn(playback_id, reachable, playback_id)

    def test_the_canonical_cards_own_streams_survive_too(self):
        published, _ = self._run([self._canonical()], [self._carried()])
        card = published[0]
        ids = {str(b.get("playback_id")) for b in card["backups"]}
        self.assertEqual(card["playback_id"], "ctv_canonical_primary")
        self.assertTrue({"ctv_canonical_b1", "ctv_canonical_b2"} <= ids, ids)

    def test_the_canonical_primary_is_never_displaced(self):
        published, _ = self._run([self._canonical()], [self._carried()])
        card = published[0]
        self.assertEqual(card["playback_id"], "ctv_canonical_primary")
        self.assertEqual(card["url"], "https://origin.test/canonical.mpd")
        self.assertEqual(card["primary_stream_key"], "canonical-key")

    def test_the_published_backup_contract_still_holds(self):
        """The Pages validator errors above five backups."""
        crowded = self._carried(backups=[
            {"name": f"Backup-{i}", "playback_id": f"ctv_willow_b{i}",
             "host": f"mz{i}.test", "stream_type": "hls"} for i in range(1, 6)
        ])
        published, _ = self._run([self._canonical()], [crowded])
        self.assertLessEqual(len(published[0]["backups"]), 5)

    def test_no_playback_id_is_published_twice(self):
        shared = self._carried(playback_id="ctv_canonical_b1")
        published, _ = self._run([self._canonical()], [shared])
        card = published[0]
        ids = [str(card.get("playback_id"))] + [
            str(b.get("playback_id")) for b in card["backups"]]
        self.assertEqual(len(ids), len(set(ids)), ids)

    def test_an_absorbed_stream_is_a_backup_not_a_copy_of_the_card(self):
        """Copying the card into a backup slot dragged its name, id, lifecycle
        fields and its own nested backups list along with it."""
        published, _ = self._run([self._canonical()], [self._carried()])
        absorbed = published[0]["backups"][0]
        for leaked in ("backups", "channels", "id", "lifecycle_state",
                       "carried_forward_reason", "available_link_count",
                       "primary_stream_key", "absorbed_event_ids"):
            self.assertNotIn(leaked, absorbed, leaked)
        self.assertEqual(absorbed["playback_id"], "ctv_willow_primary")
        self.assertEqual(absorbed["stream_type"], "hls")

    def test_no_absorbed_stream_publishes_a_url_or_headers(self):
        """Section 17: a published stream is reached through its playback_id."""
        published, _ = self._run([self._canonical()], [self._carried()])
        card = published[0]
        for entry in card["backups"]:
            for secret in ("url", "headers", "final_url", "drm_key", "cookie"):
                self.assertNotIn(secret, entry, f"{secret} in {entry.get('name')}")
        for channel in card.get("channels") or []:
            for entry in channel.get("streams") or []:
                for secret in ("url", "headers", "final_url"):
                    self.assertNotIn(secret, entry, secret)

    def test_the_backups_are_renumbered_contiguously(self):
        published, _ = self._run([self._canonical()], [self._carried()])
        names = [b["name"] for b in published[0]["backups"]]
        self.assertEqual(names, [f"Backup-{i + 1}" for i in range(len(names))])


class TheCarriedBroadcasterBecomesAChannel(ACarriedCardIsReconciledNotDuplicated):
    """Sections 6-10: the point of reconciling is that Willow survives as a
    selectable channel of the canonical event, not just as an anonymous backup."""

    def _reconciled(self):
        published, _ = self._run([self._canonical()], [self._carried()])
        return published[0]

    def test_a_channel_named_for_the_carried_broadcaster_exists(self):
        channels = self._reconciled().get("channels") or []
        self.assertEqual([c["name"] for c in channels], ["Willow"], channels)

    def test_the_channel_id_is_namespaced_by_the_canonical_event(self):
        card = self._reconciled()
        channel = card["channels"][0]
        self.assertEqual(channel["id"], "sri-lanka-vs-india-1st-test--willow")

    def test_the_channel_carries_every_carried_stream(self):
        channel = self._reconciled()["channels"][0]
        self.assertEqual(channel["stream_count"], 3)
        self.assertEqual(
            [s["playback_id"] for s in channel["streams"]],
            ["ctv_willow_primary", "ctv_willow_b1", "ctv_willow_b2"],
        )

    def test_the_channels_streams_have_distinct_identities(self):
        channel = self._reconciled()["channels"][0]
        keys = [s["variant_key"] for s in channel["streams"]]
        self.assertEqual(len(keys), len(set(keys)), keys)
        self.assertTrue(all(keys), keys)

    def test_the_channel_states_a_renderer_and_a_confidence(self):
        channel = self._reconciled()["channels"][0]
        self.assertEqual(channel["renderer"], PLAYBACK_NATIVE)
        self.assertIn(channel["name_confidence"],
                      ("explicit", "metadata", "alias", "derived"))

    def test_a_working_native_primary_is_not_defaulted_away_from(self):
        """Section 27's rule in its other shape: the canonical primary is not
        inside the absorbed channel, so the channel must not become the default
        and reorder the playback plan."""
        card = self._reconciled()
        self.assertFalse(str(card.get("default_channel_id") or ""))

    def test_the_carried_event_id_is_recorded_for_continuity(self):
        card = self._reconciled()
        self.assertEqual(card["absorbed_event_ids"], ["india-vs-sri-lanka-willow"])

    def test_a_carried_card_with_no_readable_broadcaster_adds_no_channel(self):
        anonymous = self._carried(name="Sri Lanka vs India")
        published, stats = self._run([self._canonical()], [anonymous])
        self.assertEqual(stats["reconciled_into_canonical"], 1)
        self.assertFalse(published[0].get("channels"))
        # Its streams still arrive, which is the part that matters.
        ids = {str(b.get("playback_id")) for b in published[0]["backups"]}
        self.assertIn("ctv_willow_primary", ids)


class ASessionInProgressIsNeverReconciled(ACarriedCardIsReconciledNotDuplicated):
    """No duplicate is worth touching a stream someone is watching."""

    def test_the_watched_card_stays_its_own_card(self):
        published, stats = self._run(
            [self._canonical()], [self._carried()],
            playing=["india-vs-sri-lanka-willow"],
        )
        self.assertEqual(len(published), 2)
        self.assertEqual(stats["reconciled_into_canonical"], 0)
        self.assertEqual(stats["reconciled_playing_kept_separate"], 1)

    def test_its_own_primary_stays_its_primary(self):
        published, _ = self._run(
            [self._canonical()], [self._carried()],
            playing=["india-vs-sri-lanka-willow"],
        )
        watched = next(c for c in published if c["id"] == "india-vs-sri-lanka-willow")
        self.assertEqual(watched["playback_id"], "ctv_willow_primary")
        self.assertEqual(len(watched["backups"]), 2)

    def test_it_is_kept_even_when_every_link_probes_dead(self):
        published, stats = self._run(
            [self._canonical()], [self._carried()],
            playing=["india-vs-sri-lanka-willow"], probe=lambda card: False,
        )
        self.assertEqual(len(published), 2)
        self.assertEqual(stats["protected_playing"], 1)


class ReconcilingDoesNotWeakenRetirement(ACarriedCardIsReconciledNotDuplicated):
    """A finished or dead event must still be released, canonical card or not."""

    def test_a_strong_end_signal_still_retires_the_carried_card(self):
        published, stats = self._run(
            [self._canonical()], [self._carried(status="FT")],
            probe=lambda card: False,
        )
        self.assertEqual(len(published), 1)
        self.assertEqual(stats["released_ended"], 1)
        self.assertEqual(stats["reconciled_into_canonical"], 0)
        self.assertFalse(published[0].get("absorbed_event_ids"))

    def test_a_finished_authority_verdict_still_retires_it(self):
        published, stats = self._run(
            [self._canonical()], [self._carried()], probe=lambda card: False,
            authority={"india-vs-sri-lanka-willow": False},
        )
        self.assertEqual(len(published), 1)
        self.assertEqual(stats["reconciled_into_canonical"], 0)

    def test_an_end_pending_card_is_still_reconciled_rather_than_dropped(self):
        """END_PENDING publishes, so it must arrive somewhere - and the somewhere
        is the canonical card, not a duplicate.

        Both cards are given the same long-past kickoff: the estimated end has to
        be behind us for END_PENDING to be reachable, and the two kickoffs still
        have to agree or they are not the same fixture in the first place.
        """
        old = (NOW - timedelta(hours=30)).isoformat()
        published, stats = self._run(
            [self._canonical(start_time=old)],
            [self._carried(start_time=old)],
            probe=lambda card: False,
        )
        self.assertEqual(len(published), 1, [c.get("name") for c in published])
        self.assertEqual(stats["reconciled_into_canonical"], 1)
        ids = {str(b.get("playback_id")) for b in published[0]["backups"]}
        self.assertIn("ctv_willow_primary", ids)

    def test_a_different_kickoff_is_a_different_fixture_and_is_not_folded(self):
        """Kickoff tolerance is not relaxed by the reconciler: the same two teams
        meeting on another day stay their own event."""
        published, stats = self._run(
            [self._canonical()],
            [self._carried(start_time=(NOW - timedelta(hours=30)).isoformat())],
        )
        self.assertEqual(len(published), 2)
        self.assertEqual(stats["reconciled_into_canonical"], 0)
        self.assertEqual(stats["carried_forward"], 1)

    def test_both_cards_carried_still_becomes_one_card(self):
        """The shape the duplicate actually shipped in.

        On a scan where the live playlist is empty, the canonical card is carried
        too - so there is no fresh card to fold into and the reconciliation has to
        happen among the carried set itself. Looking only at this scan's output
        found nothing to host the fold, and the duplicate survived.
        """
        published, stats = self._run(
            [], [self._canonical(), self._carried()],
        )
        self.assertEqual(len(published), 1, [c.get("name") for c in published])
        self.assertEqual(published[0]["name"], "Sri Lanka vs India 1st Test")
        self.assertEqual(stats["reconciled_into_canonical"], 1)
        self.assertEqual(stats["carried_forward"], 1)

    def test_the_match_title_wins_over_the_broadcaster_title(self):
        """Section 5. Whichever order they arrive in, the surviving card is the
        one whose title is the match rather than the channel."""
        for previous in (
            [self._canonical(), self._carried()],
            [self._carried(), self._canonical()],
        ):
            published, _ = self._run([], previous)
            self.assertEqual(len(published), 1)
            self.assertEqual(published[0]["name"], "Sri Lanka vs India 1st Test")
            self.assertEqual(published[0]["id"], "sri-lanka-vs-india-1st-test")

    def test_the_kept_card_keeps_its_own_primary_when_both_were_carried(self):
        published, _ = self._run([], [self._canonical(), self._carried()])
        self.assertEqual(published[0]["playback_id"], "ctv_canonical_primary")
        self.assertEqual(published[0]["backups"][0]["playback_id"],
                         "ctv_willow_primary")

    def test_three_spellings_of_one_match_still_become_one_card(self):
        third = self._carried(
            id="sri-lanka-vs-india", name="Sri Lanka vs India",
            playback_id="ctv_third_primary", backups=[],
        )
        published, stats = self._run(
            [], [self._canonical(), self._carried(), third],
        )
        self.assertEqual(len(published), 1, [c.get("name") for c in published])
        self.assertEqual(stats["reconciled_into_canonical"], 2)
        reachable = {str(published[0].get("playback_id"))}
        reachable.update(str(b.get("playback_id")) for b in published[0]["backups"])
        reachable.update(
            str(s.get("playback_id"))
            for c in published[0].get("channels") or []
            for s in c.get("streams") or []
        )
        for playback_id in ("ctv_canonical_primary", "ctv_willow_primary",
                            "ctv_third_primary"):
            self.assertIn(playback_id, reachable, playback_id)

    def test_the_miss_streak_is_cleared_because_the_event_is_present(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder) / "protection.json"
            for _ in range(4):
                protect_live_events(
                    [self._canonical()], [self._carried()], state_path=state,
                    now=NOW, probe=lambda card: True, authority_states={},
                )
            misses = json.loads(state.read_text(encoding="utf-8")).get("misses") or {}
            self.assertNotIn("india-vs-sri-lanka-willow", misses, misses)


if __name__ == "__main__":
    unittest.main()
