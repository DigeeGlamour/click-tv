"""PART 04: TMDB rate-limit, key and retry policy.

Every test here drives scanner/provider_health.py through mocked HTTP
statuses - no request leaves the machine, and time.sleep is patched so the
2s/5s/15s/30s ladder is asserted on rather than waited through.

The three behaviours worth stating plainly, because they are the ones that
would otherwise be got wrong under pressure:

  - A 429 is respected, not evaded. There is no second key, no rotation,
    no "try the other account" path anywhere in this module, and the test
    below asserts a 429 produces backoff-then-stop rather than a retry
    against different credentials.
  - A 401/403 is never retried. A rejected credential does not improve in
    30 seconds, and hammering a suspended key is how it stays suspended.
  - A provider that runs out of retries stops being asked for the rest of
    the run, so one bad provider day costs one ladder rather than one
    ladder per movie - which is what keeps the scan inside its time budget.
"""
import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import movie_metadata_cache as mc  # noqa: E402
from scanner import provider_health as ph  # noqa: E402


class PolicyTestCase(unittest.TestCase):
    """Isolated state file + no real sleeping + no real throttling."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = str(Path(self._tmp.name) / "provider-health.json")

        self._original_default = ph.DEFAULT_PATH
        ph.DEFAULT_PATH = self.path
        self.addCleanup(lambda: setattr(ph, "DEFAULT_PATH", self._original_default))

        ph.reset()
        self.addCleanup(ph.reset)

        self.slept = []
        sleep_patch = patch.object(ph.time, "sleep", side_effect=self.slept.append)
        sleep_patch.start()
        self.addCleanup(sleep_patch.stop)

    def _responses(self, *responses):
        """Feed _perform_request a fixed sequence of (status, payload, retry_after)."""
        queue = list(responses)
        calls = []

        def fake_perform(url, headers=None, body=None, timeout=None):
            calls.append(url)
            return queue.pop(0) if queue else (0, {}, None)

        self.calls = calls
        return patch.object(ph, "_perform_request", side_effect=fake_perform)


class SuccessAndThrottleTests(PolicyTestCase):
    def test_a_successful_request_returns_the_payload_and_counts_once(self):
        with self._responses((200, {"ok": True}, None)):
            payload = ph.request_json("tmdb", "https://api.test/x")
        self.assertEqual(payload, {"ok": True})
        record = ph.metrics()["providers"]["tmdb"]
        self.assertEqual(record["requests"], 1)
        self.assertEqual(record["successes"], 1)
        self.assertEqual(record["retries"], 0)
        self.assertEqual(record["status"], ph.STATUS_HEALTHY)

    def test_requests_to_one_provider_are_paced(self):
        """The minimum interval is held between two calls to one provider."""
        with self._responses((200, {}, None), (200, {}, None)):
            ph.request_json("tmdb", "https://api.test/1")
            ph.request_json("tmdb", "https://api.test/2")
        # The second call had to wait; the first did not.
        self.assertTrue(any(delay > 0 for delay in self.slept))


class RateLimitTests(PolicyTestCase):
    def test_retry_after_header_is_respected_over_the_ladder(self):
        with self._responses((429, {}, 7.0), (200, {"ok": True}, None)):
            payload = ph.request_json("tmdb", "https://api.test/x")
        self.assertEqual(payload, {"ok": True})
        self.assertIn(7.0, self.slept)
        self.assertNotIn(2.0, self.slept)
        record = ph.metrics()["providers"]["tmdb"]
        self.assertEqual(record["rate_limited"], 1)
        self.assertEqual(record["retries"], 1)
        self.assertTrue(record.get("last_429"))

    def test_without_retry_after_the_bounded_ladder_is_used_in_order(self):
        with self._responses(*[(429, {}, None)] * 5):
            payload = ph.request_json("tmdb", "https://api.test/x")
        self.assertEqual(payload, {})
        backoffs = [delay for delay in self.slept if delay in (2.0, 5.0, 15.0, 30.0)]
        self.assertEqual(backoffs, [2.0, 5.0, 15.0, 30.0])

    def test_the_ladder_is_bounded_and_then_the_provider_is_left_alone(self):
        with self._responses(*[(429, {}, None)] * 5) as _:
            ph.request_json("tmdb", "https://api.test/x")
        record = ph.metrics()["providers"]["tmdb"]
        self.assertEqual(record["status"], ph.STATUS_COOLING_DOWN)
        self.assertEqual(record["requests"], 5, "four waits, five attempts, then stop")
        self.assertTrue(record.get("next_retry_at"))

        # Every later call short-circuits: one bad day costs one ladder.
        with self._responses((200, {"ok": True}, None)):
            self.assertEqual(ph.request_json("tmdb", "https://api.test/y"), {})
            self.assertEqual(self.calls, [], "no request should have been attempted")
        self.assertEqual(ph.metrics()["providers"]["tmdb"]["skipped_unavailable"], 1)

    def test_a_429_never_triggers_a_second_key_or_account(self):
        """The design position, asserted: backoff is the only 429 response."""
        source = Path(ROOT / "scanner" / "provider_health.py").read_text(encoding="utf-8")
        lowered = source.casefold()
        for forbidden in ("rotate", "rotation", "key_pool", "next_key", "alternate_key"):
            self.assertNotIn(
                f"def {forbidden}", lowered, f"no key-rotation mechanism may exist ({forbidden})"
            )
        with self._responses((429, {}, None), (200, {"ok": True}, None)):
            ph.request_json("tmdb", "https://api.test/x")
        # One credential, one provider record - nothing swapped underneath.
        self.assertEqual(list(ph.metrics()["providers"]), ["tmdb"])


class TransientServerErrorTests(PolicyTestCase):
    def test_503_is_retried_then_succeeds(self):
        with self._responses((503, {}, None), (200, {"ok": True}, None)):
            payload = ph.request_json("tmdb", "https://api.test/x")
        self.assertEqual(payload, {"ok": True})
        record = ph.metrics()["providers"]["tmdb"]
        self.assertEqual(record["server_errors"], 1)
        self.assertEqual(record["successes"], 1)

    def test_500_502_504_are_all_treated_as_transient(self):
        for status in (500, 502, 504):
            ph.reset()
            with self._responses((status, {}, None), (200, {"ok": True}, None)):
                payload = ph.request_json("tmdb", "https://api.test/x")
            self.assertEqual(payload, {"ok": True}, f"HTTP {status} should be retried")

    def test_a_transport_failure_is_retried_then_gives_up_bounded(self):
        with self._responses(*[(0, {}, None)] * 5):
            payload = ph.request_json("omdb", "https://api.test/x")
        self.assertEqual(payload, {})
        record = ph.metrics()["providers"]["omdb"]
        self.assertEqual(record["transport_errors"], 5)
        self.assertEqual(record["status"], ph.STATUS_COOLING_DOWN)

    def test_a_404_is_a_real_answer_and_is_never_retried(self):
        with self._responses((404, {}, None)) as _:
            payload = ph.request_json("omdb", "https://api.test/x")
        self.assertEqual(payload, {})
        record = ph.metrics()["providers"]["omdb"]
        self.assertEqual(record["requests"], 1)
        self.assertEqual(record["retries"], 0)
        self.assertEqual(record["not_found"], 1)
        self.assertEqual(record["status"], ph.STATUS_HEALTHY, "a 404 is not ill health")


class AuthFailureTests(PolicyTestCase):
    def test_401_stops_immediately_with_no_retry_storm(self):
        with self._responses((401, {}, None), (200, {"ok": True}, None)):
            payload = ph.request_json("tmdb", "https://api.test/x")
        self.assertEqual(payload, {})
        record = ph.metrics()["providers"]["tmdb"]
        self.assertEqual(record["requests"], 1, "exactly one attempt")
        self.assertEqual(record["retries"], 0)
        self.assertEqual(record["auth_failures"], 1)
        self.assertEqual(record["status"], ph.STATUS_UNHEALTHY_AUTH)
        self.assertEqual(self.slept, [], "an auth failure must never sleep and retry")

    def test_403_marks_the_provider_unhealthy_and_others_carry_on(self):
        with self._responses((403, {}, None)):
            ph.request_json("tmdb", "https://api.test/x")
        self.assertFalse(ph.is_available("tmdb"))
        self.assertTrue(ph.is_available("omdb"), "one broken key must not disable the chain")
        self.assertTrue(ph.any_metadata_provider_available())

    def test_every_provider_down_is_reported_as_nothing_available(self):
        for provider in ph.METADATA_PROVIDERS:
            with self._responses((401, {}, None)):
                ph.request_json(provider, "https://api.test/x")
        self.assertFalse(ph.any_metadata_provider_available())


class HealthPersistenceTests(PolicyTestCase):
    def test_health_is_persisted_atomically_and_reloads(self):
        with self._responses((429, {}, None), (200, {}, None)):
            ph.request_json("tmdb", "https://api.test/x")
        self.assertTrue(ph.save())
        self.assertEqual(list(Path(self._tmp.name).glob(".*.tmp")), [])

        on_disk = json.loads(Path(self.path).read_text(encoding="utf-8"))
        self.assertEqual(on_disk["providers"]["tmdb"]["rate_limited"], 1)
        self.assertTrue(on_disk.get("updated_at"))

    def test_a_corrupt_health_file_does_not_crash_a_scan(self):
        Path(self.path).write_text("{not json", encoding="utf-8")
        ph.reset()
        self.assertTrue(ph.any_metadata_provider_available())
        self.assertEqual(ph.metrics()["providers"], {})

    def test_a_persisted_cooldown_is_respected_on_the_next_run(self):
        with self._responses(*[(429, {}, None)] * 5):
            ph.request_json("tmdb", "https://api.test/x")
        ph.save()

        ph.reset()  # a fresh process, reading the file back
        self.assertFalse(ph.is_available("tmdb"), "the 429 cooldown outlives the run")
        self.assertTrue(ph.is_available("omdb"))

    def test_an_expired_cooldown_lets_the_provider_back_in(self):
        record = ph._provider_record("tmdb")
        record["status"] = ph.STATUS_COOLING_DOWN
        record["next_retry_at"] = (
            dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=1)
        ).isoformat()
        self.assertTrue(ph.is_available("tmdb"))

    def test_cache_metrics_are_recorded_alongside_request_metrics(self):
        ph.record_cache_stats({"cached_hits": 900, "fetched": 40, "failed": 2})
        ph.record_cache_stats({"cached_hits": 100, "fetched": 0, "failed": 0})
        cache = ph.metrics()["cache"]
        self.assertEqual(cache["cached_hits"], 1000)
        self.assertEqual(cache["fetched"], 40)
        self.assertEqual(cache["failed"], 2)

    def test_the_summary_line_reports_cache_hits_requests_and_429s(self):
        ph.record_cache_stats({"cached_hits": 12, "fetched": 3})
        with self._responses((429, {}, None), (200, {}, None)):
            ph.request_json("tmdb", "https://api.test/x")
        line = ph.summary_line()
        self.assertIn("cache hits 12", line)
        self.assertIn("tmdb", line)
        self.assertIn("429", line)

    def test_no_secret_value_is_ever_written_to_the_health_file(self):
        with patch.dict(
            "os.environ", {"TMDB_API_KEY": "super-secret-key-value"}, clear=False
        ):
            with self._responses((200, {}, None)):
                ph.request_json(
                    "tmdb",
                    "https://api.test/x?api_key=super-secret-key-value",
                    headers={"Authorization": "Bearer super-secret-key-value"},
                )
        ph.save()
        on_disk = Path(self.path).read_text(encoding="utf-8")
        self.assertNotIn("super-secret-key-value", on_disk)
        self.assertNotIn("api_key", on_disk)


class CacheFirstTests(unittest.TestCase):
    """PART 04's cache-first rule, at the enrichment layer."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = str(Path(self._tmp.name) / "movie-metadata-cache.json")
        self.now = dt.datetime(2026, 9, 11, tzinfo=dt.timezone.utc)

    def test_a_cached_movie_costs_no_lookup_at_all(self):
        mc.save(
            {
                "version": 1,
                "movies": {
                    "tmdb_id:278": {
                        "genres": ["Drama"],
                        "rating": 9.3,
                        "metadata_updated_at": self.now.isoformat(),
                        "release_date": "1994-09-23",
                    }
                },
            },
            self.path,
        )
        calls = []
        summary = mc.enrich(
            [{"tmdb_id": 278, "name": "Shawshank"}],
            path=self.path,
            now=self.now,
            lookup=lambda movie: calls.append(movie) or {},
        )
        self.assertEqual(calls, [], "a fresh cache entry must not be looked up again")
        self.assertEqual(summary["cached_hits"], 1)
        self.assertEqual(summary["fetched"], 0)

    def test_settled_metadata_gets_the_long_ttl_and_a_recent_release_the_short_one(self):
        stable = {"release_date": "2011-03-04", "metadata_updated_at": "2026-07-01T00:00:00+00:00"}
        recent = {"release_date": "2026-08-01", "metadata_updated_at": "2026-07-01T00:00:00+00:00"}
        # 72 days old on 2026-09-11: past the 14-day recent TTL, inside the 90-day stable one.
        self.assertFalse(mc._is_due_for_refresh(stable, self.now))
        self.assertTrue(mc._is_due_for_refresh(recent, self.now))

    def test_a_never_resolved_movie_wins_the_budget_over_a_refresh(self):
        mc.save(
            {
                "version": 1,
                "movies": {
                    "tmdb_id:1": {
                        "genres": ["Drama"],
                        "release_date": "2026-08-01",
                        "metadata_updated_at": "2026-01-01T00:00:00+00:00",
                    }
                },
            },
            self.path,
        )
        looked_up = []

        def lookup(movie):
            looked_up.append(movie.get("name"))
            return {"tmdb_id": 99, "genres": ["Action"], "metadata_source": "tmdb"}

        summary = mc.enrich(
            [
                {"tmdb_id": 1, "name": "Stale But Known"},
                {"name": "Never Resolved (2026)"},
            ],
            path=self.path,
            now=self.now,
            lookup=lookup,
            budget=1,
        )
        self.assertEqual(looked_up, ["Never Resolved (2026)"])
        self.assertEqual(summary["fetched"], 1)
        self.assertEqual(summary["refreshed"], 0)
        self.assertEqual(summary["skipped_budget"], 1)

    def test_a_refresh_runs_when_budget_is_left_over(self):
        mc.save(
            {
                "version": 1,
                "movies": {
                    "tmdb_id:1": {
                        "genres": ["Drama"],
                        "release_date": "2026-08-01",
                        "metadata_updated_at": "2026-01-01T00:00:00+00:00",
                    }
                },
            },
            self.path,
        )
        summary = mc.enrich(
            [{"tmdb_id": 1, "name": "Stale But Known"}],
            path=self.path,
            now=self.now,
            lookup=lambda movie: {"rating": 7.7, "metadata_source": "tmdb"},
            budget=5,
        )
        self.assertEqual(summary["refreshed"], 1)

    def test_all_providers_down_abandons_lookups_without_poisoning_the_cache(self):
        """An outage must not leave a 7-day cooldown on untried movies."""
        calls = []
        summary = mc.enrich(
            [{"name": "Some Film (2026)"}],
            path=self.path,
            now=self.now,
            lookup=lambda movie: calls.append(movie) or None,
            availability=lambda: False,
        )
        self.assertEqual(calls, [])
        self.assertEqual(summary["skipped_unavailable"], 1)
        self.assertEqual(summary["failed"], 0)
        self.assertEqual(mc.load(self.path)["movies"], {}, "nothing recorded as attempted")


if __name__ == "__main__":
    unittest.main()
