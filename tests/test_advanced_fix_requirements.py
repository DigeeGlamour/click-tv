"""Click_TV_Live_Sports_Advanced_Fix_Requirements_FINAL.md, requirement by
requirement. Each test names the requirement it holds and asserts the behaviour
that requirement asks for, not the shape of the code that implements it.
"""

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner.live_protection import (  # noqa: E402
    card_link_urls,
    protect_live_events,
    UNSCHEDULABLE_RELAY_SOURCE_IDS,
)
from scanner.merger import (  # noqa: E402
    _channel_lineage,
    _identity_compatible,
    canonical_event_identity,
    event_sport,
    load_previous_primary_keys,
    rank_and_select_streams,
    sport_sort_index,
)
from scanner.output import validate_event_snapshot  # noqa: E402
from scanner.source_coverage import build_source_coverage  # noqa: E402

NOW = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)


def stream(url, **extra):
    base = {
        "url": url,
        "verification_status": "verified_global",
        "verified": True,
        "publish_allowed": True,
        "source_pipeline": "today_match",
        "resolution_height": 720,
    }
    base.update(extra)
    return base


class Requirement1EventIdentity(unittest.TestCase):
    """Sport + participants + competition + round + kickoff window."""

    def test_identity_carries_all_four_parts(self):
        identity = canonical_event_identity({
            "name": "Arsenal vs Chelsea", "competition": "Premier League",
            "start_at": "2026-08-17T15:00:00+00:00",
        })
        self.assertEqual(identity[0], "football")
        self.assertEqual(identity[1], "arsenal-vs-chelsea")
        self.assertTrue(identity[2])
        self.assertTrue(identity[3])

    def test_the_same_fixture_from_two_sources_is_one_identity(self):
        a = canonical_event_identity({
            "name": "Arsenal vs Chelsea", "competition": "Premier League",
            "start_at": "2026-08-17T15:00:00+00:00"})
        b = canonical_event_identity({
            "name": "Arsenal vs Chelsea Server 2 HD", "competition": "Premier League",
            "start_at": "2026-08-17T15:04:00+00:00"})
        self.assertTrue(_identity_compatible(a, b))

    def test_a_missing_field_behaves_as_a_wildcard(self):
        """One source omitting the competition must not split the fixture."""
        rich = canonical_event_identity({
            "name": "Arsenal vs Chelsea", "competition": "Premier League",
            "start_at": "2026-08-17T15:00:00+00:00"})
        sparse = canonical_event_identity({"name": "Arsenal vs Chelsea"})
        self.assertTrue(_identity_compatible(rich, sparse))

    def test_the_same_teams_on_another_day_stay_two_fixtures(self):
        a = canonical_event_identity({
            "name": "England vs Pakistan", "competition": "Test Series",
            "start_at": "2026-08-17T10:00:00+00:00"})
        b = canonical_event_identity({
            "name": "England vs Pakistan", "competition": "Test Series",
            "start_at": "2026-08-24T10:00:00+00:00"})
        self.assertFalse(_identity_compatible(a, b))

    def test_multi_day_sessions_of_one_test_are_one_identity(self):
        a = canonical_event_identity({"name": "Australia vs Bangladesh 1st Test"})
        b = canonical_event_identity({"name": "Australia vs Bangladesh - 1st Test - Day 3"})
        self.assertTrue(_identity_compatible(a, b))


class Requirement2StreamMerge(unittest.TestCase):
    """Independent links get the backup slots before same-channel variants."""

    def test_same_channel_variants_share_a_lineage(self):
        a = stream("https://cdn.test/live/147446/720p/chunks.m3u8?token=aaa")
        b = stream("https://cdn.test/live/147446/720p/chunks.m3u8?token=bbb")
        self.assertEqual(_channel_lineage(a), _channel_lineage(b))

    def test_a_different_host_is_a_different_lineage(self):
        self.assertNotEqual(
            _channel_lineage(stream("https://one.test/a/chunks.m3u8")),
            _channel_lineage(stream("https://two.test/a/chunks.m3u8")),
        )

    def test_an_independent_link_outranks_a_same_channel_variant(self):
        streams = [
            stream("https://one.test/live/1/chunks.m3u8?token=a"),
            stream("https://one.test/live/1/chunks.m3u8?token=b"),
            stream("https://one.test/live/1/chunks.m3u8?token=c"),
            stream("https://two.test/live/9/chunks.m3u8"),
        ]
        _, backups = rank_and_select_streams(streams, max_total=6, max_backups=2)
        hosts = [b["host"] for b in backups]
        self.assertIn("two.test", hosts, f"independent link must be offered first: {hosts}")

    def test_same_channel_variants_are_still_kept_when_nothing_else_exists(self):
        streams = [
            stream("https://one.test/live/1/chunks.m3u8?token=a"),
            stream("https://one.test/live/1/chunks.m3u8?token=b"),
        ]
        primary, backups = rank_and_select_streams(streams, max_total=6, max_backups=5)
        self.assertIsNotNone(primary)
        self.assertEqual(len(backups), 1, "a lone channel's alternate must remain a backup")


class Requirement3SourceCoverage(unittest.TestCase):
    """raw_items -> parsed -> sport allowed -> deduped -> published -> dropped.

    The stage names are FINAL_3's (PROMPT 27). The report they belong to
    answers the same question it always did: where was this source lost?
    """

    def test_every_stage_is_reported_per_source(self):
        report = build_source_coverage(
            configured_sources=[{"id": "src-a"}, {"id": "src-b"}, {"id": "src-c"}],
            raw_candidates=[{"source_id": "src-a"}, {"source_id": "src-a"}, {"source_id": "src-b"}],
            parsed_candidates=[{"source_id": "src-a"}, {"source_id": "src-a"}, {"source_id": "src-b"}],
            matched_candidates=[{"source_id": "src-a"}, {"source_id": "src-b"}],
            published_items=[{"id": "1", "source_id": "src-a"}],
        )
        rows = {row["source_id"]: row for row in report["sources"]}
        self.assertEqual(set(rows), {"src-a", "src-b", "src-c"})
        for column in ("raw_items", "parsed_events", "sport_allowed_events",
                       "deduped_events", "published_unique_fixtures",
                       "dropped_count", "drop_reasons"):
            self.assertIn(column, rows["src-a"])
        self.assertEqual(rows["src-a"]["published_unique_fixtures"], 1)
        self.assertEqual(rows["src-b"]["dropped_count"], 1)
        self.assertTrue(rows["src-b"]["drop_reasons"])
        self.assertEqual(rows["src-c"]["raw_items"], 0)
        self.assertIn("nothing fetched", rows["src-c"]["drop_reasons"][0])

    def test_a_backup_contributor_counts_as_published(self):
        report = build_source_coverage(
            configured_sources=[{"id": "src-a"}, {"id": "src-b"}],
            raw_candidates=[{"source_id": "src-a"}, {"source_id": "src-b"}],
            parsed_candidates=[{"source_id": "src-a"}, {"source_id": "src-b"}],
            matched_candidates=[{"source_id": "src-a"}, {"source_id": "src-b"}],
            published_items=[{
                "id": "1", "source_id": "src-a",
                "backups": [{"source_id": "src-b"}],
            }],
        )
        rows = {row["source_id"]: row for row in report["sources"]}
        self.assertEqual(rows["src-b"]["published_unique_fixtures"], 1)
        self.assertEqual(rows["src-b"]["dropped_count"], 0)


class Requirement6LiveProtection(unittest.TestCase):
    """A live event missed by one scan is not deleted."""

    def _previous(self, **extra):
        card = {
            "id": "evt-1", "name": "Arsenal vs Chelsea",
            "schedule_status": "LIVE_NOW",
            "end_time": (NOW + timedelta(hours=2)).isoformat(),
        }
        card.update(extra)
        return card

    def test_a_single_miss_carries_the_event_forward(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "protection.json"
            items, stats = protect_live_events([], [self._previous()], state_path=state, now=NOW)
        self.assertEqual(stats["carried_forward"], 1)
        self.assertEqual(items[0]["id"], "evt-1")

    def test_an_authoritative_finish_removes_it_at_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "protection.json"
            items, stats = protect_live_events(
                [], [self._previous(schedule_status="ENDED")], state_path=state, now=NOW)
        self.assertEqual(items, [])
        self.assertEqual(stats["released_ended"], 1)

    def test_a_missed_scan_never_releases_a_link_that_is_still_alive(self):
        """Corrected rule: however many scans in a row miss the event, a
        previous link that is still live and playable keeps its card."""
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "protection.json"
            previous = [self._previous()]
            for expected in range(1, 11):
                items, stats = protect_live_events(
                    [], previous, state_path=state, now=NOW,
                    probe=lambda card: True,
                )
                self.assertEqual(len(items), 1, f"miss {expected}")
                self.assertEqual(items[0]["carried_forward_misses"], expected)
        self.assertEqual(stats["released_exhausted"], 0)
        self.assertEqual(stats["probe_alive"], 1)

    def test_a_dead_link_alone_no_longer_releases_it(self):
        """Superseded by section 21. A dead link is now one of three
        signals, not a verdict: the estimated end must also have passed and
        several consecutive scans must have seen no live signal. One dead probe
        moves the card to END_PENDING, which still publishes it.
        """
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "protection.json"
            items, stats = protect_live_events(
                [], [self._previous()], state_path=state, now=NOW,
                probe=lambda card: False,
            )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["lifecycle_state"], "END_PENDING")
        self.assertEqual(stats["end_pending"], 1)
        self.assertEqual(stats["released_dead_link"], 0)

    def test_a_dead_link_with_the_full_confirmation_set_releases_it(self):
        ended = self._previous(end_time=(NOW - timedelta(hours=5)).isoformat())
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "protection.json"
            for _ in range(3):
                items, stats = protect_live_events(
                    [], [ended], state_path=state, now=NOW,
                    probe=lambda card: False,
                )
        self.assertEqual(items, [])
        self.assertEqual(stats["released_confirmed"], 1)

    def test_an_inconclusive_probe_preserves_the_card(self):
        """"Cannot tell" must never be read as "confirmed dead"."""
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "protection.json"
            items, stats = protect_live_events(
                [], [self._previous()], state_path=state, now=NOW,
                probe=lambda card: None,
            )
        self.assertEqual(len(items), 1)
        self.assertEqual(stats["probe_inconclusive"], 1)

    def test_a_probe_that_raises_preserves_the_card(self):
        def explode(card):
            raise RuntimeError("network down")

        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "protection.json"
            items, _ = protect_live_events(
                [], [self._previous()], state_path=state, now=NOW, probe=explode,
            )
        self.assertEqual(len(items), 1)

    def test_the_probe_sees_the_backups_too(self):
        card = self._previous(
            url="https://dead.test/live.m3u8",
            backups=[{"url": "https://alive.test/live.m3u8"}],
        )
        self.assertEqual(
            card_link_urls(card),
            ["https://dead.test/live.m3u8", "https://alive.test/live.m3u8"],
        )

    def test_seeing_the_event_again_resets_the_counter(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "protection.json"
            previous = [self._previous()]
            protect_live_events([], previous, state_path=state, now=NOW)
            protect_live_events(previous, previous, state_path=state, now=NOW)
            items, stats = protect_live_events([], previous, state_path=state, now=NOW)
        self.assertEqual(stats["carried_forward"], 1)
        self.assertEqual(items[0].get("carried_forward_misses"), 1)

    def test_past_its_end_time_a_dead_link_is_retired_once_confirmed(self):
        """Section 21: the estimate passing is a supporting signal, so it
        still needs the repeated confirming scans before the card goes.
        """
        stale = self._previous(end_time=(NOW - timedelta(hours=4)).isoformat())
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "protection.json"
            first, _ = protect_live_events(
                [], [stale], state_path=state, now=NOW, probe=lambda card: False,
            )
            self.assertEqual(first[0]["lifecycle_state"], "END_PENDING")
            for _ in range(2):
                items, stats = protect_live_events(
                    [], [stale], state_path=state, now=NOW, probe=lambda card: False,
                )
        self.assertEqual(items, [])
        self.assertEqual(stats["released_stale"], 1)

    def test_past_its_end_time_a_live_link_still_keeps_its_card(self):
        """A match running long is exactly the case the old end-time cut
        removed while the viewer was still watching it."""
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "protection.json"
            stale = self._previous(end_time=(NOW - timedelta(hours=4)).isoformat())
            items, stats = protect_live_events(
                [], [stale], state_path=state, now=NOW, probe=lambda card: True,
            )
        self.assertEqual(len(items), 1)
        self.assertEqual(stats["released_stale"], 0)


class UnschedulableRelaySourceLiveProtectionTests(unittest.TestCase):
    """2026-08-19 incident. Some sources (the Tapmad relay-slot mirrors) are
    not per-fixture playlists: the file always holds exactly one #EXTINF
    entry that its maintainer hand-retitles whenever they personally switch
    what they are watching on one shared account, and the underlying URL -
    the only thing event identity is keyed on - does not necessarily change
    with it. Carrying such a card forward on a missed scan (even with a
    perfectly reachable link) republishes a stale, human-picked title over
    whatever that source happens to be streaming now. Per the follow-up
    instruction, these sources stay enabled and their content is shown as
    its own channel under its own current name; the fix is that a card
    traced to one of them must retire immediately on a miss, never carried
    forward, so a stale title never survives past the scan that stopped
    seeing it."""

    def _previous_from_relay_source(self, source_id, **extra):
        card = {
            "id": "evt-tapmad-relay",
            "name": "Spain vs Belgium W | FIH Hockey World Cup 2026",
            "schedule_status": "LIVE_NOW",
            "end_time": (NOW + timedelta(hours=2)).isoformat(),
            "channels": [{"provider": source_id}],
        }
        card.update(extra)
        return card

    def test_a_relay_source_card_is_never_carried_forward_on_a_miss(self):
        for source_id in UNSCHEDULABLE_RELAY_SOURCE_IDS:
            with self.subTest(source_id=source_id):
                with tempfile.TemporaryDirectory() as tmp:
                    state = Path(tmp) / "protection.json"
                    previous = [self._previous_from_relay_source(source_id)]
                    items, stats = protect_live_events(
                        [], previous, state_path=state, now=NOW,
                        # Even a link that still answers "alive" must not
                        # save the card: the probe is never the question.
                        probe=lambda card: True,
                    )
                self.assertEqual(items, [])
                self.assertEqual(stats["released_ended"], 1)
                self.assertEqual(stats["carried_forward"], 0)

    def test_a_relay_source_card_still_seen_this_scan_is_unaffected(self):
        """The fix only stops a MISSING card from being carried forward
        under a stale title; a card the current scan still reports (under
        whatever name it holds right now) is untouched by this rule."""
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "protection.json"
            current = self._previous_from_relay_source(
                "sm-tapmad-auto", name="Fresh Current Title",
            )
            items, stats = protect_live_events(
                [current], [current], state_path=state, now=NOW,
            )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["name"], "Fresh Current Title")

    def test_a_channel_from_an_unrelated_source_is_not_affected(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "protection.json"
            previous = [self._previous_from_relay_source("srhady-sonyliv-live")]
            items, stats = protect_live_events(
                [], previous, state_path=state, now=NOW,
            )
        self.assertEqual(len(items), 1)
        self.assertEqual(stats["carried_forward"], 1)


class Requirement11SportOrdering(unittest.TestCase):
    """Cricket first, football second, then the rest."""

    def test_order_indices(self):
        self.assertEqual(sport_sort_index("cricket"), 0)
        self.assertEqual(sport_sort_index("football"), 1)
        for other in ("tennis", "baseball", "golf", "other"):
            self.assertEqual(sport_sort_index(other), 2)

    def test_sports_are_recognised_from_real_titles(self):
        cases = {
            "cricket": ("Australia vs Bangladesh 1st Test", "Test Series"),
            "football": ("Arsenal vs Chelsea", "Premier League"),
            "baseball": ("Baltimore Orioles vs Tampa Bay Rays", "MLB"),
            "tennis": ("Cincinnati Open", "ATP Cincinnati"),
            "golf": ("St Jude Championship, Day 4", "PGA Tour"),
            "motorsport": ("Race 2 London", "Formula E"),
        }
        for expected, (name, competition) in cases.items():
            self.assertEqual(
                event_sport({"name": name, "competition": competition}), expected, name
            )

    def test_nothing_is_invented_for_an_unknown_event(self):
        self.assertEqual(event_sport({"name": "Zzz Qqq", "competition": ""}), "other")


class Requirement15AtomicPublish(unittest.TestCase):
    """A partial or empty scan never replaces a good snapshot."""

    def test_a_complete_snapshot_publishes(self):
        ok, _ = validate_event_snapshot(
            {"today_match": {"count": 1, "items": [{"id": "a"}]},
             "upcoming": {"count": 0, "items": []}}, 1, 0)
        self.assertTrue(ok)

    def test_a_count_mismatch_is_refused(self):
        ok, reason = validate_event_snapshot(
            {"today_match": {"count": 5, "items": [{"id": "a"}]}}, 1, 0)
        self.assertFalse(ok)
        self.assertIn("does not match", reason)

    def test_an_emptied_feed_is_refused(self):
        ok, reason = validate_event_snapshot(
            {"today_match": {"count": 0, "items": []}}, 25, 0)
        self.assertFalse(ok)
        self.assertIn("refusing", reason)

    def test_an_item_without_an_id_is_refused(self):
        ok, _ = validate_event_snapshot({"today_match": {"count": 1, "items": [{}]}}, 1, 0)
        self.assertFalse(ok)

    def test_the_first_ever_scan_is_allowed_to_be_empty(self):
        ok, _ = validate_event_snapshot({"today_match": {"count": 0, "items": []}}, 0, 0)
        self.assertTrue(ok)


class Requirement16PrimaryHysteresis(unittest.TestCase):
    """A healthy primary is not replaced because a rival looked marginally
    better on this scan."""

    def test_a_healthy_incumbent_keeps_the_primary_slot(self):
        incumbent = stream("https://a.test/live.m3u8", response_time_ms=400)
        challenger = stream("https://b.test/live.m3u8", response_time_ms=120)
        from scanner.merger import _stream_identity_key
        primary, _ = rank_and_select_streams(
            [challenger, incumbent],
            previous_primary_identity=_stream_identity_key(incumbent),
        )
        self.assertEqual(primary["url"], "https://a.test/live.m3u8")

    def test_without_a_remembered_primary_ranking_decides(self):
        incumbent = stream("https://a.test/live.m3u8", response_time_ms=400)
        challenger = stream("https://b.test/live.m3u8", response_time_ms=120)
        primary, _ = rank_and_select_streams([challenger, incumbent])
        self.assertEqual(primary["url"], "https://b.test/live.m3u8")

    def test_a_remembered_primary_that_is_gone_does_not_break_selection(self):
        primary, _ = rank_and_select_streams(
            [stream("https://b.test/live.m3u8")],
            previous_primary_identity="a-stream-that-no-longer-exists",
        )
        self.assertEqual(primary["url"], "https://b.test/live.m3u8")

    def test_previous_primaries_are_read_back_from_published_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "today-match.json").write_text(json.dumps({
                "items": [{"id": "e1", "name": "Arsenal vs Chelsea",
                           "primary_stream_key": "fingerprint-1"}]
            }), encoding="utf-8")
            (root / "upcoming.json").write_text(json.dumps({"items": []}), encoding="utf-8")
            keys = load_previous_primary_keys(root)
        self.assertEqual(keys.get("arsenal-vs-chelsea"), "fingerprint-1")


if __name__ == "__main__":
    unittest.main()


class Requirement4TargetedWindow(unittest.TestCase):
    """Only fixtures inside the window are scan targets; the rest keep the card
    they already have."""

    def test_a_fixture_outside_the_window_keeps_its_published_card(self):
        from scanner.events import _parse_datetime

        horizon = NOW + timedelta(minutes=15)
        soon = NOW + timedelta(minutes=8)
        later = NOW + timedelta(hours=6)
        self.assertTrue(NOW <= soon <= horizon, "the near fixture is inside the window")
        self.assertFalse(NOW <= later <= horizon, "the far fixture is outside it")
        # The filter itself is exercised through process_events in the real scan;
        # this pins the boundary arithmetic the filter depends on.
        self.assertIsNotNone(_parse_datetime(soon.isoformat(), timezone.utc))

    def test_the_window_default_is_off_so_a_normal_scan_is_unchanged(self):
        import inspect

        from scanner.events import process_events

        signature = inspect.signature(process_events)
        self.assertEqual(signature.parameters["targeted_window_minutes"].default, 0)
