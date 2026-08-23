"""The persistence counter, tenant correlation and the Phase 0b locks.

THE_EXCLUSIVE_UPDATE section 7 lists thirteen cases this file must cover. They
all guard the same thing from a different side: a channel may only be given up
on when the evidence is about the route, is repeated across genuinely separate
observations, and is not cancelled by any moment of real sustained playback. The
loose reading of "success" is what these tests exist to make impossible - HTTP
200 with a valid small manifest was measured on every hidden channel's primary
while the channel was stored as failed.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import route_evidence as re_mod  # noqa: E402


KEY = b"test-key-not-a-real-secret"
T0 = 1_700_000_000.0

#: A 120 s PASS, i.e. the only kind of observation that resets the counter.
PASS_METRICS = {
    "announced_render_tracks": ["video", "audio"],
    "progressing_tracks": ["video", "audio"],
    "first_frame_seconds": 1.5,
    "startup_seconds": 2.0,
    "media_progress_seconds": 119.0,
    "cumulative_stall_seconds": 0.5,
    "fatal_errors": [],
}


def _obs(offset, verdict=re_mod.ADVISORY_TRANSIENT_NETWORK, **over):
    record = {"observed_at": T0 + offset, "verdict": verdict}
    record.update(over)
    return record


def _full_pass(offset):
    return _obs(
        offset,
        verdict=re_mod.PROVEN,
        kind="full_playback_session",
        window_seconds=120.0,
        playback_metrics=dict(PASS_METRICS),
    )


class Phase0bLockTests(unittest.TestCase):
    """The four values L7 says must be locked before any classifier runs."""

    def test_all_four_values_are_locked(self):
        self.assertTrue(re_mod.LOCKS_DECLARED)
        self.assertTrue(re_mod.DECLARED_TARGET_MATRIX)
        self.assertGreater(re_mod.PERSISTENCE_TTL_SECONDS, 0)
        self.assertTrue(re_mod.NORMALIZATION_ALLOWLIST_LOCKED)
        self.assertLess(re_mod.KEYFRAME_MIN_MEDIA_CLOCK_SECONDS, float("inf"))

    def test_window_separation_exceeds_measured_cache_ttl(self):
        # The largest measured CDN cache TTL bound was 23 s. Two observations
        # closer together than that can be one cached response counted twice.
        self.assertGreater(re_mod.PERSISTENCE_MIN_WINDOW_SEPARATION_SECONDS, 23.0)

    def test_identity_parameters_are_never_removable(self):
        # 22 channels differ only by "?id=NNN".
        for name in ("id", "ch", "channel", "stream"):
            self.assertNotIn(name, re_mod.REMOVABLE_QUERY_PARAMS)

    def test_keyframe_floor_exceeds_the_current_probe(self):
        # The 16 KiB gate is 2.8% of one intra interval, so it can never support
        # a keyframe verdict.
        verdict, why = re_mod.keyframe_verdict(
            media_clock_seconds=0.3, keyframes_observed=0
        )
        self.assertEqual(verdict, re_mod.UNKNOWN)
        self.assertIn("below the locked", why)


class PersistenceResetTests(unittest.TestCase):
    """Section 3: only a full sustained success resets the counter."""

    def test_http_200_does_not_reset(self):
        resets, why = re_mod.resets_persistence(
            {"kind": "http_status", "status": 200, "window_seconds": 3600}
        )
        self.assertFalse(resets)
        self.assertIn("http_status", why)

    def test_manifest_fetch_does_not_reset(self):
        resets, _ = re_mod.resets_persistence(
            {"kind": "manifest_fetch", "window_seconds": 3600}
        )
        self.assertFalse(resets)

    def test_sixteen_kib_sample_does_not_reset(self):
        resets, _ = re_mod.resets_persistence(
            {"kind": "byte_sample", "bytes": 16384, "window_seconds": 3600}
        )
        self.assertFalse(resets)

    def test_observation_shorter_than_the_window_does_not_reset(self):
        resets, why = re_mod.resets_persistence(
            {
                "kind": "full_playback_session",
                "window_seconds": 60.0,
                "playback_metrics": dict(PASS_METRICS),
            }
        )
        self.assertFalse(resets)
        self.assertIn("shorter than", why)

    def test_startup_then_failure_does_not_reset(self):
        resets, _ = re_mod.resets_persistence(
            {"kind": "startup_then_failure", "window_seconds": 120.0}
        )
        self.assertFalse(resets)

    def test_full_120s_pass_does_reset(self):
        resets, why = re_mod.resets_persistence(
            {
                "kind": "full_playback_session",
                "window_seconds": 120.0,
                "playback_metrics": dict(PASS_METRICS),
            }
        )
        self.assertTrue(resets)
        self.assertIn("120", why)

    def test_browserless_sustained_delivery_resets(self):
        resets, _ = re_mod.resets_persistence(
            {
                "kind": "sustained_media_delivery",
                "window_seconds": 121.0,
                "max_delivery_gap_seconds": 0.4,
                "fatal_errors": [],
            }
        )
        self.assertTrue(resets)

    def test_sustained_delivery_with_a_long_gap_does_not_reset(self):
        resets, why = re_mod.resets_persistence(
            {
                "kind": "sustained_media_delivery",
                "window_seconds": 121.0,
                "max_delivery_gap_seconds": 9.0,
            }
        )
        self.assertFalse(resets)
        self.assertIn("gap", why)

    def test_sustained_delivery_without_a_measured_gap_does_not_reset(self):
        resets, _ = re_mod.resets_persistence(
            {"kind": "sustained_media_delivery", "window_seconds": 121.0}
        )
        self.assertFalse(resets)


class PersistenceCounterTests(unittest.TestCase):
    """Section 6: transient + TTL + no full success -> persistent candidate."""

    def test_repeated_transient_across_separate_windows_matures(self):
        state = re_mod.persistence_state(
            [_obs(0), _obs(600), _obs(1200)], now=T0 + 1200
        )
        self.assertEqual(state["state"], re_mod.PERSISTENT_UNAVAILABLE_CANDIDATE)
        self.assertGreaterEqual(state["counter"], 2)

    def test_a_single_transient_never_matures(self):
        state = re_mod.persistence_state([_obs(0)], now=T0)
        self.assertEqual(state["state"], re_mod.UNKNOWN)

    def test_observations_inside_the_cache_window_count_once(self):
        # Three reads 5 s apart can be one cached response; measured cache TTL
        # bounds were 17-23 s.
        state = re_mod.persistence_state(
            [_obs(0), _obs(5), _obs(10)], now=T0 + 10
        )
        self.assertEqual(state["counter"], 1)
        self.assertEqual(state["state"], re_mod.UNKNOWN)

    def test_a_full_pass_resets_and_discards_everything_before_it(self):
        state = re_mod.persistence_state(
            [_obs(0), _obs(600), _full_pass(700), _obs(800)], now=T0 + 800
        )
        self.assertEqual(state["state"], re_mod.UNKNOWN)
        self.assertEqual(state["counter"], 1)
        self.assertIsNotNone(state["reset_at"])

    def test_http_200_inside_the_span_does_not_rescue_the_route(self):
        # The mirror image of the reset tests: a 200 among the transients must
        # neither reset the counter nor be counted as escalatable evidence.
        state = re_mod.persistence_state(
            [
                _obs(0),
                _obs(600, verdict=re_mod.UNKNOWN, kind="http_status", status=200),
                _obs(1200),
            ],
            now=T0 + 1200,
        )
        self.assertIsNone(state["reset_at"])
        self.assertEqual(state["state"], re_mod.PERSISTENT_UNAVAILABLE_CANDIDATE)
        self.assertEqual(state["escalatable_observations"], 2)

    def test_evidence_older_than_the_ttl_expires_out(self):
        state = re_mod.persistence_state(
            [_obs(0), _obs(600)], now=T0 + re_mod.PERSISTENCE_TTL_SECONDS + 1200
        )
        self.assertEqual(state["state"], re_mod.UNKNOWN)

    def test_repeated_vantage_blocks_never_mature(self):
        state = re_mod.persistence_state(
            [_obs(i * 600, verdict=re_mod.ADVISORY_VANTAGE_BLOCKED) for i in range(8)],
            now=T0 + 4200,
        )
        self.assertEqual(state["state"], re_mod.UNKNOWN)
        self.assertEqual(state["escalatable_observations"], 0)

    def test_repeated_device_unsupported_never_matures(self):
        state = re_mod.persistence_state(
            [
                _obs(i * 600, verdict=re_mod.ADVISORY_DEVICE_UNSUPPORTED)
                for i in range(8)
            ],
            now=T0 + 4200,
        )
        self.assertEqual(state["state"], re_mod.UNKNOWN)

    def test_repeated_structurally_risky_never_matures(self):
        state = re_mod.persistence_state(
            [
                _obs(i * 600, verdict=re_mod.ADVISORY_STRUCTURALLY_RISKY)
                for i in range(8)
            ],
            now=T0 + 4200,
        )
        self.assertEqual(state["state"], re_mod.UNKNOWN)

    def test_repeated_domain_events_never_mature(self):
        state = re_mod.persistence_state(
            [_obs(i * 600, verdict=re_mod.ADVISORY_DOMAIN_EVENT) for i in range(8)],
            now=T0 + 4200,
        )
        self.assertEqual(state["state"], re_mod.UNKNOWN)

    def test_undated_observations_are_ignored_not_guessed(self):
        state = re_mod.persistence_state(
            [{"verdict": re_mod.ADVISORY_TRANSIENT_NETWORK} for _ in range(5)]
        )
        self.assertEqual(state["state"], re_mod.UNKNOWN)
        self.assertEqual(state["counter"], 0)

    def test_an_unlocked_ttl_can_never_mature_a_candidate(self):
        state = re_mod.persistence_state(
            [_obs(0), _obs(600), _obs(1200)], now=T0 + 1200, ttl_seconds=0
        )
        self.assertEqual(state["state"], re_mod.UNKNOWN)


class TenantCorrelationTests(unittest.TestCase):
    """Section 4: an undetermined tenant leaves correlation unknown."""

    def test_underivable_tenant_gives_unknown_correlation(self):
        self.assertEqual(
            re_mod.correlation_group("https://cache.devm3u.top/live/x.m3u8"),
            re_mod.UNKNOWN,
        )

    def test_bare_registrable_domain_gives_unknown_correlation(self):
        self.assertEqual(
            re_mod.correlation_group("https://livelegitpro.in/live/x.m3u8"),
            re_mod.UNKNOWN,
        )

    def test_underivable_tenant_gives_unknown_redundancy_too(self):
        routes = [
            {"url": "https://cache.devm3u.top/live/a.m3u8"},
            {"url": "https://tenant-a.example.net/live/b.m3u8"},
        ]
        self.assertEqual(re_mod.independent_redundancy(routes, KEY), re_mod.UNKNOWN)

    def test_unknown_is_never_reported_as_zero_or_one(self):
        event = re_mod.correlated_event(
            [
                {"url": "https://cache.devm3u.top/live/a.m3u8"},
                {"url": "https://tenant-a.example.net/live/b.m3u8"},
            ]
        )
        self.assertEqual(event["correlation"], re_mod.UNKNOWN)
        self.assertNotIn(event["correlation"], (0, 1))
        self.assertEqual(event["undetermined_count"], 1)

    def test_undetermined_routes_are_neither_grouped_in_nor_excluded(self):
        event = re_mod.correlated_event(
            [
                {"url": "https://cache.devm3u.top/live/a.m3u8"},
                {"url": "https://tenant-a.example.net/live/b.m3u8"},
            ]
        )
        # Present in the report, absent from every group.
        self.assertEqual(len(event["undetermined_routes"]), 1)
        for members in event["groups"].values():
            self.assertTrue(all("devm3u" not in m for m in members))

    def test_two_tenants_on_one_shared_cdn_stay_separate(self):
        # Registrable-domain-only grouping was measured fusing 38 akamaized.net
        # tenants into one failure domain.
        a = re_mod.correlation_group("https://tenant-a.akamaized.net/x.m3u8")
        b = re_mod.correlation_group("https://tenant-b.akamaized.net/y.m3u8")
        self.assertNotEqual(a, b)

    def test_redundancy_survives_a_missing_hmac_key(self):
        # Without a key the HMAC tenant field is None; grouping on it would
        # report two independent tenants as redundancy 1, and undercounting
        # redundancy makes hiding easier.
        routes = [
            {"url": "https://tenant-a.akamaized.net/x.m3u8"},
            {"url": "https://tenant-b.akamaized.net/y.m3u8"},
        ]
        self.assertEqual(re_mod.independent_redundancy(routes, None), 2)

    def test_the_grouping_key_may_never_be_committed(self):
        self.assertTrue(
            re_mod.evidence_contains_forbidden_material(
                {"route_id": "r1", "tenant_grouping_key": "0123456789abcdef"}
            )
        )


class DeclaredMatrixScopeTests(unittest.TestCase):
    """Section 2: what actually earns global scope."""

    def test_two_chromium_profiles_are_environment_scope_only(self):
        scope = re_mod.resolve_verdict_scope(
            re_mod.PLAYBACK_FAIL,
            browser_profile="desktop_chrome",
            failed_profiles=["desktop_chrome", "android_chrome"],
        )
        self.assertTrue(scope.startswith("environment:"))

    def test_the_complete_declared_matrix_reaches_global(self):
        scope = re_mod.resolve_verdict_scope(
            re_mod.PLAYBACK_FAIL,
            browser_profile="desktop_chrome",
            failed_profiles=list(re_mod.DECLARED_TARGET_MATRIX),
        )
        self.assertEqual(scope, "global")

    def test_an_undeclared_matrix_makes_global_unreachable_via_route_b(self):
        scope = re_mod.resolve_verdict_scope(
            re_mod.PLAYBACK_FAIL,
            browser_profile="desktop_chrome",
            declared_matrix=(),
            failed_profiles=["desktop_chrome", "android_chrome", "iphone_safari", "android_tv"],
        )
        self.assertNotEqual(scope, "global")

    def test_the_locked_matrix_includes_a_tv_profile(self):
        # The player special-cases TV user agents, so a TV device is its own
        # environment and a desktop result says nothing about it.
        self.assertTrue(
            any("tv" in p.lower() for p in re_mod.DECLARED_TARGET_MATRIX)
        )

    def test_a_missing_tv_profile_cannot_reach_global(self):
        scope = re_mod.resolve_verdict_scope(
            re_mod.PLAYBACK_FAIL,
            browser_profile="desktop_chrome",
            failed_profiles=["desktop_chrome", "android_chrome", "iphone_safari"],
        )
        self.assertNotEqual(scope, "global")


class KeyframeVerdictTests(unittest.TestCase):
    def test_open_gop_is_a_structural_risk_not_a_failure(self):
        verdict, why = re_mod.keyframe_verdict(
            media_clock_seconds=17.5, keyframes_observed=0, open_gop_intra_observed=18
        )
        self.assertEqual(verdict, re_mod.ADVISORY_STRUCTURALLY_RISKY)
        self.assertFalse(re_mod.is_escalatable(verdict))
        self.assertIn("not a fault", why)

    def test_keyframes_present_is_proven(self):
        verdict, _ = re_mod.keyframe_verdict(
            media_clock_seconds=36.2, keyframes_observed=4
        )
        self.assertEqual(verdict, re_mod.PROVEN)

    def test_an_unmeasured_count_is_unknown(self):
        verdict, _ = re_mod.keyframe_verdict(media_clock_seconds=36.2)
        self.assertEqual(verdict, re_mod.UNKNOWN)


class DecoderCapabilityTests(unittest.TestCase):
    """A decoder that cannot handle the bytes is not a broken route.

    Every case here is the Zee Bangla measurement of 2026-08-23, taken in real
    Chromium across a full 120 s window: MEDIA_ERR_DECODE with audio decoding
    normally and the video decoder producing zero frames, on a route whose
    Phase 0 structure is 1080i interlaced H.264 with zero IDR frames. The stream
    is fine; this browser cannot play it. If that ever classifies as a route
    failure, a channel the owner watches gets removed.
    """

    ZEE = {
        "announced_render_tracks": ["video", "audio"],
        "progressing_tracks": ["audio"],
        "first_frame_seconds": 4.42,
        "startup_seconds": 4.42,
        "media_progress_seconds": 0.08,
        "cumulative_stall_seconds": 120,
        "fatal_errors": [
            "media element error code 3",
            "mpegts MediaError/MediaMSEError {\"code\":11,\"msg\":\"Failed to "
            "execute 'appendBuffer' on 'SourceBuffer'\"}",
        ],
        "recovered_to_pass_floor": False,
    }

    def test_a_decode_error_is_device_unsupported_not_playback_fail(self):
        verdict, _ = re_mod.classify_playback(dict(self.ZEE))
        self.assertEqual(verdict, re_mod.ADVISORY_DEVICE_UNSUPPORTED)
        self.assertNotEqual(verdict, re_mod.PLAYBACK_FAIL)

    def test_that_verdict_can_never_escalate(self):
        verdict, _ = re_mod.classify_playback(dict(self.ZEE))
        self.assertFalse(re_mod.is_escalatable(verdict))

    def test_that_verdict_is_capped_at_environment_scope(self):
        verdict, _ = re_mod.classify_playback(dict(self.ZEE))
        scope = re_mod.resolve_verdict_scope(
            verdict, browser_profile="desktop_chrome"
        )
        self.assertEqual(scope, "environment:desktop_chrome")
        self.assertNotEqual(scope, "global")

    def test_repeating_it_forever_never_matures_a_candidate(self):
        verdict, _ = re_mod.classify_playback(dict(self.ZEE))
        state = re_mod.persistence_state(
            [_obs(i * 600, verdict=verdict) for i in range(12)], now=T0 + 6600
        )
        self.assertEqual(state["state"], re_mod.UNKNOWN)
        self.assertEqual(state["escalatable_observations"], 0)

    def test_it_can_never_hide_a_visible_channel(self):
        verdict, _ = re_mod.classify_playback(dict(self.ZEE))
        allowed, why = re_mod.may_hide(
            state=re_mod.EXISTING_VISIBLE,
            evidence=[
                {
                    "route_id": "r1",
                    "url_public_template": "{id}.example.net/live/{f}",
                    "url_registrable_domain": "example.net",
                    "final_origin_public_template": "{id}.example.net/live/{f}",
                    "final_origin_registrable_domain": "example.net",
                    "failure_domain_provider": "example.net",
                    "failure_domain_tenant": "abcd",
                    "delivery_path": "proxy",
                    "browser_profile": profile,
                    "test_vantage": {"asn": f"AS{i}", "provider": f"p{i}"},
                    "media_fingerprint": {"video": "h264"},
                    "playback_metrics": dict(self.ZEE),
                    "observed_at": f"2026-08-2{i}T00:00:00Z",
                    "ttl": 1800,
                    "verdict": verdict,
                    "verdict_scope": "global",
                }
                for i, profile in enumerate(
                    ["desktop_chrome", "android_chrome", "iphone_safari", "android_tv"],
                    start=1,
                )
            ],
        )
        self.assertFalse(allowed, why)

    def test_a_genuine_route_failure_is_still_a_playback_fail(self):
        # The guard must not swallow real failures: no fatal decoder signature,
        # nothing progressed at all.
        verdict, _ = re_mod.classify_playback(
            {
                "announced_render_tracks": ["video", "audio"],
                "progressing_tracks": [],
                "first_frame_seconds": None,
                "startup_seconds": 0.0,
                "media_progress_seconds": 0.0,
                "cumulative_stall_seconds": 120,
                "fatal_errors": [],
                "recovered_to_pass_floor": False,
            }
        )
        self.assertEqual(verdict, re_mod.PLAYBACK_FAIL)
        self.assertTrue(re_mod.is_escalatable(verdict))

    def test_hls_buffer_rejections_are_decoder_limits(self):
        # hls.js spells the SourceBuffer rejection as bufferAppendError, which
        # the plain "appendbuffer" marker did not match. Measured on Asian TV in
        # the Phase 1 run, where it classified as an escalatable route failure.
        for detail in (
            "hls mediaError/bufferAppendError",
            "hls mediaError/bufferAddCodecError",
            "hls mediaError/bufferIncompatibleCodecsError",
        ):
            self.assertTrue(
                re_mod.describes_decoder_capability([detail]), detail
            )
            verdict, _ = re_mod.classify_playback(
                {
                    "announced_render_tracks": ["video", "audio"],
                    "progressing_tracks": [],
                    "first_frame_seconds": 2.0,
                    "startup_seconds": 2.0,
                    "media_progress_seconds": 1.0,
                    "cumulative_stall_seconds": 118,
                    "fatal_errors": [detail],
                    "recovered_to_pass_floor": False,
                }
            )
            self.assertEqual(verdict, re_mod.ADVISORY_DEVICE_UNSUPPORTED, detail)
            self.assertFalse(re_mod.is_escalatable(verdict), detail)

    def test_hls_network_errors_are_transient_not_route_failures(self):
        # Corrected by the Phase 5 movie run, which classified a single mpegts
        # "Failed to fetch" as PLAYBACK_FAIL - the strongest escalatable class,
        # reachable to hard_disqualified from two observations. One failed fetch
        # is not a dead route. These stay escalatable, but only through the
        # locked persistence window, which is the whole reason
        # advisory:transient_network exists: a dead origin and a momentary
        # network fault look identical once, and only persistence separates them.
        for detail in (
            "mpegts NetworkError/Exception {\"code\":-1,\"msg\":\"Failed to fetch\"}",
            "hls networkError/fragLoadError",
            "hls networkError/levelLoadError",
            "hls networkError/manifestLoadError",
        ):
            self.assertFalse(
                re_mod.describes_decoder_capability([detail]), detail
            )
            self.assertTrue(re_mod.describes_transient_network([detail]), detail)
            verdict, _ = re_mod.classify_playback(
                {
                    "announced_render_tracks": ["video", "audio"],
                    "progressing_tracks": [],
                    "first_frame_seconds": None,
                    "startup_seconds": None,
                    "media_progress_seconds": 0.0,
                    "cumulative_stall_seconds": 120,
                    "fatal_errors": [detail],
                    "recovered_to_pass_floor": False,
                }
            )
            self.assertEqual(verdict, re_mod.ADVISORY_TRANSIENT_NETWORK, detail)
            # Escalatable, so the model can still conclude something - but only
            # across the window, never on one observation.
            self.assertTrue(re_mod.is_escalatable(verdict), detail)

    def test_a_direct_fetch_refusal_is_structural_not_transient(self):
        """A missing CORS header is a route configuration fact.

        The movie probe measured 0 of 215 routes sending
        Access-Control-Allow-Origin, so a direct fetch can never succeed in any
        browser for any viewer. Calling that transient made it escalatable, when
        what it actually means is "use the proxy for this route" - a ranking and
        player-config fact that must never touch visibility.
        """
        metrics = {
            "announced_render_tracks": ["video", "audio"],
            "progressing_tracks": [],
            "first_frame_seconds": None,
            "startup_seconds": None,
            "media_progress_seconds": 0.0,
            "cumulative_stall_seconds": 35,
            "fatal_errors": [
                'mpegts NetworkError/Exception {"code":-1,"msg":"Failed to fetch"}'
            ],
            "recovered_to_pass_floor": False,
        }
        verdict, _ = re_mod.classify_playback(metrics, delivery_path="direct")
        self.assertEqual(verdict, re_mod.ADVISORY_STRUCTURALLY_RISKY)
        self.assertFalse(re_mod.is_escalatable(verdict))

    def test_the_same_refusal_through_a_proxy_stays_transient(self):
        # Through a proxy there is no CORS story to tell: the proxy sends the
        # headers, so a refusal there really is a network condition.
        metrics = {
            "announced_render_tracks": ["video", "audio"],
            "progressing_tracks": [],
            "first_frame_seconds": None,
            "startup_seconds": None,
            "media_progress_seconds": 0.0,
            "cumulative_stall_seconds": 35,
            "fatal_errors": [
                'mpegts NetworkError/Exception {"code":-1,"msg":"Failed to fetch"}'
            ],
            "recovered_to_pass_floor": False,
        }
        verdict, _ = re_mod.classify_playback(metrics, delivery_path="proxy")
        self.assertEqual(verdict, re_mod.ADVISORY_TRANSIENT_NETWORK)

    def test_a_real_status_on_a_direct_route_keeps_its_own_class(self):
        # The structural rule must only cover requests that never reached a
        # status; a 403 is still a vantage block and a 503 still transient.
        base = {
            "announced_render_tracks": ["video", "audio"],
            "progressing_tracks": [],
            "first_frame_seconds": None,
            "startup_seconds": None,
            "media_progress_seconds": 0.0,
            "cumulative_stall_seconds": 35,
            "recovered_to_pass_floor": False,
        }
        for detail, expected in (
            ('mpegts NetworkError/HttpStatusCodeInvalid {"code":403}',
             re_mod.ADVISORY_VANTAGE_BLOCKED),
            ('mpegts NetworkError/HttpStatusCodeInvalid {"code":503}',
             re_mod.ADVISORY_TRANSIENT_NETWORK),
        ):
            verdict, _ = re_mod.classify_playback(
                dict(base, fatal_errors=[detail]), delivery_path="direct"
            )
            self.assertEqual(verdict, expected, detail)

    def test_repeating_a_structural_refusal_never_matures(self):
        state = re_mod.persistence_state(
            [
                _obs(i * 600, verdict=re_mod.ADVISORY_STRUCTURALLY_RISKY)
                for i in range(12)
            ],
            now=T0 + 6600,
        )
        self.assertEqual(state["state"], re_mod.UNKNOWN)

    def test_a_403_in_a_fatal_error_is_a_vantage_block(self):
        """A geo-block must never accumulate toward disqualification.

        Measured on the Phase 5 movie run: an mpegts
        HttpStatusCodeInvalid {"code":403} classified as
        advisory:transient_network, which IS escalatable - so a block from this
        egress could have counted toward removing a route that works perfectly
        for the audience. A published, owner-working channel was measured
        returning 403 three times from this vantage; that measurement is why
        this module exists, and the transient marker list had quietly undone it.
        """
        for detail, label in (
            ('mpegts NetworkError/HttpStatusCodeInvalid {"code":403,"msg":""}', "403"),
            ('mpegts NetworkError/HttpStatusCodeInvalid {"code":401}', "401"),
            ('mpegts NetworkError/HttpStatusCodeInvalid {"code":429}', "429"),
            ('mpegts NetworkError/HttpStatusCodeInvalid {"code":451}', "451"),
            ("hls networkError/fragLoadError 403 Forbidden", "forbidden"),
        ):
            self.assertTrue(re_mod.describes_vantage_block([detail]), label)
            verdict, _ = re_mod.classify_playback(
                {
                    "announced_render_tracks": ["video", "audio"],
                    "progressing_tracks": [],
                    "first_frame_seconds": None,
                    "startup_seconds": None,
                    "media_progress_seconds": 0.0,
                    "cumulative_stall_seconds": 35,
                    "fatal_errors": [detail],
                    "recovered_to_pass_floor": False,
                }
            )
            self.assertEqual(verdict, re_mod.ADVISORY_VANTAGE_BLOCKED, label)
            self.assertFalse(re_mod.is_escalatable(verdict), label)

    def test_a_vantage_block_is_capped_at_vantage_scope(self):
        scope = re_mod.resolve_verdict_scope(
            re_mod.ADVISORY_VANTAGE_BLOCKED, vantage_id="scanner-egress"
        )
        self.assertEqual(scope, "vantage:scanner-egress")

    def test_repeating_a_403_forever_never_matures(self):
        state = re_mod.persistence_state(
            [_obs(i * 600, verdict=re_mod.ADVISORY_VANTAGE_BLOCKED) for i in range(12)],
            now=T0 + 6600,
        )
        self.assertEqual(state["state"], re_mod.UNKNOWN)
        self.assertEqual(state["escalatable_observations"], 0)

    def test_a_server_error_is_still_transient_not_a_vantage_block(self):
        # The distinction has to cut both ways: a 503 is the server's problem,
        # not the observer's, and must keep its path through the window.
        for detail in (
            'mpegts NetworkError/HttpStatusCodeInvalid {"code":503}',
            'mpegts NetworkError/HttpStatusCodeInvalid {"code":502}',
        ):
            self.assertFalse(re_mod.describes_vantage_block([detail]), detail)
            self.assertTrue(re_mod.describes_transient_network([detail]), detail)

    def test_one_transient_alone_can_never_mature(self):
        state = re_mod.persistence_state(
            [_obs(0, verdict=re_mod.ADVISORY_TRANSIENT_NETWORK)], now=T0
        )
        self.assertEqual(state["state"], re_mod.UNKNOWN)

    def test_a_failure_with_no_signature_is_still_a_route_failure(self):
        # Nothing progressed, nothing to blame the network or the decoder for.
        verdict, _ = re_mod.classify_playback(
            {
                "announced_render_tracks": ["video", "audio"],
                "progressing_tracks": [],
                "first_frame_seconds": None,
                "startup_seconds": None,
                "media_progress_seconds": 0.0,
                "cumulative_stall_seconds": 120,
                "fatal_errors": [],
                "recovered_to_pass_floor": False,
            }
        )
        self.assertEqual(verdict, re_mod.PLAYBACK_FAIL)

    def test_a_network_error_is_not_read_as_a_decoder_limit(self):
        self.assertFalse(
            re_mod.describes_decoder_capability(["media element error code 2"])
        )
        self.assertTrue(
            re_mod.describes_decoder_capability(["media element error code 3"])
        )

    def test_an_empty_error_list_is_not_a_decoder_limit(self):
        self.assertFalse(re_mod.describes_decoder_capability([]))
        self.assertFalse(re_mod.describes_decoder_capability(None))

    def test_startup_that_never_happened_is_measured_not_missing(self):
        # The Phase 1 run reported MEDIA_ERR_DECODE with the media clock frozen
        # at zero, so startup_seconds was never set. Reading that as a missing
        # field returned `unknown` and hid a decoder failure we had observed.
        verdict, reasons = re_mod.classify_playback(
            {
                "announced_render_tracks": ["video", "audio"],
                "progressing_tracks": ["audio"],
                "first_frame_seconds": None,
                "startup_seconds": None,
                "media_progress_seconds": 0.0,
                "cumulative_stall_seconds": 120,
                "fatal_errors": ["media element error code 3"],
                "recovered_to_pass_floor": False,
            }
        )
        self.assertEqual(verdict, re_mod.ADVISORY_DEVICE_UNSUPPORTED)
        self.assertIn("playback never started within the window", reasons)

    def test_no_startup_without_a_decoder_signature_is_a_route_failure(self):
        verdict, _ = re_mod.classify_playback(
            {
                "announced_render_tracks": ["video", "audio"],
                "progressing_tracks": [],
                "first_frame_seconds": None,
                "startup_seconds": None,
                "media_progress_seconds": 0.0,
                "cumulative_stall_seconds": 120,
                "fatal_errors": [],
                "recovered_to_pass_floor": False,
            }
        )
        self.assertEqual(verdict, re_mod.PLAYBACK_FAIL)
        self.assertTrue(re_mod.is_escalatable(verdict))

    def test_metrics_with_no_progress_field_are_still_unknown(self):
        # A genuinely absent measurement must stay `unknown`.
        self.assertEqual(re_mod.classify_playback({})[0], re_mod.UNKNOWN)
        self.assertEqual(
            re_mod.classify_playback({"startup_seconds": 2.0})[0], re_mod.UNKNOWN
        )


class ForbiddenMaterialTests(unittest.TestCase):
    """The credential check must not destroy legitimate reports.

    `flush()` withholds an audit that trips this check, so a false positive is
    not a harmless extra warning - it silently replaces a real report with an
    error stub. The plain 40-character rule did exactly that on a Phase 0
    sentence listing TV user agents.
    """

    def test_prose_with_slashes_is_not_a_credential(self):
        self.assertFalse(
            re_mod.evidence_contains_forbidden_material(
                {
                    "note": "the player special-cases "
                    "TV/AndroidTV/AFT/SmartTV/BRAVIA/MiBOX/TV Bro user agents"
                }
            )
        )

    def test_a_long_opaque_path_segment_is_still_caught(self):
        self.assertTrue(
            re_mod.evidence_contains_forbidden_material(
                {
                    "url": "https://h.example.net/live/"
                    "abc123DEF456ghi789JKL012mno345PQR678stu901vwx/index.m3u8"
                }
            )
        )

    def test_named_credential_parameters_are_still_caught(self):
        for field in ("token", "signature", "sig", "hdnts"):
            self.assertTrue(
                re_mod.evidence_contains_forbidden_material(
                    {"url": f"https://h.example.net/x.m3u8?{field}=abc"}
                ),
                field,
            )

    def test_headers_that_carry_credentials_are_still_caught(self):
        for blob in ("Authorization: Bearer x", "set-cookie: a=b", "bearer token"):
            self.assertTrue(
                re_mod.evidence_contains_forbidden_material({"h": blob}), blob
            )

    def test_the_committed_phase0_evidence_is_clean(self):
        import json as _json

        directory = ROOT / "the-new-latest-plan" / "phase0"
        if not directory.exists():
            self.skipTest("phase 0 evidence not present")
        found = list(directory.glob("*.json"))
        self.assertTrue(found, "phase 0 evidence directory is empty")
        for path in found:
            payload = _json.loads(path.read_text(encoding="utf-8"))
            self.assertFalse(
                re_mod.evidence_contains_forbidden_material(payload), str(path)
            )


class HmacKeyConfigurationTests(unittest.TestCase):
    """Adding the repository secret has to actually change the output.

    Nothing read a key from anywhere before this: `failure_domain_tenant` was
    unconditionally None, so setting the secret would have been inert while
    looking like it was configured.
    """

    def setUp(self):
        import os  # noqa: PLC0415

        self._os = os
        self._saved = {
            name: os.environ.get(name)
            for name in (re_mod.HMAC_KEY_ENV, re_mod.HMAC_KEY_ID_ENV)
        }

    def tearDown(self):
        for name, value in self._saved.items():
            if value is None:
                self._os.environ.pop(name, None)
            else:
                self._os.environ[name] = value

    def test_no_key_configured_is_a_supported_state(self):
        self._os.environ.pop(re_mod.HMAC_KEY_ENV, None)
        self.assertIsNone(re_mod.configured_hmac_key())
        # And it must not become a hide: unknown can never remove anything.
        domain = re_mod.failure_domain(
            "https://tenant.example.net/x.m3u8", re_mod.configured_hmac_key()
        )
        self.assertIsNone(domain["failure_domain_tenant"])

    def test_a_weak_key_is_treated_as_absent(self):
        # A short key looks like an identity while being trivially reversible,
        # which is worse than an honest `unknown`.
        self._os.environ[re_mod.HMAC_KEY_ENV] = "short"
        self.assertIsNone(re_mod.configured_hmac_key())

    def test_a_valid_key_produces_a_keyed_tenant_id(self):
        self._os.environ[re_mod.HMAC_KEY_ENV] = "k" * 32
        key = re_mod.configured_hmac_key()
        self.assertIsNotNone(key)
        domain = re_mod.failure_domain("https://tenant.example.net/x.m3u8", key)
        self.assertIsNotNone(domain["failure_domain_tenant"])

    def test_two_tenants_stay_distinct_under_a_key(self):
        self._os.environ[re_mod.HMAC_KEY_ENV] = "k" * 32
        key = re_mod.configured_hmac_key()
        first = re_mod.failure_domain("https://a.akamaized.net/x.m3u8", key)
        second = re_mod.failure_domain("https://b.akamaized.net/y.m3u8", key)
        self.assertNotEqual(
            first["failure_domain_tenant"], second["failure_domain_tenant"]
        )

    def test_the_key_id_is_recorded_for_rotation(self):
        self._os.environ[re_mod.HMAC_KEY_ID_ENV] = "key-2026-08"
        self.assertEqual(re_mod.configured_hmac_key_id(), "key-2026-08")

    def test_an_absent_key_id_is_none_not_a_placeholder(self):
        self._os.environ.pop(re_mod.HMAC_KEY_ID_ENV, None)
        self.assertIsNone(re_mod.configured_hmac_key_id())

    def test_the_workflow_passes_the_secret_through(self):
        # Without this the secret can be set in the repository and never reach
        # the scanner.
        workflow = (ROOT / ".github" / "workflows" / "scan.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn(re_mod.HMAC_KEY_ENV, workflow)
        self.assertIn(re_mod.HMAC_KEY_ID_ENV, workflow)


if __name__ == "__main__":
    unittest.main()
