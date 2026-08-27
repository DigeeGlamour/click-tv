"""The committed registry, and the three-state health policy that reads it.

Codex found the registry inert in production while every synthetic test passed,
which is the failure mode this file exists to prevent. The chain was:

  the only entry in state/route-preference.json predates the recorded_at field
    -> _is_stale() reads a timestamp-less entry as stale
      -> preferred_route_id("channel", "Zee Bangla") returns None
        -> the registry names a proven route and cannot act on it

Every test that covered promotion built its own registry with rp.record(),
which always writes recorded_at, so the committed data was the one input
nothing looked at. Several tests here therefore read the real file on purpose.

The second half covers route_health(). A boolean "is it healthy" forced every
negative to be read as final, and from this vantage most are not: a datacentre
egress sees a Bangladesh-only route as 403 and a dead Cloudflare worker in
front of a working upstream as 530.
"""
import datetime as dt
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import merger  # noqa: E402
from scanner import route_preference as rp  # noqa: E402

LIVE_REGISTRY = ROOT / "state" / "route-preference.json"


def _live() -> dict:
    with open(LIVE_REGISTRY, "r", encoding="utf-8") as handle:
        return json.load(handle)


class CommittedRegistryTests(unittest.TestCase):
    """The registry as committed, not a synthetic one."""

    def test_every_committed_entry_carries_a_recorded_at(self):
        missing = [
            key
            for key, entry in (_live().get("preferred") or {}).items()
            if not str((entry or {}).get("recorded_at") or "").strip()
        ]
        self.assertEqual(
            missing, [], f"entries with no recorded_at read as stale: {missing}"
        )

    def test_every_committed_entry_is_actually_active(self):
        """The whole point: an entry that cannot be looked up is dead weight."""
        for key, entry in (_live().get("preferred") or {}).items():
            got = rp.preferred_route_id(
                entry.get("kind") or "channel", entry.get("channel") or ""
            )
            self.assertEqual(
                got,
                entry.get("route_id"),
                f"{key} is in the registry but preferred_route_id returns {got!r}",
            )

    def test_the_zee_bangla_entry_resolves_to_its_proven_route(self):
        entry = (_live().get("preferred") or {}).get("channel|zee bangla")
        self.assertIsNotNone(entry, "the Zee Bangla preference is missing")
        self.assertEqual(entry["pass_count"], 2)
        self.assertEqual(
            rp.preferred_route_id("channel", "Zee Bangla"), entry["route_id"]
        )

    def test_the_migrated_stamp_survives_as_retained_evidence(self):
        """The legacy entry is kept, not discarded, and keeps its real date.

        The Zee Bangla preference has since been re-recorded against a route
        that answers today, so the live entry legitimately carries a fresh
        timestamp. The migrated one moved into `superseded`, which is where the
        no-fake-refresh rule now has to hold: a proof that was measured on
        2026-08-24 must not read as measured later.
        """
        entry = (_live().get("preferred") or {}).get("channel|zee bangla") or {}
        superseded = entry.get("superseded") or []
        self.assertTrue(superseded, "the previous proof was dropped rather than retained")
        for old in superseded:
            recorded = dt.datetime.fromisoformat(old["recorded_at"])
            if recorded.tzinfo is None:
                recorded = recorded.replace(tzinfo=dt.timezone.utc)
            age = (dt.datetime.now(dt.timezone.utc) - recorded).total_seconds()
            self.assertGreater(
                age, 3600,
                "a retained proof carrying a fresh stamp has been silently renewed",
            )
            self.assertTrue(old.get("why_superseded"))

    def test_a_superseded_route_is_retained_not_deleted(self):
        """A vantage-shaped negative must not cost us the record.

        The route in `superseded` returns 530 because the front in front of it
        is down. Deleting the proof on that basis would mean re-earning it from
        scratch if the front recovers.
        """
        entry = (_live().get("preferred") or {}).get("channel|zee bangla") or {}
        old = (entry.get("superseded") or [{}])[0]
        self.assertTrue(old.get("route_id"))
        self.assertEqual(old.get("pass_count"), 2)
        self.assertNotEqual(old.get("route_id"), entry.get("route_id"))

    def test_every_stamp_is_backed_by_an_evidence_report_that_exists(self):
        """Stops a stamp being written without the measurement behind it."""
        for key, entry in (_live().get("preferred") or {}).items():
            report = str((entry or {}).get("evidence_report") or "")
            self.assertTrue(report, f"{key} cites no evidence report")
            self.assertTrue(
                (ROOT / report).exists(), f"{key} cites a missing report: {report}"
            )

    def test_a_genuinely_expired_proof_falls_back_to_ordinary_ranking(self):
        """The TTL still bites - migration did not disable it."""
        entry = dict((_live().get("preferred") or {}).get("channel|zee bangla") or {})
        entry.pop("superseded", None)
        registry = {"preferred": {"channel|zee bangla": entry}}
        recorded = dt.datetime.fromisoformat(entry["recorded_at"]).timestamp()
        just_inside = recorded + rp.PREFERENCE_TTL_SECONDS - 60
        just_outside = recorded + rp.PREFERENCE_TTL_SECONDS + 60
        self.assertEqual(
            rp.preferred_route_id("channel", "Zee Bangla", registry, now=just_inside),
            entry["route_id"],
        )
        self.assertIsNone(
            rp.preferred_route_id("channel", "Zee Bangla", registry, now=just_outside)
        )

    def test_the_registry_still_stores_no_raw_url(self):
        blob = json.dumps(_live())
        self.assertNotIn("http://", blob)
        self.assertNotIn("https://", blob)


class RecordWritesTimestampTests(unittest.TestCase):
    def test_a_new_record_always_carries_recorded_at(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "pref.json")
            ok, _why = rp.record(
                "channel",
                "New Ch",
                "https://x.example.net/live/index.m3u8",
                {"pass_count": 2, "window_seconds": 120.0},
                path=path,
            )
            self.assertTrue(ok)
            entry = rp.load(path)["preferred"]["channel|new ch"]
            self.assertTrue(entry["recorded_at"])
            self.assertFalse(rp._is_stale(entry))


class ThreeStateHealthTests(unittest.TestCase):
    """Each transition the policy has to get right, one test each."""

    @staticmethod
    def _s(**fields):
        base = {"url": "https://x.example.net/live/index.m3u8"}
        base.update(fields)
        return base

    def test_healthy_a_route_this_scan_verified_may_lead(self):
        for status in sorted(rp.POSITIVE_STATUSES):
            state, _why = rp.route_health(self._s(verification_status=status))
            self.assertEqual(state, rp.HEALTH_HEALTHY, status)
            self.assertTrue(rp.is_promotable(self._s(verification_status=status))[0])

    def test_hard_failed_a_route_that_is_gone_may_not_lead(self):
        for code in sorted(rp.HARD_HTTP_STATUSES):
            state, why = rp.route_health(self._s(http_status=code))
            self.assertEqual(state, rp.HEALTH_HARD_FAILED, code)
            self.assertIn(str(code), why)
            self.assertFalse(rp.is_promotable(self._s(http_status=code))[0])

    def test_hard_failed_a_route_measured_unplayable_may_not_lead(self):
        state, why = rp.route_health(self._s(verdict="playback_fail"))
        self.assertEqual(state, rp.HEALTH_HARD_FAILED)
        self.assertIn("unplayable", why)

    def test_geo_inconclusive_is_not_treated_as_dead(self):
        """The case that matters most: this is how a channel gets lost.

        403 and 451 from a datacentre egress are about the asker. Reading them
        as "route dead" would discard routes that work for the audience the
        site is built for.
        """
        for code in (403, 451, 530):
            state, why = rp.route_health(
                self._s(http_status=code, verification_status="failed")
            )
            self.assertEqual(state, rp.HEALTH_INCONCLUSIVE, code)
            self.assertIn("vantage", why)
            allowed, _r = rp.is_promotable(
                self._s(http_status=code, verification_status="failed")
            )
            self.assertTrue(allowed, f"HTTP {code} must not cost a proven route")

    def test_geo_pending_statuses_stay_promotable(self):
        for status in sorted(rp.INCONCLUSIVE_STATUSES):
            state, _why = rp.route_health(
                self._s(verification_status=status, publish_allowed=True)
            )
            self.assertEqual(state, rp.HEALTH_INCONCLUSIVE, status)

    def test_an_unpublishable_route_never_leads_whatever_we_believe(self):
        """Health and promotability are separate, and this is why.

        A route the pipeline will not serve cannot be primary even when the
        negative behind it is inconclusive - the card would point at something
        the site refuses to deliver.
        """
        item = self._s(verification_status="geo_pending", publish_allowed=False)
        self.assertEqual(rp.route_health(item)[0], rp.HEALTH_INCONCLUSIVE)
        allowed, why = rp.is_promotable(item)
        self.assertFalse(allowed)
        self.assertIn("unpublishable", why)

    def test_nothing_to_promote_is_hard_failed_not_inconclusive(self):
        self.assertEqual(
            rp.route_health({"url": ""})[0], rp.HEALTH_HARD_FAILED
        )
        self.assertEqual(
            rp.route_health(self._s(metadata_only=True))[0], rp.HEALTH_HARD_FAILED
        )

    def test_a_non_escalatable_verdict_is_named_not_hidden(self):
        state, why = rp.route_health(
            self._s(verdict="advisory:device_or_browser_unsupported")
        )
        self.assertEqual(state, rp.HEALTH_INCONCLUSIVE)
        self.assertIn("advisory:device_or_browser_unsupported", why)

    def test_recovery_a_route_that_comes_back_is_eligible_again(self):
        """The policy holds no memory of a bad scan, by design."""
        url = "https://x.example.net/live/index.m3u8"
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "pref.json")
            rp.record(
                "channel", "Rec", url, {"pass_count": 2, "window_seconds": 120.0},
                path=path,
            )
            registry = rp.load(path)
            broken = {"url": url, "http_status": 404}
            healthy = {"url": url, "verification_status": "verified_global"}
            other = {"url": "https://other.example.net/live/x.m3u8"}

            got, promoted = rp.promote_preferred(
                [other, broken], "channel", "Rec", registry
            )
            self.assertFalse(promoted, "a route returning 404 must not be promoted")
            self.assertEqual(got[0], other)

            got, promoted = rp.promote_preferred(
                [other, healthy], "channel", "Rec", registry
            )
            self.assertTrue(promoted, "the same route, working again, must lead")
            self.assertEqual(got[0], healthy)


class RealRankingTests(unittest.TestCase):
    """Point 4 through the actual ranking entry point, not a stand-in."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self._tmp.name) / "pref.json")
        self.proven = "https://proven.example.net/live/index.m3u8"
        rp.record(
            "channel", "Seven", self.proven,
            {"pass_count": 2, "window_seconds": 120.0}, path=self.path,
        )
        self._real_load = rp.load
        rp.load = lambda path=None: self._real_load(self.path)
        self.addCleanup(setattr, rp, "load", self._real_load)
        self.addCleanup(self._tmp.cleanup)

    def _pool(self, n=7):
        pool = [
            {
                "url": f"https://rival{i}.example.net/live/x.m3u8",
                "verification_status": "verified_global",
                "verified": True,
                "publish_allowed": True,
                "stream_type": "hls",
                "source_pipeline": "tv",
            }
            for i in range(n - 1)
        ]
        pool.append({
            "url": self.proven,
            "verification_status": "verified_global",
            "verified": True,
            "publish_allowed": True,
            "stream_type": "hls",
            "source_pipeline": "tv",
        })
        return pool

    def test_the_proven_seventh_candidate_becomes_primary(self):
        primary, backups = merger.rank_and_select_streams(
            self._pool(7), max_total=6, channel_name="Seven", channel_kind="channel"
        )
        self.assertEqual(primary["url"], self.proven)
        self.assertLessEqual(1 + len(backups), 6, "slot limit exceeded")

    def test_promotion_invents_no_candidate_and_loses_no_input_host(self):
        pool = self._pool(7)
        primary, backups = merger.rank_and_select_streams(
            pool, max_total=6, channel_name="Seven", channel_kind="channel"
        )
        chosen = {primary["url"]} | {b["url"] for b in backups}
        self.assertTrue(
            chosen.issubset({s["url"] for s in pool}),
            "ranking returned a URL that was not among the candidates",
        )

    def test_an_unrelated_channel_is_ranked_exactly_as_before(self):
        pool = self._pool(7)
        with_pref = merger.rank_and_select_streams(
            list(pool), max_total=6, channel_name="Seven", channel_kind="channel"
        )
        without = merger.rank_and_select_streams(
            list(pool), max_total=6, channel_name="Unrelated", channel_kind="channel"
        )
        self.assertEqual(with_pref[0]["url"], self.proven)
        self.assertNotEqual(
            without[0]["url"], self.proven,
            "a channel with no preference must not inherit another channel's",
        )

    def test_a_hard_failed_proven_route_does_not_take_primary(self):
        """The health policy alone must stop it.

        The route is left publishable on purpose: if it were marked failed the
        publish gate would drop it first and this test would pass without
        route_health being consulted at all.
        """
        pool = self._pool(7)
        pool[-1] = dict(pool[-1], verdict="playback_fail")
        primary, _backups = merger.rank_and_select_streams(
            pool, max_total=6, channel_name="Seven", channel_kind="channel"
        )
        self.assertNotEqual(primary["url"], self.proven)

    def test_incumbent_hold_does_not_undo_the_promotion(self):
        pool = self._pool(7)
        primary, _backups = merger.rank_and_select_streams(
            pool, max_total=6,
            previous_primary_identity="https://rival0.example.net/live/x.m3u8",
            channel_name="Seven", channel_kind="channel",
        )
        self.assertEqual(primary["url"], self.proven)


class AuditModeTruthfulnessTests(unittest.TestCase):
    """Point 7: the report has to describe what the code does."""

    def test_the_written_audit_does_not_call_itself_advisory(self):
        from scanner import visibility_audit as va

        va.reset()
        va.audit_hide("unit.test", {"url": "https://x.example.net/a.m3u8"})
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "audit.json")
            va.flush(target, provenance="unit test")
            payload = json.loads(Path(target).read_text(encoding="utf-8"))
        va.reset()
        self.assertEqual(payload["mode"], "conditional_enforcement")
        self.assertTrue(payload["enforcement"]["enforced"])
        self.assertNotIn("Advisory", payload["note"])
        self.assertNotIn("No value here changed", payload["note"])

    def test_the_committed_audit_report_is_not_still_claiming_audit_only(self):
        """Engages as soon as a scan runs with the fixed writer.

        The committed report is written by CI, so right after this change it is
        still the old artifact. Rather than assert against a file this commit
        cannot regenerate honestly - re-running the scan locally would stamp it
        with a local test key and misdescribe its own provenance - the check
        keys off the `enforcement` block. Absent means the report predates the
        writer; present means it was written by it and must be truthful.
        """
        path = ROOT / "reports" / "visibility-model-audit.json"
        if not path.exists():
            self.skipTest("no audit report committed")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if "enforcement" not in payload:
            self.skipTest(
                "the committed report predates the truthful writer; the next "
                "scan regenerates it"
            )
        self.assertEqual(payload.get("mode"), "conditional_enforcement")
        self.assertTrue(payload["enforcement"]["enforced"])


class DeviceClaimWordingTests(unittest.TestCase):
    """Point 8: emulation is not a physical device, and reports must say so."""

    REPORTS = sorted((ROOT / "reports").glob("zee-device-*.json"))

    def test_a_device_report_names_the_engine_it_actually_drove(self):
        if not self.REPORTS:
            self.skipTest("no device reports committed")
        for path in self.REPORTS:
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn(
                payload.get("browser_engine"), {"chromium", "webkit", "firefox"},
                f"{path.name} does not record which engine ran",
            )

    def test_the_device_reports_span_two_engines_not_three(self):
        """Guards a specific wrong claim rather than a general one.

        A report of these runs said "three different engines". There are two:
        android_chrome and android_tv are both Chromium. Overstating engine
        coverage overstates how independent the six sessions were.
        """
        if not self.REPORTS:
            self.skipTest("no device reports committed")
        engines = {
            json.loads(p.read_text(encoding="utf-8")).get("browser_engine")
            for p in self.REPORTS
        }
        self.assertEqual(engines, {"chromium", "webkit"})

    def test_a_device_report_discloses_that_it_is_emulation(self):
        """The artifact must say what it is, not rely on the reader knowing.

        A file called zee-device-iphone_safari.json invites exactly one wrong
        reading. Prose in a report can be edited away; this pins the disclosure
        to the measurement that produced it.
        """
        if not self.REPORTS:
            self.skipTest("no device reports committed")
        for path in self.REPORTS:
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertTrue(payload.get("not_a_physical_device"), path.name)
            self.assertIn("emulation", str(payload.get("measurement_kind")))
            limits = str(payload.get("emulation_limits") or "")
            self.assertIn("physical hardware", limits, path.name)
            self.assertIn("WebKit is not iOS Safari", limits, path.name)

    def test_the_webkit_limitation_is_recorded_alongside_its_verdict(self):
        """A FAIL from a harness that cannot use the real path is not a finding.

        The iphone_safari profile returns playback_fail on a route three
        Chromium profiles proved. The cause was measured rather than guessed:
        Playwright's WebKit reports no native Apple HLS support and refuses
        MPEG-TS through MediaSource, so the path real iOS Safari would take for
        this route does not exist in it. Without this artifact beside the
        verdict, a later reader would reasonably record "Zee Bangla fails on
        iPhone", which the measurement does not support.
        """
        probe = ROOT / "reports" / "harness-media-capability.json"
        if not probe.exists():
            self.skipTest("no capability probe recorded")
        payload = json.loads(probe.read_text(encoding="utf-8"))
        webkit = (payload.get("engines") or {}).get("webkit") or {}
        self.assertEqual(
            webkit.get("native_apple_hls"), "",
            "this WebKit build now reports native HLS - re-read the iphone "
            "verdict before trusting either",
        )
        self.assertFalse(webkit.get("mse_mpegts"))
        self.assertIn("says nothing about iOS Safari", payload.get("conclusion", ""))

    # A prose-scanning test used to live here and it was wrong twice.
    #
    # It searched tracked reports for phrases like "real Android" and
    # "physical device tested". The first version flagged a sentence stating
    # what a physical-device run WOULD require - an honest statement of a
    # limit. The narrowed version then flagged the report's own line saying
    # that no report claims "physical device tested", and broke CI: the test
    # could not tell a claim from a description of the rule itself.
    #
    # Prose about limits necessarily quotes the overclaim it rules out, so a
    # regex over prose cannot separate the two. The disclosure now lives where
    # a machine can check it without reading intent - the fields above, in the
    # measurement artifact itself. Wording stays a human review matter.


if __name__ == "__main__":
    unittest.main()
