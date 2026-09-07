"""The fixture authorities, and the promise that reading them changes nothing.

Every response in `tests/fixtures/fixture-authority/` is a real one, captured
on 2026-09-07 and pruned to the fields the parsers read - values untouched.
Between them they carry every status either feed has ever been observed to
send: LiveScore Esid 1, 6, 33, 157 for cricket and 0, 1, 5, 6, 11, 13, 17, 44,
56, 91 for soccer; ESPN's Scheduled, Live, Stumps, Result, Final, Abandoned and
No result for cricket, and STATUS_SCHEDULED, STATUS_FULL_TIME, STATUS_POSTPONED,
STATUS_FINAL_PEN, STATUS_FINAL_AET, STATUS_CANCELED, STATUS_SUSPENDED and
STATUS_SECOND_HALF for soccer.

Nothing here invents a response. Football half-time has never come back from
either feed, so there is no half-time fixture and no half-time assertion - only
the assertion that an unseen state becomes UNKNOWN.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scanner import (  # noqa: E402
    authority_shadow,
    fixture_authority,
    upstream_family,
)
from scanner.schedule_resolver import (  # noqa: E402
    DEFAULT_FIXTURE_AUTHORITY_SOURCES,
    configured_event_source_ids,
    default_authority_source_ids,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(ROOT, "tests", "fixtures", "fixture-authority")
REGISTRY = os.path.join(ROOT, "config", "sources", "fixture-authority.json")

NOW = dt.datetime(2026, 9, 7, 3, 30, tzinfo=dt.timezone.utc)


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def load(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as handle:
        return json.load(handle)


def rows(name, sport, parser):
    return parser(load(name), sport)


# =======================================================================
# LiveScore
# =======================================================================

class LiveScoreParseTests(unittest.TestCase):
    def setUp(self):
        self.cricket = rows("livescore-cricket.json", "cricket",
                            fixture_authority.parse_livescore)
        self.soccer = rows("livescore-soccer.json", "football",
                           fixture_authority.parse_livescore)

    def test_cricket_parses_into_fixtures(self):
        self.assertTrue(self.cricket)
        for row in self.cricket:
            self.assertEqual(row["authority"], "livescore")
            self.assertEqual(row["upstream_family"], "LiveScore")
            self.assertEqual(row["sport"], "cricket")
            self.assertTrue(row["authority_event_id"])
            self.assertIn(" vs ", row["name"])

    def test_football_parses_into_fixtures(self):
        self.assertTrue(self.soccer)
        for row in self.soccer:
            self.assertEqual(row["sport"], "football")
            self.assertTrue(row["home"] and row["away"])

    def test_team_ids_survive(self):
        """The ids are the point: a name can be respelled, an id cannot."""
        with_ids = [row for row in self.cricket + self.soccer
                    if row["home_team_id"] and row["away_team_id"]]
        self.assertEqual(len(with_ids), len(self.cricket) + len(self.soccer))

    def test_competition_survives(self):
        self.assertTrue(all(row["competition"] for row in self.cricket))

    def test_kickoff_is_read_as_utc(self):
        """Esd is YYYYMMDDHHMMSS in UTC, checked against a known kickoff.

        `Kuwait vs Qatar` carried 20260907013000 and every feed in that scan
        agreed the match started at 01:30Z.
        """
        parsed = fixture_authority.parse_livescore_kickoff("20260907013000")
        self.assertEqual(
            parsed, dt.datetime(2026, 9, 7, 1, 30, tzinfo=dt.timezone.utc))
        self.assertTrue(all(row["kickoff"].endswith("+00:00")
                            for row in self.cricket if row["kickoff"]))

    def test_a_malformed_kickoff_is_no_kickoff(self):
        for bad in ("", None, "2026-09-07", "not a date", "202609070130"):
            self.assertIsNone(fixture_authority.parse_livescore_kickoff(bad))

    def test_round_and_cricket_format_survive(self):
        rounds = [row["round"] for row in self.cricket if row["round"]]
        formats = [row["format"] for row in self.cricket if row["format"]]
        self.assertTrue(rounds, "ErnInf carries the round")
        self.assertTrue(formats, "EtTx carries the cricket format")

    def test_gender_comes_from_the_feeds_own_words(self):
        """`EtTx` says "Womens Twenty20". No feed we publish from says either."""
        self.assertEqual(
            fixture_authority.parse_livescore({"Stages": [{
                "Snm": "Women's Twenty20 Asia Cup",
                "Events": [{"Eid": "1", "Esd": "20260907143000", "Esid": 1,
                            "Eps": "NS", "EtTx": "Womens Twenty20",
                            "T1": [{"ID": "9", "Nm": "Pakistan W"}],
                            "T2": [{"ID": "8", "Nm": "Hong Kong W"}]}]}]},
                "cricket")[0]["gender"], "women")

    def test_an_event_with_one_side_is_not_a_fixture(self):
        parsed = fixture_authority.parse_livescore({"Stages": [{
            "Snm": "X", "Events": [
                {"Eid": "1", "Esd": "20260907143000", "Esid": 1,
                 "T1": [{"Nm": "Only One"}], "T2": []}]}]}, "football")
        self.assertEqual(parsed, [])

    def test_a_payload_that_is_not_a_dict_parses_to_nothing(self):
        for junk in (None, [], "", 7):
            self.assertEqual(fixture_authority.parse_livescore(junk, "football"), [])


class LiveScoreStatusTests(unittest.TestCase):
    """One case per state that has actually come back."""

    def test_not_started(self):
        self.assertEqual(
            fixture_authority.normalize_livescore_status(1, "NS"),
            fixture_authority.UPCOMING)

    def test_full_time(self):
        self.assertEqual(
            fixture_authority.normalize_livescore_status(6, "FT"),
            fixture_authority.FINISHED)

    def test_play_in_progress(self):
        self.assertEqual(
            fixture_authority.normalize_livescore_status(33, "Play in progress"),
            fixture_authority.LIVE)

    def test_between_innings_is_in_play(self):
        """Found by the shadow report itself: it arrived UNKNOWN, was named in
        `unknown_statuses`, and is mapped now that it has been seen."""
        self.assertEqual(
            fixture_authority.normalize_livescore_status(157, "Between innings"),
            fixture_authority.LIVE)

    def test_postponed(self):
        self.assertEqual(
            fixture_authority.normalize_livescore_status(5, "Postp."),
            fixture_authority.POSTPONED)

    def test_cancelled(self):
        self.assertEqual(
            fixture_authority.normalize_livescore_status(56, "Canc."),
            fixture_authority.CANCELLED)

    def test_abandoned(self):
        self.assertEqual(
            fixture_authority.normalize_livescore_status(17, "Aband."),
            fixture_authority.ABANDONED)

    def test_after_extra_time_and_penalties_and_awarded_are_finished(self):
        for code, text in ((11, "AET"), (13, "AP"), (91, "AW")):
            self.assertEqual(
                fixture_authority.normalize_livescore_status(code, text),
                fixture_authority.FINISHED, text)

    def test_to_finish_is_unknown_rather_than_guessed(self):
        """"To finish" is neither in play nor finished. Picking one would be a
        guess wearing a reading's clothes."""
        self.assertEqual(
            fixture_authority.normalize_livescore_status(44, "ToFi"),
            fixture_authority.UNKNOWN)

    def test_blank_state_is_unknown(self):
        self.assertEqual(fixture_authority.normalize_livescore_status(0, ""),
                         fixture_authority.UNKNOWN)

    def test_an_unseen_code_is_unknown(self):
        self.assertEqual(
            fixture_authority.normalize_livescore_status(9999, "Somethingelse"),
            fixture_authority.UNKNOWN)

    def test_half_time_is_absent_because_it_has_never_been_observed(self):
        """6,240 soccer events across 17 dates and none in play at the sampling
        hour. Absent here rather than invented."""
        self.assertNotIn("ht", fixture_authority.LIVESCORE_STATUS_BY_TEXT)
        self.assertEqual(
            fixture_authority.normalize_livescore_status(-1, "HT"),
            fixture_authority.UNKNOWN)

    def test_the_text_answers_when_the_code_is_unfamiliar(self):
        self.assertEqual(
            fixture_authority.normalize_livescore_status("", "FT"),
            fixture_authority.FINISHED)

    def test_every_captured_state_maps_to_the_declared_vocabulary(self):
        parsed = (rows("livescore-cricket.json", "cricket",
                       fixture_authority.parse_livescore)
                  + rows("livescore-soccer.json", "football",
                         fixture_authority.parse_livescore))
        self.assertTrue(parsed)
        for row in parsed:
            self.assertIn(row["status"], fixture_authority.NORMALIZED_STATUSES,
                          row)

    def test_no_livescore_row_claims_an_end_time(self):
        """No LiveScore field states when a match finishes. Saying so out loud
        keeps a later reader from reaching for one."""
        for row in rows("livescore-soccer.json", "football",
                        fixture_authority.parse_livescore):
            self.assertEqual(row["end_time"], "")
            self.assertFalse(row["end_time_stated"])


# =======================================================================
# ESPN
# =======================================================================

class EspnParseTests(unittest.TestCase):
    def setUp(self):
        self.cricket = rows("espn-cricket.json", "cricket",
                            fixture_authority.parse_espn_header)
        self.soccer = rows("espn-soccer.json", "football",
                           fixture_authority.parse_espn_header)

    def test_cricket_parses_into_fixtures(self):
        self.assertTrue(self.cricket)
        for row in self.cricket:
            self.assertEqual(row["authority"], "espn-header")
            self.assertEqual(row["upstream_family"], "ESPN")
            self.assertTrue(row["authority_event_id"])

    def test_soccer_parses_into_fixtures(self):
        self.assertTrue(self.soccer)
        for row in self.soccer:
            self.assertTrue(row["home"] and row["away"])

    def test_the_name_is_built_from_the_sides_not_from_name(self):
        """Measured: ESPN's soccer `name` is the SEASON - '2026-27 English
        Premier League' - while its cricket `name` is the fixture. Building
        from `competitors[]` is the only rule that is right in both."""
        for row in self.soccer:
            self.assertIn(" vs ", row["name"])
            self.assertNotIn("Premier League", row["name"].split(" vs ")[0])
        self.assertTrue(all(row["name"] == "%s vs %s" % (row["home"], row["away"])
                            for row in self.cricket + self.soccer))

    def test_home_and_away_come_from_homeaway_not_from_array_order(self):
        """The Premier League event listed the AWAY side first, with order 1."""
        payload = {"sports": [{"leagues": [{"name": "L", "id": "1", "events": [{
            "id": "9", "date": "2026-09-07T13:00:00Z",
            "competitors": [
                {"id": "360", "homeAway": "away", "order": 1,
                 "displayName": "Manchester United"},
                {"id": "368", "homeAway": "home", "order": 2,
                 "displayName": "Everton"}]}]}]}]}
        parsed = fixture_authority.parse_espn_header(payload, "football")
        self.assertEqual(parsed[0]["home"], "Everton")
        self.assertEqual(parsed[0]["away"], "Manchester United")
        self.assertEqual(parsed[0]["home_team_id"], "368")

    def test_positional_sides_are_used_only_when_neither_declares_itself(self):
        payload = {"sports": [{"leagues": [{"name": "L", "id": "1", "events": [{
            "id": "9", "date": "2026-09-07T13:00:00Z",
            "competitors": [{"id": "1", "displayName": "First"},
                            {"id": "2", "displayName": "Second"}]}]}]}]}
        parsed = fixture_authority.parse_espn_header(payload, "football")
        self.assertEqual(parsed[0]["home"], "First")

    def test_team_ids_and_logos_survive(self):
        for row in self.cricket + self.soccer:
            self.assertTrue(row["home_team_id"] and row["away_team_id"])
        self.assertTrue(any(row.get("home_logo") for row in self.cricket))

    def test_competition_and_league_id_survive(self):
        for row in self.cricket + self.soccer:
            self.assertTrue(row["competition"])
            self.assertTrue(row["competition_id"])

    def test_kickoff_is_iso_utc(self):
        self.assertEqual(
            fixture_authority.parse_espn_kickoff("2026-09-07T01:30:00Z"),
            dt.datetime(2026, 9, 7, 1, 30, tzinfo=dt.timezone.utc))
        for row in self.cricket + self.soccer:
            self.assertTrue(row["kickoff"].endswith("+00:00"))

    def test_a_malformed_kickoff_is_no_kickoff(self):
        for bad in ("", None, "yesterday", "2026-13-45T99:99:99Z"):
            self.assertIsNone(fixture_authority.parse_espn_kickoff(bad))

    def test_cricket_format_and_round_survive(self):
        self.assertTrue([row["format"] for row in self.cricket if row["format"]])
        self.assertTrue([row["round"] for row in self.cricket if row["round"]])

    def test_end_date_is_kept_raw_and_never_read_as_a_match_end(self):
        """A T20 that finished on 2026-09-06 carried endDate 2026-09-08T23:59Z.
        `endDate` is a season or window end, so it is preserved and never
        promoted."""
        with_end = [row for row in self.cricket if row["espn_end_date_raw"]]
        self.assertTrue(with_end)
        for row in with_end:
            self.assertEqual(row["end_time"], "")
            self.assertFalse(row["end_time_stated"])

    def test_a_payload_that_is_not_a_dict_parses_to_nothing(self):
        for junk in (None, [], "", 7):
            self.assertEqual(
                fixture_authority.parse_espn_header(junk, "football"), [])

    def test_an_event_with_one_competitor_is_not_a_fixture(self):
        payload = {"sports": [{"leagues": [{"name": "L", "id": "1", "events": [
            {"id": "9", "date": "2026-09-07T13:00:00Z",
             "competitors": [{"id": "1", "displayName": "Alone"}]}]}]}]}
        self.assertEqual(
            fixture_authority.parse_espn_header(payload, "football"), [])


class EspnStatusTests(unittest.TestCase):
    """One case per state ESPN has actually sent."""

    def norm(self, **kwargs):
        return fixture_authority.normalize_espn_status(**kwargs)

    def test_scheduled_is_upcoming(self):
        self.assertEqual(
            self.norm(status="pre", state="pre", name="STATUS_SCHEDULED",
                      description="Scheduled"),
            fixture_authority.UPCOMING)

    def test_second_half_is_live(self):
        self.assertEqual(
            self.norm(status="in", state="in", name="STATUS_SECOND_HALF",
                      description="Second Half"),
            fixture_authority.LIVE)

    def test_full_time_is_finished(self):
        self.assertEqual(
            self.norm(status="post", state="post", name="STATUS_FULL_TIME",
                      description="Full Time"),
            fixture_authority.FINISHED)

    def test_after_extra_time_and_penalties_are_finished(self):
        for name in ("STATUS_FINAL_AET", "STATUS_FINAL_PEN"):
            self.assertEqual(
                self.norm(status="post", state="post", name=name),
                fixture_authority.FINISHED, name)

    def test_postponed_cancelled_suspended(self):
        self.assertEqual(self.norm(name="STATUS_POSTPONED"),
                         fixture_authority.POSTPONED)
        self.assertEqual(self.norm(name="STATUS_CANCELED"),
                         fixture_authority.CANCELLED)
        self.assertEqual(self.norm(name="STATUS_SUSPENDED"),
                         fixture_authority.SUSPENDED)

    def test_cricket_carries_no_status_name_so_the_description_answers(self):
        """Measured: `fullStatus.type.name` was None on all 344 cricket events
        across 15 dates."""
        self.assertEqual(
            self.norm(status="in", state="in", name=None, description="Live"),
            fixture_authority.LIVE)
        self.assertEqual(
            self.norm(status="post", state="post", name=None,
                      description="Result"),
            fixture_authority.FINISHED)

    def test_stumps_is_in_play_not_finished(self):
        """ESPN's own state for Stumps is "in": it is the end of a day's play
        in a multi-day match, not the end of the match."""
        self.assertEqual(
            self.norm(status="in", state="in", description="Stumps"),
            fixture_authority.LIVE)

    def test_abandoned_and_no_result_keep_their_own_answers(self):
        self.assertEqual(self.norm(state="post", description="Abandoned"),
                         fixture_authority.ABANDONED)
        self.assertEqual(self.norm(state="post", description="No result"),
                         fixture_authority.NO_RESULT)

    def test_post_alone_is_not_enough_to_claim_a_clean_finish(self):
        """post covers Result, Final, Abandoned, Canceled, Postponed,
        Suspended and No result. An unrecognised post is UNKNOWN."""
        self.assertEqual(
            self.norm(status="post", state="post", name="STATUS_SOMETHING_NEW",
                      description="Brand New Thing"),
            fixture_authority.UNKNOWN)

    def test_halftime_is_absent_because_it_has_never_been_observed(self):
        self.assertNotIn("STATUS_HALFTIME", fixture_authority.ESPN_STATUS_BY_NAME)
        self.assertNotIn("STATUS_FIRST_HALF", fixture_authority.ESPN_STATUS_BY_NAME)

    def test_every_captured_state_maps_to_the_declared_vocabulary(self):
        parsed = (rows("espn-cricket.json", "cricket",
                       fixture_authority.parse_espn_header)
                  + rows("espn-soccer.json", "football",
                         fixture_authority.parse_espn_header))
        self.assertTrue(parsed)
        for row in parsed:
            self.assertIn(row["status"], fixture_authority.NORMALIZED_STATUSES,
                          row)

    def test_the_captured_fixtures_cover_every_observed_espn_state(self):
        parsed = (rows("espn-cricket.json", "cricket",
                       fixture_authority.parse_espn_header)
                  + rows("espn-soccer.json", "football",
                         fixture_authority.parse_espn_header))
        seen = {row["status"] for row in parsed}
        for expected in (fixture_authority.UPCOMING, fixture_authority.LIVE,
                         fixture_authority.FINISHED,
                         fixture_authority.POSTPONED,
                         fixture_authority.CANCELLED,
                         fixture_authority.ABANDONED,
                         fixture_authority.SUSPENDED,
                         fixture_authority.NO_RESULT):
            self.assertIn(expected, seen)


# =======================================================================
# fetching, and what failure means
# =======================================================================

class AuthorityAvailabilityTests(unittest.TestCase):
    def test_a_fetch_failure_is_inconclusive_not_an_empty_authority(self):
        """The distinction the whole design rests on. `live_protection` already
        treats an inconclusive probe this way, and the catalogue replay proved
        the cost of the other reading: 16 of 21 cards dropped."""
        def refuse(url, timeout=0):
            return None, "http 403"

        collected, health = fixture_authority.collect(
            registry_path=REGISTRY, fetcher=refuse, now=NOW)
        self.assertTrue(health)
        for authority_id, block in health.items():
            self.assertEqual(block["state"], fixture_authority.INCONCLUSIVE,
                             authority_id)
            self.assertEqual(block["fixtures"], 0)
            self.assertEqual(collected[authority_id], [])
            self.assertTrue(all(attempt["result"] == fixture_authority.INCONCLUSIVE
                                for attempt in block["attempts"]))

    def test_an_authority_that_answered_and_listed_nothing_is_not_inconclusive(self):
        def empty(url, timeout=0):
            return ({"Stages": []} if "livescore" in url
                    else {"sports": [{"leagues": []}]}), ""

        _collected, health = fixture_authority.collect(
            registry_path=REGISTRY, fetcher=empty, now=NOW)
        for authority_id, block in health.items():
            self.assertEqual(block["state"], "ok", authority_id)
            self.assertEqual(block["fixtures"], 0)

    def test_one_bad_day_does_not_condemn_the_authority(self):
        calls = {"n": 0}

        def flaky(url, timeout=0):
            calls["n"] += 1
            if calls["n"] % 2:
                return None, "http 502"
            return {"Stages": []} if "livescore" in url else {"sports": []}, ""

        _collected, health = fixture_authority.collect(
            registry_path=REGISTRY, fetcher=flaky, now=NOW)
        self.assertTrue(any(block["state"] == "ok" for block in health.values()))

    def test_a_missing_registry_is_no_authorities_and_no_exception(self):
        self.assertEqual(
            fixture_authority.load_registry("does/not/exist.json"), [])
        collected, health = fixture_authority.collect(
            registry_path="does/not/exist.json",
            fetcher=lambda url, timeout=0: (None, "x"), now=NOW)
        self.assertEqual((collected, health), ({}, {}))

    def test_the_shipped_registry_declares_both_authorities_shadow_only(self):
        entries = fixture_authority.load_registry(REGISTRY)
        self.assertEqual({entry["id"] for entry in entries},
                         {"livescore", "espn-header"})
        for entry in entries:
            self.assertIs(entry["shadow_only"], True, entry["id"])
            self.assertEqual(entry["auth"], "none", entry["id"])
            self.assertIn(entry["adapter"], fixture_authority.PARSERS)
            self.assertEqual(set(entry["sport_paths"]), {"cricket", "football"})

    def test_the_registry_is_not_the_stream_source_registry(self):
        """Two registries, on purpose. `events.fixture_authority_sources` names
        all 21 stream feeds, which is why "authority" there separates nothing."""
        stream_ids = configured_event_source_ids()
        authority_ids = {entry["id"]
                         for entry in fixture_authority.load_registry(REGISTRY)}
        self.assertTrue(stream_ids)
        self.assertEqual(stream_ids & authority_ids, set())

    def test_results_are_consumed_in_task_order_not_arrival_order(self):
        """The six requests per authority go in parallel - sequentially they
        cost 21 seconds of a scan with a time budget - so the report must not
        be able to come out differently because one response was quicker."""
        import random
        import time

        def slow_and_shuffled(url, timeout=0):
            time.sleep(random.uniform(0, 0.02))
            date = url.split("dates=")[-1].split("&")[0] if "dates=" in url \
                else url.rstrip("/0").rsplit("/", 1)[-1]
            return {"Stages": [], "sports": [], "_date": date}, ""

        first, _health = fixture_authority.collect(
            registry_path=REGISTRY, fetcher=slow_and_shuffled, now=NOW)
        orders = []
        for _ in range(4):
            _rows, health = fixture_authority.collect(
                registry_path=REGISTRY, fetcher=slow_and_shuffled, now=NOW)
            orders.append([(attempt["sport"], attempt["date"])
                           for attempt in health["livescore"]["attempts"]])
        self.assertEqual(len(set(map(tuple, orders))), 1, orders)
        self.assertEqual(sorted(first), ["espn-header", "livescore"])

    def test_a_date_window_covers_yesterday(self):
        """A fixture that kicked off at 22:30Z is still on Today Match after
        midnight UTC. `Barbados Tridents vs Saint Lucia Kings` went unmatched
        by both authorities until yesterday was fetched."""
        seen = []

        def record(url, timeout=0):
            seen.append(url)
            return {"Stages": []}, ""

        fixture_authority.collect(registry_path=REGISTRY, fetcher=record,
                                  now=NOW)
        self.assertTrue(any("20260906" in url for url in seen))
        self.assertTrue(any("20260907" in url for url in seen))
        self.assertTrue(any("20260908" in url for url in seen))


# =======================================================================
# upstream families
# =======================================================================

class UpstreamFamilyTests(unittest.TestCase):
    FANCODE = ["sm-fancode", "sportlive-fancode-backup", "drmlive-fancode-mirror",
               "sayanpal-fancode-mirror", "vk1817-fancode-mirror",
               "dartv-fancode-mirror", "iptvflixbd-fancode-data"]

    def test_seven_fancode_feeds_are_one_witness(self):
        """Measured 2026-09-07: 39 match_ids between them, 35 present in all
        seven, zero disagreements on a name or a kickoff."""
        self.assertEqual(upstream_family.families_for(self.FANCODE), ["FanCode"])
        self.assertEqual(
            upstream_family.independent_witness_count(self.FANCODE), 1)

    def test_source_id_length_is_not_the_witness_count(self):
        described = upstream_family.describe(self.FANCODE)
        self.assertEqual(described["source_id_count"], 7)
        self.assertEqual(described["independent_witness_count"], 1)
        self.assertEqual(described["mirrors_collapsed"], 6)

    def test_five_sonyliv_feeds_are_one_witness(self):
        self.assertEqual(upstream_family.independent_witness_count([
            "srhady-sonyliv-live", "sportlive-sonyliv-backup",
            "kajju-sonyliv-backup", "drmlive-sonyliv-backup",
            "sayanpal-sonyliv-backup"]), 1)

    def test_axsports_and_bingstream_are_one_witness(self):
        self.assertEqual(upstream_family.independent_witness_count(
            ["srhady-axsports-live", "srhady-bingstream"]), 1)

    def test_the_two_tapmad_feeds_are_one_witness(self):
        """They share an id space: srhady's EntityId 15804 is sm's
        tapmad-15804, and 37 of 39 sm rows joined a srhady row across 15
        aligned snapshots."""
        self.assertEqual(upstream_family.independent_witness_count(
            ["srhady-tapmad-bd", "sm-tapmad-channels"]), 1)

    def test_livescore_and_espn_are_separate_upstreams(self):
        self.assertEqual(upstream_family.family_for("livescore"), "LiveScore")
        self.assertEqual(upstream_family.family_for("espn-header"), "ESPN")
        self.assertEqual(
            upstream_family.independent_witness_count(["livescore", "espn-header"]), 2)

    def test_a_real_card_counts_its_upstreams_not_its_feeds(self):
        described = upstream_family.describe(
            self.FANCODE + ["srhady-bingstream", "srhady-axsports-live"])
        self.assertEqual(described["source_id_count"], 9)
        self.assertEqual(described["upstream_families"], ["AXS-Bing", "FanCode"])
        self.assertEqual(described["independent_witness_count"], 2)

    def test_an_unclassified_feed_is_its_own_family(self):
        """Two strangers must not corroborate each other."""
        self.assertEqual(
            upstream_family.independent_witness_count(["new-a", "new-b"]), 2)
        self.assertTrue(
            upstream_family.family_for("new-a").startswith("unclassified:"))

    def test_every_configured_event_source_is_classified(self):
        unclassified = sorted(
            source_id for source_id in configured_event_source_ids()
            if upstream_family.family_for(source_id).startswith("unclassified:"))
        self.assertEqual(unclassified, [])

    def test_blank_ids_contribute_nothing(self):
        self.assertEqual(upstream_family.families_for(["", None, "  "]), [])


# =======================================================================
# shadow matching
# =======================================================================

def card(**kwargs):
    base = {
        "id": "toluca-vs-monterrey",
        "fixture_id": "provider:toluca-vs-monterrey|leagues cup|2026-09-07",
        "name": "Toluca vs Monterrey",
        "competition": "Leagues Cup",
        "start_time": "2026-09-07T01:15:00+00:00",
        "schedule_status": "LIVE_NOW",
        "lifecycle_state": "LIVE",
        "sport_type": "football",
        "source_ids": ["srhady-bingstream", "srhady-axsports-live"],
    }
    base.update(kwargs)
    return base


def authority_row(**kwargs):
    base = {
        "authority": "livescore",
        "upstream_family": "LiveScore",
        "authority_event_id": "1870766",
        "name": "Toluca vs Monterrey",
        "home": "Toluca",
        "away": "Monterrey",
        "home_team_id": "371",
        "away_team_id": "4719",
        "competition": "Leagues Cup: Final Stage",
        "competition_id": "1",
        "kickoff": "2026-09-07T01:15:00+00:00",
        "status_raw": "FT",
        "status_code": "6",
        "status": fixture_authority.FINISHED,
        "round": "final",
        "format": "",
        "gender": "",
        "sport": "football",
    }
    base.update(kwargs)
    return base


HEALTHY = {"livescore": {"state": "ok"}, "espn-header": {"state": "ok"}}


class ShadowMatchingTests(unittest.TestCase):
    def test_a_match_reuses_the_narrow_identity_rule(self):
        found, how = authority_shadow.match_one(card(), [authority_row()])
        self.assertEqual(how, "same_fixture")
        self.assertEqual(found["authority_event_id"], "1870766")

    def test_a_team_name_only_match_is_refused(self):
        """No kickoff on the authority row means no identification."""
        found, how = authority_shadow.match_one(
            card(), [authority_row(kickoff="")])
        self.assertIsNone(found)
        self.assertEqual(how, "")

    def test_the_anchor_rule_still_keeps_different_fixtures_apart(self):
        found, _how = authority_shadow.match_one(card(), [authority_row(
            name="Toluca vs Tigres UANL", away="Tigres UANL")])
        self.assertIsNone(found)

    def test_the_competition_travels_with_the_probe(self):
        """`genders_compatible` reads gender out of the competition too, so a
        probe carrying only a name is gender-unknown - which is why
        `Kuwait vs Qatar` refused `Kuwait vs Qatar` before this."""
        mens = card(name="Kuwait vs Qatar",
                    competition="ACC Men's Premier Cup 2026",
                    start_time="2026-09-07T01:30:00+00:00", sport_type="cricket")
        row = authority_row(name="Kuwait vs Qatar", home="Kuwait", away="Qatar",
                            competition="ACC Men's Premier Cup",
                            kickoff="2026-09-07T01:30:00+00:00",
                            status=fixture_authority.LIVE, sport="cricket")
        found, how = authority_shadow.match_one(mens, [row])
        self.assertIsNotNone(found)
        self.assertEqual(how, "same_fixture")

    def test_a_kickoff_disagreement_is_found_by_lifting_only_the_clock(self):
        row = authority_row(kickoff="2026-09-07T03:00:00+00:00")
        found, how = authority_shadow.match_one(card(), [row])
        self.assertIsNotNone(found)
        self.assertEqual(how, "kickoff_lifted")

    def test_lifting_the_clock_does_not_relax_identity(self):
        row = authority_row(name="Toluca vs Tigres UANL", away="Tigres UANL",
                            kickoff="2026-09-07T03:00:00+00:00")
        found, _how = authority_shadow.match_one(card(), [row])
        self.assertIsNone(found)

    def test_two_candidates_are_a_question_not_an_identification(self):
        rows_in = [authority_row(authority_event_id="a"),
                   authority_row(authority_event_id="b",
                                 kickoff="2026-09-07T01:30:00+00:00")]
        _found, how = authority_shadow.match_one(card(), rows_in)
        self.assertEqual(how, "ambiguous")

    def test_a_near_miss_names_the_side_that_differed(self):
        """How `Saint Lucia Kings` against ESPN's `St Lucia Kings` becomes a
        legible finding instead of a silent UNVERIFIED."""
        cpl = card(name="Barbados Tridents vs Saint Lucia Kings",
                   competition="Caribbean Premier League 2026",
                   start_time="2026-09-06T22:30:00+00:00", sport_type="cricket")
        row = authority_row(name="Barbados Tridents vs St Lucia Kings",
                            home="Barbados Tridents", away="St Lucia Kings",
                            competition="Caribbean Premier League",
                            kickoff="2026-09-06T23:00:00+00:00", sport="cricket")
        misses = authority_shadow.near_misses(cpl, [row])
        self.assertEqual(len(misses), 1)
        self.assertEqual(misses[0]["agreed_side"], "barbados tridents")
        self.assertIn("lucia", misses[0]["ours"])


class ShadowVerdictTests(unittest.TestCase):
    def test_two_independent_authorities_agreeing_is_verified(self):
        row = authority_shadow.compare(
            card(schedule_status="UPCOMING", lifecycle_state=""),
            {"livescore": [authority_row(status=fixture_authority.UPCOMING,
                                         status_raw="NS", status_code="1")],
             "espn-header": [authority_row(
                 authority="espn-header", upstream_family="ESPN",
                 authority_event_id="401879291",
                 status=fixture_authority.UPCOMING,
                 status_raw="Scheduled", status_code="STATUS_SCHEDULED")]},
            HEALTHY)
        self.assertEqual(row["verdict"], fixture_authority.VERIFIED)
        self.assertEqual(row["independent_authority_count"], 2)
        self.assertEqual(row["authority_upstreams"], ["ESPN", "LiveScore"])
        self.assertEqual(row["authority_agreement"], "AGREE")

    def test_two_authorities_disagreeing_is_a_conflict(self):
        row = authority_shadow.compare(
            card(schedule_status="UPCOMING", lifecycle_state=""),
            {"livescore": [authority_row(status=fixture_authority.FINISHED)],
             "espn-header": [authority_row(
                 authority="espn-header", upstream_family="ESPN",
                 status=fixture_authority.LIVE)]},
            HEALTHY)
        self.assertEqual(row["verdict"], fixture_authority.CONFLICT)
        self.assertEqual(row["authority_agreement"], "DISAGREE")
        self.assertTrue(any("authorities disagree" in reason
                            for reason in row["conflict_reasons"]))

    def test_a_card_contradicting_the_authority_is_a_conflict(self):
        row = authority_shadow.compare(
            card(), {"livescore": [authority_row()]}, HEALTHY)
        self.assertEqual(row["verdict"], fixture_authority.CONFLICT)
        self.assertTrue(row["card_status_conflict"])
        self.assertIn("card says LIVE, authority says FINISHED",
                      row["conflict_reasons"])

    def test_one_authority_with_a_clean_match_is_verified(self):
        row = authority_shadow.compare(
            card(schedule_status="ENDED"),
            {"livescore": [authority_row()], "espn-header": []}, HEALTHY)
        self.assertEqual(row["verdict"], fixture_authority.VERIFIED)
        self.assertEqual(row["independent_authority_count"], 1)
        self.assertEqual(row["authority_agreement"], "SINGLE")

    def test_an_unknown_authority_status_is_only_partial(self):
        row = authority_shadow.compare(
            card(schedule_status="UPCOMING"),
            {"livescore": [authority_row(status=fixture_authority.UNKNOWN,
                                         status_raw="ToFi", status_code="44")]},
            HEALTHY)
        self.assertEqual(row["verdict"], fixture_authority.PARTIAL_AUTHORITY)

    def test_a_kickoff_lifted_match_is_reported_as_a_kickoff_conflict(self):
        row = authority_shadow.compare(
            card(schedule_status="ENDED"),
            {"livescore": [authority_row(kickoff="2026-09-07T03:00:00+00:00")]},
            HEALTHY)
        self.assertEqual(row["verdict"], fixture_authority.CONFLICT)
        self.assertTrue(any(reason.startswith("kickoff")
                            for reason in row["conflict_reasons"]))

    def test_no_authority_match_is_unverified(self):
        row = authority_shadow.compare(
            card(), {"livescore": [], "espn-header": []}, HEALTHY)
        self.assertEqual(row["verdict"], fixture_authority.UNVERIFIED)
        self.assertEqual(row["independent_authority_count"], 0)

    def test_an_unavailable_authority_is_reported_as_inconclusive(self):
        row = authority_shadow.compare(
            card(), {"livescore": []},
            {"livescore": {"state": fixture_authority.INCONCLUSIVE}})
        self.assertEqual(row["authorities"]["livescore"]["result"],
                         fixture_authority.INCONCLUSIVE)
        self.assertEqual(row["verdict"], fixture_authority.UNVERIFIED)
        self.assertEqual(row["conflict_reasons"], [])

    def test_awaiting_a_link_is_not_a_claim_about_the_match(self):
        """A card badged "link খোঁজা হচ্ছে" during play is the site being honest
        about what it has, not contradicting anyone."""
        row = authority_shadow.compare(
            card(schedule_status="LINK_UPDATING"),
            {"livescore": [authority_row(status=fixture_authority.LIVE)]},
            HEALTHY)
        self.assertEqual(row["card_status_normalized"],
                         authority_shadow.AWAITING_LINK)
        self.assertFalse(row["card_status_conflict"])

    def test_a_sport_disagreement_is_a_conflict(self):
        row = authority_shadow.compare(
            card(schedule_status="ENDED", sport_type="cricket"),
            {"livescore": [authority_row(sport="football")]}, HEALTHY)
        self.assertTrue(any(reason.startswith("sport")
                            for reason in row["conflict_reasons"]))

    def test_the_row_counts_upstreams_not_feeds(self):
        row = authority_shadow.compare(
            card(source_ids=UpstreamFamilyTests.FANCODE),
            {"livescore": []}, HEALTHY)
        self.assertEqual(len(row["source_ids"]), 7)
        self.assertEqual(row["independent_witness_count"], 1)


class ShadowReportTests(unittest.TestCase):
    def build(self, today=None, upcoming=None, by_authority=None, health=None):
        return authority_shadow.build(
            today if today is not None else [card()],
            upcoming if upcoming is not None else [],
            by_authority if by_authority is not None
            else {"livescore": [authority_row()], "espn-header": []},
            health if health is not None else HEALTHY,
            now=NOW)

    def test_the_report_says_it_is_shadow_only(self):
        report = self.build()
        self.assertIs(report["shadow_only"], True)
        self.assertIn("no card was added, removed", report["note"])

    def test_totals_add_up_to_the_fixtures_examined(self):
        report = self.build(today=[card()], upcoming=[card(id="b", name="A vs B")])
        totals = report["totals"]
        self.assertEqual(totals["today_match"], 1)
        self.assertEqual(totals["upcoming"], 1)
        self.assertEqual(totals["fixtures"], 2)
        self.assertEqual(sum(totals["verdicts"].values()), 2)

    def test_every_verdict_is_one_of_the_four(self):
        report = self.build()
        for row in report["fixtures"]:
            self.assertIn(row["verdict"], (
                fixture_authority.VERIFIED, fixture_authority.PARTIAL_AUTHORITY,
                fixture_authority.UNVERIFIED, fixture_authority.CONFLICT))

    def test_an_unverified_fixture_stays_in_the_report(self):
        """UNVERIFIED is an observation about our evidence, not a sentence on
        the fixture. It is in the report and it is still published."""
        report = self.build(by_authority={"livescore": [], "espn-header": []})
        self.assertEqual(report["totals"]["unverified"], 1)
        self.assertEqual(len(report["fixtures"]), 1)

    def test_a_conflicted_fixture_stays_in_the_report(self):
        report = self.build()
        self.assertEqual(report["totals"]["conflict"], 1)
        self.assertEqual(len(report["fixtures"]), 1)

    def test_an_unavailable_authority_is_named(self):
        report = self.build(health={
            "livescore": {"state": fixture_authority.INCONCLUSIVE},
            "espn-header": {"state": "ok"}})
        self.assertEqual(report["authority_unavailable"], ["livescore"])

    def test_the_summary_carries_the_headline_numbers(self):
        summary = authority_shadow.summarize(self.build())
        self.assertIs(summary["shadow_only"], True)
        for key in ("fixtures", "verified", "partial_authority", "unverified",
                    "conflict", "matched_by_two_independent_authorities",
                    "authority_agreements", "authority_disagreements",
                    "card_status_conflicts"):
            self.assertIn(key, summary)

    def test_the_report_is_json_serialisable(self):
        json.dumps(self.build())

    def test_a_non_dict_card_is_skipped_rather_than_crashing(self):
        report = self.build(today=[card(), None, "junk"])
        self.assertEqual(report["totals"]["fixtures"], 1)

    def test_witness_distribution_is_reported(self):
        report = self.build()
        self.assertTrue(report["totals"]
                        ["independent_authority_count_distribution"])


class ShadowChangesNothingTests(unittest.TestCase):
    """The promise of this step, asserted rather than intended."""

    def test_comparing_does_not_mutate_the_card(self):
        original = card()
        before = json.dumps(original, sort_keys=True)
        authority_shadow.compare(
            original, {"livescore": [authority_row()]}, HEALTHY)
        self.assertEqual(json.dumps(original, sort_keys=True), before)

    def test_building_the_report_does_not_mutate_the_tabs(self):
        today, upcoming = [card()], [card(id="u", name="C vs D")]
        before = json.dumps([today, upcoming], sort_keys=True)
        authority_shadow.build(today, upcoming,
                               {"livescore": [authority_row()]}, HEALTHY,
                               now=NOW)
        self.assertEqual(json.dumps([today, upcoming], sort_keys=True), before)

    def test_the_existing_fixture_id_is_carried_not_replaced(self):
        row = authority_shadow.compare(
            card(), {"livescore": [authority_row()]}, HEALTHY)
        self.assertEqual(row["fixture_id"],
                         "provider:toluca-vs-monterrey|leagues cup|2026-09-07")
        self.assertEqual(row["authorities"]["livescore"]["event_id"], "1870766")
        self.assertNotEqual(row["fixture_id"],
                            row["authorities"]["livescore"]["event_id"])

    def test_one_authoritys_id_is_never_read_as_the_others(self):
        row = authority_shadow.compare(
            card(schedule_status="ENDED"),
            {"livescore": [authority_row(authority_event_id="1870766")],
             "espn-header": [authority_row(
                 authority="espn-header", upstream_family="ESPN",
                 authority_event_id="401879291")]},
            HEALTHY)
        self.assertEqual(row["authorities"]["livescore"]["event_id"], "1870766")
        self.assertEqual(row["authorities"]["espn-header"]["event_id"],
                         "401879291")
        self.assertEqual(row["independent_authority_count"], 2)

    def test_a_failing_authority_cannot_remove_a_fixture(self):
        report = authority_shadow.build(
            [card()], [], {"livescore": [], "espn-header": []},
            {"livescore": {"state": fixture_authority.INCONCLUSIVE},
             "espn-header": {"state": fixture_authority.INCONCLUSIVE}},
            now=NOW)
        self.assertEqual(len(report["fixtures"]), 1)
        self.assertEqual(report["totals"]["unverified"], 1)
        self.assertEqual(report["totals"]["conflict"], 0)

    def test_the_shadow_step_is_wrapped_so_a_report_cannot_cost_a_publish(self):
        source = read(os.path.join(ROOT, "scanner", "events.py"))
        block = source.split("fixture_authority.collect(now=now)")[0]
        self.assertTrue(block.rstrip().endswith("try:")
                        or "try:" in block[-400:])
        self.assertIn('schedule_stats["fixture_authority_shadow"] = {"error"',
                      source)

    def test_the_step_is_off_unless_settings_asks_for_it(self):
        """So a scan assembled in a test does no network I/O, and so nobody
        can turn it on by accident."""
        source = read(os.path.join(ROOT, "scanner", "events.py"))
        self.assertIn(
            'event_settings.get("fixture_authority_shadow") is not True',
            source)

    def test_the_targeted_trigger_skips_it(self):
        source = read(os.path.join(ROOT, "scanner", "events.py"))
        marker = source.index("fixture_authority.collect(now=now)")
        window = source[marker - 900:marker]
        self.assertIn("elif skip_live_protection:", window)

    def test_production_settings_enable_it(self):
        settings = json.loads(read(
            os.path.join(ROOT, "config", "settings.json")))
        self.assertIs(settings["events"]["fixture_authority_shadow"], True)

    def test_the_shadow_step_runs_after_the_payload_is_built(self):
        """It reads settled tabs. If it ran earlier it could be tempted to
        write to them."""
        source = read(os.path.join(ROOT, "scanner", "events.py"))
        self.assertLess(source.index('"today_match": _payload('),
                        source.index("fixture_authority.collect(now=now)"))


# =======================================================================
# the legacy authority gate
# =======================================================================

class LegacyAuthorityGateTests(unittest.TestCase):
    def test_the_guides_two_names_are_still_the_declared_default(self):
        self.assertEqual(
            DEFAULT_FIXTURE_AUTHORITY_SOURCES,
            frozenset({"srhady-axsports-upcoming", "srhady-willow-event-upcoming"}))

    def test_neither_of_them_is_a_configured_source(self):
        """Not in any of the 13 revisions of the registry. On their own the
        fallback matched nothing, so a missing settings key would have refused
        every candidate and published empty tabs."""
        self.assertEqual(
            DEFAULT_FIXTURE_AUTHORITY_SOURCES & configured_event_source_ids(),
            frozenset())

    def test_the_fallback_now_lands_on_the_configured_registry(self):
        fallback = default_authority_source_ids()
        self.assertTrue(DEFAULT_FIXTURE_AUTHORITY_SOURCES <= fallback)
        self.assertTrue(configured_event_source_ids() <= fallback)
        self.assertIn("srhady-bingstream", fallback)

    def test_the_fallback_only_widens_and_never_narrows(self):
        self.assertTrue(
            DEFAULT_FIXTURE_AUTHORITY_SOURCES <= default_authority_source_ids())

    def test_an_unreadable_registry_leaves_the_guides_names_alone(self):
        self.assertEqual(
            default_authority_source_ids("does/not/exist.json"),
            DEFAULT_FIXTURE_AUTHORITY_SOURCES)

    def test_settings_still_decides_in_a_normal_scan(self):
        """The fallback is unreachable while settings supplies a list, which is
        why this change cannot move a published card."""
        settings = json.loads(read(
            os.path.join(ROOT, "config", "settings.json")))
        self.assertIsInstance(settings["events"]["fixture_authority_sources"], list)
        self.assertTrue(settings["events"]["fixture_authority_sources"])


if __name__ == "__main__":
    unittest.main()
