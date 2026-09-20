"""ধারা ৪.৭ - the provider order, the quota, and the breaker.

The plan is blunt about the chain that existed here: TMDB → OMDb → Cinemeta →
… is "বর্তমানে কোডে যা আছে তার বর্ণনা মাত্র — চূড়ান্ত নির্দেশ নয়", a description
and not an instruction, and the real order has to come from capability, quota,
health and confidence.

Two rules carry the most weight:

  * **the defaults must change nothing.** A migration that quietly reorders
    seven providers on the day it ships is not one anybody can review, so the
    default weights reproduce the old sequence exactly and a test holds that.
  * **nobody available is not a failure.** ধারা ৪.৭'s second unbreakable
    condition: with every provider down the film still publishes, carrying
    `metadata_pending` / `artwork_pending`.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scanner import provider_health as ph  # noqa: E402
from scanner import provider_router as pr  # noqa: E402


class CapabilityTests(unittest.TestCase):
    """A provider that cannot answer is not asked - that is a fact, not taste."""

    def test_artwork_only_providers_are_not_asked_for_metadata(self) -> None:
        self.assertFalse(pr.can_answer(
            "fanart", capability=pr.CAPABILITY_MOVIE_METADATA,
            have={"tmdb_id": 1}))
        self.assertTrue(pr.can_answer(
            "fanart", capability=pr.CAPABILITY_ARTWORK, have={"tmdb_id": 1}))

    def test_a_provider_without_its_required_id_is_not_asked(self) -> None:
        """Cinemeta is an imdb_id lookup and Fanart a tmdb_id one. Calling them
        without those spends a request that can only fail."""
        self.assertFalse(pr.can_answer(
            "cinemeta", capability=pr.CAPABILITY_MOVIE_METADATA, have={}))
        self.assertTrue(pr.can_answer(
            "cinemeta", capability=pr.CAPABILITY_MOVIE_METADATA,
            have={"imdb_id": "tt0111161"}))
        self.assertFalse(pr.can_answer(
            "fanart", capability=pr.CAPABILITY_ARTWORK, have={"tmdb_id": 0}))

    def test_television_and_anime_providers_stay_in_their_lane(self) -> None:
        self.assertFalse(pr.can_answer(
            "tvmaze", capability=pr.CAPABILITY_SERIES_METADATA,
            kind=pr.KIND_MOVIE))
        self.assertTrue(pr.can_answer(
            "tvmaze", capability=pr.CAPABILITY_SERIES_METADATA,
            kind=pr.KIND_SERIES))
        self.assertFalse(pr.can_answer(
            "anilist", capability=pr.CAPABILITY_SERIES_METADATA,
            kind=pr.KIND_SERIES))
        self.assertTrue(pr.can_answer(
            "anilist", capability=pr.CAPABILITY_SERIES_METADATA,
            kind=pr.KIND_ANIME))

    def test_an_unknown_provider_cannot_answer_anything(self) -> None:
        self.assertFalse(pr.can_answer(
            "not-a-provider", capability=pr.CAPABILITY_MOVIE_METADATA))


class DefaultOrderTests(unittest.TestCase):
    """The defaults reproduce the sequence the code used to hard-code."""

    def setUp(self) -> None:
        self.path = str(Path(tempfile.mkdtemp()) / "provider-health.json")
        ph.reset(self.path)
        self.addCleanup(ph.reset, None)

    def test_movie_metadata_order_is_unchanged(self) -> None:
        self.assertEqual(
            pr.order(capability=pr.CAPABILITY_MOVIE_METADATA,
                     kind=pr.KIND_MOVIE, have={"imdb_id": "tt1"}),
            ["tmdb", "omdb", "cinemeta", "moviesdatabase"],
        )

    def test_a_missing_imdb_id_simply_removes_cinemeta(self) -> None:
        self.assertEqual(
            pr.order(capability=pr.CAPABILITY_MOVIE_METADATA,
                     kind=pr.KIND_MOVIE, have={}),
            ["tmdb", "omdb", "moviesdatabase"],
        )

    def test_series_and_anime_orders_are_unchanged(self) -> None:
        self.assertEqual(
            pr.order(capability=pr.CAPABILITY_SERIES_METADATA,
                     kind=pr.KIND_SERIES, have={"imdb_id": "tt1"}),
            ["tmdb", "omdb", "cinemeta", "tvmaze"],
        )
        self.assertEqual(
            pr.order(capability=pr.CAPABILITY_SERIES_METADATA,
                     kind=pr.KIND_ANIME, have={}),
            ["tmdb", "omdb", "anilist"],
        )

    def test_fanart_appears_only_once_a_tmdb_id_exists(self) -> None:
        self.assertNotIn("fanart", pr.order(
            capability=pr.CAPABILITY_ARTWORK, have={}))
        self.assertIn("fanart", pr.order(
            capability=pr.CAPABILITY_ARTWORK, have={"tmdb_id": 42}))

    def test_the_order_is_configurable_without_touching_code(self) -> None:
        """"কোনো প্রোভাইডারের অবস্থান স্থির নয়" - the point of the whole module."""
        config = pr.provider_config()
        config["omdb"] = {**config["omdb"], "weight": 1}
        self.assertEqual(
            pr.order(capability=pr.CAPABILITY_MOVIE_METADATA, have={},
                     config=config)[0],
            "omdb",
        )

    def test_a_partial_config_override_keeps_the_rest_of_the_spec(self) -> None:
        """A capability list emptied by a partial edit is how a provider stops
        being asked without anybody noticing."""
        directory = Path(tempfile.mkdtemp())
        settings = directory / "settings.json"
        settings.write_text(json.dumps(
            {"metadata_providers": {"providers": {"tmdb": {"weight": 99}}}}),
            encoding="utf-8")
        config = pr.provider_config(settings)
        self.assertEqual(config["tmdb"]["weight"], 99)
        self.assertIn(
            pr.CAPABILITY_MOVIE_METADATA, config["tmdb"]["capabilities"])


class AvailabilityTests(unittest.TestCase):
    """Cooling down, tripped or spent means excluded, not demoted."""

    def setUp(self) -> None:
        self.path = str(Path(tempfile.mkdtemp()) / "provider-health.json")
        ph.reset(self.path)
        self.addCleanup(ph.reset, None)

    def _record(self, provider):
        return ph._provider_record(provider)

    def test_a_cooling_down_provider_leaves_the_order(self) -> None:
        record = self._record("tmdb")
        ph._mark_unavailable(record, ph.STATUS_COOLING_DOWN, 3600)
        self.assertNotIn(
            "tmdb", pr.order(capability=pr.CAPABILITY_MOVIE_METADATA, have={}))

    def test_an_auth_broken_provider_leaves_the_order(self) -> None:
        record = self._record("omdb")
        ph._mark_unavailable(record, ph.STATUS_UNHEALTHY_AUTH, 3600)
        self.assertNotIn(
            "omdb", pr.order(capability=pr.CAPABILITY_MOVIE_METADATA, have={}))

    def test_everybody_down_is_an_empty_order_not_an_exception(self) -> None:
        """ধারা ৪.৭ - the film still publishes; the caller reads this and marks
        it pending."""
        for provider in pr.DEFAULT_PROVIDERS:
            ph._mark_unavailable(
                self._record(provider), ph.STATUS_COOLING_DOWN, 3600)
        self.assertEqual(
            pr.order(capability=pr.CAPABILITY_MOVIE_METADATA, have={}), [])
        self.assertFalse(pr.any_available())


class QuotaTests(unittest.TestCase):
    """ধারা ৪.৭ - a daily soft budget, with a slice held back."""

    def setUp(self) -> None:
        self.path = str(Path(tempfile.mkdtemp()) / "provider-health.json")
        ph.reset(self.path)
        self.addCleanup(ph.reset, None)

    def _omdb(self):
        record = ph._provider_record("omdb")
        record["daily_soft_budget"] = 100
        record["reserved_for_fallback"] = 20
        return record

    def test_ordinary_enrichment_may_not_spend_the_reserve(self) -> None:
        """"OMDb: ফ্রি কোটার একটি অংশ জরুরি fallback-এর জন্য সংরক্ষিত রাখা".

        A budget with no reserve is spent by the first thousand films of the
        day, and the one lookup that actually needed a fallback finds nothing
        left.
        """
        record = self._omdb()
        self.assertEqual(ph.remaining_quota(record), 80)
        self.assertEqual(ph.remaining_quota(record, reserved=True), 100)

    def test_spending_reduces_what_is_left(self) -> None:
        self._omdb()
        for _ in range(30):
            ph.note_spend("omdb")
        record = ph._provider_record("omdb")
        self.assertEqual(ph.remaining_quota(record), 50)
        self.assertEqual(ph.remaining_quota(record, reserved=True), 70)

    def test_an_exhausted_ordinary_budget_still_leaves_the_reserve(self) -> None:
        self._omdb()
        for _ in range(80):
            ph.note_spend("omdb")
        record = ph._provider_record("omdb")
        self.assertEqual(ph.remaining_quota(record), 0)
        self.assertEqual(ph.remaining_quota(record, reserved=True), 20)

    def test_a_spent_provider_drops_out_of_the_order(self) -> None:
        self._omdb()
        for _ in range(80):
            ph.note_spend("omdb")
        self.assertNotIn(
            "omdb", pr.order(capability=pr.CAPABILITY_MOVIE_METADATA, have={}))

    def test_a_provider_with_no_budget_is_unmetered(self) -> None:
        self.assertEqual(ph.remaining_quota(ph._provider_record("tmdb")), -1)
        self.assertIn(
            "tmdb", pr.order(capability=pr.CAPABILITY_MOVIE_METADATA, have={}))

    def test_the_counter_resets_when_the_day_turns(self) -> None:
        record = self._omdb()
        for _ in range(50):
            ph.note_spend("omdb")
        self.assertEqual(ph.remaining_quota(record), 30)
        record["quota_day"] = "1999-01-01"
        self.assertEqual(ph.remaining_quota(record), 80)
        self.assertEqual(record["spent_today"], 0)
        self.assertTrue(record["reset_at"])

    def test_budgets_come_from_configuration(self) -> None:
        applied = pr.apply_budgets()
        self.assertIn("omdb", applied)
        self.assertEqual(
            ph._provider_record("omdb")["daily_soft_budget"], applied["omdb"])


class CircuitBreakerTests(unittest.TestCase):
    """ধারা ৪.৭ - "৩–৫ বার পরপর ব্যর্থ → ৫–১৫ মিনিট open → half-open টেস্ট"."""

    def setUp(self) -> None:
        self.path = str(Path(tempfile.mkdtemp()) / "provider-health.json")
        ph.reset(self.path)
        self.addCleanup(ph.reset, None)
        self.record = ph._provider_record("tmdb")

    def test_the_breaker_counts_failures_across_requests(self) -> None:
        """The gap this closes: 150 lookups each failing once never exhausted a
        single request's retry ladder, so the old breaker never opened."""
        for _ in range(ph.BREAKER_THRESHOLD - 1):
            ph._note_failure(self.record)
        self.assertTrue(ph.is_available("tmdb"))
        ph._note_failure(self.record)
        self.assertFalse(ph.is_available("tmdb"))

    def test_a_success_resets_the_count(self) -> None:
        for _ in range(ph.BREAKER_THRESHOLD - 1):
            ph._note_failure(self.record)
        ph._note_success(self.record)
        self.assertEqual(self.record["consecutive_failures"], 0)
        for _ in range(ph.BREAKER_THRESHOLD - 1):
            ph._note_failure(self.record)
        self.assertTrue(ph.is_available("tmdb"))

    def test_a_real_no_result_is_not_a_failure(self) -> None:
        """ধারা ৪.৭ - "404 / no result → প্রোভাইডার ঠিক আছে, শুধু এই আইটেম
        মেলেনি". A provider that keeps saying "not here" is working."""
        source = (Path(__file__).resolve().parents[1]
                  / "scanner" / "provider_health.py").read_text(encoding="utf-8")
        marker = source.index('_bump(record, "not_found")')
        self.assertIn("_note_success(record)", source[marker:marker + 200])

    def test_an_open_breaker_reopens_for_longer_after_a_failed_test(self) -> None:
        for _ in range(ph.BREAKER_THRESHOLD):
            ph._note_failure(self.record)
        first = float(self.record["breaker_open_seconds"])

        # Time passes: the breaker offers one half-open test.
        self.record["breaker_open_until"] = (
            dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)
        ).isoformat()
        self.assertTrue(ph.is_available("tmdb"))
        self.assertTrue(self.record.get("breaker_half_open"))

        ph._note_failure(self.record)
        self.assertFalse(ph.is_available("tmdb"))
        self.assertGreater(float(self.record["breaker_open_seconds"]), first)

    def test_a_successful_half_open_test_closes_the_breaker(self) -> None:
        for _ in range(ph.BREAKER_THRESHOLD):
            ph._note_failure(self.record)
        self.record["breaker_open_until"] = (
            dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)
        ).isoformat()
        self.assertTrue(ph.is_available("tmdb"))
        ph._note_success(self.record)
        self.assertTrue(ph.is_available("tmdb"))
        self.assertNotIn("breaker_half_open", self.record)

    def test_a_tripped_provider_leaves_the_order(self) -> None:
        for _ in range(ph.BREAKER_THRESHOLD):
            ph._note_failure(self.record)
        self.assertNotIn(
            "tmdb", pr.order(capability=pr.CAPABILITY_MOVIE_METADATA, have={}))


class LatencyTests(unittest.TestCase):
    """Among equal weights, the faster provider goes first."""

    def setUp(self) -> None:
        self.path = str(Path(tempfile.mkdtemp()) / "provider-health.json")
        ph.reset(self.path)
        self.addCleanup(ph.reset, None)

    def test_latency_is_a_rolling_mean(self) -> None:
        record = ph._provider_record("tmdb")
        ph._note_latency(record, 100.0)
        self.assertEqual(record["average_latency_ms"], 100.0)
        ph._note_latency(record, 200.0)
        self.assertGreater(record["average_latency_ms"], 100.0)
        self.assertLess(record["average_latency_ms"], 200.0)

    def test_a_slower_provider_sorts_after_an_equal_faster_one(self) -> None:
        config = pr.provider_config()
        for name in ("tmdb", "omdb"):
            config[name] = {**config[name], "weight": 10}
        ph._note_latency(ph._provider_record("tmdb"), 900.0)
        ph._note_latency(ph._provider_record("omdb"), 40.0)
        self.assertEqual(
            pr.order(capability=pr.CAPABILITY_MOVIE_METADATA, have={},
                     config=config)[0],
            "omdb",
        )

    def test_headroom_is_a_bucket_so_latency_can_still_decide(self) -> None:
        """Comparing raw counts made "unmetered" beat every metered provider
        outright - an infinite headroom always sorts first - so latency could
        never decide anything, which is half the reason the plan asks for a
        dynamic order at all.

        tmdb is unmetered and slow; omdb is metered, has plenty left, and is
        fast. Same weight, so the fast one wins.
        """
        config = pr.provider_config()
        for name in ("tmdb", "omdb"):
            config[name] = {**config[name], "weight": 10}
        omdb = ph._provider_record("omdb")
        omdb["daily_soft_budget"] = 100
        omdb["reserved_for_fallback"] = 10
        ph._note_latency(ph._provider_record("tmdb"), 900.0)
        ph._note_latency(omdb, 40.0)
        self.assertEqual(
            pr.order(capability=pr.CAPABILITY_MOVIE_METADATA, have={},
                     config=config)[0],
            "omdb",
        )

    def test_a_provider_running_low_drifts_below_an_equal_one(self) -> None:
        config = pr.provider_config()
        for name in ("tmdb", "omdb"):
            config[name] = {**config[name], "weight": 10}
        omdb = ph._provider_record("omdb")
        omdb["daily_soft_budget"] = 100
        omdb["reserved_for_fallback"] = 0
        for _ in range(95):
            ph.note_spend("omdb")
        # Relative position, not the whole list: `moviesdatabase` is eligible
        # for this capability too and belongs in the answer. Asserting the
        # exact list made this test about which providers exist rather than
        # about the ordering rule it is named for.
        found = pr.order(capability=pr.CAPABILITY_MOVIE_METADATA, have={},
                         config=config)
        self.assertLess(found.index("tmdb"), found.index("omdb"))


class PendingMarkTests(unittest.TestCase):
    """ধারা ৪.৭ - enrichment is not a condition of publishing."""

    def test_a_bare_film_is_marked_on_both_counts(self) -> None:
        movie = {"id": "x", "name": "New Film", "url": "https://c.test/x.mkv"}
        marks = pr.mark_pending(movie)
        self.assertTrue(marks[pr.PENDING_METADATA])
        self.assertTrue(marks[pr.PENDING_ARTWORK])
        self.assertTrue(movie["metadata_pending"])
        self.assertTrue(movie["artwork_pending"])

    def test_nothing_is_ever_removed_from_the_card(self) -> None:
        movie = {"id": "x", "name": "New Film", "url": "https://c.test/x.mkv",
                 "category": "Mix", "backups": []}
        before = dict(movie)
        pr.mark_pending(movie)
        for key, value in before.items():
            self.assertEqual(movie[key], value)

    def test_a_mark_is_cleared_once_the_field_arrives(self) -> None:
        """The marks describe the present, not a history of what was missing."""
        movie = {"id": "x", "name": "Film", "metadata_pending": True,
                 "artwork_pending": True, "tmdb_id": 278,
                 "logo": "https://img.test/p.jpg"}
        pr.mark_pending(movie)
        self.assertNotIn("metadata_pending", movie)
        self.assertNotIn("artwork_pending", movie)

    def test_artwork_and_metadata_are_judged_separately(self) -> None:
        movie = {"id": "x", "name": "Film", "logo": "https://img.test/p.jpg"}
        marks = pr.mark_pending(movie)
        self.assertTrue(marks[pr.PENDING_METADATA])
        self.assertFalse(marks[pr.PENDING_ARTWORK])

    def test_the_marks_are_the_ones_the_no_loss_gate_reads(self) -> None:
        from scanner import movie_coverage

        self.assertIn(pr.PENDING_METADATA, movie_coverage.PENDING_FLAGS)
        self.assertIn(pr.PENDING_ARTWORK, movie_coverage.PENDING_FLAGS)

    def test_marking_counts_what_a_scan_report_needs(self) -> None:
        counts = pr.mark_all_pending([
            {"id": "a", "name": "A"},
            {"id": "b", "name": "B", "tmdb_id": 1, "logo": "https://i.test/b.jpg"},
            {"id": "c", "name": "C", "logo": "https://i.test/c.jpg"},
        ])
        self.assertEqual(counts[pr.PENDING_METADATA], 2)
        self.assertEqual(counts[pr.PENDING_ARTWORK], 1)


class SnapshotTests(unittest.TestCase):
    """A-06 - provider quota in the run report, where somebody can act on it."""

    def setUp(self) -> None:
        self.path = str(Path(tempfile.mkdtemp()) / "provider-health.json")
        ph.reset(self.path)
        self.addCleanup(ph.reset, None)

    def test_every_field_the_plan_names_is_reported(self) -> None:
        pr.apply_budgets()
        rows = {row["provider"]: row for row in pr.snapshot()["providers"]}
        omdb = rows["omdb"]
        for field in ("remaining_quota", "reset_at", "cooldown_until",
                      "daily_soft_budget", "average_latency_ms",
                      "last_http_status", "breaker_open_until",
                      "consecutive_failures"):
            self.assertIn(field, omdb)

    def test_the_snapshot_says_whether_anyone_can_answer(self) -> None:
        self.assertTrue(pr.snapshot()["any_movie_metadata_available"])
        for provider in pr.DEFAULT_PROVIDERS:
            ph._mark_unavailable(
                ph._provider_record(provider), ph.STATUS_COOLING_DOWN, 3600)
        self.assertFalse(pr.snapshot()["any_movie_metadata_available"])


class MetadataCacheVersionTests(unittest.TestCase):
    """ধারা ৪.৭ - cleaner_version and classifier_version in the key."""

    def setUp(self) -> None:
        from scanner import movie_metadata_cache

        self.mc = movie_metadata_cache
        self.now = dt.datetime(2026, 9, 20, tzinfo=dt.timezone.utc)

    def _current(self, **extra):
        return {
            "metadata_updated_at": self.now.isoformat(),
            "metadata_schema": self.mc.METADATA_SCHEMA,
            "cleaner_version": self.mc.CLEANER_VERSION,
            "classifier_version": self.mc.classifier_version(),
            **extra,
        }

    def test_a_record_resolved_under_the_current_rules_is_not_due(self) -> None:
        self.assertFalse(
            self.mc._is_due_for_refresh(self._current(), self.now))

    def test_an_older_title_cleaner_makes_a_record_due(self) -> None:
        """ধাপ ৩ changes the cleaner. Without this key every answer the old
        cleaner produced sits behind a 90-day TTL and the change is invisible."""
        record = self._current(cleaner_version=self.mc.CLEANER_VERSION - 1)
        self.assertTrue(self.mc._is_due_for_refresh(record, self.now))

    def test_an_older_classifier_makes_a_record_due(self) -> None:
        record = self._current(classifier_version=0)
        self.assertTrue(self.mc._is_due_for_refresh(record, self.now))

    def test_an_older_field_set_still_makes_a_record_due(self) -> None:
        record = self._current(metadata_schema=self.mc.METADATA_SCHEMA - 1)
        self.assertTrue(self.mc._is_due_for_refresh(record, self.now))

    def test_a_record_predating_the_keys_is_due_exactly_once(self) -> None:
        """It carries neither key. The schema bump already forces one re-read
        for those, and making them due twice would spend the budget saying the
        same thing."""
        record = {"metadata_updated_at": self.now.isoformat(),
                  "metadata_schema": self.mc.METADATA_SCHEMA}
        self.assertFalse(self.mc._is_due_for_refresh(record, self.now))

    def test_the_classifier_version_is_read_from_its_own_module(self) -> None:
        """One number, so it cannot drift from the rules it describes."""
        from scanner import series_signal

        self.assertEqual(
            self.mc.classifier_version(), series_signal.CLASSIFIER_VERSION)

    def test_an_answered_lookup_stamps_all_three_versions(self) -> None:
        record: dict = {}
        self.mc._stamp_versions(record)
        self.assertEqual(record["metadata_schema"], self.mc.METADATA_SCHEMA)
        self.assertEqual(record["cleaner_version"], self.mc.CLEANER_VERSION)
        self.assertEqual(
            record["classifier_version"], self.mc.classifier_version())


if __name__ == "__main__":
    unittest.main()
