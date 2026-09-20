"""ধাপ ৮ / S-02 - the separate path for repairing one broken link.

S-02 in one line: a single dead link could only be fixed by running the whole
catalogue, and that run's budget is then divided across 2,475 links - so repair
was effectively a matter of luck.

The rules that carry the most weight here:

  * **severity is about the viewer.** P0 is "nobody can watch this", which is
    why it outranks a dead primary with a working backup - the second is a
    quality problem and the first is an outage. A queue ordered by age would
    spend its budget on P3s while a P0 waited.
  * **the queue is derived, not accumulated.** A card that has quietly healed
    leaves without anybody remembering to remove it; a queue that only grows is
    one nobody trusts.
  * **being queued is not a disposition.** ধারা ৪.০ has two axes: a card stays
    `published_movie` while its link sits here as `confirmed_unavailable`.
  * **a repair run writes state, never a published surface.** One writer, and
    it is the scan's publisher.
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
from scanner import movie_repair_queue as rq  # noqa: E402

NOW = datetime(2026, 9, 20, tzinfo=timezone.utc)


class SeverityTests(unittest.TestCase):
    """ধারা ৪.৫ - and the ordering it implies."""

    def test_no_live_link_anywhere_is_p0(self) -> None:
        self.assertEqual(
            rq.classify(primary_healthy=False, live_backup_count=0),
            rq.P0_NO_LIVE_LINK,
        )

    def test_a_dead_primary_with_a_live_backup_is_p1(self) -> None:
        self.assertEqual(
            rq.classify(primary_healthy=False, live_backup_count=2),
            rq.P1_PRIMARY_DEAD_BACKUP_LIVE,
        )

    def test_a_dead_backup_with_a_live_primary_is_p2(self) -> None:
        self.assertEqual(
            rq.classify(primary_healthy=True, live_backup_count=1,
                        dead_backup_count=1),
            rq.P2_BACKUP_DEAD_PRIMARY_LIVE,
        )

    def test_restricted_or_uncertain_is_p3(self) -> None:
        self.assertEqual(
            rq.classify(primary_healthy=True, live_backup_count=1,
                        restricted=True),
            rq.P3_RESTRICTED_OR_UNCERTAIN,
        )

    def test_a_healthy_card_is_not_queued_at_all(self) -> None:
        self.assertIsNone(
            rq.classify(primary_healthy=True, live_backup_count=2))


class OrderingTests(unittest.TestCase):
    """Severity first, age only within it."""

    def _queue(self):
        queue = {"entries": {}}
        rq.enqueue(queue, content_id="old-p3", broken_link_id="a",
                   severity=rq.P3_RESTRICTED_OR_UNCERTAIN,
                   now=NOW - timedelta(days=7))
        rq.enqueue(queue, content_id="new-p0", broken_link_id="b",
                   severity=rq.P0_NO_LIVE_LINK, now=NOW)
        rq.enqueue(queue, content_id="old-p1", broken_link_id="c",
                   severity=rq.P1_PRIMARY_DEAD_BACKUP_LIVE,
                   now=NOW - timedelta(days=3))
        return queue

    def test_a_fresh_outage_outranks_a_week_old_quality_problem(self) -> None:
        order = [entry["content_id"] for entry in rq.due(self._queue(), now=NOW)]
        self.assertEqual(order, ["new-p0", "old-p1", "old-p3"])

    def test_age_decides_within_one_severity(self) -> None:
        queue = {"entries": {}}
        rq.enqueue(queue, content_id="later", broken_link_id="a",
                   severity=rq.P0_NO_LIVE_LINK, now=NOW)
        rq.enqueue(queue, content_id="earlier", broken_link_id="b",
                   severity=rq.P0_NO_LIVE_LINK, now=NOW - timedelta(hours=5))
        order = [entry["content_id"] for entry in rq.due(queue, now=NOW)]
        self.assertEqual(order, ["earlier", "later"])

    def test_a_limit_is_what_keeps_a_repair_run_small(self) -> None:
        """The whole point of S-02 is that this is not a catalogue scan."""
        self.assertEqual(len(rq.due(self._queue(), limit=2, now=NOW)), 2)


class BackoffTests(unittest.TestCase):
    def setUp(self) -> None:
        self.queue = {"entries": {}}
        rq.enqueue(self.queue, content_id="c", broken_link_id="l",
                   severity=rq.P1_PRIMARY_DEAD_BACKUP_LIVE, now=NOW)
        self.key = rq.entry_key("c", "l")

    def test_a_failed_attempt_backs_off(self) -> None:
        hours = []
        for _ in range(5):
            entry = rq.record_attempt(
                self.queue, self.key, repaired=False, now=NOW)
            scheduled = datetime.fromisoformat(entry["next_attempt_at"])
            hours.append(round((scheduled - NOW).total_seconds() / 3600, 1))
        self.assertEqual(hours, [1.0, 4.0, 12.0, 24.0, 48.0])

    def test_an_outage_is_retried_sooner_than_the_first_full_hour(self) -> None:
        queue = {"entries": {}}
        rq.enqueue(queue, content_id="c", broken_link_id="l",
                   severity=rq.P0_NO_LIVE_LINK, now=NOW)
        entry = rq.record_attempt(
            queue, rq.entry_key("c", "l"), repaired=False, now=NOW)
        scheduled = datetime.fromisoformat(entry["next_attempt_at"])
        self.assertLess((scheduled - NOW).total_seconds(), 3600)

    def test_an_entry_is_not_offered_before_its_next_attempt(self) -> None:
        rq.record_attempt(self.queue, self.key, repaired=False, now=NOW)
        self.assertEqual(rq.due(self.queue, now=NOW), [])
        self.assertEqual(
            len(rq.due(self.queue, now=NOW + timedelta(hours=2))), 1)

    def test_a_repaired_link_leaves_the_queue(self) -> None:
        self.assertIsNone(
            rq.record_attempt(self.queue, self.key, repaired=True, now=NOW))
        self.assertEqual(self.queue["entries"], {})

    def test_requeuing_does_not_restart_the_backoff(self) -> None:
        """A link that has failed five repairs has failed five repairs, and
        resetting the counter every scan would make the backoff a fixed
        interval."""
        for _ in range(3):
            rq.record_attempt(self.queue, self.key, repaired=False, now=NOW)
        rq.enqueue(self.queue, content_id="c", broken_link_id="l",
                   severity=rq.P1_PRIMARY_DEAD_BACKUP_LIVE, now=NOW)
        self.assertEqual(self.queue["entries"][self.key]["attempts"], 3)

    def test_an_exhausted_entry_is_kept_but_not_offered(self) -> None:
        """It is evidence. Dropping it loses the only record that this content
        ever had a working link."""
        for _ in range(rq.MAX_ATTEMPTS):
            rq.record_attempt(self.queue, self.key, repaired=False, now=NOW)
        self.assertIn(self.key, self.queue["entries"])
        self.assertEqual(
            rq.due(self.queue, now=NOW + timedelta(days=30)), [])
        self.assertEqual(rq.summarise(self.queue)["exhausted"], 1)

    def test_the_latest_severity_wins(self) -> None:
        """A backup dying while the primary is fine is a P2; the same card
        losing its primary later is a P0."""
        rq.enqueue(self.queue, content_id="c", broken_link_id="l",
                   severity=rq.P0_NO_LIVE_LINK, now=NOW)
        self.assertEqual(
            self.queue["entries"][self.key]["severity"], rq.P0_NO_LIVE_LINK)


class DerivedFromTheCatalogueTests(unittest.TestCase):
    """The queue is re-derived, so a healed card leaves it by itself."""

    def _card(self, identity, url, backups=()):
        return {
            "id": identity, "name": identity, "url": url,
            "source_id": "src",
            "backups": [{"url": backup, "source_id": "src"} for backup in backups],
        }

    def _health(self, **statuses):
        store = {"links": {}}
        for url, status in statuses.items():
            lh.record_result(
                store, identity=lh.stream_id(url, source_id="src"), url=url,
                content_id="film", verification_status=status,
                http_status=404 if status == "failed" else 0, now=NOW)
        return store

    def test_a_card_with_nothing_live_is_queued_as_p0(self) -> None:
        card = self._card("film", "https://c.test/a.mkv")
        store = self._health(**{"https://c.test/a.mkv": "failed"})
        queue = {"entries": {}}
        counts = rq.refresh_from_catalogue(queue, [("mix", card)], store, now=NOW)
        self.assertEqual(counts[rq.P0_NO_LIVE_LINK], 1)
        self.assertEqual(rq.summarise(queue)["no_live_link"], 1)

    def test_a_dead_primary_with_a_live_backup_is_queued_as_p1(self) -> None:
        card = self._card("film", "https://c.test/a.mkv",
                          ["https://c.test/b.mkv"])
        store = self._health(**{"https://c.test/a.mkv": "failed",
                                "https://c.test/b.mkv": "verified_global"})
        queue = {"entries": {}}
        counts = rq.refresh_from_catalogue(queue, [("mix", card)], store, now=NOW)
        self.assertEqual(counts[rq.P1_PRIMARY_DEAD_BACKUP_LIVE], 1)

    def test_a_healed_card_leaves_the_queue_without_being_told(self) -> None:
        card = self._card("film", "https://c.test/a.mkv")
        broken = self._health(**{"https://c.test/a.mkv": "failed"})
        queue = {"entries": {}}
        rq.refresh_from_catalogue(queue, [("mix", card)], broken, now=NOW)
        self.assertEqual(rq.summarise(queue)["queued"], 1)

        healed = self._health(**{"https://c.test/a.mkv": "verified_global"})
        counts = rq.refresh_from_catalogue(queue, [("mix", card)], healed, now=NOW)
        self.assertEqual(counts["resolved"], 1)
        self.assertEqual(rq.summarise(queue)["queued"], 0)

    def test_an_unchecked_link_is_not_a_fault(self) -> None:
        """Every link starts unknown. Queuing those would put the whole
        catalogue in the repair queue on day one."""
        card = self._card("film", "https://c.test/a.mkv")
        queue = {"entries": {}}
        counts = rq.refresh_from_catalogue(
            queue, [("mix", card)], {"links": {}}, now=NOW)
        self.assertEqual(rq.summarise(queue)["queued"], 0)
        self.assertEqual(sum(counts.get(s, 0) for s in rq.SEVERITIES), 0)

    def test_a_healthy_card_is_never_queued(self) -> None:
        card = self._card("film", "https://c.test/a.mkv",
                          ["https://c.test/b.mkv"])
        store = self._health(**{"https://c.test/a.mkv": "verified_global",
                                "https://c.test/b.mkv": "verified_global"})
        queue = {"entries": {}}
        rq.refresh_from_catalogue(queue, [("mix", card)], store, now=NOW)
        self.assertEqual(rq.summarise(queue)["queued"], 0)

    def test_nothing_about_the_card_is_changed(self) -> None:
        """ধারা ৪.০ - two axes. Being queued is a stream-health fact."""
        card = self._card("film", "https://c.test/a.mkv")
        before = json.dumps(card, sort_keys=True)
        store = self._health(**{"https://c.test/a.mkv": "failed"})
        rq.refresh_from_catalogue({"entries": {}}, [("mix", card)], store, now=NOW)
        self.assertEqual(json.dumps(card, sort_keys=True), before)


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.path = Path(tempfile.mkdtemp()) / "movie-repair-queue.json"

    def test_a_missing_queue_is_empty_not_an_error(self) -> None:
        self.assertEqual(rq.load(self.path)["entries"], {})

    def test_a_damaged_queue_is_empty_not_a_scan_failure(self) -> None:
        self.path.write_text("{not json", encoding="utf-8")
        self.assertEqual(rq.load(self.path)["entries"], {})

    def test_the_queue_round_trips_with_its_severity_counts(self) -> None:
        queue = {"entries": {}}
        rq.enqueue(queue, content_id="c", broken_link_id="l",
                   severity=rq.P0_NO_LIVE_LINK, now=NOW)
        self.assertTrue(rq.save(queue, self.path))
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(payload["by_severity"][rq.P0_NO_LIVE_LINK], 1)
        self.assertEqual(len(rq.load(self.path)["entries"]), 1)

    def test_every_field_the_plan_names_is_present(self) -> None:
        queue = {"entries": {}}
        entry = rq.enqueue(queue, content_id="c", broken_link_id="l",
                           severity=rq.P0_NO_LIVE_LINK, reason="why", now=NOW)
        for field in ("content_id", "broken_link_id", "severity", "reason",
                      "queued_at", "attempts", "next_attempt_at"):
            self.assertIn(field, entry)


class WorkflowContractTests(unittest.TestCase):
    """ধারা ৪.৮ - one new workflow, and what it may write."""

    PATH = ROOT / ".github" / "workflows" / "movie-repair.yml"

    def setUp(self) -> None:
        self.text = self.PATH.read_text(encoding="utf-8")

    def test_it_runs_every_four_to_six_hours(self) -> None:
        self.assertIn("cron:", self.text)
        self.assertIn("*/5", self.text)

    def test_it_has_its_own_concurrency_group(self) -> None:
        """A repair run must not queue behind a forty-minute catalogue scan -
        running between scans is the whole point of it existing."""
        self.assertIn("group: movie-repair", self.text)

    def test_it_refuses_to_write_published_data(self) -> None:
        """One writer, and it is the scan's publisher. ধারা ৪.৯ rule 2."""
        self.assertIn("git diff --quiet -- data/", self.text)
        self.assertIn("a repair run must not write data/", self.text)

    def test_it_only_commits_the_two_state_files(self) -> None:
        self.assertIn(
            "git add state/movie-repair-queue.json state/movie-link-health.json",
            self.text,
        )

    def test_it_names_a_missing_module_rather_than_failing_blankly(self) -> None:
        for required in ("scanner/movie_repair_queue.py",
                         "scanner/movie_link_health.py",
                         "scripts/movie-repair-run.py"):
            self.assertIn(required, self.text)

    def test_the_runner_script_defaults_to_writing_nothing(self) -> None:
        source = (ROOT / "scripts" / "movie-repair-run.py").read_text(
            encoding="utf-8")
        self.assertIn('"--apply", action="store_true"', source)
        self.assertIn("if arguments.apply:", source)


class ScanWiringTests(unittest.TestCase):
    SOURCE = (ROOT / "scanner" / "fast_pipeline.py").read_text(encoding="utf-8")

    def test_the_queue_is_refreshed_after_the_ledger_is_written(self) -> None:
        ledger = self.SOURCE.index("health.save(store)")
        refresh = self.SOURCE.index("_refresh_repair_queue(store)")
        self.assertLess(ledger, refresh)

    def test_a_queue_failure_never_costs_a_scan(self) -> None:
        marker = self.SOURCE.index("def _refresh_repair_queue(")
        window = self.SOURCE[marker:marker + 1600]
        self.assertIn("repair queue refresh skipped", window)


if __name__ == "__main__":
    unittest.main()
