"""ধাপ ৭ / S-01 - when a link actually needs checking again.

The plan calls this the single largest speed win, and the measurement behind it
is plain: 2,475 links are re-checked every night in a 40-minute budget, the
budget runs out, and 390 of them (23%) sit in `stale_last_good` having never
been reached. The scanner is not short of workers - it spends its whole budget
redoing yesterday's work.

Three rules carry the most weight here:

  * **a link with no record is always due**, so the first run after this ships
    verifies exactly what it verifies today and the state seeds itself from
    real observations rather than from an assumption;
  * **a carried item is still published** - removed from the probe queue, never
    from the run, or the no-loss gate would rightly block the publish;
  * **repeated failure from our vantage is never `dead`**. 404 and 410 are the
    only route to terminal, because a session/IP/token-dependent stream can
    fail here and work elsewhere - this codebase has 1,312 posters proving it.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import movie_link_health as lh  # noqa: E402

NOW = datetime(2026, 9, 20, tzinfo=timezone.utc)
URL = "https://cdn.test/movies/film.mkv"


def _healthy(store=None, **kwargs):
    return lh.record_result(
        store if store is not None else {"links": {}},
        identity=kwargs.pop("identity", "one"),
        url=kwargs.pop("url", URL),
        content_id=kwargs.pop("content_id", "film-1"),
        verification_status=kwargs.pop("verification_status", "verified_global"),
        now=kwargs.pop("now", NOW),
        **kwargs,
    )


class HostClassTests(unittest.TestCase):
    """ধারা ৪.২'s `host_class`, which decides half the TTL."""

    def test_an_ordinary_cdn_is_stable(self) -> None:
        self.assertEqual(lh.host_class(URL), lh.HOST_STABLE)

    def test_the_hosts_this_catalogue_measured_as_short_lived_are_unstable(self) -> None:
        for url in ("https://image.sm-iptv-monirul-islam.workers.dev/p.jpg",
                    "https://srhady-live-stream.hf.space/x.jpg"):
            with self.subTest(url=url):
                self.assertEqual(lh.host_class(url), lh.HOST_UNSTABLE)

    def test_a_signed_url_is_tokenized(self) -> None:
        for url in ("https://c.test/a.m3u8?token=abc",
                    "https://c.test/a.m3u8?expires=1790000000",
                    "https://c.test/a.m3u8?hdnts=exp%3D123~hmac%3Dabc"):
            with self.subTest(url=url):
                self.assertEqual(lh.host_class(url), lh.HOST_TOKENIZED)

    def test_an_empty_url_does_not_raise(self) -> None:
        self.assertEqual(lh.host_class(""), lh.HOST_STABLE)


class StreamIdentityTests(unittest.TestCase):
    """The gate and the no-loss accounting must agree what "same link" means."""

    def test_a_re_signed_token_is_the_same_link(self) -> None:
        self.assertEqual(
            lh.stream_id("https://c.test/a.m3u8?token=aaa&expires=1"),
            lh.stream_id("https://c.test/a.m3u8?token=bbb&expires=2"),
        )

    def test_two_header_profiles_are_two_links(self) -> None:
        self.assertNotEqual(
            lh.stream_id(URL, header_profile="android_tv"),
            lh.stream_id(URL, header_profile="web_referer"),
        )

    def test_two_sources_are_two_links(self) -> None:
        self.assertNotEqual(
            lh.stream_id(URL, source_id="a"), lh.stream_id(URL, source_id="b"))


class TheFirstRunChangesNothingTests(unittest.TestCase):
    """The safety property the whole design rests on."""

    def test_a_link_with_no_record_is_always_due(self) -> None:
        self.assertEqual(
            lh.verification_decision(None, url=URL, now=NOW), (True, lh.DUE_NEW))

    def test_a_record_that_was_never_verified_is_due(self) -> None:
        record = lh.new_record(identity="x", url=URL)
        self.assertEqual(
            lh.verification_decision(record, url=URL, now=NOW)[0], True)

    def test_a_new_record_carries_no_next_verify_at(self) -> None:
        """An absent schedule reads as "due", which is the point."""
        self.assertEqual(lh.new_record(identity="x", url=URL)["next_verify_at"], "")


class TheOneQuestionTests(unittest.TestCase):
    """ধারা ৪.৩ - NEW / CHANGED / DUE / REPAIR verify; FRESH HEALTHY does not."""

    def setUp(self) -> None:
        self.record = _healthy(source_revision="etag-1")

    def _decide(self, **kwargs):
        kwargs.setdefault("url", URL)
        kwargs.setdefault("content_id", "not-todays-bucket")
        kwargs.setdefault("now", NOW)
        return lh.verification_decision(self.record, **kwargs)

    def test_a_fresh_healthy_link_is_not_verified(self) -> None:
        self.assertEqual(self._decide(), (False, lh.FRESH))

    def test_a_link_past_its_ttl_is_verified(self) -> None:
        self.assertEqual(
            self._decide(now=NOW + timedelta(days=6)), (True, lh.DUE_TTL))

    def test_a_changed_source_revision_cancels_the_ttl(self) -> None:
        """ধারা ৪.২ - "সোর্স বদলালে TTL বাতিল"."""
        self.assertEqual(
            self._decide(source_revision="etag-2"), (True, lh.DUE_CHANGED))

    def test_a_record_with_no_known_revision_is_verified_once(self) -> None:
        """"we do not know what revision this came from" is not the same claim
        as "it has not changed"."""
        record = _healthy(identity="norev")
        self.assertEqual(
            lh.verification_decision(
                record, url=URL, content_id="x", source_revision="etag-9",
                now=NOW),
            (True, lh.DUE_CHANGED),
        )

    def test_an_unhealthy_link_is_always_verified(self) -> None:
        self.record["status"] = lh.STATUS_FAILING
        self.assertEqual(self._decide()[0], True)

    def test_a_repair_queued_link_jumps_the_ttl(self) -> None:
        self.assertEqual(
            self._decide(repair_queued=True), (True, lh.DUE_REPAIR))

    def test_a_reported_playback_failure_outranks_everything(self) -> None:
        """ধারা ৪.৪ puts it at the top: a viewer has already hit the failure."""
        self.assertEqual(
            self._decide(playback_failed=True, repair_queued=True),
            (True, lh.DUE_PLAYBACK_FAILURE),
        )

    def test_the_sweep_reaches_a_link_that_is_otherwise_never_due(self) -> None:
        """"সুস্থ লিংক আর কখনো দেখব না" is the mistake ধারা ৪.৪ names outright."""
        content = next(
            str(index) for index in range(500)
            if lh.sweep_bucket(str(index)) == NOW.toordinal() % lh.SWEEP_BUCKETS
        )
        self.assertEqual(
            lh.verification_decision(
                self.record, url=URL, content_id=content, now=NOW),
            (True, lh.DUE_SWEEP),
        )


class TtlLadderTests(unittest.TestCase):
    """ধারা ৪.৪, with the numbers it states."""

    def test_the_failure_backoff_is_one_six_twentyfour_seventytwo(self) -> None:
        hours = []
        store = {"links": {}}
        for _ in range(4):
            record = lh.record_result(
                store, identity="f", url=URL, content_id="c",
                verification_status="failed", http_status=500, now=NOW)
            hours.append(record["ttl_seconds"] / 3600)
        self.assertEqual(hours, [1.0, 6.0, 24.0, 72.0])

    def test_an_unstable_host_is_rechecked_within_two_days(self) -> None:
        seconds = lh.ttl_seconds(
            {"host_class": lh.HOST_UNSTABLE, "consecutive_failures": 0})
        self.assertGreaterEqual(seconds, 24 * 3600)
        self.assertLessEqual(seconds, 48 * 3600)

    def test_a_healthy_public_link_is_rechecked_within_a_week(self) -> None:
        seconds = lh.ttl_seconds(
            {"host_class": lh.HOST_STABLE, "consecutive_failures": 0})
        self.assertGreaterEqual(seconds, 3 * 86400)
        self.assertLessEqual(seconds, 7 * 86400)

    def test_a_trusted_private_link_gets_about_a_week(self) -> None:
        self.assertEqual(
            lh.ttl_seconds({"host_class": lh.HOST_STABLE,
                            "consecutive_failures": 0}, trusted_private=True),
            7 * 86400,
        )

    def test_a_tokenized_link_is_rechecked_before_its_token_dies(self) -> None:
        expiry = int((NOW + timedelta(hours=8)).timestamp())
        seconds = lh.ttl_seconds(
            {"host_class": lh.HOST_TOKENIZED, "consecutive_failures": 0},
            url=f"https://c.test/a.m3u8?expires={expiry}", now=NOW)
        self.assertLess(seconds, 8 * 3600)

    def test_an_already_expired_token_is_due_now_not_in_the_past(self) -> None:
        expiry = int((NOW - timedelta(hours=1)).timestamp())
        self.assertEqual(
            lh.ttl_seconds(
                {"host_class": lh.HOST_TOKENIZED, "consecutive_failures": 0},
                url=f"https://c.test/a.m3u8?expires={expiry}", now=NOW),
            0,
        )

    def test_an_unparseable_expiry_falls_back_rather_than_guessing(self) -> None:
        self.assertEqual(
            lh.ttl_seconds(
                {"host_class": lh.HOST_TOKENIZED, "consecutive_failures": 0},
                url="https://c.test/a.m3u8?expires=next-tuesday", now=NOW),
            lh.TTL_TOKENIZED_FALLBACK_SECONDS,
        )

    def test_a_far_future_number_is_not_read_as_an_expiry(self) -> None:
        self.assertIsNone(
            lh.token_expiry("https://c.test/a.m3u8?expires=99999999999", NOW))


class StatusTransitionTests(unittest.TestCase):
    """ধারা ৪.০ axis 2, and the v৩.২ correction that shaped it."""

    def test_a_success_is_healthy_and_resets_the_failure_count(self) -> None:
        store = {"links": {}}
        lh.record_result(store, identity="a", url=URL, content_id="c",
                         verification_status="failed", http_status=500, now=NOW)
        record = lh.record_result(
            store, identity="a", url=URL, content_id="c",
            verification_status="verified_global", now=NOW)
        self.assertEqual(record["status"], lh.STATUS_HEALTHY)
        self.assertEqual(record["consecutive_failures"], 0)
        self.assertNotIn("stale_reason", record)

    def test_404_is_the_only_route_to_dead(self) -> None:
        for status in (404, 410):
            with self.subTest(status=status):
                record = lh.record_result(
                    {"links": {}}, identity="d", url=URL,
                    verification_status="failed", http_status=status, now=NOW)
                self.assertEqual(record["status"], lh.STATUS_DEAD)

    def test_repeated_failure_from_our_vantage_is_never_dead(self) -> None:
        """v৩.২ struck "multi-vantage multi-run failure" off the definitive
        list. A session/IP/token-dependent stream can fail here and work
        elsewhere - 1,312 posters in this codebase prove exactly that."""
        store = {"links": {}}
        for _ in range(6):
            record = lh.record_result(
                store, identity="u", url=URL, content_id="c",
                verification_status="failed", http_status=403, now=NOW)
        self.assertEqual(record["status"], lh.STATUS_CONFIRMED_UNAVAILABLE)
        self.assertNotIn(record["status"], lh.TERMINAL_STATUSES)

    def test_a_403_is_recorded_as_geo_not_as_a_dead_host(self) -> None:
        record = lh.record_result(
            {"links": {}}, identity="g", url=URL,
            verification_status="failed", http_status=403, now=NOW)
        self.assertEqual(record["stale_reason"], lh.STALE_GEO)

    def test_confirmed_unavailable_is_not_a_content_disposition(self) -> None:
        """ধারা ৪.০ v৩.৩ - two axes. This one is about the link."""
        from scanner import movie_coverage

        self.assertNotIn(
            lh.STATUS_CONFIRMED_UNAVAILABLE, movie_coverage.DISPOSITIONS)


class PrimaryPolicyTests(unittest.TestCase):
    """ধারা ৪.৫ - preferred_primary is policy, active_primary is reality."""

    def test_a_private_link_is_preferred_and_active(self) -> None:
        record = _healthy(identity="p", preferred_primary=True)
        self.assertTrue(record["preferred_primary"])
        self.assertTrue(record["active_primary"])

    def test_the_owners_policy_survives_a_failure(self) -> None:
        store = {"links": {}}
        _healthy(store, identity="p", preferred_primary=True)
        record = lh.record_result(
            store, identity="p", url=URL, verification_status="failed",
            http_status=500, now=NOW)
        self.assertTrue(
            record["preferred_primary"],
            msg="policy does not change because a check failed",
        )

    def test_a_private_link_that_comes_back_is_primary_again(self) -> None:
        store = {"links": {}}
        _healthy(store, identity="p", preferred_primary=True)
        lh.record_result(store, identity="p", url=URL,
                         verification_status="failed", http_status=500, now=NOW)
        record = lh.record_result(
            store, identity="p", url=URL,
            verification_status="manual_trusted", now=NOW)
        self.assertTrue(record["active_primary"])


class StaleReasonTests(unittest.TestCase):
    """ধারা ৪.৪ - "stale ০%" is not the claim; "budget → 0" is."""

    def test_every_stale_entry_carries_a_reason(self) -> None:
        store = {"links": {}}
        lh.record_result(store, identity="a", url=URL,
                         verification_status="failed", http_status=403, now=NOW)
        lh.record_result(store, identity="b", url=URL,
                         verification_status="failed", http_status=500, now=NOW)
        for record in store["links"].values():
            self.assertIn(record["stale_reason"], lh.STALE_REASONS)

    def test_a_link_the_run_never_reached_is_stale_for_budget(self) -> None:
        store = {"links": {}}
        _healthy(store, identity="a")
        lh.mark_skipped_for_budget(store, "a")
        self.assertEqual(store["links"]["a"]["stale_reason"], lh.STALE_BUDGET)

    def test_the_summary_singles_out_the_budget_number(self) -> None:
        store = {"links": {}}
        _healthy(store, identity="a")
        lh.record_result(store, identity="b", url=URL,
                         verification_status="failed", http_status=403, now=NOW)
        lh.mark_skipped_for_budget(store, "a")
        summary = lh.summarise(store)
        self.assertEqual(summary["stale_for_budget"], 1)
        self.assertEqual(summary["stale_reasons"][lh.STALE_GEO], 1)
        self.assertEqual(summary["links"], 2)


class SweepTests(unittest.TestCase):
    """ধারা ৪.৪ - `hash(content_id) % 7`, ~354 links a night."""

    def test_the_buckets_are_reasonably_even(self) -> None:
        from collections import Counter

        counts = Counter(lh.sweep_bucket(f"film-{i}") for i in range(2475))
        self.assertEqual(len(counts), lh.SWEEP_BUCKETS)
        # A perfect split is 353. Anything inside a fifth of that is fine; the
        # point is that no night gets double the work.
        self.assertLess(max(counts.values()) - min(counts.values()), 120)

    def test_an_item_stays_in_its_bucket(self) -> None:
        self.assertEqual(lh.sweep_bucket("film-1"), lh.sweep_bucket("film-1"))

    def test_every_item_is_swept_within_seven_days(self) -> None:
        swept = {
            day for day in range(lh.SWEEP_BUCKETS)
            if lh.is_sweep_day("film-1", NOW + timedelta(days=day))
        }
        self.assertEqual(len(swept), 1, msg="exactly one night, every week")


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.path = Path(tempfile.mkdtemp()) / "movie-link-health.json"

    def test_a_missing_store_is_empty_not_an_error(self) -> None:
        self.assertEqual(lh.load(self.path)["links"], {})

    def test_a_damaged_store_is_empty_not_a_scan_failure(self) -> None:
        self.path.write_text("{not json", encoding="utf-8")
        self.assertEqual(lh.load(self.path)["links"], {})

    def test_the_store_round_trips(self) -> None:
        store = {"links": {}}
        _healthy(store, identity="a")
        self.assertTrue(lh.save(store, self.path))
        reloaded = lh.load(self.path)
        self.assertEqual(reloaded["links"]["a"]["status"], lh.STATUS_HEALTHY)

    def test_every_field_the_plan_names_is_present(self) -> None:
        record = _healthy()
        for field in ("stream_id", "content_id", "source_id", "status",
                      "last_verified_at", "last_healthy_at",
                      "consecutive_failures", "next_verify_at", "host_class",
                      "source_revision", "preferred_primary", "active_primary"):
            self.assertIn(field, record)


class PipelineGateTests(unittest.TestCase):
    """The wiring: carried items leave the probe queue, never the run."""

    SOURCE = (ROOT / "scanner" / "fast_pipeline.py").read_text(encoding="utf-8")

    def test_the_gate_runs_before_the_worker_profile_is_built(self) -> None:
        partition = self.SOURCE.index("_partition_by_link_health(")
        workers = self.SOURCE.index("worker_profile = _mode_worker_profile(")
        self.assertLess(partition, workers)

    def test_carried_items_rejoin_before_anything_is_published(self) -> None:
        rejoin = self.SOURCE.index("final_results = _record_link_health(")
        publish = self.SOURCE.index("_atomic_write_json(bd_output_path")
        self.assertLess(rejoin, publish)

    def test_only_movie_candidates_are_gated(self) -> None:
        """Live TV, Today and Upcoming run on their own cadence and this plan
        does not touch them."""
        marker = self.SOURCE.index("def _partition_by_link_health(")
        window = self.SOURCE[marker:marker + 3000]
        self.assertIn("_is_movie_candidate(item, mode)", window)

    def test_the_gate_can_be_switched_off(self) -> None:
        settings = json.loads(
            (ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
        self.assertIn("movie_link_health", settings)
        self.assertIn("enabled", settings["movie_link_health"])

    def test_a_failure_in_the_gate_verifies_everything(self) -> None:
        """Degrading to today's behaviour is the only safe failure mode."""
        marker = self.SOURCE.index("def _partition_by_link_health(")
        window = self.SOURCE[marker:marker + 3600]
        self.assertIn("return items, [], None", window)


class WhatTheGateMayCallTerminal(unittest.TestCase):
    """ধারা ৪.১ - only a definitive 404/410 is terminal, and the gate has to be
    TOLD which links those were.

    It was not. `scan.py` built the coverage report without any terminal
    evidence, so `terminal_evidence` stayed 0 and 362 links with a confirmed
    404 were counted as unexplained loss - blocking a publish over content
    that really had been removed.
    """

    def _store(self):
        return {"schema_version": 1, "links": {}}

    def test_a_confirmed_404_becomes_terminal_evidence(self):
        store = self._store()
        lh.record_result(
            store, identity="a", url="https://cdn.example.com/gone.mkv",
            verification_status="failed", http_status=404)
        records = lh.terminal_records(store)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["reason"], "confirmed_404_or_410")

    def test_the_reason_is_one_the_gate_accepts(self):
        from scanner import movie_coverage

        store = self._store()
        lh.record_result(store, identity="a", url="https://cdn/x.mkv",
                          verification_status="failed", http_status=410)
        self.assertIn(lh.terminal_records(store)[0]["reason"],
                      movie_coverage.DEFINITIVE_REJECT_REASONS)

    def test_a_vantage_refusal_is_never_terminal(self):
        """403, 451, geo - the whole point of ধারা ৪.১. If any of these leaked
        through, the gate would let real content be dropped."""
        for status in (401, 402, 403, 407, 451):
            with self.subTest(status=status):
                store = self._store()
                lh.record_result(store, identity="a", url="https://cdn/x.mkv",
                                  verification_status="failed",
                                  http_status=status)
                self.assertEqual(lh.terminal_records(store), [])

    def test_a_host_that_would_not_answer_is_never_terminal(self):
        store = self._store()
        lh.record_result(store, identity="a", url="https://cdn/x.mkv",
                          verification_status="failed", http_status=0)
        self.assertEqual(lh.terminal_records(store), [])

    def test_repeated_failure_from_this_vantage_is_never_terminal(self):
        store = self._store()
        for _ in range(6):
            lh.record_result(store, identity="a", url="https://cdn/x.mkv",
                              verification_status="failed", http_status=0)
        self.assertEqual(store["links"]["a"]["status"],
                         lh.STATUS_CONFIRMED_UNAVAILABLE)
        self.assertEqual(lh.terminal_records(store), [])

    def test_a_healthy_link_is_never_terminal(self):
        store = self._store()
        lh.record_result(store, identity="a", url="https://cdn/x.mkv",
                          verification_status="verified_global")
        self.assertEqual(lh.terminal_records(store), [])

    def test_the_family_is_what_the_gate_matches_on(self):
        """Stored rather than the url: it is what the gate compares, and it
        carries no signed token."""
        from scanner import movie_coverage

        store = self._store()
        url = "https://cdn.example.com/a/b.mkv?token=secret&expires=99"
        lh.record_result(store, identity="a", url=url,
                          verification_status="failed", http_status=404)
        family = movie_coverage.stream_family(url)
        self.assertEqual(store["links"]["a"]["stream_family"], family)
        self.assertEqual(lh.terminal_records(store)[0]["url"], family)
        self.assertNotIn("secret", lh.terminal_records(store)[0]["url"])

    def test_a_record_written_before_the_family_existed_is_skipped(self):
        """It costs one run: a dead link's TTL is an hour, so it is verified
        again and gains one. Claiming it terminal without the family would be
        claiming it about a link we cannot name."""
        store = {"links": {"old": {"status": lh.STATUS_DEAD}}}
        self.assertEqual(lh.terminal_records(store), [])

    def test_a_damaged_store_yields_nothing_rather_than_raising(self):
        self.assertEqual(lh.terminal_records(None), [])
        self.assertEqual(lh.terminal_records({"links": "not a mapping"}), [])


if __name__ == "__main__":
    unittest.main()
