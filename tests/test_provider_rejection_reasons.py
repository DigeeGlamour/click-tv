"""PROMPT 29 - `provider_rejected` says what it is made of.

FINAL_3, অংশ ৪ঘ: ninety-eight candidates from the fixture-authority feeds were
turned away and the report carried one number. The code has three named
refusals, the audit for this prompt found a fourth, and if five of those
ninety-eight had been real cricket or football fixtures there was no way to
find out.

Nothing here refuses anything new. Each reason is an existing `return None` in
`_provider_fixture_item`, and the aggregate is unchanged - it is now
accompanied by the breakdown that adds up to it, and by the name of every
rejected cricket or football fixture, the way sport_filter.py already names
what it discards.
"""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scanner import schedule_resolver as sr  # noqa: E402

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)


def _item(**extra):
    item = {"name": "India Vs Pakistan", "competition": "Asia Cup",
            "source_id": "srhady-bingstream"}
    item.update(extra)
    return item


class EveryExistingRefusalHasAName(unittest.TestCase):
    def _refuse(self, item, source_time=None):
        rejection = {}
        result = sr._provider_fixture_item(
            item, source_time, NOW, 4, rejection=rejection)
        self.assertIsNone(result, "expected this candidate to be refused")
        return rejection

    def test_a_finished_fixture_is_named_as_a_dead_status(self):
        for status in ("ENDED", "FINISHED", "CANCELLED", "POSTPONED"):
            rejection = self._refuse(_item(status=status), NOW + timedelta(hours=1))
            self.assertEqual(sr.REJECT_DEAD_STATUS, rejection["reason"], status)
            self.assertEqual(status, rejection["status"])

    def test_a_status_that_is_neither_live_nor_upcoming_is_its_own_reason(self):
        rejection = self._refuse(_item(status="INTERRUPTED"),
                                 NOW + timedelta(hours=1))
        self.assertEqual(sr.REJECT_NOT_LIVE_OR_UPCOMING, rejection["reason"])

    def test_no_kickoff_and_not_playing_is_its_own_reason(self):
        rejection = self._refuse(_item(status="UPCOMING"), None)
        self.assertEqual(sr.REJECT_NO_KICKOFF, rejection["reason"])

    def test_the_fourth_refusal_the_audit_found_is_reported_too(self):
        """A not-started fixture whose kickoff is long past: the feed is stale.

        FINAL_3 names three reasons; this branch is in the code as well, at
        `is_upcoming and schedule_status == "ENDED"`. Leaving it unnamed would
        put real refusals in an `unrecorded` bucket.
        """
        rejection = self._refuse(_item(status="NS"), NOW - timedelta(hours=12))
        self.assertEqual(sr.REJECT_UPCOMING_ALREADY_PAST, rejection["reason"])

    def test_every_named_reason_is_one_the_code_can_actually_produce(self):
        produced = {
            self._refuse(_item(status="ENDED"), NOW)["reason"],
            self._refuse(_item(status="INTERRUPTED"), NOW)["reason"],
            self._refuse(_item(status="UPCOMING"), None)["reason"],
            self._refuse(_item(status="NS"), NOW - timedelta(hours=12))["reason"],
        }
        self.assertEqual(set(sr.PROVIDER_REJECT_REASONS), produced)


class TheBreakdownReconcilesWithTheAggregate(unittest.TestCase):
    def _resolve(self, candidates):
        return sr.enrich_event_candidates(
            candidates,
            fixture_path="config/does-not-exist.json",
            now=NOW,
            authority_source_ids={"srhady-bingstream"},
        )

    def _candidates(self):
        return [
            _item(status="ENDED", start_time=(NOW - timedelta(hours=3)).isoformat()),
            _item(name="Genoa Vs Como", status="FINISHED",
                  start_time=(NOW - timedelta(hours=4)).isoformat()),
            _item(name="Some Handball Thing", status="INTERRUPTED",
                  start_time=(NOW + timedelta(hours=2)).isoformat()),
            _item(name="Ipswich Vs Liverpool", status="UPCOMING"),
            _item(name="Bengaluru FC Vs Mohun Bagan", status="NS",
                  start_time=(NOW - timedelta(hours=20)).isoformat()),
            _item(name="Fiorentina Vs Torino", status="UPCOMING",
                  start_time=(NOW + timedelta(hours=6)).isoformat()),
        ]

    def test_the_counts_sum_to_the_number_that_was_already_reported(self):
        _, stats = self._resolve(self._candidates())
        self.assertEqual(5, stats["provider_rejected"])
        self.assertEqual(stats["provider_rejected"],
                         sum(stats["provider_rejected_reasons"].values()))

    def test_the_accepted_fixture_is_still_accepted(self):
        """Reporting a refusal must not become a refusal."""
        resolved, stats = self._resolve(self._candidates())
        self.assertEqual(1, stats["provider_fixture"])
        self.assertIn("Fiorentina Vs Torino",
                      [str(item.get("name")) for item in resolved])

    def test_every_reason_bucket_exists_even_at_zero(self):
        _, stats = self._resolve([_item(status="UPCOMING",
                                        start_time=(NOW + timedelta(hours=6)).isoformat())])
        self.assertEqual(set(sr.PROVIDER_REJECT_REASONS),
                         set(stats["provider_rejected_reasons"]))
        self.assertEqual(0, sum(stats["provider_rejected_reasons"].values()))

    def test_the_source_of_each_refusal_is_recorded(self):
        _, stats = self._resolve(self._candidates())
        by_source = stats["provider_rejected_by_source"]["srhady-bingstream"]
        self.assertEqual(5, by_source["total"])
        self.assertEqual(
            5,
            sum(count for reason, count in by_source.items() if reason != "total"),
        )


class RejectedCricketAndFootballAreNamed(unittest.TestCase):
    def _stats(self):
        _, stats = sr.enrich_event_candidates(
            [
                _item(name="Genoa Vs Como", competition="Italian Serie A",
                      status="FINISHED",
                      start_time=(NOW - timedelta(hours=4)).isoformat()),
                _item(name="India Vs Pakistan", competition="Asia Cup",
                      status="ENDED",
                      start_time=(NOW - timedelta(hours=6)).isoformat()),
                _item(name="Las Vegas Aces Vs Seattle Storm", competition="WNBA",
                      status="ENDED",
                      start_time=(NOW - timedelta(hours=6)).isoformat()),
            ],
            fixture_path="config/does-not-exist.json", now=NOW,
            authority_source_ids={"srhady-bingstream"},
        )
        return stats

    def test_a_rejected_football_fixture_is_named_with_its_reason(self):
        named = {row["name"]: row for row in self._stats()["provider_rejected_fixtures"]}
        self.assertIn("Genoa Vs Como", named)
        self.assertEqual("football", named["Genoa Vs Como"]["sport"])
        self.assertEqual(sr.REJECT_DEAD_STATUS, named["Genoa Vs Como"]["reason"])
        self.assertEqual("srhady-bingstream", named["Genoa Vs Como"]["source_id"])

    def test_a_rejected_cricket_fixture_is_named_too(self):
        named = {row["name"]: row for row in self._stats()["provider_rejected_fixtures"]}
        self.assertEqual("cricket", named["India Vs Pakistan"]["sport"])

    def test_another_sport_is_counted_but_not_named(self):
        """The tabs carry cricket and football; a rejected WNBA game is not a
        fixture anybody is looking for."""
        stats = self._stats()
        self.assertEqual(3, stats["provider_rejected"])
        self.assertNotIn(
            "Las Vegas Aces Vs Seattle Storm",
            [row["name"] for row in stats["provider_rejected_fixtures"]],
        )

    def test_nothing_sensitive_is_written_into_the_report(self):
        _, stats = sr.enrich_event_candidates(
            [_item(name="Genoa Vs Como", competition="Italian Serie A",
                   status="FINISHED",
                   start_time=(NOW - timedelta(hours=4)).isoformat(),
                   url="https://cdn.example/live.m3u8?play_token=SECRET",
                   headers={"Cookie": "session=SECRET"})],
            fixture_path="config/does-not-exist.json", now=NOW,
            authority_source_ids={"srhady-bingstream"},
        )
        row = stats["provider_rejected_fixtures"][0]
        self.assertEqual(
            {"name", "competition", "sport", "source_id", "status", "reason",
             "start_time"},
            set(row),
        )
        self.assertNotIn("SECRET", str(row))

    def test_the_named_list_cannot_grow_without_limit(self):
        many = [
            _item(name="Team %d Vs Team %d" % (index, index + 1),
                  competition="English Premier League", status="FINISHED",
                  start_time=(NOW - timedelta(hours=4)).isoformat())
            for index in range(sr.PROVIDER_REJECT_SAMPLE_LIMIT + 15)
        ]
        _, stats = sr.enrich_event_candidates(
            many, fixture_path="config/does-not-exist.json", now=NOW,
            authority_source_ids={"srhady-bingstream"})
        self.assertEqual(sr.PROVIDER_REJECT_SAMPLE_LIMIT,
                         len(stats["provider_rejected_fixtures"]))
        self.assertEqual(15, stats["provider_rejected_fixtures_truncated"])
        self.assertEqual(len(many), stats["provider_rejected"])


class ThePrimeVideoBehaviourIsNotABug(unittest.TestCase):
    """`srhady-primevideo-sports` refuses to build a card on an identity it
    cannot resolve. FINAL_3, অংশ ৪ঙ: keep that behaviour, show it in the
    report rather than letting the source be silently absent."""

    def test_an_identity_refusal_is_not_reclassified_as_a_provider_rejection(self):
        _, stats = sr.enrich_event_candidates(
            [{"name": "Live Sports Channel", "source_id": "srhady-primevideo-sports",
              "status": "LIVE", "url": "https://x.example/a.m3u8"}],
            fixture_path="config/does-not-exist.json", now=NOW,
            authority_source_ids={"srhady-bingstream"},
        )
        self.assertEqual(0, stats["provider_rejected"])
        self.assertEqual(0, sum(stats["provider_rejected_reasons"].values()))


if __name__ == "__main__":
    unittest.main()
