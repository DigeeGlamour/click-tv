"""A source going quiet must not delete the fixtures it published.

The replay in `RealIncidentReplay` is the 2026-09-06 14:11:49Z scan, byte for
byte: `tests/fixtures/source-outage/` holds what was published at 14:09:08Z,
what the 14:11:49Z full scan merged instead, and the per-source health that scan
recorded. Twenty-six cards left that publish; ten were the same fixture under
another feed's spelling, so sixteen fixtures were actually lost. The rest of
the file is the class of fault rather than that instance of it.
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scanner import fixture_dedupe, source_outage  # noqa: E402
from scanner.events import _stream_health  # noqa: E402
from scanner.source_loader import _merge_health_history  # noqa: E402
from scanner.targeted_scan import fixture_key  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "fixtures", "source-outage")
NOW = datetime(2026, 9, 6, 14, 11, 0, tzinfo=timezone.utc)


def load_fixture(name):
    with open(os.path.join(FIXTURES, name), "r", encoding="utf-8") as handle:
        return json.load(handle)


def always_upcoming(_card):
    return True


def health_record(**overrides):
    record = {
        "source_id": "feed",
        "pipeline": "today_match",
        "url": "https://example.test/feed.json",
        "status": "success",
        "raw_items": 40,
        "last_scan": NOW.isoformat(),
        "last_productive": NOW.isoformat(),
        "last_productive_items": 40,
    }
    record.update(overrides)
    return record


def card(**overrides):
    base = {
        "id": "home-vs-away",
        "name": "Home vs Away",
        "start_time": (NOW + timedelta(hours=4)).isoformat(),
        "source_id": "feed",
        "source_ids": ["feed"],
        "schedule_source_url": "https://example.test/feed.json",
        "status": "UPCOMING",
        "lifecycle_state": "UPCOMING",
    }
    base.update(overrides)
    return base


class SourceStates(unittest.TestCase):
    """What a scan may conclude about a source from its own health record."""

    def states(self, **records):
        return source_outage.read_source_states(records, now=NOW)

    def test_a_source_that_answered_with_records_is_productive(self):
        states = self.states(feed=health_record())
        self.assertEqual(states["feed"]["state"], source_outage.PRODUCTIVE)

    def test_empty_now_and_productive_an_hour_ago_is_an_outage(self):
        states = self.states(feed=health_record(
            status="success_empty", raw_items=0,
            last_productive=(NOW - timedelta(hours=1)).isoformat(),
            last_productive_items=408,
        ))
        self.assertEqual(states["feed"]["state"], source_outage.OUTAGE)
        self.assertEqual(states["feed"]["content_state"], source_outage.EMPTY)

    def test_a_source_that_never_produced_anything_protects_nothing(self):
        """0matbank-trysports-cricket-live was EMPTY on the same real scan.

        It has published no fixture in days. Treating its silence as an outage
        would hold cards it never authored, forever.
        """
        states = self.states(feed=health_record(
            status="success_empty", raw_items=0,
            last_productive="", last_productive_items=0,
        ))
        self.assertEqual(states["feed"]["state"], source_outage.UNKNOWN)
        self.assertIn("ever saw it produce anything", states["feed"]["reason"])

    def test_productivity_older_than_the_memory_is_not_an_outage(self):
        states = source_outage.read_source_states(
            {"feed": health_record(
                status="success_empty", raw_items=0,
                last_productive=(NOW - timedelta(hours=9)).isoformat(),
                last_productive_items=408)},
            now=NOW, memory_hours=6)
        self.assertEqual(states["feed"]["state"], source_outage.UNKNOWN)
        self.assertIn("beyond the 6h memory", states["feed"]["reason"])

    def test_a_record_from_an_earlier_scan_says_nothing_about_this_one(self):
        states = self.states(feed=health_record(
            status="success_empty", raw_items=0,
            last_scan=(NOW - timedelta(hours=3)).isoformat(),
            last_productive=(NOW - timedelta(hours=3, minutes=5)).isoformat(),
        ))
        self.assertEqual(states["feed"]["state"], source_outage.UNKNOWN)
        self.assertEqual(states["feed"]["reason"], "no health record from this scan")

    def test_an_http_failure_is_an_outage_and_reads_as_unreachable(self):
        states = self.states(feed=health_record(
            status="failed", raw_items=0,
            last_productive=(NOW - timedelta(minutes=20)).isoformat(),
            last_productive_items=17,
        ))
        self.assertEqual(states["feed"]["state"], source_outage.OUTAGE)
        self.assertEqual(states["feed"]["content_state"], source_outage.UNREACHABLE)

    def test_a_disabled_source_is_not_an_outage(self):
        states = self.states(feed=health_record(status="disabled", raw_items=0))
        self.assertEqual(states["feed"]["state"], source_outage.UNKNOWN)

    def test_only_event_sources_are_read(self):
        states = self.states(
            tv=health_record(pipeline="tv", status="failed", raw_items=0),
            feed=health_record(),
        )
        self.assertNotIn("tv", states)
        self.assertIn("feed", states)


class FixtureAuthority(unittest.TestCase):
    """Which source is entitled to withdraw a fixture."""

    STATES = {
        "scheduler": {"url": "https://example.test/scheduler.json"},
        "streamer": {"url": "https://example.test/streamer.json"},
    }

    def test_the_scheduling_feed_wins_over_the_primary_source_id(self):
        """The real discriminator.

        `Baltika Kaliningrad Vs Lokomotiv Moscow` carried three source ids, two
        of them healthy - and only one of them had ever said when the match
        starts. The other two attached a stream to somebody else's fixture.
        """
        item = card(source_id="streamer",
                    source_ids=["streamer", "scheduler"],
                    schedule_source_url="https://example.test/scheduler.json")
        self.assertEqual(
            source_outage.fixture_authority(item, self.STATES), "scheduler")

    def test_without_a_schedule_url_the_primary_source_answers(self):
        item = card(source_id="streamer", schedule_source_url="")
        self.assertEqual(
            source_outage.fixture_authority(item, self.STATES), "streamer")

    def test_a_single_contributor_answers_when_nothing_else_does(self):
        item = {"id": "x", "source_ids": ["streamer"]}
        self.assertEqual(
            source_outage.fixture_authority(item, self.STATES), "streamer")

    def test_several_contributors_and_no_statement_names_nobody(self):
        item = {"id": "x", "source_ids": ["streamer", "scheduler"]}
        self.assertEqual(source_outage.fixture_authority(item, self.STATES), "")

    def test_every_contributor_is_found_wherever_it_is_recorded(self):
        item = {
            "source_id": "a",
            "source_ids": ["b"],
            "source_provenance": [{"source_id": "c"}],
            "channels": [{"source_ids": ["d"]}],
        }
        self.assertEqual(
            source_outage.contributing_source_ids(item), ["a", "b", "c", "d"])


class HoldRules(unittest.TestCase):
    """When a previously published Upcoming card is held, and when it is not."""

    def hold(self, previous, published=(), today=(), states=None, now=NOW,
             still_upcoming=always_upcoming, **kwargs):
        return source_outage.hold_upcoming_through_outage(
            list(published), list(previous), list(today),
            states=states if states is not None else self.outage_states(),
            now=now, still_upcoming=still_upcoming, fixture_key=fixture_key,
            **kwargs)

    def outage_states(self):
        return source_outage.read_source_states(
            {"feed": health_record(
                status="success_empty", raw_items=0,
                last_productive=(NOW - timedelta(minutes=13)).isoformat(),
                last_productive_items=408)},
            now=NOW)

    def healthy_states(self):
        return source_outage.read_source_states({"feed": health_record()}, now=NOW)

    def test_no_outage_changes_nothing(self):
        kept, stats = self.hold([card()], states=self.healthy_states())
        self.assertEqual(kept, [])
        self.assertEqual(stats["held"], 0)
        self.assertEqual(stats["sources_in_outage"], [])

    def test_a_card_whose_author_went_silent_is_held(self):
        kept, stats = self.hold([card()])
        self.assertEqual(len(kept), 1)
        self.assertEqual(stats["held"], 1)
        self.assertEqual(stats["refused"], {})
        self.assertEqual(kept[0]["source_outage_authority"], "feed")
        self.assertIn("feed", kept[0]["source_outage_hold_reason"])
        self.assertIn("feed", kept[0]["carried_forward_reason"])

    def test_a_healthy_author_that_stopped_listing_it_has_removed_it(self):
        """The one rule that keeps this from becoming a resurrection engine.

        One feed really is in outage this scan, so the protection is running -
        and it still refuses a card the *healthy* feed scheduled and dropped.
        """
        states = source_outage.read_source_states({
            "feed": health_record(
                status="success_empty", raw_items=0,
                last_productive=(NOW - timedelta(minutes=13)).isoformat(),
                last_productive_items=408),
            "other": health_record(
                source_id="other", url="https://example.test/other.json"),
        }, now=NOW)
        withdrawn = card(source_id="other", source_ids=["other"],
                         schedule_source_url="https://example.test/other.json")
        kept, stats = self.hold([withdrawn], states=states)
        self.assertEqual(kept, [])
        self.assertEqual(stats["held"], 0)
        self.assertEqual(stats["refused"], {"authority productive this scan": 1})

    def test_a_card_this_scan_published_is_never_reconsidered(self):
        fresh = card(name="Home vs Away (fresh)")
        kept, stats = self.hold([card()], published=[fresh])
        self.assertEqual(kept, [fresh])
        self.assertEqual(stats["considered"], 0)

    def test_a_fixture_promoted_to_today_is_not_a_loss(self):
        """Guyana Amazon Warriors moved tabs on the real scan; it did not go."""
        kept, stats = self.hold([card()], today=[card()])
        self.assertEqual(kept, [])
        self.assertEqual(stats["considered"], 0)

    def test_the_same_fixture_on_today_under_another_spelling_is_not_re_added(self):
        """The fold settles a spelling variant within one tab. Across two tabs
        nothing does, so the identity question is asked here."""
        promoted = card(id="different-id", name="Home Town vs Away City",
                        fixture_id="provider:home-town-vs-away-city|x|2026-09-06")
        kept, stats = self.hold(
            [card()], today=[promoted],
            is_same_fixture=lambda left, right: True)
        self.assertEqual(kept, [])
        self.assertEqual(stats["considered"], 0)

    def test_an_unrelated_fixture_does_not_block_the_hold(self):
        kept, stats = self.hold(
            [card()], today=[card(id="other", name="Other vs Else")],
            is_same_fixture=lambda left, right: False)
        self.assertEqual(stats["held"], 1)

    def test_a_finished_fixture_is_not_held(self):
        kept, stats = self.hold(
            [card(status="FINISHED")],
            is_ended=lambda item: str(item.get("status")) == "FINISHED")
        self.assertEqual(kept, [])
        self.assertEqual(stats["refused"], {"fixture has ended": 1})

    def test_a_card_past_its_own_clock_is_not_held(self):
        kept, stats = self.hold([card()], still_upcoming=lambda item: False)
        self.assertEqual(kept, [])
        self.assertEqual(stats["refused"], {"past its own clock": 1})

    def test_the_hold_expires_however_silent_the_source_stays(self):
        """Genuinely gone is genuinely gone. Three hours, then it goes."""
        held_since = (NOW - timedelta(hours=4)).isoformat()
        kept, stats = self.hold(
            [card(source_outage_hold_since=held_since)], hold_minutes=180)
        self.assertEqual(kept, [])
        self.assertEqual(stats["hold_expired"], 1)
        self.assertEqual(stats["refused"], {"hold window exhausted": 1})

    def test_the_hold_window_is_measured_from_the_first_scan_that_held_it(self):
        first, _ = self.hold([card()])
        self.assertEqual(first[0]["source_outage_hold_scans"], 1)
        second, _ = self.hold([first[0]], now=NOW + timedelta(minutes=20))
        self.assertEqual(second[0]["source_outage_hold_since"],
                         first[0]["source_outage_hold_since"])
        self.assertEqual(second[0]["source_outage_hold_scans"], 2)
        self.assertEqual(second[0]["source_outage_hold_minutes"], 20)

    def test_a_card_with_no_named_author_is_not_held(self):
        kept, stats = self.hold(
            [{"id": "x", "source_ids": ["feed", "other"], "name": "X"}])
        self.assertEqual(kept, [])
        self.assertEqual(stats["refused"], {"no fixture authority on the card": 1})

    def test_a_held_card_keeps_every_source_and_every_stream(self):
        original = card(
            source_ids=["feed", "mirror"],
            channels=[{"name": "A", "url": "http://a.test/1.m3u8",
                       "source_ids": ["feed"],
                       "backups": [{"url": "http://a.test/2.m3u8"}]}],
        )
        kept, _ = self.hold([original])
        self.assertEqual(kept[0]["source_ids"], ["feed", "mirror"])
        self.assertEqual(kept[0]["channels"], original["channels"])

    def test_holding_never_mutates_the_previous_snapshot(self):
        original = card()
        before = json.dumps(original, sort_keys=True)
        self.hold([original])
        self.assertEqual(json.dumps(original, sort_keys=True), before)


class RealIncidentReplay(unittest.TestCase):
    """The 2026-09-06 14:11:49Z scan, replayed against the protection."""

    @classmethod
    def setUpClass(cls):
        cls.before = load_fixture("upcoming-before-outage.json")["items"]
        cls.during = load_fixture("upcoming-during-outage.json")["items"]
        cls.today = load_fixture("today-during-outage.json")["items"]
        cls.health = load_fixture("source-health-during-outage.json")["sources"]
        cls.states = source_outage.read_source_states(cls.health, now=NOW)

    def replay(self, states=None):
        return source_outage.hold_upcoming_through_outage(
            list(self.during), self.before, self.today,
            states=states if states is not None else self.states,
            now=NOW,
            still_upcoming=lambda item: True,
            fixture_key=fixture_key,
            is_same_fixture=fixture_dedupe.same_fixture)

    def test_the_fixtures_are_the_real_published_lists(self):
        self.assertEqual(len(self.before), 143)
        self.assertEqual(len(self.during), 123)

    def test_one_source_went_quiet_and_it_is_the_one_that_did(self):
        outages = sorted(source_id for source_id, entry in self.states.items()
                         if entry["state"] == source_outage.OUTAGE)
        self.assertEqual(outages, ["sm-sports-data"])

    def test_the_healthy_feeds_are_still_read_as_healthy(self):
        for source_id in ("srhady-bingstream", "srhady-axsports-live"):
            self.assertEqual(self.states[source_id]["state"],
                             source_outage.PRODUCTIVE, source_id)

    def test_every_fixture_the_scan_dropped_is_held_and_none_is_refused(self):
        """26 cards left that publish; 10 of them were the same fixture the
        scan republished under another feed's spelling, so 16 fixtures were
        actually lost and 16 come back."""
        kept, stats = self.replay()
        self.assertEqual(stats["held"], 16)
        self.assertEqual(stats["considered"], 16)
        self.assertEqual(stats["refused"], {})
        self.assertEqual(len(kept), 139)

    def test_every_held_card_was_scheduled_by_the_silent_feed(self):
        kept, _ = self.replay()
        held = [item for item in kept if item.get("source_outage_hold_reason")]
        self.assertEqual(len(held), 16)
        for item in held:
            self.assertEqual(item["source_outage_authority"], "sm-sports-data")

    def test_nothing_the_scan_published_is_displaced(self):
        kept, _ = self.replay()
        self.assertEqual(kept[:len(self.during)], self.during)

    def test_the_result_carries_no_duplicate_id(self):
        kept, _ = self.replay()
        ids = [str(item.get("id")) for item in kept if item.get("id")]
        self.assertEqual(len(ids), len(set(ids)))

    def test_the_result_puts_nothing_on_both_tabs(self):
        kept, _ = self.replay()
        today_keys = {fixture_key(item) for item in self.today}
        today_keys.discard("")
        clash = [item for item in kept if fixture_key(item) in today_keys]
        self.assertEqual(clash, [])

    def test_a_spelling_the_scan_republished_is_never_held_at_all(self):
        """`SK Beveren Vs Oud Heverlee Leuven` is `SK Beveren vs OH Leuven`.

        Refusing it here is better than folding it afterwards: the fold works
        within one tab, and a fixture the scan promoted to Today would have had
        nothing to fold against.
        """
        kept, _ = self.replay()
        held = {str(item.get("name")) for item in kept
                if item.get("source_outage_hold_reason")}
        for spelling in ("SK Beveren Vs Oud Heverlee Leuven",
                         "Deportivo Alavés Vs Osasuna",
                         "Racing Club Vs Atlético Tucumán"):
            self.assertNotIn(spelling.encode().decode("unicode_escape"), held)

    def test_the_fold_finds_only_what_the_hold_cannot_ask_about(self):
        """The hold asks `same_fixture`, which is pairwise; the fold's
        short-form rule has to count its candidates across the whole list and
        so cannot be asked one pair at a time.

        On this data that is exactly one pair - `Baltika vs Lokomotiv` beside
        `Baltika Kaliningrad Vs Lokomotiv Moscow`, which this replay recorded as
        an open finding when it was written and which the fold now settles.
        Everything else the hold already declined to add.
        """
        kept, _ = self.replay()
        folded, rows = fixture_dedupe.fold(kept, lambda home, away, date: "")
        self.assertEqual([row["rule"] for row in rows],
                         ["both clubs named more briefly, one candidate"])
        self.assertEqual(rows[0]["folded"], "Baltika vs Lokomotiv")
        self.assertEqual(len(folded), 138)

    def test_no_source_id_and_no_stream_url_is_lost_by_holding(self):
        kept, _ = self.replay()
        def collect(items):
            sources, urls = set(), set()
            for item in items:
                sources.update(source_outage.contributing_source_ids(item))
                for channel in item.get("channels") or ():
                    if isinstance(channel, dict):
                        if channel.get("url"):
                            urls.add(str(channel["url"]))
                        for backup in channel.get("backups") or ():
                            if isinstance(backup, dict) and backup.get("url"):
                                urls.add(str(backup["url"]))
            return sources, urls
        was_sources, was_urls = collect(self.before)
        now_sources, now_urls = collect(kept)
        self.assertEqual(was_sources - now_sources, set())
        self.assertEqual(was_urls - now_urls, set())

    def test_recovery_publishes_this_scan_and_holds_nothing(self):
        """The feed answers again: the fresh output wins, with no residue."""
        recovered = dict(self.health)
        recovered["sm-sports-data"] = dict(recovered["sm-sports-data"])
        recovered["sm-sports-data"].update(
            status="success", raw_items=408,
            last_productive=NOW.isoformat(), last_productive_items=408)
        states = source_outage.read_source_states(recovered, now=NOW)
        kept, stats = self.replay(states)
        self.assertEqual(stats["held"], 0)
        self.assertEqual(kept, self.during)

    def test_a_feed_that_stays_empty_stops_protecting_anything(self):
        """No infinite preservation. Past the memory window the silence is not
        an outage any more - it is simply what that feed is now."""
        stale = dict(self.health)
        stale["sm-sports-data"] = dict(stale["sm-sports-data"])
        stale["sm-sports-data"]["last_productive"] = (
            NOW - timedelta(hours=48)).isoformat()
        states = source_outage.read_source_states(stale, now=NOW)
        kept, stats = source_outage.hold_upcoming_through_outage(
            list(self.during), self.before, self.today, states=states, now=NOW,
            still_upcoming=lambda item: True, fixture_key=fixture_key)
        self.assertEqual(stats["held"], 0)
        self.assertEqual(len(kept), 123)

    def test_a_partial_failure_leaves_the_healthy_feeds_untouched(self):
        """The 20 that vanished were one feed's. Nobody else's card moved."""
        kept, _ = self.replay()
        held_keys = {fixture_key(item) for item in kept
                     if item.get("source_outage_hold_reason")}
        for item in self.during:
            self.assertNotIn(fixture_key(item), held_keys)

    def test_the_hold_introduces_no_card_inside_the_routing_window(self):
        """T-25 is not this rule's business, and it must not become it."""
        kept, _ = self.replay()
        threshold = NOW + timedelta(minutes=25)
        for item in kept:
            if not item.get("source_outage_hold_reason"):
                continue
            start = source_outage.parse_time(item.get("start_time"))
            if start is not None:
                self.assertGreater(start, threshold, str(item.get("name")))


class StreamHealthAttribution(unittest.TestCase):
    """A degraded scan that can name what degraded it."""

    def playable(self, **overrides):
        item = {"id": "a", "url": "http://a.test/x.m3u8",
                "verified": True, "verification_status": "verified_global",
                "publish_allowed": True}
        item.update(overrides)
        return item

    def carried_card(self):
        return self.playable(
            carried_forward_reason="still live: primary_playable")

    def test_the_warning_names_the_source_that_produced_nothing(self):
        health = _stream_health(
            [self.carried_card()], [],
            {"source_outage": {"sources_in_outage": ["sm-sports-data"]}})
        self.assertEqual(health["state"], "degraded")
        self.assertEqual(health["sources_in_outage"], ["sm-sports-data"])
        self.assertIn("sm-sports-data", health["warnings"][0])

    def test_the_verdict_is_not_softened_by_having_an_explanation(self):
        with_cause = _stream_health(
            [self.carried_card()], [],
            {"source_outage": {"sources_in_outage": ["feed"]}})
        without = _stream_health([self.carried_card()], [], {})
        self.assertEqual(with_cause["state"], "degraded")
        self.assertEqual(without["state"], "degraded")
        self.assertEqual(without["sources_in_outage"], [])
        self.assertNotIn(";", without["warnings"][0])

    def test_a_catalogue_route_is_counted_and_named(self):
        """`_is_playable` asks for a URL on the card - the right question for
        admission, the wrong one for a report about what the viewer can watch.
        validate-pages.py publishes a card carrying a playback id and no URL,
        because the catalogue holds the proven URL for it.

        Measured 2026-09-07 01:39: "0 with a stream" of 80 cards, six of them
        playable through the catalogue and playable all night.
        """
        card = {"id": "c", "playback_id": "ctv_" + "a" * 32,
                "publish_allowed": True}
        health = _stream_health([card], [], {})
        self.assertEqual(health["fixtures_with_stream"], 0)
        self.assertEqual(health["fixtures_playable_via_catalogue"], 1)
        self.assertIn("1 playable through the catalogue", health["warnings"][0])

    def test_the_verdict_is_unchanged_by_saying_so(self):
        """A scan that proved no route of its own is still degraded."""
        card = {"id": "c", "playback_id": "ctv_" + "a" * 32,
                "publish_allowed": True}
        self.assertEqual(_stream_health([card], [], {})["state"], "degraded")

    def test_a_card_with_no_route_at_all_adds_nothing(self):
        health = _stream_health([{"id": "c", "publish_allowed": True}], [], {})
        self.assertEqual(health["fixtures_playable_via_catalogue"], 0)
        self.assertNotIn("catalogue", health["warnings"][0])

    def test_a_scan_that_found_a_route_is_still_healthy(self):
        fresh = self.playable(id="b")
        health = _stream_health(
            [fresh], [], {"source_outage": {"sources_in_outage": ["feed"]}})
        self.assertEqual(health["state"], "ok")
        self.assertEqual(health["warnings"], [])


class ProductivityLedger(unittest.TestCase):
    """state/source-health.json learns to tell answering from producing."""

    def merge(self, previous, current):
        return _merge_health_history({"sources": previous}, current)

    def test_records_are_kept_when_a_source_produces(self):
        merged = self.merge({}, {"feed": {
            "status": "success", "raw_items": 408,
            "last_scan": NOW.isoformat()}})
        self.assertEqual(merged["feed"]["last_productive"], NOW.isoformat())
        self.assertEqual(merged["feed"]["last_productive_items"], 408)
        self.assertEqual(merged["feed"]["consecutive_unproductive"], 0)

    def test_an_empty_success_counts_as_unproductive_and_keeps_the_memory(self):
        """`success_empty` sets `last_success`, which is why `last_success`
        could never answer this question."""
        first = self.merge({}, {"feed": {
            "status": "success", "raw_items": 408, "last_scan": NOW.isoformat()}})
        later = (NOW + timedelta(minutes=7)).isoformat()
        second = self.merge(first, {"feed": {
            "status": "success_empty", "raw_items": 0, "last_scan": later}})
        self.assertEqual(second["feed"]["last_productive"], NOW.isoformat())
        self.assertEqual(second["feed"]["last_productive_items"], 408)
        self.assertEqual(second["feed"]["consecutive_unproductive"], 1)
        self.assertEqual(second["feed"]["last_success"], later)

    def test_the_unproductive_run_accumulates_and_then_resets(self):
        state = self.merge({}, {"feed": {
            "status": "success_empty", "raw_items": 0,
            "last_scan": NOW.isoformat()}})
        for _ in range(3):
            state = self.merge(state, {"feed": {
                "status": "success_empty", "raw_items": 0,
                "last_scan": NOW.isoformat()}})
        self.assertEqual(state["feed"]["consecutive_unproductive"], 4)
        state = self.merge(state, {"feed": {
            "status": "success", "raw_items": 12, "last_scan": NOW.isoformat()}})
        self.assertEqual(state["feed"]["consecutive_unproductive"], 0)

    def test_a_disabled_source_records_neither(self):
        merged = self.merge({}, {"feed": {
            "status": "disabled", "raw_items": 0, "last_scan": NOW.isoformat()}})
        self.assertNotIn("last_productive", merged["feed"])
        self.assertNotIn("consecutive_unproductive", merged["feed"])

    def test_the_ledger_is_what_the_protection_reads(self):
        """End to end: a productive scan, then an empty one, then the verdict."""
        state = self.merge({}, {"feed": health_record(raw_items=408)})
        later = NOW + timedelta(minutes=7)
        state = self.merge(state, {"feed": health_record(
            status="success_empty", raw_items=0, last_scan=later.isoformat())})
        states = source_outage.read_source_states(state, now=later)
        self.assertEqual(states["feed"]["state"], source_outage.OUTAGE)


class TheTwoGuardsDivideTheSpace(unittest.TestCase):
    """Everything gone was already refused; some gone was not.

    `scanner/output.py validate_event_snapshot` refuses to publish a scan whose
    Upcoming list came back completely empty while cards are published. That
    guard was in place on 2026-09-06 and did not fire, because 123 of 143 is not
    empty - and the 20 missing ones were the whole problem. The hold covers the
    partial case; nothing here weakens the total one.
    """

    def test_an_entirely_empty_upcoming_is_still_refused_at_the_publish_gate(self):
        from scanner.output import validate_event_snapshot
        ok, reason = validate_event_snapshot(
            {"upcoming": {"count": 0, "items": []}}, 0, 143)
        self.assertFalse(ok)
        self.assertIn("refusing to replace a good snapshot", reason)

    def test_a_partial_list_passes_that_gate_which_is_why_this_rule_exists(self):
        from scanner.output import validate_event_snapshot
        partial = [{"id": "e%d" % index} for index in range(123)]
        ok, _ = validate_event_snapshot(
            {"upcoming": {"count": 123, "items": partial}}, 0, 143)
        self.assertTrue(ok)


class TwentyPlayableCardsToZero(unittest.TestCase):
    """The other half of the same outage, at the size it really happened.

    Every playable Today card on 2026-09-06 came from one feed - 20 of them at
    14:09Z, and `fixtures_with_fresh_stream` fell 20 -> 0 when that feed went
    empty. Live protection is what kept those cards on the page; this states
    the two ends of it so neither can be lost: a scan that cannot check must
    not delete, and a link that is really dead must not be kept forever.
    """

    NOW = datetime(2026, 9, 6, 14, 39, tzinfo=timezone.utc)

    def twenty(self, **extra):
        cards = []
        for index in range(20):
            item = {
                "id": "evt-%02d" % index,
                "name": "Home %02d vs Away %02d" % (index, index),
                "schedule_status": "LIVE_NOW",
                "url": "https://feed.test/%02d.m3u8" % index,
                "verified": True,
                "verification_status": "verified_global",
                "publish_allowed": True,
                "end_time": (self.NOW + timedelta(hours=2)).isoformat(),
                "source_id": "quiet-feed",
            }
            item.update(extra)
            cards.append(item)
        return cards

    def protect(self, previous, probe, state, repeats=1):
        import tempfile
        from scanner.live_protection import protect_live_events
        if state is None:
            state = os.path.join(
                self.enterContext(tempfile.TemporaryDirectory()), "protection.json")
        for _ in range(repeats):
            items, stats = protect_live_events(
                [], previous, state_path=state, now=self.NOW, probe=probe)
        return items, stats

    def test_a_scan_that_found_nothing_deletes_none_of_them(self):
        items, stats = self.protect(self.twenty(), lambda card: None, None)
        self.assertEqual(len(items), 20)
        self.assertEqual(stats["probe_inconclusive"], 20)

    def test_a_scan_whose_probe_says_they_are_alive_deletes_none_of_them(self):
        items, _ = self.protect(self.twenty(), lambda card: True, None, repeats=5)
        self.assertEqual(len(items), 20)

    def test_every_stream_url_survives_the_carry(self):
        previous = self.twenty()
        items, _ = self.protect(previous, lambda card: None, None)
        self.assertEqual(
            {item["url"] for item in items},
            {item["url"] for item in previous})

    def test_links_that_really_are_dead_are_not_kept_for_ever(self):
        finished = self.twenty(
            end_time=(self.NOW - timedelta(hours=5)).isoformat())
        items, stats = self.protect(
            finished, lambda card: False, None, repeats=3)
        self.assertEqual(items, [])
        self.assertEqual(stats["released_confirmed"], 20)

    def test_the_report_says_degraded_and_names_the_feed(self):
        """What the operator sees while all 20 are being carried."""
        health = _stream_health(
            self.twenty(carried_forward_reason="still live: primary_playable"),
            [], {"source_outage": {"sources_in_outage": ["quiet-feed"]}})
        self.assertEqual(health["fixtures_with_stream"], 20)
        self.assertEqual(health["fixtures_with_fresh_stream"], 0)
        self.assertEqual(health["fixtures_with_carried_stream"], 20)
        self.assertEqual(health["state"], "degraded")
        self.assertIn("quiet-feed", health["warnings"][0])


class TheScanActuallyRunsIt(unittest.TestCase):
    """End to end through `process_events`, because a rule nothing calls is
    not a rule. The scan reads `data/upcoming.json` and
    `state/source-health.json` from its working directory, so the test builds
    one and runs the real function in it."""

    NOW = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)

    def run_scan(self, health, previous, results, *, targeted=0):
        import tempfile
        from scanner.events import process_events

        root = os.path.abspath(os.getcwd())
        temp = self.enterContext(tempfile.TemporaryDirectory())
        os.makedirs(os.path.join(temp, "data"))
        os.makedirs(os.path.join(temp, "state"))
        os.makedirs(os.path.join(temp, "reports"))
        os.makedirs(os.path.join(temp, "working"))

        def write(relative, payload):
            with open(os.path.join(temp, relative), "w", encoding="utf-8") as handle:
                json.dump(payload, handle)

        write(os.path.join("data", "upcoming.json"), {"items": previous})
        write(os.path.join("data", "today-match.json"), {"items": []})
        write(os.path.join("state", "source-health.json"),
              {"updated_at": self.NOW.isoformat(), "sources": health})
        write(os.path.join("working", "bd-results.json"), {"results": results})
        write("settings.json", {
            "timezone": "UTC",
            "events": {"timezone": "UTC", "upcoming_future_days": 120,
                       "allowed_sports": ["cricket", "football"]},
        })

        os.chdir(temp)
        try:
            return process_events(
                os.path.join(temp, "working", "bd-results.json"),
                os.path.join(temp, "settings.json"),
                os.path.join(root, "config", "event-fixtures.json"),
                now=self.NOW,
                targeted_window_minutes=targeted,
            )
        finally:
            os.chdir(root)

    def published_card(self, **overrides):
        item = {
            "id": "alpha-vs-beta",
            "name": "Alpha vs Beta",
            "fixture_id": "provider:alpha-vs-beta|test league|2026-09-06",
            "start_time": (self.NOW + timedelta(hours=5)).isoformat(),
            "start_at": (self.NOW + timedelta(hours=5)).isoformat(),
            "competition": "Test League",
            "sport_type": "football",
            "sport_class": "confirmed_football",
            "status": "UPCOMING",
            "schedule_status": "UPCOMING",
            "lifecycle_state": "UPCOMING",
            "category": "upcoming",
            "source_pipeline": "upcoming",
            "metadata_only": True,
            "allow_without_stream": True,
            "publish_allowed": True,
            "verification_status": "metadata_only",
            "available_link_count": 0,
            "source_id": "quiet-feed",
            "source_ids": ["quiet-feed"],
            "schedule_source_url": "https://example.test/quiet.json",
        }
        item.update(overrides)
        return item

    def health(self, quiet):
        record = health_record(
            source_id="quiet-feed", url="https://example.test/quiet.json",
            last_scan=self.NOW.isoformat())
        if quiet:
            record.update(
                status="success_empty", raw_items=0,
                last_productive=(self.NOW - timedelta(minutes=20)).isoformat(),
                last_productive_items=311)
        else:
            record.update(last_productive=self.NOW.isoformat())
        return {"quiet-feed": record}

    def test_a_full_scan_holds_the_card_of_a_feed_that_went_quiet(self):
        result = self.run_scan(self.health(quiet=True), [self.published_card()], [])
        names = [item["name"] for item in result["upcoming"]["items"]]
        self.assertIn("Alpha vs Beta", names)
        held = result["upcoming"]["items"][0]
        self.assertEqual(held["source_outage_authority"], "quiet-feed")

    def test_a_full_scan_publishes_nothing_extra_when_the_feed_is_healthy(self):
        result = self.run_scan(self.health(quiet=False), [self.published_card()], [])
        self.assertEqual(result["upcoming"]["items"], [])

    def test_a_targeted_trigger_does_not_run_this_rule(self):
        """It republishes the whole snapshot already, and probing on behalf of
        a scan that did not look is exactly what live protection refuses."""
        result = self.run_scan(
            self.health(quiet=True), [self.published_card()], [], targeted=30)
        stats = result["today_match"].get("scan_stats") or {}
        outage = stats.get("source_outage") if isinstance(stats, dict) else None
        if outage is not None:
            self.assertEqual(outage, {"skipped": "targeted scan"})
        names = [item["name"] for item in result["upcoming"]["items"]]
        self.assertEqual(names.count("Alpha vs Beta"), 1)
        self.assertNotIn("source_outage_authority",
                         result["upcoming"]["items"][0])


if __name__ == "__main__":
    unittest.main()
