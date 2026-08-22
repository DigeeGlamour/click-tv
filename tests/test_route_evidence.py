"""The visibility rules that protect working channels, locked down by tests.

Every case here exists because getting it wrong removes a channel a viewer can
actually watch, or publishes one nobody can. The scanner observes from a cloud
egress while viewers are in Bangladesh, so a negative reading here is routinely
a statement about the observer - a published, owner-working channel was measured
returning HTTP 403 three times from this vantage.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import route_evidence as re_mod  # noqa: E402


KEY = b"test-key-not-a-real-secret"


def _vantage(asn, provider):
    return {"asn": asn, "provider": provider}


def _record(**over):
    """A complete, escalatable, globally scoped observation."""
    base = {
        "route_id": "r1",
        "url_public_template": "{id}.example.net/live/{seg}/{file}",
        "url_registrable_domain": "example.net",
        "final_origin_public_template": "{id}.example.net/live/{seg}/{file}",
        "final_origin_registrable_domain": "example.net",
        "failure_domain_provider": "example.net",
        "failure_domain_tenant": "abcd1234",
        "delivery_path": "direct",
        "browser_profile": "Chromium-151/Linux",
        "test_vantage": _vantage("AS1", "provider-one"),
        "media_fingerprint": {"video": "h264"},
        "playback_metrics": {"media_progress_seconds": 0.0},
        "observed_at": "2026-08-22T10:00:00Z",
        "ttl": "2026-08-23T10:00:00Z",
        "verdict": re_mod.PLAYBACK_FAIL,
        "verdict_scope": "global",
    }
    base.update(over)
    return base


class SourceIdentityTests(unittest.TestCase):
    def test_identity_query_parameters_are_never_stripped(self):
        """22 published channels share one host+path and differ only by ?id=NNN.
        Dropping unknown parameters would fuse them into a single source."""
        a = re_mod.normalize_source_identity("https://h.example/live.m3u8?id=419")
        b = re_mod.normalize_source_identity("https://h.example/live.m3u8?id=420")
        self.assertNotEqual(a, b)

    def test_only_allowlisted_volatile_parameters_are_removed(self):
        with_token = re_mod.normalize_source_identity(
            "https://h.example/x.m3u8?id=7&token=abc&expires=99"
        )
        without = re_mod.normalize_source_identity("https://h.example/x.m3u8?id=7")
        self.assertEqual(with_token, without)

    def test_http_and_https_of_one_route_share_an_identity(self):
        self.assertEqual(
            re_mod.normalize_source_identity("http://h.example/a/b.m3u8"),
            re_mod.normalize_source_identity("https://h.example/a/b.m3u8"),
        )

    def test_two_delivery_attempts_of_one_source_count_once(self):
        routes = [
            {"url": "https://h.example/a.m3u8"},
            {"url": "https://h.example/a.m3u8"},
        ]
        self.assertEqual(re_mod.distinct_sources(routes), 1)


class RedactionTests(unittest.TestCase):
    def test_host_labels_beyond_the_provider_become_placeholders(self):
        """A stable account id was measured embedded in a redirect hostname."""
        template = re_mod.redact_public_template(
            "http://9900011.02244.provider.example/live/AbCdEfGhIj/KlMnOpQr/55501.ts"
        )
        self.assertNotIn("9900011", template)
        self.assertNotIn("AbCdEfGhIj", template)
        self.assertIn("provider.example", template)

    def test_signed_parameters_are_rendered_as_redacted(self):
        template = re_mod.redact_public_template("https://h.example/a.m3u8?token=SECRET")
        self.assertNotIn("SECRET", template)
        self.assertIn("{redacted}", template)

    def test_a_record_carrying_a_credential_is_rejected(self):
        bad = _record(url_public_template="https://h.example/a.ts?token=" + "A" * 60)
        self.assertTrue(re_mod.evidence_contains_forbidden_material(bad))

    def test_a_clean_record_is_accepted(self):
        self.assertFalse(re_mod.evidence_contains_forbidden_material(_record()))


class HmacTests(unittest.TestCase):
    def test_without_a_key_there_is_no_unkeyed_fallback(self):
        self.assertIsNone(re_mod.hmac_id("anything", None))

    def test_two_tenants_on_one_provider_get_different_ids(self):
        """Hashing the redacted form would have collided them into one."""
        a = re_mod.failure_domain("https://tenant-a.example.net/x.m3u8", KEY)
        b = re_mod.failure_domain("https://tenant-b.example.net/x.m3u8", KEY)
        self.assertEqual(a["failure_domain_provider"], b["failure_domain_provider"])
        self.assertNotEqual(a["failure_domain_tenant"], b["failure_domain_tenant"])


class TenantDerivationTests(unittest.TestCase):
    def test_a_generic_infra_label_leaves_tenancy_undetermined(self):
        """cache.devm3u.top is the backup host of several hidden channels."""
        tenant, method = re_mod.derive_tenant("cache.devm3u.top")
        self.assertIsNone(tenant)
        self.assertEqual(method, "generic_or_infra_sub_label")

    def test_a_bare_registrable_domain_leaves_tenancy_undetermined(self):
        tenant, method = re_mod.derive_tenant("proxpanel.example")
        self.assertIsNone(tenant)
        self.assertEqual(method, "no_sub_label")

    def test_a_real_sub_label_is_a_tenant(self):
        tenant, method = re_mod.derive_tenant("aajtaklive-amd.akamaized.example")
        self.assertEqual(tenant, "aajtaklive-amd")
        self.assertEqual(method, "host_sub_label")

    def test_undetermined_tenancy_makes_redundancy_unknown_not_zero_or_one(self):
        routes = [{"url": "https://cache.devm3u.example/a.m3u8"}]
        self.assertEqual(re_mod.independent_redundancy(routes, KEY), re_mod.UNKNOWN)

    def test_two_tenants_of_one_provider_are_partial_independence(self):
        routes = [
            {"url": "https://ten-a.akamaized.example/a.m3u8"},
            {"url": "https://ten-b.akamaized.example/b.m3u8"},
        ]
        self.assertEqual(re_mod.independent_redundancy(routes, KEY), 2)


class TransportClassificationTests(unittest.TestCase):
    def test_geo_and_rate_limit_statuses_are_vantage_not_failure(self):
        for status in (401, 403, 429, 451):
            self.assertEqual(
                re_mod.classify_transport(status), re_mod.ADVISORY_VANTAGE_BLOCKED
            )

    def test_server_side_and_cloudflare_5xx_are_transient(self):
        for status in (408, 500, 502, 503, 504, 520, 522, 524, 527):
            self.assertEqual(
                re_mod.classify_transport(status), re_mod.ADVISORY_TRANSIENT_NETWORK
            )

    def test_tls_and_reset_are_transient(self):
        for kind in ("tls_failure", "connection_reset", "read_timeout"):
            self.assertEqual(
                re_mod.classify_transport(error_kind=kind),
                re_mod.ADVISORY_TRANSIENT_NETWORK,
            )

    def test_dns_and_connect_timeout_are_vantage(self):
        for kind in ("dns", "connect_timeout"):
            self.assertEqual(
                re_mod.classify_transport(error_kind=kind),
                re_mod.ADVISORY_VANTAGE_BLOCKED,
            )

    def test_http_200_is_never_success(self):
        """Four routes stored as browser failures answer 200 with HTML."""
        self.assertEqual(re_mod.classify_transport(200, content_type="text/html"), re_mod.UNKNOWN)
        self.assertEqual(
            re_mod.classify_transport(200, content_type="application/vnd.apple.mpegurl"),
            re_mod.UNKNOWN,
        )


class EscalatabilityTests(unittest.TestCase):
    def test_only_transient_persistent_and_playback_fail_escalate(self):
        self.assertTrue(re_mod.is_escalatable(re_mod.ADVISORY_TRANSIENT_NETWORK))
        self.assertTrue(re_mod.is_escalatable(re_mod.PERSISTENT_UNAVAILABLE_CANDIDATE))
        self.assertTrue(re_mod.is_escalatable(re_mod.PLAYBACK_FAIL))

    def test_vantage_device_and_structural_never_escalate(self):
        """Repetition of these says nothing new about the route."""
        for verdict in (
            re_mod.ADVISORY_VANTAGE_BLOCKED,
            re_mod.ADVISORY_DEVICE_UNSUPPORTED,
            re_mod.ADVISORY_STRUCTURALLY_RISKY,
            re_mod.ADVISORY_DOMAIN_EVENT,
        ):
            self.assertFalse(re_mod.is_escalatable(verdict))

    def test_unknown_never_escalates(self):
        self.assertFalse(re_mod.is_escalatable(re_mod.UNKNOWN))


class PlaybackAcceptanceTests(unittest.TestCase):
    def _metrics(self, **over):
        base = {
            "first_frame_seconds": 2.0,
            "startup_seconds": 3.0,
            "media_progress_seconds": 118.0,
            "cumulative_stall_seconds": 1.0,
            "fatal_errors": [],
            "announced_render_tracks": ["video", "audio"],
            "progressing_tracks": ["video", "audio"],
        }
        base.update(over)
        return base

    def test_a_clean_120s_observation_passes(self):
        verdict, _ = re_mod.classify_playback(self._metrics())
        self.assertEqual(verdict, re_mod.PROVEN)

    def test_an_audio_only_channel_can_pass(self):
        """QURAN RADIOTV SMC is audio-only; requiring audio+video would have
        made it permanently unpassable."""
        verdict, _ = re_mod.classify_playback(
            self._metrics(announced_render_tracks=["audio"], progressing_tracks=["audio"])
        )
        self.assertEqual(verdict, re_mod.PROVEN)

    def test_an_idle_announced_data_stream_does_not_block_a_pass(self):
        verdict, _ = re_mod.classify_playback(
            self._metrics(
                announced_render_tracks=["video", "audio"],
                progressing_tracks=["video", "audio"],
            )
        )
        self.assertEqual(verdict, re_mod.PROVEN)

    def test_one_dead_announced_render_track_is_a_failure(self):
        verdict, reasons = re_mod.classify_playback(
            self._metrics(progressing_tracks=["video"])
        )
        self.assertEqual(verdict, re_mod.PLAYBACK_FAIL)
        self.assertTrue(any("audio" in r for r in reasons))

    def test_slow_startup_is_ambiguous_not_a_pass_and_not_a_fail(self):
        verdict, _ = re_mod.classify_playback(self._metrics(startup_seconds=18.0))
        self.assertEqual(verdict, re_mod.UNKNOWN)

    def test_excess_stall_is_ambiguous(self):
        verdict, _ = re_mod.classify_playback(self._metrics(cumulative_stall_seconds=9.0))
        self.assertEqual(verdict, re_mod.UNKNOWN)

    def test_partial_progress_is_ambiguous(self):
        verdict, _ = re_mod.classify_playback(self._metrics(media_progress_seconds=60.0))
        self.assertEqual(verdict, re_mod.UNKNOWN)

    def test_no_first_frame_within_thirty_seconds_fails(self):
        verdict, _ = re_mod.classify_playback(
            self._metrics(first_frame_seconds=None, media_progress_seconds=0.0)
        )
        self.assertEqual(verdict, re_mod.PLAYBACK_FAIL)

    def test_a_recovered_fatal_error_is_ambiguous_not_a_fail(self):
        verdict, _ = re_mod.classify_playback(
            self._metrics(fatal_errors=["MediaMSEError"], recovered_to_pass_floor=True)
        )
        self.assertEqual(verdict, re_mod.UNKNOWN)

    def test_an_unrecovered_fatal_error_fails(self):
        verdict, _ = re_mod.classify_playback(
            self._metrics(fatal_errors=["MEDIA_ERR_DECODE"], media_progress_seconds=0.0)
        )
        self.assertEqual(verdict, re_mod.PLAYBACK_FAIL)

    def test_missing_metrics_are_unknown(self):
        self.assertEqual(re_mod.classify_playback({})[0], re_mod.UNKNOWN)

    def test_one_decoded_frame_is_not_a_pass(self):
        verdict, _ = re_mod.classify_playback(
            self._metrics(media_progress_seconds=0.5, first_frame_seconds=1.0)
        )
        self.assertEqual(verdict, re_mod.PLAYBACK_FAIL)


class VerdictScopeTests(unittest.TestCase):
    def test_a_device_codec_limitation_is_environment_scoped(self):
        """HEVC unsupported in the tested Chromium says nothing about Android."""
        scope = re_mod.resolve_verdict_scope(
            re_mod.ADVISORY_DEVICE_UNSUPPORTED, browser_profile="Chromium-151/Linux"
        )
        self.assertTrue(scope.startswith("environment:"))
        self.assertNotEqual(scope, "global")

    def test_a_vantage_block_is_vantage_scoped(self):
        scope = re_mod.resolve_verdict_scope(
            re_mod.ADVISORY_VANTAGE_BLOCKED, vantage_id="cloud-a"
        )
        self.assertTrue(scope.startswith("vantage:"))

    def test_two_arbitrary_profiles_do_not_earn_global_scope(self):
        scope = re_mod.resolve_verdict_scope(
            re_mod.PLAYBACK_FAIL,
            browser_profile="Chromium-151/Linux",
            declared_matrix=("desktop", "android", "iphone", "androidtv"),
            failed_profiles=("desktop", "android"),
        )
        self.assertNotEqual(scope, "global")

    def test_the_complete_declared_matrix_earns_global_scope(self):
        matrix = ("desktop", "android", "iphone", "androidtv")
        scope = re_mod.resolve_verdict_scope(
            re_mod.PLAYBACK_FAIL, declared_matrix=matrix, failed_profiles=matrix
        )
        self.assertEqual(scope, "global")

    def test_an_environment_independent_failure_is_global(self):
        scope = re_mod.resolve_verdict_scope(
            re_mod.PERSISTENT_UNAVAILABLE_CANDIDATE, environment_independent=True
        )
        self.assertEqual(scope, "global")


class VantageIndependenceTests(unittest.TestCase):
    def test_two_workers_on_one_provider_account_are_not_independent(self):
        """All four configured play proxies share one provider account."""
        a = _vantage("AS13335", "cloudflare")
        b = _vantage("AS13335", "cloudflare")
        self.assertFalse(re_mod.vantages_are_independent(a, b))

    def test_different_asn_and_provider_are_independent(self):
        self.assertTrue(
            re_mod.vantages_are_independent(
                _vantage("AS8075", "microsoft"), _vantage("AS199458", "other")
            )
        )

    def test_an_unmeasured_vantage_is_never_independent(self):
        self.assertFalse(
            re_mod.vantages_are_independent(_vantage("", ""), _vantage("AS1", "p"))
        )


class MayHideGuardTests(unittest.TestCase):
    """The single decision point. These are the channel-loss regressions."""

    def test_a_visible_channel_is_never_hidden_on_a_vantage_block(self):
        allowed, reason = re_mod.may_hide(
            state=re_mod.EXISTING_VISIBLE,
            evidence=[
                _record(verdict=re_mod.ADVISORY_VANTAGE_BLOCKED, verdict_scope="vantage:a"),
                _record(verdict=re_mod.ADVISORY_VANTAGE_BLOCKED, verdict_scope="vantage:b"),
            ],
        )
        self.assertFalse(allowed)
        self.assertIn("escalatable", reason)

    def test_a_visible_channel_is_never_hidden_on_transient_alone(self):
        allowed, _ = re_mod.may_hide(
            state=re_mod.EXISTING_VISIBLE,
            evidence=[_record(verdict=re_mod.ADVISORY_TRANSIENT_NETWORK)],
        )
        self.assertFalse(allowed)

    def test_a_visible_channel_is_never_hidden_on_unknown(self):
        allowed, _ = re_mod.may_hide(
            state=re_mod.EXISTING_VISIBLE,
            evidence=[_record(verdict=re_mod.UNKNOWN), _record(verdict=re_mod.UNKNOWN)],
        )
        self.assertFalse(allowed)

    def test_a_visible_channel_with_no_evidence_at_all_stays_visible(self):
        allowed, _ = re_mod.may_hide(state=re_mod.EXISTING_VISIBLE, evidence=[])
        self.assertFalse(allowed)

    def test_a_healthy_sibling_source_blocks_hiding_the_channel(self):
        """Channel 24 holds a backup while its primary is marked failed."""
        allowed, reason = re_mod.may_hide(
            state=re_mod.EXISTING_VISIBLE,
            evidence=[
                _record(observed_at="t1", test_vantage=_vantage("AS1", "p1")),
                _record(observed_at="t2", test_vantage=_vantage("AS2", "p2")),
            ],
            healthy_sibling_sources=1,
        )
        self.assertFalse(allowed)
        self.assertIn("working route", reason)

    def test_incomplete_evidence_cannot_hide(self):
        allowed, _ = re_mod.may_hide(
            state=re_mod.EXISTING_VISIBLE,
            evidence=[
                _record(observed_at="t1", test_vantage=None),
                _record(observed_at="t2", ttl=None),
            ],
        )
        self.assertFalse(allowed)

    def test_one_time_window_is_not_two(self):
        allowed, reason = re_mod.may_hide(
            state=re_mod.EXISTING_VISIBLE,
            evidence=[
                _record(observed_at="same", test_vantage=_vantage("AS1", "p1")),
                _record(observed_at="same", test_vantage=_vantage("AS2", "p2")),
            ],
        )
        self.assertFalse(allowed)
        self.assertIn("one time window", reason)

    def test_two_dependent_vantages_are_not_two_vantages(self):
        allowed, reason = re_mod.may_hide(
            state=re_mod.EXISTING_VISIBLE,
            evidence=[
                _record(observed_at="t1", test_vantage=_vantage("AS1", "same")),
                _record(observed_at="t2", test_vantage=_vantage("AS1", "same")),
            ],
        )
        self.assertFalse(allowed)
        self.assertIn("independent", reason)

    def test_environment_scoped_failure_never_hides_globally(self):
        allowed, _ = re_mod.may_hide(
            state=re_mod.EXISTING_VISIBLE,
            evidence=[
                _record(observed_at="t1", verdict_scope="environment:Chromium-151/Linux",
                        test_vantage=_vantage("AS1", "p1")),
                _record(observed_at="t2", verdict_scope="environment:Chromium-151/Linux",
                        test_vantage=_vantage("AS2", "p2")),
            ],
        )
        self.assertFalse(allowed)

    def test_the_full_bar_does_allow_hiding(self):
        """The escalation path must actually exist, or a dead route could never
        be retired."""
        allowed, reason = re_mod.may_hide(
            state=re_mod.EXISTING_VISIBLE,
            evidence=[
                _record(observed_at="t1", test_vantage=_vantage("AS1", "p1")),
                _record(observed_at="t2", test_vantage=_vantage("AS2", "p2")),
            ],
        )
        self.assertTrue(allowed)
        self.assertIn("two independent vantages", reason)

    def test_a_legacy_hidden_record_is_not_auto_unhidden(self):
        allowed, _ = re_mod.may_hide(state=re_mod.LEGACY_HIDDEN, evidence=[])
        self.assertTrue(allowed)

    def test_grandfathered_records_keep_their_visibility(self):
        allowed, reason = re_mod.may_hide(
            state=re_mod.EXISTING_VISIBLE, grandfathered=True
        )
        self.assertTrue(allowed)
        self.assertIn("visibility unchanged", reason)

    def test_a_new_unproven_item_is_not_published(self):
        allowed, _ = re_mod.may_hide(state=re_mod.NEW_UNPROVEN, evidence=[])
        self.assertTrue(allowed)


class ThreeStateTests(unittest.TestCase):
    def test_published_items_are_existing_visible(self):
        self.assertEqual(
            re_mod.three_state(is_published=True, is_legacy_hidden=False),
            re_mod.EXISTING_VISIBLE,
        )

    def test_hidden_items_are_legacy_hidden(self):
        self.assertEqual(
            re_mod.three_state(is_published=False, is_legacy_hidden=True),
            re_mod.LEGACY_HIDDEN,
        )

    def test_unseen_items_are_new_unproven(self):
        self.assertEqual(
            re_mod.three_state(is_published=False, is_legacy_hidden=False),
            re_mod.NEW_UNPROVEN,
        )


class BrowserConfirmationGuardTests(unittest.TestCase):
    """The measured Channel 24 bug: three links, one route attempted, whole
    channel hidden."""

    def _conf(self, **over):
        base = {
            "kind": "channel",
            "name": "Some TV",
            "ok": False,
            "reason": "no_decoded_frame",
            "mediaError": "",
            "session": {"planLength": 1, "attemptsRun": 1, "currentRoute": "direct"},
        }
        base.update(over)
        return base

    def test_untested_alternative_sources_block_the_hide(self):
        allowed, reason = re_mod.may_hide_from_browser_confirmation(
            confirmation=self._conf(), distinct_source_count=3
        )
        self.assertFalse(allowed)
        self.assertIn("untested", reason)

    def test_a_single_source_channel_fully_attempted_may_still_be_hidden(self):
        allowed, _ = re_mod.may_hide_from_browser_confirmation(
            confirmation=self._conf(), distinct_source_count=1
        )
        self.assertTrue(allowed)

    def test_an_exhausted_plan_covering_every_source_allows_the_hide(self):
        allowed, _ = re_mod.may_hide_from_browser_confirmation(
            confirmation=self._conf(session={"planLength": 3, "attemptsRun": 3}),
            distinct_source_count=3,
        )
        self.assertTrue(allowed)

    def test_an_abandoned_plan_blocks_the_hide(self):
        """Measured: Ekattor TV ran 4 of 8 planned attempts across 3 sources.
        Counts alone cannot show the third source was ever tried."""
        allowed, reason = re_mod.may_hide_from_browser_confirmation(
            confirmation=self._conf(session={"planLength": 8, "attemptsRun": 4}),
            distinct_source_count=3,
        )
        self.assertFalse(allowed)
        self.assertIn("abandoned", reason)

    def test_a_vantage_explainable_failure_blocks_the_hide(self):
        for marker in ("403", "forbidden", "geo blocked", "dns"):
            allowed, reason = re_mod.may_hide_from_browser_confirmation(
                confirmation=self._conf(reason=marker), distinct_source_count=1
            )
            self.assertFalse(allowed, marker)
            self.assertIn("vantage-explainable", reason)

    def test_a_transient_failure_blocks_the_hide(self):
        for marker in ("503", "522", "tls handshake", "econnreset"):
            allowed, reason = re_mod.may_hide_from_browser_confirmation(
                confirmation=self._conf(reason=marker), distinct_source_count=1
            )
            self.assertFalse(allowed, marker)
            self.assertIn("transient-explainable", reason)

    def test_multiple_sources_with_no_attempt_record_block_the_hide(self):
        allowed, reason = re_mod.may_hide_from_browser_confirmation(
            confirmation=self._conf(session={}), distinct_source_count=2
        )
        self.assertFalse(allowed)
        self.assertIn("does not record", reason)

    def test_a_missing_confirmation_never_hides(self):
        allowed, _ = re_mod.may_hide_from_browser_confirmation(
            confirmation=None, distinct_source_count=1
        )
        self.assertFalse(allowed)


if __name__ == "__main__":
    unittest.main()
