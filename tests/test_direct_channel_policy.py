"""A direct channel is a stream somebody is carrying. It is not a fixture.

PROMPT 14. The Tapmad direct feed publishes a playable URL, a name, a logo and
a group, and nothing else. Fetched 2026-09-10:

    top level   status, name, owner, channels_amount, last_update, response
    rows        2, both group "Cricket"
    fields      id, name, logo, group, url - 2 of 2 on every row
    kickoff     none anywhere
    status      none anywhere

The audit that opened the question measured 113 rows across six groups
(Football 41, Tennis 29, Cricket 28, Skating 9, MMA 5, Multisports 1). The
feed has changed since - the rows moved under `response` and there are two of
them - so these tests cover the policy against both shapes rather than
trusting either count.

Both URLs answered 200 `application/x-mpegURL` under the repository's own
`akamaized.net` header rule and 403 without it, and their media playlists
differ - segment paths `1789038884/stream0_05173.ts` against
`1789028206/stream0_10512.ts` - so they are two real, distinct live streams.

The rows are NAMED like fixtures. That name is a title the feed chose: one of
those two URLs carries the path `ZIMvsIND` while its title says Rotterdam
against Glasgow. So a name is published as a name, and nothing here invents a
kickoff, a fixture id or a lifecycle out of it.
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scanner import direct_channel                          # noqa: E402
from scanner.direct_channel import (                        # noqa: E402
    CHANNEL_STATUS, DEFAULT_ALLOWED_SPORTS, ENTRY_TYPE, EXCLUDED_BY_SPORT,
    allowed_sports, channel_identity, classify, sport_of,
)
from scanner.parsers.event_adapters import (                # noqa: E402
    ADAPTERS, adapt_tapmad_direct, parse_event_source_flat, reset_adapter_stats,
)
from scanner.schedule_resolver import (                     # noqa: E402
    _today_source_channel_fallback,
)
from scanner.upstream_family import family_for              # noqa: E402
from scanner.events import _is_a_scheduled_fixture          # noqa: E402

SOURCE_ID = "sm-tapmad-channels"

#: The feed as fetched on 2026-09-10, both rows, verbatim.
REAL_FEED = {
    "status": "success",
    "name": "Tapmad Live Channels",
    "owner": "Monirul Islam",
    "channels_amount": 2,
    "last_update": "2026-09-10",
    "response": [
        {
            "id": "tapmad-15935",
            "name": "England vs Pakistan | Pakistan Tour of England 2026",
            "logo": "https://d34080pnh6e62j.cloudfront.net/images/a.jpg",
            "group": "Cricket",
            "url": "https://saseries.akamaized.net/hls/live/2110097/"
                   "2353jkiL-tapmad/master.m3u8",
        },
        {
            "id": "tapmad-16706",
            "name": "Rotterdam Dockers vs Glasgow Cosmic | European T20 "
                    "Premier League 2026",
            "logo": "https://d34080pnh6e62j.cloudfront.net/images/b.jpg",
            "group": "Cricket",
            "url": "https://saseries.akamaized.net/hls/live/2110097/"
                   "ZIMvsIND-3827e1/master.m3u8",
        },
    ],
}

#: The group mix the original audit measured, so the policy is tested against
#: sports the feed does not happen to be carrying today.
AUDITED_MIX = (
    [{"id": "t-f%d" % i, "name": "Football %d" % i, "logo": "l",
      "group": "Football", "url": "https://x.test/f%d.m3u8" % i}
     for i in range(3)]
    + [{"id": "t-t%d" % i, "name": "Tennis %d" % i, "logo": "l",
        "group": "Tennis", "url": "https://x.test/t%d.m3u8" % i}
       for i in range(2)]
    + [{"id": "t-c%d" % i, "name": "Cricket %d" % i, "logo": "l",
        "group": "Cricket", "url": "https://x.test/c%d.m3u8" % i}
       for i in range(2)]
    + [{"id": "t-s", "name": "Skating", "logo": "l", "group": "Skating",
        "url": "https://x.test/s.m3u8"},
       {"id": "t-m", "name": "MMA", "logo": "l", "group": "MMA",
        "url": "https://x.test/m.m3u8"},
       {"id": "t-x", "name": "Multi", "logo": "l", "group": "Multisports",
        "url": "https://x.test/x.m3u8"}]
)


def _published(rows, allowed=DEFAULT_ALLOWED_SPORTS):
    return classify(rows, source_id=SOURCE_ID, allowed=allowed)


class TheFeedAsItIsToday(unittest.TestCase):
    def test_the_rows_live_under_response(self):
        entries, diagnostics = _published(REAL_FEED["response"])
        self.assertEqual(2, diagnostics["rows"])
        self.assertEqual(2, len(entries))

    def test_a_feed_with_no_response_list_yields_nothing(self):
        """The audited shape had the rows at the top level. If the feed moves
        them again the adapter returns nothing rather than guessing."""
        reset_adapter_stats()
        self.assertEqual([], adapt_tapmad_direct({"channels": []}, SOURCE_ID))
        self.assertEqual([], adapt_tapmad_direct({}, SOURCE_ID))

    def test_both_rows_are_cricket_and_both_publish(self):
        _entries, diagnostics = _published(REAL_FEED["response"])
        self.assertEqual({"cricket": 2}, diagnostics["by_sport"])
        self.assertEqual(0, diagnostics["excluded"])

    def test_every_row_is_accounted_for(self):
        _entries, diagnostics = _published(REAL_FEED["response"])
        self.assertEqual(
            diagnostics["rows"],
            diagnostics["published"] + diagnostics["excluded"]
            + diagnostics["duplicate_urls_folded"] + len(diagnostics["skipped"]))


class ADirectChannelIsNotAFixture(unittest.TestCase):
    def setUp(self):
        reset_adapter_stats()
        self.records = adapt_tapmad_direct(REAL_FEED, SOURCE_ID)

    def test_it_says_what_it_is(self):
        for record in self.records:
            self.assertEqual(ENTRY_TYPE, record["entry_type"])

    def test_it_carries_no_kickoff(self):
        for record in self.records:
            self.assertEqual("", record["start_time"])
            self.assertEqual("", record["end_time"])

    def test_it_carries_no_status_for_a_lifecycle_to_read(self):
        for record in self.records:
            self.assertEqual("", record["status_raw"])
            self.assertIsNone(record["source_says_ended"])

    def test_it_carries_no_fixture_shaped_fields(self):
        for record in self.records:
            for field in ("fixture_id", "participants", "competition",
                          "round", "authority_status", "lifecycle_state"):
                self.assertFalse(record.get(field), field)

    def test_the_name_is_kept_as_a_name(self):
        names = [record["name"] for record in self.records]
        self.assertTrue(any("England vs Pakistan" in name for name in names))

    def test_the_logo_survives(self):
        for record in self.records:
            self.assertTrue(record["logo"])

    def test_the_published_card_is_channel_live_with_no_clock(self):
        content = json.dumps(REAL_FEED)
        source = {"id": SOURCE_ID, "name": "Tapmad Direct Channels",
                  "adapter": "tapmad_direct", "url": "https://x/Tapmad_sm.json",
                  "broadcaster": "Tapmad", "entry_type": ENTRY_TYPE}
        reset_adapter_stats()
        items = parse_event_source_flat(content, source)
        self.assertEqual(2, len(items))
        for item in items:
            card = _today_source_channel_fallback(
                dict(item, source_pipeline="today_match"))
            self.assertIsNotNone(card)
            self.assertEqual(CHANNEL_STATUS, card["schedule_status"])
            self.assertEqual(CHANNEL_STATUS, card["status"])
            self.assertTrue(card["today_source_channel"])
            self.assertEqual(ENTRY_TYPE, card["entry_type"])
            self.assertIsNone(card.get("fixture_id"))
            self.assertIsNone(card.get("start_time"))
            self.assertIsNone(card.get("end_time"))
            self.assertFalse(card["schedule_verified"])

    def test_the_fixture_preservation_rule_refuses_it(self):
        """PROMPT 13 keeps a real fixture whose routes all died. A direct
        channel is not one, and both of its guards say so."""
        content = json.dumps(REAL_FEED)
        source = {"id": SOURCE_ID, "adapter": "tapmad_direct",
                  "url": "https://x/f.json", "broadcaster": "Tapmad"}
        reset_adapter_stats()
        for item in parse_event_source_flat(content, source):
            card = _today_source_channel_fallback(
                dict(item, source_pipeline="today_match"))
            self.assertFalse(_is_a_scheduled_fixture(card))

    def test_the_flatten_carries_the_label_through(self):
        """`common` in the flattener is a whitelist, so a field the adapter
        invented is dropped unless it is named there."""
        reset_adapter_stats()
        items = parse_event_source_flat(json.dumps(REAL_FEED), {
            "id": SOURCE_ID, "adapter": "tapmad_direct", "url": "u",
            "broadcaster": "Tapmad"})
        for item in items:
            self.assertEqual(ENTRY_TYPE, item["entry_type"])
            self.assertTrue(item["channel_identity"])


class TheSportPolicyIsExplicit(unittest.TestCase):
    def test_the_default_is_cricket_and_football(self):
        self.assertEqual(("cricket", "football"), DEFAULT_ALLOWED_SPORTS)

    def test_the_repository_configures_it(self):
        settings = json.loads(
            (ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(["cricket", "football"],
                         settings["direct_channel"]["allowed_sports"])

    def test_config_is_read_rather_than_hard_coded(self):
        self.assertEqual(
            ("cricket",),
            allowed_sports({"direct_channel": {"allowed_sports": ["Cricket"]}}))

    def test_an_empty_allowlist_falls_back_rather_than_allowing_everything(self):
        """A policy that fails open is not a policy."""
        self.assertEqual(DEFAULT_ALLOWED_SPORTS,
                         allowed_sports({"direct_channel": {"allowed_sports": []}}))
        self.assertEqual(DEFAULT_ALLOWED_SPORTS, allowed_sports({}))
        self.assertEqual(DEFAULT_ALLOWED_SPORTS, allowed_sports(None))

    def test_cricket_and_football_publish(self):
        entries, diagnostics = _published(AUDITED_MIX)
        self.assertEqual({"football": 3, "cricket": 2}, diagnostics["by_sport"])
        self.assertEqual(5, len(entries))

    def test_soccer_is_football(self):
        self.assertEqual("football", sport_of("Soccer"))
        self.assertEqual("cricket", sport_of("Cricket"))

    def test_tennis_skating_mma_and_multisports_are_excluded(self):
        _entries, diagnostics = _published(AUDITED_MIX)
        self.assertEqual({"Tennis": 2, "Skating": 1, "MMA": 1,
                          "Multisports": 1}, diagnostics["excluded_by_sport"])

    def test_each_exclusion_names_the_policy(self):
        _entries, diagnostics = _published(AUDITED_MIX)
        for row in diagnostics["excluded_rows"]:
            self.assertEqual(EXCLUDED_BY_SPORT, row["reason"])

    def test_the_excluded_rows_stay_in_the_diagnostics(self):
        """Parsed and reported, never deleted from ingestion: a row nobody can
        see is still a row somebody has to be able to count."""
        _entries, diagnostics = _published(AUDITED_MIX)
        self.assertEqual(5, diagnostics["excluded"])
        self.assertEqual(5, len(diagnostics["excluded_rows"]))
        for row in diagnostics["excluded_rows"]:
            self.assertTrue(row["name"])
            self.assertTrue(row["group"])

    def test_an_unknown_group_is_not_silently_permitted(self):
        rows = [{"id": "t-z", "name": "Chess", "logo": "l", "group": "Chess",
                 "url": "https://x.test/z.m3u8"}]
        _entries, diagnostics = _published(rows)
        self.assertEqual(0, diagnostics["published"])
        self.assertEqual(1, diagnostics["excluded"])

    def test_a_row_with_no_group_is_not_permitted_either(self):
        rows = [{"id": "t-n", "name": "No group", "logo": "l",
                 "url": "https://x.test/n.m3u8"}]
        _entries, diagnostics = _published(rows)
        self.assertEqual(0, diagnostics["published"])

    def test_the_adapter_reports_the_policy_it_applied(self):
        reset_adapter_stats()
        adapt_tapmad_direct(REAL_FEED, SOURCE_ID)
        from scanner.parsers.event_adapters import _stats
        self.assertEqual(["cricket", "football"],
                         _stats(SOURCE_ID)["direct_channel"]["allowed_sports"])


class ChannelIdentityAndGrouping(unittest.TestCase):
    def test_the_feeds_own_id_is_the_identity(self):
        self.assertEqual("tapmad-15935",
                         channel_identity(REAL_FEED["response"][0], SOURCE_ID))

    def test_no_fixture_id_is_ever_used_as_identity(self):
        entries, _diagnostics = _published(REAL_FEED["response"])
        for entry in entries:
            self.assertNotIn("fixture_id", entry)
            self.assertTrue(entry["channel_identity"])

    def test_a_row_with_no_id_falls_back_on_its_url(self):
        row = {"name": "Chan", "logo": "l", "group": "Cricket",
               "url": "https://host.test/a/b.m3u8"}
        self.assertEqual("host-test-a-b-m3u8", channel_identity(row, SOURCE_ID))

    def test_a_row_with_neither_falls_back_on_its_name(self):
        row = {"name": "Star Sports 1", "group": "Cricket"}
        self.assertEqual("sm-tapmad-channels-star-sports-1",
                         channel_identity(row, SOURCE_ID))

    def test_the_same_channel_twice_is_one_entry_with_a_backup(self):
        rows = [
            {"id": "c1", "name": "Chan", "logo": "l", "group": "Cricket",
             "url": "https://x.test/primary.m3u8"},
            {"id": "c1", "name": "Chan", "logo": "l", "group": "Cricket",
             "url": "https://x.test/backup.m3u8"},
        ]
        entries, diagnostics = _published(rows)
        self.assertEqual(1, len(entries))
        self.assertEqual("https://x.test/primary.m3u8", entries[0]["url"])
        self.assertEqual(["https://x.test/backup.m3u8"], entries[0]["backups"])
        self.assertEqual(1, diagnostics["backups_attached"])

    def test_the_same_url_twice_is_kept_once(self):
        rows = [
            {"id": "c1", "name": "Chan", "logo": "l", "group": "Cricket",
             "url": "https://x.test/same.m3u8"},
            {"id": "c1", "name": "Chan", "logo": "l", "group": "Cricket",
             "url": "https://x.test/same.m3u8"},
        ]
        entries, diagnostics = _published(rows)
        self.assertEqual(1, len(entries))
        self.assertEqual([], entries[0]["backups"])
        self.assertEqual(1, diagnostics["duplicate_urls_folded"])

    def test_two_broadcasters_stay_two_entries(self):
        rows = [
            {"id": "a", "name": "Broadcaster A", "logo": "l",
             "group": "Cricket", "url": "https://x.test/a.m3u8"},
            {"id": "b", "name": "Broadcaster B", "logo": "l",
             "group": "Cricket", "url": "https://x.test/b.m3u8"},
        ]
        entries, _diagnostics = _published(rows)
        self.assertEqual(2, len(entries))
        self.assertEqual([], entries[0]["backups"])
        self.assertEqual([], entries[1]["backups"])

    def test_one_broadcaster_never_becomes_anothers_backup(self):
        """Two names sharing a URL stay two channels.

        Requirement 6 asks for two things that meet here: keep an exact
        repeated URL once, and keep different broadcasters separate. Identity
        is the key, so each keeps its own primary - folding them would be
        exactly the "one broadcaster as another's backup" the requirement
        forbids, and it is the more damaging of the two mistakes.
        """
        rows = [
            {"id": "a", "name": "Broadcaster A", "logo": "l",
             "group": "Cricket", "url": "https://x.test/shared.m3u8"},
            {"id": "b", "name": "Broadcaster B", "logo": "l",
             "group": "Cricket", "url": "https://x.test/shared.m3u8"},
        ]
        entries, _diagnostics = _published(rows)
        self.assertEqual(2, len(entries))
        self.assertEqual(["a", "b"],
                         [entry["channel_identity"] for entry in entries])
        for entry in entries:
            self.assertEqual([], entry["backups"])

    def test_but_a_url_is_never_stolen_as_someone_elses_backup(self):
        """The cross-identity guard, which only ever blocks a BACKUP.

        Broadcaster A publishes two routes; B's second row repeats A's
        primary. B keeps its own primary and does NOT collect A's route.
        """
        rows = [
            {"id": "a", "name": "Broadcaster A", "logo": "l",
             "group": "Cricket", "url": "https://x.test/a-one.m3u8"},
            {"id": "b", "name": "Broadcaster B", "logo": "l",
             "group": "Cricket", "url": "https://x.test/b-one.m3u8"},
            {"id": "b", "name": "Broadcaster B", "logo": "l",
             "group": "Cricket", "url": "https://x.test/a-one.m3u8"},
        ]
        entries, diagnostics = _published(rows)
        self.assertEqual(2, len(entries))
        by_identity = {entry["channel_identity"]: entry for entry in entries}
        self.assertEqual([], by_identity["b"]["backups"])
        self.assertEqual("https://x.test/b-one.m3u8", by_identity["b"]["url"])
        self.assertEqual(1, diagnostics["duplicate_urls_folded"])

    def test_the_two_real_rows_stay_separate(self):
        entries, _diagnostics = _published(REAL_FEED["response"])
        self.assertEqual(["tapmad-15935", "tapmad-16706"],
                         [entry["channel_identity"] for entry in entries])

    def test_a_row_with_no_url_is_skipped_and_said_so(self):
        rows = [{"id": "c", "name": "Chan", "group": "Cricket"}]
        entries, diagnostics = _published(rows)
        self.assertEqual([], entries)
        self.assertEqual(1, len(diagnostics["skipped"]))


class OneUpstreamIsOneWitness(unittest.TestCase):
    def test_both_tapmad_feeds_are_the_same_family(self):
        self.assertEqual("Tapmad", family_for(SOURCE_ID))
        self.assertEqual("Tapmad", family_for("srhady-tapmad-bd"))

    def test_so_the_direct_feed_cannot_corroborate_the_fixture_feed(self):
        """`authority_status.read` counts upstream families, not feeds, so two
        views of Tapmad are one witness however many rows they agree on."""
        from scanner.authority_status import read
        evidence = read({"authorities": {
            "srhady-tapmad-bd": {
                "result": "matched", "matched_by": "same_fixture",
                "verified": True, "status": "FINISHED",
                "upstream_family": "Tapmad", "kickoff": "2026-09-10T12:00:00+00:00"},
            SOURCE_ID: {
                "result": "matched", "matched_by": "same_fixture",
                "verified": True, "status": "FINISHED",
                "upstream_family": "Tapmad", "kickoff": "2026-09-10T12:00:00+00:00"},
        }, "verdict": "COMPARED", "conflict_reasons": []})
        self.assertEqual(["Tapmad"], evidence.families)
        self.assertEqual("LOW", evidence.confidence)

    def test_the_direct_feed_is_not_a_fixture_authority_source(self):
        from scanner.schedule_resolver import DEFAULT_FIXTURE_AUTHORITY_SOURCES
        self.assertNotIn(SOURCE_ID, DEFAULT_FIXTURE_AUTHORITY_SOURCES)


class TheSourceHasARegistryOfItsOwn(unittest.TestCase):
    """And the reason is a rule, not tidiness.

    Every id in `config/sources/today-match.json` is declared in
    `settings.events.fixture_authority_sources` - two existing tests pin that
    the two lists are equal - and an id in that list may bring a fixture into
    existence. A direct channel may not. Registering it there would also have
    broken it: a row with no status is refused by the provider-fixture path
    and would never reach the channel fallback that publishes it.
    """

    def setUp(self):
        self.direct = json.loads(
            (ROOT / "config" / "sources" / "direct-channels.json").read_text(
                encoding="utf-8"))["sources"]
        self.events = json.loads(
            (ROOT / "config" / "sources" / "today-match.json").read_text(
                encoding="utf-8"))["sources"]
        self.entry = next(row for row in self.direct if row["id"] == SOURCE_ID)

    def test_it_is_registered_with_the_direct_adapter(self):
        self.assertEqual("tapmad_direct", self.entry["adapter"])
        self.assertEqual(ENTRY_TYPE, self.entry["entry_type"])
        self.assertTrue(self.entry["enabled"])

    def test_it_points_at_the_json_feed(self):
        self.assertTrue(self.entry["url"].endswith("Tapmad_sm.json"))

    def test_a_channel_with_no_url_is_nothing_so_it_opts_out(self):
        self.assertFalse(self.entry["allow_without_stream"])

    def test_it_is_not_in_the_fixture_authority_registry(self):
        self.assertNotIn(SOURCE_ID, [row["id"] for row in self.events])

    def test_and_it_is_not_declared_a_fixture_authority(self):
        settings = json.loads(
            (ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
        self.assertNotIn(
            SOURCE_ID, settings["events"]["fixture_authority_sources"])

    def test_the_one_tapmad_fixture_feed_is_still_the_only_one(self):
        """The 2026-08-19 relay removal left exactly one Tapmad EVENT source,
        and this change does not add a second."""
        from scanner import source_loader
        loaded = source_loader.load_sources_config("config")
        self.assertEqual(
            ["srhady-tapmad-bd"],
            [row["id"] for row in loaded["today_match"] if "tapmad" in row["id"]])
        self.assertEqual(
            [SOURCE_ID],
            [row["id"] for row in loaded["direct_channel"]])

    def test_a_direct_channel_record_publishes_on_the_today_surface(self):
        """It is configured in its own registry and still lands on Today,
        because that is where a live channel belongs."""
        from scanner.parsers.event_adapters import record_pipeline
        self.assertEqual("today_match", record_pipeline(
            {"entry_type": ENTRY_TYPE, "status_raw": ""}, "direct_channel"))

    def test_the_fixture_feed_is_untouched_beside_it(self):
        fixture = next(
            row for row in self.events if row["id"] == "srhady-tapmad-bd")
        self.assertEqual("tapmad", fixture["adapter"])
        self.assertNotIn("entry_type", fixture)

    def test_the_adapter_is_registered(self):
        self.assertIn("tapmad_direct", ADAPTERS)

    def test_an_empty_or_broken_feed_costs_nothing_else(self):
        """Requirement 10. An unusable feed yields no channels and raises
        nothing, so the fixture pipeline never depends on it."""
        reset_adapter_stats()
        for payload in ({}, {"response": []}, {"response": "not a list"},
                        {"response": [None, 5, "x"]}):
            self.assertEqual([], adapt_tapmad_direct(payload, SOURCE_ID))

    def test_a_row_of_rubbish_does_not_stop_the_good_rows(self):
        reset_adapter_stats()
        payload = {"response": [None] + REAL_FEED["response"]}
        self.assertEqual(2, len(adapt_tapmad_direct(payload, SOURCE_ID)))


class TheFrontendContractIsUnchanged(unittest.TestCase):
    def test_the_status_is_the_one_the_site_already_renders(self):
        self.assertEqual("CHANNEL_LIVE", CHANNEL_STATUS)
        app = (ROOT / "site" / "assets" / "js" / "app.js").read_text(
            encoding="utf-8", errors="replace")
        self.assertIn("CHANNEL_LIVE", app)

    def test_channel_live_still_routes_to_today(self):
        from scanner.event_lifecycle import ROUTE_LIVE_STATUSES
        self.assertIn("CHANNEL_LIVE", ROUTE_LIVE_STATUSES)

    def test_no_new_entry_type_reaches_the_frontend_vocabulary(self):
        """The site renders CHANNEL_LIVE. `entry_type` is data for this
        repository, and nothing was added to the site to read it."""
        app = (ROOT / "site" / "assets" / "js" / "app.js").read_text(
            encoding="utf-8", errors="replace")
        self.assertNotIn("direct_channel", app)


if __name__ == "__main__":
    unittest.main()
